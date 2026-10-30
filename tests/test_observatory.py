"""Acceptance fixtures are synthetic and must never be deployed as production prices."""
from datetime import datetime,timedelta
import json,base64
import pytest
from core import db,observatory as ob,yahoo,sitebuild,ghinbox

@pytest.fixture
def database(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path);monkeypatch.setattr(db,'DB_PATH',tmp_path/'t.db');db.init_db()
    with db.connect() as conn:
        yield conn

def minute(c,s,t,p):
    c.execute('INSERT OR REPLACE INTO minute_bars(symbol,ts,open,high,low,close,volume,source) VALUES (?,?, ?,?,?,?,1,?)',(s,t,p,p,p,p,'fixture'))

def prices(c):
    for s,p in [('X',100),('KOSDAQ',1000)]:
        for time,m in [('09:27',1),('09:28',1.02),('09:58',1.03),('10:28',1.06)]:
            minute(c,s,'2026-10-06T'+time,p*m)

def row(a='a',title='광학미러 공급 계약',ts='2026-10-06T09:28'):
    return {'id':a,'title':title,'ts':ts,'kind':'ext','time_quality':'ok'}

def article(c,a,story=None):
    if story and story != a:
        c.execute('INSERT OR IGNORE INTO article_links(a,b) VALUES(?,?)',(a,story))
    c.execute('INSERT INTO articles(id,symbol,ts,title,story,url,time_from) VALUES (?,?,?,?,?,?,?)',(a,'X','2026-10-06T09:28','광학미러 공급 계약',story,'https://example.org/'+a,'page'))

def event(c,a='a'):
    return ob.ensure_event(c,a,'fixture','manual')

def test_T01_pre_publication_base(database):
    prices(database);o=ob.observe(database,'X','2026-10-06T09:28')['intraday']
    assert o['base_own']==100 and o['base_own_time']=='2026-10-06T09:27'
    assert o['base_lag_minutes']==1 and o['steps']['30']['own']==pytest.approx(.03)

def test_T02_stale_base_rejected(database):
    prices(database);database.execute("DELETE FROM minute_bars WHERE ts LIKE '%09:27'")
    minute(database,'X','2026-10-06T09:20',100)
    o=ob.observe(database,'X','2026-10-06T09:28')['intraday']
    assert o['base_own'] is None and o['steps']['30']['own'] is None

@pytest.mark.parametrize('iv',[5,60])
def test_T03_completed_coarse(iv):
    p=yahoo.completed_points([('2026-10-06T09:00',100)],iv)
    assert p[0][0]==(datetime(2026,10,6,9)+timedelta(minutes=iv)).isoformat(timespec='minutes')

def test_T04_missing_endpoint(database):
    prices(database);database.execute("DELETE FROM minute_bars WHERE ts LIKE '%09:58'")
    o=ob.observe(database,'X','2026-10-06T09:28')['intraday']['steps']
    assert o['30']['own'] is None and o['60']['own']==pytest.approx(.06)

def test_T05_missing_index_endpoint(database):
    prices(database);database.execute("DELETE FROM minute_bars WHERE symbol='KOSDAQ' AND ts LIKE '%09:58'")
    s=ob.observe(database,'X','2026-10-06T09:28')['intraday']['steps']['30']
    assert s['own']==pytest.approx(.03) and s['kq'] is None and s['diff'] is None

@pytest.mark.parametrize('ts,day,mode',[('2026-10-06T08:00','2026-10-06','open'),('2026-10-06T15:01','2026-10-07','late'),('2026-10-10T12:00','2026-10-12','open')])
def test_T06_exchange_calendar(database,ts,day,mode):
    for d in ['2026-10-05','2026-10-06','2026-10-07','2026-10-12']:
        for s,p in [('X',100),('KOSDAQ',1000)]: database.execute('INSERT INTO daily_bars(symbol,date,close) VALUES(?,?,?)',(s,d,p))
    o=ob.observe(database,'X',ts);assert o['day']==day and o['mode']==mode and o['start'].endswith('09:00:00')

def test_T07_mismatched_base_times(database):
    prices(database);database.execute("DELETE FROM minute_bars WHERE symbol='KOSDAQ'")
    database.executemany('INSERT INTO coarse_bars(symbol,interval,ts,close) VALUES (?,?,?,?)',[('KOSDAQ',5,'2026-10-06T09:20',1000),('KOSDAQ',5,'2026-10-06T09:53',1010)])
    f=ob.observe(database,'X','2026-10-06T09:28')['intraday']
    assert f['base_kq_time']=='2026-10-06T09:25' and not f['comparable']
    assert f['steps']['30']['kq']==pytest.approx(.01) and f['steps']['30']['diff'] is None

def test_T08_explicit_story_and_disclosure_merge(database):
    for a in ['a','b','c']:article(database,a,'a')
    ds,_=ob.build(database,'X',[row('a'),row('b'),row('c')]);assert len(ds)==1
    eid=next(iter(ds));did=event(database,'d123')
    ob.mutate(database,{'type':'news_event_link','event_id':eid,'other_id':did,'reason':'같은 계약 공시 확인'})
    assert ob.resolve(database,did)==eid
    assert database.execute('SELECT COUNT(*) FROM news_event_members WHERE event_id=?',(eid,)).fetchone()[0]==4

def test_T09_same_day_separate_contracts(database):
    ds,_=ob.build(database,'X',[row('a','계약 A'),row('b','계약 B')]);assert len(ds)==2

def test_T10_representative_change_notes_and_alias(database):
    article(database,'a','a');article(database,'b','a')
    ds,_=ob.build(database,'X',[row('a')]);eid=next(iter(ds))
    ob.mutate(database,{'type':'news_event_note','event_id':eid,'body':'당시 기록','kind':'ai_analysis'})
    ds,_=ob.build(database,'X',[row('b','변경된 제목')]);assert next(iter(ds))==eid and ds[eid]['notes'][0]['body']=='당시 기록'

def test_T11_unknown_time(database):
    prices(database);r=row();r['untimed']=True
    ds,_=ob.build(database,'X',[r]);d=next(iter(ds.values()));assert d['first_public_at'] is None and d['observation']['intraday'] is None

def daily(c,count=60):
    start=datetime(2026,8,3);days=[]
    while len(days)<count:
        if start.weekday()<5:days.append(start.date().isoformat())
        start+=timedelta(days=1)
    for i,d in enumerate(days):
        for s,p in [('X',100+i),('KOSDAQ',1000+i)]:c.execute('INSERT INTO daily_bars(symbol,date,close) VALUES(?,?,?)',(s,d,p))
    return days

def test_T12_five_twenty_actual_ranges(database):
    days=daily(database);a=ob.daily_window(database,'X',days[25],5);b=ob.daily_window(database,'X',days[25],20)
    assert len(a['points'])==11 and len(b['points'])==41 and a['base_day']==days[24]
    assert a['post']['own']==pytest.approx(130/124-1) and b['post']['own']==pytest.approx(145/124-1)

def test_T13_future_pending(database):
    days=daily(database,8);w=ob.daily_window(database,'X',days[-1],20)
    assert w['status']=='pending' and w['post']['own'] is None and max(p['offset'] for p in w['points'])==0

def test_T14_summary_chart_same_observation(database):
    prices(database);rows=[row()];ds,_=ob.build(database,'X',rows);d=next(iter(ds.values()))
    step=rows[0]['steps']['30']['own'];point=next(p[1] for p in rows[0]['path']['own'] if p[0]==30)
    assert step==point==d['observation']['intraday']['steps']['30']['own']

def test_T17_append_only_note_revision_and_idempotency(database):
    eid=event(database)
    a={'type':'news_event_note','event_id':eid,'body':'당시 AI 기록','kind':'ai_analysis','request_id':'req-1','analysis_at':'2026-10-06T11:00','snapshot':{'version':'v1'}}
    ob.mutate(database,a);ob.mutate(database,a)
    ob.mutate(database,{**a,'request_id':'req-2','body':'사후 확인','kind':'retrospective_review','revision_parent_id':'req-1'})
    records=database.execute('SELECT payload FROM news_event_notes ORDER BY id').fetchall()
    assert len(records)==2 and json.loads(records[0][0])['body']=='당시 AI 기록'
    assert json.loads(records[1][0])['revision_parent_id']=='req-1'

def test_T19_migration_keeps_existing_rows(database):
    prices(database);article(database,'a');n=database.execute('SELECT count(*) FROM minute_bars').fetchone()[0]
    ob.migrate(database);ob.migrate(database)
    assert database.execute('SELECT count(*) FROM minute_bars').fetchone()[0]==n
    assert database.execute('SELECT count(*) FROM articles').fetchone()[0]==1

def test_T20_encrypted_static_and_inbox(database,tmp_path):
    eid=event(database);item={'type':'news_event_note','event_id':eid,'body':'PRIVATE NOTE SENTINEL','kind':'ai_analysis'}
    key=sitebuild.derive_key('synthetic-test-password',b'test-salt-only')
    sealed=base64.b64encode(sitebuild.encrypt(key,json.dumps(item).encode())).decode()
    issue={'title':'[요청] test','body':'```enc\n'+sealed+'\n```'}
    parsed=ghinbox.parse(issue,lambda text:json.loads(sitebuild.decrypt(key,base64.b64decode(text))))
    ob.mutate(database,parsed)
    output=tmp_path/'site';sitebuild.build(output,'synthetic-test-password',b'test-salt-only', [('observation:'+eid,json.dumps(item),False)])
    for f in output.rglob('*'):
        if f.is_file():assert b'PRIVATE NOTE SENTINEL' not in f.read_bytes()
    assert json.loads(sitebuild.read_item(key,(output/'d'/sitebuild.file_name('observation:'+eid)).read_bytes()))==item

def test_T22_missing_market_not_zero(database,monkeypatch):
    from core import causes,articles
    monkeypatch.setattr(articles,'decompose',lambda *a:None)
    assert causes._decompose_window('X',datetime(2026,10,6).date(),.05,None,[{'code':'Y','name':'Y','change':.01}],False) is None
    assert causes._decompose_window('X',datetime(2026,10,6).date(),.05,.01,[],False) is None

def test_T24_independent_decimal_price_reproduction(database):
    from decimal import Decimal
    prices(database);s=ob.observe(database,'X','2026-10-06T09:28')['intraday']['steps']['60']
    expected=float(Decimal('106')/Decimal('100')-1)
    assert s['own']==pytest.approx(expected,abs=1e-12)

def test_split_preserves_original_notes_and_members(database):
    article(database,'a','a');article(database,'b','a');ds,_=ob.build(database,'X',[row('a')]);eid=next(iter(ds))
    ob.mutate(database,{'type':'news_event_split','event_id':eid,'member_id':'b','reason':'서로 다른 계약'})
    assert database.execute('SELECT COUNT(DISTINCT event_id) FROM news_event_members').fetchone()[0]==2

def test_revision_snapshots_only_change_when_observations_change(database):
    prices(database);ob.build(database,'X',[row()]);ob.build(database,'X',[row()])
    assert database.execute('SELECT COUNT(*) FROM news_event_observations').fetchone()[0]==1
    minute(database,'X','2026-10-06T09:58',105);ob.build(database,'X',[row()])
    assert database.execute('SELECT COUNT(*) FROM news_event_observations').fetchone()[0]==2

def test_T23_documentation_matches_disabled_event_study():
    from pathlib import Path
    from core import articles
    assert articles.EVENT_STUDY is False
    readme=Path('README.md').read_text('utf8')
    assert 'EVENT_STUDY = False' in readme and '단순 KOSDAQ 대비 차이(%p)' in readme
    assert '기사별 사건연구/유의성 판정은 비활성' in readme

def test_source_boundaries_are_retained_in_summary(database):
    prices(database)
    database.execute("UPDATE minute_bars SET source='different_feed' WHERE ts LIKE '%09:58'")
    rows=[row()];ob.build(database,'X',rows)
    assert next(p for p in rows[0]['path']['own'] if p[0]==30)[3]=='different_feed'

def test_invalid_note_parent_cannot_attach_to_other_event(database):
    eid=event(database)
    with pytest.raises(ValueError,match='수정 대상'):
        ob.mutate(database,{'type':'news_event_note','event_id':eid,'kind':'ai_analysis','body':'x','revision_parent_id':'nonexistent'})

def test_legacy_identity_and_ai_records_survive_candidate_change(database):
    for a in ['a','b']: article(database,a)
    eid=event(database,'a')
    ob.ensure_event(database,'b','old auto group','story:a',['a'])
    ob.mutate(database,{'type':'news_event_note','event_id':eid,'kind':'ai_analysis','body':'preserve'})
    ds,_=ob.build(database,'X',[row('a'),row('b')])
    assert ds[eid]['identity_status']=='review_required'
    assert ds[eid]['notes'][0]['body']=='preserve'
    assert len(ds[eid]['members'])==2

def test_earlier_external_article_does_not_reclassify_press_release(database):
    article(database,'a');article(database,'b','a')
    database.execute("UPDATE articles SET ts='2026-10-06T10:00',kind_override='pr' WHERE id='b'")
    rows=[row('a')]
    ds,_=ob.build(database,'X',rows);d=next(iter(ds.values()))
    assert rows[0]['kind']=='pr'
    assert d['event_kind']=='company_press_release'
    assert d['first_public_at']=='2026-10-06T09:28'
    assert d['pr_distributed_at'] is None
    ob.mutate(database,{'type':'news_event_meta','event_id':d['event_id'],'pr_distributed_at':'2026-10-06T09:50','time_source':'당사 배포 기록'})
    ds,_=ob.build(database,'X',[row('a')])
    assert ds[d['event_id']]['pr_distributed_at']=='2026-10-06T09:50:00'

@pytest.mark.parametrize('own,kq,sec,bm,bs',[(.04,.01,.015,1,1),(.01,.03,.02,2,1),(0,.02,.01,0,0),(-.01,.02,-.02,1,1)])
def test_unbounded_contributions_and_zero_coefficients(database,monkeypatch,own,kq,sec,bm,bs):
    from core import causes,articles
    monkeypatch.setattr(articles,'decompose',lambda *a:{'beta_market':bm,'beta_sector':bs,'beta_sector_market':0})
    d=causes._decompose_window('X',datetime(2026,10,6).date(),own,kq,[{'code':x,'name':x,'change':sec} for x in ['A','B']],False)
    assert d['market']==pytest.approx(bm*kq)
    assert d['sector']==pytest.approx(bs*sec)
    assert sum(d[k] for k in ['market','sector','specific'])==pytest.approx(own)
    text=causes._desk(d,None,None,False,[],{"ok":False,"text":"없음"},None,None,None,None,1)[0]['text']
    assert text.count('%p')==3 and '추정' in text and '원인을 확정하지' in text

def test_similar_titles_are_grouped_as_before(database):
    # 사용자가 쓰던 묶음 방식 유지: 제목이 비슷한 같은 소식은 한 묶음(story)
    from core import articles
    for a in ['a','b']: article(database,a)
    database.commit()
    articles.group_stories('X')
    assert database.execute('SELECT COUNT(DISTINCT story) FROM articles').fetchone()[0]==1

@pytest.mark.parametrize('kind',ob.KINDS)
def test_confirmed_source_classification_is_retained(database,kind):
    article(database,'a')
    eid=event(database,'a')
    ob.mutate(database,{'type':'news_event_meta','event_id':eid,'event_kind':kind,
                        'publisher':'확인 발행 주체','primary_source_url':'https://example.org/source'})
    ds,_=ob.build(database,'X',[row('a')])
    assert ds[eid]['event_kind']==kind
    assert ds[eid]['primary_source_url']=='https://example.org/source'
    assert ds[eid]['history'][0]['action']=='news_event_meta'

def test_automatic_company_article_keeps_press_release_classification(database):
    article(database,'a');article(database,'b')
    rows=[{**row('a'),'kind':'company'},row('b')]
    ds,_=ob.build(database,'X',rows)
    assert [r['kind'] for r in rows]==['company','ext']
    assert {d['event_kind'] for d in ds.values()}=={'company_press_release','external_article'}

def test_explicit_event_kind_still_overrides_row_kind(database):
    article(database,'a')
    eid=event(database,'a')
    ob.mutate(database,{'type':'news_event_meta','event_id':eid,'event_kind':'external_article'})
    rows=[{**row('a'),'kind':'company'}]
    ds,_=ob.build(database,'X',rows)
    assert rows[0]['kind']=='ext' and ds[eid]['event_kind']=='external_article'

def test_three_kinds_only():
    assert ob.KINDS==('company_press_release','external_article','disclosure')

def test_legacy_company_announcement_reads_as_external_article_and_is_preserved(database):
    article(database,'a')
    eid=event(database,'a')
    # A record saved by the earlier four-kind build, written directly so the stored value is untouched.
    database.execute('UPDATE news_events SET metadata=? WHERE id=?',(json.dumps({'event_kind':'external_company_announcement','publisher':'고객사'}),eid))
    database.execute("INSERT INTO news_event_history(event_id,changed_at,action,payload) VALUES(?,?,?,?)",
                     (eid,'2026-10-20T10:00:00+09:00','news_event_meta',json.dumps({'before':{},'after':{'event_kind':'external_company_announcement'}})))
    database.execute("UPDATE articles SET kind_override='external_company_announcement' WHERE id='a'")
    rows=[row('a')]
    ds,_=ob.build(database,'X',rows)
    assert ds[eid]['event_kind']=='external_article' and rows[0]['kind']=='ext'
    assert ds[eid]['legacy_event_kind']=='external_company_announcement'
    assert ds[eid]['publisher']=='고객사'
    assert {m['kind'] for m in ds[eid]['members']}=={'ext'}
    stored=database.execute('SELECT metadata FROM news_events WHERE id=?',(eid,)).fetchone()[0]
    assert json.loads(stored)['event_kind']=='external_company_announcement'
    assert database.execute("SELECT kind_override FROM articles WHERE id='a'").fetchone()[0]=='external_company_announcement'
    assert any('external_company_announcement' in h['payload'] for h in ds[eid]['history'])

def test_legacy_kind_request_is_saved_as_external_article(database):
    article(database,'a')
    eid=event(database,'a')
    ob.mutate(database,{'type':'news_event_meta','event_id':eid,'event_kind':'external_company_announcement'})
    meta=json.loads(database.execute('SELECT metadata FROM news_events WHERE id=?',(eid,)).fetchone()[0])
    assert meta['event_kind']=='external_article'
    change=json.loads(database.execute("SELECT payload FROM news_event_history WHERE event_id=? AND action='news_event_meta'",(eid,)).fetchone()[0])
    assert change['requested_event_kind']=='external_company_announcement'
    with pytest.raises(ValueError):
        ob.mutate(database,{'type':'news_event_meta','event_id':eid,'event_kind':'unknown_kind'})

def test_archived_legacy_record_reads_as_external_article(database):
    article(database,'a')
    eid=event(database,'a')
    database.execute('INSERT OR REPLACE INTO news_event_records VALUES(?,?)',(eid,json.dumps(
        {'event_id':eid,'event_kind':'external_company_announcement','members':[{'id':'a','kind':'external_company_announcement'}],'notes':[]})))
    ds,_=ob.build(database,'X',[])
    assert ds[eid]['archived_source'] and ds[eid]['event_kind']=='external_article'
    assert ds[eid]['legacy_event_kind']=='external_company_announcement' and ds[eid]['members'][0]['kind']=='ext'

def test_link_preserves_press_metadata_and_distribution(database):
    article(database,'a');article(database,'b')
    a=event(database,'a');b=event(database,'b')
    ob.mutate(database,{'type':'news_event_meta','event_id':a,'event_kind':'external_article'})
    ob.mutate(database,{'type':'news_event_meta','event_id':b,'event_kind':'company_press_release',
        'pr_distributed_at':'2026-10-06T11:00','primary_source_url':'https://example.org/company'})
    ob.mutate(database,{'type':'news_event_link','event_id':a,'other_id':b,'reason':'원문 확인'})
    ds,_=ob.build(database,'X',[row('a'),row('b')])
    assert ds[a]['event_kind']=='company_press_release'
    assert ds[a]['pr_distributed_at']=='2026-10-06T11:00:00'
    assert ds[a]['identity_status']=='confirmed'
    assert any(x.get('primary_source_url')=='https://example.org/company' for x in ds[a]['classification_sources'])

def test_note_from_site_gets_stored_observation(database):
    # 사이트는 짧은 요청(스냅샷 버전만)을 보내고, 반영할 때 저장된 관측값을 기록에 붙임
    prices(database);article(database,'a')
    details,_=ob.build(database,'X',[row()])
    eid=next(iter(details))
    ob.mutate(database,{'type':'news_event_note','event_id':eid,'body':'AI 답','kind':'ai_analysis','request_id':'r-site','snapshot':{'version':'v9'}})
    saved=json.loads(database.execute("SELECT payload FROM news_event_notes WHERE id='r-site'").fetchone()[0])
    assert saved['snapshot']['version']=='v9'
    assert saved['snapshot']['observation']['intraday']['steps']['30']['own']==pytest.approx(.03)

def test_krx_close_not_after_hours_last_price(database):
    # 네이버 일봉 close = 넥스트레이드 시간외 포함 마지막 가격 → 분석은 거래소 정규장 종가(다음 날 기준가)로
    for d,c,b in [('2026-10-14',12600,12820),('2026-10-15',10900,12620),('2026-10-16',11660,11200)]:
        database.execute('INSERT INTO daily_bars(symbol,date,open,high,low,close,volume,base) VALUES (?,?,?,?,?,?,1,?)',('X',d,c,c,c,c,b))
    for d,c in [('2026-10-14',800),('2026-10-15',760),('2026-10-16',790)]:
        database.execute('INSERT INTO daily_bars(symbol,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,1)',('KOSDAQ',d,c,c,c,c))
    minute(database,'X','2026-10-16T15:30',11470); minute(database,'X','2026-10-16T19:59',11660)   # 시간외는 1분봉에 없지만 있어도 15:30 이후는 무시
    k = yahoo.krx_closes(database,'X')
    assert k['2026-10-14']==12620 and k['2026-10-15']==11200 and k['2026-10-16']==11470
    assert yahoo.krx_closes(database,'KOSDAQ')['2026-10-15']==760
    f = yahoo.reaction_frame(database,'X','2026-10-15',datetime(2026,10,15,9,0),'late',min_minutes=1)
    assert f['base_own']==12620 and f['base_basis']=='previous_krx_close'
    w = ob.daily_window(database,'X','2026-10-15',1)
    assert w['base_own']==12620 and [p['stock_price'] for p in w['points']]==[12620,11200,11470]


def test_peerfit_prefers_group_that_moves_with_stock(tmp_path, monkeypatch):
    import random
    from datetime import date, timedelta
    from core import db, peerfit
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path); monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'monitor.db'); db.init_db()
    rnd = random.Random(3)
    days, d = [], date(2026, 5, 1)
    while len(days) < 70:
        if d.weekday() < 5: days.append(d.isoformat())
        d += timedelta(days=1)
    own, kq = 10000.0, 800.0
    with db.connect() as conn:
        conn.execute("INSERT INTO stock_themes(code, theme_no, theme_name) VALUES ('000001', 7, '우주항공')")
        for day in days:
            m, th, ind = rnd.gauss(0, .01), rnd.gauss(0, .02), rnd.gauss(0, .01)
            kq *= 1 + m; own *= 1 + .5 * m + th + rnd.gauss(0, .003)
            for sym, v in (('000001', own), ('KOSDAQ', kq)):
                conn.execute("INSERT INTO daily_bars(symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,1)", (sym, day, v, v, v, v))
            conn.execute("INSERT INTO theme_daily(date, theme_no, name, change_rate) VALUES (?, 7, '우주항공', ?)", (day, (th + m) * 100))
            conn.execute("INSERT INTO industry_daily(date, industry_no, name, change_rate) VALUES (?, 284, '우주항공과국방', ?)", (day, (ind + m) * 100))
        res = peerfit.compare(conn, '000001')
    by = {r['name']: r for r in res['rows']}
    assert by['테마 우주항공']['corr_ex_market'] > .9 > by['WICS 우주항공과국방']['corr_ex_market'] + .5
    assert '테마 우주항공' in peerfit.summary_line(res)


def test_regular_ohlc_ignores_after_hours_and_history_bars(tmp_path, monkeypatch):
    import webserver as ws
    from core import db
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path); monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'monitor.db'); db.init_db()
    with db.connect() as conn:
        rows = [('2026-10-30T09:00', 10000, 10150, 9950, 'naver_today'), ('2026-10-30T12:20', 9720, 9720, 9700, 'naver_today'),
                ('2026-10-30T15:30', 9830, 9830, 9830, 'naver_today'), ('2026-10-30T16:12', 10300, 10300, 10300, 'naver_today'),
                ('2026-10-29T09:00', 10640, 10640, 10640, 'naver_history')]
        conn.executemany("INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume,source) VALUES ('000001',?,?,?,?,?,1,?)",
                         [(t, o, h, l, o, s) for t, o, h, l, s in rows])
        assert ws.regular_ohlc(conn, '000001') == {'2026-10-30': (10000, 10150, 9700)}


def test_kis_token_reused_and_trial_line(tmp_path, monkeypatch):
    import json as _json
    from datetime import datetime
    from core import db, kis
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path); monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'monitor.db'); db.init_db()
    monkeypatch.setenv('KIS_APP_KEY', 'k'); monkeypatch.setenv('KIS_APP_SECRET', 's')
    calls = []
    def fake(method, path, *, headers, params=None, body=None, timeout=10):
        calls.append(path)
        if path == '/oauth2/tokenP':
            return {'access_token': 'T1', 'access_token_token_expired': '2026-10-31 09:00:00'}
        assert headers['authorization'] == 'Bearer T1'
        if path.endswith('inquire-price'):
            return {'rt_cd': '0', 'output': {'stck_prpr': '9800', 'stck_oprc': '9800', 'stck_hgpr': '10250', 'stck_lwpr': '9700',
                                             'stck_sdpr': '10170', 'acml_vol': '39255', 'hts_avls': '1147', 'per': '23.50'}}
        if path.endswith('itemchartprice'):
            return {'rt_cd': '0', 'output2': [{'stck_bsop_date': '20261030', 'stck_cntg_hour': '153000', 'stck_prpr': '9830',
                                               'stck_oprc': '9830', 'stck_hgpr': '9830', 'stck_lwpr': '9830', 'cntg_vol': '227'}]}
        if path.endswith('index-price'):
            return {'rt_cd': '0', 'output': {'bstp_nmix_prpr': '760.10', 'bstp_nmix_prdy_ctrt': '-3.83'}}
        return {'rt_cd': '1', 'msg_cd': 'X', 'msg1': '없음'}
    monkeypatch.setattr(kis, '_http', fake)
    monkeypatch.setattr(kis, 'GAP', 0)
    with db.connect() as conn:
        conn.execute("INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume,source) VALUES ('000001','2026-10-30T15:30',9830,9830,9830,9830,227,'naver_today')")
    db.set_state('quote:000001', _json.dumps({'price': 9800, 'volume': 39255}))
    line = kis.trial('000001', datetime(2026, 10, 30, 16, 30))
    assert calls.count('/oauth2/tokenP') == 1
    kis.trial('000001', datetime(2026, 10, 30, 16, 40))
    assert calls.count('/oauth2/tokenP') == 1, '토큰은 다시 받지 않음'
    assert '현재가 한투 9800 / 네이버 9800' in line and '1분봉 1개(15:30~15:30) 중 네이버 종가 일치 1' in line
    assert 'KOSDAQ 한투 760.1' in line and '실패: 투자자' in line and 'T1' not in line
