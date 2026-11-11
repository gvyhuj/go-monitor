// "갱신 지연" 원인 가르기: '주가 감시' 실행만 봐야 함 (다른 워크플로의 성공으로 "수집은 정상"이라 하면 안 됨)
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const code = source.slice(source.indexOf("  let RUN_HEALTH = null"), source.indexOf("  /** 위 표시 한 칸"));
const now = new Date().toISOString();
const monitor = [{ conclusion: "failure", created_at: now, path: ".github/workflows/monitor.yml" }];
const other = [{ conclusion: "success", created_at: now, path: ".github/workflows/observatory-tests.yml" }];
const context = {
  STATIC: { repo: "o/r" }, Date,
  fetch: async (url) => ({ ok: true, json: async () => ({ workflow_runs: url.includes("/workflows/monitor.yml/") ? monitor : [...other, ...monitor] }) }),
};
vm.createContext(context);
vm.runInContext(`${code}; globalThis.runHealth = runHealth;`, context);
context.runHealth().then((health) => {
  assert.equal(health, "runner");
  console.log("run health check passed");
});
