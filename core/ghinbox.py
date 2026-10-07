"""웹사이트에서 보낸 요청(기사 직접 등록·목록에서 빼기)을 GitHub 이슈로 받아 반영.

사이트의 버튼을 누르면 내용이 채워진 'GitHub 새 이슈' 화면이 열리고, [Create]를 누르면 이슈가 생깁니다.
다음 자동 실행 때 저장소 주인(GITHUB_REPOSITORY_OWNER)이 만든 이슈만 반영하고, 댓글을 남긴 뒤 닫습니다.
다른 사람이 만든 이슈는 건드리지 않습니다.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
TYPES = {"등록": "pr", "빼기": "hide", "다시표시": "show", "삭제": "delete_pr"}


def _req(method: str, path: str, body=None):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "stock-monitor",
        **({"Content-Type": "application/json"} if data else {}),
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def parse(issue: dict, unseal=None) -> dict | None:
    """이슈 하나 → 요청 dict. 형식이 아니면 None.
    새 형식: 제목 '[요청] …', 본문의 ```enc 블록에 사이트 열쇠로 암호화한 요청 (공개 저장소에서 내용이 안 보임)."""
    title = str(issue.get("title") or "").strip()
    if title.startswith("[요청]"):
        e = re.search(r"```enc\s*([A-Za-z0-9+/=\s]+?)\s*```", str(issue.get("body") or ""), re.S)
        if not e or unseal is None:
            raise ValueError("암호화된 요청을 찾지 못했습니다.")
        try:
            item = unseal(re.sub(r"\s", "", e.group(1)))
        except Exception:
            raise ValueError("요청을 풀지 못했습니다 (사이트 비밀번호가 바뀐 뒤 만든 요청일 수 있습니다).")
        if not isinstance(item, dict) or item.get("type") not in ("pr", "hide", "show", "delete_pr"):
            raise ValueError("알 수 없는 요청입니다.")
        if item["type"] == "pr" and (not item.get("title") or not item.get("published_ts")):
            raise ValueError("기사 제목과 발표 시각이 필요합니다.")
        return item
    m = re.match(r"^\[(등록|빼기|다시표시|삭제)\]\s*(.*)$", title)
    if not m:
        return None
    kind = TYPES[m.group(1)]
    body = str(issue.get("body") or "")
    item = None
    j = re.search(r"```json\s*(\{.*?\})\s*```", body, re.S)
    if j:
        try:
            item = json.loads(j.group(1))
        except ValueError:
            item = None
    if not isinstance(item, dict):
        item = {}
    item["type"] = kind                       # 제목의 종류가 우선
    if kind == "pr":
        item.setdefault("title", m.group(2).strip())
        if not item.get("published_ts"):
            t = re.search(r"발표 시각:\s*(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2})", body)
            if t:
                item["published_ts"] = f"{t.group(1)}T{t.group(2).zfill(5)}"
        if not item.get("url"):
            u = re.search(r"주소:\s*(https?://\S+)", body)
            if u:
                item["url"] = u.group(1)
        if not item.get("title") or not item.get("published_ts"):
            raise ValueError("기사 제목과 발표 시각이 필요합니다.")
    else:
        item["id"] = str(item.get("id") or m.group(2)).strip()
        if not item["id"]:
            raise ValueError("대상 기사 번호가 없습니다.")
    return item


def process(apply, log=None, unseal=None) -> int:
    repo = os.environ.get("GITHUB_REPOSITORY")
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    if not repo or not owner or not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        return 0
    issues = _req("GET", f"/repos/{repo}/issues?state=open&creator={owner}&per_page=50&sort=created&direction=asc") or []
    n = 0
    for it in issues:
        if it.get("pull_request") or (it.get("user") or {}).get("login") != owner:
            continue
        try:
            item = parse(it, unseal)
        except ValueError as e:
            _req("POST", f"/repos/{repo}/issues/{it['number']}/comments", {"body": f"반영하지 못했습니다: {e}"})
            _req("PATCH", f"/repos/{repo}/issues/{it['number']}", {"state": "closed", "state_reason": "not_planned"})
            continue
        if item is None:
            continue                          # 사이트 요청이 아닌 일반 이슈는 그대로 둠
        try:
            apply(item)
            msg = "사이트에 반영했습니다. 몇 분 뒤 사이트를 새로 고치면 보입니다."
            reason = "completed"
            n += 1
        except Exception as e:                # noqa: BLE001
            msg, reason = f"반영하지 못했습니다: {e}", "not_planned"
        _req("POST", f"/repos/{repo}/issues/{it['number']}/comments", {"body": msg})
        _req("PATCH", f"/repos/{repo}/issues/{it['number']}", {"state": "closed", "state_reason": reason})
        if log:
            log(f"사이트 요청 #{it['number']} {item.get('type')}: {msg}")
    return n
