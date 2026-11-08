// Synthetic encrypted publications: regular refresh must update the visible record header even when relay fails.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const fsSync = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const zlib = require("node:zlib");
const { execFileSync } = require("node:child_process");
const { chromium } = require("playwright");

(async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "quote-header-"));
  const root = path.join(dir, "site"), password = "public-site-password";
  const venv = path.join(__dirname, "../.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  execFileSync(process.env.PYTHON || (fsSync.existsSync(venv) ? venv : "python3"), [path.join(__dirname, "fixtures/news_page_site.py"), root]);
  const key = crypto.pbkdf2Sync(password, "0123456789abcdef", 200000, 32, "sha256");
  let revision = 0;
  const server = http.createServer(async (req, res) => {
    try {
      const p = new URL(req.url, "http://localhost").pathname;
      const filename = path.join(root, p === "/" ? "index.html" : p);
      if (!filename.startsWith(root + path.sep)) throw new Error("invalid path");
      let body = await fs.readFile(filename);
      if (p === "/d/live.bin") {
        const dec = crypto.createDecipheriv("aes-256-gcm", key, body.subarray(0, 12));
        dec.setAuthTag(body.subarray(-16));
        let plain = Buffer.concat([dec.update(body.subarray(12, -16)), dec.final()]);
        if (plain[0] === 31 && plain[1] === 139) plain = zlib.gunzipSync(plain);
        const data = JSON.parse(plain);
        data.published_at = `2026-10-28T10:${41 + revision * 6}:00+09:00`;
        delete data.status.relay;
        data.status.quote = { price: 10500 + revision * 100, change: 150 + revision * 100, change_pct: 1.45 + revision,
          market_status: "OPEN", traded_at: `2026-10-28T10:${40 + revision * 6}:00`, high: 10700, low: 10350, volume: 1000 };
        const nonce = crypto.randomBytes(12), enc = crypto.createCipheriv("aes-256-gcm", key, nonce);
        body = Buffer.concat([nonce, enc.update(zlib.gzipSync(JSON.stringify(data))), enc.final(), enc.getAuthTag()]);
      }
      res.writeHead(200, { "Content-Type": ({ ".html": "text/html", ".css": "text/css", ".js": "text/javascript" })[path.extname(filename)] || "application/octet-stream", "Cache-Control": "no-store" });
      res.end(body);
    } catch (_) { res.writeHead(404); res.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  try {
    for (const width of [1280, 390]) {
      revision = 0;
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.clock.install({ time: new Date("2026-10-28T10:50:00+09:00") });
      await page.goto(`http://127.0.0.1:${server.address().port}/#/`);
      await page.getByPlaceholder("비밀번호", { exact: true }).fill(password);
      await page.getByRole("button", { name: "들어가기", exact: true }).click();
      await page.locator("#rec-head .rh-box").waitFor();
      assert.match(await page.locator("#rec-head").innerText(), /10:40 현재/);
      revision = 1;
      await page.clock.fastForward(62000);
      await page.waitForFunction(() => document.querySelector("#q-price").textContent.includes("10,600"));
      await page.clock.runFor(700);
      assert.match(await page.locator("#engine").innerText(), /10:47/);
      assert.match(await page.locator("#rec-head").innerText(), /10:46 현재/, "정기 갱신 시 차트 머리의 시각도 반영");
      assert.match(await page.locator("#rec-head .rh-p").innerText(), /10,600/);
      // Equal data should not rebuild the header; background updates must not replace a selected past day.
      await page.locator("#rec-head").evaluate((el) => { el.firstElementChild.dataset.marker = "kept"; });
      await page.clock.fastForward(62000);
      await page.waitForTimeout(250);
      assert.equal(await page.locator("#rec-head .rh-box").getAttribute("data-marker"), "kept");
      // 날짜 이동(‹ 날짜 › 오늘·달력)은 가격 카드 안에 한 줄로: 차트 위 따로 있던 버튼 줄은 없음
      assert.equal(await page.locator("#rec-chart .rc-head [data-step], #rec-chart .rc-head [data-today], #rec-chart .rc-head [data-jump]").count(), 0);
      assert.equal(await page.locator("#rec-head [data-jump]").count(), 1);
      const before = await page.locator("#rec-head .rh-date").innerText();
      await page.locator('#rec-head [data-step="-1"]').click();
      await page.waitForFunction((b) => document.querySelector("#rec-head .rh-date").innerText !== b, before);
      assert.equal(await page.locator("dialog[open]").count(), 0, "날짜 화살표는 하루 분석 창을 열지 않음");
      const date = await page.locator("#rec-head .rh-d").innerText();
      revision = 2;
      await page.clock.fastForward(62000);
      await page.waitForFunction(() => document.querySelector("#engine").textContent.includes("10:53"));
      assert.equal(await page.locator("#rec-head .rh-d").innerText(), date);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log("정기 시세 갱신 검증 통과: PC·모바일 머리 시각·가격 반영 / 같은 값 유지 / 과거 날짜 유지");
  } finally {
    await browser.close(); await new Promise((r) => server.close(r)); await fs.rm(dir, { recursive: true, force: true });
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
