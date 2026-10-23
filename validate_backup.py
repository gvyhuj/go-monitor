from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from core.sitebuild import decrypt, derive_key, encrypt, unpack_db
from validate_event_study import validate


def inspect_backup(backup: Path, password: str, output: Path) -> dict:
    encrypted = backup.read_bytes()
    original_hash = hashlib.sha256(encrypted).hexdigest()
    with tempfile.TemporaryDirectory(prefix="event-study-") as temporary:
        database = Path(temporary) / "monitor.db"
        database.write_bytes(unpack_db(decrypt(derive_key(password, encrypted[:16]), encrypted[16:])))
        database_hash = hashlib.sha256(database.read_bytes()).hexdigest()
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute("SELECT value FROM system_state WHERE key='private_config'").fetchone()
            configuration = json.loads(row[0]) if row else {}
            symbol = configuration.get("symbol")
            if not symbol:
                raise ValueError("분석할 종목 설정이 없습니다.")
            calendar = [row[0] for row in connection.execute(
                "SELECT date FROM daily_bars WHERE symbol='KOSDAQ' ORDER BY date")]
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("daily_bars", "minute_bars", "articles", "disclosures", "pr_events")}
        if len(calendar) < 62:
            raise ValueError("검증할 시장 시세가 부족합니다.")
        report = validate(database, symbol, calendar[61], calendar[-1])
        if hashlib.sha256(database.read_bytes()).hexdigest() != database_hash:
            raise RuntimeError("검증 중 복원한 데이터가 변경됐습니다.")
    if hashlib.sha256(backup.read_bytes()).hexdigest() != original_hash:
        raise RuntimeError("암호화 백업이 변경됐습니다.")
    summary = {key: report[key] for key in ("event_records", "candidate_days", "evaluated_days", "unavailable_days",
                                          "legacy_flag_rate", "new_flag_rate", "interpretation")}
    summary.update({"row_counts": counts, "market_days": len(calendar), "source_unchanged": True,
                    "coverage": report["coverage"],
                    "backup_sha256": original_hash, "validation_available": report["evaluated_days"] > 0,
                    "accuracy_improvement_proven": False})
    report["summary"] = summary
    output.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(16)
    serialized = json.dumps(report, ensure_ascii=False, allow_nan=False).encode("utf-8")
    (output / "validation-report.enc").write_bytes(salt + encrypt(derive_key(password, salt), serialized))
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    password = os.environ.get("VALIDATION_DATA_KEY")
    if not password:
        print("검증에 필요한 기존 암호화 설정이 없습니다.", file=sys.stderr)
        return 1
    try:
        summary = inspect_backup(Path("validation-source/monitor.db.enc"), password, Path("validation-output"))
    except Exception:
        print("백업 복원 또는 검증에 실패했습니다. 원본 자료는 변경하지 않았습니다.", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
