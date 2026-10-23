"""수집 현황: 출처마다 마지막 성공·실패 시각을 남기고, 오래 실패 중인 출처를 찾음.

monitor.Collector._safe가 부를 때마다 system_state 'collect:<이름>'에 기록합니다.
'확인 필요' = 마지막 실패가 마지막 성공보다 나중이고, 성공이 장중 1시간·장 밖 6시간보다 오래됨.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .db import connect, get_state, set_state

PREFIX = "collect:"
# 알림을 보낼 만큼 중요한 출처 (데이터베이스의 뼈대)
KEY_SOURCES = ("이 종목 기사", "공시", "일봉", "투자자별 순매수")


def _now() -> str:
    from .feed import kst_now           # 실행 환경(GitHub은 UTC)과 관계없이 한국 시각
    return kst_now().isoformat(timespec="seconds")


def mark(name: str, ok: bool, error: Exception | str | None = None, at: str | None = None):
    key = PREFIX + name
    st = get_state(key)
    try:
        cur = json.loads(st["value"]) if st and st.get("value") else {}
    except (ValueError, TypeError):
        cur = {}
    at = at or _now()
    if ok:
        cur["last_ok"] = at
    else:
        cur["last_err"] = at
        cur["error"] = str(error or "")[:300]
    set_state(key, json.dumps(cur, ensure_ascii=False))


def limit(now: datetime, market: bool) -> timedelta:
    return timedelta(hours=1) if market else timedelta(hours=6)


def snapshot(now: datetime | None = None, market: bool = False) -> list[dict]:
    if now is None:
        from .feed import kst_now
        now = kst_now()
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM system_state WHERE key LIKE ?", (PREFIX + "%",)).fetchall()
    out = []
    for key, value in rows:
        try:
            v = json.loads(value)
        except (ValueError, TypeError):
            continue
        ok, err = v.get("last_ok"), v.get("last_err")
        failing = bool(err and (not ok or err > ok))
        stale = failing and (not ok or now - datetime.fromisoformat(ok) > limit(now, market))
        out.append({"name": key[len(PREFIX):], "last_ok": ok, "last_err": err,
                    "error": v.get("error") if failing else None, "failing": failing, "stale": stale,
                    "key": key[len(PREFIX):] in KEY_SOURCES})
    out.sort(key=lambda r: (not r["stale"], not r["key"], r["name"]))
    return out


def stored(symbol: str) -> dict:
    """쌓인 양 (기간과 건수)."""
    with connect() as conn:
        one = lambda q, *a: conn.execute(q, a).fetchone()
        daily = one("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_bars WHERE symbol=?", symbol)
        minute = one("SELECT MIN(ts), MAX(ts), COUNT(*) FROM minute_bars WHERE symbol=?", symbol)
        arts = one("SELECT MIN(ts), MAX(ts), COUNT(*) FROM articles WHERE symbol=? AND hidden=0", symbol)
        discs = one("SELECT MIN(date), MAX(date), COUNT(*) FROM disclosures WHERE symbol=?", symbol)
        rep = one("SELECT COUNT(*) FROM articles WHERE symbol=? AND hidden=0 AND reporter IS NOT NULL", symbol)
    pack = lambda r: {"from": r[0], "to": r[1], "n": r[2]}
    backup = get_state("backup:last")
    try:
        b = json.loads(backup["value"]) if backup and backup.get("value") else None
    except (ValueError, TypeError):
        b = None
    return {"daily": pack(daily), "minute": pack(minute), "articles": pack(arts), "disclosures": pack(discs),
            "reporters": rep[0], "backup": b}
