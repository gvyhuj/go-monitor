/* 주가 모니터 – 화면 스크립트
 * 데이터: PC에서는 webserver.py(/api/...), 웹사이트(GitHub Pages)에서는 암호화된 d/*.bin 파일. */
(() => {
  "use strict";
  const LW = window.LightweightCharts;
  const $ = (sel, el = document) => el.querySelector(sel);
  const main = $("#main");
  const KIND = { up: "급등", down: "급락", flat: "거래량만 급증" };
  const kindOf = (e) => (e.tier === "관찰" || e.tier === "요청" ? ({ up: "상승", down: "하락" }[e.direction] || KIND[e.direction]) : KIND[e.direction]);
  const NO_CAUSE = '<span class="muted">원인 미입력</span>';
  const scopeTag = (e) => `<span class="scope">${e.scope === "하루" || e.event_type === "daily" ? "하루" : "장중"}</span>`;
  const tierTag = (e) => (e.tier === "대형" ? `<span class="tier">대형</span>`
    : e.tier === "관찰" ? `<span class="tier watch" title="3~5% 움직임: 기록만 하고 알림은 보내지 않습니다">관찰</span>` : "");
  const isDaily = (e) => e.scope === "하루" || e.event_type === "daily";
  const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

  let STATUS = null;          // 마지막 /api/status
  let charts = [];            // 페이지 전환 시 정리할 차트
  let timers = [];            // 페이지 전용 타이머

  // ---------------------------------------------------------------- format
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const won = (v) => (v == null ? "-" : Math.round(v).toLocaleString("ko-KR"));
  const int = (v) => (v == null ? "-" : Math.round(v).toLocaleString("ko-KR"));
  const pct = (v, d = 2) => (v == null ? "-" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(d)}%`);
  const points = (value) => value == null ? "-" : `${pct(value)}p`;
  const pctRaw = (v, d = 2) => (v == null ? "-" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}%`);
  const sgnCls = (v) => (v == null ? "" : v > 0 ? "up-t" : v < 0 ? "down-t" : "");
  const parseT = (t) => { const [d, hm] = t.split("T"); const [y, m, dd] = d.split("-").map(Number); const [h, mi] = (hm || "00:00").split(":").map(Number); return { y, m, d: dd, h, mi }; };
  const toTime = (t) => { const p = parseT(t); return Date.UTC(p.y, p.m - 1, p.d, p.h, p.mi) / 1000; };
  const hhmm = (t) => (t ? t.slice(11, 16) : "");
  const dayLabel = (t) => { const p = parseT(t); const w = new Date(Date.UTC(p.y, p.m - 1, p.d)).getUTCDay(); return `${p.m}월 ${p.d}일 (${WEEK[w]})`; };
  const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const utcHHMM = (sec) => { const d = new Date(sec * 1000); return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`; };
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const STATIC = window.GO_STATIC || null;   // GitHub Pages 사이트 (자료는 비밀번호로 암호화되어 있음)
  const CLOUD = !!window.GO_CLOUD || !!STATIC; // PC 화면이 아닌 '어디서나 보기' 사이트에서 열렸는지
  async function rawApi(path, opts) {
    const res = await fetch(path, opts);
    const body = await res.json().catch(() => ({}));
    if (res.status === 401 && body.login) { location.href = "/login"; throw new Error("로그인이 필요합니다."); }
    if (!res.ok) throw new Error(body.error || `요청 실패 (${res.status})`);
    return body;
  }
  async function api(path, opts) {
    if (CLOUD && (!opts || !opts.method || opts.method === "GET")) return cloudApi(path);
    return rawApi(path, opts);
  }

  // ---- 정적 사이트: 비밀번호 → 열쇠(PBKDF2) → 자료 파일(d/*.bin, AES-GCM) 풀기
  let SKEY = null;
  const KEY_NAME = STATIC ? `go_key_${STATIC.salt}` : "";
  const b64d = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const b64e = (u8) => { let s = ""; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode(...u8.subarray(i, i + 0x8000)); return btoa(s); };
  const store = (kind) => { try { return window[kind]; } catch (_) { return null; } };
  function savedKey() {
    for (const k of ["localStorage", "sessionStorage"]) { try { const v = store(k) && store(k).getItem(KEY_NAME); if (v) return v; } catch (_) {} }
    return null;
  }
  function forgetKey() {
    for (const k of ["localStorage", "sessionStorage"]) {
      try {
        const st = store(k); if (!st) continue;
        Object.keys(st).filter((x) => x.startsWith("go_key_")).forEach((x) => st.removeItem(x));
      } catch (_) {}
    }
  }
  async function deriveKey(pw) {
    const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: b64d(STATIC.salt), iterations: STATIC.iter }, base, 256);
    return new Uint8Array(bits);
  }
  const aesKey = (raw) => crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
  const fileName = (k) => k.replace(/[:/]/g, "_") + ".bin";
  async function fetchBin(key, bust) {
    const res = await fetch(`d/${fileName(key)}?v=${encodeURIComponent(bust)}`, { cache: "no-store" });
    if (res.status === 404) throw new Error("아직 사이트에 올라오지 않은 자료입니다. 다음 자동 수집 때 반영됩니다.");
    if (!res.ok) throw new Error(`자료를 받지 못했습니다 (${res.status})`);
    return new Uint8Array(await res.arrayBuffer());
  }
  async function openBin(buf, k) {
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: buf.slice(0, 12) }, k, buf.slice(12));
    return JSON.parse(new TextDecoder().decode(plain));
  }
  async function staticGet(key, bust) {
    const buf = await fetchBin(key, bust);
    try { return await openBin(buf, SKEY); }
    catch (_) { forgetKey(); location.reload(); throw new Error("비밀번호가 바뀌었습니다. 다시 들어와 주세요."); }
  }
  function loginOverlay() {
    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.className = "login-wrap";
      box.innerHTML = `<form class="login-card" autocomplete="on">
        <div class="login-brand"><svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="#002060"/><circle cx="16" cy="16" r="8.5" fill="none" stroke="#fff" stroke-width="2.4"/><circle cx="16" cy="16" r="3" fill="#1973B9"/></svg>주가 모니터</div>
        <h1>주가 모니터</h1>
        <p>비밀번호를 입력해 주세요. 자료는 이 비밀번호로 잠겨 있어서, 비밀번호 없이는 아무도 내용을 볼 수 없습니다.</p>
        <input type="text" name="username" value="monitor" autocomplete="username" hidden>
        <input type="password" name="pw" autocomplete="current-password" placeholder="비밀번호" required>
        <label class="login-keep"><input type="checkbox" name="keep" checked> 이 기기에서 로그인 유지</label>
        <button type="submit">들어가기</button>
        <p class="login-err" role="alert"></p>
      </form>`;
      document.body.appendChild(box);
      document.body.classList.add("locked");
      const f = $("form", box), err = $(".login-err", box), btn = $("button", box);
      setTimeout(() => f.pw.focus(), 50);
      f.onsubmit = async (ev) => {
        ev.preventDefault();
        err.textContent = ""; btn.disabled = true; btn.textContent = "확인 중…";
        try {
          const raw = await deriveKey(f.pw.value);
          const k = await aesKey(raw);
          await openBin(await fetchBin("live", Date.now()), k);   // 맞는 비밀번호인지 실제로 풀어 봄
          const st = store(f.keep.checked ? "localStorage" : "sessionStorage");
          try { st && st.setItem(KEY_NAME, b64e(raw)); } catch (_) {}
          SKEY = k;
          box.remove(); document.body.classList.remove("locked");
          resolve();
        } catch (e) {
          btn.disabled = false; btn.textContent = "들어가기";
          err.textContent = /아직 사이트/.test(e.message) ? "아직 첫 자료가 올라오지 않았습니다. 몇 분 뒤 다시 열어 주세요." : "비밀번호가 맞지 않습니다.";
          f.pw.select();
        }
      };
    });
  }
  async function ensureLogin() {
    const saved = savedKey();
    if (saved) {
      try { SKEY = await aesKey(b64d(saved)); return; } catch (_) { forgetKey(); }
    }
    await loginOverlay();
  }
  function logout() { forgetKey(); location.hash = ""; location.reload(); }
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest && ev.target.closest("[data-logout]");
    if (!a) return;
    ev.preventDefault();
    if (STATIC) logout(); else location.href = "/logout";
  });

  // ---- 기사 등록·빼기 요청: Cloudflare 사이트는 보관함으로, GitHub 사이트는 GitHub 이슈로 보냄
  async function sealInbox(item) {          // 요청 내용을 사이트 열쇠로 암호화 (공개 저장소에는 알아볼 수 없는 글자만 남음)
    const iv = crypto.getRandomValues(new Uint8Array(12));
    let plain = new TextEncoder().encode(JSON.stringify(item));
    if (plain.length > 1500 && typeof CompressionStream !== "undefined") {   // 붙여넣은 원인 글처럼 긴 요청은 줄여서 보냄
      plain = new Uint8Array(await new Response(new Blob([plain]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer());
    }
    const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, SKEY, plain));
    const out = new Uint8Array(12 + ct.length); out.set(iv); out.set(ct, 12);
    return b64e(out);
  }
  async function sendInbox(item) {
    if (!STATIC) {
      return rawApi("/api/inbox", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(item) })
        .then(() => "요청을 보냈습니다. 다음 자동 수집 때 반영됩니다.");
    }
    const sealed = await sealInbox(item);
    const title = `[요청] 사이트 반영 ${new Date().toISOString().slice(5, 16).replace("T", " ")}`;
    const body = `아래 [Create] 버튼을 누르면 몇 분 안에 사이트에 반영됩니다. 내용은 고치지 마세요.\n\n\`\`\`enc\n${sealed}\n\`\`\`\n`;
    const url = `https://github.com/${STATIC.repo}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
    if (url.length > 7000) {                 // 너무 긴 주소는 GitHub이 받지 않을 수 있음 → 본문을 복사해 두고 직접 붙여넣기
      let copied = false;
      try { await navigator.clipboard.writeText(body); copied = true; } catch (_) {}
      if (!copied) throw new Error("내용이 길어 복사가 필요한데, 이 브라우저에서 복사가 막혔습니다. 내용을 줄이거나 다른 브라우저로 해 보세요.");
      window.open(`https://github.com/${STATIC.repo}/issues/new?title=${encodeURIComponent(title)}`, "_blank", "noopener");
      return "내용이 길어 복사해 두었습니다. 새 창의 GitHub 화면에서 본문 칸(Add a description)에 붙여넣기(Ctrl+V / 길게 눌러 붙여넣기) 한 뒤 초록색 [Create]를 누르면 몇 분 안에 반영됩니다.";
    }
    window.open(url, "_blank", "noopener");
    return "GitHub 화면이 새 창으로 열렸습니다. 거기서 초록색 [Create] 버튼을 누르면 몇 분 안에 반영됩니다 (내용은 암호화되어 있어 다른 사람은 알아볼 수 없습니다).";
  }

  // ---- 클라우드 모드: 올라온 묶음(live)과 개별 자료(kv)를 PC 화면과 같은 형식으로 돌려줌
  let LIVE = null, LIVE_AT = 0, KVC = {};
  async function cloudLive(force) {
    if (!LIVE || force || Date.now() - LIVE_AT > 60000) {
      const next = STATIC ? await staticGet("live", Date.now()) : await rawApi("/api/live");
      if (!LIVE || next.published_at !== LIVE.published_at) KVC = {};
      LIVE = next; LIVE_AT = Date.now();
    }
    return LIVE;
  }
  async function kv(key) {
    if (!KVC[key]) {
      const p = STATIC ? staticGet(key, (LIVE && LIVE.published_at) || Date.now()) : rawApi(`/api/kv/${encodeURIComponent(key)}`);
      KVC[key] = p.catch((e) => { delete KVC[key]; throw e; });
    }
    return KVC[key];
  }
  async function cloudApi(path) {
    const u = new URL(path, location.origin), p = u.pathname, q = Object.fromEntries(u.searchParams);
    const L = await cloudLive();
    if (p === "/api/status") return { ...L.status, viewer: "cloud", published_at: L.published_at };
    if (p === "/api/intraday") return (!q.date || q.date === L.intraday.date) ? L.intraday : kv(`intraday:${q.date}`);
    if (p === "/api/events") {
      const kind = q.kind && q.kind !== "all" ? q.kind : null;
      const events = L.events.events.filter((e) => (!q.from || e.start_ts >= q.from) && (!q.to || e.start_ts <= `${q.to}T23:59`) && (!kind || e.direction === kind));
      const days = L.events.trading_days.filter((d) => (!q.from || d >= q.from) && (!q.to || d <= q.to));
      return { events, trading_days: days };
    }
    let m = p.match(/^\/api\/events\/(\d+)$/);
    if (m) return kv(`event:${m[1]}`);
    if (p === "/api/articles") {
      const a = await kv("articles");
      if (!q.limit) return a;
      return { ...a, items: a.items.filter((x) => x.category !== "단순 언급").slice(0, +q.limit), summary: null };
    }
    if (p === "/api/disclosures") return kv("disclosures");
    if (p === "/api/daily") return kv("daily");
    m = p.match(/^\/api\/disclosures\/(.+)$/);
    if (m) {
      const all = await kv("disclosure_details");
      const d = all[decodeURIComponent(m[1])];
      if (!d) throw new Error("이 공시는 아직 사이트에 올라오지 않았습니다. 다음 자동 수집 때 반영됩니다.");
      return d;
    }
    m = p.match(/^\/api\/articles\/(.+)$/);
    if (m) {
      const all = await kv("article_details");
      const d = all[decodeURIComponent(m[1])];
      if (!d) throw new Error("이 기사는 아직 사이트에 올라오지 않았습니다. 다음 자동 수집 때 반영됩니다.");
      return d;
    }
    throw new Error("이 사이트에서는 쓸 수 없는 기능입니다.");
  }

  function csvDownload(rows) {
    const p = (x) => (x == null ? "" : (x * 100).toFixed(2));
    const head = ["번호", "날짜", "시작", "끝", "구분", "기준", "대형", "움직임(%)", "KOSDAQ 같은 기간(%)", "가장 유력한 원인", "신뢰도", "판단 요약", "분석 단계", "감지 근거"];
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [head.map(q).join(",")].concat(rows.map((e) => [e.id, e.start_ts.slice(0, 10), hhmm(e.start_ts), hhmm(e.last_ts), e.kind, isDaily(e) ? "하루" : "장중", e.tier === "대형" ? "대형" : "", p(e.peak_return_5m), p(e.kosdaq_change), e.cause_headline || "", e.cause_confidence || "", e.cause_summary || "", e.cause_stage || "", e.rule || ""].map(q).join(",")));
    const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `events_${isoDate(new Date()).replace(/-/g, "")}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function cleanupPage() {
    charts.forEach((c) => { try { c.remove(); } catch (_) {} });
    charts = [];
    timers.forEach(clearInterval);
    timers = [];
  }

  // ---------------------------------------------------------------- charts
  function baseChart(el, dark, opts = {}) {
    const text = dark ? "rgba(255,255,255,.62)" : css("--muted");
    const grid = dark ? "rgba(255,255,255,.07)" : css("--line");
    const chart = LW.createChart(el, {
      autoSize: true,
      layout: { background: { type: "solid", color: "transparent" }, textColor: text, fontFamily: css("--font") || "sans-serif", fontSize: 12, attributionLogo: false, panes: { separatorColor: grid, separatorHoverColor: grid } },
      grid: { vertLines: { visible: false }, horzLines: { color: grid } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, tickMarkFormatter: (t) => utcHHMM(t), fixLeftEdge: true, fixRightEdge: true },
      localization: { locale: "ko-KR", timeFormatter: (t) => utcHHMM(t) },
      crosshair: {
        mode: LW.CrosshairMode.Magnet,
        vertLine: { color: dark ? "rgba(255,255,255,.35)" : css("--line-2"), labelBackgroundColor: dark ? "#0B2A66" : css("--navy") },
        horzLine: { color: dark ? "rgba(255,255,255,.35)" : css("--line-2"), labelBackgroundColor: dark ? "#0B2A66" : css("--navy") },
      },
      ...opts,
    });
    charts.push(chart);
    return chart;
  }

  const kindColor = (k, dark) => (dark ? { up: "#FF6B7D", down: "#7FA6FF", flat: "#F2BE4B" } : { up: css("--up"), down: css("--down"), flat: css("--vol") })[k];

  /** 가격(위)·거래량(아래) 두 칸 차트. events가 있으면 구간 거래량을 종류 색으로 칠하고 표시를 붙임. */
  function priceVolumeChart(el, legendEl, bars, { dark = false, events = [], prevClose = null, extendTo = null, interactive = true, onEvent = null } = {}) {
    const chart = baseChart(el, dark, interactive ? {} : { handleScroll: false, handleScale: false });
    const lineColor = dark ? "#FFFFFF" : (matchMedia("(prefers-color-scheme: dark)").matches ? css("--lens") : css("--navy-2"));
    const price = chart.addSeries(LW.LineSeries, {
      color: lineColor, lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      priceFormat: { type: "custom", minMove: 1, formatter: (v) => won(v) },
      crosshairMarkerRadius: 4,
    }, 0);
    const vol = chart.addSeries(LW.HistogramSeries, {
      priceFormat: { type: "custom", minMove: 1, formatter: (v) => int(v) }, priceLineVisible: false, lastValueVisible: false,
    }, 1);
    chart.panes()[1].setStretchFactor(0.32);
    chart.panes()[0].setStretchFactor(1);

    // 이상변동 구간 (시작 5분 전 ~ 마지막 감지)
    const spans = events.map((e) => ({ k: e.direction, a: toTime(e.start_ts) - 4 * 60, b: toTime(e.last_ts) }));
    const baseVol = dark ? "rgba(255,255,255,.26)" : (matchMedia("(prefers-color-scheme: dark)").matches ? "rgba(255,255,255,.22)" : "#C3CAD6");
    const pdata = [], vdata = [];
    for (const b of bars) {
      const t = toTime(b.t);
      pdata.push({ time: t, value: b.c });
      const sp = spans.find((s) => t >= s.a && t <= s.b);
      vdata.push({ time: t, value: b.v, color: sp ? kindColor(sp.k, dark) : baseVol });
    }
    // 장 끝(15:30)까지 빈 칸을 두어 하루 전체 위치가 보이게 함
    if (extendTo && bars.length) {
      let t = toTime(bars[bars.length - 1].t) + 60;
      const end = toTime(extendTo);
      while (t <= end) { pdata.push({ time: t }); vdata.push({ time: t }); t += 60; }
    }
    price.setData(pdata);
    vol.setData(vdata);

    if (prevClose) {
      price.createPriceLine({ price: prevClose, color: dark ? "rgba(255,255,255,.4)" : css("--line-2"), lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true, title: "전일 종가" });
    }
    if (events.length) {
      const markers = events.map((e) => ({
        id: String(e.id),
        time: toTime(e.start_ts),
        position: e.direction === "down" ? "aboveBar" : e.direction === "up" ? "belowBar" : "aboveBar",
        shape: e.direction === "up" ? "arrowUp" : e.direction === "down" ? "arrowDown" : "circle",
        color: kindColor(e.direction, dark),
        text: pct(e.peak_return_5m, 1),
        size: 1,
      })).sort((a, b) => a.time - b.time);
      LW.createSeriesMarkers(price, markers);
      // 차트의 급등·급락 표시를 누르면 목록과 같은 원인 분석 창을 엶
      if (onEvent) chart.subscribeClick((p) => { if (p.hoveredObjectId) onEvent(+p.hoveredObjectId); });
    }
    if (extendTo) {
      // 장 끝까지 빈 칸이 보이도록 가장자리 고정을 풀고 범위를 직접 지정
      chart.applyOptions({ timeScale: { fixLeftEdge: false, fixRightEdge: false, rightOffset: 0 } });
      chart.timeScale().setVisibleLogicalRange({ from: 0, to: pdata.length - 1 });
    }
    else chart.timeScale().fitContent();

    if (legendEl) {
      const show = (t) => {
        const b = bars.find((x) => toTime(x.t) === t) || bars[bars.length - 1];
        if (!b) { legendEl.innerHTML = ""; return; }
        const chg = prevClose ? (b.c / prevClose - 1) : null;
        legendEl.innerHTML = `<span>${hhmm(b.t)}</span><span><b>${won(b.c)}원</b>${chg != null ? ` <span class="${dark ? "" : sgnCls(chg)}">${pct(chg)}</span>` : ""}</span><span>1분 거래량 <b>${int(b.v)}주</b></span>`;
      };
      show(null);
      chart.subscribeCrosshairMove((p) => show(p && p.time ? p.time : null));
    }
    return chart;
  }

  /** 이 종목 vs KOSDAQ 변화율(%) 한 축 비교 */
  function indexedChart(el, legendEl, own, kq, base, kqBase, startT, label, daily = false) {
    const dfmt = (t) => { const d = new Date(t * 1000); return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`; };
    const chart = baseChart(el, false, daily ? { timeScale: { borderVisible: false, timeVisible: false, tickMarkFormatter: dfmt, fixLeftEdge: true, fixRightEdge: true }, localization: { locale: "ko-KR", timeFormatter: dfmt } } : {});
    const ownColor = matchMedia("(prefers-color-scheme: dark)").matches ? css("--lens") : css("--navy-2");
    const fmt = { type: "custom", minMove: 0.01, formatter: (v) => pctRaw(v) };
    const s1 = chart.addSeries(LW.LineSeries, { color: ownColor, lineWidth: 2, priceFormat: fmt, priceLineVisible: false, lastValueVisible: true });
    const s2 = chart.addSeries(LW.LineSeries, { color: css("--muted"), lineWidth: 2, lineStyle: LW.LineStyle.Dashed, priceFormat: fmt, priceLineVisible: false, lastValueVisible: true });
    const d1 = own.map((b) => ({ time: toTime(b.t), value: (b.c / base - 1) * 100 }));
    const d2 = kqBase ? kq.map((b) => ({ time: toTime(b.t), value: (b.c / kqBase - 1) * 100 })) : [];
    s1.setData(d1);
    if (d2.length) s2.setData(d2);
    s1.createPriceLine({ price: 0, color: css("--line-2"), lineWidth: 1, lineStyle: LW.LineStyle.Solid, axisLabelVisible: false });
    const st = toTime(startT);
    const near = d1.find((p) => p.time >= st);
    if (near) LW.createSeriesMarkers(s1, [{ time: near.time, position: "aboveBar", shape: "arrowDown", color: css("--ink"), text: label }]);
    chart.timeScale().fitContent();
    const show = (t) => {
      const a = d1.find((p) => p.time === t) || d1[d1.length - 1];
      const b = d2.find((p) => p.time === (a && a.time));
      if (!a) return;
      legendEl.innerHTML = `<span>${daily ? dfmt(a.time) : utcHHMM(a.time)}</span><span><i style="background:${ownColor}"></i>이 종목 <b class="${sgnCls(a.value)}">${pctRaw(a.value)}</b></span>` +
        `<span><i style="background:${css("--muted")}"></i>KOSDAQ <b>${b ? pctRaw(b.value) : (d2.length ? "-" : "데이터 없음")}</b></span>`;
    };
    show(null);
    chart.subscribeCrosshairMove((p) => show(p && p.time ? p.time : null));
    return chart;
  }

  // ---------------------------------------------------------------- status
  async function refreshStatus() {
    try {
      STATUS = await api("/api/status");
    } catch (e) {
      STATUS = null;
      setEngine("stopped", "화면 서버 연결 끊김");
      showAlert(`<strong>화면 서버에 연결할 수 없습니다.</strong> 01_프로그램_실행.bat을 다시 실행해 주세요.`);
      return;
    }
    const s = STATUS;
    document.body.classList.toggle("shared", s.viewer === "shared");
    document.body.classList.toggle("cloud", s.viewer === "cloud");
    if (s.share) renderShareBtn(s.share);
    if (s.viewer === "cloud") {
      const pub = s.published_at ? new Date(s.published_at) : null;
      const age = pub ? (Date.now() - pub.getTime()) / 60000 : 1e9;
      const kst = new Date(Date.now() + 9 * 3600e3), hm = kst.getUTCHours() * 100 + kst.getUTCMinutes(), wd = kst.getUTCDay();
      // 휴장일: 오늘 장중에 받은 네이버 장 상태가 '열림'이 아니고 오늘 체결도 없으면 장중으로 보지 않음
      const today = kst.toISOString().slice(0, 10), q = s.quote || {};
      const closedToday = q.market_status && q.market_status !== "OPEN" && String(q.traded_at || "").slice(0, 10) !== today
        && String(s.published_at || "").slice(0, 10) === today && String(s.published_at).slice(11, 16) >= "09:05";
      const marketNow = wd >= 1 && wd <= 5 && hm >= 905 && hm <= 1535 && !closedToday;
      const when = s.published_at ? `${s.published_at.slice(5, 10).replace("-", "/")} ${s.published_at.slice(11, 16)}` : "-";
      const iv = (LIVE && LIVE.interval_min) || 15;
      const byPc = !STATIC && LIVE && LIVE.runner === "pc";
      // 갱신 안내는 큰 띠 대신 오른쪽 위 '갱신' 표시에 마우스를 올리면 보이게 (문제가 있을 때만 경고 띠)
      $("#engine").title = byPc
        ? `회사 PC에서 ${when}에 올린 자료입니다. PC가 켜져 있으면 장중 ${iv}분마다 새로 올라옵니다.`
        : `${when}에 자동 수집·분석한 자료입니다. 장중에는 약 ${iv}분마다 새로 갱신됩니다.`;
      if (marketNow && age > iv * 2 + 15) {
        setEngine("stale", `갱신 지연 (마지막 ${when})`);
        const head = `<strong>지금 장중인데 ${Math.round(age)}분째 새 자료가 없습니다.</strong> `;
        showAlert(head + (byPc ? "회사 PC가 꺼져 있거나 감시 프로그램이 멈춘 것 같습니다." : "자동 실행이 늦어지고 있습니다. 보통 다음 실행 때 그동안의 분봉까지 한꺼번에 채워집니다."));
        if (STATIC && STATIC.repo) runHealth().then((h) => {
          if (!h || $("#alert").hidden) return;
          showAlert(head + (h === "publish"
            ? "자동 실행과 수집은 정상인데 사이트 게시(GitHub Pages)가 늦어지고 있습니다. 수집은 계속되며, 풀리면 그동안의 자료가 한꺼번에 채워집니다."
            : "자동 실행이 멈췄거나 실패하고 있습니다. GitHub 저장소의 Actions 탭에서 최근 실행을 확인해 주세요."));
        });
      } else {
        setEngine("ok", `갱신 ${when}`);
        showAlert(null);
      }
      renderQuote();
      renderFoot();
      return;
    }
    const lastOk = s.data.last_ok ? s.data.last_ok.slice(11, 19) : "";
    setEngine(s.engine.state, s.engine.state === "ok" ? `감시 중${lastOk ? ` (시세 ${lastOk})` : ""}` : s.engine.label);

    if (s.engine.state === "stale" || s.engine.state === "stopped") {
      showAlert("<strong>감시 엔진이 꺼져 있어 지금은 기록되지 않습니다.</strong> 01_프로그램_실행.bat을 실행해 주세요.");
    } else if (s.engine.state === "error") {
      showAlert(`<strong>감시 엔진에 오류가 있습니다.</strong> ${esc(s.engine.message || "")} — logs 폴더의 engine.log를 보내주세요.`);
    } else if (!s.data.ok) {
      showAlert(`<strong>네이버에서 시세를 받지 못하고 있습니다.</strong> ${esc(s.data.message)}<br>인터넷 연결을 확인하고, 계속되면 05_데이터연결_점검.bat을 실행해 주세요.`);
    } else {
      showAlert(null);
    }
    renderQuote();
    renderFoot();
  }

  // 자료가 오래됐을 때 원인 구분: GitHub 공개 정보로 최근 자동 실행이 계속 성공 중인지 확인
  // "publish" = 실행은 정상인데 사이트 게시만 늦음, "runner" = 실행이 멈췄거나 실패, null = 확인 못 함
  let RUN_HEALTH = null, RUN_HEALTH_AT = 0;
  async function runHealth() {
    if (Date.now() - RUN_HEALTH_AT < 5 * 60000) return RUN_HEALTH;      // 공개 API 횟수 제한(시간당 60번) 때문에 5분마다만
    RUN_HEALTH_AT = Date.now();
    try {
      const r = await fetch(`https://api.github.com/repos/${STATIC.repo}/actions/runs?per_page=10`, { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const runs = (await r.json()).workflow_runs || [];
      const recent = runs.filter((x) => Date.now() - new Date(x.created_at).getTime() < 15 * 60000);
      RUN_HEALTH = recent.some((x) => x.conclusion === "success") ? "publish" : "runner";
    } catch (_) { RUN_HEALTH = null; }
    return RUN_HEALTH;
  }

  function setEngine(state, label) {
    const el = $("#engine");
    el.dataset.state = state;
    $(".engine-label", el).textContent = label;
  }
  function showAlert(html) {
    const el = $("#alert");
    if (!html) { el.hidden = true; return; }
    el.innerHTML = html; el.hidden = false;
  }

  // 오늘 '지금 등락'은 한 곳(네이버 현재가)에서만 가져와 화면 어디서나 같은 숫자를 씀
  // 15:30 이후 네이버 시세는 넥스트레이드 애프터마켓 가격이라 거래소 종가와 다름 → 거래소 종가(15:30 1분봉)를 보여주고
  // 애프터마켓 가격은 따로 (ah). d: 그날 1분봉 자료 (없으면 사이트에 올라온 최신 자료)
  function krxClose(d) {
    if (!d || !d.prev_close || !(d.bars || []).length) return null;
    const last = [...d.bars].reverse().find((b) => b.t.slice(11, 16) <= "15:30");
    if (!last || last.t.slice(11, 16) < "15:30") return null;          // 종가 단일가 체결 전
    return { price: last.c, chg: last.c / d.prev_close - 1, prev: d.prev_close };
  }
  function liveFor(date, d) {
    const q = STATUS && STATUS.quote;
    if (!q || q.price == null || q.change_pct == null || !q.traded_at || q.traded_at.slice(0, 10) !== date) return null;
    const at = q.traded_at.slice(11, 16);
    if (at > "15:30") {
      const k = krxClose(d || (LIVE && LIVE.intraday && LIVE.intraday.date === date ? LIVE.intraday : null));
      if (k) return { price: k.price, chg: k.chg, prev: k.prev, at: "15:30", open: false, closed: true,
        ah: { price: q.price, chg: q.change_pct / 100, at } };
    }
    return { price: q.price, chg: q.change_pct / 100, at, open: q.market_status === "OPEN" };
  }
  const ahLine = (ah) => (ah ? `시간외(넥스트레이드) ${won(ah.price)}원 <span class="${sgnCls(ah.chg)}">${pct(ah.chg)}</span> · ${ah.at}` : "");

  function renderQuote() {
    if (!STATUS) return;
    const q = STATUS.quote, k = STATUS.kosdaq;
    $("#q-name").textContent = STATUS.name;
    $("#q-code").textContent = STATUS.symbol;
    if (q && q.price != null) {
      const lv = q.traded_at ? liveFor(q.traded_at.slice(0, 10)) : null;
      const k = lv && lv.closed ? lv : null;               // 장 마감 뒤: 거래소 종가
      const price = k ? k.price : q.price, change = k ? k.price - k.prev : q.change, cp = k ? k.chg * 100 : q.change_pct;
      $("#q-price").textContent = won(price);
      const cls = change > 0 ? "up" : change < 0 ? "down" : "";
      const arrow = change > 0 ? "▲" : change < 0 ? "▼" : "";
      const asof = k ? "장 마감 · 거래소 종가" : q.market_status === "OPEN" ? `${(q.traded_at || "").slice(11, 16)} 기준` : "장 마감";
      $("#q-change").innerHTML = `<span class="chg ${cls}">${arrow} ${won(Math.abs(change))} (${pctRaw(cp)})</span><span class="asof">${asof}</span>${k ? `<span class="ah">${ahLine(k.ah)}</span>` : ""}`;
      $("#q-volume").innerHTML = `${int(q.volume)}<small>주</small>`;
    }
    if (k && k.price != null) {
      const cls = k.change_pct > 0 ? "up" : k.change_pct < 0 ? "down" : "";
      $("#q-kosdaq").innerHTML = `${k.price.toFixed(2)}<small class="${cls}">${pctRaw(k.change_pct)}</small>`;
    }
  }

  function renderFoot() {
    if (!STATUS) return;
    const since = STATUS.first_bar_ts ? dayLabel(STATUS.first_bar_ts) : "-";
    $("#foot").innerHTML = `<span>데이터 출처 네이버 증권·다음 금융(공식 제공 서비스가 아니어서 형식이 바뀌면 수신이 멈출 수 있습니다)</span>` +
      `<span>${since}부터 기록, 1분봉 ${int(STATUS.bars_count)}건, 급등·급락 ${int(STATUS.events_count)}건${CLOUD ? ' · <a href="#" data-logout>로그아웃</a>' : ""}</span>`;
  }

  // ---------------------------------------------------------------- dashboard
  // 최근 거래일(최대 15일)을 하루 한 장씩 세로로 쌓음. 카드가 화면 가까이 오면 그날 자료를 불러옴.
  // 이상변동이 없던 날은 한 줄로 접어 두고, 누르면 차트를 펼침. 오늘 카드만 30초마다 새로 그림.
  let feedObserver = null;
  // 대시보드: [최근] [달력] [표] 세 가지 보기 (예전 '이벤트 기록'은 표 보기)
  const VIEWS = [["recent", "최근", "#/"], ["calendar", "달력", "#/calendar"], ["table", "표", "#/events"]];
  async function pageDashboard(view = "recent") {
    $("#hero").hidden = false;
    main.innerHTML = `<div class="wrap"><div class="view-bar"><div class="seg" role="tablist" aria-label="보기">${VIEWS.map(([v, t, h]) =>
      `<a href="${h}" role="tab" aria-pressed="${v === view}">${t}</a>`).join("")}</div></div><div id="view"></div></div>`;
    if (view === "calendar") return viewCalendar();
    if (view === "table") return viewTable();
    return viewRecent();
  }

  // 달력: 날마다 등락(색)과 그날 소식(보도자료·공시·외부 기사)·급등·급락을 한 화면에. 날짜를 누르면 아래에 그날 내용
  const calState = { month: null, sel: null };
  const MAIN_DISC = new Set(["A", "B", "C", "I", "K"]);       // 정기(실적)·주요사항·발행·거래소 공시·거래소 조치
  async function viewCalendar() {
    const box = $("#view");
    box.innerHTML = `<p class="loading">불러오는 중…</p>`;
    let daily, evs, arts, discs;
    try {
      [daily, evs, arts, discs] = await Promise.all([
        api("/api/daily").then((d) => d.days || []),
        api("/api/events").then((d) => d.events || []).catch(() => []),
        api("/api/articles").then((d) => d.items || []).catch(() => []),
        api("/api/disclosures").then((d) => d.items || []).catch(() => []),
      ]);
    } catch (e) { box.innerHTML = `<div class="empty"><strong>달력 자료를 불러오지 못했습니다.</strong>${esc(e.message)} 다음 자동 수집 뒤에 다시 열어 보세요.</div>`; return; }
    if (!daily.length) { box.innerHTML = `<div class="empty"><strong>아직 일별 시세가 없습니다.</strong></div>`; return; }
    const byDay = (list, dayOf) => { const m = {}; list.forEach((x) => { const d = dayOf(x); if (d) (m[d] ||= []).push(x); }); return m; };
    const price = Object.fromEntries(daily.map((x) => [x.date, x]));
    const evDay = byDay(evs, (e) => e.start_ts.slice(0, 10));
    const artDay = byDay(arts.filter((a) => !a.hidden && a.category !== "단순 언급" && a.ts), (a) => a.ts.slice(0, 10));
    const discDay = byDay(discs.filter((x) => x.ts), (x) => x.ts.slice(0, 10));
    const months = [...new Set(daily.map((x) => x.date.slice(0, 7)))].sort();
    if (!calState.month || !months.includes(calState.month)) calState.month = months[months.length - 1];
    const isPr = (a) => a.kind === "pr" || a.kind === "company";

    function grid() {
      const [y, m] = calState.month.split("-").map(Number);
      const first = new Date(y, m - 1, 1), days = new Date(y, m, 0).getDate();
      const lead = first.getDay();                                 // 일요일 시작
      const cells = [];
      for (let i = 0; i < lead; i++) cells.push(`<div class="cal-cell blank"></div>`);
      for (let d = 1; d <= days; d++) {
        const ds = `${calState.month}-${String(d).padStart(2, "0")}`;
        const px = price[ds], ch = px && px.change;
        const pr = (artDay[ds] || []).filter(isPr).length, ext = (artDay[ds] || []).filter((a) => !isPr(a)).length;
        const dc = (discDay[ds] || []).filter((x) => MAIN_DISC.has(x.pblntf || "")).length;
        const tint = ch == null ? "" : `style="--tint:${Math.min(1, Math.abs(ch) / 0.1).toFixed(2)}"`;
        cells.push(`<button type="button" class="cal-cell${ch == null ? " closed" : ""}${ch > 0 ? " up" : ch < 0 ? " down" : ""}${ds === calState.sel ? " sel" : ""}" data-day="${ds}" ${tint}>
          <span class="cal-d">${d}</span>
          ${ch != null ? `<span class="cal-ch ${sgnCls(ch)}">${pct(ch, 1)}</span>` : ""}
          <span class="cal-marks">${pr ? `<i class="mk pr" title="그린광학 보도자료 ${pr}건"></i>` : ""}${dc ? `<i class="mk disc" title="주요 공시 ${dc}건"></i>` : ""}${ext ? `<i class="mk ext" title="외부 기사 ${ext}건"></i>` : ""}</span>
        </button>`);
      }
      const mi = months.indexOf(calState.month);
      return `<div class="cal-head">
          <button type="button" class="btn small" data-mv="-1" ${mi <= 0 ? "disabled" : ""} aria-label="이전 달">‹</button>
          <b>${y}년 ${m}월</b>
          <button type="button" class="btn small" data-mv="1" ${mi >= months.length - 1 ? "disabled" : ""} aria-label="다음 달">›</button>
          <span class="cal-legend"><span><i class="mk pr"></i>그린광학 보도자료</span><span><i class="mk disc"></i>주요 공시</span><span><i class="mk ext"></i>외부 기사</span></span>
        </div>
        <div class="cal-grid">${["일", "월", "화", "수", "목", "금", "토"].map((w) => `<div class="cal-w">${w}</div>`).join("")}${cells.join("")}</div>`;
    }

    function dayPanel(ds) {
      const px = price[ds], ev = evDay[ds] || [], ar = artDay[ds] || [], dc = discDay[ds] || [];
      const li = (href, meta, title) => `<li><span class="muted">${meta}</span> <a href="${href}">${esc(title)}</a></li>`;
      return `<section class="panel cal-day">
        <div class="cal-day-head"><h3 class="d-sec">${dayLabel(ds)}</h3>
          ${px ? `<span>종가 ${won(px.close)}원 · <b class="${sgnCls(px.change)}">${pct(px.change)}</b> · KOSDAQ ${pct(px.kosdaq)} · 거래량 ${int(px.volume)}주</span>` : `<span class="muted">거래 없음</span>`}</div>
        ${ev.length ? `<h4 class="sub">급등·급락</h4><ul class="cal-list">${ev.map((e) => `<li><button type="button" class="btn-link" data-ev="${e.id}">${isDaily(e) ? "하루" : `${hhmm(e.start_ts)}~${hhmm(e.last_ts)}`} ${e.direction === "up" ? "급등" : "급락"} ${pct(e.peak_return_5m)}${e.tier && e.tier !== "일반" ? ` · ${esc(e.tier)}` : ""}</button></li>`).join("")}</ul>` : ""}
        ${ar.length ? `<h4 class="sub">기사 ${ar.length}건</h4><ul class="cal-list">${ar.map((a) => li(`#/pr/${esc(a.id)}`, `${hhmm(a.ts)} · ${esc(a.office || "")}${isPr(a) ? ' · <span class="ktag k-pr">그린광학 보도자료</span>' : ""}`, a.title)).join("")}</ul>` : ""}
        ${dc.length ? `<h4 class="sub">공시 ${dc.length}건</h4><ul class="cal-list">${dc.map((x) => li(`#/pr/${esc(x.id)}`, `${x.untimed ? "시각 미상" : hhmm(x.ts)}`, x.title)).join("")}</ul>` : ""}
        ${!ev.length && !ar.length && !dc.length ? `<p class="muted">이날 모인 급등·급락·기사·공시가 없습니다.</p>` : ""}
        <div class="ask-row">
          <button type="button" class="btn small" id="cal-chart">그날 차트 보기</button>
          ${px ? `<button type="button" class="btn small editable" id="cal-ask">이 날짜 분석 요청</button>` : ""}
          <span class="form-msg" id="cal-msg" role="status"></span></div>
        <div id="cal-chart-box"></div>
      </section>`;
    }

    function render() {
      box.innerHTML = `<div class="panel cal">${grid()}</div>${calState.sel ? dayPanel(calState.sel) : `<p class="muted cal-hint">날짜를 누르면 그날의 급등·급락·기사·공시가 아래에 나옵니다.</p>`}`;
    }
    render();
    box.onclick = async (ev) => {
      const mv = ev.target.closest("[data-mv]");
      if (mv) { calState.month = months[months.indexOf(calState.month) + +mv.dataset.mv]; calState.sel = null; render(); return; }
      const cell = ev.target.closest(".cal-cell[data-day]");
      if (cell) { calState.sel = cell.dataset.day; render(); $(".cal-day").scrollIntoView({ behavior: "smooth", block: "nearest" }); return; }
      const e = ev.target.closest("[data-ev]");
      if (e) { openEvent(+e.dataset.ev); return; }
      if (ev.target.closest("#cal-ask")) {
        const msg = $("#cal-msg");
        msg.className = "form-msg"; msg.textContent = "요청하는 중…";
        try { msg.textContent = CLOUD ? await sendInbox({ type: "analyze", date: calState.sel, from: null, to: null }) : "PC 화면에서는 '다시 분석' 버튼을 이용해 주세요."; }
        catch (er) { msg.className = "form-msg err"; msg.textContent = er.message; }
        return;
      }
      if (ev.target.closest("#cal-chart")) {
        const cb = $("#cal-chart-box");
        cb.innerHTML = `<p class="loading">불러오는 중…</p>`;
        let d;
        try { d = await api(`/api/intraday?date=${calState.sel}`); } catch (_) { d = null; }
        if (!d || !d.bars || !d.bars.length) { cb.innerHTML = `<p class="muted">이날 분봉 기록이 없습니다.</p>`; return; }
        const card = document.createElement("article");
        card.className = "day-card";
        card.innerHTML = dayCardHtml(d, artDay[calState.sel] || [], false);
        cb.innerHTML = ""; cb.appendChild(card);
        priceVolumeChart($(".day-chart", card), $(".day-legend", card), d.bars, {
          events: d.events.filter((x) => !isDaily(x)), prevClose: d.prev_close, interactive: false, onEvent: (id) => openEvent(id) });
      }
    };
  }

  async function viewRecent() {
    $("#view").innerHTML = `<div class="feed" id="feed"><p class="loading">불러오는 중…</p></div>`;
    renderRules();
    const tog = $("#rules-toggle");
    tog.onclick = () => { const r = $("#rules"); r.hidden = !r.hidden; tog.setAttribute("aria-expanded", String(!r.hidden)); };

    let latest;
    try { latest = await api("/api/intraday"); }
    catch (e) { $("#feed").innerHTML = `<div class="empty"><strong>데이터를 불러오지 못했습니다.</strong>${esc(e.message)}</div>`; return; }
    if (!latest.date) {
      $("#feed").innerHTML = `<div class="empty"><strong>아직 수집된 시세가 없습니다.</strong>감시 엔진이 첫 데이터를 받는 중입니다. 1분 안에 표시됩니다.</div>`;
      return;
    }
    const dates = latest.dates;
    const [evs, arts] = await Promise.all([
      api(`/api/events?from=${dates[dates.length - 1]}`).then((d) => d.events).catch(() => []),
      api("/api/articles").then((d) => d.items).catch(() => []),
    ]);
    // 지난 날짜 차트 자료는 만들어 둔 것을 다시 쓰므로, 원인(나중에 붙여넣은 것)은 매번 새로 받는 이벤트 목록 값으로 덮어씀
    const evById = new Map(evs.map((e) => [e.id, e]));
    // 지난 날짜에 나중에 생긴 기록(차트 구간 요청 등)도 보이게, 만들어 둔 자료에 없는 그날 기록을 더함
    const fresh = (d) => {
      const have = new Set(d.events.map((e) => e.id));
      const added = evs.filter((e) => e.start_ts.slice(0, 10) === d.date && !have.has(e.id));
      return { ...d, events: d.events.filter((e) => !evs.length || evById.has(e.id)).map((e) => (evById.has(e.id) ? { ...e, ...evById.get(e.id) } : e)).concat(added) };
    };
    const artsByDay = {};
    for (const a of arts) {
      if (a.category === "단순 언급" || a.hidden || !a.ts) continue;
      (artsByDay[a.ts.slice(0, 10)] ||= []).push(a);
    }
    const mag = (a) => (a.effect && a.effect.measure && a.effect.measure.value != null ? Math.abs(a.effect.measure.value) : -1);
    for (const k in artsByDay) artsByDay[k].sort((x, y) => mag(y) - mag(x));

    // 모든 날짜를 펼친 채로 보여주고, 지난 날짜는 '접기'로 한 줄로 줄일 수 있음
    $("#feed").innerHTML = dates.map((x) => {
      return `<article class="day-card" data-date="${x}">
        <div class="day-card-load"><span class="day-card-date">${dayLabel(x)}</span><span class="muted">불러오는 중…</span></div></article>`;
    }).join("");

    const cards = [...document.querySelectorAll("#feed .day-card")];
    const state = new Map();     // date → { d, chart, open }
    const draw = (card, d) => {
      const date = card.dataset.date, isLatest = date === dates[0];
      const st = state.get(date) || {};
      if (st.chart) { try { st.chart.remove(); } catch (_) {} charts = charts.filter((c) => c !== st.chart); st.chart = null; }
      d = fresh(d);
      st.d = d;
      state.set(date, st);
      const dayArts = artsByDay[date] || [];
      if (!isLatest && st.open === false) { card.classList.add("is-quiet"); card.innerHTML = foldRow(d, dayArts); return; }
      card.classList.remove("is-quiet");
      card.innerHTML = (isLatest ? "" : `<button type="button" class="day-collapse btn-link">접기</button>`) + dayCardHtml(d, dayArts, isLatest);
      const closed = d.bars.length && d.bars[d.bars.length - 1].t.slice(11) >= "15:30";
      if (d.bars.length) {
        st.chart = priceVolumeChart($(".day-chart", card), $(".day-legend", card), d.bars, {
          events: d.events.filter((e) => !isDaily(e)), prevClose: d.prev_close, interactive: false, onEvent: (id) => openEvent(id),
          extendTo: closed ? null : `${d.date}T15:30`,
        });
        attachRange(card, st.chart, d, st, arts, dates[dates.indexOf(date) + 1] || null);
      }
      if (isLatest) {
        const n = d.events.filter((e) => !isDaily(e)).length || (d.events.some(isDaily) ? 1 : 0);
        $("#q-events").innerHTML = `${n}<small>건</small>`;
        $("#q-events-label").textContent = "오늘 급등·급락";
      }
    };
    const load = async (card) => {
      const date = card.dataset.date;
      try { draw(card, date === dates[0] ? latest : await api(`/api/intraday?date=${date}`)); }
      catch (e) { card.innerHTML = `<div class="day-card-load"><span class="day-card-date">${dayLabel(date)}</span><span class="muted">불러오지 못했습니다. ${esc(e.message)}</span></div>`; }
    };

    $("#feed").onclick = (ev) => {
      const fold = ev.target.closest(".day-fold, .day-collapse");
      if (fold) {
        const card = fold.closest(".day-card"), st = state.get(card.dataset.date);
        st.open = fold.classList.contains("day-fold"); draw(card, st.d);
        return;
      }
      const del = ev.target.closest("[data-ev-del]");
      if (del) { deleteRecord(del); return; }
      const b = ev.target.closest("[data-id]");
      if (b) openEvent(+b.dataset.id);
    };

    if (feedObserver) feedObserver.disconnect();
    const loaded = new Set();
    feedObserver = new IntersectionObserver((entries) => {
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        // 맨 아래로 바로 건너뛰어도 그 위 카드가 빈 채로 남지 않게 위쪽 카드까지 함께 불러옴
        for (const c of cards.slice(0, cards.indexOf(en.target) + 1)) {
          if (loaded.has(c)) continue;
          loaded.add(c); feedObserver.unobserve(c); load(c);
        }
      }
    }, { rootMargin: "600px 0px" });
    cards.forEach((c) => feedObserver.observe(c));

    renderQuote();
    timers.push(setInterval(async () => {
      try { latest = await api("/api/intraday"); } catch (_) { return; }
      if (latest.date !== dates[0]) { route(); return; }       // 새 거래일이 시작되면 피드를 처음부터 다시 그림
      if (cards[0].querySelector("[data-range-form]:not([hidden])")) return;   // 구간 원인을 입력하는 중이면 다시 그리지 않음
      draw(cards[0], latest);
    }, 30000));
  }

  // ---------------------------------------------------------------- 차트 구간 골라 원인 묻기
  // PC는 차트를 마우스로 끌어 구간을 고르고, 폰은 시작·끝을 차례로 누름 (손가락 끌기는 페이지 스크롤과 겹치므로)
  function attachRange(card, chart, d, st, arts, prevDay) {
    const el = $(".day-chart", card), panel = $(".range-panel", card);
    if (!el || !panel) return;
    el.classList.add("range-able");
    const box = document.createElement("div");
    box.className = "range-sel";
    box.hidden = true;
    el.appendChild(box);
    const ts = chart.timeScale();
    const timeAt = (ev) => ts.coordinateToTime(ev.clientX - el.getBoundingClientRect().left);
    const paint = () => {
      const r = st.range;
      const a = r && ts.timeToCoordinate(r[0]), b = r && ts.timeToCoordinate(r[1]);
      if (a == null || b == null) { box.hidden = true; return; }
      box.hidden = false;
      box.style.left = `${Math.min(a, b)}px`;
      box.style.width = `${Math.max(2, Math.abs(b - a))}px`;
    };
    const show = () => { paint(); renderRangePanel(panel, d, st, arts, prevDay, () => { st.range = null; show(); }); };
    let drag = null;
    el.addEventListener("pointerdown", (ev) => {
      if (ev.pointerType !== "mouse" || ev.button !== 0) return;
      const t = timeAt(ev);
      if (t == null) return;
      drag = { t0: t, x0: ev.clientX, moved: false };
      st.range = null;                     // 누를 때마다 이전 구간은 지우고 새로 고름
      paint();
      el.setPointerCapture(ev.pointerId);
    });
    el.addEventListener("pointermove", (ev) => {
      if (!drag) return;
      // 버튼을 누르고 있을 때만 끌기 (손 뗌 신호를 놓쳤으면 끌기를 끝냄 — 마우스만 움직여도 구간이 늘어나지 않게)
      if (!(ev.buttons & 1)) { drag = null; return; }
      if (Math.abs(ev.clientX - drag.x0) > 8) drag.moved = true;
      const t = timeAt(ev);
      if (drag.moved && t != null) { st.range = [drag.t0, t]; paint(); }
    });
    el.addEventListener("pointerup", (ev) => {
      // PC 끌기 = 직접 고른 구간. 클릭(폰은 탭) = 그 지점까지 갑자기 움직인 구간을 자동으로 찾음
      // (폰에서 화면을 끌어 스크롤하면 브라우저가 pointercancel을 보내므로 여기로 오지 않음)
      if (drag && drag.moved && st.range) st.range.sort((x, y) => x - y);
      else {
        const t = timeAt(ev);
        const r = t == null ? null : autoRange(d, t);
        if (!r) { drag = null; show(); return; }
        st.range = r;
      }
      drag = null;
      show();
    });
    el.addEventListener("lostpointercapture", () => { if (drag && !drag.moved) drag = null; });
    requestAnimationFrame(show);           // 오늘 카드를 다시 그려도 고른 구간은 유지
  }

  /** 클릭한 지점에서 최대 60분을 거슬러 올라가 움직임이 시작된 곳을 찾음
   *  (떨어졌으면 그 사이 가장 높았던 때, 올랐으면 가장 낮았던 때부터 클릭한 지점까지) */
  function autoRange(d, t) {
    const upto = d.bars.filter((x) => toTime(x.t) <= t);
    const end = upto.length ? upto[upto.length - 1] : d.bars[0];
    if (!end) return null;
    const te = toTime(end.t);
    const win = d.bars.filter((x) => { const u = toTime(x.t); return u >= te - 60 * 60 && u <= te; });
    const hi = win.reduce((m, x) => (x.c > m.c ? x : m), win[0]), lo = win.reduce((m, x) => (x.c < m.c ? x : m), win[0]);
    const drop = hi.c / end.c - 1, rise = end.c / lo.c - 1;     // 시작점에서 클릭 지점까지 얼마나 움직였나
    const down = drop >= rise, ext = down ? hi : lo, total = Math.abs(end.c - ext.c);
    // 최고(최저)점 뒤로 한동안 옆으로 기다 갑자기 움직인 경우, 실제로 움직이기 시작한 곳을 시작점으로:
    // 클릭 지점까지 남은 움직임이 전체의 85% 이상인 가장 늦은 때
    let start = ext;
    for (const x of win) {
      if (toTime(x.t) < toTime(ext.t)) continue;
      if (Math.abs(end.c - x.c) >= total * 0.85 && (down ? x.c >= end.c : x.c <= end.c)) start = x;
    }
    const ts0 = toTime(start.t);
    return [ts0 < te ? ts0 : Math.max(toTime(win[0].t), te - 30 * 60), te];
  }

  /** 고른 구간의 숫자 (브라우저에 있는 1분봉·KOSDAQ만으로 계산) */
  function rangeStats(d, a, b) {
    const inR = d.bars.filter((x) => { const t = toTime(x.t); return t >= a && t <= b; });
    if (!inR.length) return null;
    const first = inR[0], last = inR[inR.length - 1];
    const startPx = first.o ?? first.c, endPx = last.c;
    const lo = (x) => x.l ?? x.c, hi = (x) => x.h ?? x.c;
    const low = inR.reduce((m, x) => (lo(x) < lo(m) ? x : m)), high = inR.reduce((m, x) => (hi(x) > hi(m) ? x : m));
    const kq = d.kosdaq || [];
    const kAt = (t) => { let k = null; for (const x of kq) { if (toTime(x.t) <= t) k = x; else break; } return k; };
    const k0 = kAt(a - 60) || kq[0], k1 = kAt(b);
    const vol = inR.reduce((s, x) => s + (x.v || 0), 0);
    const out = d.bars.filter((x) => { const t = toTime(x.t); return t < a || t > b; });
    const outAvg = out.length ? out.reduce((s, x) => s + (x.v || 0), 0) / out.length : null;
    const pc = d.prev_close;
    return {
      from: hhmm(first.t), to: hhmm(last.t), minutes: inR.length,
      own: endPx / startPx - 1, startPx, endPx,
      fromPrev: pc ? endPx / pc - 1 : null,
      low: { at: hhmm(low.t), fromPrev: pc ? lo(low) / pc - 1 : null },
      high: { at: hhmm(high.t), fromPrev: pc ? hi(high) / pc - 1 : null },
      kq: k0 && k1 && k0 !== k1 ? k1.c / k0.c - 1 : null,
      vol, volRatio: outAvg ? (vol / inR.length) / outAvg : null,
    };
  }

  function renderRangePanel(panel, d, st, arts, prevDay, clear) {
    if (!st.range) {
      panel.innerHTML = `<p class="range-hint">차트를 클릭(폰은 탭)하면 그 지점까지 갑자기 움직인 구간을 찾아 원인을 물어볼 수 있습니다. PC는 마우스로 끌어 구간을 직접 고를 수도 있습니다.</p>`;
      return;
    }
    const r = rangeStats(d, st.range[0], st.range[1]);
    if (!r) {
      panel.innerHTML = `<p class="range-hint">고른 구간에 1분봉이 없습니다. 다시 골라 주세요. <button type="button" class="btn-link" data-range-clear>선택 해제</button></p>`;
      $("[data-range-clear]", panel).onclick = clear;
      return;
    }
    panel.innerHTML = `<div class="range-box">
      <div class="range-head"><b>${r.from} ~ ${r.to} 구간</b>
        <span>이 종목 <b class="num ${sgnCls(r.own)}">${pct(r.own)}</b>${r.fromPrev != null ? ` <span class="muted">(전일 대비 ${pct(r.fromPrev)})</span>` : ""}</span>
        <span>KOSDAQ <b class="num">${r.kq == null ? "-" : pct(r.kq)}</b></span>
        <span>거래량 <b class="num">${int(r.vol)}주</b>${r.volRatio ? ` <span class="muted">(그날 다른 시간의 ${r.volRatio.toFixed(1)}배)</span>` : ""}</span>
        <button type="button" class="btn-link" data-range-clear>선택 해제</button></div>
      <div class="ask-row"><button class="btn primary small" type="button" data-range-ask>이 구간 ChatGPT에게 묻기</button>
        <button class="btn small editable" type="button" data-range-rec>이 구간 기록하고 원인 넣기</button></div>
      <form class="mc-form editable" data-range-form hidden>
        <label><span>받은 답 붙여넣기 <small class="muted">(비워 두면 구간만 기록하고 원인은 나중에)</small></span><textarea rows="5" data-rf-body placeholder="ChatGPT 답을 그대로 붙여넣으세요 (Ctrl+V)."></textarea></label>
        <label>한 줄 요약 <small class="muted">(붙여넣으면 자동으로 채워짐)</small><input type="text" maxlength="200" data-rf-head></label>
        <p class="note" style="margin:0">${r.from}~${r.to} 구간이 이 날짜 아래 줄로 기록되고, 붙여넣은 원인이 그 줄에 붙습니다. 저장을 누르면 GitHub 창이 열리고, [Create]를 누르면 다음 자동 실행(장중 약 5분) 때 반영됩니다.</p>
        <div class="ask-row"><button class="btn primary small" type="submit">기록하기</button></div>
      </form>
      <span class="form-msg" data-range-msg></span></div>`;
    const msg = $("[data-range-msg]", panel);
    $("[data-range-clear]", panel).onclick = clear;
    $("[data-range-ask]", panel).onclick = () => openAsk(rangeContext(d, r, arts, prevDay, "이 구간 움직임의 원인을 찾아 줘."), msg);
    // 고른 구간을 기록(요청)으로 남기고 원인을 함께 붙임 → 다음 실행 뒤 이 날짜 아래 줄로 보임
    const form = $("[data-range-form]", panel), body = $("[data-rf-body]", panel), head = $("[data-rf-head]", panel);
    $("[data-range-rec]", panel).onclick = () => { form.hidden = !form.hidden; if (!form.hidden) body.focus(); };
    head.oninput = () => { head.dataset.touched = "1"; };
    body.oninput = () => { if (!head.dataset.touched) head.value = guessSummary(body.value).head; };
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      const h = head.value.replace(CONF_RE, "").trim(), b = body.value.trim();
      const item = { type: "analyze", date: d.date, from: r.from, to: r.to };
      if (b) Object.assign(item, { cause_body: b, cause_headline: h });
      try { msg.className = "form-msg"; msg.textContent = await sendInbox(item); form.hidden = true; }
      catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
    };
  }

  /** 구간 질문용 자료: 숫자·사실만 (원인 판단은 넣지 않음) */
  function rangeContext(d, r, arts, prevDay, question) {
    const name = ($("#q-name") && $("#q-name").textContent) || "이 종목";
    const L = [];
    L.push(`아래는 '${name}' 주가가 특정 구간에 갑자기 움직인 것에 대해 내 사이트가 가진 숫자와 사실 자료야. 원인 판단은 들어 있지 않아.`);
    L.push("이 구간의 움직임이 초점이지만, 원인을 이 시간 안에서만 찾지는 말아줘. 웹 검색으로 ① 전날 장 마감 뒤부터 이 시점까지 나온 이 종목 공시·기사·리포트 ② 밤사이 미국 증시·환율 등 해외 변수 ③ 이 종목이 속한 업종·테마의 최근 며칠 흐름과 이 구간 흐름 ④ 그날 시장 전체 흐름(지수, 외국인·기관 수급)을 확인해줘.");
    L.push("그리고 '이 구간에 직접 방아쇠가 된 일'과 '그 전부터 쌓여 있던 배경'을 구분해서 설명해줘. 확인된 사실과 추정을 구분하고, 근거 기사는 링크를 달아줘. 설명되지 않는 부분은 억지로 연결하지 말고 '미확인'으로 남겨줘.");
    L.push("답의 첫 줄은 '한 줄 요약 (신뢰도 높음/중간/낮음)' 형식으로 써줘. 한국어 존댓말로.", "");
    L.push(`[구간] ${d.date} ${r.from}~${r.to} (${r.minutes}분) · 이 종목 ${pct(r.own)} (${won(r.startPx)}원 → ${won(r.endPx)}원)`);
    if (d.prev_close) L.push(`[전일 대비] 전일 종가 ${won(d.prev_close)}원 · 구간 끝 ${pct(r.fromPrev)} · 구간 최저 ${pct(r.low.fromPrev)}(${r.low.at}) · 구간 최고 ${pct(r.high.fromPrev)}(${r.high.at})`);
    L.push(`[KOSDAQ 같은 구간] ${r.kq == null ? "데이터 없음" : pct(r.kq)}`);
    L.push(`[거래량] 구간 ${int(r.vol)}주${r.volRatio ? ` · 1분당 거래량이 그날 다른 시간의 ${r.volRatio.toFixed(1)}배` : ""}`);
    const since = `${prevDay || d.date}T15:30`, until = `${d.date}T${r.to}`;
    (arts || []).filter((a) => a.category !== "단순 언급" && !a.hidden && a.ts && a.ts >= since && a.ts <= until).slice(0, 8)
      .forEach((a) => L.push(`[이 종목 기사] ${a.ts.replace("T", " ")} 「${a.title}」${a.office ? ` (${a.office})` : ""}`));
    return `${L.join("\n")}\n\n[질문] ${question}`;
  }

  /** ChatGPT 새 창에 자료를 채워 엶 (보내기는 사용자가 직접). 따로 묻고 싶은 것은 그 창에서 덧붙임 */
  function openAsk(text, msg) {
    try { navigator.clipboard.writeText(text).catch(() => {}); } catch (_) {}
    window.open(`https://chatgpt.com/?q=${encodeURIComponent(text)}`, "_blank", "noopener");
    msg.className = "form-msg";
    msg.textContent = "ChatGPT 새 창을 열었습니다. 모델을 고른 뒤 보내고, 받은 답을 복사해 여기에 붙여넣으세요. (새 창 입력칸이 비어 있으면 Ctrl+V — 자료를 복사해 두었습니다)";
  }

  /** 지난 날짜: 한 줄 (날짜 · 종가 등락 · 그날 기록 · 원인 한 줄). 누르면 그 자리에서 펼침 */
  function foldRow(d, dayArts) {
    const chg = dayChange(d);
    const dailyEv = d.events.filter(isDaily).slice(-1)[0] || null;
    const intra = d.events.filter((e) => !isDaily(e));
    const lead = dailyEv || intra.find((e) => e.cause_headline) || null;
    const tags = (dailyEv ? `<span class="chip ${dailyEv.direction}">${kindOf(dailyEv)}</span>${tierTag(dailyEv)}` : "") +
      (intra.length ? `<span class="tier watch">장중 ${intra.length}건</span>` : "");
    const what = d.events.length
      ? (lead && lead.cause_headline ? `<span class="cause-label">원인</span><span class="cause-text">${esc(lead.cause_headline)}</span>` : "")
      : `급등·급락 없음${dayArts.length ? ` · 기사 ${dayArts.length}건` : ""}`;
    return `<button type="button" class="day-fold">
      <span class="day-card-date">${dayLabel(d.date)}</span>
      <span class="day-fold-val num ${sgnCls(chg)}">${chg == null ? "-" : pct(chg)}</span>
      <span class="day-fold-tags">${tags}</span>
      <span class="day-fold-what">${what}</span>
      <span class="day-fold-more">펼치기</span></button>`;
  }

  function dayChange(d) {
    if (!d.prev_close || !d.bars.length) return null;
    const live = liveFor(d.date, d);
    return live ? live.chg : d.bars[d.bars.length - 1].c / d.prev_close - 1;
  }

  function dayCardHtml(d, dayArts, isLatest) {
    const closed = d.bars.length && d.bars[d.bars.length - 1].t.slice(11) >= "15:30";
    // 하루 전체 등락 (전일 종가 대비)를 맨 위 한 칸으로. 하루 기록(급등·급락)이 있으면 이 칸이 곧 그 기록이고,
    // 같은 방향의 장중 조각(09:00 -4%, 09:55 -3% 등)은 이 안에 포함되므로 따로 나열하지 않음.
    const dailyEv = d.events.filter(isDaily).slice(-1)[0] || null;
    const rest = d.events
      .filter((e) => !isDaily(e))      // 장중 급등·급락 순간은 그때 기록 그대로 모두 남김
      .sort((x, y) => (x.start_ts < y.start_ts ? -1 : 1));          // 시간 순
    let dayTotal = `<div class="day-total"><div class="day-total-main"><span class="day-total-label">${dayLabel(d.date)}</span></div></div>`;
    if (d.prev_close && d.bars.length) {
      const lastBar = d.bars[d.bars.length - 1];
      const live = liveFor(d.date, d);
      const nowPx = live ? live.price : lastBar.c;
      const nowAt = live ? live.at : hhmm(lastBar.t);
      const chg = live ? live.chg : lastBar.c / d.prev_close - 1;
      const lowR = Math.min(...d.bars.map((b) => b.l ?? b.c)) / d.prev_close - 1;
      const highR = Math.max(...d.bars.map((b) => b.h ?? b.c)) / d.prev_close - 1;
      const wonT = (v) => `${Math.round(v).toLocaleString("ko-KR")}원`;
      const head = dailyEv
        ? `<span class="day-total-tags"><span class="chip ${dailyEv.direction}">${kindOf(dailyEv)}</span>${tierTag(dailyEv)}</span>`
        : "";
      const cause = dailyEv && dailyEv.cause_headline
        ? `<span class="day-total-cause"><span class="cause-label">원인</span><span class="cause-text">${esc(dailyEv.cause_headline)}</span></span>` : "";
      const inner = `
        <div class="day-total-main">
          <span class="day-total-label">${dayLabel(d.date)}${isLatest ? `<span class="day-now">${live && live.closed ? "장 마감" : closed && !(live && live.open) ? "최근 거래일" : `${nowAt} 현재`}</span>` : ""}${head}</span>
          <span class="day-total-sub">전일 종가 ${wonT(d.prev_close)} → ${closed && !(live && live.open) ? "종가 " : ""}${wonT(nowPx)} · 장중 최고 ${pct(highR)} / 최저 ${pct(lowR)}</span>
          ${isLatest && live && live.ah ? `<span class="day-total-sub day-total-ah">${ahLine(live.ah)}</span>` : ""}
          ${cause}
        </div>
        <span class="day-total-val num ${sgnCls(chg)}">${pct(chg)}</span>`;
      dayTotal = dailyEv
        ? `<button type="button" class="day-total is-event" data-id="${dailyEv.id}">${inner}</button>`
        : `<div class="day-total">${inner}</div>`;
    }
    const row = (e) => `
        <li><button class="tl-row" type="button" data-id="${e.id}">
          <span class="tl-time num">${hhmm(e.start_ts)}<small class="tl-to">~${hhmm(e.peak_ts)}</small></span>
          <span><span class="chip ${e.direction}">${kindOf(e)}</span>${tierTag(e)}</span>
          <span class="tl-rule">${e.cause_headline ? `<span class="cause-label">원인</span><span class="cause-text">${esc(e.cause_headline)}</span>` : esc(compareLine(e))}</span>
          <span class="tl-val num ${sgnCls(e.peak_return_5m)}">${pct(e.peak_return_5m)}<small>전일 대비</small></span>
        </button>${e.tier === "요청" ? `<div class="tl-actions editable"><button type="button" class="btn-link" data-ev-del="${e.id}">이 기록 삭제</button><span class="form-msg"></span></div>` : ""}</li>`;
    const chart = d.bars.length
      ? `<div class="day-legend"></div><div class="day-chart"></div><div class="range-panel"></div>`
      : `<p class="muted day-nochart">이날 1분봉 기록이 없습니다.</p>`;
    const list = rest.length
      ? `<ul class="timeline">${rest.map(row).join("")}</ul>`
      : (!dailyEv ? `<p class="day-calm">${isLatest ? "오늘은 아직 급등·급락이 없습니다." : "이날은 급등·급락이 없었습니다."}</p>` : "");
    const artsHtml = dayArts.length ? `<div class="day-arts"><h3>이날 기사${dayArts.length > 3 ? ` <span class="muted">${dayArts.length}건 중 3건</span>` : ""}</h3>
      <ul class="mini-arts">${dayArts.slice(0, 3).map((a) => `<li><a href="#/pr/${esc(a.id)}">
        <span class="al-meta">${a.manual ? '<span class="tier watch">직접 등록</span>' : ""}${catTag(a.category)}${toneTag(a.tone)}<span class="muted">${hhmm(a.ts)}</span>${measureList(a.effect && a.effect.measure)}</span>
        <span class="t">${esc(a.title)}</span></a></li>`).join("")}</ul></div>` : "";
    return dayTotal + chart + list + artsHtml;
  }

  function compareLine(e) {
    if (e.kosdaq_change == null) return `구간 안 ${pct(e.own_change)} · KOSDAQ 데이터 없음`;
    const alone = Math.abs(e.kosdaq_change) < 0.003 && Math.abs(e.own_change || 0) >= 0.01;
    return alone
      ? `구간 안 ${pct(e.own_change)} · KOSDAQ은 거의 그대로(${pct(e.kosdaq_change)})`
      : `구간 안 ${pct(e.own_change)} · KOSDAQ ${pct(e.kosdaq_change)}`;
  }

  function renderRules() {
    if (!STATUS) return;
    const r = STATUS.rules || {};
    $("#rules").innerHTML = `
      <li><span>하루: 종가가 전일보다</span><b>±${r.move_pct}% 이상</b></li>
      <li><span>장중: ${r.window_min}분 안에</span><b>±${r.move_pct}% 이상</b></li>
      <li><span>장중은 그 가격이 유지된 시간</span><b>${r.hold_min}분 이상</b></li>
      <li><span>장중 구간 거래대금</span><b>${r.min_value_eok}억 원 이상</b></li>
      <li><span>'대형' 표시</span><b>±${r.big_pct}% 이상</b></li>`;
  }

  // ---------------------------------------------------------------- events page
  const evState = { range: "1m", kind: "all", tier: "all" };

  async function viewTable() {
    $("#view").innerHTML = `
      <p class="page-sub" id="ev-sub" style="margin:0 0 14px"></p>
      <div class="controls">
        <div class="seg" id="ev-range" aria-label="기간">
          <button type="button" data-v="1w">1주</button><button type="button" data-v="1m">1개월</button><button type="button" data-v="3m">3개월</button><button type="button" data-v="all">전체</button>
        </div>
        <div class="seg" id="ev-kind" aria-label="구분">
          <button type="button" data-v="all">전체</button><button type="button" data-v="up">오름</button><button type="button" data-v="down">내림</button>
        </div>
        <div class="seg" id="ev-tier" aria-label="크기">
          <button type="button" data-v="all">모든 크기</button><button type="button" data-v="main" title="±5% 이상 (알림 보낸 것)">급등·급락만</button><button type="button" data-v="watch" title="±3~5% 관찰 + 직접 기록한 구간">관찰·직접 기록</button>
        </div>
        <span class="spacer"></span>
        <button class="btn editable" type="button" id="ask-toggle" title="기준에 안 걸린 날도 원인을 분석해 기록에 올립니다">분석 요청</button>
        <a class="btn" id="ev-csv" href="#">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 19h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>엑셀로 내려받기</a>
      </div>
      <form class="panel form pr-form editable" id="ask-form" autocomplete="off" hidden>
        <p class="hint">기준(±3%)에 안 걸려 기록되지 않은 움직임도 원인이 궁금하면 여기서 요청하세요. 시간대를 비우면 하루(전일 종가 → 종가) 기준, 시간을 넣으면 그 시간대만 분석합니다. 장중 약 5분, 그 밖에는 15분 안에 이 목록에 올라오고 원인 분석이 이어집니다.</p>
        <div class="pr-form-grid">
          <label class="field"><span>날짜</span><input type="date" name="date" value="${isoDate(new Date())}" required></label>
          <label class="field"><span>시작 시각 (선택)</span><input type="time" name="from" min="09:00" max="15:30"></label>
          <label class="field"><span>끝 시각 (선택)</span><input type="time" name="to" min="09:00" max="15:30"></label>
          <button class="btn primary" type="submit">분석 요청하기</button>
        </div>
        <div class="form-msg" id="ask-msg" role="status"></div>
      </form>
      <div class="panel table-panel"><div class="table-wrap" id="ev-table"><p class="loading" style="padding:20px">불러오는 중…</p></div></div>`;
    const bindSeg = (id, key) => {
      const el = $(id);
      const sync = () => el.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === evState[key])));
      sync();
      el.onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; evState[key] = b.dataset.v; sync(); loadEvents(); };
    };
    bindSeg("#ev-range", "range");
    bindSeg("#ev-kind", "kind");
    bindSeg("#ev-tier", "tier");
    $("#ask-toggle").onclick = () => { const f = $("#ask-form"); f.hidden = !f.hidden; };
    $("#ask-form").onsubmit = async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target), msg = $("#ask-msg");
      msg.className = "form-msg"; msg.textContent = "요청하는 중…";
      try {
        const item = { type: "analyze", date: f.get("date"), from: f.get("from") || null, to: f.get("to") || null };
        if (item.from && item.to && item.from >= item.to) throw new Error("끝 시각이 시작 시각보다 늦어야 합니다.");
        msg.textContent = CLOUD ? await sendInbox(item) : "PC 화면에서는 '다시 분석' 버튼을 이용해 주세요.";
      } catch (e) { msg.className = "form-msg err"; msg.textContent = e.message; }
    };
    await loadEvents();
  }

  function rangeFrom() {
    const days = { "1w": 7, "1m": 31, "3m": 92 }[evState.range];
    if (!days) return null;
    const d = new Date(); d.setDate(d.getDate() - days);
    return isoDate(d);
  }

  async function loadEvents() {
    const from = rangeFrom();
    const qs = new URLSearchParams();
    if (from) qs.set("from", from);
    let d;
    try { d = await api(`/api/events?${qs}`); }
    catch (e) { $("#ev-table").innerHTML = `<div class="empty" style="padding:20px"><strong>기록을 불러오지 못했습니다.</strong>${esc(e.message)}</div>`; return; }

    const all = d.events;
    const counts = { up: 0, down: 0, flat: 0 };
    all.forEach((e) => counts[e.direction]++);
    const since = d.trading_days[0];
    $("#ev-sub").textContent = all.length
      ? `${since ? dayLabel(since) + "부터 " : ""}${all.length}건. 급등·급락(±5% 이상) ${all.filter((e) => e.tier !== "관찰" && e.tier !== "요청").length}건, 관찰(±3~5%, 알림 없음) ${all.filter((e) => e.tier === "관찰").length}건${all.some((e) => e.tier === "요청") ? `, 직접 기록 ${all.filter((e) => e.tier === "요청").length}건` : ""}입니다 (하루 기준 ${all.filter(isDaily).length}건, 장중 ${all.filter((e) => !isDaily(e)).length}건).`
      : "이 기간에는 급등·급락이 없었습니다.";

    let list = all;
    if (evState.kind !== "all") list = list.filter((e) => e.direction === evState.kind);
    if (evState.tier === "main") list = list.filter((e) => e.tier !== "관찰" && e.tier !== "요청");
    else if (evState.tier === "watch") list = list.filter((e) => e.tier === "관찰" || e.tier === "요청");

    const csv = new URLSearchParams();
    if (from) csv.set("from", from);
    if (evState.kind !== "all") csv.set("kind", evState.kind);
    $("#ev-csv").href = `/api/events.csv?${csv}`;
    $("#ev-csv").onclick = CLOUD ? (ev) => { ev.preventDefault(); csvDownload(list); } : null;

    // 같은 날 안에서는 하루 전체 기록을 맨 위에, 장중 구간은 그 아래 (원래 순서 유지)
    list = list.map((e, i) => [e, i]).sort((x, y) => {
      const dx = x[0].start_ts.slice(0, 10), dy = y[0].start_ts.slice(0, 10);
      if (dx !== dy) return dx < dy ? 1 : -1;
      return (isDaily(y[0]) - isDaily(x[0])) || (x[1] - y[1]);
    }).map((x) => x[0]);

    if (!list.length) {
      $("#ev-table").innerHTML = `<div class="empty" style="padding:22px 16px;border:0"><strong>조건에 맞는 급등·급락이 없습니다.</strong>기간이나 구분을 바꿔 보세요.</div>`;
      return;
    }
    $("#ev-table").innerHTML = `<table class="data"><thead><tr>
        <th>일시</th><th>구분</th><th class="r">전일 대비</th><th class="r">KOSDAQ 같은 기간</th><th>원인</th></tr></thead><tbody>
      ${list.map((e, i) => {
        // 같은 날 기록은 날짜를 한 번만 적은 머리줄 아래로 묶음
        const day = e.start_ts.slice(0, 10);
        const head = i === 0 || list[i - 1].start_ts.slice(0, 10) !== day
          ? `<tr class="day-head"><td colspan="5">${dayLabel(day)}<small>${list.filter((x) => x.start_ts.startsWith(day)).length}건</small></td></tr>` : "";
        return head + `<tr data-id="${e.id}" tabindex="0"${isDaily(e) ? ' class="is-daily"' : ""}>
        <td class="when num">${isDaily(e) ? "하루<small>전일 종가 → 종가</small>" : `${hhmm(e.start_ts)}~${hhmm(e.peak_ts || e.last_ts)}`}</td>
        <td><span class="chip ${e.direction}">${kindOf(e)}</span>${scopeTag(e)}${tierTag(e)}</td>
        <td class="r num ${sgnCls(e.peak_return_5m)}"><b>${pct(e.peak_return_5m)}</b></td>
        <td class="r num">${e.kosdaq_change == null ? '<span class="muted">없음</span>' : pct(e.kosdaq_change)}</td>
        <td class="rule" title="${esc(e.rule)}">${e.cause_headline ? `${esc(e.cause_headline)}` : NO_CAUSE}</td></tr>`;
      }).join("")}
      </tbody></table>`;
    const tb = $("#ev-table tbody");
    tb.onclick = (ev) => { const r = ev.target.closest("tr[data-id]"); if (r) openEvent(+r.dataset.id); };
    tb.onkeydown = (ev) => { if (ev.key === "Enter") { const r = ev.target.closest("tr[data-id]"); if (r) openEvent(+r.dataset.id); } };
  }

  // 직접 기록한 구간 지우기 (두 번 눌러야 지워짐). 붙인 원인도 함께 지워짐
  async function deleteRecord(btn) {
    const msg = btn.nextElementSibling;
    if (btn.dataset.confirm !== "1") { btn.dataset.confirm = "1"; btn.textContent = "한 번 더 누르면 삭제"; return; }
    btn.disabled = true;
    try { msg.className = "form-msg"; msg.textContent = await sendInbox({ type: "event_delete", id: +btn.dataset.evDel }); btn.textContent = "삭제 요청함"; }
    catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; btn.disabled = false; }
  }

  // ---------------------------------------------------------------- event drawer
  let drawerCharts = [];
  function openDrawer(html) {
    $("#drawer-body").innerHTML = html;
    const dr = $("#drawer");
    dr.classList.add("open");
    dr.setAttribute("aria-hidden", "false");
    setTimeout(() => $(".drawer-close").focus(), 50);
  }
  function closeDrawer() {
    const dr = $("#drawer");
    dr.classList.remove("open");
    dr.setAttribute("aria-hidden", "true");
    drawerCharts.forEach((c) => { try { c.remove(); } catch (_) {} });
    charts = charts.filter((c) => !drawerCharts.includes(c));
    drawerCharts = [];
  }
  $("#drawer").addEventListener("click", (ev) => { if (ev.target.closest("[data-close]")) closeDrawer(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && $("#drawer").classList.contains("open")) closeDrawer(); });

  async function openEvent(id) {
    openDrawer(`<p class="loading">불러오는 중…</p>`);
    let d;
    try { d = await api(`/api/events/${id}`); }
    catch (e) { $("#drawer-body").innerHTML = `<div class="empty"><strong>불러오지 못했습니다.</strong>${esc(e.message)}</div>`; return; }
    renderEventDrawer(d);
  }

  const TIMING_CLS = { "선행": "lead", "동시": "same", "후행": "late", "확인 필요": "same" };

  function splitBlock(sp) {
    if (!sp || sp.own == null) return "";
    const fp = sp.factor_parts;
    const rows = fp ? [
      { k: "이 종목 실제 변동", v: sp.own, cls: "me" },
      { k: `시장 몫 <small>KOSDAQ ${pct(fp[0].factor)} × 민감도 ${fp[0].beta.toFixed(2)}</small>`, v: sp.market },
      ...fp.slice(1).map((f) => ({ k: `분야 몫 · ${esc(f.name)} <small>${f.kind === "seg" ? "비교 종목 평균" : "ETF"} ${pct(f.factor)} 중 시장과 무관한 부분 ${pct(f.factor_resid)} × 민감도 ${f.beta.toFixed(2)}</small>`, v: f.contrib })),
      { k: "이 종목 고유 몫 <small>= 실제 − 시장 몫 − 분야 몫</small>", v: sp.specific, cls: "abn" },
    ] : [
      { k: "이 종목 실제 변동", v: sp.own, cls: "me" },
      { k: `시장 몫 <small>KOSDAQ ${pct(sp.kosdaq)}${sp.beta_market ? ` × 민감도 ${sp.beta_market.toFixed(2)}` : ""}</small>`, v: sp.market },
      { k: `업종 몫 <small>${sp.sector_avg != null ? `같은 업종 평균 ${pct(sp.sector_avg)}` : "같은 업종 시세 없음"}</small>`, v: sp.sector },
      { k: "이 종목 고유 몫 <small>= 실제 − 시장 몫 − 업종 몫</small>", v: sp.specific, cls: "abn" },
    ];
    const max = Math.max(0.005, ...rows.map((r) => Math.abs(r.v || 0)));
    const share = sp.external_share;
    const judge = share == null ? "" : share >= 0.75 ? "대부분 시장·업종 전체 흐름으로 설명됩니다." : share >= 0.4 ? "절반가량은 시장·업종 흐름, 나머지는 이 종목 고유 요인입니다." : "시장·업종으로는 거의 설명되지 않아, 이 종목 고유 요인을 찾아야 하는 움직임입니다.";
    return `<ul class="rel decomp">${rows.map((r) => `<li class="${r.cls || ""}"><span class="nm">${r.k}</span>
        <span class="dbar"><i class="${(r.v || 0) >= 0 ? "b" : "s"}" style="${(r.v || 0) >= 0 ? "left:50%" : `left:${50 - (Math.abs(r.v) / max) * 50}%`};width:${(Math.abs(r.v || 0) / max) * 50}%"></i></span>
        <span class="num v ${sgnCls(r.v)}">${pct(r.v)}</span></li>`).join("")}</ul>
      <p class="flow-sum" style="margin-top:8px">${judge}${sp.abn_p != null ? ` 고유 몫은 ${esc(sp.abn_basis || "평소 기록 대비")}, 이만큼 이상 움직인 경우가 <b>${sp.abn_p < 0.01 ? "1% 미만" : `${(sp.abn_p * 100).toFixed(sp.abn_p < 0.1 ? 1 : 0)}%`}</b>입니다 — ${sp.abn_p <= 0.05 ? "드문 크기라 이례적입니다." : sp.abn_p <= 0.15 ? "다소 드문 크기입니다." : "평소에도 흔한 크기입니다."}` : ""}</p>`;
  }

  function causalBlock(ca, mv) {
    if (!ca) return "";
    const rows = [];
    const verdict = (ok, txt) => `<span class="cv ${ok === true ? "pass" : ok === false ? "fail" : "na"}">${txt}</span>`;
    const es = ca.event_study;
    if (es && es.ok) {
      const sig = es.p < 0.05;
      const pre = (es.car || []).find((x) => x.window.startsWith("사전"));
      rows.push(`<li><div class="ch">${verdict(sig, sig ? "이례적" : "평소 범위")}<b>${esc(es.method || "시장 회귀")}</b><small>추정에 사용한 ${es.n_est}거래일</small></div>
        <p>평소 관계로 예상되는 등락 ${pct(es.expected)} 대비 실제 ${pct(es.actual)} → 비정상수익률 <b>${pct(es.ar)}</b> (모형 가정에 따른 양측 p=${es.p.toFixed(3)}).
        ${pre ? ` 직전 5일 비정상수익률 합계 ${pct(pre.car)}(p=${pre.p.toFixed(2)})` : ""}
        ${es.vol_ratio ? ` 거래량은 평소의 ${es.vol_ratio.toFixed(1)}배.` : ""}</p></li>`);
    } else if (es) rows.push(`<li><div class="ch">${verdict(null, "자료 부족")}<b>시장만 감안한 계산</b></div><p>${esc(es.reason || "")}</p></li>`);
    const fa = ca.factor;
    if (fa && fa.ok) {
      const sig = fa.p != null && fa.p < 0.05;
      rows.push(`<li><div class="ch">${verdict(sig, sig ? "고유 움직임 큼" : "평소 범위")}<b>① 몫 나누기 계산식</b><small>시장(KOSDAQ) + 업종 ETF 회귀, 직전 ${fa.n_est}거래일</small></div>
        <p>${fa.parts.map((x) => `${esc(x.name)} ${pct(x.contrib)}`).join(" + ")} + 고유 <b>${pct(fa.specific)}</b> = 실제 ${pct(fa.actual)}.
        고유 몫은 평소 고유 변동(하루 ±${(fa.sigma * 100).toFixed(1)}%)의 ${Math.abs(fa.z).toFixed(1)}배(p=${fa.p.toFixed(3)}). 설명 비율(R²) ${(fa.r2 * 100).toFixed(0)}%.</p></li>`);
    }
    const ti = ca.intensity;
    if (ti && ti.vol_x) {
      const thin = ti.impact_x && ti.impact_x >= 2.5 && ti.vol_x < 2, heavy = ti.vol_x >= 2;
      rows.push(`<li><div class="ch">${verdict(heavy ? true : thin ? false : null, heavy ? "많은 거래 동반" : thin ? "얇은 호가" : "보통")}<b>③ 거래 강도</b><small>20일 평균 대비 거래량 · 같은 돈 대비 움직임</small></div>
        <p>거래량 ${int(ti.volume)}주로 20일 평균의 <b>${ti.vol_x.toFixed(1)}배</b>, 거래대금 ${eok(ti.value)}(평균 ${eok(ti.adval)}).
        ${ti.impact_x ? `같은 거래대금 대비 주가 움직임은 평소의 ${ti.impact_x.toFixed(1)}배입니다.` : ""}
</p></li>`);
    }
    if (!rows.length) return "";
    return `<details class="causal-more"><summary>계산 근거 자세히 보기</summary><ul class="causal">${rows.join("")}</ul></details>`;
  }

  // 숫자로 본 움직임 (자동 기록). 원인 결론·후보는 싣지 않음 — 원인은 직접 붙여넣은 글로
  function factsBlock(c) {
    const head = `<div class="sec-head"><h3 class="d-sec">숫자로 본 움직임</h3>
      <span class="stage">${c ? `자동 기록, ${esc((c.analyzed_at || "").slice(5, 16).replace("T", " "))} 기준` : "아직 계산 전"}</span></div>`;
    if (!c) return head + `<div class="empty"><strong>아직 계산 전입니다.</strong>다음 자동 수집 때 채워집니다.</div>`;
    return head + `<h4 class="d-sub-h">몫 나누기 · 시장 몫 + 업종 몫 + 이 종목 고유 몫</h4>${splitBlock(c.split) || '<p class="note">시장·업종 비교 자료가 없어 나누지 못했습니다.</p>'}
      ${causalBlock(c.causal, c.move)}`;
  }

  // 원인: ChatGPT 등에서 받은 답을 붙여넣은 글
  // 붙여넣은 답의 마크다운(### 제목, **굵게**, - 목록, 1. 번호, [글](주소), 주소)을 화면 형식으로
  function mdInline(t) {
    return esc(t)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(https?:\/\/[^\s<)\]]+)/g, (_, txt, url, bare) =>
        `<a class="ext" href="${url || bare}" target="_blank" rel="noopener">${txt || bare}</a>`)
      .replace(/\*\*([^*]+?)\*\*/g, "<b>$1</b>")
      .replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<i>$2</i>");
  }
  function mdRender(src) {
    const out = [];
    let list = null;                    // "ul" | "ol"
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    for (const raw of String(src).split(/\r?\n/)) {
      const ln = raw.trimEnd();
      let m;
      if (!ln.trim()) { close(); continue; }
      if ((m = ln.match(/^\s*#{1,6}\s+(.*)$/))) { close(); out.push(`<h4 class="mc-h">${mdInline(m[1])}</h4>`); continue; }
      if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(ln)) { close(); out.push("<hr>"); continue; }
      if ((m = ln.match(/^(\s*)[-*•]\s+(.*)$/)) || (m = ln.match(/^(\s*)\d+[.)]\s+(.*)$/))) {
        const kind = /^\s*\d/.test(ln) ? "ol" : "ul";
        if (list !== kind) { close(); out.push(`<${kind}>`); list = kind; }
        out.push(`<li${m[1].length >= 2 ? ' class="sub"' : ""}>${mdInline(m[2])}</li>`);
        continue;
      }
      close();
      out.push(`<p>${mdInline(ln.replace(/^>\s?/, ""))}</p>`);
    }
    close();
    return out.join("");
  }
  // 붙여넣은 ChatGPT 답에서 한 줄 요약·신뢰도 뽑기 (서버 core/manual.py summary·confidence와 같은 규칙)
  const CONF_RE = /[(\[（]?\s*신뢰도\s*[:：]?\s*(높음|중간|낮음)\s*[)\]）]?/;
  const SUM_LABEL = /^(한\s*줄\s*요약|요약|결론|종합\s*판단|종합)\s*[:：\-–—]?\s*/;
  function guessSummary(body) {
    const clean = (ln) => ln.replace(/^[>*\-•■□▶▷◆◇●○※\s\d.)]+/, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/\*\*/g, "").trim();
    const isTitle = (t) => (t.length <= 8 && !/[.다요음]$/.test(t)) || (/원인\s*분석/.test(t) && /\d{4}[.\-/]\s*\d{1,2}/.test(t));
    const lines = String(body || "").split(/\r?\n/).filter((ln) => !/^\s*#/.test(ln)).map(clean);
    let pick = null;
    const i = lines.findIndex((t) => /^한\s*줄\s*요약/.test(t));
    if (i >= 0) pick = lines[i].replace(CONF_RE, "").replace(SUM_LABEL, "").replace(/^[\s:：]+/, "").trim() || lines.slice(i + 1).find((x) => x) || null;
    if (!pick) pick = lines.find((x) => x && !isTitle(x)) || lines.find((x) => x) || "";
    const head = pick.replace(CONF_RE, "").replace(SUM_LABEL, "").trim();
    const cm = String(body || "").split(/\r?\n/).slice(0, 6).join("\n").match(CONF_RE);
    return { head: head.length > 80 ? head.slice(0, 80) + "…" : head, conf: cm ? cm[1] : "" };
  }

  // 원인 한 줄: 입력한 요약 → 없으면 본문에서 추린 줄 (신뢰도 표시는 하지 않음)
  function causeHead(m) {
    const h0 = m && m.headline ? m.headline.replace(/\*\*/g, "").replace(CONF_RE, "").trim() : "";
    return (m && m.headline ? h0.replace(SUM_LABEL, "").trim() || h0 : guessSummary(m && m.body).head) || "원인 요약 없음";
  }
  const kstNow = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 16);

  // 왜 움직였나: 넣은 원인을 위에 (하루 기록은 장중에 여러 번 넣을 수 있어 넣은 시각·그때 등락과 함께 최신부터).
  // 원인이 없으면 ① 묻기 → ② 붙여넣기 → ③ 저장 순서로 바로 보이게
  function manualBlock(e) {
    const m = e.manual;
    const daily = isDaily(e);
    const entries = m ? (m.entries && m.entries.length ? m.entries : [m]) : [];
    const day = e.start_ts.slice(0, 10);
    const when = (x) => {
      if (!daily || !x.start_ts) return "";
      const intra = x.start_ts.slice(0, 10) === day && hhmm(x.start_ts) < "15:30";
      return `<span class="mc-when">${intra ? `${hhmm(x.start_ts)} 기준` : "장 마감 뒤"}${x.move != null ? ` <b class="${sgnCls(x.move)}">${pct(x.move)}</b>` : ""}</span>`;
    };
    const view = entries.length ? entries.map((x, i) => `<div class="manual-cause">
        <div class="mc-head">${when(x)}${esc(causeHead(x))}</div>
        <div class="mc-body">${mdRender(x.body || "")}</div>
        <div class="ask-row editable mc-actions"><button class="btn-link" type="button" data-mc-edit="${i}">고치기</button><button class="btn-link" type="button" data-mc-del="${i}">삭제</button></div>
        </div>`).join("")
      : `<div class="manual-cause is-empty"><div class="mc-head">원인 미입력</div>
        <p class="note editable" style="margin:0">아래 순서대로 하면 이 칸과 목록에 원인이 표시됩니다.</p></div>`;
    const step = (n, t) => `<span class="step-n">${n}</span>${t}`;
    const has = entries.length > 0;
    return `<div class="sec-head"><h3 class="d-sec">왜 움직였나</h3>${daily && entries.length > 1 ? `<span class="stage">원인 기록 ${entries.length}개</span>` : ""}</div>
      ${view}
      <div class="ask-block">
        <div class="ask-row"><button class="btn primary" type="button" id="ask-gpt" title="숫자·사실 자료를 복사하고 ChatGPT 새 창을 엽니다">${has ? "ChatGPT에게 다시 묻기" : `${step(1, "ChatGPT에게 묻기")}`}</button>
          ${has ? `<button class="btn editable" type="button" id="mc-open">${daily ? "지금 원인 하나 더 넣기" : "원인 고치기"}</button>` : ""}</div>
        <span class="form-msg" id="ask-claude-msg"></span>
        <form class="mc-form editable" id="mc-form"${has ? " hidden" : ""}>
          <label><span>${has ? "원인 본문" : step(2, "받은 답 붙여넣기")}</span><textarea id="mc-body" rows="${has ? 10 : 5}" placeholder="ChatGPT 답을 그대로 붙여넣으세요 (Ctrl+V)."></textarea></label>
          <label>한 줄 요약 <small class="muted">(붙여넣으면 자동으로 채워짐, 고쳐도 됨)</small><input id="mc-head" type="text" maxlength="200" placeholder="예: 외국계 차익실현 + 시장 전반 위험회피"></label>
          <p class="note" style="margin:0" id="mc-note">${daily ? "지금 시각과 지금 등락이 함께 기록되고, 앞서 넣은 원인도 그대로 남습니다." : `${hhmm(e.start_ts)}~${hhmm(e.last_ts)} 순간의 원인으로 저장됩니다.`} 저장을 누르면 GitHub 창이 열립니다. 거기서 초록색 [Create]를 누르면 다음 자동 실행(장중 약 5분) 때 반영됩니다.</p>
          <div class="ask-row"><button class="btn primary" type="submit">${has ? "저장" : step(3, "저장")}</button>${has ? '<button class="btn" type="button" id="mc-cancel">취소</button>' : ""}</div>
        </form>
      </div>`;
  }

  const STATE_TXT = { top5: "", out: "5위 밖·멈춤", none: "미집계", unknown: "" };

  function flowBlock(c) {
    const f = c && c.flows, inv = c && c.investor_day;
    let html = `<h3 class="d-sec">누가 사고 팔았나</h3>`;
    if (!f && !inv) {
      return html + `<div class="empty"><strong>이 시간대 거래원 기록이 없습니다.</strong>증권사 창구별 거래는 감시 엔진이 켜져 있을 때 1분마다 기록됩니다. 투자자별 순매수는 장 마감 후 확정됩니다.</div>`;
    }
    if (f) {
      const all = [...f.top_buyers, ...f.top_sellers];
      const max = Math.max(1, ...all.map((r) => Math.abs(r.net)));
      const sideTxt = (v, st) => (v === 0 && st === "none" ? "미집계(5위 밖)" : `${int(v)}주${st === "out" ? " (5위 밖으로 밀려나 멈춤)" : ""}`);
      const row = (r) => {
        const tip = `매수 ${sideTxt(r.buy, r.buy_state)} / 매도 ${sideTxt(r.sell, r.sell_state)}`;
        const st = r.net > 0 ? r.buy_state : r.sell_state;
        return `<li title="${esc(tip)}"><span class="nm">${esc(r.name)}${r.foreign ? '<em class="fx">외국계</em>' : ""}${r.from_daum ? '<em class="st">다음 금융</em>' : ""}${STATE_TXT[st] ? `<em class="st">${STATE_TXT[st]}</em>` : ""}</span>
        <span class="fbar"><i class="${r.net >= 0 ? "b" : "s"}" style="width:${(Math.abs(r.net) / max) * 100}%"></i></span>
        <span class="num v ${r.net >= 0 ? "up-t" : "down-t"}">${r.net_is_min ? '<small>최소</small> ' : ""}${r.net >= 0 ? "+" : "−"}${int(Math.abs(r.net))}</span></li>`;
      };
      const basis = f.basis === "구간" ? `${hhmm(f.from_ts)}~${hhmm(f.to_ts)} 사이 증감` : f.basis === "장 마감 기록" ? "그날 장 마감 기록" : "그날 누적";
      html += `<p class="flow-sum">증권사 창구 기준(${basis}). 외국계 창구 합계 <b class="${f.foreign_net >= 0 ? "up-t" : "down-t"}">${f.foreign_net >= 0 ? "+" : "−"}${int(Math.abs(f.foreign_net))}주</b>${f.window_volume ? `, 같은 구간 전체 거래량 ${int(f.window_volume)}주` : ""}</p>
        <div class="flows"><div><h4>순매수 상위</h4><ul>${f.top_buyers.map(row).join("") || '<li class="muted">없음</li>'}</ul></div>
        <div><h4>순매도 상위</h4><ul>${f.top_sellers.map(row).join("") || '<li class="muted">없음</li>'}</ul></div></div>
        ${verifyBlock(f)}`;
    }
    if (inv) {
      const parts = [["외국인", inv.foreign_net], ["기관", inv.organ_net], ["개인", inv.individual_net]];
      const max = Math.max(1, ...parts.map((p) => Math.abs(p[1] || 0)));
      html += `<h4 class="sub">그날 하루 투자자별 순매수 (거래소 확정치)</h4><ul class="inv">${parts.map(([k, v]) => `
        <li><span class="nm">${k}</span><span class="dbar"><i class="${v >= 0 ? "b" : "s"}" style="${v >= 0 ? "left:50%" : `left:${50 - (Math.abs(v) / max) * 50}%`};width:${(Math.abs(v) / max) * 50}%"></i></span>
        <span class="num v ${v >= 0 ? "up-t" : "down-t"}">${v >= 0 ? "+" : "−"}${int(Math.abs(v || 0))}주</span></li>`).join("")}</ul>`;
    } else {
      html += `<p class="note">그날 투자자별(외국인·기관·개인) 확정 순매수는 장 마감 후 자동으로 추가됩니다.</p>`;
    }
    return html;
  }

  function verifyBlock(f) {
    const cc = f.crosscheck, eod = f.eod || {};
    let rows = [`<li><b>출처</b> ${esc(f.source || "")}</li>`,
];
    if (cc) {
      const ok = cc.compared && cc.matched === cc.compared && cc.foreign_sum.same;
      rows.push(`<li><b>교차 검증</b> ${esc(cc.ts.slice(11))}에 다음 금융과 대조: 두 곳 모두 상위 5위 안인 값 ${cc.compared}개 중 ${cc.matched}개 일치, 외국계 합계 ${cc.foreign_sum.same ? "일치" : `불일치(네이버 ${int(cc.foreign_sum.naver[0])} / 다음 ${int(cc.foreign_sum.daum[0])})`}${(cc.diffs || []).length ? `. 차이: ${cc.diffs.map((d) => `${esc(d.name)} ${d.side} 네이버 ${int(d.naver)} / 다음 ${int(d.daum)}`).join(", ")} (두 곳을 받은 몇 초 사이 체결로 생길 수 있음)` : ""}${(cc.missing_in_naver || []).length ? `<br>네이버 목록에 빠진 상위 창구를 다음 금융 값으로 보충했습니다: ${cc.missing_in_naver.map((m) => `${esc(m.name)} 매수 ${int(m.buy)} / 매도 ${int(m.sell)}`).join(", ")}` : ""}${(cc.frozen_diffs || []).length ? `<br><span class="muted">참고: 5위 밖에서 멈춘 값은 멈춘 시점에 따라 두 곳이 다를 수 있어 판정에서 뺐습니다 (${cc.frozen_diffs.map((d) => `${esc(d.name)} ${d.side} ${int(d.naver)}/${int(d.daum)}`).join(", ")}).</span>` : ""}</li>`);
    }
    const srcs = Object.keys(eod);
    if (srcs.length) {
      const names = [...new Set([...(f.top_buyers || []), ...(f.top_sellers || [])].slice(0, 3).map((r) => r.name))];
      const pick = (src, nm) => (eod[src] || []).find((x) => x.name.replace(/\s|증권|투자/g, "") === nm.replace(/\s|증권|투자/g, ""));
      rows.push(`<li><b>장 마감 기록</b> <span class="muted">(장 마감 직후 기준)</span><br>${names.map((nm) => `${esc(nm)} ${srcs.map((src) => { const x = pick(src, nm); return `${src} ${x ? `매수 ${int(x.buy)}/매도 ${x.sell || x.sell_top5 ? int(x.sell) : "미집계"}` : "목록에 없음"}`; }).join(" · ")}`).join("<br>")}</li>`);
    }
    return `<details class="verify"><summary>이 숫자는 얼마나 정확한가요?</summary><ul>${rows.join("")}</ul></details>`;
  }

  /** 후속 공시 (실제 보유자·보유 목적). 같은 창구의 업종 거래 목록은 화면에서 뺌 (ChatGPT 자료에는 그대로 넣음) */
  function followupBlock(followups) {
    return (followups || []).length ? `<div class="followup"><b>후속 공시</b> ${followups.map((x) => `${esc(x.date)} ${esc(x.title)}`).join(", ")} — 실제 보유자와 보유 목적이 이 공시에 나옵니다.</div>` : "";
  }

  function eok(v) {
    if (v == null) return "-";
    const a = Math.abs(v) / 1e8;
    return a >= 0.1 ? `${v < 0 ? "−" : ""}${a.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억 원` : `${v < 0 ? "−" : ""}${Math.round(Math.abs(v) / 1e4).toLocaleString("ko-KR")}만 원`;
  }

  function relatedBlock(c, own) {
    const rel = (c && c.related) || [];
    if (!rel.length) return "";
    const valid = rel.filter((r) => r.change != null);
    const max = Math.max(0.005, Math.abs(own || 0), ...valid.map((r) => Math.abs(r.change || 0)));
    const bar = (v) => `<span class="dbar"><i class="${(v || 0) >= 0 ? "b" : "s"}" style="${(v || 0) >= 0 ? "left:50%" : `left:${50 - (Math.abs(v) / max) * 50}%`};width:${(Math.abs(v || 0) / max) * 50}%"></i></span>`;
    const groups = {};
    valid.forEach((r) => { (groups[r.source || "비교 종목"] = groups[r.source || "비교 종목"] || []).push(r); });
    const segs = (c.segments || []).map((g) => g.name).filter((n) => groups[n]);
    Object.keys(groups).forEach((n) => { if (!segs.includes(n)) segs.push(n); });
    const avg = (ms) => ms.reduce((a, m) => a + m.change, 0) / ms.length;
    return `<h3 class="d-sec">이 종목 사업 분야별 비교 종목</h3>
      ${valid.length ? `<ul class="rel"><li class="me"><span class="nm">이 종목</span>${bar(own)}<span class="num v ${sgnCls(own)}">${pct(own)}</span></li></ul>
        ${segs.map((n) => { const ms = groups[n].sort((a, b) => (b.change || 0) - (a.change || 0)); const a = avg(ms);
          const sg = (c.segments || []).find((g) => g.name === n);
          const lk = sg && sg.link != null ? `<span class="muted"> · 평소 연동성 ${sg.link.toFixed(2)}${sg.link_lo != null ? ` (95% 범위 ${sg.link_lo.toFixed(2)}~${sg.link_hi.toFixed(2)}, ${esc(sg.link_label)})` : ""}</span>` : "";
          return `<div class="seg-group"><div class="seg-head"><b>${esc(n)}${lk}</b><span class="num ${sgnCls(a)}">평균 ${pct(a)}</span></div>
          <ul class="rel">${ms.map((r) => `<li><span class="nm">${esc(r.name)}</span>${bar(r.change)}<span class="num v ${sgnCls(r.change)}">${pct(r.change)}</span></li>`).join("")}</ul></div>`; }).join("")}
        ${e_isDailyNote(c) ? `<p class="note">${e_isDailyNote(c)}</p>` : ""}`
        : `<p class="note">이 시간대 비교 종목 시세가 없습니다.</p>`}`;
  }
  const e_isDailyNote = (c) => (c && c.move && c.move.from && c.move.to ? "같은 기간 변동률입니다." : "");

  /** 공시 (뉴스 목록은 화면에서 뺌: 이 종목 기사는 '이 종목 기사' 칸에 있음. ChatGPT 자료에는 그대로 넣음) */
  function disclosureBlock(c) {
    const discl = (c && c.disclosures) || [];
    if (!discl.length) return "";
    return `<section class="d-block"><h3 class="d-sec">공시</h3><ul class="news">
      ${discl.map((x) => `<li><span class="when">${esc(x.date.slice(5))}</span><span class="timing same">공시</span><span class="tt">${esc(x.title)}</span></li>`).join("")}
    </ul></section>`;
  }

  function renderEventDrawer(d) {
    const e = d.event, c = d.cause;
    const daily = isDaily(e);
    const running = daily && e.status === "open";           // 장 마감 전 하루 기록
    const live = running ? liveFor(e.start_ts.slice(0, 10)) : null;
    const mv = live ? live.chg : e.peak_return_5m;          // 오늘은 대시보드 맨 위 현재가와 같은 숫자
    // 장중: 큰 숫자 하나 = 전일 종가 대비 (알림·감지 기준과 같음). 구간 안 변동은 보조 숫자
    const held = !daily && e.peak_ts && e.last_ts && e.last_ts > e.peak_ts ? ` · ${hhmm(e.last_ts)}까지 유지` : "";
    $("#drawer-body").innerHTML = `
      <span class="chip ${e.direction}">${kindOf(e)}</span>${scopeTag(e)}${tierTag(e)}
      <h2 class="d-title" id="drawer-title">${dayLabel(e.start_ts)}${daily ? "" : ` ${hhmm(e.start_ts)}~${hhmm(e.peak_ts)}`} <span class="${sgnCls(mv)}">${pct(mv)}</span></h2>
      <p class="d-sub">${running
        ? `전일 종가 대비 ${live ? live.at + " 현재" : "지금까지"} 움직임입니다. 장 마감 뒤 종가로 확정됩니다.`
        : daily ? "전일 종가 → 종가 (거래소 확정치)" : `전일 종가 대비 ${hhmm(e.peak_ts)} 가격${held}`}</p>
      <dl class="facts${daily ? "" : " four"}">
        <div><dt>${running ? "지금 (전일 대비)" : daily ? "종가 등락" : "전일 대비"}</dt><dd class="num ${sgnCls(mv)}">${pct(mv)}</dd></div>
        ${daily ? "" : `<div><dt>구간 안 변동</dt><dd class="num ${sgnCls(e.own_change)}">${e.own_change == null ? "-" : pct(e.own_change)}<small>${hhmm(e.start_ts)}~${hhmm(e.last_ts)}</small></dd></div>`}
        <div><dt>KOSDAQ 같은 ${daily ? "날" : "구간"}</dt><dd class="num">${e.kosdaq_change == null ? '<small style="margin:0">데이터 없음</small>' : pct(e.kosdaq_change)}</dd></div>
        <div><dt>거래대금</dt><dd class="num">${e.trade_value ? eok(e.trade_value) : "-"}</dd></div>
      </dl>
      <section class="d-block d-conclusion">${manualBlock(e)}</section>
      ${e.tier === "요청" ? `<p class="editable" style="margin-top:12px"><button class="btn danger small" type="button" data-ev-del="${e.id}">이 기록 삭제</button><span class="form-msg"></span></p>` : ""}
      <section class="evidence"><h3 class="evidence-title">근거 자세히 <span class="muted">몫 나누기 · 비교 종목 · 매매 창구</span></h3>
        <section class="d-block">${factsBlock(c)}</section>
        <section class="d-block">${relatedBlock(c, e.own_change)}</section>
        ${disclosureBlock(c)}
        <section class="d-block">${flowBlock(c)}${followupBlock(d.followups)}</section>
        <div class="rule-full"><b>감지 근거</b><br>${esc(e.rule)}</div>
      </section>`;
    $("#ask-gpt").onclick = () => openAsk(eventContext(d, "이 움직임의 원인을 찾아 줘."), $("#ask-claude-msg"));
    const dDel = $("#drawer-body [data-ev-del]");
    if (dDel) dDel.onclick = () => deleteRecord(dDel);
    // 원인 붙여넣기·고치기·삭제 → 암호화된 요청(GitHub 이슈)으로 보냄
    const form = $("#mc-form"), msg = $("#ask-claude-msg");
    const entries = e.manual ? (e.manual.entries && e.manual.entries.length ? e.manual.entries : [e.manual]) : [];
    const day = e.start_ts.slice(0, 10);
    let editing = null;                       // 고치는 중인 원인 (없으면 새로 넣기)
    const sendReq = async (item) => {
      msg.className = "form-msg";
      try { msg.textContent = await sendInbox(item); } catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
    };
    const headIn = $("#mc-head"), bodyIn = $("#mc-body");
    const openForm = (x) => {
      editing = x;
      bodyIn.value = x ? x.body || "" : "";
      headIn.value = x ? causeHead(x) : "";
      headIn.dataset.touched = x ? "1" : "";
      form.hidden = false; bodyIn.focus();
    };
    if ($("#mc-open")) $("#mc-open").onclick = () => openForm(daily ? null : entries[0] || null);
    if ($("#mc-cancel")) $("#mc-cancel").onclick = () => { form.hidden = true; editing = null; };
    $("#drawer-body").querySelectorAll("[data-mc-edit]").forEach((b) => { b.onclick = () => openForm(entries[+b.dataset.mcEdit]); });
    $("#drawer-body").querySelectorAll("[data-mc-del]").forEach((b) => {
      b.onclick = () => {
        if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = "한 번 더 누르면 삭제"; return; }
        const x = entries[+b.dataset.mcDel];
        b.disabled = true; b.textContent = "삭제 요청함";
        sendReq({ type: "cause_delete", date: x.date, direction: x.direction, scope: x.scope, start_ts: x.start_ts, last_ts: x.last_ts, at_ts: x.start_ts });
      };
    });
    // 붙여넣으면 한 줄 요약을 자동으로 채움 (직접 고친 칸은 건드리지 않음)
    headIn.oninput = () => { headIn.dataset.touched = "1"; };
    bodyIn.oninput = () => { if (!headIn.dataset.touched) headIn.value = guessSummary(bodyIn.value).head; };
    form.onsubmit = (ev) => {
      ev.preventDefault();
      const body = bodyIn.value.trim();
      if (!body) { msg.className = "form-msg err"; msg.textContent = "ChatGPT 답을 붙여넣어 주세요."; return; }
      const headline = headIn.value.replace(CONF_RE, "").trim();
      if (!daily) {
        sendReq({ type: "cause", date: day, direction: e.direction, scope: "intraday", start_ts: e.start_ts, last_ts: e.last_ts, headline, body });
        return;
      }
      // 하루 기록: 새로 넣으면 지금 시각·지금 등락으로 하나 더, 고치면 그 시각의 기록을 덮어씀
      const live = liveFor(day);
      const item = editing
        ? { type: "cause", date: day, direction: editing.direction, scope: "day", at_ts: editing.start_ts, move: editing.move, headline, body }
        : { type: "cause", date: day, direction: e.direction, scope: "day", at_ts: kstNow(), move: live ? live.chg : e.peak_return_5m, headline, body };
      sendReq(item);
    };
    const btn = $("#re-analyze");
    if (btn) btn.onclick = async () => {
      btn.disabled = true; btn.textContent = "분석하는 중…";
      try {
        await api(`/api/events/${e.id}/analyze`, { method: "POST" });
        drawerCharts.forEach((x) => { try { x.remove(); } catch (_) {} });
        drawerCharts = [];
        renderEventDrawer(await api(`/api/events/${e.id}`));
      } catch (err) { btn.disabled = false; btn.textContent = "다시 분석"; alertInline(btn, err.message); }
    };
  }

  // 원인 질문용: 숫자·사실 자료만 정리 (사이트의 자동 추정 결론은 넣지 않음 — 답이 그 결론에 끌려가지 않게)
  function eventContext(d, question) {
    const e = d.event, c = d.cause || {};
    const name = ($("#q-name") && $("#q-name").textContent) || "이 종목";
    const L = [];
    L.push(`아래는 '${name}' 주가 움직임에 대해 내 사이트가 자동으로 모은 숫자와 사실 자료야. 원인 판단은 들어 있지 않아.`);
    L.push("웹 검색으로 ① 그날 시장 전체 흐름(지수, 외국인·기관 수급) ② 이 종목이 속한 업종·테마의 최근 며칠 흐름 ③ 이 종목 자체 소식(공시·기사·리포트)을 확인해서 원인을 찾아줘.");
    L.push("[몫 나누기]의 이 종목 고유 몫이 평소에도 흔한 크기(아래 '드문 정도'가 5% 이상)라면 이 종목만의 원인은 따로 없을 수 있어. 그때는 ①② 시장·업종이 그날 왜 움직였는지를 먼저 밝히고, ③ 종목 소식은 고유 몫을 설명할 근거가 있을 때만 원인으로 써줘.");
    L.push("확인된 사실과 추정을 구분하고, 근거 기사는 링크를 달아줘. 설명되지 않는 부분은 억지로 연결하지 말고 '미확인'으로 남겨줘.");
    L.push("검색한 자료는 아래 거래일 날짜와 맞는지 꼭 확인하고, 검색으로 확인한 지수·등락률이 아래 숫자와 다르면 다르다고 적어줘.");
    L.push("답의 첫 줄은 '한 줄 요약'으로 써줘. 한국어 존댓말로.", "");
    const ca = c.causal || {};
    const recent = (ca.recent || []).filter((x) => x.own != null);
    const md = (s) => `${+s.slice(5, 7)}/${+s.slice(8, 10)}`;
    const day = e.start_ts.slice(0, 10);
    const prev = recent.length ? recent[recent.length - 1].date : null;
    const ti = ca.intensity;
    const valX = ti && ti.value_x != null ? `직전 20거래일 평균의 ${ti.value_x.toFixed(1)}배` : null;
    L.push(isDaily(e)
      ? `[움직임] ${day} 하루 (${prev ? md(prev) : "전일"} 종가 → ${md(day)} 종가) ${pct(e.peak_return_5m)} · KOSDAQ ${e.kosdaq_change == null ? "-" : pct(e.kosdaq_change)} · 거래대금 ${e.trade_value ? eok(e.trade_value) : "-"}${valX ? ` (${valX})` : ""}`
      : `[움직임] ${e.start_ts.replace("T", " ")}~${hhmm(e.last_ts)} ${pct(e.peak_return_5m)} · KOSDAQ ${e.kosdaq_change == null ? "-" : pct(e.kosdaq_change)} · 거래대금 ${e.trade_value ? eok(e.trade_value) : "-"}${valX ? ` · 그날 하루 거래대금 ${valX}` : ""}`);
    if (recent.length) L.push(`[직전 ${recent.length}거래일] ${recent.map((x) => `${md(x.date)} ${pct(x.own)}(KOSDAQ ${x.kosdaq == null ? "-" : pct(x.kosdaq)})`).join(", ")}`);
    const sp = c.split;
    if (sp && sp.own != null) {
      const kq = sp.kosdaq != null ? sp.kosdaq : e.kosdaq_change;
      const mk = sp.beta_market != null && kq != null ? ` (KOSDAQ ${pct(kq)} × 이 종목의 평소 시장 민감도 ${sp.beta_market.toFixed(2)})` : "";
      L.push(`[몫 나누기] 실제 ${pct(sp.own)} = 시장 ${pct(sp.market)}${mk} + 업종 ${pct(sp.sector)} + 이 종목 고유 ${pct(sp.specific)}`);
      const parts = (sp.factor_parts || []).slice(1).filter((p) => p.contrib != null && Math.abs(p.contrib) >= 0.0005);
      if (sp.method === "분야별 회귀") L.push(`[업종 몫 계산법] 비교 종목 평균을 그대로 더한 값이 아님. 분야별 등락에서 시장과 같이 움직인 부분을 빼고, 이 종목이 평소 그 분야와 얼마나 같이 움직였는지(직전 약 120거래일)를 곱한 합${parts.length ? ` — ${parts.map((p) => `${p.name} ${pct(p.contrib)}(분야 등락 ${pct(p.factor)}${p.factor_resid != null ? `, 시장 몫 뺀 뒤 ${pct(p.factor_resid)}` : ""})`).join(", ")}` : ""}`);
      if (sp.rarity != null) L.push(`[고유 몫 드문 정도] 평소 ${sp.n_null}거래일의 고유 움직임 중 이만큼 이상 컸던 날 ${(sp.rarity * 100).toFixed(0)}%`);
      else if (sp.z != null) L.push(`[고유 몫 크기] 평소 하루 고유 변동폭의 ${Math.abs(sp.z).toFixed(1)}배`);
    }
    const f = c.flows;
    if (f) {
      const r = (x) => `${x.name}${x.foreign ? "(외국계)" : ""} ${x.net >= 0 ? "+" : ""}${int(x.net)}주`;
      L.push(`[창구] 순매수 상위: ${(f.top_buyers || []).map(r).join(", ") || "-"} / 순매도 상위: ${(f.top_sellers || []).map(r).join(", ") || "-"} / 외국계 합계 ${int(f.foreign_net || 0)}주`);
    } else L.push("[창구] 사이트에 이 시간대 거래원 기록 없음 (없다는 뜻이 아니라 수집되지 않음)");
    const inv = c.investor_day;
    if (inv) L.push(`[투자자별 확정치] 외국인 ${int(inv.foreign_net || 0)}주, 기관 ${int(inv.organ_net || 0)}주, 개인 ${int(inv.individual_net || 0)}주`);
    else L.push("[투자자별 확정치] 사이트에 기록 없음 (수집되지 않음, 직접 확인 필요)");
    const b = c.buyer;
    if (b) {
      (b.lines || []).filter((l) => ["업종 묶음", "개별 관심", "업종 일부", "연속성", "외국인 확정치"].includes(l.kind))
        .forEach((l) => L.push(`[${l.kind}] ${l.text}`));
      const peers = (b.peers || []).filter((p) => p.listed && p.net);
      if (peers.length) L.push(`[같은 날 ${b.broker} 창구의 비교 종목 매매] ${peers.map((p) => `${p.name} ${p.net >= 0 ? "+" : ""}${int(p.net)}주`).join(", ")}`);
    }
    const rel = (c.related || []).filter((x) => x.change != null);
    if (rel.length) {
      const g = {};
      rel.forEach((x) => { (g[x.source || "비교 종목"] = g[x.source || "비교 종목"] || []).push(x); });
      Object.entries(g).forEach(([k, ms]) => L.push(`[비교 종목: ${k}] 평균 ${pct(ms.reduce((a, x) => a + x.change, 0) / ms.length)} — ${ms.sort((a, x) => a.change - x.change).slice(0, 6).map((x) => `${x.name} ${pct(x.change)}`).join(", ")}`));
    }
    const ac = c.articles;
    const lead = (ac && ac.lead) || [];
    if (lead.length) lead.slice(0, 5).forEach((a) => L.push(`[이 종목 기사] ${a.ts.replace("T", " ")} 「${a.title}」`));
    else L.push("[이 종목 기사] 사이트가 모은 기사 없음 (직접 검색 필요)");
    const ds = c.disclosures || [];
    if (ds.length) ds.slice(0, 5).forEach((x) => L.push(`[공시] ${x.date} ${x.title}`));
    else L.push("[공시] 사이트 기록상 당일·직전 거래일 공시 없음 (사이트 수집 누락일 수 있으니 DART 확인 필요)");
    let text = L.filter((x) => x !== undefined).join("\n");
    if (text.length > 5500) text = text.slice(0, 5500) + "\n…(일부 생략)";
    return `${text}\n\n[질문] ${question}`;
  }

  function alertInline(el, msg) {
    const p = document.createElement("p");
    p.className = "note"; p.style.color = "var(--up)"; p.textContent = msg;
    el.parentElement.after(p);
  }

  // ---------------------------------------------------------------- articles (기사·공시 모음)
  const CAT_CLS = { "회사 발표": "c-co", "언론 분석": "c-press", "리서치": "c-res", "테마·수혜주": "c-theme", "시황·특징주": "c-mkt", "단순 언급": "c-etc" };
  const TONE_TXT = { 1: "호재", "-1": "악재", 0: "" };
  const catTag = (c) => `<span class="ctag ${CAT_CLS[c] || "c-etc"}">${esc(c)}</span>`;
  const toneTag = (t) => (t ? `<span class="tone ${t > 0 ? "pos" : "neg"}">${t > 0 ? "호재" : "악재"}</span>` : "");
  const verdictTag = (e) => (e && e.verdict ? `<span class="vtag v-${e.verdict_cls}">${esc(e.verdict)}</span>` : "");
  const artState = { cat: "main" };

  function measureList(measure) {
    if (!measure || measure.method !== "event-study-v2") return "";
    return `<span class="muted">실제 ${pct(measure.own)} · 예상 대비</span><span class="num ${sgnCls(measure.value)}">${points(measure.value)}</span><span class="muted">반응일 기준</span>`;
  }

  function measureTxt(m) {
    if (!m) return "";
    if (m.method === "event-study-v2") return `반응일 실제 등락 <b class="${sgnCls(m.own)}">${pct(m.own)}</b> · 평소 예상 ${pct(m.expected)}<br>
      ${esc(m.label)} <b class="${sgnCls(m.value)}">${points(m.value)}</b> · 95% 범위 ${points(m.ci_low)} ~ ${points(m.ci_high)}`;
    return "새 계산 방식으로 다시 계산한 뒤 표시합니다.";
  }
  // 평소 기록과 비교한 드문 정도 (정규분포 가정 없이: 평소 같은 길이 고유 움직임 중 이번 이상이었던 비율)
  function rarityTxt(rr, n, unit) {
    if (rr == null) return "";
    const p = rr * 100;
    return `평소 ${unit} ${n ? `${n}개 중` : ""} 이만큼 이상 움직인 경우 ${p < 1 ? "1% 미만" : `${p.toFixed(p < 10 ? 1 : 0)}%`}`;
  }

  // 계산 방법: 화면 곳곳에 있던 설명을 한곳에 (화면에는 숫자와 그 항목에만 해당하는 사실만 남김)
  function pageMethod() {
    const sec = (id, title, body) => `<section class="method-sec" id="m-${id}"><h2>${title}</h2>${body}</section>`;
    const toc = [["words", "먼저 알아 둘 말"], ["articles", "기사·공시 모음"], ["events", "급등·급락 (주가 분석)"], ["limits", "한계"]];
    main.innerHTML = `<div class="wrap method">
      <div class="page-head"><div><h1 class="page-title">README</h1>
        <p class="page-sub">사이트의 숫자를 어떻게 계산하는지, 어떻게 읽으면 되는지 모아 둔 곳입니다.</p></div></div>
      <nav class="method-toc" aria-label="목차">${toc.map(([id, t]) => `<a href="#/method" data-to="m-${id}">${t}</a>`).join("")}</nav>
      ${sec("words", "먼저 알아 둘 말", `
        <dl>
          <dt>반응일</dt><dd>기사·공시가 주가에 처음 반영될 수 있는 거래일입니다. 장 마감(15:30) 뒤나 휴장일에 나왔으면 다음 거래일입니다. 같은 내용의 공시가 36시간 안에 먼저 나왔으면 공시 시각부터 잽니다.</dd>
          <dt>평소 예상</dt><dd>그날 코스닥과 비교 종목이 움직인 만큼, 이 종목이 평소 관계대로라면 움직였을 값입니다. 평소 관계는 과거 주가로 추정합니다.</dd>
          <dt>예상 대비 차이 (%p)</dt><dd>실제 등락 − 평소 예상. 양수는 실제로 올랐다는 뜻이 아니라 예상보다 높았다는 뜻입니다.</dd>
          <dt>고유 움직임</dt><dd>실제 등락에서 시장 몫과 업종(분야) 몫을 뺀 나머지입니다. 회사 소식, 수급, 그 밖의 이유가 모두 섞여 있습니다.</dd>
          <dt>95% 범위</dt><dd>평소 주가의 출렁임을 감안했을 때 차이가 있을 만한 범위입니다. 범위에 0이 들어가면 차이를 확정할 수 없다는 뜻이지, 영향이 없었다는 뜻은 아닙니다. 하루 주가는 출렁임이 커서 기사 하나로는 범위가 넓게 나옵니다. 0이 없어도 원인이 확정되지는 않습니다.</dd>
          <dt>평소 같은 구간 중 이보다 크게 움직인 비율</dt><dd>최근 같은 길이 구간들의 고유 움직임 중 이번 이상으로 컸던 비율입니다. 3%면 평소 그런 구간 100개 중 3개꼴이라는 뜻입니다. 정규분포를 가정하지 않고 실제 기록과 바로 비교합니다.</dd>
          <dt>p값</dt><dd>평소 관계가 그대로라면 이만큼 차이가 우연히 나올 확률입니다. 작을수록 드뭅니다. 여러 구간을 동시에 보면 우연히 작게 나오는 구간도 생깁니다.</dd>
        </dl>`)}
      ${sec("articles", "기사·공시 모음", `
        <h3>탭과 건수</h3>
        <ul>
          <li><b>그린광학 보도자료</b>: 직접 등록한 보도자료와, 제목·본문으로 '회사 발표'로 분류된 기사입니다. 목록에서 구분을 직접 고칠 수 있습니다.</li>
          <li><b>외부 기사</b>: 그 밖의 기사입니다. 이름만 나오는 기사(단순 언급)와 주가 움직임을 다룬 기사(시황·특징주)는 뺍니다.</li>
          <li><b>공시</b>: DART·거래소(KIND) 공시입니다. 위 종류 버튼으로 켠 공시만 목록과 건수에 들어갑니다.</li>
          <li>같은 반응일에 나온 기사는 주가 반응을 나눌 수 없어 한 건으로 셉니다. 한 달 평균은 최근 6개월 건수 ÷ 6입니다. 실린 매체는 같은 내용을 낸 매체 수입니다.</li>
        </ul>
        <h3>소식 없는 날과 비교</h3>
        <ul>
          <li>최근 2년의 거래일을 그날 있었던 소식으로 나눕니다: 그린광학 보도자료 날 → 주요 공시 날 → 외부 기사만 있는 날 → 소식 없는 날 (한 날이 여러 종류면 앞의 것). 주요 공시는 정기(실적)·주요사항·발행·거래소 공시와 거래소 조치입니다.</li>
          <li>각 소식은 반응일과 그다음 거래일을 함께 셉니다. 반응이 다음 날까지 이어질 수 있어서입니다.</li>
          <li>평소 관계(코스닥·비교 종목과 함께 움직이는 정도)는 <b>소식 없는 날만으로</b> 한 번 추정하고, 모든 날의 예상 대비 차이를 구합니다.</li>
          <li><b>크게 움직인 날</b>의 기준은 소식 없는 날의 차이 크기를 작은 것부터 줄 세웠을 때 상위 10%가 시작되는 값입니다. 그래서 소식 없는 날 줄은 늘 10% 안팎이고, 다른 줄을 이 10%와 견주어 봅니다. 기준값은 이 종목의 평소 출렁임에서 나오므로 자료가 쌓이면 조금씩 바뀝니다.
            <div class="method-now" id="m-cut">지금 기준값을 불러오는 중…</div></li>
          <li>기사 자체의 효과가 아니라 '소식이 있던 날'과 '없던 날'의 차이입니다. 사건(예: 계약) 자체의 영향, 같은 날의 다른 소식, 수집하지 못한 소식이 섞여 있습니다.</li>
        </ul>
        <h3>발표 직후 30분 반응 (기사 상세 맨 위)</h3>
        <ul>
          <li>공개 시각이 확실하고 분봉이 있는 기사에만 나옵니다. 장중(15:00까지) 공개면 공개 시각부터 30분, 장 시작 전·전날 장 마감 뒤 공개면 전날 종가부터 09:30까지입니다.</li>
          <li>나오지 않는 경우: 공개 시각 확인 필요 기사, 시각 미상 공시, 15시 넘어 공개된 기사, 분봉 기록이 없는 옛 기사.</li>
          <li>고유 움직임 = 이 종목 등락 − 시장 몫(코스닥 × 민감도) − 업종 몫. 같은 시간대 업종 분봉이 없으면 시장 몫만 뺍니다.</li>
          <li>비교 대상은 최근 약 45일의 같은 길이 구간(장 시작 전 공개면 같은 시각까지의 움직임)입니다. 짧은 구간이라 다른 요인이 끼어들 틈이 적지만, 장 시작 전 공개는 밤사이 다른 소식이 섞일 수 있습니다.</li>
        </ul>
        <h3>하루 단위 (평소 예상과 비교 · 일별 사건연구)</h3>
        <ul>
          <li>반응일 10거래일 전까지의 120거래일로 평소 관계를 추정합니다. 이 기간에 회사 보도자료·주요 공시가 있던 날은 빼고 그만큼 더 앞의 날로 채웁니다. 그날들의 큰 움직임이 평소 출렁임을 부풀리지 않게 하려는 것입니다.</li>
          <li>코스닥 등락과 비교 종목 평균 등락으로 회귀합니다. 비교 종목 기록이 모자라면 코스닥만 씁니다.</li>
          <li>95% 범위는 t분포로 구하고, 평소 관계를 추정할 때의 오차도 넣습니다. 오차가 서로 독립이고 크기가 일정하다고 가정합니다.</li>
          <li>살펴보는 구간: 공개 전 5일 [−5,−1], 반응일 [0,0], 전후 3일 [−1,+1], 이후 3일 [0,+2], 후속 3일 [+1,+3]. 여러 날은 각 날의 차이를 더한 값이고 실제 투자 수익률과 다릅니다. 여러 구간을 함께 보는 만큼 우연히 튀는 구간이 생길 수 있습니다(보정하지 않음).</li>
          <li>일별 종가 기준이라 장중 기사는 공개 전의 그날 움직임도 포함합니다.</li>
        </ul>
        <h3>몫 나누기 (기사 상세 · 급등·급락 상세 공통)</h3>
        <ul>
          <li>하루 등락 = 시장 몫 + 분야별 몫 + 고유 움직임. 분야는 이 종목의 사업 분야별 비교 종목 묶음(업종 ETF가 설정돼 있으면 함께)입니다.</li>
          <li>민감도는 반응일 5거래일 전까지의 120거래일로 추정합니다(기사·공시 날은 빼고 채움). 분야에서 시장과 겹치는 부분을 먼저 뺍니다.</li>
          <li>큰 사건 며칠에 끌려가지 않도록 추정할 때만 극단값을 누르고(중앙값 ± 4×MAD), 분야 민감도는 기록이 짧거나 연관이 약하면 작게 반영합니다(축소 추정). 평소 추세는 빼지 않습니다.</li>
          <li>평소 고유 변동폭은 중앙값 기반으로 재고, 드문 정도는 실제 기록과 비교합니다. 기록이 40거래일보다 짧으면 시장 1배로 단순 비교합니다.</li>
        </ul>
        <h3>기사 표시</h3>
        <ul>
          <li><b>공개 시각 확인 필요</b>: 구글 뉴스 시각이 16:00·17:00으로 반복 표시되는 기사입니다. 오류가 확정된 것은 아니지만, 원문 시각을 확인하기 전에는 반응일과 숫자를 잠정치로 보세요.</li>
          <li><b>장 외 공개</b>: 장 마감 뒤·휴장일 공개라 다음 거래일 움직임에 그 사이 다른 소식이 섞일 수 있습니다.</li>
          <li><b>오후 늦게 공개</b>(14시 이후): 남은 거래 시간이 짧아 반응이 다음 날까지 이어질 수 있으니 여러 날 구간도 함께 보세요.</li>
          <li><b>시황·특징주</b>: 주가가 움직인 뒤 그 움직임을 다룬 기사라 원인보다 결과에 가깝습니다. <b>단순 언급</b>: 이름만 나오는 기사라 그날 움직임을 기사 효과로 보기 어렵습니다.</li>
          <li><b>거래량 급증</b>: 반응일 거래량이 직전 60거래일 중 상위 5% 안입니다.</li>
        </ul>`)}
      ${sec("events", "급등·급락 (주가 분석)", `
        <h3>감지 기준</h3>
        <ul>
          <li>전일 종가 대비 ±3%는 관찰(알림 없음), ±5%는 급등·급락(알림), ±10%는 대형으로 기록합니다. 장중 30분 안의 빠른 움직임도 따로 잡습니다.</li>
          <li>5거래일 누적 ±10%는 하루하루가 기준 밑이어도 알립니다. 같은 날 같은 방향은 한 번만 알리고, ±3% 안으로 회복했다가 다시 넘으면 한 번 더 알립니다.</li>
        </ul>
        <h3>계산 근거</h3>
        <ul>
          <li><b>몫 나누기</b>: 위 '몫 나누기'와 같은 계산입니다. 업종 자료가 모자라면 시장만 감안한 사건연구로 대신합니다.</li>
          <li><b>팩터 분해</b>: 코스닥과 업종 ETF로 최대 250거래일 회귀합니다. R²는 이 모형이 평소 이 종목 움직임을 설명하는 비율이고, 낮으면(20% 미만) 시장·업종보다 개별 요인으로 움직이는 종목이라는 뜻입니다.</li>
          <li><b>거래 강도</b>: 거래량·거래대금이 20거래일 평균의 몇 배인지(2배 이상이면 많은 매매가 실린 움직임), 같은 거래대금으로 평소보다 얼마나 크게 움직였는지(Amihud 지표, 2.5배 이상이고 거래량이 적으면 호가가 얇아 소수 주문으로 움직였을 수 있음)를 봅니다.</li>
          <li><b>비교 종목 평소 연동성</b>: 과거 약 110~120거래일 동안 코스닥 영향을 뺀 뒤 이 종목과 그 분야가 함께 움직인 정도(상관계수)입니다. 95% 범위의 아래쪽 값이 0보다 클 때만 원인 판단에 씁니다.</li>
        </ul>
        <h3>수급 숫자 읽는 법</h3>
        <ul>
          <li><b>증권사 창구</b>: 매수·매도 각각 상위 5개 증권사만 공개됩니다. '미집계'는 0주가 아니라 5위 밖이라 보이지 않는다는 뜻이고, '5위 밖·멈춤'은 순위에서 밀려난 뒤의 거래가 반영되지 않은 값입니다. 그래서 순매수 앞에 '최소'를 붙입니다. 네이버와 다음 금융 값을 대조해 검증합니다.</li>
          <li><b>투자자별 순매수</b>(외국인·기관·개인): 거래소가 장 마감 후 확정하는 공식 수치입니다. 창구 숫자와 다를 수 있습니다(외국계 창구를 국내 투자자가 쓰기도 함).</li>
        </ul>`)}
      ${sec("limits", "한계", `
        <ul>
          <li>모든 숫자는 시간상·업종 대비 연관성을 보여 줄 뿐, 그 기사·공시 때문에 움직였다고 증명하지 않습니다.</li>
          <li>수집하지 못한 소식(다른 매체, 커뮤니티, 큰손 매매)은 반영되지 않습니다.</li>
          <li>시세는 네이버 증권 화면 자료로 모읍니다. 형식이 바뀌면 일부 수집이 멈출 수 있지만, 이미 모은 기록은 그대로 남습니다.</li>
        </ul>`)}
    </div>`;
    // 지금 쓰고 있는 '크게 움직인 날' 기준값과 그 계산 과정 (자료가 쌓이면 바뀌므로 그때그때 보여 줌)
    api("/api/articles").then((d) => {
      const b = d && d.baseline, box = $("#m-cut");
      if (!box) return;
      if (!b) { box.textContent = "아직 계산할 자료가 부족해 기준값이 없습니다."; return; }
      const q = b.groups.none || {};
      box.innerHTML = `지금 기준값: <b>±${(b.cut * 100).toFixed(1)}%p</b>
        (${esc(b.from)}~${esc(b.to)}의 소식 없는 날 ${q.n || 0}일에 대해 '실제 등락 − 평소 예상'의 크기(오름·내림 구분 없이)를 작은 것부터 줄 세웠을 때,
        위에서 ${Math.round((1 - b.top) * 100)}%가 시작되는 값. 이 기간 소식 없는 날 중 이 기준을 넘은 날은 ${q.big_up + q.big_down}일)`;
    }).catch(() => { const box = $("#m-cut"); if (box) box.textContent = "지금 기준값을 불러오지 못했습니다."; });
    main.querySelector(".method-toc").onclick = (ev) => {
      const a = ev.target.closest("a[data-to]"); if (!a) return;
      ev.preventDefault();
      document.getElementById(a.dataset.to).scrollIntoView({ behavior: "smooth", block: "start" });
    };
  }

  async function pagePR(selId) {
    main.innerHTML = `<div class="wrap">
      <div class="page-head"><div><h1 class="page-title">기사·공시 모음</h1>
        <p class="page-sub" id="art-sub"></p></div></div>
      <div id="pr-score"></div>
      <div class="controls"><div class="seg" id="art-cat" aria-label="기사 구분"></div>
        <input type="search" class="art-search" id="art-q" placeholder="제목·매체·제출인 검색" aria-label="기사·공시 검색" autocomplete="off"><span class="spacer"></span>
        <button class="btn-link editable" type="button" id="pr-toggle" title="자동으로 모이지 않은 보도자료나 정확한 배포 시각을 아는 기사를 직접 넣습니다">기사 직접 등록</button></div>
      <form class="panel form pr-form editable" id="pr-form" autocomplete="off" hidden>
        <p class="hint">네이버·구글 뉴스에서 자동으로 모이지 않은 보도자료나, 정확한 배포 시각을 아는 기사를 등록합니다.</p>
        <div class="pr-form-grid">
          <label class="field"><span>제목</span><input name="title" required placeholder="예: 이 종목, 위성용 광학계 공급 계약"></label>
          <label class="field"><span>최초 공개일</span><input type="date" name="date" value="${isoDate(new Date())}" required></label>
          <label class="field"><span>공개 시각</span><input type="time" name="time" value="${String(new Date().getHours()).padStart(2, "0")}:${String(new Date().getMinutes()).padStart(2, "0")}" required></label>
          <label class="field"><span>기사 주소 (선택)</span><input name="url" type="url" placeholder="https://"></label>
          <button class="btn primary" type="submit">등록하고 분석하기</button>
        </div>
        <div class="form-msg" id="pr-msg" role="status"></div>
      </form>
      <div class="disc-types" id="disc-types" role="group" aria-label="공시 종류" hidden></div>
      <div class="pr-layout art-layout">
        <ul class="art-list" id="art-list"><li class="loading">불러오는 중…</li></ul>
        <section id="pr-detail" class="art-detail"></section>
      </div></div>`;

    $("#pr-toggle").onclick = () => { const f = $("#pr-form"); f.hidden = !f.hidden; };
    $("#pr-form").onsubmit = async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target);
      const msg = $("#pr-msg");
      msg.className = "form-msg"; msg.textContent = "등록하는 중…";
      try {
        const payload = { title: f.get("title"), published_ts: `${f.get("date")}T${f.get("time")}`, pr_type: "직접 등록", url: f.get("url") };
        if (CLOUD) {
          msg.textContent = await sendInbox({ type: "pr", ...payload });
          ev.target.reset();
          return;
        }
        const r = await api("/api/pr", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        msg.textContent = "등록했습니다.";
        ev.target.reset();
        location.hash = `#/pr/pr${r.id}`;
      } catch (e) { msg.className = "form-msg err"; msg.textContent = e.message; }
    };

    let d, dd;
    try { [d, dd] = await Promise.all([api("/api/articles"), api("/api/disclosures").catch(() => null)]); }
    catch (e) { $("#art-list").innerHTML = `<li class="empty"><strong>기사를 불러오지 못했습니다.</strong>${esc(e.message)}</li>`; return; }
    const items = d.items;
    const discs = (dd && dd.items) || [];
    const DTYPES = { ...((dd && dd.types) || {}) };
    discById = new Map(discs.map((x) => [x.id, x]));
    const byId = new Map(items.map((a) => [a.id, a]));
    // 같은 반응일 = 한 사건 (엔진이 묶어 줌. 예전 자료면 기사 하나를 한 사건으로)
    const events = (d.events || items.map((a) => ({ day: a.effect && a.effect.reaction_day, lead: a.id, ids: [a.id],
      kind: a.kind || (a.manual ? "pr" : a.category === "회사 발표" ? "company" : "ext"), outlets: a.outlets_total || 1,
      noise: ["단순 언급", "시황·특징주"].includes(a.category) }))).filter((e) => byId.has(e.lead));
    items.forEach((a) => { if (!a.kind) a.kind = a.manual ? "pr" : a.category === "회사 발표" ? "company" : "ext"; });
    const groups = {
      pr: events.filter((e) => e.kind === "pr" || e.kind === "company"),
      ext: events.filter((e) => e.kind !== "pr" && e.kind !== "company" && !e.noise),
    };
    const LABEL = { pr: "그린광학 보도자료", ext: "외부 기사", disc: "공시" };
    // 공시: 종류(DART 분류 + 거래소 조치) 버튼으로 거름. 처음엔 주가와 관련이 큰 거래소·주요사항·발행 공시와 거래소 조치만
    if (!artState.discTypes) artState.discTypes = new Set(["I", "B", "C", "K"]);
    const discShown = () => discs.filter((x) => artState.discTypes.has(x.pblntf || "?"));
    groups.disc = discShown();

    const renderScore = () => { $("#pr-score").innerHTML = countLine(artState.cat) + baselineBlock(); };
    // 지금 탭에 몇 건이 실렸는지 (같은 반응일 기사는 한 건으로 셈). 공시 탭은 켜 둔 종류만
    const since = isoDate(new Date(Date.now() - 183 * 864e5));
    function countLine(cat) {
      const isDisc = cat === "disc";
      const list = isDisc ? groups.disc : groups[cat];
      const dayOf = (x) => (isDisc ? x.ts : x.day || (byId.get(x.lead) || {}).ts || "").slice(0, 10);
      const recent = list.filter((x) => dayOf(x) >= since);
      const first = list.map(dayOf).filter(Boolean).sort()[0];
      const label = isDisc ? "선택한 종류 공시" : cat === "pr" ? "그린광학 보도자료" : "외부 기사";
      const avgOut = recent.length ? recent.reduce((sum, x) => sum + (x.outlets || 1), 0) / recent.length : null;
      return `<div class="score-line">
        <span><small>최근 6개월 ${label}</small><b>${recent.length}건</b></span>
        <span><small>한 달 평균</small><b>${(recent.length / 6).toFixed(1)}건</b></span>
        ${isDisc ? `<span><small>시각을 아는 공시 (6개월)</small><b>${recent.filter((x) => !x.untimed).length}건</b></span>`
          : `<span><small>한 건당 실린 매체 (6개월 평균)</small><b>${avgOut != null ? avgOut.toFixed(1) + "곳" : "-"}</b></span>`}
        <span><small>전체${first ? ` (${esc(first)}부터)` : ""}</small><b>${list.length}건</b></span></div>`;
    }
    // 소식 없는 날과 비교: 기사 하나는 잡음이 커도, 같은 종류 날을 모으면 차이가 보임
    function baselineBlock() {
      const b = d.baseline;
      if (!b) return "";
      const NAMES = { pr: "그린광학 보도자료 날", disc: "주요 공시 날", ext: "외부 기사만 있는 날", none: "소식 없는 날 (기준)" };
      const rows = ["pr", "disc", "ext", "none"].map((k) => {
        const g = b.groups[k] || {};
        return `<tr${k === "none" ? ' class="muted"' : ""}><td>${NAMES[k]}</td><td class="num">${g.n || 0}일</td>
          <td class="num">${g.big_share != null ? `<b>${Math.round(g.big_share * 100)}%</b> <small class="muted">${g.n}일 중 ${g.big_up + g.big_down}일 (오름 ${g.big_up} · 내림 ${g.big_down})</small>` : "-"}</td>
          <td class="num">${g.median_abs != null ? (g.median_abs * 100).toFixed(2) + "%p" : "-"}</td></tr>`;
      }).join("");
      return `<section class="baseline"><h3 class="baseline-title">소식 없는 날과 비교 <small class="muted">${esc(b.from)} ~ ${esc(b.to)}</small></h3>
        <table class="ladder"><thead><tr><th>날 종류</th><th>날 수</th><th>크게 움직인 날 비율 <small class="muted">(예상과 ±${(b.cut * 100).toFixed(1)}%p 넘게 차이)</small></th><th>평소 예상과 차이 크기 (중앙값)</th></tr></thead><tbody>${rows}</tbody></table></section>`;
    }

    if (!["pr", "ext", "disc"].includes(artState.cat)) artState.cat = "pr";
    // 검색: 지금 탭 목록을 제목·매체(기사) / 제목·제출인·종류(공시)로 거름. 같은 날 묶인 기사 중 하나라도 맞으면 그 사건을 보여줌
    const norm = (t) => String(t || "").toLowerCase().replace(/\s+/g, "");
    const hit = (...xs) => { const q = norm(artState.q); return !q || xs.some((x) => norm(x).includes(q)); };
    const shown = (cat) => cat === "disc" ? groups.disc.filter((x) => hit(x.title, x.filer, x.kind))
      : groups[cat].filter((e) => e.ids.some((id) => { const a = byId.get(id); return a && hit(a.title, a.office, a.category); }));
    const qBox = $("#art-q");
    qBox.value = artState.q || "";
    qBox.oninput = () => { artState.q = qBox.value; renderList(); followTab(); };
    const seg = $("#art-cat");
    seg.innerHTML = Object.keys(LABEL).map((v) => `<button type="button" data-v="${v}" aria-pressed="${artState.cat === v}">${LABEL[v]} <small></small></button>`).join("");
    seg.onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; artState.cat = b.dataset.v; renderList(); followTab(); };
    const typeBox = $("#disc-types");
    const typeCount = (t) => discs.filter((x) => (x.pblntf || "?") === t).length;
    const typeKeys = Object.keys(DTYPES).filter((t) => typeCount(t));
    if (discs.some((x) => !x.pblntf)) { typeKeys.push("?"); DTYPES["?"] = "분류 없음"; }
    function renderTypes() {
      const allOn = typeKeys.every((t) => artState.discTypes.has(t));
      typeBox.innerHTML = `<button type="button" data-t="*" aria-pressed="${allOn}">전체 <small>${discs.length}</small></button>`
        + typeKeys.map((t) => `<button type="button" data-t="${t}" aria-pressed="${artState.discTypes.has(t)}">${esc(DTYPES[t])} <small>${typeCount(t)}</small></button>`).join("");
    }
    typeBox.onclick = (ev) => {
      const b = ev.target.closest("button[data-t]"); if (!b) return;
      const t = b.dataset.t, S = artState.discTypes;
      if (t === "*") { if (typeKeys.every((k) => S.has(k))) S.clear(); else typeKeys.forEach((k) => S.add(k)); }
      else if (S.has(t)) S.delete(t); else S.add(t);
      groups.disc = discShown();
      renderList();
      followTab();
    };
    // 탭·종류를 바꾸면 오른쪽 상세도 그 목록의 맨 위 항목으로 (지금 보던 항목이 목록에 있으면 그대로)
    function followTab() {
      const list = shown(artState.cat);
      const ids = artState.cat === "disc" ? list.map((x) => x.id) : list.flatMap((e) => e.ids);
      if (current && ids.includes(current)) return;
      current = ids.length ? (artState.cat === "disc" ? ids[0] : list[0].lead) : null;
      history.replaceState(null, "", current ? `#/pr/${current}` : "#/pr");
      renderList();
      if (current) loadPR(current);
      else $("#pr-detail").innerHTML = `<div class="empty"><strong>${artState.q ? "검색에 맞는 항목이 없습니다." : artState.cat === "disc" ? "보여줄 공시가 없습니다." : "보여줄 기사가 없습니다."}</strong></div>`;
    }

    function renderList() {
      seg.querySelectorAll("button").forEach((b) => {
        b.setAttribute("aria-pressed", String(b.dataset.v === artState.cat));
        // 탭 숫자: 검색어가 있으면 맞는 수 (공시는 종류 버튼과 관계없이 전체 공시 중)
        b.querySelector("small").textContent = b.dataset.v === "disc"
          ? (artState.q ? discs.filter((x) => hit(x.title, x.filer, x.kind)).length : discs.length) : shown(b.dataset.v).length;
      });
      typeBox.hidden = artState.cat !== "disc";
      if (artState.cat === "disc") renderTypes();
      renderScore();
      const ul = $("#art-list");
      if (artState.cat === "disc") {
        const list = shown("disc");
        if (!list.length) {
          ul.innerHTML = artState.q && groups.disc.length ? `<li class="empty"><strong>검색에 맞는 공시가 없습니다.</strong>다른 검색어를 넣거나 위 종류 버튼을 더 켜 보세요.</li>`
            : !dd ? `<li class="empty"><strong>공시 자료를 불러오지 못했습니다.</strong>사이트에 아직 올라오지 않았으면 다음 자동 수집 때 반영됩니다.</li>`
            : `<li class="empty"><strong>${discs.length ? "선택한 종류의 공시가 없습니다." : "아직 모인 공시가 없습니다."}</strong>${discs.length ? "위 종류 버튼을 눌러 다른 공시도 볼 수 있습니다." : "자동 수집이 돌면 DART에서 이 회사 공시를 모읍니다."}</li>`;
          return;
        }
        ul.innerHTML = list.map((x) => {
          const ef = x.effect || {}, m = ef.measure;
          return `<li><button type="button" data-id="${esc(x.id)}" aria-current="${x.id === current}">
          <span class="al-meta"><span class="ktag k-disc">공시</span>${catTag(x.kind || "공시")}<span class="muted">${dayLabel(x.ts)} ${x.untimed ? "시각 미상" : hhmm(x.ts)}</span>${artFlags(ef)}</span>
          <span class="t">${esc(x.title)}</span>
          <span class="al-foot">${verdictTag(ef)}${measureList(m)}${m && m.vol_ratio ? `<span class="muted">거래량 ${m.vol_ratio.toFixed(1)}배</span>` : ""}<span class="muted">${esc(x.filer || "")}</span>${(x.links || []).length ? `<span class="muted">같은 내용 기사 ${x.links.length}건</span>` : ""}</span></button></li>`;
        }).join("");
        ul.onclick = (ev) => { const b = ev.target.closest("button[data-id]"); if (b) location.hash = `#/pr/${b.dataset.id}`; };
        return;
      }
      const list = shown(artState.cat);
      if (!list.length) {
        ul.innerHTML = `<li class="empty"><strong>${artState.q ? "검색에 맞는 기사가 없습니다." : "해당 기사가 없습니다."}</strong></li>`;
        return;
      }
      const sub = (a) => `<button type="button" data-id="${esc(a.id)}" aria-current="${a.id === current}" class="sub">
        <span class="al-meta">${catTag(a.category)}<span class="muted">${dayLabel(a.ts)} ${hhmm(a.ts)} · ${esc(a.office)}</span></span>
        <span class="t">${esc(a.title)}</span></button>`;
      ul.innerHTML = list.map((e) => {
        const a = byId.get(e.lead), ef = a.effect || {}, m = ef.measure;
        const others = e.ids.filter((x) => x !== e.lead && byId.has(x)).map((x) => byId.get(x));
        return `<li><button type="button" data-id="${esc(a.id)}" aria-current="${a.id === current}">
          <span class="al-meta">${kindTag(a)}${catTag(a.category)}${toneTag(a.tone)}<span class="muted">${dayLabel(a.ts)} ${hhmm(a.ts)}</span>${artFlags(ef, a, e)}</span>
          <span class="t">${esc(a.title)}</span>
          <span class="al-foot">${verdictTag(ef)}${measureList(m)}${m && m.vol_ratio ? `<span class="muted">거래량 ${m.vol_ratio.toFixed(1)}배</span>` : ""}<span class="muted">${e.outlets > 1 ? `${e.outlets}개 매체` : esc(a.office)}</span></span></button>
          ${others.length ? `<details class="art-group"${others.some((x) => x.id === current) ? " open" : ""}><summary>같은 날 기사 ${others.length}건 더</summary>${others.map(sub).join("")}</details>` : ""}</li>`;
      }).join("");
      ul.onclick = (ev) => { const b = ev.target.closest("button[data-id]"); if (b) location.hash = `#/pr/${b.dataset.id}`; };
    }
    let current = selId;
    if (selId && discById.has(selId)) {
      artState.cat = "disc";
      const x = discById.get(selId);
      if (!artState.discTypes.has(x.pblntf || "?")) { artState.discTypes.add(x.pblntf || "?"); groups.disc = discShown(); }
    } else if (selId) {
      const ev = events.find((e) => e.ids.includes(selId));
      if (ev) artState.cat = ev.kind === "pr" || ev.kind === "company" ? "pr" : "ext";
    }
    if (!current) { const first = shown(artState.cat)[0] || events[0]; current = first && (first.lead || first.id); }
    $("#art-sub").innerHTML = items.length ? ""
      : "아직 모인 기사가 없습니다. 자동 수집이 돌면 네이버·구글 뉴스에서 이 종목 기사를 모읍니다.";
    renderList();
    if (CLOUD) {
      const displayedPublication = LIVE && LIVE.published_at;
      let checking = false;
      timers.push(setInterval(async () => {
        if (checking || document.hidden) return;
        checking = true;
        const activeRoute = location.hash;
        try {
          const next = await cloudLive(true);
          if (activeRoute !== location.hash || !$("#pr-detail") || next.published_at === displayedPublication) return;
          if (main.querySelector("input:focus, textarea:focus, #pr-form:not([hidden])")) return;
          const scrollPosition = window.scrollY;
          cleanupPage();
          await pagePR(current);
          window.scrollTo(0, scrollPosition);
        } catch (_) {
        } finally { checking = false; }
      }, 30000));
    }
    if (!current) { $("#pr-detail").innerHTML = `<div class="empty"><strong>아직 기사가 없습니다.</strong>자동 수집이 돌면 몇 분 안에 모입니다.</div>`; return; }
    loadPR(current);
  }

  let discById = new Map();     // 기사·공시 모음 화면이 받아 둔 공시 (기사의 '공시 먼저'를 그 공시로 잇는 데 씀)
  const kindTag = (a) => `<span class="ktag ${a.kind === "pr" ? "k-pr" : a.kind === "disc" ? "k-disc" : "k-ext"}">${a.kind === "pr" ? "확인된 자체 보도자료" : a.kind === "company" ? "회사 관련 보도" : a.kind === "disc" ? "공시" : "외부 기사"}</span>${a.vol_rank != null && a.vol_rank <= 0.05 ? '<span class="tier watch" title="반응일 거래량이 직전 60거래일 중 상위 5% 안">거래량 급증</span>' : ""}`;
  const artFlags = (ef, row = null, event = null) => `${ef && ef.off_session ? '<span class="tier watch" title="장 마감 뒤·휴장일 공개라 다음 거래일 움직임에 다른 요인이 섞일 수 있음">장 외 공개</span>' : ""}${ef && ef.disclosure ? '<span class="tier watch" title="같은 내용의 공시가 먼저 나와 공시 시각부터 쟀습니다">공시 먼저</span>' : ""}${row && row.time_quality === "check_time" || event && event.time_check ? '<span class="tier watch" title="구글 뉴스 게시 시각이 반복되는 시간대입니다. 오류가 확정된 것은 아니며, 원문 시각 확인이 필요합니다">공개 시각 확인 필요</span>' : ""}${event && event.followup_of ? '<span class="tier watch">기존 정보 재보도</span>' : ""}${event && event.followup_candidate_of ? '<span class="tier watch">후속 보도 검토</span>' : ""}`;

  function decompBlock(dc, tm, d0, two, noun = "기사") {
    const p = (two && dc.d01) || dc.d0, sig = dc.sigma;
    const parts = p.parts || [];
    const rows = [
      { k: "이 종목 실제 변동", v: p.own, cls: "me" },
      { k: `시장 몫 <small>KOSDAQ ${pct(p.kosdaq)} × 민감도 ${dc.beta_market.toFixed(2)}</small>`, v: p.market },
      ...(parts.length ? parts.map((f) => ({ k: `분야 몫 · ${esc(f.name)} <small>${f.kind === "etf" ? "ETF" : "비교 종목 평균"} ${pct(f.factor)} 중 시장과 무관한 부분 ${pct(f.factor_resid)} × 민감도 ${f.beta.toFixed(2)}</small>`, v: f.contrib }))
        : [{ k: `업종 몫 <small>${dc.method === "단순" ? "기록이 짧아 시장만 감안" : "비교 종목 시세 없음"}</small>`, v: p.sector }]),
      { k: `설명되지 않은 고유 움직임 <small>= 실제 − 시장 몫 − 분야 몫</small>`, v: p.abnormal, cls: "abn" },
    ];
    const max = Math.max(0.005, ...rows.map((r) => Math.abs(r.v || 0)));
    const zTxt = p.z != null && sig ? `평소 이 종목 혼자 움직이는 폭(하루 ±${(sig * 100).toFixed(1)}%)의 ${Math.abs(p.z).toFixed(1)}배` : "";
    const judge = p.rarity != null ? `${rarityTxt(p.rarity, p.n_null, two && dc.d01 ? "2일 고유 움직임" : "하루 고유 움직임")}.` : "";
    return `<h3 class="d-sec" style="margin-top:22px">공개 무렵 주가 움직임 나누기 <small class="muted">${two && dc.d01 ? `반응일 ${esc(d0)}+다음날 2일 누적` : `반응일 ${esc(d0)}`}</small></h3>
      <ul class="rel decomp">${rows.map((r) => `<li class="${r.cls || ""}"><span class="nm">${r.k}</span>
        <span class="dbar"><i class="${(r.v || 0) >= 0 ? "b" : "s"}" style="${(r.v || 0) >= 0 ? "left:50%" : `left:${50 - (Math.abs(r.v) / max) * 50}%`};width:${(Math.abs(r.v || 0) / max) * 50}%"></i></span>
        <span class="num v ${sgnCls(r.v)}">${pct(r.v)}</span></li>`).join("")}</ul>
      <p class="flow-sum" style="margin-top:10px">${zTxt ? `고유 움직임은 ${zTxt}입니다. ` : ""}${judge}</p>
      ${tm ? `<p class="flow-sum"><b>시간 순서</b> ${esc(tm.label)}</p>` : ""}
      <p class="note" style="margin-top:6px">${dc.method !== "단순" ? `민감도 추정: ${esc(dc.window[0])}~${esc(dc.window[1])}, ${dc.n_obs}거래일` : "기록이 짧아 시장 1배로 단순 비교"}</p>`;
  }

  function eventStudyBlock(study) {
    if (!study) return "";
    if (!study.ok) return `<h3 class="d-sec">평소 예상과 비교</h3><p class="note">${esc(study.reason)}</p>`;
    const points = (value) => `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%p`;
    const rows = (study.car || []).map((item) => `<tr><td>${esc(item.window)}</td>
      <td class="num">${points(item.car)}</td><td class="num">${points(item.ci_low)} ~ ${points(item.ci_high)}</td></tr>`).join("");
    return `<section class="evidence"><h3 class="evidence-title">평소 예상과 비교 — 일별 이벤트 연구</h3>
      <p>반응일 실제 주가는 <b>${pct(study.actual)}</b>, 평소 시장${study.beta_sector != null ? "·비교 종목" : ""}과의 관계로 예상한 움직임은 <b>${pct(study.expected)}</b>입니다.
      차이는 <b>${points(study.ar)}</b>입니다.</p>
      <table class="ladder"><thead><tr><th>살펴본 기간</th><th>예상보다 더 움직인 부분</th><th>95% 불확실성 범위</th></tr></thead><tbody>${rows}</tbody></table>
      ${study.pre_drift ? `<p class="note">공개 전 5일에도 같은 방향으로 이례적으로 움직였습니다.</p>` : ""}
      ${study.pending_windows.length ? `<p class="note">시세가 부족하거나 아직 지나지 않아 계산하지 않은 구간: ${study.pending_windows.map(esc).join(", ")}.</p>` : ""}
      <p class="note">추정: ${esc(study.method)} · ${esc(study.est_from)}~${esc(study.est_to)}, ${study.n_est}거래일 (반응일 직전 ${study.gap}거래일${study.excluded_news_days ? `, 보도자료·주요 공시 날 ${study.excluded_news_days}일` : ""} 제외)</p></section>`;
  }

  async function loadPR(id) {
    const box = $("#pr-detail");
    box.innerHTML = `<p class="loading">계산하는 중…</p>`;
    let d;
    const isDisc = /^dK?\d+$/.test(id);
    try { d = await api(isDisc ? `/api/disclosures/${id}` : `/api/articles/${id}`); }
    catch (e) { box.innerHTML = `<div class="empty"><strong>불러오지 못했습니다.</strong>${esc(e.message)}</div>`; return; }
    const a = d.article, e = d.effect, m = e.minute, dl = e.daily;
    if (!a.kind) a.kind = a.manual ? "pr" : a.category === "회사 발표" ? "company" : "ext";
    const noun = isDisc ? "공시" : "기사";
    // 결론 먼저: 판정 · 고유 반응 · 실린 매체 · 장 외/공시 먼저
    let html = `<div class="al-meta">${kindTag(a)}${catTag(a.category)}${toneTag(a.tone)}${artFlags(e)}</div>
      <h2 class="pr-title">${esc(a.title)}</h2>
      <p class="pr-meta">${isDisc ? `${a.office ? `${esc(a.office)} 제출 · ` : ""}${dayLabel(a.ts)} ${a.untimed ? "접수 (시각 미상)" : `${hhmm(a.ts)} 접수`}${a.url ? ` · <a href="${esc(a.url)}" target="_blank" rel="noopener">${a.pblntf === "K" ? "KIND에서 원문 보기" : "DART에서 원문 보기"}</a>` : ""}`
        : `${esc(a.office)} · ${dayLabel(a.ts)} ${hhmm(a.ts)} 공개${a.outlets_total > 1 ? ` · 같은 내용 ${a.outlets_total}개 매체` : ""}${a.url ? ` · <a href="${esc(a.url)}" target="_blank" rel="noopener">기사 열기</a>` : ""}`}</p>`;
    const notes = [];
    if (e.disclosure) {
      const dsc = [...discById.values()].find((x) => x.ts === e.disclosure.ts && x.title === e.disclosure.title);
      notes.push(`같은 내용의 공시(${dsc ? `<a href="#/pr/${esc(dsc.id)}">「${esc(e.disclosure.title)}」</a>` : `「${esc(e.disclosure.title)}」`})가 ${dayLabel(e.disclosure.ts)} ${hhmm(e.disclosure.ts)}에 먼저 나와, 공시 시각부터 쟀습니다.`);
    }
    if (isDisc && a.untimed) notes.push("접수 시각 미상 → 그날 하루(전일 종가 → 종가)로 계산");
    if (e.off_session) notes.push(`장 마감 뒤·휴장일 공개 → 반응일 ${e.reaction_day ? esc(e.reaction_day) : "다음 거래일"}`);
    if (e.late && !d.intraday) notes.push("오후 늦게(14시 이후) 공개");
    if (a.time_quality === "check_time" || e.time_quality === "check_time") notes.push("공개 시각 확인 필요: 구글 뉴스 시각이 16:00 또는 17:00으로 표시됨");
    // 공개 시각이 확실하고 분봉이 있으면 발표 직후 30분을 대표로 (다른 요인이 끼어들 틈이 적음), 하루 숫자는 그 아래
    const iw = d.intraday;
    if (iw) {
      const span = iw.kind === "open" ? `전날 종가 → ${esc(e.reaction_day)} ${hhmm(iw.end)} <small class="muted">(장 시작 전 공개라 처음 거래된 ${iw.minutes}분)</small>`
        : `${hhmm(iw.start)} → ${hhmm(iw.end)} <small class="muted">(공개 직후 ${iw.minutes}분)</small>`;
      html += `<div class="verdict-box intraday-box"><div class="vb-head">발표 직후 ${iw.minutes}분 반응</div>
        <div class="vb-span">${span}</div>
        <div class="iw-nums"><span><small>이 종목</small><b class="${sgnCls(iw.own)}">${pct(iw.own)}</b></span>
          <span><small>시장·업종 몫</small><b>${points(iw.market + iw.sector)}</b></span>
          <span><small>고유 움직임</small><b class="${sgnCls(iw.abnormal)}">${points(iw.abnormal)}</b></span>
          <span><small>평소 같은 구간 중 이보다 크게 움직인 비율</small><b>${iw.rarity < 0.01 ? "1% 미만" : `${Math.round(iw.rarity * 100)}%`}</b></span></div>
        <div class="vb-note">비교 대상: 최근 ${iw.kind === "open" ? "같은 시각(전날 종가 → 09:30) 움직임" : `${iw.minutes}분 구간`} ${iw.n_null}개${iw.segments_used ? "" : " · 업종 분봉 없음(시장 몫만 뺌)"}</div></div>
        <h3 class="d-sec" style="margin-top:18px">하루 전체 <small class="muted">반응일 종가 기준</small></h3>`;
    }
    html += `<div class="verdict-box v-${e.verdict_cls}">${e.verdict ? `<div class="vb-head">${esc(e.verdict)}</div>` : ""}
      <div>${e.measure ? measureTxt(e.measure) : (e.reaction_day ? "" : "기사가 나온 무렵의 시세 기록이 없어 계산하지 못했습니다.")}</div>
      ${dl && dl.vol_rank != null ? `<div class="vb-note">반응일 거래량 ${int(dl.volume)}주 — 직전 60거래일 중 이만큼 이상 거래된 날 ${dl.vol_rank < 0.01 ? "1% 미만" : `${(dl.vol_rank * 100).toFixed(0)}%`}${dl.vol_ratio ? ` (평소의 ${dl.vol_ratio.toFixed(1)}배)` : ""}</div>` : ""}
      ${notes.map((x) => `<div class="vb-note">${x}</div>`).join("")}</div>`;
    // 구분·악재 표시 고치기 (직접 등록한 기사는 늘 그린광학 보도자료)
    if (!a.manual && !isDisc) {
      const toKind = a.kind === "pr" ? "ext" : "pr";
      html += `<div class="ask-row editable art-fix">
        <button class="btn small" type="button" data-kind="${toKind}">${toKind === "pr" ? "그린광학 보도자료로 바꾸기" : "외부 기사로 바꾸기"}</button>
        ${a.kind === "ext" ? `<button class="btn small" type="button" data-tone="${a.tone < 0 ? 0 : -1}">${a.tone < 0 ? "악재 표시 지우기" : "악재로 표시"}</button>` : ""}
        ${a.kind !== a.kind_auto && a.kind_auto ? `<button class="btn-link" type="button" data-kind="auto">자동 판정으로 되돌리기</button>` : ""}
        <span class="form-msg" id="art-fix-msg"></span></div>`;
    }

    let ev = eventStudyBlock(e.event_study);
    if (dl) {
      const row = (k, label) => `<tr><td class="h">${label}</td><td class="v num ${sgnCls(dl[k])}">${pct(dl[k])}</td><td class="v num">${pct((dl.peers || {})[k])}</td><td class="v num">${pct((dl.kosdaq || {})[k])}</td><td class="v num ${sgnCls((dl.excess || {})[k])}"><b>${pct((dl.excess || {})[k])}</b></td></tr>`;
      ev += `<h3 class="d-sec" style="margin-top:22px">일별 반응 <small class="muted">반응일 ${esc(e.reaction_day)} · 전날 종가 ${won(dl.prev_close)}원 기준</small></h3>
        <table class="ladder"><thead><tr><th></th><th style="text-align:right">이 종목</th><th style="text-align:right">같은 업종</th><th style="text-align:right">KOSDAQ</th><th style="text-align:right">초과 반응</th></tr></thead><tbody>
        ${row("d0", "반응일")}${e.late ? row("d1", "다음날까지") : ""}</tbody></table>
        <p class="note" style="margin-top:4px">초과 반응은 ${esc(dl.bench_name)} 대비입니다. 반응일 거래량 ${int(dl.volume)}주${dl.vol_ratio ? `, 직전 20일 평균의 ${dl.vol_ratio.toFixed(1)}배` : ""}${dl.gap != null ? `, 시초가 갭 ${pct(dl.gap)}` : ""}.</p>`;
    }
    if (m) {
      const hs = ["5", "15", "30", "60", "120"].filter((h) => m.returns[h] != null);
      const bench = Object.keys(m.peers || {}).length ? m.peers : m.kosdaq;
      ev += `<h3 class="d-sec" style="margin-top:22px">공개 직후 반응 <small class="muted">참고용 · ${m.outside_session ? `장 외 공개 → ${hhmm(m.reaction_start)} 장 시작부터` : `${hhmm(m.reaction_start)}부터`}</small></h3>
        <table class="ladder"><thead><tr><th></th><th style="text-align:right">이 종목</th><th style="text-align:right">${esc(m.bench_name)}</th><th style="text-align:right">초과 반응</th></tr></thead><tbody>
        ${hs.map((h) => `<tr><td class="h">+${h}분</td><td class="v num ${sgnCls(m.returns[h])}">${pct(m.returns[h])}</td><td class="v num">${pct(bench[h])}</td><td class="v num ${sgnCls(m.excess[h])}"><b>${pct(m.excess[h])}</b></td></tr>`).join("")}
        </tbody></table>
        <p class="note" style="margin-top:4px">120분 안 최고 ${pct(m.max_up)}(${hhmm(m.max_up_at)}), 최저 ${pct(m.max_down)}(${hhmm(m.max_down_at)})${m.vol_after30 != null ? `. 공개 후 30분 거래량 ${int(m.vol_after30)}주${m.vol_usual30 ? `(평소 ${int(m.vol_usual30)}주)` : ""}` : ""}.</p>`;
    }
    ev += `<div class="chart-legend" id="pr-legend" style="margin-top:14px"></div><div class="chart-box" id="pr-chart"></div>`;
    if ((d.confounders || []).length) ev += `<h3 class="d-sec" style="margin-top:18px">함께 봐야 할 것</h3><ul class="notes" style="margin-top:0">${d.confounders.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    if ((e.events || []).length) ev += `<div class="rule-full"><b>반응일에 감지된 급등·급락</b><br>${e.events.map((x) => `<a href="#/events" data-ev="${x.id}">${hhmm(x.start_ts)} ${KIND[x.direction]} ${pct(x.peak_return_5m)}</a>`).join(", ")}</div>`;

    // 공시: 같은 내용이라 이 공시 시각부터 잰 기사들
    if (isDisc) {
      const ls = a.links || [];
      html += `<section class="outlet-list"><h3 class="d-sec">같은 내용의 보도자료·기사 <small class="muted">${ls.length}건</small></h3>
        ${ls.length ? `<ul>${ls.map((r) => `<li><span class="muted">${dayLabel(r.ts)} ${hhmm(r.ts)} · ${esc(r.office || "-")}</span> <a href="#/pr/${esc(r.id)}">${esc(r.title)}</a>${r.kind === "pr" ? ' <span class="ktag k-pr">그린광학 보도자료</span>' : ""}</li>`).join("")}</ul>`
          : `<p class="note">이 공시 뒤 36시간 안에 같은 내용(계약·실적·IR·증자·전환사채)으로 나온 기사가 없습니다.</p>`}</section>`;
      html += `<section class="evidence"><h3 class="evidence-title">근거 자세히 <span class="muted">몫 나누기 · 일별·분별 반응 · 차트</span></h3>${ev}</section>`;
    }
    // 이 기사를 실은 매체 전부 (링크), 링크를 모으지 못한 매체는 네이버 뉴스 검색으로
    const outlets = [{ id: a.id, manual: a.manual, ts: a.ts, office: a.office, title: a.title, url: a.url }, ...(a.related_titles || [])];
    const missing = Math.max(0, (a.outlets_total || 1) - outlets.length);
    // 같은 기사가 두 번 들어온 경우(직접 등록 + 자동 수집 등) 한쪽을 지울 수 있게: 직접 등록은 삭제, 자동 수집은 이 줄만 숨김
    const link = (r) => `<li><span class="muted">${dayLabel(r.ts)} ${hhmm(r.ts)} · ${esc(r.office || "-")}</span> ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>` : esc(r.title)}${outlets.length > 1 && r.id ? ` <button type="button" class="btn-link editable dup-del" data-dup="${esc(r.id)}" data-manual="${r.manual ? 1 : 0}">중복이라 삭제</button>` : ""}</li>`;
    if (!isDisc) html += `<section class="outlet-list"><h3 class="d-sec">이 기사를 실은 매체 <small class="muted">${a.outlets_total || outlets.length}곳</small></h3>
      <ul>${outlets.map(link).join("")}</ul>
      <span class="form-msg" id="dup-msg"></span>
      ${missing ? `<p class="note">나머지 ${missing}곳은 네이버가 묶음으로만 알려줘 개별 링크가 없습니다 → <a href="https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(`"${a.title.slice(0, 40)}"`)}" target="_blank" rel="noopener">네이버 뉴스에서 같은 제목 찾기</a></p>` : ""}
</section>`;
    if (!isDisc) {
      html += `<section class="evidence"><h3 class="evidence-title">근거 자세히 <span class="muted">몫 나누기 · 일별·분별 반응 · 차트</span></h3>${ev}</section>`;
      html += `<p class="editable" style="margin-top:18px">${a.manual ? `<button class="btn danger" type="button" id="pr-del">이 기사 삭제</button>` : `<button class="btn" type="button" id="art-hide">${a.hidden ? "목록에 다시 표시" : "관계없는 기사라 목록에서 빼기"}</button>`}</p>`;
    }
    box.innerHTML = html;

    const draw = () => {
      if (m && d.bars && d.bars.length) {
        const kqBase = d.kosdaq.length ? (d.kosdaq.filter((b) => b.t < m.reaction_start).slice(-1)[0] || d.kosdaq[0]).c : null;
        indexedChart($("#pr-chart"), $("#pr-legend"), d.bars, d.kosdaq, m.base_price, kqBase, m.reaction_start, m.outside_session ? "장 시작" : "공개");
      } else if ((d.daily_bars || []).length && e.reaction_day) {
        const own = d.daily_bars.map((r) => ({ t: r.date, c: r.close }));
        const kq = (d.daily_kosdaq || []).map((r) => ({ t: r.date, c: r.close }));
        const prev = d.daily_bars.filter((r) => r.date < e.reaction_day).slice(-1)[0];
        const kprev = (d.daily_kosdaq || []).filter((r) => r.date < e.reaction_day).slice(-1)[0];
        if (prev) indexedChart($("#pr-chart"), $("#pr-legend"), own, kq, prev.close, kprev ? kprev.close : null, e.reaction_day, "반응일", true);
      } else {
        $("#pr-chart").outerHTML = `<p class="note">차트를 그릴 시세 기록이 없습니다.</p>`;
      }
    };
    draw();

    box.querySelectorAll("[data-ev]").forEach((x) => x.addEventListener("click", (ev2) => { ev2.preventDefault(); openEvent(+x.dataset.ev); }));
    box.querySelectorAll(".dup-del").forEach((b) => b.addEventListener("click", async () => {
      const msg = $("#dup-msg");
      if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = "한 번 더 누르면 삭제"; return; }
      const item = b.dataset.manual === "1" ? { type: "delete_pr", id: b.dataset.dup, title: a.title }
        : { type: "hide_one", id: b.dataset.dup, title: a.title };
      try { msg.className = "form-msg"; msg.textContent = await sendInbox(item); b.disabled = true; b.textContent = "삭제 요청함"; }
      catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
    }));
    const queued = (btn, text) => { btn.disabled = true; btn.textContent = STATIC ? "GitHub 창에서 [Create]를 누르면 반영됩니다" : "요청을 보냈습니다 (다음 자동 수집 때 반영)"; btn.title = text || ""; };
    box.querySelectorAll(".art-fix [data-kind], .art-fix [data-tone]").forEach((b) => b.addEventListener("click", async () => {
      const msg = $("#art-fix-msg");
      const item = b.dataset.kind ? { type: "article_kind", id: a.id, kind: b.dataset.kind, title: a.title }
        : { type: "article_tone", id: a.id, tone: +b.dataset.tone, title: a.title };
      try { msg.className = "form-msg"; msg.textContent = await sendInbox(item); b.disabled = true; }
      catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
    }));
    const del = $("#pr-del");
    if (del) del.onclick = async () => {
      if (del.dataset.confirm !== "1") { del.dataset.confirm = "1"; del.textContent = "한 번 더 누르면 삭제됩니다"; return; }
      if (CLOUD) { return queued(del, await sendInbox({ type: "delete_pr", id: a.id, title: a.title })); }
      await api(`/api/pr/${a.id.slice(2)}`, { method: "DELETE" });
      location.hash = "#/pr";
    };
    const hide = $("#art-hide");
    if (hide) hide.onclick = async () => {
      if (CLOUD) { return queued(hide, await sendInbox({ type: a.hidden ? "show" : "hide", id: a.id, title: a.title })); }
      await api(`/api/articles/${a.id}/${a.hidden ? "show" : "hide"}`, { method: "POST" });
      location.hash = "#/pr";
    };
  }

  // ---------------------------------------------------------------- share
  let shareTimer = null;
  function renderShareBtn(st) {
    const b = $("#share-btn");
    if (!b) return;
    const c = STATUS && STATUS.cloud;
    b.dataset.on = (c && c.connected) || st.status === "running" ? "1" : "";
    $("#share-btn-label").textContent = c && c.connected ? "어디서나 보기 켜짐" : st.status === "running" ? "임시 공유 중" : "어디서나 보기";
  }
  function shareBody(st) {
    const warn = `<ul class="share-notes"><li>링크를 받은 사람은 <b>보기만</b> 할 수 있습니다. 기사 등록·삭제, 다시 분석, 공유 설정은 이 PC에서만 됩니다.</li>
      <li>이 PC와 프로그램이 켜져 있는 동안만 열립니다. 공유를 중지하면 링크는 바로 끊기고, 다시 만들면 새 주소가 생깁니다.</li>
      <li>링크를 아는 사람은 누구나 볼 수 있으니 회사 안 필요한 분께만 보내 주세요. 네이버·다음 시세를 다시 보여주는 화면이라 외부 공개용으로 쓰지 않는 것이 좋습니다.</li>
      <li>Cloudflare의 무료 임시 연결(가입·결제 없음)을 씁니다. 처음 한 번 공식 배포처에서 연결 프로그램(cloudflared)을 받습니다.</li></ul>`;
    if (st.status === "running") {
      return `<p class="share-ok">지금 공유 중입니다.${st.started_at ? ` (${esc(st.started_at.slice(5).replace("T", " "))}부터)` : ""}</p>
        <div class="share-link"><input id="share-link" readonly value="${esc(st.link || "")}"><button class="btn primary" type="button" id="share-copy">복사</button></div>
        <p style="margin:14px 0 0"><button class="btn danger" type="button" id="share-stop">공유 중지</button></p>${warn}`;
    }
    if (st.status === "starting") {
      return `<p>${esc(st.message || "공유 주소를 만드는 중입니다…")}${st.progress != null ? ` ${st.progress}%` : ""}</p><div class="progress"><i style="width:${st.progress != null ? st.progress : 35}%"></i></div>${warn}`;
    }
    return `${st.status === "error" ? `<p class="form-msg err">${esc(st.message || "공유를 시작하지 못했습니다.")}</p>` : st.message ? `<p class="muted">${esc(st.message)}</p>` : ""}
      <p>이 화면을 다른 사람에게 링크로 보여줄 수 있습니다. 링크는 필요할 때만 만들고, 다 보면 중지하세요.</p>
      <p><button class="btn primary" type="button" id="share-start">공유 링크 만들기</button></p>${warn}`;
  }
  async function openShare() {
    const m = $("#share-modal");
    m.hidden = false;
    const draw = (st) => {
      renderShareBtn(st);
      $("#share-body").innerHTML = shareBody(st);
      const go = $("#share-start"), stop = $("#share-stop"), copy = $("#share-copy");
      if (go) go.onclick = async () => { go.disabled = true; draw(await api("/api/share/start", { method: "POST" })); poll(); };
      if (stop) stop.onclick = async () => { draw(await api("/api/share/stop", { method: "POST" })); };
      if (copy) copy.onclick = async () => {
        const inp = $("#share-link");
        try { await navigator.clipboard.writeText(inp.value); } catch (_) { inp.select(); document.execCommand("copy"); }
        copy.textContent = "복사됨";
        setTimeout(() => { copy.textContent = "복사"; }, 1500);
      };
    };
    const poll = () => {
      clearInterval(shareTimer);
      shareTimer = setInterval(async () => {
        if (m.hidden) { clearInterval(shareTimer); return; }
        const st = await api("/api/share").catch(() => null);
        if (!st) return;
        draw(st);
        if (st.status !== "starting") clearInterval(shareTimer);
      }, 1500);
    };
    $("#share-body").innerHTML = `<p class="loading">확인 중…</p>`;
    const st = await api("/api/share").catch((e) => ({ status: "error", message: e.message }));
    draw(st);
    if (st.status === "starting") poll();
  }
  const shareBtn = $("#share-btn");
  if (shareBtn) shareBtn.onclick = () => { location.hash = "#/cloud"; };
  $("#share-modal").addEventListener("click", (ev) => { if (ev.target.closest("[data-mclose]")) { $("#share-modal").hidden = true; clearInterval(shareTimer); } });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !$("#share-modal").hidden) { $("#share-modal").hidden = true; clearInterval(shareTimer); } });


  // ---------------------------------------------------------------- cloud (어디서나 보기)
  async function pageCloud() {
    main.innerHTML = `<div class="wrap narrow"><div class="page-head"><div><h1 class="page-title">어디서나 보기</h1>
      <p class="page-sub">회사 PC가 분석한 결과를 Cloudflare 무료 서비스에 올려, 휴대폰·집 PC에서도 고정 주소로 볼 수 있게 합니다. 비밀번호를 아는 사람만 들어올 수 있고, 보기 전용입니다.</p></div></div>
      <div id="cloud-box"><p class="loading">확인 중…</p></div>
      <section class="panel cloud-panel" style="margin-top:22px">
        <h2 class="d-sec">임시 공유 링크 <small class="muted">실시간 화면을 잠깐 보여줄 때</small></h2>
        <p class="note" style="margin-top:0">이 PC의 화면을 그대로 연결하는 임시 주소입니다. 이 PC가 켜져 있는 동안만 열리고, 만들 때마다 주소가 바뀝니다. 늘 쓰는 주소는 위의 '어디서나 보기'를 쓰세요.</p>
        <button class="btn" type="button" id="open-share">임시 공유 링크 열기</button>
      </section></div>`;
    $("#open-share").onclick = openShare;
    await drawCloud();
    timers.push(setInterval(() => drawCloud(true), 5000));
  }

  async function drawCloud(silent) {
    const box = $("#cloud-box");
    if (!box) return;
    let st;
    try { st = await api("/api/cloud"); } catch (e) { if (!silent) box.innerHTML = `<div class="empty"><strong>상태를 확인하지 못했습니다.</strong>${esc(e.message)}</div>`; return; }
    if (silent && box.querySelector("input:focus")) return;   // 입력 중에는 다시 그리지 않음
    if (st.connected) {
      const t = (x) => (x ? `${x.slice(5, 10).replace("-", "/")} ${x.slice(11, 16)}` : "-");
      box.innerHTML = `<section class="panel cloud-panel">
        <p class="share-ok">사이트가 연결되어 있습니다.</p>
        <div class="share-link"><input id="cloud-url" readonly value="${esc(st.url)}"><button class="btn primary" type="button" id="cloud-copy">복사</button><a class="btn" href="${esc(st.url)}" target="_blank" rel="noopener">열기</a></div>
        <dl class="facts" style="margin-top:18px">
          <div><dt>마지막 업로드</dt><dd class="num" style="font-size:18px">${t(st.last_publish)}${st.busy ? "<small>올리는 중…</small>" : ""}</dd></div>
          <div><dt>실시간 요약</dt><dd class="num" style="font-size:18px">${t(st.last_live)}</dd></div>
          <div><dt>오늘 업로드 (무료 한도 안)</dt><dd class="num" style="font-size:18px">${st.writes_today}<small>/ ${st.write_cap}건</small></dd></div>
        </dl>
        ${st.last_error ? `<p class="form-msg err">마지막 오류 (${t(st.last_error_at)}): ${esc(st.last_error)}</p>` : ""}
        <div class="cloud-actions">
          <button class="btn primary" type="button" id="cloud-pub" ${st.busy ? "disabled" : ""}>지금 올리기</button>
          <button class="btn" type="button" id="cloud-pw-open">비밀번호 바꾸기</button>
          <span class="spacer"></span>
          <button class="btn danger" type="button" id="cloud-off">연결 해제</button>
        </div>
        <form id="cloud-pw" class="cloud-pw" hidden autocomplete="off">
          <label class="field"><span>새 비밀번호 (4자 이상)</span><input type="password" name="pw" required minlength="4" autocomplete="new-password"></label>
          <label class="field"><span>한 번 더</span><input type="password" name="pw2" required minlength="4" autocomplete="new-password"></label>
          <button class="btn primary" type="submit">바꾸기</button><p class="note">바꾸면 이미 로그인한 기기도 모두 다시 비밀번호를 물어봅니다.</p>
        </form>
        <div class="form-msg" id="cloud-msg" role="status"></div>
        <ul class="share-notes">
          <li>장중에는 2분마다 요약(현재가·차트·이상변동 목록), 10분마다 이상변동 상세, 30분마다 기사 분석을 올립니다.</li>
          <li>회사 PC가 꺼져 있어도 사이트는 마지막으로 올린 자료를 보여주고, 화면 위에 언제 올린 자료인지 표시합니다.</li>
          <li>Cloudflare 무료 한도(하루 쓰기 1,000건) 안에서 쓰도록 하루 ${st.write_cap}건까지만 올립니다. 한도를 넘어도 요금이 나가지 않고 다음 날까지 업로드만 멈춥니다.</li>
          <li>사이트 주소와 비밀번호는 필요한 분께만 알려 주세요.</li>
        </ul></section>`;
      $("#cloud-copy").onclick = async () => { const i = $("#cloud-url"); try { await navigator.clipboard.writeText(i.value); } catch (_) { i.select(); document.execCommand("copy"); } $("#cloud-copy").textContent = "복사됨"; };
      $("#cloud-pub").onclick = async () => { $("#cloud-pub").disabled = true; try { await api("/api/cloud/publish", { method: "POST" }); $("#cloud-msg").textContent = "올리는 중입니다. 처음에는 1~2분 걸릴 수 있습니다."; } catch (e) { cloudErr(e); } };
      $("#cloud-pw-open").onclick = () => { const f = $("#cloud-pw"); f.hidden = !f.hidden; };
      $("#cloud-pw").onsubmit = async (ev) => {
        ev.preventDefault();
        const f = new FormData(ev.target);
        if (f.get("pw") !== f.get("pw2")) return cloudErr(new Error("두 비밀번호가 다릅니다."));
        try { await api("/api/cloud/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: f.get("pw") }) }); $("#cloud-msg").className = "form-msg"; $("#cloud-msg").textContent = "비밀번호를 바꿨습니다."; ev.target.reset(); ev.target.hidden = true; }
        catch (e) { cloudErr(e); }
      };
      const off = $("#cloud-off");
      off.onclick = async () => {
        if (off.dataset.confirm !== "1") {
          off.dataset.confirm = "1";
          off.textContent = "한 번 더 누르면 해제";
          $("#cloud-msg").innerHTML = `<label><input type="checkbox" id="cloud-del"> Cloudflare에 올린 사이트와 자료도 지우기 (안 지우면 사이트는 마지막 자료로 남음)</label>`;
          return;
        }
        try { await api("/api/cloud/disconnect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ delete_remote: !!($("#cloud-del") || {}).checked }) }); drawCloud(); }
        catch (e) { cloudErr(e); }
      };
      return;
    }
    if (silent) return;
    box.innerHTML = `<section class="panel cloud-panel">
      <h2 class="d-sec">처음 한 번만 설정 (약 5분)</h2>
      <ol class="cloud-steps">
        <li><b>Cloudflare 무료 가입</b> — <a href="https://dash.cloudflare.com/sign-up" target="_blank" rel="noopener">dash.cloudflare.com/sign-up</a> 에서 이메일로 가입하고, 메일로 온 인증 링크를 누릅니다. 신용카드는 필요 없습니다.</li>
        <li><b>API 토큰 만들기</b> — <a href="https://dash.cloudflare.com/profile/api-tokens" target="_blank" rel="noopener">API 토큰 페이지</a>에서
          [토큰 생성] → 목록의 <b>'Cloudflare Workers 편집'</b>(Edit Cloudflare Workers) 옆 [템플릿 사용] →
          '계정 리소스'는 <b>포함 / 본인 계정</b>, '영역 리소스'는 <b>포함 / 모든 영역</b> → [요약 계속] → [토큰 생성] → 나온 토큰을 [복사]합니다.
          <span class="muted">(토큰은 이때 한 번만 보여주니 바로 아래에 붙여 넣으세요)</span></li>
        <li><b>아래에 붙여 넣고 비밀번호 정하기</b> — 사이트에 들어갈 때 쓸 비밀번호입니다 (Cloudflare 비밀번호와 다르게 정하세요).</li>
      </ol>
      <form id="cloud-form" autocomplete="off">
        <label class="field"><span>API 토큰</span><input name="token" type="password" required placeholder="붙여 넣기 (Ctrl+V)" autocomplete="off"></label>
        <div class="field-row">
          <label class="field"><span>사이트 비밀번호 (4자 이상)</span><input name="pw" type="password" required minlength="4" autocomplete="new-password"></label>
          <label class="field"><span>한 번 더</span><input name="pw2" type="password" required minlength="4" autocomplete="new-password"></label>
        </div>
        <button class="btn primary" type="submit" id="cloud-go">사이트 만들기</button>
        <div class="form-msg" id="cloud-msg" role="status"></div>
      </form>
      <ul class="share-notes">
        <li>Cloudflare Workers 무료 플랜을 씁니다. 요금이 나가는 설정은 하지 않으며, 무료 한도를 넘으면 요금 대신 업로드가 잠시 멈춥니다.</li>
        <li>토큰과 비밀번호 확인값은 이 폴더(data\\cloud.json)에만 저장됩니다. 비밀번호 자체는 저장하지 않습니다.</li>
        <li>회사 보안 규정상 외부 클라우드에 올려도 되는 자료인지 먼저 확인해 주세요.</li>
      </ul></section>`;
    $("#cloud-form").onsubmit = async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target);
      if (f.get("pw") !== f.get("pw2")) return cloudErr(new Error("두 비밀번호가 다릅니다."));
      const btn = $("#cloud-go");
      btn.disabled = true; btn.textContent = "만드는 중… (10~30초)";
      $("#cloud-msg").className = "form-msg"; $("#cloud-msg").textContent = "Cloudflare에 저장소와 사이트를 만드는 중입니다.";
      try {
        await api("/api/cloud/setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: f.get("token"), password: f.get("pw") }) });
        drawCloud();
      } catch (e) { btn.disabled = false; btn.textContent = "사이트 만들기"; cloudErr(e); }
    };
  }
  function cloudErr(e) { const m = $("#cloud-msg"); if (m) { m.className = "form-msg err"; m.textContent = e.message; } }

  // ---------------------------------------------------------------- router
  async function route() {
    cleanupPage();
    closeDrawer();
    const h = location.hash.replace(/^#/, "") || "/";
    const parts = h.split("/").filter(Boolean);
    const page = parts[0] || "dash";
    document.querySelectorAll(".tabs a").forEach((a) => {
      const on = a.dataset.route === (["dash", "calendar", "events"].includes(page) ? "dash" : page);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    const dashView = { dash: "recent", calendar: "calendar", events: "table" }[page];
    $("#hero").hidden = !dashView;
    window.scrollTo(0, 0);
    if (page === "events") { document.title = "급등·급락 표 | 주가 모니터"; await pageDashboard("table"); if (parts[1]) openEvent(+parts[1]); }
    else if (page === "calendar") { document.title = "달력 | 주가 모니터"; await pageDashboard("calendar"); }
    else if (page === "cloud" && !CLOUD) { document.title = "어디서나 보기 | 주가 모니터"; await pageCloud(); }
    else if (page === "pr") { document.title = "기사·공시 모음 | 주가 모니터"; await pagePR(parts[1] || null); }
    else if (page === "method") { document.title = "README | 주가 모니터"; pageMethod(); }
    else { document.title = "주가 모니터"; await pageDashboard(); }
  }

  window.addEventListener("hashchange", route);
  (STATIC ? ensureLogin() : Promise.resolve()).then(() => {
    refreshStatus().then(route);
    setInterval(refreshStatus, STATIC ? 30000 : 10000);
  });
})();
