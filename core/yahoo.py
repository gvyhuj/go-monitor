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


def _day_rows(conn, code: str, day: str):
    for iv in sorted(RANGES):
        own = conn.execute("SELECT ts, close FROM coarse_bars WHERE symbol=? AND interval=? AND substr(ts,1,10)=? ORDER BY ts",
                           (code, iv, day)).fetchall()
        idx = conn.execute("SELECT ts, close FROM coarse_bars WHERE symbol=? AND interval=? AND substr(ts,1,10)=? ORDER BY ts",
                           (INDEX, iv, day)).fetchall()
        if own and idx:
            return iv, own, idx
    return None, [], []


def reaction(code: str, base_ts: str, day: str, prev_close: float | None, prev_index: float | None,
             steps=(30, 60, 120)) -> dict | None:
    """1분봉이 없는 지난 기사의 발표 직후 반응 (야후 5분봉, 없으면 60분봉): 이 종목·코스닥·차이.
    장 시작 전 공개면 전날 종가부터 장 시작 뒤 각 시점까지.
    15시 넘어 공개면 그날 종가(마지막 봉)부터 다음 거래일 장 시작 뒤 각 시점까지."""
    t0 = datetime.fromisoformat(base_ts)
    open_t = datetime.fromisoformat(day + "T09:00")
    with connect() as conn:
        iv, own, idx = _day_rows(conn, code, day)
        late = t0.date().isoformat() == day and t0.time() > datetime.strptime("15:00", "%H:%M").time()
        if late:
            nxt = conn.execute("SELECT MIN(substr(ts,1,10)) FROM coarse_bars WHERE symbol=? AND substr(ts,1,10)>?",
                               (code, day)).fetchone()[0]
            if not own or not nxt:
                return None
            b_own, b_idx = own[-1][1], idx[-1][1]          # 그날 마지막 봉 종가
            day = nxt
            iv, own, idx = _day_rows(conn, code, day)
    if not own:
        return None
    if late:
        start, kind = datetime.fromisoformat(day + "T09:00"), "late"
    elif t0 < open_t:
        start, b_own, b_idx, kind = open_t, prev_close, prev_index, "open"
    elif t0.date().isoformat() == day:
        start, kind = t0, "session"
        b_own, b_idx = _price_at(own, iv, t0), _price_at(idx, iv, t0)
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
        p_own, p_idx = _price_at(own, iv, end), _price_at(idx, iv, end)
        if p_own is None or p_idx is None:
            continue
        o, k = p_own / b_own - 1, p_idx / b_idx - 1
        rows.append({"minutes": m, "end": end.isoformat(timespec="minutes"), "own": o, "kosdaq": k, "diff": o - k})
    return {"interval": iv, "kind": kind, "day": day, "start": start.isoformat(timespec="minutes"), "rows": rows} if rows else None


def day_bars(code: str, day: str) -> tuple[int | None, list[dict]]:
    """그날의 가장 촘촘한 봉 (5분 → 60분). (간격, [{t,o,h,l,c,v}])"""
    with connect() as conn:
        for iv in sorted(RANGES):
            rows = conn.execute("SELECT ts, open, high, low, close, volume FROM coarse_bars WHERE symbol=? AND interval=? "
                                "AND substr(ts,1,10)=? ORDER BY ts", (code, iv, day)).fetchall()
            if rows:
                return iv, [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": int(r[5] or 0)} for r in rows]
    return None, []
