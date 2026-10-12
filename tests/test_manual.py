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


def test_day_cause_shows_on_day_and_intraday(tmpdb):
    manual.save({"type": "cause", "date": DAY, "direction": "down", "scope": "day",
                 "body": "확인해보면, 오늘은…\n외국인 매도", "source": "ChatGPT"}, NOW)
    d = _find(_ev("daily", "09:00", "15:30"))
    assert d and d["inherited"] is False and d["source"] == "ChatGPT"
    i = _find(_ev("intraday", "09:00", "10:29"))
    assert i and i["inherited"] is True                 # 하루 원인을 장중 순간에도 보여 줌
    assert _find(_ev("intraday", "09:00", "10:29", direction="up")) is None


def test_intraday_cause_overrides_day(tmpdb):
    manual.save({"type": "cause", "date": DAY, "direction": "down", "scope": "day", "body": "하루"}, NOW)
    manual.save({"type": "cause", "date": DAY, "direction": "down", "scope": "intraday",
                 "start_ts": f"{DAY}T09:00", "last_ts": f"{DAY}T10:29", "body": "장중"}, NOW)
    assert _find(_ev("intraday", "09:10", "10:40"))["body"] == "장중"   # 구간이 겹치면 연결 (다시 묶여도)
    assert _find(_ev("intraday", "13:00", "13:30"))["body"] == "하루"   # 안 겹치는 순간은 하루 원인


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
    assert manual.summary({"headline": "", "body": "\n## 1. 외국인 매도가 가장 유력\n본문"}) == "외국인 매도가 가장 유력"
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
