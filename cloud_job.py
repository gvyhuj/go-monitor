"""클라우드에서 한 번 실행되는 감시 작업 (GitHub Actions가 장중 5분마다 실행).

한 번 실행할 때 하는 일
  1. 지난 기록(암호화된 SQLite)을 풀어서 이어 씀
  2. 사이트에서 들어온 요청 반영 (기사 직접 등록, 목록에서 빼기)
  3. 네이버·다음에서 수집 (오늘 1분봉 전체, KOSDAQ, 현재가, 거래원, 기사, 시장 뉴스, 테마 …)
  4. 주가 급등·급락 감지 → 원인 분석 (1차·준최종·최종)
  5. 웹사이트 파일 만들기 (GitHub Pages, 분석 자료는 사이트 비밀번호로 암호화)

두 가지 방식
  - GO_STATIC_SITE=1 : GitHub Pages (기본, 이 저장소 workflow). 요청은 GitHub 이슈로 받음
  - GO_CF_TOKEN      : Cloudflare Workers 사이트 (예전 방식)

    python cloud_job.py            # 보통 실행
    python cloud_job.py password   # (Cloudflare 방식) 사이트 비밀번호만 다시 적용
"""
from __future__ import annotations

import json
import logging
import os
import sys
import secrets
import base64
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

from core import causes, cloud, feed
from core.db import DB_PATH, connect, get_state, init_db, set_state
from core.detector import detect_symbol, load_settings

FLAG_FIELDS = ["history_synced_on", "eod_done_on", "last_quote_at", "daily_synced_on", "last_trader_minute",
               "last_news_at", "last_discl_at", "last_related_at", "last_investor_at", "last_crosscheck_at",
               "eod_traders_on", "first_backfill_done", "last_macro_at", "last_theme_at", "eod_theme_on",
               "eod_daily_on", "overseas_on"]


def _enc(v):
    if isinstance(v, datetime):
        return None if v == datetime.min else {"dt": v.isoformat()}
    if isinstance(v, date):
        return {"d": v.isoformat()}
    return v


def _dec(v):
    if isinstance(v, dict):
        if "dt" in v:
            return datetime.fromisoformat(v["dt"])
        if "d" in v:
            return date.fromisoformat(v["d"])
    return v


def load_flags(c):
    st = get_state("job:flags")
    if not st or not st.get("value"):
        return
    try:
        flags = json.loads(st["value"])
    except ValueError:
        return
    for k in FLAG_FIELDS:
        if k in flags and flags[k] is not None:
            setattr(c, k, _dec(flags[k]))


def save_flags(c):
    set_state("job:flags", json.dumps({k: _enc(getattr(c, k, None)) for k in FLAG_FIELDS}))


def apply_inbox(item: dict, log):
    """사이트에서 보낸 요청 하나를 기록에 반영."""
    t = item.get("type")
    with connect() as conn:
        if t == "pr":
            title = str(item.get("title") or "").strip()[:200]
            ts = datetime.fromisoformat(str(item.get("published_ts"))).isoformat(timespec="minutes")
            if title:
                conn.execute("INSERT INTO pr_events(title, published_ts, url, pr_type) VALUES (?, ?, ?, ?)",
                             (title, ts, str(item.get("url") or "")[:500], "직접 등록(사이트)"))
                log.info("사이트에서 기사 등록: %s (%s)", title, ts)
        elif t in ("hide", "show"):
            aid = str(item.get("id") or "")
            conn.execute("UPDATE articles SET hidden=? WHERE id=? OR story=?", (1 if t == "hide" else 0, aid, aid))
            log.info("사이트에서 기사 %s: %s", "빼기" if t == "hide" else "다시 표시", aid)
        elif t == "delete_pr":
            pid = str(item.get("id") or "").replace("pr", "")
            if pid.isdigit():
                conn.execute("DELETE FROM pr_events WHERE id=?", (int(pid),))
                log.info("사이트에서 직접 등록 기사 삭제: %s", pid)


def checkpoint():
    """기록 파일 하나로 정리 (data 브랜치에 저장하기 전에)."""
    with connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


# ------------------------------------------------------------------ GitHub Pages 방식
STATE_DIR = Path(os.environ.get("GO_STATE_DIR", "state"))        # actions/cache 로 다음 실행에 넘김 (암호화)
RESTORE_DIR = Path(os.environ.get("GO_RESTORE_DIR", "restore"))  # data 브랜치의 백업 (암호화)
BACKUP_DIR = Path(os.environ.get("GO_BACKUP_DIR", "backup"))     # 이번에 data 브랜치에 올릴 백업
SITE_DIR = Path(os.environ.get("GO_SITE_DIR", "_site"))


def static_mode() -> bool:
    return os.environ.get("GO_STATIC_SITE") == "1"


def _site_password() -> str:
    return os.environ.get("GO_SITE_PASSWORD", "")


def _data_key() -> str:
    """기록 백업용 열쇠. 따로 없으면 사이트 비밀번호 (이때는 비밀번호를 바꾸면 이전 백업을 못 풂)."""
    return os.environ.get("GO_DATA_KEY") or _site_password()


def restore_state(log) -> str:
    from core import sitebuild
    if DB_PATH.exists():
        return "기존 파일"
    key = _data_key()
    for src, label in ((STATE_DIR, "직전 실행"), (RESTORE_DIR, "data 브랜치 백업")):
        if sitebuild.restore_db(src, DB_PATH, key):
            log.info("지난 기록을 이어 씁니다 (%s, %.1f MB)", label, DB_PATH.stat().st_size / 1e6)
            return label
        if sitebuild.find_backup(src):
            log.error("%s 기록을 풀지 못했습니다. DATA_KEY(없으면 SITE_PASSWORD)가 바뀐 것 같습니다.", label)
    log.info("첫 실행입니다. 새 기록을 만듭니다.")
    return "새로 시작"


def site_salt(password: str) -> bytes:
    fp = hashlib.sha256(("go-fp:" + password).encode("utf-8")).hexdigest()
    st = get_state("site:salt")
    try:
        cur = json.loads(st["value"]) if st and st.get("value") else {}
    except ValueError:
        cur = {}
    if cur.get("salt") and cur.get("fp") == fp:
        return base64.b64decode(cur["salt"])
    salt = secrets.token_bytes(16)
    set_state("site:salt", json.dumps({"salt": base64.b64encode(salt).decode(), "fp": fp}))
    return salt


def build_static_site(now, log) -> dict:
    from core import sitebuild
    pw = _site_password()
    items = sitebuild.refresh_items(now, log.info)
    salt = site_salt(pw)
    checkpoint()
    n = sitebuild.build(SITE_DIR, pw, salt, items, repo=os.environ.get("GITHUB_REPOSITORY", ""))
    # 다음 실행으로 넘길 기록 (암호화)
    sitebuild.backup_db(DB_PATH, STATE_DIR, _data_key())
    # data 브랜치 백업은 1시간에 한 번
    last = get_state("site:last_backup")
    due = not last or not last.get("value") or \
        datetime.fromisoformat(last["value"]) < now.replace(tzinfo=None) - timedelta(minutes=55)
    if due:
        set_state("site:last_backup", now.replace(tzinfo=None).isoformat(timespec="seconds"))
        checkpoint()
        sitebuild.backup_db(DB_PATH, STATE_DIR, _data_key())
        sitebuild.backup_db(DB_PATH, BACKUP_DIR, _data_key())
    return {"files": n, "backup": due}


def annotate(level: str, text: str):
    """GitHub 실행 화면 맨 위에 보이는 알림 (error / warning / notice)."""
    if os.environ.get("GITHUB_ACTIONS"):
        msg = str(text).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::{level} title=주가 감시::{msg}", flush=True)


def import_seeds(log):
    """seed/*.enc: 감시를 켜기 전에 따로 받아 둔 자료(암호화)를 한 번만 기록에 넣음.

    """
    from core import naver, sitebuild
    from core.feed import save_trader_daily
    seed_dir = Path(__file__).resolve().parent / "seed"
    key = _data_key()
    if not seed_dir.exists() or not key:
        return
    for f in sorted(seed_dir.glob("*.enc")):
        flag = f"seed:{f.name}"
        st = get_state(flag)
        if st and st.get("value"):
            continue
        raw = f.read_bytes()
        try:
            data = json.loads(sitebuild.decrypt(sitebuild.derive_key(key, raw[:16]), raw[16:]))
        except Exception as e:
            log.warning("자료 %s 를 풀지 못했습니다: %s", f.name, e)
            continue
        day = data["date"]
        for code, info in (data.get("traders") or {}).items():
            save_trader_daily(code, naver.parse_trader_info(info), day)
        with connect() as conn:
            conn.execute("UPDATE events SET cause_stage=NULL WHERE substr(start_ts,1,10)=?", (day,))
        set_state(flag, datetime.now().isoformat(timespec="minutes"))
        log.info("미리 받아 둔 %s 거래원 자료를 넣었습니다 (%d종목)", day, len(data.get("traders") or {}))


def summary(text: str):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main(argv) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
    log = logging.getLogger("job")
    task = argv[1] if len(argv) > 1 else "run"
    static = static_mode()
    if static:
        if len(_site_password()) < 8:
            log.error("SITE_PASSWORD(사이트 비밀번호)가 없거나 8자보다 짧습니다. 저장소 Settings → Secrets 에 넣어 주세요.")
            summary("### 실패\nSITE_PASSWORD 비밀값이 없거나 8자보다 짧습니다.")
            annotate("error", f"SITE_PASSWORD 비밀값이 없거나 8자보다 짧습니다 (현재 {len(_site_password())}자).")
            return 1
        restored = restore_state(log)
    init_db()
    try:                                   # 회사 설정은 암호화된 기록 안에만 둠
        from core.detector import save_private_from_file
        if save_private_from_file():
            log.info("회사 설정을 암호화된 기록으로 옮겼습니다")
    except Exception as e:  # noqa: BLE001
        log.warning("회사 설정 옮기기 실패: %s", e)
    _s = load_settings()
    for v in {_s.get("symbol"), _s.get("display_name")}:   # 공개 실행 기록(로그)에 종목코드·회사 이름이 보이지 않게 가림
        if v and v != "이 종목":
            print(f"::add-mask::{v}", flush=True)
    if not _s.get("symbol"):
        annotate("error", "회사 설정(종목코드)이 없습니다. 암호화된 기록이 비었거나 GO_PRIVATE_CONFIG 비밀값이 필요합니다.")
        return 1
    if static:
        try:
            import_seeds(log)
        except Exception as e:
            log.warning("미리 받아 둔 자료 넣기 실패(계속 진행): %s", e)
    settings = load_settings()
    code = settings["symbol"]
    now = feed.kst_now()
    log.info("클라우드 감시 실행 (%s, %s)", task, now.isoformat(timespec="minutes"))

    site_ok = False
    inbox_n = 0
    if static:
        from core import ghinbox
        try:
            inbox_n = ghinbox.process(lambda item: apply_inbox(item, log), log.info)
        except Exception as e:
            log.warning("사이트 요청(GitHub 이슈) 확인 실패(계속 진행): %s", e)
    elif cloud.env_mode():
        try:
            st = cloud.ensure_site(log.info, force=(task == "password"))
            site_ok = True
            summary(f"### 사이트 주소\n{st.get('url')}\n")
        except Exception as e:
            log.error("사이트 준비 실패: %s", e)
            summary(f"### 사이트 준비 실패\n{e}\n")
    else:
        log.warning("사이트 설정(GO_STATIC_SITE 또는 GO_CF_TOKEN)이 없어 사이트 만들기는 건너뜁니다.")
    if task == "password" and not static:
        if site_ok:
            cloud.publish(("ui",), log=log.info)
        return 0 if site_ok else 1

    if site_ok:
        try:
            inbox_n = cloud.pull_inbox(lambda item: apply_inbox(item, log), log.info)
        except Exception as e:
            log.warning("사이트 요청 확인 실패(계속 진행): %s", e)

    failed = None
    try:
        # 수집: 데스크톱 감시 엔진과 같은 규칙을 한 번 실행 (마지막 실행 시각은 기록에 저장)
        import monitor
        c = monitor.Collector(code, log)
        c.sync_mode = True
        load_flags(c)
        try:
            c.run_once()
        finally:
            save_flags(c)

        # 국제·정치 이슈 기사 (구글 뉴스): 매 실행마다 (내부에서 장중 15분·장 밖 60분 간격으로 조절)
        from core import newssearch
        issue_err = []
        try:
            n_purged = 0 if get_state("issue_news:purged_v1") else newssearch.purge_disallowed()
            set_state("issue_news:purged_v1", "1")
            if n_purged:
                annotate("notice", f"허용 출처가 아닌 이슈 기사 {n_purged}건 정리 (한국·영어권 주요 언론만 유지)")
        except Exception as e:  # noqa: BLE001
            annotate("warning", f"이슈 기사 정리 실패: {e}")
        try:
            n_issue_new = newssearch.sync_recent(issue_err.append)
            if n_issue_new or issue_err:
                annotate("notice", f"이슈 기사 새로 {n_issue_new}건" + (f", 실패: {issue_err[0][:150]}" if issue_err else ""))
        except Exception as e:  # noqa: BLE001
            annotate("warning", f"이슈 기사 수집 실패: {e}")
        if not get_state("issue_news:filter_v1"):          # 출처 제한 후 지난 날짜를 한 번 다시 보충
            with connect() as conn:
                conn.execute("DELETE FROM system_state WHERE key LIKE 'issue_news:backfill:%'")
            set_state("issue_news:filter_v1", now.isoformat(timespec="minutes"))
        # 최근 2주 이상변동 날짜의 이슈 기사를 한 번씩 보충하고, 보충되면 그날 이벤트를 다시 분석
        with connect() as conn:
            ev_days = sorted({r[0] for r in conn.execute(
                "SELECT DISTINCT substr(start_ts,1,10) FROM events WHERE symbol=? AND start_ts>=?",
                (code, (now - timedelta(days=14)).isoformat(timespec="minutes")))})
        for d in ev_days:
            try:
                added = newssearch.backfill(date.fromisoformat(d), log=log.info)
            except Exception as e:  # noqa: BLE001
                annotate("warning", f"지난 이슈 기사 보충 실패({d}): {e}")
                continue
            if added:
                with connect() as conn:
                    conn.execute("UPDATE events SET cause_stage=NULL WHERE symbol=? AND substr(start_ts,1,10)=?", (code, d))
                annotate("notice", f"{d} 이슈 기사 {added}건 보충 → 그날 이상변동 다시 분석")

        n = detect_symbol(code)
        if n:
            log.info("신규 이상변동 %d건", n)
        causes.upgrade_reanalysis(code, now, log)
        # 업종 ETF 일봉·묶음 확인 종목이 비어 있으면 하루 한 번 수집을 기다리지 않고 바로 채움
        from core import macro as _macro
        etfs = [f["code"] for f in (settings.get("factor_etfs") or [])]
        with connect() as conn:
            empty = [e for e in etfs if not conn.execute("SELECT 1 FROM daily_bars WHERE symbol=? LIMIT 1", (e,)).fetchone()]
        if empty:
            log.info("업종 ETF 일봉 %d개 수집: %s", _macro.sync_daily_bars(empty), ", ".join(empty))
        if not _macro.basket_universe() and _macro.my_themes(code):
            try:
                _macro.sync_basket_universe(code, log=log)
            except Exception as e:  # noqa: BLE001
                log.warning("묶음 확인 종목 수집 실패: %s", e)
        # 업종 ETF 일봉이 처음 들어오면 최근 이벤트를 팩터 분해로 다시 분석
        if etfs and not (get_state("factor_ready") or {}).get("value"):
            with connect() as conn:
                have = all(conn.execute("SELECT COUNT(*) FROM daily_bars WHERE symbol=?", (e,)).fetchone()[0] >= 60 for e in etfs)
                if have:
                    conn.execute("UPDATE events SET cause_stage=NULL WHERE symbol=?", (code,))
            if have:
                set_state("factor_ready", now.isoformat(timespec="minutes"))
                log.info("업종 ETF 자료가 준비되어 이벤트를 다시 분석합니다")
        causes.refresh_pending(code, feed.kst_now(), fetch=c.fetch, log=log)
        # 급등·급락 알림 (GitHub 앱 푸시: 저장소 주인 @언급)
        try:
            from core import alerts
            if alerts.send_test(log.warning):
                annotate("notice", "알림 시험 글을 보냈습니다")
            n_alert = alerts.send_new(code, log=log.info)
            if n_alert:
                annotate("notice", f"급등·급락 알림 {n_alert}건 보냄")
        except Exception as e:  # noqa: BLE001
            annotate("warning", f"알림 보내기 실패: {e}")

        last = get_state("job:last_purge")
        if not last or last.get("value") != now.date().isoformat():
            feed.purge_old_bars(int(settings.get("keep_bars_days", 120)))
            feed.purge_related_bars(code)
            set_state("job:last_purge", now.date().isoformat())
    except Exception as e:  # noqa: BLE001 - 수집이 실패해도 사이트와 기록은 저장
        failed = e
        log.exception("수집·분석 중 오류: %s", e)
        summary(f"- 수집·분석 오류: {e}")
        import traceback
        annotate("error", "수집·분석 오류: " + "".join(traceback.format_exception(e)[-4:]))
    set_state("monitor_status", "running")
    from core.db import now_iso
    set_state("monitor_heartbeat", now_iso())

    if site_ok:
        try:
            r = cloud.publish(log=log.info)
            summary(f"- 업로드 {r.get('written', 0)}건, 오늘 {cloud.writes_today()}/{cloud.DAILY_WRITE_CAP}건"
                    + (f", 사이트 요청 {inbox_n}건 반영" if inbox_n else ""))
        except Exception as e:
            log.error("사이트 업로드 실패: %s", e)
            summary(f"- 업로드 실패: {e}")
    if static:
        try:
            r = build_static_site(now, log)
            repo = os.environ.get("GITHUB_REPOSITORY", "")
            if repo:
                owner, name = repo.split("/", 1)
                summary(f"### 사이트 주소\nhttps://{owner.lower()}.github.io/{name}/\n")
            summary(f"- 사이트 자료 {r['files']}개 (암호화), 기록 이어쓰기: {restored}"
                    + (", data 브랜치 백업" if r["backup"] else "") + (f", 사이트 요청 {inbox_n}건 반영" if inbox_n else ""))
        except Exception as e:
            log.error("사이트 만들기 실패: %s", e)
            summary(f"- 사이트 만들기 실패: {e}")
            raise
    checkpoint()
    with connect() as conn:
        bars = conn.execute("SELECT COUNT(*), MAX(ts) FROM minute_bars WHERE symbol=?", (code,)).fetchone()
        evs = conn.execute("SELECT COUNT(*) FROM events WHERE symbol=?", (code,)).fetchone()[0]
    summary(f"- 1분봉 {bars[0]:,}건 (마지막 {bars[1]}), 이상변동 누적 {evs}건")
    annotate("notice", f"1분봉 {bars[0]:,}건 (마지막 {bars[1]}), 이상변동 누적 {evs}건, 기록 이어쓰기: {restored if static else '-'}")
    try:                                # 자료 점검 (공개 시세 수준의 정보만 표시)
        from core import macro, newssearch
        chk = []
        with connect() as conn:
            for i_f, f in enumerate(settings.get("factor_etfs") or [], 1):
                rows = conn.execute("SELECT date, close FROM daily_bars WHERE symbol=? ORDER BY date DESC LIMIT 30",
                                    (f["code"],)).fetchall()
                dup = conn.execute(
                    "SELECT b.symbol, COUNT(*) FROM daily_bars a JOIN daily_bars b ON a.date=b.date AND a.close=b.close "
                    "AND b.symbol<>a.symbol WHERE a.symbol=? AND a.date>=? GROUP BY b.symbol ORDER BY 2 DESC LIMIT 1",
                    (f["code"], rows[-1][0] if rows else "9999")).fetchone()
                n_all = conn.execute("SELECT COUNT(*) FROM daily_bars WHERE symbol=?", (f["code"],)).fetchone()[0]
                chk.append(f"업종ETF{i_f} 일봉 {n_all}일(마지막 {rows[0][0] if rows else '-'})"
                           + (f" ⚠ 다른 종목과 종가 {dup[1]}일 같음" if dup and dup[1] >= 10 else ""))
            since = (feed.kst_now() - timedelta(days=1)).isoformat(timespec="minutes")
            n_issue = conn.execute("SELECT COUNT(*) FROM macro_news WHERE (id LIKE 's%' OR id LIKE 'g%') AND ts>=?", (since,)).fetchone()[0]
            other = {lab: conn.execute("SELECT COUNT(*) FROM daily_bars WHERE symbol=?", (c_,)).fetchone()[0]
                     for lab, c_ in (("종목", code), ("KOSDAQ", "KOSDAQ"))}
            n_theme = conn.execute("SELECT COUNT(*) FROM stock_themes WHERE code=?", (code,)).fetchone()[0]
        chk.append("일봉 " + ", ".join(f"{k} {v}일" for k, v in other.items()) + f" / 종목 테마 {n_theme}개"
                   + " / 회사 설정 " + ("있음" if settings.get("symbol") else "없음"))
        for i_f, f in enumerate(settings.get("factor_etfs") or [], 1):
            if any(x.startswith(f"업종ETF{i_f} 일봉 0일") for x in chk):
                try:
                    got = macro.fetch_daily(f["code"])
                    chk.append(f"업종ETF{i_f} 직접 받기 {len(got)}일")
                except Exception as e:  # noqa: BLE001
                    chk.append(f"업종ETF{i_f} 직접 받기 실패: {type(e).__name__}")
        bu = macro.basket_universe()
        with connect() as conn:
            tops = conn.execute("SELECT office, COUNT(*) FROM macro_news WHERE id LIKE 'g%' AND ts>=? GROUP BY office "
                                "ORDER BY 2 DESC LIMIT 12", (since,)).fetchall()
        chk.append("이슈 기사(구글 뉴스) " + (f"최근 하루 {n_issue}건" if newssearch.enabled() else "꺼짐")
                   + (" — 출처 " + ", ".join(f"{o} {n}" for o, n in tops) if tops else ""))
        chk.append("묶음 확인 종목 " + (", ".join(f"{k} {len(v.get('stocks') or [])}개" for k, v in bu.items()) or "아직 없음"))
        annotate("notice", "자료 점검: " + " / ".join(chk))
    except Exception as e:  # noqa: BLE001
        annotate("warning", f"자료 점검 실패: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        annotate("error", "실행 실패: " + "".join(traceback.format_exception(e)[-5:]))
        sys.exit(1)
