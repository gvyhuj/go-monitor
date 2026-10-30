"""이 종목 일별 등락을 어느 묶음이 더 잘 설명하는지 비교 (KOSDAQ / 네이버 업종 WICS / 네이버 테마).

비교 기준 (최근 days 거래일, 일별 등락률):
  - 상관: 이 종목 등락과 묶음 등락의 상관계수
  - R²: 묶음 하나로 이 종목 등락을 얼마나 설명하는지 (0~1)
  - 시장 뺀 상관: 둘 다에서 KOSDAQ 몫을 뺀 뒤의 상관 (시장 전체 흐름이 아니라 그 묶음 고유의 흐름이 맞는지)
  - KOSDAQ+묶음 R²: KOSDAQ과 묶음을 함께 썼을 때 설명력
이 종목·KOSDAQ은 거래소(KRX) 정규장 종가, 업종·테마는 네이버 등락률 기록, 묶음 종목은 저장 일봉 종가.
"""
from __future__ import annotations

import numpy as np

from . import yahoo


def _rets(closes: dict) -> dict:
    ds = sorted(d for d, v in closes.items() if v)
    return {b: closes[b] / closes[a] - 1 for a, b in zip(ds, ds[1:])}


def _mean_by_day(series: list[dict]) -> dict:
    out = {}
    for s in series:
        for d, v in s.items():
            out.setdefault(d, []).append(v)
    return {d: sum(v) / len(v) for d, v in out.items()}


def _r2(y, cols) -> float:
    x = np.column_stack([np.ones(len(y)), *cols])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    res = y - x @ coef
    tot = ((y - y.mean()) ** 2).sum()
    return float(1 - (res ** 2).sum() / tot) if tot else 0.0


def _resid(y, x):
    b = np.polyfit(x, y, 1)
    return y - np.polyval(b, x)


def factors(conn, code: str) -> dict:
    """{묶음 이름: {날짜: 등락률}}"""
    from .feed import WICS_PEERS
    from .macro import basket_universe
    out = {"KOSDAQ": _rets(yahoo.krx_closes(conn, yahoo.INDEX))}
    wics = {}
    for no, name in WICS_PEERS:
        s = {d: r / 100 for d, r in conn.execute(
            "SELECT date, change_rate FROM industry_daily WHERE industry_no=? AND change_rate IS NOT NULL", (no,))}
        if s:
            wics[name] = s
            out[f"WICS {name}"] = s
    if wics:
        out["WICS 4업종 평균"] = _mean_by_day(list(wics.values()))
    peers = []
    for (c,) in conn.execute("SELECT code FROM related_stocks"):
        cl = {d: v for d, v in conn.execute("SELECT date, close FROM daily_bars WHERE symbol=?", (c,))}
        if cl:
            peers.append(_rets(cl))
    if peers:
        out[f"WICS 비교 종목 {len(peers)}개 평균"] = _mean_by_day(peers)
    themes = {}
    for no, name in conn.execute("SELECT theme_no, theme_name FROM stock_themes WHERE code=?", (code,)):
        s = {d: r / 100 for d, r in conn.execute(
            "SELECT date, change_rate FROM theme_daily WHERE theme_no=? AND change_rate IS NOT NULL", (no,))}
        if s:
            themes[name] = s
            out[f"테마 {name}"] = s
    if themes:
        out[f"테마 {len(themes)}개 평균"] = _mean_by_day(list(themes.values()))
    for seg, v in basket_universe().items():
        rs = []
        for x in v.get("stocks") or []:
            cl = {d: c for d, c in conn.execute("SELECT date, close FROM daily_bars WHERE symbol=?", (x["code"],))}
            if cl:
                rs.append(_rets(cl))
        if rs:
            out[f"테마 {seg} 대형 {len(rs)}개 평균"] = _mean_by_day(rs)
    return out


def compare(conn, code: str, days: int = 120, min_n: int = 20) -> dict:
    own = _rets(yahoo.krx_closes(conn, code))
    recent = sorted(own)[-days:]
    fs = factors(conn, code)
    kq = fs.get("KOSDAQ", {})
    rows = []
    for name, f in fs.items():
        ds = [d for d in recent if d in f and d in kq]
        if len(ds) < min_n:
            rows.append({"name": name, "n": len(ds)})
            continue
        y = np.array([own[d] for d in ds]); x = np.array([f[d] for d in ds]); k = np.array([kq[d] for d in ds])
        row = {"name": name, "n": len(ds), "from": ds[0], "to": ds[-1],
               "corr": float(np.corrcoef(y, x)[0, 1]), "r2": _r2(y, [x])}
        if name != "KOSDAQ":
            row["corr_ex_market"] = float(np.corrcoef(_resid(y, k), _resid(x, k))[0, 1])
            row["r2_with_kosdaq"] = _r2(y, [k, x])
        rows.append(row)
    return {"code": code, "days": days, "rows": rows}


def summary_line(res: dict) -> str:
    ok = [r for r in res["rows"] if "corr" in r]
    if not ok:
        return "묶음 설명력: 비교할 기록이 부족합니다 (" + ", ".join(f"{r['name']} {r['n']}일" for r in res["rows"]) + ")"
    ok.sort(key=lambda r: -(r.get("corr_ex_market") if r["name"] != "KOSDAQ" else 9))
    def one(r):
        s = f"{r['name']}: 상관 {r['corr']:.2f}, R² {r['r2']:.2f}"
        if r["name"] != "KOSDAQ":
            s += f", 시장 뺀 상관 {r['corr_ex_market']:.2f}, KOSDAQ+묶음 R² {r['r2_with_kosdaq']:.2f}"
        return s + f" ({r['n']}일)"
    short = [f"{r['name']} {r['n']}일" for r in res["rows"] if "corr" not in r]
    return "묶음 설명력(최근 일별): " + " / ".join(one(r) for r in ok) + (f" / 기록 부족: {', '.join(short)}" if short else "")
