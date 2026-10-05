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

def event_study(symbol: str, d0: str, est_len: int = 120, gap: int = 10) -> dict | None:
    lo = (date.fromisoformat(d0) - timedelta(days=400)).isoformat()
    hi = (date.fromisoformat(d0) + timedelta(days=7)).isoformat()
    own, mkt = _rets(symbol, lo, hi), _rets(KOSDAQ, lo, hi)
    dates = sorted(d for d in own if d in mkt)
    if d0 not in dates:
        return None
    i0 = dates.index(d0)
    est = dates[max(0, i0 - gap - est_len):max(0, i0 - gap)]
    if len(est) < 50:
        return {"ok": False, "reason": f"평소 관계를 계산할 기록이 부족합니다 ({len(est)}거래일)."}
    y = np.array([own[d] for d in est])
    x = np.array([mkt[d] for d in est])
    X = np.column_stack([np.ones_like(x), x])
    (a, b), *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ np.array([a, b])
    sig = float(np.sqrt((resid ** 2).sum() / (len(est) - 2)))

    def ar(d):
        return own[d] - (a + b * mkt[d])

    out = {"ok": True, "n_est": len(est), "est_from": est[0], "est_to": est[-1], "alpha": float(a), "beta": float(b),
           "sigma": sig, "actual": own[d0], "expected": float(a + b * mkt[d0]), "ar": ar(d0)}
    out["t"] = out["ar"] / sig
    out["p"] = _p_norm(out["t"])
    windows = {"사전 5일 [-5,-1]": range(-5, 0), "전후 3일 [-1,+1]": range(-1, 2), "이후 3일 [0,+2]": range(0, 3)}
    out["car"] = []
    for name, rg in windows.items():
        ds = [dates[i0 + k] for k in rg if 0 <= i0 + k < len(dates)]
        if len(ds) != len(rg):
            continue
        car = sum(ar(d) for d in ds)
        t = car / (sig * math.sqrt(len(ds)))
        out["car"].append({"window": name, "car": car, "t": t, "p": _p_norm(t)})
    vols = {r[0]: r[3] for r in _daily(symbol, lo, hi)}
    base = [vols[d] for d in est if vols.get(d)]
    if base and vols.get(d0):
        out["vol_ratio"] = vols[d0] / float(np.median(base))
    pre = next((c for c in out["car"] if c["window"].startswith("사전")), None)
    out["pre_drift"] = bool(pre and pre["p"] < 0.05 and pre["car"] * out["ar"] > 0)
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
