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
