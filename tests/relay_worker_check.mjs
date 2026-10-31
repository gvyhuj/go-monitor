// 실시간 중계 Worker 검사: 들어가는 값 확인, 토큰이 없으면 한투를 부르지 않음, 3초 안 같은 답, 한투 오류는 502
import assert from "node:assert/strict";
import { test } from "node:test";

const load = async () => (await import(`../core/relay_worker.js?x=${Math.random()}`)).default;
const env = (tok) => ({
  AUTH: "SECRET-AUTH", CODE: "000001", KIS_APP_KEY: "k", KIS_APP_SECRET: "s", ORIGIN: "https://owner.github.io",
  KV: { get: async (key, type) => (key === "kis_token" && tok ? tok : null) },
});
const req = (auth, path = "/live") => new Request(`https://go-relay.owner.workers.dev${path}`, { headers: auth ? { "x-go-auth": auth } : {} });
function kisFake(calls, fail) {
  return async (url, init) => {
    const u = new URL(url), tr = init.headers.tr_id;
    calls.push({ port: u.port, tr, auth: init.headers.authorization });
    if (fail) return new Response(JSON.stringify({ rt_cd: "1", msg1: "조회 처리 중 오류" }), { status: 500 });
    const out = {
      FHKST01010100: { output: { stck_prpr: "9900", prdy_vrss: "100", prdy_ctrt: "1.02", stck_oprc: "9800", stck_hgpr: "9950", stck_lwpr: "9780", stck_sdpr: "9800", acml_vol: "1234", acml_tr_pbmn: "12300000" } },
      FHPUP02100000: { output: { bstp_nmix_prpr: "730.10", bstp_nmix_prdy_vrss: "5.70", bstp_nmix_prdy_ctrt: "0.79" } },
      FHKST03010200: { output2: [
        { stck_bsop_date: "20261102", stck_cntg_hour: "100500", stck_oprc: "9890", stck_hgpr: "9900", stck_lwpr: "9880", stck_prpr: "9900", cntg_vol: "10" },
        { stck_bsop_date: "20261102", stck_cntg_hour: "100400", stck_oprc: "9880", stck_hgpr: "9890", stck_lwpr: "9870", stck_prpr: "9885", cntg_vol: "7" }] },
    }[tr];
    return new Response(JSON.stringify({ rt_cd: "0", ...out }), { status: 200 });
  };
}

test("wrong or missing auth is refused before any KIS call", async () => {
  const w = await load(), calls = [];
  globalThis.fetch = kisFake(calls);
  assert.equal((await w.fetch(req(null), env({ token: "T", exp_ms: Date.now() + 3600e3 }))).status, 401);
  assert.equal((await w.fetch(req("nope"), env({ token: "T", exp_ms: Date.now() + 3600e3 }))).status, 401);
  assert.equal(calls.length, 0);
});

test("no shared token (or about to expire) → 503 and KIS is not called (the Worker never issues tokens)", async () => {
  const w = await load(), calls = [];
  globalThis.fetch = kisFake(calls);
  assert.equal((await w.fetch(req("SECRET-AUTH"), env(null))).status, 503);
  assert.equal((await w.fetch(req("SECRET-AUTH"), env({ token: "T", exp_ms: Date.now() + 10e3 }))).status, 503);
  assert.equal(calls.length, 0);
});

test("live: quote + KOSDAQ + today's minute bars on port 9443 with the shared token; same answer within 3 seconds", async () => {
  const w = await load(), calls = [];
  globalThis.fetch = kisFake(calls);
  const res = await w.fetch(req("SECRET-AUTH"), env({ token: "TOK", exp_ms: Date.now() + 3600e3 }));
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("access-control-allow-origin"), "https://owner.github.io");
  const d = await res.json();
  assert.deepEqual(calls.map((c) => [c.port, c.tr, c.auth]), [["9443", "FHKST01010100", "Bearer TOK"], ["9443", "FHPUP02100000", "Bearer TOK"], ["9443", "FHKST03010200", "Bearer TOK"]]);
  assert.equal(d.quote.price, 9900); assert.equal(d.quote.rate, 1.02); assert.equal(d.kosdaq.price, 730.1);
  assert.deepEqual(d.bars.map((b) => [b.t, b.c]), [["2026-11-02T10:04", 9885], ["2026-11-02T10:05", 9900]]);
  await w.fetch(req("SECRET-AUTH"), env({ token: "TOK", exp_ms: Date.now() + 3600e3 }));
  assert.equal(calls.length, 3, "3초 안에는 한투를 다시 부르지 않음");
});

test("KIS error → 502 with the message (site keeps using the regular data)", async () => {
  const w = await load(), calls = [];
  globalThis.fetch = kisFake(calls, true);
  const res = await w.fetch(req("SECRET-AUTH"), env({ token: "TOK", exp_ms: Date.now() + 3600e3 }));
  assert.equal(res.status, 502);
  assert.match((await res.json()).error, /조회 처리 중 오류/);
});
