from __future__ import annotations

import numpy as np
from scipy.stats import t as student_t


WINDOWS = {
    "사전 5일 [-5,-1]": (-5, -1),
    "반응일 [0,0]": (0, 0),
    "전후 3일 [-1,+1]": (-1, 1),
    "이후 3일 [0,+2]": (0, 2),
    "후속 3일 [+1,+3]": (1, 3),
}


def returns_from_prices(prices: dict, calendar: list[str]) -> dict:
    return {day: prices[day] / prices[previous] - 1
            for previous, day in zip(calendar, calendar[1:])
            if all(prices.get(item) is not None and np.isfinite(prices[item]) and prices[item] > 0
                   for item in (previous, day))}


def analyze_returns(own: dict, market: dict, day: str, sector: dict | None = None,
                    est_len: int = 120, gap: int = 10, calendar: list[str] | None = None) -> dict | None:
    if est_len < 50 or gap < 5:
        raise ValueError("추정 기간은 50거래일 이상, 간격은 5거래일 이상이어야 합니다.")
    dates = sorted(set(calendar)) if calendar is not None else sorted(market)
    if day not in dates or day not in own or day not in market:
        return None
    event_index = dates.index(day)
    candidates = dates[max(0, event_index - gap - est_len):max(0, event_index - gap)]
    estimation = [day for day in candidates if day in own and day in market
                  and np.isfinite(own[day]) and np.isfinite(market[day])]
    if len(estimation) < 50:
        return {"ok": False, "reason": f"평소 관계를 계산할 기록이 부족합니다 ({len(estimation)}거래일)."}
    use_sector = False
    if sector:
        covered = [day for day in estimation if day in sector and np.isfinite(sector[day])]
        if len(covered) >= max(50, int(np.ceil(0.8 * len(estimation)))):
            probe = np.array([[1.0, market[day], sector[day]] for day in covered])
            use_sector = np.linalg.matrix_rank(probe) == 3
            if use_sector:
                estimation = covered

    def design(day):
        return [1.0, market[day]] + ([sector[day]] if use_sector else [])

    design_matrix = np.array([design(day) for day in estimation])
    observed = np.array([own[day] for day in estimation])
    if np.linalg.matrix_rank(design_matrix) < design_matrix.shape[1]:
        return {"ok": False, "reason": "시장 수익률의 변화가 부족해 평소 관계를 추정할 수 없습니다."}
    coefficients = np.linalg.lstsq(design_matrix, observed, rcond=None)[0]
    residuals = observed - design_matrix @ coefficients
    degrees = len(estimation) - len(coefficients)
    variance = float(residuals @ residuals / degrees)
    if variance <= np.finfo(float).eps ** 2:
        return {"ok": False, "reason": "평소 오차가 0에 가까워 통계 검정을 계산할 수 없습니다."}
    coefficient_covariance = variance * np.linalg.inv(design_matrix.T @ design_matrix)
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
    return {"ok": True, "method": "시장·비교 종목 회귀" if use_sector else "시장 회귀",
            "n_est": len(estimation), "est_from": estimation[0], "est_to": estimation[-1],
            "gap": gap, "alpha": float(coefficients[0]), "beta": float(coefficients[1]),
            "beta_sector": float(coefficients[2]) if use_sector else None,
            "sigma": float(np.sqrt(variance)), "df": degrees, "actual": own[day],
            "expected": current["expected_sum"], "ar": current["car"],
            **{key: current[key] for key in ("se", "t", "p", "ci_low", "ci_high")},
            "car": windows, "pending_windows": pending,
            "pre_drift": bool(pre and pre["p"] < 0.05 and pre["car"] * current["car"] > 0),
            "basis": "일별 종가로 계산하므로 기사 공개 전의 당일 움직임도 포함합니다.",
            "assumptions": "오차가 서로 독립이고 분산이 일정한 정규분포라고 가정한 검정입니다. 여러 구간의 p값은 다중 검정 보정 전입니다."}
