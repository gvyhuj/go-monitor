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
      const body = await fs.readFile(filename);     // 먼저 읽음: 폴더 등 실패면 404 (머리글을 두 번 쓰지 않게)
      response.writeHead(200, { "Content-Type": mime[path.extname(filename)] || "application/octet-stream" });
      response.end(body);
    } catch (_) { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  try {
    for (const [width, height] of [[1280, 900], [390, 844]]) {
      const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" });   // 움직임 없이 값 검사 (움직임은 아래 따로)
      await page.addInitScript(() => { window.__opened = []; window.open = (u) => { window.__opened.push(u); return null; }; });   // GitHub 창 대신 주소만 기록
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
      // README 탭 없음, 맨 아래 출처 문구·로그아웃 없음
      assert.deepEqual(await page.locator(".tabs a").allInnerTexts(), ["주가 기록", "뉴스"]);
      assert.equal((await page.locator("#foot").innerText()).trim(), "");
      assert.deepEqual((await list.evaluateAll((xs) => xs.map((x) => x.dataset.id))).sort(), ["2001", "2002", "2003"]);
      // 목록 숫자는 기본 +60분, 겹쳐 보기의 +30분을 누르면 목록도 +30분으로
      assert.match(await page.locator('#art-list button[data-id="2001"] .al-rx').innerText(), /^−2\.\d\d%\s*\+60분$/);
      await page.locator('#pr-detail .rx-ov-tools button[data-h="30"]').click();
      assert.match(await page.locator('#art-list button[data-id="2001"] .al-rx').innerText(), /^−1\.\d\d%\s*\+30분$/);
      await page.locator('#pr-detail .rx-ov-tools button[data-h="60"]').click();

      // Nothing selected: the right side is the overview of exactly the listed articles (median line).
      await page.locator("#pr-detail .rx-ov").waitFor();
      assert.match(await page.locator("#rx-n").innerText(), /대상 3건/);
      assert.ok(await page.locator("#rx-plot .rx-avg").count() > 0, "중앙값 선");
      assert.match(await page.locator("#rx-cards").innerText(), /\+60분 중앙값/);
      assert.doesNotMatch(await page.locator("#rx-cards").innerText(), /오른 \/ 내린|평균/);
      assert.equal(await page.locator("#rx-vskq").count(), 0, "KOSDAQ 빼고 보기 없음");

      // Sorting by reaction and the conditions apply to the list and the overview together.
      assert.deepEqual(await page.locator("#art-sort option").allInnerTexts(), ["최신순", "많이 오른 순", "많이 내린 순"]);
      await page.locator("#art-sort").selectOption("down");
      assert.equal(await list.first().getAttribute("data-id"), "2001");
      await page.locator("#art-sort").selectOption("up");
      assert.equal(await list.first().getAttribute("data-id"), "2002");
      await page.locator("#art-cond").click();
      assert.deepEqual(await page.locator('#art-pop button[data-ck="range"]').allInnerTexts(), ["전체", "3달", "6달", "1년", "직접 고르기"]);
      // 위 요약 칸은 조건을 따라감 (기본 1년, 이름도 1년)
      assert.match(await page.locator("#pr-score").innerText(), /기준\s*최근 1년\s*그린광학 보도자료 · 2025-10-29 ~ 2026-10-29[\s\S]*한 달 평균[\s\S]*한 건당 실린 매체[\s\S]*전체\s*3건\s*매체 기사 합계 3개/);
      await page.locator('#art-pop button[data-ck="mode"][data-cv="late"]').click();
      assert.equal(await list.count(), 0);
      assert.match(await page.locator("#pr-score").innerText(), /15시 이후[\s\S]*전체\s*0건/);
      // 기간을 3달·직접 고르기로 바꾸면 이름과 숫자가 따라감
      await page.locator('#art-pop button[data-ck="mode"][data-cv="all"]').click();
      await page.locator('#art-pop button[data-ck="range"][data-cv="92"]').click();
      assert.match(await page.locator("#pr-score").innerText(), /기준\s*최근 3달\s*그린광학 보도자료 · 2026-07-29 ~ 2026-10-29[\s\S]*전체\s*3건/);
      await page.locator('#art-pop button[data-ck="range"][data-cv="custom"]').click();
      await page.locator("#art-from").fill("2026-10-16"); await page.locator("#art-from").dispatchEvent("change");
      await page.locator("#art-to").fill("2026-10-22"); await page.locator("#art-to").dispatchEvent("change");
      assert.match(await page.locator("#pr-score").innerText(), /기준\s*직접 고른 기간\s*그린광학 보도자료 · 2026-10-16 ~ 2026-10-22[\s\S]*전체\s*2건/);
      await page.locator('#art-pop button[data-ck="range"][data-cv="365"]').click();
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
      assert.equal(await page.locator("#pr-close").innerText(), "← 전체 기사 보기");
      assert.equal(await page.locator("#obs-question").innerText(), "AI에게 묻기");
      assert.equal(await page.locator("#rx-tab-note").innerText(), "", "공개 직후 탭 아래 안내 문장 없음");
      // 왼쪽 목록의 다른 기사에 마우스를 올리면 오른쪽 그래프에서 그 기사 선이 진해짐 (그래프 위 마우스로는 제목을 띄우지 않음)
      if (width > 700) {
        const chartEl = page.locator("#pr-detail .rx-chart");
        await page.mouse.move(5, 5);
        const before = await chartEl.screenshot();
        await page.locator('#art-list button[data-id="2002"]').hover();
        const after = await chartEl.screenshot();
        assert.ok(!before.equals(after), "목록에서 고른 기사 선 강조");
        await page.mouse.move(5, 5);
        assert.ok((await chartEl.screenshot()).equals(before), "목록에서 벗어나면 원래대로");
        assert.equal(await page.locator(".rx-line-tip").count(), 0);
      }
      assert.match(await page.locator("#pr-cards").innerText(), /다른 기사 중앙값/);
      assert.doesNotMatch(await page.locator("#pr-cards").innerText(), /KOSDAQ/, "코스닥 값은 '코스닥과 비교' 표에만");
      assert.match(await page.locator("#pr-detail .obs-fold > summary", { hasText: "코스닥과 비교" }).first().innerText(), /코스닥과 비교/);
      // '수정' 메뉴: 여러 개 고르기·기사 등록·고른 기사의 목록에서 삭제
      assert.equal(await page.locator(".art-more > summary").innerText(), "수정 ▾");
      assert.equal(await page.locator(".art-more-menu #art-hide").textContent(), "목록에서 삭제");
      await page.locator('#pr-detail [data-rxr="5"]').click();
      assert.match(await page.locator("#pr-cards").innerText(), /공개 전 5거래일/);
      assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll("#pr-cards small, #pr-cards strong")]
        .filter((e) => e.scrollWidth > e.clientWidth + 1 || e.getBoundingClientRect().right > e.parentElement.getBoundingClientRect().right + 1).map((e) => e.textContent)), [], "카드 글자가 잘리지 않음");
      for (const label of ["유사 사건"]) {
        await page.locator("#pr-detail .obs-fold > summary", { hasText: label }).first().waitFor();
      }
      // 접는 항목은 유사 사건 하나: 실은 매체는 '수정' 메뉴로, 관련 소식·검토 기록·분류·연결 수정·분석 기준은 없앰
      assert.deepEqual((await page.locator("#pr-detail .obs-folds > .obs-fold > summary, #obs-extras > .obs-fold > summary").allInnerTexts()).map((t) => t.split("\n")[0]),
        ["유사 사건"]);
      // '수정' 메뉴 → 실은 매체 · 같은 내용 합치기 팝업
      if (width > 700) {
        await page.locator(".art-more > summary").click();
        await page.locator("#art-outlets").click();
        await page.locator("#outlets-pop .outlet-list li").first().waitFor();
        assert.match(await page.locator("#outlets-pop").innerText(), /이 기사를 실은 매체 · 같은 내용 합치기[\s\S]*검증일보/);
        assert.equal(await page.locator(".art-more").evaluate((x) => x.open), false, "메뉴는 닫힘");
        await page.keyboard.press("Escape");
        assert.equal(await page.locator("#outlets-pop").isVisible(), false);
        // 수정·엑셀로는 조건·정렬과 같은 높이, 오른쪽 끝
        const hs = await page.evaluate(() => ["#art-cond", "#art-sort", ".art-more > summary"].map((q) => Math.round((q === "#art-sort" ? document.querySelector(q).closest("label") : document.querySelector(q)).getBoundingClientRect().height)));
        assert.ok(Math.max(...hs) - Math.min(...hs) <= 1, `높이 ${hs}`);
        const right = await page.evaluate(() => { const c = document.querySelector("#art-cat").closest(".controls").getBoundingClientRect().right; return c - document.querySelector(".art-more").getBoundingClientRect().right; });
        assert.ok(right < 2, `수정 메뉴가 오른쪽 끝 (${right}px)`);
      }

      // AI에게 묻기: 질문 전체를 복사만 하고(새 창 없음) '질문이 복사되었습니다.' 알림이 잠깐 뜸
      assert.equal(await page.locator("#obs-note-open").count(), 0);
      await page.locator("#obs-question:not([disabled])").waitFor();
      await page.evaluate(() => { window.__copied = null; navigator.clipboard.writeText = async (t) => { window.__copied = t; }; window.__opened.length = 0; });
      await page.locator("#obs-question").click();
      await page.waitForFunction(() => window.__copied);
      const copied = await page.evaluate(() => window.__copied);
      assert.ok(copied.length > 8000 && copied.includes('"event_id"'), `질문 전체 복사 (${copied.length}자)`);
      assert.equal(await page.locator("#toast").innerText(), "질문이 복사되었습니다.");
      assert.equal(await page.evaluate(() => window.__opened.length), 0, "새 창을 열지 않음");

      // The event ID is visible for linking, and old 뉴스 반응 links open the same article in the list.
      const eid = await page.locator("#obs-extras").getAttribute("data-eid");
      assert.match(eid, /^ne-[0-9a-f]+$/);
      await page.locator("#pr-close").click();
      assert.equal(await page.evaluate(() => location.hash), "#/pr");
      await page.locator("#pr-detail .rx-ov").waitFor();
      // 겹쳐 보기: 중앙값·평균 문장 없음, 목록에 마우스를 올리면 그 기사 선 강조 + 제목
      assert.doesNotMatch(await page.locator("#rx-sum").innerText(), /중앙값|오른 \d/);
      await page.locator('#art-list button[data-id="2002"]').hover();
      assert.equal(await page.locator('#rx-plot g.rx-g.is-hot').getAttribute("data-id"), "2002");
      assert.match(await page.locator("#rx-hover").innerText(), /3분기 실적 설명회 개최/);
      await page.mouse.move(5, 5);
      assert.equal(await page.locator('#rx-plot g.rx-g.is-hot').count(), 0);
      // 전체로 간 뒤: 설명은 접혀 있고, '← 방금 본 기사'로 돌아감
      assert.equal(await page.locator("#rx-sum details.rx-sum-more").evaluate((x) => x.open), false);
      assert.match(await page.locator("#rx-sum details.rx-sum-more > summary").innerText(), /계산 기준 자세히 보기/);
      assert.match(await page.locator(".pr-back-title").innerText(), /광학미러 공급 계약 체결/);
      await page.locator("#pr-back").click();
      await page.locator("#pr-detail .pr-title").filter({ hasText: "광학미러 공급 계약 체결" }).waitFor();
      assert.equal(await page.evaluate(() => location.hash), "#/pr/2001");
      await page.locator("#pr-close").click();
      await page.locator("#pr-detail .rx-ov").waitFor();
      await page.goto(`${base}/#/react/event/${eid}`);
      await page.locator("#pr-detail .pr-title").filter({ hasText: "광학미러 공급 계약 체결" }).waitFor();
      assert.equal(await page.evaluate(() => location.hash), "#/pr/2001");
      await page.goto(`${base}/#/react`);
      await page.locator("#pr-detail .rx-ov").waitFor();
      assert.equal(await page.evaluate(() => location.hash), "#/pr");

      // 시각 출처 글자는 없고 '시각 고치기'만(회색), 카드 3개가 줄을 꽉 채움
      await page.goto(`${base}/#/pr/2003`);
      await page.locator("#pr-detail .pr-title").filter({ hasText: "반도체 장비용 렌즈 수주" }).waitFor();
      assert.doesNotMatch(await page.locator("#pr-detail .pr-meta").innerText(), /시각 출처/);
      assert.match(await page.locator("#pr-detail .intraday-box .vb-head").innerText(), /13:10 공개부터/, "머리 글과 그래프 기준 시각이 같음");
      assert.equal(await page.locator("#pr-detail .pr-meta [data-time-open]").innerText(), "시각 고치기");
      await page.locator("#pr-cards > div").first().waitFor();
      const cardGap = await page.evaluate(() => { const g = document.querySelector("#pr-cards"), c = [...g.children];
        return g.getBoundingClientRect().right - Math.max(...c.map((x) => x.getBoundingClientRect().right)); });
      assert.ok(cardGap < 2, `카드 오른쪽 빈 공간 ${cardGap}px`);
      const cardH = await page.evaluate(() => document.querySelector("#pr-cards > div").getBoundingClientRect().height);
      assert.ok(cardH < (width > 700 ? 45 : 64), `카드 높이 ${cardH}px (넓으면 한 줄, 좁으면 이름 위·값 아래)`);
      const hs = await page.evaluate(() => [...document.querySelectorAll("#pr-cards > div")].map((x) => Math.round(x.getBoundingClientRect().height)));
      assert.equal(new Set(hs).size, 1, `카드 높이가 모두 같음 ${hs}`);
      // 선행 공시: 이름이 보이고, 누르면 팝업으로 공시 내용
      assert.match(await page.locator("#pr-detail .lead-disc").innerText(), /선행 공시\s*단일판매ㆍ공급계약체결[\s\S]*13:02 접수 · 기사보다 8분 먼저/);
      await page.locator("#lead-disc-open").click();
      const pop = page.locator("#disc-pop");
      await pop.waitFor();
      assert.match(await pop.innerText(), /단일판매ㆍ공급계약체결[\s\S]*접수[\s\S]*13:02[\s\S]*DART에서 원문 보기/);
      await page.keyboard.press("Escape");
      assert.equal(await pop.isVisible(), false, "Esc로 팝업 닫힘");
      assert.equal(await page.evaluate(() => location.hash), "#/pr/2003", "팝업은 기사 화면을 떠나지 않음");

      // 유사 사건: 분야 태그가 겹치는 다른 사건만 (지금 기사와 같은 주제·예전 기록은 빼고, 전체 목록이 나오면 안 됨)
      await page.goto(`${base}/#/pr/2005`);
      await page.locator("#pr-detail .obs-fold > summary", { hasText: "유사 사건" }).click();
      const simRows = page.locator("#obs-results tbody tr");
      await simRows.first().waitFor();
      const sim = await simRows.allInnerTexts();
      assert.equal(sim.length, 1, sim.join(" / "));
      assert.match(sim[0], /우주항공 부품주 동향[\s\S]*겹침 우주항공/);
      assert.match(await page.locator("#obs-similar-sub").innerText(), /「우주항공」 · 1건/);
      await page.goto(`${base}/#/pr/2003`);
      await page.locator("#pr-detail .obs-fold > summary", { hasText: "유사 사건" }).click();
      await simRows.first().waitFor();
      const sim2 = await simRows.allInnerTexts();
      assert.ok(sim2.some((t) => t.includes("광학미러 공급 계약")) && !sim2.some((t) => /실적 설명회|우주항공/.test(t)), sim2.join(" / "));

      // 일별 표: 위 다섯 칸 줄 대신 고른 기간 요약 카드 4개 + 순매수·거래량 한 줄
      await page.goto(`${base}/#/days`);
      await page.locator(".ps-cards").waitFor();
      assert.equal(await page.locator("#hero").isHidden(), true);
      assert.deepEqual((await page.locator(".ps-card > small").allInnerTexts()), ["기간 등락 · 시장 대비", "기간 최고 · 최저", "오른 날 · 내린 날", "기간 소식"]);
      assert.match(await page.locator(".ps-range").innerText(), /2026-09-21 ~ 2026-10-28 · 28거래일/);
      assert.match(await page.locator(".ps-card").first().innerText(), /\+3\.91%[\s\S]*KOSDAQ[\s\S]*시장보다/);
      assert.match(await page.locator(".ps-news").innerText(), /보도자료 3[\s\S]*외부 3[\s\S]*주요 공시 1/);
      assert.match(await page.locator(".ps-line").innerText(), /순매수 합계[\s\S]*평균 거래량/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0);
      // 오른 날 → 그 날들만 표에
      const upN = +(await page.locator('[data-quick="up"]').innerText()).replace("일", "");
      await page.locator('[data-quick="up"]').click();
      assert.equal(await page.locator("#days-body tr").count(), upN);
      assert.ok((await page.locator("#days-body tr td:nth-child(6)").allInnerTexts()).every((t) => t.startsWith("+") && t !== "+0.00%"));
      // 최고 날 → 등락 고르기를 풀고 그날 줄로
      await page.locator(".ps-go[data-goto]").first().click();
      await page.locator("#days-body tr.is-flash").waitFor();
      // 기간 소식 → 같은 기간 뉴스 목록
      await page.locator("[data-news]").click();
      await page.waitForFunction(() => location.hash === "#/pr");
      await page.locator("#art-cond-txt").waitFor();
      assert.match(await page.locator("#art-cond-txt").innerText(), /2026-09-21|9\/21|09-21/);

      // 주요 숫자의 기간 수익률: 네이버처럼 'N개월 수익률' + ▲/▼
      await page.goto(`${base}/#/`);
      await page.locator(".rs-ret").waitFor();
      assert.deepEqual(await page.locator(".rs-ret small").allInnerTexts(), ["1개월 수익률", "3개월 수익률", "1년 수익률"]);
      assert.match(await page.locator(".rs-ret b").first().innerText(), /^[▲▼]\s?\d+\.\d\d%$|^0\.00%$|^-$/);
      assert.match(await page.locator(".rs-ret > div").first().getAttribute("title"), /종가 [\d,]+원 → .* [\d,]+원/);

      // 주가 기록 1일 차트: 날짜는 달력에서만 (키보드로 숫자를 넣어도 바뀌지 않음)
      await page.goto(`${base}/#/`);
      const jump = page.locator("[data-jump]");
      await jump.waitFor();
      // 머리 박스: 장중 최고·최저는 가격과 %, '종목이 시장보다' 문구 없음
      assert.match(await page.locator("#rec-head .rh-box").innerText(), /장중 최고 [\d,]+원 \([+−-]?\d+\.\d\d%\) \/ 최저 [\d,]+원 \(/);
      assert.doesNotMatch(await page.locator("#rec-head .rh-box").innerText(), /종목이 시장보다/);
      // 최고·최저 가격과 %는 같은 색 (오르면 빨강·내리면 파랑, 같으면 기본색)
      assert.deepEqual(await page.locator("#rec-head .rh-s span").evaluateAll((xs) => xs.map((x) => /원 \(/.test(x.textContent))), [true, true]);
      // 주가 기록 맨 위 박스: 시간외 문구처럼 긴 글도 박스 안에서 줄바꿈 (모바일 넘침 방지)
      const head = await page.evaluate(() => {
        const s = document.querySelector(".rh-s"), k = document.querySelector(".rh-kq small:last-child"), box = document.querySelector(".rh-box");
        s.textContent = "기준가 10,740원 → 시간외 포함 최종가 10,150원 · 장중 최고 −0.09% / 최저 −6.98% · 매우 긴 설명 문구 시험";
        if (k) k.textContent = "종목이 시장보다 −5.49%p · 시간외 포함 최종가 기준 비교";
        const r = box.getBoundingClientRect();
        return [...box.querySelectorAll("*")].map((x) => x.getBoundingClientRect().right - r.right).reduce((m, v) => Math.max(m, v), -1e9);
      });
      assert.ok(head <= 1, `박스 밖으로 ${head}px 넘침`);
      const before = await jump.inputValue();
      await jump.focus();
      await page.keyboard.type("20261001");
      assert.equal(await jump.inputValue(), before, "숫자 입력 막힘");

      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // 주가 기록 1일 차트: 30초마다 새 자료를 확인하지만, 바뀐 게 없으면 다시 그리지 않음 (깜빡임 없음)
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      await page.clock.install({ time: new Date("2026-10-29T12:00:00+09:00") });
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(`${base}/#/`);
      await page.getByPlaceholder("비밀번호", { exact: true }).fill(PASSWORD);
      await page.getByRole("button", { name: "들어가기", exact: true }).click();
      await page.locator("#rec-head .rh-box").waitFor();
      await page.locator('.rec-tabs button[data-range="1d"]').click();
      await page.locator("#rec-chart .rc-head").waitFor();
      await page.evaluate(() => {
        window.__chart = document.querySelector("#rec-chart .rc-head"); window.__seen = [];
        new MutationObserver(() => { if (document.querySelector("#rec-chart .loading")) window.__seen.push("loading"); })
          .observe(document.querySelector("#rec-chart"), { subtree: true, childList: true });
      });
      await page.clock.fastForward(31000);
      await page.waitForTimeout(300);
      await page.clock.fastForward(31000);
      await page.waitForTimeout(300);
      assert.ok(await page.evaluate(() => document.querySelector("#rec-chart .rc-head") === window.__chart), "같은 자료인데 1일 차트를 다시 그림");
      assert.deepEqual(await page.evaluate(() => window.__seen), [], "'불러오는 중'이 다시 나옴");
      assert.deepEqual(errors, []);
      await page.close();
    }
    // 주가 기록: 아래 칸은 네이버 테마 등락 (네이버 값 그대로), 누르면 그날 오른·내린 종목과 편입 이유
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      await page.clock.setFixedTime(new Date("2026-10-29T12:00:00+09:00"));
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(`${base}/#/`);
      await page.getByPlaceholder("비밀번호", { exact: true }).fill(PASSWORD);
      await page.getByRole("button", { name: "들어가기", exact: true }).click();
      const seg = page.locator("#rec-bento .b-seg");
      await seg.locator(".seg-chip").first().waitFor();
      assert.match(await seg.innerText(), /10\/28 테마 등락\s*네이버 테마[\s\S]*방위산업\/전쟁 및 테러\s*\+0\.80%[\s\S]*우주항공산업\s*−2\.40%/);
      assert.doesNotMatch(await seg.innerText(), /WICS|반도체/);
      await seg.getByRole("button", { name: /우주항공산업/ }).click();
      const pop = page.locator("#ind-modal");
      await pop.locator("#ind-body .ind-rate").waitFor();
      assert.match(await pop.innerText(), /10\/28 우주항공산업[\s\S]*−2\.40%[\s\S]*네이버 테마 '우주항공산업\(누리호\/인공위성 등\)'[\s\S]*오른 종목 3 · 내린 종목 15 · 전체 20[\s\S]*검증위성\s*\+3\.10%[\s\S]*검증방산\s*−4\.20%/);
      assert.doesNotMatch(await pop.innerText(), /넣은 이유/);
      assert.equal(await pop.getByRole("link", { name: /네이버에서 보기/ }).getAttribute("href"), "https://stock.naver.com/market/stock/kr/theme/1?no=7");
      await page.keyboard.press("Escape");
      // 하루 창: 업종(WICS) 비교 종목 대신 그날 네이버 테마 등락 (업종 몫 계산은 하지 않음)
      await page.locator("[data-head].is-click").first().click();
      const rel = page.locator("#drawer-body [data-ev-rel]");
      await rel.locator(".rel").waitFor();
      assert.match(await rel.innerText(), /그날 테마 등락[\s\S]*방위산업\/전쟁 및 테러[\s\S]*\+0\.80%[\s\S]*우주항공산업[\s\S]*−2\.40%/);
      assert.doesNotMatch(await page.locator("#drawer-body").innerText(), /사업 분야별 비교 종목|업종/);
      assert.equal(await rel.getByRole("link", { name: /우주항공산업/ }).getAttribute("href"), "https://stock.naver.com/market/stock/kr/theme/1?no=7");
      if (process.env.SHOT_DIR) { await page.waitForTimeout(800); await page.screenshot({ path: path.join(process.env.SHOT_DIR, "day-theme-drawer.png") }); }
      await page.keyboard.press("Escape");
      // 테마 기록 전 날: WICS로 채우지 않고 '-' + '기록 전' (마우스를 올리면 이유), 눌러도 링크는 있음
      await page.locator('.rec-tabs button[data-range="1d"]').click();
      await page.locator('[data-step="-1"]').first().click();
      await page.locator('[data-step="-1"]').first().click();
      await page.waitForFunction(() => /10\/26 테마 등락/.test(document.querySelector("#rec-bento .b-seg").innerText));
      assert.match(await seg.innerText(), /기록 전[\s\S]*방위산업\/전쟁 및 테러\s*-[\s\S]*우주항공산업\s*-/);
      assert.match(await seg.locator("[title*='10/27부터']").first().getAttribute("title"), /지난 기록을 주지 않아 10\/27부터/);
      await seg.getByRole("button", { name: /우주항공산업/ }).click();
      assert.match(await pop.innerText(), /이 날은 테마 기록이 없습니다[\s\S]*네이버에서 보기/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // 실시간 중계: 장중이면 15초마다 중계에 묻고(들어가는 값 머리말), 머리 가격·'실시간' 표시가 바뀜. 중계가 안 되면 정기 자료 그대로
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      await page.clock.install({ time: new Date("2026-10-28T10:05:10+09:00") });
      const errors = [], seen = [];
      page.on("pageerror", (error) => errors.push(error.message));
      let fail = false;
      await page.route("https://go-relay.test.workers.dev/live", (route) => {
        seen.push(route.request().headers()["x-go-auth"]);
        if (fail) return route.fulfill({ status: 502, body: "{}", headers: { "access-control-allow-origin": "*" } });
        return route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" },
          body: JSON.stringify({ at: "2026-10-28T10:05", quote: { price: 12345, change: 345, rate: 2.88, open: 12000, high: 12400, low: 11990, base: 12000, volume: 4321, value: 53000000 },
            kosdaq: { price: 812.34, change: 4.1, rate: 0.51 }, bars: [{ t: "2026-10-28T10:05", o: 12300, h: 12345, l: 12300, c: 12345, v: 9 }],
            kosdaq_bars: [{ t: "2026-10-28T10:05", c: 812.34 }] }) });
      });
      await page.goto(`${base}/#/`);
      await page.getByPlaceholder("비밀번호", { exact: true }).fill(PASSWORD);
      await page.getByRole("button", { name: "들어가기", exact: true }).click();
      await page.locator("#q-price").waitFor({ state: "attached" });
      // 새로고침 직후 15초를 기다리지 않고 바로 실시간 값 (예전엔 첫 15초 동안 정기 실행 시각의 값이 보였음)
      await page.clock.runFor(1000);
      await page.waitForFunction(() => document.querySelector("#q-price").textContent.includes("12,345"), null, { timeout: 3000 });
      // 메인 화면 큰 가격도 실시간 값으로 (예전엔 새로고침해야 바뀌었음)
      await page.waitForFunction(() => (document.querySelector("#rec-head .rh-p") || {}).textContent?.includes("12,345"), null, { timeout: 3000 });
      await page.clock.runFor(15000);
      assert.match(await page.locator("#q-change").textContent(), /345[\s\S]*2\.88%[\s\S]*10:05 기준/);
      assert.match(await page.locator("#engine").textContent(), /실시간 10:05/);
      assert.ok(seen.length >= 1 && seen.every((x) => x === "RELAY-AUTH"), "들어가는 값 머리말");
      fail = true;                                     // 중계가 안 되면: 화면은 마지막 값 그대로, 오류 없음
      await page.clock.runFor(16000);
      assert.match(await page.locator("#q-price").textContent(), /12,345/);
      // 30초마다 도는 정기 자료 갱신이 '실시간 10:05'를 '갱신 (정기 실행 시각)'으로 덮어 시각이 왔다 갔다 하지 않음 (실시간 값이 2분 안이면)
      await page.clock.runFor(15000);
      assert.match(await page.locator("#engine").textContent(), /실시간 10:05/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // 휴대폰: 네이버 테마 링크는 모바일 증권 주소 (PC 주소는 휴대폰에서 네이버 모바일 증권 첫 화면으로 넘어감)
    {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
        userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1" });
      await page.clock.setFixedTime(new Date("2026-10-29T12:00:00+09:00"));
      await page.goto(`${base}/#/`);
      await page.getByPlaceholder("비밀번호", { exact: true }).fill(PASSWORD);
      await page.getByRole("button", { name: "들어가기", exact: true }).click();
      const seg = page.locator("#rec-bento .b-seg");
      await seg.locator(".seg-chip").first().waitFor();
      await seg.getByRole("button", { name: /우주항공산업/ }).click();
      const pop = page.locator("#ind-modal");
      await pop.locator("#ind-body .ind-rate").waitFor();
      assert.equal(await pop.getByRole("link", { name: /네이버에서 보기/ }).getAttribute("href"), "https://m.stock.naver.com/domestic/home/theme/7");
      await page.close();
    }
    // 움직임: 기사 열기(떠오르기·제목 옮겨 가기), 겹쳐 보기 선 그리기, 기간 숫자 올라가기 — 끝나면 값은 그대로
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      await page.clock.setFixedTime(new Date("2026-10-29T12:00:00+09:00"));
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(`${base}/#/pr`);
      await page.getByPlaceholder("비밀번호", { exact: true }).fill(PASSWORD);
      await page.getByRole("button", { name: "들어가기", exact: true }).click();
      await page.locator("#pr-detail .rx-ov").waitFor();
      assert.equal(await page.locator("#rx-plot.mv-draw").count(), 1, "겹쳐 보기 선 그리기");
      // 조건을 바꿀 때마다 위 요약 숫자가 올라감 (처음 한 번만이 아니라)
      for (const cv of ["all", "92"]) {
        await page.locator("#art-cond").click();
        await page.locator(`#art-pop button[data-ck="range"][data-cv="${cv}"]`).click();
        const seen = new Set();
        for (let i = 0; i < 8; i++) { seen.add(await page.locator('#pr-score b[data-key="sc-pr-month"]').textContent()); await page.waitForTimeout(40); }
        assert.ok(seen.size > 1, `기간 ${cv}: 숫자가 바뀌며 올라감 ${[...seen]}`);
        await page.keyboard.press("Escape");
      }
      await page.locator('#art-list button[data-id="2001"]').click();
      await page.locator("#pr-detail .pr-title").waitFor();
      assert.equal(await page.locator("#pr-detail.mv-in").count(), 1, "기사 상세 떠오르기");
      assert.equal(await page.locator("#pr-detail .pr-title").evaluate((el) => el.getAnimations().length), 0, "제목 옮겨 가기 효과는 뺌");
      await page.locator("#pr-detail .rx-chart.mv-reveal").waitFor();
      assert.equal(await page.locator("#pr-detail").evaluate((el) => getComputedStyle(el).position), "sticky", "오른쪽 상세는 화면에 고정");
      // 오른쪽 상세 위에서 휠을 돌려도 화면이 내려감 (안에서 끝까지 내리면 화면으로 이어짐)
      assert.notEqual(await page.locator("#pr-detail").evaluate((el) => getComputedStyle(el).overscrollBehaviorY), "contain");
      // 주가 기록: 기간·1일 차트는 선 그리기 없이 바로 (메인 차트), 날짜를 바꾸면 머리 박스 숫자 올라가기
      await page.goto(`${base}/#/`);
      await page.locator("#rec-head .rh-box").waitFor();
      for (const n of [1, 2]) {
        await page.locator(".rec-tabs button").nth(n).click();
        await page.locator("#rec-chart .rc-line").waitFor();
        assert.equal(await page.locator("#rec-chart").evaluate((el) => el.classList.contains("mv-reveal") || el.getAnimations().length > 0), false, "기간 차트 선 그리기 없음");
      }
      await page.locator('.rec-tabs button[data-range="1d"]').click();
      await page.locator("#rec-chart .rc-head").waitFor();
      assert.equal(await page.locator("#rec-chart .mv-reveal").count(), 0, "1일 차트 나타나기 없음");
      await page.locator('[data-step="-1"]').first().click();
      await page.waitForFunction(() => document.querySelector("#rec-head [data-key='rh-price']"));
      assert.ok(await page.locator("#rec-head [data-key='rh-price']").evaluate((el) => el.closest(".rh-box") !== null));
      // 뉴스 대시보드: 위 숫자 올라가기, 작은 추이선·월별 추이 선 그리기
      await page.goto(`${base}/#/pivot`);
      await page.locator("#dash-kpi [data-count]").first().waitFor();
      assert.equal(await page.locator("#dash-kpi [data-count]").count(), 5);
      await page.locator("#dash-month.mv-reveal .dl-line").first().waitFor({ state: "attached" });
      // 폰 스크롤처럼 높이만 바뀌는 resize에는 다시 그리지 않음 (월별 추이 움직임이 두 번 나오던 문제)
      await page.evaluate(() => { window.__svg = document.querySelector("#dash-month svg"); window.dispatchEvent(new Event("resize")); });
      await page.waitForTimeout(400);
      assert.ok(await page.evaluate(() => document.querySelector("#dash-month svg") === window.__svg), "같은 폭이면 다시 그리지 않음");
      assert.equal(await page.locator("#dash-month svg").evaluate((el) => getComputedStyle(el).animationName), "none", "추이 그래프에 페이드가 겹치지 않음");
      // 상위 언론사·자주 쓴 기자: 막대가 왼쪽에서 자라고 건수가 올라감
      await page.locator("#dash-office.mv-grow .dash-row .bar").first().waitFor({ state: "attached" });
      assert.ok(await page.locator("#dash-office .dash-row .bar").first().evaluate((el) => el.getAnimations().length > 0), "막대 자라기");
      assert.ok(await page.locator("#dash-office .ct b[data-count]").count() > 0);
      await page.goto(`${base}/#/days`);
      await page.locator(".ps-cards").waitFor();
      assert.equal(await page.locator(".ps-cards").evaluate((el) => getComputedStyle(el).display), "grid", "기간 카드 옆으로 넘기기는 되돌림");
      await page.waitForFunction(() => /^\+3\.91%$/.test(document.querySelector(".ps-big").textContent));   // 숫자 올라가기가 끝나면 원래 값
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
