// Synthetic publications: verified close, missing auction minute, pending confirmation and stale relay.
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
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "regular-close-"));
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
        data.published_at = "2026-10-28T15:30:10+09:00";
        const final = {date:"2026-10-28",source:"kis_daily",close:9870,confirmed_at:"2026-10-28T15:30:01"};
        data.status.quote = {price:revision ? 10070:9870,change:revision ? 320:120,change_pct:revision ? 3.28:1.23,
          market_status:"CLOSE",traded_at:"2026-10-28T15:30:00",regular_close:revision?null:final,regular_close_pending:!!revision,high:10010,low:9350};
        if (revision === 2) data.status.quote = {...data.status.quote, regular_close:{...final,date:"2026-10-27"}, regular_close_pending:true, traded_at:"2026-10-27T15:30:00"};
        data.intraday = {...data.intraday,date:"2026-10-28",events:[],prev_close:9750,
          bars:[{t:"2026-10-28T15:19",o:9850,h:9920,l:9850,c:9910,v:47},{t:"2026-10-28T15:30",o:9910,h:9910,l:9910,c:9910,v:0}],
          close_point:revision?null:{t:"2026-10-28T15:30",c:9870,source:"kis_daily"}};
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
      for (revision=0;revision<3;revision++) {
        const page=await browser.newPage({viewport:{width,height:900},reducedMotion:"reduce"});
        const errors=[];page.on("pageerror",e=>errors.push(e.message));
        await page.clock.setFixedTime(new Date("2026-10-28T15:30:30+09:00"));
        // Simulate a lingering 15:30 relay response: it must not override the verified daily close.
        await page.route("https://go-relay.test.workers.dev/live",r=>r.fulfill({status:200,contentType:"application/json",headers:{"access-control-allow-origin":"*"},body:JSON.stringify({at:revision===0?"2026-10-28T15:31":"2026-10-28T15:30",quote:{price:9910,change:160,rate:1.64},bars:[{t:"2026-10-28T15:30",c:9910,v:0}]})}));
        if (revision === 1) await page.route("https://go-relay.test.workers.dev/live",r=>r.fulfill({status:502,body:"{}",headers:{"access-control-allow-origin":"*"}}));
        await page.goto(`http://127.0.0.1:${server.address().port}/#/`);
        await page.getByPlaceholder("비밀번호",{exact:true}).fill(password);
        await page.getByRole("button",{name:"들어가기",exact:true}).click();
        await page.waitForFunction(()=>window.__closingPlot && document.querySelector("#rec-head .rh-p"));
        const result=await page.evaluate(()=>({header:document.querySelector("#rec-head .rh-p").innerText,label:document.querySelector("#rec-head .rh-d").innerText,last:window.__closingPlot.price.filter(x=>x.value!=null).at(-1),volume:window.__closingPlot.volume.at(-1)}));
        assert.match(result.header,revision === 2?/9,910/:revision?/10,070/:/9,870/);
        assert.match(result.label,revision === 2?/15:30 현재/:revision?/종가 확인 중/:/15:30 정규장 마감/);
        assert.equal(result.last.value,revision?9910:9870);
        if(!revision){assert.equal(result.volume.value,undefined);assert.match(await page.locator("#rec-chart .rc-head").innerText(),/한투 일봉 확인/);}
        assert.deepEqual(errors,[]);await page.close();
      }
    }
    console.log("정규장 종가 브라우저 검증 통과: PC·모바일 상단·차트 일치 / 거래량 미생성 / 오래된 중계 차단 / 미확인 표시 / 다음 거래일 갱신");
  } finally {
    await browser.close(); await new Promise((r) => server.close(r)); await fs.rm(dir, { recursive: true, force: true });
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
