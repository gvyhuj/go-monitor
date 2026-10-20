"""대시보드 장중 화면: 전일 종가."""
from __future__ import annotations

import pandas as pd
import pytest

from core import db
import webserver as ws


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(ws, "load_settings", lambda: {"symbol": "000000"})
    monkeypatch.setattr(ws, "event_rows", lambda *a: [])
    with db.connect() as conn:
        conn.execute("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume) "
                     "VALUES ('000000', '2026-10-20T15:30', 11360, 11360, 11360, 11360, 10)")

    def load_range(sym, a, b):
        if b.date().isoformat() < "2026-10-20":            # 전날(10/19) 분봉: 종가 11,990
            return pd.DataFrame({"ts": [pd.Timestamp("2026-10-19T15:30")], "close": [11990.0]})
        return pd.DataFrame(columns=["ts", "close"])
    monkeypatch.setattr(ws.analysis, "load_range", load_range)
    monkeypatch.setattr(ws, "bars_payload", lambda *a, **k: [])
    return monkeypatch


def test_next_morning_quote_does_not_replace_prev_close(setup):
    # 10/21 장 시작 전: 네이버는 10/20 종가에 등락 0을 돌려줌 → 전일 종가로 쓰면 안 됨
    setup.setattr(ws, "state_json", lambda k: {"price": 11360, "change": 0, "traded_at": "2026-10-21T08:30:00+09:00"})
    assert ws.api_intraday({})["prev_close"] == 11990


def test_same_day_quote_used(setup):
    setup.setattr(ws, "state_json", lambda k: {"price": 11360, "change": -500, "traded_at": "2026-10-20T15:30:00+09:00"})
    assert ws.api_intraday({})["prev_close"] == 11860        # 네이버 기준 전일 종가 사용
