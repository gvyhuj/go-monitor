"""원인은 직접 붙여넣은 글(ChatGPT 등)로, 숫자는 자동 기록으로.

자동 원인 추정(결론·원인 후보·'관련 기사 N건' 같은 배경 추정)은 실제와 다르게 엮는 경우가 있어 화면에서 뺍니다.
숫자와 사실 자료(몫 나누기, 창구 매매, 비교 종목, 테마 등락, 기사 목록)는 그대로 둡니다.

연결 규칙
- 하루 기록: 같은 날·같은 방향의 '하루' 원인
- 장중 순간: 같은 날·같은 방향이고 구간이 겹치는 '장중' 원인 → 없으면 그날 '하루' 원인을 함께 보여 줌
"""
from __future__ import annotations

import re
from datetime import datetime

from .db import connect

# 업종·테마·거시 배경 중 사실 자료로 남길 줄 (나머지 '주제별 기사 N건'은 추정 연결이라 뺌)
_MACRO_FACTS = {"이 종목 테마", "이날 강한 테마", "전날 밤 해외", "시장 일정"}
# 창구 단서 중 추정인 줄
_BUYER_GUESS = {"배경 이슈"}
_GUESS_TAIL = re.compile(r"\s*(이 종목만 겨냥했다기보다[^.]*\.|업종 전체보다[^.]*\.)")


def _scope(event_type: str) -> str:
    return "day" if event_type == "daily" else "intraday"


def find(conn, ev: dict) -> dict | None:
    """이벤트(행)에 붙일 직접 입력 원인. 장중 순간은 자기 것이 없으면 그날 하루 원인을 씀 (inherited=True)."""
    day, d = ev["start_ts"][:10], ev["direction"]
    if _scope(ev["event_type"]) == "intraday":
        r = conn.execute("SELECT * FROM manual_causes WHERE date=? AND direction=? AND scope='intraday' "
                         "AND start_ts<=? AND last_ts>=? ORDER BY updated_at DESC LIMIT 1",
                         (day, d, ev["last_ts"], ev["start_ts"])).fetchone()
        if r:
            return {**dict(r), "inherited": False}
    r = conn.execute("SELECT * FROM manual_causes WHERE date=? AND direction=? AND scope='day'", (day, d)).fetchone()
    if not r:
        return None
    return {**dict(r), "inherited": _scope(ev["event_type"]) == "intraday"}


def summary(m: dict | None) -> str | None:
    """목록에 보일 한 줄: 입력한 요약 → 없으면 본문에서 제목(#)이 아닌 첫 줄(마크다운 기호 정리, 80자)."""
    if not m:
        return None
    if (m.get("headline") or "").strip():
        return m["headline"].strip()[:120]
    for ln in (m.get("body") or "").splitlines():
        if re.match(r"^\s*#", ln):
            continue
        t = re.sub(r"^[>*\-•\s\d.)]+", "", ln)
        t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t).replace("**", "").strip()
        if t:
            return t[:80] + ("…" if len(t) > 80 else "")
    return None


def save(item: dict, now: datetime) -> str:
    day = str(item.get("date") or "")[:10]
    datetime.fromisoformat(day)                          # 형식 확인
    d = item.get("direction")
    if d not in ("up", "down"):
        raise ValueError("방향(up/down)이 필요합니다.")
    scope = "day" if item.get("scope") == "day" else "intraday"
    st = str(item.get("start_ts") or "")[:16] if scope == "intraday" else ""
    lt = str(item.get("last_ts") or "")[:16] if scope == "intraday" else ""
    if scope == "intraday" and not (st and lt):
        raise ValueError("장중 원인은 구간(시작·끝 시각)이 필요합니다.")
    with connect() as conn:
        if item.get("type") == "cause_delete":
            conn.execute("DELETE FROM manual_causes WHERE date=? AND direction=? AND scope=? AND start_ts=?",
                         (day, d, scope, st))
            what = "삭제"
        else:
            body = str(item.get("body") or "").strip()
            if not body:
                raise ValueError("원인 내용이 비어 있습니다.")
            conn.execute(
                "INSERT INTO manual_causes(date, direction, scope, start_ts, last_ts, headline, body, source, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(date, direction, scope, start_ts) DO UPDATE SET "
                "last_ts=excluded.last_ts, headline=excluded.headline, body=excluded.body, source=excluded.source, "
                "updated_at=excluded.updated_at",
                (day, d, scope, st, lt, str(item.get("headline") or "").strip()[:200] or None, body[:20000],
                 (str(item.get("source") or "")[:40] or None), now.isoformat(timespec="minutes")))
            what = "저장"
        # 사이트 자료를 다시 만들게: 그날 이벤트 상세와 지난 날짜 장중 화면
        conn.execute("UPDATE events SET cause_updated_at=? WHERE substr(start_ts,1,10)=? AND direction=?",
                     (now.isoformat(timespec="minutes"), day, d))
        try:
            conn.execute("DELETE FROM site_items WHERE key=?", (f"intraday:{day}",))
        except Exception:  # noqa: BLE001 - 사이트 자료 표가 아직 없을 때
            pass
    return f"{day} {'상승' if d == 'up' else '하락'} {'하루' if scope == 'day' else st[11:] + '~' + lt[11:]} 원인 {what}"


def facts_only(c: dict | None) -> dict | None:
    """자동 원인 분석에서 결론·원인 후보·추정 문구를 빼고 숫자와 사실 자료만 남김."""
    if not c:
        return c
    out = {k: v for k, v in c.items()
           if k not in ("headline", "confidence", "summary_line", "desk", "candidates", "drivers", "notes")}
    b = c.get("buyer")
    if b:
        lines = []
        for ln in b.get("lines") or []:
            if ln.get("kind") in _BUYER_GUESS:
                continue
            lines.append({**ln, "text": _GUESS_TAIL.sub("", ln.get("text") or "")})
        out["buyer"] = {**b, "lines": lines}
    m = c.get("macro")
    if m:
        out["macro"] = {**m, "verdict": None,
                        "lines": [ln for ln in m.get("lines") or [] if ln.get("kind") in _MACRO_FACTS]}
    return out
