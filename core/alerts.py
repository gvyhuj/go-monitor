"""급등·급락 알림 (GitHub 앱 푸시).

급등·급락이 새로 감지되면 저장소에 알림 글(이슈)을 하나 올리고 저장소 주인을 @언급합니다.
GitHub 휴대폰 앱이 '언급' 알림을 푸시로 보내 줍니다. 글은 올리자마자 닫아서 목록이 쌓이지 않게 합니다.

저장소가 공개라서 알림 글에는 '급등 +8.7% (10:12)' 같은 공개 시세 정보만 적고,
원인 분석은 비밀번호가 걸린 사이트에서만 보게 합니다.

같은 날 같은 방향은 한 번만, 그 뒤 ±10% 이상('대형')으로 커지면 한 번 더 보냅니다.
장중에 ±3% 안으로 회복했다가 다시 기준을 넘으면 한 번 더 보냅니다.
여러 날 누적(5거래일 ±10%)도 알립니다.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta

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


def _body(extra: str = "", event_id: int | None = None) -> str:
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("GITHUB_REPOSITORY", "/").split("/")[0]
    link = f"{_site()}#/events/{event_id}" if event_id and _site() else _site()   # 누르면 그 이상변동 상세가 바로 열림
    return (f"@{owner} {extra}\n\n원인 분석은 사이트에서 확인하세요: {link}\n\n"
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
            "SELECT id, event_type, start_ts, last_ts, direction, peak_return_5m, tier, status, detection_rule FROM events "
            "WHERE symbol=? AND substr(start_ts,1,10) >= ? ORDER BY start_ts", (symbol, today))]
    from .detector import recover_key
    rec = get_state(recover_key(symbol, today))
    since = rec["value"] if rec and rec.get("value") else None   # 오늘 회복한 시각 (그 뒤 다시 넘으면 새 알림)
    sfx = f"@{since}" if since else ""
    n = 0
    for e in rows:
        tier = e.get("tier") or "일반"
        if tier in ("관찰", "요청"):            # 관찰(3~5%)·직접 요청한 분석은 사이트 기록만
            continue
        if since and e["event_type"] == "intraday" and e["start_ts"][11:16] <= since:
            continue                        # 회복 전 움직임은 이미 알림이 나간 일
        key = f"{e['start_ts'][:10]}:{e['direction']}:{tier}{sfx}"
        if key in sent:
            continue
        word = "급등" if e["direction"] == "up" else "급락"
        mv = e.get("peak_return_5m") or 0
        running = e["event_type"] == "daily" and e.get("status") == "open"   # 장중 전일 대비 누적
        md = e['start_ts'][5:10].replace('-', '/')
        if running:
            m = re.search(r"→ (\d\d:\d\d)", e.get("detection_rule") or "")
            when = f"{md} {m.group(1) if m else datetime.now().strftime('%H:%M')} 전일 대비"
        elif e["event_type"] == "daily":
            when = f"{md} 종가 기준"
        else:
            when = f"{md} {e['start_ts'][11:16]}~{e['last_ts'][11:16]}"
        title = f"{word} {mv*100:+.1f}% ({when})" + (" · 대형" if tier == "대형" else "")
        try:
            _post_issue(title, _body(f"주가가 {'장중 전일 종가 대비' if running else e['start_ts'][11:16] + '부터' if e['event_type'] != 'daily' else '하루 동안'} "
                                      f"{mv*100:+.1f}% 움직였습니다.", e.get("id")))
            sent.add(key)
            if tier == "대형":
                sent.add(f"{e['start_ts'][:10]}:{e['direction']}:일반{sfx}")
            n += 1
            if log:
                log(f"알림 보냄: {title}")
        except (urllib.error.URLError, KeyError, ValueError) as ex:
            if log:
                log(f"알림 실패: {ex}")
            break
    _save(sent)
    return n + send_multiday(symbol, log)


def send_multiday(symbol: str, log=None) -> int:
    """여러 날 누적 급등·급락 알림 (장 마감 뒤 확정 종가 기준, 최근 3일 안의 기록만)."""
    from .detector import multiday_key
    st = get_state(multiday_key(symbol))
    try:
        items = json.loads(st["value"]) if st else []
    except (ValueError, TypeError):
        return 0
    sent = _sent()
    keys = {f"multi:{m['date']}:{m['direction']}": m for m in items}
    if not get_state("alerts:multiday_init"):     # 처음 켤 때 지난 기록으로 알림이 쏟아지지 않게
        _save(sent | set(keys))
        set_state("alerts:multiday_init", datetime.now().isoformat(timespec="minutes"))
        return 0
    recent = (datetime.now() - timedelta(days=3)).date().isoformat()
    n = 0
    for key, m in keys.items():
        if key in sent or m["date"] < recent:
            continue
        word = "상승" if m["direction"] == "up" else "하락"
        md = lambda d: d[5:10].replace("-", "/")
        title = f"{m['days']}일 누적 {word} {m['return']*100:+.1f}% ({md(m['from'])} 종가 → {md(m['date'])} 종가)"
        try:
            _post_issue(title, _body(f"주가가 {m['days']}거래일 동안 {m['return']*100:+.1f}% 움직였습니다 "
                                      "(하루하루는 기준 밑이어도 쌓이면 알립니다)."))
            sent.add(key)
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


# ------------------------------------------------------------------ 갱신 멈춤 알림
# A: 수집은 정상인데 사이트(GitHub Pages)에 새 자료가 30분 넘게 안 올라감
# B: 네이버 시세를 30분 넘게 정상으로 받지 못함 (값이 그대로인 것과는 구분: '응답 성공 시각'으로 봄)
# 같은 종류는 하루 한 번만 알리고, 다시 정상이 되면 '복구'를 한 번 알림.
# 자동 실행 자체가 멈춘 경우는 알림을 보낼 주체가 없으므로 사이트 경고로만 알림.
STALE_MIN = 30
HEALTH_WORD = {"A": "사이트 게시 지연", "B": "시세 수집 실패"}


def is_trading_day(now: datetime, quote: dict | None, data_last_ok: datetime | None) -> bool:
    """오늘이 거래일인지: 휴장일 목록 대신 네이버 장 상태로 판단 (선거일 같은 임시 휴장도 맞음).
    네이버를 최근에 못 받아 상태를 모르면 평일을 거래일로 봄."""
    if now.weekday() >= 5:
        return False
    fresh = data_last_ok is not None and now - data_last_ok <= timedelta(minutes=STALE_MIN)
    if not fresh or not quote:
        return True
    return quote.get("market_status") == "OPEN" or str(quote.get("traded_at") or "")[:10] == now.date().isoformat()


def market_hours(now: datetime, trading_day: bool) -> bool:
    return trading_day and now.weekday() < 5 and "09:00" <= now.strftime("%H:%M") <= "15:30"


def health_problems(now: datetime, *, data_last_ok: datetime | None, site_modified: datetime | None,
                    build_streak_start: datetime | None, trading_day: bool) -> set:
    """지금 장중에 멈춰 있는 것들 ('A', 'B')."""
    if not market_hours(now, trading_day):
        return set()
    late = now - timedelta(minutes=STALE_MIN)
    bad = set()
    if data_last_ok is None or data_last_ok < late:
        bad.add("B")
    # 사이트 파일이 오래됐어도, 자동 실행 자신이 30분 동안 계속 돌며 올리고 있었을 때만 '게시 지연'
    # (실행이 멈췄다가 다시 돈 직후에 잘못 알리지 않게)
    if site_modified is not None and site_modified < late and build_streak_start is not None and build_streak_start <= late:
        bad.add("A")
    return bad


def _site_modified() -> datetime | None:
    """지금 사이트가 실제로 내주는 자료 파일의 수정 시각 (한국 시간)."""
    base = _site()
    if not base:
        return None
    from email.utils import parsedate_to_datetime
    req = urllib.request.Request(f"{base}d/live.bin?check={int(datetime.now().timestamp())}", method="HEAD")
    with urllib.request.urlopen(req, timeout=15) as r:
        lm = r.headers.get("Last-Modified")
    if not lm:
        return None
    return (parsedate_to_datetime(lm) + timedelta(hours=9)).replace(tzinfo=None)


def _state_json(key: str) -> dict:
    st = get_state(key)
    try:
        return json.loads(st["value"]) if st and st.get("value") else {}
    except (ValueError, TypeError):
        return {}


def check_health(now: datetime, trading_day: bool, log=None) -> list:
    """장중 갱신 멈춤을 확인하고 필요하면 알림·복구 알림을 보냄. 보낸 제목 목록을 돌려줌."""
    def _dt(key):
        st = get_state(key)
        try:
            return datetime.fromisoformat(st["value"]) if st and st.get("value") else None
        except (ValueError, TypeError):
            return None

    # 자동 실행이 끊김 없이 돈 시작 시각 (직전 실행과 10분 넘게 벌어지면 새로 시작)
    prev = _dt("health:last_run")
    streak = _dt("health:streak_start")
    if prev is None or streak is None or now - prev > timedelta(minutes=10):
        streak = now
    set_state("health:last_run", now.isoformat(timespec="seconds"))
    set_state("health:streak_start", streak.isoformat(timespec="seconds"))

    try:
        site_mod = _site_modified()
    except (urllib.error.URLError, ValueError, TypeError) as ex:
        site_mod = None
        if log:
            log(f"사이트 확인 실패: {ex}")
    bad = health_problems(now, data_last_ok=_dt("data_last_ok"), site_modified=site_mod,
                          build_streak_start=streak, trading_day=trading_day)
    st = _state_json("health:alerts")
    today = now.date().isoformat()
    sent = []
    for kind, word in HEALTH_WORD.items():
        k = st.get(kind) or {}
        if kind in bad and not k.get("active") and k.get("alerted") != today:
            if kind == "A":
                title = f"[{word}] 사이트 자료가 {site_mod:%H:%M}에 멈춰 있습니다 ({now:%m/%d %H:%M} 확인)"
                msg = "자동 실행과 수집은 정상인데 사이트(GitHub Pages)에 새 자료가 올라가지 않고 있습니다. 수집은 계속되며, 풀리면 그동안의 자료가 한꺼번에 채워집니다."
            else:
                last = _dt("data_last_ok")
                err = (get_state("data_status") or {}).get("value") or ""
                title = f"[{word}] 네이버 시세를 {last:%H:%M} 이후 받지 못하고 있습니다" if last else f"[{word}] 네이버 시세를 받지 못하고 있습니다"
                msg = "자동 실행은 돌고 있지만 네이버에서 시세를 받지 못하고 있습니다." + (f" 마지막 오류: {err[:200]}" if err.startswith("error") else "")
        elif kind not in bad and k.get("active"):
            title = f"[복구] {word}가 풀렸습니다 ({now:%m/%d %H:%M})"
            msg = "다시 정상적으로 갱신되고 있습니다."
        else:
            continue
        try:
            _post_issue(title, _body(msg))
        except (urllib.error.URLError, KeyError, ValueError) as ex:
            if log:
                log(f"알림 실패: {ex}")
            continue
        st[kind] = {"active": True, "alerted": today} if kind in bad else {"active": False, "alerted": k.get("alerted")}
        sent.append(title)
        if log:
            log(f"알림 보냄: {title}")
    set_state("health:alerts", json.dumps(st))
    return sent
