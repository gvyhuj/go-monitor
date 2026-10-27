"""한국투자증권 오픈API로 어떤 자료를 받을 수 있는지 확인 (수동 실행 'KIS 확인' 워크플로).

기록·사이트는 건드리지 않음. 저장소가 공개라 실행 기록도 공개 → 값(가격·수량)은 찍지 않고
성공/실패, 받은 줄 수, 날짜 범위, 항목 이름만 남김.

python -m core.kis_probe 0015G0
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta

from .kis import Client, KisError, day_minutes

Q = "/uapi/domestic-stock/v1/quotations"


def _rows(d: dict) -> list:
    for k in ("output2", "output", "output1"):
        v = d.get(k)
        if isinstance(v, list) and v:
            return v
    v = d.get("output") or d.get("output1")
    return [v] if isinstance(v, dict) and v else []


def _dates(rows: list) -> str:
    ds = sorted({str(r.get(k)) for r in rows for k in ("stck_bsop_date", "bsop_date", "stck_bsop_date1", "deal_date") if r.get(k)})
    return f"{ds[0]}~{ds[-1]}" if ds else "-"


def checks(code: str, today: date) -> list[tuple[str, str, str, dict]]:
    """(이름, 경로, tr_id, 조건) — 공식 예제 코드의 이름 그대로."""
    d1 = (today - timedelta(days=365)).strftime("%Y%m%d")
    d0 = today.strftime("%Y%m%d")
    J = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    U = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "1001"}
    return [
        ("현재가", f"{Q}/inquire-price", "FHKST01010100", J),
        ("호가·예상체결", f"{Q}/inquire-asking-price-exp-ccn", "FHKST01010200", J),
        ("체결(최근 틱)", f"{Q}/inquire-ccnl", "FHKST01010300", J),
        ("일자별 시세(최근 30일)", f"{Q}/inquire-daily-price", "FHKST01010400", {**J, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "1"}),
        ("기간별 일봉(1년)", f"{Q}/inquire-daily-itemchartprice", "FHKST03010100",
         {**J, "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": d0, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}),
        ("당일 분봉", f"{Q}/inquire-time-itemchartprice", "FHKST03010200",
         {**J, "FID_ETC_CLS_CODE": "", "FID_INPUT_HOUR_1": "153000", "FID_PW_DATA_INCU_YN": "N"}),
        ("투자자별(최근 30일)", f"{Q}/inquire-investor", "FHKST01010900", J),
        ("투자자별 일별(기간)", f"{Q}/investor-trade-by-stock-daily", "FHPTJ04160001",
         {**J, "FID_INPUT_DATE_1": d0, "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": ""}),
        ("거래원(회원사) 상위", f"{Q}/inquire-member", "FHKST01010600", J),
        ("프로그램매매 종목별", f"{Q}/program-trade-by-stock", "FHPPG04650100", J),
        ("공매도 일별", f"{Q}/daily-short-sale", "FHPST04830000", {**J, "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": d0}),
        ("신용잔고 일별", f"{Q}/daily-credit-balance", "FHPST04760000",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20476", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": d0}),
        ("코스닥 지수 일봉(1년)", f"{Q}/inquire-daily-indexchartprice", "FHKUP03500100",
         {**U, "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": d0, "FID_PERIOD_DIV_CODE": "D"}),
        ("코스닥 지수 분봉", f"{Q}/inquire-time-indexchartprice", "FHKUP03500200",
         {**U, "FID_ETC_CLS_CODE": "0", "FID_INPUT_HOUR_1": "60", "FID_PW_DATA_INCU_YN": "Y"}),
        ("종목 기본정보", "/uapi/domestic-stock/v1/quotations/search-stock-info", "CTPF1002R", {"PRDT_TYPE_CD": "300", "PDNO": code}),
        ("손익계산서", "/uapi/domestic-stock/v1/finance/income-statement", "FHKST66430200",
         {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}),
    ]


def main(argv) -> int:
    code = argv[1] if len(argv) > 1 else "0015G0"
    key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not secret:
        print("KIS_APP_KEY / KIS_APP_SECRET 이 없습니다 (GitHub Secrets).")
        return 1
    try:
        cl = Client(key, secret)
    except KisError as e:
        print(f"접근 토큰 발급 실패: {e}")
        return 1
    print(f"| 자료 | 결과 | 줄 수 | 날짜 범위 | 항목 이름(앞 8개) |\n|---|---|---|---|---|")
    for name, path, tr, params in checks(code, date.today()):
        try:
            rows = _rows(cl.get(path, tr, params))
            keys = ", ".join(list(rows[0])[:8]) if rows else "-"
            print(f"| {name} | 성공 | {len(rows)} | {_dates(rows)} | {keys} |")
        except KisError as e:
            print(f"| {name} | 실패 | - | - | {str(e)[:80]} |")
        time.sleep(0.3)
    # 지난 1분봉: 날짜마다 몇 개 받는지 (보관 기간 확인)
    print("\n지난 1분봉 (주식일별분봉조회) — 날짜별 받은 봉 수")
    for day in ("2025-11-17", "2025-11-28", "2025-12-15", "2026-01-15", "2026-02-16", "2026-04-22", "2026-06-15", "2026-09-22"):
        for market, c, label in (("J", code, "종목"), ("U", "1001", "코스닥 지수")):
            try:
                bars = day_minutes(cl, c, day, market=market, pause=0.3)
                span = f"{bars[0].ts[11:]}~{bars[-1].ts[11:]}" if bars else "-"
                print(f"- {day} {label}: {len(bars)}개 ({span})")
            except KisError as e:
                print(f"- {day} {label}: 실패 {str(e)[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
