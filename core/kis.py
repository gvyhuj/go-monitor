"""한국투자증권 Open API (KIS Developers) — 시세 조회만 (주문 없음).

열쇠: 환경 변수 KIS_APP_KEY / KIS_APP_SECRET (GitHub 저장소 Secrets). 없으면 아무것도 하지 않음.
접속 토큰: 앱키로 하루(약 24시간)짜리 토큰을 받아 기록(system_state 'kis:token')에 두고, 끝나기 1시간 전까지 다시 씀.
  (한투는 토큰을 너무 자주 새로 받으면 막으므로 실행마다 새로 받지 않음. 토큰 값은 기록·로그에 그대로 남기지 않음)

    https://openapi.koreainvestment.com:9443
    POST /oauth2/tokenP                                                접속 토큰
    GET  /uapi/domestic-stock/v1/quotations/inquire-price              주식 현재가 (tr_id FHKST01010100)
    GET  /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice 당일 1분봉 30개씩 (FHKST03010200)
    GET  /uapi/domestic-stock/v1/quotations/inquire-index-price        업종(지수) 현재가 (FHPUP02100000, KOSDAQ=1001)
    GET  /uapi/domestic-stock/v1/quotations/inquire-investor           투자자별 매매 (FHKST01010900)
시장 구분(FID_COND_MRKT_DIV_CODE): J=거래소(KRX), NX=넥스트레이드, UN=통합, U=업종 지수.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

BASE = os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
TOKEN_KEY = "kis:token"
KOSDAQ_INDEX = "1001"


class KisError(Exception):
    pass


def enabled() -> bool:
    return bool(os.environ.get("KIS_APP_KEY") and os.environ.get("KIS_APP_SECRET"))


def _http(method: str, path: str, *, headers: dict, params: dict | None = None, body: dict | None = None, timeout=10):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"content-type": "application/json; charset=utf-8", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8"))
            msg = msg.get("msg1") or msg.get("error_description") or msg
        except Exception:  # noqa: BLE001
            msg = e.reason
        raise KisError(f"{path} {e.code}: {str(msg)[:160]}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise KisError(f"{path}: {type(e).__name__}: {str(e)[:160]}") from None


def token(now: datetime | None = None) -> str:
    """기록에 둔 토큰이 1시간 넘게 남았으면 그대로, 아니면 새로 받음."""
    from .db import get_state, set_state
    now = now or datetime.now()
    st = get_state(TOKEN_KEY)
    if st and st.get("value"):
        try:
            v = json.loads(st["value"])
            if datetime.fromisoformat(v["expires"]) - now > timedelta(hours=1) and v.get("token"):
                return v["token"]
        except (ValueError, KeyError, TypeError):
            pass
    r = _http("POST", "/oauth2/tokenP", headers={}, body={
        "grant_type": "client_credentials", "appkey": os.environ["KIS_APP_KEY"], "appsecret": os.environ["KIS_APP_SECRET"]})
    tok = r.get("access_token")
    if not tok:
        raise KisError(f"토큰 발급 실패: {str(r.get('error_description') or r)[:160]}")
    exp = r.get("access_token_token_expired")
    try:
        expires = datetime.strptime(exp, "%Y-%m-%d %H:%M:%S") if exp else now + timedelta(seconds=int(r.get("expires_in") or 86400))
    except ValueError:
        expires = now + timedelta(seconds=int(r.get("expires_in") or 86400))
    set_state(TOKEN_KEY, json.dumps({"token": tok, "expires": expires.isoformat(timespec="seconds")}))
    return tok


_LAST = [0.0]
GAP = float(os.environ.get("KIS_MIN_GAP", "0.6"))      # 요청 사이 최소 간격(초). 한투 '초당 거래건수 초과'를 피하려고


def _get(path: str, tr_id: str, params: dict, _retry: bool = True) -> dict:
    wait = _LAST[0] + GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _LAST[0] = time.monotonic()
    try:
        return _get_once(path, tr_id, params)
    except KisError as e:
        if _retry and "초당" in str(e):                # 한도에 걸리면 1초 쉬고 한 번만 다시
            time.sleep(1.0)
            return _get(path, tr_id, params, _retry=False)
        raise


def _get_once(path: str, tr_id: str, params: dict) -> dict:
    h = {"authorization": f"Bearer {token()}", "appkey": os.environ["KIS_APP_KEY"],
         "appsecret": os.environ["KIS_APP_SECRET"], "tr_id": tr_id, "custtype": "P"}
    r = _http("GET", path, headers=h, params=params)
    if str(r.get("rt_cd", "0")) != "0":
        raise KisError(f"{tr_id}: {r.get('msg_cd', '')} {str(r.get('msg1', ''))[:160]}")
    return r


def _num(v):
    try:
        f = float(str(v).replace(",", ""))
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def quote(code: str, market: str = "J") -> dict:
    o = _get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
             {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code}).get("output") or {}
    return {"price": _num(o.get("stck_prpr")), "change": _num(o.get("prdy_vrss")), "rate": _num(o.get("prdy_ctrt")),
            "open": _num(o.get("stck_oprc")), "high": _num(o.get("stck_hgpr")), "low": _num(o.get("stck_lwpr")),
            "base": _num(o.get("stck_sdpr")), "volume": _num(o.get("acml_vol")), "value": _num(o.get("acml_tr_pbmn")),
            "market_cap_100m": _num(o.get("hts_avls")), "per": _num(o.get("per")), "pbr": _num(o.get("pbr")),
            "eps": _num(o.get("eps")), "bps": _num(o.get("bps"))}


def minutes(code: str, until_hhmmss: str, market: str = "J") -> list[dict]:
    """그 시각까지의 당일 1분봉 (최근 30개, 오래된 순)."""
    rows = _get("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
                {"FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code,
                 "FID_INPUT_HOUR_1": until_hhmmss, "FID_PW_DATA_INCU_YN": "N"}).get("output2") or []
    out = []
    for r in rows:
        d, t = r.get("stck_bsop_date") or "", r.get("stck_cntg_hour") or ""
        if len(d) == 8 and len(t) == 6:
            out.append({"ts": f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}", "open": _num(r.get("stck_oprc")),
                        "high": _num(r.get("stck_hgpr")), "low": _num(r.get("stck_lwpr")),
                        "close": _num(r.get("stck_prpr")), "volume": _num(r.get("cntg_vol"))})
    return sorted(out, key=lambda x: x["ts"])


def index_quote(no: str = KOSDAQ_INDEX) -> dict:
    o = _get("/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
             {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": no}).get("output") or {}
    return {"price": _num(o.get("bstp_nmix_prpr")), "change": _num(o.get("bstp_nmix_prdy_vrss")),
            "rate": _num(o.get("bstp_nmix_prdy_ctrt"))}


def investors(code: str) -> list[dict]:
    rows = _get("/uapi/domestic-stock/v1/quotations/inquire-investor", "FHKST01010900",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}).get("output") or []
    return [{"date": r.get("stck_bsop_date"), "individual": _num(r.get("prsn_ntby_qty")),
             "foreign": _num(r.get("frgn_ntby_qty")), "organ": _num(r.get("orgn_ntby_qty"))} for r in rows]


def trial(code: str, now: datetime) -> str:
    """시험 운행: 한투에서 받은 값과 기록(네이버) 값을 나란히. 화면·기록은 바꾸지 않음. 실행 기록 한 줄."""
    from .db import connect, get_state
    parts, errs = [], []
    nq = {}
    st = get_state(f"quote:{code}")
    try:
        nq = json.loads(st["value"]) if st and st.get("value") else {}
    except ValueError:
        pass

    def step(label, fn):
        try:
            return fn()
        except KisError as e:
            errs.append(f"{label} {e}")
            return None

    q = step("현재가", lambda: quote(code))
    if q:
        nv = lambda k: nq.get(k) if nq.get(k) is not None else "-"
        parts.append(f"현재가 한투 {q['price']} / 네이버 {nv('price')}, 시가 {q['open']}, 고가 {q['high']}, 저가 {q['low']}, "
                     f"기준가 {q['base']}, 거래량 {q['volume']} / 네이버 {nv('volume')}, 시총 {q['market_cap_100m']}억, "
                     f"PER {q['per']} PBR {q['pbr']} EPS {q['eps']} BPS {q['bps']}")
    for mk in ("NX", "UN"):                         # 넥스트레이드·통합 시세를 따로 주는지
        x = step(f"현재가({mk})", lambda mk=mk: quote(code, mk))
        if x:
            parts.append(f"{mk} 현재가 {x['price']} 거래량 {x['volume']}")
    # 장중이면 지금까지, 아니면 마지막 거래일 장 마감(15:30)까지
    hhmm = now.strftime("%H%M%S") if now.weekday() < 5 and "090000" <= now.strftime("%H%M%S") <= "153000" else "153000"
    m = step("1분봉", lambda: minutes(code, hhmm))
    if m:
        with connect() as conn:
            mine = {r[0]: r[1:] for r in conn.execute(
                "SELECT ts, open, high, low, close FROM minute_bars WHERE symbol=? AND ts >= ? AND ts <= ?",
                (code, m[0]["ts"], m[-1]["ts"]))}
        same = sum(1 for b in m if b["ts"] in mine and abs((mine[b["ts"]][3] or 0) - (b["close"] or 0)) < 1e-6)
        parts.append(f"1분봉 {len(m)}개({m[0]['ts'][11:]}~{m[-1]['ts'][11:]}) 중 네이버 종가 일치 {same}·네이버에 없음 "
                     f"{sum(1 for b in m if b['ts'] not in mine)}")
    k = step("KOSDAQ", index_quote)
    if k:
        parts.append(f"KOSDAQ 한투 {k['price']} ({k['rate']}%)")
    inv = step("투자자", lambda: investors(code))
    if inv:
        parts.append(f"투자자 {inv[0]['date']} 개인 {inv[0]['individual']} 외국인 {inv[0]['foreign']} 기관 {inv[0]['organ']}")
    return "한투 시험: " + (" · ".join(parts) or "받은 값 없음") + (" · 실패: " + " / ".join(errs) if errs else "")
