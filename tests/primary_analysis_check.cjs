const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const formatters = source.slice(source.indexOf("  function measureList("), source.indexOf("  // 평소 기록과 비교한 드문 정도"));
const counts = source.slice(source.indexOf("    const since = isoDate("), source.indexOf("    function baselineBlock("));
const summary = source.slice(source.indexOf("    function baselineBlock("), source.indexOf("    if (![\"pr\", \"ext\", \"disc\"]"));
const context = {
  esc: (value) => String(value), sgnCls: () => "", pct: (value) => `${(value * 100).toFixed(2)}%`,
  points: (value) => `${(value * 100).toFixed(2)}%p`,
  isoDate: () => "2026-04-23", byId: new Map(),
  groups: { pr: [{ day: "2026-10-15", outlets: 3 }, { day: "2026-09-01", outlets: 1 }, { day: "2025-12-01", outlets: 9 }], ext: [],
    disc: [{ ts: "2026-10-01T09:00", untimed: false }, { ts: "2026-08-01T15:30", untimed: true }] },
  d: { baseline: { from: "2024-10-23", to: "2026-10-22", cut: 0.052, top: 0.9, method: "시장 회귀", groups: {
    pr: { n: 40, mean: 0.02, median_abs: 0.031, big_share: 0.3, big_up: 9, big_down: 3 },
    disc: { n: 25, mean: 0.01, median_abs: 0.02, big_share: 0.2, big_up: 3, big_down: 2 },
    ext: { n: 60, mean: 0, median_abs: 0.015, big_share: 0.12, big_up: 4, big_down: 3 },
    none: { n: 300, mean: 0, median_abs: 0.012, big_share: 0.1, big_up: 16, big_down: 14 } } } }
};
vm.createContext(context);
vm.runInContext(formatters + counts + summary, context);
const measure = { method: "event-study-v2", own: -0.05, expected: -0.0541, value: 0.0041,
  ci_low: -0.08, ci_high: 0.09, label: "반응일 예상 대비 차이" };
const detail = context.measureTxt(measure);
assert.match(detail, /실제 등락.*-5\.00%/);
assert.match(detail, /0\.41%p/);
assert.match(detail, /실제 상승이 아니라/);
assert.match(context.measureList(measure), /실제 -5\.00%/);
assert.equal(context.measureList({ value: 0.9 }), "");
const prCount = context.countLine("pr");
assert.match(prCount, /최근 6개월 그린광학 보도자료<\/small><b>2건/);
assert.match(prCount, /한 건당 실린 매체 \(6개월 평균\)<\/small><b>2\.0곳/);
assert.match(prCount, /전체 \(2025-12-01부터\)<\/small><b>3건/);
assert.match(context.countLine("disc"), /시각을 아는 공시 \(6개월\)<\/small><b>1건/);
const box = context.baselineBlock();
assert.match(box, /소식 없는 날과 비교/);
assert.match(box, /<b>30%<\/b>.*40일 중 12일/s);
assert.doesNotMatch(box, /<details|평균 예상 대비 차이/);
context.d.baseline = null;
assert.equal(context.baselineBlock(), "");
assert.match(context.measureTxt({ value: 0.9 }), /다시 계산/);
console.log("새 대표 분석 화면 검증 통과: 실제 등락 분리·%p 단위·기존 값 제외·소식 없는 날 비교 상자");
