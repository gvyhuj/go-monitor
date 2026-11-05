"""Synthetic data: verified closing price must not fabricate minute OHLC or volume."""
import json
from datetime import datetime
import pytest
from core import db, feed, kis, macro


def test_verified_close_keeps_quote_and_chart_consistent(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    db.init_db()
    monkeypatch.setenv('KIS_APP_KEY', 'fixture')
    monkeypatch.setenv('KIS_APP_SECRET', 'fixture')
    monkeypatch.setattr(feed, 'kst_now', lambda: datetime(2026, 11, 4, 16, 55))
    calls = []
    rows = [{'date': '2026-11-03', 'open': 9750, 'high': 9750, 'low': 9750, 'close': 9750, 'volume': 10},
            {'date': '2026-11-04', 'open': 9820, 'high': 10010, 'low': 9350, 'close': 9870, 'volume': 29590}]
    def daily(*args, **kwargs):
        calls.append('daily')
        return rows
    monkeypatch.setattr(kis, 'daily_range', daily)
    monkeypatch.setattr(kis, 'minutes', lambda *a, **kw: [dict(ts='2026-11-04T15:30', open=9870, high=9870, low=9870, close=9870, volume=175)])
    macro._sync_daily_kis('000001')
    feed._confirm_closing_minute('000001', feed.kst_now())
    final = feed.verified_regular_close('000001', '2026-11-04')
    assert final['close'] == 9870 and final['base'] == 9750
    def quote(*args):
        calls.append('quote')
        return {'price': 10070, 'change': 320, 'rate': 3.28, 'open': 9820, 'high': 10110, 'low': 9350, 'volume': 30000}
    monkeypatch.setattr(kis, 'quote', quote)
    monkeypatch.setattr(kis, 'index_quote', lambda: {'price': 715, 'change': 10, 'rate': 1.5})
    db.set_state('regular_close:KOSDAQ:2026-11-04', json.dumps({'date': '2026-11-04', 'source': 'kis_regular_minute', 'asof': '2026-11-04T15:30',
        'confirmed_at': '2026-11-04T16:55:00', 'open': 715, 'high': 715, 'low': 715, 'close': 715, 'volume': 100, 'base': 705}))
    feed.update_quotes('000001')
    out = json.loads(db.get_state('quote:000001')['value'])
    assert out['price'] == 9870 and out['change'] == 120 and out['high'] == 10010
    assert out['traded_at'] == '2026-11-04T15:30:00' and out['regular_close']['source'] == 'kis_regular_minute'
    assert out['latest_quote']['price'] == 10070 and out['latest_quote']['queried_at'] == '2026-11-04T16:55:00'
    assert out['overtime_status']['state'] == 'ok' and out['after']['price'] == 10070 and out['after']['session'] == 'krx_after'
    assert calls == ['daily', 'quote'], 'reuse collection; no added daily/quote API requests'
    import webserver as ws
    monkeypatch.setattr(ws, 'load_settings', lambda: {'symbol': '000001'})
    payload = ws.api_intraday({'date': '2026-11-04'})
    assert payload['close_point'] is None, 'a later daily snapshot must never replace the 15:30 minute'
    assert payload['bars'][-1]['c'] == 9870 and payload['bars'][-1]['v'] == 175
    with db.connect() as conn:
        assert tuple(conn.execute("SELECT close,volume FROM minute_bars WHERE symbol='000001'").fetchone()) == (9870, 175)
    db.set_state('after:000001', json.dumps({'date': '2026-11-04', 'source': 'kis', 'price': 9950, 'change': -120, 'rate': -0.01, 'at': '17:00'}))
    after = feed._kis_after('000001', datetime(2026, 11, 4, 20, 30), True, dict(close=9870), None)
    assert after['change'] == 80 and abs(after['rate'] - 80 / 9870) < 1e-12


def test_intraday_daily_is_not_a_confirmed_close(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    db.init_db()
    monkeypatch.setattr(feed, 'kst_now', lambda: datetime(2026, 11, 4, 14, 0))
    monkeypatch.setattr(kis, 'daily_range', lambda *a, **kw: [
        {'date': '2026-11-04', 'open': 9820, 'high': 10010, 'low': 9350, 'close': 9870, 'volume': 100}])
    macro._sync_daily_kis('000001')
    assert feed.verified_regular_close('000001', '2026-11-04') is None
    quote = {'price': 10070, 'change': 320}
    feed._apply_regular_close(quote, '000001', '2026-11-04')
    assert quote['regular_close_pending'] and quote['price'] == 10070


def test_missing_close_check_is_throttled_and_normalizes_cached_quote(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    db.init_db()
    calls = []
    monkeypatch.setattr(macro, '_sync_daily_kis', lambda c, **kw: calls.append(c))
    monkeypatch.setattr(feed, '_confirm_closing_minute', lambda *a: None)
    now = datetime(2026, 11, 4, 17, 0)
    feed._ensure_regular_closes('000001', now)
    feed._ensure_regular_closes('000001', now)
    assert calls == ['000001', 'KOSDAQ']
    for c in ('000001', 'KOSDAQ'):
        db.set_state(f'regular_close:{c}:2026-11-04', json.dumps({'date': '2026-11-04', 'source': 'kis_regular_minute', 'asof': '2026-11-04T15:30',
            'confirmed_at': '2026-11-04T17:00:00', 'close': 9870, 'base': 9750, 'open': 9820, 'high': 10010, 'low': 9350, 'volume': 100}))
    db.set_state('quote:000001', json.dumps({'price': 10070, 'change': 320, 'traded_at': '2026-11-04T15:30:00', 'regular_close_pending': True}))
    feed._ensure_regular_closes('000001', datetime(2026, 11, 4, 17, 1))
    assert calls == ['000001', 'KOSDAQ']
    quote = json.loads(db.get_state('quote:000001')['value'])
    assert quote['price'] == 9870 and not quote.get('regular_close_pending')


def test_daily_current_price_cannot_replace_timed_close(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    db.init_db()
    clock = [datetime(2026, 11, 5, 16, 40)]
    monkeypatch.setattr(feed, 'kst_now', lambda: clock[0])
    for code in ('000001', 'KOSDAQ'):
        db.set_state(f'regular_close:{code}:2026-11-05', json.dumps({
            'date': '2026-11-05', 'source': 'kis_daily',
            'confirmed_at': '2026-11-05T15:31:00', 'close': 9140}))
    db.set_state('quote:000001', json.dumps({'price': 9060, 'traded_at': '2026-11-05T15:30:00'}))
    db.set_state('quote:KOSDAQ', json.dumps({'price': 682, 'traded_at': '2026-11-05T15:30:00'}))
    with db.connect() as conn:
        conn.execute("INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume,source) VALUES('000001','2026-11-05T15:30',9170,9170,9170,9170,0,'kis')")
    calls = []
    def daily(code, start, end, **kwargs):
        calls.append((code, start, end))
        if kwargs.get('index'):
            return [dict(date='2026-11-04', open=714.95, high=714.95, low=714.95, close=714.95, volume=10),
                    dict(date='2026-11-05', open=710, high=715, low=675, close=682, volume=1000)]
        return [dict(date='2026-11-04', open=9870, high=9870, low=9870, close=9870, volume=10),
                dict(date='2026-11-05', open=9740, high=9980, low=8950, close=9060, volume=64778)]
    monkeypatch.setattr(kis, 'daily_range', daily)
    point = dict(ts='2026-11-05T15:30', open=9140, high=9140, low=9140, close=9140, volume=262)
    monkeypatch.setattr(kis, 'minutes', lambda *a, **kw: [point])
    monkeypatch.setattr(kis, 'index_minutes', lambda *a, **kw: [dict(point, close=678.41)])
    feed._ensure_regular_closes('000001', clock[0])
    quote = json.loads(db.get_state('quote:000001')['value'])
    assert quote['price'] == 9140 and quote['change'] == -730
    assert quote['regular_close']['confirmed_at'] == '2026-11-05T16:40:00'
    index = json.loads(db.get_state('quote:KOSDAQ')['value'])
    assert index['price'] == 678.41
    assert index['change_pct'] == pytest.approx((678.41 / 714.95 - 1) * 100)
    macro.store_daily_kis('000001', daily('000001', '2026-11-04', '2026-11-05'))
    calls.pop()
    with db.connect() as conn:
        assert conn.execute("SELECT close FROM daily_bars WHERE symbol='000001' AND date='2026-11-05'").fetchone()[0] == 9140
    assert len(calls) == 2
    assert all(start == '2026-11-04' and end == '2026-11-05' for _, start, end in calls)
    clock[0] = datetime(2026, 11, 5, 16, 49)
    feed._ensure_regular_closes('000001', clock[0])
    assert len(calls) == 2
    clock[0] = datetime(2026, 11, 5, 16, 50)
    feed._ensure_regular_closes('000001', clock[0])
    assert len(calls) == 4
    clock[0] = datetime(2026, 11, 5, 18, 35)
    feed._ensure_regular_closes('000001', clock[0])
    assert len(calls) == 6
    clock[0] = datetime(2026, 11, 5, 22, 0)
    feed._ensure_regular_closes('000001', clock[0])
    assert len(calls) == 6
    with db.connect() as conn:
        assert tuple(conn.execute("SELECT close,volume FROM minute_bars WHERE symbol='000001'").fetchone()) == (9140, 262)
    macro.store_daily_kis('000001', daily('000001', '2026-11-04', '2026-11-05') + [
        dict(date='2026-11-06', open=9300, high=9300, low=9300, close=9300, volume=1)])
    with db.connect() as conn:
        assert conn.execute("SELECT base FROM daily_bars WHERE symbol='000001' AND date='2026-11-06'").fetchone()[0] == 9140


def test_missing_closing_minute_never_promotes_daily_price(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    db.init_db()
    macro.store_daily_kis('000001', [dict(date='2026-11-05', open=9740, high=9980, low=8950, close=9060, volume=64778)])
    db.set_state('regular_close:000001:2026-11-05', json.dumps({
        'date': '2026-11-05', 'close': 9060, 'source': 'kis_daily', 'confirmed_at': '2026-11-05T17:00:00'}))
    assert feed.verified_regular_close('000001', '2026-11-05') is None
    monkeypatch.setattr(kis, 'minutes', lambda *a: [dict(ts='2026-11-05T15:29', close=9170, volume=0)])
    feed._confirm_closing_minute('000001', datetime(2026, 11, 5, 17, 5))
    assert feed.verified_regular_close('000001', '2026-11-05') is None
    q = dict(price=9060)
    feed._apply_regular_close(q, '000001', '2026-11-05')
    assert q['regular_close_pending'] and not q.get('regular_close')


def test_after_market_uses_current_price_and_no_new_trade_is_not_shown(tmp_path, monkeypatch):
    # 2026-09-14부터 시간외 단일가 폐지 → 애프터마켓(16:00~20:00). 한투 현재가가 그 가격 (2026-11-05 실측: 종가 9,140 → 현재가 9,190)
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    db.init_db()
    monkeypatch.setattr(kis, 'overtime', lambda *a: (_ for _ in ()).throw(AssertionError('없어진 시간외 단일가는 묻지 않음')), raising=False)
    now, rc = datetime(2026, 11, 5, 17, 0), dict(close=9140, volume=1000)
    after = feed._kis_after('000001', now, True, rc, dict(price=9190, volume=1008))
    assert (after['price'], after['change'], after['session']) == (9190, 50, 'krx_after')
    assert json.loads(db.get_state('after_status:000001')['value'])['state'] == 'ok'
    after = feed._kis_after('000001', now, True, rc, dict(price=9140, volume=1000))     # 종가 그대로·거래량 그대로 = 새 거래 없음
    assert after['price'] == 9190, 'retain previously confirmed trades'
    assert json.loads(db.get_state('after_status:000001')['value'])['state'] == 'unavailable'
    feed._kis_after('000001', now, True, rc, None)                                       # 현재가를 못 받음
    assert json.loads(db.get_state('after_status:000001')['value'])['state'] == 'error'


def test_cached_naver_overtime_is_removed_even_after_query_window(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    db.init_db()
    for hour in (17, 20):
        db.set_state('after:000001', json.dumps(dict(date='2026-11-05', price=9130, volume=100, source='naver', session='afterMarket')))
        assert feed._kis_after('000001', datetime(2026, 11, 5, hour, 0), True, dict(close=9140, volume=100), dict(price=9140, volume=100)) is None
        assert json.loads(db.get_state('after:000001')['value']) is None
