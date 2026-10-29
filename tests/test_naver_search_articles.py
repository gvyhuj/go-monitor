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
            _hit("s2", "충북 직업계고 취업 희망 반토막", f"... 여러 종목 ... {NAME} ...", "https://x.example.com/2", "x.example.com"),
        ] if start == 1 else []
    monkeypatch.setattr(newssearch, "search", fake_search)
    assert articles.sync_naver_search(SYM) == 2          # 요약 뒷부분에만 이름이 있어도 받음 (원문 검사가 '이름만 언급'을 거름)
    with db.connect() as conn:
        rows = conn.execute("SELECT id, office, category, source FROM articles ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("s1", "충청타임즈", "언론 분석", "naver_search"),
                                        ("s2", "x.example.com", "언론 분석", "naver_search")]
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


def test_manual_merge_joins_story(tmpdb):
    with db.connect() as conn:
        for aid, title, ts in (("a1", "충북인자위, 직업계고 학부모와 중소기업 탐방", "2026-10-26T15:02"),
                               ("a2", "직업계고 학부모 우수 중소기업 탐방", "2026-10-26T15:30"),
                               ("b1", "청주 고교생 취업 선호 3년 새 급감", "2026-10-26T16:00")):
            conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url) VALUES (?, ?, ?, 'o', ?, 'u')", (aid, SYM, ts, title))
    articles.group_stories(SYM)
    with db.connect() as conn:
        st = dict(conn.execute("SELECT id, story FROM articles").fetchall())
    assert st["a2"] == st["a1"] and st["b1"] == "b1"
    with db.connect() as conn:
        conn.execute("UPDATE articles SET story_with='a2' WHERE id='b1'")
    articles.group_stories(SYM)
    with db.connect() as conn:
        st = dict(conn.execute("SELECT id, story FROM articles").fetchall())
    assert st["b1"] == st["a1"]


def test_manual_merge_moves_whole_story(tmpdb):
    """묶음의 기사 하나를 다른 묶음에 넣으면, 그 기사와 자동으로 같이 묶여 있던 기사도 함께 옮겨짐."""
    with db.connect() as conn:
        for aid, title, ts in (("a1", "충북인자위, 직업계고 학부모와 중소기업 탐방", "2026-10-26T15:02"),
                               ("a2", "직업계고 학부모 우수 중소기업 탐방", "2026-10-26T15:30"),
                               ("b1", "청주 고교생 취업 선호 3년 새 급감", "2026-10-26T16:00"),
                               ("b2", "청주 고교생 취업 선호 3년 새 급감 우려", "2026-10-26T16:10")):
            conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url) VALUES (?, ?, ?, 'o', ?, 'u')", (aid, SYM, ts, title))
        conn.execute("UPDATE articles SET story_with='b2' WHERE id='a1'")     # 6개 매체 묶음의 대표를 다른 묶음에 넣음
    articles.group_stories(SYM)
    with db.connect() as conn:
        st = dict(conn.execute("SELECT id, story FROM articles").fetchall())
    assert st["a1"] == st["a2"] == st["b1"] == st["b2"]


def test_merge_request_end_to_end(tmpdb, monkeypatch):
    """사이트의 '이 묶음에 넣기' 요청 → 묶음 반영 → 목록은 한 줄, 합쳐진 기사 주소로 들어가도 대표 기사가 열림 → 빼기."""
    import logging
    import cloud_job
    monkeypatch.setattr(cloud_job, "load_settings", lambda: {"symbol": SYM})
    monkeypatch.setattr(articles, "COVERAGE_FROM", "2026-01-01")
    with db.connect() as conn:
        for aid, office, title, ts in (("a1", "국제뉴스", "청주상의 충북인자위, 직업계고 학부모와 우수 중소기업 탐방", "2026-10-26T15:02"),
                                       ("a2", "충청타임즈", "충북인자위, 직업계고 학부모와 함께 우수 중소기업 탐방", "2026-10-26T15:10"),
                                       ("b1", "공뉴스", "충북 고교생 취업 선호 2년 새 반토막", "2026-10-26T15:18"),
                                       ("b2", "중부매일", "충북 고교생 취업 선호 3년 새 반토막", "2026-10-26T15:32")):
            conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url, category) VALUES (?, ?, ?, ?, ?, 'u', '언론 분석')",
                         (aid, SYM, ts, office, title))
    articles.group_stories(SYM)
    leads = lambda: {a["id"]: sorted(r["id"] for r in a["related_titles"]) for a in articles.article_rows(SYM)}
    assert leads() == {"a1": ["a2"], "b1": ["b2"]}
    # 공뉴스 기사 화면에서 '청주상의…'(a1)을 이 묶음에 넣기 → a1 묶음 전체가 b1 묶음으로
    cloud_job.apply_inbox({"type": "article_merge", "id": "a1", "into": "b1"}, logging.getLogger())
    assert leads() == {"a1": ["a2", "b1", "b2"]}                  # 대표는 가장 이른 기사
    assert articles.find(SYM, "b1")["id"] == "a1"                 # 보던 기사 주소로 들어가도 열림
    assert articles.find(SYM, "a1")["story_with"] == "b1"           # 대표가 된 합친 기사에도 '빼기'를 띄울 수 있게
    with db.connect() as conn:
        assert conn.execute("SELECT story_with FROM articles WHERE id='a1'").fetchone()[0] == "b1"
    cloud_job.apply_inbox({"type": "article_merge", "id": "a1", "into": None}, logging.getLogger())
    assert leads() == {"a1": ["a2"], "b1": ["b2"]}


def test_merge_several_at_once(tmpdb, monkeypatch):
    """여러 건을 골라 요청 하나로: 모두 한 묶음."""
    import logging
    import cloud_job
    monkeypatch.setattr(cloud_job, "load_settings", lambda: {"symbol": SYM})
    with db.connect() as conn:
        for aid, title, ts in (("t", "충북인자위 직업계고 학부모 탐방", "2026-10-26T15:02"),
                               ("x", "청주 고교생 취업 선호 급감", "2026-10-26T15:18"),
                               ("y", "학부모 40명 생산시설 견학", "2026-10-26T16:00"),
                               ("z", "지역 중소기업 인식 개선 나서", "2026-10-26T17:00")):
            conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url) VALUES (?, ?, ?, 'o', ?, 'u')", (aid, SYM, ts, title))
    articles.group_stories(SYM)
    cloud_job.apply_inbox({"type": "article_merge", "ids": ["x", "y", "z"], "into": "t"}, logging.getLogger())
    with db.connect() as conn:
        st = dict(conn.execute("SELECT id, story FROM articles").fetchall())
    assert st["x"] == st["y"] == st["z"] == st["t"]


def test_bulk_hide_and_kind(tmpdb, monkeypatch):
    """여러 개 고르기: 한 요청으로 여러 기사 삭제 / 구분 바꾸기 (묶음 전체 함께)."""
    import logging
    import cloud_job
    with db.connect() as conn:
        for aid, story in (("p", "p"), ("q", "q"), ("q2", "q"), ("r", "r")):
            conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url, story) VALUES (?, ?, '2026-10-26T10:00', 'o', ?, 'u', ?)",
                         (aid, SYM, aid, story))
    cloud_job.apply_inbox({"type": "hide", "ids": ["p", "q"]}, logging.getLogger())
    cloud_job.apply_inbox({"type": "article_kind", "ids": ["r", "q"], "kind": "pr"}, logging.getLogger())
    with db.connect() as conn:
        rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, hidden, kind_override FROM articles")}
    assert rows == {"p": (1, None), "q": (1, "pr"), "q2": (1, "pr"), "r": (0, "pr")}


def test_merge_both_directions_still_merges(tmpdb):
    """같은 두 기사를 서로 반대 방향으로 합쳐도(A에 B, B에 A) 하나로 묶임 (예전에는 순환이라 아무것도 안 됨)."""
    with db.connect() as conn:
        for aid, title, ts in (("a", "충북인자위 직업계고 학부모 탐방", "2026-10-26T15:02"),
                               ("b", "청주 고교생 취업 선호 급감", "2026-10-26T15:18"),
                               ("c", "지역 중소기업 인식 개선 나서", "2026-10-26T17:00")):
            conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url) VALUES (?, ?, ?, 'o', ?, 'u')", (aid, SYM, ts, title))
        conn.execute("UPDATE articles SET story_with='b' WHERE id='a'")
        conn.execute("UPDATE articles SET story_with='a' WHERE id='b'")
        conn.execute("UPDATE articles SET story_with='b' WHERE id='c'")
    articles.group_stories(SYM)
    with db.connect() as conn:
        st = dict(conn.execute("SELECT id, story FROM articles").fetchall())
    assert st["a"] == st["b"] == st["c"] == "a"              # 묶음 번호는 가장 이른 기사


def test_merge_same_article_into_two_stories_keeps_both(tmpdb, monkeypatch):
    """같은 기사를 다른 묶음에 또 합쳐도 앞서 합친 것이 지워지지 않음 → 세 묶음이 하나로."""
    import logging
    import cloud_job
    monkeypatch.setattr(cloud_job, "load_settings", lambda: {"symbol": SYM})
    with db.connect() as conn:
        for aid, title, ts in (("a", "충북인자위 직업계고 학부모 탐방", "2026-10-26T15:02"),
                               ("b", "청주 고교생 취업 선호 급감", "2026-10-26T15:18"),
                               ("c", "지역 중소기업 인식 개선 나서", "2026-10-26T17:00")):
            conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url) VALUES (?, ?, ?, 'o', ?, 'u')", (aid, SYM, ts, title))
    log = logging.getLogger()
    cloud_job.apply_inbox({"type": "article_merge", "id": "b", "into": "a"}, log)
    cloud_job.apply_inbox({"type": "article_merge", "id": "b", "into": "c"}, log)
    with db.connect() as conn:
        st = dict(conn.execute("SELECT id, story FROM articles").fetchall())
    assert st["a"] == st["b"] == st["c"]
    cloud_job.apply_inbox({"type": "article_merge", "id": "b", "into": None}, log)   # 빼기: b가 들어간 쌍을 모두 지움
    with db.connect() as conn:
        st = dict(conn.execute("SELECT id, story FROM articles").fetchall())
    assert len({st["a"], st["b"], st["c"]}) == 3
