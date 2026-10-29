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


def test_same_publisher_portal_and_direct_links_count_as_one_outlet(tmpdb):
    with db.connect() as conn:
        _art(conn, "portal", "2026-09-08T19:00", f"{NAME}, 카메라 공동 개발")
        _art(conn, "direct", "2026-09-08T19:00", f"{NAME}, 카메라 공동 개발")
        _art(conn, "other", "2026-09-08T09:28", f"{NAME}, 카메라 공동 개발")
        conn.execute("UPDATE articles SET story='story', office='머니투데이'")
        conn.executemany("INSERT INTO article_links(a,b) VALUES(?,?)", [("portal","direct"),("portal","other")])
        conn.execute("UPDATE articles SET office='이데일리' WHERE id='other'")
    rows = articles.article_rows("X")
    assert len(rows) == 1
    assert rows[0]["outlets_total"] == 2
    assert len(rows[0]["related_titles"]) == 2  # Three article links, two publishers.


def test_pr_when_company_news_on_three_outlets_and_override(tmpdb):
    with db.connect() as conn:
        _art(conn, "a", "2026-09-08T08:00", f"{NAME}-SDT, SWIR 카메라 공동 개발 협력", outlets=2)
        _art(conn, "b", "2026-09-08T09:30", f"SDT·{NAME} 손잡았다…양자점 카메라 국산화 협력")
        _art(conn, "c", "2026-09-18T06:10", f"{NAME}, 외형 커졌지만 재고 부담도")
        _art(conn, "d", "2026-09-20T10:00", f"{NAME}, 유상증자 결정 소식")
    rows = {a["id"]: a for a in articles.article_rows("X")}
    assert rows["a"]["kind"] == "company" and rows["a"]["tone"] == 0  # 3개 매체여도 자체 배포 확정 불가
    assert rows["c"]["kind"] == "ext" and rows["c"]["tone"] == 0       # 뚜렷한 악재 단어 없으면 표시 안 함
    assert rows["d"]["tone"] == -1                                     # 유상증자 → 악재
    with db.connect() as conn:
        conn.execute("UPDATE articles SET kind_override='pr' WHERE id='c'")
    assert {a["id"]: a for a in articles.article_rows("X")}["c"]["kind"] == "pr"


def test_group_events_keep_independent_same_day_events():
    def it(aid, ts, kind, day, cat="회사 발표", out=1):
        return {"id": aid, "ts": ts, "kind": kind, "category": cat, "outlets_total": out, "effect": {"reaction_day": day}}
    ev = articles.group_events([it("a", "2026-05-26T13:37", "ext", "2026-05-26"),
                                it("b", "2026-05-26T13:47", "pr", "2026-05-26", out=3),
                                it("c", "2026-05-26T14:00", "ext", "2026-05-26", cat="단순 언급"),
                                it("d", "2026-06-01T09:00", "ext", "2026-06-01", cat="시황·특징주")])
    first = next(e for e in ev if e["lead"] == "b")
    assert first["kind"] == "pr" and first["outlets"] == 3 and len(first["ids"]) == 1
    assert len([e for e in ev if e["day"] == "2026-05-26"]) == 3
    assert next(e for e in ev if e["day"] == "2026-06-01")["noise"]


def test_off_session_keeps_statistical_verdict_without_arbitrary_downgrade():
    eff = {"event_study": {"ok": True, "ar": 0.05, "actual": 0.06, "expected": 0.01,
                           "ci_low": 0.02, "ci_high": 0.08, "p": 0.01, "n_est": 120}}
    assert articles._verdict(eff, 0, "회사 발표")[1] == "up"
    assert articles._verdict({**eff, "off_session": True}, 0, "회사 발표")[1] == "up"


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


def test_late_article_does_not_mix_two_days_into_daily_summary():
    dc = {"d0": {"abnormal": 0.005, "z": 0.5, "own": 0.01, "market": 0.0, "sector": 0.0},
          "d01": {"abnormal": 0.06, "z": 4.0, "own": 0.07, "market": 0.0, "sector": 0.0}}
    study = {"ok": True, "ar": 0.005, "actual": 0.01, "expected": 0.005,
             "ci_low": -0.02, "ci_high": 0.03, "p": 0.7, "n_est": 120}
    v = articles._verdict({"decomp": dc, "event_study": study, "late": True}, 0, "회사 발표")
    assert v[1] == "flat" and v[2]["value"] == 0.005 and v[2]["basis"] == "반응일"


def test_verdict_uses_model_uncertainty_not_legacy_rarity():
    def v(x, rr, basis="하루"):
        part = {"abnormal": x, "z": 9.0, "rarity": rr, "own": x, "market": 0.0, "sector": 0.0}
        eff = {"decomp": {"d0": part}, "event_study": {"ok": True, "ar": x, "actual": x, "expected": 0.0,
               "ci_low": x - 0.02, "ci_high": x + 0.02, "p": 0.01, "n_est": 120}}
        if basis == "60분":
            eff["minute60"] = {**part, "abnormal": x}
        return articles._verdict(eff, 0, "회사 발표")
    assert v(0.03, 0.03)[1] == "up"
    assert v(0.03, 0.10)[1] == "up"
    assert v(0.03, 0.30)[1] == "up"
    m = v(0.012, 0.02, "60분")
    assert m[1] == "flat" and m[2]["basis"] == "반응일"


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


def test_manual_duplicate_of_crawled_article_removed_once(tmpdb):
    with db.connect() as conn:
        conn.execute("INSERT INTO pr_events(title, published_ts, url, pr_type) VALUES (?,?,?,?)",
                     (f"{NAME}, 인도 위성군에 광학미러 공급", "2026-10-06T09:10", "", "직접 등록"))
        conn.execute("INSERT INTO pr_events(title, published_ts, url, pr_type) VALUES (?,?,?,?)",
                     (f"{NAME}, 아직 기사 안 난 보도자료", "2026-10-07T09:00", "", "직접 등록"))
        _art(conn, "n1", "2026-10-06T09:09", f"{NAME}, 인도 위성군에 광학미러 공급")
    assert articles.drop_manual_duplicates("X") == 1
    with db.connect() as conn:
        left = [r[0] for r in conn.execute("SELECT title FROM pr_events")]
    assert left == [f"{NAME}, 아직 기사 안 난 보도자료"]
    rows = articles.article_rows("X")
    assert not any(a.get("manual") and "광학미러" in a["title"] for a in rows)
    assert articles.drop_manual_duplicates("X") == 0          # 한 번만


def test_reported_time_quality_and_followup_flags():
    from core.article_integrity import time_quality, likely_followup
    assert time_quality({"ts": "2026-06-19T16:00", "source": "google"}) == "check_time"
    assert time_quality({"ts": "2026-06-19T16:00", "source": "naver"}) == "reported"
    assert time_quality({"ts": "2026-06-19T10:00", "source": "google"}) == "reported"
    assert time_quality({"ts": "2026-06-19T16:00", "source": "google", "manual": True}) == "manually_entered"
    a = {"ts": "2026-02-25T10:38", "title": "그린광학, 美 우주군 프로젝트 참여"}
    b = {"ts": "2026-02-25T17:00", "title": "그린광학, 미 우주군 프로젝트 국내 유일 선정"}
    # Same publication date can still produce DIFFERENT event-study dates.
    assert likely_followup(a, b)
    b["ts"] = "2026-03-20T17:00"
    assert not likely_followup(a, b)


def test_kind_pr_is_confirmed_only_and_same_story_followup_is_not_another_independent_event(tmpdb):
    with db.connect() as c:
        _art(c, "a", "2026-06-19T10:00", f"{NAME}, 위성용 광학부품 계약", 5)
    rows = articles.article_rows("X")
    assert rows[0]["kind"] == "company"
    def item(identity, published, reaction):
        return {"id": identity, "ts": published, "story": "same",
                "kind": "company", "time_quality": "reported", "category": "회사 발표",
                "title": f"{NAME}, 위성용 광학부품 계약",
                "effect": {"reaction_day": reaction}}
    events = articles.group_events([item("a", "2026-06-19T10:00", "2026-06-19"),
                                   item("b", "2026-06-19T16:00", "2026-06-22")])
    later = next(e for e in events if e["day"] == "2026-06-22")
    assert later["followup_of"] == "a"


def test_company_report_only_when_company_is_subject():
    # 다른 기관이 주어인 기사('…그린광학 방문')는 회사 발표가 아니라 외부 기사(언론 분석)
    from core import articles as a
    assert a.classify("충북지역인적자원개발위원회, 직업계고 학부모 우수 중소기업 탐방…그린광학 방문", "", name="그린광학")[0] == "언론 분석"
    assert a.classify("그린광학, 인도 GalaxEye 위성군에 광학미러 공급", "", name="그린광학")[0] == "회사 발표"
    assert a.classify("그린광학 신제품 출시", "", name="그린광학")[0] == "회사 발표"


def test_false_negative_words_are_not_bad_news():
    # '인적자원'의 '적자'는 악재가 아님, 진짜 '영업적자'는 악재
    from core import articles as a
    t = "충북지역인적자원개발위원회, 직업계고 학부모 우수 중소기업 탐방…그린광학 방문"
    assert not any(w in a.neg_text(t) for w in a.STRONG_NEG) and a.tone_of(t) >= 0
    assert any(w in a.neg_text("그린광학, 3분기 영업적자 확대") for w in a.STRONG_NEG)


def test_lead_pick_changes_title_and_office_but_keeps_first_time(tmpdb):
    import logging
    import cloud_job
    with db.connect() as conn:
        _art(conn, "a", "2026-04-22T09:10", f"{NAME}, 82억 공급계약 체결")
        _art(conn, "b", "2026-04-22T14:14", f"{NAME}, 소형무장헬기 유도탄 공급계약→매출 확대 기대")
        conn.execute("UPDATE articles SET story='s1', office=CASE id WHEN 'a' THEN '뉴스핌' ELSE '톱스타뉴스' END")
    with db.connect() as conn:
        conn.execute("INSERT INTO article_links(a,b) VALUES('a','b')")
    auto = articles.article_rows("X")
    assert [a["id"] for a in auto] == ["a"] and not auto[0].get("picked")
    cloud_job.apply_inbox({"type": "article_lead", "id": "b", "ids": ["a", "b"]}, logging.getLogger())
    lead = articles.article_rows("X")[0]
    assert (lead["id"], lead["office"], lead["picked"]) == ("b", "톱스타뉴스", True)
    assert lead["ts"] == "2026-04-22T09:10" and lead["own_ts"] == "2026-04-22T14:14"   # 반응 기준 시각은 처음 나온 때
    assert [r["id"] for r in lead["related_titles"]] == ["a"]
    cloud_job.apply_inbox({"type": "article_lead", "id": None, "ids": ["a", "b"]}, logging.getLogger())  # 자동으로
    assert articles.article_rows("X")[0]["id"] == "a"


def test_time_fix_overrides_and_restores(tmpdb):
    import logging
    import cloud_job
    with db.connect() as conn:
        _art(conn, "g", "2026-04-22T16:00", f"{NAME}, 82억 공급계약 체결")
        conn.execute("UPDATE articles SET source='google' WHERE id='g'")
    a = articles.article_rows("X")[0]
    assert a["time_source"] == "구글 뉴스" and a["time_quality"] == "check_time"
    cloud_job.apply_inbox({"type": "article_time", "id": "g", "ts": "2026-04-22T14:14"}, logging.getLogger())
    a = articles.article_rows("X")[0]
    assert (a["ts"], a["time_source"], a["time_quality"], a["ts_auto"]) == ("2026-04-22T14:14", "직접 고침", "manually_entered", "2026-04-22T16:00")
    cloud_job.apply_inbox({"type": "article_time", "id": "g", "ts": "2026-04-22T14:20"}, logging.getLogger())   # 다시 고쳐도 원래 시각은 처음 것
    assert articles.article_rows("X")[0]["ts_auto"] == "2026-04-22T16:00"
    cloud_job.apply_inbox({"type": "article_time", "id": "g", "ts": None}, logging.getLogger())
    a = articles.article_rows("X")[0]
    assert (a["ts"], a["time_source"], a["ts_auto"]) == ("2026-04-22T16:00", "구글 뉴스", None)
