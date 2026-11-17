"""기사·공시 알림 (GitHub 앱 푸시, 급등·급락 알림과 같은 방식).

실행 한 번에 새로 들어온 것을 알림 글 하나로 묶어 보냅니다.
  기사  같은 내용 묶음(story, 사이트에서 'n개 매체'로 한 줄에 묶이는 것)마다 처음 들어왔을 때 한 번만.
        같은 묶음에 매체가 더 붙어도 다시 알리지 않음. 묶음에 자사 보도자료가 하나라도 있으면 보도자료로 셈
  공시  새 공시, 들어올 때마다 (지금 모으는 공시 전부)
지난 기사를 뒤늦게 찾아 넣은 것(기사 시각이 WINDOW_DAYS보다 오래된 것)은 알리지 않음.

보도자료 판단은 사이트와 같음: 직접 고른 구분(kind_override)이 먼저, 없으면 분류 '회사 발표'.
단 제목에 악재 단어가 있으면 회사가 낸 기사로 보지 않음 (회사가 스스로 악재 기사를 내는 일은 드묾).
'이름만 언급' 검사가 끝난 기사만 셈 (지워질 기사로 알림이 가지 않게).
공개 저장소라 알림에는 제목 없이 구분·시각·매체와 원문 링크만 적음.
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
    """지금 기록에 있는 것: 최근 WINDOW_DAYS일에 나온 기사 묶음(묶음별 기사 id·보도자료 여부·매체 수), 공시 번호."""
    from .articles import _key_office
    since = (now - timedelta(days=WINDOW_DAYS)).isoformat(timespec="minutes")
    with connect() as conn:
        recent = [dict(r) for r in conn.execute(
            "SELECT id, story FROM articles WHERE symbol=? AND first_seen>=? AND ts>=? AND hidden=0 "
            "AND mention_checked_at IS NOT NULL", (symbol, since, since))]
        stories = {r["story"] or r["id"] for r in recent}
        group = {}
        if stories:
            q = ",".join("?" * len(stories))
            for r in conn.execute(
                    f"SELECT id, story, ts, office, url, outlets, title, category, kind_override FROM articles "
                    f"WHERE symbol=? AND hidden=0 AND mention_checked_at IS NOT NULL AND COALESCE(story, id) IN ({q})",
                    (symbol, *stories)):
                group.setdefault(r["story"] or r["id"], []).append(dict(r))
        discl = [r[0] for r in conn.execute(
            "SELECT no FROM disclosures WHERE symbol=? AND first_seen>=?", (symbol, since[:16]))]
    out = {}
    for sid, g in group.items():
        # 실린 매체 수: 사이트와 같게, 네이버 묶음 수와 실제 모은 매체 수 중 큰 값
        first = min(g, key=lambda a: (a.get("ts") or "9999", a["id"]))      # 가장 먼저 나온 기사 (사이트의 대표)
        out[sid] = {"ids": sorted(a["id"] for a in g), "pr": any(is_pr(a) for a in g),
                    "outlets": max(max(int(a.get("outlets") or 1) for a in g), len({_key_office(a.get("office")) for a in g})),
                    "first": {"id": first["id"], "ts": first.get("ts") or "", "office": first.get("office") or "", "url": first.get("url") or ""}}
    return {"stories": out, "disc": sorted(discl)}


def diff(state: dict, snap: dict) -> dict:
    """지난번 보낸 것과 비교해 새로 알릴 것. 묶음 안 기사 중 하나라도 이미 알렸으면 그 묶음은 알린 것
    (묶음 기준 기사가 바뀌어 묶음 번호가 달라져도 다시 알리지 않게)."""
    seen, seen_disc = set(state["ids"]), set(state["disc"])
    new = [st for st in snap["stories"].values() if not seen & set(st["ids"])]
    return {"pr": [st for st in new if st["pr"]], "ext": [st for st in new if not st["pr"]],
            "disc": [d for d in snap["disc"] if d not in seen_disc]}


def merge(state: dict, snap: dict) -> dict:
    """보낸 뒤의 기록: 지금 기간 안에 있는 기사 id·공시 번호."""
    return {"v": 2, "ids": sorted({i for st in snap["stories"].values() for i in st["ids"]}),
            "disc": sorted(set(snap["disc"]))}


def title_of(new: dict, now: datetime) -> str:
    parts = []
    if new["pr"]:
        n = max(st["outlets"] for st in new["pr"])
        parts.append(f"보도자료 {len(new['pr'])}건" + (f" ({n}개 매체)" if len(new["pr"]) == 1 and n > 1 else ""))
    if new["ext"]:
        parts.append(f"외부 기사 {len(new['ext'])}건")
    if new["disc"]:
        parts.append(f"공시 {len(new['disc'])}건")
    return f"[소식] {' · '.join(parts)} ({now:%m/%d %H:%M})"


def body_of(new: dict) -> str:
    """알림 본문: 하나씩 '구분 · 시각 · 매체 (외 n곳) → 원문 링크'(기사 원문, 공시는 DART·KIND).
    공개 저장소라 제목은 넣지 않음 (주소 자체에는 회사 이름이 없음)."""
    import os
    from .dart import viewer_url
    from .kind import VIEWER
    lines = []
    for kind, label in (("pr", "보도자료"), ("ext", "외부 기사")):
        for st in sorted(new[kind], key=lambda x: x["first"]["ts"]):
            f, more = st["first"], st["outlets"] - 1
            lines.append(f"- {label} · {f['ts'][11:16]} {f['office']}{f' 외 {more}곳' if more > 0 else ''} → {f['url']}")
    if new["disc"]:
        with connect() as conn:
            ts = dict(conn.execute(f"SELECT no, ts FROM disclosures WHERE no IN ({','.join('?' * len(new['disc']))})",
                                   new["disc"]).fetchall())
        for no in sorted(new["disc"], key=lambda n: ts.get(n) or ""):
            url = VIEWER.format(no[1:]) if no.startswith("K") else viewer_url(no)      # K로 시작 = 거래소(KIND) 공시
            lines.append(f"- 공시 · {(ts.get(no) or '')[11:16] or '시각 미상'} → {url}")
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("GITHUB_REPOSITORY", "/").split("/")[0]
    return (f"@{owner} 새 기사·공시가 들어왔습니다.\n\n" + "\n".join(lines)
            + "\n\n_자동 알림입니다. 이 글은 알림용이라 바로 닫힙니다._")


def send_new(symbol: str, now: datetime | None = None, log=None, post=None) -> str | None:
    """새 기사·공시가 있으면 알림 하나를 보내고 제목을 돌려줌. 처음 켤 때는 지금까지 것을 보낸 것으로 표시만 함."""
    from . import alerts
    now = now or datetime.now()
    post = post or alerts._post_issue
    snap = snapshot(symbol, now)
    state = _load()
    if state is None or state.get("v") != 2:   # 처음 켤 때(또는 기록 형식이 바뀐 뒤): 지난 기사·공시로 알림이 쏟아지지 않게
        set_state(KEY, json.dumps(merge({}, snap)))
        return None
    new = diff(state, snap)
    if not (new["pr"] or new["ext"] or new["disc"]):
        set_state(KEY, json.dumps(merge(state, snap)))
        return None
    title = title_of(new, now)
    try:
        post(title, body_of(new))
    except (urllib.error.URLError, KeyError, ValueError) as ex:
        if log:
            log(f"알림 실패: {ex}")
        return None                 # 보낸 것으로 표시하지 않음 → 다음 실행에 다시
    set_state(KEY, json.dumps(merge(state, snap)))
    if log:
        log(f"알림 보냄: {title}")
    return title
