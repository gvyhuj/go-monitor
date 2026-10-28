"""뉴스 반응에서 '한쪽으로 쏠릴 확률'이 숫자로 나오는 조건 목록 (수동 실행 '급등락 기준 살펴보기'에 함께).

자동 실행이 미리 만들어 기록에 넣어 둔 뉴스 반응 자료(site_items 'reactions')를 사이트와 같은 조건으로 걸러서
조건마다 중앙값·오른 수·내린 수·쏠릴 확률(동전 던지기, 양쪽)을 셈. 공개 저장소라 가격·제목은 찍지 않고 비율·건수만.

python react_stats.py <백업 파일>   (열쇠: GO_DATA_KEY, 없으면 GO_SITE_PASSWORD)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from math import comb
from pathlib import Path
from statistics import median

from core.sitebuild import decrypt, derive_key, unpack_db

MAIN_DISC = {"A", "B", "C", "I", "K"}            # web/app.js 의 MAIN_DISC 와 같음
KINDS = [("all", "전체"), ("pr", "보도자료"), ("ext", "외부기사"), ("disc", "주요 공시")]
MODES = [("all", "전체"), ("open", "장 시작 전"), ("session", "장중"), ("late", "15시 이후")]


def chance(up: int, dn: int) -> float:
    n, k = up + dn, max(up, dn)
    if not n or up == dn:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def kind_ok(x, kind):
    if kind == "all":
        return True
    if kind == "pr":
        return x.get("kind") in ("pr", "company")
    if kind == "disc":
        return x.get("kind") == "disc" and (x.get("pblntf") or "") in MAIN_DISC
    return x.get("kind") == "ext"


def value(x, h, vs_kq):
    s = (x.get("steps") or {}).get(h) or {}
    if s.get("own") is None:
        return None
    if vs_kq:
        return None if s.get("kq") is None else s["own"] - s["kq"]
    return s["own"]


def row(label, items, h, vs_kq):
    vs = [v for v in (value(x, h, vs_kq) for x in items) if v is not None]
    if not vs:
        return None
    up, dn = sum(v > 0 for v in vs), sum(v < 0 for v in vs)
    p = chance(up, dn) if up + dn >= 10 else None
    shown = "판단 보류(10건 미만)" if p is None else ("1% 미만" if p < 0.01 else f"약 {round(p * 100)}%")
    return p, f"| {label} | +{h}분 | {'빼고' if vs_kq else '그대로'} | {len(vs)} | {median(vs) * 100:+.2f}% | {up} | {dn} | {shown} |"


def main(argv) -> int:
    password = os.environ.get("GO_DATA_KEY") or os.environ.get("GO_SITE_PASSWORD")
    raw = Path(argv[1]).read_bytes()
    data = unpack_db(decrypt(derive_key(password, raw[:16]), raw[16:]))
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "monitor.db"
        db.write_bytes(data)
        with closing(sqlite3.connect(db)) as conn:
            r = conn.execute("SELECT value FROM site_items WHERE key='reactions'").fetchone()
    items = [x for x in (json.loads(r[0]).get("items") or []) if x.get("category") != "단순 언급"] if r else []
    print(f"## 뉴스 반응: 조건별 쏠릴 확률 (전체 기간, 자료 {len(items)}건)\n")
    print("확률이 숫자로 나오려면 오르거나 내린 건이 10건 이상이어야 함. 확률이 작은 순.\n")
    rows = []
    for h in ("30", "60"):
        for vs_kq in (False, True):
            for kv, kt in KINDS:
                for mv, mt in MODES:
                    sel = [x for x in items if kind_ok(x, kv) and (mv == "all" or x.get("mode") == mv)]
                    out = row(f"{kt} · {mt}", sel, h, vs_kq)
                    if out:
                        rows.append(out)
            for t in sorted({x.get("topic") for x in items if x.get("topic")}):
                out = row(f"주제: {t}", [x for x in items if x.get("topic") == t], h, vs_kq)
                if out:
                    rows.append(out)
    rows.sort(key=lambda o: (o[0] is None, o[0] if o[0] is not None else 2))
    print("| 조건 (종류 · 공개 시점) | 시간 | KOSDAQ 움직임 | 건수 | 중앙값 | 오른 | 내린 | 쏠릴 확률 |\n|---|---|---|---|---|---|---|---|")
    for _, line in rows:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
