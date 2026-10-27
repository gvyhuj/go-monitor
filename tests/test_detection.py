"""감지·알림 회귀 테스트.

10/12처럼 '천천히 오래 빠진 날'을 만든 가짜 분봉으로 확인합니다. 공개 저장소라 실제 가격 대신
전일 종가를 10,000원으로 둔 비율만 씁니다 (회사를 알아볼 수 없게).

    pip install pytest && python -m pytest tests
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from core import alerts, db, detector

SYM = "TEST"
PREV = 10_000.0


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    # 알림 동작(한 번만, 회복 뒤 한 번 더 등)을 보는 시험이라 기준 숫자는 예전 값(±3·5·10%)으로 고정
    real = detector.load_settings
    monkeypatch.setattr(detector, "load_settings",
                        lambda: {**real(), "event_watch_pct": 3.0, "event_move_pct": 5.0, "event_big_pct": 10.0})
    db.set_state(detector.TIERS_KEY, "test")         # 단계 다시 매기기는 따로 시험
    sent = []
    monkeypatch.setattr(alerts, "_post_issue", lambda title, body: sent.append(title) or 1)
    return sent


def _path(points):
    """[(HH:MM, 가격), ...] 사이를 1분 단위 직선으로 이음."""
    out = []
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        a, b = datetime.strptime(t0, "%H:%M"), datetime.strptime(t1, "%H:%M")
        n = int((b - a).total_seconds() // 60)
        out += [(a + timedelta(minutes=i), p0 + (p1 - p0) * i / n) for i in range(n)]
    t, p = points[-1]
    return out + [(datetime.strptime(t, "%H:%M"), p)]


# 10/12 모양: 09:00~09:20 -4%, 09:55까지 조금 반등, 10:29까지 -7%로 더 빠지고 그 근처에 머무름
SLOW_DROP = [("09:00", 9990), ("09:20", 9600), ("09:55", 9700), ("10:29", 9300), ("11:48", 9320)]


def _put(day: str, path, prev_day: str = None):
    with db.connect() as conn:
        if prev_day:
            conn.execute("INSERT OR REPLACE INTO daily_bars(symbol, date, open, high, low, close, volume) "
                         "VALUES (?,?,?,?,?,?,?)", (SYM, prev_day, PREV, PREV, PREV, PREV, 100000))
        for t, p in path:
            ts = f"{day}T{t:%H:%M}"
            conn.execute("INSERT OR REPLACE INTO minute_bars(symbol, ts, open, high, low, close, volume, source) "
                         "VALUES (?,?,?,?,?,?,?,?)", (SYM, ts, p, p, p, round(p), 1000, "test"))


def _bars(day, path):
    return pd.DataFrame([{"symbol": SYM, "ts": pd.Timestamp(f"{day}T{t:%H:%M}"), "open": p, "high": p, "low": p,
                          "close": float(round(p)), "volume": 1000, "source": "test"} for t, p in path])


def _events(day):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT event_type, direction, tier, status, start_ts, last_ts FROM events WHERE symbol=? "
            "AND substr(start_ts,1,10)=? ORDER BY start_ts", (SYM, day))]


S = {"event_move_pct": 5.0, "event_watch_pct": 3.0, "event_big_pct": 10.0, "event_window_minutes": 30,
     "event_hold_minutes": 5, "event_min_value_won": 5e7, "skip_detection_from": "15:20"}


def test_slow_drop_is_one_intraday_event():
    day = "2026-10-12"
    evs = detector.intraday_events(_bars(day, _path(SLOW_DROP)), PREV, S, day_over=False)
    downs = [e for e in evs if e["direction"] == "down"]
    assert len(downs) == 1, downs            # 09:00 조각과 09:55 조각이 한 건으로 묶여야 함
    assert downs[0]["start_ts"].endswith("09:00")
    assert downs[0]["peak_return_5m"] <= -0.065


def test_running_daily_catches_slow_drop():
    day = "2026-10-12"
    ev = detector.running_daily_event(_bars(day, _path(SLOW_DROP)), PREV, S)
    assert ev and ev["direction"] == "down" and ev["tier"] == "일반" and ev["status"] == "open"


def test_one_alert_for_one_drop(tmpdb):
    today = date.today().isoformat()
    _put(today, _path(SLOW_DROP), prev_day="2000-01-01")
    detector.detect_symbol(SYM, now=datetime.fromisoformat(f"{today}T11:49"))
    alerts.init_if_needed("other")           # 처음 켤 때 표시만 해 두는 단계를 건너뜀
    with db.connect() as conn:
        conn.execute("DELETE FROM system_state WHERE key='alerts:sent'")
    db.set_state("alerts:sent", json.dumps(["init"]))
    assert alerts.send_new(SYM) == 1
    assert len(tmpdb) == 1 and tmpdb[0].startswith("급락")
    detector.detect_symbol(SYM, now=datetime.fromisoformat(f"{today}T11:52"))
    assert alerts.send_new(SYM) == 0         # 같은 하락으로는 다시 보내지 않음


def test_recover_then_drop_again_alerts_twice(tmpdb):
    today = date.today().isoformat()
    db.set_state("alerts:sent", json.dumps(["init"]))
    _put(today, _path(SLOW_DROP), prev_day="2000-01-01")
    detector.detect_symbol(SYM, now=datetime.fromisoformat(f"{today}T11:49"))
    assert alerts.send_new(SYM) == 1
    # 12:30까지 -1%로 회복 → 임시 하루 기록 삭제, 회복 시각 저장
    _put(today, _path([("11:48", 9320), ("12:00", 9900), ("12:30", 9900)]))
    detector.detect_symbol(SYM, now=datetime.fromisoformat(f"{today}T12:31"))
    assert not [e for e in _events(today) if e["event_type"] == "daily"]
    assert db.get_state(detector.recover_key(SYM, today))["value"] == "12:30"
    assert alerts.send_new(SYM) == 0         # 회복만으로는 알림 없음
    # 다시 -6%로 빠져 유지 → 한 번 더
    _put(today, _path([("12:30", 9900), ("12:40", 9400), ("13:10", 9400)]))
    detector.detect_symbol(SYM, now=datetime.fromisoformat(f"{today}T13:11"))
    assert alerts.send_new(SYM) == 1
    assert len(tmpdb) == 2


def test_prev_close_uses_naver_base(tmpdb):
    day = "2026-10-12"
    _put(day, [], prev_day="2026-10-10")
    assert detector._prev_close(SYM, day) == PREV
    # 배당락 날: 네이버 기준가 9,800원 (현재가 9,500 − 전일 대비 −300)
    db.set_state(f"quote:{SYM}", json.dumps({"price": 9500, "change": -300, "traded_at": f"{day}T11:00:00+09:00"}))
    assert detector._prev_close(SYM, day) == 9800
    assert detector._prev_close(SYM, "2026-10-13") == PREV   # 다른 날에는 쓰지 않음


def _daily(closes, start=date(2026, 9, 1)):
    with db.connect() as conn:
        d = start
        for c in closes:
            while d.weekday() >= 5:
                d += timedelta(days=1)
            conn.execute("INSERT OR REPLACE INTO daily_bars(symbol, date, open, high, low, close, volume) "
                         "VALUES (?,?,?,?,?,?,?)", (SYM, d.isoformat(), c, c, c, c, 1000))
            d += timedelta(days=1)


def test_krx_daily_rescan_fixes_numbers_and_drops_below_threshold(tmpdb):
    # 넥스트레이드가 섞인 예전 일봉으로 만든 '급등 +6.97%' 기록 → 거래소 종가로는 +2.41%라 지워짐,
    # 거래소 종가로 -11.25%인 날은 같은 번호에 숫자만 고쳐 씀. 붙여넣은 원인은 그대로 남음
    from core import manual
    s = {"event_move_pct": 5.0, "event_watch_pct": 3.0, "event_big_pct": 10.0}
    rows = [("2026-10-14", 12620), ("2026-10-15", 11200), ("2026-10-16", 11470)]
    with db.connect() as conn:
        for d, c in rows:
            conn.execute("INSERT INTO daily_bars(symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
                         (SYM, d, c, c, c, c, 1000))
        for d, dr, r, tier in (("2026-10-15", "down", -0.1349, "대형"), ("2026-10-16", "up", 0.0697, "일반")):
            conn.execute("INSERT INTO events(symbol, start_ts, last_ts, event_type, direction, peak_return_5m, "
                         "return_5m, peak_ts, status, tier) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (SYM, f"{d}T09:00", f"{d}T15:30", "daily", dr, r, r, f"{d}T15:30", "closed", tier))
    manual.save({"type": "cause", "date": "2026-10-16", "direction": "up", "scope": "day", "body": "원인 글"},
                datetime(2026, 10, 16, 16, 0))
    assert detector._rescan_daily_krx(SYM, s, datetime(2026, 10, 21, 10, 0), sync=False)
    with db.connect() as conn:
        evs = {r["start_ts"][:10]: dict(r) for r in conn.execute("SELECT * FROM events WHERE event_type='daily'")}
        kept = conn.execute("SELECT COUNT(*) FROM manual_causes WHERE date='2026-10-16'").fetchone()[0]
    assert set(evs) == {"2026-10-15"}
    assert abs(evs["2026-10-15"]["peak_return_5m"] - (11200 / 12620 - 1)) < 1e-9 and evs["2026-10-15"]["tier"] == "대형"
    assert kept == 1
    assert not detector._rescan_daily_krx(SYM, s, datetime(2026, 10, 21, 10, 0), sync=False)   # 한 번만


def test_small_trade_value_is_still_detected():
    # 거래대금 기준을 없앰: 분당 1주만 거래돼도(거래대금이 아주 작아도) 움직임이 기준을 넘으면 기록
    day = "2026-10-12"
    bars = _bars(day, _path(SLOW_DROP))
    bars["volume"] = 1
    evs = detector.intraday_events(bars, PREV, S, day_over=False)
    assert [e for e in evs if e["direction"] == "down"]
    assert detector.running_daily_event(bars, PREV, S) is not None


def test_retier_events_once_with_new_thresholds(tmpdb, monkeypatch):
    """기준을 바꾸면 한 번: 관찰 밑은 지우고 나머지는 새 단계로 (직접 기록한 요청은 그대로)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM system_state WHERE key=?", (detector.TIERS_KEY,))
        conn.executemany("INSERT INTO events(symbol, start_ts, last_ts, event_type, direction, tier, peak_return_5m) "
                         "VALUES ('S', '2026-10-01T10:00', '2026-10-01T10:30', 'intraday', 'down', ?, ?)",
                         [("관찰", -0.04), ("일반", -0.06), ("대형", -0.12), ("대형", 0.16), ("요청", -0.01)])
    monkeypatch.setattr(detector, "load_settings", lambda: {"event_watch_pct": 5.0, "event_move_pct": 10.0, "event_big_pct": 15.0})
    out = detector.retier_events("S")
    assert out == {"deleted": 1, "changed": 2, "kept": 1}
    with db.connect() as conn:
        tiers = sorted((round(r[0], 2), r[1]) for r in conn.execute("SELECT peak_return_5m, tier FROM events"))
    assert tiers == [(-0.12, "일반"), (-0.06, "관찰"), (-0.01, "요청"), (0.16, "대형")]
    assert detector.retier_events("S") is None
