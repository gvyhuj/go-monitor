"""클라우드에서 한 번 실행되는 감시 작업 (GitHub Actions가 장중 5분마다 실행).

한 번 실행할 때 하는 일
  1. 지난 기록(암호화된 SQLite)을 풀어서 이어 씀
  2. 사이트에서 들어온 요청 반영 (기사 직접 등록, 목록에서 빼기)
  3. 네이버·다음에서 수집 (오늘 1분봉 전체, KOSDAQ, 현재가, 거래원, 기사, 시장 뉴스, 테마 …)
  4. 주가 급등·급락 감지 → 원인 분석 (1차·준최종·최종)
  5. 웹사이트 파일 만들기 (GitHub Pages, 분석 자료는 사이트 비밀번호로 암호화)

두 가지 방식
  - GO_STATIC_SITE=1 : GitHub Pages (기본, 이 저장소 workflow). 요청은 GitHub 이슈로 받음
  - GO_CF_TOKEN      : Cloudflare Workers 사이트 (예전 방식)

    python cloud_job.py            # 보통 실행
    python cloud_job.py password   # (Cloudflare 방식) 사이트 비밀번호만 다시 적용
"""
from __future__ import annotations

import json
import logging
import os
import sys
import secrets
import base64
import gzip
import hashlib
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from core import causes, cloud, feed
from core.db import DB_PATH, connect, get_state, init_db, set_state
from core.detector import detect_symbol, load_settings

FLAG_FIELDS = ["history_synced_on", "eod_done_on", "last_quote_at", "daily_synced_on", "last_trader_minute",
               "last_news_at", "last_discl_at", "last_related_at", "last_investor_at", "last_crosscheck_at",
               "eod_traders_on", "first_backfill_done", "last_macro_at", "last_theme_at", "eod_theme_on",
               "eod_daily_on", "overseas_on"]


# 단계별 걸린 시간 (실행 기록 맨 끝에 한 줄로): 어디가 느린지 보고 장중 실행 간격을 줄이는 데 씀
_LAP = [time.monotonic(), []]


def lap(label: str):
    t = time.monotonic()
    _LAP[1].append((label, t - _LAP[0]))
    _LAP[0] = t


def _enc(v):
    if isinstance(v, datetime):
        return None if v == datetime.min else {"dt": v.isoformat()}
    if isinstance(v, date):
        return {"d": v.isoformat()}
    return v


def _dec(v):
    if isinstance(v, dict):
        if "dt" in v:
            return datetime.fromisoformat(v["dt"])
        if "d" in v:
            return date.fromisoformat(v["d"])
    return v


def load_flags(c):
    st = get_state("job:flags")
    if not st or not st.get("value"):
        return
    try:
        flags = json.loads(st["value"])
    except ValueError:
        return
    for k in FLAG_FIELDS:
        if k in flags and flags[k] is not None:
            setattr(c, k, _dec(flags[k]))


def save_flags(c):
    set_state("job:flags", json.dumps({k: _enc(getattr(c, k, None)) for k in FLAG_FIELDS}))


def _unzip(raw: bytes) -> bytes:
    """사이트가 긴 요청(붙여넣은 원인 글)은 gzip으로 줄여 보냄."""
    return gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw


def apply_inbox(item: dict, log):
    """사이트에서 보낸 요청 하나를 기록에 반영."""
    t = item.get("type")
    with connect() as conn:
        if t in ('news_event_note', 'news_event_meta', 'news_event_link', 'news_event_split'):
            from core.observatory import mutate
            mutate(conn, item)
            log.info('사건 관찰 기록 요청 반영')
        elif t == "pr":
            title = str(item.get("title") or "").strip()[:200]
            ts = datetime.fromisoformat(str(item.get("published_ts"))).isoformat(timespec="minutes")
            if title:
                conn.execute("INSERT INTO pr_events(title, published_ts, url, pr_type) VALUES (?, ?, ?, ?)",
                             (title, ts, str(item.get("url") or "")[:500], "직접 등록(사이트)"))
                log.info("사이트에서 기사 등록: %s (%s)", title, ts)
        elif t in ("hide", "show"):             # 한 건(id) 또는 여러 건(ids, 소식 목록 '여러 개 고르기')
            ids = [str(x) for x in (item.get("ids") or [item.get("id")]) if x]
            for aid in ids:
                conn.execute("UPDATE articles SET hidden=? WHERE id=? OR story=?", (1 if t == "hide" else 0, aid, aid))
            log.info("사이트에서 기사 %s: %s", "빼기" if t == "hide" else "다시 표시", ", ".join(ids))
        elif t == "event_delete":               # 직접 기록한 구간(요청)만 지움 + 그 구간에 붙인 장중 원인도 함께
            eid = int(item.get("id") or 0)
            ev = conn.execute("SELECT * FROM events WHERE id=? AND tier='요청'", (eid,)).fetchone()
            if ev:
                if ev["event_type"] != "daily":
                    conn.execute("DELETE FROM manual_causes WHERE scope='intraday' AND date=? AND direction=? "
                                 "AND start_ts<=? AND last_ts>=?",
                                 (ev["start_ts"][:10], ev["direction"], ev["last_ts"], ev["start_ts"]))
                conn.execute("DELETE FROM events WHERE id=?", (eid,))
                log.info("사이트에서 직접 기록한 구간 삭제: %s %s~%s", ev["start_ts"][:10], ev["start_ts"][11:16], ev["last_ts"][11:16])
        elif t == "hide_one":                   # 중복으로 두 번 들어온 기사 중 이 한 줄만 숨김 (같은 묶음의 다른 기사는 그대로)
            aid = str(item.get("id") or "")
            conn.execute("UPDATE articles SET hidden=1 WHERE id=?", (aid,))
            log.info("사이트에서 중복 기사 숨김: %s", aid)
        elif t == "article_kind":               # 우리 보도자료(pr) / 외부 기사(ext) / 자동(auto)
            ids, kind = [str(x) for x in (item.get("ids") or [item.get("id")]) if x], item.get("kind")
            val = kind if kind in ("pr", "ext") else None
            for aid in ids:
                conn.execute("UPDATE articles SET kind_override=? WHERE id=? OR story=?", (val, aid, aid))
            log.info("사이트에서 기사 구분 변경: %s → %s", ", ".join(ids), val or "자동")
        elif t == "article_tone":               # 악재(-1) / 표시 안 함(0) / 자동(auto)
            aid, tone = str(item.get("id") or ""), item.get("tone")
            val = int(tone) if tone in (-1, 0, "-1", "0") else None
            conn.execute("UPDATE articles SET tone_override=? WHERE id=? OR story=?", (val, aid, aid))
            log.info("사이트에서 기사 악재 표시 변경: %s → %s", aid, "자동" if val is None else val)
        elif t == "article_merge":              # 같은 내용인데 자동으로 안 묶인 기사(여러 건 가능)를 대상 기사 묶음에 넣기 (into 없으면 풀기)
            into = str(item["into"]) if item.get("into") else None
            ids = [str(x) for x in (item.get("ids") or [item.get("id")]) if x]
            for aid in ids:
                if into and aid != into:          # 합친 쌍을 쌓음 (같은 기사를 다른 묶음에 또 합쳐도 앞의 것이 지워지지 않게)
                    conn.execute("INSERT OR IGNORE INTO article_links(a, b) VALUES (?, ?)", (aid, into))
                    conn.execute("UPDATE articles SET story_with=COALESCE(story_with, ?) WHERE id=?", (into, aid))   # 화면의 '직접 합침' 표시용
                elif not into:                    # 빼기: 이 기사가 들어간 합친 쌍을 모두 지움
                    conn.execute("DELETE FROM article_links WHERE a=? OR b=?", (aid, aid))
                    conn.execute("UPDATE articles SET story_with=NULL WHERE id=? OR story_with=?", (aid, aid))
            log.info("사이트에서 기사 묶기: %s → %s", ", ".join(ids), into or "풀기")
        elif t == "article_time":               # 공개 시각 직접 고치기 (ts 없으면 원래 시각으로)
            aid, ts = str(item.get("id") or ""), item.get("ts")
            if aid.startswith("pr") and aid[2:].isdigit():          # 직접 등록한 기사
                if ts:
                    conn.execute("UPDATE pr_events SET published_ts=? WHERE id=?",
                                 (datetime.fromisoformat(str(ts)).isoformat(timespec="minutes"), int(aid[2:])))
            elif ts:
                conn.execute("UPDATE articles SET ts_auto=COALESCE(ts_auto, ts), time_from_auto=CASE WHEN ts_auto IS NULL "
                             "THEN time_from ELSE time_from_auto END, ts=?, time_from='manual' WHERE id=?",
                             (datetime.fromisoformat(str(ts)).isoformat(timespec="minutes"), aid))
            else:
                conn.execute("UPDATE articles SET ts=ts_auto, time_from=time_from_auto, ts_auto=NULL, time_from_auto=NULL "
                             "WHERE id=? AND ts_auto IS NOT NULL", (aid,))
            log.info("사이트에서 기사 공개 시각 %s: %s %s", "고침" if ts else "원래대로", aid, ts or "")
        elif t == "article_lead":               # 묶음의 대표 기사(제목·언론사) 직접 고르기 (id 없으면 자동으로 되돌림)
            from core.articles import LEAD_PICKS, lead_picks
            ids = {str(x) for x in (item.get("ids") or []) if x}
            pick = str(item["id"]) if item.get("id") else None
            picks = (lead_picks(conn) - ids - ({pick} if pick else set())) | ({pick} if pick else set())
            conn.execute("INSERT INTO system_state(key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                         "value=excluded.value, updated_at=excluded.updated_at",
                         (LEAD_PICKS, json.dumps(sorted(picks)), feed.kst_now().isoformat(timespec="minutes")))
            log.info("사이트에서 대표 기사 %s: %s", "고름" if pick else "자동으로", pick or ", ".join(sorted(ids)))
        elif t == "analyze":
            from core.detector import requested_event
            day = str(item.get("date") or "")[:10]
            date.fromisoformat(day)               # 형식 확인
            hhmm = lambda v: (str(v)[:5] if v and len(str(v)) >= 4 else None)
            r = requested_event(load_settings()["symbol"], day, hhmm(item.get("from")), hhmm(item.get("to")))
            log.info("사이트에서 분석 요청: %s %s~%s (%s)", day, item.get("from") or "", item.get("to") or "",
                     "기존 기록 다시 분석" if r["existing"] else "새 기록")
            if str(item.get("cause_body") or "").strip():     # 차트에서 고른 구간에 원인을 함께 붙여넣은 경우
                from core import manual
                daily = r["event_type"] == "daily"
                log.info("사이트에서 구간 원인 %s", manual.save({
                    "type": "cause", "date": day, "direction": r["direction"], "scope": "day" if daily else "intraday",
                    "start_ts": r["start_ts"], "last_ts": r["last_ts"], "headline": item.get("cause_headline"),
                    "body": item["cause_body"]}, feed.kst_now()))
        elif t in ("cause", "cause_delete"):
            from core import manual
            log.info("사이트에서 원인 %s", manual.save(item, feed.kst_now()))
        elif t == "delete_pr":
            pid = str(item.get("id") or "").replace("pr", "")
            if pid.isdigit():
                conn.execute("DELETE FROM pr_events WHERE id=?", (int(pid),))
                log.info("사이트에서 직접 등록 기사 삭제: %s", pid)
        try:                                   # 기사 목록을 바로 다시 만들게 (장중 30분 기다리지 않음)
            conn.execute("DELETE FROM site_items WHERE key IN ('articles', 'article_details')")
        except Exception:  # noqa: BLE001
            pass
    if t == "article_merge":                   # 묶음을 바로 다시 계산 (위 기록이 저장된 뒤) + 실제로 합쳐졌는지 기록
        from core.articles import group_stories
        group_stories(load_settings()["symbol"])
        into = str(item["into"]) if item.get("into") else None
        if into:
            with connect() as conn:
                st = {r[0]: r[1] for r in conn.execute("SELECT id, story FROM articles WHERE id IN (%s)" % ",".join("?" * (len(item.get("ids") or [1]) + 1)),
                                                       [into, *[str(x) for x in (item.get("ids") or [item.get("id")])]])}
            for aid in [str(x) for x in (item.get("ids") or [item.get("id")]) if x]:
                if aid not in st or into not in st:
                    log.warning("기사 묶기 확인: %s → %s 실패 (기사가 기록에 없음: %s)", aid, into, aid if aid not in st else into)
                else:
                    log.info("기사 묶기 확인: %s → %s %s", aid, into, "같은 묶음" if st[aid] == st[into] else "다른 묶음(반영 안 됨)")


def checkpoint():
    """기록 파일 하나로 정리 (백업하기 전에)."""
    with connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


# ------------------------------------------------------------------ GitHub Pages 방식
STATE_DIR = Path(os.environ.get("GO_STATE_DIR", "state"))        # actions/cache 로 다음 실행에 넘김 (암호화)
RESTORE_DIR = Path(os.environ.get("GO_RESTORE_DIR", "restore"))  # 릴리스(최신 백업, 없으면 주간 백업)의 백업 (암호화)
BACKUP_DIR = Path(os.environ.get("GO_BACKUP_DIR", "backup"))     # 이번에 '최신 백업' 릴리스에 올릴 백업 (1시간마다 덮어씀)
WEEKLY_DIR = Path(os.environ.get("GO_WEEKLY_DIR", "weekly"))     # 이번에 '주간 백업' 릴리스에 붙일 백업 (날짜별로 26주 보관)
SITE_DIR = Path(os.environ.get("GO_SITE_DIR", "_site"))


def static_mode() -> bool:
    return os.environ.get("GO_STATIC_SITE") == "1"


def _site_password() -> str:
    return os.environ.get("GO_SITE_PASSWORD", "")


def _data_key() -> str:
    """기록 백업용 열쇠. 따로 없으면 사이트 비밀번호 (이때는 비밀번호를 바꾸면 이전 백업을 못 풂)."""
    return os.environ.get("GO_DATA_KEY") or _site_password()


def restore_state(log) -> str:
    from core import sitebuild
    if DB_PATH.exists():
        return "기존 파일"
    key = _data_key()
    for src, label in ((STATE_DIR, "직전 실행"), (RESTORE_DIR, "최신 백업")):
        if sitebuild.restore_db(src, DB_PATH, key):
            log.info("지난 기록을 이어 씁니다 (%s, %.1f MB)", label, DB_PATH.stat().st_size / 1e6)
            return label
        if sitebuild.find_backup(src):
            log.error("%s 기록을 풀지 못했습니다. DATA_KEY(없으면 SITE_PASSWORD)가 바뀐 것 같습니다.", label)
    log.info("첫 실행입니다. 새 기록을 만듭니다.")
    return "새로 시작"


def site_salt(password: str, state_key: str = "site:salt") -> bytes:
    fp = hashlib.sha256(("go-fp:" + password).encode("utf-8")).hexdigest()
    st = get_state(state_key)
    try:
        cur = json.loads(st["value"]) if st and st.get("value") else {}
    except ValueError:
        cur = {}
    if cur.get("salt") and cur.get("fp") == fp:
        return base64.b64decode(cur["salt"])
    salt = secrets.token_bytes(16)
    set_state(state_key, json.dumps({"salt": base64.b64encode(salt).decode(), "fp": fp}))
    return salt


def build_static_site(now, log) -> dict:
    from core import sitebuild
    pw = _site_password()
    items = sitebuild.refresh_items(now, log.info)
    from core.eventstudy import validate_primary_exports
    counts = validate_primary_exports(items)
    log.info("새 사건연구 대표 값 검증 완료: %s", json.dumps(counts, ensure_ascii=False))
    salt = site_salt(pw)
    checkpoint()
    n = sitebuild.build(SITE_DIR, pw, salt, items, repo=os.environ.get("GITHUB_REPOSITORY", ""))
    # 주간 백업: 1시간 백업은 최신 하나만 덮어쓰므로, 잘못된 기록이 덮어써도 되돌릴 수 있게 날짜별로 따로 남김.
    # '했음' 표시를 다음 실행으로 넘길 기록에 담아야 하므로 그 저장보다 먼저 (늦게 하면 실행마다 다시 올림)
    weekly = weekly_backup_due(now)
    if weekly:
        set_state("site:last_weekly_backup", weekly)
        checkpoint()
        sitebuild.backup_db(DB_PATH, WEEKLY_DIR, _data_key())
        (WEEKLY_DIR / "monitor.db.enc").rename(WEEKLY_DIR / f"monitor-{weekly}.db.enc")
    # 다음 실행으로 넘길 기록 (암호화)
    sitebuild.backup_db(DB_PATH, STATE_DIR, _data_key())
    # 최신 백업(릴리스)은 1시간에 한 번
    last = get_state("site:last_backup")
    due = not last or not last.get("value") or \
        datetime.fromisoformat(last["value"]) < now.replace(tzinfo=None) - timedelta(minutes=55)
    if due:
        set_state("site:last_backup", now.replace(tzinfo=None).isoformat(timespec="seconds"))
        checkpoint()
        sizes = sitebuild.backup_db(DB_PATH, BACKUP_DIR, _data_key())
        # 수집 현황 화면용: 마지막 백업 시각·크기 (다음 실행으로 넘기는 기록보다 먼저 남겨야 이어짐)
        set_state("backup:last", json.dumps({"at": now.replace(tzinfo=None).isoformat(timespec="minutes"), **sizes,
                                             "tables": table_sizes()}, ensure_ascii=False))
        checkpoint()
        sitebuild.backup_db(DB_PATH, STATE_DIR, _data_key())
        log.info("백업 크기: 기록 %.1f MB → 정리 %.1f MB → 압축·암호화 %.1f MB", sizes["db"] / 1e6,
                 sizes["compact"] / 1e6, sizes["backup"] / 1e6)
        summary(f"- 백업 크기: 기록 {sizes['db'] / 1e6:.1f} MB → 정리 {sizes['compact'] / 1e6:.1f} MB"
                f" → 압축·암호화 {sizes['backup'] / 1e6:.1f} MB")
        summary("- 표별 크기: " + table_sizes())
    return {"files": n, "backup": due, "weekly": weekly}


def table_sizes() -> str:
    """표마다 줄 수와 차지하는 크기(dbstat이 있을 때). 백업이 왜 큰지 보려고."""
    with connect() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        try:
            size = dict(conn.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name").fetchall())
        except Exception:  # noqa: BLE001 - dbstat 없는 SQLite
            size = {}
        rows = []
        for t in tables:
            n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            rows.append((size.get(t, 0), t, n))
    rows.sort(reverse=True)
    return ", ".join(f"{t} {n:,}줄" + (f" {b / 1e6:.1f}MB" if b else "") for b, t, n in rows)


def weekly_backup_due(now) -> str | None:
    """마지막 주간 백업에서 7일이 지났으면 오늘 날짜(파일 이름에 씀)."""
    today = now.date()
    last = get_state("site:last_weekly_backup")
    try:
        if last and last.get("value") and (today - date.fromisoformat(last["value"])).days < 7:
            return None
    except ValueError:
        pass
    return today.isoformat()


def annotate(level: str, text: str):
    """GitHub 실행 화면 맨 위에 보이는 알림 (error / warning / notice)."""
    if os.environ.get("GITHUB_ACTIONS"):
        msg = str(text).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::{level} title=주가 감시::{msg}", flush=True)


def import_seeds(log):
    """seed/*.enc: 감시를 켜기 전에 따로 받아 둔 자료(암호화)를 한 번만 기록에 넣음.

    """
    from core import naver, sitebuild
    from core.feed import save_trader_daily
    seed_dir = Path(__file__).resolve().parent / "seed"
    key = _data_key()
    if not seed_dir.exists() or not key:
        return
    for f in sorted(seed_dir.glob("*.enc")):
        flag = f"seed:{f.name}"
        st = get_state(flag)
        if st and st.get("value"):
            continue
        raw = f.read_bytes()
        try:
            data = json.loads(sitebuild.decrypt(sitebuild.derive_key(key, raw[:16]), raw[16:]))
        except Exception as e:
            log.warning("자료 %s 를 풀지 못했습니다: %s", f.name, e)
            continue
        day = data["date"]
        for code, info in (data.get("traders") or {}).items():
            save_trader_daily(code, naver.parse_trader_info(info), day)
        with connect() as conn:
            conn.execute("UPDATE events SET cause_stage=NULL WHERE substr(start_ts,1,10)=?", (day,))
        set_state(flag, datetime.now().isoformat(timespec="minutes"))
        log.info("미리 받아 둔 %s 거래원 자료를 넣었습니다 (%d종목)", day, len(data.get("traders") or {}))


def summary(text: str):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main(argv) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
    log = logging.getLogger("job")
    task = argv[1] if len(argv) > 1 else "run"
    static = static_mode()
    if static:
        if len(_site_password()) < 4:
            log.error("SITE_PASSWORD(사이트 비밀번호)가 없거나 4자보다 짧습니다. 저장소 Settings → Secrets 에 넣어 주세요.")
            summary("### 실패\nSITE_PASSWORD 비밀값이 없거나 4자보다 짧습니다.")
            annotate("error", f"SITE_PASSWORD 비밀값이 없거나 4자보다 짧습니다 (현재 {len(_site_password())}자).")
            return 1
        restored = restore_state(log)
    init_db()
    try:                                   # 회사 설정은 암호화된 기록 안에만 둠
        from core.detector import save_private_from_file
        if save_private_from_file():
            log.info("회사 설정을 암호화된 기록으로 옮겼습니다")
    except Exception as e:  # noqa: BLE001
        log.warning("회사 설정 옮기기 실패: %s", e)
    _s = load_settings()
    for v in {_s.get("symbol"), _s.get("display_name")}:   # 공개 실행 기록(로그)에 종목코드·회사 이름이 보이지 않게 가림
        if v and v != "이 종목":
            print(f"::add-mask::{v}", flush=True)
    if not _s.get("symbol"):
        annotate("error", "회사 설정(종목코드)이 없습니다. 암호화된 기록이 비었거나 GO_PRIVATE_CONFIG 비밀값이 필요합니다.")
        return 1
    if static:
        try:
            import_seeds(log)
        except Exception as e:
            log.warning("미리 받아 둔 자료 넣기 실패(계속 진행): %s", e)
    settings = load_settings()
    code = settings["symbol"]
    now = feed.kst_now()
    log.info("클라우드 감시 실행 (%s, %s)", task, now.isoformat(timespec="minutes"))
    lap("준비")

    site_ok = False
    inbox_n = 0
    if static:
        from core import ghinbox
        try:
            from core import sitebuild as _sb
            _pw = _site_password()
            _ikey = _sb.derive_key(_pw, site_salt(_pw))
            inbox_n = ghinbox.process(lambda item: apply_inbox(item, log), log.info,
                                      unseal=lambda b: json.loads(_unzip(_sb.decrypt(_ikey, base64.b64decode(b)))))
        except Exception as e:
            log.warning("사이트 요청(GitHub 이슈) 확인 실패(계속 진행): %s", e)
    elif cloud.env_mode():
        try:
            st = cloud.ensure_site(log.info, force=(task == "password"))
            site_ok = True
            summary(f"### 사이트 주소\n{st.get('url')}\n")
        except Exception as e:
            log.error("사이트 준비 실패: %s", e)
            summary(f"### 사이트 준비 실패\n{e}\n")
    else:
        log.warning("사이트 설정(GO_STATIC_SITE 또는 GO_CF_TOKEN)이 없어 사이트 만들기는 건너뜁니다.")
    if task == "password" and not static:
        if site_ok:
            cloud.publish(("ui",), log=log.info)
        return 0 if site_ok else 1

    if site_ok:
        try:
            inbox_n = cloud.pull_inbox(lambda item: apply_inbox(item, log), log.info)
        except Exception as e:
            log.warning("사이트 요청 확인 실패(계속 진행): %s", e)

    # 한 번만: 거슬러 채웠던 2026년 전 기사('구분 미확인')는 필요 없어 지움
    if not get_state("articles:purge_pre_coverage_v1"):
        from core.articles import COVERAGE_FROM
        with connect() as conn:
            n_old = conn.execute("DELETE FROM articles WHERE ts < ?", (COVERAGE_FROM,)).rowcount
        set_state("articles:purge_pre_coverage_v1", now.isoformat(timespec="minutes"))
        annotate("notice", f"2026년 전 기사 {n_old}건 삭제")

    # 한 번만: 예전엔 기사 페이지를 한 번 못 열어도 '확인함'으로 두어 기자명이 비어 있던 기사를 다시 읽게 함
    if not get_state("articles:reporter_retry_v1"):
        with connect() as conn:
            n_re = conn.execute("UPDATE articles SET reporter_at=NULL, reporter_tries=0 WHERE reporter IS NULL").rowcount
        set_state("articles:reporter_retry_v1", now.isoformat(timespec="minutes"))
        annotate("notice", f"기자명이 빈 기사 {n_re}건 다시 읽기")

    # 한 번만: 기자 대신 언론사 이름('뉴시스')이 읽혔거나 비어 있던 기사를 고친 규칙으로 다시 읽게 함
    if not get_state("articles:reporter_retry_v2"):
        with connect() as conn:
            n_re = conn.execute("UPDATE articles SET reporter=NULL, reporter_at=NULL, reporter_tries=0 "
                                "WHERE reporter IS NULL OR instr(REPLACE(COALESCE(office, ''), ' ', ''), reporter) > 0").rowcount
        set_state("articles:reporter_retry_v2", now.isoformat(timespec="minutes"))
        annotate("notice", f"기자명 다시 읽기 (빈 것·언론사 이름이 들어간 것) {n_re}건")

    # 한 번만: 네이버 뉴스판을 먼저 읽게 바꾼 뒤 기자명이 빈 기사를 모두 다시 읽게 함
    if not get_state("articles:reporter_retry_v3"):
        with connect() as conn:
            n_re = conn.execute("UPDATE articles SET reporter_at=NULL, reporter_tries=0 WHERE reporter IS NULL").rowcount
        set_state("articles:reporter_retry_v3", now.isoformat(timespec="minutes"))
        annotate("notice", f"기자명 빈 기사 {n_re}건 네이버 뉴스판으로 다시 읽기")

    # 한 번만: 열었는데 이름이 없던 기사도 '확인함'으로 굳히지 않게 바꾼 뒤(블로터 등), 빈 기사를 모두 다시 읽게 함
    # 한 번만: 예전 방식(기사마다 합친 상대 하나)에서 덮어써져 사라진 합치기 요청까지 실행 기록(10/26)에서 되살려 쌓음
    if not get_state("articles:links_seed_v1"):
        pairs = [("0030014015708", "0180006309689"), ("0030014015708", "g0369fe45d35189a34a"), ("0180006345920", "sf3da29cefba800c9f3"),
                 ("g751ef1f83409ff4ac8", "s46329805fc80906eb6"), ("gdd52d68876ad29218d", "s9c2bb981eb43f23f40"),
                 ("s46329805fc80906eb6", "s0c608229a09d2fc783"), ("s46329805fc80906eb6", "s8e5b699def622749dd"),
                 ("s6d2269e97fa913784d", "s46329805fc80906eb6")] + [
                (x, "s35a87d97e0d5997ad7") for x in ("s1f735504a551a8598a", "g0da5b9e70f507e63db", "gf28b484b37489ad9ca",
                                                     "s80d4a54dfeb428ebf7", "g8637cfd85e76542045", "g39a00269f237f8d23a", "g0541eaf1f4c5b05e84")]
        with connect() as conn:
            conn.executemany("INSERT OR IGNORE INTO article_links(a, b) VALUES (?, ?)", pairs)
        from core.articles import group_stories
        group_stories(code)
        set_state("articles:links_seed_v1", now.isoformat(timespec="minutes"))
        annotate("notice", f"합친 기사 쌍 {len(pairs)}개를 다시 쌓고 묶음을 다시 계산")

    if not get_state("articles:reporter_retry_v4"):
        with connect() as conn:
            n_re = conn.execute("UPDATE articles SET reporter_at=NULL, reporter_tries=0 WHERE reporter IS NULL").rowcount
        set_state("articles:reporter_retry_v4", now.isoformat(timespec="minutes"))
        annotate("notice", f"기자명 빈 기사 {n_re}건 다시 읽기 (네이버판에 없으면 언론사 원문에서)")

    # 한 번만: 비교 종목을 네이버 업종(WICS) 묶음으로 바꾸고, 새 종목의 일봉·오늘 분봉을 바로 받음 (내일까지 기다리지 않게)
    if not get_state("peers:wics_v1"):
        try:
            from core import macro
            rel = feed.sync_related(code)
            codes = [r["code"] for r in rel]
            n_d = macro.sync_daily_bars(codes)
            n_m = feed.sync_related_bars(codes, False, now)      # 오늘 분봉만 (지난 분봉은 내일 첫 실행의 하루 수집에서)
            set_state("peers:wics_v1", now.isoformat(timespec="minutes"))
            annotate("notice", f"비교 종목을 네이버 업종(WICS) {len(codes)}개로 바꿈 (일봉 {n_d}건, 분봉 {n_m}건 받음)")
        except Exception as e:      # noqa: BLE001 - 다음 실행에 다시
            annotate("warning", f"비교 종목 WICS 전환 실패: {type(e).__name__}: {str(e)[:150]}")

    # 매번: 아직 기자명을 안 읽은 기사가 있으면 한 번에 많이 읽어 바로 채움 (평소엔 수집할 때 15건씩)
    try:
        from core import reporters
        with connect() as conn:
            todo = conn.execute("SELECT COUNT(*) FROM articles WHERE symbol=? AND reporter_at IS NULL "
                                "AND COALESCE(reporter_tries, 0) < ?", (code, reporters.MAX_TRIES)).fetchone()[0]
        if todo:
            errs = []
            got = reporters.fill(code, limit=150, log=lambda m: (errs.append(m), log.info(m)))
            annotate("notice", f"기자명 읽기: {min(todo, 150)}건 중 {got}건 찾음 (남은 기사 {max(0, todo - 150)}건)"
                     + (f" · 못 연 기사 {len(errs)}건, 예: {errs[0][:160]}" if errs else ""))
    except Exception as e:      # noqa: BLE001
        annotate("warning", f"기자명 읽기 실패: {type(e).__name__}: {str(e)[:150]}")

    # 매번: 기자명이 빈 기사가 많은 언론사와 그 까닭 (네이버 주소 / 원문 주소 / 못 엶 3번)
    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT office, SUM(url LIKE '%news.naver.com%'), SUM(url NOT LIKE '%news.naver.com%'), "
                "SUM(COALESCE(reporter_tries, 0) >= 3), SUM(reporter_at IS NULL), COUNT(*) FROM articles "
                "WHERE symbol=? AND reporter IS NULL GROUP BY office ORDER BY COUNT(*) DESC LIMIT 6", (code,)).fetchall()
        if rows:
            annotate("notice", "기자명 빈 기사: " + " / ".join(
                f"{o or '-'} {n}건(네이버 {nv or 0}·원문 {orig or 0}·못 엶 {bad or 0}·아직 {todo or 0})"
                for o, nv, orig, bad, todo, n in rows))
    except Exception as e:      # noqa: BLE001
        annotate("warning", f"기자명 점검 실패: {type(e).__name__}")

    # 매번: 막은 매체(핀포인트뉴스) 기사, 단순 언급·시황 기사, 회사 이름만 스쳐 지나가는 기사를 지움 (지운 기사는 다시 모으지 않음)
    try:
        from core import relevance
        from core.articles import company_name
        n_blk = relevance.purge_blocked(code)
        n_only = relevance.check(code, company_name(), log=log.info)
        with connect() as conn:
            left = conn.execute("SELECT COUNT(*) FROM articles WHERE symbol=? AND mention_checked_at IS NULL", (code,)).fetchone()[0]
        if n_blk or n_only or left:
            annotate("notice", f"기사 정리: 막은 매체·단순 언급·시황 {n_blk}건 · 이름만 언급 {n_only}건 삭제 (아직 살펴볼 기사 {left}건)")
    except Exception as e:      # noqa: BLE001 - 정리가 실패해도 수집은 계속
        annotate("warning", f"기사 정리 실패: {type(e).__name__}: {str(e)[:150]}")

    lap("요청·기사 정리")
    failed = None
    try:
        # 수집: 데스크톱 감시 엔진과 같은 규칙을 한 번 실행 (마지막 실행 시각은 기록에 저장)
        import monitor
        c = monitor.Collector(code, log)
        c.sync_mode = True
        load_flags(c)
        try:
            c.run_once()
        finally:
            save_flags(c)
        lap("수집(시세·기사·공시)")

        # 국제·정치 이슈 기사 (구글 뉴스): 매 실행마다 (내부에서 장중 15분·장 밖 60분 간격으로 조절)
        from core import newssearch
        issue_err = []
        try:
            n_purged = 0 if get_state("issue_news:purged_v1") else newssearch.purge_disallowed()
            set_state("issue_news:purged_v1", "1")
            if n_purged:
                annotate("notice", f"허용 출처가 아닌 이슈 기사 {n_purged}건 정리 (한국·영어권 주요 언론만 유지)")
        except Exception as e:  # noqa: BLE001
            annotate("warning", f"이슈 기사 정리 실패: {e}")
        try:
            n_issue_new = newssearch.sync_recent(issue_err.append)
            if n_issue_new or issue_err:
                annotate("notice", f"이슈 기사 새로 {n_issue_new}건" + (f", 실패: {issue_err[0][:150]}" if issue_err else ""))
        except Exception as e:  # noqa: BLE001
            annotate("warning", f"이슈 기사 수집 실패: {e}")
        if not get_state("issue_news:filter_v1"):          # 출처 제한 후 지난 날짜를 한 번 다시 보충
            with connect() as conn:
                conn.execute("DELETE FROM system_state WHERE key LIKE 'issue_news:backfill:%'")
            set_state("issue_news:filter_v1", now.isoformat(timespec="minutes"))
        # 최근 2주 이상변동 날짜의 이슈 기사를 한 번씩 보충하고, 보충되면 그날 이벤트를 다시 분석
        with connect() as conn:
            ev_days = sorted({r[0] for r in conn.execute(
                "SELECT DISTINCT substr(start_ts,1,10) FROM events WHERE symbol=? AND start_ts>=?",
                (code, (now - timedelta(days=14)).isoformat(timespec="minutes")))})
        for d in ev_days:
            try:
                added = newssearch.backfill(date.fromisoformat(d), log=log.info)
            except Exception as e:  # noqa: BLE001
                annotate("warning", f"지난 이슈 기사 보충 실패({d}): {e}")
                continue
            if added:
                with connect() as conn:
                    conn.execute("UPDATE events SET cause_stage=NULL WHERE symbol=? AND substr(start_ts,1,10)=?", (code, d))
                annotate("notice", f"{d} 이슈 기사 {added}건 보충 → 그날 이상변동 다시 분석")

        lap("이슈 기사")
        n = detect_symbol(code)
        if n:
            log.info("신규 이상변동 %d건", n)
        causes.upgrade_reanalysis(code, now, log)
        # 업종 ETF 일봉·묶음 확인 종목이 비어 있으면 하루 한 번 수집을 기다리지 않고 바로 채움
        from core import macro as _macro
        etfs = [f["code"] for f in (settings.get("factor_etfs") or [])]
        with connect() as conn:
            empty = [e for e in etfs if not conn.execute("SELECT 1 FROM daily_bars WHERE symbol=? LIMIT 1", (e,)).fetchone()]
        if empty:
            log.info("업종 ETF 일봉 %d개 수집: %s", _macro.sync_daily_bars(empty), ", ".join(empty))
        if not _macro.basket_universe() and _macro.my_themes(code):
            try:
                _macro.sync_basket_universe(code, log=log)
            except Exception as e:  # noqa: BLE001
                log.warning("묶음 확인 종목 수집 실패: %s", e)
        # 업종 ETF 일봉이 처음 들어오면 최근 이벤트를 팩터 분해로 다시 분석
        if etfs and not (get_state("factor_ready") or {}).get("value"):
            with connect() as conn:
                have = all(conn.execute("SELECT COUNT(*) FROM daily_bars WHERE symbol=?", (e,)).fetchone()[0] >= 60 for e in etfs)
                if have:
                    conn.execute("UPDATE events SET cause_stage=NULL WHERE symbol=?", (code,))
            if have:
                set_state("factor_ready", now.isoformat(timespec="minutes"))
                log.info("업종 ETF 자료가 준비되어 이벤트를 다시 분석합니다")
        causes.refresh_pending(code, feed.kst_now(), fetch=c.fetch, log=log)
        try:                                   # 급등·급락이 없는 날도 하루 분석 (하루 칸을 누르면 같은 분석 창)
            causes.refresh_days(code, feed.kst_now(), fetch=c.fetch, log=log)
        except Exception as e:  # noqa: BLE001
            log.warning("하루 분석 실패: %s", e)
        lap("이상변동 분석")
        # 급등·급락 알림 (GitHub 앱 푸시: 저장소 주인 @언급)
        try:
            from core import alerts
            if alerts.send_test(log.warning):
                annotate("notice", "알림 시험 글을 보냈습니다")
            n_alert = alerts.send_new(code, log=log.info)
            if n_alert:
                annotate("notice", f"급등·급락 알림 {n_alert}건 보냄")
        except Exception as e:  # noqa: BLE001
            annotate("warning", f"알림 보내기 실패: {e}")
        # 기사·공시 알림 (보도자료 매체 수가 늘 때, 외부 기사·공시가 새로 들어올 때)
        try:
            from core import newsalerts
            t = newsalerts.send_new(code, log=log.info)
            if t:
                annotate("notice", f"기사·공시 알림 보냄: {t}")
        except Exception as e:  # noqa: BLE001
            annotate("warning", f"기사·공시 알림 실패: {e}")

        last = get_state("job:last_purge")
        if not last or last.get("value") != now.date().isoformat():
            feed.purge_old_bars(int(settings.get("keep_bars_days", 0)))
            feed.purge_related_bars(code)
            set_state("job:last_purge", now.date().isoformat())
        # 네이버 검색 API 열쇠 확인: 하루 한 번 회사 이름으로 한 번 불러 보고 결과를 남김 (수집 현황 · 실행 화면)
        if newssearch.enabled() and (get_state("naver_api:checked") or {}).get("value") != now.date().isoformat():
            from core import collect_status
            from core.articles import company_name
            try:
                got = newssearch.search(company_name(), display=10)
                where = "NAVER API HUB" if newssearch._ENDPOINT == [True] else "네이버 개발자센터"
                collect_status.mark("네이버 검색 API", True)
                annotate("notice", f"네이버 검색 API 정상 ({where} 열쇠, 시험 검색 {len(got)}건)")
            except Exception as e:  # noqa: BLE001
                collect_status.mark("네이버 검색 API", False, e)
                annotate("warning", f"네이버 검색 API 실패: {e}")
            set_state("naver_api:checked", now.date().isoformat())
        elif not newssearch.enabled() and not get_state("naver_api:missing_noted"):
            annotate("notice", "네이버 검색 API 열쇠(NAVER_CLIENT_ID / NAVER_CLIENT_SECRET)가 없습니다")
        # 야후 5분봉·60분봉 (1분봉이 없는 지난 날의 차트용): 하루 한 번, 장 마감 뒤
        last_y = get_state("yahoo:last_sync_v2")          # v2: 코스닥 지수 봉도 함께
        if (not last_y or last_y.get("value") != now.date().isoformat()) and now.strftime("%H:%M") >= "16:00":
            from core import collect_status, yahoo
            try:
                added = yahoo.sync(code)
                collect_status.mark("야후 5분봉·60분봉", True)
                set_state("yahoo:last_sync_v2", now.date().isoformat())
                annotate("notice", f"야후 분봉 새로 5분 {added.get(5, 0)}개 · 60분 {added.get(60, 0)}개")
            except Exception as e:  # noqa: BLE001 - 부가 자료라 실패해도 계속
                collect_status.mark("야후 5분봉·60분봉", False, e)
                annotate("warning", f"야후 분봉 받기 실패: {e}")
    except Exception as e:  # noqa: BLE001 - 수집이 실패해도 사이트와 기록은 저장
        failed = e
        log.exception("수집·분석 중 오류: %s", e)
        summary(f"- 수집·분석 오류: {e}")
        import traceback
        annotate("error", "수집·분석 오류: " + "".join(traceback.format_exception(e)[-4:]))
    lap("알림·기타")
    set_state("monitor_status", "running")
    # 갱신 멈춤 알림 (수집이 실패한 실행에서도 확인하도록 수집 try 밖에서)
    if static:
        try:
            from core import alerts
            now2 = feed.kst_now()
            q = get_state(f"quote:{code}")
            ok = get_state("data_last_ok")
            day_open = alerts.is_trading_day(now2, json.loads(q["value"]) if q and q.get("value") else None,
                                             datetime.fromisoformat(ok["value"]) if ok and ok.get("value") else None)
            for t in alerts.check_health(now2, trading_day=day_open, log=log.info):
                annotate("notice", t)
        except Exception as e:  # noqa: BLE001
            annotate("warning", f"갱신 멈춤 확인 실패: {e}")
    from core.db import now_iso
    set_state("monitor_heartbeat", now_iso())

    if site_ok:
        try:
            r = cloud.publish(log=log.info)
            summary(f"- 업로드 {r.get('written', 0)}건, 오늘 {cloud.writes_today()}/{cloud.DAILY_WRITE_CAP}건"
                    + (f", 사이트 요청 {inbox_n}건 반영" if inbox_n else ""))
        except Exception as e:
            log.error("사이트 업로드 실패: %s", e)
            summary(f"- 업로드 실패: {e}")
    if static:
        try:
            lap("점검")
            r = build_static_site(now, log)
            lap("사이트 만들기")
            repo = os.environ.get("GITHUB_REPOSITORY", "")
            if repo:
                owner, name = repo.split("/", 1)
                summary(f"### 사이트 주소\nhttps://{owner.lower()}.github.io/{name}/\n")
            summary(f"- 사이트 자료 {r['files']}개 (암호화), 기록 이어쓰기: {restored}"
                    + (", 최신 백업" if r["backup"] else "") + (", 주간 백업" if r.get("weekly") else "") + (f", 사이트 요청 {inbox_n}건 반영" if inbox_n else ""))
        except Exception as e:
            log.error("사이트 만들기 실패: %s", e)
            summary(f"- 사이트 만들기 실패: {e}")
            raise
    checkpoint()
    with connect() as conn:
        bars = conn.execute("SELECT COUNT(*), MAX(ts) FROM minute_bars WHERE symbol=?", (code,)).fetchone()
        evs = conn.execute("SELECT COUNT(*) FROM events WHERE symbol=?", (code,)).fetchone()[0]
    summary(f"- 1분봉 {bars[0]:,}건 (마지막 {bars[1]}), 이상변동 누적 {evs}건")
    annotate("notice", f"1분봉 {bars[0]:,}건 (마지막 {bars[1]}), 이상변동 누적 {evs}건, 기록 이어쓰기: {restored if static else '-'}")
    try:                                # 자료 점검 (공개 시세 수준의 정보만 표시)
        from core import macro, newssearch
        chk = []
        with connect() as conn:
            for i_f, f in enumerate(settings.get("factor_etfs") or [], 1):
                rows = conn.execute("SELECT date, close FROM daily_bars WHERE symbol=? ORDER BY date DESC LIMIT 30",
                                    (f["code"],)).fetchall()
                dup = conn.execute(
                    "SELECT b.symbol, COUNT(*) FROM daily_bars a JOIN daily_bars b ON a.date=b.date AND a.close=b.close "
                    "AND b.symbol<>a.symbol WHERE a.symbol=? AND a.date>=? GROUP BY b.symbol ORDER BY 2 DESC LIMIT 1",
                    (f["code"], rows[-1][0] if rows else "9999")).fetchone()
                n_all = conn.execute("SELECT COUNT(*) FROM daily_bars WHERE symbol=?", (f["code"],)).fetchone()[0]
                chk.append(f"업종ETF{i_f} 일봉 {n_all}일(마지막 {rows[0][0] if rows else '-'})"
                           + (f" ⚠ 다른 종목과 종가 {dup[1]}일 같음" if dup and dup[1] >= 10 else ""))
            since = (feed.kst_now() - timedelta(days=1)).isoformat(timespec="minutes")
            n_issue = conn.execute("SELECT COUNT(*) FROM macro_news WHERE (id LIKE 's%' OR id LIKE 'g%') AND ts>=?", (since,)).fetchone()[0]
            other = {lab: conn.execute("SELECT COUNT(*) FROM daily_bars WHERE symbol=?", (c_,)).fetchone()[0]
                     for lab, c_ in (("종목", code), ("KOSDAQ", "KOSDAQ"))}
            n_theme = conn.execute("SELECT COUNT(*) FROM stock_themes WHERE code=?", (code,)).fetchone()[0]
        chk.append("일봉 " + ", ".join(f"{k} {v}일" for k, v in other.items()) + f" / 종목 테마 {n_theme}개"
                   + " / 회사 설정 " + ("있음" if settings.get("symbol") else "없음"))
        for i_f, f in enumerate(settings.get("factor_etfs") or [], 1):
            if any(x.startswith(f"업종ETF{i_f} 일봉 0일") for x in chk):
                try:
                    got = macro.fetch_daily(f["code"])
                    chk.append(f"업종ETF{i_f} 직접 받기 {len(got)}일")
                except Exception as e:  # noqa: BLE001
                    chk.append(f"업종ETF{i_f} 직접 받기 실패: {type(e).__name__}")
        try:                               # 직접 등록 기사가 사이트 목록에 들어갔는지 (건수만)
            import webserver as _ws
            with connect() as conn:
                n_pr = conn.execute("SELECT COUNT(*) FROM pr_events").fetchone()[0]
                cached_list = conn.execute("SELECT value, built_at FROM site_items WHERE key='articles'").fetchone()
            in_site = cached_list[0].count('"id": "pr') + cached_list[0].count('"id":"pr') if cached_list else -1
            chk.append(f"직접 등록 {n_pr}건 / 사이트 목록에 {in_site}건 (목록 갱신 {cached_list[1][5:16] if cached_list else '-'})")
        except Exception as e:  # noqa: BLE001
            chk.append(f"직접 등록 확인 실패: {type(e).__name__}")
        bu = macro.basket_universe()
        with connect() as conn:
            tops = conn.execute("SELECT office, COUNT(*) FROM macro_news WHERE id LIKE 'g%' AND ts>=? GROUP BY office "
                                "ORDER BY 2 DESC LIMIT 12", (since,)).fetchall()
        chk.append("이슈 기사(구글 뉴스) " + (f"최근 하루 {n_issue}건" if newssearch.enabled() else "꺼짐")
                   + (" — 출처 " + ", ".join(f"{o} {n}" for o, n in tops) if tops else ""))
        chk.append("묶음 확인 종목 " + (", ".join(f"{k} {len(v.get('stocks') or [])}개" for k, v in bu.items()) or "아직 없음"))
        annotate("notice", "자료 점검: " + " / ".join(chk))
    except Exception as e:  # noqa: BLE001
        annotate("warning", f"자료 점검 실패: {e}")
    # 한투 열쇠가 있으면: 지난 기록(일봉·KOSDAQ 일봉·지난 1분봉)을 한투로 다시 받기 (장중엔 조금씩, 장 밖엔 많이)
    try:
        from core import kis, kis_backfill
        if kis.enabled():
            n_now = feed.kst_now().replace(tzinfo=None)
            busy = n_now.weekday() < 5 and "08:50" <= n_now.strftime("%H:%M") <= "15:40"
            msg = kis_backfill.run(code, n_now, 4 if busy else 40, log=log)
            if msg:
                annotate("notice", msg)
    except Exception as e:  # noqa: BLE001 - 다음 실행에 이어서
        annotate("warning", f"한투 다시 받기 실패(다음 실행에 이어서): {type(e).__name__}: {str(e)[:150]}")
    # 하루 한 번: 이 종목 일별 등락을 KOSDAQ·네이버 업종(WICS)·네이버 테마 중 어느 묶음이 더 잘 설명하는지 (실행 기록에 한 줄)
    if (get_state("peerfit:day") or {}).get("value") != now.date().isoformat():
        try:
            from core import macro as _m, peerfit
            with connect() as conn:
                missing = [x["code"] for x in _m.basket_codes()
                           if not conn.execute("SELECT 1 FROM daily_bars WHERE symbol=? LIMIT 1", (x["code"],)).fetchone()]
            if missing:                       # 테마 대형 종목 일봉 (처음 한 번)
                _m.sync_daily_bars(missing)
            with connect() as conn:
                res = peerfit.compare(conn, code)
            set_state("peerfit", json.dumps(res, ensure_ascii=False))
            set_state("peerfit:day", now.date().isoformat())
            annotate("notice", peerfit.summary_line(res))
        except Exception as e:  # noqa: BLE001 - 참고용 계산이라 실패해도 계속
            annotate("warning", f"묶음 설명력 계산 실패: {type(e).__name__}: {str(e)[:150]}")
    lap("마무리")
    annotate("notice", f"단계별 시간(초, 합계 {sum(x for _, x in _LAP[1]):.0f}): " + " · ".join(f"{k} {v:.0f}" for k, v in _LAP[1]))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        annotate("error", "실행 실패: " + "".join(traceback.format_exception(e)[-5:]))
        sys.exit(1)
