"""원인 판단에 쓰는 계산 (실무 3단계).

① 몫 나누기 (팩터 모형: 월스트리트 리스크 모델 방식, MSCI Barra·Axioma)
   실제 등락 = 시장(KOSDAQ) 몫 + 업종 몫(방산 ETF·반도체 ETF) + 이 종목 고유 몫.
   기울기(민감도)는 이벤트 10거래일 전까지 최대 250거래일 기록으로 추정.
   고유 몫이 평소 고유 변동의 몇 배인지(z)로 '회사만의 이유가 있는 움직임인지' 판단.
   업종 ETF 기록이 모자라면 시장만 감안한 사건연구(MacKinlay 1997)로 대신 계산.
   장중 30분 움직임은 같은 길이의 평소 움직임 분포와 비교(window_rarity).

② 기사 직후 검증 (고빈도 시간창, narrow-window identification)
   기사·PR이 나온 직후 30분(장 마감 뒤 발표면 다음 날 시초가) 움직임을
   평소 같은 길이 구간 움직임 분포와 비교. 상위 5% 안이면 '기사 효과 있음'.

③ 거래 강도 (트레이딩 데스크 기본 지표)
   거래량·거래대금이 20거래일 평균(ADV)의 몇 배였는지, 같은 돈으로 평소보다 얼마나 크게 움직였는지(Amihud).

모두 '확률적 근거'이며, 주문을 낸 사람의 실제 이유를 증명하지는 않습니다.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np

from .db import connect

KOSDAQ = "KOSDAQ"


# ------------------------------------------------------------------ 공통

def _p_norm(t: float) -> float:
    """양측 p값 (정규분포 근사)."""
    return math.erfc(abs(t) / math.sqrt(2))


def _daily(code: str, lo: str, hi: str) -> list[tuple]:
    with connect() as conn:
        return conn.execute("SELECT date, open, close, volume FROM daily_bars WHERE symbol=? AND date BETWEEN ? AND ? "
                            "ORDER BY date", (code, lo, hi)).fetchall()


def _rets(code: str, lo: str, hi: str) -> dict:
    rows = _daily(code, lo, hi)
    return {rows[i][0]: rows[i][2] / rows[i - 1][2] - 1 for i in range(1, len(rows)) if rows[i - 1][2]}


def _p_txt(p):
    return "-" if p is None else ("0.001 미만" if p < 0.001 else f"{p:.3f}")


# ------------------------------------------------------------------ ① 대체: 사건연구 (업종 ETF 기록 부족 시)

NEWS_DISC_TYPES = ("A", "B", "C", "I", "K")     # 정기(실적)·주요사항·발행·거래소 공시·거래소 조치 (지분 보고 등은 뺌)
NOISE_CATEGORIES = ("단순 언급", "시황·특징주")
_NEWS_CACHE: dict = {}


def _first_session(calendar: list[str], ts: str) -> int:
    """공개 시각이 처음 반영되는 거래일 위치 (장 마감 15:30 뒤면 다음 거래일)."""
    import bisect
    d = ts[:10]
    i = bisect.bisect_left(calendar, d)
    if i < len(calendar) and calendar[i] == d and ts[11:16] > "15:30":
        i += 1
    return i


def news_days(symbol: str) -> dict[str, set]:
    """소식이 있던 거래일 {pr: 회사 보도자료·회사 관련 보도, disc: 주요 공시, ext: 외부 기사}.
    각 소식의 반응일과 다음 거래일 (발표 다음 날까지 반응이 이어질 수 있어서)."""
    with connect() as conn:
        sig = tuple(tuple(r) for r in conn.execute(
            "SELECT (SELECT COUNT(*)||MAX(ts) FROM articles WHERE symbol=?), "
            "(SELECT COUNT(*)||MAX(ts) FROM disclosures WHERE symbol=?), "
            "(SELECT COUNT(*)||MAX(published_ts) FROM pr_events), "
            "(SELECT MAX(date) FROM daily_bars WHERE symbol=?)", (symbol, symbol, KOSDAQ)))
        if _NEWS_CACHE.get("key") == (symbol, sig):
            return _NEWS_CACHE["value"]
        calendar = [r[0] for r in conn.execute("SELECT date FROM daily_bars WHERE symbol=? ORDER BY date", (KOSDAQ,))]
        arts = conn.execute("SELECT ts, category, kind_override FROM articles WHERE symbol=? AND hidden=0",
                            (symbol,)).fetchall()
        prs = [r[0] for r in conn.execute("SELECT published_ts FROM pr_events")]
        discs = [r[0] or r[1] + "T15:30" for r in conn.execute(
            "SELECT ts, date FROM disclosures WHERE symbol=? AND (pblntf IS NULL OR pblntf IN (%s))"
            % ",".join("?" * len(NEWS_DISC_TYPES)), (symbol, *NEWS_DISC_TYPES))]
    out = {"pr": set(), "disc": set(), "ext": set()}

    def add(kind, ts):
        i = _first_session(calendar, ts)
        out[kind].update(calendar[i:i + 2])
    for ts, category, override in arts:
        if override == "pr" or (category == "회사 발표" and override != "ext"):
            add("pr", ts)
        elif category not in NOISE_CATEGORIES:
            add("ext", ts)
    for ts in prs:
        add("pr", ts)
    for ts in discs:
        add("disc", ts)
    _NEWS_CACHE.update(key=(symbol, sig), value=out)
    return out


def estimation_exclusions(symbol: str) -> set:
    """평소 관계를 추정할 때 뺄 날: 회사 보도자료·회사 관련 보도·주요 공시가 있던 날 (외부 기사는 거의 매일이라 둠)."""
    try:
        n = news_days(symbol)
    except Exception:
        return set()
    return n["pr"] | n["disc"]


def news_baseline(symbol: str, years: float = 2.0) -> dict | None:
    """소식 종류별 날의 예상 대비 차이를 '소식 없는 날'과 나란히 비교."""
    from .eventstudy import baseline_by_group, returns_from_prices
    hi = date.today().isoformat()
    lo = (date.today() - timedelta(days=int(365 * years) + 10)).isoformat()
    market_prices = {row[0]: row[2] for row in _daily(KOSDAQ, lo, hi)}
    calendar = sorted(market_prices)
    own = returns_from_prices({row[0]: row[2] for row in _daily(symbol, lo, hi)}, calendar)
    mkt = returns_from_prices(market_prices, calendar)
    with connect() as conn:
        peer_codes = [row[0] for row in conn.execute("SELECT code FROM related_stocks WHERE code NOT IN (?, ?)",
                                                  (symbol, KOSDAQ))]
    peer_returns = [returns_from_prices({row[0]: row[2] for row in _daily(code, lo, hi)}, calendar)
                    for code in peer_codes]
    sector = {day: sum(s[day] for s in peer_returns) / len(peer_returns)
              for day in mkt if peer_returns and all(day in s for s in peer_returns)}
    n = news_days(symbol)
    days = sorted(own)
    pr = n["pr"]
    disc = n["disc"] - pr
    ext = n["ext"] - pr - disc
    quiet = set(days) - pr - disc - ext
    return baseline_by_group(own, mkt, days, {"pr": pr, "disc": disc, "ext": ext, "none": quiet}, "none", sector)


def event_study(symbol: str, d0: str, est_len: int = 120, gap: int = 10) -> dict | None:
    from .eventstudy import analyze_returns, returns_from_prices

    lo = (date.fromisoformat(d0) - timedelta(days=520)).isoformat()
    hi = (date.fromisoformat(d0) + timedelta(days=14)).isoformat()
    market_prices = {row[0]: row[2] for row in _daily(KOSDAQ, lo, hi)}
    calendar = sorted(market_prices)
    own = returns_from_prices({row[0]: row[2] for row in _daily(symbol, lo, hi)}, calendar)
    mkt = returns_from_prices(market_prices, calendar)
    with connect() as conn:
        peer_codes = [row[0] for row in conn.execute("SELECT code FROM related_stocks WHERE code NOT IN (?, ?)",
                                                  (symbol, KOSDAQ))]
    peer_returns = [returns_from_prices({row[0]: row[2] for row in _daily(code, lo, hi)}, calendar)
                    for code in peer_codes]
    sector = {day: sum(series[day] for series in peer_returns) / len(peer_returns)
              for day in mkt if peer_returns and all(day in series for series in peer_returns)}
    out = analyze_returns(own, mkt, d0, sector, est_len, gap, calendar[1:], exclude=estimation_exclusions(symbol))
    if not out or not out.get("ok"):
        return out
    vols = {r[0]: r[3] for r in _daily(symbol, lo, hi)}
    base = [vols[d] for d in sorted(vols) if out["est_from"] <= d <= out["est_to"] and vols.get(d)]
    if base and vols.get(d0):
        out["vol_ratio"] = vols[d0] / float(np.median(base))
    return out


# ------------------------------------------------------------------ ② 기사 직후 검증 (+ 장중 ① 이례성)

def _minutes(code: str, lo: str, hi: str):
    with connect() as conn:
        rows = conn.execute("SELECT ts, close FROM minute_bars WHERE symbol=? AND ts BETWEEN ? AND ? ORDER BY ts",
                            (code, lo, hi)).fetchall()
    return [(datetime.fromisoformat(t), c) for t, c in rows]


def _price_at(rows, t: datetime):
    last = None
    for ts, c in rows:
        if ts > t:
            break
        last = c
    return last


def _null_windows(rows, skip_day, minutes: int) -> list[float]:
    """이벤트 날을 뺀 과거 장중의 '같은 길이 구간' 움직임들 (겹치지 않게)."""
    null, by_day = [], {}
    for ts_, c in rows:
        if ts_.date() != skip_day:
            by_day.setdefault(ts_.date(), []).append((ts_, c))
    for d, rs in by_day.items():
        st = datetime.combine(d, datetime.min.time()) + timedelta(hours=9)
        k = st
        while k + timedelta(minutes=minutes) <= st + timedelta(hours=6, minutes=20):
            x, y = _price_at(rs, k), _price_at(rs, k + timedelta(minutes=minutes))
            if x and y:
                null.append(y / x - 1)
            k += timedelta(minutes=minutes)
    return null


def window_rarity(symbol: str, t_end: datetime, minutes: int, move: float) -> dict | None:
    """장중 이벤트의 고유 몫이 평소 같은 길이 구간 움직임에 비해 얼마나 드문지 (① 이례성)."""
    if move is None:
        return None
    day = t_end.date()
    rows = _minutes(symbol, (t_end - timedelta(days=45)).isoformat(timespec="minutes"), day.isoformat() + "T00:00")
    null = _null_windows(rows, day, minutes)
    if len(null) < 40:
        return {"ok": False, "reason": "비교할 평소 분봉 기록이 부족합니다."}
    sd = float(np.std(null)) or None
    p = (1 + sum(1 for v in null if abs(v) >= abs(move))) / (1 + len(null))
    return {"ok": True, "p": p, "n": len(null), "sd": sd, "z": (move / sd) if sd else 0.0}


def _prev_closes(code: str, lo: str, hi: str) -> dict:
    """{날짜: 그 전 거래일 종가}"""
    rows = _daily(code, lo, hi)
    return {rows[i][0]: rows[i - 1][2] for i in range(1, len(rows)) if rows[i - 1][2]}


def _by_day(rows) -> dict:
    out = {}
    for ts_, c in rows:
        out.setdefault(ts_.date(), []).append((ts_, c))
    return out


def abnormal_rarity(symbol: str, day: date, move: float, beta_m: float, minutes: int | None = 60,
                    until=None, lookback_days: int = 45) -> dict | None:
    """기사 반응(시장 몫을 뺀 고유 움직임)이 평소 같은 구간의 '시장 몫을 뺀' 움직임에 비해 얼마나 드문지.

    minutes가 있으면 장중 겹치지 않는 minutes분 구간들, minutes=None이면 날마다 '전일 종가 → until 시각'
    (장 시작 전 기사용). 비교 대상도 같은 계수(β시장)로 KOSDAQ 몫을 빼서 기준을 맞춤.
    """
    lo = (day - timedelta(days=lookback_days)).isoformat()
    hi = day.isoformat() + "T00:00"
    own, kq = _by_day(_minutes(symbol, lo, hi)), _by_day(_minutes(KOSDAQ, lo, hi))
    null = []
    if minutes is None:
        pc_o, pc_k = _prev_closes(symbol, lo, day.isoformat()), _prev_closes(KOSDAQ, lo, day.isoformat())
        for d, rs in own.items():
            ds = d.isoformat()
            t = datetime.combine(d, until)
            a, b = _price_at(rs, t), _price_at(kq.get(d, []), t)
            if a and b and pc_o.get(ds) and pc_k.get(ds):
                null.append((a / pc_o[ds] - 1) - beta_m * (b / pc_k[ds] - 1))
    else:
        for d, rs in own.items():
            ks = kq.get(d, [])
            st = datetime.combine(d, datetime.min.time()) + timedelta(hours=9)
            k = st
            while k + timedelta(minutes=minutes) <= st + timedelta(hours=6, minutes=20):
                x, y = _price_at(rs, k), _price_at(rs, k + timedelta(minutes=minutes))
                u, v = _price_at(ks, k), _price_at(ks, k + timedelta(minutes=minutes))
                if x and y and u and v:
                    null.append((y / x - 1) - beta_m * (v / u - 1))
                k += timedelta(minutes=minutes)
    if len(null) < 20:
        return {"ok": False, "reason": "비교할 평소 분봉 기록이 부족합니다."}
    p = (1 + sum(1 for x in null if abs(x) >= abs(move))) / (1 + len(null))
    return {"ok": True, "p": p, "n": len(null)}


def window_return(code: str, day: date, until, start: datetime | None = None) -> float | None:
    """그날 until 시각 가격의 등락: start가 없으면 전일 종가 대비, 있으면 start 시각 가격 대비."""
    rows = _minutes(code, day.isoformat() + "T00:00", day.isoformat() + "T23:59")
    b = _price_at(rows, datetime.combine(day, until) if not isinstance(until, datetime) else until)
    if start is None:
        a = _prev_closes(code, (day - timedelta(days=10)).isoformat(), day.isoformat()).get(day.isoformat())
    else:
        a = _price_at(rows, start)
    return b / a - 1 if a and b else None


def narrow_window(symbol: str, ts: str, minutes: int = 30) -> dict | None:
    """발표 직후 움직임이 평소 같은 길이 구간보다 얼마나 드문지 (경험적 p값)."""
    t = datetime.fromisoformat(ts[:16])
    day = t.date()
    open_t = datetime.combine(day, datetime.min.time()) + timedelta(hours=9)
    close_t = open_t + timedelta(hours=6, minutes=20)
    session_end = open_t + timedelta(hours=6, minutes=30)
    if t.weekday() < 5 and open_t <= t <= close_t:
        minutes = max(5, min(minutes, int((session_end - t).total_seconds() // 60)))
        rows = _minutes(symbol, (t - timedelta(days=45)).isoformat(timespec="minutes"),
                        (t + timedelta(minutes=minutes + 1)).isoformat(timespec="minutes"))
        a, b = _price_at(rows, t), _price_at(rows, t + timedelta(minutes=minutes))
        if not a or not b:
            return None
        r = b / a - 1
        null = _null_windows(rows, day, minutes)
        if len(null) < 40:
            return {"ok": False, "reason": "비교할 평소 분봉 기록이 부족합니다."}
        p = (1 + sum(1 for v in null if abs(v) >= abs(r))) / (1 + len(null))
        return {"ok": True, "window": f"발표 후 {minutes}분", "ret": r, "p": p, "n_null": len(null)}
    # 장 밖 발표 → 다음 거래일 시초가 (전일 종가 대비)
    lo = (day - timedelta(days=260)).isoformat()
    rows = _daily(symbol, lo, (day + timedelta(days=7)).isoformat())
    nxt = next((i for i, r in enumerate(rows) if (r[0] > day.isoformat() if t >= close_t else r[0] >= day.isoformat())), None)
    if nxt is None or nxt == 0:
        return None
    gaps = [rows[i][1] / rows[i - 1][2] - 1 for i in range(1, nxt) if rows[i - 1][2] and rows[i][1]][-120:]
    g0 = rows[nxt][1] / rows[nxt - 1][2] - 1 if rows[nxt - 1][2] and rows[nxt][1] else None
    if g0 is None or len(gaps) < 40:
        return {"ok": False, "reason": "비교할 시초가 기록이 부족합니다."}
    p = (1 + sum(1 for v in gaps if abs(v) >= abs(g0))) / (1 + len(gaps))
    return {"ok": True, "window": f"다음 거래일({rows[nxt][0][5:]}) 시초가", "ret": g0, "p": p, "n_null": len(gaps)}


# ------------------------------------------------------------------ ① 팩터 수익 분해 (Barra 방식)

def factor_attribution(symbol: str, d0: str, etfs: list[dict], est_len: int = 250, gap: int = 10) -> dict | None:
    lo = (date.fromisoformat(d0) - timedelta(days=420)).isoformat()
    own, mkt = _rets(symbol, lo, d0), _rets(KOSDAQ, lo, d0)
    fac = [(f["name"], _rets(f["code"], lo, d0)) for f in etfs]
    fac = [(n, r) for n, r in fac if d0 in r]
    dates = sorted(d for d in own if d in mkt and all(d in r for _, r in fac))
    if d0 not in dates:
        return None
    i0 = dates.index(d0)
    est = dates[max(0, i0 - gap - est_len):max(0, i0 - gap)]
    if len(est) < 60:
        return {"ok": False, "reason": f"업종 ETF와 함께 계산할 기록이 부족합니다 ({len(est)}거래일)."}
    m = np.array([mkt[d] for d in est])
    Xm = np.column_stack([np.ones_like(m), m])
    # 업종 ETF는 시장과 겹치는 부분을 빼서 '업종만의 움직임'으로 (Barra 업종 팩터와 같은 생각)
    resid_f, coefs = [], []
    for n, r in fac:
        y = np.array([r[d] for d in est])
        b = np.linalg.lstsq(Xm, y, rcond=None)[0]
        coefs.append(b)
        resid_f.append(y - Xm @ b)
    X = np.column_stack([np.ones_like(m), m] + resid_f)
    yo = np.array([own[d] for d in est])
    beta = np.linalg.lstsq(X, yo, rcond=None)[0]
    res = yo - X @ beta
    sig = float(np.sqrt((res ** 2).sum() / (len(est) - X.shape[1])))
    r2 = float(1 - (res ** 2).sum() / ((yo - yo.mean()) ** 2).sum())
    m0 = mkt[d0]
    parts = [{"name": "시장(KOSDAQ)", "factor": m0, "beta": float(beta[1]), "contrib": float(beta[1] * m0)}]
    for k, (n, r) in enumerate(fac):
        f0 = r[d0] - (coefs[k][0] + coefs[k][1] * m0)
        parts.append({"name": n, "factor": r[d0], "factor_resid": float(f0), "beta": float(beta[2 + k]),
                      "contrib": float(beta[2 + k] * f0)})
    spec = own[d0] - float(beta[0]) - sum(p["contrib"] for p in parts)
    return {"ok": True, "n_est": len(est), "actual": own[d0], "alpha": float(beta[0]), "parts": parts,
            "specific": spec, "sigma": sig, "z": spec / sig if sig else None, "p": _p_norm(spec / sig) if sig else None,
            "r2": r2}


# ------------------------------------------------------------------ ① 하루 몫 나누기 (이벤트 상세·기사 분석 공통)

def _segment_rets(lo: str, hi: str) -> dict[str, dict]:
    """사업 분야별 비교 종목 평균 등락 {분야: {날짜: 평균}} (그날 시세가 있는 종목만으로 평균)."""
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT code, source FROM related_stocks")]
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["source"] or "비교 종목", []).append(_rets(r["code"], lo, hi))
    out = {}
    for name, series in groups.items():
        days = sorted(set().union(*[s.keys() for s in series])) if series else []
        avg = {}
        for d in days:
            xs = [s[d] for s in series if d in s]
            if xs:
                avg[d] = sum(xs) / len(xs)
        if avg:
            out[name] = avg
    return out


WINSOR_K = 4.0      # 민감도 추정 때 값을 누르는 범위 (중앙값 ± 4×MAD)
RIDGE_OBS = 30.0     # 분야 계수 축소 강도 (기록 60일이면 약 2/3, 120일이면 약 4/5로 줄어듦)


def day_split(symbol: str, d0: str, etfs: list[dict] | None = None, est_len: int = 120, gap: int = 5) -> dict | None:
    """반응일 하루(전일 종가 → 종가) 등락을 시장 몫 + 분야별 몫 + 고유 몫으로 나눔.

    - 분야별 몫: 사업 분야(비교 종목 묶음)마다 평균 등락을 따로 넣고(업종 ETF가 설정돼 있으면 함께),
      각 분야에서 시장과 겹치는 부분을 뺀 뒤 회귀 → 이 종목과 실제로 같이 움직이는 분야만 크게 반영됨
    - 평소 추세(α)는 빼지 않음: 몇 달 오른(내린) 추세가 하루 반응을 깎지 않게
    - 분야 계수는 축소 추정(릿지, RIDGE_OBS): 기록이 짧거나 같이 움직이는 정도가 약한 분야는 작게 반영
    - 평소 고유 변동폭(σ)은 중앙값 기반(1.4826 × MAD): 큰 사건 며칠에 부풀지 않게
    - 얼마나 드문지는 정규분포 가정 대신 실제 기록과 비교(rarity): 추정 기간의 고유 움직임 중
      이만큼 이상 컸던 비율 (꼬리가 두꺼운 종목이라 z=2를 '100번 중 5번'으로 읽으면 틀림)
    - 민감도는 반응일 gap거래일 전까지 est_len거래일로 추정. 기록이 40거래일보다 짧으면 시장 1배로 단순 비교
    결과의 d0은 반응일 하루, d01은 반응일+다음날 2일 누적.
    """
    day0 = date.fromisoformat(d0)
    lo, hi = (day0 - timedelta(days=int(est_len * 2.4) + 40)).isoformat(), (day0 + timedelta(days=10)).isoformat()
    own, mkt = _rets(symbol, lo, hi), _rets(KOSDAQ, lo, hi)
    facs = [(n, r, "seg") for n, r in _segment_rets(lo, hi).items()]
    facs += [(f["name"], _rets(f["code"], lo, hi), "etf") for f in (etfs or [])]
    dates = sorted(d for d in own if d in mkt)
    if d0 not in dates:
        return None
    i0 = dates.index(d0)
    from .eventstudy import estimation_days
    est_all = estimation_days(dates, i0, est_len, gap, estimation_exclusions(symbol))   # 기사·공시 날은 건너뜀
    # 추정 기간의 80% 이상 시세가 있는 분야만 씀 (최근 상장 종목뿐인 분야는 뺌)
    facs = [f for f in facs if est_all and sum(d in f[1] for d in est_all) >= 0.8 * len(est_all) and d0 in f[1]]
    est = [d for d in est_all if all(d in f[1] for f in facs)]
    out = {"n_obs": len(est), "window": [est[0], est[-1]] if est else None}
    if len(est) >= 40:
        # 민감도는 큰 사건 날(±16% 같은 날)에 끌려가지 않게, 추정할 때만 값을 중앙값 ± 4×MAD 안으로 눌러서(윈저화) 구함
        def w(x):
            x = np.asarray(x, dtype=float)
            med = np.median(x)
            mad = 1.4826 * np.median(np.abs(x - med)) or float(np.std(x)) or 1.0
            return np.clip(x, med - WINSOR_K * mad, med + WINSOR_K * mad)
        m_raw = np.array([mkt[d] for d in est])
        m = w(m_raw)
        resid, resid_raw, slopes = [], [], []
        for _, r, _ in facs:                     # 분야에서 시장과 겹치는 부분을 뺌 (분야만의 움직임)
            y_raw = np.array([r[d] for d in est])
            y = w(y_raw)
            b = float(m @ y / (m @ m)) if m @ m else 0.0
            slopes.append(b)
            resid.append(y - b * m)
            resid_raw.append(y_raw - b * m_raw)
        yo_raw = np.array([own[d] for d in est])
        yo = w(yo_raw)
        # 분야는 표준화한 뒤 릿지(시장 계수는 축소하지 않음): 관측 RIDGE_OBS개만큼의 '0일 것'이라는 사전 믿음
        scale = [float(np.std(r)) or 1.0 for r in resid]
        Z = np.column_stack([m] + [r / sc for r, sc in zip(resid, scale)])
        pen = np.diag([0.0] + [RIDGE_OBS] * len(resid))
        cz = np.linalg.solve(Z.T @ Z + pen, Z.T @ yo)
        coef = np.array([cz[0]] + [cz[1 + i] / scale[i] for i in range(len(resid))])
        # 평소 고유 움직임(σ·얼마나 드문가 비교용)은 누르지 않은 실제 값으로
        res = yo_raw - np.column_stack([m_raw] + resid_raw) @ coef
        sigma = float(1.4826 * np.median(np.abs(res - np.median(res))))
        out.update({"method": "분야별 회귀", "beta_market": float(coef[0]), "sigma": sigma})
        betas = [float(c) for c in coef[1:]]
    else:                                        # 기록이 짧으면 시장 1배, 분야 몫 없이
        res = np.array([own[d] - mkt[d] for d in dates[max(0, i0 - 60):i0]])
        diffs = res
        sigma = float(1.4826 * np.median(np.abs(diffs - np.median(diffs)))) if len(diffs) >= 10 else None
        facs, slopes, betas = [], [], []
        out.update({"method": "단순", "beta_market": 1.0, "sigma": sigma})
    out["beta_sector"] = None
    out["parts"] = [{"name": n, "kind": k, "beta": betas[i], "slope": slopes[i]} for i, (n, _, k) in enumerate(facs)]
    resid_hist = [float(x) for x in res]

    def window(n_days: int) -> dict | None:
        ds = dates[i0:i0 + n_days]
        if len(ds) < n_days or any(d not in mkt for d in ds):
            return None
        own_r = float(np.prod([1 + own[d] for d in ds]) - 1)
        mk = sum(out["beta_market"] * mkt[d] for d in ds)
        parts = []
        for i, (n, r, k) in enumerate(facs):
            fr = sum(r.get(d, 0.0) - slopes[i] * mkt[d] for d in ds)
            parts.append({"name": n, "kind": k, "factor": float(np.prod([1 + r.get(d, 0.0) for d in ds]) - 1),
                          "factor_resid": float(fr), "beta": betas[i], "contrib": float(betas[i] * fr)})
        sec = sum(p["contrib"] for p in parts)
        abn = own_r - mk - sec
        sig = out["sigma"]
        # 평소 같은 길이(n_days일) 고유 움직임 중 이번 이상으로 컸던 비율
        null = [sum(resid_hist[i:i + n_days]) for i in range(len(resid_hist) - n_days + 1)]
        rarity = (1 + sum(1 for v in null if abs(v) >= abs(abn))) / (1 + len(null)) if len(null) >= 20 else None
        return {"own": own_r, "market": float(mk), "sector": float(sec), "abnormal": float(abn),
                "rarity": rarity, "n_null": len(null),
                "z": float(abn / (sig * len(ds) ** 0.5)) if sig else None,
                "kosdaq": float(np.prod([1 + mkt[d] for d in ds]) - 1), "sector_avg": None,
                "parts": parts, "days": len(ds)}

    out["d0"], out["d01"] = window(1), window(2)
    return out


def recent_days(symbol: str, d0: str, n: int = 5) -> list[dict]:
    """반응일 직전 n거래일의 이 종목·KOSDAQ 하루 등락 (원인 질문에 최근 흐름으로 붙임)."""
    lo = (date.fromisoformat(d0) - timedelta(days=n * 2 + 20)).isoformat()
    own, mkt = _rets(symbol, lo, d0), _rets(KOSDAQ, lo, d0)
    ds = [d for d in sorted(own) if d < d0][-n:]
    return [{"date": d, "own": own[d], "kosdaq": mkt.get(d)} for d in ds]


# ------------------------------------------------------------------ ③ 거래 강도

def trading_intensity(symbol: str, d0: str, n: int = 20) -> dict | None:
    lo = (date.fromisoformat(d0) - timedelta(days=120)).isoformat()
    rows = _daily(symbol, lo, d0)
    if len(rows) < n + 2 or rows[-1][0] != d0:
        return None
    vals = [(r[0], (r[2] or 0) * (r[3] or 0), r[3] or 0, (rows[i][2] / rows[i - 1][2] - 1) if i and rows[i - 1][2] else None)
            for i, r in enumerate(rows)]
    hist = vals[-n - 1:-1]
    d = vals[-1]
    adv = float(np.mean([v[2] for v in hist])) if hist else 0
    adval = float(np.mean([v[1] for v in hist])) if hist else 0
    ami = [abs(v[3]) / (v[1] / 1e8) for v in vals[-61:-1] if v[3] is not None and v[1] > 0]
    ami0 = abs(d[3]) / (d[1] / 1e8) if d[3] is not None and d[1] > 0 else None
    med = float(np.median(ami)) if ami else None
    return {"volume": d[2], "value": d[1], "vol_x": d[2] / adv if adv else None, "value_x": d[1] / adval if adval else None,
            "adv": adv, "adval": adval, "impact": ami0, "impact_normal": med,
            "impact_x": (ami0 / med) if (ami0 is not None and med) else None}
