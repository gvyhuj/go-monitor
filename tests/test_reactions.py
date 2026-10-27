from __future__ import annotations

import webserver


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
    assert r["path"]["own"] == [[-14, 0.0], [76, 0.04]]          # 공개 30분 전보다 앞선 봉(13:00, 09:00)은 뺌
    assert r["path"]["kq"] == [[-14, 0.0], [76, 0.01]]
    assert r["steps"] == {"60": {"own": 0.03, "kq": 0.01}}
    assert webserver.reaction_row(detail())["interval"] == 1


def test_reactions_skip_hidden_and_tag_disclosures():
    out = webserver.reactions_from([{"id": "a", "hidden": True}], {"a": detail()},
                                   [{"id": "d1"}], {"d1": {**detail(), "article": {**detail()["article"], "id": "d1", "kind": "disc"}}})
    assert [(x["id"], x["kind"]) for x in out["items"]] == [("d1", "disc")]
