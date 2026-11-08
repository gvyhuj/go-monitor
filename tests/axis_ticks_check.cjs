// 기사 그래프 오른쪽 축: 종목·KOSDAQ 이름표와 겹치는 눈금 글자는 그리지 않음 (반쯤 가려져 보이던 문제)
const { test } = require("node:test"); const assert = require("node:assert/strict"); const fs = require("node:fs"); const vm = require("node:vm");
const src = fs.readFileSync(require("node:path").join(__dirname, "../web/app.js"), "utf8");
const fn = (n) => { const a = src.indexOf("  function " + n + "("); assert.ok(a >= 0, n + " 없음"); return src.slice(a, src.indexOf("\n  }\n", a) + 4); };
test("이름표와 12px 안쪽으로 겹치는 눈금만 비움", () => {
  const c = { Math }; vm.createContext(c);
  vm.runInContext(fn("tickLabels"), c);
  const toY = (p) => 200 - p * 16;            // 1% = 16px
  const out = c.tickLabels([6, 4, 2, 0, -2, -4], toY, [toY(0.24), toY(-2.37)], (v) => `${v}%`);
  assert.deepEqual([...out], ["6%", "4%", "2%", "", "", "-4%"]);
});
