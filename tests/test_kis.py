from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from core import db, kis
from core.naver import Bar


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "monitor.db")
    db.init_db()
    return tmp_path


def minutes(day, start="09:00", n=381, price=100.0, label_end=False):
    t0 = datetime.fromisoformat(f"{day}T{start}")
    out = []
    for i in range(n):
        t = t0 + timedelta(minutes=i + (1 if label_end else 0))
        out.append(Bar(t.isoformat(timespec="minutes"), price + i, price + i, price + i, price + i, 10))
    return out


def test_parse_rows_keeps_regular_session_of_that_day():
    rows = [{"stck_bsop_date": "20260422", "stck_cntg_hour": "141400", "stck_prpr": "14200", "stck_oprc": "14150",
             "stck_hgpr": "14250", "stck_lwpr": "14100", "cntg_vol": "321"},
            {"stck_bsop_date": "20260421", "stck_cntg_hour": "141400", "stck_prpr": "1"},        # 다른 날
            {"stck_bsop_date": "20260422", "stck_cntg_hour": "085900", "stck_prpr": "1"},        # 장 전
            {"stck_bsop_date": "20260422", "stck_cntg_hour": "100000", "bstp_nmix_prpr": "850.5"}]   # 업종 지수 이름
    got = kis.parse_rows(rows, "20260422")
    assert list(got) == ["141400", "100000"]
    b = got["141400"]
    assert (b.ts, b.open, b.high, b.low, b.close, b.volume) == ("2026-04-22T14:14", 14150, 14250, 14100, 14200, 321)
    assert got["100000"].close == 850.5


def test_day_minutes_pages_backwards_from_close():
    calls = []

    class Fake:
        def get(self, path, tr_id, params):
            calls.append(params["FID_INPUT_HOUR_1"])
            end = datetime.strptime("20260422" + params["FID_INPUT_HOUR_1"], "%Y%m%d%H%M%S")
            rows = []
            for i in range(120):
                t = end - timedelta(minutes=i)
                if t.strftime("%H%M%S") < "090000":
                    break
                rows.append({"stck_bsop_date": "20260422", "stck_cntg_hour": t.strftime("%H%M%S"), "stck_prpr": "100"})
            return {"output2": rows}
    bars = kis.day_minutes(Fake(), "X", "2026-04-22", pause=0)
    assert calls == ["153000", "133000", "113000", "093000"]
    assert len(bars) == 391 and bars[0].ts == "2026-04-22T09:00" and bars[-1].ts == "2026-04-22T15:30"


def test_best_offset_matches_naver_bar_start_times():
    naver = {b.ts: b.close for b in minutes("2026-10-01")}
    assert kis.best_offset(minutes("2026-10-01"), naver) == 0
    assert kis.best_offset(minutes("2026-10-01", label_end=True), naver) == -1    # 봉 끝 시각으로 적은 경우
    assert kis.best_offset(minutes("2026-10-01", n=50), naver) is None           # 겹치는 봉이 너무 적음
    assert kis.shift(minutes("2026-10-01", n=2, label_end=True), -1)[0].ts == "2026-10-01T09:00"


def test_backfill_fills_missing_days_once_and_resumes(database, monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_BACKFILL", "1")
    monkeypatch.setattr(kis.Client, "_token", lambda self: "t")
    days = ["2026-04-21", "2026-04-22", "2026-10-01"]
    with db.connect() as conn:
        conn.executemany("INSERT INTO daily_bars(symbol, date, close) VALUES ('X', ?, 100)", [(d,) for d in days])
        conn.executemany("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'naver_today')",
                         [(s, b.ts, b.open, b.high, b.low, b.close, b.volume) for s in ("X", "KOSDAQ") for b in minutes("2026-10-01")])
    asked = []

    def fake_day(cl, code, day, market="J", pause=0):
        asked.append((code, day, market))
        return minutes(day, label_end=True)          # 한국투자증권이 봉 끝 시각으로 준다고 가정
    monkeypatch.setattr(kis, "day_minutes", fake_day)
    now = datetime(2026, 10, 27, 18, 0)
    st = kis.backfill("X", now, budget_sec=60)
    assert st["done"] and st["offset"] == -1 and st["index"] == -1 and st["filled"] == 2
    assert (st["first"], st["last"]) == ("2026-04-21", "2026-04-22")
    with db.connect() as conn:
        first = conn.execute("SELECT MIN(ts), COUNT(*) FROM minute_bars WHERE symbol='X' AND ts LIKE '2026-04-22%'").fetchone()
        kq = conn.execute("SELECT COUNT(*) FROM minute_bars WHERE symbol='KOSDAQ' AND source='kis'").fetchone()[0]
        naver_kept = conn.execute("SELECT COUNT(*) FROM minute_bars WHERE symbol='X' AND ts LIKE '2026-10-01%' AND source='naver_today'").fetchone()[0]
    assert tuple(first) == ("2026-04-22T09:00", 381) and kq == 2 * 381 and naver_kept == 381
    n = len(asked)
    assert kis.backfill("X", now)["skipped"] and len(asked) == n          # 다 채우면 다시 안 부름
    monkeypatch.delenv("KIS_APP_KEY")
    with db.connect() as conn:
        conn.execute("DELETE FROM system_state WHERE key=?", (kis.STATE,))
    assert kis.backfill("X", now)["skipped"]                              # 열쇠가 없으면 아무것도 안 함


def test_backfill_stops_on_api_error_and_retries_that_day(database, monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_BACKFILL", "1")
    monkeypatch.setattr(kis.Client, "_token", lambda self: "t")
    with db.connect() as conn:
        conn.executemany("INSERT INTO daily_bars(symbol, date, close) VALUES ('X', ?, 100)", [("2026-04-21",), ("2026-04-22",)])

    def boom(cl, code, day, market="J", pause=0):
        if day == "2026-04-22":
            raise kis.KisError("EGW00201 초당 거래건수를 초과하였습니다.")
        return minutes(day)
    monkeypatch.setattr(kis, "day_minutes", boom)
    st = kis.backfill("X", datetime(2026, 10, 27, 18, 0))
    assert not st.get("done") and st["cursor"] == "2026-04-21" and "2026-04-22" in st["last_error"]
    assert json.loads(db.get_state(kis.STATE)["value"])["cursor"] == "2026-04-21"


def test_backfill_skips_index_when_past_days_give_one_bar(database, monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_BACKFILL", "1")
    monkeypatch.setattr(kis.Client, "_token", lambda self: "t")
    with db.connect() as conn:
        conn.executemany("INSERT INTO daily_bars(symbol, date, close) VALUES ('X', ?, 100)", [("2026-04-22",), ("2026-10-01",)])
        conn.executemany("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'naver_today')",
                         [(s, b.ts, b.open, b.high, b.low, b.close, b.volume) for s in ("X", "KOSDAQ") for b in minutes("2026-10-01")])
    monkeypatch.setattr(kis, "day_minutes", lambda cl, code, day, market="J", pause=0:
                        minutes(day, n=1) if market == "U" else minutes(day))
    st = kis.backfill("X", datetime(2026, 10, 27, 18, 0))
    assert st["done"] and st["index"] is None and "지수" in st["index_error"]
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM minute_bars WHERE symbol='KOSDAQ' AND source='kis'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM minute_bars WHERE symbol='X' AND source='kis'").fetchone()[0] == 381


def jump_day(day, source):
    """10:00~10:20 사이 100 → 107로 오르고 그대로 유지하는 하루."""
    out = []
    t0 = datetime.fromisoformat(f"{day}T09:00")
    for i in range(381):
        t = t0 + timedelta(minutes=i)
        p = 100.0 if t.hour < 10 else min(107.0, 100.0 + (t.minute + (t.hour - 10) * 60) * 0.35)
        out.append(("X", t.isoformat(timespec="minutes"), p, p, p, p, 1000, source))
    return out


def test_rescan_finds_intraday_moves_only_before_naver_days(database):
    with db.connect() as conn:
        conn.executemany("INSERT INTO daily_bars(symbol, date, close, base) VALUES ('X', ?, 107, 100)",
                         [("2026-04-22",), ("2026-10-01",)])
        conn.executemany("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume, source) VALUES (?,?,?,?,?,?,?,?)",
                         jump_day("2026-04-22", "kis") + jump_day("2026-10-01", "naver_today"))
    db.set_state(kis.STATE, json.dumps({"done": True}))
    n = kis.rescan_if_due("X")
    with db.connect() as conn:
        days = [r[0] for r in conn.execute("SELECT DISTINCT substr(start_ts,1,10) FROM events WHERE symbol='X' AND event_type='intraday'")]
    assert n >= 1 and days == ["2026-04-22"]                 # 네이버 기록이 있는 10/1은 그대로
    assert kis.rescan_if_due("X") is None                     # 한 번만


def test_trim_removes_only_observation_on_backfilled_days(database):
    with db.connect() as conn:
        conn.execute("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume, source) VALUES ('X','2026-09-23T09:00',1,1,1,1,1,'naver_today')")
        rows = [("2026-04-22T10:00", "intraday", "관찰"), ("2026-04-22T11:00", "intraday", "일반"), ("2026-04-23T10:00", "intraday", "대형"),
                ("2026-04-22T09:00", "daily", "관찰"), ("2026-10-01T10:00", "intraday", "관찰")]
        conn.executemany("INSERT INTO events(symbol, start_ts, last_ts, event_type, direction, tier) VALUES ('X', ?, ?, ?, 'up', ?)",
                         [(t, t, k, tier) for t, k, tier in rows])
    out = kis.trim_backfilled_observations("X")
    assert out["deleted"] == 1 and out["by_tier"] == {"관찰": 1, "일반": 1, "대형": 1}
    with db.connect() as conn:
        left = sorted((r[0], r[1]) for r in conn.execute("SELECT start_ts, tier FROM events"))
    assert left == [("2026-04-22T09:00", "관찰"), ("2026-04-22T11:00", "일반"), ("2026-04-23T10:00", "대형"), ("2026-10-01T10:00", "관찰")]
    assert kis.trim_backfilled_observations("X") is None                 # 한 번만


def test_rescan_skips_observation_moves(database):
    """채운 지난 날에 +4%만 오른 날은 '관찰'이라 기록하지 않음."""
    def small_jump(day, source):
        return [(r[0], r[1], *[100 + (x - 100) * 4 / 7 for x in r[2:6]], r[6], r[7]) for r in jump_day(day, source)]
    with db.connect() as conn:
        conn.executemany("INSERT INTO daily_bars(symbol, date, close, base) VALUES ('X', ?, 104, 100)", [("2026-04-22",), ("2026-10-01",)])
        conn.executemany("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume, source) VALUES (?,?,?,?,?,?,?,?)",
                         small_jump("2026-04-22", "kis") + jump_day("2026-10-01", "naver_today"))
    db.set_state(kis.STATE, json.dumps({"done": True}))
    assert kis.rescan_if_due("X") == 0
