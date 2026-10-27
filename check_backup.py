"""백업 복원 시험: 암호화된 백업(최신·주간)을 실제로 풀어 SQLite로 열고, 핵심 표가 차 있고 최근 자료까지 있는지 확인.

공개 저장소라 실행 기록도 공개 → 값은 찍지 않고 표별 줄 수·마지막 날짜만 남김.
문제가 있으면 끝 상태 1 (워크플로가 실패로 표시되어 GitHub이 저장소 주인에게 메일을 보냄).

python check_backup.py <백업 파일> [<백업 파일> ...]   (열쇠: GO_DATA_KEY, 없으면 GO_SITE_PASSWORD)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from core.sitebuild import decrypt, derive_key, unpack_db

# (표, 날짜 칸) — 날짜 칸이 있으면 마지막 날짜도 확인
TABLES = [("daily_bars", "date"), ("minute_bars", "ts"), ("articles", "ts"), ("disclosures", "ts"),
          ("events", "start_ts"), ("investor_daily", None), ("trader_daily", "date")]
FRESH_DAYS = {"daily_bars": 7, "minute_bars": 7, "articles": 10}     # 이보다 오래되면 자료가 멈춘 것 (긴 연휴 감안)


def check(path: Path, password: str, today: date) -> list[str]:
    raw = path.read_bytes()
    problems = []
    try:
        data = unpack_db(decrypt(derive_key(password, raw[:16]), raw[16:]))
    except Exception as e:  # noqa: BLE001
        return [f"{path.name}: 풀지 못함 ({type(e).__name__}) — 열쇠가 맞는지, 파일이 깨지지 않았는지 확인"]
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "monitor.db"
        db.write_bytes(data)
        with closing(sqlite3.connect(db)) as conn:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            print(f"### {path.name} ({len(raw) / 1e6:.1f}MB) · 무결성 {ok}")
            if ok != "ok":
                problems.append(f"{path.name}: SQLite 무결성 검사 실패 ({ok[:100]})")
            print("| 표 | 줄 수 | 마지막 날짜 |\n|---|---|---|")
            for table, col in TABLES:
                try:
                    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    last = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()[0] if col else None
                except sqlite3.Error as e:
                    problems.append(f"{path.name}: {table} 표를 읽지 못함 ({e})")
                    continue
                print(f"| {table} | {n:,} | {str(last or '-')[:16]} |")
                if n == 0 and table in ("daily_bars", "minute_bars", "articles"):
                    problems.append(f"{path.name}: {table} 표가 비어 있음")
                lim = FRESH_DAYS.get(table)
                if lim and path.name != "monitor.db.enc":       # 주간 백업은 최대 1주 전 것
                    lim += 7
                if lim and last and str(last)[:10] < (today - timedelta(days=lim)).isoformat():
                    problems.append(f"{path.name}: {table} 마지막 자료가 {str(last)[:10]} ({lim}일 넘게 멈춤)")
    return problems


def main(argv) -> int:
    password = os.environ.get("GO_DATA_KEY") or os.environ.get("GO_SITE_PASSWORD")
    files = [Path(x) for x in argv[1:] if Path(x).is_file()]
    if not password:
        print("::error::GO_DATA_KEY / GO_SITE_PASSWORD 가 없습니다.")
        return 1
    if not files:
        print("::error::확인할 백업 파일이 없습니다.")
        return 1
    problems = []
    for f in files:
        problems += check(f, password, date.today())
    for p in problems:
        print(f"::error::{p}")
    print("\n" + ("문제 없음: 백업을 풀어 열 수 있고 최근 자료까지 들어 있습니다." if not problems else f"문제 {len(problems)}건"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
