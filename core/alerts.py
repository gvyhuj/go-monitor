"""급등·급락 알림 (GitHub 앱 푸시).

급등·급락이 새로 감지되면 저장소에 알림 글(이슈)을 하나 올리고 저장소 주인을 @언급합니다.
GitHub 휴대폰 앱이 '언급' 알림을 푸시로 보내 줍니다. 글은 올리자마자 닫아서 목록이 쌓이지 않게 합니다.

저장소가 공개라서 알림 글에는 '급등 +8.7% (10:12)' 같은 공개 시세 정보만 적고,
원인 분석은 비밀번호가 걸린 사이트에서만 보게 합니다.

같은 날 같은 방향은 한 번만, 그 뒤 ±10% 이상('대형')으로 커지면 한 번 더 보냅니다.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime

from .db import connect, get_state, set_state

KEY = "alerts:sent"


def _sent() -> set:
    st = get_state(KEY)
    try:
        return set(json.loads(st["value"])) if st else set()
    except (ValueError, TypeError):
        return set()


def _save(keys: set):
    set_state(KEY, json.dumps(sorted(keys)[-400:]))


def _post_issue(title: str, body: str) -> int | None:
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return None
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
           "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"}
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues", method="POST", headers=hdr,
                                 data=json.dumps({"title": title, "body": body}).encode())
    with urllib.request.urlopen(req, timeout=15) as r:
        num = json.loads(r.read())["number"]
    try:                                    # 알림은 이미 나갔으니 글은 닫아 둠
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues/{num}", method="PATCH", headers=hdr,
                                     data=json.dumps({"state": "closed", "state_reason": "completed"}).encode())
        urllib.request.urlopen(req, timeout=15).close()
    except urllib.error.URLError:
        pass
    return num


def _site() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def _body(extra: str = "") -> str:
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("GITHUB_REPOSITORY", "/").split("/")[0]
    return (f"@{owner} {extra}\n\n원인 분석은 사이트에서 확인하세요: {_site()}\n\n"
            "_자동 알림입니다. 이 글은 알림용이라 바로 닫힙니다._")


def init_if_needed(symbol: str):
    """처음 켤 때 지난 기록으로 알림이 쏟아지지 않도록, 지금까지의 이상변동은 보낸 것으로 표시."""
    if get_state(KEY):
        return
    with connect() as conn:
        rows = conn.execute("SELECT start_ts, direction, tier FROM events WHERE symbol=?", (symbol,)).fetchall()
    _save({f"{r[0][:10]}:{r[1]}:{r[2] or '일반'}" for r in rows} | {"init"})


def send_new(symbol: str, log=None) -> int:
    """새로 감지된 이상변동 알림. 보낸 건수를 돌려줌."""
    init_if_needed(symbol)
    sent = _sent()
    today = datetime.now().date().isoformat()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, event_type, start_ts, last_ts, direction, peak_return_5m, tier FROM events "
            "WHERE symbol=? AND substr(start_ts,1,10) >= ? ORDER BY start_ts", (symbol, today))]
    n = 0
    for e in rows:
        tier = e.get("tier") or "일반"
        key = f"{e['start_ts'][:10]}:{e['direction']}:{tier}"
        if key in sent:
            continue
        word = "급등" if e["direction"] == "up" else "급락"
        mv = e.get("peak_return_5m") or 0
        when = (f"{e['start_ts'][5:10].replace('-', '/')} 종가 기준" if e["event_type"] == "daily"
                else f"{e['start_ts'][5:10].replace('-', '/')} {e['start_ts'][11:16]}~{e['last_ts'][11:16]}")
        title = f"{word} {mv*100:+.1f}% ({when})" + (" · 대형" if tier == "대형" else "")
        try:
            _post_issue(title, _body(f"주가가 {'30분 안에' if e['event_type'] != 'daily' else '하루 동안'} "
                                      f"{mv*100:+.1f}% 움직였습니다."))
            sent.add(key)
            if tier == "대형":
                sent.add(f"{e['start_ts'][:10]}:{e['direction']}:일반")
            n += 1
            if log:
                log(f"알림 보냄: {title}")
        except (urllib.error.URLError, KeyError, ValueError) as ex:
            if log:
                log(f"알림 실패: {ex}")
            break
    _save(sent)
    return n


def send_test(log=None) -> bool:
    force = os.environ.get("GO_ALERT_TEST") == "1"
    if get_state("alerts:test_v1") and not force:
        return False
    try:
        _post_issue(f"[알림 시험] 주가 감시 알림 ({datetime.now():%m/%d %H:%M})",
                    _body("이 알림이 휴대폰 GitHub 앱에 보이면 설정이 끝난 것입니다. "
                          "앞으로 급등·급락이 감지되면 이런 알림이 옵니다."))
        set_state("alerts:test_v1", datetime.now().isoformat(timespec="minutes"))
        return True
    except (urllib.error.URLError, KeyError, ValueError) as ex:
        if log:
            log(f"시험 알림 실패: {ex}")
        return False
