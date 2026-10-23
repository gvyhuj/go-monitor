"""급등·급락 알림 (메일).

급등·급락이 새로 감지되면 ALERT_EMAIL_TO 주소로 메일을 보냅니다 (보내는 계정: SMTP_USER·SMTP_PASSWORD).
예전에는 저장소 이슈로 GitHub 앱 알림을 보냈지만 지금은 메일로만 보냅니다.
메일 설정이 없거나 발송에 실패하면 보낸 것으로 표시하지 않아 다음 실행에서 다시 시도합니다.

메일에는 '급등 +8.7% (10:12)' 같은 시세 정보만 적고, 원인 분석은 비밀번호가 걸린 사이트에서 보게 합니다.

같은 날 같은 방향은 한 번만, 그 뒤 ±10% 이상('대형')으로 커지면 한 번 더 보냅니다.
장중에 ±3% 안으로 회복했다가 다시 기준을 넘으면 한 번 더 보냅니다.
여러 날 누적(5거래일 ±10%)도 알립니다.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
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


class AlertError(ValueError):
    """알림을 보내지 못함 (메일 설정 없음·메일 서버 오류). 보낸 것으로 표시하지 않아 다음 실행에서 다시 시도."""


def email_enabled() -> bool:
    return all(os.environ.get(k) for k in ("ALERT_EMAIL_TO", "SMTP_USER", "SMTP_PASSWORD"))


def _send_email(title: str, body: str):
    """받는 주소는 쉼표로 여러 개. 기본은 Gmail(앱 비밀번호), SMTP_HOST/SMTP_PORT로 바꿀 수 있음."""
    from email.message import EmailMessage
    to = [x.strip() for x in os.environ["ALERT_EMAIL_TO"].split(",") if x.strip()]
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 465)
    msg = EmailMessage()
    msg["Subject"] = f"[주가 감시] {title}"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.send_message(msg)


def _notify(title: str, body: str, log=None):
    """알림은 메일로만 보냄 (예전의 GitHub 앱 알림은 끔)."""
    if not email_enabled():
        raise AlertError("메일 설정(ALERT_EMAIL_TO·SMTP_USER·SMTP_PASSWORD)이 없어 알림을 보내지 못했습니다.")
    try:
        _send_email(title, body)
    except (OSError, smtplib.SMTPException) as ex:
        raise AlertError(f"메일 발송 실패: {ex}") from ex


def _site() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def _body(extra: str = "", event_id: int | None = None) -> str:
    link = f"{_site()}#/events/{event_id}" if event_id and _site() else _site()   # 누르면 그 이상변동 상세가 바로 열림
    return f"{extra}\n\n원인 분석은 사이트에서 확인하세요: {link}\n\n자동 알림입니다."


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
            _notify(title, _body(f"주가가 {'장중 전일 종가 대비' if running else e['start_ts'][11:16] + '부터' if e['event_type'] != 'daily' else '하루 동안'} "
                                      f"{mv*100:+.1f}% 움직였습니다.", e.get("id")), log)
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
            _notify(title, _body(f"주가가 {m['days']}거래일 동안 {m['return']*100:+.1f}% 움직였습니다 "
                                      "(하루하루는 기준 밑이어도 쌓이면 알립니다)."), log)
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
    """메일 설정을 처음 넣으면 시험 메일을 한 번 (실행 화면에서 '알림 시험 보내기'를 켜면 다시)."""
    force = os.environ.get("GO_ALERT_TEST") == "1"
    if not email_enabled() or (get_state("alerts:email_test_v1") and not force):
        return False
    try:
        _notify(f"[알림 시험] 주가 감시 알림 ({datetime.now():%m/%d %H:%M})",
                _body("이 메일이 보이면 설정이 끝난 것입니다. 앞으로 급등·급락이 감지되면 이 주소로 알림이 옵니다."), log)
    except AlertError as ex:
        if log:
            log(f"시험 알림 실패: {ex}")
        return False
    set_state("alerts:email_test_v1", datetime.now().isoformat(timespec="minutes"))
    return True


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
            _notify(title, _body(msg), log)
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
