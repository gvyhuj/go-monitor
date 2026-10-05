"""다음 금융 거래원 자료 (네이버와 교차 검증용 두 번째 출처).

다음 금융 화면 설명: "코스콤에서 제공하는 매매상위 5개사 자료에 의한 추정치"
- 매매상위  https://finance.daum.net/api/trader/ranks
            앞의 5개가 현재 상위, 그 뒤는 '이탈 거래원'(5위 밖으로 밀려나 수량이 멈춘 창구)
            intervalType=TODAY(당일) / YESTERDAY(전일)
- 일별 합계 https://finance.daum.net/api/trader/histories  (외국계/국내 창구 일별 매수·매도, 추정치)
다음 금융 API는 Referer 가 없으면 403을 돌려준다.
"""
from __future__ import annotations

import json
import urllib.request

from .naver import DataSourceError, USER_AGENT, is_foreign_broker, _int

API = "https://finance.daum.net/api"


def http_get(url: str, code: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Referer": f"https://finance.daum.net/quotes/A{code}",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        raise DataSourceError(f"다음 금융 접속 실패: {type(e).__name__}: {e}")


def _json(url: str, code: str):
    try:
        return json.loads(http_get(url, code).decode("utf-8"))
    except DataSourceError:
        raise
    except Exception as e:
        raise DataSourceError(f"다음 금융 형식 오류: {e}")


def fetch_trader_ranks(code: str, interval: str = "TODAY") -> dict:
    url = (f"{API}/trader/ranks?symbolCode=A{code}&limit=10&BidFieldName=bidAccTradeVolume&BidOrder=desc"
           f"&AskFieldName=askAccTradeVolume&AskOrder=desc&intervalType={interval}&page=1&perPage=10")
    d = _json(url, code)
    merged: dict[str, dict] = {}
    for side in ("BID", "ASK"):
        data = (d.get(side) or {}).get("data") or []
        for i, r in enumerate(data):
            name = r.get("traderName") or ""
            m = merged.setdefault(name, {
                "name": name, "buy": _int(r.get("bidAccTradeVolume")), "sell": _int(r.get("askAccTradeVolume")),
                "foreign": bool(r.get("isForeignInvestor")) or is_foreign_broker(name, name),
                "buy_top5": False, "sell_top5": False,
            })
            if side == "BID" and i < 5:
                m["buy_top5"] = True
            if side == "ASK" and i < 5:
                m["sell_top5"] = True
    return {
        "source": "다음 금융", "interval": interval, "base_date": d.get("baseDate"),
        "foreign_buy": _int((d.get("BID") or {}).get("foreignInvestorAskBidSum")),
        "foreign_sell": _int((d.get("ASK") or {}).get("foreignInvestorAskBidSum")),
        "traders": list(merged.values()),
    }


def fetch_trader_histories(code: str, n: int = 30) -> list[dict]:
    d = _json(f"{API}/trader/histories?symbolCode=A{code}&page=1&perPage={n}", code)
    out = []
    for r in d.get("data") or []:
        f, k = r.get("foreignTrader") or {}, r.get("domesticTrader") or {}
        out.append({
            "date": str(r.get("date") or "")[:10],
            "foreign_buy": _int(f.get("bidAccVolume")), "foreign_sell": _int(f.get("askAccVolume")),
            "foreign_net": _int(f.get("netSales")),
            "domestic_buy": _int(k.get("bidAccVolume")), "domestic_sell": _int(k.get("askAccVolume")),
            "domestic_net": _int(k.get("netSales")),
            "close": r.get("tradePrice"),
        })
    return out
