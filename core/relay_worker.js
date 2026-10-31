// 실시간 중계 (Cloudflare Worker): 사이트가 장중 몇 초마다 묻는 '지금 가격'을 한투 Open API에서 받아 줌.
// - 한투 열쇠(KIS_APP_KEY·KIS_APP_SECRET)는 이 Worker의 비밀값에만 있음 (사이트·브라우저에는 없음)
// - 접속 토큰은 여기서 받지 않음: GitHub 실행이 하루 한 번 받아 KV('kis_token')에 넣어 둔 것을 씀 (토큰 발급은 한 곳에서만)
// - 사이트 비밀번호를 아는 사람만: 암호화된 사이트 자료 안의 AUTH 값을 x-go-auth 머리말로 보내야 답함
// - 한투는 3초에 한 번까지만 부름 (그 사이 요청은 직전 답을 그대로)
const BASE = "https://openapi.koreainvestment.com:9443";
const MEMO_MS = 3000;
let memo = { at: 0, body: null };

const num = (v) => {
  const f = Number(String(v ?? "").replace(/,/g, ""));
  return v === undefined || v === null || v === "" || Number.isNaN(f) ? null : f;
};

function kstNow(ms) {
  const k = new Date(ms + 9 * 3600e3).toISOString();      // 한국 시각
  return { day: k.slice(0, 10), hm: k.slice(11, 16), hms: k.slice(11, 19).replace(/:/g, "") };
}

async function kis(env, tok, path, trId, params) {
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
  const q = (await kis(env, tok, "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
    { FID_COND_MRKT_DIV_CODE: "J", FID_INPUT_ISCD: code })).output || {};
  const ix = (await kis(env, tok, "/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
    { FID_COND_MRKT_DIV_CODE: "U", FID_INPUT_ISCD: "1001" })).output || {};
  const until = t.hms < "153000" ? t.hms : "153000";
  const rows = (await kis(env, tok, "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
    { FID_ETC_CLS_CODE: "", FID_COND_MRKT_DIV_CODE: "J", FID_INPUT_ISCD: code, FID_INPUT_HOUR_1: until, FID_PW_DATA_INCU_YN: "N" })).output2 || [];
  const bars = [];
  for (const r of rows) {
    const d = r.stck_bsop_date || "", h = r.stck_cntg_hour || "";
    if (d.length !== 8 || h.length !== 6) continue;
    const c = num(r.stck_prpr);
    bars.push({ t: `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}T${h.slice(0, 2)}:${h.slice(2, 4)}`,
                o: num(r.stck_oprc) ?? c, h: num(r.stck_hgpr) ?? c, l: num(r.stck_lwpr) ?? c, c, v: num(r.cntg_vol) ?? 0 });
  }
  bars.sort((a, b) => (a.t < b.t ? -1 : 1));
  return { status: 200, body: {
    at: `${t.day}T${t.hm}`,
    quote: { price: num(q.stck_prpr), change: num(q.prdy_vrss), rate: num(q.prdy_ctrt), open: num(q.stck_oprc),
             high: num(q.stck_hgpr), low: num(q.stck_lwpr), base: num(q.stck_sdpr), volume: num(q.acml_vol), value: num(q.acml_tr_pbmn) },
    kosdaq: { price: num(ix.bstp_nmix_prpr), change: num(ix.bstp_nmix_prdy_vrss), rate: num(ix.bstp_nmix_prdy_ctrt) },
    bars,
  } };
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
    if (memo.body && now - memo.at < MEMO_MS) return json(memo.body);
    try {
      const r = await live(env, now);
      if (r.status === 200) memo = { at: now, body: r.body };
      return json(r.body, r.status);
    } catch (e) {
      return json({ error: String(e.message || e).slice(0, 160) }, 502);
    }
  },
};
