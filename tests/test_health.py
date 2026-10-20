"""갱신 멈춤 알림: 게시 지연(A)·시세 수집 실패(B) 판단, 하루 한 번, 복구 알림."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core import alerts, db
from core.db import set_state

NOW = datetime(2026, 10, 20, 10, 5)      # 화요일 장중


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def _p(**kw):
    base = dict(data_last_ok=NOW - timedelta(minutes=2), site_modified=NOW - timedelta(minutes=3),
                build_streak_start=NOW - timedelta(hours=2), trading_day=True)
    base.update(kw)
    return alerts.health_problems(NOW, **base)


def test_normal():
    assert _p() == set()


def test_site_stuck_is_A():
    assert _p(site_modified=NOW - timedelta(minutes=31)) == {"A"}


def test_site_old_right_after_job_restart_is_not_A():
    # 자동 실행이 멈췄다 방금 다시 돈 경우: 사이트가 오래된 건 실행이 멈췄기 때문
    assert _p(site_modified=NOW - timedelta(minutes=60), build_streak_start=NOW - timedelta(minutes=3)) == set()


def test_fetch_fail_is_B():
    assert _p(data_last_ok=NOW - timedelta(minutes=31)) == {"B"}
    assert _p(data_last_ok=None) == {"B"}


def test_flat_price_is_not_B():
    # 가격이 그대로여도 네이버 응답이 계속 성공하면 정상
    assert _p(data_last_ok=NOW - timedelta(minutes=1)) == set()


def test_outside_hours_or_holiday():
    late = dict(site_modified=NOW - timedelta(hours=3), data_last_ok=NOW - timedelta(hours=3))
    assert alerts.health_problems(NOW.replace(hour=16), build_streak_start=NOW - timedelta(hours=5),
                                  trading_day=True, **late) == set()
    assert _p(trading_day=False, **late) == set()
    assert alerts.health_problems(datetime(2026, 10, 18, 10, 5), build_streak_start=NOW - timedelta(days=3),
                                  trading_day=True, **late) == set()       # 일요일


def test_alert_once_a_day_then_recovery(tmpdb, monkeypatch):
    posted = []
    monkeypatch.setattr(alerts, "_post_issue", lambda t, b: posted.append(t) or 1)
    site = {"m": NOW - timedelta(minutes=31)}   # 바로 지금 30분을 넘김
    monkeypatch.setattr(alerts, "_site_modified", lambda: site["m"])
    set_state("data_last_ok", NOW.isoformat(timespec="seconds"))

    t = NOW - timedelta(minutes=45)
    while t < NOW:                         # 3분마다 끊김 없이 돈 실행
        alerts.check_health(t, trading_day=True)
        t += timedelta(minutes=3)
    assert posted == []                    # 30분이 되기 전에는 알리지 않음

    alerts.check_health(NOW, trading_day=True)
    assert len(posted) == 1 and "사이트 게시 지연" in posted[0]
    alerts.check_health(NOW + timedelta(minutes=3), trading_day=True)
    assert len(posted) == 1                # 계속 멈춰 있어도 다시 보내지 않음

    site["m"] = NOW + timedelta(minutes=5)  # 풀림
    set_state("data_last_ok", (NOW + timedelta(minutes=6)).isoformat(timespec="seconds"))
    alerts.check_health(NOW + timedelta(minutes=6), trading_day=True)
    assert len(posted) == 2 and posted[1].startswith("[복구]")

    site["m"] = NOW - timedelta(minutes=10)  # 같은 날 다시 멈춰도 하루 한 번
    alerts.check_health(NOW + timedelta(minutes=30), trading_day=True)
    alerts.check_health(NOW + timedelta(minutes=33), trading_day=True)
    assert len(posted) == 2


def test_trading_day_from_naver_status():
    ok = NOW - timedelta(minutes=2)
    assert alerts.is_trading_day(NOW, {"market_status": "OPEN", "traded_at": "2026-10-20T10:04:00+09:00"}, ok)
    # 평일 휴장일(선거일 등): 네이버가 닫힘이고 오늘 체결이 없음
    assert not alerts.is_trading_day(NOW, {"market_status": "CLOSE", "traded_at": "2026-10-19T15:30:00+09:00"}, ok)
    # 네이버를 못 받고 있으면 상태를 모르니 평일은 거래일로 봄 (그래야 수집 실패 알림이 나감)
    assert alerts.is_trading_day(NOW, {"market_status": "CLOSE", "traded_at": "2026-10-19T15:30:00+09:00"},
                                 NOW - timedelta(hours=1))
    assert not alerts.is_trading_day(datetime(2026, 10, 18, 10, 0), None, None)   # 일요일
