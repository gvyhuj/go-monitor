"""주간 백업 시점·파일 이름, 메일 알림 (GitHub 앱 알림은 보내지 않음)."""
from __future__ import annotations

import smtplib
from datetime import datetime

import pytest

MAIL = {"ALERT_EMAIL_TO": "a@x.com, b@x.com", "SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "app-pw",
        "GITHUB_REPOSITORY": "o/r"}


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


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, context=None, timeout=None):
        assert (host, port) == ("smtp.gmail.com", 465)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        assert (user, password) == ("me@gmail.com", "app-pw")

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_alert_goes_by_email_only(monkeypatch):
    from core import alerts
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    for k, v in MAIL.items():
        monkeypatch.setenv(k, v)
    assert not hasattr(alerts, "_post_issue")                 # GitHub 이슈(앱 알림)는 더 이상 만들지 않음
    alerts._notify("급등 +8.7% (10/23 10:12)", alerts._body("주가가 움직였습니다.", 5))
    msg = FakeSMTP.sent[0]
    assert msg["Subject"] == "[주가 감시] 급등 +8.7% (10/23 10:12)" and msg["To"] == "a@x.com, b@x.com"
    text = msg.get_content()
    assert text.startswith("주가가 움직였습니다.") and "https://o.github.io/r/#/events/5" in text and "@" not in text


def test_without_mail_settings_alert_is_not_marked_sent(monkeypatch):
    from core import alerts
    for k in MAIL:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(alerts.AlertError):
        alerts._notify("t", "b")

    def broken(*a, **k):
        raise smtplib.SMTPAuthenticationError(535, b"bad")
    for k, v in MAIL.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(alerts, "_send_email", broken)
    with pytest.raises(ValueError):                             # 기존 호출부가 잡는 오류 → 다음 실행에서 다시 시도
        alerts._notify("t", "b")


def test_test_mail_sent_once_after_setup(fresh_db, monkeypatch):
    from core import alerts
    sent = []
    monkeypatch.setattr(alerts, "_send_email", lambda t, b: sent.append(t))
    for k in MAIL:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("GO_ALERT_TEST", raising=False)
    assert not alerts.send_test()
    for k, v in MAIL.items():
        monkeypatch.setenv(k, v)
    assert alerts.send_test() and not alerts.send_test()
    monkeypatch.setenv("GO_ALERT_TEST", "1")
    assert alerts.send_test()
    assert len(sent) == 2
