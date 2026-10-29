/* 주가 모니터 – 화면 스크립트
 * 데이터: PC에서는 webserver.py(/api/...), 웹사이트(GitHub Pages)에서는 암호화된 d/*.bin 파일. */
(() => {
  "use strict";
  const LW = window.LightweightCharts;
  const $ = (sel, el = document) => el.querySelector(sel);
  const main = $("#main");
  const KIND = { up: "급등", down: "급락", flat: "거래량만 급증" };
  const kindOf = (e) => (e.tier === "관찰" || e.tier === "요청" || e.tier === "일일" ? ({ up: "상승", down: "하락" }[e.direction] || KIND[e.direction]) : KIND[e.direction]);
  // 장중 이상변동 숫자의 기준: 장 시작부터 이어진 움직임만 전일 종가 대비, 나머지는 움직임 시작 가격 대비 (감지 규칙 글에 적힌 시작점)
  const moveBasis = (e) => (e.event_type === "daily" || /\(전일 종가 /.test(e.rule || "") ? "전일 대비" : "구간 움직임");
  const scopeTag = (e) => `<span class="scope">${e.scope === "하루" || e.event_type === "daily" ? "하루" : "장중"}</span>`;
  const tierTag = (e) => (e.tier === "대형" ? `<span class="tier">대형</span>`
    : e.tier === "관찰" ? `<span class="tier watch" title="±5~10% 움직임: 기록만 하고 알림은 보내지 않습니다">관찰</span>` : "");
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
  // ⓘ 설명 (마우스를 올리거나 폰은 누르면)
  const infoTip = (lines, title = "") => `<span class="tip" tabindex="0" aria-label="${esc(title || "설명")}">ⓘ<span class="tip-box" role="tooltip">${title ? `<b>${esc(title)}</b>` : ""}${lines.map((l) => `<span>${esc(l)}</span>`).join("")}</span></span>`;
  const COARSE_WHY = "야후는 5분봉을 최근 약 60일치만 주고(60분봉은 약 2년), 받아 둔 날만 남습니다. 그래서 야후 분봉을 받기 시작한 날보다 60일 넘게 앞선 날은 60분봉뿐입니다.";
  const coarseTip = (iv) => infoTip([`이날은 1분봉 기록이 없어 야후 파이낸스 ${iv}분봉으로 그렸습니다.`, ...(+iv === 60 ? [COARSE_WHY] : [])]);
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
  // 화면 아래에 잠깐 떴다 사라지는 알림 (kind "err"면 빨강)
  let toastTimer = 0;
  function toast(msg, kind = "") {
    const el = $("#toast");
    if (!el || !msg) return;
    el.textContent = msg; el.className = `toast ${kind}`; el.hidden = false;
    el.style.animation = "none"; void el.offsetWidth; el.style.animation = "";      // 연달아 떠도 다시 올라오게
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, kind === "err" ? 5000 : 3000);
  }
  // 요청 보내기: 누른 버튼은 보내는 동안 돌아가는 표시, 끝나면 ✓ (버튼 글자는 각 화면이 정함)
  async function sendInbox(item) {
    const btn = document.activeElement && document.activeElement.closest ? document.activeElement.closest("button") : null;
    if (btn) { btn.classList.remove("is-done"); btn.classList.add("is-busy"); }
    try {
      const msg = await sendInboxRaw(item);
      if (btn) { btn.classList.remove("is-busy"); btn.classList.add("is-done"); }
      toast(STATIC ? "GitHub 창에서 [Create]를 누르면 몇 분 안에 반영됩니다." : "요청을 보냈습니다. 다음 자동 수집 때 반영됩니다.");
      return msg;
    } catch (e) {
      if (btn) btn.classList.remove("is-busy");
      toast(e.message || "요청을 보내지 못했습니다.", "err");
      throw e;
    }
  }
  async function sendInboxRaw(item) {
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
    m = p.match(/^\/api\/day\/(\d{4}-\d{2}-\d{2})$/);
    if (m) return kv(`day:${m[1]}`);
    if (p === "/api/articles") {
      const a = await kv("articles");
      if (!q.limit) return a;
      return { ...a, items: a.items.filter((x) => x.category !== "단순 언급").slice(0, +q.limit), summary: null };
    }
    if (p === "/api/disclosures") return kv("disclosures");
    if (p === "/api/daily") return kv("daily");
    if (p === "/api/coarse") return kv("coarse").catch(() => ({ days: {} }));
    if (p === "/api/reactions") return kv("reactions");
    if (p === "/api/observatory") return kv("observatory");
    if (p.startsWith("/api/observatory/")) return kv("observation:" + decodeURIComponent(p.split("/").pop()));
    m = p.match(/^\/api\/disclosures\/(.+)$/);
    if (m) {
      const all = await kv("disclosure_details");
      const d = all[decodeURIComponent(m[1])];
      if (!d) throw new Error("이 공시는 아직 사이트에 올라오지 않았습니다. 다음 자동 수집 때 반영됩니다.");
      return d;
    }
    m = p.match(/^\/api\/articles\/(.+)$/);
    if (m) {
      const all = await kv("article_details"), id = decodeURIComponent(m[1]);
      let d = all[id];
      if (!d) {                             // 묶음 대표가 아닌 기사(같은 내용의 다른 매체·합친 기사)면 그 묶음의 대표 기사
        const lead = ((await kv("articles")).items || []).find((a) => (a.related || []).some((r) => r.id === id));
        d = lead && all[lead.id];
      }
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

  function csvDownloadRows(name, head, rows) {
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [head.map(q).join(",")].concat(rows.map((r) => r.map(q).join(",")));
    const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${name}_${isoDate(new Date()).replace(/-/g, "")}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  const TODAY_CARDS = ["q-price-card", "q-news-card", "q-events-card", "q-kosdaq-card", "q-volume-card"];
  function restoreHero() {
    const strip = $("#hero .today-strip");
    if (!strip) return;
    TODAY_CARDS.forEach((id) => { const el = document.getElementById(id); if (el && el.parentElement !== strip) strip.appendChild(el); });
  }
  function cleanupPage() {
    restoreHero();
    if (PAST_DAY) { PAST_DAY = null; renderQuote(); }
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
  function priceVolumeChart(el, legendEl, bars, { dark = false, events = [], news = [], prevClose = null, extendTo = null, interactive = true, onEvent = null, onNews = null, barMinutes = 1 } = {}) {
    const chart = baseChart(el, dark, interactive ? {} : { handleScroll: false, handleScale: false });
    // 선 색: 전일 종가(없으면 첫 봉)보다 위면 빨강, 아래면 파랑 (네이버·증권사 앱처럼)
    const ref = prevClose || (bars.length ? bars[0].c : null), last = bars.length ? bars[bars.length - 1].c : null;
    const lineColor = kindColor(ref && last != null && last < ref ? "down" : "up", dark);
    // 선 아래를 선 색으로 옅게 채우고 아래로 갈수록 투명하게 (그라데이션)
    const rgba = (a) => { const m = /^#?([0-9a-f]{6})$/i.exec(String(lineColor).trim()); if (!m) return "transparent";
      const v = parseInt(m[1], 16); return `rgba(${v >> 16}, ${(v >> 8) & 255}, ${v & 255}, ${a})`; };
    const price = chart.addSeries(LW.AreaSeries, {
      lineColor, topColor: rgba(0.3), bottomColor: rgba(0.02), lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true,
      priceFormat: { type: "custom", minMove: 1, formatter: (v) => won(v) },
      crosshairMarkerRadius: 4,
    }, 0);
    const vol = chart.addSeries(LW.HistogramSeries, {
      priceFormat: { type: "custom", minMove: 1, formatter: (v) => int(v) }, priceLineVisible: false, lastValueVisible: false,
    }, 1);
    price.priceScale().applyOptions({ scaleMargins: { top: 0.16, bottom: 0.14 } });   // 위·아래 여백: 급등락 글자 자리
    chart.panes()[1].setStretchFactor(0.32);
    chart.panes()[0].setStretchFactor(1);

    // 이상변동 구간 (시작 5분 전 ~ 마지막 감지)
    const spans = events.map((e) => ({ k: e.direction, a: toTime(e.start_ts) - 4 * 60, b: toTime(e.last_ts) }));
    const baseVol = dark ? "rgba(255,255,255,.26)" : (matchMedia("(prefers-color-scheme: dark)").matches ? "rgba(255,255,255,.22)" : "#C3CAD6");
    const pdata = [], vdata = [];
    // 왼쪽 끝 여백: 첫 봉 앞에 빈 칸 몇 개 (09:00 급등·급락 표시 글자가 차트 밖으로 잘리지 않게)
    if (bars.length) for (let k = 6; k >= 1; k--) { const t = toTime(bars[0].t) - k * barMinutes * 60; pdata.push({ time: t }); vdata.push({ time: t }); }
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
    let pinT = null, showAt = () => {};      // 이름표에 마우스를 올린 동안 위 줄(시각·가격·거래량)을 그 시각으로 고정 (아래 legend에서 채움)
    el.style.position = "relative";
    // 급등·급락 표시: 숫자(-4.5% 등)에 실제로 닿은 지점에 흰 속 고리 점, 그 옆(상승은 위, 하락은 아래)에 색 글자.
    // 바탕 상자 없이 글자만(흰 테두리 글씨) 두어 선을 가리지 않음. 누르면 원인 분석 창
    const tags = events.map((e) => {
      const t = toTime(e.peak_ts || e.start_ts), b = bars.find((x) => toTime(x.t) === t), color = kindColor(e.direction, dark);
      const mk = (tagName, cls) => {
        const n = document.createElement(tagName);
        n.className = cls;
        n.addEventListener("pointerdown", (ev) => ev.stopPropagation());   // 차트 구간 고르기와 겹치지 않게
        n.addEventListener("pointerup", (ev) => ev.stopPropagation());
        if (onEvent) n.addEventListener("click", () => onEvent(e.id));
        // 이름표 위에 마우스가 있으면 차트는 마우스가 나간 줄 알고 맨 위 줄에 마지막 봉(예: 15:30)을 보여 줌
        // → 그 표시가 가리키는 시각·가격으로 십자선을 옮겨, 위 줄도 그 시각 값이 나오게
        if (b) {
          n.addEventListener("mouseenter", () => { chart.setCrosshairPosition(b.c, t, price); showAt(t); });
          n.addEventListener("mouseleave", () => { chart.clearCrosshairPosition(); showAt(null); });
        }
        el.appendChild(n);
        return n;
      };
      const ring = mk("button", "ev-ring"), tag = mk("button", "ev-tag");
      ring.type = tag.type = "button";
      ring.setAttribute("aria-label", `${pct(e.peak_return_5m, 1)} 원인 분석 열기`);
      tag.textContent = pct(e.peak_return_5m, 1);
      tag.style.color = ring.style.borderColor = color;
      return { tag, ring, t, px: b ? b.c : null, up: e.direction === "up" };
    });
    // 그날 소식: 기간 차트와 같은 모양(보도자료 ●, 공시 ■, 외부 기사 ▲)을 공개 시각의 선 위에.
    // 장 시작 전 공개는 첫 봉, 장 마감 뒤 공개는 마지막 봉에. 같은 봉의 같은 종류는 하나로(마우스를 올리면 제목들), 누르면 뉴스 탭
    const SHAPE = { pr: '<circle cx="8" cy="8" r="6"/>', disc: '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/>', ext: '<path d="M8,1.5L14.5,13.5L1.5,13.5Z"/>' };
    const NAME = { pr: "보도자료", disc: "공시", ext: "외부 기사" };
    const dots = [];
    if (bars.length && news.length) {
      const t0 = toTime(bars[0].t), t1 = toTime(bars[bars.length - 1].t), groups = new Map();
      for (const a of news) {
        const ta = toTime(a.ts), tt = ta <= t0 ? t0 : ta >= t1 ? t1 : ta;
        const b = bars.find((x) => toTime(x.t) >= tt) || bars[bars.length - 1], key = `${b.t}|${a.k}`;
        if (!groups.has(key)) groups.set(key, { b, k: a.k, items: [] });
        groups.get(key).items.push(a);
      }
      const order = { pr: 0, disc: 1, ext: 2 }, perBar = {};
      [...groups.values()].sort((p, q) => order[p.k] - order[q.k]).forEach((g) => {
        const n = document.createElement("button"), t = toTime(g.b.t);
        n.type = "button"; n.className = `nw-mk ${g.k}${g.items.some((a) => a.cur) ? " cur" : ""}`;   // cur: 지금 열어 둔 기사·공시
        n.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">${SHAPE[g.k]}</svg>`;
        n.title = g.items.map((a) => `${hhmm(a.ts)} ${NAME[g.k]} · ${a.title}`).join(String.fromCharCode(10));
        n.setAttribute("aria-label", `${NAME[g.k]} ${g.items.length}건 뉴스 탭에서 열기`);
        n.addEventListener("pointerdown", (ev) => ev.stopPropagation());
        n.addEventListener("pointerup", (ev) => ev.stopPropagation());
        if (onNews) n.addEventListener("click", () => onNews(g.items[0].id));
        n.addEventListener("mouseenter", () => { chart.setCrosshairPosition(g.b.c, t, price); showAt(t); });
        n.addEventListener("mouseleave", () => { chart.clearCrosshairPosition(); showAt(null); });
        el.appendChild(n);
        const slot = perBar[g.b.t] = (perBar[g.b.t] || 0) + 1;       // 같은 봉에 여러 종류면 옆으로 조금씩
        dots.push({ n, t, px: g.b.c, dx: (slot - 1) * 13 });
      });
    }
    // 글자끼리 겹치면(가까운 시각의 두 표시, 좁은 화면) 더 멀리 띄우거나 반대쪽으로
    const placeTags = () => {
      const H = chart.panes()[0].getHeight() || el.clientHeight, W = el.clientWidth, placed = [];   // 가격 칸 안에만 (거래량 칸 침범 X)
      const hit = (a, b) => a.l < b.r + 4 && b.l < a.r + 4 && a.t < b.b + 4 && b.t < a.b + 4;
      const place = ({ g: { tag, ring, up }, x, y }) => {
        const off = x == null || y == null;
        tag.hidden = ring.hidden = off;
        if (off) return;
        const w = tag.offsetWidth || 52, h = tag.offsetHeight || 18, d = up ? -1 : 1;
        const clampX = (v) => Math.min(Math.max(v, w / 2 + 2), W - w / 2 - 2);   // 좌우 끝에서 글자가 잘리지 않게
        const box = ([cx, ty]) => ({ l: cx - w / 2, r: cx + w / 2, t: ty - h / 2, b: ty + h / 2 });
        const inside = ([, ty]) => ty - h / 2 >= 0 && ty + h / 2 <= H;
        // 후보 자리: 점 위(상승)나 아래(하락)로 조금씩 멀리, 막히면 반대쪽
        const cands = [16, 32, 48].map((k) => [clampX(x), y + d * k]);
        cands.push([clampX(x), y - d * 16], [clampX(x), y - d * 32]);
        const [cx, ty] = cands.find((c) => inside(c) && !placed.some((p) => hit(p, box(c)))) ?? cands.find(inside) ?? cands[0];
        placed.push(box([cx, ty]), { l: x - 7, r: x + 7, t: y - 7, b: y + 7 });
        tag.style.left = `${cx}px`; ring.style.left = `${x}px`;
        tag.style.top = `${ty}px`; ring.style.top = `${y}px`;
      };
      const pos = (g) => ({ g, x: chart.timeScale().timeToCoordinate(g.t), y: g.px == null ? null : price.priceToCoordinate(g.px) });
      dots.forEach((g) => {                        // 소식 표시는 선 위 그 자리 (글자들이 피해 가도록 먼저 자리 차지)
        const { x, y } = pos(g);
        g.n.hidden = x == null || y == null;
        if (g.n.hidden) return;
        g.n.style.left = `${x + g.dx}px`; g.n.style.top = `${y}px`;
        placed.push({ l: x + g.dx - 8, r: x + g.dx + 8, t: y - 8, b: y + 8 });
      });
      tags.map(pos).sort((p, q) => (p.x ?? 0) - (q.x ?? 0)).forEach(place);
    };
    if (tags.length || dots.length) {
      chart.timeScale().subscribeVisibleLogicalRangeChange(placeTags);
      chart.timeScale().subscribeSizeChange(placeTags);
      requestAnimationFrame(placeTags);
      // 차트 높이가 바뀌면(1일 탭에서 빈칸만큼 키울 때 등) 가격 축이 다시 그려진 뒤 표시를 다시 놓음 (위 구독은 폭 변화만 알림)
      if (window.ResizeObserver) new ResizeObserver(() => requestAnimationFrame(() => requestAnimationFrame(placeTags))).observe(el);
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
        legendEl.innerHTML = `<span>${hhmm(b.t)}</span><span><b>${won(b.c)}원</b>${chg != null ? ` <span class="${dark ? "" : sgnCls(chg)}">${pct(chg)}</span>` : ""}</span><span>${barMinutes}분 거래량 <b>${int(b.v)}주</b></span>`;
      };
      show(null);
      // 급등·급락 이름표 위에 있을 때(pinT)는 차트가 '마우스 나감'으로 알려도 그 표시 시각 값을 그대로 보여 줌
      chart.subscribeCrosshairMove((p) => show(p && p.time ? p.time : pinT));
      showAt = (t) => { pinT = t; show(t); };
    }
    return chart;
  }

  /** 이 종목 vs KOSDAQ 변화율(%) 한 축 비교 */
  // extra: { others: [[분, 값], …][], median: [[분, 값, n], …], label } — 비교 시작(startT) 기준 분으로 그린 다른 기사·중앙값 (뒤에 연하게)
  function indexedChart(el, legendEl, own, kq, base, kqBase, startT, label, daily = false, extra = null) {
    const dfmt = (t) => { const d = new Date(t * 1000); return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`; };
    // 휠·끌기로 확대·이동되지 않게 고정 (기사 화면을 스크롤하다 차트가 줌되던 문제). 마우스를 올리면 숫자는 그대로 보임
    const fixed = { handleScroll: false, handleScale: false };
    const chart = baseChart(el, false, daily ? { ...fixed, timeScale: { borderVisible: false, timeVisible: false, tickMarkFormatter: dfmt, fixLeftEdge: true, fixRightEdge: true }, localization: { locale: "ko-KR", timeFormatter: dfmt } } : fixed);
    const ownColor = matchMedia("(prefers-color-scheme: dark)").matches ? css("--lens") : css("--navy-2");
    const ownName = (STATUS && STATUS.name) || "이 종목";      // 회사 이름 (비공개 설정의 display_name)
    const fmt = { type: "custom", minMove: 0.01, formatter: (v) => pctRaw(v) };
    const st0 = toTime(startT), atMin = (sr) => sr.map(([m, v]) => ({ time: st0 + Math.round(m * 60), value: v * 100 }));
    if (extra) {
      const quiet = { priceFormat: fmt, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
      extra.others.forEach((sr) => chart.addSeries(LW.LineSeries, { ...quiet, color: css("--line-2"), lineWidth: 1 }).setData(atMin(sr)));
      if (extra.median.length) chart.addSeries(LW.LineSeries, { ...quiet, color: css("--brand-blue"), lineWidth: 2 }).setData(atMin(extra.median));
    }
    const s1 = chart.addSeries(LW.LineSeries, { color: ownColor, lineWidth: extra ? 3 : 2, priceFormat: fmt, priceLineVisible: false, lastValueVisible: true, title: ownName });
    const s2 = chart.addSeries(LW.LineSeries, { color: css("--muted"), lineWidth: 2, lineStyle: LW.LineStyle.Dashed, priceFormat: fmt, priceLineVisible: false, lastValueVisible: true, title: "KOSDAQ" });
    const pt = (b, b0) => (b.c == null ? { time: toTime(b.t) } : { time: toTime(b.t), value: (b.c / b0 - 1) * 100 });     // 가격 없는 날은 빈칸(선을 잇지 않음)
    const d1 = own.map((b) => pt(b, base));
    const d2 = kqBase ? kq.map((b) => pt(b, kqBase)) : [];
    const st = toTime(startT);
    s1.setData(d1.some(p => p.time === st) ? d1 : [...d1, { time: st }].sort((a,b) => a.time-b.time));
    if (d2.length) s2.setData(d2);
    s1.createPriceLine({ price: 0, color: css("--line-2"), lineWidth: 1, lineStyle: LW.LineStyle.Solid, axisLabelVisible: false });
    const near = d1.find((p) => p.time === st);
    if (d1.length) {
      // 공개 시각: 화살표 대신 선 위 작은 점 + 세로 점선 + 위쪽 이름표 (차트를 움직이거나 크기가 바뀌면 따라감)
      if (near) LW.createSeriesMarkers(s1, [{ time: st, position: "inBar", shape: "circle", color: css("--mk-pub"), size: 0.7 }]);
      el.style.position = "relative";
      const pin = document.createElement("div");
      pin.className = "rx-pub";
      pin.innerHTML = `<span class="rx-pub-tag">${esc(label)}${label === "공개" ? ` ${utcHHMM(st)}` : ""}</span>`;
      el.appendChild(pin);
      const place = () => {
        const x = chart.timeScale().timeToCoordinate(st);
        pin.hidden = x == null;
        if (x != null) { pin.style.left = `${x}px`; pin.style.bottom = `${chart.timeScale().height()}px`; }
      };
      chart.timeScale().subscribeVisibleLogicalRangeChange(place);
      chart.timeScale().subscribeSizeChange(place);
      place();
      // 폰에서는 차트 칸 크기가 늦게 정해지기도 해서, 화면이 자리 잡은 뒤 한 번 더 맞춤
      requestAnimationFrame(place);
      setTimeout(place, 400);
      if (window.ResizeObserver) new ResizeObserver(place).observe(el);
    }
    chart.timeScale().fitContent();
    const show = (t) => {
      const stamp = t == null ? (d1[d1.length - 1] || {}).time : t;
      const a = d1.find((p) => p.time === stamp);
      const b = d2.find((p) => p.time === stamp);
      if (stamp == null) return;
      legendEl.innerHTML = `<span>${daily ? dfmt(stamp) : utcHHMM(stamp)}</span><span><i class="rx-sw" style="border-color:${ownColor}"></i>${esc(ownName)} <b class="${sgnCls(a && a.value)}">${a && a.value != null ? pctRaw(a.value) : "-"}</b></span>` +
        `<span><i class="rx-sw dash" style="border-color:${css("--muted")}"></i>KOSDAQ <b>${b && b.value != null ? pctRaw(b.value) : (d2.length ? "-" : "데이터 없음")}</b></span>`
        + (extra ? `${extra.median.length ? `<span><i class="rx-sw" style="border-color:${css("--brand-blue")}"></i>중앙값</span>` : ""}<span><i class="rx-sw" style="border-color:${css("--line-2")}"></i>다른 ${esc(extra.label || "기사")} ${extra.n}건 (+${extra.until}분까지)</span>` : "");
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
        const stuck = ((s.collect && s.collect.sources) || []).filter((r) => r.stale);
        if (stuck.length) setEngine("stale", `갱신 ${when} · 수집 확인 필요`);
        else setEngine("ok", `갱신 ${when}`);
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

  // 오늘 '지금 등락'은 한 곳(네이버 현재가)에서만 가져와 화면 어디서나 같은 숫자를 씀.
  // 네이버·증권사 앱과 같은 기준: 가격은 넥스트레이드(~20:00) 포함 마지막 체결가, 등락은 기준가(전날 거래소 종가) 대비
  function liveFor(date) {
    const q = STATUS && STATUS.quote;
    if (!q || q.price == null || q.change_pct == null || !q.traded_at || q.traded_at.slice(0, 10) !== date) return null;
    const at = q.traded_at.slice(11, 16), open = q.market_status === "OPEN";
    return { price: q.price, chg: q.change_pct / 100, at, open, closed: !open && at >= "15:30", ext: at > "15:30" };
  }

  // 현재 하루 수치와 저장된 분석 근거는 별개다. 장 마감 뒤에도 메인과 상세는 같은 시세를 쓴다.
  function dailySnapshot(e) {
    const day = e.start_ts.slice(0, 10), row = DAY_ROWS[day] || {}, live = liveFor(day);
    const change = live ? live.chg : row.change ?? e.peak_return_5m;
    return { change, price: live ? live.price : row.close,
      kosdaq: row.kosdaq ?? e.kosdaq_change,
      label: live ? `${live.at} ${live.ext ? "시간외 포함 최종가" : live.open ? "현재가" : "정규장 종가"}`
        : row.close != null ? "일봉 종가" : `${hhmm(e.last_ts || e.peak_ts)} 저장된 하루 기록`,
      live };
  }

  function dailyEvidenceNote(e) {
    if (!isDaily(e)) return "";
    const snap = dailySnapshot(e);
    return `분석 당시 저장된 하루 등락 ${pct(e.peak_return_5m)} · 기록 종료 ${hhmm(e.last_ts || e.peak_ts) || "시각 미상"}. 원인 본문과 아래 분석 근거는 저장 당시 자료이며 현재 시세로 자동 수정하지 않습니다.${snap.change != null && e.peak_return_5m != null && Math.abs(snap.change - e.peak_return_5m) > 0.00005 ? ` 현재 표시 ${pct(snap.change)}와 기준이 다릅니다.` : ""}`;
  }

  let QUOTE_CP = null;          // 지금 등락(%) — KOSDAQ 카드의 '시장보다' 계산용
  let PAST_DAY = null;          // 주가 기록에서 지난 날을 골랐으면 그 날짜 (위 칸들이 그날 기준)
  function renderQuote() {
    if (!STATUS) return;
    const q = STATUS.quote, k = STATUS.kosdaq;
    $("#q-name").textContent = STATUS.name;
    $("#q-code").textContent = STATUS.symbol;
    if (PAST_DAY) return;
    if (q && q.price != null) {
      const lv = q.traded_at ? liveFor(q.traded_at.slice(0, 10)) : null;
      const price = q.price, change = q.change, cp = q.change_pct;
      $("#q-price").textContent = won(price);
      const cls = change > 0 ? "up" : change < 0 ? "down" : "";
      const arrow = change > 0 ? "▲" : change < 0 ? "▼" : "";
      const asof = q.market_status === "OPEN" ? `${(q.traded_at || "").slice(11, 16)} 기준`
        : lv && lv.ext ? `장 마감 · 시간외 포함 ${lv.at}` : "장 마감";
      $("#q-change").innerHTML = `<span class="chg ${cls}">${arrow} ${won(Math.abs(change))} (${pctRaw(cp)})</span><span class="asof">${asof}</span>`;
      $("#q-volume").innerHTML = `${int(q.volume)}<small>주</small>`;
      QUOTE_CP = cp;
    }
    if (k && k.price != null) {
      $("#q-kosdaq").innerHTML = `${k.price.toFixed(2)}<small class="${sgnCls(k.change_pct)}">${pctRaw(k.change_pct)}</small>`;
      const gap = QUOTE_CP != null && k.change_pct != null ? QUOTE_CP - k.change_pct : null;
      $("#q-kosdaq-sub").textContent = gap == null ? "" : `종목이 시장보다 ${gap >= 0 ? "+" : "−"}${Math.abs(gap).toFixed(2)}%p`;
    }
  }

  function renderFoot() {
    if (!STATUS) return;
    $("#foot").innerHTML = `<span>데이터 출처 네이버 증권·다음 금융</span>${CLOUD ? '<span><a href="#" data-logout>로그아웃</a></span>' : ""}`;
  }

  // ---------------------------------------------------------------- 오늘 한눈에 (위 카드 줄)
  // 소식 칸: 최근 거래일의 그린광학 보도자료(건수·실린 매체 수)·주요 공시·외부 기사. 차트 점과 같은 기준
  function fillToday(daily, arts, discs, events, onNews, day = null) {
    if (!daily.length) return;
    const last = daily[daily.length - 1].date;
    const latest = day && daily.some((x) => x.date === day) ? day : last;     // 고른 날 (없으면 최근 거래일)
    const today = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
    const when = latest === today ? "오늘" : `${+latest.slice(5, 7)}/${+latest.slice(8, 10)}`;
    const on = (ts) => !!ts && ts.slice(0, 10) === latest;
    PAST_DAY = latest === last ? null : latest;
    const i = daily.findIndex((x) => x.date === latest), px = daily[i], pv = i > 0 ? daily[i - 1] : null;
    const live = STATUS && STATUS.quote && STATUS.quote.price != null && String(STATUS.quote.traded_at || "").slice(0, 10) === latest;
    if (PAST_DAY || !live) {            // 지난 날(또는 실시간 시세가 없을 때): 그날 종가·등락, KOSDAQ 등락, 거래량
      const base = px.base || (pv && pv.close), chg = base ? px.close - base : null;     // 네이버 기준가 대비
      $("#q-price").textContent = won(px.close);
      $("#q-change").innerHTML = `${chg == null ? "" : `<span class="chg ${chg > 0 ? "up" : chg < 0 ? "down" : ""}">${chg > 0 ? "▲" : chg < 0 ? "▼" : ""} ${won(Math.abs(chg))} (${pct(px.change)})</span>`}<span class="asof">${when} 종가</span>`;
      $("#q-kosdaq").innerHTML = px.kosdaq_close == null && px.kosdaq == null ? "-"       // 지수와 등락률을 함께 (지금 시세 칸과 같은 모양)
        : `${px.kosdaq_close == null ? "" : px.kosdaq_close.toFixed(2)}<small class="${sgnCls(px.kosdaq)}">${px.kosdaq == null ? "" : pct(px.kosdaq)}</small>`;
      const gap = px.change != null && px.kosdaq != null ? (px.change - px.kosdaq) * 100 : null;
      $("#q-kosdaq-sub").textContent = gap == null ? "" : `종목이 시장보다 ${gap >= 0 ? "+" : "−"}${Math.abs(gap).toFixed(2)}%p`;
      $("#q-volume").innerHTML = `${int(px.volume)}<small>주</small>`;
    } else renderQuote();
    const ar = arts.filter((a) => !a.hidden && a.category !== "단순 언급" && on(a.ts));
    const isPr = (a) => a.kind === "pr" || a.kind === "company";
    const pr = ar.filter(isPr), ext = ar.length - pr.length;
    const outlets = pr.reduce((t, a) => t + (a.outlets_total || 1), 0);
    const dc = discs.filter((x) => on(x.ts) && MAIN_DISC.has(x.pblntf || "")).length;
    $("#q-news-label").textContent = `${when} 소식`;
    $("#q-news").innerHTML = `<span class="t-go" data-go="pr" title="뉴스 탭에서 이날 보도자료 보기"><i class="mk pr"></i>보도자료 <b>${pr.length}</b></span>`
      + `<span class="t-go" data-go="disc" title="뉴스 탭에서 이날 공시 보기"><i class="mk disc"></i>공시 <b>${dc}</b></span><span class="t-go" data-go="ext" title="뉴스 탭에서 이날 외부 기사 보기"><i class="mk ext"></i>외부기사 <b>${ext}</b></span>`;
    const dcList = discs.filter((x) => on(x.ts) && MAIN_DISC.has(x.pblntf || ""));
    const lines = [...pr.map((a) => ({ k: "pr", a })), ...dcList.map((a) => ({ k: "disc", a })), ...ar.filter((a) => !isPr(a)).map((a) => ({ k: "ext", a }))];
    const MAXL = 4;
    $("#q-news-list").innerHTML = lines.slice(0, MAXL).map(({ k, a }) => `<span class="tn-item" data-open="${esc(a.id)}" title="${esc(a.title)}"><i class="mk ${k}"></i><span class="muted">${a.untimed ? "--:--" : hhmm(a.ts)}</span><span class="tn-t">${esc(a.title)}</span></span>`).join("")
      + (lines.length > MAXL ? `<span class="tn-more" data-go="${lines[MAXL].k}">외 ${lines.length - MAXL}건 →</span>` : "");
    // 실린 매체 수는 아래 작은 글씨로 (위 숫자 줄은 한 줄로)
    $("#q-news-sub").textContent = lines.length ? (pr.length ? `보도자료 ${outlets}개 매체에 실림 · 제목을 누르면 뉴스 탭에서 열림` : "제목을 누르면 뉴스 탭에서 열림") : "";
    const card = $("#q-news-card");
    if (card) card.onclick = (ev) => {
      const open = ev.target.closest("[data-open]");
      if (open) { artState.day = latest; location.hash = `#/pr/${open.dataset.open}`; return; }
      const go = ev.target.closest("[data-go]");
      if (go) { artState.cat = go.dataset.go; artState.day = latest; location.hash = "#/pr"; return; }
      return onNews ? onNews(latest) : (calState.sel = latest, location.hash = "#/");
    };
    // 급등·급락: 그날 알림 대상(관찰·직접 요청 제외) 중 가장 크게 움직인 것
    const ev = (events || []).filter((e) => on(e.start_ts) && e.tier !== "관찰" && e.tier !== "요청");
    $("#q-events-label").textContent = `${when} 급등·급락`;
    if (!ev.length) $("#q-events").textContent = "없음";
    else {
      const top = ev.reduce((m, e) => (Math.abs(e.peak_return_5m || 0) > Math.abs(m.peak_return_5m || 0) ? e : m), ev[0]);
      $("#q-events").innerHTML = `<span class="${sgnCls(top.peak_return_5m)}">${top.direction === "up" ? "급등" : "급락"} ${pct(top.peak_return_5m, 1)}</span>${ev.length > 1 ? `<small>외 ${ev.length - 1}건</small>` : ""}`;
    }
  }

  // ---------------------------------------------------------------- dashboard
  // 최근 거래일(최대 15일)을 하루 한 장씩 세로로 쌓음. 카드가 화면 가까이 오면 그날 자료를 불러옴.
  // 이상변동이 없던 날은 한 줄로 접어 두고, 누르면 차트를 펼침. 오늘 카드만 30초마다 새로 그림.
  // 주가 기록: [차트] [일별 표] (차트 = 기간 탭 + 히트맵 + 고른 날, 급등·급락 기록 내려받기는 일별 표에)
  const VIEWS = [["recent", "차트", "#/"], ["days", "일별 표", "#/days"]];
  async function pageDashboard(view = "recent") {
    $("#hero").hidden = false;
    main.innerHTML = `<div class="wrap"><div class="view-bar"><div class="seg" role="tablist" aria-label="보기">${VIEWS.map(([v, t, h]) =>
      `<a href="${h}" role="tab" aria-pressed="${v === view}">${t}</a>`).join("")}</div></div><div id="view"></div></div>`;
    if (view === "days") return viewDays();
    return viewCalendar();
  }

  // 일별 표: 모든 거래일을 한 줄씩 (시세·수급·그날 기사·공시 수). 급등·급락 표와 따로
  async function viewDays() {
    restoreHero();
    const box = $("#view");
    box.innerHTML = `<p class="loading">불러오는 중…</p>`;
    let daily, arts, discs;
    try {
      [daily, arts, discs] = await Promise.all([
        api("/api/daily").then((d) => d.days || []),
        api("/api/articles").then((d) => d.items || []).catch(() => []),
        api("/api/disclosures").then((d) => d.items || []).catch(() => []),
      ]);
    } catch (e) { box.innerHTML = `<div class="empty"><strong>일별 자료를 불러오지 못했습니다.</strong>${esc(e.message)}</div>`; return; }
    if (!daily.length) { box.innerHTML = `<div class="empty"><strong>아직 일별 시세가 없습니다.</strong></div>`; return; }
    api("/api/events").then((d) => fillToday(daily, arts, discs, d.events || [])).catch(() => fillToday(daily, arts, discs, []));
    const count = (list, dayOf) => { const m = {}; list.forEach((x) => { const d = dayOf(x); if (d) m[d] = (m[d] || 0) + 1; }); return m; };
    const shown = (a) => !a.hidden && a.category !== "단순 언급" && a.ts;
    const prN = count(arts.filter((a) => shown(a) && (a.kind === "pr" || a.kind === "company")), (a) => a.ts.slice(0, 10));
    const extN = count(arts.filter((a) => shown(a) && a.kind !== "pr" && a.kind !== "company"), (a) => a.ts.slice(0, 10));
    const discN = count(discs.filter((x) => x.ts && MAIN_DISC.has(x.pblntf)), (x) => x.ts.slice(0, 10));
    const won = (v) => (v == null ? "-" : Math.round(v).toLocaleString("ko-KR"));
    const net = (v) => (v == null ? "-" : `<span class="${sgnCls(v)}">${v > 0 ? "+" : ""}${Math.round(v).toLocaleString("ko-KR")}</span>`);
    const hit = (ch) => !daysState.dir || (ch != null && (daysState.dir === "up" ? ch >= daysState.th / 100 : ch <= -daysState.th / 100));
    const inRange = () => spanRows(daily, daysState);
    box.innerHTML = `<div class="controls days-controls">
        <div class="rec-tabs" role="tablist">${[...REC_RANGES, ["custom", "기간 지정"]].map(([v, t]) => `<button type="button" data-drange="${v}" aria-pressed="${daysState.range === v}">${t}</button>`).join("")}</div>
        ${periodHtml(daily, daysState, "data-dperiod")}
        <span class="muted">등락 고르기</span><div class="seg" role="group" aria-label="등락 고르기">${[["", "끄기"], ["up", "오른 날"], ["down", "내린 날"]].map(([v, t]) => `<button type="button" data-dir="${v}" aria-pressed="${daysState.dir === v}">${t}</button>`).join("")}</div>
        <label class="field mv-th"><span>기준 (%)</span><input type="number" id="mv-th" min="0" step="0.5" value="${esc(String(daysState.th))}"${daysState.dir ? "" : " disabled"}></label>
        <span class="spacer"></span>
        <button type="button" class="btn" id="days-csv">엑셀로 내려받기</button>
        <button type="button" class="btn" id="events-csv" title="감지 기준(급등·급락 ±10%, 대형 ±15%, 관찰 ±5~10%)에 걸린 하루·장중 기록 전체">급등·급락 기록 내려받기</button></div>
      <div id="days-sum" class="days-sum"></div>
      <div class="table-wrap"><table class="days-table"><thead><tr>
        <th>날짜</th><th>시가</th><th>고가</th><th>저가</th><th>종가</th><th>등락</th><th>거래량</th><th>KOSDAQ</th>
        <th>외국인</th><th>기관</th><th>개인</th><th>보도자료</th><th>외부 기사</th><th>주요 공시</th></tr></thead><tbody id="days-body"></tbody></table></div>`;
    let rows = [];
    // 기간 요약 (Investing.com 과거 데이터 표 아래 요약 참고): 최고·최저·변화율·평균 거래량
    function draw() {
      const span = inRange();
      if (!span.length) {
        rows = [];
        $("#days-sum").innerHTML = "";
        $("#days-body").innerHTML = `<tr><td colspan="14" class="muted">고른 기간에 거래일이 없습니다.</td></tr>`;
        return;
      }
      rows = span.filter((x) => hit(x.change)).reverse();
      const first = span[0], last = span[span.length - 1];
      const base = first.change != null ? first.close / (1 + first.change) : first.close;
      const hi = span.reduce((m, x) => (x.high > m.high ? x : m), span[0]), lo = span.reduce((m, x) => (x.low < m.low ? x : m), span[0]);
      const avgVol = span.reduce((t, x) => t + (x.volume || 0), 0) / span.length;
      $("#days-sum").innerHTML = `<span><span class="muted">기간</span> ${esc(first.date)} ~ ${esc(last.date)} (${span.length}거래일)</span>
        <span><span class="muted">최고</span> <b>${won(hi.high)}</b> <small class="muted">${esc(hi.date.slice(5))}</small></span>
        <span><span class="muted">최저</span> <b>${won(lo.low)}</b> <small class="muted">${esc(lo.date.slice(5))}</small></span>
        <span><span class="muted">변화율</span> <b class="${sgnCls(last.close / base - 1)}">${pct(last.close / base - 1)}</b></span>
        <span><span class="muted">평균 거래량</span> <b>${won(avgVol)}</b></span>
        ${daysState.dir ? `<span><span class="muted">${daysState.dir === "up" ? "오른" : "내린"} 날 ${daysState.th}% 이상</span> <b>${rows.length}일</b></span>` : ""}`;
      $("#days-body").innerHTML = rows.length ? rows.map((x) => `<tr><td><a href="#/calendar" data-day="${esc(x.date)}">${esc(x.date)}</a></td>
          <td class="num">${won(x.open)}</td><td class="num">${won(x.high)}</td><td class="num">${won(x.low)}</td><td class="num">${won(x.close)}</td>
          <td class="num ${sgnCls(x.change)}">${x.change == null ? "-" : pct(x.change)}</td><td class="num">${won(x.volume)}</td>
          <td class="num ${sgnCls(x.kosdaq)}">${x.kosdaq == null ? "-" : pct(x.kosdaq)}</td>
          <td class="num">${net(x.foreign)}</td><td class="num">${net(x.organ)}</td><td class="num">${net(x.individual)}</td>
          <td class="num">${prN[x.date] || ""}</td><td class="num">${extN[x.date] || ""}</td><td class="num">${discN[x.date] || ""}</td></tr>`).join("")
        : `<tr><td colspan="14" class="muted">조건에 맞는 날이 없습니다.</td></tr>`;
    }
    draw();
    box.onclick = (ev) => {
      const a = ev.target.closest("a[data-day]");
      if (a) { calState.sel = a.dataset.day; return; }
      const rb = ev.target.closest("[data-drange]");
      if (rb) {
        const prev = inRange(); daysState.range = rb.dataset.drange;
        box.querySelectorAll("[data-drange]").forEach((b) => b.setAttribute("aria-pressed", String(b === rb)));
        $(".rec-period", box).hidden = daysState.range !== "custom";
        if (daysState.range === "custom") startPeriod(daily, daysState, prev, box, "data-dperiod");
        draw(); return;
      }
      const dir = ev.target.closest("[data-dir]");
      if (dir) {
        daysState.dir = dir.dataset.dir;
        box.querySelectorAll("[data-dir]").forEach((b) => b.setAttribute("aria-pressed", String(b === dir)));
        $("#mv-th").disabled = !daysState.dir; draw();
      }
    };
    box.onchange = (ev) => { const p = ev.target.closest("[data-dperiod]"); if (p && setPeriod(daysState, p, "data-dperiod")) draw(); };
    box.oninput = (ev) => {                  // 기준 숫자는 치는 즉시 반영
      if (ev.target.id !== "mv-th") return;
      const v = parseFloat(ev.target.value); if (!(v > 0)) return;
      daysState.th = Math.round(v * 10) / 10; draw();
    };
    $("#events-csv").onclick = async () => { try { csvDownload((await api("/api/events")).events || []); } catch (e) { toast(e.message, "err"); } };
    $("#days-csv").onclick = () => csvDownloadRows("일별기록",
      ["날짜", "시가", "고가", "저가", "종가", "등락(%)", "거래량", "KOSDAQ(%)", "외국인 순매수", "기관 순매수", "개인 순매수", "보도자료", "외부 기사", "주요 공시"],
      rows.map((x) => [x.date, x.open, x.high, x.low, x.close, x.change == null ? "" : (x.change * 100).toFixed(2), x.volume,
        x.kosdaq == null ? "" : (x.kosdaq * 100).toFixed(2), x.foreign ?? "", x.organ ?? "", x.individual ?? "",
        prN[x.date] || 0, extN[x.date] || 0, discN[x.date] || 0]));
  }

  // 주가 기록 (로빈후드·토스 종목 화면 참고): 기간 탭이 있는 큰 차트가 주인공, 선 위에 그날 소식 점,
  // 아래 고른 날 요약 + 그날 1분 차트(구간 골라 원인 묻기). 날마다 등락은 [일별 표]
  const calState = { range: "1d", sel: null, from: "", to: "" };
  const daysState = { range: "all", dir: "", th: 5, from: "", to: "" };   // 일별 표: 기간, 등락 고르기
  const MAIN_DISC = new Set(["A", "B", "C", "I", "K"]);       // 정기(실적)·주요사항·발행·거래소 공시·거래소 조치
  const REC_RANGES = [["1w", "1주", 5], ["1m", "1달", 22], ["3m", "3달", 66], ["1y", "1년", 250], ["all", "전체", 1e9]];
  // 기간 지정 (야후 파이낸스 과거 데이터 'Time Period'의 시작·끝 날짜, Investing.com 과거 데이터 날짜 고르기 참고)
  const spanRows = (daily, s) => (s.range === "custom"
    ? daily.filter((x) => x.date >= s.from && x.date <= s.to)
    : daily.slice(-(REC_RANGES.find((r) => r[0] === s.range) || REC_RANGES[REC_RANGES.length - 1])[2]));
  const periodHtml = (daily, s, attr) => `<div class="rec-period"${s.range === "custom" ? "" : " hidden"}>
      <input type="date" ${attr}="from" aria-label="시작 날짜" value="${esc(s.from)}" min="${esc(daily[0].date)}" max="${esc(daily[daily.length - 1].date)}">
      <span class="muted">~</span>
      <input type="date" ${attr}="to" aria-label="끝 날짜" value="${esc(s.to)}" min="${esc(daily[0].date)}" max="${esc(daily[daily.length - 1].date)}"></div>`;
  function startPeriod(daily, s, prev, box, attr) {   // 기간 지정을 처음 누르면 보던 기간으로 채움
    if (!s.from || !s.to) { const r = prev.length ? prev : daily.slice(-22); s.from = r[0].date; s.to = r[r.length - 1].date; }
    box.querySelectorAll(`[${attr}]`).forEach((i) => { i.value = s[i.getAttribute(attr)]; });
  }
  function setPeriod(s, input, attr) {
    if (!input.value) return false;
    s[input.getAttribute(attr)] = input.value;
    if (s.from > s.to) [s.from, s.to] = [s.to, s.from];
    input.parentElement.querySelectorAll(`[${attr}]`).forEach((i) => { i.value = s[i.getAttribute(attr)]; });
    return true;
  }
  async function viewCalendar() {
    const box = $("#view");
    box.innerHTML = `<p class="loading">불러오는 중…</p>`;
    renderRules();
    let daily, arts, discs;
    try {
      [daily, arts, discs] = await Promise.all([
        api("/api/daily").then(keepDaily),
        api("/api/articles").then((d) => d.items || []).catch(() => []),
        api("/api/disclosures").then((d) => d.items || []).catch(() => []),
      ]);
    } catch (e) { box.innerHTML = `<div class="empty"><strong>시세 자료를 불러오지 못했습니다.</strong>${esc(e.message)} 다음 자동 수집 뒤에 다시 열어 보세요.</div>`; return; }
    if (!$("#view")) return;
    if (!daily.length) { box.innerHTML = `<div class="empty"><strong>아직 일별 시세가 없습니다.</strong></div>`; return; }
    // 오늘 일봉은 장 마감 뒤(15:50)에야 쌓이므로, 장중에는 실시간 1분봉으로 오늘 줄을 만들어 붙임 → 1일 탭 기본값이 오늘
    try {
      const lv = await api("/api/intraday"), lb = (lv && lv.bars) || [], last = daily[daily.length - 1];
      if (lv && lv.date > last.date && lb.length) {
        const close = lb[lb.length - 1].c, kc = lv.kosdaq && lv.kosdaq.length ? lv.kosdaq[lv.kosdaq.length - 1].c : null;
        const base = lv.prev_close || last.close;
        daily.push({ date: lv.date, close, open: lb[0].o, high: Math.max(...lb.map((b) => b.h)), low: Math.min(...lb.map((b) => b.l)),
          volume: lb.reduce((s, b) => s + (b.v || 0), 0), change: base ? close / base - 1 : null,
          kosdaq: kc && last.kosdaq_close ? kc / last.kosdaq_close - 1 : null, kosdaq_close: kc,
          foreign: null, organ: null, individual: null, foreign_ratio: null, industries: lv.industries || null,
          foreign_est: lv.foreign_est || null, industry_info: lv.industry_info || null, traders: lv.traders_live || null, peers: lv.peers_live || null, live: true });
        DAY_ROWS[lv.date] = daily[daily.length - 1];
      } else if (lv && lv.date === last.date && lv.foreign_est && last.individual == null && last.foreign == null && last.organ == null) {
        last.foreign_est = lv.foreign_est;       // 장 마감 뒤 일봉은 생겼지만 거래소 개인·외국인·기관 확정치는 아직 → 장중 추정을 이어서 보여 줌
      }
    } catch (_) {}
    const byDay =(list, dayOf) => { const m = {}; list.forEach((x) => { const d = dayOf(x); if (d) (m[d] ||= []).push(x); }); return m; };
    const price = Object.fromEntries(daily.map((x) => [x.date, x]));
    const artDay = byDay(arts.filter((a) => !a.hidden && a.category !== "단순 언급" && a.ts), (a) => a.ts.slice(0, 10));
    const discDay = byDay(discs.filter((x) => x.ts && MAIN_DISC.has(x.pblntf || "")), (x) => x.ts.slice(0, 10));
    const latest = daily[daily.length - 1].date;
    if (!calState.sel || !price[calState.sel]) calState.sel = latest;
    const onNews = (ds) => { pick(ds); const el = $("#rec-day"); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); };
    let events = [];
    const fillDay = () => { fillToday(daily, arts, discs, events, onNews, calState.sel); const g = $("#rec-bento"); if (g) $(".b-seg", g).innerHTML = segHtml(); drawHeadSide(); };
    api("/api/events").then((d) => { events = d.events || []; if ($("#view")) fillDay(); }).catch(() => {});
    const isPr = (a) => a.kind === "pr" || a.kind === "company";
    const news = (ds) => {
      const ar = artDay[ds] || [];
      return { pr: ar.filter(isPr).length, ext: ar.filter((a) => !isPr(a)).length, disc: (discDay[ds] || []).length };
    };
    const inRange = () => spanRows(daily, calState);

    let chart = null, loadSeq = 0, coarseAll = null;
    const st = {};
    function shell() {
      box.innerHTML = `<div class="bento" id="rec-bento"><section class="panel rec-card">
          <div class="rec-tabs" role="tablist">${[["1d", "1일"], ...REC_RANGES, ["custom", "기간 지정"]].map(([v, t]) => `<button type="button" data-range="${v}" aria-pressed="${calState.range === v}">${t}</button>`).join("")}</div>
          ${periodHtml(daily, calState, "data-period")}
          <div id="rec-head"></div>
          <div class="rec-main"><div class="rec-left"><div id="rec-chart"></div>
            <div class="rec-legend"><span><i class="mk pr"></i>그린광학 보도자료</span><span><i class="mk disc"></i>주요 공시</span><span><i class="mk ext"></i>외부 기사</span></div></div>
            <aside class="rec-side" id="rec-side"></aside></div>
        </section><div class="tcard b-seg">${segHtml()}</div></div>
        <section id="rec-day"></section>`;
      // 위 카드 줄의 소식 카드를 격자 안으로 옮김 (값은 계속 같은 카드에서 갱신됨).
      // 가격 칸은 차트 칸 머리로, KOSDAQ은 머리 오른쪽으로, 거래량 칸은 오른쪽 '주요 숫자'로 옮겨 숨긴 위 줄에 둠 (급등·급락 칸은 머리의 표시로 대신)
      const grid = $("#rec-bento", box), seg = $(".b-seg", grid);
      const [, nc] = TODAY_CARDS.map((id) => document.getElementById(id));
      grid.insertBefore(nc, seg);
      $("#hero").hidden = true;
      const side = $("#rec-side", grid);
      if (window.ResizeObserver) {         // 오른쪽 숫자 칸 높이가 바뀌면 종가 선 차트 높이를 다시 맞춤
        let t = 0, lastH = 0;
        new ResizeObserver(() => {
          const h = Math.round(side.getBoundingClientRect().height);
          if (h === lastH) return; lastH = h;
          clearTimeout(t); t = setTimeout(() => { if (!grid.isConnected) return; if (calState.range !== "1d") lineChart(); else fitDayChart(); }, 150);
        }).observe(side);
      }
    }

    // 차트 칸 머리: 고른 날 (왼쪽 날짜·기준가→종가·장중 최고/최저, 오른쪽 종목 이름 위 / 가격·등락 크게). 네이버 기준
    function headHtml() {
      const ds = calState.sel, px = price[ds];
      if (!px) return "";
      const live = ds === latest ? liveFor(ds) : null;
      const close = live ? live.price : px.close, chg = live ? live.chg : px.change, kq = px.kosdaq;
      const base = px.base || (live && STATUS.quote.change != null ? STATUS.quote.price - STATUS.quote.change : null);
      const ev = events.filter((e) => isDaily(e) && e.start_ts.slice(0, 10) === ds && e.tier !== "일일").slice(-1)[0] || null;
      const done = !(live && live.open);
      const tag = ds === latest ? `<span class="day-now">${live ? `${live.at} ${live.open ? "현재" : live.ext ? "시간외 포함" : "정규장 마감"}` : "장 마감"}</span>` : "";
      const r = (v) => (base && v ? pct(v / base - 1) : "-");
      const ext = live ? live.ext : false;
      const hi = (live && STATUS.quote.high) || px.high, lo = (live && STATUS.quote.low) || px.low;     // 오늘은 네이버 현재가의 고가·저가
      return `<button type="button" class="rh-box is-click" data-head title="${ev ? "이날 급등·급락 기록 자세히" : done ? "이날 하루 분석" : "지금까지 하루 움직임 분석"}">
        <span class="rh-l"><span class="rh-d">${dayLabel(ds)}${tag}${ev ? `<span class="chip ${ev.direction}">${kindOf(ev)}</span>${tierTag(ev)}` : ""}</span>
          <span class="rh-s">${base ? `기준가 ${won(base)}원 → ` : ""}${done ? ext ? "시간외 포함 최종가 " : "종가 " : ""}${won(close)}원 · 장중 최고 <span class="${sgnCls(hi && base ? hi - base : 0)}">${r(hi)}</span> / 최저 <span class="${sgnCls(lo && base ? lo - base : 0)}">${r(lo)}</span></span></span>
        <span class="rh-r"><span class="rh-nm"><b>${esc(STATUS.name || "")}</b>${esc(STATUS.symbol || "")}</span>
          <span class="rh-p">${won(close)}<small>원</small><b class="${sgnCls(chg)}">${chg == null ? "-" : pct(chg)}</b></span></span>
        <span class="rh-kq"><small>KOSDAQ${ext ? " 정규장" : ""}</small><b class="${sgnCls(kq)}">${kq == null ? "-" : pct(kq)}</b>${kq != null && chg != null ? `<small>종목이 시장보다 ${pct(chg - kq).replace("%", "%p")}${ext ? " (종목은 시간외 포함)" : ""}</small>` : ""}</span>
      </button>`;
    }
    // 오른쪽 '주요 숫자' (네이버 종목 화면처럼). 고른 날의 기록 + 네이버 '지금' 숫자(현재 기준)
    // ⓘ에 마우스를 올리거나(폰은 누르면) 설명 (네이버 종목 화면 설명 그대로)
    const TIPS = {
      PER: ["주가가 주당순이익(EPS)의 몇 배인지 나타내는 지표입니다.", "· PER = 현재가 ÷ EPS"],
      EPS: ["1주당 벌어들인 순이익입니다.", "지배기업귀속 최근 4분기 합산 순이익을 수정평균발행주식수로 나눈 값이며, 보통주와 우선주를 합산해서 계산합니다."],
      PBR: ["주가가 주당순자산(BPS)의 몇 배인지 나타내는 지표입니다.", "· PBR = 현재가 ÷ BPS"],
      BPS: ["1주당 순자산(자본)입니다.", "최근 분기 자본총계를 수정기말유통주식수로 나눈 값이며, 보통주와 우선주를 합산해 계산합니다."],
    };
    const tip = (title, lines) => `<span class="tip" tabindex="0" aria-label="${esc(title)} 설명">ⓘ<span class="tip-box" role="tooltip">${title ? `<b>${esc(title)}</b>` : ""}${lines.map((l) => `<span>${esc(l)}</span>`).join("")}</span></span>`;
    const tipK = (k) => `${k} ${tip(k, TIPS[k])}`;
    function sideHtml() {
      const ds = calState.sel, px = price[ds], i = daily.findIndex((x) => x.date === ds);
      if (!px) return "";
      const isLatest = ds === latest, q = STATUS && STATUS.quote, live = isLatest ? liveFor(ds) : null;
      const nv = (STATUS && STATUS.naver) || {}, info = nv.info || {};
      const from = new Date(new Date(ds).getTime() - 365 * 864e5).toISOString().slice(0, 10);
      const yr = daily.filter((x) => x.date > from && x.date <= ds);
      const hi52 = Math.max(...yr.map((x) => x.high || x.close)), lo52 = Math.min(...yr.map((x) => x.low || x.close));
      const close = live ? live.price : px.close, pos = hi52 > lo52 ? Math.min(1, Math.max(0, (close - lo52) / (hi52 - lo52))) : 0;
      const full = daily[0].date <= from;
      // 오늘(장중·장 마감 직후)은 일봉이 아직 없어 기준가가 비므로 네이버 현재가에서 (현재가 − 전일 대비)
      const lq = live && q && q.price != null ? q : null;
      const base = px.base || (lq && lq.change != null ? lq.price - lq.change : null);
      const colored = (v) => `<span class="${base && v ? sgnCls(v - base) : ""}">${v ? won(v) : "-"}</span>`;
      // 시가·고가·저가·거래량·대금: 오늘은 네이버 현재가 한 번 받은 값으로 모두 맞춤 (분봉 합계·10분마다 받는 숫자와 섞이지 않게)
      const vol = lq && lq.volume != null ? lq.volume : px.volume;
      const op = (lq && lq.open) || px.open, hi = (lq && lq.high) || px.high, lo = (lq && lq.low) || px.low;
      const value = lq && lq.value_text ? lq.value_text : info.accumulatedTradingValue || "-";
      const kv = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
      const signed = (v) => (v == null ? "-" : `<span class="${sgnCls(v)}">${v > 0 ? "+" : v < 0 ? "−" : ""}${int(Math.abs(v))}</span>`);
      // 수익률: 네이버와 같은 방식 (고른 날 종가 ÷ N개월 전 날짜 이후 첫 거래일 종가, 기록이 짧으면 첫 거래일)
      const back = (m) => {
        const t = new Date(ds); t.setMonth(t.getMonth() - m);
        const k = t.toISOString().slice(0, 10), b = daily.find((x) => x.date >= k) || daily[0];
        return b && b.date < ds ? close / b.close - 1 : null;
      };
      const ret = (m, label) => { const v = back(m); return `<div><small>${label}</small><b class="${sgnCls(v)}">${v == null ? "-" : pct(v)}</b></div>`; };
      const inv = px.individual != null || px.foreign != null || px.organ != null;
      // 장중: 거래소 개인·외국인·기관은 장 마감 뒤에만 나오므로, 외국인 자리에 증권사 외국계 창구 추정(매수 − 매도)
      const fe = !inv && px.foreign_est && px.foreign_est.date === ds && px.foreign_est.buy != null ? px.foreign_est : null;
      const now = (k) => info[k] || "-";
      // 시총·PER·PBR은 가격 따라 바뀌므로 가격과 같은 순간 값으로: 시총은 현재가 응답, PER·PBR = 현재가 ÷ EPS·BPS (네이버 정의 그대로)
      const numOf = (s) => { const v = parseFloat(String(s || "").replace(/[^0-9.\-]/g, "")); return isFinite(v) ? v : null; };
      const capTxt = (v) => { const e = Math.round(v / 1e8), jo = Math.floor(e / 1e4), r = e % 1e4;
        return jo ? `${jo}조${r ? ` ${r.toLocaleString("ko-KR")}억` : ""}` : `${r.toLocaleString("ko-KR")}억`; };
      const ratio = (k, per) => { const d = numOf(info[per]); return lq && d > 0 ? `${(lq.price / d).toFixed(2)}배` : now(k); };
      const cap = lq && lq.market_cap ? capTxt(lq.market_cap) : now("marketValue");
      const asOf = lq ? hhmm(lq.traded_at.slice(0, 16)) : nv.fetched_at ? hhmm(nv.fetched_at) : "";
      return `<h4>${mdOf(ds)} 주요 숫자 ${tip("", ["시/고/저, 거래량/거래대금, 52주 최고/최저는 KRX+NXT 통합 기준으로 제공됩니다."])}</h4>
        <div class="rs-52"><div class="rs-bar"><i style="left:${(pos * 100).toFixed(1)}%"></i></div>
          <div class="rs-52l"><span>${full ? "52주" : "기록 중"} 최저 <b>${won(lo52)}</b></span><span>최고 <b>${won(hi52)}</b></span></div></div>
        <dl class="rs-kv">${kv("기준가", base ? won(base) : "-")}${kv("시가", colored(op))}${kv("고가", colored(hi))}${kv("저가", colored(lo))}
          ${kv("거래량", `${int(vol)}주`)}${isLatest ? kv("대금", value) : ""}</dl>
        <div class="rs-inv">${inv ? `<div><small>개인</small><b>${signed(px.individual)}</b></div><div><small>외국인</small><b>${signed(px.foreign)}</b></div><div><small>기관</small><b>${signed(px.organ)}</b></div>`
          : fe ? `<div><small>개인</small><b class="muted">-</b></div><div><small>외국인 <em class="muted">추정</em> ${tip("", [`${fe.ts ? `${hhmm(fe.ts)} 기준 ` : ""}외국계 창구 추정입니다.`, "개인·기관과 거래소 확정치는 장 마감 뒤에 나옵니다."])}</small><b>${signed(fe.buy - fe.sell)}</b></div><div><small>기관</small><b class="muted">-</b></div>`
          : `<p class="muted">개인·외국인·기관은 장 마감 뒤 집계</p>`}</div>
        <div class="rs-ret">${ret(1, "1개월")}${ret(3, "3개월")}${ret(12, "1년")}</div>
        ${nv.info ? `<div class="rs-now"><span class="rs-now-h">현재 기준 <small>네이버 ${asOf}</small></span>
          <dl class="rs-kv">${kv("시총", cap)}${kv(tipK("PER"), ratio("per", "eps"))}${kv(tipK("PBR"), ratio("pbr", "bps"))}${kv(tipK("EPS"), now("eps"))}${kv(tipK("BPS"), now("bps"))}</dl></div>` : ""}`;
    }
    function drawHeadSide() {
      const h = $("#rec-head"), s = $("#rec-side");
      if (h) h.innerHTML = headHtml();
      if (s) s.innerHTML = sideHtml();
    }
    // 머리 칸을 누르면: 하루 급등·급락 기록이 있으면 그 기록, 없으면 장 마감 뒤 자동 하루 분석 (없으면 ChatGPT 묻기)
    async function openHead() {
      const ds = calState.sel, i = daily.findIndex((x) => x.date === ds);
      const ev = events.filter((e) => isDaily(e) && e.start_ts.slice(0, 10) === ds && e.tier !== "일일").slice(-1)[0];
      if (ev) { openEvent(ev.id); return; }
      let d = null;
      try { d = await api(`/api/intraday?date=${ds}`); } catch (_) { d = null; }
      if (!d || d.date !== ds) d = { date: ds, bars: [], prev_close: (price[ds] || {}).base, close: (price[ds] || {}).close };
      openDay(d, arts, i > 0 ? daily[i - 1].date : null, price[ds]);
    }

    // 아래 작은 칸: 고른 날 사업 분야
    const selRow = () => { const i = daily.findIndex((x) => x.date === calState.sel); return i >= 0 ? i : daily.length - 1; };
    const mdOf = (d) => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`;
    // 사업 분야: 네이버 업종(WICS) 고른 날 등락률 (2026-10-26부터 기록, 그 전 날은 구성 종목으로 다시 계산한 값)
    const INDUSTRY_LABELS = [["우주항공과국방", "우주항공·국방"], ["반도체와반도체장비", "반도체"], ["디스플레이장비및부품", "디스플레이 장비·부품"], ["디스플레이패널", "디스플레이 패널"]];
    function segHtml() {
      const x = daily[selRow()], ind = x.industries || {};
      return `<span class="tl">${mdOf(x.date)} 업종 등락 <small class="muted">네이버 업종(WICS)${x.industries_calc ? " · 구성 종목으로 다시 계산" : ""}</small></span>
          <span class="seg-chips">${INDUSTRY_LABELS.map(([k, label]) =>
            `<button type="button" class="seg-chip" data-ind="${esc(k)}" title="누르면 이 업종의 그날 오른·내린 종목">${label} <b class="${ind[k] == null ? "" : sgnCls(ind[k])}">${ind[k] == null ? "-" : pct(ind[k])}</b></button>`).join("")}</span>`;
    }
    // 업종 칸을 누르면: 그날 등락률, 오른·내린 종목 수, 많이 오른·내린 종목 5개씩
    function openIndustry(k) {
      const x = daily[selRow()], label = (INDUSTRY_LABELS.find(([n]) => n === k) || [k, k])[1];
      const rate = (x.industries || {})[k], info = (x.industry_info || {})[k];
      const list = (xs) => xs && xs.length ? `<ul class="ind-list">${xs.map((s) => `<li><span>${esc(s.name)}</span><b class="${sgnCls(s.change)}">${pct(s.change)}</b></li>`).join("")}</ul>` : `<p class="muted">-</p>`;
      $("#ind-title").textContent = `${mdOf(x.date)} ${label}`;
      $("#ind-body").innerHTML = rate == null && !info
        ? `<p class="note">이 날은 업종 기록이 없습니다.</p>`
        : `<p class="ind-rate"><b class="${rate == null ? "" : sgnCls(rate)}">${rate == null ? "-" : pct(rate)}</b>
             <span class="muted">네이버 업종(WICS) '${esc(k)}'${x.live ? " · 장중 값" : ""}</span></p>
           ${x.industries_calc ? `<p class="note">네이버는 업종 등락률의 지난 기록을 주지 않아(2026-10-26부터 직접 기록), 이 날은 지금의 구성 종목 등락률(기준가 대비)을 기준가 시가총액으로 가중 평균해 다시 계산했습니다. 네이버 값과 0.1~0.2%p 안팎 다를 수 있고, 그 사이 편입·편출된 종목은 반영되지 않습니다.</p>` : ""}
           ${info ? `<p class="muted">오른 종목 ${int(info.rise)} · 내린 종목 ${int(info.fall)} · 전체 ${int(info.total)}</p>
           <div class="ind-cols"><div><h3 class="d-sec">많이 오른 종목</h3>${list(info.up)}</div><div><h3 class="d-sec">많이 내린 종목</h3>${list(info.down)}</div></div>` : ""}`;
      $("#ind-modal").hidden = false;
    }

    // 일별 종가 선 (기간 탭), 선 위에 그날 소식 점, 마우스로 훑으면 그날 숫자
    function lineChart(H = 280) {
      const el = $("#rec-chart");
      if (calState.range === "1d") return;          // 1일 탭은 위 칸에 그날 1분 차트 (dayChart)
      const rows = inRange();
      if (!rows.length) { el.innerHTML = `<p class="muted day-nochart">고른 기간에 거래일이 없습니다.</p>`; return; }
      const W = Math.max(320, Math.round(el.clientWidth || 760)), L = 8, R = 62, T = 16, B = 26;
      const cs = rows.map((x) => x.close), lo = Math.min(...cs), hi = Math.max(...cs), pad = (hi - lo) * 0.08 || hi * 0.02;
      const X = (i) => L + (rows.length > 1 ? (i * (W - L - R)) / (rows.length - 1) : (W - L - R) / 2);
      const Y = (v) => T + (H - T - B) * (1 - (v - (lo - pad)) / (hi - lo + 2 * pad));
      const first = rows.length > 1 && rows[0].change != null ? rows[0].close / (1 + rows[0].change) : rows[0].close;
      const up = rows[rows.length - 1].close >= first;
      const path = rows.map((x, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(x.close).toFixed(1)}`).join("");
      const area = `${path}L${X(rows.length - 1).toFixed(1)},${H - B}L${X(0).toFixed(1)},${H - B}Z`;   // 선 아래 옅은 채우기
      const every = Math.max(1, Math.ceil(rows.length / 6));
      const marks = rows.map((x, i) => {
        const n = news(x.date), cx = X(i), cy = Y(x.close);
        // 선(빨강·파랑)과 겹치지 않는 색·모양: 보도자료 파랑 ●, 공시 주황 ■, 외부 기사 회색 ▲ (흰 테두리로 선과 뗌)
        if (n.pr) return `<circle class="rc-mk pr" data-i="${i}" cx="${cx}" cy="${cy}" r="7"/>`;
        if (n.disc) return `<rect class="rc-mk disc" data-i="${i}" x="${cx - 6}" y="${cy - 6}" width="12" height="12" rx="1.5"/>`;
        if (n.ext) return `<path class="rc-mk ext" data-i="${i}" d="M${cx},${cy - 8}L${cx + 7.5},${cy + 5.5}L${cx - 7.5},${cy + 5.5}Z"/>`;
        return "";
      }).join("");
      const si = rows.findIndex((x) => x.date === calState.sel);
      const chg = rows[rows.length - 1].close / first - 1;
      el.innerHTML = `<div class="rc-head"><span class="rc-period"><span class="muted">${esc(rows[0].date)} ~ ${esc(rows[rows.length - 1].date)}</span>
          <b class="${sgnCls(chg)}">${pct(chg)}</b><span class="muted">이 기간</span></span></div>
        <div class="dl-wrap"><svg viewBox="0 0 ${W} ${H}" class="dl-svg rc-svg ${up ? "up" : "down"}" role="img" aria-label="일별 종가">
          <defs><linearGradient id="rc-fill-${up ? "up" : "down"}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="rc-f0"/><stop offset="1" class="rc-f1"/></linearGradient></defs>
          ${[lo, (lo + hi) / 2, hi].map((v) => `<line class="dl-grid" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text class="dl-ax" x="${W - R + 6}" y="${Y(v) + 4}">${won(v)}</text>`).join("")}
          ${rows.map((x, i) => (i % every === 0) ? `<text class="dl-ax" x="${X(i)}" y="${H - 8}" text-anchor="middle">${esc(x.date.slice(5).replace("-", "."))}</text>` : "").join("")}
          ${si >= 0 ? `<line class="rc-sel" x1="${X(si)}" x2="${X(si)}" y1="${T}" y2="${H - B}"/>` : ""}
          <path class="rc-area" d="${area}" fill="url(#rc-fill-${up ? "up" : "down"})"/>
          <path class="rc-line ${up ? "up" : "down"}" d="${path}"/>
          ${marks}
          <line class="dl-cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>
          <line class="rc-hline" x1="${L}" x2="${W - R}" y1="0" y2="0" visibility="hidden"/>
          <circle class="rc-halo" r="11" cx="-20" cy="-20"/>
          <circle class="rc-dot" r="4" cx="-10" cy="-10"/>
          <g class="rc-chip rc-xchip" visibility="hidden"><rect rx="5" height="20"/><text y="14" text-anchor="middle"></text></g>
          <g class="rc-chip rc-ychip" visibility="hidden"><rect rx="5" height="20" width="${R - 4}"/><text x="${(R - 4) / 2}" y="14" text-anchor="middle"></text></g>
          ${rows.map((x, i) => `<rect class="dl-hit" data-i="${i}" x="${X(i) - (W - L - R) / Math.max(1, rows.length - 1) / 2}" y="0" width="${Math.max(6, (W - L - R) / Math.max(1, rows.length - 1))}" height="${H}"/>`).join("")}
        </svg></div><div class="rc-info"></div>`;
      // 차트 아래 날짜 정보: 마우스를 올린 날, 아니면 고른 날
      const info = $(".rc-info", el);
      const infoFor = (x, hovering) => {
        const n = news(x.date);
        const cell = (k, v) => `<div class="ri-cell"><span class="ri-k">${k}</span><span class="ri-v">${v}</span></div>`;
        info.innerHTML = `<div class="ri-day"><span class="ri-k">${hovering ? "마우스를 올린 날" : "고른 날"}</span><b>${dayLabel(x.date)}</b></div>`
          + cell("종가", `${won(x.close)}원 <b class="${sgnCls(x.change)}">${x.change == null ? "-" : pct(x.change)}</b>`)
          + cell("KOSDAQ", `${x.kosdaq_close == null ? "" : `${x.kosdaq_close.toFixed(2)} `}<b class="${sgnCls(x.kosdaq)}">${x.kosdaq == null ? "-" : pct(x.kosdaq)}</b>`)
          + cell("거래량", `${int(x.volume)}주`)
          + cell("소식", n.pr || n.disc || n.ext ? [n.pr ? `<span><i class="mk pr"></i>보도자료 <b>${n.pr}</b></span>` : "", n.disc ? `<span><i class="mk disc"></i>공시 <b>${n.disc}</b></span>` : "", n.ext ? `<span><i class="mk ext"></i>외부기사 <b>${n.ext}</b></span>` : ""].filter(Boolean).join(" ") : `<span class="muted">없음</span>`);
      };
      const selRowOf = () => rows.find((x) => x.date === calState.sel) || rows[rows.length - 1];
      infoFor(selRowOf(), false);
      // 오른쪽 숫자 칸이 차트보다 길면 그만큼 차트를 키워 다시 그림 (한 번만, 넓은 화면에서)
      const left = el.closest(".rec-left"), side = $("#rec-side");
      if (H === 280 && left && side && side.offsetHeight && getComputedStyle(left.parentElement).gridTemplateColumns.split(" ").length === 2) {
        const spare = side.getBoundingClientRect().bottom - left.lastElementChild.getBoundingClientRect().bottom;   // 왼쪽 칸은 늘어나 있으므로 마지막 내용 기준
        if (spare > 8) return lineChart(Math.min(560, H + spare));
      }
      const svg = $(".rc-svg", el), cross = $(".dl-cross", el), dot = $(".rc-dot", el);
      const hline = $(".rc-hline", el), halo = $(".rc-halo", el), xchip = $(".rc-xchip", el), ychip = $(".rc-ychip", el);
      const chip = (g, x, y, text, w) => { const t = $("text", g), r = $("rect", g); t.textContent = text; r.setAttribute("width", w); if (g === xchip) { r.setAttribute("x", -w / 2); } g.setAttribute("transform", `translate(${x},${y})`); g.setAttribute("visibility", "visible"); };
      let hot = null;
      svg.addEventListener("mousemove", (ev) => {
        const h = ev.target.closest(".dl-hit"); if (!h) return;
        const i = +h.dataset.i, x = rows[i];
        cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
        hline.setAttribute("y1", Y(x.close)); hline.setAttribute("y2", Y(x.close)); hline.setAttribute("visibility", "visible");
        dot.setAttribute("cx", X(i)); dot.setAttribute("cy", Y(x.close));
        halo.setAttribute("cx", X(i)); halo.setAttribute("cy", Y(x.close));
        const ds = `${+x.date.slice(5, 7)}.${+x.date.slice(8, 10)}`, xw = 46;
        chip(xchip, Math.min(Math.max(X(i), L + xw / 2), W - R - xw / 2), H - B + 3, ds, xw);
        chip(ychip, W - R + 2, Y(x.close) - 10, won(x.close), R - 4);
        if (hot) hot.classList.remove("hot");
        hot = $(`.rc-mk[data-i="${i}"]`, svg); if (hot) hot.classList.add("hot");
        infoFor(x, true);
      });
      svg.addEventListener("mouseleave", () => {
        infoFor(selRowOf(), false); cross.setAttribute("visibility", "hidden"); dot.setAttribute("cx", -10);
        hline.setAttribute("visibility", "hidden"); halo.setAttribute("cx", -20); xchip.setAttribute("visibility", "hidden"); ychip.setAttribute("visibility", "hidden");
        if (hot) { hot.classList.remove("hot"); hot = null; }
      });
      svg.addEventListener("click", (ev) => { const h = ev.target.closest(".dl-hit"); if (h) pick(rows[+h.dataset.i].date); });
    }

    // 1일 탭: 그날 급등·급락 줄이 없거나 적어 왼쪽 칸이 오른쪽 '주요 숫자'보다 짧으면, 남는 만큼 1분 차트를 키워 빈칸을 없앰
    function fitDayChart() {
      const el = $("#rec-chart"), c = el && $(".rc-day .day-chart", el), left = el && el.closest(".rec-left"), side = $("#rec-side");
      if (!c || !left || !side) return;
      c.style.height = "";                                   // 기본 높이(CSS)로 되돌린 뒤 다시 잼
      if (getComputedStyle(left.parentElement).gridTemplateColumns.split(" ").length !== 2) return;   // 좁은 화면(한 줄)은 그대로
      const spare = side.getBoundingClientRect().bottom - left.lastElementChild.getBoundingClientRect().bottom;
      if (spare > 8) c.style.height = `${Math.min(600, c.offsetHeight + spare)}px`;
    }

    // 고른 날 1분 차트 (없으면 야후 5분·60분봉), 구간 골라 원인 묻기
    async function dayChart() {
      const seq = ++loadSeq, ds = calState.sel;
      const el = calState.range === "1d" ? $("#rec-chart") : $("#rec-daychart");
      if (!el) return;
      if (chart) { try { chart.remove(); } catch (_) {} charts = charts.filter((c) => c !== chart); chart = null; }
      el.innerHTML = `<p class="loading">불러오는 중…</p>`;
      let d = null;
      try { d = await api(`/api/intraday?date=${ds}`); } catch (_) { d = null; }   // 날짜로 받음 (장중엔 일봉 마지막 날 ≠ 실시간 분봉 날)
      if (d && d.date !== ds) d = null;
      let coarseIv = null;
      if (!d || !d.bars || !d.bars.length) {
        try {
          coarseAll = coarseAll || (await api("/api/coarse")).days || {};
          const c = coarseAll[ds];
          if (c && c.bars.length) { d = { date: ds, bars: c.bars, kosdaq: [], events: [], prev_close: c.prev_close }; coarseIv = c.interval; }
        } catch (_) {}
      }
      if (seq !== loadSeq || !el.isConnected) return;
      const i = daily.findIndex((x) => x.date === ds);
      const nav = `<div class="rc-head"><button type="button" class="btn small" data-step="-1" ${i <= 0 ? "disabled" : ""} aria-label="이전 거래일">‹ 이전 날</button>
        <button type="button" class="btn small" data-step="1" ${i >= daily.length - 1 ? "disabled" : ""} aria-label="다음 거래일">다음 날 ›</button>
        <button type="button" class="btn small" data-today ${i >= daily.length - 1 ? "disabled" : ""} title="오늘(휴장일이면 가장 최근 거래일)로">오늘</button>
        <label class="rc-jump" title="달력에서 고르거나 날짜를 넣으면 그날 1일 차트로 갑니다 (장이 안 열린 날이면 그 전 거래일)"><input type="date" aria-label="날짜로 가기" data-jump value="${esc(ds)}" min="${esc(daily[0].date)}" max="${esc(latest)}"></label>
        ${calState.jumpNote ? `<span class="muted rc-jump-note">${esc(calState.jumpNote)}</span>` : ""}
        ${coarseIv ? coarseTip(coarseIv) : ""}</div>`;
      calState.jumpNote = "";
      if (!d || !d.bars || !d.bars.length) { el.innerHTML = nav + `<p class="muted day-nochart">이날 분봉 기록이 없습니다.</p>`; return; }
      el.innerHTML = nav + `<article class="day-card rc-day">${dayCardHtml(d, [], ds === latest)}</article>`;
      const art = $(".rc-day", el);
      const closed = d.bars[d.bars.length - 1].t.slice(11) >= "15:30";
      chart = priceVolumeChart($(".day-chart", art), $(".day-legend", art), d.bars, {
        events: d.events.filter((e) => !isDaily(e)), prevClose: d.prev_close, interactive: false, onEvent: (id) => openEvent(id),
        news: [...(artDay[ds] || []).filter((a) => !a.untimed).map((a) => ({ ts: a.ts, k: isPr(a) ? "pr" : "ext", title: a.title, id: a.id })),
          ...(discDay[ds] || []).filter((x) => !x.untimed).map((x) => ({ ts: x.ts, k: "disc", title: x.title, id: x.id }))],
        onNews: (id) => { artState.day = ds; location.hash = `#/pr/${id}`; },
        extendTo: closed || coarseIv ? null : `${d.date}T15:30`, barMinutes: coarseIv || 1,
      });
      st.range = null;
      if (!coarseIv) attachRange(art, chart, d, st, arts, i > 0 ? daily[i - 1].date : null);
      const dayAsk = $("[data-day-ask]", art);
      if (dayAsk) dayAsk.onclick = () => openDay(d, arts, i > 0 ? daily[i - 1].date : null, price[ds]);
      requestAnimationFrame(fitDayChart);
    }

    // 고른 날 주요 숫자 (토스 '1일 범위·52주 범위'·투자자별, 네이버 시가·고가·저가·거래량 칸 참고)
    function keyStats(ds, px) {
      const from = new Date(new Date(ds).getTime() - 365 * 864e5).toISOString().slice(0, 10);
      const yr = daily.filter((x) => x.date > from && x.date <= ds);
      const hi52 = Math.max(...yr.map((x) => x.high || x.close)), lo52 = Math.min(...yr.map((x) => x.low || x.close));
      const full = daily[0].date <= from;
      const signed = (v) => (v == null ? "-" : `<span class="${sgnCls(v)}">${v > 0 ? "+" : ""}${int(v)}</span>`);
      const cell = (k, v) => `<div><dt>${k}</dt><dd class="num">${v}</dd></div>`;
      return `<dl class="key-stats">
        ${cell("시가", won(px.open))}${cell("고가", won(px.high))}${cell("저가", won(px.low))}${cell("종가", won(px.close))}
        ${cell("거래량", `${int(px.volume)}주`)}
        ${cell(full ? "52주 최고" : "기록 중 최고", won(hi52))}${cell(full ? "52주 최저" : "기록 중 최저", won(lo52))}
        ${px.foreign != null || px.organ != null || px.individual != null ? cell("개인·외국인·기관", `${signed(px.individual)} · ${signed(px.foreign)} · ${signed(px.organ)}`) : ""}
      </dl>`;
    }

    function dayPanel() {
      const ds = calState.sel, px = price[ds], ar = artDay[ds] || [], dc = discDay[ds] || [];
      const li = (href, meta, title) => `<li><span class="muted">${meta}</span> <a href="${href}">${esc(title)}</a></li>`;
      $("#rec-day").innerHTML = calState.range === "1d" ? "" : `<section class="panel rec-daycard">
        <div class="rec-day-head"><h3>${dayLabel(ds)} <small class="muted">1분 차트</small></h3>
          ${px ? `<b class="num ${sgnCls(px.change)}">${px.change == null ? "-" : pct(px.change)}</b><span class="muted">KOSDAQ ${px.kosdaq == null ? "-" : pct(px.kosdaq)}</span>` : ""}
</div>
        <div id="rec-daychart" class="rec-daychart"></div>
</section>`;
    }

    function render() { shell(); fillDay(); lineChart(); dayPanel(); dayChart(); }
    function pick(ds) {
      if (!price[ds]) return;
      calState.sel = ds;
      fillDay(); lineChart(); dayPanel(); dayChart();
    }
    render();
    box.onclick = async (ev) => {
      if (ev.target.closest("[data-head].is-click")) { openHead(); return; }
      const ib = ev.target.closest("[data-ind]");
      if (ib) { openIndustry(ib.dataset.ind); return; }
      const rb = ev.target.closest("[data-range]");
      if (rb) {
        const was = calState.range, prev = was === "1d" ? [] : inRange(); calState.range = rb.dataset.range;
        box.querySelectorAll("[data-range]").forEach((b) => b.setAttribute("aria-pressed", String(b === rb)));
        $(".rec-period", box).hidden = calState.range !== "custom";
        if (calState.range === "custom") startPeriod(daily, calState, prev, box, "data-period");
        if (calState.range === "1d") { calState.sel = latest; fillDay(); }
        if (calState.range === "1d" || was === "1d") { if (calState.range !== "1d" && chart) { try { chart.remove(); } catch (_) {} chart = null; } lineChart(); dayPanel(); dayChart(); }
        else lineChart();
        return;
      }
      if (ev.target.closest("[data-today]")) { pick(latest); return; }
      const step = ev.target.closest("[data-step]");
      if (step) { const i = daily.findIndex((x) => x.date === calState.sel) + +step.dataset.step; if (daily[i]) pick(daily[i].date); return; }
      const pk = ev.target.closest("[data-pick]");
      if (pk) { pick(pk.dataset.pick); return; }
      const e = ev.target.closest("[data-id]");
      if (e && e.closest(".day-card") && !e.closest(".range-panel")) { openEvent(+e.dataset.id); return; }
      const del = ev.target.closest("[data-ev-del]");
      if (del) { deleteRecord(del); return; }
    };
    box.onchange = (ev) => {
      const j = ev.target.closest("[data-jump]");
      if (j) { jumpTo(j.value); return; }
      const p = ev.target.closest("[data-period]"); if (p && setPeriod(calState, p, "data-period")) lineChart(); };
    // 날짜로 가기: 장이 열린 날이면 그날, 아니면 그 전 거래일 (기록 첫날보다 앞이면 첫날)
    function jumpTo(v) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(v || "") || v === calState.sel) return;
      let t = [...daily].reverse().find((x) => x.date <= v) || daily[0];
      const md = (x) => `${+x.slice(5, 7)}/${+x.slice(8, 10)}`;
      if (v < daily[0].date) calState.jumpNote = `기록은 ${daily[0].date.replace(/-/g, ".")}부터 → 첫날 차트`;
      else if (t.date !== v) calState.jumpNote = `${md(v)}: 장이 열리지 않은 날 → ${md(t.date)} 차트`;
      pick(t.date);
    }
    renderQuote();
    if (recResize) window.removeEventListener("resize", recResize);
    let rz = null;
    recResize = () => { clearTimeout(rz); rz = setTimeout(() => { if ($("#rec-chart")) lineChart(); }, 200); };
    window.addEventListener("resize", recResize);
    timers.push(setInterval(() => {          // 1일 탭에서 최근 거래일을 보고 있으면 30초마다 새로 (구간 원인 입력 중이면 그대로)
      const host = calState.range === "1d" ? "#rec-chart" : "#rec-daychart";
      if (calState.sel !== latest || !$(host) || $(`${host} [data-range-form]:not([hidden])`)) return;
      dayChart();
    }, 30000));
  }
  let recResize = null;

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
    const first = inR[0], last = inR[inR.length - 1], pc = d.prev_close;
    // 시작 가격 = 구간 바로 앞 봉의 종가 (차트 선과 같은 값 — 첫 봉 시가로 재면 그 1분 움직임이 빠짐).
    // 장 첫 봉부터 고르면 전일 종가 (시가에 이미 오른·내린 만큼까지, 차트 위 표시·급등락 감지와 같은 기준)
    const i0 = d.bars.indexOf(first), fromOpen = i0 === 0 && !!pc;
    const startPx = fromOpen ? pc : i0 > 0 ? d.bars[i0 - 1].c : first.o ?? first.c, endPx = last.c;
    const lo = (x) => x.l ?? x.c, hi = (x) => x.h ?? x.c;
    const low = inR.reduce((m, x) => (lo(x) < lo(m) ? x : m)), high = inR.reduce((m, x) => (hi(x) > hi(m) ? x : m));
    const kq = d.kosdaq || [];
    const kAt = (t) => { let k = null; for (const x of kq) { if (toTime(x.t) <= t) k = x; else break; } return k; };
    const k0 = kAt(a - 60) || kq[0], k1 = kAt(b);
    const vol = inR.reduce((s, x) => s + (x.v || 0), 0);
    const out = d.bars.filter((x) => { const t = toTime(x.t); return t < a || t > b; });
    const outAvg = out.length ? out.reduce((s, x) => s + (x.v || 0), 0) / out.length : null;
    return {
      from: hhmm(first.t), to: hhmm(last.t), minutes: inR.length,
      own: endPx / startPx - 1, startPx, endPx, fromOpen, startAt: fromOpen ? null : hhmm((d.bars[i0 - 1] || first).t),
      swingHi: hi(high) / startPx - 1, swingLo: lo(low) / startPx - 1, hiAt: hhmm(high.t), loAt: hhmm(low.t),
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
        <span>이 종목 <b class="num ${sgnCls(r.own)}">${pct(r.own)}</b> <span class="muted">${r.fromOpen ? "(전일 종가부터 끝까지)" : `(${r.startAt} 가격 ${won(r.startPx)}원 → ${r.to} ${won(r.endPx)}원${r.fromPrev != null ? ` · 전일 대비 ${pct(r.fromPrev)}` : ""})`}</span></span>
        <span>구간 안 최고 <b class="num ${sgnCls(r.swingHi)}">${pct(r.swingHi)}</b><span class="muted">(${r.hiAt})</span> · 최저 <b class="num ${sgnCls(r.swingLo)}">${pct(r.swingLo)}</b><span class="muted">(${r.loAt})</span></span>
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

  /** 구간 질문용 자료: 숫자·사실만 (원인 판단은 넣지 않음). whole: 그날 장 전체(하루 움직임)를 묻는 경우 */
  function rangeContext(d, r, arts, prevDay, question, whole = false) {
    const name = ($("#q-name") && $("#q-name").textContent) || "이 종목";
    const L = [];
    if (whole) {
      L.push(`아래는 '${name}' 주가의 ${d.date} 하루 움직임(전일 종가 대비 ${pct(r.fromPrev)})에 대해 내 사이트가 가진 숫자와 사실 자료야. 원인 판단은 들어 있지 않아.`);
      L.push(`웹 검색으로 ① 전날 장 마감 뒤부터 ${r.endLabel ? `${d.date} ${r.endLabel} 기준시각까지` : "이날 장 마감까지"} 나온 이 종목 공시·기사·리포트 ② 밤사이 미국 증시·환율 등 해외 변수 ③ 이 종목이 속한 업종·테마의 최근 며칠 흐름과 이날 흐름 ④ 그날 시장 전체 흐름(지수, 외국인·기관 수급)을 확인해줘.`);
      L.push("그리고 '이날 직접 방아쇠가 된 일'과 '그 전부터 쌓여 있던 배경'을 구분해서 설명해줘. 장중에 크게 꺾이거나 튄 시각이 있으면 그 시각도 짚어줘. 확인된 사실과 추정을 구분하고, 근거 기사는 링크를 달아줘. 설명되지 않는 부분은 억지로 연결하지 말고 '미확인'으로 남겨줘.");
    } else {
      L.push(`아래는 '${name}' 주가가 특정 구간에 갑자기 움직인 것에 대해 내 사이트가 가진 숫자와 사실 자료야. 원인 판단은 들어 있지 않아.`);
      L.push("이 구간의 움직임이 초점이지만, 원인을 이 시간 안에서만 찾지는 말아줘. 웹 검색으로 ① 전날 장 마감 뒤부터 이 시점까지 나온 이 종목 공시·기사·리포트 ② 밤사이 미국 증시·환율 등 해외 변수 ③ 이 종목이 속한 업종·테마의 최근 며칠 흐름과 이 구간 흐름 ④ 그날 시장 전체 흐름(지수, 외국인·기관 수급)을 확인해줘.");
      L.push("그리고 '이 구간에 직접 방아쇠가 된 일'과 '그 전부터 쌓여 있던 배경'을 구분해서 설명해줘. 확인된 사실과 추정을 구분하고, 근거 기사는 링크를 달아줘. 설명되지 않는 부분은 억지로 연결하지 말고 '미확인'으로 남겨줘.");
    }
    L.push("답의 첫 줄은 '한 줄 요약 (신뢰도 높음/중간/낮음)' 형식으로 써줘. 한국어 존댓말로.", "");
    L.push(whole ? `[하루] ${d.date} (분봉 ${r.from}~${r.to}) · ${r.fromOpen ? "전일 종가" : "시가"} ${won(r.startPx)}원 → ${r.endLabel || "종가"} ${won(r.endPx)}원 (${pct(r.endPx / r.startPx - 1)})`
      : `[구간] ${d.date} ${r.from}~${r.to} (${r.minutes}분) · 이 종목 ${pct(r.own)} (${r.fromOpen ? "전일 종가 " : ""}${won(r.startPx)}원 → ${won(r.endPx)}원)`);
    if (d.prev_close) L.push(`[전일 대비] 전일 종가 ${won(d.prev_close)}원 · ${whole ? r.endLabel || "종가" : "구간 끝"} ${pct(r.fromPrev)} · ${whole ? "장중" : "구간"} 최저 ${pct(r.low.fromPrev)}(${r.low.at}) · ${whole ? "장중" : "구간"} 최고 ${pct(r.high.fromPrev)}(${r.high.at})`);
    L.push(`[KOSDAQ ${whole ? r.endLabel && r.endLabel.includes("시간외") ? "이날 정규장 (전일 대비)" : "이날 (전일 대비)" : "같은 구간"}] ${r.kq == null ? "데이터 없음" : pct(r.kq)}`);
    L.push(`[거래량] ${whole ? "이날" : "구간"} ${int(r.vol)}주${r.volRatio ? ` · 1분당 거래량이 그날 다른 시간의 ${r.volRatio.toFixed(1)}배` : ""}`);
    const since = `${prevDay || d.date}T15:30`, until = `${d.date}T${r.to}`;
    (arts || []).filter((a) => a.category !== "단순 언급" && !a.hidden && a.ts && a.ts >= since && a.ts <= until).slice(0, 8)
      .forEach((a) => L.push(`[이 종목 기사] ${a.ts.replace("T", " ")} 「${a.title}」${a.office ? ` (${a.office})` : ""}`));
    return `${L.join("\n")}\n\n[질문] ${question}`;
  }

  /** 장 마감 뒤 하루 칸(급등·급락 기록이 없는 날)을 누르면: 장 마감 뒤 자동으로 해 둔 하루 분석을 급등·급락 상세와 같은 창으로.
   *  아직 분석이 없으면(분석 전·오래된 날) ChatGPT에게 묻는 창 */
  async function openDay(d, arts, prevDay, px) {
    openDrawer(`<p class="loading">불러오는 중…</p>`);
    let r = null;
    try { r = await api(`/api/day/${d.date}`); } catch (_) { r = null; }
    if (r && r.event && r.cause) renderEventDrawer(r);
    else openDayAsk(d, arts, prevDay, px);
  }

  /** 장 마감 뒤 하루 칸을 누르면: 그날 장 전체를 ChatGPT에게 묻는 창 (구간 묻기와 같은 자료·같은 기록 방식) */
  function openDayAsk(d, arts, prevDay, px) {
    const r = rangeStats(d, toTime(d.bars[0].t), toTime(d.bars[d.bars.length - 1].t));
    if (!r) return;
    const live = liveFor(d.date), now = live && live.open;                        // now: 장중 (지금까지의 하루)
    const fin = live ? live.price : d.close;                                        // 네이버 종가·현재가 (넥스트레이드 포함)
    if (fin) { r.endPx = fin; r.fromPrev = fin / d.prev_close - 1; }
    if (live) r.endLabel = `${live.at} ${live.ext ? "시간외 포함 최종가" : now ? "현재가" : "정규장 종가"}`;
    if (now) r.to = live.at;
    if (px && px.kosdaq != null) r.kq = px.kosdaq;                                  // KOSDAQ도 전일 대비
    if (now && STATUS.quote.volume != null) r.vol = STATUS.quote.volume;          // 장중: 네이버 현재가의 누적 거래량
    else if (px && px.volume != null) r.vol = px.volume;                           // 거래량도 일봉(거래소 집계)과 같게
    const vol = r.vol;
    openDrawer(`
      <h2 class="d-title" id="drawer-title">${dayLabel(d.date)} ${now ? `${live.at} 현재` : "하루"} <span class="${sgnCls(r.fromPrev)}">${pct(r.fromPrev)}</span></h2>
      <p class="d-sub">기준가 ${won(d.prev_close)}원 → ${r.endLabel || "일봉 종가"} ${won(r.endPx)}원. 분봉 구간 ${r.from}~${r.to}의 숫자와 그날 기사를 ChatGPT에 넘겨 원인을 묻습니다.${live && live.ext ? " KOSDAQ은 정규장 기준입니다." : ""}</p>
      <dl class="facts four">
        <div><dt>${now ? "지금 등락" : live && live.ext ? "시간외 포함 등락" : "종가 등락"}</dt><dd class="num ${sgnCls(r.fromPrev)}">${pct(r.fromPrev)}</dd></div>
        <div><dt>KOSDAQ</dt><dd class="num ${sgnCls(r.kq)}">${r.kq == null ? "-" : pct(r.kq)}</dd></div>
        <div><dt>장중 최고 · 최저</dt><dd class="num"><span class="${sgnCls(r.high.fromPrev)}">${pct(r.high.fromPrev)}</span> · <span class="${sgnCls(r.low.fromPrev)}">${pct(r.low.fromPrev)}</span></dd></div>
        <div><dt>거래량</dt><dd class="num">${int(vol)}주</dd></div>
      </dl>
      <div class="ask-row"><button class="btn primary" type="button" data-day-gpt title="숫자·사실 자료를 복사하고 ChatGPT 새 창을 엽니다">ChatGPT에게 묻기</button>
        <button class="btn editable" type="button" data-day-rec>받은 답 기록하기</button></div>
      <form class="mc-form editable" data-day-form hidden>
        <label><span>받은 답 붙여넣기</span><textarea rows="6" data-df-body placeholder="ChatGPT 답을 그대로 붙여넣으세요 (Ctrl+V)."></textarea></label>
        <label>한 줄 요약 <small class="muted">(붙여넣으면 자동으로 채워짐)</small><input type="text" maxlength="200" data-df-head></label>
        <p class="note" style="margin:0">${now ? "장 시작부터 지금까지" : "이날 장 전체"}(${r.from}~${r.to})가 이 날짜 아래 줄로 기록되고, 붙여넣은 원인이 그 줄에 붙습니다. 저장을 누르면 GitHub 창이 열리고, [Create]를 누르면 다음 자동 실행 때 반영됩니다.</p>
        <div class="ask-row"><button class="btn primary small" type="submit">기록하기</button></div>
      </form>
      <span class="form-msg" data-day-msg></span>
      <section class="evidence"><section class="d-block" data-ev-rel></section><section class="d-block"><div data-ev-flow></div></section></section>`);
    const box = $("#drawer-body"), msg = $("[data-day-msg]", box);
    fillDayEvidence(box, d.date, r.fromPrev, { rel: true, flow: true });
    $("[data-day-gpt]", box).onclick = () => openAsk(rangeContext(d, r, arts, prevDay, now ? "오늘 장 시작부터 지금까지 주가 움직임의 원인을 찾아 줘." : "이날 하루 주가 움직임의 원인을 찾아 줘.", true), msg);
    const form = $("[data-day-form]", box), body = $("[data-df-body]", box), head = $("[data-df-head]", box);
    $("[data-day-rec]", box).onclick = () => { form.hidden = !form.hidden; if (!form.hidden) body.focus(); };
    head.oninput = () => { head.dataset.touched = "1"; };
    body.oninput = () => { if (!head.dataset.touched) head.value = guessSummary(body.value).head; };
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      const b = body.value.trim();
      if (!b) { msg.className = "form-msg err"; msg.textContent = "ChatGPT 답을 붙여넣어 주세요."; return; }
      const item = { type: "analyze", date: d.date, from: r.from, to: r.to, cause_body: b, cause_headline: head.value.replace(CONF_RE, "").trim() };
      try { msg.className = "form-msg"; msg.textContent = await sendInbox(item); form.hidden = true; }
      catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
    };
  }

  /** ChatGPT 새 창에 자료를 채워 엶 (보내기는 사용자가 직접). 따로 묻고 싶은 것은 그 창에서 덧붙임 */
  function openAsk(text, msg) {
    try { navigator.clipboard.writeText(text).catch(() => {}); } catch (_) {}
    window.open(`https://chatgpt.com/?q=${encodeURIComponent(text)}`, "_blank", "noopener");
    msg.className = "form-msg";
    msg.textContent = "ChatGPT 새 창을 열었습니다. 모델을 고른 뒤 보내고, 받은 답을 복사해 여기에 붙여넣으세요. (새 창 입력칸이 비어 있으면 Ctrl+V — 자료를 복사해 두었습니다)";
  }


  function dayChange(d) {
    if (!d.prev_close || !d.bars.length) return null;
    const live = liveFor(d.date);
    return live ? live.chg : (d.close || d.bars[d.bars.length - 1].c) / d.prev_close - 1;
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
      const live = liveFor(d.date);
      const nowPx = live ? live.price : d.close || lastBar.c;          // 네이버 종가 (넥스트레이드 포함), 없으면 15:30 분봉
      const nowAt = live ? live.at : hhmm(lastBar.t);
      const chg = live ? live.chg : nowPx / d.prev_close - 1;
      const lowR = Math.min(...d.bars.map((b) => b.l ?? b.c)) / d.prev_close - 1;
      const highR = Math.max(...d.bars.map((b) => b.h ?? b.c)) / d.prev_close - 1;
      const wonT = (v) => `${Math.round(v).toLocaleString("ko-KR")}원`;
      const head = dailyEv
        ? `<span class="day-total-tags"><span class="chip ${dailyEv.direction}">${kindOf(dailyEv)}</span>${tierTag(dailyEv)}</span>`
        : "";
      const inner = `
        <div class="day-total-main">
          <span class="day-total-label">${dayLabel(d.date)}${isLatest ? `<span class="day-now">${live && live.closed ? "장 마감" : closed && !(live && live.open) ? "최근 거래일" : `${nowAt} 현재`}</span>` : ""}${head}</span>
          <span class="day-total-sub">기준가 ${wonT(d.prev_close)} → ${closed && !(live && live.open) ? "종가 " : ""}${wonT(nowPx)}${(live ? live.ext : d.close && d.close !== lastBar.c) ? " (시간외 포함)" : ""} · 장중 최고 ${pct(highR)} / 최저 ${pct(lowR)}</span>
        </div>
        <span class="day-total-val num ${sgnCls(chg)}">${pct(chg)}</span>`;
      dayTotal = dailyEv
        ? `<button type="button" class="day-total is-event" data-id="${dailyEv.id}">${inner}</button>`
        : closed && !(live && live.open)
          ? `<button type="button" class="day-total is-event" data-day-ask title="이날 하루 움직임을 ChatGPT에게 묻기">${inner}</button>`
          : `<div class="day-total">${inner}</div>`;
    }
    const row = (e) => `
        <li><button class="tl-row" type="button" data-id="${e.id}">
          <span class="tl-time num">${hhmm(e.start_ts)}<small class="tl-to">~${hhmm(e.peak_ts)}</small></span>
          <span><span class="chip ${e.direction}">${kindOf(e)}</span>${tierTag(e)}</span>
          <span class="tl-rule">${esc(compareLine(e))}</span>
          <span class="tl-val num ${sgnCls(e.peak_return_5m)}">${pct(e.peak_return_5m)}<small>${moveBasis(e)}</small></span>
        </button>${e.tier === "요청" ? `<div class="tl-actions editable"><button type="button" class="btn-link" data-ev-del="${e.id}">이 기록 삭제</button><span class="form-msg"></span></div>` : ""}</li>`;
    const chart = d.bars.length
      ? `<div class="day-legend"></div><div class="day-chart"></div><div class="range-panel"></div>`
      : `<p class="muted day-nochart">이날 1분봉 기록이 없습니다.</p>`;
    const list = rest.length
      ? `<ul class="timeline">${rest.map(row).join("")}</ul>`
      : "";
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
      <li><span>'대형' 표시</span><b>±${r.big_pct}% 이상</b></li>
      ${r.watch_pct != null ? `<li><span>'관찰' (기록만, 알림 없음)</span><b>±${r.watch_pct}% 이상</b></li>` : ""}`;
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
    if (isDaily(d.event)) await ensureDaily();
    renderEventDrawer(d);
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
  // 그날 일봉 자료 (비교 종목 등락·투자자별 순매수·장 마감 거래원). 자동 분석이 없는 날의 메인 기록 창에서 씀
  let PEERS = [], dailyP = null;
  const DAY_ROWS = {};
  function keepDaily(d) { PEERS = d.peers || PEERS; (d.days || []).forEach((x) => { DAY_ROWS[x.date] = x; }); return d.days || []; }
  const ensureDaily = () => (dailyP ||= api("/api/daily").then(keepDaily).catch(() => { dailyP = null; return []; }));
  function dayRelatedBlock(ds, own) {
    const ch = (DAY_ROWS[ds] || {}).peers;
    if (!PEERS.length) return "";
    const html = relatedBlock({ related: PEERS.map((p, i) => ({ name: p.name, source: p.source, change: ch ? ch[i] : null })), segments: [] }, own);
    return ch ? `${html}<p class="note">그날 하루 등락(전일 대비)입니다.</p>` : html.replace("이 시간대 비교 종목 시세가 없습니다.", "이날 비교 종목 시세가 아직 없습니다.");
  }
  function dayFlowBlock(ds) {
    const x = DAY_ROWS[ds] || {}, t = x.traders;
    const inv = x.foreign != null || x.organ != null || x.individual != null
      ? { foreign_net: x.foreign || 0, organ_net: x.organ || 0, individual_net: x.individual || 0 } : null;
    const hasT = t && ((t.buy || []).length || (t.sell || []).length);
    if (!inv && !hasT) return flowBlock(null);
    let html = inv ? flowBlock({ investor_day: inv }) : `<h3 class="d-sec">누가 사고 팔았나</h3><p class="note">그날 투자자별(외국인·기관·개인) 확정 순매수는 장 마감 후 추가됩니다.</p>`;
    if (hasT) {
      const max = Math.max(1, ...[...(t.buy || []), ...(t.sell || [])].map((r) => r.qty || 0));
      const row = (r, side) => `<li><span class="nm">${esc(r.name)}${r.foreign ? '<em class="fx">외국계</em>' : ""}</span>
        <span class="fbar"><i class="${side}" style="width:${((r.qty || 0) / max) * 100}%"></i></span><span class="num v">${int(r.qty || 0)}</span></li>`;
      html += `<h4 class="sub">증권사 창구 ${t.ts ? `${hhmm(t.ts)} 기준` : "장 마감 기록"} (매수·매도 상위 5, 주)</h4>
        <div class="flows"><div><h4>매수 상위</h4><ul>${(t.buy || []).map((r) => row(r, "b")).join("") || '<li class="muted">없음</li>'}</ul></div>
        <div><h4>매도 상위</h4><ul>${(t.sell || []).map((r) => row(r, "s")).join("") || '<li class="muted">없음</li>'}</ul></div></div>`;
    }
    return html;
  }
  function fillDayEvidence(box, ds, own, want) {
    const rel = $("[data-ev-rel]", box), flow = $("[data-ev-flow]", box);
    const fill = () => {
      if (!box.contains(rel)) return;
      if (want.rel && rel) rel.innerHTML = dayRelatedBlock(ds, own ?? (DAY_ROWS[ds] || {}).change);
      if (want.flow && flow) flow.innerHTML = dayFlowBlock(ds);
    };
    if (DAY_ROWS[ds] && PEERS.length) fill(); else ensureDaily().then(fill);
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

  function contributionBlock(sp) {
    if (!sp || sp.own == null) return '';
    return `<section class="d-block"><h3>모형 추정 기여분 (저장 당시)</h3><dl class="facts">${[['그린광학 실제 등락률',pct(sp.own)],['시장 추정 기여분',points(sp.market)],['업종 추정 기여분',points(sp.sector)],['종목 고유 추정분',points(sp.specific)]].map(([k,v])=>`<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl><p class="note">과거 관계에 따른 모형 추정이며 실제 상승·하락 원인을 확정하지 않습니다. 종목 고유 추정분에는 누락 요인과 모형 오차도 포함됩니다.</p></section>`;
  }

  function renderEventDrawer(d) {
    const e = d.event, c = d.cause;
    const daily = isDaily(e);
    const snapshot = daily ? dailySnapshot(e) : null;
    const live = snapshot && snapshot.live;
    const running = daily && (live ? live.open : e.status === "open");
    const mv = snapshot ? snapshot.change : e.peak_return_5m;
    const kq = snapshot ? snapshot.kosdaq : e.kosdaq_change;
    // 장중: 큰 숫자 하나 = 감지한 움직임 크기 (움직임 시작 가격 → 가장 멀리 간 가격, 알림·감지 기준과 같음). 장 시작부터 이어진 움직임이면 전일 종가가 시작 가격
    const held = !daily && e.peak_ts && e.last_ts && e.last_ts > e.peak_ts ? ` · ${hhmm(e.last_ts)}까지 유지` : "";
    const basis = moveBasis(e);
    $("#drawer-body").innerHTML = `
      <span class="chip ${e.direction}">${kindOf(e)}</span>${scopeTag(e)}${tierTag(e)}
      <h2 class="d-title" id="drawer-title">${dayLabel(e.start_ts)}${daily ? "" : ` ${hhmm(e.start_ts)}~${hhmm(e.peak_ts)}`} <span class="${sgnCls(mv)}">${pct(mv)}</span></h2>
      <p class="d-sub">${running
        ? `전일 종가 대비 ${live ? live.at + " 현재" : "지금까지"} 움직임입니다. 장 마감 뒤 종가로 확정됩니다.`
        : daily ? `기준가(전날 거래소 종가) → ${esc(snapshot.label)}${snapshot.price != null ? ` ${won(snapshot.price)}원` : ""}` : `${basis === "전일 대비" ? "전일 종가" : `${hhmm(e.start_ts)} 가격`} → ${hhmm(e.peak_ts)} 가격${held}`}</p>
      <dl class="facts${daily ? "" : " four"}">
        <div><dt>${running ? "지금 (전일 대비)" : daily ? live && live.ext ? "시간외 포함 등락" : "종가 등락" : basis}</dt><dd class="num ${sgnCls(mv)}">${pct(mv)}</dd></div>
        ${daily ? "" : `<div><dt>구간 안 변동</dt><dd class="num ${sgnCls(e.own_change)}">${e.own_change == null ? "-" : pct(e.own_change)}<small>${hhmm(e.start_ts)}~${hhmm(e.last_ts)}</small></dd></div>`}
        <div><dt>KOSDAQ ${daily ? live && live.ext ? "정규장" : "같은 날" : "같은 구간"}</dt><dd class="num">${kq == null ? '<small style="margin:0">데이터 없음</small>' : pct(kq)}</dd></div>
        <div><dt>거래대금${daily ? " (저장 당시)" : ""}</dt><dd class="num">${e.trade_value ? eok(e.trade_value) : "-"}</dd></div>
      </dl>
      ${daily ? `<p class="note">${esc(dailyEvidenceNote(e))}</p>` : ""}
      ${contributionBlock(c && c.split)}
      <section class="d-block d-conclusion">${manualBlock(e)}</section>
      ${e.tier === "요청" ? `<p class="editable" style="margin-top:12px"><button class="btn danger small" type="button" data-ev-del="${e.id}">이 기록 삭제</button><span class="form-msg"></span></p>` : ""}
      <section class="evidence">
        <section class="d-block" data-ev-rel>${relatedBlock(c, e.own_change)}</section>
        ${disclosureBlock(c)}
        <section class="d-block"><div data-ev-flow>${c && (c.flows || c.investor_day) ? flowBlock(c) : ""}</div>${followupBlock(d.followups)}</section>
      </section>`;
    // 자동 분석에 비교 종목·거래원 자료가 없으면 그날 일봉 자료로 채움
    fillDayEvidence($("#drawer-body"), e.start_ts.slice(0, 10), daily ? mv : null,
      { rel: !relatedBlock(c, e.own_change), flow: !(c && (c.flows || c.investor_day)) });
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
    L.push("시장·업종·종목 고유 추정분은 모형 추정치이며 실제 원인이 아닙니다. 기여 비중이나 주도 판정을 하지 말고, 음수 또는 실제 등락보다 큰 추정치를 그대로 제시하세요.");
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
    const snapshot = isDaily(e) ? dailySnapshot(e) : null;
    L.push(isDaily(e)
      ? `[움직임] ${day} 하루 (${prev ? md(prev) : "전일"} 기준가 → ${snapshot.label}${snapshot.price != null ? ` ${won(snapshot.price)}원` : ""}) ${pct(snapshot.change)} · KOSDAQ${snapshot.live && snapshot.live.ext ? " 정규장" : " 같은 날"} ${pct(snapshot.kosdaq)} · 거래대금(저장 당시) ${e.trade_value ? eok(e.trade_value) : "-"}${valX ? ` (${valX})` : ""}`
      : `[움직임] ${e.start_ts.replace("T", " ")}~${hhmm(e.last_ts)} ${pct(e.peak_return_5m)} · KOSDAQ ${e.kosdaq_change == null ? "-" : pct(e.kosdaq_change)} · 거래대금 ${e.trade_value ? eok(e.trade_value) : "-"}${valX ? ` · 그날 하루 거래대금 ${valX}` : ""}`);
    if (snapshot) L.push(`[분석 기준] ${dailyEvidenceNote(e)} 아래 몫 나누기·수급·비교 종목은 저장 당시 자료입니다.`);
    if (recent.length) L.push(`[직전 ${recent.length}거래일] ${recent.map((x) => `${md(x.date)} ${pct(x.own)}(KOSDAQ ${x.kosdaq == null ? "-" : pct(x.kosdaq)})`).join(", ")}`);
    const sp = c.split;
    if (sp && sp.own != null) {
      const kq = sp.kosdaq != null ? sp.kosdaq : e.kosdaq_change;
      const mk = sp.beta_market != null && kq != null ? ` (KOSDAQ ${pct(kq)} × 이 종목의 평소 시장 민감도 ${sp.beta_market.toFixed(2)})` : "";
      L.push(`[몫 나누기] 그린광학 실제 등락률: ${pct(sp.own)}; 시장 추정 기여분: ${points(sp.market)}${mk}; 업종 추정 기여분: ${points(sp.sector)}; 종목 고유 추정분: ${points(sp.specific)}`);
      const parts = (sp.factor_parts || []).slice(1).filter((p) => p.contrib != null && Math.abs(p.contrib) >= 0.0005);
      if (sp.method === "분야별 회귀") L.push(`[업종 몫 계산법] 비교 종목 평균을 그대로 더한 값이 아님. 분야별 등락에서 시장과 같이 움직인 부분을 빼고, 이 종목이 평소 그 분야와 얼마나 같이 움직였는지(직전 약 120거래일)를 곱한 합${parts.length ? ` — ${parts.map((p) => `${p.name} ${points(p.contrib)}(분야 등락 ${pct(p.factor)}${p.factor_resid != null ? `, 시장 몫 뺀 뒤 ${pct(p.factor_resid)}` : ""})`).join(", ")}` : ""}`);
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
  const artState = { cat: "main", day: null,     // day: 주가 기록의 소식 칸에서 누른 날짜 (그날 소식만)
    range: "365", from: "", to: "", mode: "all", topic: "", sort: "time" };   // 조건(목록과 오른쪽 그래프에 같이 적용)·정렬
  let artPopClose = null;  // 조건 창 바깥을 누르면 닫기
  let prHooks = null;     // 뉴스 목록이 정해 줌: 기사 그래프에 겹칠 다른 기사, 사건으로 기사 열기, 전체로 돌아가기

  function measureList(measure) {
    if (!measure || measure.method !== "event-study-v2") return "";
    return `<span class="muted">반응일 실제</span><span class="num ${sgnCls(measure.own)}">${pct(measure.own)}</span>`;
  }


  // 소식 없는 날과 비교 (README에 표시): 기사 하나는 잡음이 커도, 같은 종류 날을 모으면 차이가 보임
  function baselineHtml(b) {
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

  // 계산 방법: 화면 곳곳에 있던 설명을 한곳에 (화면에는 숫자와 그 항목에만 해당하는 사실만 남김)
  function pageMethod() {
    // 대제목마다 접고 펴기 (처음엔 모두 접힘)
    const sec = (id, title, body) => `<details class="method-sec" id="m-${id}"><summary><h2>${title}</h2></summary><div class="method-body">${body}</div></details>`;
    const toc = [["words", "용어 설명"], ["prices", "주가 기록"], ["articles", "뉴스"], ["events", "급등·급락 (주가 분석)"], ["limits", "한계"]];
    main.innerHTML = `<div class="wrap method">
      <div class="page-head"><div><h1 class="page-title">README</h1>
        <p class="page-sub">사이트의 숫자를 어떻게 계산하는지, 어떻게 읽으면 되는지 모아 둔 곳입니다.</p></div></div>
      <nav class="method-toc" aria-label="목차">${toc.map(([id, t]) => `<a href="#/method" data-to="m-${id}">${t}</a>`).join("")}</nav>
      ${sec("words", "용어 설명", `
        <dl>
          <dt>반응일</dt><dd>기사·공시가 주가에 처음 반영될 수 있는 거래일입니다. 장 마감(15:30) 뒤나 휴장일에 나왔으면 다음 거래일입니다. 같은 내용의 공시가 36시간 안에 먼저 나왔으면 공시 시각부터 잽니다.</dd>
          <dt>이 종목 · KOSDAQ · 차이</dt><dd>기사 화면과 뉴스 목록 반응의 기본 숫자입니다. 공개 직전 가격(장 시작 전 공개는 전날 종가)부터 +30분·+60분 뒤까지 그린광학과 코스닥이 각각 몇 % 움직였는지, 그리고 둘의 차이(%p)입니다. 차이는 시장 전체와 상관없이 그린광학만 따로 움직인 만큼입니다.</dd>
          <dt>고유 움직임</dt><dd>기사 화면 '통계로 보기 (참고)'에 있습니다. 실제 등락에서 시장 몫과 업종 몫(평소 관계로 추정)을 뺀 나머지입니다. 회사 소식, 수급, 그 밖의 이유가 모두 섞여 있습니다.</dd>
          <dt>평소 같은 구간 중 이보다 크게 움직인 비율</dt><dd>역시 '통계로 보기'에 있습니다. 최근 같은 길이 구간들의 고유 움직임 중 이번 이상으로 컸던 비율입니다. 3%면 평소 그런 구간 100개 중 3개꼴이라는 뜻입니다.</dd>
          <dt>평소 예상 · 예상 대비 차이</dt><dd>아래 '소식 없는 날과 비교' 표에만 씁니다. 그날 코스닥·비교 종목이 움직인 만큼 이 종목이 평소 관계대로라면 움직였을 값이 평소 예상이고, 실제 등락 − 평소 예상이 차이입니다.</dd>
        </dl>`)}
      ${sec("prices", "주가 기록", `
        <ul>
          <li><b>가격 기준</b>: 최근 거래일의 메인과 하루 상세는 같은 최신 시세를 사용합니다. 시간외 시세가 있으면 시각과 '시간외 포함 최종가'를 표시하고, KOSDAQ 정규장 값과 구분합니다. 1분 차트는 수집된 분봉 시각까지입니다. 기존 원인 글과 분석 근거는 저장 당시 자료로 남으며, 최신 수치와 다르면 상세에 표시합니다.</li>
          <li><b>차트</b>: 처음에는 1일(최근 거래일 1분 차트)이 보입니다. 기간 탭(1일·1주·1달·3달·1년·전체·기간 지정)으로 종가 선을 봅니다. 기간 지정은 시작·끝 날짜를 직접 고르며, 일별 표에도 같은 칸이 있습니다. 선 위의 점은 그날 소식(보도자료·주요 공시·외부 기사)이고, 마우스를 올리면 그날 종가·등락·KOSDAQ·소식 수가 나옵니다. 점이나 선을 누르면 그날을 고릅니다. 1일 탭은 고른 날의 1분 차트이고, 차트를 끌거나 눌러 구간을 고르면 그 구간의 원인을 물어볼 수 있습니다. 1분봉이 없는 날은 야후 파이낸스 5분봉(없으면 60분봉)으로 그리고, 마지막 점은 거래소 종가입니다.</li>
          <li><b>날마다 등락</b>: 차트 아래 한 칸이 하루입니다(빨강 오름 · 파랑 내림, 진할수록 크게). '등락 고르기'로 몇 % 이상 오르거나 내린 날만 표시하고, 전체 기간에서 맞는 날짜를 모아 보여 줍니다.</li>
          <li><b>일별 표</b>: 모든 거래일을 한 줄씩 보여 줍니다. 시가·고가·저가·종가, 등락, 거래량, KOSDAQ 등락, 외국인·기관·개인 순매수(주), 그날 그린광학 보도자료·외부 기사·주요 공시 수입니다. '엑셀로 내려받기'로 전체를 받을 수 있습니다.</li>
          <li><b>급등·급락</b>: 감지 기준(급등·급락 ±10%, 대형 ±15%, 관찰 ±5~10%)에 걸린 하루·장중 기록입니다. 달력에서 날짜를 누르면 그날 기록이 나오고, 일별 표의 '급등·급락 기록 내려받기'로 전체를 받을 수 있습니다.</li>
          <li><b>검색</b>: 맨 위 검색창에 날짜(9/8, 9월 8일, 2026-09-08)를 넣으면 그날 1일 차트로 바로 갑니다. 장이 열리지 않은 날이면 앞뒤 거래일 차트로 가는 버튼이 나옵니다. 달(4월, 2026-04)을 넣으면 그 달의 날짜·기사·공시와 '그 달 주가 차트 보기'가 나옵니다. 하루 등락률(+6.5%나 6.5%는 6.5% 이상 오른 날, -5%는 5% 이상 내린 날)로 찾으면 그날 기사·공시도 함께 나오고, 제목·매체·기자·상대 회사로도 찾습니다.</li>
        </ul>`)}
      ${sec("articles", "뉴스", `
        <h3>탭과 건수</h3>
        <ul>
          <li><b>건수</b>: '기사'는 같은 내용을 하나로 센 수, '보도'는 그 기사를 실은 매체마다 하나로 센 수입니다. 예: 6월 22일 LIG 공급계약 소식을 이데일리·뉴시스·머니투데이 3곳이 실었다면 기사 1건, 보도 3건입니다. 공시 탭은 켠 종류만 세고, 전체 공시 수를 함께 적습니다.</li>
          <li><b>그린광학 보도자료</b>: 직접 등록한 보도자료와, 제목·본문으로 '회사 발표'로 분류된 기사입니다. 목록에서 구분을 직접 고칠 수 있습니다.</li>
          <li><b>외부 기사</b>: 그 밖의 기사입니다. 이름만 나오는 기사(단순 언급)와 주가 움직임을 다룬 기사(시황·특징주)는 모으지 않습니다.</li>
          <li><b>공시</b>: DART·거래소(KIND) 공시입니다. 위 종류 버튼으로 켠 공시만 목록과 건수에 들어갑니다.</li>
          <li>목록 한 줄은 같은 내용 묶음(여러 매체가 같은 내용을 낸 것) 하나입니다. 같은 날이라도 내용이 다르면 따로 나옵니다. 한 달 평균은 최근 6개월 건수 ÷ 6입니다. 실린 매체는 같은 내용을 낸 매체 수입니다.</li>
        </ul>
        <h3>대시보드</h3>
        <ul>
          <li>숫자 카드(기사 수·언론사), 월별 기사 추이(보도자료·외부 기사 두 선), 상위 언론사·자주 쓴 기자 순위, 주제, 기사 피드로 되어 있습니다.</li>
          <li>추이의 한 달, 순위의 한 줄, 주제 타일을 누르면 그 조건으로 나머지가 함께 걸러지고, 다시 누르면 풀립니다. 예: 언론사에서 한 곳을 누르면 기자 순위에 그 언론사 기자만 남습니다. 순위의 %는 지금 조건의 기사 중 비중입니다.</li>
          <li>기자는 같은 이름이라도 언론사가 다르면 따로 셉니다. 기자명을 찾지 못한 기사는 기자 순위에서 빠집니다.</li>
          <li><b>내려받기</b>: '지금 조건 엑셀로 내려받기'는 걸러진 기사, '원본 전체 내려받기'는 조건과 관계없이 모든 기사(매체별)입니다. 엑셀에서 직접 피벗할 수 있습니다.</li>
          <li><b>세는 단위</b>: 대시보드는 '보도' 수로 셉니다. 예: 같은 소식을 이데일리·뉴시스·머니투데이 3곳이 실었다면 3건이고, 상위 언론사에서 세 곳이 각각 1건씩 올라갑니다.</li>
          <li><b>주제</b>: 제목(없으면 요약 앞부분)에 들어 있는 단어로 자동 분류합니다. 제목이나 본문에 MOU(양해각서)가 있으면 대표 주제는 MOU이고, MOU 체결만으로는 수주·계약에 넣지 않습니다. 그 밖에는 순서대로 먼저 걸린 것이 대표 주제입니다: 실적 → 수주·계약 → 자금·주식 → 정부·기관 지원 → 방산 → 우주·위성 → 반도체 → 기술·제품 → 전시·IR·행사 → 주가·시황 → 기타. 여러 주제에 걸리면 내려받은 파일의 '걸린 주제 전부'에 모두 적습니다.</li>
          <li><b>모으지 않는 기사</b>: 단순 언급·시황·특징주로 분류된 기사는 모으지 않고, 이미 모은 것도 지웠습니다. 핀포인트뉴스와 Investing.com 한국어(다른 매체 기사를 다시 싣는 곳) 기사는 모으지 않습니다. 제목과 첫머리(요약)에 회사 이름이 없고 원문 전체에도 이름이 2번 이하로만 나오는 기사(시황 기사의 종목 나열 등)는 원문을 열어 확인한 뒤 지우고 다시 모으지 않습니다. 원문을 못 열면 그대로 둡니다.</li>
          <li><b>기자</b>: 기사 원문을 한 번 열어 본문의 기자 표기(예: [매체 홍길동 기자], 홍길동 기자 이메일, ○○ 홍길동입니다)에서 읽습니다. 자동 수집 때마다 15건씩 새 기사부터 채우고, 지난 기사도 차례로 채웁니다. 구글 뉴스로 모은 기사는 연결 주소를 원래 기사 주소로 바꾼 뒤 그 원문에서 읽습니다(바꾼 주소가 '기사 열기' 링크가 됩니다). 표기를 찾지 못한 기사는 '(미확인)'입니다.</li>
          <li><b>공개 시각</b>: 구글 뉴스는 날짜만 맞고 시각을 16:00·17:00으로 주는 일이 있습니다. 원래 주소를 찾은 기사는 원문 페이지의 게시 시각(입력 시각)으로 고칩니다. 원문 시각을 읽지 못하면 '공개 시각 확인 필요'가 붙습니다.</li>
        </ul>
        <h3>소식 없는 날과 비교</h3>
        <div id="method-baseline"><p class="loading">불러오는 중…</p></div>
        <ul>
          <li>최근 2년(기사를 모으기 시작한 날 이후)의 거래일을 그날 있었던 소식으로 나눕니다: 그린광학 보도자료 날 → 주요 공시 날 → 외부 기사만 있는 날 → 소식 없는 날 (한 날이 여러 종류면 앞의 것). 주요 공시는 정기(실적)·주요사항·발행·거래소 공시와 거래소 조치입니다.</li>
          <li>각 소식은 반응일과 그다음 거래일을 함께 셉니다. 반응이 다음 날까지 이어질 수 있어서입니다.</li>
          <li>평소 관계(코스닥·비교 종목과 함께 움직이는 정도)는 <b>소식 없는 날만으로</b> 한 번 추정하고, 모든 날의 예상 대비 차이를 구합니다.</li>
          <li><b>크게 움직인 날</b>의 기준은 소식 없는 날의 차이 크기를 작은 것부터 줄 세웠을 때 상위 10%가 시작되는 값입니다. 그래서 소식 없는 날 줄은 늘 10% 안팎이고, 다른 줄을 이 10%와 견주어 봅니다. 기준값은 이 종목의 평소 출렁임에서 나오므로 자료가 쌓이면 조금씩 바뀝니다.
            <div class="method-now" id="m-cut">지금 기준값을 불러오는 중…</div></li>
          <li>기사 자체의 효과가 아니라 '소식이 있던 날'과 '없던 날'의 차이입니다. 사건(예: 계약) 자체의 영향, 같은 날의 다른 소식, 수집하지 못한 소식이 섞여 있습니다.</li>
        </ul>
        <h3>뉴스 반응 (뉴스 목록 오른쪽)</h3>
        <ul>
          <li>뉴스 목록에서 기사를 고르지 않았을 때 오른쪽은 <b>지금 목록에 보이는 기사</b>를 공개 시각 0분에 맞춰 겹쳐 본 그래프입니다. 회색 선이 기사 하나, 파란 굵은 선이 <b>중앙값</b>입니다. 크게 움직인 몇 건에 끌려가는 평균 대신 보통의 흐름을 보여 주며, 평균은 아래 숫자로 함께 적습니다.</li>
          <li>목록 위 탭(그린광학 보도자료·외부 기사·공시)과 [조건](기간·공개 시점·주제)이 목록과 그래프에 같이 걸립니다. [정렬]에서 반응 큰 순으로 볼 수 있고, [엑셀로]는 지금 목록의 +30분·+60분 값을 내려받습니다.</li>
          <li>기사를 고르면 같은 자리에 그 기사 그래프가 나오고, 지금 목록의 다른 기사(연한 회색)와 중앙값(파랑)이 +30분(또는 +60분)까지 겹칩니다. [전후 5거래일] 탭은 공개 전에 이미 움직였는지, 뒤에 이어졌는지 보는 참고용입니다.</li>
          <li><b>기간</b>(기본 최근 1년)은 반응을 재는 시간이 아니라 <b>언제 나온 기사를 모을지</b>입니다. 반응은 늘 +30분·+60분만 잽니다.</li>
          <li>기사 하나하나는 잡음이 큽니다. 같은 종류를 여러 건 모았을 때 중앙값 선이 0에서 꾸준히 벗어나는지를 보세요.</li>
          <li>그 아래 '우연 확률'은 기사가 주가에 아무 영향이 없다고(오를 확률 반, 내릴 확률 반) 쳤을 때, 오른 건과 내린 건이 지금만큼 한쪽으로 기울 확률입니다(동전 던지기와 같은 계산). 이 확률이 작을수록 우연으로 보기 어렵고, 보통 5% 밑이면 '우연만으로는 설명하기 어렵다'고 봅니다. 오르내린 방향만 세므로 움직인 크기와 같은 날 다른 소식은 따지지 않습니다.</li>
        </ul>
        <h3>기사 상세: 발표 직후 반응</h3>
        <ul>
          <li>공개 직전 가격부터 +10·30·60·120분(15:30까지)의 이 종목·KOSDAQ 등락과 차이를 표로 보여 줍니다. 그 아래 그래프는 같은 시간의 이 종목·KOSDAQ 경로입니다.</li>
          <li><b>공개 시점</b>: 장 시작 전(전날 장 마감 뒤~당일 9시)은 전날 종가부터 장 시작 뒤, 장중(9시~15시)은 공개 직전 가격부터, 15시 이후(~15:30)는 남은 거래가 짧아 그날 종가부터 다음 거래일 장 시작 뒤로 잽니다.</li>
          <li><b>봉</b>: 1분봉이 있으면 1분봉(9/23부터 네이버, 그 전은 한국투자증권으로 한 번 채움), 없으면 야후 파이낸스 5분·60분봉입니다. 지난 날짜의 코스닥은 1분봉이 없어 야후 봉으로 비교합니다('1분봉 기준 (KOSDAQ 60분봉)'처럼 표시).</li>
          <li>나오지 않는 경우: 공개 시각을 모르는 공시, 공개 무렵 봉 기록이 없는 경우.</li>
        </ul>
        <h3>기사 표시</h3>
        <ul>
          <li><b>시각 출처</b>: 기사 화면의 공개 시각 옆에 어디서 읽은 시각인지(원문 페이지·네이버 뉴스·구글 뉴스·직접 고침) 적습니다. 구글 뉴스 시각이 16:00·17:00으로 반복되면 '확인 필요'가 붙으니, 원문 시각을 확인하기 전에는 숫자를 잠정치로 보세요. '시각 고치기'로 원문의 입력 시각을 넣으면 그 시각으로 다시 계산됩니다.</li>
          <li><b>대표 기사</b>: 같은 내용을 여러 매체가 냈으면 목록에는 한 줄(대표)만 보입니다. 기사 화면 '이 기사를 실은 매체'에서 '대표로'를 누르면 목록에 보일 제목·언론사를 바꿀 수 있습니다. 반응 계산의 기준 시각은 묶음에서 가장 먼저 나온 때 그대로입니다.</li>

          <li><b>거래량 급증</b>: 반응일 거래량이 직전 60거래일 중 상위 5% 안입니다.</li>
        </ul>`)}
      ${sec("events", "급등·급락 (주가 분석)", `
        <h3>감지 기준</h3>
        <ul>
          <li>전일 종가 대비 ±5%는 관찰(사이트 기록만, 알림 없음), ±10%는 급등·급락(알림), ±15%는 대형으로 기록합니다. 장중 30분 안의 빠른 움직임도 같은 기준으로 따로 잡습니다.</li>
          <li>기준을 이렇게 정한 이유: 그린광학은 상장 뒤 하루 등락 중간값이 약 4%로, ±3%는 거래일 60% 이상, ±5%도 40% 가까이에서 일어납니다. ±10%가 상위 약 10%(한 달 2~3번)라 알림 기준으로 삼았습니다. 시장 때문이든 아니든 ±5% 넘은 날은 모두 관찰로 남습니다.</li>
          <li>같은 날 같은 방향은 한 번만 알리고, 관찰 기준(±5%) 안으로 회복했다가 다시 넘으면 한 번 더 알립니다.</li>
        </ul>
        <h3>비교 종목</h3>
        <ul>
          <li>급등·급락 창의 '이 종목 사업 분야별 비교 종목'은 네이버 업종(WICS) 네 곳의 시가총액 상위 종목입니다. '평소 연동성'은 과거 약 110~120거래일 동안 코스닥 영향을 뺀 뒤 이 종목과 그 분야가 함께 움직인 정도(상관계수)입니다.</li>
          <li>ChatGPT에게 묻기를 누르면 이 비교 종목·수급·공시·기사와 함께 시장·업종 몫 계산 결과도 자료로 넘깁니다(화면에는 싣지 않음).</li>
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
      const b = d && d.baseline, box = $("#m-cut"), table = $("#method-baseline");
      if (table) table.innerHTML = b ? baselineHtml(b) : `<p class="muted">아직 계산할 자료가 부족합니다.</p>`;
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
      const target = document.getElementById(a.dataset.to);
      target.open = true;                     // 목차로 가면 그 칸을 펼침
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  }

  const artViewSeg = (view) => { const v = view === true ? "pivot" : view || "list";      // 예전 호출: true = 대시보드
    return `<div class="seg view-seg" role="tablist" aria-label="보기"><a href="#/pr" aria-pressed="${v === "list"}">목록</a><a href="#/pivot" aria-pressed="${v === "pivot"}">대시보드</a></div>`; };

  // ---------------------------------------------------------------- 뉴스 반응
  // 기사·공시마다 공개 시각을 0분으로 맞춘 주가 경로(기준값 대비)를 한 그래프에 겹쳐 보고, 아래 표로 +30·60·120분·당일·다음날을 비교.
  // 통계 판정 없이 숫자 그대로 (같은 촘촘함의 봉끼리만 겹치도록 기본은 1분봉)
  function reactionAt(path, minute) {
    const point = (path || []).find(([t]) => t === minute);
    return point ? point[1] : null;
  }
  function reactionSamples(rows, valueOf) {
    const groups = new Map();
    for (const row of rows) {
      const value = valueOf(row);
      if (value == null || !Number.isFinite(value)) continue;
      const key = `${row.event_key || row.id}|${row.start || ""}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(value);
    }
    // Conflicting duplicate measurements must not depend on row order.
    return [...groups.values()].filter(vs => Math.max(...vs) - Math.min(...vs) < 1e-8).map(vs => vs[0]);
  }
  const reactState = { kind: "all", mode: "all", h: "30", topic: "", vsKq: false, sort: "ts", dir: -1, pick: null, range: "365", from: "", to: "" };   // 기간 기본: 최근 1년
  // 공개 시점: 장 시작 전(전날 장 마감 뒤~당일 9시, 전날 종가부터) / 장중(9시~15시, 공개 직전 가격부터) / 15시 이후(그날 종가부터 다음 거래일)
  const REACT_MODES = [["all", "전체", ""], ["open", "장 시작 전", "전날 장 마감 뒤~당일 9시 공개: 전날 종가부터 그날 9시 이후"],
    ["session", "장중", "9시~15시 공개: 공개 직전 가격부터"], ["late", "15시 이후", "15시~15시 30분 공개: 남은 거래가 짧아 그날 종가부터 다음 거래일 9시 이후"]];
  const REACT_H = [["30", "+30분"], ["60", "+60분"]];
  const MODE_TXT = { open: "장 시작부터", session: "장중 기준시각부터", late: "다음 거래일 장 시작부터" };
  function reactionBasisText(x) {
    const reason = { disclosure: "선행 공시", story: "묶음 최초 공개", publication: "기사·공시 공개" }[x.basis_reason] || "분석 기준";
    const stamp = (t) => t ? t.slice(0, 16).replace("T", " ") : "시각 미상";
    return `${reason} ${stamp(x.analysis_ts || x.ts)}${x.start ? ` · 비교 시작 ${stamp(x.start)}` : ""}${x.mode ? ` · ${MODE_TXT[x.mode] || ""}` : ""}`;
  }
const OBS_STATUS = {confirmed:'확인',estimated:'추정',unknown:'미상',complete:'관측 가능',partial:'일부 미집계',unavailable:'관측값 없음',pending:'진행 중'};
const OBS_KIND = {company_press_release:'당사 보도자료',external_article:'외부기사',disclosure:'주요 공시'};
// 이전 기록의 '외부 기업 발표'는 저장값을 바꾸지 않고 외부기사로 읽습니다.
const obsKind = (k) => k === 'external_company_announcement' ? 'external_article' : k;
const obsLink = (url,label) => /^https?:\/\//i.test(url || '') ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : `${esc(label)} (원문 주소 없음)`;
function observationQuestion(d) {
  const o=d.observation, f=o.intraday || {};
  return `그린광학 사건 관찰 기록입니다. 기사의 인과 효과를 단정하지 마세요.\n${JSON.stringify({event_id:d.event_id,title:d.title,first_public_at:d.first_public_at,time_source:d.time_source,time_status:d.time_status,pr_distributed_at:d.pr_distributed_at,analysis_at:d.analysis_at,observation:o,sources:d.members.map(x=>({title:x.title,ts:x.ts,url:x.url})),identity_status:d.identity_status,same_event_candidates:d.same_event_candidates,event_kind:d.event_kind,classification_sources:d.classification_sources,context:d.context,market_context:d.market_context,snapshot_version:d.snapshot_version},null,2)}\n확인 사실 / 가능한 배경 / 직접 방아쇠 후보 / 반대 증거 / 미확인 / 당시 판단 신뢰도(주관적 평가)를 구분해 주세요. 출처 링크와 발표 시각을 제시하고 추가 외부 기사·공시 검색이 필요한 부분을 밝혀 주세요. 수집 정보 없음은 실제 사건 없음을 뜻하지 않습니다. 일별 저장 종가의 장후 포함·기업행동 보정 여부도 확인해 주세요.`;
}
const OBS_SRC = {disclosure:'공시 접수 시각',manual:'직접 입력',page:'기사 원문',original_page:'기사 원문',naver:'네이버',google:'구글 뉴스',gnews:'구글 뉴스',unknown:'미상',calc:'자체 계산',fixture:'검증용 자료'};
const obsSrc = (s) => OBS_SRC[s] || s || '미상';
const obsStamp = (t) => t ? t.slice(0,16).replace('T',' ').replace(/-/g,'.') : '시각 미상';
// 기사 상세(뉴스 목록) 아래에 붙는 사건 관찰 기록: AI 질문·결과 저장, 관련 소식, 검토 기록, 유사 사건, 분류·연결 수정.
// 그래프와 숫자는 기사 상세가 그림. host: 접는 항목을 넣을 곳. 버튼(#obs-question, #obs-note-open)은 기사 상세에 이미 있음
async function observationExtras(host, eid, { openEvent = null } = {}) {
  if(!host)return;
  let d,index;
  try { index=await api('/api/observatory'); eid=index.aliases?.[eid]||eid;d=await api('/api/observatory/'+encodeURIComponent(eid)); }
  catch(e){host.innerHTML=`<p class="obs-muted" role="alert">사건 관찰 기록을 불러오지 못했습니다: ${esc(e.message)}</p>`;return;}
  if(!host.isConnected)return;
  const range='day';
  let revision=null,noteDraft=null,noteTab='ai_analysis',noteOpen=false,filterDraft=null,openFolds=new Set();
  const NOTE_TABS=[['manual_observation','확인 사실'],['ai_analysis','AI 분석'],['retrospective_review','사후 검토']];
  const stampText=t=>t?t.replace('T',' '):'미상';
  const render=()=>{
    if (host.querySelector('#obs-note')) noteDraft=Object.fromEntries(new FormData(host.querySelector('#obs-note')));
    if (host.querySelector('#obs-filters')) filterDraft=[...host.querySelector('#obs-filters').querySelectorAll('input,select')].map(x=>[x.id,x.type==='checkbox'?x.checked:x.value]);
    if (host.querySelector('[data-fold]')) openFolds=new Set([...host.querySelectorAll('details[data-fold][open]')].map(x=>x.dataset.fold));
    const o=d.observation,f=o.intraday||{};
    const lead=d.members[0]||{};
    const kindLabel=k=>({pr:'보도자료',company:'보도자료',disc:'공시'})[k]||'기사';
    const notesOf=k=>d.notes.filter(n=>n.kind===k);
    const noteHtml=n=>`<article class="obs-note"><p class="obs-note-head"><strong>${esc({manual_observation:'당시 관찰',ai_analysis:'당시 AI 해석',retrospective_review:'사후 확인'}[n.kind])}</strong> · ${esc(obsStamp(n.analysis_at||n.created_at))} · ${esc(n.author_type==='AI'?'AI':'직접 작성')} · ${esc(n.review_status||'')}</p><pre>${esc(n.body)}</pre><small>주관적 신뢰도 ${esc(n.confidence_label||'미기재')}${n.revision_parent_id?' · 이전 기록을 고친 새 버전':''}</small>${(n.sources||[]).length?`<p>${n.sources.map(s=>obsLink(s,s)).join(' · ')}</p>`:''}<button type="button" class="btn small" data-note-edit="${esc(n.id)}">새 버전으로 수정</button></article>`;
    const factLine=`공개 ${obsStamp(d.first_public_at)} · 그린광학 +30분 ${pct(f.steps?.['30']?.own)} / KOSDAQ +30분 ${pct(f.steps?.['30']?.kq)} (관측값, 기사 효과로 단정하지 않음)`;
    const fold=(key,title,meta,body)=>`<details class="obs-fold" data-fold="${key}"${openFolds.has(key)?' open':''}><summary><span>${title}</span><small>${meta}</small></summary><div class="obs-fold-body">${body}</div></details>`;
    const nNotes=NOTE_TABS.map(([k,t])=>`${t} ${notesOf(k).length}`).join(' · ');
    host.innerHTML=`<textarea id="obs-question-text" aria-label="AI 질문과 실제 관측 자료" readonly hidden></textarea>
    <form id="obs-note" class="obs-form" ${noteOpen?'':'hidden'}><label>기록 종류<select name="kind">${NOTE_TABS.map(([k])=>`<option value="${k}">${({manual_observation:'당시 수동 관찰',ai_analysis:'당시 AI 분석',retrospective_review:'사후 재검토'})[k]}</option>`).join('')}</select></label>
      <label>작성 주체<select name="author_type"><option value="AI">AI</option><option value="manual">직접 작성</option></select></label><label>분석 시각 (KST)<input name="analysis_at" type="datetime-local" required></label>
      <label class="wide">분석 결과 / 검토 메모<textarea name="body" required rows="6"></textarea></label><label class="wide">확인 출처 (줄마다 URL)<textarea name="sources" rows="2"></textarea></label>
      <label>주관적 신뢰도<input name="confidence_label" placeholder="높음 / 보통 / 낮음 / 미평가"></label><label>재검토 상태<select name="review_status"><option>검토 대기</option><option>재검토 완료</option><option>추가 자료 필요</option></select></label>
      <div class="obs-form-actions"><button class="btn primary">기록 암호화 저장 요청</button><button type="button" class="btn" id="obs-note-cancel">닫기</button><span id="obs-save-status" role="status"></span></div></form>
    ${fold('context','관련 소식·시장 상황',`${d.members.length+d.context.length}건`,`
      ${d.members.map(m=>`<div class="obs-item"><span class="obs-dot">${kindLabel(m.kind)}</span><time>${esc(obsStamp(m.ts))}</time><br><strong>${obsLink(m.url,m.title)}</strong><p>${esc(m.office||'')} · 시각 출처 ${esc(obsSrc(m.time_source))}${m.original_ts&&m.original_ts.slice(0,16)!==(m.ts||'').slice(0,16)?` · 원래 시각 ${esc(obsStamp(m.original_ts))}`:''}</p></div>`).join('')}
      ${d.context.map(x=>`<div class="obs-item"><span class="obs-dot muted">${x.kind==='disc'?'공시':'동시'}</span><time>${esc(obsStamp(x.ts))}</time><br><strong><a href="#/pr/${encodeURIComponent(x.id)}">${esc(x.title)}</a></strong>${x.event_id?` <button type="button" class="btn-link" data-obs-open="${esc(x.event_id)}">이 기사 보기</button>`:''}</div>`).join('')}
      ${d.context.length?'':'<p class="obs-muted">앞뒤 하루의 다른 소식: 수집 정보 없음(실제 없음과 다름)</p>'}
      <div class="obs-item"><span class="obs-dot">시장</span><strong>같은 시각 KOSDAQ</strong><p>+30분 ${pct(f.steps?.['30']?.kq)} · +60분 ${pct(f.steps?.['60']?.kq)}</p></div>
      <div class="obs-item"><span class="obs-dot">업종</span><strong>그날 업종·테마</strong>${[...(d.market_context?.industry||[]),...(d.market_context?.themes||[])].map(x=>`<p>${esc(x.name)} <b class="${sgnCls(x.change_rate)}">${pctRaw(x.change_rate)}</b> <small>${esc(obsSrc(x.updated_at))}</small></p>`).join('')||'<p>수집 정보 없음(실제 없음과 다름)</p>'}<p class="obs-muted">${esc(d.market_context?.basis||'')} 같은 시간에 겹친 것은 인과관계가 아닙니다.</p></div>`)}
    ${fold('notes','검토 기록',nNotes,`
      <div class="obs-tabs" role="tablist" aria-label="검토 기록 종류">${NOTE_TABS.map(([k,t])=>`<button type="button" role="tab" data-note-tab="${k}" aria-selected="${noteTab===k}">${t} <small>${notesOf(k).length}</small></button>`).join('')}</div>
      <div class="obs-tab-body">${noteTab==='manual_observation'?`<p class="obs-muted">${esc(factLine)}</p>`:''}${notesOf(noteTab).map(noteHtml).join('')||'<p class="obs-muted">저장된 기록 없음</p>'}</div>`)}
    ${fold('similar','유사 사건','<span id="obs-similar-sub"></span>',`<div id="obs-results" class="rx-table-wrap"></div>
      <details class="obs-more"><summary>조건 바꿔 찾기</summary><div class="obs-form" id="obs-filters">
      <label>키워드 / 고객·파트너<input id="obs-query" type="search"></label><label>태그<select id="obs-tag"><option value="">전체</option>${[...new Set(index.items.flatMap(x=>x.business_tags))].sort().map(x=>`<option>${esc(x)}</option>`).join('')}</select></label>
      <label>종류<select id="obs-kind"><option value="">전체</option>${Object.entries(OBS_KIND).map(([v,t])=>`<option value="${v}">${t}</option>`).join('')}</select></label><label>발표 주체<input id="obs-publisher"></label><label>원천<input id="obs-source"></label>
      <label>시작일<input id="obs-from" type="date"></label><label>종료일<input id="obs-to" type="date"></label><label>시간대<select id="obs-session"><option value="">전체</option><option>장전</option><option>장중</option><option>15시 이후</option><option>미상</option></select></label>
      <label>주가 +30분 부호<select id="obs-sign"><option value="">전체</option><option value="up">상승</option><option value="down">하락</option><option value="missing">미집계</option></select></label><label>자료 품질<select id="obs-quality"><option value="">전체</option><option value="complete">관측 가능</option><option value="partial">일부 미집계</option><option value="unavailable">관측값 없음</option><option value="pending">진행 중</option></select></label>
      <label class="obs-check"><input id="obs-has-note" type="checkbox"> 검토 기록 있는 사건만</label></div></details>`)}
    ${fold('edit','분류·연결 수정','',`<p class="obs-muted">${esc(d.source_note)}</p><form id="obs-meta" class="obs-form">
      <label>종류<select name="event_kind">${Object.entries(OBS_KIND).map(([v,t])=>`<option value="${v}" ${v===obsKind(d.event_kind)?'selected':''}>${t}</option>`).join('')}</select></label>
      ${[['business_tags','복수 태그 (쉼표)',d.business_tags.join(', ')],['publisher','발표 주체',d.publisher],['partner','고객/파트너',d.partner],['first_public_at','최초 공개 KST (미상이면 비움)',d.first_public_at],['analysis_at','분석 기준 KST',d.analysis_at],['pr_distributed_at','당사 PR 실제 배포 KST',d.pr_distributed_at],['primary_source_url','원천 주소',d.primary_source_url],['time_source','시각 출처',d.time_source]].map(([n,t,v])=>`<label>${t}<input name="${n}" value="${esc(v||'')}"></label>`).join('')}
      <label>시각 상태<select name="time_status">${['confirmed','estimated','unknown'].map(x=>`<option value="${x}" ${x===d.time_status?'selected':''}>${OBS_STATUS[x]}</option>`).join('')}</select></label>
      <label class="obs-check"><input name="followup_candidate" type="checkbox" ${d.followup_candidate?'checked':''}> 후속 보도 검토 후보</label><div class="obs-form-actions"><button class="btn">암호화 정정 요청</button></div></form>
      <form id="obs-merge" class="obs-form"><label>같은 사건으로 확인한 다른 사건 ID<input name="other_id" required placeholder="ne-…"></label><label>연결 근거<input name="reason" required></label><div class="obs-form-actions"><button class="btn">같은 사건 연결 요청</button></div></form>
      <form id="obs-split" class="obs-form"><label>분리할 원문 ID<select name="member_id">${d.members.map(m=>`<option value="${esc(m.id)}">${esc(m.id)} · ${esc(m.title)}</option>`).join("")}</select></label><label>분리 근거<input name="reason" required></label><div class="obs-form-actions"><button class="btn">별도 사건으로 분리 요청</button></div></form>
      <details><summary>변경 이력 원본</summary><pre>${esc(JSON.stringify({history:d.history,source:f.source,snapshot:d.snapshot_version},null,2))}</pre></details>`)}
    ${fold('basis','분석 기준·출처','',`<ul class="obs-basis-list">
        <li>사건 ID <code class="obs-eid">${esc(d.event_id)}</code> (다른 기사와 같은 사건으로 연결할 때 이 ID를 씁니다)</li>
        <li>최초 공개 ${esc(obsStamp(d.first_public_at))} · 시각 출처 ${esc(obsSrc(d.time_source))} (${esc(OBS_STATUS[d.time_status]||d.time_status)})</li>
        <li>분석 기준 ${esc(obsStamp(d.analysis_at))} · 비교 시작 ${esc(obsStamp(o.start))}</li>
        <li>당사 보도자료 실제 배포 ${esc(d.pr_distributed_at?obsStamp(d.pr_distributed_at):'미상')}</li>
        <li>사건 연결 ${esc(({confirmed:'동일 사건 확인',review_required:'과거 묶음 · 확인 필요',individual:'개별 사건'})[d.identity_status]||'확인 필요')} · 동일 사건 후보(미확정) ${esc((d.same_event_candidates||[]).map(x=>x.a+' ↔ '+x.b).join(', ')||'없음')}</li>
        ${o.missing_reason?`<li>미집계 사유: ${esc(o.missing_reason)}</li>`:''}
        <li>계산 ${esc(o.calculation_version)} · 재계산 ${esc(obsStamp(d.recomputed_at))} · 전후 20거래일 값은 AI 질문·CSV 자료에 포함</li></ul>
      <p><button type="button" class="btn small" id="obs-link">사건 링크 복사</button> <button type="button" class="btn small" id="obs-export">관측값 CSV</button> <button type="button" class="btn small" id="obs-sources-export">원문 목록 CSV</button></p>`)}
`;
    host.querySelectorAll('[data-note-tab]').forEach(b=>b.onclick=()=>{noteTab=b.dataset.noteTab;render();});
    host.querySelectorAll('[data-obs-open]').forEach(b=>b.onclick=()=>openEvent?openEvent(b.dataset.obsOpen):null);
    $('#obs-link').onclick=()=>navigator.clipboard.writeText(location.href).then(()=>toast('사건 링크 복사됨')).catch(()=>toast('주소창의 링크를 복사해 주세요.','err'));
    $('#obs-question').onclick=async()=>{const q=observationQuestion(d);$('#obs-question-text').value=q;$('#obs-question-text').hidden=false;try{await navigator.clipboard.writeText(q);toast('실제 자료를 포함한 질문을 복사했습니다. AI 답을 받은 뒤 [결과 붙여넣기]로 저장하세요.');}catch(e){toast('아래 질문을 선택해서 복사해 주세요.','err');}};
    const openNote=(kind)=>{noteOpen=true;const form=$('#obs-note');form.hidden=false;if(kind)form.elements.kind.value=kind;if(!form.elements.analysis_at.value)form.elements.analysis_at.value=new Date(Date.now()+9*36e5).toISOString().slice(0,16);form.elements.body.focus();};
    $('#obs-note-open').onclick=()=>{revision=null;openNote(noteTab);};
    $('#obs-note-cancel').onclick=()=>{noteOpen=false;revision=null;$('#obs-note').hidden=true;};
    $('#obs-sources-export').onclick=()=>csvDownloadRows('사건_원문목록',['사건 ID','원문 ID','제목','매체','URL','공개 KST','수집 KST','원래 시각','시각 출처','PR 실제 배포 KST','분석 기준 KST'],d.members.map(m=>[d.event_id,m.id,m.title,m.office,m.url,m.ts,m.collected_at,m.original_ts,m.time_source,d.pr_distributed_at,d.analysis_at]));
    $('#obs-export').onclick=()=>{const rows=[];for(const [rk,p] of [['day',f],['5',o.windows['5']],['20',o.windows['20']]])for(const x of (p?.points||[]))rows.push([d.event_id,rk,x.time,x.offset,x.stock_price??'',x.kosdaq_price??'',...['own','kq','diff'].map(k=>x[k]==null?'':x[k]*100),d.first_public_at,p.base_own_time||p.base_day,p.base_kq_time||p.base_day,p.base_own,p.base_kq,p.interval||'daily',p.kq_interval||'daily',JSON.stringify(p.source),o.calculation_version,x.own==null||x.kq==null?'partial':'observed']);
      csvDownloadRows('사건_관측', ['event_id','범위','KST 관측 시각','상대 시점','원시 주가','원시 KOSDAQ','주가 수익률 %','KOSDAQ 수익률 %','차이 %p','공개 시각','기준 주가 시각','기준 지수 시각','주가 기준가격','KOSDAQ 기준가격','주가 봉','지수 봉','출처','계산 버전','상태'],rows);};
    $('#obs-meta').onsubmit=async e=>{e.preventDefault();const v=Object.fromEntries(new FormData(e.target));v.business_tags=v.business_tags.split(',').map(x=>x.trim()).filter(Boolean);v.followup_candidate=!!v.followup_candidate;for(const k of ['first_public_at','analysis_at','pr_distributed_at'])v[k]=v[k]||null;try{await sendInbox({type:'news_event_meta',event_id:eid,...v});}catch(_) {}};
    $('#obs-split').onsubmit=async e=>{e.preventDefault();try{await sendInbox({type:'news_event_split',event_id:eid,...Object.fromEntries(new FormData(e.target))});}catch(_) {}};
    $('#obs-merge').onsubmit=async e=>{e.preventDefault();try{await sendInbox({type:'news_event_link',event_id:eid,...Object.fromEntries(new FormData(e.target))});}catch(_) {}};
    if(noteDraft)for(const [key,value] of Object.entries(noteDraft)){const input=$('#obs-note').elements[key];if(input)input.value=value;}
    $('#obs-note').onsubmit=async e=>{e.preventDefault();const v=Object.fromEntries(new FormData(e.target));try{const msg=await sendInbox({type:'news_event_note',request_id:crypto.randomUUID(),event_id:eid,...v,sources:v.sources.split('\n').map(x=>x.trim()).filter(Boolean),question:observationQuestion(d),snapshot:{version:d.snapshot_version,observation:d.observation},revision_parent_id:revision});$('#obs-save-status').textContent=msg;}catch(err){$('#obs-save-status').textContent=err.message;}};
    host.querySelectorAll('[data-note-edit]').forEach(b=>b.onclick=()=>{const n=d.notes.find(x=>x.id===b.dataset.noteEdit);revision=n.id;openNote();const form=$('#obs-note');for(const k of ['body','kind','author_type','confidence_label','review_status'])form.elements[k].value=n[k]||'';form.elements.sources.value=(n.sources||[]).join('\n');$('#obs-save-status').textContent='과거 기록은 유지하고 새 버전을 저장합니다.';form.scrollIntoView({block:'center'});});
    const search=()=>{
      const v=id=>$('#obs-'+id).value,query=v('query').toLowerCase();
      const list=index.items.filter(x=>{const m=x.steps['30']?.own,t=x.first_public_at?.slice(11,16),session=!t?'미상':t<'09:00'?'장전':t>'15:00'?'15시 이후':'장중';return (!query||[x.title,x.partner,...x.business_tags].join(' ').toLowerCase().includes(query))&&(!v('tag')||x.business_tags.includes(v('tag')))&&(!v('kind')||obsKind(x.event_kind)===v('kind'))&&(!v('publisher')||x.publisher.includes(v('publisher')))&&(!v('source')||x.time_source.includes(v('source')))&&(!v('from')||(x.first_public_at||'')>=v('from'))&&(!v('to')||(x.first_public_at||'').slice(0,10)<=v('to'))&&(!v('session')||session===v('session'))&&(!v('quality')||x.status===v('quality'))&&(!$('#obs-has-note').checked||x.notes_count>0)&&(!v('sign')||(v('sign')==='missing'?m==null:v('sign')==='up'?m>0:m<0));})
        .sort((a,b)=>(b.first_public_at||'').localeCompare(a.first_public_at||''));
      const values=list.map(x=>x.steps['30']?.own).filter(x=>x!=null).sort((a,b)=>a-b),n=values.length;
      $('#obs-similar-sub').textContent=`${v('tag')?`태그 「${v('tag')}」 `:''}${list.length}건`;
      $('#obs-results').innerHTML=`<table class="rx-table obs-similar"><thead><tr><th>공개</th><th>사건</th><th>+30분</th><th>KOSDAQ 대비</th><th></th></tr></thead><tbody>${list.slice(0,50).map(x=>`<tr${x.event_id===d.event_id?' class="on"':''}><td class="nowrap">${esc(x.first_public_at?obsStamp(x.first_public_at).slice(2,10):'미상')}</td><td>${esc(x.title)}<br><small class="muted">${esc(OBS_KIND[obsKind(x.event_kind)])}</small></td><td class="num ${sgnCls(x.steps['30']?.own)}">${pct(x.steps['30']?.own)}</td><td class="num">${points(x.steps['30']?.diff)}</td><td class="nowrap">${x.event_id===d.event_id?'<span class="obs-muted">지금 기사</span>':`<button type="button" class="btn-link" data-obs-open="${esc(x.event_id)}">보기</button>`}</td></tr>`).join('')||'<tr><td colspan="5">조건에 맞는 사건이 없습니다.</td></tr>'}</tbody></table>
      <p class="obs-muted">유효 n=${n} (사건 ID별 1건)${list.length>50?' · 최근 50건 표시':''}. ${n<5?'소표본: 통계 결론 보류.':''} ${n>=2?`+30분 중앙값 ${pct(n%2?values[n>>1]:(values[n/2-1]+values[n/2])/2)} · 평균 ${pct(values.reduce((a,b)=>a+b,0)/n)}`:''} 기사 효과의 통계적 증거가 아닙니다.</p>`;
      $('#obs-results').querySelectorAll('[data-obs-open]').forEach(b=>b.onclick=()=>openEvent?openEvent(b.dataset.obsOpen):null);
    };
    $('#obs-filters').oninput=search;$('#obs-filters').onchange=search;
    if(filterDraft)for(const [id,val] of filterDraft){const el=document.getElementById(id);if(el){if(el.type==='checkbox')el.checked=val;else el.value=val;}}
    else $('#obs-tag').value=d.business_tags.find(t=>[...$('#obs-tag').options].some(o=>o.value===t))||'';
    search();
  };render();
}

  // ---- '여러 기사 겹쳐 보기'(뉴스 목록 오른쪽)와 기사 그래프에 겹치는 다른 기사·중앙값: 같은 계산을 한 곳에서
  // Read only the observation at this minute, including for averages and index subtraction.
  const at = reactionAt;
  const series = (x) => {
    const own = (x.path && x.path.own) || [];
    if (!reactState.vsKq) return own;
    const kq = (x.path && x.path.kq) || [];
    return own.map(([t, y,,source]) => { const k = at(kq, t); return k == null ? null : [t, y - k,null,source+"|"+(kq.find(p=>p[0]===t)?.[3]||"")]; }).filter(Boolean);
  };
  const stepV = (x, m) => { const s0 = (x.steps || {})[m]; if (!s0 || s0.own == null) return null; return reactState.vsKq ? (Object.hasOwn(s0,"diff") ? s0.diff : s0.kq == null ? null : s0.own - s0.kq) : s0.own; };
  const median = (vs) => { const a = vs.filter((v) => v != null).sort((p, q) => p - q); if (!a.length) return null; const h = a.length >> 1; return a.length % 2 ? a[h] : (a[h - 1] + a[h]) / 2; };
  // 파란 선: 각 시각에 관측된 사건(중복 제외 2건 이상)의 중앙값 — 크게 움직인 몇 건에 끌려가지 않게
  const centerPath = (list, X0, X1) => { const out = []; for (let m = X0; m <= X1; m += 1) { const vs = reactionSamples(list, x => at(series(x), m)); if (vs.length >= 2) out.push([m, median(vs), vs.length]); } return out; };
  // 비교 대상: 공시는 주요 공시만 (예전 뉴스 반응과 같은 기준)
  const reactionComparable = (x) => x.category !== "단순 언급" && (x.kind !== "disc" || MAIN_DISC.has(x.pblntf || ""));
  function rxPlot(el, list) {
    const W = el.clientWidth || 800, H = Math.max(240, Math.min(380, W * 0.42)), L = 44, R = 12, T = 12, B = 26;
    // 공개 뒤 +H분까지 (앞쪽은 H의 1/4만큼 공개 전도 보여 줌)
    const X1 = +reactState.h, X0 = -Math.max(10, Math.round(X1 / 4));
    const lines = list.map((x) => ({ x, s: series(x).filter(([m]) => m >= X0 && m <= X1) })).filter((o) => o.s.length >= 1);
    if (!lines.length) { el.innerHTML = `<div class="empty"><strong>그릴 경로가 없습니다.</strong>조건을 넓혀 보세요.</div>`; return 0; }
    const vals = lines.flatMap((o) => o.s.filter(([m]) => m >= X0).map((p) => p[1]));
    let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
    const pad = Math.max(0.005, (hi - lo) * 0.08); lo -= pad; hi += pad;
    const xs = (m) => L + ((m - X0) / (X1 - X0)) * (W - L - R), ys = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    const avg = centerPath(list, X0, X1);
    const clip = (s) => { const v0 = at(s, X0); const inR = s.filter(([m]) => m > X0); return v0 == null ? inR : [[X0, v0], ...inR]; };
    const d = (s, gap = 1) => { const points = clip(s); return points.map(([m, v], i) => `${i && m - points[i - 1][0] <= gap && points[i][3]===points[i-1][3] ? "L" : "M"}${xs(m).toFixed(1)},${ys(v).toFixed(1)}`).join(""); };
    const gaps = (series, gap, cls, start, intervals) => series.slice(1).map((p,i) => {
      const prev=series[i]; if(p[0]-prev[0]<=gap || p[0]-prev[0]>60 || p[3]!==prev[3]) return "";
      const time=m=>start ? new Date(toTime(start)*1000+m*60000).toISOString().slice(0,16).replace('T',' ')+' KST' : (m>0?'+':'')+m+'분';
      const tip=`관측 데이터 없는 구간: ${time(prev[0])} ~ ${time(p[0])} · ${intervals}. 이 구간은 관측 데이터가 없거나 해당 시각에 비교 가능한 데이터가 없습니다. 점선은 표시용 연결이며 계산에 사용되지 않습니다.${p[2]?' 유효 사건 '+p[2]+'건, 같은 시각 관측':''}`;
      return `<path class="rx-gap ${cls}" d="M${xs(prev[0])},${ys(prev[1])}L${xs(p[0])},${ys(p[1])}"/><path class="rx-gap-hit ${cls}" tabindex="0" role="button" aria-label="${esc(tip)}" data-gap="${esc(tip)}" d="M${xs(prev[0])},${ys(prev[1])}L${xs(p[0])},${ys(p[1])}"><title>${esc(tip)}</title></path>`;
    }).join('');
    // 눈금: 0을 지나는 깔끔한 간격 (0.1·0.2·0.5·1·2·5%…)
    const stepY = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2].find((g) => (hi - lo) / g <= 6) || 0.5;
    const tick = (v) => `${v > 1e-9 ? "+" : v < -1e-9 ? "−" : ""}${Math.abs(v * 100).toFixed(stepY < 0.01 ? 1 : 0)}${reactState.vsKq ? "%p" : "%"}`;
    const yt = []; for (let k = Math.ceil(lo / stepY); k * stepY <= hi + 1e-12; k++) yt.push(k * stepY);
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="공개 뒤 주가 경로 ${lines.length}건">
      ${yt.map((v) => `<line class="rx-grid" x1="${L}" x2="${W - R}" y1="${ys(v)}" y2="${ys(v)}"/><text class="rx-ax" x="${L - 6}" y="${ys(v) + 4}" text-anchor="end">${tick(v)}</text>`).join("")}
      <line class="rx-zero" x1="${L}" x2="${W - R}" y1="${ys(0)}" y2="${ys(0)}"/>
      <line class="rx-t0" x1="${xs(0)}" x2="${xs(0)}" y1="${T}" y2="${H - B}"/>
      ${[X0, ...Array.from({ length: X1 / (X1 <= 30 ? 10 : X1 <= 60 ? 15 : 30) + 1 }, (_, i) => i * (X1 <= 30 ? 10 : X1 <= 60 ? 15 : 30))].map((m) => `<text class="rx-ax" x="${xs(m)}" y="${H - 8}" text-anchor="middle">${m === 0 ? "비교 시작" : `${m > 0 ? "+" : ""}${m}분`}</text>`).join("")}
      ${lines.map((o) => { const dd = d(o.s, o.x.interval || 1); return `<g class="rx-g" data-id="${esc(o.x.id)}" data-t="${esc(`${dayLabel(o.x.ts)} ${hhmm(o.x.ts)} · ${o.x.title}`)}"><path class="rx-one${reactState.pick === o.x.id ? " on" : ""}" d="${dd}"/><path class="rx-hit" d="${dd}"/>${gaps(o.s,o.x.interval||1,reactState.pick===o.x.id?"on":"",o.x.start,`그린광학 ${o.x.interval}분 / KOSDAQ ${o.x.kq_interval||"-"}분`)}${o.s.filter(([m]) => m >= X0).map(([m,v]) => `<circle cx="${xs(m)}" cy="${ys(v)}" r="1" fill="#9aa5b6"><title>${m > 0 ? "+" : ""}${m}분 ${reactState.vsKq ? points(v) : pct(v)}</title></circle>`).join("")}</g>`; }).join("")}
      ${avg.length ? `<path class="rx-avg" d="${d(avg)}"/>${gaps(avg,1,"average",null,`그린광학 ${[...new Set(list.map(x=>x.interval))].join("/")}분 / KOSDAQ ${[...new Set(list.map(x=>x.kq_interval))].join("/")}분`)}${avg.map(([m,v,n]) => `<circle cx="${xs(m)}" cy="${ys(v)}" r="2" tabindex="0" data-gap="${m}분 · 유효 사건 ${n}건, 같은 시각 관측 · 실제 공통 시각의 중앙값" class="rx-avg"><title>${m}분 · 유효 사건 ${n}건, 같은 시각 관측</title></circle>`).join("")}` : ""}
      ${reactState.pick ? (() => {            // 고른 기사: 이 종목(굵게) + 같은 시간 KOSDAQ(점선)
        const o = lines.find((q) => q.x.id === reactState.pick); if (!o) return "";
        const kq = !reactState.vsKq && o.x.path && o.x.path.kq && o.x.path.kq.length > 1 ? `<path class="rx-kq" d="${d(o.x.path.kq.filter(([m]) => m <= X1), o.x.kq_interval || 1)}"/>` : "";
        return `${kq}<path class="rx-one on top" d="${d(o.s, o.x.interval || 1)}"/>`; })() : ""}
    </svg>`;
    el.querySelectorAll?.('[data-gap]').forEach(node=>{const show=()=>{$("#rx-hover").textContent=node.dataset.gap;$("#rx-gap-note").textContent=node.dataset.gap;};node.onfocus=show;node.onmouseenter=show;node.onclick=ev=>{ev.stopPropagation();show();};});
    return lines.length;
  }
  // list: 지금 목록에 보이는 기사·공시의 반응 행. onPick(id): 그래프의 선을 누르면 그 기사 열기
  function reactionOverview(host, list, { title = "공개 뒤 주가", onPick = null } = {}) {
    list = list.filter(reactionComparable);
    host.innerHTML = `<section class="rx-ov" aria-label="여러 기사 겹쳐 보기">
      <p class="obs-eyebrow">지금 목록의 기사 겹쳐 보기</p><h3>${esc(title)} <small class="muted" id="rx-n"></small></h3>
      <p class="obs-muted">목록 조건과 같은 기사만 비교합니다. 기사를 고르면 그 기사 그래프에 다른 기사와 중앙값이 겹쳐 나옵니다.</p>
      <div class="rx-ov-tools"><span class="rx-leg"><span><i class="rx-l-one"></i>기사 하나</span><span><i class="rx-l-avg"></i>중앙값</span><span>┈ 관측 누락 (계산 제외)</span></span><span class="spacer"></span>
        <span class="seg" role="group" aria-label="비교 시작 뒤 얼마까지">${REACT_H.map(([v, t]) => `<button type="button" data-h="${v}" aria-pressed="${reactState.h === v}">${t}</button>`).join("")}</span>
        <label class="rx-chk" title="그린광학 등락에서 같은 시간 KOSDAQ 등락을 뺀 값 (단순 차이, 인과 효과를 뺀 값이 아님)"><input type="checkbox" id="rx-vskq" ${reactState.vsKq ? "checked" : ""}> KOSDAQ 빼고 보기</label></div>
      <span class="rx-hover" id="rx-hover"></span><div id="rx-plot" class="rx-plot"></div><p class="note" id="rx-gap-note" role="status"></p>
      <div class="obs-cards" id="rx-cards"></div><p class="note" id="rx-sum"></p></section>`;
    const draw = () => reactionSummary(list);
    host.querySelector(".rx-ov-tools").onclick = (ev) => { const b = ev.target.closest("button[data-h]"); if (!b) return; reactState.h = b.dataset.h; host.querySelectorAll("button[data-h]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.h === reactState.h))); draw(); };
    host.querySelector(".rx-ov-tools").onchange = (ev) => { if (ev.target.id === "rx-vskq") { reactState.vsKq = ev.target.checked; draw(); } };
    const plotEl = $("#rx-plot");
    plotEl.onclick = (ev) => { const gap = ev.target.closest("[data-gap]"); if (gap) { $("#rx-hover").textContent = gap.dataset.gap; $("#rx-gap-note").textContent = gap.dataset.gap; return; } const g = ev.target.closest("g[data-id]"); if (g && onPick) onPick(g.dataset.id); };
    plotEl.onmouseover = (ev) => { const gap = ev.target.closest("[data-gap]"); const g = ev.target.closest("g[data-id]"); $("#rx-hover").textContent = gap ? gap.dataset.gap : g ? g.dataset.t : ""; };
    plotEl.onmouseleave = () => { $("#rx-hover").textContent = ""; };
    draw();
    return { draw };
  }
  function reactionSummary(list) {
    const plotted = rxPlot($("#rx-plot"), list);
    const measured = list.filter((x) => stepV(x, reactState.h) != null).length;
    $("#rx-n").textContent = `대상 ${list.length}건 · 경로 ${plotted}건 · +${reactState.h}분 집계 ${measured}건${reactState.vsKq ? " · KOSDAQ 대비 차이" : ""}`;
    const s = (m) => { const vs = reactionSamples(list, x => stepV(x, m)); if (!vs.length) return null;
      const up = vs.filter((v) => v > 0).length, dn = vs.filter((v) => v < 0).length;
      const mean = vs.reduce((a, b) => a + b, 0) / vs.length, fmt = (v) => reactState.vsKq ? points(v) : pct(v);
      return `+${m}분 중앙값 <b class="${sgnCls(median(vs))}">${fmt(median(vs))}</b> · 평균 ${fmt(mean)} (${reactState.vsKq ? "시장 웃돈" : "오른"} ${up} · ${reactState.vsKq ? "시장 밑돈" : "내린"} ${dn} · ${reactState.vsKq ? "시장과 같음" : "보합"} ${vs.length - up - dn}, 중복 제외 ${vs.length}사건)`; };
    $("#rx-sum").innerHTML = (s(reactState.h) || `+${reactState.h}분 집계 가능한 자료가 없습니다.`)
      + `<br><span class="muted">집계 제외 ${list.length - measured}건: 공개 시각 미상 또는 기준·종료 시각의 관측값 부족. KOSDAQ 대비 차이는 양쪽의 같은 시각 가격이 있을 때만 계산합니다.</span>`
      + `<br>동일한 사건을 다룬 기사가 여러 개 있어도 통계에는 1건만 반영합니다.
      <br>주가 데이터가 부족한 경우 해당 구간의 계산에서 제외합니다.
      <br>중앙값 선은 분석 가능한 사건이 2건 이상일 때 표시합니다.
      <br>‘KOSDAQ 움직임 빼고 보기’를 선택하면 시장 전체의 상승·하락 영향을 단순 차감하여 비교합니다.
      <br>주가 움직임은 기사 외 여러 요인의 영향을 받으므로 기사의 직접적인 효과를 뜻하지 않습니다.
      <details><summary>계산 기준 자세히 보기</summary>파란 선은 각 시각 유효 사건의 중앙값(정렬했을 때 가운데 값)입니다. 크게 움직인 몇 건에 끌려가는 평균 대신 보통의 흐름을 보여 줍니다. 평균은 아래 숫자로 함께 적습니다. 사건 ID와 비교 시작으로 중복 제거합니다. 서로 다른 값이 충돌하는 사건은 제외합니다. 1사건뿐인 시각에는 중앙값 선을 표시하지 않습니다. 공개 직전 한 봉 간격 안의 완료 가격을 기준으로 하며 같은 시각 가격이 없으면 KOSDAQ 차이는 미집계입니다. 없는 가격을 보간하지 않습니다. 점선 연결은 표시 전용이며 계산·CSV에 포함하지 않습니다. KOSDAQ 차감은 단순 비교이며 시장의 인과 효과를 완전히 제거한 값이 아닙니다. 소표본은 일반화할 수 없습니다.</details>`
      + (reactState.vsKq ? `<br>KOSDAQ 비교 가능 관측 시각이 드뭅니다. 이 점들은 실제 공통 시각의 중앙값입니다. 봉 간격: ${[...new Set(list.map(x=>`그린광학 ${x.interval||'-'}분 / KOSDAQ ${x.kq_interval||'-'}분`))].join(', ')}` : '');
    const buckets=new Map(); for(const x of list){const v=stepV(x,reactState.h);if(v==null)continue;const k=(x.event_key||x.id)+'|'+(x.start||'');if(!buckets.has(k))buckets.set(k,[]);buckets.get(k).push(v);}
    const conflicts=[...buckets.entries()].filter(([,v])=>Math.max(...v)-Math.min(...v)>=1e-8);
    $("#rx-sum").innerHTML+=`<details><summary>집계 품질: 충돌/제외 ${conflicts.length}사건 · 시각 미상 ${list.filter(x=>x.untimed).length}건 · 중복 제외 ${[...buckets.values()].reduce((n,v)=>n+v.length-1,0)}건</summary>${conflicts.map(([k,v])=>esc(k)+': '+v.map(pct).join(', ')).join('<br>')||'충돌 없음'}</details>`;
    const fmt = (v) => (reactState.vsKq ? points(v) : pct(v)), vs = reactionSamples(list, (x) => stepV(x, reactState.h));
    const dv = reactionSamples(list, (x) => { const s0 = (x.steps || {})[reactState.h]; return s0 && s0.own != null && s0.kq != null ? (Object.hasOwn(s0, "diff") ? s0.diff : s0.own - s0.kq) : null; });
    const card = (label, v, text) => `<div><small>${label}</small><strong class="${sgnCls(v)}">${text ?? (v == null ? "-" : fmt(v))}</strong></div>`;
    $("#rx-cards").innerHTML = card(`+${reactState.h}분 중앙값`, median(vs)) + card("평균", vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null)
      + card(reactState.vsKq ? "시장 웃돈 / 밑돈" : "오른 / 내린", null, `${vs.filter((v) => v > 0).length} / ${vs.filter((v) => v < 0).length}`)
      + (reactState.vsKq ? "" : `<div><small>KOSDAQ 대비 중앙값</small><strong class="${sgnCls(median(dv))}">${dv.length ? points(median(dv)) : "-"}</strong></div>`);
  }
  // 기사 그래프에 겹칠 다른 기사·중앙값 (위 겹쳐 보기와 같은 조건·같은 계산). 분 = 비교 시작 기준
  // 기사 그래프는 등락률이라 겹칠 선도 등락률로 계산 (KOSDAQ 빼고 보기와 무관)
  function reactionOverlay(list, pickId) {
    list = list.filter(reactionComparable);
    const X1 = +reactState.h, keep = reactState.vsKq;
    reactState.vsKq = false;
    try {
      const others = list.filter((x) => x.id !== pickId);
      const at30 = reactionSamples(others, (x) => stepV(x, reactState.h));
      return { until: X1, n: others.length, median: centerPath(list, -30, X1), othersMedian: median(at30),
        others: others.map((x) => series(x).filter(([m]) => m >= -30 && m <= X1)).filter((sr) => sr.length) };
    } finally { reactState.vsKq = keep; }
  }
  // 엑셀: 지금 목록의 반응 (+30·+60분 모두)
  function reactionCsv(list) {
    const kqV = (x, m) => { const s0 = (x.steps || {})[m]; return s0 && s0.kq != null ? s0.kq : null; };
    const val = (x, k) => (["30", "60"].includes(k) ? stepV(x, k) : kqV(x, k.slice(2)));
    csvDownloadRows("기사_반응", ["공개", "종류", "제목", "봉", ...["30", "kq30", "60", "kq60"].map((k) => k.startsWith("kq") ? `KOSDAQ +${k.slice(2)}분` : reactState.vsKq ? `그린광학−KOSDAQ +${k}분 (%p)` : `그린광학 +${k}분`), "주제", "언론사", "분석 기준 시각", "비교 시작 시각", "기준 설명", "동일 사건 키", "KST", "기준 가격 시각", "가격 소스", "계산 버전", "사건 ID"],
      list.map((x) => [x.ts, x.kind, x.title, x.interval || "", ...["30", "kq30", "60", "kq60"].map((k) => { const v = val(x, k); return v == null ? "" : (v * 100).toFixed(2); }), x.topic || "", x.office || "", x.analysis_ts || "", x.start || "", reactionBasisText(x), x.event_key || x.id, "Asia/Seoul", x.base_price_time || "", JSON.stringify(x.price_source || {}), x.calculation_version || "", x.event_id || ""]));
  }

  // 기사 대시보드: 월별·주제·언론사·기자 막대를 누르면 서로 걸러짐 (다시 누르면 풀림). 원본은 언제든 엑셀로
  const pivotState = { range: "all", unit: "outlet", kind: "all", f: {}, pages: { office: 0, reporter: 0, feed: 0 }, q: { office: "", reporter: "" } };
  const DASH_PER = 10;           // 순위·기사 목록 한 쪽에 보이는 줄 수
  // 쪽 번호: ‹ 1 2 3 … 9 › (지금 쪽 앞뒤 2쪽과 처음·끝)
  function dashPager(key, page, total) {
    const n = Math.ceil(total / DASH_PER);
    if (n <= 1) return "";
    const nums = [...new Set([0, n - 1, ...[-2, -1, 0, 1, 2].map((d) => page + d)])].filter((i) => i >= 0 && i < n).sort((x, y) => x - y);
    let html = "", prev = -1;
    for (const i of nums) {
      if (i - prev > 1) html += `<span class="pg-gap">…</span>`;
      html += `<button type="button" class="pg-btn" data-pg-k="${key}" data-pg="${i}" aria-current="${i === page}">${i + 1}</button>`;
      prev = i;
    }
    return `<nav class="dash-pager" aria-label="쪽 넘기기"><button type="button" class="pg-btn" data-pg-k="${key}" data-pg="${page - 1}" ${page <= 0 ? "disabled" : ""} aria-label="이전 쪽">‹</button>${html}<button type="button" class="pg-btn" data-pg-k="${key}" data-pg="${page + 1}" ${page >= n - 1 ? "disabled" : ""} aria-label="다음 쪽">›</button></nav>`;
  }
  const PIV_DIMS = { topic: "주제", office: "언론사", reporter: "기자", category: "분류", kind: "구분", month: "월", quarter: "분기", year: "연도" };
  const PIV_TIME = new Set(["month", "quarter", "year"]);
  // 기사 목록(대표 + 같은 내용을 실은 매체들)을 한 줄 = 기사 하나로 펼침
  function pivotRows(items, unit) {
    const out = [];
    for (const a of items) {
      if (a.hidden) continue;
      const kind = a.kind === "pr" || a.kind === "company" || a.manual ? "그린광학 보도자료" : "외부 기사";
      const base = { kind, category: a.category || "-", lead: a.id };
      out.push({ ...base, id: a.id, ts: a.ts || "", office: a.office || "-", reporter: a.reporter || "", title: a.title,
        url: a.url || "", topic: a.topic || "기타", topics: (a.topics || []).join(", ") });
      if (unit === "outlet") for (const r of a.related || []) out.push({ ...base, id: r.id, ts: r.ts || a.ts || "",
        office: r.office || "-", reporter: r.reporter || "", title: r.title, url: r.url || "", topic: r.topic || a.topic || "기타", topics: r.topic || "" });
    }
    return out;
  }
  const pivotKey = (r, dim) => {
    const y = r.ts.slice(0, 4), m = +r.ts.slice(5, 7);
    if (dim === "month") return r.ts.slice(0, 7) || "-";
    if (dim === "quarter") return y ? `${y} ${Math.ceil(m / 3)}분기` : "-";
    if (dim === "year") return y || "-";
    if (dim === "reporter") return r.reporter || "(미확인)";
    return r[dim] || "-";
  };
  // 한 기준(dim)으로 세기: [{key, n, by: {구분: 건수}}] 많은 순 (시간 기준은 시간 순)
  const DASH_KINDS = ["그린광학 보도자료", "외부 기사"];
  const dashKey = (r, dim) => (dim === "reporter" ? (r.reporter ? `${r.reporter} · ${r.office}` : "") : pivotKey(r, dim));
  function dashAgg(rows, dim) {
    const m = new Map();
    for (const r of rows) {
      const k = dashKey(r, dim);
      if (!k) continue;
      const x = m.get(k) || { key: k, n: 0, by: {} };
      x.n += 1; x.by[r.kind] = (x.by[r.kind] || 0) + 1;
      m.set(k, x);
    }
    const out = [...m.values()];
    return PIV_TIME.has(dim) ? out.sort((x, y) => (x.key < y.key ? -1 : 1)) : out.sort((x, y) => y.n - x.n || (x.key < y.key ? -1 : 1));
  }

  // 기사 대시보드 (언론 모니터링 대시보드 구성 참고: 숫자 카드 · 기사 추이 · 상위 언론사 · 자주 쓴 기자 · 주제 타일 · 기사 피드)
  // 선·순위·타일을 누르면 그 조건으로 나머지가 함께 걸러짐 (다시 누르면 풀림). 원본은 언제든 엑셀로
  const DASH_CLS = { "그린광학 보도자료": "s-pr", "외부 기사": "s-ext" };
  function dashLine(el, rows, sel) {
    // 월별 두 선 (보도자료·외부 기사), 마우스를 올리면 그달 숫자, 누르면 그달로 걸러짐
    if (!rows.length) { el.innerHTML = `<p class="muted">해당 기사가 없습니다.</p>`; return; }
    const W = Math.max(320, Math.round(el.clientWidth || 760)), H = 220, L = 34, R = 14, T = 14, B = 28;   // 상자 폭에 맞춰 (글자 크기 그대로)
    const max = Math.max(1, ...rows.map((x) => Math.max(...DASH_KINDS.map((k) => x.by[k] || 0))));
    const step = rows.length > 1 ? (W - L - R) / (rows.length - 1) : 0;
    const X = (i) => L + (rows.length > 1 ? i * step : (W - L - R) / 2), Y = (v) => T + (H - T - B) * (1 - v / max);
    const ticks = [0, Math.ceil(max / 2), max];
    const path = (k) => rows.map((x, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(x.by[k] || 0).toFixed(1)}`).join("");
    const every = Math.max(1, Math.ceil(rows.length / 10));
    el.innerHTML = `<div class="dl-wrap"><svg viewBox="0 0 ${W} ${H}" class="dl-svg" role="img" aria-label="월별 기사 수">
      ${ticks.map((t) => `<line class="dl-grid" x1="${L}" x2="${W - R}" y1="${Y(t)}" y2="${Y(t)}"/><text class="dl-ax" x="${L - 6}" y="${Y(t) + 4}" text-anchor="end">${t}</text>`).join("")}
      ${rows.map((x, i) => (i % every === 0 || i === rows.length - 1) ? `<text class="dl-ax" x="${X(i)}" y="${H - 8}" text-anchor="middle">${esc(x.key.slice(2).replace("-", "."))}</text>` : "").join("")}
      ${sel ? rows.map((x, i) => x.key === sel ? `<rect class="dl-sel" x="${X(i) - Math.max(8, step / 2)}" y="${T}" width="${Math.max(16, step)}" height="${H - T - B}"/>` : "").join("") : ""}
      ${DASH_KINDS.map((k) => `<path class="dl-line ${DASH_CLS[k]}" d="${path(k)}"/>`).join("")}
      ${DASH_KINDS.map((k) => rows.map((x, i) => `<circle class="dl-dot ${DASH_CLS[k]}" cx="${X(i)}" cy="${Y(x.by[k] || 0)}" r="4"/>`).join("")).join("")}
      <line class="dl-cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>
      ${rows.map((x, i) => `<rect class="dl-hit" data-i="${i}" x="${X(i) - Math.max(8, step / 2)}" y="0" width="${Math.max(16, step)}" height="${H}"/>`).join("")}
    </svg><div class="dl-tip" hidden></div></div>
    <div class="dash-legend">${DASH_KINDS.map((k) => `<span><i class="${DASH_CLS[k]}"></i>${k}</span>`).join("")}</div>`;
    const svg = $(".dl-svg", el), tip = $(".dl-tip", el), cross = $(".dl-cross", el);
    svg.addEventListener("mousemove", (ev) => {
      const h = ev.target.closest(".dl-hit"); if (!h) return;
      const i = +h.dataset.i, x = rows[i];
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
      tip.hidden = false;
      tip.innerHTML = `<b>${esc(x.key)}</b>${DASH_KINDS.map((k) => `<span><i class="${DASH_CLS[k]}"></i>${k} <b>${x.by[k] || 0}</b></span>`).join("")}<span class="muted">합계 ${x.n}건 · 누르면 이 달만</span>`;
      const r = svg.getBoundingClientRect(), px = (X(i) / W) * r.width;
      tip.style.left = `${Math.min(Math.max(px, 70), r.width - 70)}px`;
    });
    svg.addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
    svg.addEventListener("click", (ev) => { const h = ev.target.closest(".dl-hit"); if (h) el.dispatchEvent(new CustomEvent("pick", { bubbles: true, detail: { d: "month", v: rows[+h.dataset.i].key } })); });
  }

  let dashResize = null, dashPick = null, dashClick = null;
  async function pagePivot() {
    main.innerHTML = `<div class="wrap dash">
      <div class="page-head"><div><h1 class="page-title">뉴스</h1><p class="page-sub">선·순위·타일을 누르면 그 조건으로 나머지가 함께 걸러집니다. 다시 누르면 풀립니다.</p></div>${artViewSeg(true)}</div>
      <div class="dash-bar-top" id="piv-ctl"></div>
      <div id="dash-chips" class="dash-chips"></div>
      <div class="dash-bento">
      <div id="dash-kpi" class="dash-kpis"></div>
      <section class="panel dash-card d-trend"><div class="dash-card-head"><h3>기사 추이 <small class="muted">월별</small></h3></div><div id="dash-month"></div></section>
      <section class="panel dash-card d-topic"><div class="dash-card-head"><h3>주제</h3></div><div id="dash-topic" class="dash-chips-row"></div></section>
        <section class="panel dash-card d-half"><div class="dash-card-head"><h3>상위 언론사</h3><small class="muted">10곳씩</small><input type="search" class="dash-q" data-q="office" placeholder="언론사 찾기" aria-label="언론사 이름으로 찾기" autocomplete="off"><span class="dash-leg"><span><i class="dot s-pr"></i>보도자료</span><span><i class="dot s-ext"></i>외부 기사</span></span></div><div id="dash-office"></div></section>
        <section class="panel dash-card d-half"><div class="dash-card-head"><h3>자주 쓴 기자</h3><small class="muted">10명씩 · 같은 이름도 언론사가 다르면 따로</small><input type="search" class="dash-q" data-q="reporter" placeholder="기자·언론사 찾기" aria-label="기자 이름으로 찾기" autocomplete="off"><span class="dash-leg"><span><i class="dot s-pr"></i>보도자료</span><span><i class="dot s-ext"></i>외부 기사</span></span></div><div id="dash-reporter"></div></section>
      <section class="panel dash-card d-full"><div class="dash-card-head"><h3>기사 <small class="muted" id="dash-n"></small></h3><select id="dash-feed-month" class="dash-sel" aria-label="기사 목록 달"></select><span class="spacer"></span>
        <span class="dash-dl"><button class="btn small" type="button" id="piv-csv">지금 조건 엑셀로</button>
        <button class="btn small" type="button" id="piv-csv-all">원본 전체 엑셀로</button></span></div>
        <ul class="dash-feed" id="dash-list"></ul><div id="dash-feed-pager"></div></section></div></div>`;
    let items;
    try { items = (await api("/api/articles")).items || []; }
    catch (e) { $("#dash-list").innerHTML = `<li class="empty"><strong>기사를 불러오지 못했습니다.</strong>${esc(e.message)}</li>`; return; }
    if (!$("#dash-list")) return;
    const seg = (key, opts) => `<div class="seg" role="group">${opts.map(([v, t]) => `<button type="button" data-k="${key}" data-v="${v}" aria-pressed="${pivotState[key] === v}">${t}</button>`).join("")}</div>`;
    const drawCtl = () => {
      $("#piv-ctl").innerHTML = `${seg("range", [["all", "전체"], ["365", "1년"], ["183", "6개월"], ["92", "3개월"], ["31", "1개월"]])}
        ${seg("kind", [["all", "전체"], ["그린광학 보도자료", "보도자료"], ["외부 기사", "외부 기사"]])}`;
    };
    drawCtl();
    const F = pivotState.f;
    const DIMS = { month: "월", topic: "주제", office: "언론사", reporter: "기자" };
    let base = [], shown = [];
    const csv = (name, list) => csvDownloadRows(name, ["날짜", "시각", "구분", "분류", "대표 주제", "걸린 주제 전부", "언론사", "기자", "제목", "주소"],
      list.map((r) => [r.ts.slice(0, 10), r.ts.slice(11, 16), r.kind, r.category, r.topic, r.topics, r.office, r.reporter, r.title, r.url]));
    const pass = (r, skip) => Object.entries(F).every(([d, v]) => d === skip || dashKey(r, d) === v);
    function board(el, dim) {
      const all = base.filter((r) => pass(r, dim));
      // 이름 찾기: 순위는 전체 기준 그대로 두고, 찾는 말이 들어간 줄만 보여 줌 (기자는 '이름 · 언론사'에서 찾음)
      const nq = (pivotState.q[dim] || "").toLowerCase().replace(/\s+/g, "");
      const ranked = dashAgg(all, dim).map((x, i) => ({ ...x, rank: i + 1 }));
      const full = nq ? ranked.filter((x) => x.key.toLowerCase().replace(/\s+/g, "").includes(nq)) : ranked;
      const pages = pivotState.pages, last = Math.max(0, Math.ceil(full.length / DASH_PER) - 1);
      pages[dim] = Math.min(pages[dim] || 0, last);
      const from = pages[dim] * DASH_PER, list = full.slice(from, from + DASH_PER);
      const total = all.filter((r) => dashKey(r, dim)).length || 1;
      const max = full.length ? full[0].n : 1;          // 막대 길이는 1위 기준 (쪽을 넘겨도 같은 눈금)
      // 플러저블(Plausible) 상위 출처 목록처럼 이름 뒤에 건수만큼 옅은 막대, 막대는 보도자료·외부 기사 비율로 나눔
      el.innerHTML = list.length ? `<ol class="dash-board">${list.map((x, i) => {
        const [name, sub] = dim === "reporter" ? x.key.split(" · ") : [x.key, ""];
        const pr = x.by["그린광학 보도자료"] || 0, ext = x.by["외부 기사"] || 0;
        return `<li><button type="button" class="dash-row${F[dim] === x.key ? " on" : F[dim] ? " off" : ""}" data-d="${dim}" data-v="${esc(x.key)}">
          <span class="rk">${x.rank}</span>
          <span class="nm"><span class="bar" style="width:${((x.n / max) * 100).toFixed(1)}%"><i class="b-pr" style="flex:${pr}"></i><i class="b-ext" style="flex:${ext}"></i></span>
            <b>${esc(name)}</b>${sub ? `<small class="muted">${esc(sub)}</small>` : ""}</span>
          <span class="ct"><b>${x.n}</b><small class="muted">${Math.round((x.n / total) * 100)}%</small></span>
          <span class="mix"><span title="그린광학 보도자료"${pr ? "" : ' class="zero"'}><i class="dot s-pr"></i>${pr}</span><span title="외부 기사"${ext ? "" : ' class="zero"'}><i class="dot s-ext"></i>${ext}</span></span></button></li>`;
      }).join("")}</ol>${dashPager(dim, pages[dim], full.length)}` : `<p class="muted">${nq ? `“${esc(pivotState.q[dim])}”이(가) 들어간 ${dim === "reporter" ? "기자" : "언론사"}가 없습니다.` : "해당 기사가 없습니다."}</p>`;
      if (dim === "reporter") {               // 기자 이름을 못 읽은 기사도 숫자로 (언론사별)
        const miss = all.filter((r) => !r.reporter);
        if (miss.length) {
          const by = dashAgg(miss, "office").slice(0, 5).map((x) => `${esc(x.key)} ${x.n}`).join(" · ");
          el.insertAdjacentHTML("beforeend", `<p class="dash-miss muted">기자 미확인 <b>${miss.length}건</b> (${by})</p>`);
        }
      }
    }
    // 주제: 가로로 줄지은 칩 (토스 '주식 골라보기' 칩처럼), 누르면 걸러짐
    function tiles(el) {
      const list = dashAgg(base.filter((r) => pass(r, "topic")), "topic");
      el.innerHTML = list.length ? list.map((x) => `<button type="button" class="dash-chip dash-row${F.topic === x.key ? " on" : F.topic ? " off" : ""}" data-d="topic" data-v="${esc(x.key)}"
          title="보도자료 ${x.by["그린광학 보도자료"] || 0} · 외부 ${x.by["외부 기사"] || 0}">${esc(x.key)}<b>${x.n}</b></button>`).join("")
        : `<p class="muted">해당 기사가 없습니다.</p>`;
    }
    function spark(rows) {
      if (rows.length < 2) return "";
      const last = rows.slice(-12), mx = Math.max(1, ...last.map((x) => x.n));
      const pts = last.map((x, i) => `${(i / (last.length - 1)) * 100},${28 - (x.n / mx) * 24}`).join(" ");
      return `<svg class="kpi-spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}"/></svg>`;
    }
    function draw() {
      const since = pivotState.range === "all" ? "" : isoDate(new Date(Date.now() - +pivotState.range * 864e5));
      base = pivotRows(items, pivotState.unit).filter((r) => (!since || r.ts.slice(0, 10) >= since)
        && (pivotState.kind === "all" || r.kind === pivotState.kind));
      shown = base.filter((r) => pass(r)).sort((x, y) => (x.ts < y.ts ? 1 : -1));
      const offices = new Set(shown.map((r) => r.office)).size;
      const monthsAll = dashAgg(shown, "month");
      const prN = shown.filter((r) => r.kind === "그린광학 보도자료").length, extN = shown.length - prN;
      const share = (n) => (shown.length ? `${Math.round((n / shown.length) * 100)}%` : "-");
      $("#dash-kpi").innerHTML = `
        <div class="kpi"><small>보도</small><b>${shown.length}<em>건</em></b>${spark(monthsAll)}</div>
        <div class="kpi k-pr"><small><i class="mk pr"></i>그린광학 보도자료</small><b>${prN}<em>건</em></b><span class="muted">전체의 ${share(prN)}</span></div>
        <div class="kpi k-ext"><small><i class="mk ext"></i>외부 기사</small><b>${extN}<em>건</em></b><span class="muted">전체의 ${share(extN)}</span></div>
        <div class="kpi"><small>언론사</small><b>${offices}<em>곳</em></b><span class="muted">기자 확인 ${new Set(shown.filter((r) => r.reporter).map((r) => r.reporter + r.office)).size}명</span></div>`;
      $("#dash-chips").innerHTML = Object.keys(F).length ? Object.entries(F).map(([d, v]) => `<button type="button" class="chip-f" data-d="${d}" data-v="${esc(v)}">${DIMS[d]}: ${esc(v)} ✕</button>`).join("")
        + `<button type="button" class="btn-link" id="dash-clear">모두 풀기</button>` : "";
      dashLine($("#dash-month"), dashAgg(base.filter((r) => pass(r, "month")), "month"), F.month);
      board($("#dash-office"), "office");
      board($("#dash-reporter"), "reporter");
      tiles($("#dash-topic"));
      $("#dash-n").textContent = `${shown.length}건`;
      // 기사 목록: 달 고르기(전체 또는 한 달) + 10건씩 쪽 넘기기
      const months = dashAgg(base.filter((r) => pass(r, "month")), "month").map((x) => x.key).reverse();
      $("#dash-feed-month").innerHTML = `<option value="">전체 기간</option>${months.map((m) => `<option value="${esc(m)}"${F.month === m ? " selected" : ""}>${+m.slice(0, 4)}년 ${+m.slice(5, 7)}월</option>`).join("")}`;
      const pg = pivotState.pages;
      pg.feed = Math.min(pg.feed, Math.max(0, Math.ceil(shown.length / DASH_PER) - 1));
      $("#dash-feed-pager").innerHTML = dashPager("feed", pg.feed, shown.length);
      $("#dash-list").innerHTML = shown.slice(pg.feed * DASH_PER, (pg.feed + 1) * DASH_PER).map((r) => `<li><div>
        <a href="#/pr/${esc(r.lead)}" class="t">${esc(r.title)}</a>
        <div class="meta"><span class="ktag ${r.kind === "그린광학 보도자료" ? "k-pr" : "k-ext"}">${r.kind === "그린광학 보도자료" ? "보도자료" : "외부"}</span>
          <span class="muted">${r.ts.slice(0, 10)} ${hhmm(r.ts)} · ${esc(r.office)}${r.reporter ? ` · ${esc(r.reporter)} 기자` : ""} · ${esc(r.topic)}</span>
          ${r.url ? `<a class="muted" href="${esc(r.url)}" target="_blank" rel="noopener">원문</a>` : ""}</div></div></li>`).join("")
        || `<li class="empty"><strong>조건에 맞는 기사가 없습니다.</strong></li>`;
    }
    draw();
    document.querySelectorAll(".dash-q").forEach((inp) => {      // 언론사·기자 이름 찾기 (그 칸만 다시 그림 → 입력 중 커서 유지)
      const dim = inp.dataset.q;
      inp.value = pivotState.q[dim] || "";
      inp.oninput = () => { pivotState.q[dim] = inp.value.trim(); pivotState.pages[dim] = 0; board($(`#dash-${dim}`), dim); };
    });
    let rz = null;                    // 창 폭이 바뀌면 추이 그래프를 다시 (대시보드를 떠나면 한 번만 남는 처리기는 아무것도 안 함)
    if (dashResize) window.removeEventListener("resize", dashResize);
    dashResize = () => { clearTimeout(rz); rz = setTimeout(() => { if ($("#dash-month")) draw(); }, 200); };
    window.addEventListener("resize", dashResize);
    const firstPages = () => { const p = pivotState.pages; p.office = p.reporter = p.feed = 0; };     // 조건이 바뀌면 첫 쪽부터
    const toggle = (d, v) => { if (F[d] === v) delete F[d]; else F[d] = v; firstPages(); draw(); };
    $("#dash-feed-month").onchange = (ev) => { const v = ev.target.value; if (v) F.month = v; else delete F.month; firstPages(); draw(); };
    $("#piv-ctl").onclick = (ev) => {
      const b = ev.target.closest("button[data-k]"); if (!b) return;
      pivotState[b.dataset.k] = b.dataset.v; firstPages(); drawCtl(); draw();
    };
    // main은 화면을 바꿔도 그대로라, 대시보드를 다시 열 때마다 처리기가 쌓이면 한 번 눌러도 두 번 토글되어 풀려 버림 → 이전 것은 떼고 붙임
    if (dashPick) { main.removeEventListener("pick", dashPick); main.removeEventListener("click", dashClick); }
    dashPick = (ev) => toggle(ev.detail.d, ev.detail.v);
    dashClick = (ev) => {
      if (!$("#dash-list")) return;
      if (ev.target.closest("#dash-clear")) { for (const k of Object.keys(F)) delete F[k]; firstPages(); draw(); return; }
      const pb = ev.target.closest(".pg-btn[data-pg-k]");
      if (pb) {
        pivotState.pages[pb.dataset.pgK] = +pb.dataset.pg; draw();
        if (pb.dataset.pgK === "feed") $("#dash-list").closest(".dash-card").scrollIntoView({ block: "start", behavior: "smooth" });
        return;
      }
      const b = ev.target.closest(".dash-row, .chip-f"); if (!b) return;
      toggle(b.dataset.d, b.dataset.v);
    };
    main.addEventListener("pick", dashPick);
    main.addEventListener("click", dashClick);
    $("#piv-csv").onclick = () => csv("기사_지금조건", shown);
    $("#piv-csv-all").onclick = () => csv("기사_원본전체", pivotRows(items, "outlet").sort((x, y) => (x.ts < y.ts ? 1 : -1)));
  }

  async function pagePR(selId) {
    main.innerHTML = `<div class="wrap">
      <div class="page-head"><div><h1 class="page-title">뉴스</h1>
        <p class="page-sub" id="art-sub"></p></div>${artViewSeg(false)}</div>
      <div id="pr-score"></div>
      <div class="controls"><div class="seg" id="art-cat" aria-label="기사 구분"></div>
        <input type="search" class="art-search" id="art-q" placeholder="제목·매체·제출인 검색" aria-label="기사·공시 검색" autocomplete="off"><span id="art-day"></span>
        <div class="art-cond-wrap"><button type="button" class="art-dd" id="art-cond" aria-expanded="false" aria-controls="art-pop">조건 ▾ <b id="art-cond-txt"></b></button><div class="art-pop" id="art-pop" role="dialog" aria-label="목록 조건" hidden></div></div>
        <label class="art-dd">정렬 <select id="art-sort" aria-label="정렬"><option value="time">최신순</option><option value="abs">반응 큰 순</option><option value="up">많이 오른 순</option><option value="down">많이 내린 순</option></select></label>
        <button class="btn small" type="button" id="art-csv" title="지금 목록의 공개 뒤 주가 (+30·+60분)">엑셀로</button>
        <details class="art-more editable"><summary aria-label="기사 도구">⋯</summary><div class="art-more-menu">
          <button class="btn-link" type="button" id="art-multi" title="기사 여러 개를 골라 한 번에 삭제하거나 구분을 바꿉니다">여러 개 고르기</button>
          <button class="btn-link" type="button" id="pr-toggle" title="자동으로 모이지 않은 보도자료나 정확한 배포 시각을 아는 기사를 직접 넣습니다">기사 등록</button></div></details>
        <span class="editable art-act" id="art-act"></span></div>
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
      <div class="art-bulk editable" id="art-bulk" hidden></div>
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
    let rx;
    try { [d, dd, rx] = await Promise.all([api("/api/articles"), api("/api/disclosures").catch(() => null), api("/api/reactions").catch(() => ({ items: [] }))]); }
    catch (e) { $("#art-list").innerHTML = `<li class="empty"><strong>기사를 불러오지 못했습니다.</strong>${esc(e.message)}</li>`; return; }
    const items = d.items;
    const discs = (dd && dd.items) || [];
    const DTYPES = { ...((dd && dd.types) || {}) };
    discById = new Map(discs.map((x) => [x.id, x]));
    const byId = new Map(items.map((a) => [a.id, a]));
    const rxById = new Map(((rx && rx.items) || []).map((r) => [r.id, r]));      // 공개 뒤 주가 (뉴스 반응과 같은 자료)
    // 공유된 사건 주소(#/react/event/ne-…)로 들어오면 그 사건의 기사로
    if (selId && selId.startsWith("ne-")) { const r = [...rxById.values()].find((x) => x.event_id === selId); selId = r ? r.id : null; history.replaceState(null, "", selId ? `#/pr/${selId}` : "#/pr"); }
    // 목록 한 줄 = 같은 내용 묶음 하나 (같은 날이라도 내용이 다르면 따로)
    const events = (items.map((a) => ({ day: a.effect && a.effect.reaction_day, lead: a.id, ids: [a.id],
      kind: a.kind || (a.manual ? "pr" : a.category === "회사 발표" ? "company" : "ext"), outlets: a.outlets_total || 1,
      noise: ["단순 언급", "시황·특징주"].includes(a.category) }))).filter((e) => byId.has(e.lead));
    items.forEach((a) => { if (!a.kind) a.kind = a.manual ? "pr" : a.category === "회사 발표" ? "company" : "ext"; });
    const groups = {
      pr: events.filter((e) => e.kind === "pr" || e.kind === "company"),
      ext: events.filter((e) => e.kind !== "pr" && e.kind !== "company" && !e.noise),
    };
    const LABEL = { pr: "그린광학 보도자료", ext: "외부 기사", disc: "공시" };
    // 공시: 종류(DART 분류 + 거래소 조치) 버튼으로 거름. discTypes가 null이면 '전체'(처음 기본값),
    // Set이면 고른 종류만 (전체 상태에서 한 종류를 누르면 그것만, 더 누르면 더해지고, 다 빼면 다시 전체)
    if (artState.discTypes === undefined) artState.discTypes = null;
    const discShown = () => discs.filter((x) => !artState.discTypes || artState.discTypes.has(x.pblntf || "?"));
    groups.disc = discShown();

    const renderScore = () => { $("#pr-score").innerHTML = countLine(artState.cat); };     // 소식 없는 날 비교는 README로
    // 지금 탭에 몇 건이 실렸는지 (같은 반응일 기사는 한 건으로 셈). 공시 탭은 켜 둔 종류만
    const since = isoDate(new Date(Date.now() - 183 * 864e5));
    function countLine(cat) {
      const isDisc = cat === "disc";
      const list = isDisc ? groups.disc : groups[cat];
      const dayOf = (x) => (isDisc ? x.ts : x.day || (byId.get(x.lead) || {}).ts || "").slice(0, 10);
      const recent = list.filter((x) => dayOf(x) >= since);
      const first = list.map(dayOf).filter(Boolean).sort()[0];
      const label = isDisc ? "선택한 종류 공시" : cat === "pr" ? "그린광학 보도자료" : "외부 기사";
      const outs = (x) => x.outlets || 1;
      const avgOut = recent.length ? recent.reduce((sum, x) => sum + outs(x), 0) / recent.length : null;
      const media = list.reduce((sum, x) => sum + outs(x), 0);
      return `<div class="score-line">
        <span><small>최근 6개월 ${label}</small><b>${recent.length}건</b></span>
        <span><small>한 달 평균</small><b>${(recent.length / 6).toFixed(1)}건</b></span>
        ${isDisc ? `<span><small>시각을 아는 공시 (6개월)</small><b>${recent.filter((x) => !x.untimed).length}건</b></span>`
          : `<span><small>한 건당 실린 매체 (6개월 평균)</small><b>${avgOut != null ? avgOut.toFixed(1) + "곳" : "-"}</b></span>`}
        <span><small>전체${first ? ` (${esc(first)}부터)` : ""}</small><b>${list.length}건</b>${isDisc ? (typeof discs !== "undefined" && list.length !== discs.length ? ` <small>공시 전체 ${discs.length}건 중</small>` : "")
          : ` <small>보도 ${media}건</small>`}</span></div>`;
    }
    function baselineBlock() { return baselineHtml(d.baseline); }

    if (!["pr", "ext", "disc"].includes(artState.cat)) artState.cat = "pr";
    // 검색: 지금 탭 목록을 제목·매체(기사) / 제목·제출인·종류(공시)로 거름. 같은 날 묶인 기사 중 하나라도 맞으면 그 사건을 보여줌
    const norm = (t) => String(t || "").toLowerCase().replace(/\s+/g, "");
    const hit = (...xs) => { const q = norm(artState.q); return !q || xs.some((x) => norm(x).includes(q)); };
    const onDay = (ts) => !artState.day || (!!ts && ts.slice(0, 10) === artState.day);
    // 조건: 기간 · 공개 시점(장 전/장중/15시 이후, 반응 자료 기준) · 주제
    const inRange = (ts) => { const dd0 = (ts || "").slice(0, 10), r = artState.range;
      if (r === "all") return true;
      if (r === "custom") return (!artState.from || dd0 >= artState.from) && (!artState.to || dd0 <= artState.to);
      return dd0 >= isoDate(new Date(Date.now() - +r * 864e5)); };
    const condOk = (id, ts, topic) => { const r = rxById.get(id);
      return inRange(ts) && (artState.mode === "all" || (r && r.mode === artState.mode)) && (!artState.topic || (topic || (r && r.topic)) === artState.topic); };
    const shown = (cat) => sortList(cat, cat === "disc" ? groups.disc.filter((x) => onDay(x.ts) && condOk(x.id, x.ts) && hit(x.title, x.filer, x.kind))
      : groups[cat].filter((e) => condOk(e.lead, (byId.get(e.lead) || {}).ts, (byId.get(e.lead) || {}).topic) && e.ids.some((id) => {
        const a = byId.get(id);
        // 같은 내용으로 묶인 다른 매체(related)의 매체·제목·기자도 찾음 (대표 기사가 다른 매체여도 '블로터'로 걸리게)
        const rel = (a && a.related || []).flatMap((r) => [r.office, r.title, r.reporter]);
        return a && onDay(a.ts) && hit(a.title, a.office, a.category, a.reporter, ...rel);
      })));
    const rxOf = (x) => rxById.get(x.lead || x.id);
    const rxVal = (x) => { const r = rxOf(x); return r ? stepV(r, reactState.h) : null; };
    function sortList(cat, list) {
      if (artState.sort === "time") return list;
      const key = artState.sort === "abs" ? (v) => -Math.abs(v) : artState.sort === "up" ? (v) => -v : (v) => v;
      return [...list].sort((p, q) => { const a = rxVal(p), b = rxVal(q); if (a == null) return b == null ? 0 : 1; if (b == null) return -1; return key(a) - key(b); });
    }
    const rxCell = (x) => { const v = rxVal(x); return `<span class="al-rx ${sgnCls(v)}" title="공개 뒤 +${reactState.h}분${reactState.vsKq ? " (KOSDAQ 대비)" : ""}">${v == null ? "-" : reactState.vsKq ? points(v) : pct(v)}<small>+${reactState.h}분</small></span>`; };
    const currentRows = () => { const list = shown(artState.cat); return list.map(rxOf).filter(Boolean); };
    // 조건 버튼
    const topicsAll = [...new Set([...items.map((a) => a.topic), ...[...rxById.values()].map((r) => r.topic)].filter(Boolean))].sort();
    const RANGES = [["all", "전체"], ["92", "최근 3달"], ["183", "6달"], ["365", "1년"], ["custom", "직접 고르기"]];
    const MODES = [["all", "전체"], ["open", "장 시작 전"], ["session", "장중"], ["late", "15시 이후"]];
    const condText = () => [artState.range === "all" ? "전체 기간" : artState.range === "custom" ? `${artState.from || "…"}~${artState.to || "…"}` : artState.range === "365" ? "최근 1년" : RANGES.find((r) => r[0] === artState.range)[1].replace(/^(\d)/, "최근 $1"),
      artState.mode !== "all" ? MODES.find((m) => m[0] === artState.mode)[1] : "", artState.topic].filter(Boolean).join(" · ");
    const pop = $("#art-pop"), condBtn = $("#art-cond");
    const drawCond = () => {
      $("#art-cond-txt").textContent = condText();
      const segB = (key, opts) => `<div class="seg" role="group">${opts.map(([v, t]) => `<button type="button" data-ck="${key}" data-cv="${esc(v)}" aria-pressed="${artState[key] === v}">${esc(t)}</button>`).join("")}</div>`;
      pop.innerHTML = `<h4>기간</h4>${segB("range", RANGES)}${artState.range === "custom" ? `<p class="art-pop-dates"><input type="date" id="art-from" value="${esc(artState.from)}" aria-label="시작 날짜"> ~ <input type="date" id="art-to" value="${esc(artState.to)}" aria-label="끝 날짜"></p>` : ""}
        <h4>공개 시점</h4>${segB("mode", MODES)}<h4>주제</h4>${segB("topic", [["", "전체"], ...topicsAll.map((t) => [t, t])])}
        <p class="art-pop-foot">목록과 오른쪽 그래프에 같이 적용됩니다.</p>`;
    };
    condBtn.onclick = (ev) => { ev.stopPropagation(); pop.hidden = !pop.hidden; condBtn.setAttribute("aria-expanded", String(!pop.hidden)); };
    pop.onclick = (ev) => { ev.stopPropagation(); const b = ev.target.closest("button[data-ck]"); if (!b) return;
      artState[b.dataset.ck] = b.dataset.cv;
      if (artState.range === "custom" && !artState.from) { artState.to = isoDate(new Date()); artState.from = isoDate(new Date(Date.now() - 92 * 864e5)); }
      drawCond(); renderList(); followTab(); };
    pop.onchange = (ev) => { if (ev.target.id === "art-from" || ev.target.id === "art-to") { artState[ev.target.id === "art-from" ? "from" : "to"] = ev.target.value; drawCond(); renderList(); followTab(); } };
    const closePop = (ev) => {
      if (pop.hidden || !pop.isConnected) return;
      if (ev.type === "keydown" ? ev.key === "Escape" : !ev.target.closest(".art-cond-wrap")) { pop.hidden = true; condBtn.setAttribute("aria-expanded", "false"); if (ev.type === "keydown") condBtn.focus(); }
    };
    if (artPopClose) { document.removeEventListener("click", artPopClose); document.removeEventListener("keydown", artPopClose); }     // 화면을 다시 그려도 하나만
    artPopClose = closePop; document.addEventListener("click", closePop); document.addEventListener("keydown", closePop);
    drawCond();
    const sortSel = $("#art-sort");
    sortSel.value = artState.sort;
    sortSel.onchange = () => { artState.sort = sortSel.value; renderList(); };
    $("#art-csv").onclick = () => reactionCsv(currentRows());
    const dayBox = $("#art-day");
    const renderDay = () => { dayBox.innerHTML = artState.day ? `<button type="button" class="chip-f" title="날짜 거르기 풀기">${+artState.day.slice(5, 7)}/${+artState.day.slice(8, 10)} 소식만 ✕</button>` : ""; };
    dayBox.onclick = () => { artState.day = null; renderDay(); renderList(); followTab(); };
    renderDay();
    const qBox = $("#art-q");
    qBox.value = artState.q || "";
    qBox.oninput = () => { artState.q = qBox.value; renderList(); followTab(); };
    const seg = $("#art-cat");
    seg.innerHTML = Object.keys(LABEL).map((v) => `<button type="button" data-v="${v}" aria-pressed="${artState.cat === v}">${LABEL[v]} <small></small></button>`).join("");
    seg.onclick = (ev) => {
      const b = ev.target.closest("button"); if (!b) return;
      artState.cat = b.dataset.v;
      if (multi.on) { multi.ids.clear(); multi.cat = artState.cat; if (artState.cat === "disc") multi.on = false; }
      drawBulk(); renderList(); followTab();
    };
    const typeBox = $("#disc-types");
    const typeCount = (t) => discs.filter((x) => (x.pblntf || "?") === t).length;
    const typeKeys = Object.keys(DTYPES).filter((t) => typeCount(t));
    if (discs.some((x) => !x.pblntf)) { typeKeys.push("?"); DTYPES["?"] = "분류 없음"; }
    function renderTypes() {
      const S = artState.discTypes;           // null = 전체 (전체만 채워지고 나머지는 빈 버튼)
      typeBox.innerHTML = `<button type="button" data-t="*" aria-pressed="${!S}">전체 <small>${discs.length}</small></button>`
        + typeKeys.map((t) => `<button type="button" data-t="${t}" aria-pressed="${!!S && S.has(t)}">${esc(DTYPES[t])} <small>${typeCount(t)}</small></button>`).join("");
    }
    typeBox.onclick = (ev) => {
      const b = ev.target.closest("button[data-t]"); if (!b) return;
      const t = b.dataset.t;
      if (t === "*") artState.discTypes = null;                       // 전체로
      else if (!artState.discTypes) artState.discTypes = new Set([t]);  // 전체 상태에서 누르면 그 종류만
      else {
        const S = artState.discTypes;
        if (S.has(t)) S.delete(t); else S.add(t);                     // 더하거나 빼기
        if (!S.size || typeKeys.every((k) => S.has(k))) artState.discTypes = null;   // 다 빼거나 다 고르면 전체
      }
      groups.disc = discShown();
      renderList();
      followTab();
    };
    // 탭·검색·조건을 바꾸면: 보던 기사가 목록에 있으면 그대로(겹치는 다른 기사만 새 목록으로), 없으면 오른쪽은 겹쳐 보기
    function followTab() {
      const list = shown(artState.cat);
      const flat = artState.cat === "disc";
      const ids = flat ? list.map((x) => x.id) : list.flatMap((e) => e.ids);
      if (current && ids.includes(current)) { loadPR(current); return; }
      current = null;
      history.replaceState(null, "", "#/pr");
      renderList();
      showOverview();
    }
    // 아무것도 고르지 않았을 때 오른쪽: 지금 목록의 기사 겹쳐 보기 (뉴스 반응 그래프)
    function showOverview() {
      const box = $("#pr-detail"), act = $("#art-act");
      if (act) act.innerHTML = "";
      const rows = currentRows();
      if (!shown(artState.cat).length) { box.innerHTML = `<div class="empty"><strong>${artState.q ? "검색에 맞는 항목이 없습니다." : artState.cat === "disc" ? "보여줄 공시가 없습니다." : "보여줄 기사가 없습니다."}</strong>위 조건을 넓혀 보세요.</div>`; return; }
      reactionOverview(box, rows, { title: `공개 뒤 주가 · ${LABEL[artState.cat]}`, onPick: (id) => openInPlace(id) });
    }

    function renderList() {
      $("#art-list").classList.toggle("is-multi", multi.on && artState.cat !== "disc");
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
          const ef = x.effect || {};
          return `<li><button type="button" data-id="${esc(x.id)}" aria-current="${x.id === current}">
          <span class="al-meta"><span class="ktag k-disc">공시</span>${catTag(x.kind || "공시")}<span class="muted">${dayLabel(x.ts)} ${x.untimed ? "시각 미상" : hhmm(x.ts)}</span>${artFlags(ef)}</span>
          <span class="t">${esc(x.title)}</span>
          <span class="al-foot">${verdictTag(ef)}<span class="muted">${esc(x.filer || "")}</span>${(x.links || []).length ? `<span class="muted">같은 내용 기사 ${x.links.length}건</span>` : ""}</span>${rxCell(x)}</button></li>`;
        }).join("");
        ul.onclick = (ev) => { const b = ev.target.closest("button[data-id]"); if (b) openInPlace(b.dataset.id); };
        return;
      }
      const list = shown(artState.cat);
      if (!list.length) {
        ul.innerHTML = `<li class="empty"><strong>${artState.q ? "검색에 맞는 기사가 없습니다." : artState.day ? "이날 이 구분의 기사가 없습니다." : "해당 기사가 없습니다."}</strong>${artState.day ? "위 날짜 표시의 ✕를 누르면 전체가 보입니다." : ""}</li>`;
        return;
      }
      ul.innerHTML = list.map((e) => {
        const a = byId.get(e.lead), ef = a.effect || {};
        const pickable = multi.on && !a.manual;
        return `<li><button type="button" data-id="${esc(a.id)}" aria-current="${a.id === current}"${multi.on ? ` aria-pressed="${multi.ids.has(a.id)}"${a.manual ? ' data-nopick="1" title="직접 등록한 기사는 하나씩 삭제합니다"' : ""}` : ""}>
          ${multi.on ? `<span class="pick-box${pickable ? "" : " off"}" aria-hidden="true"></span>` : ""}<span class="al-meta"><span class="ktag k-office">${esc(a.office || "-")}</span>${toneTag(a.tone)}<span class="muted">${dayLabel(a.ts)} ${hhmm(a.ts)}</span>${artFlags(ef, a, e)}</span>
          <span class="t">${esc(a.title)}</span>
          ${verdictTag(ef) || e.outlets > 1 ? `<span class="al-foot">${verdictTag(ef)}${e.outlets > 1 ? `<span class="muted">${e.outlets}개 매체</span>` : ""}</span>` : ""}${rxCell(e)}</button></li>`;
      }).join("");
      ul.onclick = (ev) => {
        const b = ev.target.closest("button[data-id]"); if (!b) return;
        if (!multi.on) { openInPlace(b.dataset.id); return; }
        if (b.dataset.nopick) return;            // 고르는 중에는 상세를 열지 않고 체크만
        const id = b.dataset.id;
        if (multi.ids.has(id)) multi.ids.delete(id); else multi.ids.add(id);
        b.setAttribute("aria-pressed", String(multi.ids.has(id)));
        drawBulk();
      };
    }
    // 여러 개 고르기: 보도자료·외부 기사 탭에서 골라 한 번에 삭제·구분 바꾸기 (요청 하나 = GitHub [Create] 한 번)
    const multi = { on: false, cat: null, ids: new Set() };
    function drawBulk() {
      const bar = $("#art-bulk"), btn = $("#art-multi");
      if (btn) { btn.hidden = artState.cat === "disc"; btn.textContent = multi.on ? "고르기 끝" : "여러 개 고르기"; }
      if (!bar) return;
      bar.hidden = !multi.on;
      if (!multi.on) { bar.innerHTML = ""; return; }
      const n = multi.ids.size, to = artState.cat === "pr" ? "ext" : "pr";
      bar.innerHTML = `<b>${n}건 선택</b><span class="muted">기사를 눌러 고르세요 (n개 매체 묶음은 묶음 전체가 함께 바뀝니다)</span><span class="spacer"></span>
        <button type="button" class="btn small" data-bulk="hide" ${n ? "" : "disabled"}>목록에서 삭제</button>
        <button type="button" class="btn small" data-bulk="${to}" ${n ? "" : "disabled"}>${to === "pr" ? "그린광학 보도자료로" : "외부 기사로"}</button>
        <button type="button" class="btn-link" data-bulk="cancel">취소</button><span class="form-msg" id="bulk-msg"></span>`;
    }
    $("#art-multi").onclick = () => {
      multi.on = !multi.on; multi.cat = artState.cat; multi.ids.clear();
      drawBulk(); renderList();
    };
    $("#art-bulk").onclick = async (ev) => {
      const b = ev.target.closest("[data-bulk]"); if (!b) return;
      const act = b.dataset.bulk, ids = [...multi.ids];
      if (act === "cancel") { multi.on = false; multi.ids.clear(); drawBulk(); renderList(); return; }
      if (!ids.length) return;
      if (act === "hide" && b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = `${ids.length}건 삭제 — 한 번 더 누르기`; return; }
      const title = `${ids.length}건 ${act === "hide" ? "목록에서 삭제" : act === "pr" ? "그린광학 보도자료로" : "외부 기사로"}`;
      const item = act === "hide" ? { type: "hide", ids, title } : { type: "article_kind", ids, kind: act, title };
      const msg = $("#bulk-msg");
      try {
        msg.className = "form-msg"; msg.textContent = await sendInbox(item);
        $("#art-bulk").querySelectorAll("button.btn").forEach((x) => { x.disabled = true; });
        b.textContent = `${title} 요청함`;
      } catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
    };
    drawBulk();
    let current = selId;
    // 목록에서 기사·공시를 누르면: 페이지를 다시 그리지 않고(스크롤 그대로) 오른쪽 상세만 바꿈. 주소는 바꿔 두어 뒤로 가기·공유 가능
    const openInPlace = (id) => {
      if (id === current) return;
      current = id;
      history.pushState(null, "", `#/pr/${id}`);
      $("#art-list").querySelectorAll("button[data-id]").forEach((x) => x.setAttribute("aria-current", String(x.dataset.id === id)));
      loadPR(id);
      if (window.matchMedia && window.matchMedia("(max-width: 900px)").matches) $("#pr-detail").scrollIntoView({ block: "start", behavior: "smooth" });
    };
    const closeDetail = () => {
      const was = current; current = null;
      history.pushState(null, "", "#/pr");
      $("#art-list").querySelectorAll("button[data-id]").forEach((x) => x.setAttribute("aria-current", "false"));
      showOverview();
      const b = was && [...$("#art-list").querySelectorAll("button[data-id]")].find((x) => x.dataset.id === was);
      if (b) b.focus({ preventScroll: true });
    };
    prHooks = {
      overlay: (id) => { const r = rxById.get(id); return r ? { ...reactionOverlay(currentRows(), id), label: LABEL[artState.cat] } : null; },
      openEvent: (eid) => { const r = [...rxById.values()].find((x) => x.event_id === eid); if (!r) return;
        const inList = shown(artState.cat).some((x) => (x.ids || [x.id]).includes(r.id));
        if (inList) openInPlace(r.id); else location.hash = `#/pr/${r.id}`; },
      close: closeDetail,
    };
    if (selId && discById.has(selId)) {
      artState.cat = "disc";
      const x = discById.get(selId);
      if (artState.discTypes && !artState.discTypes.has(x.pblntf || "?")) { artState.discTypes.add(x.pblntf || "?"); groups.disc = discShown(); }
    } else if (selId) {
      if (!byId.has(selId)) {               // 합쳐져 묶음 대표가 아니게 된 기사 주소 → 그 묶음의 대표 기사로
        const lead = items.find((a) => (a.related || []).some((r) => r.id === selId));
        if (lead) { selId = current = lead.id; history.replaceState(null, "", `#/pr/${lead.id}`); }
      }
      const ev = events.find((e) => e.ids.includes(selId));
      if (ev) artState.cat = ev.kind === "pr" || ev.kind === "company" ? "pr" : "ext";
    }
    if (current && !shown(artState.cat).some((x) => (x.ids || [x.id]).includes(current) || x.lead === current)) { artState.range = "all"; artState.mode = "all"; artState.topic = ""; drawCond(); }   // 주소로 연 기사가 조건 밖이면 조건을 풂
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
          if (main.querySelector("input:focus, textarea:focus, #pr-form:not([hidden]), #merge-list:not([hidden]), #art-bulk:not([hidden])")) return;
          const scrollPosition = window.scrollY;
          cleanupPage();
          await pagePR(current);
          window.scrollTo(0, scrollPosition);
        } catch (_) {
        } finally { checking = false; }
      }, 30000));
    }
    if (!items.length && !discs.length) { $("#pr-detail").innerHTML = `<div class="empty"><strong>아직 기사가 없습니다.</strong>자동 수집이 돌면 몇 분 안에 모입니다.</div>`; return; }
    if (!current) { showOverview(); return; }
    loadPR(current);
  }

  let discById = new Map();     // 기사·공시 모음 화면이 받아 둔 공시 (기사의 '공시 먼저'를 그 공시로 잇는 데 씀)
  const kindTag = (a) => `<span class="ktag ${a.kind === "pr" ? "k-pr" : a.kind === "disc" ? "k-disc" : "k-ext"}">${a.kind === "pr" ? "확인된 자체 보도자료" : a.kind === "company" ? "회사 관련 보도" : a.kind === "disc" ? "공시" : "외부 기사"}</span>${a.vol_rank != null && a.vol_rank <= 0.05 ? '<span class="tier watch" title="반응일 거래량이 직전 60거래일 중 상위 5% 안">거래량 급증</span>' : ""}`;
  const artFlags = (ef, row = null, event = null) => `${row && row.time_quality === "check_time" || event && event.time_check ? '<span class="tier watch" title="구글 뉴스 게시 시각이 반복되는 시간대입니다. 오류가 확정된 것은 아니며, 원문 시각 확인이 필요합니다">공개 시각 확인 필요</span>' : ""}${event && event.followup_of ? '<span class="tier watch">기존 정보 재보도</span>' : ""}${event && event.followup_candidate_of ? '<span class="tier watch">후속 보도 검토</span>' : ""}`;


  // 어떤 봉으로 쟀는지: 1분봉(네이버·한국투자증권) / 5분·60분봉(야후 파이낸스, 1분봉이 없는 날) — 출처는 툴팁으로
  const BAR_TIP = "1분봉은 기록된 관측 시각, 야후 5분·60분봉은 봉 종료 시각 기준입니다. 기준·종료 시각의 가격이 없으면 계산하지 않습니다. 60분봉만으로 30분 사이의 등락을 추정하지 않습니다.";
  const barLabel = (iv, kv) => `<span title="${BAR_TIP}">${iv || "-"}분봉 기준${kv && kv !== iv ? ` (KOSDAQ ${kv}분봉)` : ""}</span>`;
  // 단순 숫자: +N분 이 종목 · KOSDAQ · 차이 (통계 없이 그대로)
  const simpleSteps = (rows, main) => !rows.length ? `<p class="note">기준·종료 시각의 가격이 부족해 계산할 수 없습니다.</p>` : `<table class="simple-steps"><thead><tr><th></th>${rows.map((r) => `<th${r.minutes === main ? ' class="main"' : ""}>+${r.minutes}분</th>`).join("")}</tr></thead><tbody>
      <tr><th>이 종목</th>${rows.map((r) => `<td class="${sgnCls(r.own)}${r.minutes === main ? " main" : ""}">${pct(r.own)}</td>`).join("")}</tr>
      <tr><th>KOSDAQ</th>${rows.map((r) => `<td class="${sgnCls(r.kq)}">${pct(r.kq)}</td>`).join("")}</tr>
      <tr><th>차이</th>${rows.map((r) => { const v = r.own != null && r.kq != null ? (Object.hasOwn(r,"diff") ? r.diff : r.own - r.kq) : null; return `<td class="${sgnCls(v)}">${points(v)}</td>`; }).join("")}</tr></tbody></table>`;
  // 공개 시각 출처 (모든 분석의 기준이라 늘 보여 줌). 구글 뉴스 16·17시처럼 의심스러우면 표시
  const timeSrc = (a) => ` <span class="time-src${a.time_quality === "check_time" ? " warn" : ""}" title="공개 시각을 어디서 읽었는지">시각 출처: ${esc(a.time_source || "-")}${a.time_quality === "check_time" ? " · 확인 필요" : ""}</span>`
    + (a.manual ? "" : ` <button type="button" class="btn-link editable" data-time-open>시각 고치기</button>`);
  function timeForm(a) {
    if (a.manual) return "";
    const v = (a.own_ts || a.ts || "").slice(0, 16);
    return `<form class="time-form editable" data-time-form hidden>
      <label>공개 시각 <input type="datetime-local" data-time-v value="${esc(v)}" required></label>
      <button class="btn small primary" type="submit">저장</button>
      ${a.ts_auto ? `<button class="btn small" type="button" data-time-reset title="자동으로 읽은 시각(${esc(a.ts_auto.slice(5, 16).replace("T", " "))})으로">원래대로</button>` : ""}
      <span class="muted">기사 원문에 적힌 입력·승인 시각을 넣으세요. 반응 계산이 이 시각 기준으로 다시 됩니다.</span>
      <span class="form-msg" data-time-msg></span></form>`;
  }
  function bindTimeForm(box, a) {
    const open = $("[data-time-open]", box), form = $("[data-time-form]", box);
    if (!open || !form) return;
    const msg = $("[data-time-msg]", form);
    const send = async (ts) => {
      try { msg.className = "form-msg"; msg.textContent = await sendInbox({ type: "article_time", id: a.id, ts, title: a.title }); }
      catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
    };
    open.onclick = () => { form.hidden = !form.hidden; };
    form.onsubmit = (ev) => { ev.preventDefault(); const v = $("[data-time-v]", form).value; if (v) send(v); };
    const rs = $("[data-time-reset]", form);
    if (rs) rs.onclick = () => send(null);
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
    let html = `      <div class="pr-close-row"><span class="obs-eyebrow">선택한 ${noun}</span><button type="button" class="btn small" id="pr-close" aria-label="선택 해제하고 지금 목록 겹쳐 보기로">✕ 전체로</button></div>
      <h2 class="pr-title">${esc(a.title)}</h2>
      <p class="pr-meta">${isDisc ? `${a.office ? `${esc(a.office)} 제출 · ` : ""}${dayLabel(a.ts)} ${a.untimed ? "접수 (시각 미상)" : `${hhmm(a.ts)} 접수`}${a.url ? ` · <a href="${esc(a.url)}" target="_blank" rel="noopener">${a.pblntf === "K" ? "KIND에서 원문 보기" : "DART에서 원문 보기"}</a>` : ""}`
        : `${esc(a.office)} · ${dayLabel(a.own_ts || a.ts)} ${hhmm(a.own_ts || a.ts)} 공개${timeSrc(a)}${a.outlets_total > 1 ? ` · 같은 내용 ${a.outlets_total}개 매체` : ""}${a.url ? ` · <a href="${esc(a.url)}" target="_blank" rel="noopener">기사 열기</a>` : ""}`}
        ${a.ts ? (() => {      // 장 마감 뒤·휴장일 공개는 반응일(다음 거래일) 차트 → 날짜를 버튼에 적음
          const rd = e.reaction_day || a.ts.slice(0, 10), other = rd !== a.ts.slice(0, 10);
          const ck = isDisc ? "disc" : a.kind === "pr" || a.kind === "company" ? "pr" : "ext";
          return `<button type="button" class="btn small pr-daychart" data-daychart="${esc(rd)}" data-cur="${esc(a.id)}" data-cur-ts="${a.untimed ? "" : esc(a.ts)}" data-cur-k="${ck}" data-cur-title="${esc(a.title)}">${other ? `반응일(${+rd.slice(5, 7)}/${+rd.slice(8, 10)}) 주가 차트` : "그날 주가 차트"}</button>`;
        })() : ""}</p>`;
    if (!isDisc) html += timeForm(a);
    const analysisTs = d.observation_analysis_at || e.base_ts || (e.disclosure && e.disclosure.ts) || a.ts;
    const basisReason = e.disclosure ? "disclosure" : analysisTs !== (a.own_ts || a.ts) ? "story" : "publication";
    const windowInfo = d.observation?.intraday || d.intraday || d.intraday_coarse;
    if (windowInfo && !a.untimed) html += `<p class="note">${esc(reactionBasisText({ ts: a.own_ts || a.ts, analysis_ts: analysisTs,
      basis_reason: d.observation ? "publication" : basisReason, start: windowInfo.start, mode: windowInfo.kind }))}</p>`;
    const notes = [];
    if (e.disclosure) {
      const dsc = [...discById.values()].find((x) => x.ts === e.disclosure.ts && x.title === e.disclosure.title);
      notes.push(`같은 내용의 공시(${dsc ? `<a href="#/pr/${esc(dsc.id)}">「${esc(e.disclosure.title)}」</a>` : `「${esc(e.disclosure.title)}」`})가 ${dayLabel(e.disclosure.ts)} ${hhmm(e.disclosure.ts)}에 먼저 나와, 선행 공시 연결 후보입니다. 사건 관찰 기록에서 같은 사건인지 확인·연결할 수 있습니다.`);
    }
    if (isDisc && a.untimed) notes.push("접수 시각 미상 → 그날 하루(전일 종가 → 종가)로 계산");
    if (e.off_session) notes.push(`장 마감 뒤·휴장일 공개 → 반응일 ${e.reaction_day ? esc(e.reaction_day) : "다음 거래일"}`);
    if (e.late && !d.intraday && !d.intraday_coarse) notes.push(a.ts && a.ts.slice(11, 16) > "15:00"
      ? "15시 넘어 공개 → 그날 남은 거래가 짧아 다음 거래일 장 시작 30분 반응으로 잽니다 (다음 거래일 09:30 뒤 그래프가 나옴)"
      : "오후 늦게(14시 이후) 공개");
    if (a.time_quality === "check_time" || e.time_quality === "check_time") notes.push("공개 시각 확인 필요: 구글 뉴스 시각이 16:00 또는 17:00으로 표시됨");
    // 발표 직후: 시간대별로 같은 방식(시장 몫 + 업종 몫 + 고유 움직임)으로 한 표에, 30분 줄을 대표로 (공개 시각이 확실하고 분봉이 있을 때)
    const iw = d.intraday;
    const ic = !iw && d.intraday_coarse;
    const span0 = (w) => (w.kind === "open" ? `장 시작 전 공개 → 전날 종가부터 ${esc(e.reaction_day)} 장 시작 뒤`
      : w.kind === "late" ? `15시 넘어 공개 → 그날 종가부터 다음 거래일(${esc(w.day)}) 장 시작 뒤` : `${hhmm(w.start)} 공개부터`);
    let numsHtml = "";
    if (iw) {
      const rows = iw.rows && iw.rows.length ? iw.rows : [{ minutes: iw.minutes, own: iw.own, market: iw.market, sector: iw.sector, abnormal: iw.abnormal }];
      numsHtml = `${simpleSteps(d.observation?.intraday ? Object.entries(d.observation.intraday.steps).map(([m,r])=>({minutes:+m,...r})) : rows.map((r) => ({ minutes: r.minutes, own: r.own, kq: r.kosdaq })), iw.minutes)}
        ${m ? `<div class="vb-note">120분 안 최고 ${pct(m.max_up)}(${hhmm(m.max_up_at)}), 최저 ${pct(m.max_down)}(${hhmm(m.max_down_at)})${m.vol_after30 != null ? ` · 공개 후 30분 거래량 ${int(m.vol_after30)}주${m.vol_usual30 ? `(평소 ${int(m.vol_usual30)}주)` : ""}` : ""}</div>` : ""}
        <details class="stat-more"><summary>통계로 보기 (참고)</summary>
          <div class="rx-steps">${rows.map((r) => `<span><small class="muted">+${r.minutes}분 고유 움직임</small> <b class="${sgnCls(r.abnormal)}">${points(r.abnormal)}</b></span>`).join("")}</div>
          <div class="vb-note">고유 움직임 = 이 종목 등락 − (시장·업종이 움직인 만큼 평소 관계로 예상한 등락). +${iw.minutes}분 기준으로 평소 같은 길이 구간 ${iw.n_null}개 중 이보다 크게 움직인 비율 <b>${iw.rarity < 0.01 ? "1% 미만" : `${Math.round(iw.rarity * 100)}%`}</b>${iw.segments_used ? "" : " · 업종 분봉 없음(시장 몫만 뺌)"}. 기사 하나만으로 판단하기엔 잡음이 큽니다.</div>
        </details>`;
    } else if (ic) {
      numsHtml = `${simpleSteps(d.observation?.intraday ? Object.entries(d.observation.intraday.steps).map(([m,r])=>({minutes:+m,...r})) : ic.rows.map((r) => ({ minutes: r.minutes, own: r.own, kq: r.kosdaq })), ic.rows.some((r) => r.minutes === 30) ? 30 : ic.rows[0] && ic.rows[0].minutes)}
        <div class="vb-note">기준·종료 시각에 기록된 가격만 사용합니다. '-'는 해당 시각 가격이 없어 미계산이며 보합이 아닙니다. KOSDAQ도 같은 두 시각의 가격이 있을 때만 비교합니다. 장 전·장 외의 기준가는 저장된 전 거래일 일봉 종가(종목은 시간외 포함 가능)입니다. 평소 관계를 뺀 '고유 움직임' 계산은 하지 않음.</div>`;
    }
    // 공개 뒤 주가: 한 차트에 이 기사 + KOSDAQ + (지금 목록의) 다른 기사·중앙값. 탭으로 전후 5거래일
    const win5 = d.observation?.windows?.["5"];
    if (iw || ic || d.reaction_chart || (win5 && (win5.points || []).length)) {
      const w = iw || ic || d.observation?.intraday;
      html += `<section class="verdict-box intraday-box"><div class="vb-head">공개 뒤 주가 <small class="muted">${w ? span0(w) : ""}${iw ? " · 1분봉 기준" : ic ? ` · ${barLabel(ic.interval, ic.kq_interval)}` : ""}</small></div>
        <div class="rx-tabs"><div class="seg" role="group" aria-label="관찰 기간"><button type="button" data-rxr="day" aria-pressed="true">공개 직후</button><button type="button" data-rxr="5" aria-pressed="false">전후 5거래일</button></div><span class="rx-cmp" id="rx-cmp"></span></div>
        <div class="rx-legend day-legend"></div><div class="rx-chart"></div><div class="obs-empty" id="rx-empty" hidden></div>
        <div class="obs-cards" id="pr-cards"></div><p class="note" id="rx-tab-note"></p>
        ${numsHtml ? `<details class="obs-fold"><summary><span>수치 자세히 · 계산 기준</span><small>+30·60·120분</small></summary><div class="obs-fold-body">${numsHtml}</div></details>` : ""}</section>`;
    }
    // 공개 시각 확인 같은 안내만 (반응일 하루 표는 뺌)
    if (notes.length) html += `<section class="verdict-box">${notes.map((x) => `<div class="vb-note">${x}</div>`).join("")}</section>`;
    // 한 줄 동작: AI 질문 만들기 · 결과 붙여넣기(사건 관찰 기록) · 구분 바꾸기 (직접 등록한 기사는 늘 그린광학 보도자료)
    const toKinds = !a.manual && !isDisc ? (a.kind === "pr" || a.kind === "company" ? ["ext"] : ["pr"]) : [];
    if (d.observation_event_id || toKinds.length) html += `<div class="obs-cta art-fix">${d.observation_event_id ? `<button type="button" class="btn primary" id="obs-question" disabled>✦ AI 질문 만들기</button><button type="button" class="btn" id="obs-note-open" disabled>결과 붙여넣기</button>` : ""}
        ${toKinds.map((k) => `<button class="btn editable" type="button" data-kind="${k}">${k === "pr" ? "그린광학 보도자료로 바꾸기" : "외부 기사로 바꾸기"}</button>`).join("")}<span class="form-msg" id="art-fix-msg"></span></div>`;
    html += `<div class="obs-folds">`;

    // 예상 대비 차이·95% 범위 같은 분석 숫자는 맨 위가 아니라 근거 쪽에 (판단은 사용자가)
    const ev = "";

    // 공시: 같은 내용이라 이 공시 시각부터 잰 기사들
    if (isDisc) {
      const ls = a.links || [];
      html += `<details class="obs-fold" data-fold="links"><summary><span>같은 내용의 보도자료·기사</span><small>${ls.length}건</small></summary><div class="obs-fold-body"><section class="outlet-list">
        ${ls.length ? `<ul>${ls.map((r) => `<li><span class="muted">${dayLabel(r.ts)} ${hhmm(r.ts)} · ${esc(r.office || "-")}</span> <a href="#/pr/${esc(r.id)}">${esc(r.title)}</a>${r.kind === "pr" ? ' <span class="ktag k-pr">그린광학 보도자료</span>' : ""}</li>`).join("")}</ul>`
          : `<p class="note">이 공시 뒤 36시간 안에 같은 내용(계약·실적·IR·증자·전환사채)으로 나온 기사가 없습니다.</p>`}</section></div></details>`;
      html += ev ? `<section class="evidence">${ev}</section>` : "";
    }
    // 이 기사를 실은 매체 전부 (링크), 링크를 모으지 못한 매체는 네이버 뉴스 검색으로
    const outlets = [{ id: a.id, manual: a.manual, merged: !!a.story_with, ts: a.own_ts || a.ts, office: a.office, title: a.title, url: a.url, lead: true }, ...(a.related_titles || [])];
    const missing = Math.max(0, (a.outlets_total || 1) - outlets.length);
    // 같은 기사가 두 번 들어온 경우(직접 등록 + 자동 수집 등) 한쪽을 지울 수 있게: 직접 등록은 삭제, 자동 수집은 이 줄만 숨김
    // 대표(목록에 보이는 제목·언론사): 다른 매체 줄의 '대표로'를 누르면 그 기사로 바꿈 (반응 계산 시각은 가장 먼저 나온 때 그대로)
    const leadBtn = (r) => (outlets.length < 2 ? ""
      : r.lead ? ` <span class="tier watch" title="목록에 보이는 제목·언론사">대표</span>${a.picked ? ` <button type="button" class="btn-link editable lead-pick" data-lead="" title="자동(직접 등록 → 가장 먼저 나온 기사)으로 되돌립니다">자동으로</button>` : ""}`
        : r.id ? ` <button type="button" class="btn-link editable lead-pick" data-lead="${esc(r.id)}" title="이 기사의 제목·언론사를 목록에 대표로 보여 줍니다">대표로</button>` : "");
    const link = (r) => `<li><span class="muted" title="시각 출처: ${esc(r.time_source || (r.lead ? a.time_source : "") || "-")}">${dayLabel(r.ts)} ${hhmm(r.ts)} · ${esc(r.office || "-")}</span>${leadBtn(r)} ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>` : esc(r.title)}${outlets.length > 1 && r.id ? ` <button type="button" class="btn-link editable dup-del" data-dup="${esc(r.id)}" data-manual="${r.manual ? 1 : 0}">삭제</button>` : ""}${r.merged ? ` <button type="button" class="btn-link editable unmerge" data-unmerge="${esc(r.id)}" title="직접 합친 기사(와 같이 들어온 기사)를 이 묶음에서 뺍니다">묶음에서 빼기</button>` : ""}</li>`;
    if (!isDisc) html += `<details class="obs-fold" data-fold="outlets"><summary><span>이 기사를 실은 매체 · 같은 내용 합치기</span><small>${a.outlets_total || outlets.length}곳</small></summary><div class="obs-fold-body"><section class="outlet-list"><p class="muted">기사 링크 ${outlets.filter((r) => r.url).length}개</p>
      <p class="note">같은 매체의 자사 사이트·포털 링크가 함께 있을 수 있습니다. 매체 수는 중복을 제외합니다.</p>
      <ul>${outlets.map(link).join("")}</ul>
      <span class="form-msg" id="dup-msg"></span>
      ${a.manual ? "" : `<div class="merge-box editable"><button type="button" class="btn-link" id="merge-open">같은 내용인데 따로 보이는 기사 합치기</button><div id="merge-list" hidden></div></div>`}
      ${missing ? `<p class="note">나머지 ${missing}곳은 네이버가 묶음으로만 알려줘 개별 링크가 없습니다 → <a href="https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(`"${a.title.slice(0, 40)}"`)}" target="_blank" rel="noopener">네이버 뉴스에서 같은 제목 찾기</a></p>` : ""}
</section></div></details>`;
    if (!isDisc) html += ev ? `<section class="evidence">${ev}</section>` : "";
    html += `<div id="obs-extras"></div></div>`;      // 사건 관찰 기록 접는 항목 (관련 소식 · 검토 기록 · 유사 사건 · 분류·연결 수정 · 분석 기준)
    box.innerHTML = html;
    const closeBtn = $("#pr-close");
    if (closeBtn) closeBtn.onclick = () => prHooks && prHooks.close();
    if (!isDisc) bindTimeForm(box, a);
    const act = $("#art-act");
    if (act) act.innerHTML = isDisc ? "" : a.manual ? `<button class="btn-link danger-link" type="button" id="pr-del">이 기사 삭제</button>`
      : `<button class="btn-link" type="button" id="art-hide" title="관계없는 기사를 목록에서 뺍니다 (다시 표시할 수 있음)">${a.hidden ? "목록에 다시 표시" : "목록에서 삭제"}</button>`;
    // 발표 직후 그래프: 이 종목·KOSDAQ를 공개 시각 기준 % 변화로 한 축에 (선 위 화살표 = 공개 시각)
    const rc = d.reaction_chart, rxEl = $(".rx-chart", box);
    if (rxEl) {
      const ov = prHooks && prHooks.overlay(a.id);
      const stepsD = d.observation?.intraday?.steps || {};
      const card = (label, v, unit = pct) => `<div><small>${label}</small><strong class="${sgnCls(v)}">${v == null ? "미집계" : unit(v)}</strong></div>`;
      const emptyBox = $("#rx-empty", box), legend = $(".rx-legend", box);
      let tabChart = null;
      const drawTab = (tab) => {
        if (tabChart) { try { tabChart.remove(); } catch (_) {} charts = charts.filter((c) => c !== tabChart); tabChart = null; }
        rxEl.innerHTML = ""; legend.innerHTML = ""; emptyBox.hidden = true; rxEl.hidden = false;
        box.querySelectorAll("[data-rxr]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.rxr === tab)));
        $("#rx-cmp", box).textContent = tab === "day" && ov && ov.n ? `비교: 지금 목록의 다른 ${ov.label} ${ov.n}건 · 중앙값은 +${ov.until}분까지` : "";
        const empty = (msg) => { rxEl.hidden = true; emptyBox.hidden = false; emptyBox.innerHTML = `<strong>이 구간에는 비교할 관측 시세가 없습니다.</strong><span>${esc(msg)} 없는 값은 만들지 않고 비워 둡니다.</span>`; };
        if (tab === "day") {
          if (rc && rc.base_own) tabChart = indexedChart(rxEl, legend, rc.own, rc.kq, rc.base_own, rc.base_kq, rc.start, rc.kind === "open" || rc.kind === "late" ? "장 시작" : basisReason === "publication" ? "공개" : "분석 기준", false, ov && ov.n ? ov : null);
          else empty(d.observation?.missing_reason ? `${d.observation.missing_reason}.` : "공개 시각 앞뒤의 분봉 시세가 없습니다." + (win5 ? " 일별 흐름은 [전후 5거래일]에서 볼 수 있습니다." : ""));
          $("#pr-cards", box).innerHTML = card("+30분", stepsD["30"]?.own) + card("+60분", stepsD["60"]?.own) + card("KOSDAQ +30분", stepsD["30"]?.kq)
            + (ov && ov.n ? card(`다른 기사 중앙값 +${ov.until}분`, ov.othersMedian) : card("시장 대비 +30분", stepsD["30"]?.diff, points));
          $("#rx-tab-note", box).textContent = "공개 직전 완료 시세 = 0%. 다른 기사·중앙값은 지금 목록 조건과 같은 기사이며 같은 사건은 1건만 셉니다. 기사의 직접 효과를 뜻하지 않습니다.";
        } else {
          const pts = (win5 && win5.points) || [], b0 = win5 && win5.base_own;
          if (pts.some((p) => p.stock_price != null) && b0) tabChart = indexedChart(rxEl, legend, pts.map((p) => ({ t: `${p.time}T09:00`, c: p.stock_price })), pts.map((p) => ({ t: `${p.time}T09:00`, c: p.kosdaq_price })), b0, win5.base_kq, `${d.observation.day}T09:00`, "반응일", true);
          else empty("이 기간에 저장된 일별 종가가 없습니다.");
          const post = (win5 && win5.post) || {};
          $("#pr-cards", box).innerHTML = card("공개 전 5거래일", win5 && win5.pre_trend) + card("공개 후 5거래일", post.own) + card("KOSDAQ", post.kq) + card("시장 대비", post.diff, points);
          $("#rx-tab-note", box).textContent = `${win5 && win5.base_day ? `${win5.base_day} 저장 종가 = 0%. ` : ""}공개 전에 이미 움직였는지, 공개 뒤 움직임이 이어졌는지 보는 참고용입니다. ${(win5 && win5.price_warning) || ""}`;
        }
      };
      box.querySelectorAll("[data-rxr]").forEach((b) => (b.onclick = () => drawTab(b.dataset.rxr)));
      drawTab("day");
    }
    if (d.observation_event_id) observationExtras($("#obs-extras", box), d.observation_event_id, { openEvent: (eid) => prHooks && prHooks.openEvent(eid) })
      .then(() => { box.querySelectorAll("#obs-question, #obs-note-open").forEach((b) => { b.disabled = false; }); });

    box.querySelectorAll("[data-ev]").forEach((x) => x.addEventListener("click", (ev2) => { ev2.preventDefault(); openEvent(+x.dataset.ev); }));
    box.querySelectorAll(".dup-del").forEach((b) => b.addEventListener("click", async () => {
      const msg = $("#dup-msg");
      if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = "한 번 더 누르면 삭제"; return; }
      const item = b.dataset.manual === "1" ? { type: "delete_pr", id: b.dataset.dup, title: a.title }
        : { type: "hide_one", id: b.dataset.dup, title: a.title };
      try { msg.className = "form-msg"; msg.textContent = await sendInbox(item); b.disabled = true; b.textContent = "삭제 요청함"; }
      catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
    }));
    // 합치기: 이 기사 앞뒤 3일 안의 다른 묶음 기사 중에서 골라 이 묶음에 넣음
    const mOpen = $("#merge-open");
    if (mOpen) mOpen.onclick = async () => {
      const lst = $("#merge-list");
      if (!lst.hidden) { lst.hidden = true; return; }
      lst.hidden = false; lst.innerHTML = `<p class="loading">불러오는 중…</p>`;
      let items = [];
      try { items = (await api("/api/articles")).items || []; } catch (err) { lst.innerHTML = `<p class="form-msg err">${esc(err.message)}</p>`; return; }
      const t0 = new Date(a.ts).getTime(), mine = new Set(outlets.map((r) => r.id));
      const near = items.filter((x) => !x.manual && !mine.has(x.id) && x.ts && Math.abs(new Date(x.ts).getTime() - t0) <= 3 * 864e5)
        .sort((p, q) => Math.abs(new Date(p.ts).getTime() - t0) - Math.abs(new Date(q.ts).getTime() - t0)).slice(0, 15);
      lst.innerHTML = near.length ? `<p class="note">이 기사와 같은 내용인 기사를 모두 고른 뒤 아래 버튼을 누르면 한 번에 이 묶음으로 합쳐집니다 (고른 기사와 이미 묶여 있던 기사도 함께). 앞뒤 3일, 가까운 순.</p>
        <ul class="merge-ul">${near.map((x) => `<li><label><input type="checkbox" value="${esc(x.id)}">
          <span class="muted">${dayLabel(x.ts)} ${hhmm(x.ts)} · ${esc(x.office || "-")}${(x.outlets_total || 1) > 1 ? ` · ${x.outlets_total}개 매체` : ""}</span>
          <span class="mt">${esc(x.title)}</span></label></li>`).join("")}</ul>
        <div class="merge-act"><button type="button" class="btn primary small" id="merge-go" disabled>고른 기사 합치기</button><span class="form-msg" id="merge-msg"></span></div>`
        : `<p class="note">앞뒤 3일 안에 합칠 만한 다른 기사가 없습니다.</p>`;
      const go = $("#merge-go");
      if (!go) return;
      const picked = () => [...lst.querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value);
      lst.onchange = () => { const n = picked().length; go.disabled = !n; go.textContent = n ? `고른 ${n}건 합치기` : "고른 기사 합치기"; };
      go.onclick = async () => {
        const ids = picked(), msg = $("#merge-msg");
        if (!ids.length) return;
        try {
          msg.className = "form-msg";
          msg.textContent = await sendInbox({ type: "article_merge", ids, into: a.id, title: a.title });
          go.disabled = true; go.textContent = `${ids.length}건 합치기 요청함`;
          lst.querySelectorAll("input[type=checkbox]").forEach((c) => { c.disabled = true; });
        } catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
      };
    };
    box.querySelectorAll(".lead-pick").forEach((b) => b.addEventListener("click", async () => {
      const msg = $("#dup-msg");
      try {
        msg.className = "form-msg";
        msg.textContent = await sendInbox({ type: "article_lead", id: b.dataset.lead || null, ids: outlets.map((r) => r.id).filter(Boolean), title: a.title });
        b.disabled = true; b.textContent = "요청함";
      } catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
    }));
    box.querySelectorAll("[data-unmerge]").forEach((b) => b.addEventListener("click", async () => {
      const msg = $("#dup-msg");
      try { msg.className = "form-msg"; msg.textContent = await sendInbox({ type: "article_merge", id: b.dataset.unmerge, into: null, title: a.title }); b.disabled = true; b.textContent = "요청함"; }
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
  $("#ind-modal").addEventListener("click", (ev) => { if (ev.target.closest("[data-iclose]")) $("#ind-modal").hidden = true; });
  // 기사·공시 상세의 '그날 주가 차트': 페이지를 옮기지 않고 창으로 그날 1일 차트 (주가 기록 탭과 같은 차트, 지금 기사 표시는 강조)
  let dayModalChart = null;
  const closeDayModal = () => {
    $("#day-modal").hidden = true;
    if (dayModalChart) { try { dayModalChart.remove(); } catch (_) {} charts = charts.filter((c) => c !== dayModalChart); dayModalChart = null; }
    $("#day-body").innerHTML = "";
  };
  async function openDayModal(ds, curId, cur = null) {
    const body = $("#day-body");
    closeDayModal();
    // 제목을 누르면 주가 기록 화면의 그날 1일 차트로 이동
    $("#day-title").innerHTML = `<a class="dm-title-link" href="#/day/${esc(ds)}" title="주가 기록 화면에서 이날 1일 차트 보기">${dayLabel(ds)} 주가 <span aria-hidden="true">›</span></a>`;
    $("#day-title a").onclick = () => closeDayModal();
    body.innerHTML = `<p class="loading">불러오는 중…</p>`;
    $("#day-modal").hidden = false;
    let d = null, iv = 1;
    try { d = await api(`/api/intraday?date=${ds}`); } catch (_) { d = null; }
    if (d && d.date !== ds) d = null;
    if (!d || !d.bars || !d.bars.length) {           // 1분봉이 없는 날: 야후 5분·60분봉
      try { const c = ((await api("/api/coarse")).days || {})[ds]; if (c && c.bars.length) { d = { date: ds, bars: c.bars, events: [], prev_close: c.prev_close }; iv = c.interval; } } catch (_) {}
    }
    if ($("#day-modal").hidden) return;
    if (!d || !d.bars || !d.bars.length) { body.innerHTML = `<p class="muted">이날 분봉 기록이 없습니다.</p>`; return; }
    const [arts, discs] = await Promise.all([
      api("/api/articles").then((r) => r.items || []).catch(() => []),
      api("/api/disclosures").then((r) => r.items || []).catch(() => []),
    ]);
    if ($("#day-modal").hidden) return;
    // 그날 나온 소식 (같은 내용으로 묶인 다른 매체 기사도 각자 공개 시각에). 지금 연 기사·공시는 종류·날짜와 상관없이 늘 넣음
    // (전날 장 마감 뒤 공개라 반응일 차트를 열었으면 첫 봉에 표시)
    const on = (x) => x.ts && x.ts.slice(0, 10) === ds && !x.untimed;
    const isPrA = (a) => a.kind === "pr" || a.kind === "company";
    const news = [];
    for (const a of arts) {
      if (a.hidden || a.category === "단순 언급") continue;
      const k = isPrA(a) ? "pr" : "ext";
      for (const r of [a, ...(a.related_titles || [])])
        if (r.id === curId ? r.ts && !r.untimed && r.ts.slice(0, 10) <= ds : on(r)) news.push({ ts: r.ts, k, title: r.title, id: r.id, cur: r.id === curId });
    }
    for (const x of discs)
      if (x.id === curId ? x.ts && !x.untimed && x.ts.slice(0, 10) <= ds : on(x) && MAIN_DISC.has(x.pblntf || "")) news.push({ ts: x.ts, k: "disc", title: x.title, id: x.id, cur: x.id === curId });
    if (cur && cur.ts && cur.ts.slice(0, 10) <= ds && !news.some((x) => x.cur)) news.push({ ...cur, id: curId, cur: true });   // 목록에 따로 없는 묶음 속 기사
    const bars = d.bars, last = bars[bars.length - 1].c, base = d.prev_close;
    const hi = Math.max(...bars.map((b) => b.h || b.c)), lo = Math.min(...bars.map((b) => b.l || b.c));
    const r = (v) => (base ? pct(v / base - 1) : "-");
    body.innerHTML = `<p class="dm-sub">${base ? `기준가 ${won(base)}원 → ` : ""}${won(last)}원 <b class="${base ? sgnCls(last - base) : ""}">${r(last)}</b> · 장중 최고 <span class="${base ? sgnCls(hi - base) : ""}">${r(hi)}</span> / 최저 <span class="${base ? sgnCls(lo - base) : ""}">${r(lo)}</span>${iv > 1 ? ` ${coarseTip(iv)}` : ""}</p>
      <div class="day-legend"></div><div class="dm-chart"></div>
      <div class="rec-legend"><span><i class="mk pr"></i>그린광학 보도자료</span><span><i class="mk disc"></i>주요 공시</span><span><i class="mk ext"></i>외부 기사</span></div>`;
    const closed = bars[bars.length - 1].t.slice(11) >= "15:30";
    dayModalChart = priceVolumeChart($(".dm-chart", body), $(".day-legend", body), bars, {
      events: (d.events || []).filter((e) => !isDaily(e)), news, prevClose: base, interactive: false, barMinutes: iv,
      extendTo: closed || iv > 1 ? null : `${ds}T15:30`,
      onEvent: (id) => { closeDayModal(); openEvent(id); },
      onNews: (id) => { closeDayModal(); location.hash = `#/pr/${id}`; },
    });
  }
  document.addEventListener("click", (ev) => { const b = ev.target.closest("[data-daychart]"); if (b) openDayModal(b.dataset.daychart, b.dataset.cur, { ts: b.dataset.curTs, k: b.dataset.curK, title: b.dataset.curTitle }); });
  $("#day-modal").addEventListener("click", (ev) => { if (ev.target.closest("[data-dclose]")) closeDayModal(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !$("#day-modal").hidden) closeDayModal(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") $("#ind-modal").hidden = true; });
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
  // ---------------------------------------------------------------- 수집 현황 (오른쪽 위 갱신 표시를 누르면)
  async function pageStatus() {
    main.innerHTML = `<div class="wrap"><div class="page-head"><div><h1 class="page-title">수집 현황</h1>
      <p class="page-sub">출처마다 마지막으로 받은 시각과 쌓인 양입니다. '확인 필요'는 실패가 이어진 채 장중 1시간·장 밖 6시간이 지난 것입니다.</p></div></div>
      <div id="status-out"><p class="loading">불러오는 중…</p></div></div>`;
    if (!STATUS) await refreshStatus();
    const c = (STATUS && STATUS.collect) || {};
    const at = (t) => (t ? `${t.slice(5, 10).replace("-", "/")} ${t.slice(11, 16)}` : "-");
    const rows = (c.sources || []).map((r) => `<tr class="${r.stale ? "st-bad" : r.failing ? "st-warn" : ""}">
      <td>${esc(r.name)}${r.key ? ' <small class="muted">주요</small>' : ""}</td>
      <td>${r.stale ? "확인 필요" : r.failing ? "최근 실패" : "정상"}</td>
      <td class="num">${at(r.last_ok)}</td><td class="num">${r.failing ? at(r.last_err) : "-"}</td>
      <td class="st-err">${r.failing ? esc(r.error || "") : ""}</td></tr>`).join("");
    const st = c.stored || {};
    const span = (x, unit) => (x && x.n ? `${Number(x.n).toLocaleString("ko-KR")}${unit} <small class="muted">${esc(String(x.from || "").slice(0, 10))} ~ ${esc(String(x.to || "").slice(0, 10))}</small>` : "-");
    const b = st.backup;
    $("#status-out").innerHTML = `
      <section class="panel"><h2 class="d-sec">출처별</h2>${rows ? `<div class="table-wrap"><table class="days-table status-table"><thead><tr>
        <th>출처</th><th>상태</th><th>마지막 성공</th><th>마지막 실패</th><th>오류</th></tr></thead><tbody>${rows}</tbody></table></div>`
        : `<p class="muted">아직 기록이 없습니다. 다음 자동 수집부터 쌓입니다.</p>`}</section>
      <section class="panel"><h2 class="d-sec">쌓인 기록</h2><dl class="status-dl">
        <dt>일봉</dt><dd>${span(st.daily, "일")}</dd>
        <dt>1분봉</dt><dd>${span(st.minute, "개")}</dd>
        <dt>기사</dt><dd>${span(st.articles, "건")}${st.articles && st.articles.n ? ` <small class="muted">기자명 ${Number(st.reporters || 0).toLocaleString("ko-KR")}건</small>` : ""}</dd>
        <dt>공시</dt><dd>${span(st.disclosures, "건")}</dd>
        <dt>최신 백업</dt><dd>${b ? `${at(b.at)} · 압축·암호화 ${(b.backup / 1e6).toFixed(1)}MB <small class="muted">(기록 ${(b.db / 1e6).toFixed(1)}MB)</small>` : "-"}</dd>
        ${b && b.tables ? `<dt>표별 크기</dt><dd><small class="muted">${esc(b.tables)}</small></dd>` : ""}
      </dl></section>`;
  }

  // ---------------------------------------------------------------- 전체 검색 (기사·공시·사건·날짜)
  // 날짜: 2026-04-22 · 26.4.22 · 20260422 · 4/22 · 4-22 · 4월 22일 · 0422 · 4월 · 2026-04 처럼 쓰면 그 날짜(달)를 찾음. 나머지는 제목·매체·기자·제출인·상대 회사
  // 하루 날짜가 거래일 하나로 정해지면 검색 결과 대신 바로 그날 1일 차트로 감
  // 등락률: +6.5%·6.5% → 6.5% 이상 오른 날, -5% → 5% 이상 내린 날
  function searchMove(q) {
    const m = q.replace(/\s+/g, "").replace("−", "-").match(/^([+-]?)(\d+(?:\.\d+)?)%$/);
    if (!m) return null;
    const v = +m[2] / 100;
    return { sign: m[1], v, test: (c) => c != null && (m[1] === "-" ? c <= -v : c >= v) };
  }

  function searchDates(q) {
    const t = q.replace(/\s+/g, ""), p2 = (x) => x.padStart(2, "0");
    const ok = (mo, d) => +mo >= 1 && +mo <= 12 && (d == null || (+d >= 1 && +d <= 31));
    let m = t.match(/^(\d{4}|\d{2})(?:[-./]|년)(\d{1,2})(?:[-./]|월)(\d{1,2})일?$/) || t.match(/^(\d{4})(\d{2})(\d{2})$/);
    if (m && ok(m[2], m[3])) return { day: `${m[1].length === 2 ? `20${m[1]}` : m[1]}-${p2(m[2])}-${p2(m[3])}` };
    m = t.match(/^(\d{1,2})(?:[-/.]|월)(\d{1,2})일?$/) || t.match(/^(\d{2})(\d{2})$/);
    if (m && ok(m[1], m[2])) return { md: `-${p2(m[1])}-${p2(m[2])}` };
    m = t.match(/^(\d{4})[-./](\d{1,2})$/) || t.match(/^(\d{4})년(\d{1,2})월$/);
    if (m && ok(m[2])) return { month: `${m[1]}-${p2(m[2])}` };
    m = t.match(/^(\d{1,2})월$/);
    if (m && ok(m[1])) return { mm: `-${p2(m[1])}-` };
    return null;
  }

  const SEARCH_EX = [["9/8", "그날 차트"], ["4월", "한 달"], ["+5%", "5% 이상 오른 날"], ["-5%", "5% 이상 내린 날"], ["LIG", "기사·공시"]];
  async function pageSearch(q) {
    main.innerHTML = `<div class="wrap"><div class="page-head"><div><h1 class="page-title">검색</h1>
      <p class="page-sub" id="search-sub"></p></div></div>
      <form class="search-box" id="search-form" role="search" autocomplete="off">
        <input type="search" id="search-q" value="${esc(q)}" placeholder="날짜, 등락률, 기사 제목·매체·기자, 공시 제목" aria-label="검색어">
        <button class="btn primary" type="submit">검색</button></form>
      <p class="search-ex">예: ${SEARCH_EX.map(([x, t]) => `<a href="#/search/${encodeURIComponent(x)}"><b>${esc(x)}</b> <span class="muted">${t}</span></a>`).join("")}</p>
      <div id="search-out"><p class="loading">찾는 중…</p></div></div>`;
    $("#search-form").onsubmit = (ev) => { ev.preventDefault(); location.hash = `#/search/${encodeURIComponent($("#search-q").value.trim())}`; };
    const box = $("#gsearch-q");
    if (box && box.value !== q) box.value = q;
    if (!q.trim()) {
      $("#search-out").innerHTML = `<div class="empty"><strong>찾을 말을 넣어 주세요.</strong>날짜(9/8, 2026-09-08)를 넣으면 그날 1일 차트로 바로 갑니다. 달(4월), 하루 등락률(+5%, -5%), 기사·공시 제목·매체·기자도 찾을 수 있습니다.</div>`;
      $("#search-q").focus(); return;
    }
    const [ad, dd, daily] = await Promise.all([
      api("/api/articles").catch(() => ({})), api("/api/disclosures").catch(() => ({})),
      api("/api/daily").then((d) => d.days || []).catch(() => []),
    ]);
    const mv = searchMove(q);
    const dt = mv ? null : searchDates(q);
    const onDay = (ts) => !!dt && !!ts && (dt.day ? ts.startsWith(dt.day) : dt.month ? ts.startsWith(dt.month)
      : dt.md ? ts.slice(4, 10) === dt.md : ts.slice(4, 8) === dt.mm);
    const days = mv ? daily.filter((x) => mv.test(x.change)).reverse() : dt ? daily.filter((x) => onDay(x.date)).reverse() : [];
    // 하루 날짜가 거래일 하나로 정해지면 바로 그날 1일 차트 (뒤로 가기는 검색 전 화면으로)
    if ((dt?.day || dt?.md) && days.length === 1) { location.replace(`#/day/${days[0].date}`); return; }
    const norm = (t) => String(t || "").toLowerCase().replace(/\s+/g, "");
    const nq = norm(q);
    const hit = (...xs) => (dt || mv ? false : xs.some((x) => norm(x).includes(nq)));
    const arts = [];
    for (const a of ad.items || []) {
      if (a.hidden) continue;
      for (const r of [a, ...(a.related || [])]) if (onDay(r.ts) || hit(r.title, r.office, r.reporter)) arts.push({ ...r, kind: a.kind, category: a.category });
    }
    const discs = (dd.items || []).filter((x) => onDay(x.ts) || hit(x.title, x.filer, x.kind));
    if (mv) {                          // 등락률 검색: 그날 소식도 함께 (날짜로 이어 봄)
      const set = new Set(days.map((x) => x.date));
      for (const a of ad.items || []) if (!a.hidden && a.ts && set.has(a.ts.slice(0, 10)) && a.category !== "단순 언급") arts.push(a);
      for (const x of dd.items || []) if (x.ts && set.has(x.ts.slice(0, 10)) && MAIN_DISC.has(x.pblntf)) discs.push(x);
    }
    arts.sort((x, y) => (x.ts < y.ts ? 1 : -1));
    // 글자 검색이면 제목에서 찾은 말을 칠함
    const hlRe = !dt && !mv ? new RegExp(esc(q.trim()).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi") : null;
    const hl = (t) => (hlRe ? esc(t).replace(hlRe, (x) => `<mark>${x}</mark>`) : esc(t));
    const li = (href, meta, title) => `<li><a href="${href}"><span class="al-meta">${meta}</span><span class="t">${hl(title)}</span></a></li>`;
    const more = (n, max) => (n > max ? `<li class="muted search-more">외 ${n - max}건 — 검색어를 더 좁혀 보세요.</li>` : "");
    const sec = (title, n, body, extra = "") => (n ? `<section class="search-sec"><h2>${title} <small class="muted">${n}건</small>${extra}</h2><ul class="search-list">${body}</ul></section>` : "");
    // 날짜 검색인데 그날 시세가 없으면(주말·휴일): 앞뒤 거래일로 이어 줌
    let notice = "";
    if ((dt?.day || dt?.md) && !days.length && daily.length) {
      const last = daily[daily.length - 1].date;
      let target = dt.day;
      if (!target) { const y = +last.slice(0, 4); target = `${y}${dt.md}` <= last ? `${y}${dt.md}` : `${y - 1}${dt.md}`; }
      const prev = [...daily].reverse().find((x) => x.date < target), next = daily.find((x) => x.date > target);
      const go = (x, t) => (x ? `<a class="btn small" href="#/day/${esc(x.date)}">${t} ${dayLabel(x.date)} 차트</a>` : "");
      notice = `<div class="search-notice"><strong>${target.slice(0, 4)}년 ${dayLabel(target)}은 시세가 없습니다.</strong>
        <span class="muted">${target > last ? "아직 오지 않은 날입니다." : target < daily[0].date ? "기록을 모으기 전 날입니다." : "장이 열리지 않은 날(주말·휴일)입니다."}</span>
        <div class="search-go">${go(prev, "이전 거래일")}${go(next, "다음 거래일")}</div></div>`;
    }
    // 달 검색: 그 달 전체 차트로 (여러 해에 걸치면 가장 최근 해)
    let monthLink = "";
    if ((dt?.month || dt?.mm) && days.length) {
      const y = days[0].date.slice(0, 7), md = days.filter((x) => x.date.startsWith(y));
      monthLink = ` <a class="btn small search-h-go" href="#/period/${md[md.length - 1].date}/${md[0].date}">${+y.slice(0, 4)}년 ${+y.slice(5)}월 주가 차트 보기 →</a>`;
    }
    const dayRow = (x) => `<li><a href="#/day/${esc(x.date)}"><span class="al-meta"><span class="num ${sgnCls(x.change)}">${x.change == null ? "-" : pct(x.change)}</span><span class="muted">KOSDAQ ${x.kosdaq == null ? "-" : pct(x.kosdaq)} · 거래량 ${Math.round(x.volume || 0).toLocaleString("ko-KR")}주</span></span><span class="t">${x.date.slice(0, 4)}년 ${dayLabel(x.date)} <span class="search-go-t">1일 차트 →</span></span></a></li>`;
    const html = notice
      + sec("날짜", days.length, days.slice(0, 300).map(dayRow).join("") + more(days.length, 300), monthLink)
      + sec("기사", arts.length, arts.slice(0, 80).map((a) => li(`#/pr/${esc(a.id)}`, `${kindTag({ kind: a.kind })}<span class="muted">${dayLabel(a.ts)} ${hhmm(a.ts)} · ${esc(a.office || "")}${a.reporter ? ` · ${esc(a.reporter)} 기자` : ""}</span>`, a.title)).join("") + more(arts.length, 80))
      + sec("공시", discs.length, discs.slice(0, 80).map((x) => li(`#/pr/${esc(x.id)}`, `<span class="ktag k-disc">공시</span><span class="muted">${dayLabel(x.ts)} ${x.untimed ? "시각 미상" : hhmm(x.ts)} · ${esc(x.filer || "")}</span>`, x.title)).join("") + more(discs.length, 80));
    const kindLabel = mv ? `하루 등락 ${mv.sign === "-" ? `−${(mv.v * 100).toFixed(1)}% 이하` : `+${(mv.v * 100).toFixed(1)}% 이상`} · 그날 소식 포함`
      : dt ? `날짜로 찾음 · “${q}”` : `제목·매체·기자·제출인에서 찾음 · “${q}”`;
    $("#search-sub").textContent = kindLabel;
    $("#search-out").innerHTML = html || `<div class="empty"><strong>찾은 것이 없습니다.</strong>다른 말로 찾아보세요. 날짜는 9/8, 2026-09-08, 4월처럼 쓸 수 있습니다.</div>`;
  }

  async function route() {
    cleanupPage();
    closeDrawer();
    const h = location.hash.replace(/^#/, "") || "/";
    const parts = h.split("/").filter(Boolean);
    // 바로 가기 주소: #/day/2026-09-08 → 그날 1일 차트, #/period/시작/끝 → 그 기간 차트 (검색에서 씀). 주소는 #/로 바꿔 둠
    const ymd = /^\d{4}-\d{2}-\d{2}$/;
    if (parts[0] === "day" && ymd.test(parts[1] || "")) { calState.range = "1d"; calState.sel = parts[1]; }
    else if (parts[0] === "period" && ymd.test(parts[1] || "") && ymd.test(parts[2] || "")) { calState.range = "custom"; [calState.from, calState.to] = [parts[1], parts[2]].sort(); calState.sel = calState.to; }
    if (["day", "period"].includes(parts[0])) { history.replaceState(null, "", "#/"); return route(); }
    const page = parts[0] || "dash";
    if (page !== "pr") artState.day = null;
    document.querySelectorAll(".tabs a").forEach((a) => {
      const on = a.dataset.route === (["dash", "calendar", "events", "days"].includes(page) ? "dash" : ["pivot", "react"].includes(page) ? "pr" : page);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    const dashView = { dash: "recent", calendar: "recent", events: "recent", days: "days" }[page];
    $("#hero").hidden = !dashView;
    window.scrollTo(0, 0);
    if (page === "events") { document.title = "주가 모니터"; await pageDashboard(); if (parts[1]) openEvent(+parts[1]); }   // 예전 주소(알림 링크)
    else if (page === "calendar") { document.title = "주가 모니터"; await pageDashboard(); }
    else if (page === "days") { document.title = "일별 표 | 주가 모니터"; await pageDashboard("days"); }
    else if (page === "cloud" && !CLOUD) { document.title = "어디서나 보기 | 주가 모니터"; await pageCloud(); }
    else if (page === "pr") { document.title = "뉴스 | 주가 모니터"; await pagePR(parts[1] || null); }
    else if (page === "pivot") { document.title = "기사 대시보드 | 주가 모니터"; await pagePivot(); }
    else if (page === "react") { history.replaceState(null, "", parts[1] === "event" && parts[2] ? `#/pr/${parts[2]}` : "#/pr"); return route(); }   // 예전 뉴스 반응 주소 → 뉴스 목록
    else if (page === "method") { document.title = "README | 주가 모니터"; pageMethod(); }
    else if (page === "status") { document.title = "수집 현황 | 주가 모니터"; await pageStatus(); }
    else if (page === "search") { document.title = "검색 | 주가 모니터"; await pageSearch(decodeURIComponent(parts.slice(1).join("/"))); }
    else { document.title = "주가 모니터"; await pageDashboard(); }
  }

  window.addEventListener("hashchange", route);
  $("#engine").addEventListener("click", () => { location.hash = "#/status"; });
  $("#gsearch").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const q = $("#gsearch-q").value.trim();
    location.hash = `#/search/${encodeURIComponent(q)}`;
  });
  (STATIC ? ensureLogin() : Promise.resolve()).then(() => {
    refreshStatus().then(route);
    setInterval(refreshStatus, STATIC ? 30000 : 10000);
  });
})();
