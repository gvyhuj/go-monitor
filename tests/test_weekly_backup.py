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


def test_weekly_mark_is_inside_the_record_handed_to_next_run(fresh_db, monkeypatch, tmp_path):
    """다음 실행으로 넘기는 기록을 저장하는 순간 '주간 백업 했음' 표시가 이미 들어 있어야 함 (아니면 실행마다 다시 올림)."""
    import cloud_job
    from core import eventstudy, history, sitebuild
    from core.db import get_state
    monkeypatch.setattr(cloud_job, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(cloud_job, "WEEKLY_DIR", tmp_path / "weekly")
    monkeypatch.setattr(cloud_job, "BACKUP_DIR", tmp_path / "backup")
    monkeypatch.setattr(cloud_job, "SITE_DIR", tmp_path / "site")
    monkeypatch.setattr(cloud_job, "DB_PATH", fresh_db / "monitor.db")
    monkeypatch.setenv("GO_SITE_PASSWORD", "pw")
    monkeypatch.delenv("GO_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(sitebuild, "refresh_items", lambda *a, **k: [])
    monkeypatch.setattr(eventstudy, "validate_primary_exports", lambda items: {})
    monkeypatch.setattr(sitebuild, "build", lambda *a, **k: 0)
    monkeypatch.setattr(history, "build_admin", lambda *a, **k: None)
    seen = {}
    real = sitebuild.backup_db

    def spy(db, out, pw):
        if out == cloud_job.STATE_DIR:
            seen.setdefault("mark_when_carried", (get_state("site:last_weekly_backup") or {}).get("value"))
        return real(db, out, pw)
    monkeypatch.setattr(sitebuild, "backup_db", spy)

    class Log:
        def info(self, *a): pass
    r = cloud_job.build_static_site(datetime(2026, 10, 23, 12, 50), Log())
    assert r["weekly"] == "2026-10-23"
    assert seen["mark_when_carried"] == "2026-10-23"
    assert (tmp_path / "weekly" / "monitor-2026-10-23.db.enc").exists()
