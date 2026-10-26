"""네이버 검색 API로 회사 기사 보충: 제목에 이름이 없는 지역지 기사도 모으고, 종목 뉴스와 같은 기사는 매체 수를 덮어쓰지 않음."""
from __future__ import annotations

import pytest

from core import articles, db, newssearch

NAME = "가나광학"
SYM = "000000"


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(articles, "company_name", lambda: NAME)
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    db.init_db()


def _hit(aid, title, body, orig, office):
    return {"id": aid, "ts": "2026-10-23T10:00", "office": office, "title": title, "body": body,
            "url": orig, "orig": orig}


def test_collects_articles_without_name_in_title(tmpdb, monkeypatch):
    calls = []

    def fake_search(q, start=1, display=100):
        calls.append(start)
        return [
            _hit("s1", "충북인자위, 직업계고 학부모와 함께 우수 중소기업 탐방",
                 f"충북인자위는 23일 직업계고 학부모와 {NAME}을 방문했다.", "https://www.cctimes.kr/news/1", "충청타임즈"),
            _hit("s2", "코스닥 마감 시황", f"... 여러 종목 ... {NAME} ...", "https://x.example.com/2", "x.example.com"),
        ] if start == 1 else []
    monkeypatch.setattr(newssearch, "search", fake_search)
    assert articles.sync_naver_search(SYM) == 1
    with db.connect() as conn:
        rows = conn.execute("SELECT id, office, category, source FROM articles").fetchall()
    assert [tuple(r) for r in rows] == [("s1", "충청타임즈", "언론 분석", "naver_search")]
    assert calls == [1]                                  # 100건이 안 되면 다음 쪽은 받지 않음
    assert articles.sync_naver_search(SYM) == 0          # 10분 안에는 다시 부르지 않음
    assert calls == [1]


def test_does_not_overwrite_stock_news_outlets(tmpdb, monkeypatch):
    with db.connect() as conn:
        conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url, outlets, source) VALUES "
                     "('0010001', ?, '2026-10-23T10:00', '연합뉴스', ?, 'u', 6, 'naver')", (SYM, f"{NAME} 수주"))
    monkeypatch.setattr(newssearch, "search", lambda q, start=1, display=100:
                        [_hit("0010001", f"{NAME} 수주", "", "https://www.yna.co.kr/1", "연합뉴스")] if start == 1 else [])
    assert articles.sync_naver_search(SYM) == 0
    with db.connect() as conn:
        assert conn.execute("SELECT outlets FROM articles").fetchone()[0] == 6


def test_office_name_from_known_url(tmpdb, monkeypatch):
    with db.connect() as conn:
        conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url, source) VALUES "
                     "('g1', ?, '2026-10-20T10:00', '동양일보', 't', 'https://www.dynews.co.kr/news/9', 'google')", (SYM,))
    monkeypatch.setattr(newssearch, "search", lambda q, start=1, display=100:
                        [_hit("s9", f"{NAME} 탐방", "", "https://www.dynews.co.kr/news/10", "dynews.co.kr")] if start == 1 else [])
    articles.sync_naver_search(SYM)
    with db.connect() as conn:
        assert conn.execute("SELECT office FROM articles WHERE id='s9'").fetchone()[0] == "동양일보"


def test_off_without_keys(tmpdb, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID")
    assert articles.sync_naver_search(SYM) == 0
