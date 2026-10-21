"""기사 영향 분석: 분류(회사 이름·소음), 우리 보도자료 판정, 같은 반응일 묶기, 장 외 판정, 공시 먼저."""
from __future__ import annotations

from datetime import datetime

import pytest

from core import articles, db

NAME = "가나광학"


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(articles, "company_name", lambda: NAME)
    db.init_db()


def test_classify_uses_company_name_and_drops_noise():
    assert articles.classify(f"{NAME}, 42억 규모 공급계약", "", NAME)[0] == "회사 발표"
    assert articles.classify(f"{NAME} 투자분석 2026. 09. 12", "", NAME)[0] == "단순 언급"
    assert articles.classify(f"{NAME} 실적 분석", "", NAME, office="주달")[0] == "단순 언급"
    assert articles.classify("2026년10월20일(화) S브리핑", "", NAME)[0] == "단순 언급"


def _art(conn, aid, ts, title, outlets=1, category=None):
    cat = category or articles.classify(title, "", NAME)[0]
    conn.execute("INSERT INTO articles(id, symbol, ts, office, title, body, url, outlets, category, tone, mention) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (aid, "X", ts, "매체", title, "", "", outlets, cat, 0, "제목"))


def test_pr_when_company_news_on_three_outlets_and_override(tmpdb):
    with db.connect() as conn:
        _art(conn, "a", "2026-09-08T08:00", f"{NAME}-SDT, SWIR 카메라 공동 개발 협력", outlets=2)
        _art(conn, "b", "2026-09-08T09:30", f"SDT·{NAME} 손잡았다…양자점 카메라 국산화 협력")
        _art(conn, "c", "2026-09-18T06:10", f"{NAME}, 외형 커졌지만 재고 부담도")
        _art(conn, "d", "2026-09-20T10:00", f"{NAME}, 유상증자 결정 소식")
    rows = {a["id"]: a for a in articles.article_rows("X")}
    assert rows["a"]["kind"] == "pr" and rows["a"]["tone"] == 0        # 보도자료는 호재·악재 표시 안 함
    assert rows["c"]["kind"] == "ext" and rows["c"]["tone"] == 0       # 뚜렷한 악재 단어 없으면 표시 안 함
    assert rows["d"]["tone"] == -1                                     # 유상증자 → 악재
    with db.connect() as conn:
        conn.execute("UPDATE articles SET kind_override='pr' WHERE id='c'")
    assert {a["id"]: a for a in articles.article_rows("X")}["c"]["kind"] == "pr"


def test_group_events_by_reaction_day():
    def it(aid, ts, kind, day, cat="회사 발표", out=1):
        return {"id": aid, "ts": ts, "kind": kind, "category": cat, "outlets_total": out, "effect": {"reaction_day": day}}
    ev = articles.group_events([it("a", "2026-05-26T13:37", "ext", "2026-05-26"),
                                it("b", "2026-05-26T13:47", "pr", "2026-05-26", out=3),
                                it("c", "2026-05-26T14:00", "ext", "2026-05-26", cat="단순 언급"),
                                it("d", "2026-06-01T09:00", "ext", "2026-06-01", cat="시황·특징주")])
    first = next(e for e in ev if e["day"] == "2026-05-26")
    assert first["kind"] == "pr" and first["lead"] == "b" and first["outlets"] == 4 and len(first["ids"]) == 3
    assert next(e for e in ev if e["day"] == "2026-06-01")["noise"]


def test_off_session_lowers_verdict_one_step():
    eff = {"daily": {"excess": {"d0": 0.05}, "bench_name": "KOSDAQ", "d0": 0.05},
           "decomp": {"d0": {"abnormal": 0.05, "z": 3.0, "own": 0.06, "market": 0.01, "sector": 0.0}}}
    assert articles._verdict(eff, 0, "회사 발표")[1] == "up"
    assert articles._verdict({**eff, "off_session": True}, 0, "회사 발표")[1] == "up-weak"


def test_earlier_disclosure_moves_base_time(tmpdb):
    with db.connect() as conn:
        conn.execute("INSERT INTO disclosures(no, symbol, date, title, kind, ts) VALUES (?,?,?,?,?,?)",
                     ("1", "X", "2026-08-06", f"(주){NAME} 단일판매ㆍ공급계약체결", "단일판매/공급계약", "2026-08-06T11:11"))
    t = datetime(2026, 8, 6, 14, 32)
    assert articles.earlier_disclosure("X", f"{NAME}, 42억 규모 방산 센서부 공급계약", t)["ts"] == "2026-08-06T11:11"
    assert articles.earlier_disclosure("X", f"{NAME}, 전시회 참가", t) is None
