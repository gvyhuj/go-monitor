from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from .db import connect
from .sitebuild import decrypt, derive_key, encrypt


def analysis_version() -> str:
    root = Path(__file__).resolve().parents[1]
    sources = [root / "core" / name for name in ("articles.py", "causal.py", "eventstudy.py", "analysis.py", "causes.py")]
    return hashlib.sha256(b"".join(path.read_bytes() for path in sources)).hexdigest()[:16]


def _payload(entity: str, data: dict) -> dict:
    if entity.startswith("event:"):
        return {key: value for key, value in data.items() if key not in ("bars", "kosdaq", "related_bars")}
    return {key: data[key] for key in ("article", "effect", "confounders") if key in data}


def capture(history, key: str, raw: str, recorded_at: str, seed: bool = False) -> None:
    if history is None:
        return
    if key in ("article_details", "disclosure_details"):
        prefix = "article" if key == "article_details" else "disclosure"
        for identity, data in json.loads(raw).items():
            history.record(f"{prefix}:{identity}", data, recorded_at,
                           version="기존 보관본·계산 버전 미상" if seed else None, seed=seed)
    elif key.startswith("event:"):
        history.record(key, json.loads(raw), recorded_at,
                       version="기존 보관본·계산 버전 미상" if seed else None, seed=seed)


class History:
    def __init__(self, password: str, other_passwords: tuple[str, ...] = ()):
        if len(password) < 8 or password in other_passwords:
            raise ValueError("관리자 비밀번호는 일반·백업 비밀번호와 다르게 8자 이상으로 설정해야 합니다.")
        with connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS analysis_history (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                               "entity_digest TEXT NOT NULL, result_digest TEXT NOT NULL, payload BLOB NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_history_entity ON analysis_history(entity_digest,id)")
            row = connection.execute("SELECT value FROM system_state WHERE key='history:salt'").fetchone()
            salt = bytes.fromhex(row[0]) if row else secrets.token_bytes(16)
            self.key = derive_key(password, salt)
            verifier = hmac.new(self.key, b"history:verify", hashlib.sha256).hexdigest()
            previous = connection.execute("SELECT value FROM system_state WHERE key='history:verifier'").fetchone()
            if previous and not hmac.compare_digest(previous[0], verifier):
                raise ValueError("관리자 비밀번호가 기존 이력과 다릅니다. 비밀번호 변경 전에 이력 재암호화가 필요합니다.")
            connection.execute("INSERT OR IGNORE INTO system_state(key,value) VALUES ('history:salt',?)", (salt.hex(),))
            connection.execute("INSERT OR IGNORE INTO system_state(key,value) VALUES ('history:verifier',?)", (verifier,))
        self.version = analysis_version()

    def _digest(self, value: str) -> str:
        return hmac.new(self.key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def record(self, entity: str, data: dict, recorded_at: str, version: str | None = None,
               seed: bool = False) -> bool:
        payload = _payload(entity, data)
        if not payload:
            return False
        version = version or self.version
        canonical = json.dumps({"version": version, "data": payload}, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
        entity_digest, result_digest = self._digest(entity), self._digest(canonical)
        with connect() as connection:
            previous = connection.execute("SELECT result_digest FROM analysis_history WHERE entity_digest=? "
                                          "ORDER BY id DESC LIMIT 1", (entity_digest,)).fetchone()
            if previous and (seed or previous[0] == result_digest):
                return False
            record = {"entity": entity, "version": version, "recorded_at": recorded_at, "data": payload}
            encrypted = encrypt(self.key, json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            connection.execute("INSERT INTO analysis_history(entity_digest,result_digest,payload) VALUES (?,?,?)",
                               (entity_digest, result_digest, encrypted))
        return True

    def records(self) -> dict[str, list[dict]]:
        with connect() as connection:
            rows = connection.execute("SELECT id,payload FROM analysis_history ORDER BY id").fetchall()
        grouped = {}
        for row in rows:
            record = json.loads(decrypt(self.key, row[1]))
            record["id"] = row[0]
            grouped.setdefault(record["entity"], []).append(record)
        return grouped


def build_admin(out: Path, password: str, history: History | None) -> None:
    import base64
    import shutil

    source = Path(__file__).resolve().parents[1] / "web" / "admin"
    target = out / "admin"
    shutil.copytree(source, target)
    config = {"enabled": history is not None}
    if history is not None:
        salt = secrets.token_bytes(16)
        key = derive_key(password, salt)
        config.update({"salt": base64.b64encode(salt).decode(), "iterations": 200_000})
        index = []
        for entity, records in history.records().items():
            file_id = history._digest(entity)
            article = records[-1]["data"].get("article") or {}
            event = records[-1]["data"].get("event") or {}
            label = article.get("title") or f"주가 기록 {event.get('start_ts') or entity}"
            index.append({"file": file_id, "label": label, "count": len(records),
                          "last_at": records[-1]["recorded_at"]})
            (target / f"{file_id}.bin").write_bytes(encrypt(key, json.dumps(records, ensure_ascii=False).encode("utf-8")))
        (target / "index.bin").write_bytes(encrypt(key, json.dumps(index, ensure_ascii=False).encode("utf-8")))
    (target / "config.js").write_text("window.GO_ADMIN=" + json.dumps(config) + ";", encoding="utf-8")
