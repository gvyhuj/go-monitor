"""Persistent news-event observations. Raw records remain in their original tables.

No causal estimates. All returns are ratios of identifiable observed prices.
This module is also used by encrypted static exports and the local API.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from . import yahoo

VERSION = 'observatory-3-krx-close'
KINDS = ('company_press_release', 'external_article', 'disclosure')
# Earlier builds offered a fourth kind. Stored values and history stay as written; they are read as external articles.
LEGACY_KINDS = {'external_company_announcement': 'external_article'}


def normalize_kind(kind):
    return LEGACY_KINDS.get(kind, kind)


def normalize_member_kind(kind):
    return 'ext' if kind in LEGACY_KINDS else kind


SCHEMA = '''
CREATE TABLE IF NOT EXISTS article_candidates (a TEXT, b TEXT, score REAL, PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS news_calculation_migrations (version TEXT PRIMARY KEY, changed_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_events (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_event_members (
 member_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_event_aliases (alias TEXT PRIMARY KEY, event_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_event_history (
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, changed_at TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_event_records (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_event_notes (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_event_observations (
 event_id TEXT NOT NULL, digest TEXT NOT NULL, calculation_version TEXT NOT NULL,
 recomputed_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(event_id,digest));
'''


def stamp():
    return datetime.now(yahoo.KST).isoformat(timespec='seconds')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def migrate(conn):
    # No deletion/rewriting of existing articles, prices or manual causes.
    for statement in SCHEMA.split(';'):
        if statement.strip():
            conn.execute(statement)


def resolve(conn, eid):
    seen = set()
    while eid not in seen:
        seen.add(eid)
        row = conn.execute('SELECT event_id FROM news_event_aliases WHERE alias=?', (eid,)).fetchone()
        if not row:
            return eid
        eid = row[0]
    raise ValueError('사건 별칭 순환')


def ensure_event(conn, member, title, evidence, siblings=()):
    """Prefer existing member identity; a changing representative never changes IDs."""
    row = conn.execute('SELECT event_id FROM news_event_members WHERE member_id=?', (member,)).fetchone()
    candidates = []
    for key in siblings:
        old = conn.execute('SELECT event_id FROM news_event_members WHERE member_id=?', (key,)).fetchone()
        if old:
            candidates.append(resolve(conn, old[0]))
    eid = resolve(conn, row[0]) if row else (candidates[0] if candidates else 'ne-' + uuid.uuid4().hex)
    conn.execute('INSERT OR IGNORE INTO news_events(id,title,created_at) VALUES(?,?,?)', (eid, title, stamp()))
    conn.execute('INSERT OR IGNORE INTO news_event_members VALUES(?,?,?)', (member, eid, evidence))
    return eid


def mutate(conn, item):
    """Called only after the existing inbox authentication/decryption boundary."""
    migrate(conn)
    eid = resolve(conn, str(item.get('event_id', '')))
    row = conn.execute('SELECT * FROM news_events WHERE id=?', (eid,)).fetchone()
    if not row:
        raise ValueError('없는 사건 ID')
    action = item['type']
    if action == 'news_event_note':
        payload = {k: item.get(k) for k in ('body','kind','author_type','sources','confidence_label',
                    'review_status','analysis_at','question','snapshot','revision_parent_id')}
        if not str(payload['body'] or '').strip() or len(str(payload['body'])) > 100000:
            raise ValueError('기록 본문은 1~100000자')
        if payload['kind'] not in ('manual_observation','ai_analysis','retrospective_review'):
            raise ValueError('기록 종류 오류')
        payload['body'] = str(payload['body'])
        # The site sends only the snapshot version; attach the stored observation so the note keeps what was seen.
        snap = payload.get('snapshot') if isinstance(payload.get('snapshot'), dict) else {}
        if 'observation' not in snap:
            rec = conn.execute('SELECT payload FROM news_event_records WHERE event_id=?', (eid,)).fetchone()
            if rec:
                snap = {**snap, 'observation': json.loads(rec[0]).get('observation')}
        payload['snapshot'] = snap or None
        if payload.get('sources') is not None and not isinstance(payload['sources'], list):
            raise ValueError('확인 출처는 URL 목록')
        payload['sources'] = [str(x)[:2000] for x in (payload.get('sources') or [])][:100]
        payload['analysis_at'] = kst(payload['analysis_at']).isoformat(timespec='seconds') if payload.get('analysis_at') else stamp()
        parent = payload.get('revision_parent_id')
        if parent and not conn.execute('SELECT 1 FROM news_event_notes WHERE id=? AND event_id=?', (parent,eid)).fetchone():
            raise ValueError('수정 대상 기록 오류')
        nid = str(item.get('request_id') or uuid.uuid4().hex)
        conn.execute('INSERT OR IGNORE INTO news_event_notes VALUES(?,?,?,?)', (nid,eid,stamp(),dumps(payload)))
    elif action == 'news_event_meta':
        meta = json.loads(row['metadata'])
        before = dict(meta)
        requested = item.get('event_kind')
        if 'event_kind' in item:
            item = {**item, 'event_kind': normalize_kind(requested)}
        for key in ('event_kind','business_tags','publisher','partner','primary_source_url','first_public_at',
                    'time_source','time_status','pr_distributed_at','analysis_at','followup_candidate'):
            if key in item:
                meta[key] = item[key]
        if meta.get('event_kind') and meta['event_kind'] not in KINDS:
            raise ValueError('사건 종류 오류')
        if meta.get('time_status') and meta['time_status'] not in ('confirmed','estimated','unknown'):
            raise ValueError('시각 확인 상태 오류')
        for key in ('publisher','partner','primary_source_url','time_source'):
            if key in meta:
                meta[key] = str(meta[key] or '')[:2000]
        for key in ('first_public_at','pr_distributed_at','analysis_at'):
            if meta.get(key):
                meta[key] = kst(meta[key]).isoformat(timespec='seconds')
        if 'business_tags' in meta:
            if not isinstance(meta['business_tags'], list):
                raise ValueError('태그는 목록')
            meta['business_tags'] = list(dict.fromkeys(str(t)[:80] for t in meta['business_tags']))[:30]
        conn.execute('UPDATE news_events SET metadata=? WHERE id=?', (dumps(meta),eid))
        change = {'before':before,'after':meta}
        if requested in LEGACY_KINDS:
            change['requested_event_kind'] = requested
        conn.execute('INSERT INTO news_event_history(event_id,changed_at,action,payload) VALUES(?,?,?,?)',
                     (eid,stamp(),action,dumps(change)))
    elif action == 'news_event_split':
        member = str(item.get('member_id',''))
        if not conn.execute('SELECT 1 FROM news_event_members WHERE member_id=? AND event_id=?',(member,eid)).fetchone():
            raise ValueError('분리 대상 오류')
        new_id='ne-'+uuid.uuid4().hex
        conn.execute('INSERT INTO news_events(id,title,created_at) VALUES(?,?,?)',(new_id,row['title'],stamp()))
        conn.execute('UPDATE news_event_members SET event_id=?, evidence=? WHERE member_id=?',
                     (new_id,'manual_split',member))
        for target in (eid,new_id):
            conn.execute('INSERT INTO news_event_history(event_id,changed_at,action,payload) VALUES(?,?,?,?)',
                         (target,stamp(),action,dumps({'member':member,'from':eid,'to':new_id,'reason':item.get('reason')})))
    elif action == 'news_event_link':
        # Explicit human-confirmed merge; preserve old deep links and notes.
        other = resolve(conn, str(item.get('other_id','')))
        if other == eid or not conn.execute('SELECT 1 FROM news_events WHERE id=?',(other,)).fetchone():
            raise ValueError('연결 대상 오류')
        conn.execute('INSERT OR REPLACE INTO news_event_aliases VALUES(?,?)',(other,eid))
        conn.execute('UPDATE news_event_members SET event_id=? WHERE event_id=?',(eid,other))
        conn.execute('UPDATE news_event_notes SET event_id=? WHERE event_id=?',(eid,other))
        conn.execute('INSERT INTO news_event_history(event_id,changed_at,action,payload) VALUES(?,?,?,?)',
                     (eid,stamp(),action,dumps({'from':other,'reason':item.get('reason')})))
    else:
        raise ValueError('알 수 없는 사건 요청')
    return eid


def kst(value):
    t = datetime.fromisoformat(value)
    return t.astimezone(yahoo.KST).replace(tzinfo=None) if t.tzinfo else t


def ratio(value, base):
    return value / base - 1 if value and base and value > 0 and base > 0 else None


_CAL = {}


def calendar(conn, symbol):
    # Observed exchange sessions, not weekdays; KOSDAQ includes stock suspension days.
    # 사건마다 1분봉 전체를 훑지 않게, 마지막 봉이 그대로면 앞서 구한 달력을 다시 씀
    sig = (conn.execute('PRAGMA database_list').fetchone()[2], symbol, *(conn.execute(q, (s,)).fetchone()[0] for s in (symbol, yahoo.INDEX) for q in (
        'SELECT MAX(date) FROM daily_bars WHERE symbol=?', 'SELECT MAX(ts) FROM minute_bars WHERE symbol=?',
        'SELECT MAX(ts) FROM coarse_bars WHERE symbol=?')),
        conn.execute('SELECT COUNT(*) FROM daily_bars WHERE symbol=?', (symbol,)).fetchone()[0])
    if sig not in _CAL:
        _CAL.clear()
        _CAL[sig] = _calendar(conn, symbol)
    return list(_CAL[sig])


def _calendar(conn, symbol):
    return [r[0] for r in conn.execute('SELECT date d FROM daily_bars WHERE symbol IN (?,?) UNION '
            'SELECT substr(ts,1,10) d FROM minute_bars WHERE symbol IN (?,?) UNION '
            'SELECT substr(ts,1,10) d FROM coarse_bars WHERE symbol IN (?,?) ORDER BY d',
            (symbol,yahoo.INDEX,symbol,yahoo.INDEX,symbol,yahoo.INDEX))]


def observe(conn, symbol, public_at, unknown=False):
    out = {'calculation_version': VERSION, 'timezone':'Asia/Seoul', 'status':'unavailable',
           'missing_reason':'공개 시각 미상', 'day':None, 'intraday':None, 'windows':{}}
    if unknown or not public_at:
        return out
    t = kst(public_at)
    days = calendar(conn,symbol)
    candidates = [d for d in days if d >= t.date().isoformat() and
                  not (d == t.date().isoformat() and t.strftime('%H:%M') > '15:00')]
    if not candidates:
        out.update(status='pending',missing_reason='다음 반응 거래일 관측 대기')
        return out
    day = candidates[0]
    mode = 'late' if t.strftime('%H:%M') > '15:00' and day > t.date().isoformat() else 'open' if day > t.date().isoformat() or t.strftime('%H:%M') < '09:00' else 'session'
    start = t if mode == 'session' else datetime.fromisoformat(day+'T09:00')
    frame = yahoo.reaction_frame(conn,symbol,day,start,mode,min_minutes=1)
    out.update(day=day,mode=mode,start=start.isoformat(timespec='seconds'),status='partial',missing_reason=None)
    frame['source'] = {'own': price_source(conn,symbol,day,frame['interval']),
                       'kq': price_source(conn,yahoo.INDEX,day,frame['kq_interval'])}
    pts = []
    own, idx = dict(frame['own']),dict(frame['kq'])
    sources={s:{r[0][:16]:r[1] for r in conn.execute("SELECT ts,source FROM minute_bars WHERE symbol=? AND ts >= ? AND ts < ? || 'z'",(s,day,day))}
             for s in (symbol,yahoo.INDEX)}
    for ts in sorted(set(own)|set(idx)):
        minute = (kst(ts)-start).total_seconds()/60
        if -30 <= minute <= 120:
            a,b = ratio(own.get(ts),frame['base_own']),ratio(idx.get(ts),frame['base_kq'])
            pts.append({'time':ts,'offset':minute,'stock_price':own.get(ts),'kosdaq_price':idx.get(ts),
                        'own_source':sources[symbol].get(ts) if frame['interval']==1 else 'Yahoo',
                        'kq_source':sources[yahoo.INDEX].get(ts) if frame['kq_interval']==1 else 'Yahoo',
                        'own':a,'kq':b,'diff':a-b if a is not None and b is not None and frame['comparable'] else None})
    steps = {}
    for m in (30,60,120):
        end = start+timedelta(minutes=m)
        p = next((p for p in pts if kst(p['time']) == end),{})
        steps[str(m)] = {k:p.get(k) for k in ('own','kq','diff','stock_price','kosdaq_price')}
        steps[str(m)]['end'] = end.isoformat(timespec='seconds')
    out['intraday'] = {k:v for k,v in frame.items() if k not in ('own','kq')} | {'points':pts,'steps':steps}
    out['windows'] = {str(n):daily_window(conn,symbol,day,n,days) for n in (5,20)}
    out['status'] = 'complete' if all(steps[m]['diff'] is not None for m in ('30','60')) else 'partial'
    out['missing_reason'] = None if out['status']=='complete' else '기준·종료 가격 누락 또는 KOSDAQ 시각 불일치'
    return out


def price_source(conn,symbol,day,iv):
    if iv == 1:
        return ', '.join(r[0] for r in conn.execute("SELECT DISTINCT source FROM minute_bars WHERE symbol=? AND ts >= ? AND ts < ? || 'z'",(symbol,day,day)))
    return 'Yahoo 완료 봉' if iv else '관측값 없음'


def daily_window(conn,symbol,day,n,days=None):
    days = days or calendar(conn,symbol)
    i = days.index(day)
    base_day = days[i-1] if i else None
    prices = {s:yahoo.krx_closes(conn,s) for s in (symbol,yahoo.INDEX)}   # 거래소 정규장 종가 (시간외 제외)
    a,b = prices[symbol].get(base_day),prices[yahoo.INDEX].get(base_day)
    points = []
    for j in range(max(0,i-n), min(len(days),i+n+1)):
        d = days[j]; p,k=prices[symbol].get(d),prices[yahoo.INDEX].get(d)
        x,y=ratio(p,a),ratio(k,b)
        points.append({'time':d,'offset':j-i,'stock_price':p,'kosdaq_price':k,'own':x,'kq':y,
                       'diff':x-y if x is not None and y is not None else None})
    end = next((p for p in points if p['offset']==n),{})
    pre = next((p for p in points if p['offset']==-n),{})
    return {'points':points,'base_day':base_day,'base_own':a,'base_kq':b,'range':n,
            'post':{k:end.get(k) for k in ('own','kq','diff')},'pre_trend':ratio(a,pre.get('stock_price')),
            'status':'pending' if i+n>=len(days) else 'complete' if end.get('diff') is not None else 'partial',
            'source':'거래소(KRX) 정규장 종가: 다음 거래일 기준가(거래소 전일 종가)로 확인, 마지막 날은 15:30까지 마지막 1분 시세. 넥스트레이드 시간외 체결 제외. KOSDAQ은 지수 종가',
            'price_warning':'기업행동(분할·증자 등) 수정주가 보정은 하지 않습니다. 보정이 있었던 기간은 외부 수정주가와 다를 수 있습니다.'}


def tags(title):
    groups = {'방산':['방산','국방'], '우주항공':['우주','위성','항공'], '반도체':['반도체'],
              '디스플레이':['디스플레이'], '광학소재(ZnS)':['ZnS','황화아연'], '수주·공급':['수주','공급','계약'],
              'MOU':['MOU','협약'], '기술·개발':['기술','개발'], '실적':['실적','매출','영업이익'], 'IR':['IR','설명회']}
    return [tag for tag,words in groups.items() if any(w.lower() in title.lower() for w in words)]


def build(conn,symbol,rows):
    migrate(conn)
    visible_ids={str(r['id']) for r in rows}
    for member in conn.execute("SELECT member_id FROM news_event_members WHERE evidence='manual_split'"):
        if member[0] in visible_ids: continue
        a=conn.execute('SELECT * FROM articles WHERE id=? AND symbol=?',(member[0],symbol)).fetchone()
        if a:
            rows.append({'id':a['id'],'title':a['title'],'ts':a['ts'],'office':a['office'],
                         'kind':'pr' if a['kind_override']=='pr' else 'ext'})
    groups={}
    for r in rows:
        aid=str(r['id'])
        a=conn.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone()
        a=dict(a) if a else {}
        # Only explicit links establish identity; title/story similarity is not evidence.
        siblings=[aid]
        pending=[aid]
        while pending:
            key=pending.pop()
            linked=[x[0] for x in conn.execute(
                'SELECT b FROM article_links WHERE a=? UNION SELECT a FROM article_links WHERE b=? '
                'UNION SELECT story_with FROM articles WHERE id=? AND story_with IS NOT NULL '
                'UNION SELECT id FROM articles WHERE story_with=?',(key,key,key,key))]
            for sid in linked:
                if sid not in siblings:
                    siblings.append(sid); pending.append(sid)
        eid=ensure_event(conn,aid,r['title'],'confirmed_link' if len(siblings)>1 else 'individual',siblings)
        # Preserve historical membership and expose it for review, without reassigning IDs.
        siblings=list(dict.fromkeys(siblings+[x[0] for x in conn.execute(
            'SELECT member_id FROM news_event_members WHERE event_id=?',(eid,))]))
        r['event_id']=eid
        r['event_key']=eid
        member={**r,'url':a.get('url'),'collected_at':a.get('first_seen'),
                'time_source':a.get('time_from') or a.get('source') or ('manual' if aid.startswith('pr') else 'unknown'),
                'original_ts':a.get('ts_auto') or a.get('ts') or r.get('ts')}
        if a.get('kind_override'):
            member['kind']=normalize_member_kind(a['kind_override'])
        if r.get('kind')=='disc':
            raw=conn.execute('SELECT * FROM disclosures WHERE no=?',(aid.removeprefix('d'),)).fetchone()
            if raw:
                member.update(url=('https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno='+str(raw['no'])[1:] if str(raw['no']).startswith('K') else 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+str(raw['no'])),time_source='disclosure' if raw['ts'] else 'unknown',ts=raw['ts'])
        if aid.startswith('pr'):
            raw=conn.execute('SELECT * FROM pr_events WHERE id=?',(aid[2:],)).fetchone()
            if raw:
                member.update(url=raw['url'],original_ts=raw['published_ts'],kind='pr')
        groups.setdefault(eid,[]).append(member)
        for sid in siblings:
            if sid==aid or any(x['id']==sid for x in groups[eid]): continue
            raw=conn.execute('SELECT * FROM articles WHERE id=?',(sid,)).fetchone()
            if raw is None: continue
            raw=dict(raw)
            other=ensure_event(conn,sid,raw['title'],'confirmed_link' if len(siblings)>1 else 'individual',[aid])
            if other!=eid: continue  # Explicit human splits take precedence over story grouping.
            groups[eid].append({'id':sid,'title':raw['title'],'ts':raw['ts'],'office':raw['office'],
                'url':raw['url'],'collected_at':raw['first_seen'],'original_ts':raw.get('ts_auto') or raw['ts'],
                'time_source':raw.get('time_from') or raw.get('source') or 'unknown',
                'kind':'pr' if raw.get('kind_override')=='pr' else 'ext'})
    result={}
    for eid,members in groups.items():
        saved=conn.execute('SELECT * FROM news_events WHERE id=?',(eid,)).fetchone()
        meta=json.loads(saved['metadata'])
        members=list({m['id']:m for m in members}.values())
        valid=[m for m in members if m.get('ts') and not m.get('untimed') and m.get('time_quality')!='check_time']
        first=min(valid,key=lambda m:m['ts']) if valid else members[0]
        public=meta.get('first_public_at',first.get('ts') if valid else None)
        press=[m for m in members if m.get('kind')=='pr']
        # Same rule as the news list: an automatic '회사 발표' (company) article counts as a press release.
        kind=normalize_kind(meta.get('event_kind')) or ('company_press_release' if press or any(m.get('kind')=='company' for m in members) else 'disclosure' if any(m.get('kind')=='disc' for m in members) else 'external_article')
        # Article publication is not the issuer's distribution time.
        release_times=[]
        for m in press:
            if str(m['id']).startswith('pr'):
                release=conn.execute('SELECT published_ts FROM pr_events WHERE id=?',(str(m['id'])[2:],)).fetchone()
                if release: release_times.append(release[0])
        linked_metadata=[json.loads(x[0]) for x in conn.execute(
            'SELECT metadata FROM news_events WHERE id IN (SELECT alias FROM news_event_aliases WHERE event_id=?)',(eid,))]
        linked_press=[x for x in linked_metadata if x.get('event_kind')=='company_press_release']
        if press or linked_press:
            kind='company_press_release'
        legacy_kind=meta.get('event_kind') if meta.get('event_kind') in LEGACY_KINDS else None
        release_times += [x['pr_distributed_at'] for x in linked_press if x.get('pr_distributed_at')]
        member_evidence=[x[0] for x in conn.execute('SELECT evidence FROM news_event_members WHERE event_id=?',(eid,))]

        obs=observe(conn,symbol,meta.get('analysis_at') or public,meta.get('time_status')=='unknown' or not public)
        digest=hashlib.sha256(dumps(obs).encode()).hexdigest()
        conn.execute('INSERT OR IGNORE INTO news_event_observations VALUES(?,?,?,?,?)',(eid,digest,VERSION,stamp(),dumps(obs)))
        saved_at=conn.execute('SELECT recomputed_at FROM news_event_observations WHERE event_id=? AND digest=?',(eid,digest)).fetchone()[0]
        notes=[{'id':r['id'],'created_at':r['created_at'],**json.loads(r['payload'])} for r in conn.execute('SELECT * FROM news_event_notes WHERE event_id=? ORDER BY created_at,id',(eid,))]
        history=[dict(r) for r in conn.execute('SELECT * FROM news_event_history WHERE event_id=? ORDER BY id',(eid,))]
        context=[{'id':x['id'],'event_id':x.get('event_id'),'title':x['title'],'ts':x.get('ts'),'kind':x.get('kind')}
                 for x in rows if x.get('event_id')!=eid and x.get('ts') and public and abs((kst(x['ts'])-kst(public)).total_seconds())<=86400]
        result[eid]={'event_id':eid,'title':first['title'],'event_kind':kind,'business_tags':meta.get('business_tags',tags(first['title'])),
          'publisher':meta.get('publisher','미확인'),'partner':meta.get('partner',''),'first_public_at':public,
          'time_source':meta.get('time_source',first['time_source']),'time_status':meta.get('time_status','confirmed' if first['time_source'] in ('manual','page','original_page','disclosure') and public else 'estimated' if public else 'unknown'),
          'pr_distributed_at':meta.get('pr_distributed_at') or (min(release_times) if release_times else None),'primary_source_url':meta.get('primary_source_url') or first.get('url'),
          'analysis_at':meta.get('analysis_at') or public,'members':members,'observation':obs,'context':context,
          'notes':notes,'history':history,'identity_status':'confirmed' if any(h['action']=='news_event_link' for h in history) or (len(members)>1 and all(x=='confirmed_link' for x in member_evidence)) else 'review_required' if len(members)>1 else 'individual', 'same_event_candidates':[dict(x) for x in conn.execute('SELECT * FROM article_candidates WHERE a IN (SELECT member_id FROM news_event_members WHERE event_id=?) OR b IN (SELECT member_id FROM news_event_members WHERE event_id=?)',(eid,eid))],'recomputed_at':saved_at,'snapshot_version':digest,
          'followup_candidate':meta.get('followup_candidate',False), 'classification_sources':[{'member_id':m['id'],'kind':m.get('kind'),'url':m.get('url')} for m in members]+linked_metadata, 'legacy_event_kind':legacy_kind, 'source_note':'기사 공개 시각은 보도자료 배포 시각과 다릅니다. 자동 회사 발표 분류 기사는 기존과 같이 보도자료로 집계합니다. 발표 주체를 확인하여 필요하면 수정하세요.'}
        result[eid]['market_context'] = {
            'industry':[dict(r) for r in conn.execute('SELECT name,change_rate,updated_at FROM industry_daily WHERE date=?',(obs.get('day'),))],
            'themes':[dict(r) for r in conn.execute('SELECT name,change_rate,updated_at FROM theme_daily WHERE date=? AND mine=1',(obs.get('day'),))],
            'basis':'해당 거래일 저장 스냅샷. 기사 공개 순간과 동일한 관측 시각임을 보장하지 않음.'}
        sources=dumps([{k:m.get(k) for k in ('id','ts','original_ts','time_source','title','url')} for m in members])
        previous=conn.execute("SELECT payload FROM news_event_history WHERE event_id=? AND action='source_snapshot' ORDER BY id DESC LIMIT 1",(eid,)).fetchone()
        if not previous or previous[0]!=sources:
            conn.execute('INSERT INTO news_event_history(event_id,changed_at,action,payload) VALUES(?,?,?,?)',(eid,stamp(),'source_snapshot',sources))
        # Legacy summary graph/table uses exactly the same observation payload.
        for r in rows:
            if r.get('event_id')!=eid: continue
            f=obs.get('intraday') or {}
            # Rows keep their own article kind; only an explicit classification from the event record replaces it.
            if meta.get('event_kind'):
                r['kind']={'company_press_release':'pr','disclosure':'disc'}.get(normalize_kind(meta['event_kind']),'ext')
            elif (press or linked_press) and r.get('kind') not in ('pr','company'):
                r['kind']='pr'
            r.update(event_kind=kind,untimed=not bool(public),analysis_ts=result[eid]['analysis_at'],start=obs.get('start'),mode=obs.get('mode'),reaction_day=obs.get('day'),
                     interval=f.get('interval'),kq_interval=f.get('kq_interval'),steps=f.get('steps',{}),
                     calculation_version=VERSION,base_price_time=f.get('base_own_time'),price_source=f.get('source'),
                     path={'own':[[p['offset'],p['own'],None,p['own_source']] for p in f.get('points',[]) if p['own'] is not None],
                           'kq':[[p['offset'],p['kq'],None,p['kq_source']] for p in f.get('points',[]) if p['kq'] is not None and f.get('comparable')]})
    for eid,detail in result.items():
        conn.execute('INSERT OR REPLACE INTO news_event_records VALUES(?,?)',(eid,dumps(detail)))
    for eid,payload in conn.execute('SELECT event_id,payload FROM news_event_records'):
        if eid not in result and resolve(conn,eid)==eid:
            result[eid]=json.loads(payload)
            result[eid]['archived_source']=True
            if result[eid].get('event_kind') in LEGACY_KINDS:
                result[eid]['legacy_event_kind']=result[eid]['event_kind']
                result[eid]['event_kind']=normalize_kind(result[eid]['event_kind'])
            for m in result[eid].get('members',[]):
                m['kind']=normalize_member_kind(m.get('kind'))
            result[eid]['notes']=[{'id':r['id'],'created_at':r['created_at'],**json.loads(r['payload'])} for r in conn.execute('SELECT * FROM news_event_notes WHERE event_id=? ORDER BY created_at,id',(eid,))]
    aliases={r[0]:resolve(conn,r[1]) for r in conn.execute('SELECT * FROM news_event_aliases')}
    return result,aliases
