"""원인은 직접 붙여넣은 글(ChatGPT 등)로, 숫자는 자동 기록으로.

자동 원인 추정(결론·원인 후보·'관련 기사 N건' 같은 배경 추정)은 실제와 다르게 엮는 경우가 있어 화면에서 뺍니다.
숫자와 사실 자료(몫 나누기, 창구 매매, 비교 종목, 테마 등락, 기사 목록)는 그대로 둡니다.

연결 규칙
- 하루 기록: 같은 날의 '하루' 원인 (장중에 여러 번 넣으면 넣은 시각·그때 등락과 함께 모두 남고, 최신이 대표)
- 장중 순간: 같은 날·같은 방향이고 구간이 겹치는 '장중' 원인만 (하루 원인은 장중 순간에 보여 주지 않음 —
  각 줄에는 그 줄에 넣은 원인만 보이고 그 줄에서 지움)
"""
from __future__ import annotations

import re
from datetime import datetime

from .db import connect

# 업종·테마·거시 배경 중 사실 자료로 남길 줄 (나머지 '주제별 기사 N건'은 추정 연결이라 뺌)
_MACRO_FACTS = {"이 종목 테마", "이날 강한 테마", "전날 밤 해외", "시장 일정"}
# 창구 단서 중 추정인 줄
_BUYER_GUESS = {"배경 이슈"}
_GUESS_TAIL = re.compile(r"\s*(이 종목만 겨냥했다기보다[^.]*\.|(?:업종|테마) 전체보다[^.]*\.)")


def _scope(event_type: str) -> str:
    return "day" if event_type == "daily" else "intraday"


def find(conn, ev: dict) -> dict | None:
    """이벤트(행)에 붙일 직접 입력 원인. 그 줄(하루 또는 장중 순간)에 넣은 원인만."""
    day, d = ev["start_ts"][:10], ev["direction"]
    if _scope(ev["event_type"]) == "intraday":
        r = conn.execute("SELECT * FROM manual_causes WHERE date=? AND direction=? AND scope='intraday' "
                         "AND start_ts<=? AND last_ts>=? ORDER BY updated_at DESC LIMIT 1",
                         (day, d, ev["last_ts"], ev["start_ts"])).fetchone()
    else:
        # 하루 원인은 그날 여러 번 넣을 수 있음 (장중에 궁금할 때마다, 그때의 등락과 함께). 최신이 대표
        rows = [dict(x) for x in conn.execute(
            "SELECT * FROM manual_causes WHERE date=? AND scope='day' ORDER BY start_ts DESC, updated_at DESC", (day,))]
        if not rows:
            return None
        return {**rows[0], "inherited": False, "entries": rows}
    return {**dict(r), "inherited": False} if r else None


_CONF_RE = re.compile(r"[(\[（]?\s*신뢰도\s*[:：]?\s*(높음|중간|낮음)\s*[)\]）]?")
_SUMMARY_LABEL = re.compile(r"^(한\s*줄\s*요약|요약|결론|종합\s*판단|종합)\s*[:：\-–—]?\s*")


def _clean(ln: str) -> str:
    t = re.sub(r"^[>*\-•■□▶▷◆◇●○※\s\d.)]+", "", ln)
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t).replace("**", "").strip()


def _is_title(t: str) -> bool:
    """'종합판단', '그린광학 주가 변동 원인 분석 (2026.10.12)'처럼 내용 없는 제목 줄"""
    if len(t) <= 8 and not re.search(r"[.다요음]$", t):
        return True
    return bool(re.search(r"원인\s*분석", t) and re.search(r"\d{4}[.\-/]\s*\d{1,2}", t))


def summary(m: dict | None) -> str | None:
    """목록에 보일 한 줄: 입력한 요약 → 없으면 본문의 '한 줄 요약' 줄 → 제목 줄을 건너뛴 첫 문장 (80자).
    신뢰도 표시는 confidence()로 따로 보여 주므로 뺌."""
    if not m:
        return None
    if (m.get("headline") or "").strip():
        t = _CONF_RE.sub("", m["headline"].replace("**", "")).strip()
        t = _SUMMARY_LABEL.sub("", t).strip() or t
        return t[:120] or None
    lines = [ln for ln in (m.get("body") or "").splitlines() if not re.match(r"^\s*#", ln)]
    cleaned = [_clean(ln) for ln in lines]
    pick = None
    for i, t in enumerate(cleaned):                 # 'ChatGPT에게 묻기'는 첫 줄을 '한 줄 요약 (신뢰도 …)'로 달라고 함
        if re.match(r"^한\s*줄\s*요약", t):
            rest = _SUMMARY_LABEL.sub("", _CONF_RE.sub("", t)).strip(" :：")
            pick = rest or next((x for x in cleaned[i + 1:] if x), None)
            break
    if pick is None:
        pick = next((x for x in cleaned if x and not _is_title(x)), None) or next((x for x in cleaned if x), None)
    if not pick:
        return None
    pick = _SUMMARY_LABEL.sub("", _CONF_RE.sub("", pick)).strip()
    return pick[:80] + ("…" if len(pick) > 80 else "") if pick else None


def confidence(m: dict | None) -> str | None:
    """요약·본문 앞부분에 적힌 '신뢰도 높음/중간/낮음'"""
    if not m:
        return None
    for src in (m.get("headline") or "", "\n".join((m.get("body") or "").splitlines()[:6])):
        x = _CONF_RE.search(src)
        if x:
            return x.group(1)
    return None


def save(item: dict, now: datetime) -> str:
    day = str(item.get("date") or "")[:10]
    datetime.fromisoformat(day)                          # 형식 확인
    d = item.get("direction")
    if d not in ("up", "down"):
        raise ValueError("방향(up/down)이 필요합니다.")
    scope = "day" if item.get("scope") == "day" else "intraday"
    # 하루 원인은 넣은 시각(at_ts)마다 따로 남김 (예전 방식처럼 at_ts가 없으면 하루에 하나)
    st = str(item.get("start_ts") or "")[:16] if scope == "intraday" else str(item.get("at_ts") or "")[:16]
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
                "INSERT INTO manual_causes(date, direction, scope, start_ts, last_ts, headline, body, source, updated_at, move) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(date, direction, scope, start_ts) DO UPDATE SET "
                "last_ts=excluded.last_ts, headline=excluded.headline, body=excluded.body, source=excluded.source, "
                "updated_at=excluded.updated_at, move=COALESCE(excluded.move, manual_causes.move)",
                (day, d, scope, st, lt, str(item.get("headline") or "").strip()[:200] or None, body[:20000],
                 (str(item.get("source") or "")[:40] or None), now.isoformat(timespec="minutes"),
                 float(item["move"]) if isinstance(item.get("move"), (int, float)) else None))
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
