"""시세 수집 → DB 저장.

감시 엔진(monitor.py)이 주기적으로 호출합니다.
- 장중: 당일 1분봉(이 종목, KOSDAQ)과 현재가를 20초마다 갱신
- 장 마감 후 1회: 당일 데이터 최종 반영
- 시작 시 1회: 최근 약 7거래일 과거 분봉 보충 (빠진 구간 보완)
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

import json as _json_mod

from . import daum, naver
from .db import connect, get_state, set_state

KST = timezone(timedelta(hours=9))
KOSDAQ = "KOSDAQ"


def kst_now() -> datetime:
    """PC 시간대 설정과 관계없이 한국 시간 (tz 정보 없는 datetime)."""
    return datetime.now(KST).replace(tzinfo=None)


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def is_market_window(now: datetime) -> bool:
    """정규장 + 앞뒤 여유 (08:59 ~ 15:36)."""
    return is_weekday(now.date()) and time(8, 59) <= now.time() <= time(15, 36)


def _upsert(symbol: str, bars: list[naver.Bar], source: str, replace: bool) -> int:
    if not bars:
        return 0
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(
            f"""{verb} INTO minute_bars
                (symbol, ts, open, high, low, close, volume, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(symbol, b.ts, b.open, b.high, b.low, b.close, b.volume, source) for b in bars],
        )
        return conn.total_changes - before


def completed_only(bars: list[naver.Bar], now: datetime) -> list[naver.Bar]:
    """아직 진행 중인 현재 1분봉은 제외 (거래량이 덜 집계된 상태라서)."""
    cur_minute = now.replace(second=0, microsecond=0).isoformat(timespec="minutes")
    return [b for b in bars if b.ts < cur_minute]


def _kis_bars(rows) -> list[naver.Bar]:
    return [naver.Bar(ts=r["ts"], open=r["open"] or r["close"], high=r["high"] or r["close"], low=r["low"] or r["close"],
                      close=r["close"], volume=int(r["volume"] or 0)) for r in rows if r.get("close")]


def sync_today(code: str, is_index: bool = False, now: datetime | None = None) -> int:
    now = now or kst_now()
    symbol = KOSDAQ if is_index else code
    from . import kis
    if kis.enabled():                    # 한국투자증권 (열쇠가 있으면 시세는 모두 한투)
        n = now.replace(tzinfo=None)
        if is_index:
            rows = kis.index_bars()
        else:
            with connect() as conn:
                since = conn.execute("SELECT MAX(ts) FROM minute_bars WHERE symbol=? AND source='kis' AND ts >= ?",
                                     (symbol, n.date().isoformat())).fetchone()[0]
            rows = kis.today_bars(code, n, since)
        return _upsert(symbol, completed_only(_kis_bars(rows), now), "kis", replace=True)
    bars = completed_only(naver.fetch_today_minutes(code, is_index=is_index), now)
    return _upsert(symbol, bars, "naver_today", replace=True)


def sync_history(code: str, days: int = 14, now: datetime | None = None) -> int:
    """과거 분봉 보충. 이미 있는 분봉(당일 분봉 OHLC 등)은 덮어쓰지 않음. 한투를 쓰면 지난 분봉은 kis_backfill이 채움."""
    from . import kis
    if kis.enabled():
        return 0
    now = now or kst_now()
    bars = naver.fetch_history_minutes(code, now.date() - timedelta(days=days), now.date())
    return _upsert(code, completed_only(bars, now), "naver_history", replace=False)


def _kis_quotes(code: str):
    """한투 현재가 → 화면이 쓰는 형식 (네이버 현재가와 같은 키). 체결 시각은 장 시각으로:
    장중 지금 / 15:30~18:00(거래소 시간외) 지금 / 그 뒤·다음 날 장 전에는 마지막 거래일 18:00."""
    from . import kis
    now = kst_now().replace(tzinfo=None)
    q = kis.quote(code)
    with connect() as conn:
        last_day = conn.execute("SELECT MAX(substr(ts,1,10)) FROM minute_bars WHERE symbol=?", (code,)).fetchone()[0]
    hm = now.strftime("%H:%M")
    is_today = last_day == now.date().isoformat()
    open_ = is_today and "09:00" <= hm < "15:30"
    at = f"{now.date().isoformat()}T{hm}:00" if is_today and hm < "18:00" else f"{last_day or now.date().isoformat()}T18:00:00"
    out = {"name": None, "price": q["price"], "change": q["change"], "change_pct": q["rate"], "open": q["open"],
           "high": q["high"], "low": q["low"], "volume": q["volume"],
           "value_text": f"{q['value'] / 1e8:.2f}억" if q.get("value") else None,
           "market_cap": q["market_cap_100m"] * 1e8 if q.get("market_cap_100m") else None,
           "market_status": "OPEN" if open_ else "CLOSE", "traded_at": at, "fetched_at": now.isoformat(timespec="seconds")}
    set_state(f"quote:{code}", json.dumps(out, ensure_ascii=False))
    set_state(f"fund:{code}", json.dumps({"info": {"per": q.get("per"), "pbr": q.get("pbr")},
                                          "fetched_at": now.isoformat(timespec="minutes")}, ensure_ascii=False))
    k = kis.index_quote()
    set_state(f"quote:{KOSDAQ}", json.dumps({"price": k["price"], "change": k["change"], "change_pct": k["rate"],
                                               "market_status": out["market_status"], "traded_at": at,
                                               "fetched_at": out["fetched_at"]}, ensure_ascii=False))


def update_quotes(code: str):
    from . import kis
    if kis.enabled():
        return _kis_quotes(code)
    q = naver.fetch_quote(code)
    set_state(f"quote:{code}", json.dumps(q, ensure_ascii=False))
    try:                                   # 주가 기록 오른쪽 '주요 숫자' 칸용 네이버 숫자 (10분에 한 번)
        st = get_state(f"naver_snapshot:{code}")
        old = json.loads(st["value"]) if st and st.get("value") else {}
        if not old.get("fetched_at") or kst_now().replace(tzinfo=None) - datetime.fromisoformat(old["fetched_at"]) >= timedelta(minutes=10):
            snap = naver.fetch_snapshot(code)
            snap["fetched_at"] = kst_now().replace(tzinfo=None).isoformat(timespec="minutes")
            set_state(f"naver_snapshot:{code}", json.dumps(snap, ensure_ascii=False))
    except (naver.DataSourceError, ValueError, TypeError):
        pass
    try:
        k = naver.fetch_quote(KOSDAQ, is_index=True)
        set_state(f"quote:{KOSDAQ}", json.dumps(k, ensure_ascii=False))
    except naver.DataSourceError:
        pass  # 지수 현재가는 보조 정보


def purge_old_bars(keep_days: int):
    """오래된 분봉 정리. 이상변동 전후 구간은 이벤트 분석용으로 남김. keep_days가 0 이하면 지우지 않음(영구 보관)."""
    if keep_days <= 0:
        return
    cutoff = (kst_now() - timedelta(days=keep_days)).isoformat(timespec="minutes")
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM minute_bars
            WHERE ts < ?
              AND NOT EXISTS (
                SELECT 1 FROM events e
                WHERE datetime(minute_bars.ts) BETWEEN datetime(e.start_ts, '-60 minutes')
                                                   AND datetime(e.last_ts, '+130 minutes')
              )
              AND NOT EXISTS (
                SELECT 1 FROM pr_events p
                WHERE datetime(minute_bars.ts) BETWEEN datetime(p.published_ts, '-60 minutes')
                                                   AND datetime(p.published_ts, '+130 minutes')
              )
            """,
            (cutoff,),
        )


def record_ok(note: str = ""):
    set_state("data_status", "ok" + (f": {note}" if note else ""))
    set_state("data_last_ok", kst_now().isoformat(timespec="seconds"))


def record_error(err: Exception):
    set_state("data_status", f"error: {err}")


# ------------------------------------------------------------------ 원인 분석용 수집

def snapshot_traders(code: str, now: datetime | None = None) -> int:
    """거래원(증권사 창구) 당일 누적 수량을 1분 단위로 저장."""
    now = now or kst_now()
    info = naver.fetch_trader_info(code)
    today = now.strftime("%Y%m%d")
    rows = [t for t in info["traders"] if (t.get("bizdate") or today) == today]
    if not rows:
        return 0
    ts = now.replace(second=0, microsecond=0).isoformat(timespec="minutes")
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO trader_snapshots(symbol, ts, trader_no, name, buy, sell, is_foreign, "
            "buy_top5, sell_top5) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(code, ts, t["trader_no"], t["name"], t["buy"], t["sell"], int(t["foreign"]),
              int(t["buy_top5"]), int(t["sell_top5"])) for t in rows],
        )
    # 네이버가 함께 주는 외국계 창구 추정 합계 (장중 순매수 칸용 — 거래소 개인·외국인·기관 확정치는 장 마감 뒤에만 나옴)
    set_state(f"foreign_est:{code}", json.dumps({"date": now.date().isoformat(), "ts": ts,
                                                  "buy": info.get("foreign_buy"), "sell": info.get("foreign_sell")}))
    return len(rows)


def save_trader_daily(code: str, info: dict, date: str):
    now = kst_now().isoformat(timespec="minutes")
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO trader_daily(symbol, date, source, name, buy, sell, is_foreign, buy_top5, "
            "sell_top5, captured_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(code, date, info["source"], t["name"], t["buy"], t["sell"], int(t["foreign"]),
              int(t["buy_top5"]), int(t["sell_top5"]), now) for t in info["traders"]],
        )


def _norm(name: str) -> str:
    return (name or "").replace(" ", "").replace("증권", "").replace("투자", "")


def crosscheck_traders(code: str, now: datetime | None = None) -> dict:
    """네이버와 다음 금융 거래원을 거의 같은 시각에 받아 비교.

    두 곳 모두 코스콤이 제공하는 '매매 상위 5개사' 자료를 보여주므로 값이 같아야 한다.
    받는 순간 사이에 체결이 있으면 상위권 수량이 조금 다를 수 있어, 같은 창구의 값이
    1% 또는 50주 이내면 일치로 본다.
    """
    now = now or kst_now()
    n = naver.fetch_trader_info(code)
    d = daum.fetch_trader_ranks(code, "TODAY")
    dn = {_norm(t["name"]): t for t in d["traders"]}
    compared, matched, diffs, frozen = 0, 0, [], []
    for t in n["traders"]:
        o = dn.get(_norm(t["name"]))
        if not o:
            continue
        for side in ("buy", "sell"):
            a, b = t[side], o[side]
            if a == 0 and b == 0:
                continue
            live = t[f"{side}_top5"] and o[f"{side}_top5"]
            item = {"name": t["name"], "side": "매수" if side == "buy" else "매도", "naver": a, "daum": b}
            if not live:
                if a != b:
                    frozen.append(item)   # 5위 밖에서 멈춘 값: 멈춘 시점 차이로 다를 수 있어 판정에서 제외
                continue
            compared += 1
            if abs(a - b) <= max(50, 0.01 * max(a, b)):
                matched += 1
            else:
                diffs.append(item)
    nn = {_norm(t["name"]) for t in n["traders"]}
    missing = [{"name": o["name"], "buy": o["buy"], "sell": o["sell"], "foreign": o["foreign"],
                "buy_top5": o["buy_top5"], "sell_top5": o["sell_top5"]}
               for k, o in dn.items() if k not in nn and (o["buy_top5"] or o["sell_top5"])]
    fsum_ok = n["foreign_buy"] == d["foreign_buy"] and n["foreign_sell"] == d["foreign_sell"]
    result = {"ts": now.isoformat(timespec="minutes"), "compared": compared, "matched": matched,
              "diffs": diffs[:6], "frozen_diffs": frozen[:6], "missing_in_naver": missing, "foreign_sum": {"naver": [n["foreign_buy"], n["foreign_sell"]],
                                                 "daum": [d["foreign_buy"], d["foreign_sell"]], "same": fsum_ok}}
    set_state(f"trader_crosscheck:{code}", _json_mod.dumps(result, ensure_ascii=False))
    return result


def last_crosscheck(code: str):
    s = get_state(f"trader_crosscheck:{code}")
    try:
        return _json_mod.loads(s["value"]) if s and s.get("value") else None
    except ValueError:
        return None


def sync_broker_side_daily(code: str, n: int = 30) -> int:
    rows = daum.fetch_trader_histories(code, n)
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO broker_side_daily(symbol, date, foreign_buy, foreign_sell, foreign_net, "
            "domestic_buy, domestic_sell, domestic_net) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(code, r["date"], r["foreign_buy"], r["foreign_sell"], r["foreign_net"], r["domestic_buy"],
              r["domestic_sell"], r["domestic_net"]) for r in rows if r["date"]],
        )
    return len(rows)


def sync_research(code: str) -> int:
    rows = naver.fetch_research(code)
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO research(id, symbol, broker, title, date, url) VALUES (?, ?, ?, ?, ?, ?)",
            [(r["id"], code, r["broker"], r["title"], r["date"], r["url"]) for r in rows],
        )
    return len(rows)


def end_of_day_traders(code: str, peers: list[str], now: datetime | None = None):
    """장 마감 시점 거래원 저장 (이 종목 + 같은 업종, 네이버)."""
    now = now or kst_now()
    date = now.date().isoformat()
    for c in [code] + list(peers):
        try:                                   # 네이버만 사용 (다음 금융은 쓰지 않음)
            save_trader_daily(c, naver.fetch_trader_info(c), date)
        except Exception:
            continue


def sync_news(code: str, n: int = 20) -> int:
    items = naver.fetch_news(code, n)
    now = kst_now().isoformat(timespec="seconds")
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO news(id, symbol, ts, office, title, body, url, cluster, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(f"{code}:{i['id']}", code, i["ts"], i["office"], i["title"], i["body"], i["url"], i["cluster"], now)
             for i in items],
        )
        return conn.total_changes - before


def sync_disclosures(code: str, n: int = 20) -> int:
    """공시 목록: DART(비밀값 DART_API_KEY가 있으면) → 없으면 예전처럼 네이버. 시각은 KIND에서."""
    from . import dart
    full = False
    if dart.enabled():
        added, full = dart.sync(code)
    else:
        items = naver.fetch_disclosures(code, n)
        now = kst_now().isoformat(timespec="minutes")
        with connect() as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO disclosures(no, symbol, date, title, kind, summary, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(i["no"], code, i["date"], i["title"], i["kind"], i["summary"], now) for i in items],
            )
            added = conn.total_changes - before
    try:                                    # 공시 접수 시각 (기사보다 먼저 나왔는지 보려고)
        from . import kind
        today = kst_now().date()
        since = today - timedelta(days=400)
        if full:                            # DART 전체 기록을 처음 받은 날: 시각도 가장 오래된 공시까지
            with connect() as conn:
                first = conn.execute("SELECT MIN(date) FROM disclosures WHERE symbol=?", (code,)).fetchone()[0]
            since = min(since, date.fromisoformat(first)) if first else since
        kind.fill_times(code, since, today, add_exchange=dart.enabled())
    except Exception as e:                  # noqa: BLE001 - 시각이 없어도 공시 목록은 그대로
        print(f"::warning title=공시 시각::{type(e).__name__}: {str(e)[:150]}", flush=True)
    return added


def sync_investor(code: str) -> int:
    from . import kis
    if kis.enabled():
        rows = [{"bizdate": r["date"], "foreign": r["foreign"], "organ": r["organ"], "individual": r["individual"],
                 "close": None, "volume": None, "foreign_ratio": None} for r in kis.investors(code) if r.get("date")]
    else:
        rows = naver.fetch_investor_trend(code, 30)
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO investor_daily(symbol, bizdate, foreign_net, organ_net, individual_net, "
            "close, volume, foreign_ratio) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(code, r["bizdate"], r["foreign"], r["organ"], r["individual"], r["close"], r["volume"],
              r["foreign_ratio"]) for r in rows],
        )
    return len(rows)


def investor_stale(code: str, today: date) -> bool:
    """투자자별 순매수 기록이 비었는지: 오늘 전 마지막 거래일(일봉 기준) 값이 없으면 True (장중이라도 바로 다시 받게)."""
    with connect() as conn:
        last_day = conn.execute("SELECT MAX(date) FROM daily_bars WHERE symbol=? AND date<?",
                                (code, today.isoformat())).fetchone()[0]
        last_inv = conn.execute("SELECT MAX(bizdate) FROM investor_daily WHERE symbol=?", (code,)).fetchone()[0]
    if not last_day:
        return False
    return not last_inv or last_inv < last_day.replace("-", "")


def purge_related_bars(main_symbol: str, keep_days: int = 10):
    """같은 업종 종목 분봉은 짧게 보관 (이상변동 전후는 유지)."""
    cutoff = (kst_now() - timedelta(days=keep_days)).isoformat(timespec="minutes")
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM minute_bars
            WHERE symbol NOT IN (?, ?) AND ts < ?
              AND NOT EXISTS (
                SELECT 1 FROM events e
                WHERE datetime(minute_bars.ts) BETWEEN datetime(e.start_ts, '-60 minutes')
                                                   AND datetime(e.last_ts, '+130 minutes'))
            """,
            (main_symbol, KOSDAQ, cutoff),
        )
        conn.execute("DELETE FROM trader_snapshots WHERE ts < ?", (cutoff,))
        # 시장 전체 뉴스는 60일만 보관 (저장 용량을 작게)
        conn.execute("DELETE FROM macro_news WHERE ts < ?",
                     ((kst_now() - timedelta(days=60)).isoformat(timespec="minutes"),))
