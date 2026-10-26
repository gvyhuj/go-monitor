"""네이버 새 주소(2026-10-26 확인) 응답 읽기: 거래원 broker-trades, 투자자별 investor-trend/v2."""
from __future__ import annotations

from core import naver


def test_broker_trades_maps_to_old_shape():
    d = {"tradingDateKst": "2026-10-26",
         "foreignEstimate": {"askVolume": "290289", "bidVolume": "284591", "netVolume": "-5698"},
         "items": [{"brokerNo": "3", "brokerName": "한국투자증권(주)", "brokerDisplayName": "한국증권",
                    "askVolume": "90080", "bidVolume": "87790", "netVolume": "-2290"},
                   {"brokerNo": "61", "brokerName": "다이와증권캐피탈마켓코리아(주)", "brokerDisplayName": "다이와",
                    "askVolume": "0", "bidVolume": "51071", "netVolume": "51071"}]}
    got = naver.parse_broker_trades(d)
    assert (got["foreign_buy"], got["foreign_sell"]) == (284591, 290289)
    a, b = got["traders"]
    assert (a["trader_no"], a["name"], a["buy"], a["sell"], a["bizdate"], a["foreign"]) == ("3", "한국증권", 87790, 90080, "20261026", False)
    assert b["foreign"] and b["buy"] == 51071


def test_investor_trend_v2():
    d = {"hasNext": True, "items": [{"localTradedAt": "2026-10-23", "foreignHoldingRatio": "46.11",
                                     "krx": {"foreignNetVolume": "-868997", "organizationNetVolume": "627670",
                                             "individualNetVolume": "-61043", "closingPrice": "213500",
                                             "tradingVolume": "16145972"}, "nxt": None}]}
    assert naver.parse_investor_trend(d) == [{"bizdate": "20261023", "foreign": -868997, "organ": 627670,
                                              "individual": -61043, "close": 213500, "volume": 16145972,
                                              "foreign_ratio": 46.11}]
