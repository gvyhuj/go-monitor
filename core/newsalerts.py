"""기사·공시 알림 (GitHub 앱 푸시, 급등·급락 알림과 같은 방식).

실행 한 번에 새로 들어온 것을 알림 글 하나로 묶어 보냅니다.
  보도자료  같은 내용 묶음(story)에 그린광학 보도자료가 하나라도 있으면 묶음 전체를 보도자료로 보고,
            실린 매체 수가 늘 때마다 알림 (예: 1개 매체 → 6개 매체)
  외부 기사 보도자료 묶음이 아닌 새 기사, 들어올 때마다
  공시      새 공시, 들어올 때마다 (지금 모으는 공시 전부)

보도자료 판단은 사이트와 같음: 직접 고른 구분(kind_override)이 먼저, 없으면 분류 '회사 발표'.
단 제목에 악재 단어가 있으면 회사가 낸 기사로 보지 않음 (회사가 스스로 악재 기사를 내는 일은 드묾).
'이름만 언급' 검사가 끝난 기사만 셈 (지워질 기사로 알림이 가지 않게).
공개 저장소라 알림에는 제목 없이 건수만 적음.
"""
from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta

from .db import connect, get_state, set_state

KEY = "newsalerts:state"
WINDOW_DAYS = 3                 # 이 기간 안에 처음 본 기사·공시만 살핌 (보낸 기록도 이만큼만 보관)


def _load() -> dict | None:
    st = get_state(KEY)
    try:
        return json.loads(st["value"]) if st and st.get("value") else None
    except (ValueError, TypeError):
        return None


def is_pr(a: dict) -> bool:
    from .articles import NEG
    if a.get("kind_override") in ("pr", "ext"):
        return a["kind_override"] == "pr"
    return a.get("category") == "회사 발표" and not any(w in (a.get("title") or "") for w in NEG)


def snapshot(symbol: str, now: datetime) -> dict:
    """지금 기록에 있는 것: 보도자료 묶음별 매체 수, 외부 기사 id, 공시 번호 (최근 WINDOW_DAYS일 처음 본 것)."""
    from .articles import _key_office
    since = (now - timedelta(days=WINDOW_DAYS)).isoformat(timespec="minutes")
    with connect() as conn:
        recent = [dict(r) for r in conn.execute(
            "SELECT id, story FROM articles WHERE symbol=? AND first_seen>=? AND hidden=0 "
            "AND mention_checked_at IS NOT NULL", (symbol, since))]
        stories = {r["story"] or r["id"] for r in recent}
        group = {}
        if stories:
            q = ",".join("?" * len(stories))
            for r in conn.execute(
                    f"SELECT id, story, office, outlets, title, category, kind_override FROM articles "
                    f"WHERE symbol=? AND hidden=0 AND mention_checked_at IS NOT NULL AND COALESCE(story, id) IN ({q})",
                    (symbol, *stories)):
                group.setdefault(r["story"] or r["id"], []).append(dict(r))
        discl = [r[0] for r in conn.execute(
            "SELECT no FROM disclosures WHERE symbol=? AND first_seen>=?", (symbol, since[:16]))]
    pr, ext = {}, []
    recent_ids = {r["id"] for r in recent}
    for sid, g in group.items():
        if any(is_pr(a) for a in g):
            # 실린 매체 수: 사이트와 같게, 네이버 묶음 수와 실제 모은 매체 수 중 큰 값
            pr[sid] = max(max(int(a.get("outlets") or 1) for a in g), len({_key_office(a.get("office")) for a in g}))
        else:
            ext += [a["id"] for a in g if a["id"] in recent_ids]
    return {"pr": pr, "ext": sorted(ext), "disc": sorted(discl)}


def diff(state: dict, snap: dict) -> dict:
    """지난번 보낸 것과 비교해 새로 알릴 것."""
    pr = [(sid, state["pr"].get(sid, 0), n) for sid, n in snap["pr"].items() if n > state["pr"].get(sid, 0)]
    seen_ext, seen_disc = set(state["ext"]), set(state["disc"])
    return {"pr": pr, "ext": [i for i in snap["ext"] if i not in seen_ext],
            "disc": [d for d in snap["disc"] if d not in seen_disc]}


def merge(state: dict, snap: dict) -> dict:
    """보낸 뒤의 기록: 지금 기간 안에 있는 것만 남김 (보도자료 매체 수는 줄지 않게)."""
    return {"pr": {sid: max(n, state["pr"].get(sid, 0)) for sid, n in snap["pr"].items()},
            "ext": sorted(set(snap["ext"])), "disc": sorted(set(snap["disc"]))}


def title_of(new: dict, now: datetime) -> str:
    parts = []
    for _, before, after in new["pr"]:
        parts.append(f"보도자료 {after}개 매체" if before == 0 else f"보도자료 {before}→{after}개 매체")
    if new["ext"]:
        parts.append(f"외부 기사 {len(new['ext'])}건")
    if new["disc"]:
        parts.append(f"공시 {len(new['disc'])}건")
    return f"[소식] {' · '.join(parts)} ({now:%m/%d %H:%M})"


def send_new(symbol: str, now: datetime | None = None, log=None, post=None) -> str | None:
    """새 기사·공시가 있으면 알림 하나를 보내고 제목을 돌려줌. 처음 켤 때는 지금까지 것을 보낸 것으로 표시만 함."""
    from . import alerts
    now = now or datetime.now()
    post = post or alerts._post_issue
    snap = snapshot(symbol, now)
    state = _load()
    if state is None:               # 처음 켤 때: 지난 기사·공시로 알림이 쏟아지지 않게
        set_state(KEY, json.dumps(merge({"pr": {}, "ext": [], "disc": []}, snap)))
        return None
    new = diff(state, snap)
    if not (new["pr"] or new["ext"] or new["disc"]):
        set_state(KEY, json.dumps(merge(state, snap)))
        return None
    title = title_of(new, now)
    try:
        post(title, alerts._body("새 기사·공시가 들어왔습니다. 내용은 사이트에서 확인하세요."))
    except (urllib.error.URLError, KeyError, ValueError) as ex:
        if log:
            log(f"알림 실패: {ex}")
        return None                 # 보낸 것으로 표시하지 않음 → 다음 실행에 다시
    set_state(KEY, json.dumps(merge(state, snap)))
    if log:
        log(f"알림 보냄: {title}")
    return title
