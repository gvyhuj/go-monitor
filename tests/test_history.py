from __future__ import annotations

import json
from datetime import datetime

import pytest

from core import db, history, sitebuild

ADMIN_PASSWORD = "admin-only-test-password-2026"


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "monitor.db")
    db.init_db()
    return tmp_path


def detail(value=0.01):
    return {"article": {"title": "비공개 과거 기사 제목", "ts": "2026-10-20T10:00"},
            "effect": {"measure": {"value": value}, "verdict": "기록된 판정"},
            "bars": [{"secret_chart": 123}], "confounders": []}


def test_history_preserves_versions_and_skips_unchanged_results(database):
    archive = history.History(ADMIN_PASSWORD)
    assert archive.record("article:one", detail(), "2026-10-20T10:30")
    assert not archive.record("article:one", detail(), "2026-10-20T10:35")
    assert archive.record("article:one", detail(0.02), "2026-10-20T11:00")
    assert archive.record("article:one", detail(0.02), "2026-10-21T10:00", version="새 계산판")
    records = archive.records()["article:one"]
    assert len(records) == 3 and records[0]["data"]["effect"]["measure"]["value"] == 0.01
    assert records[-1]["version"] == "새 계산판"
    assert "bars" not in records[0]["data"]


def test_history_content_is_encrypted_inside_database(database):
    archive = history.History(ADMIN_PASSWORD)
    archive.record("article:private", detail(), "2026-10-20T10:30")
    with db.connect() as connection:
        row = connection.execute("SELECT entity_digest,result_digest,payload FROM analysis_history").fetchone()
    assert b"article:private" not in row[2]
    assert "비공개 과거 기사 제목".encode() not in db.DB_PATH.read_bytes()
    assert "article:private" not in row[0]
    assert history.History(ADMIN_PASSWORD).records() == archive.records()


def test_wrong_or_shared_password_never_replaces_old_history(database):
    archive = history.History(ADMIN_PASSWORD)
    archive.record("article:one", detail(), "2026-10-20T10:30")
    with pytest.raises(ValueError):
        history.History("another-strong-admin-password")
    with pytest.raises(ValueError):
        history.History("short")
    with pytest.raises(ValueError):
        history.History(ADMIN_PASSWORD, (ADMIN_PASSWORD,))
    assert len(archive.records()["article:one"]) == 1


def test_existing_cache_is_seeded_only_once(database):
    archive = history.History(ADMIN_PASSWORD)
    raw = json.dumps({"one": detail()})
    history.capture(archive, "article_details", raw, "2026-10-20T10:00", seed=True)
    history.capture(archive, "article_details", raw, "2026-10-20T10:00", seed=True)
    assert len(archive.records()["article:one"]) == 1
    assert "버전 미상" in archive.records()["article:one"][0]["version"]
    history.capture(archive, "article_details", raw, "2026-10-20T11:00")
    history.capture(archive, "article_details", raw, "2026-10-20T10:00", seed=True)
    assert len(archive.records()["article:one"]) == 2


def test_admin_files_require_admin_password_and_do_not_enter_public_data(database):
    archive = history.History(ADMIN_PASSWORD)
    archive.record("article:one", detail(), "2026-10-20T10:30")
    output = database / "site"
    sitebuild.build(output, "public-site-password", b"0123456789abcdef", [("live", '{"latest": true}', False)])
    history.build_admin(output, ADMIN_PASSWORD, archive)
    config = json.loads((output / "admin/config.js").read_text()[len("window.GO_ADMIN="):-1])
    import base64

    admin_key = sitebuild.derive_key(ADMIN_PASSWORD, base64.b64decode(config["salt"]))
    public_key = sitebuild.derive_key("public-site-password", base64.b64decode(config["salt"]))
    encrypted = (output / "admin/index.bin").read_bytes()
    with pytest.raises(Exception):
        sitebuild.decrypt(public_key, encrypted)
    index = json.loads(sitebuild.decrypt(admin_key, encrypted))
    assert index[0]["label"] == "비공개 과거 기사 제목"
    records = json.loads(sitebuild.decrypt(admin_key, (output / "admin" / f"{index[0]['file']}.bin").read_bytes()))
    assert len(records) == 1
    for path in (output / "d").glob("*.bin"):
        assert "비공개 과거 기사 제목" not in sitebuild.decrypt(sitebuild.derive_key("public-site-password", b"0123456789abcdef"), path.read_bytes()).decode()
    assert ADMIN_PASSWORD not in (output / "admin/config.js").read_text()


def test_admin_disabled_does_not_export_history(database):
    output = database / "site"
    history.build_admin(output, "", None)
    assert not list((output / "admin").glob("*.bin"))
    assert (output / "admin/config.js").read_text() == 'window.GO_ADMIN={"enabled": false};'


def test_refresh_archives_previous_and_latest_results_without_adding_public_history(database, monkeypatch):
    import webserver
    from core import cloud

    archive = history.History(ADMIN_PASSWORD)
    with db.connect() as connection:
        connection.execute(sitebuild._TABLE)
        connection.execute("INSERT INTO site_items VALUES (?,?,0,?)",
                           ("article_details", json.dumps({"one": detail()}), "2026-10-20T10:00"))
    monkeypatch.setattr(webserver, "api_intraday", lambda query: {"dates": []})
    monkeypatch.setattr(webserver, "api_articles", lambda query: {"items": [{"id": "one"}]})
    monkeypatch.setattr(webserver, "api_article_detail", lambda query, identity: detail(0.02))
    monkeypatch.setattr(webserver, "api_disclosures", lambda query: {"items": []})
    monkeypatch.setattr(cloud, "_live_item", lambda: ("live", "{}", False))
    items = sitebuild.refresh_items(datetime(2026, 10, 20, 17), history=archive)
    records = archive.records()["article:one"]
    assert len(records) == 2
    assert records[0]["data"]["effect"]["measure"]["value"] == 0.01
    assert records[1]["data"]["effect"]["measure"]["value"] == 0.02
    assert not any("history" in key or "admin" in key for key, _, _ in items)
    sitebuild.refresh_items(datetime(2026, 10, 20, 17, 30), history=archive)
    assert len(archive.records()["article:one"]) == 2
