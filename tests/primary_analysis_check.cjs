const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const formatters = source.slice(source.indexOf("  function measureList("), source.indexOf("  // 계산 방법:"));
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
assert.match(context.measureList(measure), /반응일 실제<\/span><span class="num ">-5\.00%/);
assert.doesNotMatch(context.measureList(measure), /예상 대비|%p/);          // 목록에는 실제 등락만 (분석 숫자는 상세 아래쪽)
assert.equal(context.measureList({ value: 0.9 }), "");
const prCount = context.countLine("pr");
assert.match(prCount, /최근 6개월 그린광학 보도자료<\/small><b>2건/);
assert.match(prCount, /한 건당 실린 매체 \(6개월 평균\)<\/small><b>2\.0곳/);
assert.match(prCount, /전체 \(2025-12-01부터\)<\/small><b>3건/);
assert.match(context.countLine("disc"), /시각을 아는 공시 \(6개월\)<\/small><b>1건/);
const box = context.baselineBlock();
assert.match(box, /소식 없는 날과 비교/);
assert.match(box, /<b>30%<\/b>.*40일 중 12일/s);
assert.doesNotMatch(box, /<details|평균 예상 대비 차이|class="note"/);
assert.match(box, /±5\.2%p 넘게 차이/);
context.d.baseline = null;
assert.equal(context.baselineBlock(), "");
// 피벗 표: 매체별/묶음 단위 세기와 합계
const piv = source.slice(source.indexOf("  const PIV_DIMS ="), source.indexOf("  async function pagePivot("));
const pctx = {};
vm.createContext(pctx);
vm.runInContext(piv + "; this.pivotRows = pivotRows; this.dashAgg = dashAgg;", pctx);
const arts = [{ id: "a", ts: "2026-10-08T09:00", office: "A", reporter: "홍", title: "t", kind: "company", category: "회사 발표", topic: "수주·계약",
  related: [{ id: "b", ts: "2026-10-08T10:00", office: "B", title: "t2" }] }, { id: "c", ts: "2026-09-01T09:00", office: "A", title: "t3", kind: "ext", topic: "기타", hidden: true }
  , { id: "d", ts: "2025-12-01T09:00", office: "A", reporter: "홍", title: "t4", kind: "ext", topic: "기타" }];
const outlet = pctx.pivotRows(arts, "outlet");
assert.equal(outlet.length, 3);
assert.equal(outlet[1].kind, "외부 기사");
assert.equal(pctx.pivotRows([{...arts[0], kind:"pr"}], "story")[0].kind, "그린광학 보도자료");
assert.equal(outlet[2].kind, "외부 기사");
assert.equal(outlet[1].topic, "수주·계약");
assert.equal(pctx.pivotRows(arts, "story").length, 2);
// 대시보드 막대: 기자는 '이름 · 언론사'로, 기자명 없는 기사는 빠짐 / 구분별 건수 / 월은 시간 순
const rep = pctx.dashAgg(outlet, "reporter");
assert.equal(rep.length, 1);
assert.equal(rep[0].key, "홍 · A");
assert.equal(rep[0].n, 2);
assert.equal(rep[0].by["그린광학 보도자료"], undefined);
assert.equal(rep[0].by["외부 기사"], 2);
assert.equal(JSON.stringify(pctx.dashAgg(outlet, "month").map((x) => x.key)), JSON.stringify(["2025-12", "2026-10"]));
assert.equal(JSON.stringify(pctx.dashAgg(outlet, "office").map((x) => [x.key, x.n])), JSON.stringify([["A", 2], ["B", 1]]));
console.log("새 대표 분석 화면 검증 통과: 실제 등락 분리·%p 단위·기존 값 제외·소식 없는 날 비교 상자·기사 대시보드 집계");
