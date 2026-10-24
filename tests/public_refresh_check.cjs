const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const crypto = require("node:crypto");
const { chromium } = require("playwright");

async function main() {
  const root = path.resolve(process.argv[2]);
  const key = crypto.pbkdf2Sync("public-site-password", Buffer.from("0123456789abcdef"), 200000, 32, "sha256");
  const measure = { method: "event-study-v2", label: "반응일 예상 대비 차이", basis: "반응일", value: -0.03,
    own: -0.05, expected: -0.02, ci_low: -0.05, ci_high: -0.01, p: 0.01, n_est: 120 };
  const study = { ok: true, actual: -0.05, expected: -0.02, ar: -0.03, ci_low: -0.05, ci_high: -0.01,
    car: [{ window: "반응일 [0,0]", car: -0.03, ci_low: -0.05, ci_high: -0.01 }], pending_windows: [],
    est_from: "2026-03-01", est_to: "2026-09-25", n_est: 120, gap: 10, method: "시장 회귀", basis: "일별 종가 기준" };
  let updated = false;
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      if (pathname === "/admin/") {
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end("<!doctype html><title>관리자 이동 검사</title>");
        return;
      }
      const filename = path.resolve(root, `.${pathname}${pathname.endsWith("/") ? "index.html" : ""}`);
      if (!filename.startsWith(root + path.sep)) throw new Error("잘못된 경로");
      let content = await fs.readFile(filename);
      if (["/d/live.bin", "/d/articles.bin", "/d/article_details.bin"].includes(pathname)) {
        const decoder = crypto.createDecipheriv("aes-256-gcm", key, content.subarray(0, 12));
        decoder.setAuthTag(content.subarray(-16));
        const data = JSON.parse(Buffer.concat([decoder.update(content.subarray(12, -16)), decoder.final()]));
        if (pathname === "/d/live.bin") data.published_at = updated ? "2026-10-23T14:03:00+09:00" : "2026-10-23T14:00:00+09:00";
        if (pathname === "/d/articles.bin") {
          const base = data.items[0];
          data.items = [-0.03, -0.01, 0.01, 0.9].map((value, index) => ({ ...base, id: index ? `demo${index}` : "demo", kind: "pr",
            effect: { reaction_day: `2026-10-${15 + index}`, verdict: "평소 예상보다 낮음", verdict_cls: "down",
              measure: { ...measure, value, method: index === 3 ? "legacy" : measure.method } } }));
          data.events = data.items.map((article) => ({ day: article.effect.reaction_day, lead: article.id, ids: [article.id], kind: "pr", outlets: 1 }));
          data.summary_critical_values = [null, null, 12.706204736, 4.30265273, 3.182446305];
        }
        if (pathname === "/d/article_details.bin") {
          data.demo.effect.measure = measure;
          data.demo.effect.event_study = study;
        }
        if (updated && pathname === "/d/article_details.bin") data.demo.effect.verdict = "새 자료 자동 반영 확인";
        const nonce = crypto.randomBytes(12), encoder = crypto.createCipheriv("aes-256-gcm", key, nonce);
        content = Buffer.concat([nonce, encoder.update(JSON.stringify(data)), encoder.final(), encoder.getAuthTag()]);
      }
      const mime = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
      response.writeHead(200, { "Content-Type": mime[path.extname(filename)] || "application/octet-stream" }); response.end(content);
    } catch (_) { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  try {
    const page = await browser.newPage();
    await page.clock.install({ time: new Date("2026-10-23T14:00:00+09:00") });
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pr/demo`);
    await page.getByPlaceholder("비밀번호", { exact: true }).fill("public-site-password");
    await page.getByRole("button", { name: "들어가기", exact: true }).click();
    await page.locator("#pr-detail .verdict-box").waitFor();
    const result = await page.locator("#pr-detail .verdict-box").innerText();
    assert.match(result, /실제 등락.*−5\.00%/);
    assert.match(result, /예상 대비 차이.*−3\.00%p/);
    assert.match(result, /95% 범위/);
    assert.match(await page.locator("#art-list").innerText(), /실제 −5\.00%/);
    assert.match(await page.locator("#pr-score").innerText(), /계산 3일 \/ 미계산 1일/);
    assert.match(await page.locator("#pr-score").innerText(), /−1\.00%p/);
    assert.equal((await page.locator("#pr-detail").innerText()).includes("판정 기준"), false);
    await page.getByLabel("기사·공시 검색").focus();
    updated = true;
    await page.clock.fastForward(31000);
    assert.equal((await page.locator("#pr-detail").innerText()).includes("새 자료 자동 반영 확인"), false);
    await page.getByRole("heading", { name: "기사 영향 분석", exact: true }).click();
    await page.clock.fastForward(31000);
    await page.getByText("새 자료 자동 반영 확인", { exact: true }).waitFor();
    assert.equal(page.url().endsWith("#/pr/demo"), true);
    console.log("기사 화면 자동 갱신 검증 통과: 새 배포 반영·입력 중 보류·선택 유지");
  } finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
