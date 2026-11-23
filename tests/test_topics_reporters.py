"""기사 주제 자동 분류와 기자명 읽기 (원문 글귀 형식은 실제 네이버 기사에서 본 것)."""
from __future__ import annotations

import json

import pytest

from core import reporters, topics


@pytest.mark.parametrize("title,primary,also", [
    ("가나광학, 이스라엘 방산기업과 47억 공급계약…ZnS 소재 추가 수주", "수주·계약", "방산"),
    ("충북TP, 가나광학 AI 코팅기술 고도화 지원", "정부·기관 지원", "기술·제품"),
    ("가나광학, 3분기 영업이익 흑자 전환", "실적", None),
    ("[특징주] 가나광학, 우주항공 테마 강세", "우주·위성", "주가·시황"),
    ("가나광학 대표 인터뷰", "기타", None),
])
def test_topic_rules(title, primary, also):
    got, all_ = topics.classify(title, "", topics.DEFAULT_TOPICS)
    assert got == primary
    if also:
        assert also in all_


@pytest.mark.parametrize("title,body,also,not_also", [
    ("가나광학, SDT와 SWIR 카메라 MOU 체결", "", None, "수주·계약"),
    ("가나광학, 해외 기업과 양해각서 체결", "", None, "수주·계약"),
    ("가나광학, 방산기업과 mou", "", "방산", "수주·계약"),
    # 제목에 없고 본문 뒤쪽(80자 밖)에만 있어도 MOU (예: 교수신문 209923)
    ("SDT·가나광학, 양자점 단파 적외선 카메라 국산화 맞손",
     "양자기술 전문기업 SDT(대표 윤지원)와 초정밀 광학 전문기업 가나광학(대표 조현일)이 양자점 기반 SWIR(단파 적외선) "
     "카메라 공동 개발에 나선다.양사는 8일 업무협약(MOU)을 체결하고", "기술·제품", "수주·계약"),
    ("가나광학, MOU 이어 47억 공급계약", "", "수주·계약", None),
])
def test_mou_is_its_own_topic(title, body, also, not_also):
    got, all_ = topics.classify(title, body, topics.DEFAULT_TOPICS)
    assert got == "MOU" and all_[0] == "MOU"
    if also:
        assert also in all_
    if not_also:
        assert not_also not in all_


@pytest.mark.parametrize("title", ["가나광학, MOUSE 신제품 출시", "SMOU 데이터"])
def test_mou_not_matched_inside_other_words(title):
    assert topics.classify(title, "", topics.DEFAULT_TOPICS)[0] != "MOU"


def test_topic_falls_back_to_summary_start():
    assert topics.classify("가나광학 대표 인터뷰", "올해 방산 수출 확대 계획을 밝혔다", topics.DEFAULT_TOPICS)[0] == "방산"


@pytest.mark.parametrize("page,name", [
    ('<strong>삼성전자 정규장 상승 마감뒤 NXT 종가 0.47%↓</strong>[이데일리 안유리 기자] SK하이닉스 주가가', "안유리"),
    ("(서울=연합뉴스) 홍길동 기자 = 가나광학이 23일", "홍길동"),
    ("본문 끝. 김철수 기자 kim@example.co.kr 저작권자", "김철수"),
    ("본문 끝. 김철수 기자(kimcs@news.com)", "김철수"),
    ("이 발표 후 주가는 올랐습니다. 곽우진 기자가 보도합니다. [리포트]", "곽우진"),
    ("환율은 1320원에서 움직였습니다. <span>SBS</span> <span>Biz</span> 윤지혜입니다.", "윤지혜"),
    ('<em class="media_end_head_journalist_name">박영희 기자</em>', "박영희"),
    # 이투데이: 구조화 데이터·dd:author에 부서명이 붙음 (본문엔 '기자 기자 이름을 클릭하면…')
    ('<script type="application/ld+json">{"author":{"@type":"Person","name":"대구경북취재본부 이종팔"}}</script>', "이종팔"),
    ('<meta property="dd:author" content="대구경북취재본부 이종팔">', "이종팔"),
    ('<dd class="reporter_name"> <a class="detaillink nolink">대구경북취재본부 이종팔 기자</a> </dd>', "이종팔"),
    # 벤처스퀘어(워드프레스): 유니코드 이스케이프에 성·이름이 띄어 있음 (= "한 혜선")
    ('{"author":{"@type":"Person","name":"\\ud55c \\ud61c\\uc120","url":"https://www.venturesquare.net/author/sunny/"}}', "한혜선"),
    ('{"author":{"@type":"Organization","name":"편집국"}}', None),
    ("기자 이름이 없는 기사 본문입니다.", None),
    ("[관련 기자] 이번 기자 회견", None),
])
def test_reporter_parse(page, name):
    assert reporters.parse(page) == name


def test_fill_retries_missing_names_and_skips_google(tmp_path, monkeypatch):
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
    for _ in range(4):                    # 못 연 3번, 열었지만 이름이 없던 2번은 다음 실행에 다시, 3번까지 (한 번 빈 화면을 받았다고 굳히지 않음)
        assert reporters.fill("X", limit=2, fetch=fetch, resolve=lambda u: None) == 0
    assert opened == ["https://n.news.naver.com/mnews/article/018/3", "https://n.news.naver.com/mnews/article/018/2",
                      "https://n.news.naver.com/mnews/article/018/1",
                      "https://n.news.naver.com/mnews/article/018/3", "https://n.news.naver.com/mnews/article/018/2",
                      "https://n.news.naver.com/mnews/article/018/3", "https://n.news.naver.com/mnews/article/018/2"]
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


def test_naver_page_without_name_falls_back_to_original(tmp_path, monkeypatch):
    # 네이버판에 기자 칸이 비어 있으면 '기사원문' 링크(언론사 페이지)에서 읽음 (블로터 등)
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(reporters._time, "sleep", lambda s: None)
    nv, orig = "https://n.news.naver.com/mnews/article/293/1", "https://www.bloter.net/news/articleView.html?idxno=676674"
    with db.connect() as conn:
        conn.execute("INSERT INTO articles(id,symbol,ts,office,title,url) VALUES ('b','X','2026-10-20T10:00','블로터','t',?)", (nv,))
    pages = {nv: '<a href="https://www.bloter.net/news/articleView.html?idxno&#x3D;676674" target="_blank" class="media_end_head_origin_link">원문</a>',
             orig: '<span class="byline">김나영 기자(steaming@bloter.net)</span>'}
    assert reporters.fill("X", fetch=lambda u: pages[u], resolve=lambda u: None) == 1
    with db.connect() as conn:
        assert conn.execute("SELECT reporter FROM articles").fetchone()[0] == "김나영"


def test_diagnose_tells_why_names_are_still_missing(tmp_path, monkeypatch):
    """10번 다 읽고도 기자명이 빈 기사: 매체별로 까닭만 (주소·제목은 돌려주지 않음, 실행 기록이 공개라서)."""
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(reporters._time, "sleep", lambda s: None)
    rows = [("a0", "가매체", "https://a.example/1", 10), ("a1", "가매체", "https://a.example/2", 10),
            ("a2", "나매체", "https://b.example/1", 10), ("a3", "다매체", "https://c.example/1", 10),
            ("a4", "라매체", "https://d.example/1", 2)]          # 아직 다시 읽는 중 → 진단하지 않음
    with db.connect() as conn:
        for aid, office, url, tries in rows:
            conn.execute("INSERT INTO articles(id,symbol,ts,title,url,office,reporter_tries) VALUES (?,?,?,?,?,?,?)",
                         (aid, "X", "2026-11-01T10:00", "t", url, office, tries))
    long_page = "<html>" + "본문 " * 1500 + "</html>"

    def fetch(u):
        if u.startswith("https://a."):
            raise reporters_error()
        return {"https://b.example/1": "<html></html>", "https://c.example/1": long_page + "홍길동기자"}[u]

    def reporters_error():
        from core.naver import DataSourceError
        return DataSourceError("네이버 접속 실패: HTTPError: HTTP Error 403: Forbidden  (https://a.example/1)")
    got = reporters.diagnose("X", fetch=fetch)
    assert got == {"가매체": {"못 엶(HTTP Error 403)": 2}, "나매체": {"빈 화면": 1}, "다매체": {"기자 칸 모양 모름('기자' 글자 있음)": 1}}
    assert "example" not in json.dumps(got, ensure_ascii=False)


def test_office_missing_count(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.connect() as conn:
        for aid, office, src in [("a0", "가매체", "naver"), ("a1", None, "google"), ("a2", " ", "naver"), ("a3", "나매체", "google")]:
            conn.execute("INSERT INTO articles(id,symbol,ts,title,url,office,source) VALUES (?,?,?,?,?,?,?)",
                         (aid, "X", "2026-11-01T10:00", "t", "u", office, src))
    assert reporters.office_missing("X") == (4, {"google": 1, "naver": 1})


def test_mask_keeps_only_page_shape():
    """공개 실행 기록용: 기사 글자는 가리고 태그·class·'기자'만 남김."""
    html = ('<div class="art_writer" data-url="https://x.example/a?id=1"><a href="https://x.example/r/9">홍길동기자</a>'
            ' gildong@x.example 입력 2026.11.16 07:31</div><meta name="author" content="Hong Kimm">')
    got = reporters.mask(html)
    assert got == ('<div class="art_writer" data-url="…"><a href="…">가가가기자</a>'
                   ' aaaaaaa@a.aaaaaaa 입력 0000.00.00 00:00</div><meta name="author" content="aaaa aaaa">')


def test_diagnose_collects_masked_shape_samples(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(reporters._time, "sleep", lambda s: None)
    with db.connect() as conn:
        conn.execute("INSERT INTO articles(id,symbol,ts,title,url,office,reporter_tries) VALUES (?,?,?,?,?,?,?)",
                     ("a0", "X", "2026-11-01T10:00", "t", "https://c.example/1", "다매체", 10))
        conn.execute("INSERT INTO articles(id,symbol,ts,title,url,office,reporter_tries) VALUES (?,?,?,?,?,?,?)",
                     ("a1", "X", "2026-11-01T10:00", "t", "https://d.example/1", "라매체", 10))
    body = "<p>" + "본문 " * 1500 + "</p>"
    pages = {"https://c.example/1": body + '<span class="writer">홍길동 기자</span>',
             "https://d.example/1": body + '<div class="byline">Hong</div>'}
    samples = {}
    reporters.diagnose("X", fetch=pages.get, samples=samples)
    assert '<span class="writer">가가가 기자</span>' in samples["다매체"][0]
    assert '<div class="byline">aaaa</div>' in samples["라매체"][0]
    assert "본문" not in json.dumps(samples, ensure_ascii=False)


def test_mask_keeps_json_author_keys():
    assert reporters.mask('{"author":{"@type":"Person","name":"홍길동"}}') == '{"author":{"@type":"Person","name":"가가가"}}'


def test_mask_hides_cut_off_tag_at_edges():
    """잘린 자리에 걸친 태그 조각의 주소도 가림 (실행 기록에 'https://www.donga.c'가 남았던 것)."""
    assert reporters.mask('<li class="x"><a href="https://www.donga.c') == '<li class="x"><a aaaa="aaaaa://aaa.aaaaa.a'
    assert reporters.mask('com/123">홍길동 기자</a>') == 'aaa/000">가가가 기자</a>'


@pytest.mark.parametrize("page,want", [
    # 한국의약통신: og:article:author 칸, '대기자' 직함 (실행 기록의 가린 모양: content="가가가 가기자", class="name")
    ('<meta property="og:article:author" content="홍길동 대기자"/>', "홍길동"),
    # 버핏연구소: 본문 첫머리 [매체=이름 기자]
    ('<p>[버핏연구소=홍길동 기자] 본문 첫 문장</p>', "홍길동"),
    # 기자명 표시 뒤 작성자 (와이드경제·디지털투데이 같은 모양)
    ('<li> <span class="show-for-sr">기자명</span> 홍길동 </li>', "홍길동"),
    ('<li> <i class="icon-user-o"><span class="show-for-sr">기자명</span></i> 홍길동 기자 </li>', "홍길동"),
    # 사람 이름이 아닌 작성자는 비워 둠
    ('<li> <span class="show-for-sr">기자명</span> AI리포터 </li>', None),
    ('<li> <span class="show-for-sr">기자명</span> 와이드경제편집국 </li>', None),
])
def test_reporter_shapes_seen_in_run_log(page, want):
    assert reporters.parse(page, "") == want


def test_error_text_for_public_log_has_no_address():
    """공개 실행 기록에 기사 주소가 남지 않게 (11/23 기록에 kjdaily.com 기사 주소가 그대로 남았던 것)."""
    msg = "기자명 읽기 실패: 네이버 접속 실패: URLError: <urlopen error timed out>  (http://www.kjdaily.com/article.php?aid=1)"
    assert reporters.no_address(msg) == "기자명 읽기 실패: 네이버 접속 실패: URLError: <urlopen error timed out>  (…)"
