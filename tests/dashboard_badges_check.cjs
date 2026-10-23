const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const fold = source.slice(source.indexOf("  function foldRow("), source.indexOf("  function dayChange("));
const card = source.slice(source.indexOf("  function dayCardHtml("), source.indexOf("  function compareLine("));
const context = {
  dayChange: () => -0.03, isDaily: (event) => event.event_type === "daily", kindOf: () => "하락",
  tierTag: () => "", dayLabel: () => "10월 23일", sgnCls: () => "down-t", pct: (value) => `${value * 100}%`,
  liveFor: () => null, hhmm: (timestamp) => timestamp.slice(11, 16), compareLine: () => "구간 안 -3% · KOSDAQ -1%",
  measureList: () => "",
  esc: (value) => String(value).replaceAll("<", "&lt;").replaceAll(">", "&gt;")
};
vm.createContext(context);
vm.runInContext(fold + card, context);
const data = { date: "2026-10-23", prev_close: 100, bars: [{ t: "2026-10-23T14:25", c: 97 }], events: [
  { id: 1, event_type: "daily", direction: "down", start_ts: "2026-10-23T09:00", peak_ts: "2026-10-23T09:09", peak_return_5m: -0.03 },
  { id: 2, event_type: "intraday", direction: "down", start_ts: "2026-10-23T09:00", peak_ts: "2026-10-23T09:09", peak_return_5m: -0.03 }
] };
for (const html of [context.foldRow(data, []), context.dayCardHtml(data, [], true)]) {
  assert.equal(html.includes("need-cause"), false);
  assert.equal(html.includes("원인 미입력"), false);
}
assert.match(context.dayCardHtml(data, [], true), /KOSDAQ -1%/);
data.events.forEach((event) => { event.cause_headline = "확인한 원인 <안전하게 표시>"; });
assert.doesNotMatch(context.foldRow(data, []), /확인한 원인/);                     // 접힌 줄에는 AI 글 대신 그날 소식
assert.match(context.foldRow(data, [{ kind: "pr", title: "공급 계약" }], { kosdaq: -0.01 }), /KOSDAQ -1%.*보도자료.*공급 계약/s);
assert.match(context.foldRow(data, []), /소식 없음/);
const opened = context.dayCardHtml(data, [], true);
assert.match(opened, /<details class="ai-memo"><summary>AI 메모<\/summary><span class="cause-text">확인한 원인 &lt;안전하게 표시&gt;/);
assert.doesNotMatch(opened, /<button[^>]*day-total[^>]*>(?:(?!<\/button>).)*ai-memo/s);          // 메모는 상세 열기 버튼 밖
console.log("일별 카드 검증 통과: 원인 미입력 배지 없음, 접힌 줄은 KOSDAQ·그날 소식, AI 메모는 접힘");
