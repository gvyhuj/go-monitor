from datetime import datetime, timedelta

import pytest

from core import db, yahoo
import webserver


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 't.db')
    db.init_db()


def bars(sym, interval, values):
    with db.connect() as conn:
        conn.executemany('INSERT INTO coarse_bars(symbol,interval,ts,close) VALUES (?,?,?,?)',
                         [(sym, interval, t, c) for t, c in values])


def test_missing_endpoints_are_not_filled_with_last_price(database):
    for sym, base in [('X', 100), ('KOSDAQ', 1000)]:
        bars(sym, 5, [('2026-08-14T09:50', base), ('2026-08-14T10:25', base * 1.03)])
    r = yahoo.reaction('X', '2026-08-14T10:00', '2026-08-14', 95, 950)
    assert [x['minutes'] for x in r['rows']] == [30]


def test_chart_uses_completed_hourly_bar_and_same_base_as_table(database):
    for sym, base in [('X', 100), ('KOSDAQ', 1000)]:
        bars(sym, 60, [('2026-04-22T09:00', base), ('2026-04-22T10:00', base * 1.1)])
    r = yahoo.reaction('X', '2026-04-22T10:01', '2026-04-22', 95, 950)
    chart = webserver._reaction_chart('X', {'reaction_day': '2026-04-22'}, None, r)
    point = next(p for p in chart['own'] if p['t'] == '2026-04-22T10:00')
    assert point['c'] == chart['base_own'] == 100
    endpoint = next(p for p in chart['own'] if p['t'] == '2026-04-22T11:00')
    assert endpoint['c'] / chart['base_own'] - 1 == pytest.approx(endpoint['c']/100-1)


def test_short_window_keeps_stock_but_not_hourly_index(database):
    start = datetime(2026, 4, 22, 9)
    with db.connect() as conn:
        conn.executemany('INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume) VALUES (?,?,0,0,0,?,0)',
                         [('X', (start + timedelta(minutes=i)).isoformat(timespec='minutes'), 100 + i) for i in range(181)])
    bars('KOSDAQ', 60, [('2026-04-22T09:00', 1000), ('2026-04-22T10:00', 1010)])
    r = yahoo.reaction('X', '2026-04-22T10:00', '2026-04-22', 95, 950)
    thirty = next(x for x in r['rows'] if x['minutes'] == 30)
    assert thirty['own'] == pytest.approx(190 / 159 - 1)
    assert thirty['kosdaq'] is None
    sixty = next(x for x in r['rows'] if x['minutes'] == 60)
    assert sixty['kosdaq'] is None  # 10:00 is not before the publication.


def test_late_reaction_uses_daily_close_for_table_and_chart(database):
    for sym, base in [('X', 100), ('KOSDAQ', 1000)]:
        bars(sym, 5, [('2026-08-12T14:55', base * .9), ('2026-08-13T09:25', base * 1.06)])
        with db.connect() as conn:
            conn.execute('INSERT INTO daily_bars(symbol,date,close) VALUES (?,?,?)', (sym, '2026-08-12', base))
            conn.execute('INSERT INTO daily_bars(symbol,date,close) VALUES (?,?,?)', (sym, '2026-08-13', base * 1.07))
    r = yahoo.reaction('X', '2026-08-12T15:13', '2026-08-12', 95, 950)
    chart = webserver._reaction_chart('X', {'reaction_day': '2026-08-12'}, None, r)
    assert r['rows'][0]['own'] == pytest.approx(.06)
    assert chart['base_own'] == 100


def test_absent_next_trading_day_is_not_skipped(database):
    with db.connect() as conn:
        conn.executemany('INSERT INTO daily_bars(symbol,date,close) VALUES (?,?,?)',
                         [('X', '2026-08-12', 100), ('X', '2026-08-13', 100), ('X', '2026-08-14', 110)])
    bars('X', 5, [('2026-08-14T09:25', 110)])
    assert yahoo.reaction('X', '2026-08-12T15:13', '2026-08-12', 95, 950) is None


def test_open_base_does_not_use_index_close_from_an_older_day(database):
    with db.connect() as conn:
        conn.executemany('INSERT INTO daily_bars(symbol,date,close) VALUES (?,?,?)',
                         [('X', '2026-08-13', 100), ('KOSDAQ', '2026-08-12', 1000)])
    bars('X', 5, [('2026-08-14T09:25', 110)])
    bars('KOSDAQ', 5, [('2026-08-14T09:25', 1010)])
    r = yahoo.reaction('X', '2026-08-14T08:00', '2026-08-14', 100, 1000)
    assert r['rows'][0]['own'] == pytest.approx(.1)
    assert r['rows'][0]['kosdaq'] is None


def test_native_minute_window_does_not_reuse_stale_endpoint(database):
    from core import causal
    with db.connect() as conn:
        conn.executemany('INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume) VALUES (?,?,0,0,0,?,0)',
                         [('X','2026-08-14T10:00',100),('X','2026-08-14T10:30',105)])
    start = datetime(2026,8,14,10)
    assert causal.window_return('X', start.date(), start + timedelta(minutes=30), start) == pytest.approx(.05)
    assert causal.window_return('X', start.date(), start + timedelta(minutes=60), start) is None


def test_intraday_stats_keep_observed_stock_when_later_index_is_missing(database, monkeypatch):
    from core import articles, causal
    start = datetime(2026, 8, 14, 9)
    with db.connect() as conn:
        for sym, count in [('X', 181), ('KOSDAQ', 91)]:
            conn.executemany('INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume) VALUES (?,?,0,0,0,?,0)',
                             [(sym, (start + timedelta(minutes=i)).isoformat(timespec='minutes'), 100+i) for i in range(count)])
    monkeypatch.setattr(articles, 'decompose', lambda *a: {'beta_market':1,'parts':[]})
    monkeypatch.setattr(causal, 'abnormal_rarity', lambda *a, **kw: {'ok':True,'p':.5,'n':100})
    w = articles.intraday_reaction('X', {'reaction_day':'2026-08-14','base_ts':'2026-08-14T10:00'}, True)
    row = next(r for r in w['rows'] if r['minutes']==60)
    chart = webserver._reaction_chart('X', {'reaction_day':'2026-08-14'}, w, None)
    assert row['own'] == pytest.approx(220/159-1)
    assert row['kosdaq'] is None and row['abnormal'] is None
    p = next(p['c'] for p in chart['own'] if p['t']=='2026-08-14T11:00')
    assert p/chart['base_own']-1 == pytest.approx(row['own'])
