const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const src=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
const fn=n=>{const a=src.indexOf('  function '+n+'(');return src.slice(a,src.indexOf('\n  }',a)+4);};
function ctx(){const nodes=new Map();const $=s=>{if(!nodes.has(s))nodes.set(s,{innerHTML:'',textContent:'',dataset:{},querySelectorAll:()=>[]});return nodes.get(s);};const c={$ ,csvDownloadRows:()=>{},main:{},location:{hash:'#/react'},window:{addEventListener(){},removeEventListener(){}},setTimeout,clearTimeout,dashResize:null,artViewSeg:()=>'',esc:v=>String(v??''),sgnCls:()=>'',pct:v=>v==null?'-':(v*100).toFixed(2)+'%',points:v=>v==null?'-':(v*100).toFixed(2)+'%p',hhmm:t=>(t||'').slice(11,16),dayLabel:t=>t,isoDate:()=>'',MAIN_DISC:new Set(),BAR_TIP:'',toTime:t=>Date.parse(t+'Z')/1000};vm.createContext(c);vm.runInContext(fn('reactionAt')+fn('reactionSamples')+src.slice(src.indexOf('  const reactState ='),src.indexOf('  // 기사 대시보드:'))+'\nreactState.range="all";reactState.h="30";',c);return c;}
const fixture=()=>({id:'a',event_key:'a',ts:'2026-10-06T09:06',title:'synthetic',kind:'ext',interval:1,kq_interval:5,start:'2026-10-06T09:06',steps:{30:{own:.03,kq:null,diff:null}},path:{own:[[0,0],[1,.01],[4,.04],[9,.09]],kq:[[4,.01],[9,.02]]}});
const run=(c,code)=>vm.runInContext(code,c);   // const 값(reactState 등)은 vm 안에서만 보임
// '여러 기사 겹쳐 보기'(뉴스 목록 오른쪽): host 안에 그래프·요약을 그림
function ov(c,items){const parts=new Map();const host={innerHTML:'',querySelector:sel=>{if(!parts.has(sel))parts.set(sel,{});return parts.get(sel);},querySelectorAll:()=>[]};const r=c.reactionOverview(host,items);return {host,tools:host.querySelector('.rx-ov-tools'),...r};}
test('T15 T25 T26: observed-only medians, gap labels and unchanged numeric export',()=>{
 const c=ctx(),a=fixture();const items=[a,{...a,id:'b',event_key:'b'}];const v=ov(c,items);
 assert.match(c.$('#rx-plot').innerHTML,/rx-gap/);assert.match(c.$('#rx-plot').innerHTML,/관측 데이터 없는 구간/);assert.match(c.$('#rx-plot').innerHTML,/계산에 사용되지 않습니다/);
 let exported;c.csvDownloadRows=(...args)=>exported=args;c.reactionCsv(items);assert.equal(exported[2][0][4],'3.00');assert.equal(exported[2][0][5],''); // KOSDAQ +30분: 관측값 없음은 빈칸
 run(c,'reactState.vsKq=true');ov(c,items);   // 화면 체크박스는 없앴지만 계산 경로는 그대로 검사
 const svg=c.$('#rx-plot').innerHTML;assert.match(svg,/유효 사건 2건/);assert.match(svg,/rx-gap average/);assert.match(svg,/그린광학 1분 \/ KOSDAQ 5분/);
 assert.equal((svg.match(/class="rx-avg"><title>/g)||[]).length,2); // Only common minutes 4 and 9.
 run(c,'reactState.vsKq=false');ov(c,[a]);assert.doesNotMatch(c.$('#rx-plot').innerHTML,/class="rx-avg"/);
});
test('T27: four default sentences and expandable detailed explanation',()=>{
 const c=ctx();ov(c,[fixture()]);const s=c.$('#rx-sum').innerHTML;
 for(const text of ['동일한 사건을 다룬 기사가 여러 개 있어도 통계에는 1건만 반영합니다.','주가 데이터가 부족한 경우 해당 구간의 계산에서 제외합니다.','중앙값 선은 분석 가능한 사건이 2건 이상일 때 표시합니다.','시장과의 비교는 아래 ‘KOSDAQ 대비 중앙값’과 기사 화면의 ‘코스닥과 비교’에서 봅니다.'])assert.ok(s.includes(text));
 assert.match(s,/<details class="rx-sum-more"><summary>계산 기준 자세히 보기/);assert.match(s,/기사의 직접적인 효과를 뜻하지 않습니다/);
});
test('T16: question uses actual payload, missing data and subjective confidence language',()=>{
 const c=ctx();const q=c.observationQuestion({event_id:'ne-test',title:'fixture',observation:{intraday:null,missing_reason:'공개 시각 미상'},members:[{title:'source',url:'https://example.org'}],context:[],snapshot_version:'v-test'});
 assert.match(q,/공개 시각 미상/);assert.match(q,/https:\/\/example.org/);assert.match(q,/v-test/);assert.match(q,/추가 외부 기사·공시 검색/);assert.match(q,/주관적 평가/);
});
test('article chart overlay: other listed articles and their median as price changes, never the selected one',()=>{
 const c=ctx(),a=fixture();const mk=(id,v)=>({...a,id,event_key:id,steps:{30:{own:v,kq:.01,diff:v-.01}},path:{own:[[-40,0],[0,0],[4,v]],kq:[[0,0],[4,.01]]}});
 run(c,'reactState.vsKq=true');   // 'KOSDAQ 빼고 보기'를 켜 둬도 기사 그래프 겹침은 등락률
 const o=c.reactionOverlay([mk('a',.01),mk('b',.02),mk('c',.30)],'a');
 assert.equal(o.n,2);assert.equal(o.others.length,2);assert.equal(o.until,30);
 assert.deepEqual(o.others.map(sr=>sr.map(p=>p[0])),[[0,4],[0,4]]);           // −30분 이전은 자름
 assert.ok(Math.abs(o.others[0][1][1]-.02)<1e-9,'차이가 아니라 등락률');
 assert.ok(Math.abs(o.othersMedian-.16)<1e-9);                                   // 고른 기사를 뺀 b·c의 중앙값
 assert.equal(run(c,'reactState.vsKq'),true,'보기 설정은 그대로');
 assert.equal(c.reactionOverlay([{...a,kind:'disc',pblntf:'X'}],'z').n,0,'주요 공시가 아니면 비교에서 뺌');
});
test('three article kinds; legacy company announcement reads as an external article',()=>{
 const c=ctx();assert.equal(run(c,'JSON.stringify(Object.values(OBS_KIND))'),JSON.stringify(['당사 보도자료','외부기사','주요 공시']));
 assert.equal(run(c,"obsKind('external_company_announcement')"),'external_article');
});
test('blue line is the median: one large mover does not pull it; mean is not shown',()=>{
 const c=ctx(),a=fixture();
 const mk=(id,v)=>({...a,id,event_key:id,steps:{30:{own:v,kq:null,diff:null}},path:{own:[[0,0],[4,v]],kq:[]}});
 ov(c,[mk('a',.01),mk('b',.02),mk('c',.30)]);
 const svg=c.$('#rx-plot').innerHTML;
 const ys=[...svg.matchAll(/<circle cx="[^"]+" cy="([^"]+)"[^>]*class="rx-avg"/g)].map(m=>+m[1]);
 assert.equal(ys.length,2);                       // minutes 0 and 4
 const endY=id=>{const g=svg.slice(svg.indexOf(`data-id="${id}"`));const d=g.match(/class="rx-one[^"]*"(?: pathLength="1")? d="([^"]+)"/)[1];return +d.split(',').pop();};
 assert.ok(Math.abs(ys[1]-endY('b'))<0.1,'파란 점은 가운데 기사(2%)와 같은 높이');
 assert.ok(Math.abs(ys[1]-endY('c'))>10,'크게 튄 기사 쪽으로 끌려가지 않음');
 assert.doesNotMatch(c.$('#rx-sum').innerHTML,/중앙값 <b|오른 \d/);assert.match(c.$('#rx-cards').innerHTML,/2\.00%/);assert.doesNotMatch(c.$('#rx-cards').innerHTML,/평균|11\.00%/);   // 평균 카드는 뺌
 assert.match(c.$('#rx-cards').innerHTML,/\+30분 중앙값/);
 assert.match(svg,/실제 공통 시각의 중앙값/);
});
test('news list defaults to +60분 (the +30/+60 toggle still switches)',()=>{
 const src0=require('node:fs').readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
 assert.match(src0,/const reactState = \{[^}]*h: "60"/);
});
