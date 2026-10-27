from __future__ import annotations

import json

import numpy as np
from scipy.stats import t as student_t


WINDOWS = {
    "사전 5일 [-5,-1]": (-5, -1),
    "반응일 [0,0]": (0, 0),
    "전후 3일 [-1,+1]": (-1, 1),
    "이후 3일 [0,+2]": (0, 2),
    "후속 3일 [+1,+3]": (1, 3),
}


MIN_EST = 30          # 평소 관계 추정에 필요한 최소 거래일 (50거래일 미만이면 '기록 짧음'으로 표시)
MIN_SIMPLE = 10       # 그보다 짧으면 시장 1배 비교 — 평소 출렁임을 구할 최소 거래일
LISTING_SKIP = 5      # 상장 첫 5거래일은 추정에서 뺌


def summary_critical_values(count: int) -> list:
    return [None, None] + [float(student_t.ppf(0.975, size - 1)) for size in range(2, count + 1)]


def validate_primary_exports(items: list) -> dict:
    payloads = {key: json.loads(raw) for key, raw, _ in items
                if key in ("articles", "article_details", "disclosures", "disclosure_details")}
    counts = {}
    for kind, details_key in (("articles", "article_details"), ("disclosures", "disclosure_details")):
        rows = payloads[kind].get("items") or []
        details = payloads[details_key]
        if len(rows) != len(details):
            raise ValueError("목록과 상세 분석의 개수가 다릅니다.")
        calculated = 0
        for row in rows:
            effect = details[str(row["id"])]["effect"]
            measure = effect.get("measure")
            study = effect.get("event_study") or {}
            if row["effect"].get("measure") != measure or row["effect"].get("verdict") != effect.get("verdict"):
                raise ValueError("목록과 상세 분석의 대표 값 또는 판정이 다릅니다.")
            if study.get("ok"):
                if not measure or measure.get("method") != "event-study-v2" or any(
                    measure.get(destination) != study[source] for destination, source in
                    (("value", "ar"), ("own", "actual"), ("expected", "expected"),
                     ("ci_low", "ci_low"), ("ci_high", "ci_high"))):
                    raise ValueError("대표 값이 새 사건연구 결과와 다릅니다.")
                calculated += 1
            elif measure is not None:
                raise ValueError("새 모델을 계산하지 못한 자료에 기존 대표 값이 남아 있습니다.")
        counts[kind] = {"total": len(rows), "calculated": calculated, "unavailable": len(rows) - calculated}
    return counts


def returns_from_prices(prices: dict, calendar: list[str]) -> dict:
    return {day: prices[day] / prices[previous] - 1
            for previous, day in zip(calendar, calendar[1:])
            if all(prices.get(item) is not None and np.isfinite(prices[item]) and prices[item] > 0
                   for item in (previous, day))}


def estimation_days(dates: list[str], event_index: int, est_len: int, gap: int,
                    exclude: set | None = None) -> list[str]:
    """반응일 gap거래일 전까지의 est_len거래일. 기사·공시가 있던 날은 건너뛰고 그만큼 더 앞에서 채움
    (그날들의 큰 움직임이 '평소 변동'을 부풀리지 않게)."""
    if not exclude:
        return dates[max(0, event_index - gap - est_len):max(0, event_index - gap)]
    usable = [day for day in dates[:max(0, event_index - gap)] if day not in exclude]
    return usable[-est_len:]


def analyze_returns(own: dict, market: dict, day: str, sector: dict | None = None,
                    est_len: int = 120, gap: int = 10, calendar: list[str] | None = None,
                    exclude: set | None = None) -> dict | None:
    if est_len < 50 or gap < 5:
        raise ValueError("추정 기간은 50거래일 이상, 간격은 5거래일 이상이어야 합니다.")
    dates = sorted(set(calendar)) if calendar is not None else sorted(market)
    if day not in dates or day not in own or day not in market:
        return None
    event_index = dates.index(day)
    # 상장한 지 얼마 안 된 종목: 상장 첫 거래일들(공모가 대비 큰 출렁임)은 평소 관계 추정에서 뺌
    own_days = sorted(d for d in own if d in market)
    skip = set(own_days[:LISTING_SKIP]) if own_days and own_days[0] > dates[0] else set()

    def usable(days):
        return [d for d in days if d in own and d in market and d not in skip
                and np.isfinite(own[d]) and np.isfinite(market[d])]

    estimation = usable(estimation_days(dates, event_index, est_len, gap, exclude))
    news_included = False
    if len(estimation) < 50 and exclude:
        # 기록이 짧아 소식 있던 날을 빼면 모자라면: 그날들도 넣어 채움 (평소 출렁임이 조금 커질 수 있음)
        wider = usable(estimation_days(dates, event_index, est_len, gap))
        if len(wider) > len(estimation):
            estimation, news_included = wider, True
    simple = False
    if len(estimation) < MIN_EST:
        # 상장 직후라 기록이 30거래일도 안 되면: 평소 관계(민감도)를 추정하지 않고 '시장만큼 움직였다면'(시장 1배)으로 비교.
        # 평소 출렁임만 반응일 5거래일 전까지의 날(소식 날 포함)로 구함
        short = usable(estimation_days(dates, event_index, est_len, min(gap, 5))) if skip else []
        if not skip:
            return {"ok": False, "reason": f"평소 관계를 계산할 기록이 부족합니다 ({len(estimation)}거래일, {MIN_EST}거래일 이상 필요)."}
        if len(short) < MIN_SIMPLE:
            return {"ok": False, "reason": f"평소 출렁임을 계산할 기록이 부족합니다 ({len(short)}거래일, {MIN_SIMPLE}거래일 이상 필요"
                                           f"{' · 상장 직후 기사' if skip else ''})."}
        estimation, simple, news_included, gap = short, True, bool(exclude), min(gap, 5)
    use_sector = False
    if sector and not simple:
        covered = [day for day in estimation if day in sector and np.isfinite(sector[day])]
        if len(covered) >= max(MIN_EST, int(np.ceil(0.8 * len(estimation)))):
            probe = np.array([[1.0, market[day], sector[day]] for day in covered])
            use_sector = np.linalg.matrix_rank(probe) == 3
            if use_sector:
                estimation = covered

    def design(day):
        return [1.0, market[day]] + ([sector[day]] if use_sector else [])

    design_matrix = np.array([design(day) for day in estimation])
    observed = np.array([own[day] for day in estimation])
    if simple:                       # 시장 1배: 계수를 추정하지 않으므로 계수 오차도 없음
        coefficients = np.array([0.0, 1.0])
    elif np.linalg.matrix_rank(design_matrix) < design_matrix.shape[1]:
        return {"ok": False, "reason": "시장 수익률의 변화가 부족해 평소 관계를 추정할 수 없습니다."}
    else:
        coefficients = np.linalg.lstsq(design_matrix, observed, rcond=None)[0]
    residuals = observed - design_matrix @ coefficients
    degrees = len(estimation) - (1 if simple else len(coefficients))
    variance = float(residuals @ residuals / degrees)
    if variance <= np.finfo(float).eps ** 2:
        return {"ok": False, "reason": "평소 오차가 0에 가까워 통계 검정을 계산할 수 없습니다."}
    coefficient_covariance = (np.zeros((2, 2)) if simple
                              else variance * np.linalg.inv(design_matrix.T @ design_matrix))
    critical = float(student_t.ppf(0.975, degrees))

    def measure(days):
        if not days or any(day not in own or day not in market or (use_sector and day not in sector) for day in days):
            return None
        event_design = np.array([design(day) for day in days])
        actual = np.array([own[day] for day in days])
        if not np.isfinite(event_design).all() or not np.isfinite(actual).all():
            return None
        expected = event_design @ coefficients
        abnormal = float(np.sum(actual - expected))
        summed_design = event_design.sum(axis=0)
        standard_error = float(np.sqrt(len(days) * variance
                               + summed_design @ coefficient_covariance @ summed_design))
        statistic = abnormal / standard_error
        return {"car": abnormal, "actual_sum": float(actual.sum()), "expected_sum": float(expected.sum()),
                "se": standard_error, "t": statistic, "p": float(2 * student_t.sf(abs(statistic), degrees)),
                "ci_low": abnormal - critical * standard_error, "ci_high": abnormal + critical * standard_error,
                "dates": days, "days": len(days)}

    current = measure([day])
    if current is None:
        return {"ok": False, "reason": "반응일의 종목·시장·비교 대상 시세가 부족합니다."}
    windows = []
    pending = []
    for label, (start, end) in WINDOWS.items():
        if event_index + start < 0 or event_index + end >= len(dates):
            pending.append(label)
            continue
        result = measure(dates[event_index + start:event_index + end + 1])
        if result is None:
            pending.append(label)
        else:
            windows.append({"window": label, **result})
    pre = next((item for item in windows if item["window"].startswith("사전")), None)
    return {"ok": True, "method": "시장 1배 비교 (상장 직후라 기록 짧음)" if simple else "시장·비교 종목 회귀" if use_sector else "시장 회귀",
            "n_est": len(estimation), "est_from": estimation[0], "est_to": estimation[-1],
            "gap": gap, "alpha": float(coefficients[0]), "beta": float(coefficients[1]),
            "beta_sector": float(coefficients[2]) if use_sector else None,
            "sigma": float(np.sqrt(variance)), "df": degrees, "actual": own[day],
            "expected": current["expected_sum"], "ar": current["car"],
            **{key: current[key] for key in ("se", "t", "p", "ci_low", "ci_high")},
            "car": windows, "pending_windows": pending,
            "pre_drift": bool(pre and pre["p"] < 0.05 and pre["car"] * current["car"] > 0),
            "excluded_news_days": 0 if news_included else len([d for d in dates[:max(0, event_index - gap)]
                                                               if exclude and d in exclude and d >= estimation[0]]),
            "news_days_included": news_included, "short_history": len(estimation) < 50, "simple_model": simple,
            "basis": "일별 종가로 계산하므로 기사 공개 전의 당일 움직임도 포함합니다.",
            "assumptions": "오차가 서로 독립이고 분산이 일정한 정규분포라고 가정한 검정입니다. 여러 구간의 p값은 다중 검정 보정 전입니다."}


def baseline_by_group(own: dict, market: dict, days: list[str], groups: dict[str, set],
                      quiet: str, sector: dict | None = None, top: float = 0.9) -> dict | None:
    """날을 '소식 종류'로 나눠 예상 대비 차이를 비교. 평소 관계는 소식 없는 날(quiet)만으로 한 번 추정.
    '큰 움직임' 기준은 소식 없는 날 차이 크기의 상위 (1-top) 경계 → 소식 없는 날은 정의상 약 10%."""
    days = [d for d in days if d in own and d in market and np.isfinite(own[d]) and np.isfinite(market[d])]
    use_sector = bool(sector) and sum(d in sector for d in days) >= 0.8 * len(days)
    if use_sector:
        days = [d for d in days if d in sector and np.isfinite(sector[d])]

    def design(d):
        return [1.0, market[d]] + ([sector[d]] if use_sector else [])

    quiet_days = [d for d in days if d in groups[quiet]]
    if len(quiet_days) < 60:
        return None
    x = np.array([design(d) for d in quiet_days])
    if np.linalg.matrix_rank(x) < x.shape[1]:
        return None
    coef = np.linalg.lstsq(x, np.array([own[d] for d in quiet_days]), rcond=None)[0]
    ar = {d: float(own[d] - np.array(design(d)) @ coef) for d in days}
    cut = float(np.quantile([abs(ar[d]) for d in quiet_days], top))
    rows = {}
    for name, members in groups.items():
        xs = [ar[d] for d in days if d in members]
        rows[name] = {"n": len(xs), "mean": float(np.mean(xs)) if xs else None,
                      "median_abs": float(np.median(np.abs(xs))) if xs else None,
                      "big_share": sum(abs(v) >= cut for v in xs) / len(xs) if xs else None,
                      "big_up": sum(v >= cut for v in xs), "big_down": sum(v <= -cut for v in xs)}
    return {"from": days[0], "to": days[-1], "cut": cut, "top": top, "groups": rows,
            "method": "시장·비교 종목 회귀" if use_sector else "시장 회귀"}
