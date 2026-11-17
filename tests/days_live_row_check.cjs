// 일별 표: 장중(오늘 일봉이 아직 없을 때) 오늘 줄이 일별 표를 바로 열어도 나옴 — 차트 화면을 거쳐 왔을 때만 보이던 문제 (2026-11-17)
// Usage: node tests/days_live_row_check.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { chromium } = require("playwright");

async function main() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "go-days-live-"));
  const build = (name, minutes) => {
    const out = path.join(dir, name);
    execFileSync(process.env.PYTHON || "python3", [path.join(__dirname, "fixtures", "news_page_site.py"), out],
      { stdio: "inherit", env: { ...process.env, GO_FIXTURE_LIVE_DAY: "1", GO_FIXTURE_LIVE_MINUTES: String(minutes) } });
    return out;
  };
  const sites = [build("a", 31), build("b", 41)];      // b: 그 뒤 10분 더 쌓인 다음 판
  let root = sites[0];
  const server = http.createServer(async (req, res) => {
    try {
      const p = decodeURIComponent(new URL(req.url, "http://x").pathname);
      const f = path.resolve(root, `.${p}${p.endsWith("/") ? "index.html" : ""}`);
      if (!f.startsWith(root + path.sep)) throw new Error("path");
      const body = await fs.readFile(f);
      res.writeHead(200, { "Content-Type": { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css" }[path.extname(f)] || "application/octet-stream" });
      res.end(body);
    } catch (_) { res.writeHead(404); res.end(); }
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.clock.install({ time: new Date("2026-10-29T09:31:00+09:00") });
    await page.goto(`http://127.0.0.1:${server.address().port}/#/days`);      // 차트 화면을 거치지 않고 바로
    await page.getByPlaceholder("비밀번호", { exact: true }).fill("public-site-password");
    await page.getByRole("button", { name: "들어가기", exact: true }).click();
    await page.locator("#days-body tr").first().waitFor();
    const first = await page.locator("#days-body tr").first().locator("td").first().innerText();
    assert.equal(first, "2026-10-29", "일별 표를 바로 열어도 장중 오늘 줄이 맨 위에");
    // 열어 둔 채로 다음 판이 올라오면 오늘 줄이 저절로 새 값 (종가 칸)
    const close = () => page.locator("#days-body tr").first().locator("td").nth(4).innerText();
    const before = await close();
    root = sites[1];
    await page.clock.runFor(95000);           // 새 판 확인(60초) + 오늘 줄 갱신(30초)
    await page.waitForFunction((b) => document.querySelector("#days-body tr td:nth-child(5)")?.textContent !== b, before, { timeout: 10000 });
    assert.notEqual(await close(), before);
    console.log("일별 표 장중 오늘 줄 검증 통과: 바로 열어도 보임 · 열어 둔 동안 저절로 새 값");
  } finally { await browser.close(); server.close(); await fs.rm(dir, { recursive: true, force: true }); }
}
main().catch((e) => { console.error(e); process.exit(1); });
