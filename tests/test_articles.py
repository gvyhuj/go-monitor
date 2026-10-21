"""기사 영향 분석: 분류(회사 이름·소음), 우리 보도자료 판정, 같은 반응일 묶기, 장 외 판정, 공시 먼저."""
from __future__ import annotations

from datetime import datetime

import pytest

from core import articles, db

NAME = "가나광학"


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(articles, "company_name", lambda: NAME)
    db.init_db()


def test_classify_uses_company_name_and_drops_noise():
    assert articles.classify(f"{NAME}, 42억 규모 공급계약", "", NAME)[0] == "회사 발표"
    assert articles.classify(f"{NAME} 투자분석 2026. 09. 12", "", NAME)[0] == "단순 언급"
    assert articles.classify(f"{NAME} 실적 분석", "", NAME, office="주달")[0] == "단순 언급"
    assert articles.classify("2026년10월20일(화) S브리핑", "", NAME)[0] == "단순 언급"


def _art(conn, aid, ts, title, outlets=1, category=None):
    cat = category or articles.classify(title, "", NAME)[0]
    conn.execute("INSERT INTO articles(id, symbol, ts, office, title, body, url, outlets, category, tone, mention) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (aid, "X", ts, "매체", title, "", "", outlets, cat, 0, "제목"))


def test_pr_when_company_news_on_three_outlets_and_override(tmpdb):
    with db.connect() as conn:
        _art(conn, "a", "2026-09-08T08:00", f"{NAME}-SDT, SWIR 카메라 공동 개발 협력", outlets=2)
        _art(conn, "b", "2026-09-08T09:30", f"SDT·{NAME} 손잡았다…양자점 카메라 국산화 협력")
        _art(conn, "c", "2026-09-18T06:10", f"{NAME}, 외형 커졌지만 재고 부담도")
        _art(conn, "d", "2026-09-20T10:00", f"{NAME}, 유상증자 결정 소식")
    rows = {a["id"]: a for a in articles.article_rows("X")}
    assert rows["a"]["kind"] == "pr" and rows["a"]["tone"] == 0        # 보도자료는 호재·악재 표시 안 함
    assert rows["c"]["kind"] == "ext" and rows["c"]["tone"] == 0       # 뚜렷한 악재 단어 없으면 표시 안 함
    assert rows["d"]["tone"] == -1                                     # 유상증자 → 악재
    with db.connect() as conn:
        conn.execute("UPDATE articles SET kind_override='pr' WHERE id='c'")
    assert {a["id"]: a for a in articles.article_rows("X")}["c"]["kind"] == "pr"


def test_group_events_by_reaction_day():
    def it(aid, ts, kind, day, cat="회사 발표", out=1):
        return {"id": aid, "ts": ts, "kind": kind, "category": cat, "outlets_total": out, "effect": {"reaction_day": day}}
    ev = articles.group_events([it("a", "2026-05-26T13:37", "ext", "2026-05-26"),
                                it("b", "2026-05-26T13:47", "pr", "2026-05-26", out=3),
                                it("c", "2026-05-26T14:00", "ext", "2026-05-26", cat="단순 언급"),
                                it("d", "2026-06-01T09:00", "ext", "2026-06-01", cat="시황·특징주")])
    first = next(e for e in ev if e["day"] == "2026-05-26")
    assert first["kind"] == "pr" and first["lead"] == "b" and first["outlets"] == 4 and len(first["ids"]) == 3
    assert next(e for e in ev if e["day"] == "2026-06-01")["noise"]


def test_off_session_lowers_verdict_one_step():
    eff = {"daily": {"excess": {"d0": 0.05}, "bench_name": "KOSDAQ", "d0": 0.05},
           "decomp": {"d0": {"abnormal": 0.05, "z": 3.0, "own": 0.06, "market": 0.01, "sector": 0.0}}}
    assert articles._verdict(eff, 0, "회사 발표")[1] == "up"
    assert articles._verdict({**eff, "off_session": True}, 0, "회사 발표")[1] == "up-weak"


def test_earlier_disclosure_moves_base_time(tmpdb):
    with db.connect() as conn:
        conn.execute("INSERT INTO disclosures(no, symbol, date, title, kind, ts) VALUES (?,?,?,?,?,?)",
                     ("1", "X", "2026-08-06", f"(주){NAME} 단일판매ㆍ공급계약체결", "단일판매/공급계약", "2026-08-06T11:11"))
    t = datetime(2026, 8, 6, 14, 32)
    assert articles.earlier_disclosure("X", f"{NAME}, 42억 규모 방산 센서부 공급계약", t)["ts"] == "2026-08-06T11:11"
    assert articles.earlier_disclosure("X", f"{NAME}, 전시회 참가", t) is None


def _series(code, rets, start="2026-01-01"):
    from datetime import date, timedelta
    d, px = date.fromisoformat(start), 10000.0
    with db.connect() as conn:
        for r in [0.0] + list(rets):
            while d.weekday() >= 5:
                d += timedelta(days=1)
            px *= 1 + r
            conn.execute("INSERT OR REPLACE INTO daily_bars(symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
                         (code, d.isoformat(), px, px, px, px, 1000))
            d += timedelta(days=1)
    return d


def test_day_split_uses_related_segment_not_noise_and_no_alpha(tmpdb):
    import numpy as np
    from core import causal
    rng = np.random.default_rng(1)
    n = 150
    mkt = rng.normal(0, 0.01, n)
    seg_a = 0.8 * mkt + rng.normal(0, 0.01, n)          # 이 종목과 같이 움직이는 분야
    seg_b = rng.normal(0, 0.01, n)                       # 상관없는 분야
    own = 0.004 + 1.2 * mkt + 1.0 * (seg_a - 0.8 * mkt) + rng.normal(0, 0.004, n)   # 매일 +0.4% 추세
    own[-1] += 0.05                                      # 마지막 날: 기사 효과 +5%
    for code, r in (("X", own), ("KOSDAQ", mkt), ("A1", seg_a), ("B1", seg_b)):
        _series(code, r)
    with db.connect() as conn:
        conn.executemany("INSERT INTO related_stocks(code, name, market, source) VALUES (?,?,?,?)",
                         [("A1", "a", "", "분야A"), ("B1", "b", "", "분야B")])
        last = conn.execute("SELECT MAX(date) FROM daily_bars WHERE symbol='X'").fetchone()[0]
    sp = causal.day_split("X", last)
    beta = {p["name"]: p["beta"] for p in sp["parts"]}
    # 릿지로 조금 줄어들지만(1.0 → 0.7~1.0) 상관없는 분야는 0 근처
    assert sp["method"] == "분야별 회귀" and 0.6 < beta["분야A"] <= 1.05 and abs(beta["분야B"]) < 0.15
    assert sp["d0"]["rarity"] is not None and sp["d0"]["rarity"] < 0.05   # 평소 기록 중 가장 큰 축
    # 추세(+0.4%)를 빼지 않으므로 고유 몫 ≈ 5% + 0.4%
    assert 0.045 < sp["d0"]["abnormal"] < 0.06 and sp["d0"]["z"] > 5


def test_late_article_judged_on_two_days():
    dc = {"d0": {"abnormal": 0.005, "z": 0.5, "own": 0.01, "market": 0.0, "sector": 0.0},
          "d01": {"abnormal": 0.06, "z": 4.0, "own": 0.07, "market": 0.0, "sector": 0.0}}
    assert articles._verdict({"decomp": dc}, 0, "회사 발표")[1] == "flat"
    v = articles._verdict({"decomp": dc, "late": True}, 0, "회사 발표")
    assert v[1] == "up" and v[2]["label"] == "반응일+다음날 고유 반응"


def test_verdict_uses_empirical_rarity_and_60min_thresholds():
    def v(x, rr, basis="하루"):
        part = {"abnormal": x, "z": 9.0, "rarity": rr, "own": x, "market": 0.0, "sector": 0.0}
        eff = {"decomp": {"d0": part}}
        if basis == "60분":
            eff["minute60"] = {**part, "abnormal": x}
        return articles._verdict(eff, 0, "회사 발표")
    assert v(0.03, 0.03)[1] == "up"
    assert v(0.03, 0.10)[1] == "up-weak"
    assert v(0.03, 0.30)[1] == "flat"           # z가 커도 평소 기록상 흔하면 효과 없음
    m = v(0.012, 0.02, "60분")
    assert m[1] == "up" and m[2]["basis"] == "60분"


def test_abnormal_rarity_market_adjusted_and_premarket(tmpdb):
    """평소 같은 구간도 시장 몫을 빼고 비교: 시장과 같이 크게 움직인 날은 '드문 고유 움직임'이 아님."""
    from datetime import date, datetime, timedelta
    import numpy as np
    from core import causal
    rng = np.random.default_rng(3)
    d = date(2026, 9, 1)
    px, kq = 10000.0, 800.0
    with db.connect() as conn:
        for _ in range(40):
            while d.weekday() >= 5:
                d += timedelta(days=1)
            for code, base in (("X", px), ("KOSDAQ", kq)):
                conn.execute("INSERT INTO daily_bars(symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
                             (code, d.isoformat(), base, base, base, base, 1000))
            mk = rng.normal(0, 0.01)                       # 시장이 크게 움직이고 이 종목은 1.5배로 따라감
            for i in range(0, 391, 10):
                t = datetime.combine(d, datetime.min.time()) + timedelta(hours=9, minutes=i)
                f = (i / 390)
                conn.execute("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume, source) VALUES (?,?,?,?,?,?,?,?)",
                             ("KOSDAQ", t.isoformat(timespec="minutes"), 0, 0, 0, kq * (1 + mk * f), 0, "t"))
                conn.execute("INSERT INTO minute_bars(symbol, ts, open, high, low, close, volume, source) VALUES (?,?,?,?,?,?,?,?)",
                             ("X", t.isoformat(timespec="minutes"), 0, 0, 0, px * (1 + 1.5 * mk * f + rng.normal(0, 0.0005)), 0, "t"))
            d += timedelta(days=1)
    day = d + timedelta(days=(7 - d.weekday()) % 7 if d.weekday() >= 5 else 0)
    from datetime import time as _t
    big = causal.abnormal_rarity("X", day, 0.03, 1.5, minutes=60)
    small = causal.abnormal_rarity("X", day, 0.0005, 1.5, minutes=60)
    assert big["ok"] and big["p"] < 0.02 and small["p"] > 0.3
    pre = causal.abnormal_rarity("X", day, 0.03, 1.5, minutes=None, until=_t(10, 0))
    assert pre["ok"] and pre["p"] < 0.1


def test_day_split_winsorized_betas_ignore_one_huge_day(tmpdb):
    import numpy as np
    from core import causal
    rng = np.random.default_rng(5)
    n = 150
    mkt = rng.normal(0, 0.01, n)
    own = 1.0 * mkt + rng.normal(0, 0.005, n)
    own[60] = 0.30; mkt[60] = 0.05                    # 추정 기간 안의 거대한 하루 (상한가 + 시장 급등)
    for code, r in (("X", own), ("KOSDAQ", mkt)):
        _series(code, r)
    with db.connect() as conn:
        last = conn.execute("SELECT MAX(date) FROM daily_bars WHERE symbol='X'").fetchone()[0]
    sp = causal.day_split("X", last)
    assert abs(sp["beta_market"] - 1.0) < 0.25          # 거대한 하루에 끌려가지 않음 (안 누르면 약 2.1)
