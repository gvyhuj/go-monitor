// Public (encrypted static) news page: automatic refresh, hold while typing, selection kept.
// Usage: node tests/public_refresh_check.cjs [SITE_DIR]
// Without SITE_DIR a synthetic site is built by tests/fixtures/public_refresh_site.py (Python: $PYTHON, .venv or python3).
// Browser: the preinstalled Playwright Chromium; set PW_CHANNEL (e.g. msedge) to use another installed channel.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const fsSync = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const { chromium } = require("playwright");

const PASSWORD = "public-site-password";
const SALT = Buffer.from("0123456789abcdef");
const FIRST = "1001", SECOND = "1002";

function python() {
  if (process.env.PYTHON) return process.env.PYTHON;
  const venv = path.join(__dirname, "..", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  return fsSync.existsSync(venv) ? venv : "python3";
}

async function siteRoot() {
  if (process.argv[2]) return { root: path.resolve(process.argv[2]), cleanup: async () => {} };
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "go-public-refresh-"));
  const root = path.join(dir, "site");
  execFileSync(python(), [path.join(__dirname, "fixtures", "public_refresh_site.py"), root], { stdio: "inherit" });
  return { root, cleanup: () => fs.rm(dir, { recursive: true, force: true }) };
}

async function main() {
  const { root, cleanup } = await siteRoot();
  const key = crypto.pbkdf2Sync(PASSWORD, SALT, 200000, 32, "sha256");
  // revision 0 = first publication; each later revision is a new publication with a visible detail change.
  let revision = 0, liveRequests = 0;
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      const filename = path.resolve(root, `.${pathname}${pathname.endsWith("/") ? "index.html" : ""}`);
      if (!filename.startsWith(root + path.sep)) throw new Error("잘못된 경로");
      let content = await fs.readFile(filename);
      if (["/d/live.bin", "/d/article_details.bin"].includes(pathname)) {
        const decoder = crypto.createDecipheriv("aes-256-gcm", key, content.subarray(0, 12));
        decoder.setAuthTag(content.subarray(-16));
        const data = JSON.parse(Buffer.concat([decoder.update(content.subarray(12, -16)), decoder.final()]));
        if (pathname === "/d/live.bin") {
          liveRequests += 1;
          data.published_at = `2026-10-23T14:${String(revision * 3).padStart(2, "0")}:00+09:00`;
        } else {
          for (const id of [FIRST, SECOND]) data[id].article.office = `${data[id].article.office} · 배포 ${revision}`;
        }
        const nonce = crypto.randomBytes(12), encoder = crypto.createCipheriv("aes-256-gcm", key, nonce);
        content = Buffer.concat([nonce, encoder.update(JSON.stringify(data)), encoder.final(), encoder.getAuthTag()]);
      }
      const mime = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css" };
      response.writeHead(200, { "Content-Type": mime[path.extname(filename)] || "application/octet-stream", "Cache-Control": "no-store" });
      response.end(content);
    } catch (_) { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.clock.install({ time: new Date("2026-10-23T14:00:00+09:00") });
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pr/${FIRST}`);
    await page.getByPlaceholder("비밀번호", { exact: true }).fill(PASSWORD);
    await page.getByRole("button", { name: "들어가기", exact: true }).click();

    const detail = page.locator("#pr-detail");
    const title = detail.locator(".pr-title");
    const selected = () => page.locator('#art-list button[data-id][aria-current="true"]').getAttribute("data-id");
    // One refresh tick (30 s), waiting until the page has really asked for the new publication.
    const tick = async () => {
      const before = liveRequests;
      await page.clock.fastForward(31000);
      for (let i = 0; i < 100 && liveRequests === before; i++) await page.waitForTimeout(50);
      assert.ok(liveRequests > before, "자동 갱신 확인 요청이 없음");
      await page.waitForTimeout(500);
    };

    await title.filter({ hasText: "검증용 광학 부품 공급 계약" }).waitFor();
    await detail.getByText("배포 0").first().waitFor();
    assert.equal(await selected(), FIRST);

    // 1. A new publication is applied automatically, on the same article.
    revision = 1;
    await tick();
    await detail.getByText("배포 1").first().waitFor();
    assert.match(await title.innerText(), /검증용 광학 부품 공급 계약/);
    assert.ok(page.url().endsWith(`#/pr/${FIRST}`));
    assert.equal(await selected(), FIRST);

    // 2. Another article chosen from the list stays selected across the refresh.
    await page.locator(`#art-list button[data-id="${SECOND}"]`).click();
    await title.filter({ hasText: "검증용 신규 설비 투자" }).waitFor();
    assert.ok(page.url().endsWith(`#/pr/${SECOND}`));

    // 3. While typing in the search box the refresh is held, and the typed text and focus are kept.
    const search = page.getByLabel("기사·공시 검색");
    await search.click();
    await search.pressSequentially("검증");
    revision = 2;
    await tick();
    assert.equal((await detail.innerText()).includes("배포 2"), false, "입력 중에 화면이 다시 그려짐");
    assert.equal(await search.inputValue(), "검증");
    assert.equal(await search.evaluate((x) => x === document.activeElement), true);

    // 4. After leaving the input, the held publication is applied and the selection is still the second article.
    await search.fill("");
    await page.getByRole("heading", { name: "뉴스", exact: true }).click();
    await tick();
    await detail.getByText("배포 2").first().waitFor();
    assert.match(await title.innerText(), /검증용 신규 설비 투자/);
    assert.ok(page.url().endsWith(`#/pr/${SECOND}`));
    assert.equal(await selected(), SECOND);

    // 5. With no new publication nothing is redrawn.
    await detail.evaluate((x) => { x.dataset.marker = "kept"; });
    await tick();
    assert.equal(await detail.getAttribute("data-marker"), "kept");

    assert.deepEqual(errors, []);
    console.log("기사 화면 자동 갱신 검증 통과: 새 배포 반영 · 입력 중 보류 · 선택 유지 · 변경 없을 때 유지");
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
    await cleanup();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
