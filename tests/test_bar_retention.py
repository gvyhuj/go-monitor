from __future__ import annotations

import pytest

from core import db, feed


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "monitor.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume) "
                     "VALUES ('000000', '2025-11-20T09:01', 1, 1, 1, 1, 10)")
    return tmp_path


def count_bars():
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM minute_bars").fetchone()[0]


def test_zero_keep_days_keeps_old_minute_bars_forever(database):
    feed.purge_old_bars(0)
    assert count_bars() == 1


def test_positive_keep_days_still_purges_old_minute_bars(database):
    feed.purge_old_bars(120)
    assert count_bars() == 0


def test_settings_keep_minute_bars_forever():
    import json
    from pathlib import Path
    settings = json.loads((Path(__file__).resolve().parents[1] / "settings.json").read_text(encoding="utf-8"))
    assert settings["keep_bars_days"] == 0
