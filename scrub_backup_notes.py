"""백업 파일 속 붙여넣은 AI 메모 지우기 (주가 기록 원인 글 + 뉴스 사건 메모). 나머지 기록은 그대로.

사용: python scrub_backup_notes.py 백업1.db.enc [백업2.db.enc ...]
열쇠는 GO_DATA_KEY (없으면 GO_SITE_PASSWORD). 파일마다 풀어서 두 표를 비우고 같은 열쇠로 다시 암호화해 덮어씀.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from core import sitebuild

TABLES = ("manual_causes", "news_event_notes")


def scrub(path: Path, key: str) -> dict:
    raw = path.read_bytes()
    data = sitebuild.unpack_db(sitebuild.decrypt(sitebuild.derive_key(key, raw[:16]), raw[16:]))
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "monitor.db"
        db.write_bytes(data)
        conn = sqlite3.connect(db)
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        n = {t: conn.execute(f"DELETE FROM {t}").rowcount for t in TABLES if t in have}
        conn.commit()
        conn.close()
        out = Path(tmp) / "out"
        sitebuild.backup_db(db, out, key)
        path.write_bytes((out / "monitor.db.enc").read_bytes())
    return n


def main(argv: list[str]) -> int:
    key = os.environ.get("GO_DATA_KEY") or os.environ.get("GO_SITE_PASSWORD", "")
    if not key:
        print("열쇠(GO_DATA_KEY 또는 GO_SITE_PASSWORD)가 없습니다.")
        return 1
    for f in argv[1:]:
        n = scrub(Path(f), key)
        print(f"{Path(f).name}: " + ", ".join(f"{t} {c}건 삭제" for t, c in n.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
