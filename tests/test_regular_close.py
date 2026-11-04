"""Synthetic data: verified closing price must not fabricate minute OHLC or volume."""
import json
from datetime import datetime
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
    macro._sync_daily_kis('000001')
    final = feed.verified_regular_close('000001', '2026-11-04')
    assert final['close'] == 9870 and final['base'] == 9750
    with db.connect() as conn:
        conn.execute("INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume,source) VALUES('000001','2026-11-04T15:30',9910,9910,9910,9910,0,'kis')")
    def quote(*args):
        calls.append('quote')
        return {'price': 10070, 'change': 320, 'rate': 3.28, 'open': 9820, 'high': 10110, 'low': 9350, 'volume': 30000}
    monkeypatch.setattr(kis, 'quote', quote)
    monkeypatch.setattr(kis, 'index_quote', lambda: {'price': 715, 'change': 10, 'rate': 1.5})
    monkeypatch.setattr(kis, 'overtime', lambda code: {'price': 0, 'volume': 0})
    feed.update_quotes('000001')
    out = json.loads(db.get_state('quote:000001')['value'])
    assert out['price'] == 9870 and out['change'] == 120 and out['high'] == 10010
    assert out['traded_at'] == '2026-11-04T15:30:00' and out['regular_close']['source'] == 'kis_daily'
    assert calls == ['daily', 'quote'], 'reuse collection; no added daily/quote API requests'
    import webserver as ws
    monkeypatch.setattr(ws, 'load_settings', lambda: {'symbol': '000001'})
    payload = ws.api_intraday({'date': '2026-11-04'})
    assert payload['close_point'] == {'t': '2026-11-04T15:30', 'c': 9870, 'source': 'kis_daily'}
    assert payload['bars'][-1]['c'] == 9910 and payload['bars'][-1]['v'] == 0
    with db.connect() as conn:
        assert tuple(conn.execute("SELECT close,volume FROM minute_bars WHERE symbol='000001'").fetchone()) == (9910, 0)
    db.set_state('after:000001', json.dumps({'date': '2026-11-04', 'price': 9950, 'change': -120, 'rate': -0.01, 'at': '17:00'}))
    after = feed._kis_after('000001', datetime(2026, 11, 4, 20, 0), True, 9870)
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
