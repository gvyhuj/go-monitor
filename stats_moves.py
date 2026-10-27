"""급등·급락 기준을 정하기 위한 숫자 (수동 실행 '급등락 기준 살펴보기' 워크플로).

백업을 풀어 그린광학의 평소 움직임 크기를 셈. 공개 저장소라 실행 기록도 공개 → 가격은 찍지 않고
비율·분위수·날 수만 남김.

- 하루 등락(종가 ÷ 기준가): 전체 기간·최근 120거래일, 크기별 분위수와 ±3·5·7·10% 넘은 날 비율 (KOSDAQ 함께)
- 장중 30분 움직임: 1분봉이 있는 날마다 '30분 안에 가장 크게 움직인 폭'(3분 중앙값 가격, 감지와 같은 방식)의 분위수와
  ±3·5% 넘은 날 비율
- 지금 기록된 급등·급락 수 (종류 · 단계 · 네이버 1분봉 첫날 전/후)

python stats_moves.py <백업 파일>   (열쇠: GO_DATA_KEY, 없으면 GO_SITE_PASSWORD)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import numpy as np

from core.sitebuild import decrypt, derive_key, unpack_db

LISTING_SKIP = 5          # 상장 첫 거래일들(공모가 대비 큰 출렁임)은 뺌
THRESHOLDS = (0.03, 0.05, 0.07, 0.10)
QS = (50, 75, 90, 95, 99)


def pct(v):
    return f"{v * 100:.1f}%"


def daily_returns(conn, sym):
    rows = conn.execute("SELECT date, close, base FROM daily_bars WHERE symbol=? AND close > 0 ORDER BY date", (sym,)).fetchall()
    out, prev = [], None
    for d, c, b in rows:
        base = b or prev
        if base:
            out.append((d, c / base - 1))
        prev = c
    return out


def table(title, rets, what="하루 등락"):
    a = np.abs(np.array([r for _, r in rets]))
    if not len(a):
        return f"\n**{title}**: 자료 없음\n"
    qs = np.percentile(a, QS)
    lines = [f"\n**{title}** — {len(a)}거래일 ({rets[0][0]} ~ {rets[-1][0]})",
             f"- {what} 크기(오름·내림 구분 없이): 평균 {pct(a.mean())}, 표준편차 {pct(np.std([r for _, r in rets]))}",
             "- 분위수: " + ", ".join(f"{q}% {pct(v)}" for q, v in zip(QS, qs)) + "  (예: 95% 값 = 상위 5% 움직임이 시작되는 크기)",
             "- 이 크기 이상 움직인 날: " + ", ".join(f"±{t * 100:.0f}% {int((a >= t).sum())}일({(a >= t).mean() * 100:.0f}%)" for t in THRESHOLDS)]
    return "\n".join(lines) + "\n"


def intraday_max30(conn, sym):
    """날마다 30분 안 최대 움직임 크기 (3분 중앙값 가격)."""
    out = []
    days = conn.execute("SELECT substr(ts,1,10) d, COUNT(*) FROM minute_bars WHERE symbol=? GROUP BY d HAVING COUNT(*) >= 300 ORDER BY d",
                        (sym,)).fetchall()
    for d, _ in days:
        px = np.array([r[0] for r in conn.execute("SELECT close FROM minute_bars WHERE symbol=? AND substr(ts,1,10)=? ORDER BY ts",
                                                   (sym, d))], dtype=float)
        if len(px) < 40:
            continue
        med = np.array([np.median(px[max(0, i - 1):i + 2]) for i in range(len(px))])
        best = 0.0
        for i in range(30, len(med)):
            w = med[i - 30:i + 1]
            best = max(best, abs(w[-1] / w.min() - 1), abs(w[-1] / w.max() - 1))
        out.append((d, best))
    return out


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
            if not sym:
                sym = conn.execute("SELECT symbol FROM daily_bars WHERE symbol != 'KOSDAQ' GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
            own = daily_returns(conn, sym)[LISTING_SKIP:]
            kq = [x for x in daily_returns(conn, "KOSDAQ") if own and x[0] >= own[0][0]]
            print("## 하루 등락 (종가 ÷ 전날 기준가, 상장 첫 5거래일 뺌)")
            print(table("그린광학 · 전체 기간", own))
            print(table("그린광학 · 최근 120거래일", own[-120:]))
            print(table("KOSDAQ 지수 · 같은 기간 (비교용)", kq))
            m30 = intraday_max30(conn, sym)
            first = conn.execute("SELECT MIN(substr(ts,1,10)) FROM minute_bars WHERE symbol=? AND source LIKE 'naver%'", (sym,)).fetchone()[0]
            print("## 장중 30분 안 최대 움직임 (1분봉 있는 날, 3분 중앙값 가격)")
            print(table("그린광학 · 전체 기간 (하루마다 가장 큰 30분 움직임)", m30, "30분 움직임"))
            if first:
                print(table(f"그린광학 · {first} 뒤 (네이버 1분봉)", [x for x in m30 if x[0] >= first], "30분 움직임"))
            print("## 지금 기록된 급등·급락 수")
            rows = conn.execute("SELECT event_type, COALESCE(tier,'-'), CASE WHEN substr(start_ts,1,10) < ? THEN '전' ELSE '후' END, COUNT(*) "
                                "FROM events WHERE symbol=? GROUP BY 1, 2, 3 ORDER BY 1, 3, 2", (first or "9999", sym)).fetchall()
            print(f"| 종류 | 단계 | 네이버 1분봉 첫날({first}) 전/후 | 건수 |\n|---|---|---|---|")
            for t, tier, when, n in rows:
                print(f"| {'하루' if t == 'daily' else '장중'} | {tier} | {when} | {n} |")
            trim = conn.execute("SELECT value FROM system_state WHERE key='kis_rescan_trim_v1'").fetchone()
            print(f"\n지난 날 관찰 정리: {'실행됨 ' + trim[0] if trim else '실행 안 됨'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
