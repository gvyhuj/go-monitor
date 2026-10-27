"""일봉 자료 검증 (수동 실행 '급등락 기준 살펴보기'에 함께): 기록된 일봉을 야후 파이낸스 일봉과 날마다 대조.

- 하루 등락이 두 출처에서 같은지 (0.3%p 넘게 다르면 목록), 한쪽에만 있는 날 (빠진 날이 있으면 이틀 치가 하루로 보임)
- 기록이 맞다면 실제로 얼마나 움직였는지: 기간 누적 등락, 달마다 하루 등락 크기(중간값)와 ±3·5% 넘은 날 수
공개 저장소라 실행 기록도 공개 → 가격·지수 값은 찍지 않고 등락률만.

python verify_daily.py <백업 파일>   (열쇠: GO_DATA_KEY, 없으면 GO_SITE_PASSWORD)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import urllib.request
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from core.sitebuild import decrypt, derive_key, unpack_db

KST = timezone(timedelta(hours=9))


def yahoo_daily(ticker: str) -> dict[str, float]:
    req = urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2y",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read())["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    return {datetime.fromtimestamp(t, KST).date().isoformat(): c for t, c in zip(res["timestamp"], closes) if c}


def rets(prices: dict[str, float]) -> dict[str, float]:
    ds = sorted(prices)
    return {d: prices[d] / prices[p] - 1 for p, d in zip(ds, ds[1:]) if prices[p]}


def check(name: str, ours: dict[str, float], ya: dict[str, float], since: str) -> None:
    ours = {d: v for d, v in ours.items() if d >= since}
    ya = {d: v for d, v in ya.items() if d >= since}
    ro, ry = rets(ours), rets(ya)
    both = sorted(set(ro) & set(ry))
    diff = [(d, ro[d], ry[d]) for d in both if abs(ro[d] - ry[d]) > 0.003]
    only_o, only_y = sorted(set(ours) - set(ya)), sorted(set(ya) - set(ours))
    print(f"\n### {name} — 기록 {len(ours)}일 / 야후 {len(ya)}일 ({since} 부터)")
    print(f"- 하루 등락이 두 출처에서 0.3%p 넘게 다른 날: {len(diff)}일 (함께 있는 {len(both)}일 중)")
    for d, a, b in diff[:15]:
        print(f"  - {d}: 기록 {a * 100:+.2f}% / 야후 {b * 100:+.2f}%")
    print(f"- 기록에만 있는 날: {len(only_o)}일 {only_o[:10]}")
    print(f"- 야후에만 있는 날(기록에 빠진 날): {len(only_y)}일 {only_y[:10]}")
    if ours:
        ds = sorted(ours)
        print(f"- 기간 누적 등락 (기록): {ds[0]} → {ds[-1]} {(ours[ds[-1]] / ours[ds[0]] - 1) * 100:+.1f}%")
    big = sorted((d for d in ro if abs(ro[d]) >= 0.05), key=lambda d: d)
    print(f"- 하루 ±5% 넘은 날 (기록): {len(big)}일 — " + ", ".join(f"{d[5:]} {ro[d] * 100:+.1f}%" + (f"(야후 {ry[d] * 100:+.1f}%)" if d in ry else "(야후 없음)") for d in big[:40]))
    by = defaultdict(list)
    for d, r in ro.items():
        by[d[:7]].append(r)
    print("\n| 달 | 거래일 | 그달 누적 | 하루 등락 크기 중간값 | ±3% 넘은 날 | ±5% 넘은 날 |\n|---|---|---|---|---|---|")
    for m in sorted(by):
        a = np.array(by[m])
        print(f"| {m} | {len(a)} | {(np.prod(1 + a) - 1) * 100:+.1f}% | {np.median(np.abs(a)) * 100:.1f}% | {(np.abs(a) >= 0.03).sum()} | {(np.abs(a) >= 0.05).sum()} |")


def main(argv) -> int:
    password = os.environ.get("GO_DATA_KEY") or os.environ.get("GO_SITE_PASSWORD")
    raw = Path(argv[1]).read_bytes()
    data = unpack_db(decrypt(derive_key(password, raw[:16]), raw[16:]))
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "monitor.db"
        db.write_bytes(data)
        with closing(sqlite3.connect(db)) as conn:
            row = conn.execute("SELECT value FROM system_state WHERE key='private_config'").fetchone()
            sym = (json.loads(row[0]) if row else {}).get("symbol")
            daily = lambda code: dict(conn.execute("SELECT date, close FROM daily_bars WHERE symbol=? AND close > 0", (code,)).fetchall())
            kq, own = daily("KOSDAQ"), daily(sym)
    since = min(own) if own else "2025-11-17"
    print("## 일봉 자료 검증 (기록 vs 야후 파이낸스)")
    for name, ours, ticker in (("KOSDAQ 지수", kq, "%5EKQ11"), ("그린광학", own, f"{sym}.KQ")):
        try:
            check(name, ours, yahoo_daily(ticker), since)
        except Exception as e:  # noqa: BLE001
            print(f"\n### {name}: 야후 일봉을 받지 못함 ({e})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
