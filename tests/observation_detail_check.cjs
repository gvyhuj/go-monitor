// News reaction → event observation detail: opens right under the clicked row, keeps the list in place.
// Usage: node tests/observation_detail_check.cjs [SITE_DIR]   (site built like tests/public_refresh_check.cjs)
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const fsSync = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { chromium } = require("playwright");

const PASSWORD = "public-site-password";

function python() {
  if (process.env.PYTHON) return process.env.PYTHON;
  const venv = path.join(__dirname, "..", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  return fsSync.existsSync(venv) ? venv : "python3";
}

async function siteRoot() {
  if (process.argv[2]) return { root: path.resolve(process.argv[2]), cleanup: async () => {} };
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "go-observation-detail-"));
  const root = path.join(dir, "site");
  execFileSync(python(), [path.join(__dirname, "fixtures", "public_refresh_site.py"), root], { stdio: "inherit" });
  return { root, cleanup: () => fs.rm(dir, { recursive: true, force: true }) };
}

async function main() {
  const { root, cleanup } = await siteRoot();
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      const filename = path.resolve(root, `.${pathname}${pathname.endsWith("/") ? "index.html" : ""}`);
      if (!filename.startsWith(root + path.sep)) throw new Error("잘못된 경로");
      const mime = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css" };
      response.writeHead(200, { "Content-Type": mime[path.extname(filename)] || "application/octet-stream" });
      response.end(await fs.readFile(filename));
    } catch (_) { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  try {
    for (const [width, height] of [[1280, 900], [390, 844]]) {
      const page = await browser.newPage({ viewport: { width, height } });
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(`${base}/#/react`);
      await page.getByPlaceholder("비밀번호", { exact: true }).fill(PASSWORD);
      await page.getByRole("button", { name: "들어가기", exact: true }).click();
      const row = page.locator('#rx-table tr[data-id="1002"]');
      await row.waitFor();

      // Opening: right under the row, address changes without redrawing the page.
      await page.evaluate(() => { document.querySelector("#rx-ctl").dataset.marker = "kept"; });
      await row.click();
      const head = page.locator("#rx-detail .obs-head h2");
      await head.filter({ hasText: "검증용 신규 설비 투자" }).waitFor();
      assert.equal(await row.evaluate((r) => r.nextElementSibling?.classList.contains("rx-detail-row")), true);
      assert.equal(await page.locator("#rx-ctl").getAttribute("data-marker"), "kept", "페이지 전체가 다시 그려짐");
      assert.match(await page.evaluate(() => location.hash), /^#\/react\/event\/ne-/);
      const url = page.url();
      for (const label of ["공개 전후 주가 흐름", "관련 소식·공시", "당시 시장·업종 상황", "사건 검토 기록", "유사 사건 비교"])
        assert.ok((await page.locator("#rx-detail").innerText()).includes(label), label);

      // Typing inside the panel (inside the table) is not swallowed by row keyboard handling.
      await page.locator("#obs-note-open").click();
      const body = page.locator('#obs-note textarea[name="body"]');
      await body.pressSequentially("메모 공백 입력");
      assert.equal(await body.inputValue(), "메모 공백 입력");

      // Closing returns to the list; clicking the same row twice toggles.
      await page.locator("#obs-close").click();
      assert.equal(await page.locator(".rx-detail-row").count(), 0);
      assert.equal(await page.evaluate(() => location.hash), "#/react");
      await row.click(); await head.waitFor();
      await row.click();
      assert.equal(await page.locator(".rx-detail-row").count(), 0);

      // A shared link opens the same event under its row.
      await page.goto("about:blank"); await page.goto(url);
      await head.filter({ hasText: "검증용 신규 설비 투자" }).waitFor();
      assert.equal(await page.locator('#rx-table tr[data-id="1002"]').evaluate((r) => r.nextElementSibling?.classList.contains("rx-detail-row")), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log("사건 관찰 상세 검증 통과: 누른 줄 바로 아래 펼침 · 페이지 유지 · 입력 · 닫기 · 공유 링크 · 모바일");
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
    await cleanup();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
