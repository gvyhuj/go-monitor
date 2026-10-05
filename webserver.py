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

from core import analysis, articles, causes, cloud, feed, macro, naver, share
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
        cj = None
        if r.get("cause_json"):
            try:
                cj = json.loads(r["cause_json"])
            except ValueError:
                cj = None
        top = (cj or {}).get("candidates") or []
        daily = r["event_type"] == "daily"
        out.append({
            "cause_headline": (cj or {}).get("headline"),
            "cause_label": top[0].get("label") if top else ("원인 미확인" if cj else None),
            "cause_confidence": (cj or {}).get("confidence") or (top[0].get("confidence") if top else None),
            "cause_summary": (cj or {}).get("summary_line"),
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
    dv = data["value"] if data else ""
    return {
        "symbol": sym, "name": s.get("display_name", sym),
        "quote": state_json(f"quote:{sym}"), "kosdaq": state_json(f"quote:{KOSDAQ}"),
        "engine": engine_state(s),
        "data": {"ok": not dv.startswith("error"), "message": dv[7:] if dv.startswith("error") else dv[4:],
                 "last_ok": data_ok["value"] if data_ok else None},
        "last_bar_ts": last_bar, "first_bar_ts": first_bar, "bars_count": n_bars, "events_count": n_events,
        "viewer": viewer, "share": share.status() if viewer == "local" else None,
        "cloud": cloud.status() if viewer == "local" else None,
        "rules": {
            "move_pct": s.get("event_move_pct", 5.0), "big_pct": s.get("event_big_pct", 10.0),
            "window_min": s.get("event_window_minutes", 30), "hold_min": s.get("event_hold_minutes", 5),
            "min_value_eok": round(float(s.get("event_min_value_won", 5e7)) / 1e8, 2),
        },
    }


def api_intraday(q):
    s = load_settings()
    sym = s["symbol"]
    with connect() as conn:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT substr(ts,1,10) d FROM minute_bars WHERE symbol=? ORDER BY d DESC LIMIT 15", (sym,))]
    if not dates:
        return {"date": None, "dates": [], "bars": [], "kosdaq": [], "events": [], "prev_close": None}
    d = q.get("date") or dates[0]
    day0 = datetime.fromisoformat(d + "T00:00")
    own = analysis.load_range(sym, day0, day0 + timedelta(hours=23, minutes=59))
    kq = analysis.load_range(KOSDAQ, day0, day0 + timedelta(hours=23, minutes=59))
    prev = analysis.load_range(sym, day0 - timedelta(days=10), day0 - timedelta(minutes=1))
    prev_close = float(prev["close"].iloc[-1]) if not prev.empty else None
    quote = state_json(f"quote:{sym}")
    if d == dates[0] and quote and quote.get("price") is not None and quote.get("change") is not None:
        prev_close = quote["price"] - quote["change"]   # 네이버 기준 전일 종가
    return {"date": d, "dates": dates, "bars": bars_payload(own), "kosdaq": bars_payload(kq, full=False),
            "prev_close": prev_close, "events": event_rows(sym, d, d, None)}


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


def api_event_detail(q, eid):
    s = load_settings()
    sym = s["symbol"]
    with connect() as conn:
        r = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    if not r:
        raise LookupError("해당 이상변동을 찾을 수 없습니다.")
    day = r["start_ts"][:10]
    ev = next((e for e in event_rows(sym, day, day, None) if e["id"] == eid), None)
    t0, t1 = datetime.fromisoformat(r["start_ts"]), datetime.fromisoformat(r["last_ts"])
    own = analysis.load_range(sym, t0 - timedelta(minutes=30), t1 + timedelta(minutes=60))
    kq = analysis.load_range(KOSDAQ, t0 - timedelta(minutes=30), t1 + timedelta(minutes=60))
    rel = []
    cause = causes.stored(eid)
    if cause:
        for x in cause.get("related") or []:
            df = analysis.load_range(x["code"], t0 - timedelta(minutes=30), t1 + timedelta(minutes=60))
            if not df.empty:
                rel.append({"code": x["code"], "name": x["name"], "change": x.get("change"),
                            "bars": bars_payload(df, full=False)})
    return {"event": ev, "bars": bars_payload(own), "kosdaq": bars_payload(kq, full=False),
            "cause": cause, "related_bars": rel,
            "followups": causes.followup_filings(sym, day)}


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
                    "대형" if e.get("tier") == "대형" else "", p(e["peak_return_5m"]), p(e["kosdaq_change"]),
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
    key = (a["id"], a["ts"], a["category"], a["tone"], stamp)
    hit = _EFF_CACHE.get(key)
    if hit is None:
        hit = articles.effect(sym, datetime.fromisoformat(a["ts"]), a["tone"], a["category"])
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
                                              "outlets_total", "manual", "hidden")},
                    "n_related": len(a.get("related_titles") or []),
                    "effect": {k: e.get(k) for k in ("reaction_day", "verdict", "verdict_cls", "measure")},
                    "d0_excess": ((e.get("daily") or {}).get("excess") or {}).get("d0"),
                    "m60_excess": ((e.get("minute") or {}).get("excess") or {}).get("60")})
    summary = articles.summarize(out) if not limit else None
    with connect() as conn:
        first = conn.execute("SELECT MIN(date) FROM daily_bars WHERE symbol=?", (sym,)).fetchone()[0]
    return {"items": out, "summary": summary, "categories": articles.CATEGORIES, "daily_since": first}


def api_article_detail(q, aid):
    s = load_settings()
    sym = s["symbol"]
    a = articles.find(sym, aid)
    if not a:
        raise LookupError("해당 기사를 찾을 수 없습니다.")
    e = articles.effect(sym, datetime.fromisoformat(a["ts"]), a["tone"], a["category"])
    conf = articles.confounders(sym, e, a["id"], a.get("story"))
    out = {"article": a, "effect": e, "confounders": conf}
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
    return out


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
    (re.compile(r"^/api/events/(\d+)$"), api_event_detail),
    (re.compile(r"^/api/pr$"), api_pr_list),
    (re.compile(r"^/api/pr/(\d+)$"), api_pr_detail),
    (re.compile(r"^/api/articles$"), api_articles),
    (re.compile(r"^/api/articles/(pr\d+|\d+)$"), api_article_detail),
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
