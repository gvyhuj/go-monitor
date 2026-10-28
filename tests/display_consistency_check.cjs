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
    won: v => String(v), int: v => String(v), eok: v => String(v), esc: v => String(v ?? ''),
    dayLabel: s => s.slice(0,10), hhmm: s => (s || '').slice(11,16),
    manualBlock: () => '기존 원인 본문', relatedBlock: () => '', disclosureBlock: () => '',
    flowBlock: () => '', followupBlock: () => '', fillDayEvidence: () => {},
    causeHead: () => '', CONF_RE: /nomatch/, document: {}, MAIN_DISC: new Set(),
  };
  vm.createContext(ctx);
  vm.runInContext(['liveFor', 'dailySnapshot', 'dailyEvidenceNote', 'renderEventDrawer', 'eventContext'].map(fn).join('\n'), ctx);
  return {ctx, $};
}
const event = { event_type: 'daily', status: 'closed', direction: 'up', start_ts: '2026-10-28T09:00',
  last_ts: '2026-10-28T15:30', peak_ts: '2026-10-28T15:30', peak_return_5m: 440/10300,
  own_change: 440/10300, kosdaq_change: .0369, trade_value: 310000000 };
test('closed daily drawer uses the same after-hours quote as the main screen', () => {
  const {ctx,$} = harness(); ctx.renderEventDrawer({event:{...event}, cause:null});
  const html = $('#drawer-body').innerHTML;
  assert.match(html.match(/<h2[\s\S]*?<\/h2>/)[0], /\+3\.01%/);
  assert.match(html, /20:00/);
  assert.match(html, /분석 당시/);
  assert.match(html, /\+4\.27%/);
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

test('reaction page explains distinct counts and the earlier calculation timestamp', async () => {
  const {ctx,$} = harness();
  const base = {id:'a',ts:'2026-04-22T19:20',analysis_ts:'2026-04-22T14:10',basis_reason:'disclosure',
    start:'2026-04-22T14:10',mode:'session',kind:'pr',title:'공급계약',interval:1,
    path:{own:[[-1,0],[10,.01],[30,.02]],kq:[[-1,0],[30,.005]]},steps:{'30':{own:.02,kq:.005}}};
  Object.assign(ctx, { main:{}, api:async()=>({items:[base,{...base,id:'b',steps:{}},
      {...base,id:'c',path:null,steps:{},untimed:true}]}), artViewSeg:()=>'', isoDate:()=>'',
    chanceTxt:()=>'', BAR_TIP:'봉 간격', dashResize:null,window:{addEventListener(){}},
    setTimeout,clearTimeout});
  const start = source.indexOf('  const reactState =');
  const end = source.indexOf('  // 기사 대시보드:',start);
  vm.runInContext(fn('reactionAt') + fn('reactionSamples') + source.slice(start,end) + '\nreactState.range="all";',ctx);
  await ctx.pageReact();
  assert.match($('#rx-n').textContent,/대상 3건 · 경로 2건 · \+30분 집계 1건/);
  assert.match($('#rx-sum').innerHTML,/집계 제외 2건/);
  assert.match($('#rx-table').innerHTML,/26\.04\.22 19:20/);
  assert.match($('#rx-table').innerHTML,/선행 공시 2026-04-22 14:10/);
  assert.match($('#rx-plot').innerHTML,/비교 시작/);
  let exported;
  ctx.csvDownloadRows = (...args) => { exported = args; };
  $('#rx-csv').onclick();
  assert.ok(exported[1].includes('분석 기준 시각'));
  assert.ok(exported[2][0].includes('2026-04-22T14:10'));
  assert.doesNotMatch($('#rx-sum').innerHTML, /우연|p값|판단 보류/);
  ctx.points = v => v == null ? '-' : `${ctx.pct(v)}p`;
  $('#rx-ctl').onchange({target:{id:'rx-vskq',checked:true}});
  assert.match($('#rx-sum').innerHTML, /시장 웃돈 1/);
  assert.match($('#rx-table').innerHTML, /\+1\.50%p/);
  $('#rx-csv').onclick();
  assert.ok(exported[1].some(v => v.includes('(%p)')));
});
