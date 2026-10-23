"""주간 백업(7일마다, 날짜가 붙은 파일, 복원 가능)과 달력용 일별 등락."""
from __future__ import annotations

from datetime import datetime

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "monitor.db")
    db.init_db()
    return tmp_path


def test_weekly_backup_every_seven_days(fresh_db):
    import cloud_job
    from core.db import set_state
    assert cloud_job.weekly_backup_due(datetime(2026, 10, 23, 9)) == "2026-10-23"
    set_state("site:last_weekly_backup", "2026-10-23")
    assert cloud_job.weekly_backup_due(datetime(2026, 10, 29, 23)) is None
    assert cloud_job.weekly_backup_due(datetime(2026, 10, 30, 0, 5)) == "2026-10-30"


def test_weekly_backup_file_is_dated_and_restorable(tmp_path):
    from core import sitebuild
    db_file = tmp_path / "x.db"
    db_file.write_bytes(b"records")
    sitebuild.backup_db(db_file, tmp_path / "weekly", "key")
    (tmp_path / "weekly" / "monitor.db.enc").rename(tmp_path / "weekly" / "monitor-2026-10-23.db.enc")
    assert sitebuild.restore_db(tmp_path / "weekly", tmp_path / "back.db", "key")
    assert (tmp_path / "back.db").read_bytes() == b"records"


def test_daily_feed_for_calendar(fresh_db, monkeypatch):
    import webserver
    from core.db import connect
    monkeypatch.setattr(webserver, "load_settings", lambda: {"symbol": "X"})
    with connect() as conn:
        for sym, rows in (("X", [("2026-10-20", 100, 10), ("2026-10-21", 110, 20), ("2026-10-22", 99, 30)]),
                          ("KOSDAQ", [("2026-10-20", 800, 0), ("2026-10-21", 808, 0)])):
            for d, c, v in rows:
                conn.execute("INSERT INTO daily_bars(symbol,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                             (sym, d, c, c, c, c, v))
    days = webserver.api_daily({})["days"]
    assert [d["date"] for d in days] == ["2026-10-20", "2026-10-21", "2026-10-22"]
    assert days[0]["change"] is None and days[1]["change"] == pytest.approx(0.10) and days[2]["change"] == pytest.approx(-0.10)
    assert days[1]["kosdaq"] == pytest.approx(0.01) and days[2]["kosdaq"] is None and days[2]["volume"] == 30
