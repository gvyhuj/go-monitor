"""화면 서버 (브라우저용 웹사이트 + 데이터 API).

감시 엔진(monitor.py)과 별도 프로세스입니다. 이 서버를 꺼도 기록은 계속됩니다.
외부 라이브러리 없이 Python 기본 기능만 사용하고, 이 PC(127.0.0.1)에서만 접속됩니다.
'공유 링크'를 켜면 Cloudflare 임시 터널로 들어온 요청은 비밀 열쇠가 맞을 때만, 읽기 전용으로 보여줍니다.

    python webserver.py --port 8501
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import mimetypes
import re
import secrets
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

from core import analysis, articles, causes, cloud, feed, macro, manual, naver, share
from core.db import connect, get_state, init_db
from core.detector import load_settings

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "web"
KOSDAQ = "KOSDAQ"
DIR_KO = {"up": "급등", "down": "급락", "flat": "거래량만 급증"}

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")


# ------------------------------------------------------------------ helpers

def clean(o):
    """JSON으로 보낼 수 있게 NaN/numpy/Timestamp 정리."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat(timespec="minutes")
    if isinstance(o, date):
        return o.isoformat()
    if hasattr(o, "item"):  # numpy scalar
        return clean(o.item())
    return o


def state_json(key):
    s = get_state(key)
    if not s or not s.get("value"):
        return None
    try:
        return json.loads(s["value"])
    except Exception:
        return None


def bars_payload(df: pd.DataFrame, full=True):
    if df is None or df.empty:
        return []
    if full:
        return [{"t": r.ts.isoformat(timespec="minutes"), "o": r.open, "h": r.high, "l": r.low,
                 "c": r.close, "v": int(r.volume)} for r in df.itertuples()]
    return [{"t": r.ts.isoformat(timespec="minutes"), "c": r.close} for r in df.itertuples()]


def change_in(df: pd.DataFrame, t0: datetime, t1: datetime):
    """미리 읽어 둔 분봉에서 t0 종가 → t1 종가 변화율."""
    if df is None or df.empty:
        return None
    a = df[df["ts"] <= t0]
    b = df[(df["ts"] > t0) & (df["ts"] <= t1)]
    if a.empty or b.empty:
        return None
    if a["ts"].iloc[-1].date() != b["ts"].iloc[-1].date():
        # 전날 지수 분봉만 있으면 같은 날 시가 기준
        base = float(b["open"].iloc[0])
    else:
        base = float(a["close"].iloc[-1])
    return float(b["close"].iloc[-1]) / base - 1 if base else None


def event_rows(symbol: str, d_from: str | None, d_to: str | None, kind: str | None):
    q = "SELECT * FROM events WHERE symbol=?"
    params = [symbol]
    if d_from:
        q += " AND start_ts >= ?"
        params.append(d_from + "T00:00")
    if d_to:
        q += " AND start_ts <= ?"
        params.append(d_to + "T23:59")
    if kind in DIR_KO:
        q += " AND direction = ?"
        params.append(kind)
    q += " ORDER BY start_ts DESC"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(q, params)]
        for r in rows:
            r["_manual"] = manual.find(conn, r)
    if not rows:
        return []
    t_min = datetime.fromisoformat(min(r["start_ts"] for r in rows)) - timedelta(days=4)
    t_max = datetime.fromisoformat(max(r["last_ts"] for r in rows)) + timedelta(minutes=5)
    own = analysis.load_range(symbol, t_min, t_max)
    kq = analysis.load_range(KOSDAQ, t_min, t_max)
    out = []
    for r in rows:
        t0 = datetime.fromisoformat(r["start_ts"]) - timedelta(minutes=5)
        t1 = datetime.fromisoformat(r["last_ts"])
        mc = r.get("_manual")              # 원인은 직접 붙여넣은 글만 (자동 추정 결론은 쓰지 않음)
        daily = r["event_type"] == "daily"
        out.append({
            "cause_headline": manual.summary(mc),
            "cause_label": None,
            "cause_confidence": manual.confidence(mc),
            "cause_summary": None,
            "manual": {k: v for k, v in mc.items() if k not in ("body", "entries")} if mc else None,   # 본문은 상세 화면에서만
            "cause_stage": r.get("cause_stage"),
            "tier": r.get("tier") or "일반", "trade_value": r.get("trade_value"),
            "scope": "하루" if daily else "장중",
            "id": r["id"], "start_ts": r["start_ts"], "last_ts": r["last_ts"],
            "direction": r["direction"], "kind": DIR_KO.get(r["direction"], r["direction"]),
            "event_type": r["event_type"],
            "peak_return_5m": r.get("peak_return_5m"), "peak_ts": r.get("peak_ts"),
            "peak_volume_ratio_5m": r.get("peak_volume_ratio_5m"),
            "own_change": r.get("peak_return_5m") if daily else change_in(own, t0, t1),
            "kosdaq_change": causes._daily_change(KOSDAQ, r["start_ts"][:10]) if daily else change_in(kq, t0, t1),
            "rule": r.get("detection_rule"), "status": r.get("status"),
        })
    return out


def engine_state(settings):
    hb = get_state("monitor_heartbeat")
    mon = get_state("monitor_status")
    mon_v = mon["value"] if mon else None
    if mon_v == "stopped":
        return {"state": "stopped", "label": "꺼짐", "heartbeat": None}
    if not hb:
        return {"state": "unknown", "label": "확인 중", "heartbeat": None}
    hb_t = datetime.fromisoformat(hb["value"])
    if (datetime.now() - hb_t).total_seconds() > max(60, settings.get("monitor_poll_seconds", 20) * 3):
        return {"state": "stale", "label": "응답 없음", "heartbeat": hb["value"]}
    if mon_v and mon_v.startswith("error"):
        return {"state": "error", "label": "오류", "heartbeat": hb["value"], "message": mon_v[7:]}
    return {"state": "ok", "label": "감시 중", "heartbeat": hb["value"]}


# ------------------------------------------------------------------ API

def api_status(_q):
    s = load_settings()
    viewer = _q.get("_viewer", "local")
    sym = s["symbol"]
    data = get_state("data_status")
    data_ok = get_state("data_last_ok")
    with connect() as conn:
        last_bar = conn.execute("SELECT MAX(ts) FROM minute_bars WHERE symbol=?", (sym,)).fetchone()[0]
        n_bars = conn.execute("SELECT COUNT(*) FROM minute_bars WHERE symbol=?", (sym,)).fetchone()[0]
        n_events = conn.execute("SELECT COUNT(*) FROM events WHERE symbol=?", (sym,)).fetchone()[0]
        first_bar = conn.execute("SELECT MIN(ts) FROM minute_bars WHERE symbol=?", (sym,)).fetchone()[0]
        # 사업 분야별 비교 종목 묶음 (분야 이름과 종목 수만, 설정 순서대로)
        segments = [{"name": r[0] or "비교 종목", "n": r[1]} for r in conn.execute(
            "SELECT source, COUNT(*) FROM related_stocks GROUP BY source ORDER BY MIN(rowid)")]
    dv = data["value"] if data else ""
    return {
        "symbol": sym, "name": s.get("display_name", sym),
        "quote": state_json(f"quote:{sym}"), "kosdaq": state_json(f"quote:{KOSDAQ}"), "naver": state_json(f"naver_snapshot:{sym}"),
        "engine": engine_state(s),
        "data": {"ok": not dv.startswith("error"), "message": dv[7:] if dv.startswith("error") else dv[4:],
                 "last_ok": data_ok["value"] if data_ok else None},
        "last_bar_ts": last_bar, "first_bar_ts": first_bar, "bars_count": n_bars, "events_count": n_events,
        "segments": segments,
        "collect": _collect_status(sym),
        "viewer": viewer, "share": share.status() if viewer == "local" else None,
        "cloud": cloud.status() if viewer == "local" else None,
        "rules": {
            "move_pct": s.get("event_move_pct", 10.0), "big_pct": s.get("event_big_pct", 15.0),
            "watch_pct": s.get("event_watch_pct", 5.0),
            "window_min": s.get("event_window_minutes", 30), "hold_min": s.get("event_hold_minutes", 5),
        },
    }


def _collect_status(sym: str) -> dict:
    from core import collect_status
    from core.alerts import market_hours
    now = feed.kst_now()
    return {"sources": collect_status.snapshot(now, market_hours(now, now.weekday() < 5)),
            "stored": collect_status.stored(sym)}


def _coarse_reaction(sym: str, e: dict, time_ok: bool):
    """1분봉이 없는 지난 기사: 야후 봉으로 +30·60·120분 (이 종목 · 코스닥 · 차이)."""
    from core import yahoo
    d0, base = e.get("reaction_day"), e.get("base_ts")
    if not time_ok or not d0 or not base:
        return None
    try:
        with connect() as conn:
            pc = conn.execute("SELECT close FROM daily_bars WHERE symbol=? AND date<? ORDER BY date DESC LIMIT 1", (sym, d0)).fetchone()
            pk = conn.execute("SELECT close FROM daily_bars WHERE symbol=? AND date<? ORDER BY date DESC LIMIT 1", (KOSDAQ, d0)).fetchone()
        return yahoo.reaction(sym, base, d0, pc[0] if pc else None, pk[0] if pk else None)
    except Exception:
        return None


def _reaction_chart(sym: str, e: dict, iw: dict | None, ic: dict | None):
    """Use the same observed timestamps and bases as the tables."""
    from core import yahoo
    w = iw or ic
    if not w or not w.get("start"):
        return None
    start = datetime.fromisoformat(w["start"])
    day = w.get("day") or e.get("reaction_day") or w["start"][:10]
    with connect() as conn:
        frame = yahoo.reaction_frame(conn, sym, day, start, w.get("kind"), 1 if iw else yahoo.MIN_DAY_MINUTES)
    if not frame["own"]:
        return None
    lo = (start - timedelta(minutes=max(30, 3 * (frame["interval"] or 1)))).isoformat(timespec="minutes")
    hi = min((start + timedelta(minutes=120)).isoformat(timespec="minutes"), day + "T15:30")
    return {**frame, "own": [{"t": t, "c": c} for t, c in frame["own"] if lo <= t <= hi],
            "kq": [{"t": t, "c": c} for t, c in frame["kq"] if lo <= t <= hi]}


def api_coarse(_q):
    """1분봉이 없는 날의 차트용 야후 5분봉·60분봉: {날짜: {interval, prev_close, bars}} (1분봉이 있는 날은 뺌)."""
    from core import yahoo
    sym = load_settings()["symbol"]
    with connect() as conn:
        have = {r[0] for r in conn.execute("SELECT DISTINCT substr(ts,1,10) FROM minute_bars WHERE symbol=?", (sym,))}
        days = [r[0] for r in conn.execute("SELECT DISTINCT substr(ts,1,10) d FROM coarse_bars WHERE symbol=? ORDER BY d", (sym,))]
        daily = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT date, close, volume FROM daily_bars WHERE symbol=? AND close > 0", (sym,))}
        closes = {d: v[0] for d, v in daily.items()}
    dates = sorted(closes)
    prev = {d: closes[p] for p, d in zip(dates, dates[1:])}
    out = {}
    for d in days:
        if d in have:
            continue
        iv, bars = yahoo.day_bars(sym, d)
        if bars:
            # 야후 봉은 마지막 30분·종가 동시호가가 빠져(60분봉은 14:00 봉에서 끝남) 끝이 실제 종가와 달라짐
            # → 15:30에 거래소 종가 한 점을 붙여 차트 끝이 그날 등락과 같게 (거래량은 하루 거래량에서 봉 합계를 뺀 나머지)
            if d in daily and bars[-1]["t"][11:16] < "15:30":
                c, vol = daily[d]
                rest = max(0, int(vol or 0) - sum(b["v"] for b in bars))
                bars.append({"t": f"{d}T15:30", "o": bars[-1]["c"], "h": max(c, bars[-1]["c"]), "l": min(c, bars[-1]["c"]),
                             "c": c, "v": rest, "close_point": True})
            out[d] = {"interval": iv, "prev_close": prev.get(d), "bars": bars}
    return {"days": out}


def api_intraday(q):
    s = load_settings()
    sym = s["symbol"]
    with connect() as conn:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT substr(ts,1,10) d FROM minute_bars WHERE symbol=? ORDER BY d DESC LIMIT 250", (sym,))]  # 달력에서 고른 날 차트 (약 1년)
    if not dates:
        return {"date": None, "dates": [], "bars": [], "kosdaq": [], "events": [], "prev_close": None}
    d = q.get("date") or dates[0]
    day0 = datetime.fromisoformat(d + "T00:00")
    own = analysis.load_range(sym, day0, day0 + timedelta(hours=23, minutes=59))
    kq = analysis.load_range(KOSDAQ, day0, day0 + timedelta(hours=23, minutes=59))
    prev = analysis.load_range(sym, day0 - timedelta(days=10), day0 - timedelta(minutes=1))
    prev_close = float(prev["close"].iloc[-1]) if not prev.empty else None
    quote = state_json(f"quote:{sym}")
    # 네이버 기준 전일 종가. 단 시세가 이 날짜 체결일 때만 (다음 날 장 시작 전엔 네이버가 등락 0으로 돌려
    # '전일 종가 = 그날 종가'가 되어 버림)
    if d == dates[0] and quote and quote.get("price") is not None and quote.get("change") is not None             and str(quote.get("traded_at") or "")[:10] == d:
        prev_close = quote["price"] - quote["change"]
    with connect() as conn:
        dr = conn.execute("SELECT close, base FROM daily_bars WHERE symbol=? AND date=?", (sym, d)).fetchone()
    day_close = None                     # 네이버 종가 (넥스트레이드 포함 마지막 체결가). 1분 차트는 정규장(15:30)까지
    if dr and dr["base"]:
        prev_close, day_close = float(dr["base"]), float(dr["close"])
    with connect() as conn:
        ind = industry_rates(conn).get(d)       # 장중 오늘 줄용 (일봉이 아직 없어 화면이 이 값으로 업종 등락을 보여 줌)
        ind_info = industry_info(conn).get(d)
        tl = trader_tops_live(conn, sym, d) if d == dates[0] else None
        # 장중 오늘: 비교 종목 일봉이 아직 없으므로 1분봉으로 그날 등락 (목록 순서는 api_daily의 peers와 같음)
        pl = None
        if d == dates[0]:
            rel = conn.execute("SELECT code FROM related_stocks WHERE code NOT IN (?, ?) ORDER BY source DESC, code",
                               (sym, KOSDAQ)).fetchall()
            pl = [causes._daily_change(r[0], d) for r in rel]
            pl = [None if v is None else round(v, 5) for v in pl] if any(v is not None for v in pl) else None
    fe = state_json(f"foreign_est:{sym}")      # 장중 외국계 창구 추정 순매수 (그날 값일 때만)
    fe = fe if fe and fe.get("date") == d and fe.get("buy") is not None else None
    return {"date": d, "dates": dates, "bars": bars_payload(own), "kosdaq": bars_payload(kq, full=False),
            "prev_close": prev_close, "close": day_close, "events": event_rows(sym, d, d, None), "industries": ind, "industry_info": ind_info, "foreign_est": fe, "traders_live": tl, "peers_live": pl}


def api_events(q):
    s = load_settings()
    sym = s["symbol"]
    d_from, d_to, kind = q.get("from"), q.get("to"), q.get("kind")
    events = event_rows(sym, d_from, d_to, kind)
    with connect() as conn:
        tq = "SELECT DISTINCT substr(ts,1,10) d FROM minute_bars WHERE symbol=?"
        params = [sym]
        if d_from:
            tq += " AND ts >= ?"
            params.append(d_from)
        if d_to:
            tq += " AND ts <= ?"
            params.append(d_to + "T23:59")
        trading_days = [r[0] for r in conn.execute(tq + " ORDER BY d", params)]
    return {"events": events, "trading_days": trading_days}


def api_daily(_q):
    """달력·일별 표용: 날마다 시가·고가·저가·종가·전일 대비 등락·거래량·KOSDAQ 등락·투자자별 순매수 (일봉 전체)."""
    sym = load_settings()["symbol"]
    with connect() as conn:
        own = conn.execute("SELECT date, close, volume, open, high, low, base FROM daily_bars WHERE symbol=? AND close > 0 "
                           "ORDER BY date", (sym,)).fetchall()
        kq = dict(conn.execute("SELECT date, close FROM daily_bars WHERE symbol=? AND close > 0", (KOSDAQ,)).fetchall())
        inv = {r[0]: r[1:] for r in conn.execute(
            "SELECT bizdate, foreign_net, organ_net, individual_net, foreign_ratio FROM investor_daily WHERE symbol=?",
            (sym,))}
        calc = set()
        ind, ind_info, tops = industry_rates(conn, calc), industry_info(conn), trader_tops(conn, sym)
        peers, peer_chg = peer_changes(conn, sym)
    kdates = sorted(kq)
    kprev = {d: kq[p] for p, d in zip(kdates, kdates[1:])}
    days = []
    for i, (d, c, v, o, h, lo, base) in enumerate(own):
        prev = base or (own[i - 1][1] if i else None)      # 네이버 기준가 (없으면 전날 종가)
        f = inv.get(d) or inv.get(d.replace("-", "")) or (None, None, None, None)
        days.append({"date": d, "close": c, "volume": v, "open": o, "high": h, "low": lo,
                     "change": c / prev - 1 if prev else None, "base": prev,
                     "kosdaq": kq[d] / kprev[d] - 1 if d in kq and d in kprev else None, "kosdaq_close": kq.get(d),
                     "foreign": f[0], "organ": f[1], "individual": f[2], "foreign_ratio": f[3],
                     "industries": ind.get(d), "industry_info": ind_info.get(d), "traders": tops.get(d),
                     "industries_calc": d in calc, "peers": peer_chg.get(d)})
    return {"days": days, "peers": peers}


def peer_changes(conn, sym: str) -> tuple[list[dict], dict]:
    """메인 기록 창의 '사업 분야별 비교 종목'용: 비교 종목 목록 [{name, source}]와 {날짜: [그날 등락률(목록 순서), 없으면 None]}.
    그날 자동 분석이 없는 날(장중의 오늘, 60일보다 오래된 날)도 일봉으로 보여 주려고."""
    rel = [dict(r) for r in conn.execute("SELECT code, name, source FROM related_stocks WHERE code NOT IN (?, ?) "
                                         "ORDER BY source DESC, code", (sym, KOSDAQ))]
    out: dict = {}
    for i, r in enumerate(rel):
        prev = None
        for d, c, base in conn.execute("SELECT date, close, base FROM daily_bars WHERE symbol=? AND close > 0 ORDER BY date",
                                       (r["code"],)):
            b = base or prev
            if b:
                out.setdefault(d, [None] * len(rel))[i] = round(c / b - 1, 5)
            prev = c
    return [{"name": r["name"], "source": r["source"]} for r in rel], out


# 사업 분야 칸에 보여 줄 네이버 업종(WICS) = 비교 종목 묶음과 같은 네 업종. 방산·우주항공은 WICS에서 '우주항공과국방' 한 업종
INDUSTRY_NAMES = [name for _, name in feed.WICS_PEERS]


def industry_rates(conn, calc: set | None = None) -> dict:
    """{날짜: {업종: 등락률(소수)}}. 없는 날은 키가 없음.
    2026-10-26부터는 네이버 값을 기록, 그 전 날은 구성 종목으로 다시 계산한 값(updated_at='calc') → calc에 그 날짜를 담음."""
    out = {}
    q = "SELECT date, name, change_rate, updated_at FROM industry_daily WHERE name IN (%s)" % ",".join("?" * len(INDUSTRY_NAMES))
    for d, name, rate, upd in conn.execute(q, INDUSTRY_NAMES):
        if rate is not None:
            out.setdefault(d, {})[name] = rate / 100
            if calc is not None and upd == "calc":
                calc.add(d)
    return out


def _tops(rows) -> dict:
    """거래원 행 [(이름, 매수, 매도, 외국계, 매수5위안, 매도5위안)] → 매수·매도 상위 5 창구."""
    buy = sorted([r for r in rows if r[4]], key=lambda r: -r[1])[:5]
    sell = sorted([r for r in rows if r[5]], key=lambda r: -r[2])[:5]
    return {"buy": [{"name": r[0], "qty": r[1], "foreign": bool(r[3])} for r in buy],
            "sell": [{"name": r[0], "qty": r[2], "foreign": bool(r[3])} for r in sell]}


def trader_tops(conn, sym: str) -> dict:
    """순매수 칸을 누르면 뜨는 창용 {날짜: 장 마감 거래원 매수·매도 상위 5} (네이버, 장 마감 기록이 있는 날만)."""
    by_day = {}
    for d, *r in conn.execute("SELECT date, name, buy, sell, is_foreign, buy_top5, sell_top5 FROM trader_daily "
                              "WHERE symbol=? AND source='네이버'", (sym,)):
        by_day.setdefault(d, []).append(r)
    return {d: _tops(rows) for d, rows in by_day.items()}


def trader_tops_live(conn, sym: str, day: str) -> dict | None:
    """장중 오늘 거래원 매수·매도 상위 5 (가장 최근 1분 기록)."""
    ts = conn.execute("SELECT MAX(ts) FROM trader_snapshots WHERE symbol=? AND ts >= ? AND ts < ? || 'z'", (sym, day, day)).fetchone()[0]
    if not ts:
        return None
    rows = conn.execute("SELECT name, buy, sell, is_foreign, buy_top5, sell_top5 FROM trader_snapshots WHERE symbol=? AND ts=?",
                        (sym, ts)).fetchall()
    return {**_tops([tuple(r) for r in rows]), "ts": ts}


def industry_info(conn) -> dict:
    """업종 칸을 누르면 뜨는 창용 {날짜: {업종: {rise, fall, total, up: [...], down: [...]}}}."""
    out = {}
    q = "SELECT date, name, rise, fall, total FROM industry_daily WHERE name IN (%s)" % ",".join("?" * len(INDUSTRY_NAMES))
    for d, name, rise, fall, total in conn.execute(q, INDUSTRY_NAMES):
        out.setdefault(d, {})[name] = {"rise": rise, "fall": fall, "total": total, "up": [], "down": []}
    for d, name, movers in conn.execute("SELECT date, name, movers FROM industry_movers"):
        if d in out and name in out[d] and movers:
            out[d][name].update(json.loads(movers))
    return out


def api_event_detail(q, eid):
    s = load_settings()
    sym = s["symbol"]
    with connect() as conn:
        r = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    if not r:
        raise LookupError("해당 이상변동을 찾을 수 없습니다.")
    day = r["start_ts"][:10]
    ev = next((e for e in event_rows(sym, day, day, None) if e["id"] == eid), None)
    if ev:
        with connect() as conn:
            ev["manual"] = manual.find(conn, dict(r))
    t0, t1 = datetime.fromisoformat(r["start_ts"]), datetime.fromisoformat(r["last_ts"])
    own = analysis.load_range(sym, t0 - timedelta(minutes=30), t1 + timedelta(minutes=60))
    kq = analysis.load_range(KOSDAQ, t0 - timedelta(minutes=30), t1 + timedelta(minutes=60))
    rel = []
    cause = manual.facts_only(causes.stored(eid))
    if cause:
        for x in cause.get("related") or []:
            df = analysis.load_range(x["code"], t0 - timedelta(minutes=30), t1 + timedelta(minutes=60))
            if not df.empty:
                rel.append({"code": x["code"], "name": x["name"], "change": x.get("change"),
                            "bars": bars_payload(df, full=False)})
    return {"event": ev, "bars": bars_payload(own), "kosdaq": bars_payload(kq, full=False),
            "cause": cause, "related_bars": rel,
            "followups": causes.followup_filings(sym, day)}


def api_day_detail(q, day):
    """급등·급락 기록이 없는 날의 하루 분석 (장 마감 뒤 자동). 화면은 급등·급락 상세와 같은 창으로 그림."""
    s = load_settings()
    sym = s["symbol"]
    from core.detector import day_record
    r = day_record(sym, day)
    if not r:
        raise LookupError("이날 일봉 기록이 없습니다.")
    with connect() as conn:
        mc = manual.find(conn, r)
    ev = {"cause_headline": manual.summary(mc), "cause_confidence": manual.confidence(mc), "manual": mc,
          "tier": "일일", "trade_value": r["trade_value"], "scope": "하루", "id": None,
          "start_ts": r["start_ts"], "last_ts": r["last_ts"], "direction": r["direction"],
          "kind": DIR_KO.get(r["direction"], r["direction"]), "event_type": "daily",
          "peak_return_5m": r["peak_return_5m"], "peak_ts": r["peak_ts"], "own_change": r["peak_return_5m"],
          "kosdaq_change": causes._daily_change(KOSDAQ, day), "rule": r["detection_rule"], "status": "closed"}
    t0, t1 = datetime.fromisoformat(r["start_ts"]), datetime.fromisoformat(r["last_ts"])
    own = analysis.load_range(sym, t0 - timedelta(minutes=30), t1 + timedelta(minutes=60))
    kq = analysis.load_range(KOSDAQ, t0 - timedelta(minutes=30), t1 + timedelta(minutes=60))
    cause = manual.facts_only(causes.day_stored(sym, day))
    rel = []
    for x in (cause or {}).get("related") or []:
        df = analysis.load_range(x["code"], t0 - timedelta(minutes=30), t1 + timedelta(minutes=60))
        if not df.empty:
            rel.append({"code": x["code"], "name": x["name"], "change": x.get("change"), "bars": bars_payload(df, full=False)})
    return {"event": ev, "bars": bars_payload(own), "kosdaq": bars_payload(kq, full=False),
            "cause": cause, "related_bars": rel, "followups": causes.followup_filings(sym, day)}


def _live_fetch(kind, arg, day):
    """화면에서 '다시 분석'을 누르면 같은 업종 시세·뉴스를 바로 받아와 반영."""
    now = feed.kst_now()
    if kind == "related_bars":
        feed.sync_related_bars(list(arg), history=(day != now.date()), now=now)
    elif kind == "news":
        feed.sync_news(arg, 10)
    elif kind == "peer_traders":
        if day != now.date():
            return {}
        out = {}
        for c in arg:
            try:
                out[c] = naver.fetch_trader_info(c)
            except Exception:
                continue
        return out


def api_event_analyze(eid):
    with connect() as conn:
        r = conn.execute("SELECT symbol, cause_stage FROM events WHERE id=?", (eid,)).fetchone()
    if not r:
        raise LookupError("해당 이상변동을 찾을 수 없습니다.")
    for fn in (feed.sync_news, feed.sync_disclosures, feed.sync_research, feed.sync_broker_side_daily,
               feed.crosscheck_traders):
        try:
            fn(r["symbol"])
        except Exception:
            pass  # 받지 못해도 저장된 자료로 분석
    return causes.analyze(eid, r["cause_stage"] or "1차", fetch=_live_fetch)


def api_events_csv(q):
    s = load_settings()
    rows = event_rows(s["symbol"], q.get("from"), q.get("to"), q.get("kind"))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["번호", "날짜", "시작", "끝", "구분", "기준", "대형", "움직임(%)", "KOSDAQ 같은 기간(%)",
                "가장 유력한 원인", "신뢰도", "판단 요약", "분석 단계", "감지 근거"])

    def p(x):
        return "" if x is None else round(x * 100, 2)

    for e in rows:
        w.writerow([e["id"], e["start_ts"][:10], e["start_ts"][11:16], e["last_ts"][11:16], e["kind"], e.get("scope"),
                    e.get("tier") if e.get("tier") in ("대형", "관찰") else "", p(e["peak_return_5m"]), p(e["kosdaq_change"]),
                    e.get("cause_headline") or "", e.get("cause_confidence") or "", e.get("cause_summary") or "",
                    e.get("cause_stage") or "", e["rule"]])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def api_pr_list(_q):
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM pr_events ORDER BY published_ts DESC")]
    return {"items": rows}


def api_pr_create(body):
    title = (body.get("title") or "").strip()
    ts = (body.get("published_ts") or "").strip()
    if not title:
        raise ValueError("제목을 입력해 주세요.")
    try:
        ts = datetime.fromisoformat(ts).isoformat(timespec="minutes")
    except ValueError:
        raise ValueError("공개 일시 형식이 올바르지 않습니다.")
    with connect() as conn:
        cur = conn.execute("INSERT INTO pr_events(title, published_ts, url, pr_type) VALUES (?, ?, ?, ?)",
                           (title, ts, (body.get("url") or "").strip(), body.get("pr_type") or "보도자료"))
        return {"id": cur.lastrowid}


def api_pr_delete(pid):
    with connect() as conn:
        conn.execute("DELETE FROM pr_events WHERE id=?", (pid,))
    return {"ok": True}


def api_pr_detail(_q, pid):
    s = load_settings()
    sym = s["symbol"]
    with connect() as conn:
        pr = conn.execute("SELECT * FROM pr_events WHERE id=?", (pid,)).fetchone()
    if not pr:
        raise LookupError("해당 PR을 찾을 수 없습니다.")
    r = analysis.analyze_pr(sym, datetime.fromisoformat(pr["published_ts"]))
    out = {"pr": dict(pr), "ok": r.ok, "message": r.message, "summary": analysis.summary_sentence(r)}
    if r.ok:
        kq = analysis.load_range(KOSDAQ, r.reaction_start - timedelta(minutes=30),
                                 r.reaction_start + timedelta(minutes=120))
        out.update({
            "reaction_start": r.reaction_start, "base_price": r.base_price,
            "horizons": analysis.HORIZONS,
            "returns": {str(k): v for k, v in r.returns.items()},
            "kosdaq_returns": {str(k): v for k, v in r.kosdaq_returns.items()},
            "max_up": r.max_up, "max_up_at": r.max_up_at, "max_down": r.max_down, "max_down_at": r.max_down_at,
            "vol_after30": r.vol_after30, "vol_usual30": r.vol_usual30,
            "other_events": [dict(e, kind=DIR_KO.get(e["direction"], "")) for e in r.other_events],
            "bars": bars_payload(r.bars), "kosdaq": bars_payload(kq, full=False),
        })
    return out


_EFF_CACHE = {}


def _data_stamp(sym):
    with connect() as conn:
        a = conn.execute("SELECT MAX(date) FROM daily_bars WHERE symbol=?", (sym,)).fetchone()[0]
        b = conn.execute("SELECT MAX(ts) FROM minute_bars WHERE symbol=?", (sym,)).fetchone()[0]
        c = conn.execute("SELECT COUNT(*) FROM related_stocks").fetchone()[0]
    return f"{a}|{b}|{c}"


def _effect_cached(sym, a, stamp):
    key = (a["id"], a["ts"], a["category"], a["tone"], a["title"], stamp)
    hit = _EFF_CACHE.get(key)
    if hit is None:
        hit = articles.effect(sym, datetime.fromisoformat(a["ts"]), a["tone"], a["category"], a["title"])
        if len(_EFF_CACHE) > 3000:
            _EFF_CACHE.clear()
        _EFF_CACHE[key] = hit
    return hit


def api_articles(q):
    s = load_settings()
    sym = s["symbol"]
    rows = articles.article_rows(sym, include_hidden=q.get("hidden") == "1")
    stamp = _data_stamp(sym)
    limit = int(q.get("limit") or 0)
    out = []
    for a in rows:
        if limit and len(out) >= limit:
            break
        if limit and a["category"] == "단순 언급":
            continue
        e = _effect_cached(sym, a, stamp)
        out.append({**{k: a.get(k) for k in ("id", "ts", "office", "title", "url", "category", "tone", "mention",
                                              "outlets_total", "manual", "hidden", "kind", "kind_auto",
                                              "story", "source", "time_quality", "reporter", "topic", "topics")},
                    "related": [{k: r.get(k) for k in ("id", "ts", "office", "reporter", "title", "url", "topic")}
                                for r in a.get("related_titles") or []],
                    "n_related": len(a.get("related_titles") or []),
                    "effect": {k: e.get(k) for k in ("reaction_day", "verdict", "verdict_cls", "measure", "off_session",
                                                     "disclosure")},
                    "gap": (e.get("daily") or {}).get("gap"), "vol_rank": (e.get("daily") or {}).get("vol_rank"),
                    "d0_excess": ((e.get("daily") or {}).get("excess") or {}).get("d0"),
                    "m60_excess": ((e.get("minute") or {}).get("excess") or {}).get("60")})
    events = articles.group_events(out) if not limit else None
    with connect() as conn:
        first = conn.execute("SELECT MIN(date) FROM daily_bars WHERE symbol=?", (sym,)).fetchone()[0]
    from core.eventstudy import summary_critical_values
    from core import causal
    try:
        baseline = causal.news_baseline(sym) if not limit else None
    except Exception:
        baseline = None
    return {"items": out, "events": events, "categories": articles.CATEGORIES, "daily_since": first,
            "summary_critical_values": summary_critical_values(len(out)), "baseline": baseline}


def align_observation(sym, out):
    from core.observatory import build
    row = reaction_row(out)
    with connect() as conn:
        details, _ = build(conn, sym, [row])
    d = details[row['event_id']]
    out['observation_event_id'] = d['event_id']
    out['observation'] = d['observation']
    out['observation_analysis_at'] = d['analysis_at']
    f = d['observation'].get('intraday')
    if f:
        out['reaction_chart'] = {**f,
            'own':[{'t':p['time'],'c':p['stock_price']} for p in f['points'] if p['stock_price'] is not None],
            'kq':[{'t':p['time'],'c':p['kosdaq_price']} for p in f['points'] if p['kosdaq_price'] is not None]}
    else:
        out['reaction_chart'] = None
    return out


def api_article_detail(q, aid):
    s = load_settings()
    sym = s["symbol"]
    a = articles.find(sym, aid)
    if not a:
        raise LookupError("해당 기사를 찾을 수 없습니다.")
    e = articles.effect(sym, datetime.fromisoformat(a["ts"]), a["tone"], a["category"], a["title"])
    # Display the publication-time uncertainty in the detailed article too.
    e["time_quality"] = a.get("time_quality")
    conf = articles.confounders(sym, e, a["id"], a.get("story"))
    out = {"article": a, "effect": e, "confounders": conf}
    try:
        out["intraday"] = articles.intraday_reaction(sym, e, a.get("time_quality") != "check_time")
    except Exception:
        out["intraday"] = None
    out["intraday_coarse"] = None if out["intraday"] else _coarse_reaction(sym, e, a.get("time_quality") != "check_time")
    try:
        out["reaction_chart"] = _reaction_chart(sym, e, out["intraday"], out["intraday_coarse"])
    except Exception:
        out["reaction_chart"] = None
    m = e.get("minute")
    if m:
        rs = datetime.fromisoformat(m["reaction_start"])
        own = analysis.load_range(sym, rs - timedelta(minutes=30), rs + timedelta(minutes=120))
        own = own[own["ts"].dt.date == rs.date()] if not own.empty else own
        kq = analysis.load_range(KOSDAQ, rs - timedelta(minutes=30), rs + timedelta(minutes=120))
        kq = kq[kq["ts"].dt.date == rs.date()] if not kq.empty else kq
        out["bars"] = bars_payload(own)
        out["kosdaq"] = bars_payload(kq, full=False)
    d0 = e.get("reaction_day")
    if d0:
        lo = (date.fromisoformat(d0) - timedelta(days=14)).isoformat()
        hi = (date.fromisoformat(d0) + timedelta(days=14)).isoformat()
        with connect() as conn:
            out["daily_bars"] = [dict(r) for r in conn.execute(
                "SELECT date, close, volume FROM daily_bars WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date", (sym, lo, hi))]
            out["daily_kosdaq"] = [dict(r) for r in conn.execute(
                "SELECT date, close FROM daily_bars WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date", (KOSDAQ, lo, hi))]
    return align_observation(sym, out)


def _disc_effect(sym, r, stamp):
    """공시 반응: 기사와 같은 계산 (공시 자체가 원천이라 '먼저 나온 공시' 찾기는 안 함)."""
    key = ("disc", r["id"], r["ts"], r["untimed"], stamp)
    hit = _EFF_CACHE.get(key)
    if hit is None:
        hit = articles.effect(sym, datetime.fromisoformat(r["ts"]), 0, "공시", "", untimed=r["untimed"])
        if hit.get("verdict"):
            hit["verdict"] = hit["verdict"].replace("기사", "공시")
        if len(_EFF_CACHE) > 3000:
            _EFF_CACHE.clear()
        _EFF_CACHE[key] = hit
    return hit


def _disc_links(sym, stamp) -> dict:
    """공시 번호 → 같은 내용이라 공시 시각부터 잰 기사들 (기사의 '공시 먼저'를 거꾸로 이은 것)."""
    by_key = {}
    for a in articles.article_rows(sym):
        e = _effect_cached(sym, a, stamp)
        dsc = e.get("disclosure")
        if dsc:
            by_key.setdefault((dsc["ts"], dsc["title"]), []).append(
                {"id": a["id"], "ts": a["ts"], "title": a["title"], "office": a["office"], "kind": a.get("kind")})
    out = {}
    with connect() as conn:
        for r in conn.execute("SELECT no, ts, title FROM disclosures WHERE symbol=? AND ts IS NOT NULL", (sym,)):
            if (r["ts"], r["title"]) in by_key:
                out[f"d{r['no']}"] = by_key[(r["ts"], r["title"])]
    return out


def api_disclosures(_q):
    s = load_settings()
    sym = s["symbol"]
    stamp = _data_stamp(sym)
    links = _disc_links(sym, stamp)
    out = []
    for r in articles.disclosure_rows(sym):
        e = _disc_effect(sym, r, stamp)
        out.append({**{k: r.get(k) for k in ("id", "no", "date", "ts", "untimed", "title", "kind", "pblntf", "filer", "rm",
                                              "url")},
                    "effect": {k: e.get(k) for k in ("reaction_day", "verdict", "verdict_cls", "measure", "off_session")},
                    "links": [x["id"] for x in links.get(r["id"], [])]})
    from core import dart
    from core.eventstudy import summary_critical_values
    return {"items": out, "types": {**dart.TYPES, "K": "거래소 조치"}, "source": "dart" if dart.enabled() else "naver",
            "summary_critical_values": summary_critical_values(len(out))}


def api_disclosure_detail(_q, did):
    s = load_settings()
    sym = s["symbol"]
    r = next((x for x in articles.disclosure_rows(sym) if x["id"] == did), None)
    if not r:
        raise LookupError("해당 공시를 찾을 수 없습니다.")
    stamp = _data_stamp(sym)
    e = _disc_effect(sym, r, stamp)
    # 기사 상세 화면을 그대로 쓰도록 같은 모양으로 (구분 kind=disc)
    a = {"id": r["id"], "ts": r["ts"], "untimed": r["untimed"], "title": r["title"], "office": r["filer"] or "",
         "url": r["url"], "category": r["kind"] or "공시", "tone": 0, "kind": "disc", "pblntf": r.get("pblntf"),
         "outlets_total": 1, "related_titles": [], "links": _disc_links(sym, stamp).get(r["id"], [])}
    out = {"article": a, "effect": e, "confounders": []}
    try:
        out["intraday"] = articles.intraday_reaction(sym, e, not r["untimed"])
    except Exception:
        out["intraday"] = None
    out["intraday_coarse"] = None if out["intraday"] else _coarse_reaction(sym, e, not r["untimed"])
    try:
        out["reaction_chart"] = _reaction_chart(sym, e, out["intraday"], out["intraday_coarse"])
    except Exception:
        out["reaction_chart"] = None
    d0 = e.get("reaction_day")
    if d0:
        lo = (date.fromisoformat(d0) - timedelta(days=14)).isoformat()
        hi = (date.fromisoformat(d0) + timedelta(days=14)).isoformat()
        with connect() as conn:
            out["daily_bars"] = [dict(x) for x in conn.execute(
                "SELECT date, close, volume FROM daily_bars WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date", (sym, lo, hi))]
            out["daily_kosdaq"] = [dict(x) for x in conn.execute(
                "SELECT date, close FROM daily_bars WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date", (KOSDAQ, lo, hi))]
    m = e.get("minute")
    if m:
        rs = datetime.fromisoformat(m["reaction_start"])
        own = analysis.load_range(sym, rs - timedelta(minutes=30), rs + timedelta(minutes=120))
        own = own[own["ts"].dt.date == rs.date()] if not own.empty else own
        kq = analysis.load_range(KOSDAQ, rs - timedelta(minutes=30), rs + timedelta(minutes=120))
        kq = kq[kq["ts"].dt.date == rs.date()] if not kq.empty else kq
        out["bars"] = bars_payload(own)
        out["kosdaq"] = bars_payload(kq, full=False)
    return align_observation(sym, out)


def reaction_row(d: dict, item: dict | None = None) -> dict:
    """기사·공시 상세 하나 → '뉴스 반응'용 한 줄: 공개 시각을 0분으로 맞춘 주가 경로(이 종목·KOSDAQ, 기준값 대비),
    +N분 등락(이 종목·KOSDAQ), 어떤 봉으로 쟀는지."""
    a, e = d["article"], d.get("effect") or {}
    item = item or {}
    iw, ic, rc = d.get("intraday"), d.get("intraday_coarse"), d.get("reaction_chart")
    w = iw or ic
    publication = a.get("own_ts") or a["ts"]
    disclosure = e.get("disclosure") or {}
    analysis_ts = e.get("base_ts") or disclosure.get("ts") or a["ts"]
    row = {"id": a["id"], "ts": a.get("own_ts") or a["ts"], "title": a["title"], "office": a.get("office") or "",
           "kind": a.get("kind") or item.get("kind"), "category": a.get("category") or item.get("category"),
           "topic": item.get("topic") or a.get("topic"), "pblntf": a.get("pblntf"),
           "time_quality": a.get("time_quality") or item.get("time_quality"), "untimed": bool(a.get("untimed")),
           "reaction_day": e.get("reaction_day"), "interval": (1 if iw else ic.get("interval")) if w else None,
           "mode": w.get("kind") if w else None, "start": w.get("start") if w else None, "steps": {}, "path": None}
    row["analysis_ts"] = analysis_ts
    row["basis_reason"] = "disclosure" if disclosure.get("ts") else "story" if analysis_ts != publication else "publication"
    # Link only explicitly associated disclosures/stories, never all news on the same day.
    if disclosure.get("ts"):
        row["event_key"] = f"disclosure:{disclosure['ts']}|{disclosure.get('title', '')}"
    elif row["kind"] == "disc":
        row["event_key"] = f"disclosure:{a['ts']}|{a['title']}"
    else:
        row["event_key"] = f"story:{a.get('story') or a['id']}"
    for r in (w or {}).get("rows") or []:
        row["steps"][str(r["minutes"])] = {"own": r.get("own"), "kq": r.get("kosdaq")}
    if rc and rc.get("base_own") and rc.get("start"):
        t0 = datetime.fromisoformat(rc["start"])

        def path(rows, base):
            out = []
            for x in rows:
                m = round((datetime.fromisoformat(x["t"]) - t0).total_seconds() / 60)
                if base and -30 <= m <= 150:
                    out.append([m, x["c"] / base - 1])
            return out
        row["path"] = {"own": path(rc["own"], rc["base_own"]), "kq": path(rc.get("kq") or [], rc.get("base_kq"))}
    row["kq_interval"] = (ic or {}).get("kq_interval") if not iw else 1
    return row


def reactions_from(art_items: list, art_details: dict, disc_items: list, disc_details: dict) -> dict:
    rows = []
    for it in art_items:
        d = art_details.get(it["id"])
        if d and not it.get("hidden"):
            rows.append(reaction_row(d, it))
    for it in disc_items:
        d = disc_details.get(it["id"])
        if d:
            rows.append(reaction_row(d, {**it, "kind": "disc"}))
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    return {"items": rows}


def api_reactions(_q):
    """뉴스 반응 (서버 화면용: 기사·공시 상세를 모두 계산하므로 무거움. 사이트는 자동 실행 때 미리 만든 값)."""
    arts = (api_articles({}).get("items") or [])
    discs = (api_disclosures({}).get("items") or [])
    ad = {a["id"]: api_article_detail({}, a["id"]) for a in arts}
    dd = {x["id"]: api_disclosure_detail({}, x["id"]) for x in discs}
    result = reactions_from(arts, ad, discs, dd)
    from core.observatory import build
    with connect() as conn:
        build(conn, load_settings()['symbol'], result['items'])
    return result


def api_observatory(_q, eid=None):
    from core.observatory import build
    rows = api_reactions({})['items']
    with connect() as conn:
        details, aliases = build(conn, load_settings()['symbol'], rows)
    if eid:
        eid = aliases.get(eid, eid)
        if eid not in details:
            raise LookupError('사건 자료 없음')
        return details[eid]
    return {'items': [observation_summary(d) for d in details.values()], 'aliases':aliases}


def observation_summary(d):
    return {k:d[k] for k in ('event_id','title','event_kind','business_tags','publisher','partner','first_public_at',
            'time_source','time_status','followup_candidate')} | {
        'steps':(d['observation'].get('intraday') or {}).get('steps',{}),
        'post5':d['observation'].get('windows',{}).get('5',{}).get('post',{}),
        'status':d['observation']['status'],'notes_count':len(d['notes']), 'exposures':len(d['members']),
        'member_ids':[str(m.get('id')) for m in d['members']]}   # 유사 사건을 뉴스 목록의 주제 묶음으로 합칠 때 씀


def api_article_hide(aid, hidden: bool):
    with connect() as conn:
        conn.execute("UPDATE articles SET hidden=? WHERE id=? OR story=?", (1 if hidden else 0, aid, aid))
    return {"ok": True}


def api_share(_q):
    return share.status()


def api_cloud(_q):
    return cloud.status()


GET_ROUTES = [
    (re.compile(r"^/api/status$"), api_status),
    (re.compile(r"^/api/intraday$"), api_intraday),
    (re.compile(r"^/api/events$"), api_events),
    (re.compile(r"^/api/daily$"), api_daily),
    (re.compile(r"^/api/coarse$"), api_coarse),
    (re.compile(r"^/api/events/(\d+)$"), api_event_detail),
    (re.compile(r"^/api/day/(\d{4}-\d{2}-\d{2})$"), api_day_detail),
    (re.compile(r"^/api/pr$"), api_pr_list),
    (re.compile(r"^/api/pr/(\d+)$"), api_pr_detail),
    (re.compile(r"^/api/articles$"), api_articles),
    (re.compile(r"^/api/articles/(pr\d+|\d+)$"), api_article_detail),
    (re.compile(r"^/api/disclosures$"), api_disclosures),
    (re.compile(r"^/api/disclosures/(dK?\d+)$"), api_disclosure_detail),
    (re.compile(r"^/api/reactions$"), api_reactions),
    (re.compile(r"^/api/observatory$"), api_observatory),
    (re.compile(r"^/api/observatory/(ne-[a-f0-9]+)$"), api_observatory),
    (re.compile(r"^/api/share$"), api_share),
    (re.compile(r"^/api/cloud$"), api_cloud),
]


# ------------------------------------------------------------------ server

PORT = 8501


def _log_share(msg):
    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        with open(ROOT / "logs" / "ui.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "StockMonitor"

    def log_message(self, fmt, *args):  # 요청마다 기록하지 않음
        pass

    # ---- 공유 링크로 들어온 요청인지 (Cloudflare 터널은 이 PC 안에서 접속하므로 헤더로 구분)
    def _external(self) -> bool:
        if self.headers.get("Cf-Connecting-Ip") or self.headers.get("Cf-Ray") or self.headers.get("X-Forwarded-For"):
            return True
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host not in ("localhost", "127.0.0.1", "")

    def _cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "go_share":
                return v
        return None

    def _gate(self, path: str):
        """외부 요청: 링크 열쇠 확인. 통과하면 None, 막으면 응답을 보내고 True."""
        tok = share.token()
        m = re.match(r"^/s/([A-Za-z0-9_\-]+)/?$", path)
        if m:
            if tok and secrets.compare_digest(m.group(1), tok):
                self.send_response(302)
                self.send_header("Set-Cookie", f"go_share={tok}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=86400")
                self.send_header("Location", "/")
                self.end_headers()
                return True
            return self._denied()
        c = self._cookie_token()
        if tok and c and secrets.compare_digest(c, tok):
            return None
        return self._denied()

    def _denied(self):
        body = ("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>링크 만료</title><body style='font-family:sans-serif;padding:40px;color:#0c1a33'>"
                "<h2>이 공유 링크는 더 이상 열리지 않습니다.</h2>"
                "<p>공유가 중지되었거나 새 링크로 바뀌었습니다. 링크를 보낸 분께 새 링크를 요청해 주세요.</p></body>").encode("utf-8")
        self._send(403, body, "text/html; charset=utf-8")
        return True

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(clean(obj), ensure_ascii=False).encode("utf-8"))

    def _error(self, e: Exception):
        if isinstance(e, LookupError):
            self._json({"error": str(e)}, 404)
        elif isinstance(e, ValueError):
            self._json({"error": str(e)}, 400)
        else:
            traceback.print_exc()
            self._json({"error": f"서버 오류: {type(e).__name__}: {e}"}, 500)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        ext = self._external()
        if ext and self._gate(url.path):
            return
        q["_viewer"] = "shared" if ext else "local"
        try:
            if url.path == "/api/events.csv":
                name = f"events_{datetime.now():%Y%m%d}.csv"
                return self._send(200, api_events_csv(q), "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            if ext and url.path in ("/api/share", "/api/cloud"):
                return self._json({"error": "공유 링크로는 볼 수 없는 메뉴입니다."}, 403)
            for pat, fn in GET_ROUTES:
                m = pat.match(url.path)
                if m:
                    args = [int(g) if g.isdigit() and fn is not api_article_detail else g for g in m.groups()]
                    return self._json(fn(q, *args))
            if url.path.startswith("/api/"):
                return self._json({"error": "없는 주소입니다."}, 404)
            return self._static(url.path)
        except Exception as e:
            self._error(e)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

    def do_POST(self):
        if self._external():
            return self._json({"error": "공유 링크로는 보기만 할 수 있습니다."}, 403)
        try:
            if self.path == '/api/inbox':
                from cloud_job import apply_inbox
                from core.ghinbox import KNOWN
                import logging
                item = self._body()
                if item.get('type') not in KNOWN:
                    raise ValueError('알 수 없는 요청')
                apply_inbox(item, logging.getLogger('local-inbox'))
                return self._json({'ok':True})
            m = re.match(r"^/api/events/(\d+)/analyze$", self.path)
            if m:
                return self._json(api_event_analyze(int(m.group(1))))
            if self.path == "/api/pr":
                return self._json(api_pr_create(self._body()), 201)
            m = re.match(r"^/api/articles/(\d+)/(hide|show)$", self.path)
            if m:
                return self._json(api_article_hide(m.group(1), m.group(2) == "hide"))
            if self.path == "/api/share/start":
                return self._json(share.start(PORT, _log_share))
            if self.path == "/api/share/stop":
                return self._json(share.stop())
            if self.path.startswith("/api/cloud/"):
                return self._json(self._cloud(self.path[len("/api/cloud/"):], self._body()))
            self._json({"error": "없는 주소입니다."}, 404)
        except Exception as e:
            self._error(e)

    def _cloud(self, action, body):
        try:
            if action == "setup":
                st = cloud.setup(body.get("token"), body.get("password"), _log_share)
                cloud.publish_async(_log_share)
                return st
            if action == "publish":
                if not cloud.enabled():
                    raise ValueError("먼저 사이트를 연결해 주세요.")
                cloud.publish_async(_log_share)
                return {**cloud.status(), "busy": True}
            if action == "password":
                return cloud.change_password(body.get("password"))
            if action == "disconnect":
                return cloud.disconnect(bool(body.get("delete_remote")))
        except cloud.CloudError as e:
            raise ValueError(str(e))
        raise LookupError("없는 주소입니다.")

    def do_DELETE(self):
        if self._external():
            return self._json({"error": "공유 링크로는 보기만 할 수 있습니다."}, 403)
        try:
            m = re.match(r"^/api/pr/(\d+)$", self.path)
            if m:
                return self._json(api_pr_delete(int(m.group(1))))
            self._json({"error": "없는 주소입니다."}, 404)
        except Exception as e:
            self._error(e)

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC / rel).resolve()
        if STATIC.resolve() not in target.parents and target != STATIC.resolve() or not target.is_file():
            target = STATIC / "index.html"
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        body = target.read_bytes()
        cache = {"Cache-Control": "max-age=86400"} if "/vendor/" in str(target) or target.suffix == ".woff2" else None
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", (cache or {}).get("Cache-Control", "no-cache"))
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8501)
    args = ap.parse_args()
    global PORT
    PORT = args.port
    init_db()
    share.stop()   # 이전 실행에서 남은 공유는 끔 (새로 켤 때 새 링크)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print(f"화면 서버 시작: http://localhost:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
