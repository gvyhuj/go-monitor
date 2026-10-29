// News page (뉴스 반응 merged into the list): one set of filters, reaction values in the list,
// one chart on the right — overview of the listed articles, or the selected article with the others and median overlaid.
// Usage: node tests/news_page_check.cjs [SITE_DIR]   (without SITE_DIR a synthetic site is built by tests/fixtures/news_page_site.py)
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
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "go-news-page-"));
  const root = path.join(dir, "site");
  execFileSync(python(), [path.join(__dirname, "fixtures", "news_page_site.py"), root], { stdio: "inherit" });
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
      await page.clock.setFixedTime(new Date("2026-10-29T12:00:00+09:00"));   // '최근 1년' 조건 기준
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(`${base}/#/pr`);
      await page.getByPlaceholder("비밀번호", { exact: true }).fill(PASSWORD);
      await page.getByRole("button", { name: "들어가기", exact: true }).click();
      const list = page.locator("#art-list button[data-id]");
      await list.first().waitFor();

      // One page: no separate 뉴스 반응 view; '회사 발표' articles are in 그린광학 보도자료.
      assert.deepEqual(await page.locator(".view-seg a").allInnerTexts(), ["목록", "대시보드"]);
      assert.deepEqual((await list.evaluateAll((xs) => xs.map((x) => x.dataset.id))).sort(), ["2001", "2002", "2003"]);
      assert.match(await page.locator('#art-list button[data-id="2001"] .al-rx').innerText(), /^−1\.\d\d%\s*\+30분$/);

      // Nothing selected: the right side is the overview of exactly the listed articles (median line).
      await page.locator("#pr-detail .rx-ov").waitFor();
      assert.match(await page.locator("#rx-n").innerText(), /대상 3건/);
      assert.ok(await page.locator("#rx-plot .rx-avg").count() > 0, "중앙값 선");
      assert.match(await page.locator("#rx-cards").innerText(), /\+30분 중앙값/);

      // Sorting by reaction and the conditions apply to the list and the overview together.
      await page.locator("#art-sort").selectOption("abs");
      assert.equal(await list.first().getAttribute("data-id"), "2002");
      await page.locator("#art-cond").click();
      await page.locator('#art-pop button[data-ck="mode"][data-cv="late"]').click();
      assert.equal(await list.count(), 0);
      await page.locator('#art-pop button[data-ck="mode"][data-cv="all"]').click();
      assert.equal(await list.count(), 3);
      await page.keyboard.press("Escape");
      assert.equal(await page.locator("#art-pop").isHidden(), true, "Esc로 조건 창 닫힘");
      await page.locator("#art-sort").selectOption("time");

      // Selecting: the same right side shows that article with the other listed articles and the median.
      await page.locator('#art-list button[data-id="2001"]').click();
      await page.locator("#pr-detail .pr-title").filter({ hasText: "광학미러 공급 계약 체결" }).waitFor();
      assert.equal(await page.evaluate(() => location.hash), "#/pr/2001");
      assert.equal(await page.locator('#art-list button[data-id="2001"]').getAttribute("aria-current"), "true");
      assert.deepEqual(await page.locator("#pr-detail [data-rxr]").allInnerTexts(), ["공개 직후", "전후 5거래일"]);
      assert.match(await page.locator("#rx-cmp").innerText(), /다른 그린광학 보도자료 2건/);
      await page.locator("#pr-detail .rx-chart canvas").first().waitFor();
      assert.match(await page.locator("#pr-cards").innerText(), /다른 기사 중앙값/);
      await page.locator('#pr-detail [data-rxr="5"]').click();
      assert.match(await page.locator("#pr-cards").innerText(), /공개 전 5거래일/);
      for (const label of ["이 기사를 실은 매체", "관련 소식·시장 상황", "검토 기록", "유사 사건", "분류·연결 수정", "분석 기준·출처"]) {
        await page.locator("#pr-detail .obs-fold > summary", { hasText: label }).first().waitFor();
      }

      // Result paste form opens and accepts typing.
      await page.locator("#obs-note-open:not([disabled])").click();
      const body = page.locator('#obs-note textarea[name="body"]');
      await body.pressSequentially("메모 공백 입력");
      assert.equal(await body.inputValue(), "메모 공백 입력");
      await page.locator("#obs-note-cancel").click();

      // The event ID is visible for linking, and old 뉴스 반응 links open the same article in the list.
      await page.locator("#pr-detail .obs-fold > summary", { hasText: "분석 기준·출처" }).click();
      const eid = await page.locator(".obs-eid").innerText();
      assert.match(eid, /^ne-[0-9a-f]+$/);
      await page.locator("#pr-close").click();
      assert.equal(await page.evaluate(() => location.hash), "#/pr");
      await page.locator("#pr-detail .rx-ov").waitFor();
      await page.goto(`${base}/#/react/event/${eid}`);
      await page.locator("#pr-detail .pr-title").filter({ hasText: "광학미러 공급 계약 체결" }).waitFor();
      assert.equal(await page.evaluate(() => location.hash), "#/pr/2001");
      await page.goto(`${base}/#/react`);
      await page.locator("#pr-detail .rx-ov").waitFor();
      assert.equal(await page.evaluate(() => location.hash), "#/pr");

      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log("뉴스 화면 검증 통과: 조건·정렬 한 곳 · 목록 반응 · 겹쳐 보기 ↔ 기사 그래프 · 기록 입력 · 예전 주소 · 모바일");
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
    await cleanup();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
