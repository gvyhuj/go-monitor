"""공시: DART 제목과 KIND 제목 맞추기, 시각 모르는 공시는 그날 하루로만 잼."""
from __future__ import annotations

from datetime import date

import pytest

from core import articles, db, kind


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def test_kind_times_match_dart_titles_with_different_tags(tmpdb, monkeypatch):
    with db.connect() as conn:
        for no, d, t in [("1", "2026-08-14", "반기보고서 (2026.06)"),
                         ("2", "2026-04-22", "[첨부추가]단일판매ㆍ공급계약체결"),
                         ("3", "2025-11-24", "[기재정정]주식등의대량보유상황보고서(일반)"),
                         ("4", "2025-11-05", "투자설명서")]:
            conn.execute("INSERT INTO disclosures(no, symbol, date, title, kind, pblntf) VALUES (?,?,?,?,?,?)",
                         (no, "X", d, t, "", "A"))
    monkeypatch.setattr(kind, "fetch", lambda code, since, until: [
        {"ts": "2026-08-14T14:25", "title": "반기보고서(일반법인) (2026.06)"},
        {"ts": "2026-04-22T14:10", "title": "단일판매ㆍ공급계약체결"},
        {"ts": "2025-11-24T16:35", "title": "[정정] 주식등의대량보유상황보고서(일반)"},
        {"ts": "2025-11-06T09:00", "title": "투자설명서"},                       # 날짜가 다르면 다른 공시
    ])
    assert kind.fill_times("X", date(2025, 1, 1), date(2026, 10, 1)) == 3
    with db.connect() as conn:
        ts = {r["no"]: r["ts"] for r in conn.execute("SELECT no, ts FROM disclosures")}
    assert ts == {"1": "2026-08-14T14:25", "2": "2026-04-22T14:10", "3": "2025-11-24T16:35", "4": None}


def test_untimed_disclosure_uses_same_day_only(tmpdb, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(articles, "trading_days", lambda s: ["2025-11-14", "2025-11-17"])
    # 상장 전(그날 거래 없음): 다음 거래일로 넘기지 않음
    e = articles.effect("X", datetime(2025, 11, 11, 15, 30), 0, "공시", "", untimed=True)
    assert e["reaction_day"] is None
    # 거래일: 그날 하루, 14시 이후 2일 누적 규칙은 쓰지 않음
    e = articles.effect("X", datetime(2025, 11, 14, 15, 30), 0, "공시", "", untimed=True)
    assert e["reaction_day"] == "2025-11-14" and not e["late"] and e["minute"] is None
