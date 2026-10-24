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


def ticker(code: str) -> str:
    return f"{code}.KQ"


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
    """5분봉·60분봉을 받아 coarse_bars에 더함 (이미 있는 봉은 그대로). 넣은 개수."""
    fetch_fn = fetch_fn or fetch
    added = {}
    for iv in RANGES:
        bars = fetch_fn(code, iv)
        with connect() as conn:
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO coarse_bars(symbol, interval, ts, open, high, low, close, volume) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             [(code, iv, b["ts"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in bars])
            added[iv] = conn.total_changes - before
    return added


def day_bars(code: str, day: str) -> tuple[int | None, list[dict]]:
    """그날의 가장 촘촘한 봉 (5분 → 60분). (간격, [{t,o,h,l,c,v}])"""
    with connect() as conn:
        for iv in sorted(RANGES):
            rows = conn.execute("SELECT ts, open, high, low, close, volume FROM coarse_bars WHERE symbol=? AND interval=? "
                                "AND substr(ts,1,10)=? ORDER BY ts", (code, iv, day)).fetchall()
            if rows:
                return iv, [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": int(r[5] or 0)} for r in rows]
    return None, []
