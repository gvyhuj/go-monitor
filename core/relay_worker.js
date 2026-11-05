// 실시간 중계: 정규장 시세와 16:00~18:00 시간외 단일가를 별도 응답으로 제공.
// - 한투 열쇠(KIS_APP_KEY·KIS_APP_SECRET)는 이 Worker의 비밀값에만 있음 (사이트·브라우저에는 없음)
// - 접속 토큰은 여기서 받지 않음: GitHub 실행이 하루 한 번 받아 KV('kis_token')에 넣어 둔 것을 씀 (토큰 발급은 한 곳에서만)
// - 사이트 비밀번호를 아는 사람만: 암호화된 사이트 자료 안의 AUTH 값을 x-go-auth 머리말로 보내야 답함
// - 계정 보호가 먼저: 한투는 15초에 한 번까지만(현재가·KOSDAQ), 1분봉은 1분에 한 번, 요청 사이 1초.
//   한투가 거절하면 다시 묻지 않고 직전 답을 그대로 (5분 넘게 묵은 답은 버림)
const BASE = "https://openapi.koreainvestment.com:9443";
const MEMO_MS = 15000;
const BARS_MS = 60000;
const GAP_MS = 1000;
const STALE_MS = 300000;
let memo = { at: 0, okAt: 0, body: null };
let barsMemo = { at: 0, bars: [], kq: [] };
let lastCall = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const num = (v) => {
  const f = Number(String(v ?? "").replace(/,/g, ""));
  return v === undefined || v === null || v === "" || Number.isNaN(f) ? null : f;
};

function kstNow(ms) {
  const k = new Date(ms + 9 * 3600e3).toISOString();      // 한국 시각
  return { day: k.slice(0, 10), hm: k.slice(11, 16), hms: k.slice(11, 19).replace(/:/g, "") };
}

async function kis(env, tok, path, trId, params) {
  const wait = lastCall + GAP_MS - Date.now();
  if (wait > 0) await sleep(wait);
  lastCall = Date.now();
  const r = await fetch(`${BASE}${path}?${new URLSearchParams(params)}`, {
    headers: { "content-type": "application/json; charset=utf-8", authorization: `Bearer ${tok}`,
               appkey: env.KIS_APP_KEY, appsecret: env.KIS_APP_SECRET, tr_id: trId, custtype: "P" },
  });
  let j = null;
  try { j = await r.json(); } catch (_) { /* 아래에서 처리 */ }
  if (!r.ok || !j || String(j.rt_cd ?? "0") !== "0") throw new Error(`${trId} ${r.status} ${(j && (j.msg1 || j.msg_cd)) || ""}`.trim());
  return j;
}

async function live(env, now) {
  const saved = await env.KV.get("kis_token", "json");
  if (!saved || !saved.token || !(saved.exp_ms > now + 60e3)) return { status: 503, body: { error: "token" } };
  const t = kstNow(now), tok = saved.token, code = env.CODE;
  if (t.hm >= "16:00" && t.hm <= "18:00") {
    let o = {}, failed = null;
    try {
      o = (await kis(env, tok, "/uapi/domestic-stock/v1/quotations/inquire-overtime-price", "FHPST02300000",
        { FID_COND_MRKT_DIV_CODE: "J", FID_INPUT_ISCD: code })).output || {};
    } catch (e) { failed = e; }
    const price = num(o.ovtm_untp_prpr), volume = num(o.ovtm_untp_vol);
    const queried = new Date(now + 9 * 3600e3).toISOString().slice(0, 19);
    let after = price > 0 && volume > 0 ? { date: t.day, price, volume, source: "kis", session: "krx_overtime", at: t.hm, queried_at: queried } : null;
    if (!after) {
      try { after = await naverOvertime(code, t.day, queried); } catch (_) { /* 미확인 응답은 가격으로 대체하지 않음 */ }
    }
    if (!after && failed) throw failed;
    return { status: 200, body: { session: "overtime", at: `${t.day}T${t.hm}`, after } };
  }
  const q = (await kis(env, tok, "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
    { FID_COND_MRKT_DIV_CODE: "J", FID_INPUT_ISCD: code })).output || {};
  const ix = (await kis(env, tok, "/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
    { FID_COND_MRKT_DIV_CODE: "U", FID_INPUT_ISCD: "1001" })).output || {};
  if (now - barsMemo.at >= BARS_MS) {           // 1분봉은 1분에 한 번만
    const until = t.hms < "153000" ? t.hms : "153000";
    const rows = (await kis(env, tok, "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
      { FID_ETC_CLS_CODE: "", FID_COND_MRKT_DIV_CODE: "J", FID_INPUT_ISCD: code, FID_INPUT_HOUR_1: until, FID_PW_DATA_INCU_YN: "N" })).output2 || [];
    const kq = (await kis(env, tok, "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice", "FHKUP03500200",
      { FID_COND_MRKT_DIV_CODE: "U", FID_ETC_CLS_CODE: "0", FID_INPUT_ISCD: "1001", FID_INPUT_HOUR_1: "60", FID_PW_DATA_INCU_YN: "Y" })).output2 || [];
    barsMemo = { at: now, bars: parseBars(rows), kq: parseBars(kq, "bstp_nmix_prpr").map((b) => ({ t: b.t, c: b.c })) };
  }
  return { status: 200, body: {
    at: `${t.day}T${t.hm}`,
    quote: { price: num(q.stck_prpr), change: num(q.prdy_vrss), rate: num(q.prdy_ctrt), open: num(q.stck_oprc),
             high: num(q.stck_hgpr), low: num(q.stck_lwpr), base: num(q.stck_sdpr), volume: num(q.acml_vol), value: num(q.acml_tr_pbmn) },
    kosdaq: { price: num(ix.bstp_nmix_prpr), change: num(ix.bstp_nmix_prdy_vrss), rate: num(ix.bstp_nmix_prdy_ctrt) },
    bars: barsMemo.bars,
    kosdaq_bars: barsMemo.kq,
  } };
}

async function naverOvertime(code, day, queried) {
  const r = await fetch(`https://polling.finance.naver.com/api/realtime/domestic/stock/${encodeURIComponent(code)}`, {
    headers: { accept: "application/json", "user-agent": "Mozilla/5.0", referer: "https://m.stock.naver.com/" }, signal: AbortSignal.timeout(5000),
  });
  if (!r.ok) return null;
  const q = (await r.json()).datas?.[0], stamp = q?.localTradedAt;
  if (!q || q.marketSessionType !== "afterMarket" || !/(Z|[+-]\d{2}:\d{2})$/.test(stamp || "")) return null;
  const ms = Date.parse(stamp);
  if (!Number.isFinite(ms)) return null;
  const traded = kstNow(ms), price = num(q.closePriceRaw || q.closePrice);
  if (traded.day !== day || traded.hm < "16:00" || traded.hm > "18:30" || !(price > 0)) return null;
  // 누적 거래량은 시간외 거래량이 아니다. 세션이 명시된 시세만 전달한다.
  return { date: day, price, volume: null, source: "naver", session: "afterMarket", traded_at: `${day}T${traded.hm}:00+09:00`, at: traded.hm, queried_at: queried };
}

function parseBars(rows, px = "stck_prpr") {
  const bars = [];
  for (const r of rows) {
    const d = r.stck_bsop_date || "", h = r.stck_cntg_hour || "";
    if (d.length !== 8 || h.length !== 6) continue;
    const c = num(r[px]);
    bars.push({ t: `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}T${h.slice(0, 2)}:${h.slice(2, 4)}`,
                o: num(r.stck_oprc) ?? c, h: num(r.stck_hgpr) ?? c, l: num(r.stck_lwpr) ?? c, c, v: num(r.cntg_vol) ?? 0 });
  }
  return bars.sort((a, b) => (a.t < b.t ? -1 : 1));
}

export default {
  async fetch(req, env) {
    const cors = { "Access-Control-Allow-Origin": env.ORIGIN || "*", "Access-Control-Allow-Headers": "x-go-auth",
                   "Access-Control-Allow-Methods": "GET, OPTIONS", "Cache-Control": "no-store", Vary: "Origin" };
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { ...cors, "content-type": "application/json; charset=utf-8" } });
    if (!env.AUTH || req.headers.get("x-go-auth") !== env.AUTH) return json({ error: "auth" }, 401);
    if (new URL(req.url).pathname !== "/live") return json({ error: "path" }, 404);
    const now = Date.now();
    const t = kstNow(now), overtime = t.hm >= "16:00" && t.hm <= "18:00";
    // 장이 바뀌면 이전 세션의 응답을 재사용하지 않음.
    if (memo.body && ((memo.body.session === "overtime") !== overtime || memo.body.at?.slice(0, 10) !== t.day)) memo = { at: 0, okAt: 0, body: null };
    if (now - memo.at < MEMO_MS) {      // 15초 안에는 한투를 다시 부르지 않음 (실패했을 때도)
      return memo.body && now - memo.okAt < STALE_MS ? json(memo.body) : json({ error: "wait" }, 503);
    }
    memo.at = now;
    try {
      const r = await live(env, now);
      if (r.status === 200) memo = { at: now, okAt: now, body: r.body };
      return json(r.body, r.status);
    } catch (e) {                        // 거절되면 다시 묻지 않고 직전 답(5분 안이면) 그대로
      if (memo.body && now - memo.okAt < STALE_MS) return json({ ...memo.body, stale: true });
      return json({ error: String(e.message || e).slice(0, 160) }, 502);
    }
  },
};
