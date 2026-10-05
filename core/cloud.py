"""어디서나 보기: 분석 결과를 Cloudflare 무료 서비스(Workers + KV)에 올려 고정 주소로 보여줍니다.

구조
  회사 PC(이 프로그램)  --장중 2분마다 결과 업로드-->  Cloudflare KV  <--  Worker(사이트, 비밀번호 로그인)  <--  휴대폰·집 PC
  - PC가 꺼져 있어도 사이트는 마지막으로 올린 결과를 보여줍니다.
  - 비용 0원: Workers 무료 플랜(하루 요청 10만 건), KV 무료(하루 쓰기 1,000건, 저장 1GB). 한도를 넘으면 요금이 아니라 잠시 멈춤.
    이 프로그램은 하루 쓰기를 900건 안으로 스스로 조절합니다.
  - 필요한 것: Cloudflare 무료 계정과 API 토큰('Cloudflare Workers 편집' 템플릿) 1개. 신용카드 필요 없음.
  - 토큰과 비밀번호 확인값은 이 폴더의 data/cloud.json 에만 저장됩니다.

Cloudflare API (https://api.cloudflare.com/client/v4)
  GET  /user/tokens/verify                                   토큰 확인
  GET  /accounts                                             계정 ID
  GET/POST /accounts/{a}/storage/kv/namespaces               KV 저장소
  PUT  /accounts/{a}/storage/kv/namespaces/{ns}/bulk         여러 값 한 번에 쓰기
  GET/PUT  /accounts/{a}/workers/subdomain                   workers.dev 하위 주소
  PUT  /accounts/{a}/workers/scripts/{name}                  사이트(Worker) 올리기
  POST /accounts/{a}/workers/scripts/{name}/subdomain        workers.dev 주소 켜기
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RUN = ROOT / "run"
CONFIG = DATA / "cloud.json"
STATE = DATA / "cloud_state.json"
WORKER_JS = ROOT / "cloud" / "worker.js"
WEB = ROOT / "web"
API = os.environ.get("GO_CF_API", "https://api.cloudflare.com/client/v4")
SCRIPT = "stock-monitor"
KV_TITLE = "stock_monitor"
DAILY_WRITE_CAP = 900          # KV 무료 한도 1,000건/일(UTC 0시 초기화) 안에서 여유를 둠
UI_FILES = ["index.html", "app.js", "app.css", "app.ico", "vendor/lightweight-charts.standalone.production.js",
            "fonts/PretendardVariable.woff2"]
KST = timezone(timedelta(hours=9))

_lock = threading.Lock()


class CloudError(Exception):
    pass


# ------------------------------------------------------------------ 저장

def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(p: Path, d: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


PUBLIC = DATA / "cloud_public.json"     # 클라우드 실행(GitHub Actions)용: 비밀 아닌 값만 저장


def env_mode() -> bool:
    """GitHub Actions 등에서 토큰을 환경변수로 받는 경우 (토큰을 파일에 저장하지 않음)."""
    return bool(os.environ.get("GO_CF_TOKEN"))


def config() -> dict:
    if env_mode():
        c = _read(PUBLIC)
        c["token"] = os.environ["GO_CF_TOKEN"].strip()
        return c
    return _read(CONFIG)


def enabled() -> bool:
    c = config()
    return bool(c.get("token") and c.get("namespace_id") and c.get("enabled", True))


def runner() -> str:
    return "github" if os.environ.get("GITHUB_ACTIONS") else "pc"


def _state() -> dict:
    return _read(STATE)


def _set_state(**kw):
    s = _state()
    s.update(kw)
    _write(STATE, s)


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def writes_today() -> int:
    return int((_state().get("writes") or {}).get(_utc_day(), 0))


def _count_writes(n: int):
    s = _state()
    w = {k: v for k, v in (s.get("writes") or {}).items() if k >= (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")}
    w[_utc_day()] = w.get(_utc_day(), 0) + n
    s["writes"] = w
    _write(STATE, s)


def status() -> dict:
    c, s = config(), _state()
    return {
        "connected": enabled(),
        "url": c.get("url"),
        "account_name": c.get("account_name"),
        "created_at": c.get("created_at"),
        "last_publish": s.get("last_publish"),
        "last_live": s.get("last_live"),
        "last_error": s.get("last_error"),
        "last_error_at": s.get("last_error_at"),
        "writes_today": writes_today(),
        "write_cap": DAILY_WRITE_CAP,
        "busy": bool(s.get("busy_until") and s["busy_until"] > time.time()),
    }


# ------------------------------------------------------------------ Cloudflare API

_FRIENDLY = {
    6003: "토큰 형식이 올바르지 않습니다. 복사할 때 앞뒤 공백이 들어가지 않았는지 확인해 주세요.",
    9109: "토큰 권한이 부족합니다. 'Cloudflare Workers 편집' 템플릿으로 토큰을 다시 만들어 주세요.",
    10000: "토큰이 맞지 않거나 권한이 부족합니다. 'Cloudflare Workers 편집' 템플릿으로 만든 토큰인지 확인해 주세요.",
    10001: "토큰이 맞지 않습니다. 다시 복사해 붙여 넣어 주세요.",
}


def _call(method: str, path: str, token: str, body=None, raw: bytes | None = None, ctype: str | None = None,
          timeout: float = 60):
    url = API + path
    data, headers = None, {"Authorization": f"Bearer {token}", "User-Agent": "StockMonitor"}
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype or "application/octet-stream"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            j = json.loads(payload.decode("utf-8"))
        except Exception:
            raise CloudError(f"Cloudflare 응답 오류 {e.code}: {payload[:200]!r}")
        return _check(j, e.code)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CloudError(f"Cloudflare에 접속하지 못했습니다. 인터넷이나 회사 방화벽을 확인해 주세요. ({e})")
    try:
        j = json.loads(payload.decode("utf-8"))
    except Exception:
        raise CloudError(f"Cloudflare 응답 형식 오류: {payload[:200]!r}")
    return _check(j, 200)


def _check(j: dict, code: int):
    if j.get("success"):
        return j.get("result")
    errs = j.get("errors") or []
    first = errs[0] if errs else {}
    msg = _FRIENDLY.get(first.get("code")) or (first.get("message") or f"HTTP {code}")
    err = CloudError(f"{msg} (Cloudflare 오류 {first.get('code', code)})")
    err.code = first.get("code")
    raise err


def _get_raw(path: str, token: str) -> bytes:
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {token}", "User-Agent": "StockMonitor"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise CloudError(f"Cloudflare 응답 오류 {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CloudError(f"Cloudflare에 접속하지 못했습니다. ({e})")


def pull_inbox(apply, log=None) -> int:
    """사이트에서 들어온 요청(기사 직접 등록·목록에서 빼기)을 가져와 적용하고 지움."""
    if not enabled():
        return 0
    from urllib.parse import quote
    cfg = config()
    base = f"/accounts/{cfg['account_id']}/storage/kv/namespaces/{cfg['namespace_id']}"
    keys = [k["name"] for k in (_call("GET", f"{base}/keys?prefix={quote('inbox:')}&limit=100", cfg["token"]) or [])]
    n = 0
    for key in sorted(keys):
        try:
            item = json.loads(_get_raw(f"{base}/values/{quote(key, safe='')}", cfg["token"]).decode("utf-8"))
            apply(item)
            n += 1
        except Exception as e:
            if log:
                log(f"사이트 요청 처리 실패 {key}: {e}")
        _call("DELETE", f"{base}/values/{quote(key, safe='')}", cfg["token"])
    return n


def _upload_worker(cfg: dict):
    meta = {
        "main_module": "worker.js",
        "compatibility_date": "2026-03-01",
        "bindings": [
            {"type": "kv_namespace", "name": "KV", "namespace_id": cfg["namespace_id"]},
            {"type": "plain_text", "name": "PW_SALT", "text": cfg["pw_salt"]},
            {"type": "secret_text", "name": "PW_HASH", "text": cfg["pw_hash"]},
            {"type": "secret_text", "name": "COOKIE_SECRET", "text": cfg["cookie_secret"]},
        ],
    }
    boundary = "----StockMon" + secrets.token_hex(12)
    parts = []
    for name, filename, ctype, content in (
            ("metadata", None, "application/json", json.dumps(meta).encode("utf-8")),
            ("worker.js", "worker.js", "application/javascript+module", WORKER_JS.read_bytes())):
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if filename:
            head += f'; filename="{filename}"'
        head += f"\r\nContent-Type: {ctype}\r\n\r\n"
        parts.append(head.encode("utf-8") + content + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode("utf-8")
    _call("PUT", f"/accounts/{cfg['account_id']}/workers/scripts/{SCRIPT}", cfg["token"], raw=body,
          ctype=f"multipart/form-data; boundary={boundary}")


def _pw_fields(password: str) -> dict:
    salt = secrets.token_hex(16)
    return {"pw_salt": salt, "pw_hash": hashlib.sha256((salt + password).encode("utf-8")).hexdigest(),
            "cookie_secret": secrets.token_hex(32)}


def _fingerprint(password: str, account_id: str) -> str:
    return hashlib.sha256(f"go-site:{account_id}:{password}".encode("utf-8")).hexdigest()


def ensure_site(log=None, force: bool = False) -> dict:
    """클라우드 실행용: 사이트가 없으면 만들고, 비밀번호(SITE_PASSWORD)가 바뀌었으면 사이트에 반영."""
    if not env_mode():
        return status()
    password = os.environ.get("GO_SITE_PASSWORD", "")
    pub = _read(PUBLIC)
    if not pub.get("namespace_id") or force:
        return setup(os.environ["GO_CF_TOKEN"], password, log)
    if pub.get("pw_fingerprint") != _fingerprint(password, pub["account_id"]):
        _check_password(password)
        cfg = config() | _pw_fields(password)
        _upload_worker(cfg)
        pub["pw_fingerprint"] = _fingerprint(password, pub["account_id"])
        _write(PUBLIC, pub)
        if log:
            log("사이트 비밀번호를 새 값으로 바꿨습니다.")
    return status()


def _check_password(password: str):
    if len(password or "") < 8:
        raise ValueError("비밀번호는 8자 이상으로 정해 주세요.")


def setup(token: str, password: str, log=None) -> dict:
    """처음 연결: KV 저장소·workers.dev 주소·사이트를 만들고 설정을 저장."""
    token = (token or "").strip()
    if not token:
        raise ValueError("API 토큰을 붙여 넣어 주세요.")
    _check_password(password)
    _call("GET", "/user/tokens/verify", token)
    accounts = _call("GET", "/accounts?per_page=50", token) or []
    if not accounts:
        raise CloudError("이 토큰으로 볼 수 있는 Cloudflare 계정이 없습니다. 토큰을 만들 때 '계정 리소스'에 본인 계정을 넣어 주세요.")
    acc = accounts[0]
    aid = acc["id"]

    # KV 저장소 (이미 있으면 다시 사용)
    ns_id = None
    for ns in _call("GET", f"/accounts/{aid}/storage/kv/namespaces?per_page=100", token) or []:
        if ns.get("title") == KV_TITLE:
            ns_id = ns["id"]
    if not ns_id:
        ns_id = _call("POST", f"/accounts/{aid}/storage/kv/namespaces", token, {"title": KV_TITLE})["id"]

    # workers.dev 하위 주소 (계정에 하나. 없으면 만듦)
    sub = None
    try:
        sub = (_call("GET", f"/accounts/{aid}/workers/subdomain", token) or {}).get("subdomain")
    except CloudError:
        sub = None
    if not sub:
        for _ in range(5):
            cand = "stockmon-" + secrets.token_hex(3)
            try:
                sub = _call("PUT", f"/accounts/{aid}/workers/subdomain", token, {"subdomain": cand})["subdomain"]
                break
            except CloudError as e:
                if log:
                    log(f"workers.dev 주소 {cand} 사용 불가: {e}")
                continue
        if not sub:
            raise CloudError("workers.dev 주소를 만들지 못했습니다. Cloudflare 화면의 Workers 메뉴를 한 번 열어 본 뒤 다시 시도해 주세요.")

    cfg = {"token": token, "account_id": aid, "account_name": acc.get("name"), "namespace_id": ns_id,
           "subdomain": sub, "script": SCRIPT, "url": f"https://{SCRIPT}.{sub}.workers.dev",
           "created_at": datetime.now(KST).isoformat(timespec="minutes"), "enabled": True, **_pw_fields(password)}
    _upload_worker(cfg)
    _call("POST", f"/accounts/{aid}/workers/scripts/{SCRIPT}/subdomain", token,
          {"enabled": True, "previews_enabled": False})
    if env_mode():
        _write(PUBLIC, {k: cfg[k] for k in ("account_id", "account_name", "namespace_id", "subdomain", "script", "url",
                                             "created_at")} | {"pw_fingerprint": _fingerprint(password, aid)})
    else:
        _write(CONFIG, cfg)
    _set_state(hashes={}, last_error=None, last_error_at=None)
    if log:
        log(f"어디서나 보기 사이트 연결: {cfg['url']}")
    return status()


def change_password(password: str) -> dict:
    _check_password(password)
    cfg = config()
    if not cfg.get("token"):
        raise ValueError("먼저 사이트를 연결해 주세요.")
    cfg.update(_pw_fields(password))      # 쿠키 서명값도 바꿔서 기존 로그인은 모두 끊김
    _upload_worker(cfg)
    _write(CONFIG, cfg)
    return status()


def disconnect(delete_remote: bool = False) -> dict:
    cfg = config()
    if delete_remote and cfg.get("token"):
        try:
            _call("DELETE", f"/accounts/{cfg['account_id']}/workers/scripts/{SCRIPT}", cfg["token"])
            _call("DELETE", f"/accounts/{cfg['account_id']}/storage/kv/namespaces/{cfg['namespace_id']}", cfg["token"])
        except CloudError as e:
            raise CloudError(f"Cloudflare에서 사이트를 지우지 못했습니다: {e}")
    try:
        CONFIG.unlink()
    except FileNotFoundError:
        pass
    _set_state(hashes={})
    return status()


# ------------------------------------------------------------------ 올릴 내용 만들기

def _h(v) -> str:
    raw = v if isinstance(v, bytes) else v.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _dump(obj) -> str:
    import webserver as ws        # 화면 서버와 똑같은 형식으로 만들기 위해 같은 함수를 씀
    return json.dumps(ws.clean(obj), ensure_ascii=False, separators=(",", ":"))


def _ui_items() -> list[tuple[str, str, bool]]:
    out = []
    for rel in UI_FILES:
        p = WEB / rel
        if not p.exists():
            continue
        if rel == "index.html":
            html = p.read_text(encoding="utf-8")
            html = html.replace('<script src="app.js"></script>',
                                '<script>window.GO_CLOUD=true</script>\n  <script src="app.js"></script>')
            out.append(("ui:/index.html", html, False))
        elif rel.endswith((".woff2", ".ico")):
            out.append((f"ui:/{rel}", base64.b64encode(p.read_bytes()).decode("ascii"), True))
        else:
            out.append((f"ui:/{rel}", p.read_text(encoding="utf-8"), False))
    return out


def _live_item() -> tuple[str, str, bool]:
    import webserver as ws
    status_ = ws.api_status({"_viewer": "cloud"})
    payload = {
        "published_at": datetime.now(KST).isoformat(timespec="seconds"),
        "runner": runner(),
        "interval_min": int(os.environ.get("GO_INTERVAL_MIN", "15" if runner() == "github" else "2")),
        "status": status_,
        "intraday": ws.api_intraday({}),
        "events": ws.api_events({}),
    }
    return ("live", _dump(payload), False)


def _event_items(days: int = 180) -> list[tuple[str, str, bool]]:
    import webserver as ws
    from .db import connect
    since = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%dT00:00")
    with connect() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM events WHERE start_ts >= ? ORDER BY id", (since,))]
    out = []
    for i in ids:
        try:
            out.append((f"event:{i}", _dump(ws.api_event_detail({}, i)), False))
        except Exception:
            continue
    return out


def _intraday_items() -> list[tuple[str, str, bool]]:
    import webserver as ws
    first = ws.api_intraday({})
    out = []
    for d in (first.get("dates") or [])[1:]:
        out.append((f"intraday:{d}", _dump(ws.api_intraday({"date": d})), False))
    return out


def _article_items() -> list[tuple[str, str, bool]]:
    import webserver as ws
    lst = ws.api_articles({})
    details = {}
    for a in lst.get("items") or []:
        try:
            details[a["id"]] = ws.api_article_detail({}, a["id"])
        except Exception:
            continue
    return [("articles", _dump(lst), False), ("article_details", _dump(details), False)]


# ------------------------------------------------------------------ 올리기

def _file_lock():
    RUN.mkdir(exist_ok=True)
    fh = open(RUN / "cloud.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def publish(parts=("live", "events", "intraday", "articles", "ui"), log=None) -> dict:
    """바뀐 것만 KV에 올림. 하루 쓰기 한도를 넘지 않도록 조절."""
    if not enabled():
        return {"skipped": "not connected"}
    with _lock:
        fh = _file_lock()
        if fh is None:
            return {"skipped": "busy"}          # 다른 프로세스(화면 서버/감시 엔진)가 올리는 중
        try:
            _set_state(busy_until=time.time() + 120)
            items = []
            if "ui" in parts:
                items += _ui_items()
            if "live" in parts:
                items.append(_live_item())
            if "events" in parts:
                items += _event_items()
            if "intraday" in parts:
                items += _intraday_items()
            if "articles" in parts:
                items += _article_items()
            hashes = dict(_state().get("hashes") or {})
            changed = [(k, v, b) for k, v, b in items if hashes.get(k) != _h(v)]
            budget = DAILY_WRITE_CAP - writes_today()
            if len(changed) > budget:
                # 한도가 빠듯하면 실시간 요약(live)부터, 나머지는 다음 날로
                changed.sort(key=lambda x: 0 if x[0] == "live" else 1 if x[0].startswith("event:") else 2)
                changed = changed[:max(0, budget)]
            if not changed:
                _set_state(busy_until=0, last_publish=datetime.now(KST).isoformat(timespec="seconds"))
                return {"written": 0}
            cfg = config()
            # 한 번에 너무 크지 않게 나눠서 올림
            batch, size = [], 0
            for k, v, b in changed:
                batch.append({"key": k, "value": v, "base64": b})
                size += len(v)
                if size > 20_000_000:
                    _bulk(cfg, batch)
                    batch, size = [], 0
            if batch:
                _bulk(cfg, batch)
            for k, v, b in changed:
                hashes[k] = _h(v)
            _count_writes(len(changed))
            now = datetime.now(KST).isoformat(timespec="seconds")
            kw = {"hashes": hashes, "busy_until": 0, "last_publish": now, "last_error": None, "last_error_at": None}
            if any(k == "live" for k, _, _ in changed):
                kw["last_live"] = now
            _set_state(**kw)
            if log:
                log(f"어디서나 보기 업로드 {len(changed)}건 (오늘 {writes_today()}/{DAILY_WRITE_CAP})")
            return {"written": len(changed), "keys": [k for k, _, _ in changed]}
        except Exception as e:
            _set_state(busy_until=0, last_error=str(e), last_error_at=datetime.now(KST).isoformat(timespec="seconds"))
            if log:
                log(f"어디서나 보기 업로드 실패: {e}")
            raise
        finally:
            fh.close()


def _bulk(cfg: dict, batch: list[dict]):
    _call("PUT", f"/accounts/{cfg['account_id']}/storage/kv/namespaces/{cfg['namespace_id']}/bulk",
          cfg["token"], batch, timeout=120)


def publish_async(log=None):
    def run():
        try:
            publish(log=log)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


class Scheduler:
    """감시 엔진에서 매 주기 호출. 장중 2분마다 요약, 기사 묶음은 30분마다."""

    def __init__(self, log=None):
        self.log = log
        self.last = {}

    def _due(self, key: str, every: timedelta, now: datetime) -> bool:
        t = self.last.get(key)
        if t is None or now - t >= every:
            self.last[key] = now
            return True
        return False

    def tick(self, now: datetime, market: bool):
        if not enabled():
            return
        parts = []
        if self._due("ui", timedelta(hours=6), now):
            parts.append("ui")
        if self._due("live", timedelta(minutes=2 if market else 30), now):
            parts.append("live")
        if self._due("events", timedelta(minutes=10 if market else 30), now):
            parts.append("events")
        if self._due("intraday", timedelta(minutes=30 if market else 180), now):
            parts.append("intraday")
        if self._due("articles", timedelta(minutes=30 if market else 120), now):
            parts.append("articles")
        if not parts:
            return
        try:
            publish(tuple(parts), log=(self.log.info if self.log else None))
        except Exception as e:
            if self.log:
                self.log.warning("어디서나 보기 업로드 실패(계속 감시): %s", e)
