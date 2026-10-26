"""막은 매체·이름만 언급한 기사는 지우고 다시 모으지 않음 (예: 방산주 시황 기사 끝 종목 나열에만 이름이 나온 기사)."""
from __future__ import annotations

import pytest

from core import articles, db, relevance

NAME = "가나광학"

LIST_ONLY = ('<meta name="description" content="19일 방위산업 관련주가 약세를 나타내고 있다.">'
             "<p>한화에어로스페이스는 2.27% 올랐다.</p><p>부품·소재 관련주에서는 삼양컴텍, " + NAME + ", 제노코 등이 하락.</p>"
             "<p>케이앤에스아이앤씨, " + NAME + ", 휴니드 등이 5% 안팎 하락.</p>")
ABOUT = ('<meta property="og:description" content="' + NAME + '이 23일 47억 규모 공급계약을 맺었다고 밝혔다.">'
         "<p>본문</p>")


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(articles, "company_name", lambda: NAME)
    db.init_db()


def _item(aid, title, office="이데일리", url="https://news.example.com/1", body=""):
    return {"id": aid, "ts": "2026-08-19T15:23", "office": office, "title": title, "body": body, "url": url}


def test_name_only_rule():
    assert relevance.name_only("방산주 희비 뚜렷", LIST_ONLY, NAME)
    assert relevance.name_only("방산주 희비 뚜렷", ABOUT, NAME)                     # 요약(첫머리)에만 이름 → 원문 횟수로 판단
    assert relevance.name_only("[부고] 홍길동 씨 별세", f'<meta property="og:description" content="… 홍철수({NAME} 이사) 장인상">'
                               f"<p>홍철수({NAME} 이사) 장인상=15일 …</p>", NAME)
    assert not relevance.name_only(f"{NAME}·센서뷰 급등", LIST_ONLY, NAME)          # 제목에 이름
    assert not relevance.name_only("방산주", "<p>" + (NAME + " ") * 3 + "</p>", NAME)  # 본문에 여러 번


def test_blocked_office_is_never_saved_and_old_rows_are_purged(tmpdb):
    with db.connect() as conn:
        conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url) VALUES ('old', '000', '2026-08-19T15:23', "
                     "'핀포인트뉴스', 't', 'https://www.pinpointnews.co.kr/news/articleView.html?idxno=478691')")
    assert relevance.purge_blocked("000") == 1
    assert relevance.blocked("Investing.com 한국어", "https://news.google.com/rss/articles/x")
    assert relevance.blocked("", "https://kr.investing.com/news/stock-market-news/article-2070219")
    n = articles._save("000", [_item("a1", f"{NAME} 공급계약", office="핀포인트 뉴스"),
                               _item("a2", f"{NAME} 공급계약", url="https://www.pinpointnews.co.kr/x"),
                               _item("a3", f"{NAME} 공급계약")], "naver")
    assert n == 1
    with db.connect() as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM articles")] == ["a3"]


def test_name_only_article_is_dropped_and_not_collected_again(tmpdb):
    articles._save("000", [_item("x", "방산주 희비 뚜렷", url="https://news.example.com/list"),
                           _item("y", "방산 관련주 동향", url="https://news.example.com/about"),
                           _item("z", f"{NAME} 공급계약", url="https://news.example.com/title")], "naver")
    pages = {"https://news.example.com/list": LIST_ONLY, "https://news.example.com/about": ABOUT}
    opened = []
    fetch = lambda u: (opened.append(u), pages[u])[1]
    assert relevance.check("000", NAME, fetch=fetch) == 2
    assert "https://news.example.com/title" not in opened        # 제목에 이름이 있으면 열지 않음
    with db.connect() as conn:
        assert sorted(r[0] for r in conn.execute("SELECT id FROM articles")) == ["z"]
    assert articles._save("000", [_item("x", "방산주 희비 뚜렷", url="https://news.example.com/list")], "naver") == 0
    assert relevance.check("000", NAME, fetch=fetch) == 0          # 이미 살펴본 기사는 다시 열지 않음
    assert len(opened) == 2


def test_obituary_and_personnel_titles_are_noise():
    for t in ("[부고] 홍길동(전 시의원)씨 별세", "[인사] 산업통상부", "홍길동 모친상", "[게시판] 충북 소식"):
        assert articles.classify(t, f"… {NAME} 이사 …", NAME)[0] == "단순 언급"


def test_unreadable_page_keeps_article(tmpdb):
    articles._save("000", [_item("x", "방산주 희비 뚜렷")], "naver")
    def fail(u):
        raise OSError("timeout")
    assert relevance.check("000", NAME, fetch=fail) == 0
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1


def test_title_listed_for_removal_is_purged(tmpdb):
    with db.connect() as conn:
        conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url) VALUES ('g1', '000', '2026-08-21T16:00', "
                     "'Investing.com 한국어', '[테마시황] 스페이스X(SPCX) 보호예수 해제 충격파…국내 우주항공산업 테마까지 덮쳤다', 'https://kr.investing.com/x')")
    assert relevance.purge_blocked("000") == 1
    assert articles._save("000", [_item("g1", "[테마시황] 스페이스X(SPCX) 보호예수 해제 충격파…")], "google") == 0


def test_noise_category_is_never_saved_and_old_rows_are_purged(tmpdb):
    with db.connect() as conn:
        conn.execute("INSERT INTO articles(id, symbol, ts, office, title, url, category) VALUES ('m1', '000', '2026-08-19T15:23', "
                     "'이데일리', '특징주 모음', 'https://news.example.com/m1', '시황·특징주')")
    assert relevance.purge_blocked("000") == 1
    n = articles._save("000", [_item("s1", f"[특징주] {NAME}, 상한가"), _item("s2", "오늘의 특징주 모음"),
                               _item("s3", f"{NAME} 공급계약")], "naver")
    assert n == 1
    with db.connect() as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM articles")] == ["s3"]
    assert articles._save("000", [_item("s2", "오늘의 특징주 모음")], "naver") == 0
