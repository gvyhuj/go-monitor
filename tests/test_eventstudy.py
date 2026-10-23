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
    evaluated = {item["date"] for item in report["results"]}
    assert not evaluated.intersection(dates[156:167])
    assert database.read_bytes() == original
    assert all(item["est_to"] < item["date"] for item in report["results"])


def test_daily_study_reaches_article_detail_with_peer_data(tmp_path, monkeypatch):
    from datetime import datetime

    from core import articles, db

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
    assert "기사 효과" not in result["verdict"]
    json.dumps(result, allow_nan=False)


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
