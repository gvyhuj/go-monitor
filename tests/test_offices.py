"""매체 이름 맞추기: 주소로만 남은 매체를 한글 이름으로 (11/23 기록: kjdaily.com·digitaltoday.co.kr가 주소로 남음)."""
from __future__ import annotations

from core import offices, reporters


def test_english_site_name_falls_back_to_korean_title():
    page = '<meta property="og:site_name" content="Digital Today"><title>디지털투데이 (DigitalToday)</title>'
    assert offices.name_from_page(page) == "디지털투데이"


def test_korean_site_name_is_used_first():
    assert offices.name_from_page('<meta property="og:site_name" content="광주매일신문"><title>x</title>') == "광주매일신문"


def test_failed_host_is_tried_again_next_day(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO articles(id,symbol,ts,title,url,office) VALUES ('a','X','2026-11-01T10:00','t','u','kjdaily.com')")
    answers = iter([None, "광주매일신문"])          # 첫날은 응답이 늦어 실패, 다음 날은 읽힘
    monkeypatch.setattr(offices, "_fetch_name", lambda h: next(answers))
    monkeypatch.setattr(offices, "_today", lambda: "2026-11-23")
    offices.normalize("X")
    offices.normalize("X")                           # 같은 날은 다시 묻지 않음 (answers가 남아 있어야 함)
    monkeypatch.setattr(offices, "_today", lambda: "2026-11-24")
    offices.normalize("X")
    with db.connect() as conn:
        assert conn.execute("SELECT office FROM articles").fetchone()[0] == "광주매일신문"


def test_office_check_counts_names_left_as_addresses(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.connect() as conn:
        for aid, office in [("a0", "뉴시스"), ("a1", "kjdaily.com"), ("a2", None)]:
            conn.execute("INSERT INTO articles(id,symbol,ts,title,url,office,source) VALUES (?,?,?,?,?,?,?)",
                         (aid, "X", "2026-11-01T10:00", "t", "u", office, "naver"))
    assert reporters.office_missing("X") == (3, {"naver": 1}, {"kjdaily.com": 1})      # 어느 매체 주소인지 (사이트 주소만)
