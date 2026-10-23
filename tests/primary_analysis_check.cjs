const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const formatters = source.slice(source.indexOf("  function measureList("), source.indexOf("  // 평소 기록과 비교한 드문 정도"));
const intervals = source.slice(source.indexOf("    const ciOf ="), source.indexOf("    // 공시 요약:"));
const summary = source.slice(source.indexOf("    function articleScore("), source.indexOf("    const renderScore ="));
const measures = [-0.03, -0.01, 0.01, 0.9].map((value, index) => ({ method: index === 3 ? "legacy" : "event-study-v2", value }));
const context = {
  esc: (value) => String(value), sgnCls: () => "", pct: (value) => `${(value * 100).toFixed(2)}%`,
  points: (value) => `${(value * 100).toFixed(2)}%p`, since: "2026-04-23",
  groups: { pr: measures.map((measure, index) => ({ day: "2026-10-15", lead: String(index), outlets: 1 })), ext: [] },
  byId: new Map(measures.map((measure, index) => [String(index), { effect: { measure } }])),
  d: { summary_critical_values: [null, null, 12.706204736, 4.30265273, 3.182446305] }
};
vm.createContext(context);
vm.runInContext(formatters + intervals + summary, context);
const measure = { method: "event-study-v2", own: -0.05, expected: -0.0541, value: 0.0041,
  ci_low: -0.08, ci_high: 0.09, label: "반응일 예상 대비 차이" };
const detail = context.measureTxt(measure);
assert.match(detail, /실제 등락.*-5\.00%/);
assert.match(detail, /0\.41%p/);
assert.match(detail, /실제 상승이 아니라/);
assert.match(context.measureList(measure), /실제 -5\.00%/);
assert.equal(context.measureList({ value: 0.9 }), "");
const score = context.articleScore("pr");
assert.match(score, /계산 3일 \/ 미확인·자료 부족 1일/);
assert.match(score, /-1\.00%p/);
assert.equal(context.articleScore("ext"), "");
assert.match(context.measureTxt({ value: 0.9 }), /다시 계산/);
console.log("새 대표 분석 화면 검증 통과: 실제 등락 분리·%p 단위·기존 값 제외·집계 표본 수");
