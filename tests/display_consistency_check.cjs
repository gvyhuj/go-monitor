const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
function fn(name) {
  const start = source.indexOf(`  function ${name}(`);
  if (start < 0) return '';
  const next = source.indexOf('\n  }', start);
  return source.slice(start, next + 4);
}
function harness() {
  const nodes = new Map();
  const $ = (sel) => {
    if (!nodes.has(sel)) nodes.set(sel, { innerHTML: '', textContent: '', dataset: {}, querySelectorAll: () => [], contains: () => true });
    return nodes.get(sel);
  };
  const ctx = { $, STATUS: { quote: { price: 10610, change_pct: 3.01, change: 310,
      traded_at: '2026-10-28T20:00', market_status: 'CLOSE' } },
    DAY_ROWS: { '2026-10-28': { close: 10740, change: 440/10300, kosdaq: .0369 } },
    isDaily: e => e.event_type === 'daily', kindOf: () => '상승', scopeTag: () => '', tierTag: () => '',
    moveBasis: () => '', sgnCls: () => '', pct: v => v == null ? '-' : `${v >= 0 ? '+' : '−'}${Math.abs(v*100).toFixed(2)}%`,
    points: v => `${(v*100).toFixed(2)}%p`, won: v => String(v), int: v => String(v), eok: v => String(v), esc: v => String(v ?? ''),
    dayLabel: s => s.slice(0,10), hhmm: s => (s || '').slice(11,16),
    manualBlock: () => '기존 원인 본문', disclosureBlock: () => '',
    flowBlock: () => '', followupBlock: () => '', fillDayEvidence: () => {},
    causeHead: () => '', CONF_RE: /nomatch/, document: {}, MAIN_DISC: new Set(),
  };
  vm.createContext(ctx);
  vm.runInContext(['liveFor', 'dailySnapshot', 'dailyEvidenceNote', 'renderEventDrawer', 'themeLine', 'eventContext'].map(fn).join('\n'), ctx);
  return {ctx, $};
}
const event = { event_type: 'daily', status: 'closed', direction: 'up', start_ts: '2026-10-28T09:00',
  last_ts: '2026-10-28T15:30', peak_ts: '2026-10-28T15:30', peak_return_5m: 440/10300,
  own_change: 440/10300, kosdaq_change: .0369, trade_value: 310000000 };
test('closed daily drawer uses the same after-hours quote as the main screen', () => {
  const {ctx,$} = harness(); ctx.renderEventDrawer({event:{...event}, cause:null});
  const html = $('#drawer-body').innerHTML;
  assert.match(html.match(/<h2[\s\S]*?<\/h2>/)[0], /\+3\.01%/);
  // 하루 기록: 기준가 줄·등락/KOSDAQ/거래대금 칸·저장 당시 안내는 위 머리 박스와 겹쳐 뺌
  assert.doesNotMatch(html, /기준가\(전날 거래소 종가\)|분석 당시 저장된|거래대금|<dl class="facts/);
  assert.match(html, /기존 원인 본문/);
});
test('AI request identifies current quote separately from saved evidence', () => {
  const {ctx} = harness();
  const text = ctx.eventContext({event:{...event}, cause:{}}, '원인');
  assert.match(text, /20:00/);
  assert.match(text, /\+3\.01%/);
  assert.match(text, /분석 당시/);
});
test('intraday event remains tied to its original interval', () => {
  const {ctx,$} = harness(); ctx.renderEventDrawer({event:{...event,event_type:'intraday'},cause:null});
  assert.match($('#drawer-body').innerHTML.match(/<h2[\s\S]*?<\/h2>/)[0], /\+4\.27%/);
});

test('a later-day quote never overwrites a historical daily record', () => {
  const {ctx,$} = harness();
  ctx.STATUS.quote.traded_at = '2026-10-29T20:00';
  ctx.renderEventDrawer({event:{...event},cause:null});
  assert.match($('#drawer-body').innerHTML.match(/<h2[\s\S]*?<\/h2>/)[0], /\+4\.27%/);
  assert.doesNotMatch($('#drawer-body').innerHTML, /20:00/);
});

test('news list overview explains distinct counts and the earlier calculation timestamp', async () => {
  const {ctx,$} = harness();
  const base = {id:'a',ts:'2026-04-22T19:20',analysis_ts:'2026-04-22T14:10',basis_reason:'disclosure',
    start:'2026-04-22T14:10',mode:'session',kind:'pr',title:'공급계약',interval:1,
    path:{own:[[-1,0],[10,.01],[30,.02]],kq:[[-1,0],[30,.005]]},steps:{'30':{own:.02,kq:.005}}};
  Object.assign(ctx, { main:{}, api:async()=>({items:[base,{...base,id:'b',steps:{}},
      {...base,id:'c',path:null,steps:{},untimed:true}]}), artViewSeg:()=>'', isoDate:()=>'',
    location:{hash:"#/react"}, toTime:t=>Date.parse(t+"Z")/1000, chanceTxt:()=>'', BAR_TIP:'봉 간격', dashResize:null,window:{addEventListener(){}},
    setTimeout,clearTimeout});
  const start = source.indexOf('  const reactState =');
  const end = source.indexOf('  // 기사 대시보드:',start);
  vm.runInContext(fn('reactionAt') + fn('reactionSamples') + source.slice(start,end) + '\nreactState.range="all";reactState.h="30";',ctx);
  const items = [base,{...base,id:'b',steps:{}},{...base,id:'c',path:null,steps:{},untimed:true}];
  const parts = new Map(), host = {innerHTML:'',querySelector:s=>{if(!parts.has(s))parts.set(s,{});return parts.get(s);},querySelectorAll:()=>[]};
  ctx.reactionOverview(host, items);
  assert.match($('#rx-n').textContent,/대상 3건 · 경로 2건 · \+30분 집계 1건/);
  assert.match($('#rx-sum').innerHTML,/집계 제외 2건/);
  assert.match($('#rx-plot').innerHTML,/비교 시작/);
  assert.doesNotMatch($('#rx-sum').innerHTML, /우연|p값|판단 보류/);
  ctx.points = v => v == null ? '-' : `${ctx.pct(v)}p`;
  vm.runInContext('reactState.vsKq=true',ctx); ctx.reactionOverview(host, items);
  assert.match($('#rx-cards').innerHTML, /\+30분 중앙값[\s\S]*%p/);   // KOSDAQ 빼고 보기: 카드가 %p로
});

test('drawer and AI text carry no share split (market·own), only theme moves', () => {
  const {ctx,$}=harness();
  const split={own:.04,market:.06,specific:-.02};
  ctx.renderEventDrawer({event:{...event},cause:{split}});
  assert.doesNotMatch($('#drawer-body').innerHTML,/모형 추정 기여분/);
  const text=ctx.eventContext({event:{...event},cause:{split}},'검토');
  assert.doesNotMatch(text,/몫 나누기|추정 기여분|고유/);   // 몫 나누기는 AI 질문에도 싣지 않음
  assert.doesNotMatch(text,/업종/);                      // 업종 몫은 계산하지 않음
  assert.match(text,/\[그날 네이버 테마 등락\]/);
});

test('AI text: broker top-5 as published, nets marked as minimum when one side is hidden', () => {
  const {ctx}=harness();
  const flows={basis:'당일 누적',foreign_net:2000,top_buyers:[{name:'키움증권',net:6000,net_is_min:true,foreign:false}],
    top_sellers:[{name:'미래에셋',net:-5000,net_is_min:false,foreign:false}],
    top5:{at:'2026-10-28T10:30',buy:[{name:'키움증권',qty:9000,foreign:false},{name:'모건스탠리',qty:6000,foreign:true}],sell:[{name:'미래에셋',qty:8000,foreign:false}]}};
  const text=ctx.eventContext({event:{...event},cause:{flows}},'검토');
  assert.match(text,/\[창구 상위 5 \(거래소 공개, 10:30까지 그날 누적\)\] 매수: 키움증권 9000주, 모건스탠리\(외국계\) 6000주 \/ 매도: 미래에셋 8000주/);
  assert.match(text,/순매수 상위: 키움증권 \+6000주 이상 \/ 순매도 상위: 미래에셋 -5000주 \/ 외국계 창구 합계 \+2000주/);
  assert.match(text,/최소치에 '이상'을 붙임/);
});
