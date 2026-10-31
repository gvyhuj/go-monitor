"""실시간 중계(Cloudflare Worker)를 만들고 한투 접속 토큰을 넘겨 줌 (GitHub 실행이 함).

구조
  사이트(브라우저) --장중 5초마다--> 중계 Worker(go-relay) --3초에 한 번까지--> 한투 Open API
  GitHub 실행 ----토큰을 받으면 KV에 넣음----> Worker가 그 토큰을 씀 (Worker는 토큰을 받지 않음: 발급은 한 곳에서만)

- 필요한 비밀값: CLOUDFLARE_API_TOKEN·CLOUDFLARE_ACCOUNT_ID(Cloudflare), KIS_APP_KEY·KIS_APP_SECRET(한투)
- 한투 열쇠는 Worker 비밀값으로만 올리고, 사이트에는 중계 주소와 들어가는 값(AUTH)만 암호화된 자료로 넣음
- 바뀐 게 있을 때만 다시 올림 (Worker 코드·열쇠·AUTH 지문)
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

from .cloud import CloudError, _call
from .db import get_state, set_state

SCRIPT = "go-relay"
KV_TITLE = "go-relay"
WORKER_JS = Path(__file__).with_name("relay_worker.js")
INFO_KEY = "relay:info"          # {url, auth} → 사이트 상태 자료(암호화)에 실어 보냄


def enabled() -> bool:
    return all(os.environ.get(k) for k in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "KIS_APP_KEY", "KIS_APP_SECRET"))


def _cf():
    return os.environ["CLOUDFLARE_API_TOKEN"].strip(), os.environ["CLOUDFLARE_ACCOUNT_ID"].strip()


def _state(key: str) -> dict:
    st = get_state(key)
    try:
        return json.loads(st["value"]) if st and st.get("value") else {}
    except ValueError:
        return {}


def _namespace(token: str, aid: str) -> str:
    for ns in _call("GET", f"/accounts/{aid}/storage/kv/namespaces?per_page=100", token) or []:
        if ns.get("title") == KV_TITLE:
            return ns["id"]
    return _call("POST", f"/accounts/{aid}/storage/kv/namespaces", token, body={"title": KV_TITLE})["id"]


def _subdomain(token: str, aid: str) -> str:
    try:
        sub = (_call("GET", f"/accounts/{aid}/workers/subdomain", token) or {}).get("subdomain")
    except CloudError:
        sub = None
    if not sub:                                   # 계정에 workers.dev 주소가 아직 없으면 하나 만듦
        sub = "gomon-" + secrets.token_hex(3)
        _call("PUT", f"/accounts/{aid}/workers/subdomain", token, body={"subdomain": sub})
    return sub


def _upload(token: str, aid: str, ns_id: str, auth: str, code: str, origin: str):
    meta = {
        "main_module": "worker.js",
        "compatibility_date": "2026-03-01",
        "compatibility_flags": ["allow_custom_ports"],      # 한투 주소는 9443 포트
        "bindings": [
            {"type": "kv_namespace", "name": "KV", "namespace_id": ns_id},
            {"type": "secret_text", "name": "KIS_APP_KEY", "text": os.environ["KIS_APP_KEY"]},
            {"type": "secret_text", "name": "KIS_APP_SECRET", "text": os.environ["KIS_APP_SECRET"]},
            {"type": "secret_text", "name": "AUTH", "text": auth},
            {"type": "secret_text", "name": "CODE", "text": code},
            {"type": "plain_text", "name": "ORIGIN", "text": origin},
        ],
    }
    boundary = "----GoRelay" + secrets.token_hex(12)
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
    _call("PUT", f"/accounts/{aid}/workers/scripts/{SCRIPT}", token, raw=body,
          ctype=f"multipart/form-data; boundary={boundary}")
    _call("POST", f"/accounts/{aid}/workers/scripts/{SCRIPT}/subdomain", token, body={"enabled": True})


def ensure(code: str, log=None) -> dict | None:
    """중계 Worker가 없거나 바뀐 게 있으면 올리고 {url, auth}를 기록에 남김. 바뀐 게 없으면 Cloudflare를 부르지 않음."""
    if not enabled():
        return None
    token, aid = _cf()
    info = _state(INFO_KEY)
    auth = info.get("auth") or secrets.token_urlsafe(32)
    owner = (os.environ.get("GITHUB_REPOSITORY", "") or "/").split("/")[0]
    origin = f"https://{owner.lower()}.github.io" if owner else "*"
    fp = hashlib.sha256("\n".join([WORKER_JS.read_text("utf-8"), os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"],
                                   auth, code, origin, aid]).encode("utf-8")).hexdigest()
    if info.get("fp") == fp and info.get("url") and info.get("ns"):
        return info
    ns_id = _namespace(token, aid)
    sub = _subdomain(token, aid)
    _upload(token, aid, ns_id, auth, code, origin)
    info = {"url": f"https://{SCRIPT}.{sub}.workers.dev", "auth": auth, "ns": ns_id, "fp": fp}
    set_state(INFO_KEY, json.dumps(info))
    set_state("relay:token_pushed", "")          # 새 저장소일 수 있으니 토큰을 다시 넣음
    if log:
        log(f"실시간 중계를 올렸습니다: {info['url']}")
    return info


def push_token(tok: dict) -> bool:
    """GitHub 실행이 받은 한투 토큰을 중계 KV에 넣음 (바뀌었을 때만). tok = {token, expires(한국 시각)}."""
    if not enabled() or not tok or not tok.get("token"):
        return False
    info = _state(INFO_KEY)
    if not info.get("ns"):
        return False
    st = get_state("relay:token_pushed")
    if st and st.get("value") == tok["expires"]:
        return False
    from datetime import datetime, timedelta, timezone
    exp = datetime.fromisoformat(tok["expires"]).replace(tzinfo=timezone(timedelta(hours=9)))
    token, aid = _cf()
    value = json.dumps({"token": tok["token"], "exp_ms": int(exp.timestamp() * 1000)})
    boundary = "----GoRelay" + secrets.token_hex(12)
    body = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                   for k, v in (("value", value), ("metadata", "{}"))) + f"--{boundary}--\r\n"
    _call("PUT", f"/accounts/{aid}/storage/kv/namespaces/{info['ns']}/values/kis_token", token,
          raw=body.encode("utf-8"), ctype=f"multipart/form-data; boundary={boundary}")
    set_state("relay:token_pushed", tok["expires"])
    return True


def public_info() -> dict | None:
    """사이트(암호화된 상태 자료)에 실을 것: 주소와 들어가는 값만."""
    info = _state(INFO_KEY)
    return {"url": info["url"], "auth": info["auth"]} if info.get("url") and info.get("auth") else None


def check() -> str:
    """중계를 실제로 한 번 불러 봄 (Cloudflare → 한투). 정상이면 한 줄 요약, 아니면 RuntimeError."""
    import urllib.error
    import urllib.request
    info = _state(INFO_KEY)
    if not info.get("url"):
        raise RuntimeError("중계가 아직 올라가지 않음")
    req = urllib.request.Request(info["url"] + "/live", headers={"x-go-auth": info["auth"], "User-Agent": "go-monitor-check"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except Exception:  # noqa: BLE001
            msg = ""
        raise RuntimeError(f"중계 응답 {e.code} {msg}".strip()) from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise RuntimeError(f"중계에 접속하지 못함: {type(e).__name__}") from None
    q = d.get("quote") or {}
    if q.get("price") is None:
        raise RuntimeError("중계가 현재가를 주지 않음")
    return f"현재가 {q['price']:,}원 · KOSDAQ {(d.get('kosdaq') or {}).get('price')} · 1분봉 {len(d.get('bars') or [])}개 ({d.get('at')} 기준)"

