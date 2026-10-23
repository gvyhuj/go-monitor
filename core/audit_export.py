"""Build a narrow, reviewable event-study export from already-collected data.

Only explicitly allowlisted market/news tables and article/disclosure results are included.
System state, encrypted histories, user settings, passwords, tokens and credentials are excluded.
The caller MUST encrypt the returned payload before deploying it to a public static site.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .db import connect

RESULT_KEYS = ("articles", "article_details", "disclosures", "disclosure_details")
MARKET_QUERIES = {
    "daily_bars": "SELECT symbol, date, open, high, low, close, volume FROM daily_bars ORDER BY symbol, date",
    "minute_bars": "SELECT symbol, ts, open, high, low, close, volume, source FROM minute_bars ORDER BY symbol, ts",
    "related_stocks": "SELECT code, name, market, source, updated_at FROM related_stocks ORDER BY code",
    "articles_raw": "SELECT id, symbol, ts, office, title, body, url, outlets, category, tone, mention, story, first_seen, hidden, source, kind_override, tone_override FROM articles ORDER BY ts, id",
    "pr_events_raw": "SELECT id, title, published_ts, url, pr_type, created_at FROM pr_events ORDER BY published_ts, id",
    "disclosures_raw": "SELECT no, symbol, date, title, kind, summary, first_seen, ts, pblntf, filer, rm FROM disclosures ORDER BY date, no",
}


def build_payload(items: list[tuple[str, str, bool]], version: str) -> dict:
    """Snapshot used by the website and its full available model input data.

    No database file or free-form system/config table is included: these can contain
    credentials, private company settings, and admin-history ciphertext.
    """
    calculated = {}
    for name, raw, is_base64 in items:
        if name in RESULT_KEYS:
            if is_base64:
                raise ValueError(f"분석 자료가 예상과 다른 인코딩입니다: {name}")
            calculated[name] = json.loads(raw)
    missing = sorted(set(RESULT_KEYS) - set(calculated))
    if missing:
        raise ValueError(f"계산식 검증용 자료가 누락되었습니다: {', '.join(missing)}")

    with connect() as conn:
        market = {
            name: [dict(row) for row in conn.execute(query)]
            for name, query in MARKET_QUERIES.items()
        }

    return {
        "format": "go-monitor-audit-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_version": version,
        "measurement": {
            "formula": "AR = actual_simple_daily_return - OLS_expected_simple_daily_return",
            "expected": "alpha + beta_market * KOSDAQ + optional beta_sector * mean(peer_returns)",
            "estimation_days_max": 120,
            "estimation_days_min": 50,
            "gap_trading_days": 10,
            "test_confidence_level": 0.95,
            "returns": "consecutive trading-day closing prices as stored; corporate-action adjustment is not independently verified",
            "note": "Event dates are not causal estimates. Intraday bars cover only retained collection history.",
        },
        "calculated": calculated,
        "raw": market,
    }
