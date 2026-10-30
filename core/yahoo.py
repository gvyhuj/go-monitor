"""야후 파이낸스 5분봉·60분봉 (지난 분봉 채우기용, coarse_bars에 1분봉과 따로 저장).

네이버는 지난 1분봉을 약 1주만 다시 주므로, 1분봉이 없는 날의 차트는 야후의 굵은 봉으로 보여 줌.
- 5분봉: 최근 약 60일 / 60분봉: 최근 약 2년 (야후가 주는 범위)
- 공식 API가 아닌 화면용 주소라 바뀌거나 막힐 수 있음. 실패해도 수집은 계속.
- 코스닥 종목은 '종목코드.KQ'
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

from .db import connect

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={iv}m&range={rg}"
RANGES = {5: "60d", 60: "730d"}
KST = timezone(timedelta(hours=9))


INDEX = "KOSDAQ"                # 기록 안 이름 (야후 ^KQ11 = 코스닥 종합지수)


def ticker(code: str) -> str:
    return "%5EKQ11" if code == INDEX else f"{code}.KQ"


def parse(raw: bytes) -> list[dict]:
    """야후 응답 → [{ts(한국 시각, 봉 시작), open, high, low, close, volume}] (값이 빈 봉은 뺌)."""
    d = json.loads(raw.decode("utf-8"))
    res = ((d.get("chart") or {}).get("result") or [None])[0]
    if not res:
        return []
    q = (res.get("indicators") or {}).get("quote") or [{}]
    q = q[0]
    out = []
    for i, t in enumerate(res.get("timestamp") or []):
        vals = [(q.get(k) or [None] * (i + 1))[i] for k in ("open", "high", "low", "close", "volume")]
        if any(v is None for v in vals[:4]):
            continue
        ts = datetime.fromtimestamp(t, KST).replace(tzinfo=None)
        if not ("09:00" <= ts.strftime("%H:%M") <= "15:30"):
            continue
        out.append({"ts": ts.isoformat(timespec="minutes"), "open": vals[0], "high": vals[1], "low": vals[2],
                    "close": vals[3], "volume": int(vals[4] or 0)})
    return out


def fetch(code: str, interval: int) -> list[dict]:
    req = urllib.request.Request(URL.format(sym=ticker(code), iv=interval, rg=RANGES[interval]),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse(r.read())


def sync(code: str, fetch_fn=None) -> dict:
    """이 종목과 코스닥 지수의 5분봉·60분봉을 받아 coarse_bars에 더함 (이미 있는 봉은 그대로). 이 종목에 넣은 개수."""
    fetch_fn = fetch_fn or fetch
    added = {}
    for sym in (code, INDEX):
        for iv in RANGES:
            bars = fetch_fn(sym, iv)
            with connect() as conn:
                before = conn.total_changes
                conn.executemany("INSERT OR IGNORE INTO coarse_bars(symbol, interval, ts, open, high, low, close, volume) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                 [(sym, iv, b["ts"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in bars])
                if sym == code:
                    added[iv] = conn.total_changes - before
    return added


def completed_points(rows, iv):
    """Native quotes use observation times; Yahoo bars use start times."""
    out = []
    for ts, close in rows:
        t = datetime.fromisoformat(ts)
        if iv != 1:
            t += timedelta(minutes=iv)
        if close and t.strftime("%H:%M") <= "15:30":
            out.append((t.isoformat(timespec="minutes"), close))
    return out


def point_at(points, t):
    """Missing observations are not filled or treated as zero returns."""
    stamp = t.replace(second=0, microsecond=0).isoformat(timespec="minutes")
    return next((c for ts, c in points if ts == stamp), None)


def _price_at(rows, iv, t):
    return point_at(completed_points(rows, iv), t)


MIN_DAY_MINUTES = 100        # 그날 1분봉이 이만큼 있으면 1분봉으로 (한국투자증권으로 채운 지난 날 포함)


def series(conn, code: str, day: str, min_minutes: int = MIN_DAY_MINUTES) -> tuple[int | None, list]:
    """그날 가장 촘촘한 봉: 1분봉(minute_bars) → 야후 5분 → 60분. (간격, [(ts, close)])"""
    rows = conn.execute("SELECT ts, close FROM minute_bars WHERE symbol=? AND ts >= ? AND ts < ? || 'z' ORDER BY ts", (code, day, day)).fetchall()
    if rows and len(rows) >= min_minutes:
        return 1, [tuple(r) for r in rows]
    for iv in sorted(RANGES):
        rows = conn.execute("SELECT ts, close FROM coarse_bars WHERE symbol=? AND interval=? AND ts >= ? AND ts < ? || 'z' ORDER BY ts",
                            (code, iv, day, day)).fetchall()
        if rows:
            return iv, [tuple(r) for r in rows]
    return None, []


def _day_rows(conn, code: str, day: str):
    """(이 종목 간격, 이 종목 봉, 지수 간격, 지수 봉) — 둘의 간격이 다를 수 있음 (지난 날 지수는 1분봉이 없음)."""
    iv, own = series(conn, code, day)
    kv, idx = series(conn, INDEX, day)
    if own and idx:
        return iv, own, kv, idx
    return None, [], None, []


def krx_closes(conn, code: str) -> dict:
    """거래소(KRX) 정규장 종가 {날짜: 가격}. 저장 일봉 close는 네이버 기준(넥스트레이드 시간외 ~20:00 포함 마지막 체결가)이라
    외부 시세(거래소 종가)와 다를 수 있음 → 분석·AI 질문은 이 값을 씀.
    1) 다음 거래일의 기준가(base = 그날 종가 − 전일 대비 = 거래소 전일 종가)
    2) 없으면(마지막 날) 그날 15:30까지의 마지막 1분 시세(네이버 1분봉은 정규장만 저장)
    3) 그래도 없으면 저장 close (지수는 시간외가 없어 close 그대로)"""
    rows = conn.execute("SELECT date, close, base FROM daily_bars WHERE symbol=? ORDER BY date", (code,)).fetchall()
    if code == INDEX:
        return {d: c for d, c, _ in rows}
    out = {}
    for i, (d, c, _) in enumerate(rows):
        nb = rows[i + 1][2] if i + 1 < len(rows) else None
        if nb:
            out[d] = nb
            continue
        m = conn.execute("SELECT close FROM minute_bars WHERE symbol=? AND ts >= ? AND ts < ? "
                         "ORDER BY ts DESC LIMIT 1", (code, d, d + "T15:31")).fetchone()
        out[d] = m[0] if m and m[0] else c
    return out


def reaction_frame(conn, code, day, start, kind, min_minutes=MIN_DAY_MINUTES):
    """Shared observed prices and bases for tables and charts, without interpolation."""
    iv, own = series(conn, code, day, min_minutes)
    kv, idx = series(conn, INDEX, day, min_minutes)
    own, idx = completed_points(own, iv), completed_points(idx, kv)
    own_time = idx_time = None
    if kind in ("open", "late"):
        prev = conn.execute("SELECT date, close FROM daily_bars WHERE symbol=? AND date<? ORDER BY date DESC LIMIT 1",
                            (code, day)).fetchone()
        # 기준 = 전 거래일 거래소 정규장 종가 (넥스트레이드 시간외 마지막 가격이 아님)
        b_own = krx_closes(conn, code).get(prev[0]) if prev else None
        pk = conn.execute("SELECT close FROM daily_bars WHERE symbol=? AND date=?", (INDEX, prev[0])).fetchone() if prev else None
        b_idx = pk[0] if pk else None
        own_time = prev[0] if prev and b_own else None
        idx_time = prev[0] if prev and b_idx else None
    else:
        def before(points, interval):
            candidates = [(ts, p) for ts, p in points if datetime.fromisoformat(ts) < start]
            if not candidates or not interval:
                return None, None
            ts, p = candidates[-1]
            # At most one native interval old; no stale quote carry-forward.
            if (start - datetime.fromisoformat(ts)).total_seconds() > interval * 60:
                return None, None
            return ts, p
        own_time, b_own = before(own, iv)
        idx_time, b_idx = before(idx, kv)
    return {"interval": iv, "kq_interval": kv, "kind": kind,
            "start": start.isoformat(timespec="minutes"), "day": day,
            "base_own_time": own_time, "base_kq_time": idx_time,
            "comparable": bool(own_time and own_time == idx_time),
            "base_lag_minutes": (start-datetime.fromisoformat(own_time)).total_seconds()/60 if own_time and kind == "session" else None,
            "base_basis": "previous_krx_close" if kind in ("open", "late") else "last_completed_before_publication",
            "base_own": b_own, "base_kq": b_idx, "own": own, "kq": idx}


def reaction(code: str, base_ts: str, day: str, prev_close: float | None, prev_index: float | None,
             steps=(30, 60, 120)) -> dict | None:
    """Exact-minute returns; missing stock/index prices remain independently missing.
    Previous-close arguments stay for compatibility; dated closes come from the shared frame.
    """
    t0 = datetime.fromisoformat(base_ts).replace(second=0, microsecond=0)
    open_t = datetime.fromisoformat(day + "T09:00")
    with connect() as conn:
        if t0.date().isoformat() == day and t0.strftime("%H:%M") > "15:00":
            nxt = conn.execute("SELECT MIN(d) FROM (SELECT date d FROM daily_bars WHERE symbol IN (?, ?) AND date>? "
                               "UNION SELECT substr(ts,1,10) FROM minute_bars WHERE symbol IN (?, ?) AND substr(ts,1,10)>?)",
                               (code, INDEX, day, code, INDEX, day)).fetchone()[0]
            if not nxt:
                return None
            day, kind = nxt, "late"
            start = datetime.fromisoformat(day + "T09:00")
        elif t0 < open_t:
            start, kind = open_t, "open"
        elif t0.date().isoformat() == day:
            start, kind = t0, "session"
        else:
            return None
        frame = reaction_frame(conn, code, day, start, kind)
    if not frame["own"]:
        return None
    rows = []
    for m in steps:
        end = start + timedelta(minutes=m)
        if end.strftime("%H:%M") > "15:30":
            break
        p, k = point_at(frame["own"], end), point_at(frame["kq"], end)
        own = p / frame["base_own"] - 1 if p and frame["base_own"] else None
        kq = k / frame["base_kq"] - 1 if k and frame["base_kq"] else None
        if own is not None or kq is not None:
            rows.append({"minutes": m, "end": end.isoformat(timespec="minutes"), "own": own, "kosdaq": kq,
                         "diff": own - kq if own is not None and kq is not None and frame['comparable'] else None})
    return {k: v for k, v in frame.items() if k not in ("own", "kq")} | {"rows": rows}


def day_bars(code: str, day: str) -> tuple[int | None, list[dict]]:
    """그날의 가장 촘촘한 봉 (5분 → 60분). (간격, [{t,o,h,l,c,v}])"""
    with connect() as conn:
        for iv in sorted(RANGES):
            rows = conn.execute("SELECT ts, open, high, low, close, volume FROM coarse_bars WHERE symbol=? AND interval=? "
                                "AND ts >= ? AND ts < ? || 'z' ORDER BY ts", (code, iv, day, day)).fetchall()
            if rows:
                return iv, [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": int(r[5] or 0)} for r in rows]
    return None, []
