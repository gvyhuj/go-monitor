const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const crypto = require("node:crypto");
const { chromium } = require("playwright");

async function main() {
  const root = path.resolve(process.argv[2]);
  const key = crypto.pbkdf2Sync("public-site-password", Buffer.from("0123456789abcdef"), 200000, 32, "sha256");
  let updated = false;
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      const filename = path.resolve(root, `.${pathname}${pathname.endsWith("/") ? "index.html" : ""}`);
      if (!filename.startsWith(root + path.sep)) throw new Error("잘못된 경로");
      let content = await fs.readFile(filename);
      if (["/d/live.bin", "/d/articles.bin", "/d/article_details.bin"].includes(pathname)) {
        const decoder = crypto.createDecipheriv("aes-256-gcm", key, content.subarray(0, 12));
        decoder.setAuthTag(content.subarray(-16));
        const data = JSON.parse(Buffer.concat([decoder.update(content.subarray(12, -16)), decoder.final()]));
        if (pathname === "/d/live.bin") data.published_at = updated ? "2026-10-23T14:03:00+09:00" : "2026-10-23T14:00:00+09:00";
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
    await page.clock.install();
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pr/demo`);
    await page.getByPlaceholder("비밀번호", { exact: true }).fill("public-site-password");
    await page.getByRole("button", { name: "들어가기", exact: true }).click();
    await page.locator("#pr-detail .verdict-box").waitFor();
    const adminLink = page.getByRole("link", { name: "관리자", exact: true });
    assert.equal(await adminLink.isVisible(), true);
    assert.equal(await adminLink.getAttribute("href"), "admin/");
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
