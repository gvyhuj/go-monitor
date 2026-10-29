"""Build a synthetic encrypted static site for tests/public_refresh_check.cjs.

Only fixture rows are used; nothing here is production data and the output must never be deployed.
Usage: python tests/fixtures/public_refresh_site.py OUT_DIR
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["GO_PRIVATE_CONFIG"] = json.dumps({"symbol": "000001", "display_name": "검증 종목"})

from core import db, sitebuild  # noqa: E402

PASSWORD = "public-site-password"
SALT = b"0123456789abcdef"
ARTICLES = [  # (id, ts, office, title)
    ("1001", "2026-10-22T10:05", "검증일보", "검증용 광학 부품 공급 계약"),
    ("1002", "2026-10-21T13:20", "검증경제", "검증용 신규 설비 투자"),
    ("1003", "2026-10-20T09:40", "검증신문", "검증용 실적 설명회 개최"),
]


def main(out: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="go-refresh-"))
    db.DATA_DIR, db.DB_PATH = work, work / "monitor.db"
    db.init_db()
    with db.connect() as conn:
        for aid, ts, office, title in ARTICLES:
            conn.execute("INSERT INTO articles(id,symbol,ts,office,title,url,category,mention,story,first_seen) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (aid, "000001", ts, office, title, f"https://example.org/{aid}", "언론 분석", "제목", aid, ts))
    items = sitebuild.refresh_items(datetime(2026, 10, 23, 14, 0))
    sitebuild.build(out, PASSWORD, SALT, items)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
