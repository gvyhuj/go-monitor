"""Build a synthetic encrypted static site for tests/news_page_check.cjs.

Only fixture rows are used (검증용 가상 시세); nothing here is production data and the output must never be deployed.
Usage: python tests/fixtures/news_page_site.py OUT_DIR
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["GO_PRIVATE_CONFIG"] = json.dumps({"symbol": "000001", "display_name": "검증 종목"})

from core import db, sitebuild, yahoo  # noqa: E402

PASSWORD = "public-site-password"
SALT = b"0123456789abcdef"
SYMBOL = "000001"
# (id, KST time, office, title, category, +30분 move). '회사 발표' = 그린광학 보도자료 탭
ARTICLES = [
    ("2001", "2026-10-22T10:05", "검증일보", "광학미러 공급 계약 체결", "회사 발표", -0.012),
    ("2002", "2026-10-20T09:40", "검증경제", "3분기 실적 설명회 개최", "회사 발표", 0.024),
    ("2003", "2026-10-15T13:10", "검증신문", "반도체 장비용 렌즈 수주", "회사 발표", 0.006),
    ("2004", "2026-10-21T11:20", "검증매체", "우주항공 부품주 동향", "언론 분석", 0.004),
]


def trading_days(start: date, end: date) -> list[str]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def main(out: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="go-news-"))
    db.DATA_DIR, db.DB_PATH = work, work / "monitor.db"
    db.init_db()
    moves = {a[1][:10]: a for a in ARTICLES}
    with db.connect() as conn:
        price, index = 10000.0, 800.0
        for i, day in enumerate(trading_days(date(2026, 9, 21), date(2026, 10, 28))):
            article = moves.get(day)
            if article:
                start = datetime.fromisoformat(day + "T09:00")
                published = datetime.fromisoformat(article[1])
                at = int((published - start).total_seconds() // 60)
                base = price
                for m in range(0, 391):
                    stamp = (start + timedelta(minutes=m)).isoformat(timespec="minutes")
                    own = base * (1 + (article[5] * min(max(m - at, 0), 60) / 30 if m > at else 0.0005 * ((m % 7) - 3) / 3))
                    kq = index * (1 + 0.00002 * m)
                    for symbol, value in ((SYMBOL, own), (yahoo.INDEX, kq)):
                        conn.execute("INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,1,'naver')",
                                     (symbol, stamp, value, value, value, value))
                price, index = own, kq
            else:
                price *= 1 + 0.004 * ((i % 5) - 2) / 2
                index *= 1 + 0.001 * ((i % 3) - 1)
            for symbol, value in ((SYMBOL, price), (yahoo.INDEX, index)):
                conn.execute("INSERT INTO daily_bars(symbol,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,1)",
                             (symbol, day, value, value, value, value))
        for aid, ts, office, title, category, _ in ARTICLES:
            conn.execute("INSERT INTO articles(id,symbol,ts,office,title,url,category,mention,story,first_seen) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (aid, SYMBOL, ts, office, title, f"https://example.org/{aid}", category, "제목", aid, ts))
        # 2003 기사보다 8분 먼저 접수된 같은 내용의 공시 → 기사 화면에 '선행 공시'
        conn.execute("INSERT INTO disclosures(no,symbol,date,ts,title,kind,pblntf,filer) VALUES (?,?,?,?,?,?,?,?)",
                     ("20261015000901", SYMBOL, "2026-10-15", "2026-10-15T13:02", "단일판매ㆍ공급계약체결", "공급계약", "I", "검증 종목"))
        # 같은 주제 기사(충북TP)가 예전 기록에 따로 남은 경우 → 유사 사건에는 한 줄만
        for aid, ts in (("2005", "2026-10-16T10:00"), ("2006", "2026-10-19T10:30")):
            conn.execute("INSERT INTO articles(id,symbol,ts,office,title,url,category,mention,story,first_seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (aid, SYMBOL, ts, "검증지역", "충북TP 우주항공 기업 지원사업 선정", f"https://example.org/{aid}", "언론 분석", "제목", aid, ts))
        # 네이버 테마 (이 종목이 속한 테마 2개, 10/27~10/28 기록)
        conn.executemany("INSERT INTO stock_themes(code, theme_no, theme_name, reason, updated_at) VALUES (?,?,?,?,?)",
                         [(SYMBOL, 7, "우주항공산업(누리호/인공위성 등)", "위성용 광학계 공급", "2026-10-20T09:00"),
                          (SYMBOL, 9, "방위산업/전쟁 및 테러", "적외선 광학계", "2026-10-20T09:00")])
        for day, a, b in (("2026-10-27", 1.25, -0.5), ("2026-10-28", -2.4, 0.8)):
            for no, name, rate in ((7, "우주항공산업(누리호/인공위성 등)", a), (9, "방위산업/전쟁 및 테러", b)):
                conn.execute("INSERT INTO theme_daily(date, theme_no, name, change_rate, rank, n_themes, rise, total, leaders, mine, updated_at) "
                             "VALUES (?,?,?,?,1,250,3,20,?,1,?)", (day, no, name, rate, json.dumps({
                                 "up": [{"name": "검증위성", "change": 0.031}], "down": [{"name": "검증방산", "change": -0.042}],
                                 "fall": 15, "me": {"chg": 0.6}}, ensure_ascii=False), day + "T15:30"))
        conn.execute("CREATE TABLE IF NOT EXISTS news_event_records (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        conn.execute("INSERT INTO news_event_records VALUES (?,?)", ("ne-0000000000a1", json.dumps({
            "event_id": "ne-0000000000a1", "title": "충북TP 우주항공 기업 지원사업 선정", "event_kind": "external_article",
            "business_tags": ["우주항공"], "publisher": "미확인", "partner": "", "first_public_at": "2026-10-09T10:00",
            "time_source": "naver", "time_status": "estimated", "followup_candidate": False,
            "observation": {"status": "unavailable", "intraday": None, "windows": {}}, "notes": [],
            "members": [{"id": "2099", "title": "충북TP 우주항공 기업 지원사업 선정", "kind": "ext"}]}, ensure_ascii=False)))
    # 실시간 중계 (주소·들어가는 값은 암호화된 상태 자료에만)
    db.set_state("relay:info", json.dumps({"url": "https://go-relay.test.workers.dev", "auth": "RELAY-AUTH", "ns": "ns", "fp": "x"}))
    items = sitebuild.refresh_items(datetime(2026, 10, 28, 18, 0))
    sitebuild.build(out, PASSWORD, SALT, items)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
