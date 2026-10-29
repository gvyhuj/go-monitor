// News reaction → selected article: the single chart panel on the right switches to it, the list stays in place.
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
      const row = page.locator('#rx-table tr[data-id="1002"]'), rowCell = row.locator("td").first();   // 제목은 기사 상세로 가는 링크라 첫 칸을 누름
      await row.waitFor();

      // Opening: the right-hand chart panel switches to this article; the list and page stay as they are.
      await page.evaluate(() => { document.querySelector("#rx-ctl").dataset.marker = "kept"; });
      await rowCell.click();
      const head = page.locator("#rx-detail .obs-head h2");
      await head.filter({ hasText: "검증용 신규 설비 투자" }).waitFor();
      assert.equal(await page.locator("#rx-overview").isHidden(), true);
      assert.equal(await page.locator("#rx-detail").isVisible(), true);
      assert.equal(await page.locator("#rx-detail .obs-detail").count(), 1, "그래프 패널은 한 곳만");
      assert.equal(await page.locator("#rx-ctl").getAttribute("data-marker"), "kept", "페이지 전체가 다시 그려짐");
      assert.match(await page.evaluate(() => location.hash), /^#\/react\/event\/ne-/);
      assert.equal(await row.getAttribute("class"), "on");
      const url = page.url();
      assert.deepEqual(await page.locator("#rx-detail [data-obs-range]").allInnerTexts(), ["공개 직후", "전후 5거래일"]);
      for (const label of ["관련 소식·시장 상황", "검토 기록", "유사 사건", "분류·연결 수정", "분석 기준·출처"])
        assert.ok((await page.locator("#rx-detail .obs-fold > summary").allInnerTexts()).some((t) => t.includes(label)), label);

      // Typing inside the panel works; the note form opens from the action button.
      await page.locator("#obs-note-open").click();
      const body = page.locator('#obs-note textarea[name="body"]');
      await body.pressSequentially("메모 공백 입력");
      assert.equal(await body.inputValue(), "메모 공백 입력");

      // Closing returns to the overview chart; clicking the same row twice toggles.
      await page.locator("#obs-close").click();
      assert.equal(await page.locator("#rx-overview").isVisible(), true);
      assert.equal(await page.locator("#rx-detail").isHidden(), true);
      assert.equal(await page.evaluate(() => location.hash), "#/react");
      await rowCell.click(); await head.waitFor();
      await rowCell.click();
      assert.equal(await page.locator("#rx-detail").isHidden(), true);

      // A shared link opens the same article in the panel with its row marked.
      await page.goto("about:blank"); await page.goto(url);
      await head.filter({ hasText: "검증용 신규 설비 투자" }).waitFor();
      assert.equal(await page.locator('#rx-table tr[data-id="1002"]').getAttribute("class"), "on");
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log("사건 관찰 검증 통과: 오른쪽 그래프 한 곳에서 전환 · 페이지 유지 · 입력 · 전체로 · 공유 링크 · 모바일");
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
    await cleanup();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
