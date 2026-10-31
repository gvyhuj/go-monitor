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
ISSUED: list[str] = []      # 한투는 접속 토큰을 하루 한 번 원칙으로 발급 (잦으면 이용 제한) → 새로 받으면 실행 기록에 남김
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
    ISSUED.append(expires.isoformat(timespec="minutes"))      # 이번 실행에 새로 받은 토큰의 만료 시각 (실행 기록용, 값은 남기지 않음)
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


def members(code: str, market: str = "J") -> dict:
    """거래원(회원사) 그날 누적: 거래소가 공개하는 매수 상위 5곳·매도 상위 5곳 (주식현재가 회원사, FHKST01010600)."""
    o = _get("/uapi/domestic-stock/v1/quotations/inquire-member", "FHKST01010600",
             {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code}).get("output") or {}
    return parse_members(o)


def parse_members(o: dict) -> dict:
    """네이버 거래원과 같은 모양 {source, foreign_buy, foreign_sell, traders: [...]}.
    한투는 매수 상위 5곳의 매수 수량, 매도 상위 5곳의 매도 수량만 줌 → 상위 5위에 없는 쪽은 None(모름, 0이 아님)."""
    by: dict = {}
    for side, key in (("buy", "shnu"), ("sell", "seln")):
        for i in range(1, 6):
            no = str(o.get(f"{key}_mbcr_no{i}") or "").strip()
            name = str(o.get(f"{key}_mbcr_name{i}") or "").strip()
            if not (no or name):
                continue
            r = by.setdefault(no or name, {"trader_no": no or name, "name": name, "name_full": name, "buy": None, "sell": None,
                                            "foreign": False, "bizdate": None, "buy_top5": False, "sell_top5": False})
            r[side] = int(_num(o.get(f"total_{key}_qty{i}")) or 0)
            r[f"{side}_top5"] = True
            r["foreign"] = r["foreign"] or str(o.get(f"{key}_mbcr_glob_yn_{i}") or "").upper() == "Y"
    return {"source": "한투", "foreign_buy": _num(o.get("glob_total_shnu_qty")), "foreign_sell": _num(o.get("glob_total_seln_qty")),
            "traders": list(by.values())}


def _bar(r, d_key="stck_bsop_date", t_key="stck_cntg_hour", px=("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr"), vol="cntg_vol"):
    d, t = r.get(d_key) or "", r.get(t_key) or ""
    if len(d) != 8 or len(t) != 6:
        return None
    o, h, lo, c = (_num(r.get(k)) for k in px)
    return {"ts": f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}", "open": o, "high": h, "low": lo, "close": c, "volume": _num(r.get(vol))}


def day_minutes(code: str, day: str, until_hhmmss: str = "153000") -> list[dict]:
    """지난 날짜 1분봉 (그 시각까지 최근 120개쯤, 오래된 순). day=YYYY-MM-DD."""
    rows = _get("/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice", "FHKST03010230",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_HOUR_1": until_hhmmss,
                 "FID_INPUT_DATE_1": day.replace("-", ""), "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": ""}
                ).get("output2") or []
    return sorted([b for b in (_bar(r) for r in rows) if b], key=lambda b: b["ts"])


def index_minutes(no: str = KOSDAQ_INDEX) -> list[dict]:
    """KOSDAQ 지수 당일 1분봉 (오래된 순)."""
    rows = _get("/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice", "FHKUP03500200",
                {"FID_COND_MRKT_DIV_CODE": "U", "FID_ETC_CLS_CODE": "0", "FID_INPUT_ISCD": no,
                 "FID_INPUT_HOUR_1": "60", "FID_PW_DATA_INCU_YN": "Y"}).get("output2") or []
    px = ("bstp_nmix_oprc", "bstp_nmix_hgpr", "bstp_nmix_lwpr", "bstp_nmix_prpr")
    return sorted([b for b in (_bar(r, px=px) for r in rows) if b], key=lambda b: b["ts"])


def daily(code: str, start: str, end: str, index: bool = False) -> list[dict]:
    """일봉 (최대 100개, 오래된 순). start·end=YYYY-MM-DD. index=True면 업종 지수(KOSDAQ=1001)."""
    if index:
        path, tr = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice", "FHKUP03500100"
        params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code}
        keys = ("bstp_nmix_oprc", "bstp_nmix_hgpr", "bstp_nmix_lwpr", "bstp_nmix_prpr")
    else:
        path, tr = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_ORG_ADJ_PRC": "0"}
        keys = ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr")
    rows = _get(path, tr, {**params, "FID_INPUT_DATE_1": start.replace("-", ""), "FID_INPUT_DATE_2": end.replace("-", ""),
                           "FID_PERIOD_DIV_CODE": "D"}).get("output2") or []
    out = []
    for r in rows:
        d = r.get("stck_bsop_date") or ""
        o, h, lo, c = (_num(r.get(k)) for k in keys)
        if len(d) == 8 and c:
            out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "open": o, "high": h, "low": lo, "close": c,
                        "volume": _num(r.get("acml_vol")), "value": _num(r.get("acml_tr_pbmn"))})
    return sorted(out, key=lambda x: x["date"])


def probe(code: str, now: datetime) -> str:
    """시험: 한투에서 실제로 무엇을 얼마나 받을 수 있는지 (일봉·지난 1분봉·지수 1분봉의 범위). 기록은 바꾸지 않음."""
    parts, errs = [], []

    def step(label, fn):
        try:
            return fn()
        except KisError as e:
            errs.append(f"{label} {e}")
            return None

    span = lambda xs, k: f"{len(xs)}개 {xs[0][k]}~{xs[-1][k]}" if xs else "0개"
    q = step("현재가", lambda: quote(code))
    if q:
        parts.append(f"현재가 {q['price']} 기준가 {q['base']} 시가 {q['open']} 거래량 {q['volume']} 대금 {q['value']} 시총 {q['market_cap_100m']}억 PER {q['per']} PBR {q['pbr']}")
    end = now.date().isoformat()
    for label, s0 in (("일봉 최근", (now - timedelta(days=150)).date().isoformat()), ("일봉 3년 전", "2023-01-01"), ("일봉 2020", "2020-01-01")):
        d = step(label, lambda s0=s0: daily(code, s0, end if label == "일봉 최근" else (datetime.fromisoformat(s0) + timedelta(days=140)).date().isoformat()))
        if d is not None:
            parts.append(f"{label} {span(d, 'date')}")
    k = step("KOSDAQ 일봉", lambda: daily(KOSDAQ_INDEX, (now - timedelta(days=150)).date().isoformat(), end, index=True))
    if k is not None:
        parts.append(f"KOSDAQ 일봉 {span(k, 'date')}" + (f" 마지막 종가 {k[-1]['close']}" if k else ""))
    last = (k or [{}])[-1].get("date") if k else None
    m = step("오늘 1분봉", lambda: minutes(code, "153000"))
    if m is not None:
        parts.append(f"당일 1분봉 {span(m, 'ts')}")
    for back in (7, 30, 90, 200, 365):
        day = (now - timedelta(days=back))
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        dm = step(f"지난 1분봉({back}일 전)", lambda d=day.date().isoformat(): day_minutes(code, d))
        if dm is not None:
            parts.append(f"{back}일 전({day.date().isoformat()}) 1분봉 {span(dm, 'ts')}")
    im = step("KOSDAQ 1분봉", index_minutes)
    if im is not None:
        parts.append(f"KOSDAQ 1분봉 {span(im, 'ts')}" + (f" 마지막 {im[-1]['close']}" if im else ""))
    inv = step("투자자", lambda: investors(code))
    if inv is not None:
        parts.append(f"투자자 {len(inv)}일 {inv[-1]['date'] if inv else ''}~{inv[0]['date'] if inv else ''}")
    return "한투 시험: " + (" · ".join(parts) or "받은 값 없음") + (" · 실패: " + " / ".join(errs) if errs else "")


# ------------------------------------------------------------------ 수집용 (feed·macro에서 씀)
def today_bars(code: str, now: datetime, since: str | None = None) -> list[dict]:
    """오늘(장 밖이면 마지막 거래일) 1분봉. 지금부터 30개씩 거슬러 since(이미 받은 마지막 분)나 09:00까지."""
    hhmm = now.strftime("%H%M%S") if now.weekday() < 5 and "090000" <= now.strftime("%H%M%S") <= "153000" else "153000"
    out: dict[str, dict] = {}
    for _ in range(16):
        bars = minutes(code, hhmm)
        if not bars:
            break
        for b in bars:
            out[b["ts"]] = b
        first = bars[0]["ts"]
        if first[11:] <= "09:00" or (since and first <= since):
            break
        t = datetime.fromisoformat(first) - timedelta(minutes=1)
        hhmm = t.strftime("%H%M%S")
    return [out[k] for k in sorted(out)]


def index_bars(no: str = KOSDAQ_INDEX) -> list[dict]:
    """KOSDAQ 지수 당일 1분봉 (최근 약 100분, 정규장 시각만)."""
    return [b for b in index_minutes(no) if "09:00" <= b["ts"][11:] <= "15:30"]


def full_day_bars(code: str, day: str) -> list[dict]:
    """지난 날짜 하루 1분봉 전부 (120개씩 거슬러 09:00까지)."""
    out: dict[str, dict] = {}
    hhmm = "153000"
    for _ in range(6):
        bars = day_minutes(code, day, hhmm)
        bars = [b for b in bars if b["ts"][:10] == day]
        if not bars:
            break
        for b in bars:
            out[b["ts"]] = b
        first = bars[0]["ts"]
        if first[11:] <= "09:00":
            break
        hhmm = (datetime.fromisoformat(first) - timedelta(minutes=1)).strftime("%H%M%S")
    return [out[k] for k in sorted(out) if "09:00" <= k[11:] <= "15:30"]


def daily_range(code: str, start: str, end: str, index: bool = False) -> list[dict]:
    """일봉 start~end 전부 (한 번에 100개·지수 50개씩 거슬러)."""
    out: dict[str, dict] = {}
    cur = end
    for _ in range(40):
        rows = daily(code, start, cur, index=index)
        rows = [r for r in rows if start <= r["date"] <= cur and r["date"] not in out]
        if not rows:
            break
        for r in rows:
            out[r["date"]] = r
        cur = (datetime.fromisoformat(rows[0]["date"]) - timedelta(days=1)).date().isoformat()
        if cur < start:
            break
    return [out[k] for k in sorted(out)]
