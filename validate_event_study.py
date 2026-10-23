from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from core.eventstudy import analyze_returns, returns_from_prices


def validate(database: Path, symbol: str, start: str, end: str) -> dict:
    database = database.resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        bars = connection.execute("SELECT symbol, date, close FROM daily_bars WHERE symbol IN (?, ?) ORDER BY date",
                                  (symbol, "KOSDAQ")).fetchall()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"articles", "disclosures", "pr_events"}
        if not required <= tables:
            raise ValueError("기사·공시·보도자료 목록이 모두 있는 데이터베이스가 필요합니다.")
        events = [row[0] for row in connection.execute("SELECT ts FROM articles WHERE symbol=?", (symbol,))]
        events += [row[0] or row[1] + "T00:00" for row in connection.execute(
            "SELECT ts, date FROM disclosures WHERE symbol=?", (symbol,))]
        events += [row[0] for row in connection.execute("SELECT published_ts FROM pr_events")]
    prices = {code: {} for code in (symbol, "KOSDAQ")}
    for code, day, close in bars:
        if close and close > 0 and math.isfinite(close):
            prices[code][day] = close
    calendar = sorted(prices["KOSDAQ"])
    returns = {code: returns_from_prices(series, calendar) for code, series in prices.items()}
    excluded = set()
    for timestamp in events:
        published = datetime.fromisoformat(timestamp)
        index = bisect.bisect_left(calendar, published.date().isoformat())
        if index < len(calendar) and calendar[index] == published.date().isoformat() and published.hour * 60 + published.minute >= 930:
            index += 1
        excluded.update(calendar[max(0, index - 5):index + 6])
    candidates = [day for day in calendar if start <= day <= end and day not in excluded]
    evaluated, unavailable, old_flags, new_flags = [], 0, 0, 0
    for day in candidates:
        result = analyze_returns(returns[symbol], returns["KOSDAQ"], day, calendar=calendar[1:])
        if not result or not result.get("ok"):
            unavailable += 1
            continue
        legacy_p = math.erfc(abs(result["ar"] / result["sigma"]) / math.sqrt(2))
        old_flags += legacy_p < 0.05
        new_flags += result["p"] < 0.05
        evaluated.append({"date": day, "ar": result["ar"], "legacy_p": legacy_p, "p": result["p"],
                          "est_to": result["est_to"]})
    total = len(evaluated)
    return {"symbol": symbol, "from": start, "to": end, "model": "시장 회귀, 일별 반응일 검정",
            "event_records": len(events), "excluded_days": len(excluded), "candidate_days": len(candidates),
            "evaluated_days": total, "unavailable_days": unavailable,
            "legacy_flag_rate": old_flags / total if total else None,
            "new_flag_rate": new_flags / total if total else None, "results": evaluated,
            "interpretation": "수집된 기사·공시 전후 5거래일을 제외한 후보일의 5% 기준 이상 반응 판정 비율입니다. 실제 오탐률이나 원인 판정 정확도가 아닙니다.",
            "limitations": ["수집하지 못한 뉴스와 사건이 후보일에 남아 있을 수 있습니다.",
                            "기사 수집의 완전성은 별도로 확인해야 합니다. 판정 비율이 낮을수록 좋은 모형이라는 뜻은 아닙니다.",
                            "구간 간 의존성과 종목의 특성을 고려해 해석해야 하며, 이 결과로 기준을 반복 조정하지 마세요.",
                            "검증은 시장 모형으로 고정합니다. 비교 종목이 추가된 화면 모형은 별도로 검증해야 합니다."]}


def main():
    parser = argparse.ArgumentParser(description="기존 데이터베이스를 읽기 전용으로 사용해 이벤트 연구를 시간순으로 점검합니다.")
    parser.add_argument("--db", type=Path, required=True, help="기존 monitor.db 경로")
    parser.add_argument("--symbol", required=True, help="분석할 종목 코드")
    parser.add_argument("--from", dest="start", required=True, help="검증 시작일 YYYY-MM-DD")
    parser.add_argument("--to", dest="end", required=True, help="검증 종료일 YYYY-MM-DD")
    args = parser.parse_args()
    try:
        start, end = datetime.fromisoformat(args.start).date(), datetime.fromisoformat(args.end).date()
        if start > end or args.symbol == "KOSDAQ":
            raise ValueError("검증 날짜 순서와 종목 코드를 확인하세요.")
        report = validate(args.db, args.symbol, start.isoformat(), end.isoformat())
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
