"""GitHub Pages용 사이트 만들기 (서버 없이 정적 파일만, 분석 자료는 비밀번호로 암호화).

- 화면 파일(index.html, app.js, 글꼴 …)은 그대로 올리고,
  분석 자료(JSON)는 사이트 비밀번호로 암호화해 d/*.bin 으로 올립니다.
- 브라우저가 비밀번호로 열쇠를 만들어 풀어 봅니다 (비밀번호가 틀리면 아무것도 볼 수 없음).
- 암호: AES-256-GCM, 열쇠 = PBKDF2-HMAC-SHA256(비밀번호, salt, 200,000회). 브라우저 WebCrypto와 같은 방식.
- 기록(SQLite) 백업도 같은 방식으로 암호화해 저장소 data 브랜치에 둡니다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ITER = 200_000
SITE_FILES = ["index.html", "app.js", "app.css", "app.ico", "vendor/lightweight-charts.standalone.production.js",
              "vendor/LICENSE-lightweight-charts.txt", "fonts/PretendardVariable.woff2", "fonts/LICENSE-Pretendard.txt"]


def derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITER, dklen=32)


def encrypt(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    return iv + AESGCM(key).encrypt(iv, data, None)


def decrypt(key: bytes, blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def file_name(key: str) -> str:
    return key.replace(":", "_").replace("/", "_") + ".bin"


def build(out: Path, password: str, salt: bytes, items: list[tuple[str, str, bool]], repo: str = "") -> int:
    """items: (키, JSON 문자열, base64여부) — core.cloud 의 업로드 목록과 같은 형식."""
    if len(password or "") < 8:
        raise ValueError("SITE_PASSWORD(사이트 비밀번호)가 없거나 8자보다 짧습니다.")
    if out.exists():
        shutil.rmtree(out)
    (out / "d").mkdir(parents=True)
    for rel in SITE_FILES:
        src = WEB / rel
        if not src.exists():
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if rel == "index.html":
            html = src.read_text(encoding="utf-8")
            cfg = json.dumps({"repo": repo, "salt": base64.b64encode(salt).decode(), "iter": ITER})
            html = html.replace('<script src="app.js"></script>',
                                f'<script>window.GO_STATIC={cfg}</script>\n  <script src="app.js"></script>')
            html = html.replace("<head>", '<head>\n  <meta name="robots" content="noindex, nofollow">', 1)
            dst.write_text(html, encoding="utf-8")
        else:
            shutil.copyfile(src, dst)
    key = derive_key(password, salt)
    n = 0
    for k, v, is_b64 in items:
        if k.startswith("ui:"):
            continue
        raw = base64.b64decode(v) if is_b64 else v.encode("utf-8")
        (out / "d" / file_name(k)).write_bytes(encrypt(key, raw))
        n += 1
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return n


def salt_for(meta_path: Path, password: str) -> bytes:
    """사이트 열쇠의 salt. 비밀번호가 바뀌면 새 salt (이전에 기억된 로그인은 다시 묻게 됨)."""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    fp = hashlib.sha256(("go-fp:" + password).encode("utf-8")).hexdigest()
    if meta.get("salt") and meta.get("fp") == fp:
        return base64.b64decode(meta["salt"])
    salt = secrets.token_bytes(16)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({"salt": base64.b64encode(salt).decode(), "fp": fp}), encoding="utf-8")
    return salt


def backup_db(db: Path, out: Path, password: str):
    salt = secrets.token_bytes(16)
    blob = encrypt(derive_key(password, salt), db.read_bytes())
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.db.enc"):          # 예전 이름의 기록 파일은 지움
        if old.name != "monitor.db.enc":
            old.unlink()
    (out / "monitor.db.enc").write_bytes(salt + blob)


def find_backup(src: Path) -> Path | None:
    files = sorted(src.glob("*.db.enc"), key=lambda f: f.stat().st_mtime, reverse=True) if src.exists() else []
    return files[0] if files else None


def restore_db(src: Path, db: Path, password: str) -> bool:
    f = find_backup(src)
    if not f:
        return False
    raw = f.read_bytes()
    try:
        data = decrypt(derive_key(password, raw[:16]), raw[16:])
    except Exception:
        return False
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(data)
    return True


# ------------------------------------------------------------------ 올릴 자료 (바뀐 것만 다시 계산)

_TABLE = """CREATE TABLE IF NOT EXISTS site_items (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, b64 INTEGER DEFAULT 0, built_at TEXT NOT NULL)"""


def _market_hours(now: datetime) -> bool:
    hm = now.hour * 100 + now.minute
    return now.weekday() < 5 and 850 <= hm <= 1600


def refresh_items(now: datetime, log=None) -> list[tuple[str, str, bool]]:
    """사이트에 올릴 자료 전체. 무거운 것(이벤트 상세·지난 날짜·기사 상세)은 필요할 때만 다시 계산해 기록에 보관."""
    from . import cloud
    import webserver as ws
    from .db import connect

    stamp = now.isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(_TABLE)
        cached = {r["key"]: dict(r) for r in conn.execute("SELECT key, value, b64, built_at FROM site_items")}

    def keep(key, fn, need: bool):
        if not need and key in cached:
            return
        try:
            v = cloud._dump(fn())
        except Exception as e:            # noqa: BLE001 - 한 항목이 실패해도 나머지는 올림
            if log:
                log(f"사이트 자료 {key} 만들기 실패: {e}")
            import traceback
            tb = traceback.format_exc().strip().splitlines()
            where = next((ln.strip() for ln in reversed(tb) if ln.strip().startswith("File ")), "")
            print(f"::warning title=사이트 자료::{key} 만들기 실패: {type(e).__name__}: {str(e)[:150]} @ {where[-120:]}", flush=True)
            return
        cached[key] = {"key": key, "value": v, "b64": 0, "built_at": stamp}
        fresh.add(key)

    fresh: set[str] = set()
    today = now.date().isoformat()
    full = all(not c["built_at"].startswith(today) for c in cached.values() if c["key"].startswith("event:")) \
        and not _market_hours(now)                       # 하루 한 번(장 외) 전체 다시 계산

    # 이벤트 상세 (최근 180일)
    since = (now - timedelta(days=180)).strftime("%Y-%m-%dT00:00")
    recent = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    with connect() as conn:
        evs = [dict(r) for r in conn.execute(
            "SELECT id, start_ts, last_ts, cause_updated_at FROM events WHERE start_ts >= ? ORDER BY id", (since,))]
    want = set()
    for e in evs:
        key = f"event:{e['id']}"
        want.add(key)
        c = cached.get(key)
        need = full or c is None or e["start_ts"][:10] >= recent or \
            (e["cause_updated_at"] or "") > c["built_at"] or (e["last_ts"] or "") > c["built_at"]
        keep(key, lambda i=e["id"]: ws.api_event_detail({}, i), need)

    # 지난 날짜 장중 차트
    first = ws.api_intraday({})
    for d in (first.get("dates") or [])[1:]:
        key = f"intraday:{d}"
        want.add(key)
        c = cached.get(key)
        keep(key, lambda d=d: ws.api_intraday({"date": d}), c is None or c["built_at"][:10] <= d)

    # 기사 목록·상세: 장중 30분, 장 외는 실행할 때마다
    want |= {"articles", "article_details"}
    c = cached.get("articles")
    stale = c is None or not _market_hours(now) or \
        (now - datetime.fromisoformat(c["built_at"])).total_seconds() > 1800
    if stale:
        lst = ws.api_articles({})
        keep("articles", lambda: lst, True)
        def details():
            out = {}
            for a in lst.get("items") or []:
                try:
                    out[a["id"]] = ws.api_article_detail({}, a["id"])
                except Exception:        # noqa: BLE001
                    continue
            return out
        keep("article_details", details, True)

    with connect() as conn:
        for k in fresh:
            c = cached[k]
            conn.execute("INSERT OR REPLACE INTO site_items(key, value, b64, built_at) VALUES (?,?,?,?)",
                         (k, c["value"], c["b64"], c["built_at"]))
        gone = [k for k in cached if k not in want]
        for k in gone:
            conn.execute("DELETE FROM site_items WHERE key=?", (k,))
    if log:
        log(f"사이트 자료: 새로 계산 {len(fresh)}건, 보관본 사용 {len(want) - len(fresh)}건"
            + (" (하루 한 번 전체 갱신)" if full else ""))

    items = [cloud._live_item()]                          # 실시간 요약은 매번
    items += [(k, cached[k]["value"], bool(cached[k]["b64"])) for k in sorted(want) if k in cached]
    return items
