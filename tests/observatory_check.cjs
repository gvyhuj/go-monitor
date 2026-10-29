const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const src=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
const fn=n=>{const a=src.indexOf('  function '+n+'(');return src.slice(a,src.indexOf('\n  }',a)+4);};
function ctx(){const nodes=new Map();const $=s=>{if(!nodes.has(s))nodes.set(s,{innerHTML:'',textContent:'',dataset:{},querySelectorAll:()=>[]});return nodes.get(s);};const c={$ ,main:{},location:{hash:'#/react'},window:{addEventListener(){},removeEventListener(){}},setTimeout,clearTimeout,dashResize:null,artViewSeg:()=>'',esc:v=>String(v??''),sgnCls:()=>'',pct:v=>v==null?'-':(v*100).toFixed(2)+'%',points:v=>v==null?'-':(v*100).toFixed(2)+'%p',hhmm:t=>(t||'').slice(11,16),dayLabel:t=>t,isoDate:()=>'',MAIN_DISC:new Set(),BAR_TIP:'',toTime:t=>Date.parse(t+'Z')/1000};vm.createContext(c);vm.runInContext(fn('reactionAt')+fn('reactionSamples')+src.slice(src.indexOf('  const reactState ='),src.indexOf('  // 기사 대시보드:'))+'\nreactState.range="all";',c);return c;}
const fixture=()=>({id:'a',event_key:'a',ts:'2026-10-06T09:06',title:'synthetic',kind:'ext',interval:1,kq_interval:5,start:'2026-10-06T09:06',steps:{30:{own:.03,kq:null,diff:null}},path:{own:[[0,0],[1,.01],[4,.04],[9,.09]],kq:[[4,.01],[9,.02]]}});
test('T15 T25 T26: observed-only means, gap labels and unchanged numeric export',async()=>{
 const c=ctx(),a=fixture();c.api=async()=>({items:[a,{...a,id:'b',event_key:'b'}]});await c.pageReact();
 assert.match(c.$('#rx-plot').innerHTML,/rx-gap/);assert.match(c.$('#rx-plot').innerHTML,/관측 데이터 없는 구간/);assert.match(c.$('#rx-plot').innerHTML,/계산에 사용되지 않습니다/);
 let exported;c.csvDownloadRows=(...args)=>exported=args;c.$('#rx-csv').onclick();assert.equal(exported[2][0][4],'3.00');
 c.$('#rx-ctl').onchange({target:{id:'rx-vskq',checked:true}});
 const svg=c.$('#rx-plot').innerHTML;assert.match(svg,/유효 사건 2건/);assert.match(svg,/rx-gap average/);assert.match(svg,/그린광학 1분 \/ KOSDAQ 5분/);
 assert.equal((svg.match(/class="rx-avg"><title>/g)||[]).length,2); // Only common minutes 4 and 9.
 assert.match(c.$('#rx-table').innerHTML,/>-<\/td>/);
 c.api=async()=>({items:[a]});await c.pageReact();assert.doesNotMatch(c.$('#rx-plot').innerHTML,/class="rx-avg"/);
});
test('T27: four default sentences and expandable detailed explanation',async()=>{
 const c=ctx();c.api=async()=>({items:[fixture()]});await c.pageReact();const s=c.$('#rx-sum').innerHTML;
 for(const text of ['동일한 사건을 다룬 기사가 여러 개 있어도 통계에는 1건만 반영합니다.','주가 데이터가 부족한 경우 해당 구간의 계산에서 제외합니다.','평균은 분석 가능한 사건이 2건 이상일 때 표시합니다.','‘KOSDAQ 움직임 빼고 보기’를 선택하면 시장 전체의 상승·하락 영향을 단순 차감하여 비교합니다.'])assert.ok(s.includes(text));
 assert.match(s,/<details><summary>계산 기준 자세히 보기/);assert.match(s,/기사의 직접적인 효과를 뜻하지 않습니다/);
});
test('T16: question uses actual payload, missing data and subjective confidence language',()=>{
 const c=ctx();const q=c.observationQuestion({event_id:'ne-test',title:'fixture',observation:{intraday:null,missing_reason:'공개 시각 미상'},members:[{title:'source',url:'https://example.org'}],context:[],snapshot_version:'v-test'});
 assert.match(q,/공개 시각 미상/);assert.match(q,/https:\/\/example.org/);assert.match(q,/v-test/);assert.match(q,/추가 외부 기사·공시 검색/);assert.match(q,/주관적 평가/);
});
test('T12 T14: chart changes actual window and renders ratios without filling nulls',()=>{
 const c=ctx();const d={observation:{start:'2026-10-06T09:06',windows:{'5':{points:[{time:'2026-10-05',offset:-1,own:0,kq:0},{time:'2026-10-06',offset:0,own:null,kq:.01},{time:'2026-10-07',offset:1,own:.04,kq:.02}],base_day:'2026-10-05',base_own:100,base_kq:1000}}},context:[]};
 const html=c.observationChart(d,'5',false);assert.match(html,/4.00%/);assert.match(html,/-5거래일/);assert.match(html,/관측점 2 \/ KOSDAQ 3/);
 assert.doesNotMatch(html,/<path class="obs-own"/); // No bridge across missing day.
});
test('three article kinds; legacy company announcement is shown and filtered as an external article',async()=>{
 const c=ctx(),a=fixture();
 c.api=async()=>({items:[{...a,event_kind:'external_company_announcement'},{...a,id:'b',event_key:'b',kind:'pr',event_kind:'company_press_release'}]});await c.pageReact();
 const ctl=c.$('#rx-ctl').innerHTML;
 assert.deepEqual([...ctl.matchAll(/data-k="kind" data-v="(\w+)"[^>]*>([^<]+)</g)].map(m=>[m[1],m[2]]),[['all','전체'],['pr','당사 보도자료'],['ext','외부기사'],['disc','주요 공시']]);
 assert.doesNotMatch(ctl+c.$('#rx-table').innerHTML,/외부 기업 발표/);
 const click=v=>c.$('#rx-ctl').onclick({target:{closest:()=>({dataset:{k:'kind',v}})}});
 click('ext');let rows=c.$('#rx-table').innerHTML;assert.match(rows,/data-id="a"/);assert.doesNotMatch(rows,/data-id="b"/);assert.match(rows,/>외부기사</);
 click('pr');rows=c.$('#rx-table').innerHTML;assert.match(rows,/data-id="b"/);assert.doesNotMatch(rows,/data-id="a"/);assert.match(rows,/>보도자료</);
});
test('automatic company announcements stay in the press release group as before the observatory',async()=>{
 const c=ctx(),a=fixture();
 c.api=async()=>({items:[{...a,kind:'company',event_kind:'company_press_release'},{...a,id:'b',event_key:'b',kind:'ext',event_kind:'external_article'}]});await c.pageReact();
 const click=v=>c.$('#rx-ctl').onclick({target:{closest:()=>({dataset:{k:'kind',v}})}});
 click('pr');let rows=c.$('#rx-table').innerHTML;assert.match(rows,/data-id="a"/);assert.doesNotMatch(rows,/data-id="b"/);assert.match(rows,/>보도자료</);
 click('ext');rows=c.$('#rx-table').innerHTML;assert.match(rows,/data-id="b"/);assert.doesNotMatch(rows,/data-id="a"/);
});
