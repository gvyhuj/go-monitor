"""감시 엔진 (백그라운드 프로세스).

브라우저 화면(app.py)과 별도로 동작합니다. 브라우저를 닫아도 계속 돌아가며,
'02_프로그램_종료.bat' 으로 종료합니다.

하는 일
- 시작 시: 최근 약 7거래일 과거 분봉 보충 + 오늘 빠진 구간 보완
- 장중(평일 08:59~15:36): 20초마다 오늘 1분봉·현재가 수집 → 이상변동 감지
- 장 마감 후 1회: 오늘 데이터 최종 반영
- 장 외 시간: 10분마다 현재가만 갱신
"""
from __future__ import annotations

import logging
import os
import time
import traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core import board, articles, causes, cloud, feed, macro, newssearch
from core.db import init_db, now_iso, set_state
from core.detector import detect_symbol, load_settings
from core import naver
from core.naver import DataSourceError

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
RUN_DIR = ROOT / "run"
STOP_FLAG = RUN_DIR / "engine.stop"


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("engine")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        LOG_DIR / "engine.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def acquire_single_instance_lock():
    """감시 엔진이 두 개 동시에 돌지 않도록 잠금 파일을 잡습니다."""
    RUN_DIR.mkdir(exist_ok=True)
    fh = open(RUN_DIR / "engine.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh  # 프로세스가 끝날 때까지 열어 둠


class Collector:
    """언제 무엇을 받아올지 결정 (장중/마감 후/장 외)."""

    def __init__(self, code: str, log: logging.Logger):
        self.code = code
        self.log = log
        self.history_synced_on = None   # 과거 분봉 보충한 날짜
        self.eod_done_on = None         # 장 마감 후 최종 반영한 날짜
        self.last_quote_at = datetime.min
        self.last_error = None
        # 원인 분석용 수집 시각
        self.daily_synced_on = None
        self.last_trader_minute = None
        self.last_news_at = datetime.min
        self.last_discl_at = datetime.min
        self.last_related_at = datetime.min
        self.last_investor_at = datetime.min
        self.last_crosscheck_at = datetime.min
        self.eod_traders_on = None
        self.fetch_cache = {}
        self.first_backfill_done = False
        self.sync_mode = False          # 클라우드 한 번 실행: 백그라운드 스레드 없이 끝까지 기다림
        self.last_macro_at = datetime.min
        self.last_theme_at = datetime.min
        self.eod_theme_on = None
        self.eod_daily_on = None
        self.overseas_on = None

    # -------------------------------------------------- 원인 분석용 보조 수집
    def _safe(self, what, fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # 보조 데이터라 실패해도 감시는 계속
            self.log.warning("%s 수집 실패(계속 진행): %s", what, e)
            return None

    def _scan_themes(self, today):
        found = self._safe("이 종목 테마 찾기", macro.sync_stock_themes, self.code, self.log)
        if found:
            self._safe("묶음 매수 확인 종목", macro.sync_basket_universe, self.code, log=self.log)
        if found and self.traded_today(today):
            now = feed.kst_now()
            if now.time() >= feed.time(9, 1):
                self._safe("테마 등락", macro.snapshot_themes, self.code, now, today.isoformat())
                # 테마 정보가 생겼으니 오늘 이상변동을 다시 분석
                from core.db import connect
                with connect() as conn:
                    conn.execute("UPDATE events SET cause_stage=NULL WHERE symbol=? AND substr(start_ts,1,10)=?",
                                 (self.code, today.isoformat()))

    def traded_today(self, today) -> bool:
        from core.db import connect
        with connect() as conn:
            return bool(conn.execute("SELECT 1 FROM minute_bars WHERE symbol=? AND substr(ts,1,10)=? LIMIT 1",
                                     (self.code, today.isoformat())).fetchone())

    def related_codes(self):
        return [r["code"] for r in feed.related_list()]

    def peer_trader_codes(self):
        """거래원(창구)을 확인할 종목: 비교 종목 + 네이버 방위산업·우주항공 테마 대형 종목."""
        codes = self.related_codes()
        return codes + [x["code"] for x in macro.basket_codes() if x["code"] not in codes]

    def factor_codes(self):
        return [f["code"] for f in (load_settings().get("factor_etfs") or [])]

    def collect_context(self, now: datetime):
        today = now.date()
        settings = load_settings()
        if self.daily_synced_on != today:
            self.daily_synced_on = today
            rel = self._safe("같은 업종 목록", feed.sync_related, self.code, settings.get("extra_peers", []))
            if rel:
                self._safe("같은 업종 과거 분봉", feed.sync_related_bars, [r["code"] for r in rel], True, now)
            self._safe("투자자별 순매수", feed.sync_investor, self.code)
            self._safe("뉴스", feed.sync_news, self.code)
            self._safe("공시", feed.sync_disclosures, self.code)
            self._safe("증권사 리포트", feed.sync_research, self.code)
            first = not self.first_backfill_done
            self.first_backfill_done = True
            n_art = self._safe("이 종목 기사", articles.sync_articles, self.code, 3 if first else 1)
            self._safe("일봉", macro.sync_daily_bars, [self.code, feed.KOSDAQ] + [r["code"] for r in (rel or [])] + self.factor_codes())
            self._safe("시장 뉴스", macro.sync_macro_news, 16 if first else 2, ("flashnews",))
            self._safe("시장 주요 뉴스", macro.sync_macro_news, 4 if first else 1, ("mainnews",))
            self._safe("해외 방산주·유가", macro.sync_overseas)
            if not macro.basket_universe() and macro.my_themes(self.code):
                self._safe("묶음 매수 확인 종목", macro.sync_basket_universe, self.code, log=self.log)
            if macro.themes_age_days(self.code) >= 7:
                # 전체 테마를 훑는 데 1~2분 걸려서 감시를 멈추지 않도록 따로 실행
                import threading
                if self.sync_mode:
                    self._scan_themes(today)
                else:
                    threading.Thread(target=self._scan_themes, args=(today,), daemon=True).start()
            if feed.is_weekday(today) and now.time() >= feed.time(9, 0) and self.traded_today(today):
                self._safe("테마 등락", macro.snapshot_themes, self.code, now, today.isoformat())
            self.log.info("기사·거시 자료 수집 (새 이 종목 기사 %s건)", n_art if n_art is not None else "-")
            self.last_news_at = self.last_discl_at = self.last_related_at = self.last_investor_at = now
            self._safe("종목토론방(최근 2주)", board.sync_board, self.code, now - timedelta(days=15), 20)
            self.log.info("원인 분석용 자료 수집 (같은 업종 %d개)", len(rel or []))

        if feed.is_market_window(now):
            minute = now.replace(second=0, microsecond=0)
            if minute != self.last_trader_minute:
                self.last_trader_minute = minute
                self._safe("거래원", feed.snapshot_traders, self.code, now)
            if now - self.last_news_at >= timedelta(minutes=5):
                self.last_news_at = now
                self._safe("뉴스", feed.sync_news, self.code)
                self._safe("이 종목 기사", articles.sync_articles, self.code, 1)
                self._safe("종목토론방", board.sync_board, self.code)
            if now - self.last_macro_at >= timedelta(minutes=5):
                self.last_macro_at = now
                self._safe("시장 뉴스", macro.sync_macro_news, 2)
            if now - self.last_theme_at >= timedelta(minutes=5) and now.time() >= feed.time(9, 1) \
                    and self.traded_today(today):
                self.last_theme_at = now
                self._safe("테마 등락", macro.snapshot_themes, self.code, now, today.isoformat())
            if now - self.last_discl_at >= timedelta(minutes=10):
                self.last_discl_at = now
                self._safe("공시", feed.sync_disclosures, self.code)
            if now - self.last_related_at >= timedelta(minutes=5):
                self.last_related_at = now
                self._safe("같은 업종 분봉", feed.sync_related_bars, self.related_codes(), False, now)
        else:
            if now - self.last_news_at >= timedelta(minutes=30):
                self.last_news_at = now
                self._safe("뉴스", feed.sync_news, self.code)
                self._safe("공시", feed.sync_disclosures, self.code)
                self._safe("이 종목 기사", articles.sync_articles, self.code, 1)
                self._safe("종목토론방", board.sync_board, self.code)
                self._safe("시장 뉴스", macro.sync_macro_news, 2)
            if feed.is_weekday(today) and now.time() >= feed.time(15, 40) and self.eod_theme_on != today \
                    and self.traded_today(today):
                self.eod_theme_on = today
                self._safe("테마 마감 등락", macro.snapshot_themes, self.code, now, today.isoformat())
            if feed.is_weekday(today) and now.time() >= feed.time(15, 50) and self.eod_daily_on != today:
                self.eod_daily_on = today
                self._safe("일봉", macro.sync_daily_bars, [self.code, feed.KOSDAQ] + self.related_codes() + self.factor_codes())
            if now.time() >= feed.time(6, 30) and now.time() < feed.time(8, 59) and self.overseas_on != today:
                self.overseas_on = today
                self._safe("해외 방산주·유가", macro.sync_overseas)
            if feed.is_weekday(today) and now.time() >= feed.time(15, 36) and self.eod_traders_on != today:
                self.eod_traders_on = today
                self._safe("장 마감 거래원", feed.end_of_day_traders, self.code, self.peer_trader_codes(), now)
                self.log.info("장 마감 거래원 기록 저장 (네이버)")
            if feed.is_weekday(today) and now.time() >= feed.time(15, 40) \
                    and now - self.last_investor_at >= timedelta(minutes=60):
                self.last_investor_at = now
                self._safe("투자자별 순매수", feed.sync_investor, self.code)

    def fetch(self, kind, arg, day):
        """원인 분석 중 필요한 자료를 그 자리에서 받아옴 (짧은 시간 안 중복 요청은 생략)."""
        now = feed.kst_now()
        if kind == "related_bars":
            if day == now.date():
                key, ttl, hist = ("rb_today",), timedelta(minutes=3), False
            else:
                key, ttl, hist = ("rb_hist", now.date()), timedelta(hours=24), True
            if now - self.fetch_cache.get(key, datetime.min) < ttl:
                return
            self.fetch_cache[key] = now
            feed.sync_related_bars(list(arg), hist, now)
        elif kind == "news":
            key = ("news", arg)
            if now - self.fetch_cache.get(key, datetime.min) < timedelta(minutes=10):
                return
            self.fetch_cache[key] = now
            feed.sync_news(arg, 10)
        elif kind == "articles":
            key = ("articles",)
            if now - self.fetch_cache.get(key, datetime.min) < timedelta(minutes=5):
                return
            self.fetch_cache[key] = now
            articles.sync_articles(arg, 1)
        elif kind == "macro_backfill":
            key = ("macro_backfill", day)
            if key in self.fetch_cache:
                return
            self.fetch_cache[key] = now
            macro.backfill_macro_news(day, log=self.log.info)
            newssearch.backfill(day, log=self.log.info)
        elif kind == "board":
            key = ("board", day)
            if now - self.fetch_cache.get(key, datetime.min) < timedelta(minutes=10):
                return
            self.fetch_cache[key] = now
            since = datetime.combine(day, datetime.min.time()) - timedelta(days=15)
            board.sync_board(arg, since=since, max_pages=40 if day != now.date() else 3)
        elif kind == "peer_traders":
            # 같은 업종 종목의 거래원은 '오늘' 것만 받을 수 있음 (지난 날짜는 장 마감 기록 사용)
            if day != now.date():
                return {}
            key = ("peer_traders",)
            cached = self.fetch_cache.get(key)
            if cached and now - cached[0] < timedelta(minutes=3):
                return cached[1]
            out = {}
            for c in arg:
                try:
                    out[c] = naver.fetch_trader_info(c)
                except Exception:
                    continue
            self.fetch_cache[key] = (now, out)
            return out

    def _ok(self, note=""):
        feed.record_ok(note)
        if self.last_error:
            self.log.info("데이터 수신 정상화")
        self.last_error = None

    def _err(self, e: Exception):
        feed.record_error(e)
        msg = str(e)
        if msg != self.last_error:
            self.log.warning("데이터 수신 오류: %s", msg)
            self.last_error = msg

    def run_once(self):
        now = feed.kst_now()
        today = now.date()
        if self.history_synced_on != today:
            self.history_synced_on = today   # 실패해도 하루 1회만 시도 (보조 데이터)
            try:
                n = feed.sync_history(self.code, now=now)
                self.log.info("과거 분봉 보충: %d건 추가", n)
            except DataSourceError as e:
                self.log.warning("과거 분봉 보충 실패(계속 진행): %s", e)

        self.collect_context(now)

        try:
            if feed.is_market_window(now):
                feed.sync_today(self.code, now=now)
                try:
                    feed.sync_today(feed.KOSDAQ, is_index=True, now=now)
                except DataSourceError as e:
                    self.log.warning("KOSDAQ 분봉 수신 실패(계속 진행): %s", e)
                feed.update_quotes(self.code)
                self.last_quote_at = now
                self._ok("장중 수집")
            elif feed.is_weekday(today) and now.time() > feed.time(15, 36) and self.eod_done_on != today:
                feed.sync_today(self.code, now=now + timedelta(days=1))   # 마지막 분봉까지 포함
                try:
                    feed.sync_today(feed.KOSDAQ, is_index=True, now=now + timedelta(days=1))
                except DataSourceError:
                    pass
                feed.update_quotes(self.code)
                self.eod_done_on = today
                self.last_quote_at = now
                self.log.info("장 마감 데이터 최종 반영")
                self._ok("장 마감")
            elif now - self.last_quote_at > timedelta(minutes=10):
                feed.update_quotes(self.code)
                self.last_quote_at = now
                self._ok("장 외 시간")
        except DataSourceError as e:
            self._err(e)


def main():
    log = setup_logging()
    lock = acquire_single_instance_lock()
    if lock is None:
        log.info("다른 감시 엔진이 이미 실행 중이라 종료합니다.")
        return
    STOP_FLAG.unlink(missing_ok=True)

    init_db()
    settings = load_settings()
    code = settings["symbol"]
    use_naver = settings.get("data_source", "naver") == "naver"
    collector = Collector(code, log) if use_naver else None

    causes.upgrade_reanalysis(code, feed.kst_now(), log)
    set_state("monitor_pid", str(os.getpid()))
    set_state("monitor_status", "running")
    log.info("감시 엔진 시작 (pid=%s, 종목=%s, 데이터=%s)", os.getpid(), code,
             "네이버" if use_naver else "데모")

    last_error = None
    last_purge = None
    publisher = cloud.Scheduler(log)
    while not STOP_FLAG.exists():
        settings = load_settings()
        try:
            if collector:
                collector.run_once()
            n = detect_symbol(settings["symbol"])
            if n:
                log.info("신규 이상변동 이벤트 %d건 감지", n)
            # 원인 분석 기록 (1차 → 준최종 → 최종)
            causes.refresh_pending(settings["symbol"], feed.kst_now(),
                                   fetch=collector.fetch if collector else None, log=log)
            today = feed.kst_now().date()
            if last_purge != today:
                feed.purge_old_bars(int(settings.get("keep_bars_days", 120)))
                feed.purge_related_bars(settings["symbol"])
                last_purge = today
            set_state("monitor_status", "running")
            set_state("monitor_heartbeat", now_iso())
            # 어디서나 보기 사이트로 결과 올리기 (연결했을 때만)
            now_k = feed.kst_now()
            publisher.tick(now_k, feed.is_market_window(now_k) and collector is not None
                           and collector.traded_today(now_k.date()))
            last_error = None
        except Exception as e:
            msg = f"error: {type(e).__name__}: {e}"
            set_state("monitor_status", msg)
            if msg != last_error:  # 같은 오류를 반복 기록하지 않음
                log.error("감시 중 오류\n%s", traceback.format_exc())
                last_error = msg
        set_state("monitor_heartbeat", now_iso())

        # 종료 요청에 빠르게 반응하도록 1초 단위로 대기
        for _ in range(max(5, int(settings.get("monitor_poll_seconds", 20)))):
            if STOP_FLAG.exists():
                break
            time.sleep(1)

    set_state("monitor_status", "stopped")
    STOP_FLAG.unlink(missing_ok=True)
    log.info("감시 엔진 정상 종료")


if __name__ == "__main__":
    main()
