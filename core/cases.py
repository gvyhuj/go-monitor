"""사건 묶음: 시간이 떨어진 기사·공시를 '같은 일'로 묶음 (같은 내용 묶음 story 위의 층).

묶는 기준 (제목만 봄, 본문은 저장하지 않음):
- 같은 상대 회사 또는 같은 금액이 14일 안에 다시 나오면 같은 사건
- 둘 다 없으면 같은 날 + 같은 대표 주제일 때만 같은 사건
- 같은 종류의 공시(계약·실적·IR·증자·전환사채)가 사건 첫 기사보다 36시간 안에 먼저 나왔으면 그 공시를 사건에 넣고 기준 시점으로 삼음
단순 언급·시황 기사는 사건을 만들지 않음.

관리자가 고친 묶음(overrides, system_state 'cases:overrides')을 자동 묶음 위에 적용:
{항목 id: 같은 사건으로 옮길 다른 항목 id 또는 "self"(따로 떼어 새 사건)}
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

WINDOW = timedelta(days=14)
DISC_BEFORE = timedelta(hours=36)
NOISE = ("단순 언급", "시황·특징주")
# '와/과' 앞에 오지만 상대 회사 이름이 아닌 말
GENERIC = {"기업", "방산기업", "우주기업", "고객사", "정부", "기관", "업체", "회사", "기술", "협력", "공동", "글로벌",
           "국내", "해외", "대기업", "중소기업", "연구소", "대학", "지자체", "그린광학"}
AMOUNT = re.compile(r"(\d+(?:[.,]\d+)*)\s*(조|억)\s*(?:원|규모)?")
PARTNER_WITH = re.compile(r"['‘\"“]?([A-Za-z0-9가-힣&·]{2,25})['’\"”]?(?:와|과)\s")
PARTNER_JOINT = re.compile(r"^([A-Za-z0-9가-힣&]{2,20})\s*[-·]\s*(?:\(주\)|㈜)?\s*그린광학")


def amount(title: str) -> float | None:
    """제목의 금액 (억 원). 없으면 None."""
    m = AMOUNT.search(title or "")
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return v * 10000 if m.group(2) == "조" else v


def partner(title: str, company: str = "그린광학") -> str | None:
    """제목에서 상대 회사로 보이는 이름. 못 찾으면 None."""
    t = title or ""
    m = PARTNER_JOINT.search(t)
    if m and m.group(1) not in GENERIC:
        return m.group(1)
    rest = t.split(company, 1)[1] if company in t else t
    for m in PARTNER_WITH.finditer(rest + " "):
        name = m.group(1)
        if name not in GENERIC and not name.endswith(("기업", "업체")):
            return name
    m = re.match(r"^([A-Za-z0-9가-힣]{2,20}),\s*(?:\(주\)|㈜)?\s*" + re.escape(company), t)   # '충북테크노파크, ㈜그린광학 …'
    if m and m.group(1) not in GENERIC:
        return m.group(1)
    return None


def _same_partner(a: str | None, b: str | None) -> bool:
    return bool(a and b) and (a == b or a.startswith(b) or b.startswith(a))


OVERRIDES_KEY = "cases:overrides"


def load_overrides() -> dict:
    import json
    from .db import get_state
    st = get_state(OVERRIDES_KEY)
    try:
        v = json.loads(st["value"]) if st and st.get("value") else {}
    except (ValueError, TypeError):
        v = {}
    return v if isinstance(v, dict) else {}


def save_override(items: list[str], to: str | None):
    """items를 to(다른 항목 id, 'self' = 따로 떼기, None = 자동으로 되돌리기)로."""
    import json
    from .db import set_state
    cur = load_overrides()
    for i in items:
        if to:
            cur[str(i)] = str(to)
        else:
            cur.pop(str(i), None)
    set_state(OVERRIDES_KEY, json.dumps(cur, ensure_ascii=False))


def _apply(cases: list[dict], overrides: dict) -> list[dict]:
    where = {i["id"]: c for c in cases for i in c["items"]}
    for iid, to in overrides.items():
        c = where.get(iid)
        if c is None:
            continue
        if to == "self":
            if len(c["items"]) == 1:
                continue
            target = {"items": [], "manual": True}
            cases.append(target)
        else:
            target = where.get(to)
            if target is None or target is c:
                continue
        item = next(i for i in c["items"] if i["id"] == iid)
        c["items"].remove(item)
        target["items"].append(item)
        c["manual"] = target["manual"] = True
        where[iid] = target
    return [c for c in cases if c["items"]]


def build(articles: list[dict], disclosures: list[dict], company: str = "그린광학",
          overrides: dict | None = None) -> list[dict]:
    """articles: api_articles의 items (같은 내용 묶음의 대표), disclosures: disclosure_rows. 최근 사건이 먼저."""
    from .articles import DISCLOSURE_MATCH
    leads = sorted((a for a in articles if a.get("category") not in NOISE and not a.get("hidden")),
                   key=lambda a: a["ts"])
    cases: list[dict] = []
    for a in leads:
        t = datetime.fromisoformat(a["ts"][:16])
        amt, who = amount(a["title"]), partner(a["title"], company)
        found = None
        for c in reversed(cases):
            if t - c["_start"] > WINDOW:
                continue
            if (who and any(_same_partner(who, p) for p in c["_partners"])) or (amt and amt in c["_amounts"]):
                found = c
                break
            if a["ts"][:10] in c["_days"] and a.get("topic") and a.get("topic") in c["_topics"]:
                found = c
                break
        if found is None:
            found = {"_start": t, "_partners": set(), "_amounts": set(), "_days": set(), "_topics": set(), "items": []}
            cases.append(found)
        found["items"].append({"type": "article", "id": a["id"], "ts": a["ts"], "title": a["title"],
                               "office": a.get("office"), "kind": a.get("kind"), "topic": a.get("topic"),
                               "outlets": a.get("outlets_total") or 1})
        found["_days"].add(a["ts"][:10])
        if a.get("topic"):
            found["_topics"].add(a["topic"])
        if who:
            found["_partners"].add(who)
        if amt:
            found["_amounts"].add(amt)
    used = set()
    for c in cases:
        first = datetime.fromisoformat(c["items"][0]["ts"][:16])
        words = " ".join(i["title"] for i in c["items"])
        for d in disclosures:
            if d["id"] in used or d.get("untimed"):
                continue
            dt = datetime.fromisoformat(d["ts"][:16])
            if not first - DISC_BEFORE <= dt <= first:
                continue
            src = f"{d.get('kind') or ''} {d.get('title') or ''}"
            if any(key in src and any(w in words for w in ws) for key, ws in DISCLOSURE_MATCH.items()):
                c["items"].insert(0, {"type": "disclosure", "id": d["id"], "ts": d["ts"], "title": d["title"],
                                      "office": d.get("filer"), "kind": "disclosure"})
                used.add(d["id"])
                break
    cases = _apply(cases, overrides or {})
    out = []
    for c in cases:
        items = sorted(c["items"], key=lambda i: i["ts"])
        arts = [i for i in items if i["type"] == "article"]
        lead = next((i for i in arts if i["kind"] == "pr"), next((i for i in arts if i["kind"] == "company"),
                                                                   arts[0] if arts else items[0]))
        partners = {w for w in (partner(i["title"], company) for i in arts) if w}
        amounts = {v for v in (amount(i["title"]) for i in arts) if v}
        topics = {i["topic"] for i in arts if i.get("topic")}
        out.append({"id": f"c{items[0]['id']}", "start": items[0]["ts"], "end": items[-1]["ts"],
                    "title": lead["title"], "lead": lead["id"],
                    "kind": "pr" if any(i["kind"] == "pr" for i in arts) else
                            "company" if any(i["kind"] == "company" for i in arts) else
                            "unknown" if arts and all(i["kind"] == "unknown" for i in arts) else "ext",
                    "partner": max(partners, key=len) if partners else None,
                    "amount": max(amounts) if amounts else None,
                    "topic": next(iter(sorted(topics)), None), "manual": bool(c.get("manual")),
                    "n_articles": len(arts), "n_outlets": sum(i.get("outlets") or 1 for i in arts),
                    "n_days": len({i["ts"][:10] for i in items}),
                    "items": items})
    out.sort(key=lambda c: c["start"], reverse=True)
    return out
