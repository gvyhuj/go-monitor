const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright");

async function main() {
  const root = path.resolve(process.argv[2]);
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      const filename = path.resolve(root, `.${pathname}${pathname.endsWith("/") ? "index.html" : ""}`);
      if (!filename.startsWith(root + path.sep)) throw new Error("잘못된 경로");
      const content = pathname === "/admin/config.json"
        ? Buffer.from((await fs.readFile(path.join(root, "admin/config.js"), "utf8")).slice("window.GO_ADMIN=".length, -1))
        : await fs.readFile(filename);
      const mime = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".bin": "application/octet-stream" };
      response.writeHead(200, { "Content-Type": mime[path.extname(filename)] || "application/octet-stream" });
      response.end(content);
    } catch (_) { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/admin/config.js", async (route) => {
      const response = await route.fetch();
      const config = JSON.parse((await response.text()).slice("window.GO_ADMIN=".length, -1));
      config.salt = Buffer.alloc(16, 1).toString("base64");
      await route.fulfill({ response, body: `window.GO_ADMIN=${JSON.stringify(config)};` });
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/admin/`);
    await page.getByLabel("관리자 비밀번호").fill("public-site-password");
    await page.getByRole("button", { name: "이력 열기" }).click();
    await page.getByText("비밀번호가 맞지 않거나 자료를 열 수 없습니다.").waitFor();
    assert.equal(await page.locator("#workspace").isVisible(), false);
    await page.getByLabel("관리자 비밀번호").fill("admin-only-test-password-2026");
    await page.getByRole("button", { name: "이력 열기" }).click();
    await page.locator("#workspace").waitFor();
    await page.getByText("2개 기록을 비교할 수 있습니다.").waitFor();
    assert.match(await page.locator("#older-view").innerText(), /1\.00%p/);
    assert.match(await page.locator("#newer-view").innerText(), /2\.00%p/);
    assert.match(await page.locator("#differences").innerText(), /판정에 사용한 고유 움직임/);
    assert.equal(await page.locator("#entity img").count(), 0);
    assert.deepEqual(await page.evaluate(() => [localStorage.length, sessionStorage.length]), [0, 0]);
    await page.getByLabel("기사·주가 기록 찾기").fill("없는 검색어");
    await page.getByText("조건에 맞는 보관 기록이 없습니다.").waitFor();
    await page.getByLabel("기사·주가 기록 찾기").fill("");
    await page.getByText("2개 기록을 비교할 수 있습니다.").waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.screenshot({ path: path.join(path.dirname(root), "admin-preview.png"), fullPage: true });
    await page.getByRole("button", { name: "잠그기" }).click();
    assert.equal(await page.locator("#workspace").isVisible(), false);
    assert.equal(await page.locator("#older-view").innerText(), "");
    assert.equal(await page.locator("#newer-view").innerText(), "");
    assert.deepEqual(errors, []);
    console.log("관리 화면 검증 통과: 암호 분리, 비교, 검색, 모바일, 잠금, 저장소 미사용");
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
