"""기사 주제 자동 분류와 기자명 읽기 (원문 글귀 형식은 실제 네이버 기사에서 본 것)."""
from __future__ import annotations

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
    assert reporters.fill("X", limit=2, fetch=fetch) == 0          # 최신 2개(3번 실패, 2번 이름 없음) + 구글은 열지 않음
    assert reporters.fill("X", limit=2, fetch=fetch) == 1          # 다음 실행에 남은 1번
    assert reporters.fill("X", limit=2, fetch=fetch) == 0          # 모두 확인함 → 다시 열지 않음
    assert opened == ["https://n.news.naver.com/mnews/article/018/3", "https://n.news.naver.com/mnews/article/018/2",
                      "https://n.news.naver.com/mnews/article/018/1"]
    with db.connect() as conn:
        got = dict(conn.execute("SELECT id, reporter FROM articles").fetchall())
    assert got == {"a0": "안유리", "a1": None, "a2": None, "a3": None}
