"""지난 기록을 한국투자증권 Open API로 다시 받기 (실행마다 조금씩, 끝나면 멈춤).

순서: ① 이 종목 일봉 전체 ② KOSDAQ 지수 일봉 전체 ③ 이 종목 지난 날짜 1분봉 (최근 날부터 거슬러, 하루씩 통째로 바꿈).
KOSDAQ 지수의 지난 1분봉은 한투가 주지 않아 기록에 있는 것을 그대로 둠.
진행 상황은 system_state 'kis:backfill' (JSON)에 남김.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import kis
from .db import connect, get_state, set_state
from .macro import KOSDAQ, store_daily_kis

STATE = "kis:backfill"
FIRST_DAY = "2020-01-01"          # 이보다 앞 일봉은 찾지 않음 (상장 전이면 한투가 빈 결과)


def _state() -> dict:
    st = get_state(STATE)
    try:
        return json.loads(st["value"]) if st and st.get("value") else {}
    except ValueError:
        return {}


def _save(s: dict):
    set_state(STATE, json.dumps(s, ensure_ascii=False))


def run(code: str, now: datetime, max_days: int, log=None) -> str | None:
    """이번 실행에서 할 만큼 하고 한 줄 요약 (다 끝났으면 None)."""
    s = _state()
    if s.get("done"):
        return None
    if s.get("pause_until") and now.isoformat(timespec="minutes") < s["pause_until"]:
        return None                       # 한투가 '접속 자리 꽉 참'이라고 해서 쉬는 중 (시세 수집은 그대로)
    say = []
    end = now.date().isoformat()
    if not s.get("daily"):
        rows = kis.daily_range(code, FIRST_DAY, end)
        n = store_daily_kis(code, rows)
        s["daily"] = {"n": n, "from": rows[0]["date"] if rows else None, "to": rows[-1]["date"] if rows else None}
        _save(s)
        say.append(f"일봉 {n}일({s['daily']['from']}~{s['daily']['to']})")
    if not s.get("kosdaq_daily"):
        with connect() as conn:
            first = conn.execute("SELECT MIN(date) FROM daily_bars WHERE symbol=?", (KOSDAQ,)).fetchone()[0] or FIRST_DAY
        rows = kis.daily_range(kis.KOSDAQ_INDEX, min(first, s["daily"].get("from") or first), end, index=True)
        n = store_daily_kis(KOSDAQ, rows)
        s["kosdaq_daily"] = {"n": n, "from": rows[0]["date"] if rows else None, "to": rows[-1]["date"] if rows else None}
        _save(s)
        say.append(f"KOSDAQ 일봉 {n}일({s['kosdaq_daily']['from']}~{s['kosdaq_daily']['to']})")
    # 지난 날짜 1분봉: 오늘 전 거래일을 최근부터 거슬러 (이미 한투로 바꾼 날은 건너뜀)
    with connect() as conn:
        days = [r[0] for r in conn.execute("SELECT date FROM daily_bars WHERE symbol=? AND date<? ORDER BY date DESC",
                                           (code, end))]
    done = set(s.get("minute_days") or [])
    todo = [d for d in days if d not in done]
    got, empty, err = 0, [], None
    s.setdefault("minute_empty", [])
    for d in todo[:max_days]:
        try:
            bars = kis.full_day_bars(code, d)
        except kis.KisError as e:            # 한투 응답 지연 등: 받은 날까지 저장하고 다음 실행에 이어서
            err = str(e)[:120]
            if "SESSION FULL" in err.upper():   # 접속 자리가 꽉 참: 6시간 쉬어 시세 수집 몫을 남김
                s["pause_until"] = (now + timedelta(hours=6)).isoformat(timespec="minutes")
                err += f" → {s['pause_until'][5:16].replace('T', ' ')}까지 쉼"
                _save(s)
            break
        if bars:
            with connect() as conn:
                conn.execute("DELETE FROM minute_bars WHERE symbol=? AND ts >= ? AND ts < ? || 'z'", (code, d, d))
                conn.executemany("INSERT OR REPLACE INTO minute_bars(symbol, ts, open, high, low, close, volume, source) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?, 'kis_backfill')",
                                 [(code, b["ts"], b["open"] or b["close"], b["high"] or b["close"], b["low"] or b["close"],
                                   b["close"], int(b["volume"] or 0)) for b in bars if b.get("close")])
            got += 1
        else:
            empty.append(d)                 # 한투가 주지 않는 날 (너무 오래됨 등): 기록에 있는 것을 그대로 둠
        done.add(d)
        s["minute_days"] = sorted(done)       # 하루마다 진행 상황 저장 (중간에 끊겨도 받은 날은 다시 받지 않음)
        s["minute_empty"] = sorted(set(s["minute_empty"]) | set(empty))
        _save(s)
    left = len([d for d in days if d not in done])
    if todo:
        say.append(f"1분봉 {got}일 받음" + (f", 한투에 없음 {len(empty)}일" if empty else "") + f", 남은 날 {left}일"
                   + (f" (중간에 멈춤: {err}, 다음 실행에 이어서)" if err else ""))
    if not left:
        s["done"] = now.isoformat(timespec="minutes")
        e = s["minute_empty"]
        say.append(f"다시 받기 끝: 1분봉 {len(s['minute_days']) - len(e)}일"
                   + (f" (한투에 없는 날 {len(e)}일: {e[0]}~{e[-1]}, 기록에 있던 값 유지)" if e else "")
                   + ". KOSDAQ 지난 1분봉은 한투가 주지 않아 기록 그대로")
    _save(s)
    return "한투로 지난 기록 다시 받기: " + " · ".join(say) if say else None
