"""야후 5분봉·60분봉: 응답 읽기, 따로 저장, 1분봉 없는 날만 달력 차트로."""
from __future__ import annotations

import json

import pytest

from core import db, yahoo


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def raw(times, closes):
    return json.dumps({"chart": {"result": [{"timestamp": times, "indicators": {"quote": [{
        "open": closes, "high": closes, "low": closes, "close": closes, "volume": [10] * len(closes)}]}}]}}).encode()


def test_parse_keeps_session_bars_in_korean_time_and_skips_empty():
    # 2026-08-14 09:00 KST = 00:00 UTC
    t0 = 1786665600
    bars = yahoo.parse(raw([t0, t0 + 300, t0 - 3600], [100.0, None, 99.0]))
    assert [b["ts"] for b in bars] == ["2026-08-14T09:00"]          # 빈 봉, 장 밖(08:00) 봉은 뺌


def test_sync_and_coarse_api_only_for_days_without_minute_bars(database, monkeypatch):
    import webserver
    monkeypatch.setattr(webserver, "load_settings", lambda: {"symbol": "X"})
    fake = {5: [{"ts": "2026-08-14T09:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 5},
                {"ts": "2026-10-20T09:00", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 5}],
            60: [{"ts": "2026-03-16T09:00", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 5}]}
    assert yahoo.sync("X", fetch_fn=lambda code, iv: fake[iv]) == {5: 2, 60: 1}
    assert yahoo.sync("X", fetch_fn=lambda code, iv: fake[iv]) == {5: 0, 60: 0}      # 이미 있는 봉은 그대로
    with db.connect() as conn:
        conn.execute("INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume) VALUES ('X','2026-10-20T09:01',1,1,1,1,1)")
        for d, c in (("2026-03-13", 10), ("2026-03-16", 11), ("2026-08-13", 20), ("2026-08-14", 21)):
            conn.execute("INSERT INTO daily_bars(symbol,date,open,high,low,close,volume) VALUES ('X',?,?,?,?,?,1)", (d, c, c, c, c))
    days = webserver.api_coarse({})["days"]
    assert sorted(days) == ["2026-03-16", "2026-08-14"]                 # 1분봉이 있는 10/20은 뺌
    assert days["2026-08-14"]["interval"] == 5 and days["2026-08-14"]["prev_close"] == 20
    assert days["2026-03-16"]["interval"] == 60 and days["2026-03-16"]["bars"][0]["t"] == "2026-03-16T09:00"
    # 마지막 봉 뒤 15:30에 거래소 종가를 붙여 차트 끝 = 그날 등락 (1/12: 60분봉 끝 27,200 → 종가 27,550)
    last = days["2026-03-16"]["bars"][-1]
    assert last["t"] == "2026-03-16T15:30" and last["c"] == 11 and last["close_point"]


def test_past_article_reaction_from_coarse_bars(database):
    # 5분봉: 10:00~10:05 봉 종가 100 → 10:30 끝 봉 103 / 코스닥 1000 → 1010
    with db.connect() as conn:
        for sym, rows in (("X", [("2026-08-14T09:55", 100), ("2026-08-14T10:25", 103), ("2026-08-14T10:55", 104)]),
                          ("KOSDAQ", [("2026-08-14T09:55", 1000), ("2026-08-14T10:25", 1010), ("2026-08-14T10:55", 1005)])):
            for ts, c in rows:
                conn.execute("INSERT INTO coarse_bars(symbol,interval,ts,open,high,low,close,volume) VALUES (?,5,?,?,?,?,?,1)",
                             (sym, ts, c, c, c, c))
    r = yahoo.reaction("X", "2026-08-14T10:00", "2026-08-14", 95, 990)
    assert r["interval"] == 5 and r["kind"] == "session"
    thirty = r["rows"][0]
    assert thirty["minutes"] == 30 and thirty["own"] == pytest.approx(0.03) and thirty["kosdaq"] == pytest.approx(0.01)
    assert thirty["diff"] == pytest.approx(0.02)
    before_open = yahoo.reaction("X", "2026-08-14T07:30", "2026-08-14", 95, 990)
    assert before_open["kind"] == "open"                                              # 전날 종가부터
    assert before_open["rows"][0]["minutes"] == 60 and before_open["rows"][0]["own"] == pytest.approx(100 / 95 - 1)   # 09:30에 끝난 봉 없음 → +60분부터
    assert yahoo.reaction("X", "2026-08-14T15:10", "2026-08-14", 95, 990) is None      # 15시 넘어 공개
    assert yahoo.reaction("X", "2026-08-15T10:00", "2026-08-15", 95, 990) is None      # 봉 없는 날


def test_reaction_chart_bars_and_base(database):
    import webserver
    with db.connect() as conn:
        conn.executemany("INSERT INTO daily_bars(symbol, date, close) VALUES (?, ?, ?)",
                         [("X", "2026-08-20", 1000), ("KOSDAQ", "2026-08-20", 800)])
        for sym, c0 in (("X", 1000), ("KOSDAQ", 800)):
            conn.executemany("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, 1)",
                             [(sym, f"2026-08-21T10:{m:02d}", c0 + m, c0 + m, c0 + m, c0 + m) for m in range(0, 60)])
    e = {"reaction_day": "2026-08-21"}
    rc = webserver._reaction_chart("X", e, {"start": "2026-08-21T10:20", "kind": "session"}, None)
    assert rc["interval"] == 1 and rc["base_own"] == 1019 and rc["base_kq"] == 819     # 10:19 봉이 10:20에 끝남
    assert rc["own"][0]["t"] == "2026-08-21T10:00" and len(rc["kq"]) == 60
    rc = webserver._reaction_chart("X", e, {"start": "2026-08-21T09:00", "kind": "open"}, None)
    assert rc["base_own"] == 1000 and rc["base_kq"] == 800                               # 장 전 공개: 전날 종가
    with db.connect() as conn:
        conn.executemany("INSERT INTO coarse_bars(symbol, interval, ts, close) VALUES (?, 5, ?, ?)",
                         [("Y", "2026-08-21T10:00", 50), ("Y", "2026-08-21T10:05", 51), ("KOSDAQ", "2026-08-21T10:00", 9)])
    rc = webserver._reaction_chart("Y", e, None, {"start": "2026-08-21T10:05", "kind": "session", "interval": 5})
    assert rc["interval"] == 5 and rc["base_own"] == 50 and [b["c"] for b in rc["own"]] == [50, 51]
