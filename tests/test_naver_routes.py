"""네이버 새 주소(2026-10-26 확인) 응답 읽기: 거래원 broker-trades, 투자자별 investor-trend/v2."""
from __future__ import annotations

import pytest

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


def test_industry_snapshot_and_rates(tmp_path, monkeypatch):
    # 네이버 업종(WICS) 등락률을 날짜별로 저장하고, 화면용으로 고른 업종만 소수로 돌려줌
    from datetime import datetime
    from core import db, macro
    import webserver
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    pages = {1: {"totalCount": 3, "groups": [{"no": 284, "name": "우주항공과국방", "changeRate": "-4.65", "riseCount": 5, "fallCount": 30, "totalCount": 35},
                                            {"no": 278, "name": "반도체와반도체장비", "changeRate": "0.32"},
                                            {"no": 305, "name": "항공사", "changeRate": "-1.41"}]}}
    monkeypatch.setattr(naver, "_json", lambda url, *a, **k: pages.get(int(url.split("page=")[1].split("&")[0]), {"groups": []}))
    assert macro.snapshot_industries(datetime(2026, 10, 26, 14, 0), "2026-10-26") == 3
    with db.connect() as conn:
        got = webserver.industry_rates(conn)
    assert got.keys() == {"2026-10-26"} and got["2026-10-26"].keys() == {"우주항공과국방", "반도체와반도체장비"}
    assert got["2026-10-26"]["우주항공과국방"] == pytest.approx(-0.0465)
    assert got["2026-10-26"]["반도체와반도체장비"] == pytest.approx(0.0032)


def test_wics_peers_take_largest_per_industry(monkeypatch):
    # 비교 종목 = WICS 업종마다 시가총액 큰 순으로 n개, 이 종목은 뺌
    from core import feed
    members = {284: [{"code": "A", "name": "a", "market": "KOSPI", "mcap": 10, "change": 0.01},
                     {"code": "ME", "name": "me", "market": "KOSDAQ", "mcap": 99, "change": 0.0},
                     {"code": "B", "name": "b", "market": "KOSPI", "mcap": 50, "change": -0.02}]}
    monkeypatch.setattr(naver, "fetch_industry_members", lambda no: members.get(no, []))
    assert feed.wics_peers("ME", 1) == [("B", "b", "KOSPI", "우주항공과국방")]
