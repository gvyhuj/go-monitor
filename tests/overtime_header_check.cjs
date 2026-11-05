// Synthetic after-hours quotes: only the displayed headline changes; chart and recorded quote stay regular.
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
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "overtime-header-"));
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
      if (p === "/app.js") body = Buffer.from(body.toString().replace("price.setData(pdata);", "window.__closingPlot = {price:pdata,volume:vdata}; price.setData(pdata);"));
      if (p === "/d/live.bin") {
        const dec = crypto.createDecipheriv("aes-256-gcm", key, body.subarray(0, 12));
        dec.setAuthTag(body.subarray(-16));
        let plain = Buffer.concat([dec.update(body.subarray(12, -16)), dec.final()]);
        if (plain[0] === 31 && plain[1] === 139) plain = zlib.gunzipSync(plain);
        const data = JSON.parse(plain);
        data.published_at = "2026-10-28T17:40:00+09:00";
        const final = {date:"2026-10-28",source:"kis_daily",close:9920,base:9750,confirmed_at:"2026-10-28T16:00:00"};
        const after = {date:"2026-10-28",price:9950,volume:120,at:"17:40",queried_at:"2026-10-28T17:40",rate:.003};
        data.status.quote = {price:9920,change:170,change_pct:1.7435897,market_status:"CLOSE",traded_at:"2026-10-28T15:30:00",regular_close:final,
          high:10010,low:9350,after:revision===0?after:revision===2?{...after,price:0,volume:0}:revision===3?{...after,date:"2026-10-27"}:null};
        data.intraday = {...data.intraday,date:"2026-10-28",events:[],prev_close:9750,
          bars:[{t:"2026-10-28T15:19",o:9850,h:9920,l:9850,c:9910,v:47},{t:"2026-10-28T15:30",o:9910,h:9910,l:9910,c:9910,v:0}],
          close_point:{t:"2026-10-28T15:30",c:9920,source:"kis_daily"}};
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
    for (const width of [1280,390]) {
      for (revision=0;revision<7;revision++) {
        const page=await browser.newPage({viewport:{width,height:900},reducedMotion:"reduce"});
        let liveAfterPrice=9950;
        const errors=[];page.on("pageerror",e=>errors.push(e.message));
        await page.clock.setFixedTime(new Date("2026-10-28T17:45:10+09:00"));
        await page.route("https://go-relay.test.workers.dev/live",r=>r.fulfill({status:200,contentType:"application/json",headers:{"access-control-allow-origin":"*"},body:JSON.stringify(revision===6?{at:"2026-10-28T17:45",quote:{price:12000,change:2250,rate:23.08},bars:[{t:"2026-10-28T15:30",c:9910,v:0}]}:{session:"overtime",at:"2026-10-28T17:45",stale:revision===5,
          after:revision>=4?{date:"2026-10-28",price:liveAfterPrice,volume:120,at:"17:45",queried_at:"2026-10-28T17:45"}:null})}));
        await page.goto(`http://127.0.0.1:${server.address().port}/#/`);
        await page.getByPlaceholder("비밀번호",{exact:true}).fill(password);
        await page.getByRole("button",{name:"들어가기",exact:true}).click();
        await page.waitForFunction(()=>window.__closingPlot && document.querySelector("#rec-head .rh-p"));
        const active = revision===0 || revision===4;
        const text=await page.locator("#rec-head .rh-p").innerText();
        assert.match(text,active?/9,950/:/9,920/);
        assert.match(text,active?/2\.05%/:/1\.74%/);
        assert.equal(await page.locator("#q-price").textContent(),"9,920","stored regular quote does not change");
        assert.equal(await page.evaluate(()=>window.__closingPlot.price.filter(x=>x.value!=null).at(-1).value),9910,"chart preserves actual regular minute");
        if(active){
          assert.match(await page.locator("#rec-head .rh-session").innerText(),/시간외 단일가[\s\S]*조회/);
          assert.match(await page.locator("#rec-head .rh-move").innerText(),/\+200원 · 전일 대비/);
          assert.match(await page.locator("#rec-head .rh-after").innerText(),/정규장 종가 9,920원 · 15:30/);
        }else assert.match(await page.locator("#rec-head .rh-after").innerText(),/시간외 체결 정보 없음/);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth),0);
        if(revision===4){
          liveAfterPrice=9700;
          await page.evaluate(()=>window.dispatchEvent(new Event("focus")));
          await page.waitForFunction(()=>document.querySelector("#rec-head .rh-p").textContent.includes("9,700"));
          assert.match(await page.locator("#rec-head .rh-move").innerText(),/−0\.51%[\s\S]*−50원/);
          assert.equal(await page.locator("#q-price").textContent(),"9,920");
          assert.equal(await page.evaluate(()=>window.__closingPlot.price.filter(x=>x.value!=null).at(-1).value),9910);
        }
        if(revision===0){
          await fs.mkdir('/workspace/artifacts/overtime-preview',{recursive:true});
          await page.screenshot({path:`/workspace/artifacts/overtime-preview/header-${width}.png`});
          await page.locator('[data-step="-1"]').click();
          assert.equal(await page.locator("#rec-head .rh-session").count(),0,"past date must not show today's overtime quote");
        }
        assert.deepEqual(errors,[]);await page.close();
      }
    }
    console.log("시간외 표시 검증 통과: PC·모바일 전일 대비 / 정규장 기록 유지 / 무체결·0·과거·오류 응답 제외 / 중계 반영");
  } finally {
    await browser.close(); await new Promise((r) => server.close(r)); await fs.rm(dir, { recursive: true, force: true });
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
