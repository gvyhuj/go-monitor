// 10% 이상 오른 날 축하 효과: 하루에 한 번만, '동작 줄이기'면 안 함
const { test } = require("node:test"); const assert = require("node:assert/strict"); const fs = require("node:fs"); const vm = require("node:vm");
const src = fs.readFileSync(require("node:path").join(__dirname, "../web/app.js"), "utf8");
const fn = (n) => { const a = src.indexOf("  function " + n + "("); return src.slice(a, src.indexOf("\n  }\n", a) + 4); };
const line = (start) => { const a = src.indexOf(start); return src.slice(a, src.indexOf("\n", a)); };
function ctx(reduce = false) {
  const store = new Map(); const made = [];
  const c = { innerWidth: 800, innerHeight: 600, performance: { now: () => 0 }, requestAnimationFrame: () => 0, Math,
    window: { devicePixelRatio: 1, matchMedia: () => ({ matches: reduce }) }, matchMedia: () => ({ matches: reduce }),
    localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) },
    document: { createElement: () => { const cv = { getContext: () => ({ scale() {}, clearRect() {}, save() {}, restore() {}, translate() {}, rotate() {}, fillRect() {} }), remove() {} }; made.push(cv); return cv; },
      body: { appendChild() {} } } };
  vm.createContext(c);
  vm.runInContext(line("  const MOTION = ") + "\n" + fn("confettiOnce"), c);
  return { c, made };
}
test("confetti: only at +10% or more, once per day per device", () => {
  const { c, made } = ctx();
  assert.equal(c.confettiOnce("2026-10-29", 0.099), false);
  assert.equal(c.confettiOnce("2026-10-29", 0.10), true);
  assert.equal(c.confettiOnce("2026-10-29", 0.15), false, "같은 날 두 번째는 안 함");
  assert.equal(c.confettiOnce("2026-10-30", 0.12), true, "다음 날은 다시");
  assert.equal(made.length, 2);
});
test("confetti: off with reduced motion", () => {
  const { c, made } = ctx(true);
  assert.equal(c.confettiOnce("2026-10-29", 0.2), false);
  assert.equal(made.length, 0);
});
