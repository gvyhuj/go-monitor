"""기사·공시 알림: 보도자료 매체 수 증가, 외부 기사·공시 새로 들어옴, 처음 켤 때, 실패 시 다시."""
from __future__ import annotations

import urllib.error
from datetime import datetime, timedelta

import pytest

from core import db, newsalerts
from core.db import connect

SYM = "000000"
T0 = datetime(2026, 10, 20, 9, 0)


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def art(aid, office, title, category, when, story=None, checked=True, outlets=1, kind=None, hidden=0):
    with connect() as conn:
        conn.execute(
            "INSERT INTO articles(id, symbol, ts, office, title, category, story, first_seen, outlets, "
            "kind_override, hidden, mention_checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, SYM, when.isoformat(timespec="minutes"), office, title, category, story or aid,
             when.isoformat(timespec="minutes"), outlets, kind, hidden,
             when.isoformat(timespec="minutes") if checked else None))


def disc(no, when):
    with connect() as conn:
        conn.execute("INSERT INTO disclosures(no, symbol, date, title, first_seen) VALUES (?,?,?,?,?)",
                     (no, SYM, when.date().isoformat(), "공시", when.isoformat(timespec="minutes")))


class Post:
    def __init__(self, fail=False):
        self.titles, self.fail = [], fail

    def __call__(self, title, body):
        if self.fail:
            raise urllib.error.URLError("down")
        self.titles.append(title)


def run(now, post):
    return newsalerts.send_new(SYM, now=now, post=post)


def test_first_run_only_marks_existing(tmpdb):
    art("a0", "A일보", "그린광학 50억 수주", "회사 발표", T0 - timedelta(hours=1))
    disc("d0", T0 - timedelta(hours=1))
    p = Post()
    assert run(T0, p) is None and p.titles == []
    assert run(T0 + timedelta(minutes=3), p) is None and p.titles == []


def test_press_release_spread_example(tmpdb):
    """9시 1건, 9시 반 5건, 10시 3건 → 1개 매체, 1→6, 6→9 세 번."""
    p = Post()
    run(T0 - timedelta(minutes=5), p)                    # 처음 켬
    art("s", "A일보", "그린광학 100억 수주", "회사 발표", T0)
    assert run(T0 + timedelta(minutes=3), p).startswith("[소식] 보도자료 1개 매체")
    for i in range(5):                                   # 받아 쓴 기사 중 하나는 분류가 '언론 분석'이어도 같은 묶음
        art(f"s{i}", f"B{i}신문", "그린광학 100억 수주 쾌거", "회사 발표" if i else "언론 분석", T0 + timedelta(minutes=30), story="s")
    assert run(T0 + timedelta(minutes=33), p).startswith("[소식] 보도자료 1→6개 매체")
    for i in range(3):
        art(f"t{i}", f"C{i}뉴스", "그린광학, 100억 수주", "회사 발표", T0 + timedelta(hours=1), story="s")
    assert run(T0 + timedelta(minutes=63), p).startswith("[소식] 보도자료 6→9개 매체")
    assert run(T0 + timedelta(minutes=66), p) is None
    assert len(p.titles) == 3


def test_external_and_disclosure_bundled(tmpdb):
    p = Post()
    run(T0, p)
    art("e1", "D경제", "그린광학 성장성 분석", "언론 분석", T0 + timedelta(minutes=1))
    art("e2", "E경제", "광학 업계 재편 속 그린광학", "언론 분석", T0 + timedelta(minutes=1))
    disc("d1", T0 + timedelta(minutes=1))
    t = run(T0 + timedelta(minutes=3), p)
    assert t.startswith("[소식] 외부 기사 2건 · 공시 1건")
    disc("d2", T0 + timedelta(minutes=6))
    assert run(T0 + timedelta(minutes=9), p).startswith("[소식] 공시 1건")
    assert len(p.titles) == 2


def test_negative_company_article_is_external(tmpdb):
    p = Post()
    run(T0, p)
    art("n1", "F일보", "그린광학 실적 부진", "회사 발표", T0 + timedelta(minutes=1))
    assert run(T0 + timedelta(minutes=3), p).startswith("[소식] 외부 기사 1건")


def test_manual_kind_override(tmpdb):
    p = Post()
    run(T0, p)
    art("k1", "G일보", "그린광학 대표 인터뷰", "언론 분석", T0 + timedelta(minutes=1), kind="pr")
    art("k2", "H일보", "그린광학 수주 기대", "회사 발표", T0 + timedelta(minutes=1), kind="ext")
    assert run(T0 + timedelta(minutes=3), p).startswith("[소식] 보도자료 1개 매체 · 외부 기사 1건")


def test_unchecked_and_hidden_wait(tmpdb):
    p = Post()
    run(T0, p)
    art("u1", "I일보", "그린광학 소식", "언론 분석", T0 + timedelta(minutes=1), checked=False)
    art("h1", "J일보", "그린광학 소식2", "언론 분석", T0 + timedelta(minutes=1), hidden=1)
    assert run(T0 + timedelta(minutes=3), p) is None
    with connect() as conn:                              # '이름만 언급' 검사를 통과하면 그때 알림
        conn.execute("UPDATE articles SET mention_checked_at=? WHERE id='u1'", (T0.isoformat(),))
    assert run(T0 + timedelta(minutes=6), p).startswith("[소식] 외부 기사 1건")


def test_failed_post_retries_next_run(tmpdb):
    run(T0, Post())
    disc("d1", T0 + timedelta(minutes=1))
    assert run(T0 + timedelta(minutes=3), Post(fail=True)) is None
    p = Post()
    assert run(T0 + timedelta(minutes=6), p).startswith("[소식] 공시 1건")
