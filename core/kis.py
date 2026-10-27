"""한국투자증권 오픈API로 지난 1분봉을 한 번 채움 (사이트가 1분봉을 모으기 전 날: 상장일 ~ 네이버 1분봉 첫날 전날).

- 열쇠(KIS_APP_KEY / KIS_APP_SECRET, GitHub Secrets)와 저장소 변수 KIS_BACKFILL=1 이 있을 때만 돌고, 다 채우면 멈춤.
- 한국투자증권은 지난 분봉을 최대 1년 보관 → 그보다 오래된 날은 못 받음.
- 주식일별분봉조회[국내주식-213]: 한 번에 최대 120개, 날짜·시각을 주면 그 시각부터 거꾸로.
- 이미 있는 1분봉(네이버)은 그대로 두고 빈 날만 넣음. 분봉 시각 표기(봉 시작/끝)는 네이버 1분봉이 있는 날과 맞춰 봐서 정함.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .analysis import KOSDAQ
from .db import connect, get_state, set_state
from .naver import Bar

BASE = "https://openapi.koreainvestment.com:9443"
STATE = "kis_backfill"
INDEX_CODE = "1001"          # 한국투자증권 업종 코드: 코스닥 종합


class KisError(Exception):
    pass


def has_keys() -> bool:
    return bool(os.environ.get("KIS_APP_KEY") and os.environ.get("KIS_APP_SECRET"))


def enabled() -> bool:
    """채우기는 열쇠만으로 돌지 않음: 저장소 변수 KIS_BACKFILL=1 로 따로 켜야 함 (열쇠는 먼저 '확인' 실행에만 쓰도록)."""
    return has_keys() and os.environ.get("KIS_BACKFILL") == "1"


class Client:
    def __init__(self, key: str, secret: str):
        self.key, self.secret = key, secret
        self.token = self._token()

    def _token(self) -> str:
        body = json.dumps({"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret}).encode()
        req = urllib.request.Request(BASE + "/oauth2/tokenP", data=body, headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise KisError(f"접근 토큰 발급 실패: HTTP {e.code} {e.read()[:200]!r}")
        if not d.get("access_token"):
            raise KisError(f"접근 토큰 발급 실패: {str(d)[:200]}")
        return d["access_token"]

    def get(self, path: str, tr_id: str, params: dict) -> dict:
        req = urllib.request.Request(f"{BASE}{path}?{urllib.parse.urlencode(params)}", headers={
            "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self.token}",
            "appkey": self.key, "appsecret": self.secret, "tr_id": tr_id, "custtype": "P"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    d = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (429, 500) and attempt < 2:      # 초당 호출 제한 → 잠깐 쉬고 다시
                    time.sleep(1.0 + attempt)
                    continue
                raise KisError(f"HTTP {e.code} {e.read()[:200]!r}")
            if d.get("rt_cd") not in (None, "0"):
                if "초당" in str(d.get("msg1")) and attempt < 2:
                    time.sleep(1.0 + attempt)
                    continue
                raise KisError(f"{d.get('msg_cd')} {d.get('msg1')}")
            return d
        raise KisError("호출 제한으로 받지 못함")


def _num(row: dict, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return float(v)
    return None


def parse_rows(rows: list[dict], ymd: str) -> dict[str, Bar]:
    """{HHMMSS: Bar} (그날 정규장 봉만). 종목은 stck_*, 업종 지수는 bstp_nmix_* 이름."""
    out = {}
    for r in rows or []:
        if r.get("stck_bsop_date") != ymd:
            continue
        hms = str(r.get("stck_cntg_hour") or "")
        c = _num(r, "stck_prpr", "bstp_nmix_prpr")
        if len(hms) != 6 or not c or not ("090000" <= hms <= "153000"):
            continue
        o, h, lo = (_num(r, "stck_oprc", "bstp_nmix_oprc") or c, _num(r, "stck_hgpr", "bstp_nmix_hgpr") or c,
                    _num(r, "stck_lwpr", "bstp_nmix_lwpr") or c)
        out[hms] = Bar(ts=f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}T{hms[:2]}:{hms[2:4]}", open=o, high=h, low=lo, close=c,
                       volume=int(_num(r, "cntg_vol") or 0))
    return out


def day_minutes(cl: Client, code: str, day: str, market: str = "J", pause: float = 0.08) -> list[Bar]:
    """그날 1분봉 전부: 15:30부터 거꾸로 120개씩."""
    ymd, hour, got = day.replace("-", ""), "153000", {}
    for _ in range(6):
        d = cl.get("/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice", "FHKST03010230", {
            "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code, "FID_INPUT_HOUR_1": hour,
            "FID_INPUT_DATE_1": ymd, "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": ""})
        rows = parse_rows(d.get("output2") or [], ymd)
        time.sleep(pause)
        new = {k: v for k, v in rows.items() if k not in got}
        if not new:
            break
        got.update(new)
        first = min(new)
        if first <= "090000":
            break
        hour = (datetime.strptime(first, "%H%M%S") - timedelta(minutes=1)).strftime("%H%M%S")
    return [got[k] for k in sorted(got)]


def shift(bars: list[Bar], minutes: int) -> list[Bar]:
    """봉 시각을 옮김 (한국투자증권이 봉 끝 시각으로 적으면 -1분 → 네이버처럼 봉 시작 시각). 09:00 전으로 가는 봉은 뺌."""
    if not minutes:
        return bars
    out = []
    for b in bars:
        t = (datetime.fromisoformat(b.ts) + timedelta(minutes=minutes)).isoformat(timespec="minutes")
        if t[11:16] >= "09:00":
            out.append(Bar(t, b.open, b.high, b.low, b.close, b.volume))
    return out


def best_offset(kis: list[Bar], have: dict[str, float]) -> int | None:
    """네이버 1분봉(have: {ts: 종가})과 가장 잘 맞는 시각 옮김(0 또는 -1분). 겹치는 봉이 적으면 None."""
    score = {}
    for m in (0, -1):
        pairs = [(have[b.ts], b.close) for b in shift(kis, m) if b.ts in have]
        score[m] = (sum(1 for a, c in pairs if abs(a - c) < 1e-6), len(pairs))
    best = max(score, key=lambda m: score[m][0])
    return best if score[best][1] >= 100 and score[best][0] >= 0.8 * score[best][1] else None


def _minute_days(conn, sym: str) -> dict[str, int]:
    return dict(conn.execute("SELECT substr(ts,1,10) d, COUNT(*) FROM minute_bars WHERE symbol=? GROUP BY d", (sym,)).fetchall())


def backfill(code: str, now: datetime, log=None, budget_sec: float = 360) -> dict:
    """빈 날을 오래된 날부터 채움 (한 번 실행에 budget_sec까지, 남으면 다음 실행에서 이어서). 결과 요약을 돌려줌."""
    st = json.loads((get_state(STATE) or {}).get("value") or "{}")
    if st.get("done") or not enabled():
        return {"skipped": True, **st}
    t0 = time.monotonic()
    cl = Client(os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"])
    since = (now - timedelta(days=364)).date().isoformat()          # 한국투자증권 보관 기간 (최대 1년)
    with connect() as conn:
        have_days = _minute_days(conn, code)
        kq_days = _minute_days(conn, KOSDAQ)
        days = [r[0] for r in conn.execute("SELECT date FROM daily_bars WHERE symbol=? AND date>=? AND date<? ORDER BY date",
                                           (code, since, now.date().isoformat()))]
        naver_day = conn.execute("SELECT substr(ts,1,10) d FROM minute_bars WHERE symbol=? AND source LIKE 'naver%' "
                                 "GROUP BY d HAVING COUNT(*) >= 300 ORDER BY d DESC LIMIT 1", (code,)).fetchone()
    # 1) 시각 표기 맞추기: 네이버 1분봉이 있는 날 하나를 받아 대조 (종목·지수 따로)
    if "offset" not in st:
        st["offset"] = 0
        if naver_day:
            with connect() as conn:
                have = dict(conn.execute("SELECT ts, close FROM minute_bars WHERE symbol=? AND substr(ts,1,10)=?",
                                         (code, naver_day[0])).fetchall())
            off = best_offset(day_minutes(cl, code, naver_day[0]), have)
            st["offset"] = off if off is not None else 0
            st["offset_checked"] = naver_day[0] if off is not None else None
    if "index" not in st:                 # 코스닥 지수도 같은 조회로 되는지 한 번 시험
        st["index"] = None
        test = next((d for d in reversed(days) if kq_days.get(d, 0) >= 300), None)
        if test:
            try:
                kb = day_minutes(cl, INDEX_CODE, test, market="U")
                with connect() as conn:
                    have = dict(conn.execute("SELECT ts, close FROM minute_bars WHERE symbol=? AND substr(ts,1,10)=?",
                                             (KOSDAQ, test)).fetchall())
                # 지난 날짜 지수는 봉이 1개만 오는 것을 확인함(2026-10 'KIS 확인') → 하루치(300개 이상)가 올 때만 씀
                kb = kb if len(kb) >= 300 else []
                if not kb:
                    st["index_error"] = "지난 날짜 코스닥 지수 분봉을 주지 않음"
                off = best_offset(kb, {k: round(v, 2) for k, v in have.items()}) if kb else None
                if off is None and kb:      # 지수는 소수점 반올림이 달라 종가 대조가 안 맞을 수 있음 → 종목과 같은 옮김
                    off = st["offset"]
                st["index"] = off
            except KisError as e:
                st["index_error"] = str(e)[:200]
    # 2) 빈 날 채우기 (오래된 날부터: 보관 기간이 먼저 끝나는 날)
    todo = [d for d in days if have_days.get(d, 0) < 300 and d > st.get("cursor", "")]
    filled = st.setdefault("filled", 0)
    for d in todo:
        if time.monotonic() - t0 > budget_sec:
            break
        try:
            bars = shift(day_minutes(cl, code, d), st["offset"])
            kbars = shift(day_minutes(cl, INDEX_CODE, d, market="U"), st["index"]) \
                if st.get("index") is not None and kq_days.get(d, 0) < 300 else []
        except KisError as e:
            st["last_error"] = f"{d}: {str(e)[:200]}"
            if log:
                log(f"한국투자증권 분봉 {d} 실패: {e}")
            break
        with connect() as conn:
            conn.executemany("INSERT OR IGNORE INTO minute_bars(symbol, ts, open, high, low, close, volume, source) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, 'kis')",
                             [(code, b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars]
                             + [(KOSDAQ, b.ts, b.open, b.high, b.low, b.close, b.volume) for b in kbars])
            if bars:          # 이 날의 사이트 장중 차트 자료를 다시 만들게
                try:
                    conn.execute("DELETE FROM site_items WHERE key=?", (f"intraday:{d}",))
                except Exception:  # noqa: BLE001 - 사이트 자료 표가 아직 없을 때
                    pass
        if bars:
            filled += 1
            st.setdefault("first", d)
            st["last"] = d
        else:
            st["empty"] = st.get("empty", 0) + 1
        st["cursor"], st["filled"] = d, filled
        set_state(STATE, json.dumps(st, ensure_ascii=False))
    else:
        st["done"] = True
    set_state(STATE, json.dumps(st, ensure_ascii=False))
    rescan_if_due(code, log)
    return json.loads(get_state(STATE)["value"])


def rescan_if_due(code: str, log=None) -> int | None:
    """다 채운 뒤 한 번: 채운 날들의 장중 급등·급락 찾기 (네이버 기록이 있는 날은 그대로). 열쇠 없이도 돎."""
    st = json.loads((get_state(STATE) or {}).get("value") or "{}")
    if not st.get("done") or "rescanned" in st:
        return None
    from .detector import rescan_before_naver
    st["rescanned"] = rescan_before_naver(code, log)
    set_state(STATE, json.dumps(st, ensure_ascii=False))
    return st["rescanned"]


TRIM_KEY = "kis_rescan_trim_v1"
TRIM_ON = False          # 급등·급락 기준(관찰을 남길지)을 정할 때까지 멈춤


def trim_backfilled_observations(code: str, log=None) -> dict | None:
    """한 번: 채운 지난 날(네이버 1분봉 첫날 전)의 장중 '관찰'(±3~5%) 기록을 지움. ±5% 이상(일반·대형)과 하루 기록은 그대로.
    지우기 전 단계별 건수를 돌려줌."""
    if get_state(TRIM_KEY):
        return None
    from .detector import first_naver_day
    first = first_naver_day(code)
    if not first:
        return None
    with connect() as conn:
        by = dict(conn.execute("SELECT tier, COUNT(*) FROM events WHERE symbol=? AND event_type='intraday' AND start_ts < ? "
                               "GROUP BY tier", (code, first)).fetchall())
        n = conn.execute("DELETE FROM events WHERE symbol=? AND event_type='intraday' AND tier='관찰' AND start_ts < ?",
                         (code, first)).rowcount
    out = {"before": first, "by_tier": by, "deleted": n}
    set_state(TRIM_KEY, json.dumps(out, ensure_ascii=False))
    if log:
        log(f"채운 지난 날({first} 전) 장중 기록: " + ", ".join(f"{k} {v}건" for k, v in by.items()) + f" → 관찰 {n}건 지움")
    return out
