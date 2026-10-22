"""직접 붙여넣은 원인: 저장·연결·화면용 정리."""
from __future__ import annotations

import gzip
import json
from datetime import datetime

import pytest

from core import db, ghinbox, manual

NOW = datetime(2026, 10, 12, 14, 5)
DAY = "2026-10-12"


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def _ev(event_type, start, last, direction="down"):
    return {"event_type": event_type, "direction": direction,
            "start_ts": f"{DAY}T{start}", "last_ts": f"{DAY}T{last}"}


def _find(ev):
    with db.connect() as conn:
        return manual.find(conn, ev)


def test_day_cause_shows_only_on_day(tmpdb):
    manual.save({"type": "cause", "date": DAY, "direction": "down", "scope": "day",
                 "body": "확인해보면, 오늘은…\n외국인 매도", "source": "ChatGPT"}, NOW)
    d = _find(_ev("daily", "09:00", "15:30"))
    assert d and d["inherited"] is False and d["source"] == "ChatGPT"
    i = _find(_ev("intraday", "09:00", "10:29"))
    assert i is None                                    # 하루 원인은 장중 순간 줄에 보이지 않음
    assert _find(_ev("intraday", "09:00", "10:29", direction="up")) is None


def test_intraday_cause_overrides_day(tmpdb):
    manual.save({"type": "cause", "date": DAY, "direction": "down", "scope": "day", "body": "하루"}, NOW)
    manual.save({"type": "cause", "date": DAY, "direction": "down", "scope": "intraday",
                 "start_ts": f"{DAY}T09:00", "last_ts": f"{DAY}T10:29", "body": "장중"}, NOW)
    assert _find(_ev("intraday", "09:10", "10:40"))["body"] == "장중"   # 구간이 겹치면 연결 (다시 묶여도)
    assert _find(_ev("intraday", "13:00", "13:30")) is None            # 안 겹치는 순간은 원인 없음


def test_resave_overwrites_and_delete(tmpdb):
    base = {"type": "cause", "date": DAY, "direction": "down", "scope": "day"}
    manual.save({**base, "body": "처음"}, NOW)
    manual.save({**base, "body": "고친 것", "headline": "요약"}, NOW)
    m = _find(_ev("daily", "09:00", "15:30"))
    assert m["body"] == "고친 것" and manual.summary(m) == "요약"
    manual.save({**base, "type": "cause_delete"}, NOW)
    assert _find(_ev("daily", "09:00", "15:30")) is None


def test_save_marks_events_for_rebuild(tmpdb):
    with db.connect() as conn:
        conn.execute("INSERT INTO events(symbol, start_ts, last_ts, event_type, direction, status) "
                     "VALUES ('X', ?, ?, 'daily', 'down', 'closed')", (f"{DAY}T09:00", f"{DAY}T15:30"))
    manual.save({"type": "cause", "date": DAY, "direction": "down", "scope": "day", "body": "x"}, NOW)
    with db.connect() as conn:
        assert conn.execute("SELECT cause_updated_at FROM events").fetchone()[0] == "2026-10-12T14:05"


def test_summary_falls_back_to_first_line():
    assert manual.summary({"headline": "", "body": "\n## 1. 외국인 매도가 가장 유력\n본문"}) == "본문"   # 제목 줄은 건너뜀
    gpt = "### 1. 종합판단\n- 이 종목 급락은 **시장 전체 급락과 업종 동반 약세**가 주된 원인 ([기사](https://a.b/c))"
    assert manual.summary({"body": gpt}) == "이 종목 급락은 시장 전체 급락과 업종 동반 약세가 주된 원인 (기사)"
    assert manual.summary(None) is None


def test_bad_requests():
    with pytest.raises(ValueError):
        manual.save({"type": "cause", "date": DAY, "direction": "down", "scope": "day", "body": " "}, NOW)
    with pytest.raises(ValueError):
        manual.save({"type": "cause", "date": DAY, "direction": "down", "scope": "intraday", "body": "x"}, NOW)


def test_facts_only_strips_conclusions():
    c = {"headline": "JP모간 묶음 매도", "confidence": "높음", "summary_line": "…", "desk": [1], "candidates": [1],
         "drivers": {}, "notes": ["x"], "split": {"own": -0.08}, "flows": {"a": 1},
         "buyer": {"broker": "JP모간", "lines": [
             {"kind": "업종 묶음", "text": "34개 중 16개도 순매도했습니다. 이 종목만 겨냥했다기보다 업종을 묶어 사고판 흐름일 가능성이 큽니다."},
             {"kind": "배경 이슈", "text": "중동·이란 기사 430건"},
             {"kind": "외국인 확정치", "text": "-1,923주"}]},
         "macro": {"verdict": "가능성이 큽니다", "lines": [
             {"kind": "이 종목 테마", "text": "우주항공 -4.64%"}, {"kind": "중동·이란", "text": "기사 267건"}]}}
    out = manual.facts_only(c)
    for k in ("headline", "confidence", "summary_line", "desk", "candidates", "drivers", "notes"):
        assert k not in out
    assert out["split"] == {"own": -0.08} and out["flows"] == {"a": 1}
    kinds = [ln["kind"] for ln in out["buyer"]["lines"]]
    assert kinds == ["업종 묶음", "외국인 확정치"]
    assert out["buyer"]["lines"][0]["text"] == "34개 중 16개도 순매도했습니다."
    assert out["macro"]["verdict"] is None and [ln["kind"] for ln in out["macro"]["lines"]] == ["이 종목 테마"]
    assert c["headline"] == "JP모간 묶음 매도"            # 원본은 그대로


def test_inbox_accepts_cause_and_gzip():
    item = {"type": "cause", "date": DAY, "direction": "down", "scope": "day", "body": "가" * 3000}
    raw = gzip.compress(json.dumps(item, ensure_ascii=False).encode())
    import cloud_job
    unseal = lambda b: json.loads(cloud_job._unzip(b))  # noqa: E731 - 암호화는 빼고 압축만 확인
    issue = {"title": "[요청] 사이트 반영 10-12 14:05", "body": "```enc\nAAAA\n```"}
    got = ghinbox.parse(issue, unseal=lambda _b: unseal(raw))
    assert got["type"] == "cause" and len(got["body"]) == 3000
    assert cloud_job._unzip(b'{"a":1}') == b'{"a":1}'


@pytest.mark.parametrize("body, want, conf", [
    ("종합판단\n시장 급락과 방산주 약세가 주된 배경입니다.\n- 근거", "시장 급락과 방산주 약세가 주된 배경입니다.", None),
    ("■ 그린광학 주가 변동 원인 분석 (2026.10.12)\n\n외국인 매도가 하락을 이끌었습니다.", "외국인 매도가 하락을 이끌었습니다.", None),
    ("한 줄 요약 (신뢰도 중간) 시장 급락과 반도체 약세가 배경입니다.\n본문", "시장 급락과 반도체 약세가 배경입니다.", "중간"),
    ("**한 줄 요약 (신뢰도 높음)**\n공급계약 공시가 직접 원인입니다.", "공급계약 공시가 직접 원인입니다.", "높음"),
])
def test_summary_skips_title_lines(body, want, conf):
    m = {"headline": None, "body": body}
    assert manual.summary(m) == want
    assert manual.confidence(m) == conf


def test_headline_drops_confidence_text():
    m = {"headline": "외국계 차익실현 (신뢰도 중간)", "body": "x"}
    assert manual.summary(m) == "외국계 차익실현" and manual.confidence(m) == "중간"


def test_headline_drops_summary_label():
    m = {"headline": "한 줄 요약 (신뢰도 중간) 시장 급락이 배경입니다.", "body": "x"}
    assert manual.summary(m) == "시장 급락이 배경입니다." and manual.confidence(m) == "중간"


def test_range_request_with_cause_creates_record_and_attaches_cause(tmpdb, monkeypatch):
    """차트에서 고른 구간: 기록(요청)을 만들고 붙여넣은 원인을 그 구간에 붙임."""
    import logging
    import cloud_job
    from core import detector
    monkeypatch.setenv("GO_PRIVATE_CONFIG", json.dumps({"symbol": "X"}))
    with db.connect() as conn:
        for i, px in enumerate([10000, 10000, 10100, 10300, 10400, 10400]):
            conn.execute("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume, source) VALUES (?,?,?,?,?,?,?,?)",
                         ("X", f"{DAY}T10:{i * 10:02d}", px, px, px, px, 100, "t"))
    cloud_job.apply_inbox({"type": "analyze", "date": DAY, "from": "10:10", "to": "10:40",
                           "cause_headline": "공급계약 소식 (신뢰도 중간)", "cause_body": "본문"}, logging.getLogger("t"))
    with db.connect() as conn:
        ev = dict(conn.execute("SELECT * FROM events").fetchone())
    assert ev["tier"] == "요청" and ev["direction"] == "up" and ev["event_type"] == "intraday"
    m = _find({"event_type": "intraday", "direction": "up", "start_ts": ev["start_ts"], "last_ts": ev["last_ts"]})
    assert m and m["body"] == "본문" and manual.summary(m) == "공급계약 소식"


def test_day_causes_kept_per_time_with_move(tmpdb):
    base = {"type": "cause", "date": DAY, "scope": "day"}
    manual.save({**base, "direction": "down", "at_ts": f"{DAY}T10:05", "move": -0.0265, "body": "오전 분석"}, NOW)
    manual.save({**base, "direction": "down", "at_ts": f"{DAY}T14:40", "move": -0.0411, "body": "오후 분석"}, NOW)
    m = _find(_ev("daily", "09:00", "15:30"))
    assert m["body"] == "오후 분석" and [e["body"] for e in m["entries"]] == ["오후 분석", "오전 분석"]
    assert [round(e["move"], 4) for e in m["entries"]] == [-0.0411, -0.0265]
    manual.save({**base, "direction": "down", "at_ts": f"{DAY}T10:05", "type": "cause_delete"}, NOW)
    assert [e["body"] for e in _find(_ev("daily", "09:00", "15:30"))["entries"]] == ["오후 분석"]


def test_delete_recorded_range_removes_it_and_its_cause_only(tmpdb, monkeypatch):
    import logging
    import cloud_job
    monkeypatch.setenv("GO_PRIVATE_CONFIG", json.dumps({"symbol": "X"}))
    with db.connect() as conn:
        for i, px in enumerate([10000, 10000, 10100, 10300, 10400, 10400]):
            conn.execute("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume, source) VALUES (?,?,?,?,?,?,?,?)",
                         ("X", f"{DAY}T10:{i * 10:02d}", px, px, px, px, 100, "t"))
        conn.execute("INSERT INTO events(symbol, start_ts, last_ts, event_type, direction, status, tier) "
                     "VALUES ('X', ?, ?, 'intraday', 'up', 'closed', '관찰')", (f"{DAY}T13:00", f"{DAY}T13:30"))
    log = logging.getLogger("t")
    cloud_job.apply_inbox({"type": "analyze", "date": DAY, "from": "10:10", "to": "10:40", "cause_body": "본문"}, log)
    with db.connect() as conn:
        rid, watch = (conn.execute("SELECT id FROM events WHERE tier=?", (t,)).fetchone()[0] for t in ("요청", "관찰"))
    cloud_job.apply_inbox({"type": "event_delete", "id": watch}, log)        # 감지된 기록은 지우지 않음
    cloud_job.apply_inbox({"type": "event_delete", "id": rid}, log)
    with db.connect() as conn:
        tiers = [r[0] for r in conn.execute("SELECT tier FROM events")]
        n_cause = conn.execute("SELECT COUNT(*) FROM manual_causes").fetchone()[0]
    assert tiers == ["관찰"] and n_cause == 0
