"""사건 묶음 (실제 기사 제목으로 확인한 예)."""
from __future__ import annotations

from core import cases


def art(i, ts, title, topic="수주·계약", kind="company", category="회사 발표"):
    return {"id": i, "ts": ts, "title": title, "topic": topic, "kind": kind, "category": category, "office": "x"}


def disc(i, ts, title, kind="거래소공시"):
    return {"id": i, "ts": ts, "title": title, "kind": kind, "untimed": False, "filer": "그린광학"}


ARTS = [
    art("lig", "2026-04-22T14:17", "그린광학, LIG디펜스앤에어로스페이스와 82억원 규모 공급계약"),
    art("isr", "2026-07-01T08:26", "그린광학, 이스라엘 방산기업과 47억 공급계약…ZnS 소재 추가 수주"),
    art("p1", "2026-03-04T17:00", "그린광학, 인도 파라스디펜스와 우주·방산용 광학 시스템 공동 개발", topic="방산"),
    art("p2", "2026-03-05T17:00", "그린광학, 인도 방산기업 파라스와 우주방산 협력…ISRO 위성사업 진출 추진", topic="우주·위성"),
    art("q1", "2026-06-09T10:37", "그린광학, 134억 규모 저궤도 위성 광통신 국책과제 참여", topic="정부·기관 지원"),
    art("q2", "2026-06-17T10:12", "그린광학, 128억원 규모 양자 관성항법 국책과제 참여", topic="정부·기관 지원"),
    art("tp1", "2026-10-20T09:26", "충북TP, 그린광학 AI 코팅기술 고도화 지원", topic="정부·기관 지원", kind="ext"),
    art("tp2", "2026-10-20T13:59", "충북테크노파크, ㈜그린광학 광학코팅 공정 고도화", topic="정부·기관 지원", kind="ext"),
    art("noise", "2026-10-20T15:00", "코스닥 광학주 동반 약세", topic="주가·시황", category="시황·특징주"),
]
DISCS = [
    disc("d1", "2026-04-22T14:10", "[첨부추가]단일판매ㆍ공급계약체결"),
    disc("d2", "2026-06-30T18:02", "단일판매ㆍ공급계약체결"),
    disc("d3", "2026-10-14T20:00", "[투자주의]소수계좌 거래집중 종목", kind="거래소 조치"),
]


def by_lead():
    return {c["lead"]: c for c in cases.build(ARTS, DISCS)}


def ids(c):
    return [i["id"] for i in c["items"]]


def test_contract_disclosure_joins_and_leads_its_articles():
    got = by_lead()
    assert ids(got["lig"]) == ["d1", "lig"] and got["lig"]["start"] == "2026-04-22T14:10"
    assert got["lig"]["partner"] == "LIG디펜스앤에어로스페이스" and got["lig"]["amount"] == 82
    assert ids(got["isr"]) == ["d2", "isr"] and got["isr"]["n_days"] == 2


def test_same_partner_on_different_days_is_one_case():
    got = by_lead()
    assert ids(got["p1"]) == ["p1", "p2"]


def test_same_topic_different_amounts_on_different_days_stay_apart():
    got = by_lead()
    assert ids(got["q1"]) == ["q1"] and ids(got["q2"]) == ["q2"]


def test_same_day_same_topic_without_partner_match_is_one_case_and_noise_is_left_out():
    got = by_lead()
    assert ids(got["tp1"]) == ["tp1", "tp2"]
    assert all("noise" not in ids(c) and "d3" not in ids(c) for c in got.values())


def test_amount_and_partner_extraction():
    assert cases.amount("1.2조원 규모 수주") == 12000
    assert cases.amount("방산 센서부 공급") is None
    assert cases.partner("그린광학, 美 우주기업 '아라비르'와 MOU") == "아라비르"
    assert cases.partner('SDT-그린광학 "양자점 기반 SWIR 카메라 완성도↑"') == "SDT"
    assert cases.partner("NASA 출신이 선택한 한국 광학기술…그린광학, 美 우주기업과 위성 카메라 공급 협력") is None


def test_manual_split_and_merge_override_auto_grouping():
    auto = {c["lead"]: c for c in cases.build(ARTS, DISCS)}
    assert ids(auto["p1"]) == ["p1", "p2"]
    got = {c["lead"]: c for c in cases.build(ARTS, DISCS, overrides={"p2": "self", "q2": "q1"})}
    assert ids(got["p1"]) == ["p1"] and got["p1"]["manual"]
    assert ids(got["p2"]) == ["p2"] and got["p2"]["partner"] == "파라스"
    assert ids(got["q1"]) == ["q1", "q2"] and got["q1"]["manual"] and got["q1"]["amount"] == 134
    assert not got["lig"]["manual"]
    # 없는 항목·같은 사건으로 옮기기는 무시
    same = cases.build(ARTS, DISCS, overrides={"zzz": "q1", "p2": "p1"})
    assert {c["lead"]: ids(c) for c in same}["p1"] == ["p1", "p2"]


def test_disclosure_can_be_split_off_alone():
    got = cases.build(ARTS, DISCS, overrides={"d1": "self"})
    alone = next(c for c in got if ids(c) == ["d1"])
    assert alone["lead"] == "d1" and alone["n_articles"] == 0


def test_save_override_roundtrip(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    cases.save_override(["a", "b"], "c")
    cases.save_override(["b"], None)
    assert cases.load_overrides() == {"a": "c"}


def test_case_move_request_from_site(tmp_path, monkeypatch):
    import logging
    import cloud_job
    from core import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    log = logging.getLogger("t")
    cloud_job.apply_inbox({"type": "case_move", "items": ["p2", "q2"], "to": "self"}, log)
    cloud_job.apply_inbox({"type": "case_move", "items": ["q2"], "to": "auto"}, log)
    assert cases.load_overrides() == {"p2": "self"}
