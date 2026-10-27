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


def _price_at(rows: list[tuple], iv: int, t: datetime) -> float | None:
    """t 시점에 끝난 마지막 봉의 종가 (봉 시작 + 간격 <= t)."""
    best = None
    for ts, close in rows:
        if datetime.fromisoformat(ts) + timedelta(minutes=iv) <= t:
            best = close
        else:
            break
    return best


MIN_DAY_MINUTES = 100        # 그날 1분봉이 이만큼 있으면 1분봉으로 (한국투자증권으로 채운 지난 날 포함)


def series(conn, code: str, day: str, min_minutes: int = MIN_DAY_MINUTES) -> tuple[int | None, list]:
    """그날 가장 촘촘한 봉: 1분봉(minute_bars) → 야후 5분 → 60분. (간격, [(ts, close)])"""
    rows = conn.execute("SELECT ts, close FROM minute_bars WHERE symbol=? AND substr(ts,1,10)=? ORDER BY ts", (code, day)).fetchall()
    if rows and len(rows) >= min_minutes:
        return 1, [tuple(r) for r in rows]
    for iv in sorted(RANGES):
        rows = conn.execute("SELECT ts, close FROM coarse_bars WHERE symbol=? AND interval=? AND substr(ts,1,10)=? ORDER BY ts",
                            (code, iv, day)).fetchall()
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


def reaction(code: str, base_ts: str, day: str, prev_close: float | None, prev_index: float | None,
             steps=(30, 60, 120)) -> dict | None:
    """발표 직후 반응의 단순 숫자 (통계 계산이 안 되는 기사): 이 종목·코스닥·차이.
    봉은 그날 가장 촘촘한 것 — 1분봉(네이버·한국투자증권) → 야후 5분봉 → 60분봉, 이 종목과 코스닥이 따로 (지난 날 코스닥은 1분봉이 없음).
    장 시작 전 공개면 전날 종가부터 장 시작 뒤 각 시점까지.
    15시 넘어 공개면 그날 종가(마지막 봉)부터 다음 거래일 장 시작 뒤 각 시점까지."""
    t0 = datetime.fromisoformat(base_ts)
    open_t = datetime.fromisoformat(day + "T09:00")
    with connect() as conn:
        iv, own, kv, idx = _day_rows(conn, code, day)
        late = t0.date().isoformat() == day and t0.time() > datetime.strptime("15:00", "%H:%M").time()
        if late:
            nxt = conn.execute("SELECT MIN(d) FROM (SELECT substr(ts,1,10) d FROM coarse_bars WHERE symbol=? AND substr(ts,1,10)>? "
                               "UNION SELECT substr(ts,1,10) FROM minute_bars WHERE symbol=? AND substr(ts,1,10)>?)",
                               (code, day, code, day)).fetchone()[0]
            if not own or not nxt:
                return None
            b_own, b_idx = own[-1][1], idx[-1][1]          # 그날 마지막 봉 종가
            day = nxt
            iv, own, kv, idx = _day_rows(conn, code, day)
    if not own:
        return None
    if late:
        start, kind = datetime.fromisoformat(day + "T09:00"), "late"
    elif t0 < open_t:
        start, b_own, b_idx, kind = open_t, prev_close, prev_index, "open"
    elif t0.date().isoformat() == day:
        start, kind = t0, "session"
        b_own, b_idx = _price_at(own, iv, t0), _price_at(idx, kv, t0)
        if b_own is None:           # 장 시작 직후 공개: 첫 봉 시가 대신 전날 종가
            b_own, b_idx = prev_close, prev_index
    else:
        return None
    if not b_own or not b_idx:
        return None
    rows = []
    for m in steps:
        if m < iv:
            continue
        end = start + timedelta(minutes=m)
        if end.time() > datetime.strptime("15:30", "%H:%M").time():
            break
        p_own, p_idx = _price_at(own, iv, end), _price_at(idx, kv, end)
        if p_own is None or p_idx is None:
            continue
        o, k = p_own / b_own - 1, p_idx / b_idx - 1
        rows.append({"minutes": m, "end": end.isoformat(timespec="minutes"), "own": o, "kosdaq": k, "diff": o - k})
    return {"interval": iv, "kq_interval": kv, "kind": kind, "day": day, "start": start.isoformat(timespec="minutes"),
            "rows": rows} if rows else None


def day_bars(code: str, day: str) -> tuple[int | None, list[dict]]:
    """그날의 가장 촘촘한 봉 (5분 → 60분). (간격, [{t,o,h,l,c,v}])"""
    with connect() as conn:
        for iv in sorted(RANGES):
            rows = conn.execute("SELECT ts, open, high, low, close, volume FROM coarse_bars WHERE symbol=? AND interval=? "
                                "AND substr(ts,1,10)=? ORDER BY ts", (code, iv, day)).fetchall()
            if rows:
                return iv, [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": int(r[5] or 0)} for r in rows]
    return None, []
