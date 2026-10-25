"""기사 주제 자동 분류와 기자명 읽기 (원문 글귀 형식은 실제 네이버 기사에서 본 것)."""
from __future__ import annotations

import json

import pytest

from core import reporters, topics


@pytest.mark.parametrize("title,primary,also", [
    ("그린광학, 이스라엘 방산기업과 47억 공급계약…ZnS 소재 추가 수주", "수주·계약", "방산"),
    ("충북TP, 그린광학 AI 코팅기술 고도화 지원", "정부·기관 지원", "기술·제품"),
    ("그린광학, 3분기 영업이익 흑자 전환", "실적", None),
    ("[특징주] 그린광학, 우주항공 테마 강세", "우주·위성", "주가·시황"),
    ("그린광학 대표 인터뷰", "기타", None),
])
def test_topic_rules(title, primary, also):
    got, all_ = topics.classify(title, "", topics.DEFAULT_TOPICS)
    assert got == primary
    if also:
        assert also in all_


def test_topic_falls_back_to_summary_start():
    assert topics.classify("그린광학 대표 인터뷰", "올해 방산 수출 확대 계획을 밝혔다", topics.DEFAULT_TOPICS)[0] == "방산"


@pytest.mark.parametrize("page,name", [
    ('<strong>삼성전자 정규장 상승 마감뒤 NXT 종가 0.47%↓</strong>[이데일리 안유리 기자] SK하이닉스 주가가', "안유리"),
    ("(서울=연합뉴스) 홍길동 기자 = 그린광학이 23일", "홍길동"),
    ("본문 끝. 김철수 기자 kim@example.co.kr 저작권자", "김철수"),
    ("본문 끝. 김철수 기자(kimcs@news.com)", "김철수"),
    ("이 발표 후 주가는 올랐습니다. 곽우진 기자가 보도합니다. [리포트]", "곽우진"),
    ("환율은 1320원에서 움직였습니다. <span>SBS</span> <span>Biz</span> 윤지혜입니다.", "윤지혜"),
    ('<em class="media_end_head_journalist_name">박영희 기자</em>', "박영희"),
    ("기자 이름이 없는 기사 본문입니다.", None),
    ("[관련 기자] 이번 기자 회견", None),
])
def test_reporter_parse(page, name):
    assert reporters.parse(page) == name


def test_fill_reads_each_article_once_and_skips_google(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(reporters, "MAX_TRIES", 3)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(reporters._time, "sleep", lambda s: None)
    with db.connect() as conn:
        for i, url in enumerate(["https://n.news.naver.com/mnews/article/018/1", "https://news.google.com/rss/articles/x",
                                 "https://n.news.naver.com/mnews/article/018/2", "https://n.news.naver.com/mnews/article/018/3"]):
            conn.execute("INSERT INTO articles(id,symbol,ts,title,url) VALUES (?,?,?,?,?)", (f"a{i}", "X", f"2026-10-2{i}T10:00", "t", url))
    pages = {"https://n.news.naver.com/mnews/article/018/1": "[이데일리 안유리 기자] 본문",
             "https://n.news.naver.com/mnews/article/018/2": "기자명 없음"}
    opened = []

    def fetch(u):
        opened.append(u)
        if u not in pages:
            raise OSError("안 열림")
        return pages[u]
    assert reporters.fill("X", limit=2, fetch=fetch, resolve=lambda u: None) == 0          # 최신 2개(3번 못 열림, 2번 이름 없음)
    assert reporters.fill("X", limit=2, fetch=fetch, resolve=lambda u: None) == 1          # 구글(원래 주소 못 찾음 → 열지 않음) + 남은 1번
    for _ in range(4):                                                                     # 못 연 3번은 다음 실행에 다시, 3번까지
        assert reporters.fill("X", limit=2, fetch=fetch, resolve=lambda u: None) == 0
    assert opened == ["https://n.news.naver.com/mnews/article/018/3", "https://n.news.naver.com/mnews/article/018/2",
                      "https://n.news.naver.com/mnews/article/018/1",
                      "https://n.news.naver.com/mnews/article/018/3", "https://n.news.naver.com/mnews/article/018/3"]
    with db.connect() as conn:
        got = dict(conn.execute("SELECT id, reporter FROM articles").fetchall())
    assert got == {"a0": "안유리", "a1": None, "a2": None, "a3": None}


def test_google_link_is_resolved_to_original_and_old_skipped_rows_retried(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(reporters._time, "sleep", lambda s: None)
    with db.connect() as conn:   # 예전 코드가 구글이라 건너뛰고 '확인함'으로 남긴 기사
        conn.execute("INSERT INTO articles(id,symbol,ts,title,url,reporter_at) VALUES "
                     "('g','X','2026-10-20T10:00','t','https://news.google.com/rss/articles/CBMiabc?oc=5','2026-10-23T10:00')")
    orig = "https://www.etnews.com/20261020000006"
    assert reporters.fill("X", fetch=lambda u: "홍길동 기자 hong@etnews.com" if u == orig else "",
                          resolve=lambda u: orig) == 1
    with db.connect() as conn:
        url, name, done = conn.execute("SELECT url, reporter, resolved_at FROM articles").fetchone()
    assert (url, name) == (orig, "홍길동") and done
    assert reporters.fill("X", fetch=lambda u: 1 / 0, resolve=lambda u: 1 / 0) == 0   # 다시 열지 않음


def test_google_batchexecute_response_parsing():
    from core import gnews
    inner = json.dumps(["garturlres", "https://www.cctimes.kr/news/articleView.html?idxno=1", 1])
    text = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"]]) + "\n\n"
    assert gnews.parse_batch(text) == "https://www.cctimes.kr/news/articleView.html?idxno=1"
    assert gnews.parse_batch("garbage") is None
    assert gnews.is_google("https://news.google.com/rss/articles/x") and not gnews.is_google("https://n.news.naver.com/a")


def test_google_article_time_is_taken_from_original_page(tmp_path, monkeypatch):
    from core import articles, db
    from core.article_integrity import time_quality
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(reporters._time, "sleep", lambda s: None)
    with db.connect() as conn:
        conn.execute("INSERT INTO articles(id,symbol,ts,title,url,source) VALUES "
                     "('g','X','2026-10-20T17:00','t','https://news.google.com/rss/articles/CBMiabc','google')")
    orig = "https://www.todayenergy.kr/news/articleView.html?idxno=304388"
    page = '<meta property="article:published_time" content="2026-10-20T09:26:32+09:00"> 임자성 기자 lim@todayenergy.kr'
    reporters.fill("X", fetch=lambda u: page, resolve=lambda u: orig)
    with db.connect() as conn:
        row = dict(zip(("ts", "time_from"), conn.execute("SELECT ts, time_from FROM articles").fetchone()))
    assert row == {"ts": "2026-10-20T09:26", "time_from": "page"}
    assert time_quality({"source": "google", **row}) == "reported"
    assert time_quality({"source": "google", "ts": "2026-10-20T17:00"}) == "check_time"


@pytest.mark.parametrize("page, want", [
    ('<meta content="2026-10-20T11:00:00+09:00" property="article:published_time">', "2026-10-20T11:00"),
    ('"datePublished": "2026-06-19T01:40:09Z"', "2026-06-19T10:40"),
    ("입력 2026.10.19 18:43 수정 2026.10.20", "2026-10-19T18:43"),
    ("시각 없음", None),
])
def test_parse_time_formats(page, want):
    assert reporters.parse_time(page) == want


@pytest.mark.parametrize("page, want", [
    ('<meta name="author" content="박기영 기자">', "박기영"),
    ('<meta property="dable:author" content="엄재천 기자" />', "엄재천"),
    ('<meta name="author" content="머니투데이">', None),
])
def test_reporter_from_meta_author(page, want):
    assert reporters.parse(page) == want


def test_office_name_in_meta_author_is_skipped():
    # 뉴시스 원문: meta author는 '뉴시스', 본문은 [서울=뉴시스] 김경택 기자 = 
    page = ('<meta property="article:author" content="뉴시스"/>'
            "<p>[서울=뉴시스] 김경택 기자 = 초정밀 광학 전문기업</p>")
    assert reporters.parse(page, "뉴시스") == "김경택"


def test_naver_version_is_read_first(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(reporters._time, "sleep", lambda s: None)
    orig, nv = "https://www.newsis.com/view/NISX1", "https://n.news.naver.com/mnews/article/003/1"
    with db.connect() as conn:
        conn.execute("INSERT INTO articles(id,symbol,ts,office,title,url,source) VALUES ('n1','X','2026-10-20T10:00','뉴시스','t',?,'naver')", (orig,))
    pages = {orig: '<meta property="article:author" content="뉴시스"/>', nv: 'media_end_head_journalist_name">김경택 기자'}
    opened = []
    got = reporters.fill("X", fetch=lambda u: (opened.append(u), pages[u])[1], resolve=lambda u: None, naver=lambda t, u: nv)
    assert got == 1 and opened == [nv]                     # 네이버판에서 읽었으면 원문은 열지 않음
    with db.connect() as conn:
        assert conn.execute("SELECT reporter FROM articles").fetchone()[0] == "김경택"
