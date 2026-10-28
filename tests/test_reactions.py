from __future__ import annotations

import webserver
import pytest


def detail(kind="session", interval=None):
    rc = {"start": "2026-04-22T14:14", "base_own": 100.0, "base_kq": 800.0,
          "own": [{"t": "2026-04-22T13:00", "c": 99.0}, {"t": "2026-04-22T14:00", "c": 100.0},
                  {"t": "2026-04-22T15:30", "c": 104.0}, {"t": "2026-04-22T09:00", "c": 90.0}],
          "kq": [{"t": "2026-04-22T14:00", "c": 800.0}, {"t": "2026-04-22T15:30", "c": 808.0}]}
    w = {"kind": kind, "start": "2026-04-22T14:14", "rows": [{"minutes": 60, "own": 0.03, "kosdaq": 0.01}]}
    if interval:
        w["interval"] = interval
    return {"article": {"id": "a", "ts": "2026-04-22T14:14", "title": "t", "office": "톱스타뉴스", "kind": "ext",
                        "category": "회사 발표"},
            "effect": {"reaction_day": "2026-04-22",
                       "daily": {"d0": 0.04, "d1": 0.02, "d3": None, "kosdaq": {"d0": 0.01, "d1": 0.0, "d3": None}}},
            "intraday": None if interval else w, "intraday_coarse": w if interval else None, "reaction_chart": rc}


def test_reaction_row_aligns_path_at_publication_and_keeps_resolution():
    r = webserver.reaction_row(detail(interval=60), {"topic": "수주"})
    assert (r["interval"], r["mode"], r["topic"]) == (60, "session", "수주")
    assert r["path"]["own"][0] == [-14, 0.0]
    assert r["path"]["own"][1] == pytest.approx([76, 0.04])          # 공개 30분 전보다 앞선 봉(13:00, 09:00)은 뺌
    assert r["path"]["kq"][0] == [-14, 0.0]
    assert r["path"]["kq"][1] == pytest.approx([76, 0.01])
    assert r["steps"] == {"60": {"own": 0.03, "kq": 0.01}}
    assert webserver.reaction_row(detail())["interval"] == 1


def test_reactions_skip_hidden_and_tag_disclosures():
    out = webserver.reactions_from([{"id": "a", "hidden": True}], {"a": detail()},
                                   [{"id": "d1"}], {"d1": {**detail(), "article": {**detail()["article"], "id": "d1", "kind": "disc"}}})
    assert [(x["id"], x["kind"]) for x in out["items"]] == [("d1", "disc")]


def test_linked_articles_and_disclosure_share_aggregation_key_only():
    article = detail()
    article['effect']['disclosure'] = {'ts': '2026-04-22T14:10', 'title': 'contract'}
    disc = detail()
    disc['article'].update(id='d1', kind='disc', ts='2026-04-22T14:10', title='contract')
    other = detail()
    other['article'].update(id='other', ts='2026-04-22T14:10', title='different news')
    rows = webserver.reactions_from([{'id':'a'}, {'id':'other'}], {'a':article,'other':other},
                                   [{'id':'d1'}], {'d1':disc})['items']
    keys = {r['id']:r['event_key'] for r in rows}
    assert len(rows) == 3
    assert keys['a'] == keys['d1'] != keys['other']


def test_reaction_keeps_publication_and_earlier_disclosure_separate():
    d = detail()
    d["article"]["ts"] = "2026-04-22T19:20"
    d["effect"]["disclosure"] = {"ts": "2026-04-22T14:10", "title": "공급계약"}
    d["reaction_chart"]["start"] = d["intraday"]["start"] = "2026-04-22T14:10"
    r = webserver.reaction_row(d)
    assert r["ts"] == "2026-04-22T19:20"
    assert r["analysis_ts"] == "2026-04-22T14:10"
    assert r["basis_reason"] == "disclosure"
    assert r["mode"] == "session"  # This describes the calculation, not 19:20 publication.


def test_reaction_explains_representative_selected_after_first_publication():
    d = detail()
    d["article"]["own_ts"] = "2026-04-22T19:20"
    r = webserver.reaction_row(d)
    assert r["ts"] == "2026-04-22T19:20"
    assert r["analysis_ts"] == "2026-04-22T14:14"
    assert r["basis_reason"] == "story"
