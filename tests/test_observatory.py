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
    assert causes._decompose_window('X',datetime(2026,10,6).date(),.05,None,False) is None

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

@pytest.mark.parametrize('own,kq,bm',[(.04,.01,1),(.01,.03,2),(0,.02,0),(-.01,.02,1)])
def test_unbounded_contributions_and_zero_coefficients(database,monkeypatch,own,kq,bm):
    # 몫 나누기 = 시장 몫 + 고유 몫 (업종 몫은 계산하지 않음)
    from core import causes,articles
    monkeypatch.setattr(articles,'decompose',lambda *a:{'beta_market':bm})
    d=causes._decompose_window('X',datetime(2026,10,6).date(),own,kq,False)
    assert d['market']==pytest.approx(bm*kq) and 'sector' not in d
    assert d['market']+d['specific']==pytest.approx(own)
    text=causes._desk(d,None,None,False,[],{"ok":False,"text":"없음"},None,None,None,None,1)[0]['text']
    assert text.count('%p')==2 and '업종' not in text and '추정' in text and '원인을 확정하지' in text

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


def test_kis_token_reused_and_probe_line(tmp_path, monkeypatch):
    from datetime import datetime
    from core import db, kis
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path); monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'monitor.db'); db.init_db()
    monkeypatch.setenv('KIS_APP_KEY', 'k'); monkeypatch.setenv('KIS_APP_SECRET', 's')
    calls = []
    def fake(method, path, *, headers, params=None, body=None, timeout=10):
        calls.append(path)
        if path == '/oauth2/tokenP':
            return {'access_token': 'SECRETTOK', 'access_token_token_expired': '2026-10-31 09:00:00'}
        assert headers['authorization'] == 'Bearer SECRETTOK'
        if path.endswith('inquire-price'):
            return {'rt_cd': '0', 'output': {'stck_prpr': '9800', 'stck_sdpr': '10170', 'acml_vol': '39255', 'hts_avls': '1147', 'per': '47.12'}}
        if path.endswith('itemchartprice') and 'daily' not in path:
            return {'rt_cd': '0', 'output2': [{'stck_bsop_date': '20261030', 'stck_cntg_hour': '153000', 'stck_prpr': '9830',
                                               'stck_oprc': '9830', 'stck_hgpr': '9830', 'stck_lwpr': '9830', 'cntg_vol': '227'}]}
        if path.endswith('inquire-daily-itemchartprice'):
            return {'rt_cd': '0', 'output2': [{'stck_bsop_date': '20261029', 'stck_clpr': '10150', 'stck_oprc': '10640'},
                                              {'stck_bsop_date': '20261030', 'stck_clpr': '9800', 'stck_oprc': '9800'}]}
        if path.endswith('dailychartprice'):
            return {'rt_cd': '1', 'msg_cd': 'E', 'msg1': '조회 불가'}
        if path.endswith('indexchartprice'):
            return {'rt_cd': '0', 'output2': [{'stck_bsop_date': '20261030', 'bstp_nmix_prpr': '724.38', 'stck_cntg_hour': '153000'}]}
        if path.endswith('inquire-investor'):
            return {'rt_cd': '0', 'output': [{'stck_bsop_date': '20261030', 'prsn_ntby_qty': '4438'}]}
        return {'rt_cd': '1', 'msg_cd': 'X', 'msg1': '없음'}
    monkeypatch.setattr(kis, '_http', fake)
    monkeypatch.setattr(kis, 'GAP', 0)
    line = kis.probe('000001', datetime(2026, 10, 31, 1, 0))
    kis.probe('000001', datetime(2026, 10, 31, 2, 0))
    assert calls.count('/oauth2/tokenP') == 1, '토큰은 다시 받지 않음'
    assert kis.ISSUED[-1:] == ['2026-10-31T09:00'] and not any('SECRETTOK' in x for x in kis.ISSUED)   # 실행 기록용: 만료 시각만
    assert '현재가 9800 기준가 10170' in line and '일봉 최근 2개 2026-10-29~2026-10-30' in line
    assert '당일 1분봉 1개' in line and '실패: 지난 1분봉(7일 전)' in line and 'SECRETTOK' not in line


def test_kis_collection_quote_minutes_daily(tmp_path, monkeypatch):
    import json as _json
    from datetime import datetime
    from core import db, feed, kis, macro
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path); monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'monitor.db'); db.init_db()
    monkeypatch.setenv('KIS_APP_KEY', 'k'); monkeypatch.setenv('KIS_APP_SECRET', 's')
    now = datetime(2026, 11, 2, 10, 5)
    monkeypatch.setattr(feed, 'kst_now', lambda: now)
    monkeypatch.setattr(kis, 'quote', lambda code, market='J': {'price': 9900, 'change': 100, 'rate': 1.02, 'open': 9800, 'high': 9950,
        'low': 9780, 'base': 9800, 'volume': 1234, 'value': 12_300_000, 'market_cap_100m': 1159, 'per': 47.6, 'pbr': 1.77})
    monkeypatch.setattr(kis, 'index_quote', lambda no='1001': {'price': 730.1, 'change': 5.7, 'rate': 0.79})
    def minutes(code, hhmm, market='J'):     # 10:04까지 30개씩: 09:35~10:04, 09:05~09:34, 09:00~09:04
        end = datetime(2026, 11, 2, int(hhmm[:2]), int(hhmm[2:4]))
        out = []
        for i in range(30):
            t = end.replace(minute=0) if False else end - __import__('datetime').timedelta(minutes=i)
            if t.strftime('%H%M') < '0900':
                break
            out.append({'ts': t.strftime('%Y-%m-%dT%H:%M'), 'open': 9800, 'high': 9800, 'low': 9800, 'close': 9800 + i, 'volume': 1})
        return sorted(out, key=lambda b: b['ts'])
    monkeypatch.setattr(kis, 'minutes', minutes)
    n = feed.sync_today('000001', now=now)
    with db.connect() as conn:
        first, last, cnt, src = conn.execute("SELECT MIN(ts), MAX(ts), COUNT(*), MIN(source) FROM minute_bars WHERE symbol='000001'").fetchone()
    assert (first, last, cnt, src) == ('2026-11-02T09:00', '2026-11-02T10:04', 65, 'kis') and n == 65
    feed.update_quotes('000001')
    q = _json.loads(db.get_state('quote:000001')['value'])
    assert q['market_status'] == 'OPEN' and q['traded_at'] == '2026-11-02T10:05:00' and q['value_text'] == '0.12억' and q['market_cap'] == 1159e8
    assert _json.loads(db.get_state('fund:000001')['value'])['info'] == {'per': 47.6, 'pbr': 1.77}
    assert _json.loads(db.get_state('quote:KOSDAQ')['value'])['change_pct'] == 0.79
    macro.store_daily_kis('000001', [{'date': '2026-10-29', 'open': 1, 'high': 1, 'low': 1, 'close': 10150, 'volume': 1},
                                     {'date': '2026-10-30', 'open': 1, 'high': 1, 'low': 1, 'close': 9800, 'volume': 1}])
    with db.connect() as conn:
        assert conn.execute("SELECT base FROM daily_bars WHERE symbol='000001' AND date='2026-10-30'").fetchone()[0] == 10150


def test_kis_backfill_replaces_history_in_steps(tmp_path, monkeypatch):
    from datetime import datetime
    from core import db, kis, kis_backfill
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path); monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'monitor.db'); db.init_db()
    days = ['2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30']
    monkeypatch.setattr(kis, 'daily_range', lambda code, s, e, index=False: [
        {'date': d, 'open': 1, 'high': 1, 'low': 1, 'close': 100 + i, 'volume': 1} for i, d in enumerate(days)])
    monkeypatch.setattr(kis, 'full_day_bars', lambda code, d: [] if d == '2026-10-27' else [
        {'ts': d + 'T09:00', 'open': 1, 'high': 2, 'low': 1, 'close': 2, 'volume': 5}])
    with db.connect() as conn:
        conn.execute("INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume,source) VALUES ('000001','2026-10-29T10:00',9,9,9,9,1,'naver_history')")
        conn.execute("INSERT INTO minute_bars(symbol,ts,open,high,low,close,volume,source) VALUES ('000001','2026-10-27T10:00',8,8,8,8,1,'naver_history')")
    now = datetime(2026, 10, 31, 2, 0)
    m1 = kis_backfill.run('000001', now, 2)
    assert '일봉 4일(2026-10-27~2026-10-30)' in m1 and 'KOSDAQ 일봉 4일' in m1 and '1분봉 2일 받음' in m1 and '남은 날 2일' in m1
    m2 = kis_backfill.run('000001', now, 2)
    assert '한투에 없음 1일' in m2 and '다시 받기 끝' in m2 and '2026-10-27~2026-10-27' in m2
    assert kis_backfill.run('000001', now, 2) is None
    with db.connect() as conn:
        rows = dict(conn.execute("SELECT ts, source FROM minute_bars WHERE symbol='000001'").fetchall())
    assert rows['2026-10-29T09:00'] == 'kis_backfill' and '2026-10-29T10:00' not in rows and rows['2026-10-27T10:00'] == 'naver_history'


def _member_out(buys, sells, glob=(5000, 3000)):
    """한투 '주식현재가 회원사' 응답: buys/sells = [(번호, 이름, 수량, 외국계?)] 상위 순."""
    o = {'glob_total_shnu_qty': str(glob[0]), 'glob_total_seln_qty': str(glob[1])}
    for key, xs in (('shnu', buys), ('seln', sells)):
        for i, (no, nm, q, fx) in enumerate(xs, 1):
            o.update({f'{key}_mbcr_no{i}': no, f'{key}_mbcr_name{i}': nm, f'total_{key}_qty{i}': str(q),
                      f'{key}_mbcr_glob_yn_{i}': 'Y' if fx else 'N'})
    return o


def test_kis_brokers_unknown_side_is_not_zero(tmp_path, monkeypatch):
    # 한투 거래원: 매수 상위 5곳은 매수만, 매도 상위 5곳은 매도만 → 모르는 쪽은 0이 아니라 '모름'.
    # 순매수는 아는 만큼만(5위 마지막 수량을 넘지 않는다는 것으로 최소치), 방향도 모르면 순위에서 뺌
    from datetime import datetime
    from core import causes, db, feed, kis
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path); monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'monitor.db'); db.init_db()
    monkeypatch.setenv('KIS_APP_KEY', 'k'); monkeypatch.setenv('KIS_APP_SECRET', 's')
    buys = [('1', '키움증권', 9000, False), ('2', '모건스탠리', 6000, True), ('3', '미래에셋', 3000, False), ('4', 'NH투자', 2500, False), ('5', '삼성', 2000, False)]
    sells = [('3', '미래에셋', 8000, False), ('6', '신한', 4000, False), ('7', 'KB', 3500, False), ('8', '한화', 3200, False), ('9', '토스', 3000, False)]
    info = kis.parse_members(_member_out(buys, sells))
    by = {t['name']: t for t in info['traders']}
    assert by['키움증권']['buy'] == 9000 and by['키움증권']['sell'] is None and by['모건스탠리']['foreign']
    assert by['미래에셋']['buy'] == 3000 and by['미래에셋']['sell'] == 8000 and info['foreign_buy'] == 5000
    monkeypatch.setattr(kis, 'members', lambda code, market='J': info)
    feed.snapshot_traders('000001', datetime(2026, 11, 2, 10, 30))
    f = causes._flows('000001', datetime(2026, 11, 2, 9, 0), datetime(2026, 11, 2, 10, 30), 50000)
    tb = {r['name']: r for r in f['top_buyers']}
    # 키움: 매도는 5위(3000주) 밖이라 3000주 미만 → 순매수 최소 6000주
    assert tb['키움증권']['net'] == 6000 and tb['키움증권']['net_is_min'] and tb['키움증권']['sell_state'] == 'hidden'
    # 삼성: 매수 2000 < 매도 5위 3000 → 방향을 모름 → 순위에서 빠짐 (0으로 치지 않음)
    assert '삼성' not in tb and all(r['name'] != '삼성' for r in f['top_sellers'])
    # 미래에셋: 양쪽 다 상위 5위 → 정확한 순매도 −5000
    ts = {r['name']: r for r in f['top_sellers']}
    assert ts['미래에셋']['net'] == -5000 and not ts['미래에셋']['net_is_min']
    # 외국계 합계는 한투가 따로 주는 값 (상위 5곳 합이 아님)
    assert f['foreign_net'] == 2000 and f['foreign_buy'] == 5000
    assert [x['qty'] for x in f['top5']['buy']] == [9000, 6000, 3000, 2500, 2000] and '한국투자증권' in f['source']
    # 구간(10:30→10:40) 증감: 외국계 합계도 같은 두 시각의 차이
    buys2 = [('1', '키움증권', 20000, False)] + buys[1:]
    monkeypatch.setattr(kis, 'members', lambda code, market='J': kis.parse_members(_member_out(buys2, sells, glob=(7000, 3500))))
    feed.snapshot_traders('000001', datetime(2026, 11, 2, 10, 40))
    g = causes._flows('000001', datetime(2026, 11, 2, 10, 30), datetime(2026, 11, 2, 10, 40), 20000)
    assert g['basis'] == '구간' and g['foreign_net'] == 1500
    assert [(r['name'], r['net']) for r in g['top_buyers']] == [('키움증권', 8000)]     # 11000 − 매도 5위 밖(3000 미만)


def test_kis_token_survives_lost_record(tmp_path, monkeypatch):
    # 기록(DB)이 다음 실행에 못 넘어가도(실행 실패 등) 따로 저장한 토큰을 써서 새로 받지 않음
    import cloud_job
    from core import db, kis
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path); monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'monitor.db'); db.init_db()
    monkeypatch.setenv('KIS_APP_KEY', 'k'); monkeypatch.setenv('KIS_APP_SECRET', 's'); monkeypatch.setenv('GO_DATA_KEY', 'dk')
    monkeypatch.setattr(cloud_job, 'STATE_DIR', tmp_path / 'state')
    calls = []
    def fake(method, path, **kw):
        calls.append(path)
        return {'access_token': 'TOK1', 'access_token_token_expired': '2099-01-01 09:00:00'}
    monkeypatch.setattr(kis, '_http', fake); monkeypatch.setattr(kis, 'PERSIST', []); monkeypatch.setattr(kis, 'ISSUED', [])
    log = __import__('logging').getLogger('t')
    cloud_job.kis_token_guard(log)                      # 실행 1: 시작하자마자 받고 따로 저장
    assert calls == ['/oauth2/tokenP'] and (tmp_path / 'state' / 'kis_token.enc').exists()
    assert b'TOK1' not in (tmp_path / 'state' / 'kis_token.enc').read_bytes()      # 암호화
    with db.connect() as conn:                            # 실행 1의 기록이 넘어가지 못함
        conn.execute("DELETE FROM system_state WHERE key='kis:token'")
    cloud_job.kis_token_guard(log)                      # 실행 2: 따로 저장한 토큰을 씀
    assert calls == ['/oauth2/tokenP'] and kis.token() == 'TOK1'
