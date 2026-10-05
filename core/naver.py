"""네이버 증권 시세 수집.

공식 API가 아니라 네이버 증권 화면이 쓰는 데이터 주소를 그대로 읽습니다.
네이버가 주소/형식을 바꾸면 동작이 멈출 수 있으므로, 실패는 모두 예외로
올려서 감시 엔진이 '데이터 수신 오류'로 표시하게 합니다.

사용하는 주소 (2026-10-02 확인)
- 당일 1분봉 (시가/고가/저가/종가/분당 거래량)
    https://api.stock.naver.com/chart/domestic/item/{code}/minute
    https://api.stock.naver.com/chart/domestic/index/KOSDAQ/minute
  * 필드명이 accumulatedTradingVolume 이지만 실제 값은 '그 1분 동안의 거래량'
  * 체결이 없는 분은 빠져 있음
- 과거 1분봉 (최근 약 7거래일, 종가/누적거래량만 제공, EUC-KR 텍스트)
    https://fchart.stock.naver.com/siseJson.nhn?symbol={code}&requestType=1
        &startTime=YYYYMMDD&endTime=YYYYMMDD&timeframe=minute
  * 15:30 이후 NXT 시간외 체결도 섞여 있어 정규장(09:00~15:30)만 사용
  * 지수(KOSDAQ)는 제공되지 않음
- 현재가
    https://polling.finance.naver.com/api/realtime/domestic/stock/{code}
    https://polling.finance.naver.com/api/realtime/domestic/index/KOSDAQ
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)

REGULAR_START = "0900"
REGULAR_END = "1530"


class DataSourceError(Exception):
    """네이버에서 데이터를 받지 못했거나 형식이 예상과 다를 때."""


@dataclass
class Bar:
    ts: str          # 'YYYY-MM-DDTHH:MM'
    open: float
    high: float
    low: float
    close: float
    volume: int


def http_get(url: str, timeout: float = 10.0, retries: int = 2) -> bytes:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://stock.naver.com/" if "://stock.naver.com" in url else "https://m.stock.naver.com/",
                "Accept": "application/json, text/plain, */*",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise DataSourceError(f"네이버 접속 실패: {type(last).__name__}: {last}  ({url})")


def _ts(yyyymmddhhmm: str) -> str:
    s = yyyymmddhhmm
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:{s[10:12]}"


def _is_regular(hhmm: str) -> bool:
    return REGULAR_START <= hhmm <= REGULAR_END


# ------------------------------------------------------------------ 당일 분봉

def parse_today_minutes(raw: bytes) -> list[Bar]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise DataSourceError(f"당일 분봉 형식 오류: {e}")
    if not isinstance(data, list):
        raise DataSourceError(f"당일 분봉 형식 오류: 목록이 아님 ({str(data)[:200]})")
    bars = []
    for row in data:
        try:
            dt = str(row["localDateTime"])          # YYYYMMDDHHMMSS
            hhmm = dt[8:12]
            if not _is_regular(hhmm):
                continue
            bars.append(Bar(
                ts=_ts(dt[:12]),
                open=float(row["openPrice"]),
                high=float(row["highPrice"]),
                low=float(row["lowPrice"]),
                close=float(row["currentPrice"]),
                volume=int(float(row.get("accumulatedTradingVolume") or 0)),
            ))
        except (KeyError, TypeError, ValueError) as e:
            raise DataSourceError(f"당일 분봉 항목 오류: {e} / {str(row)[:200]}")
    return bars


def fetch_today_minutes(code: str, is_index: bool = False) -> list[Bar]:
    kind = "index" if is_index else "item"
    url = f"https://api.stock.naver.com/chart/domestic/{kind}/{code}/minute"
    return parse_today_minutes(http_get(url))


# ------------------------------------------------------------------ 과거 분봉

_ROW_RE = re.compile(
    r'\[\s*"(\d{12})"\s*,\s*([^,\]]+)\s*,\s*([^,\]]+)\s*,\s*([^,\]]+)\s*,'
    r'\s*([^,\]]+)\s*,\s*([^,\]]+)'
)


def parse_history_minutes(raw: bytes) -> list[Bar]:
    text = raw.decode("euc-kr", errors="replace")
    if "[" not in text:
        raise DataSourceError(f"과거 분봉 형식 오류: {text[:200]}")
    rows = sorted(_ROW_RE.findall(text), key=lambda r: r[0])
    bars: list[Bar] = []
    prev_day, prev_cum = None, 0
    for dt, _o, _h, _l, close, cum in rows:
        day, hhmm = dt[:8], dt[8:12]
        try:
            close_f = float(close)
            cum_i = int(float(cum))
        except ValueError:
            continue
        if day != prev_day:
            prev_day, prev_cum = day, 0
        vol = max(0, cum_i - prev_cum)   # 누적거래량 → 분당 거래량
        prev_cum = cum_i
        if not _is_regular(hhmm):
            continue
        bars.append(Bar(ts=_ts(dt), open=close_f, high=close_f, low=close_f,
                        close=close_f, volume=vol))
    return bars


def fetch_history_minutes(code: str, start: date, end: date) -> list[Bar]:
    url = (
        "https://fchart.stock.naver.com/siseJson.nhn"
        f"?symbol={code}&requestType=1&startTime={start:%Y%m%d}"
        f"&endTime={end:%Y%m%d}&timeframe=minute"
    )
    return parse_history_minutes(http_get(url, timeout=20))


# ------------------------------------------------------------------ 현재가

def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def parse_quote(raw: bytes) -> dict:
    try:
        d = json.loads(raw.decode("utf-8"))["datas"][0]
    except Exception as e:
        raise DataSourceError(f"현재가 형식 오류: {e}")
    return {
        "name": d.get("stockName"),
        "price": _num(d.get("closePriceRaw") or d.get("closePrice")),
        "change": _num(d.get("compareToPreviousClosePriceRaw") or d.get("compareToPreviousClosePrice")),
        "change_pct": _num(d.get("fluctuationsRatioRaw") or d.get("fluctuationsRatio")),
        "open": _num(d.get("openPriceRaw") or d.get("openPrice")),
        "high": _num(d.get("highPriceRaw") or d.get("highPrice")),
        "low": _num(d.get("lowPriceRaw") or d.get("lowPrice")),
        "volume": _num(d.get("accumulatedTradingVolumeRaw")),
        "market_status": d.get("marketStatus"),          # OPEN / CLOSE ...
        "traded_at": d.get("localTradedAt"),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def fetch_quote(code: str, is_index: bool = False) -> dict:
    kind = "index" if is_index else "stock"
    url = f"https://polling.finance.naver.com/api/realtime/domestic/{kind}/{code}"
    return parse_quote(http_get(url))


# ------------------------------------------------------------------ 원인 분석용
# (2026-10-02 확인) 모두 공개 화면이 쓰는 주소이며 로그인 정보는 사용하지 않음
#   거래원      https://stock.naver.com/api/domestic/detail/{code}/traderInfo
#               당일 누적, 상위 거래원(증권사 창구)별 매수/매도 수량. 최상위 buyQuant/sellQuant는 외국계 추정 합계
#   투자자별    https://stock.naver.com/api/domestic/detail/{code}/trend?tradeType=KRX&startIdx=0&pageSize=N
#               일별 개인/외국인/기관 순매수 (장 마감 후 확정분)
#   뉴스        https://m.stock.naver.com/api/news/stock/{code}?pageSize=N&page=1   (datetime = 한국 시각)
#   공시        https://stock.naver.com/api/domestic/detail/notice?itemCode={code}&startIdx=0&pageSize=N
#   같은 업종   https://m.stock.naver.com/api/stock/{code}/integration  → industryCompareInfo

def _json(url: str, _referer: str = ""):
    req_bytes = http_get(url)
    try:
        return json.loads(req_bytes.decode("utf-8"))
    except Exception as e:
        raise DataSourceError(f"형식 오류: {e} ({url})")


def _int(v) -> int:
    try:
        return int(float(str(v).replace(",", "").replace("+", "")))
    except (TypeError, ValueError):
        return 0


FOREIGN_HINTS = ("서울지점", "모간", "모건", "골드만", "메릴", "씨티", "맥쿼리", "노무라", "CLSA", "UBS",
                 "도이치", "HSBC", "크레디", "비엔피", "BNP", "다이와", "SG증권", "제이피")


def is_foreign_broker(name_kr: str, display: str = "") -> bool:
    s = f"{name_kr} {display}"
    return any(h in s for h in FOREIGN_HINTS)


def mark_top5(rows: list[dict]) -> list[dict]:
    """거래원 자료는 '매수·매도 상위 5개사' 기준이라, 지금 5위 안에 있는지 표시.

    5위 밖(이탈) 창구의 수량은 밀려난 시점에서 멈춰 있고,
    한쪽이 0으로 나오면 '0주'가 아니라 '상위 5위 밖이라 집계되지 않음'이다.
    """
    by_buy = sorted([r for r in rows if r["buy"] > 0], key=lambda r: -r["buy"])
    by_sell = sorted([r for r in rows if r["sell"] > 0], key=lambda r: -r["sell"])
    top_b = {id(r) for r in by_buy[:5]}
    top_s = {id(r) for r in by_sell[:5]}
    for r in rows:
        r["buy_top5"] = id(r) in top_b
        r["sell_top5"] = id(r) in top_s
    return rows


def fetch_trader_info(code: str) -> dict:
    return parse_trader_info(_json(f"https://stock.naver.com/api/domestic/detail/{code}/traderInfo"))


def parse_trader_info(d: dict) -> dict:
    rows = []
    for t in d.get("traderList") or []:
        rows.append({
            "trader_no": str(t.get("traderNo")),
            "name": t.get("display_name") or t.get("nameKr") or "",
            "name_full": t.get("nameKr") or "",
            "buy": _int(t.get("buyQuant")),
            "sell": _int(t.get("sellQuant")),
            "foreign": is_foreign_broker(t.get("nameKr") or "", t.get("display_name") or ""),
            "bizdate": t.get("bizdate"),
        })
    mark_top5(rows)
    return {"source": "네이버", "foreign_buy": _int(d.get("buyQuant")), "foreign_sell": _int(d.get("sellQuant")),
            "traders": rows}


def fetch_research(code: str) -> list[dict]:
    """증권사 리포트 목록 (네이버 종목 통합 정보에 포함)."""
    d = _json(f"https://m.stock.naver.com/api/stock/{code}/integration", "https://m.stock.naver.com/")
    out = []
    for r in d.get("researches") or []:
        w = str(r.get("wdt") or "")
        out.append({"id": str(r.get("id")), "broker": r.get("bnm") or "", "title": r.get("tit") or "",
                    "date": f"{w[0:4]}-{w[4:6]}-{w[6:8]}" if len(w) >= 8 else w,
                    "url": f"https://m.stock.naver.com/research/company/{r.get('id')}"})
    return out


def fetch_investor_trend(code: str, n: int = 20) -> list[dict]:
    d = _json(f"https://stock.naver.com/api/domestic/detail/{code}/trend?tradeType=KRX&startIdx=0&pageSize={n}")
    out = []
    for r in d or []:
        out.append({
            "bizdate": r.get("bizdate"),
            "foreign": _int(r.get("foreignerPureBuyQuant")),
            "organ": _int(r.get("organPureBuyQuant")),
            "individual": _int(r.get("individualPureBuyQuant")),
            "close": _int(r.get("closePrice")),
            "volume": _int(r.get("tradeVolume")),
            "foreign_ratio": float(r.get("frgnHoldRatio") or 0),
        })
    return out


def fetch_news(code: str, n: int = 20) -> list[dict]:
    d = _json(f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={n}&page=1", "https://m.stock.naver.com/")
    out = []
    for group in d or []:
        for it in group.get("items") or []:
            dt = str(it.get("datetime") or "")
            if len(dt) < 12:
                continue
            out.append({
                "id": str(it.get("id") or f"{it.get('officeId')}{it.get('articleId')}"),
                "ts": _ts(dt[:12]),
                "office": it.get("officeName") or "",
                "title": html.unescape(it.get("titleFull") or it.get("title") or "").strip(),
                "body": html.unescape(it.get("body") or "").strip(),
                "url": it.get("mobileNewsUrl") or "",
                "cluster": int(group.get("total") or 1),
            })
    return out


def fetch_disclosures(code: str, n: int = 20) -> list[dict]:
    d = _json(f"https://stock.naver.com/api/domestic/detail/notice?itemCode={code}&startIdx=0&pageSize={n}")
    out = []
    for r in d or []:
        seq = str(r.get("bizdateSeq") or "")
        date = f"{seq[0:4]}-{seq[4:6]}-{seq[6:8]}" if len(seq) >= 8 else str(r.get("datetime") or "")[:10]
        text = re.sub(r"\.[A-Za-z\-_ ]+\{[^}]*\}", " ", str(r.get("contents") or ""))
        text = re.sub(r"\s+", " ", text).strip()
        out.append({
            "no": str(r.get("no")),
            "date": date,
            "title": (r.get("title") or "").strip(),
            "kind": r.get("comment") or "",
            "summary": text[:400],
        })
    return out


def fetch_related(code: str) -> list[dict]:
    d = _json(f"https://m.stock.naver.com/api/stock/{code}/integration", "https://m.stock.naver.com/")
    out = []
    for r in d.get("industryCompareInfo") or []:
        if r.get("itemCode") == code:
            continue
        out.append({"code": r.get("itemCode"), "name": r.get("stockName"),
                    "market": (r.get("stockExchangeType") or {}).get("code", "")})
    return out
