/* 주가 모니터 – 화면 스크립트
 * 데이터: PC에서는 webserver.py(/api/...), 웹사이트(GitHub Pages)에서는 암호화된 d/*.bin 파일. */
(() => {
  "use strict";
  const LW = window.LightweightCharts;
  const $ = (sel, el = document) => el.querySelector(sel);
  const main = $("#main");
  const KIND = { up: "급등", down: "급락", flat: "거래량만 급증" };
  const kindOf = (e) => (e.tier === "관찰" || e.tier === "요청" ? ({ up: "상승", down: "하락" }[e.direction] || KIND[e.direction]) : KIND[e.direction]);
  const CONF_CLS = { "높음": "hi", "중간": "mid", "낮음": "lo" };
  const confTag = (c) => (c ? `<span class="conf ${CONF_CLS[c] || ""}">신뢰도 ${c}</span>` : "");
  const scopeTag = (e) => `<span class="scope">${e.scope === "하루" || e.event_type === "daily" ? "하루" : "장중"}</span>`;
  const tierTag = (e) => (e.tier === "대형" ? `<span class="tier">대형</span>`
    : e.tier === "관찰" ? `<span class="tier watch" title="3~5% 움직임: 기록만 하고 알림은 보내지 않습니다">관찰</span>`
    : e.tier === "요청" ? `<span class="tier watch" title="직접 분석을 요청한 움직임">요청</span>` : "");
  const isDaily = (e) => e.scope === "하루" || e.event_type === "daily";
  const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

  let STATUS = null;          // 마지막 /api/status
  let charts = [];            // 페이지 전환 시 정리할 차트
  let timers = [];            // 페이지 전용 타이머
  let heroChart = null;

  // ---------------------------------------------------------------- format
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const won = (v) => (v == null ? "-" : Math.round(v).toLocaleString("ko-KR"));
  const int = (v) => (v == null ? "-" : Math.round(v).toLocaleString("ko-KR"));
  const pct = (v, d = 2) => (v == null ? "-" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(d)}%`);
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
  const b64e = (u8) => btoa(String.fromCharCode(...u8));
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
    const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, SKEY, new TextEncoder().encode(JSON.stringify(item))));
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
  function priceVolumeChart(el, legendEl, bars, { dark = false, events = [], prevClose = null, extendTo = null, interactive = true } = {}) {
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
        time: toTime(e.start_ts),
        position: e.direction === "down" ? "aboveBar" : e.direction === "up" ? "belowBar" : "aboveBar",
        shape: e.direction === "up" ? "arrowUp" : e.direction === "down" ? "arrowDown" : "circle",
        color: kindColor(e.direction, dark),
        text: pct(e.peak_return_5m, 1),
        size: 1,
      })).sort((a, b) => a.time - b.time);
      LW.createSeriesMarkers(price, markers);
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
        legendEl.innerHTML = `<span>${hhmm(b.t)}</span><span><b>${won(b.c)}원</b>${chg != null ? ` <span class="${dark ? "" : sgnCls(chg)}">${pct(chg)}</span>` : ""}</span><span>거래량 <b>${int(b.v)}주</b></span>`;
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
      const marketNow = wd >= 1 && wd <= 5 && hm >= 905 && hm <= 1535;
      const when = s.published_at ? `${s.published_at.slice(5, 10).replace("-", "/")} ${s.published_at.slice(11, 16)}` : "-";
      const iv = (LIVE && LIVE.interval_min) || 15;
      const byPc = !STATIC && LIVE && LIVE.runner === "pc";
      const banner = $(".shared-banner");
      if (banner) banner.textContent = byPc
        ? `회사 PC에서 ${when}에 올린 자료입니다. PC가 켜져 있으면 장중 ${iv}분마다 새로 올라옵니다.`
        : `${when}에 자동 수집·분석한 자료입니다. 장중에는 약 ${iv}분마다 새로 갱신됩니다.`;
      if (marketNow && age > iv * 2 + 15) {
        setEngine("stale", `갱신 지연 (마지막 ${when})`);
        showAlert(`<strong>지금 장중인데 ${Math.round(age)}분째 새 자료가 없습니다.</strong> ${byPc ? "회사 PC가 꺼져 있거나 감시 프로그램이 멈춘 것 같습니다." : "자동 실행이 늦어지고 있습니다. 보통 다음 실행 때 그동안의 분봉까지 한꺼번에 채워집니다."}`);
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

  function renderQuote() {
    if (!STATUS) return;
    const q = STATUS.quote, k = STATUS.kosdaq;
    $("#q-name").textContent = STATUS.name;
    $("#q-code").textContent = STATUS.symbol;
    if (q && q.price != null) {
      $("#q-price").textContent = won(q.price);
      const cls = q.change > 0 ? "up" : q.change < 0 ? "down" : "";
      const arrow = q.change > 0 ? "▲" : q.change < 0 ? "▼" : "";
      const asof = q.market_status === "OPEN" ? `${(q.traded_at || "").slice(11, 16)} 기준` : "장 마감";
      $("#q-change").innerHTML = `<span class="chg ${cls}">${arrow} ${won(Math.abs(q.change))} (${pctRaw(q.change_pct)})</span><span class="asof">${asof}</span>`;
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
    $("#foot").innerHTML = `<span>데이터 출처 네이버 증권(공식 제공 서비스가 아니어서 형식이 바뀌면 수신이 멈출 수 있습니다)</span>` +
      `<span>${since}부터 기록, 1분봉 ${int(STATUS.bars_count)}건, 이상변동 ${int(STATUS.events_count)}건${CLOUD ? ' · <a href="#" data-logout>로그아웃</a>' : ""}</span>`;
  }

  // ---------------------------------------------------------------- dashboard
  let dashDate = null;

  async function pageDashboard() {
    $("#hero").hidden = false;
    main.innerHTML = `<div class="wrap"><div class="grid-2">
      <section><div class="page-head" style="margin-bottom:14px"><h2 class="sec" id="tl-title" style="margin:0">오늘 이상변동</h2>
        <div class="seg" id="date-seg" aria-label="날짜 선택"></div></div>
        <div id="timeline"><p class="loading">불러오는 중…</p></div></section>
      <aside><div class="aside-block"><h2 class="sec">최근 이 종목 기사</h2><div id="recent-arts"><p class="muted">불러오는 중…</p></div></div>
        <div class="aside-block"><h2 class="sec">최근 7거래일</h2><div id="weekbars"></div></div>
        <div class="aside-block"><h2 class="sec">이렇게 감지합니다</h2><ul class="rules" id="rules"></ul></div></aside>
    </div></div>`;
    await loadDay(dashDate);
    loadWeek();
    loadRecentArticles();
    renderRules();
    timers.push(setInterval(() => { if (!dashDate || dashDate === latestDate) loadDay(null, true); }, 30000));
  }

  let latestDate = null;
  async function loadDay(date, silent) {
    let d;
    try { d = await api(`/api/intraday${date ? `?date=${date}` : ""}`); }
    catch (e) { $("#timeline").innerHTML = `<div class="empty"><strong>데이터를 불러오지 못했습니다.</strong>${esc(e.message)}</div>`; return; }
    if (!d.date) {
      $("#timeline").innerHTML = `<div class="empty"><strong>아직 수집된 시세가 없습니다.</strong>감시 엔진이 첫 데이터를 받는 중입니다. 1분 안에 표시됩니다.</div>`;
      $("#hero-chart").innerHTML = "";
      return;
    }
    latestDate = d.dates[0];
    dashDate = d.date === latestDate ? null : d.date;
    const isLatest = d.date === latestDate;

    // 날짜 버튼
    $("#date-seg").innerHTML = d.dates.slice(0, 5).reverse().map((x) => {
      const p = parseT(x);
      return `<button type="button" data-date="${x}" aria-pressed="${x === d.date}">${p.m}/${p.d}</button>`;
    }).join("");
    $("#date-seg").onclick = (ev) => { const b = ev.target.closest("button"); if (b) loadDay(b.dataset.date === latestDate ? null : b.dataset.date); };

    // 히어로 차트
    if (heroChart) { try { heroChart.remove(); } catch (_) {} charts = charts.filter((c) => c !== heroChart); }
    $("#hero-chart").innerHTML = "";
    const closed = d.bars.length && d.bars[d.bars.length - 1].t.slice(11) >= "15:30";
    heroChart = priceVolumeChart($("#hero-chart"), $("#hero-legend"), d.bars, {
      dark: true, events: d.events.filter((e) => !isDaily(e)), prevClose: d.prev_close, interactive: false,
      extendTo: closed ? null : `${d.date}T15:30`,
    });
    $("#session-date").textContent = `${dayLabel(d.date)} 장중 1분봉${isLatest && !closed ? ", 진행 중" : ""}`;
    $("#q-events-label").textContent = isLatest ? "오늘 급등·급락" : `${parseT(d.date).m}/${parseT(d.date).d} 급등·급락`;
    $("#q-events").innerHTML = `${d.events.length}<small>건</small>`;

    // 타임라인
    $("#tl-title").textContent = isLatest ? "오늘 급등·급락" : `${dayLabel(d.date)} 급등·급락`;
    if (!d.events.length) {
      $("#timeline").innerHTML = `<div class="empty"><strong>${isLatest ? "오늘은 아직 급등·급락이 없습니다." : "이날은 급등·급락이 없었습니다."}</strong>오른쪽 '이렇게 감지합니다'의 기준을 넘으면 이곳과 차트에 표시됩니다.</div>`;
    } else {
      $("#timeline").innerHTML = `<ul class="timeline">${d.events.slice().reverse().map((e) => `
        <li><button class="tl-row" type="button" data-id="${e.id}">
          <span class="tl-time num">${isDaily(e) ? "하루" : hhmm(e.start_ts)}</span>
          <span><span class="chip ${e.direction}">${kindOf(e)}</span>${tierTag(e)}</span>
          <span class="tl-rule">${e.cause_headline ? `${confTag(e.cause_confidence)}${esc(e.cause_headline)}` : esc(compareLine(e))}</span>
          <span class="tl-val num ${sgnCls(e.peak_return_5m)}">${pct(e.peak_return_5m)}<small>${isDaily(e) ? "종가 기준" : "구간 최대"}</small></span>
        </button></li>`).join("")}</ul>`;
      $("#timeline").onclick = (ev) => { const b = ev.target.closest("[data-id]"); if (b) openEvent(+b.dataset.id); };
    }
    if (!silent) renderQuote();
  }

  function compareLine(e) {
    if (e.kosdaq_change == null) return `${hhmm(e.start_ts)}~${hhmm(e.last_ts)} 이 종목 ${pct(e.own_change)}, KOSDAQ 비교 데이터 없음`;
    const alone = Math.abs(e.kosdaq_change) < 0.003 && Math.abs(e.own_change || 0) >= 0.01;
    return alone
      ? `KOSDAQ은 거의 그대로(${pct(e.kosdaq_change)}), 이 종목만 ${pct(e.own_change)}`
      : `이 종목 ${pct(e.own_change)}, 같은 구간 KOSDAQ ${pct(e.kosdaq_change)}`;
  }

  async function loadWeek() {
    const from = new Date(); from.setDate(from.getDate() - 16);
    let d;
    try { d = await api(`/api/events?from=${isoDate(from)}`); } catch (_) { return; }
    const days = d.trading_days.slice(-7);
    if (!days.length) { $("#weekbars").innerHTML = `<p class="muted">기록이 쌓이면 표시됩니다.</p>`; return; }
    const byDay = {};
    for (const e of d.events) { const k = e.start_ts.slice(0, 10); (byDay[k] ||= { up: 0, down: 0, flat: 0 })[e.direction]++; }
    const max = Math.max(3, ...days.map((x) => { const c = byDay[x] || {}; return (c.up || 0) + (c.down || 0) + (c.flat || 0); }));
    const col = (x) => {
      const c = byDay[x] || { up: 0, down: 0, flat: 0 };
      const total = c.up + c.down + c.flat;
      if (!total) return `<div class="wb" title="${x} 이상변동 없음"><span class="zero"></span></div>`;
      const seg = (k) => c[k] ? `<span class="s-${k}" style="height:${(c[k] / max) * 100}%"></span>` : "";
      return `<div class="wb" title="${x} 급등 ${c.up}, 급락 ${c.down}">${seg("flat")}${seg("down")}${seg("up")}</div>`;
    };
    $("#weekbars").innerHTML = `<div class="weekbars" style="--n:${days.length}">${days.map(col).join("")}</div>
      <div class="wb-labels" style="--n:${days.length}">${days.map((x) => { const p = parseT(x); const c = byDay[x]; const n = c ? c.up + c.down + c.flat : 0; return `<span><b>${n}</b>${p.m}/${p.d}</span>`; }).join("")}</div>
      <p class="note">막대 색은 <span class="up-t">급등</span>, <span class="down-t">급락</span>입니다. 전체 기록은 <a href="#/events">이벤트 기록</a>에서 볼 수 있습니다.</p>`;
  }

  async function loadRecentArticles() {
    let d;
    try { d = await api("/api/articles?limit=4"); } catch (_) { $("#recent-arts").innerHTML = ""; return; }
    if (!d.items.length) { $("#recent-arts").innerHTML = `<p class="muted">기사가 모이면 표시됩니다.</p>`; return; }
    $("#recent-arts").innerHTML = `<ul class="mini-arts">${d.items.map((a) => `<li><a href="#/pr/${esc(a.id)}">
      <span class="al-meta">${a.manual ? '<span class="tier watch">직접 등록</span>' : ""}${catTag(a.category)}${toneTag(a.tone)}<span class="muted">${parseT(a.ts).m}/${parseT(a.ts).d}</span></span>
      <span class="t">${esc(a.title)}</span>
      <span class="al-foot">${verdictTag(a.effect)}${a.effect && a.effect.measure ? `<span class="num ${sgnCls(a.effect.measure.value)}">${pct(a.effect.measure.value)}</span>` : ""}</span></a></li>`).join("")}</ul>
      <p class="note">초과 반응(업종·KOSDAQ 대비) 기준입니다. 전체는 <a href="#/pr">기사 영향 분석</a>에서 볼 수 있습니다.</p>`;
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
  const evState = { range: "1m", kind: "all", tier: "all", day: null };

  async function pageEvents() {
    main.innerHTML = `<div class="wrap">
      <div class="page-head"><div><h1 class="page-title">이벤트 기록</h1><p class="page-sub" id="ev-sub">감시 엔진이 감지한 이상변동이 모두 여기에 쌓입니다.</p></div></div>
      <div class="controls">
        <div class="seg" id="ev-range" aria-label="기간">
          <button type="button" data-v="1w">1주</button><button type="button" data-v="1m">1개월</button><button type="button" data-v="3m">3개월</button><button type="button" data-v="all">전체</button>
        </div>
        <div class="seg" id="ev-kind" aria-label="구분">
          <button type="button" data-v="all">전체</button><button type="button" data-v="up">오름</button><button type="button" data-v="down">내림</button>
        </div>
        <div class="seg" id="ev-tier" aria-label="크기">
          <button type="button" data-v="all">모든 크기</button><button type="button" data-v="main" title="±5% 이상 (알림 보낸 것)">급등·급락만</button><button type="button" data-v="watch" title="±3~5% 관찰 + 직접 요청한 분석">관찰·요청</button>
        </div>
        <span class="day-filter" id="ev-day" hidden></span>
        <span class="spacer"></span>
        <button class="btn editable" type="button" id="ask-toggle" title="기준에 안 걸린 날도 원인을 분석해 기록에 올립니다">분석 요청</button>
        <a class="btn" id="ev-csv" href="#">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 19h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>엑셀로 내려받기</a>
      </div>
      <form class="panel form pr-form editable" id="ask-form" autocomplete="off" hidden>
        <p class="hint">기준(±3%)에 안 걸려 기록되지 않은 움직임도 원인이 궁금하면 여기서 요청하세요. 시간대를 비우면 하루(전일 종가 → 종가) 기준, 시간을 넣으면 그 시간대만 분석합니다. 장중 약 3분, 그 밖에는 15분 안에 이 목록에 '요청'으로 올라오고 원인 분석이 이어집니다.</p>
        <div class="pr-form-grid">
          <label class="field"><span>날짜</span><input type="date" name="date" value="${isoDate(new Date())}" required></label>
          <label class="field"><span>시작 시각 (선택)</span><input type="time" name="from" min="09:00" max="15:30"></label>
          <label class="field"><span>끝 시각 (선택)</span><input type="time" name="to" min="09:00" max="15:30"></label>
          <button class="btn primary" type="submit">분석 요청하기</button>
        </div>
        <div class="form-msg" id="ask-msg" role="status"></div>
      </form>
      <div class="panel cal-wrap" id="cal"></div>
      <div class="panel table-panel"><div class="table-wrap" id="ev-table"><p class="loading" style="padding:20px">불러오는 중…</p></div></div>
    </div>`;
    const bindSeg = (id, key) => {
      const el = $(id);
      const sync = () => el.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === evState[key])));
      sync();
      el.onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; evState[key] = b.dataset.v; if (key === "range") evState.day = null; sync(); loadEvents(); };
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
      ? `${since ? dayLabel(since) + "부터 " : ""}${all.length}건. 급등·급락(±5% 이상) ${all.filter((e) => e.tier !== "관찰" && e.tier !== "요청").length}건, 관찰(±3~5%, 알림 없음) ${all.filter((e) => e.tier === "관찰").length}건${all.some((e) => e.tier === "요청") ? `, 직접 요청 ${all.filter((e) => e.tier === "요청").length}건` : ""}입니다 (하루 기준 ${all.filter(isDaily).length}건, 장중 ${all.filter((e) => !isDaily(e)).length}건).`
      : "이 기간에는 급등·급락이 없었습니다.";

    loadCalendar();

    let list = all;
    if (evState.kind !== "all") list = list.filter((e) => e.direction === evState.kind);
    if (evState.tier === "main") list = list.filter((e) => e.tier !== "관찰" && e.tier !== "요청");
    else if (evState.tier === "watch") list = list.filter((e) => e.tier === "관찰" || e.tier === "요청");
    if (evState.day) list = list.filter((e) => e.start_ts.startsWith(evState.day));

    const dayEl = $("#ev-day");
    if (evState.day) {
      dayEl.hidden = false;
      dayEl.innerHTML = `${dayLabel(evState.day)}만 보는 중 <button type="button">해제</button>`;
      $("button", dayEl).onclick = () => { evState.day = null; loadEvents(); };
    } else dayEl.hidden = true;

    const csv = new URLSearchParams();
    if (evState.day) { csv.set("from", evState.day); csv.set("to", evState.day); } else if (from) csv.set("from", from);
    if (evState.kind !== "all") csv.set("kind", evState.kind);
    $("#ev-csv").href = `/api/events.csv?${csv}`;
    $("#ev-csv").onclick = CLOUD ? (ev) => { ev.preventDefault(); csvDownload(list); } : null;

    if (!list.length) {
      $("#ev-table").innerHTML = `<div class="empty" style="padding:22px 16px;border:0"><strong>조건에 맞는 이상변동이 없습니다.</strong>기간이나 구분을 바꿔 보세요.</div>`;
      return;
    }
    $("#ev-table").innerHTML = `<table class="data"><thead><tr>
        <th>일시</th><th>구분</th><th class="r">움직임</th><th class="r">KOSDAQ 같은 기간</th><th>가장 유력한 원인</th></tr></thead><tbody>
      ${list.map((e) => `<tr data-id="${e.id}" tabindex="0">
        <td class="when num">${dayLabel(e.start_ts)}<small>${isDaily(e) ? "하루 (전일 종가 → 종가)" : `${hhmm(e.start_ts)}~${hhmm(e.last_ts)}`}</small></td>
        <td><span class="chip ${e.direction}">${kindOf(e)}</span>${scopeTag(e)}${tierTag(e)}</td>
        <td class="r num ${sgnCls(e.peak_return_5m)}"><b>${pct(e.peak_return_5m)}</b></td>
        <td class="r num">${e.kosdaq_change == null ? '<span class="muted">없음</span>' : pct(e.kosdaq_change)}</td>
        <td class="rule" title="${esc(e.cause_summary || e.rule)}">${e.cause_headline ? `${confTag(e.cause_confidence)}${esc(e.cause_headline)}` : '<span class="muted">분석 대기</span>'}</td></tr>`).join("")}
      </tbody></table>`;
    const tb = $("#ev-table tbody");
    tb.onclick = (ev) => { const r = ev.target.closest("tr"); if (r) openEvent(+r.dataset.id); };
    tb.onkeydown = (ev) => { if (ev.key === "Enter") { const r = ev.target.closest("tr"); if (r) openEvent(+r.dataset.id); } };
  }

  async function loadCalendar() {
    const since = new Date(); since.setDate(since.getDate() - 182);
    try {
      const d = await api(`/api/events?from=${isoDate(since)}`);
      renderCalendar(d.trading_days, d.events, null);
    } catch (_) { /* 달력은 보조 정보 */ }
  }

  function renderCalendar(tradingDays, events, from) {
    const el = $("#cal");
    if (!tradingDays.length) { el.innerHTML = `<p class="muted" style="margin:0">아직 기록된 거래일이 없습니다.</p>`; return; }
    const byDay = {};
    events.forEach((e) => { const k = e.start_ts.slice(0, 10); (byDay[k] ||= { up: 0, down: 0, flat: 0, n: 0 }); byDay[k][e.direction]++; byDay[k].n++; });
    const tset = new Set(tradingDays);
    const start = new Date((from || tradingDays[0]) + "T00:00:00");
    const end = new Date(tradingDays[tradingDays.length - 1] + "T00:00:00");
    // 월요일부터 시작
    const s = new Date(start); s.setDate(s.getDate() - ((s.getDay() + 6) % 7));
    let html = `<span class="lbl"></span><span class="lbl">월</span><span class="lbl"></span><span class="lbl">수</span><span class="lbl"></span><span class="lbl">금</span>`;
    let lastMonth = -1;
    for (let w = new Date(s); w <= end; w.setDate(w.getDate() + 7)) {
      const m = w.getMonth();
      const showMonth = m !== lastMonth;
      lastMonth = m;
      html += `<span class="lbl">${showMonth ? `${m + 1}월` : ""}</span>`;
      for (let i = 0; i < 5; i++) {
        const d = new Date(w); d.setDate(w.getDate() + i);
        const k = isoDate(d);
        if (d < start || d > end) { html += `<span class="cell" style="visibility:hidden"></span>`; continue; }
        if (!tset.has(k)) { html += `<span class="cell off" title="${k} 기록 없음(휴장 또는 PC 꺼짐)"></span>`; continue; }
        const c = byDay[k];
        if (!c) { html += `<button type="button" class="cell" title="${k} 이상변동 없음" data-day="${k}"></button>`; continue; }
        const dom = c.up >= c.down && c.up >= c.flat ? "up" : c.down >= c.flat ? "down" : "flat";
        const lvl = c.n >= 4 ? "" : c.n >= 2 ? "l2" : "l1";
        html += `<button type="button" class="cell has c-${dom} ${lvl} ${evState.day === k ? "sel" : ""}" data-day="${k}" title="${k} ${c.n}건: 급등 ${c.up}, 급락 ${c.down}" aria-label="${k} 이상변동 ${c.n}건"></button>`;
      }
    }
    el.innerHTML = `<div class="cal-head"><h2 class="sec" style="margin:0">거래일별 기록</h2><p>최근 6개월까지 보여줍니다. 칸을 누르면 그날 이상변동만 볼 수 있습니다.</p></div>
      <div class="cal">${html}</div>
      <div class="cal-legend"><span><i style="background:var(--line)"></i>이상변동 없음</span><span><i style="box-shadow:inset 0 0 0 1px var(--line-2)"></i>기록 없음</span><span><i style="background:var(--up)"></i>급등 위주</span><span><i style="background:var(--down)"></i>급락 위주</span><span>색이 진할수록 건수가 많음</span></div>`;
    el.onclick = (ev) => { const b = ev.target.closest("button.has"); if (!b) return; evState.day = evState.day === b.dataset.day ? null : b.dataset.day; loadEvents(); };
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
      ...fp.slice(1).map((f) => ({ k: `업종 몫 · ${esc(f.name)} <small>ETF ${pct(f.factor)} 중 시장과 무관한 부분 ${pct(f.factor_resid)} × 민감도 ${f.beta.toFixed(2)}</small>`, v: f.contrib })),
      { k: "이 종목 고유 몫 <small>= 실제 − 시장 몫 − 업종 몫 (리스크 모델의 '종목 고유 수익')</small>", v: sp.specific, cls: "abn" },
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
      <p class="flow-sum" style="margin-top:8px">${judge}${sp.z != null ? ` 고유 몫은 평소 이 종목 혼자 움직이는 폭의 <b>${Math.abs(sp.z).toFixed(1)}배</b>입니다${sp.abn_basis ? ` (${esc(sp.abn_basis)})` : ""} — ${Math.abs(sp.z) >= 2 ? "평소엔 100번 중 5번도 안 나오는 크기라 이례적입니다." : "평소에도 나올 수 있는 크기입니다."}` : ""}</p>`;
  }

  function causalBlock(ca, mv) {
    if (!ca) return "";
    const rows = [];
    const verdict = (ok, txt) => `<span class="cv ${ok === true ? "pass" : ok === false ? "fail" : "na"}">${txt}</span>`;
    const es = ca.event_study;
    if (es && es.ok) {
      const sig = es.p < 0.05;
      const pre = (es.car || []).find((x) => x.window.startsWith("사전"));
      rows.push(`<li><div class="ch">${verdict(sig, sig ? "이례적" : "평소 범위")}<b>시장만 감안한 계산</b><small>업종 ETF 기록이 모자라 대신 사용 · 직전 ${es.n_est}거래일</small></div>
        <p>KOSDAQ 움직임으로 예상되는 등락 ${pct(es.expected)} 대비 실제 ${pct(es.actual)} → 비정상수익률 <b>${pct(es.ar)}</b> (평소 하루 ±${(es.sigma * 100).toFixed(1)}%의 ${Math.abs(es.t).toFixed(1)}배, p=${es.p.toFixed(3)}).
        ${sig ? "통계적으로 평소에 보기 드문 움직임입니다." : "평소에도 나올 수 있는 크기라, 특별한 원인이 없었을 수도 있습니다."}
        ${pre ? ` 직전 5일 비정상수익률 합계 ${pct(pre.car)}(p=${pre.p.toFixed(2)})${pre.p < 0.05 ? " — 미리 움직인 흔적이 있습니다." : " — 미리 움직인 흔적은 없습니다."}` : ""}
        ${es.vol_ratio ? ` 거래량은 평소의 ${es.vol_ratio.toFixed(1)}배.` : ""}</p></li>`);
    } else if (es) rows.push(`<li><div class="ch">${verdict(null, "자료 부족")}<b>시장만 감안한 계산</b></div><p>${esc(es.reason || "")}</p></li>`);
    const fa = ca.factor;
    if (fa && fa.ok) {
      const sig = fa.p != null && fa.p < 0.05;
      rows.push(`<li><div class="ch">${verdict(sig, sig ? "고유 움직임 큼" : "평소 범위")}<b>① 몫 나누기 계산식</b><small>시장(KOSDAQ) + 업종 ETF 회귀, 직전 ${fa.n_est}거래일</small></div>
        <p>${fa.parts.map((x) => `${esc(x.name)} ${pct(x.contrib)}`).join(" + ")} + 고유 <b>${pct(fa.specific)}</b> = 실제 ${pct(fa.actual)}.
        고유 몫은 평소 고유 변동(하루 ±${(fa.sigma * 100).toFixed(1)}%)의 ${Math.abs(fa.z).toFixed(1)}배(p=${fa.p.toFixed(3)}). 이 모형이 평소 이 종목 움직임을 설명하는 비율(R²)은 ${(fa.r2 * 100).toFixed(0)}%입니다${fa.r2 < 0.2 ? " — 시장·업종보다 개별 요인으로 움직이는 종목이라는 뜻입니다" : ""}.</p></li>`);
    }
    const ti = ca.intensity;
    if (ti && ti.vol_x) {
      const thin = ti.impact_x && ti.impact_x >= 2.5 && ti.vol_x < 2, heavy = ti.vol_x >= 2;
      rows.push(`<li><div class="ch">${verdict(heavy ? true : thin ? false : null, heavy ? "많은 거래 동반" : thin ? "얇은 호가" : "보통")}<b>③ 거래 강도</b><small>20일 평균 대비 거래량 · 같은 돈 대비 움직임</small></div>
        <p>거래량 ${int(ti.volume)}주로 20일 평균의 <b>${ti.vol_x.toFixed(1)}배</b>, 거래대금 ${eok(ti.value)}(평균 ${eok(ti.adval)}).
        ${ti.impact_x ? `같은 거래대금 대비 주가 움직임은 평소의 ${ti.impact_x.toFixed(1)}배입니다.` : ""}
        ${heavy ? "실제로 많은 매매가 실린 움직임이라 '확신 있는 매수·매도'에 가깝습니다." : thin ? "평소보다 적은 돈으로 크게 움직였습니다. 호가가 얇아 소수 주문만으로 생긴 움직임일 수 있습니다." : ""}</p></li>`);
    }
    if (!rows.length) return "";
    return `<details class="causal-more"><summary>계산 근거 자세히 보기</summary><ul class="causal">${rows.join("")}</ul>
      <p class="note">p값은 '우연히 이만큼 나올 확률'입니다. 0.05보다 작으면 우연으로 보기 어렵다고 판단합니다.</p></details>`;
  }

  function causeBlock(c, eventId) {
    const stageNote = c ? `${esc(c.stage)} 분석, ${esc((c.analyzed_at || "").slice(5, 16).replace("T", " "))} 기준` : "아직 분석 전";
    const head = `<div class="sec-head"><h3 class="d-sec">왜 움직였나</h3>
      <span class="stage">${stageNote}</span>
      <button class="btn small local-only" type="button" id="re-analyze" data-id="${eventId}">다시 분석</button></div>`;
    if (!c) return head + `<div class="empty"><strong>원인 분석이 아직 없습니다.</strong>다음 자동 수집 때 분석됩니다.</div>`;
    const cands = c.candidates || [];
    const ST = { yes: ["●", "확인"], part: ["◐", "일부"], no: ["○", "없음"], ref: ["·", "참고"] };
    const desk = (c.desk || []).length ? `<div class="desk"><div class="desk-h">한눈에 점검 <small>① 몫 나누기 → ② 기사 직후 검증 → ③ 수급</small></div><ul>${c.desk.map((d) => `<li class="ds-${d.state}"><span class="ds-mark" title="${ST[d.state][1]}">${ST[d.state][0]}</span><b>${esc(d.k)}</b><span>${esc(d.text)}</span></li>`).join("")}</ul></div>` : "";
    const verdict = c.summary_line ? `<div class="verdict-box ${CONF_CLS[c.confidence] || ""}"><div class="vb-head">${confTag(c.confidence)}<b>${esc(c.headline || "")}</b></div><p>${esc(c.summary_line)}</p></div>` : "";
    const body = cands.length ? `<h4 class="d-sub-h">① 몫 나누기 · 시장 몫 + 업종 몫 + 이 종목 고유 몫</h4>${splitBlock(c.split) || '<p class="note">시장·업종 비교 자료가 없어 나누지 못했습니다.</p>'}
      ${causalBlock(c.causal, c.move)}
      <h4 class="d-sub-h">원인 후보 · ①②③ 근거가 많이 맞는 순서</h4>
      <ol class="causes">${cands.map((x, i) => `
        <li class="cause">
          <div class="cause-meta"><span class="rank">${i + 1}</span><span class="tag t-${x.type}">${esc(x.label)}</span>${confTag(x.confidence)}
            ${x.timing ? `<span class="timing ${TIMING_CLS[x.timing] || ""}">${esc(x.timing === "선행" ? "움직임 전" : x.timing === "후행" ? "움직임 후" : x.timing === "동시" ? "같은 시간" : x.timing)}</span>` : ""}</div>
          <div class="cause-title">${esc(x.title)}</div>
          <p>${esc(x.summary)}</p>
          ${(x.checks || []).length ? `<ul class="checks">${x.checks.map((k) => `<li class="${k.ok ? "ok" : "no"}"><span class="ck">${k.ok ? "✓" : "–"}</span><b>${esc(k.step)} ${k.step === "①" ? "몫 나누기" : k.step === "②" ? "기사 직후 검증" : "수급"}</b><span>${esc(k.text)}</span></li>`).join("")}</ul>` : ""}
          ${(x.evidence || []).filter(Boolean).length ? `<ul class="evidence">${x.evidence.filter(Boolean).map((v) => `<li>${esc(v)}</li>`).join("")}</ul>` : ""}
          ${(x.links || []).filter((l) => l.url).map((l) => `<a class="ext" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title)}</a>`).join("")}
        </li>`).join("")}</ol>` : `<div class="cause none"><div class="cause-title">원인 후보 없음</div></div>`;
    const notes = c.notes || [];
    return head + verdict + desk + body + (notes.length ? `<ul class="notes">${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "")
      + `<p class="note">신뢰도: 후보마다 핵심 근거(시장·업종 흐름은 ①, 기사는 ②, 수급은 ①)가 맞아야 '중간' 이상이고, 세 근거 중 2개 이상 맞으면 '높음'입니다. 수급은 '누가' 샀는지만 알 뿐 '왜'는 모르므로 최대 '중간'입니다. 어느 후보도 핵심 근거가 맞지 않으면 '원인 미확인'으로 표시합니다. 실제 주문 이유는 공개되지 않으므로 확정이 아닌 추정입니다.</p>`;
  }

  function boardBlock(c) {
    const b = c && c.board;
    if (!b) return "";
    return `<h3 class="d-sec">종목토론방</h3>
      <p class="flow-sum">움직임 전 2시간 글 ${b.before}건 (평소 2시간 평균 ${b.baseline_2h}건), 움직임 뒤 1시간까지 ${b.after}건.</p>
      ${(b.top || []).length ? `<ul class="news">${b.top.map((x) => `<li><span class="when">${esc(x.ts.slice(5, 16).replace("T", " "))}</span><span class="timing ${x.lead ? "lead" : "late"}">${x.lead ? "움직임 전" : "움직임 후"}</span><span class="tt">${esc(x.title)}<small>조회 ${int(x.views)}</small></span></li>`).join("")}</ul>` : ""}
      <p class="note">토론방 글은 사실 확인이 안 된 개인 의견입니다. 공식 소식이 없을 때 어떤 이야기가 돌았는지 보는 참고 자료입니다. <a class="ext" href="${esc(b.url)}" target="_blank" rel="noopener">토론방 열기</a></p>`;
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
        <span class="num v ${v >= 0 ? "up-t" : "down-t"}">${v >= 0 ? "+" : "−"}${int(Math.abs(v || 0))}주</span></li>`).join("")}</ul>
        <p class="note">투자자 유형별 순매수는 거래소가 장 마감 후 확정하는 공식 수치입니다. 위의 증권사 창구 숫자와 다를 수 있습니다(외국계 창구를 국내 투자자가 쓰기도 합니다).</p>`;
    } else {
      html += `<p class="note">그날 투자자별(외국인·기관·개인) 확정 순매수는 장 마감 후 자동으로 추가됩니다.</p>`;
    }
    return html;
  }

  function verifyBlock(f) {
    const cc = f.crosscheck, eod = f.eod || {};
    let rows = [`<li><b>출처</b> ${esc(f.source || "")}</li>`,
      `<li><b>읽는 법</b> 매수·매도 각각 상위 5개 증권사만 공개됩니다. '미집계'는 0주가 아니라 5위 밖이라 보이지 않는다는 뜻이고, '5위 밖·멈춤'은 순위에서 밀려난 뒤의 거래가 반영되지 않은 값입니다. 그래서 순매수 앞에 '최소'를 붙였습니다.</li>`];
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

  function buyerBlock(c, followups) {
    const b = c && c.buyer;
    if (!b) return "";
    const fu = (followups || []).length ? `<div class="followup"><b>후속 공시</b> ${followups.map((x) => `${esc(x.date)} ${esc(x.title)}`).join(", ")} — 실제 보유자와 보유 목적이 이 공시에 나옵니다.</div>` : "";
    const peers = (b.peers || []).filter((p) => p.listed || p.net);
    return `<h3 class="d-sec">${esc(b.broker)} 창구는 왜 ${b.side}했을까</h3>
      <p class="flow-sum">${esc(b.broker)}${b.foreign ? "(외국계)" : ""} 창구가 ${b.qty_is_min ? "최소 " : ""}${int(b.qty)}주${b.amount ? `(약 ${eok(b.amount)})` : ""}를 순${b.side}했습니다. 주문한 고객이 누구인지, 왜 주문했는지는 공개되지 않아서 아래는 공개 자료로 모은 단서입니다.</p>
      ${fu}
      <ul class="clues">${b.lines.map((l) => `<li class="${l.strong ? "strong" : ""}"><span class="ck">${esc(l.kind)}</span><span>${esc(l.text)}${l.url ? ` <a class="ext" href="${esc(l.url)}" target="_blank" rel="noopener">보기</a>` : ""}</span></li>`).join("")}</ul>
      ${peers.length ? `<h4 class="sub">같은 날 ${esc(b.broker)} 창구의 같은 업종 거래</h4><ul class="rel">${peers.map((p) => `<li><span class="nm">${esc(p.name)}</span><span class="muted">${p.listed ? `${p.net >= 0 ? "+" : "−"}${int(Math.abs(p.net))}주` : "상위 거래원에 없음"}</span><span class="num v ${sgnCls(p.net)}">${p.amount != null ? eok(p.amount) : "-"}</span></li>`).join("")}</ul>
        <p class="note">같은 업종 종목의 거래원도 상위 5개사 기준 추정치입니다. 금액은 그날 마지막 가격으로 계산한 대략치입니다.</p>` : ""}`;
  }

  function articleBlock(c) {
    const ac = c && c.articles;
    if (!ac) return "";
    let body;
    if (ac.lead.length) {
      body = `<ul class="art-lead">${ac.lead.map((a) => `<li>
        <div class="al-meta">${catTag(a.category)}${toneTag(a.tone)}<span class="muted">${a.gap_min < 60 * 24 ? `움직임 ${a.gap_min >= 60 ? `${Math.floor(a.gap_min / 60)}시간 ` : ""}${a.gap_min % 60}분 전` : `${dayLabel(a.ts)} ${hhmm(a.ts)}`}</span></div>
        <a class="al-title" href="#/pr/${esc(a.id)}">${esc(a.title)}</a>
        <div class="al-foot">${verdictTag(a)}${a.measure ? `<span class="muted">${measureTxt(a.measure)}</span>` : ""}<span class="muted">${esc(a.office || "")}${a.outlets_total > 1 ? ` 외 ${a.outlets_total - 1}곳` : ""}</span></div></li>`).join("")}</ul>`;
    } else {
      body = `<p class="flow-sum">이 움직임 전 3일 안에 나온 이 종목 기사는 없습니다.${ac.last_before ? ` 가장 최근 기사는 ${dayLabel(ac.last_before.ts)} 「${esc(ac.last_before.title)}」(${esc(ac.last_before.category)})입니다.` : ""} 기사보다는 업종·수급 쪽을 먼저 보세요.</p>`;
    }
    const late = (ac.late || []).length ? `<p class="note" style="margin-top:8px">움직임 뒤에 나온 기사: ${ac.late.map((a) => `<a href="#/pr/${esc(a.id)}">${esc(a.title)}</a> (${hhmm(a.ts)}, ${esc(a.category)})`).join(", ")}</p>` : "";
    return `<section class="d-block art-first"><h3 class="d-sec">직전 이 종목 기사와 그 반응</h3>${body}${late}
      <p class="note" style="margin-top:6px">회사 발표뿐 아니라 언론 분석·리서치·테마 기사도 포함합니다. 기사를 누르면 기사 영향 분석에서 자세히 볼 수 있습니다.</p></section>`;
  }

  function macroBlock(c) {
    const mc = c.macro;
    const names = (mc.theme_names || []).join(", ");
    return `<h3 class="d-sec">업종·테마·거시 배경</h3>
      ${mc.verdict ? `<div class="followup">${esc(mc.verdict)}</div>` : ""}
      <ul class="clues">${mc.lines.map((l) => `<li class="${l.strong ? "strong" : ""}"><span class="ck">${esc(l.kind)}</span><span>${esc(l.text)}${l.url ? ` <a class="ext" href="${esc(l.url)}" target="_blank" rel="noopener">보기</a>` : ""}</span></li>`).join("")}
        ${(mc.sector_news || []).length ? `<li class="strong"><span class="ck">업종 시황 기사</span><span>${mc.sector_news.slice(0, 4).map((x) => `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a> <small class="muted">${esc(x.office)} ${esc(x.ts.slice(5, 16).replace("T", " "))}</small>`).join("<br>")}</span></li>` : ""}</ul>
      <p class="note">${names ? `이 종목 테마(네이버 분류): ${esc(names)}. ` : ""}시장 뉴스는 네이버 증권 주요뉴스·실시간 속보를 주제별로 분류한 것입니다. 같은 시기에 있었던 일을 모은 배경 정보이며, 실제 주문 이유를 확정하지는 않습니다.</p>`;
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
        <p class="note">${e_isDailyNote(c)} 비교 종목은 이 종목 사업 분야별 고객사·동종업체입니다. '평소 연동성'은 과거 약 110~120거래일 동안 시장(KOSDAQ) 영향을 뺀 뒤 이 종목과 그 분야가 함께 움직인 정도(상관계수)입니다. 95% 범위의 아래쪽 값이 0보다 커야(우연으로 보기 어려울 때만) 원인 판단에 씁니다.</p>`
        : `<p class="note">이 시간대 비교 종목 시세가 없습니다.</p>`}`;
  }
  const e_isDailyNote = (c) => (c && c.move && c.move.from && c.move.to ? "같은 기간 변동률입니다." : "");

  function newsBlock(c) {
    const news = (c && c.news) || [], discl = (c && c.disclosures) || [];
    if (!news.length && !discl.length) return `<h3 class="d-sec">뉴스·공시</h3><p class="note">이 시간대 앞뒤로 확인된 뉴스와 공시가 없습니다.</p>`;
    return `<h3 class="d-sec">뉴스·공시</h3><ul class="news">
      ${discl.map((x) => `<li><span class="when">${esc(x.date.slice(5))}</span><span class="timing same">공시</span><span class="tt">${esc(x.title)}</span></li>`).join("")}
      ${news.map((n) => `<li><span class="when">${esc(n.ts.slice(5, 16).replace("T", " "))}</span>
        <span class="timing ${n.timing === "선행" ? "lead" : "late"}">${n.timing === "선행" ? "움직임 전" : "움직임 후"}</span>
        <span class="tt">${n.peer ? `<em class="peer">${esc(n.peer)}</em>` : ""}<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a><small>${esc(n.office)}</small></span></li>`).join("")}
    </ul><p class="note">움직임 뒤에 나온 기사는 주가가 먼저 움직인 뒤 확인된 정보라 원인으로 단정하지 않습니다.</p>`;
  }

  function renderEventDrawer(d) {
    const e = d.event, c = d.cause;
    const span = e.last_ts !== e.start_ts ? `${hhmm(e.start_ts)}부터 ${hhmm(e.last_ts)}까지` : `${hhmm(e.start_ts)}`;
    const daily = isDaily(e);
    $("#drawer-body").innerHTML = `
      <span class="chip ${e.direction}">${kindOf(e)}</span>${scopeTag(e)}${tierTag(e)}
      <h2 class="d-title" id="drawer-title">${dayLabel(e.start_ts)}${daily ? "" : ` ${hhmm(e.start_ts)}`} <span class="${sgnCls(e.peak_return_5m)}">${pct(e.peak_return_5m)}</span></h2>
      <p class="d-sub">${daily ? "전일 종가 대비 종가 기준 움직임입니다." : `${span} 사이 움직임입니다.`}</p>
      <dl class="facts">
        <div><dt>${daily ? "종가 등락" : "구간 최대 움직임"}</dt><dd class="num ${sgnCls(e.peak_return_5m)}">${pct(e.peak_return_5m)}<small>${daily ? "" : hhmm(e.peak_ts)}</small></dd></div>
        <div><dt>KOSDAQ 같은 기간</dt><dd class="num">${e.kosdaq_change == null ? '<small style="margin:0">데이터 없음</small>' : pct(e.kosdaq_change)}</dd></div>
        <div><dt>거래대금</dt><dd class="num">${e.trade_value ? eok(e.trade_value) : "-"}</dd></div>
      </dl>
      <section class="d-block">${causeBlock(c, e.id)}</section>
      ${articleBlock(c)}
      <section class="d-block">${flowBlock(c)}</section>
      ${c && c.buyer ? `<section class="d-block">${buyerBlock(c, d.followups)}</section>` : ""}
      ${c && c.macro ? `<section class="d-block">${macroBlock(c)}</section>` : ""}
      ${boardBlock(c) ? `<section class="d-block">${boardBlock(c)}</section>` : ""}
      <section class="d-block"><h3 class="d-sec">차트</h3>
        <div class="chart-legend" id="d-legend"></div>
        <div class="chart-box" id="d-chart"></div>
        <p class="note" style="margin-top:0">${daily ? "그날 장중 1분봉입니다." : "전 30분부터 후 60분까지. 색칠된 거래량 막대가 급등·급락 구간입니다."}</p></section>
      <section class="d-block">${relatedBlock(c, e.own_change)}</section>
      <section class="d-block">${newsBlock(c)}</section>
      <div class="rule-full"><b>감지 근거</b><br>${esc(e.rule)}</div>`;
    const ch = priceVolumeChart($("#d-chart"), $("#d-legend"), d.bars, { events: daily ? [] : [e] });
    drawerCharts.push(ch);
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

  function alertInline(el, msg) {
    const p = document.createElement("p");
    p.className = "note"; p.style.color = "var(--up)"; p.textContent = msg;
    el.parentElement.after(p);
  }

  // ---------------------------------------------------------------- articles (기사 영향 분석)
  const CAT_CLS = { "회사 발표": "c-co", "언론 분석": "c-press", "리서치": "c-res", "테마·수혜주": "c-theme", "시황·특징주": "c-mkt", "단순 언급": "c-etc" };
  const TONE_TXT = { 1: "호재", "-1": "악재", 0: "" };
  const catTag = (c) => `<span class="ctag ${CAT_CLS[c] || "c-etc"}">${esc(c)}</span>`;
  const toneTag = (t) => (t ? `<span class="tone ${t > 0 ? "pos" : "neg"}">${t > 0 ? "호재" : "악재"}</span>` : "");
  const verdictTag = (e) => (e && e.verdict ? `<span class="vtag v-${e.verdict_cls}">${esc(e.verdict)}</span>` : "");
  const artState = { cat: "main" };

  function measureTxt(m) {
    if (!m) return "";
    return `${esc(m.label)} <b class="${sgnCls(m.value)}">${pct(m.value)}</b>${m.vol_ratio ? `, 거래량 평소의 ${m.vol_ratio.toFixed(1)}배` : ""}`;
  }

  async function pagePR(selId) {
    main.innerHTML = `<div class="wrap">
      <div class="page-head"><div><h1 class="page-title">기사 영향 분석</h1>
        <p class="page-sub" id="art-sub">이 종목이 다뤄진 기사를 모두 모아, 기사가 나간 뒤 주가가 같은 업종·KOSDAQ보다 얼마나 더 움직였는지(초과 반응) 보여줍니다.</p></div>
        <button class="btn editable" type="button" id="pr-toggle">기사 직접 등록</button></div>
      <form class="panel form pr-form editable" id="pr-form" autocomplete="off" hidden>
        <p class="hint">네이버에 아직 안 올라온 보도자료나, 정확한 배포 시각을 아는 기사를 등록합니다. 분봉은 최근 약 7거래일, 일봉은 1년 전까지 계산됩니다.</p>
        <div class="pr-form-grid">
          <label class="field"><span>제목</span><input name="title" required placeholder="예: 이 종목, 위성용 광학계 공급 계약"></label>
          <label class="field"><span>최초 공개일</span><input type="date" name="date" value="${isoDate(new Date())}" required></label>
          <label class="field"><span>공개 시각</span><input type="time" name="time" value="${String(new Date().getHours()).padStart(2, "0")}:${String(new Date().getMinutes()).padStart(2, "0")}" required></label>
          <label class="field"><span>기사 주소 (선택)</span><input name="url" type="url" placeholder="https://"></label>
          <button class="btn primary" type="submit">등록하고 분석하기</button>
        </div>
        <div class="form-msg" id="pr-msg" role="status"></div>
      </form>
      <div id="art-summary"></div>
      <div class="controls"><div class="seg" id="art-cat" aria-label="기사 유형"></div></div>
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

    let d;
    try { d = await api("/api/articles"); }
    catch (e) { $("#art-list").innerHTML = `<li class="empty"><strong>기사를 불러오지 못했습니다.</strong>${esc(e.message)}</li>`; return; }
    const items = d.items;
    const counts = {};
    items.forEach((a) => { counts[a.category] = (counts[a.category] || 0) + 1; });
    $("#art-sub").innerHTML = items.length
      ? `이 종목이 다뤄진 기사 ${items.length}건(같은 내용은 하나로 묶음). 기사가 나간 뒤 주가가 같은 업종·KOSDAQ보다 얼마나 더 움직였는지(<b>초과 반응</b>)로 봅니다.`
      : "아직 모인 기사가 없습니다. 감시 엔진이 켜지면 네이버에서 이 종목 기사를 자동으로 모읍니다.";

    // 유형별 요약
    const sum = (d.summary || []).filter((x) => x.category !== "단순 언급");
    $("#art-summary").innerHTML = sum.length ? `<div class="sum-cards">${sum.map((x) => `
      <div class="sum-card"><div class="sc-top">${catTag(x.category)}<span class="muted">${x.count}건</span></div>
        <div class="sc-main ${sgnCls(x.avg)}">${x.avg == null ? "-" : pct(x.avg)}</div>
        <div class="sc-cap">평균 초과 반응${x.measured < x.count ? ` (계산 ${x.measured}건)` : ""}</div>
        <div class="sc-sub">${x.pos_n ? `호재 기사 평균 <b class="${sgnCls(x.pos_avg)}">${pct(x.pos_avg)}</b>` : ""}${x.pos_n && x.neg_n ? " · " : ""}${x.neg_n ? `악재 기사 평균 <b class="${sgnCls(x.neg_avg)}">${pct(x.neg_avg)}</b>` : ""}${!x.pos_n && !x.neg_n ? "&nbsp;" : ""}</div>
        <div class="sc-sub">뚜렷한 반응 ${x.strong}건</div></div>`).join("")}</div>
      <p class="note" style="margin:-6px 0 18px">초과 반응 = 이 종목 변동률 − 같은 업종 평균(없으면 KOSDAQ). 분봉 기록이 있으면 공개 후 60분, 없으면 반응일 하루 기준입니다. ${d.daily_since ? `일봉은 ${esc(d.daily_since)}부터 있습니다.` : ""}</p>` : "";

    const nManual = items.filter((a) => a.manual).length;
    const cats = [["main", "주요 기사"], ...(nManual ? [["manual", "직접 등록"]] : []), ...d.categories.map((c) => [c, c])];
    counts.manual = nManual;
    const seg = $("#art-cat");
    seg.innerHTML = cats.map(([v, l]) => `<button type="button" data-v="${esc(v)}" aria-pressed="${artState.cat === v}">${esc(l)}${v !== "main" && counts[v] ? ` <small>${counts[v]}</small>` : ""}</button>`).join("");
    seg.onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; artState.cat = b.dataset.v; renderList(); };

    function renderList() {
      seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === artState.cat)));
      const list = items.filter((a) => artState.cat === "main" ? a.category !== "단순 언급"
        : artState.cat === "manual" ? a.manual : a.category === artState.cat);
      const ul = $("#art-list");
      if (!list.length) { ul.innerHTML = `<li class="empty"><strong>이 유형의 기사가 없습니다.</strong></li>`; return; }
      ul.innerHTML = list.map((a) => `<li><button type="button" data-id="${esc(a.id)}" aria-current="${a.id === current}">
        <span class="al-meta">${a.manual ? '<span class="tier watch">직접 등록</span>' : ""}${catTag(a.category)}${toneTag(a.tone)}<span class="muted">${dayLabel(a.ts)} ${hhmm(a.ts)}</span></span>
        <span class="t">${esc(a.title)}</span>
        <span class="al-foot">${verdictTag(a.effect)}${a.effect && a.effect.measure ? `<span class="num ${sgnCls(a.effect.measure.value)}">${pct(a.effect.measure.value)}</span>` : ""}<span class="muted">${esc(a.office)}${a.outlets_total > 1 ? ` 외 ${a.outlets_total - 1}곳` : ""}</span></span>
      </button></li>`).join("");
      ul.onclick = (ev) => { const b = ev.target.closest("button[data-id]"); if (b) location.hash = `#/pr/${b.dataset.id}`; };
    }
    let current = selId || (items.find((a) => a.category !== "단순 언급") || items[0] || {}).id;
    if (selId) { const a = items.find((x) => x.id === selId); if (a && a.category === "단순 언급") artState.cat = "단순 언급"; }
    renderList();
    if (!current) { $("#pr-detail").innerHTML = `<div class="empty"><strong>아직 기사가 없습니다.</strong>감시 엔진이 켜져 있으면 몇 분 안에 모입니다.</div>`; return; }
    loadPR(current);
  }

  function decompBlock(dc, tm, d0) {
    const p = dc.d0, sig = dc.sigma;
    const rows = [
      { k: "이 종목 실제 변동", v: p.own, cls: "me" },
      { k: `시장 몫 <small>KOSDAQ ${pct(p.kosdaq)} × 민감도 ${dc.beta_market.toFixed(2)}</small>`, v: p.market },
      { k: `업종 몫 <small>${p.sector_avg != null ? `업종 평균 ${pct(p.sector_avg)} 중 시장과 무관한 부분 × 민감도 ${dc.beta_sector.toFixed(2)}` : "같은 업종 시세 없음"}</small>`, v: p.sector },
      { k: "기사(고유) 몫 <small>= 실제 − 시장 몫 − 업종 몫</small>", v: p.abnormal, cls: "abn" },
    ];
    const max = Math.max(0.005, ...rows.map((r) => Math.abs(r.v || 0)));
    const zTxt = p.z != null ? `평소 이 종목 혼자 움직이는 폭(하루 ±${(sig * 100).toFixed(1)}%)의 ${Math.abs(p.z).toFixed(1)}배` : "";
    const judge = p.z == null ? "" : Math.abs(p.z) >= 2 ? "평소보다 확실히 큰 움직임이라 기사 효과로 볼 근거가 있습니다." : Math.abs(p.z) >= 1 ? "평소보다 다소 큰 정도라 기사 효과는 약하게만 보입니다." : "평소에도 이 정도는 움직이므로 기사 효과라고 보기 어렵습니다.";
    const two = dc.d01 && dc.d01.days === 2 ? `<p class="note" style="margin-top:6px">다음날까지 2일 누적: 실제 ${pct(dc.d01.own)}, 기사(고유) 몫 ${pct(dc.d01.abnormal)}${dc.d01.z != null ? ` (평소 폭의 ${Math.abs(dc.d01.z).toFixed(1)}배)` : ""}</p>` : "";
    return `<h3 class="d-sec" style="margin-top:22px">기사 효과 나누기 <small class="muted">반응일 ${esc(d0)}</small></h3>
      <ul class="rel decomp">${rows.map((r) => `<li class="${r.cls || ""}"><span class="nm">${r.k}</span>
        <span class="dbar"><i class="${(r.v || 0) >= 0 ? "b" : "s"}" style="${(r.v || 0) >= 0 ? "left:50%" : `left:${50 - (Math.abs(r.v) / max) * 50}%`};width:${(Math.abs(r.v || 0) / max) * 50}%"></i></span>
        <span class="num v ${sgnCls(r.v)}">${pct(r.v)}</span></li>`).join("")}</ul>
      <p class="flow-sum" style="margin-top:10px">${zTxt ? `기사(고유) 몫은 ${zTxt}입니다. ` : ""}${judge}</p>
      ${tm ? `<p class="flow-sum"><b>시간 순서</b> ${esc(tm.label)}</p>` : ""}
      ${two}
      <p class="note" style="margin-top:6px">${dc.method === "회귀" ? `민감도는 ${esc(dc.window[0])}~${esc(dc.window[1])} ${dc.n_obs}거래일의 일별 주가로 계산했습니다 (이 종목이 평소 시장·업종을 몇 배로 따라 움직이는지).` : "기록이 짧아 시장·업종을 1배로 단순 비교했습니다. 기록이 쌓이면 자동으로 정밀 계산으로 바뀝니다."}</p>`;
  }

  async function loadPR(id) {
    const box = $("#pr-detail");
    box.innerHTML = `<p class="loading">계산하는 중…</p>`;
    let d;
    try { d = await api(`/api/articles/${id}`); }
    catch (e) { box.innerHTML = `<div class="empty"><strong>불러오지 못했습니다.</strong>${esc(e.message)}</div>`; return; }
    const a = d.article, e = d.effect, m = e.minute, dl = e.daily;
    const notOurs = a.category !== "회사 발표" && !a.manual;
    let html = `<div class="al-meta">${catTag(a.category)}${toneTag(a.tone)}${notOurs ? '<span class="muted">회사가 낸 기사가 아닙니다</span>' : ""}</div>
      <h2 class="pr-title">${esc(a.title)}</h2>
      <p class="pr-meta">${esc(a.office)} · ${dayLabel(a.ts)} ${hhmm(a.ts)} 공개${a.outlets_total > 1 ? ` · 같은 내용 ${a.outlets_total}개 매체` : ""}${a.url ? ` · <a href="${esc(a.url)}" target="_blank" rel="noopener">기사 열기</a>` : ""}</p>`;
    if (a.body) html += `<p class="art-body">${esc(a.body)}</p>`;

    html += `<div class="verdict-box v-${e.verdict_cls}"><div class="vb-head">${esc(e.verdict || "")}</div><div>${e.measure ? measureTxt(e.measure) : (e.reaction_day ? "" : "기사가 나온 무렵의 시세 기록이 없어 계산하지 못했습니다.")}</div>
      ${a.category === "시황·특징주" ? `<div class="muted" style="margin-top:4px">주가가 움직인 뒤 그 움직임을 다룬 기사라, 원인보다 결과에 가깝습니다.</div>` : ""}
      ${a.category === "단순 언급" ? `<div class="muted" style="margin-top:4px">이 종목 이름만 나오는 기사라 같은 날 움직임을 이 기사 효과로 보기 어렵습니다.</div>` : ""}</div>`;

    if (e.decomp && e.decomp.d0) html += decompBlock(e.decomp, e.timing, e.reaction_day);
    if (dl) {
      const row = (k, label) => `<tr><td class="h">${label}</td><td class="v num ${sgnCls(dl[k])}">${pct(dl[k])}</td><td class="v num">${pct((dl.peers || {})[k])}</td><td class="v num">${pct((dl.kosdaq || {})[k])}</td><td class="v num ${sgnCls((dl.excess || {})[k])}"><b>${pct((dl.excess || {})[k])}</b></td></tr>`;
      html += `<h3 class="d-sec" style="margin-top:22px">일별 반응 <small class="muted">반응일 ${esc(e.reaction_day)} · 전날 종가 ${won(dl.prev_close)}원 기준</small></h3>
        <table class="ladder"><thead><tr><th></th><th style="text-align:right">이 종목</th><th style="text-align:right">같은 업종</th><th style="text-align:right">KOSDAQ</th><th style="text-align:right">초과 반응</th></tr></thead><tbody>
        ${row("d0", "반응일")}${row("d1", "다음날까지")}${row("d3", "3거래일")}</tbody></table>
        <p class="note" style="margin-top:4px">초과 반응은 ${esc(dl.bench_name)} 대비입니다. 반응일 거래량 ${int(dl.volume)}주${dl.vol_ratio ? `, 직전 20일 평균의 ${dl.vol_ratio.toFixed(1)}배` : ""}.${new Date(a.ts.replace("T", " ")).getHours() >= 16 || a.ts.slice(11) > "15:30" ? " 장 마감 뒤 공개라 다음 거래일부터 반영됩니다." : ""}</p>`;
    }
    if (m) {
      const hs = ["5", "15", "30", "60", "120"].filter((h) => m.returns[h] != null);
      const bench = Object.keys(m.peers || {}).length ? m.peers : m.kosdaq;
      html += `<h3 class="d-sec" style="margin-top:22px">공개 직후 반응 <small class="muted">${m.outside_session ? `장 외 공개 → ${hhmm(m.reaction_start)} 장 시작부터` : `${hhmm(m.reaction_start)}부터`}</small></h3>
        <table class="ladder"><thead><tr><th></th><th style="text-align:right">이 종목</th><th style="text-align:right">${esc(m.bench_name)}</th><th style="text-align:right">초과 반응</th></tr></thead><tbody>
        ${hs.map((h) => `<tr><td class="h">+${h}분</td><td class="v num ${sgnCls(m.returns[h])}">${pct(m.returns[h])}</td><td class="v num">${pct(bench[h])}</td><td class="v num ${sgnCls(m.excess[h])}"><b>${pct(m.excess[h])}</b></td></tr>`).join("")}
        </tbody></table>
        <p class="note" style="margin-top:4px">120분 안 최고 ${pct(m.max_up)}(${hhmm(m.max_up_at)}), 최저 ${pct(m.max_down)}(${hhmm(m.max_down_at)})${m.vol_after30 != null ? `. 공개 후 30분 거래량 ${int(m.vol_after30)}주${m.vol_usual30 ? `(평소 ${int(m.vol_usual30)}주)` : ""}` : ""}.</p>`;
    }
    html += `<div class="chart-legend" id="pr-legend" style="margin-top:14px"></div><div class="chart-box" id="pr-chart"></div>`;
    if ((d.confounders || []).length) html += `<h3 class="d-sec" style="margin-top:18px">함께 봐야 할 것</h3><ul class="notes" style="margin-top:0">${d.confounders.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    if ((e.events || []).length) html += `<div class="rule-full"><b>반응일에 감지된 이상변동</b><br>${e.events.map((x) => `<a href="#/events" data-ev="${x.id}">${hhmm(x.start_ts)} ${KIND[x.direction]} ${pct(x.peak_return_5m)}</a>`).join(", ")}</div>`;
    if ((a.related_titles || []).length) html += `<div class="rule-full"><b>같은 내용의 다른 기사</b><br>${a.related_titles.map((r) => `${dayLabel(r.ts)} ${hhmm(r.ts)} ${esc(r.office)} <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>`).join("<br>")}</div>`;
    html += `<p class="note">시간상·업종 대비 연관성을 보여주는 것이며, 이 기사 때문에 움직였다고 단정하지는 않습니다.</p>`;
    html += `<p class="editable" style="margin-top:18px">${a.manual ? `<button class="btn danger" type="button" id="pr-del">이 기사 삭제</button>` : `<button class="btn" type="button" id="art-hide">${a.hidden ? "목록에 다시 표시" : "관계없는 기사라 목록에서 빼기"}</button>`}</p>`;
    box.innerHTML = html;

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
    box.querySelectorAll("[data-ev]").forEach((x) => x.addEventListener("click", (ev) => { ev.preventDefault(); openEvent(+x.dataset.ev); }));
    const queued = (btn, text) => { btn.disabled = true; btn.textContent = STATIC ? "GitHub 창에서 [Create]를 누르면 반영됩니다" : "요청을 보냈습니다 (다음 자동 수집 때 반영)"; btn.title = text || ""; };
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
          <label class="field"><span>새 비밀번호 (8자 이상)</span><input type="password" name="pw" required minlength="8" autocomplete="new-password"></label>
          <label class="field"><span>한 번 더</span><input type="password" name="pw2" required minlength="8" autocomplete="new-password"></label>
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
          <label class="field"><span>사이트 비밀번호 (8자 이상)</span><input name="pw" type="password" required minlength="8" autocomplete="new-password"></label>
          <label class="field"><span>한 번 더</span><input name="pw2" type="password" required minlength="8" autocomplete="new-password"></label>
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
    heroChart = null;
    closeDrawer();
    const h = location.hash.replace(/^#/, "") || "/";
    const parts = h.split("/").filter(Boolean);
    const page = parts[0] || "dash";
    document.querySelectorAll(".tabs a").forEach((a) => {
      const on = a.dataset.route === (page === "dash" ? "dash" : page);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    $("#hero").hidden = page !== "dash";
    window.scrollTo(0, 0);
    if (page === "events") { document.title = "이벤트 기록 | 주가 모니터"; await pageEvents(); if (parts[1]) openEvent(+parts[1]); }
    else if (page === "cloud" && !CLOUD) { document.title = "어디서나 보기 | 주가 모니터"; await pageCloud(); }
    else if (page === "pr") { document.title = "기사 영향 분석 | 주가 모니터"; await pagePR(parts[1] || null); }
    else { document.title = "주가 모니터"; await pageDashboard(); }
  }

  window.addEventListener("hashchange", route);
  (STATIC ? ensureLogin() : Promise.resolve()).then(() => {
    refreshStatus().then(route);
    setInterval(refreshStatus, STATIC ? 30000 : 10000);
  });
})();
