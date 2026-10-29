from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pytest

from core.eventstudy import analyze_returns, returns_from_prices


def sample(seed=7, size=180):
    random = np.random.default_rng(seed)
    dates = []
    day = date(2025, 1, 1)
    while len(dates) < size:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    market = random.normal(0, 0.01, size)
    sector = 0.6 * market + random.normal(0, 0.008, size)
    own = 0.001 + 1.2 * market + 0.8 * sector + random.normal(0, 0.003, size)
    return dates, dict(zip(dates, own)), dict(zip(dates, market)), dict(zip(dates, sector))


def test_new_information_shock_and_sector_adjustment():
    dates, own, market, sector = sample()
    day = dates[160]
    baseline = analyze_returns(own, market, day, sector)
    own[day] += 0.08
    result = analyze_returns(own, market, day, sector)
    assert result["ok"] and result["beta_sector"] == pytest.approx(0.8, abs=0.15)
    assert result["ar"] - baseline["ar"] == pytest.approx(0.08)
    assert result["p"] < 0.001 and result["ci_low"] > 0
    json.dumps(result, allow_nan=False)


def test_summary_uses_exact_student_t_for_sample_size():
    from scipy.stats import t as student_t
    from core.eventstudy import summary_critical_values

    values = summary_critical_values(23)
    assert values[:2] == [None, None]
    assert values[3] == pytest.approx(student_t.ppf(0.975, 2))
    assert values[21] == pytest.approx(student_t.ppf(0.975, 20))


def test_export_guard_rejects_legacy_values_and_inconsistent_list():
    from core.articles import _verdict
    from core.eventstudy import validate_primary_exports

    study = {"ok": True, "actual": -0.05, "expected": -0.02, "ar": -0.03,
             "p": 0.01, "ci_low": -0.05, "ci_high": -0.01, "n_est": 120}
    verdict, _, measure = _verdict({"event_study": study}, 0, "공시")
    effect = {"event_study": study, "measure": measure, "verdict": verdict}
    listing = {"items": [{"id": "one", "effect": {"measure": measure, "verdict": verdict}}]}
    details = {"one": {"effect": effect}}

    def exports():
        return [("articles", json.dumps(listing), False), ("article_details", json.dumps(details), False),
                ("disclosures", '{"items": []}', False), ("disclosure_details", "{}", False)]

    assert validate_primary_exports(exports())["articles"]["calculated"] == 1
    listing["items"][0]["effect"]["measure"] = {"value": 0.0041}
    with pytest.raises(ValueError):
        validate_primary_exports(exports())
    effect["measure"] = listing["items"][0]["effect"]["measure"]
    with pytest.raises(ValueError):
        validate_primary_exports(exports())


def test_estimation_never_uses_event_or_later_data():
    dates, own, market, sector = sample()
    day = dates[160]
    baseline = analyze_returns(own, market, day, sector)
    for future in dates[160:]:
        own[future] += 0.2
    result = analyze_returns(own, market, day, sector)
    for key in ("alpha", "beta", "beta_sector", "sigma", "est_from", "est_to"):
        assert result[key] == baseline[key]
    assert result["est_to"] == dates[149]


def test_missing_future_does_not_shorten_window():
    dates, own, market, _ = sample()
    result = analyze_returns(own, market, dates[-1])
    assert "후속 3일 [+1,+3]" in result["pending_windows"]
    assert not any(item["window"] == "후속 3일 [+1,+3]" for item in result["car"])
    del own[dates[161]]
    result = analyze_returns(own, market, dates[160])
    assert "이후 3일 [0,+2]" in result["pending_windows"]


def test_prediction_uncertainty_includes_coefficient_estimation():
    dates, own, market, _ = sample()
    day = dates[160]
    market[day] = 0.3
    result = analyze_returns(own, market, day)
    assert result["se"] > result["sigma"] * 2
    assert result["ci_low"] < result["ar"] < result["ci_high"]
    cumulative = next(item for item in result["car"] if item["window"] == "이후 3일 [0,+2]")
    assert cumulative["se"] > result["sigma"] * np.sqrt(3)


def test_collinear_or_missing_sector_falls_back_to_market():
    dates, own, market, _ = sample()
    duplicate = {day: 2 * value for day, value in market.items()}
    result = analyze_returns(own, market, dates[160], duplicate)
    assert result["ok"] and result["beta_sector"] is None
    result = analyze_returns(own, market, dates[160], {dates[0]: 0.01})
    assert result["ok"] and result["beta_sector"] is None


def test_unavailable_data_and_degenerate_model_are_explicit():
    dates, own, market, _ = sample()
    assert not analyze_returns(own, market, dates[20])["ok"]
    assert not analyze_returns(market, market, dates[160])["ok"]
    assert not analyze_returns(own, dict.fromkeys(dates, 0.0), dates[160])["ok"]
    assert analyze_returns({}, market, dates[160]) is None
    with pytest.raises(ValueError):
        analyze_returns(own, market, dates[160], gap=0)


def test_missing_trading_day_does_not_become_a_one_day_return():
    prices = {"2026-10-19": 100.0, "2026-10-21": 120.0, "2026-10-22": 126.0}
    result = returns_from_prices(prices, ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"])
    assert list(result) == ["2026-10-22"]
    assert result["2026-10-22"] == pytest.approx(0.05)
    dates, own, market, _ = sample()
    del market[dates[161]]
    result = analyze_returns(own, market, dates[160], calendar=dates)
    assert "이후 3일 [0,+2]" in result["pending_windows"]


def test_null_simulation_is_not_systematically_overconfident():
    random = np.random.default_rng(42)
    dates, _, _, _ = sample(size=150)
    flagged = 0
    for _ in range(400):
        market = random.normal(0, 0.01, len(dates))
        own = 0.001 + 1.2 * market + random.normal(0, 0.01, len(dates))
        result = analyze_returns(dict(zip(dates, own)), dict(zip(dates, market)), dates[140])
        flagged += result["p"] < 0.05
    assert 0.02 <= flagged / 400 <= 0.08


def test_read_only_validation_excludes_news_and_preserves_database(tmp_path):
    import sqlite3

    from validate_event_study import validate

    dates, own, market, _ = sample()
    database = tmp_path / "monitor.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("CREATE TABLE daily_bars(symbol, date, close); CREATE TABLE articles(symbol, ts);"
                                 "CREATE TABLE disclosures(symbol, ts, date); CREATE TABLE pr_events(published_ts);")
        for code, returns in (("X", own), ("KOSDAQ", market)):
            price = 100.0
            for day in dates:
                price *= 1 + returns[day]
                connection.execute("INSERT INTO daily_bars VALUES (?,?,?)", (code, day, price))
        connection.execute("INSERT INTO articles VALUES (?,?)", ("X", dates[160] + "T16:00"))
    original = database.read_bytes()
    report = validate(database, "X", dates[140], dates[-1])
    assert report["evaluated_days"] > 0 and report["event_records"] == 1
    assert report["excluded_days"] == 11
    assert report["coverage"]["event_model_available_days"] == 1
    assert len(report["event_results"]) == 1
    evaluated = {item["date"] for item in report["results"]}
    assert not evaluated.intersection(dates[156:167])
    assert database.read_bytes() == original
    assert all(item["est_to"] < item["date"] for item in report["results"])


def test_daily_study_reaches_article_detail_with_peer_data(tmp_path, monkeypatch):
    from datetime import datetime

    from core import articles, db
    monkeypatch.setattr(articles, "EVENT_STUDY", True)      # 꺼 둔 계산을 켰을 때도 맞게 도는지

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "monitor.db")
    db.init_db()
    dates, own, market, sector = sample()
    with db.connect() as connection:
        for code, returns in (("X", own), ("KOSDAQ", market), ("PEER", sector)):
            price = 100.0
            for day in dates:
                price *= 1 + returns[day]
                connection.execute("INSERT INTO daily_bars(symbol,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                                   (code, day, price, price, price, price, 1000))
        connection.execute("INSERT INTO related_stocks(code,name,source) VALUES (?,?,?)", ("PEER", "비교", "업종"))
    result = articles.effect("X", datetime.fromisoformat(dates[160] + "T11:00"))
    study = result["event_study"]
    assert study["ok"] and study["beta_sector"] is not None
    assert len(study["car"]) == 5 and study["est_to"] < dates[160]
    assert result["measure"]["value"] == study["ar"]
    assert result["measure"]["method"] == "event-study-v2"
    assert result["verdict"] is None
    json.dumps(result, allow_nan=False)
    import webserver
    monkeypatch.setattr(webserver, "load_settings", lambda: {"symbol": "X"})
    article = {"id": "integration", "ts": dates[160] + "T11:00", "tone": 0, "category": "회사 발표",
               "title": "새 계산식 연결 검사", "kind": "pr", "office": "검사"}
    monkeypatch.setattr(articles, "article_rows", lambda *args, **kwargs: [article])
    webserver._EFF_CACHE.clear()
    listing = webserver.api_articles({})
    detailed = webserver.api_article_detail({}, "integration")
    assert listing["items"][0]["effect"]["measure"] == detailed["effect"]["measure"]
    assert detailed["effect"]["measure"]["value"] == detailed["effect"]["event_study"]["ar"]
    monkeypatch.setattr(articles, "disclosure_rows", lambda symbol: [
        {"id": "dK1", "ts": dates[160] + "T11:00", "untimed": False, "title": "공시 연결 검사", "pblntf": "K"}])
    disclosure_list = webserver.api_disclosures({})
    assert disclosure_list["items"][0]["effect"]["measure"]["value"] == study["ar"]
    with db.connect() as connection:
        connection.execute("DELETE FROM daily_bars WHERE symbol='X' AND date=?", (dates[160],))
    missing = articles.effect("X", datetime.fromisoformat(dates[160] + "T11:00"))
    assert missing["reaction_day"] == dates[160] and missing["measure"] is None
    from core import feed
    monkeypatch.setattr(feed, "kst_now", lambda: datetime.fromisoformat(dates[160] + "T12:00"))
    pending = articles.effect("X", datetime.fromisoformat(dates[160] + "T11:00"))
    assert pending["event_study"]["pending"] and pending["measure"] is None


def test_primary_verdict_uses_new_model_not_legacy_or_intraday():
    from core.articles import _verdict

    study = {"ok": True, "actual": -0.05, "expected": -0.02, "ar": -0.03,
             "p": 0.01, "ci_low": -0.05, "ci_high": -0.01, "n_est": 120}
    effect = {"event_study": study, "decomp": {"d0": {"abnormal": 0.0041}},
              "minute60": {"abnormal": 0.08}, "late": True, "off_session": True}
    verdict, classification, measure = _verdict(effect, 1, "공시")
    assert classification == "down" and verdict is None
    assert measure["value"] == -0.03 and measure["own"] == -0.05
    assert measure["expected"] == -0.02 and measure["ci_high"] == -0.01
    assert measure["basis"] == "반응일"


@pytest.mark.parametrize("study", [None, {"ok": False, "reason": "평소 기록 부족"}])
def test_unavailable_new_model_never_falls_back_to_old_value(study):
    from core.articles import _verdict

    verdict, classification, measure = _verdict(
        {"event_study": study, "decomp": {"d0": {"abnormal": 0.0041}}}, 0, "회사 발표")
    assert classification == "none" and measure is None
    if study is None:                      # 계산을 꺼 둠(EVENT_STUDY=False) → 표시 없음
        assert verdict is None
    else:
        assert "부족" in verdict


def test_non_significance_does_not_claim_market_caused_the_drop():
    from core.articles import _verdict

    study = {"ok": True, "actual": -0.1, "expected": -0.1041, "ar": 0.0041,
             "p": 0.94, "ci_low": -0.08, "ci_high": 0.09, "n_est": 120}
    verdict, classification, measure = _verdict({"event_study": study}, 0, "공시")
    assert classification == "flat" and verdict is None
    assert measure["own"] < 0 < measure["value"]


def test_encrypted_backup_validation_keeps_details_encrypted(tmp_path, monkeypatch):
    from core import db, sitebuild
    from validate_backup import inspect_backup

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "source.db")
    db.init_db()
    dates, _, _, _ = sample(size=70)
    with db.connect() as connection:
        connection.execute("INSERT INTO system_state(key,value) VALUES (?,?)",
                           ("private_config", json.dumps({"symbol": "PRIVATE_CODE"})))
        connection.executemany("INSERT INTO daily_bars(symbol,date,close) VALUES ('KOSDAQ',?,100)",
                               [(day,) for day in dates])
    salt, password = b"0123456789abcdef", "test-only-password"
    encrypted = salt + sitebuild.encrypt(sitebuild.derive_key(password, salt), db.DB_PATH.read_bytes())
    backup = tmp_path / "monitor.db.enc"
    backup.write_bytes(encrypted)
    output = tmp_path / "result"
    summary = inspect_backup(backup, password, output)
    assert not summary["validation_available"] and not summary["accuracy_improvement_proven"]
    assert summary["source_unchanged"] and backup.read_bytes() == encrypted
    assert "PRIVATE_CODE" not in (output / "summary.json").read_text(encoding="utf-8")
    report = (output / "validation-report.enc").read_bytes()
    decoded = json.loads(sitebuild.decrypt(sitebuild.derive_key(password, report[:16]), report[16:]))
    assert decoded["symbol"] == "PRIVATE_CODE"
    assert not list(output.glob("*.db"))
    with pytest.raises(Exception):
        inspect_backup(backup, "wrong-password", tmp_path / "failed")
    assert not (tmp_path / "failed").exists()


def test_computed_effect_has_numbers_but_no_verdict_text():
    from core.articles import _verdict

    study = {"ok": True, "actual": 0.0819, "expected": 0.0456, "ar": 0.0362,
             "p": 0.5, "ci_low": -0.0712, "ci_high": 0.1436, "n_est": 120}
    verdict, classification, measure = _verdict({"event_study": study}, 0, "회사 발표")
    assert verdict is None and classification == "flat"
    assert (measure["own"], measure["expected"], measure["value"]) == (0.0819, 0.0456, 0.0362)


def test_news_days_are_skipped_and_backfilled_in_estimation():
    dates, own, market, sector = sample(size=260)
    day = dates[240]
    plain = analyze_returns(own, market, day, sector)
    news = set(dates[150:200:5])
    shocked = dict(own)
    for d in news:
        shocked[d] += 0.15            # 기사 날 큰 움직임이 평소 변동을 부풀림
    inflated = analyze_returns(shocked, market, day, sector)
    cleaned = analyze_returns(shocked, market, day, sector, exclude=news)
    assert cleaned["n_est"] == plain["n_est"] == 120
    assert cleaned["est_from"] < plain["est_from"]        # 뺀 날만큼 더 앞에서 채움
    assert cleaned["sigma"] == pytest.approx(plain["sigma"], rel=0.25) and inflated["sigma"] > 2 * cleaned["sigma"]
    assert cleaned["excluded_news_days"] == len(news)


def test_baseline_compares_news_days_with_quiet_days():
    from core.eventstudy import baseline_by_group

    dates, own, market, sector = sample(size=300)
    news = set(dates[20:300:10])
    for d in news:
        own[d] += 0.03
    quiet = set(dates) - news
    b = baseline_by_group(own, market, dates, {"pr": news, "none": quiet}, "none", sector)
    assert b["groups"]["none"]["big_share"] == pytest.approx(0.1, abs=0.02)
    assert b["groups"]["pr"]["big_share"] > 0.8 and b["groups"]["pr"]["mean"] == pytest.approx(0.03, abs=0.005)
    json.dumps(b, allow_nan=False)


def test_news_days_from_database_feed_exclusion_and_baseline(tmp_path, monkeypatch):
    from core import articles, causal, db

    monkeypatch.setattr(articles, "COVERAGE_FROM", "2025-01-01")     # 예시 날짜(2025년)도 수집 기간 안으로
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "monitor.db")
    db.init_db()
    causal._NEWS_CACHE.clear()
    dates, own, market, _ = sample(size=200)
    with db.connect() as connection:
        for code, returns in (("X", own), ("KOSDAQ", market)):
            price = 100.0
            for day in dates:
                price *= 1 + returns[day]
                connection.execute("INSERT INTO daily_bars(symbol,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                                   (code, day, price, price, price, price, 1000))
        rows = [("a1", dates[100] + "T10:00", "회사 발표", None), ("a2", dates[110] + "T16:00", "회사 발표", None),
                ("a3", dates[120] + "T10:00", "언론 분석", None), ("a4", dates[130] + "T10:00", "시황·특징주", None),
                ("a5", dates[140] + "T10:00", "회사 발표", "ext"), ("a6", dates[150] + "T10:00", "리서치", "pr")]
        for aid, ts, category, override in rows:
            connection.execute("INSERT INTO articles(id,symbol,ts,title,category,kind_override) VALUES (?,?,?,?,?,?)",
                               (aid, "X", ts, aid, category, override))
        connection.execute("INSERT INTO disclosures(no,symbol,date,ts,pblntf) VALUES ('1','X',?,?, 'B')",
                           (dates[90], dates[90] + "T09:30"))
        connection.execute("INSERT INTO disclosures(no,symbol,date,ts,pblntf) VALUES ('2','X',?,?, 'D')",
                           (dates[95], dates[95] + "T09:30"))
    n = causal.news_days("X")
    assert n["pr"] == {dates[100], dates[101], dates[111], dates[112], dates[150], dates[151]}   # 장 마감 뒤 → 다음 거래일부터
    assert n["ext"] == {dates[120], dates[121], dates[140], dates[141]}                         # 시황·특징주는 소식으로 안 셈
    assert n["disc"] == {dates[90], dates[91]}                                                   # 지분 보고(D)는 뺌
    study = causal.event_study("X", dates[190])
    assert study["ok"] and study["excluded_news_days"] == 8
    baseline = causal.news_baseline("X")
    assert baseline and baseline["groups"]["pr"]["n"] == 6 and baseline["groups"]["none"]["n"] > 60
    assert baseline["from"] >= dates[100]          # 첫 기사 전(기사를 모으지 않던 때)은 비교에서 뺌
    assert sum(g["n"] for g in baseline["groups"].values()) <= len(dates) - 100


def test_intraday_reaction_uses_30_minutes_after_reliable_time(tmp_path, monkeypatch):
    from datetime import datetime

    from core import articles, causal, db

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "monitor.db")
    db.init_db()
    causal._NEWS_CACHE.clear()
    dates, own, market, _ = sample()
    random = np.random.default_rng(3)
    event_day = dates[170]
    with db.connect() as connection:
        for code, returns in (("X", own), ("KOSDAQ", market)):
            price = 100.0
            for i, day in enumerate(dates):
                prev = price
                price *= 1 + returns[day]
                connection.execute("INSERT INTO daily_bars(symbol,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                                   (code, day, price, price, price, price, 1000))
                if i < 140:
                    continue
                level = prev
                for step in sorted(set(range(0, 391, 5)) | {59}):   # Include actual 09:59 pre-publication quote
                    stamp = datetime.fromisoformat(day + "T09:00") + timedelta(minutes=step)
                    level *= 1 + random.normal(0, 0.002)
                    if code == "X" and day == event_day and stamp.strftime("%H:%M") == "10:05":
                        level *= 1.05                                # 발표 5분 뒤 +5%
                    connection.execute("INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                                       (code, stamp.isoformat(timespec="minutes"), level, level, level, level, 10))

    def reaction(ts, time_ok=True):
        eff = articles.effect("X", datetime.fromisoformat(ts))
        return articles.intraday_reaction("X", eff, time_ok)

    hit = reaction(event_day + "T10:00")
    assert hit["kind"] == "session" and hit["start"] == event_day + "T10:00" and hit["end"] == event_day + "T10:30"
    assert hit["abnormal"] > 0.04 and hit["rarity"] < 0.02 and hit["n_null"] > 100
    # 시간대별 표: 모두 같은 방식(시장 몫 + 업종 몫 + 고유), 30분 줄은 대표 숫자와 같음
    assert [r["minutes"] for r in hit["rows"]] == [5, 15, 30, 60, 120]
    thirty = next(r for r in hit["rows"] if r["minutes"] == 30)
    assert thirty["abnormal"] == pytest.approx(hit["abnormal"]) and thirty["own"] == pytest.approx(hit["own"])
    assert all(r["own"] == pytest.approx(r["market"] + r["sector"] + r["abnormal"]) for r in hit["rows"])
    before_open = reaction(event_day + "T08:00")
    assert before_open["kind"] == "open" and before_open["start"] == event_day + "T09:00" and before_open["end"] == event_day + "T09:30"
    assert reaction(event_day + "T10:00", time_ok=False) is None        # 공개 시각 확인 필요
    late = reaction(event_day + "T15:10")                               # 30분을 채울 수 없음 → 다음 거래일 장 시작 30분
    assert late["kind"] == "late" and late["day"] > event_day and late["start"] == late["day"] + "T09:00" and late["end"] == late["day"] + "T09:30"
    assert reaction(dates[100] + "T10:00") is None                      # 분봉 없는 옛 기사
    json.dumps(hit, allow_nan=False)



def test_recently_listed_short_history_fills_with_news_days():
    dates, own, market, sector = sample()
    listed = dates[100:]                       # 상장 뒤 80거래일만 기록
    own = {d: own[d] for d in listed}
    day = dates[170]
    news = set(dates[100:160:2])               # 소식 있던 날이 많아 빼면 50거래일이 안 됨
    result = analyze_returns(own, market, day, sector, calendar=dates, exclude=news)
    assert result["ok"] and result["news_days_included"] and result["excluded_news_days"] == 0
    assert result["est_from"] == dates[105]   # 상장 첫 5거래일은 뺌
    assert result["n_est"] == 55 and not result["short_history"]


def test_short_history_minimum():
    dates, own, market, _ = sample()
    own = {d: own[d] for d in dates[130:]}
    result = analyze_returns(own, market, dates[175], calendar=dates)
    assert result["ok"] and result["short_history"] and result["n_est"] == 30
    own = {d: own[d] for d in dates[140:]}         # 30거래일 미만: 시장 1배 비교 (5거래일 전까지, 상장 첫 5일 뺌)
    result = analyze_returns(own, market, dates[175], calendar=dates)
    assert result["ok"] and result["simple_model"] and result["n_est"] == 25 and result["beta"] == 1.0
    assert result["expected"] == market[dates[175]] and result["ci_low"] < result["ar"] < result["ci_high"]
    own = {d: own[d] for d in dates[160:]}
    result = analyze_returns(own, market, dates[175], calendar=dates)
    assert not result["ok"] and "10거래일 이상" in result["reason"]
