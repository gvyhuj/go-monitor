from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .db import connect, get_state, set_state

ROOT = Path(__file__).resolve().parents[1]


# 종목코드·회사 이름·비교 종목·회사 일정처럼 회사를 알아볼 수 있는 설정은 공개 저장소에 두지 않고
# 암호화된 기록(DB) 안의 'private_config'에 둠. (또는 비밀값 GO_PRIVATE_CONFIG, 로컬 private_settings.json)
PRIVATE_KEYS = ("symbol", "display_name", "calendar", "peer_groups", "_peer_groups_note", "peer_group_links",
                "factor_etfs", "extra_peers", "driver_queries")
_private_cache: dict | None = None


def _private() -> dict:
    global _private_cache
    if _private_cache:
        return _private_cache
    out = {}
    env = os.environ.get("GO_PRIVATE_CONFIG")
    if env:
        try:
            out = json.loads(env)
        except ValueError:
            out = {}
    if not out and (ROOT / "private_settings.json").exists():
        try:
            out = json.loads((ROOT / "private_settings.json").read_text(encoding="utf-8"))
        except ValueError:
            out = {}
    if not out:
        try:
            from .db import get_state
            st = get_state("private_config")
            out = json.loads(st["value"]) if st else {}
        except Exception:      # DB가 아직 없을 때
            out = {}
    if out:
        _private_cache = out
    return out


def save_private_from_file() -> bool:
    """settings.json에 아직 회사 설정이 들어 있으면 암호화된 기록(DB)으로 옮겨 둠 (한 번)."""
    global _private_cache
    raw = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    if not raw.get("symbol"):
        return False
    from .db import set_state
    priv = {k: raw[k] for k in PRIVATE_KEYS if k in raw}
    set_state("private_config", json.dumps(priv, ensure_ascii=False))
    _private_cache = priv
    return True


def load_settings() -> dict:
    s = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    for k, v in _private().items():
        if v not in (None, "", [], {}) or not s.get(k):
            s[k] = v
    return s




def robust_z(value: float, samples: list[float]) -> float:
    """중앙값·MAD 기반 이상도. 거래가 뜸해 MAD가 0이면 평균절대편차로 대체."""
    vals = [float(x) for x in samples if x is not None]
    if len(vals) < 5:
        return 0.0
    med = statistics.median(vals)
    devs = [abs(x - med) for x in vals]
    scale = statistics.median(devs) / 0.6745
    if scale < 1e-12:
        scale = (sum(devs) / len(devs)) * 1.2533
    if scale < 1e-12:
        return 0.0
    return (value - med) / scale


def load_bars(symbol: str, since: str | None = None) -> pd.DataFrame:
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT symbol, ts, open, high, low, close, volume, source "
            "FROM minute_bars WHERE symbol=? AND ts >= ? ORDER BY ts",
            conn,
            params=(symbol, since or ""),
        )
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"])
    df["date"] = df["ts"].dt.date
    df["minute"] = df["ts"].dt.strftime("%H:%M")
    return df


def _fill_minute_grid(day: pd.DataFrame) -> pd.DataFrame:
    """체결이 없던 분을 채움 (종가는 직전 값 유지, 거래량 0).

    거래가 뜸한 종목은 빈 분이 많아서, 채우지 않으면 '5분 변동'이
    실제로는 10~20분 변동이 되어 버립니다.
    """
    day = day.sort_values("ts").drop_duplicates("ts").set_index("ts")
    start = day.index[0].normalize() + pd.Timedelta(hours=9)
    grid = pd.date_range(min(start, day.index[0]), day.index[-1], freq="1min")
    out = day.reindex(grid)
    out["close"] = out["close"].ffill().bfill()
    for col in ("open", "high", "low"):
        out[col] = out[col].fillna(out["close"])
    out["volume"] = out["volume"].fillna(0)
    out["symbol"] = out["symbol"].ffill().bfill()
    out["source"] = out["source"].fillna("filled")
    out.index.name = "ts"
    return out.reset_index()


def _feature_rows(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for _, day in df.groupby(df["ts"].dt.date, sort=True):
        day = _fill_minute_grid(day)
        day["date"] = day["ts"].dt.date
        day["minute"] = day["ts"].dt.strftime("%H:%M")
        day["ret5"] = day["close"].pct_change(5)
        day["ret15"] = day["close"].pct_change(15)
        day["vol5"] = day["volume"].rolling(5).sum()
        out.append(day)
    return pd.concat(out, ignore_index=True) if out else df


DETECTOR_VERSION = "3"   # 기준이 바뀌면 올림 → 새 기준으로 다시 찾음 (2→3: 관찰 단계 추가, 기존 기록은 유지)


def _ensure_version(symbol: str, log=None):
    st = get_state("detector_version")
    if st and st.get("value") == DETECTOR_VERSION:
        return False
    if st and st.get("value") == "2" and DETECTOR_VERSION == "3":
        with connect() as conn:                 # 기존 급등·급락은 그대로 두고, 받아 둔 기간을 다시 훑어 '관찰'만 더함
            conn.execute("DELETE FROM system_state WHERE key=?", (_state_key(symbol),))
        set_state("detector_version", DETECTOR_VERSION)
        if log:
            log("관찰 단계(3~5%) 추가: 받아 둔 기간을 다시 훑습니다")
        return True
    with connect() as conn:
        n = conn.execute("DELETE FROM events WHERE symbol=?", (symbol,)).rowcount
        conn.execute("DELETE FROM system_state WHERE key=?", (_state_key(symbol),))
        try:
            conn.execute("DELETE FROM site_items WHERE key LIKE 'event:%'")
        except Exception:
            pass
    set_state("detector_version", DETECTOR_VERSION)
    if log:
        log(f"급등·급락 기준 변경: 예전 기록 {n}건을 지우고 새 기준으로 다시 찾습니다")
    return True


def _state_key(symbol: str) -> str:
    return f"detector_day:{symbol}"


def _prev_close(symbol: str, day: str):
    """전 거래일 종가: 일봉 → 없으면 분봉 마지막 값."""
    with connect() as conn:
        r = conn.execute("SELECT close FROM daily_bars WHERE symbol=? AND date<? ORDER BY date DESC LIMIT 1",
                         (symbol, day)).fetchone()
        m = conn.execute("SELECT substr(ts,1,10) d, close FROM minute_bars WHERE symbol=? AND ts<? "
                         "ORDER BY ts DESC LIMIT 1", (symbol, day + "T00:00")).fetchone()
        rd = conn.execute("SELECT date FROM daily_bars WHERE symbol=? AND date<? ORDER BY date DESC LIMIT 1",
                          (symbol, day)).fetchone()
    # 더 최근 날짜의 값을 씀 (일봉이 아직 안 들어온 날 대비)
    if m and (not rd or m["d"] > rd["date"]):
        return float(m["close"])
    if r:
        return float(r["close"])
    return float(m["close"]) if m else None


def _fmt_won(v):
    return f"{v/1e8:,.1f}억 원" if v >= 1e7 else f"{v/1e4:,.0f}만 원"


def _upsert(conn, symbol, ev) -> bool:
    """같은 날·같은 방향·같은 종류에서 시간이 겹치면 갱신, 아니면 새로 추가. 새로 추가했으면 True."""
    day = ev["start_ts"][:10]
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE symbol=? AND event_type=? AND direction=? AND substr(start_ts,1,10)=?",
        (symbol, ev["event_type"], ev["direction"], day))]
    hit = None
    for r in rows:
        if ev["event_type"] == "daily" or not (ev["last_ts"] < r["start_ts"] or ev["start_ts"] > r["last_ts"]):
            hit = r
            break
    cols = ["start_ts", "last_ts", "peak_ts", "peak_return_5m", "return_5m", "detection_rule", "trade_value", "tier",
            "status"]
    if hit:
        changed = any((hit.get(c) != ev.get(c)) for c in cols)
        if changed:
            conn.execute("UPDATE events SET " + ", ".join(f"{c}=?" for c in cols) + ", updated_at=CURRENT_TIMESTAMP "
                         "WHERE id=?", [ev.get(c) for c in cols] + [hit["id"]])
            # 구간이 길어졌으면 원인을 다시 분석
            if hit["last_ts"] != ev["last_ts"] or hit["start_ts"] != ev["start_ts"]:
                conn.execute("UPDATE events SET cause_stage=NULL WHERE id=? AND cause_stage!='최종'", (hit["id"],))
        return False
    conn.execute(
        "INSERT INTO events(symbol, start_ts, last_ts, event_type, direction, return_5m, peak_return_5m, peak_ts, "
        "status, detection_rule, trade_value, tier) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (symbol, ev["start_ts"], ev["last_ts"], ev["event_type"], ev["direction"], ev["return_5m"],
         ev["peak_return_5m"], ev["peak_ts"], ev["status"], ev["detection_rule"], ev["trade_value"], ev["tier"]))
    return True


def intraday_events(day: pd.DataFrame, prev_close, s: dict, day_over: bool) -> list[dict]:
    """하루 1분봉에서 '30분 안에 ±N% 움직이고, 그 가격이 몇 분 이상 유지되고, 거래대금이 충분한' 구간을 찾음.

    - 체결 한두 건으로 튄 가격(얇은 호가)을 거르기 위해 3분 중앙값 가격을 씀
    - 장 시작 직후 움직임도 잡도록 전일 종가를 09시 직전 값으로 넣음
    """
    main_th = float(s.get("event_move_pct", 5.0)) / 100
    th = min(main_th, float(s.get("event_watch_pct", main_th * 100)) / 100)   # 관찰 단계부터 찾음
    big = float(s.get("event_big_pct", 10.0)) / 100
    win = int(s.get("event_window_minutes", 30))
    hold = int(s.get("event_hold_minutes", 5))
    min_val = float(s.get("event_min_value_won", 5e7))
    skip_from = s.get("skip_detection_from", "15:20")
    # 실제 체결이 있던 분끼리 앞뒤 3개 중앙값 → 체결 한 건으로 튄 가격은 빠짐. 그 뒤 빈 분은 직전 값으로 채움
    real = day.sort_values("ts").drop_duplicates("ts")
    real = real[real["volume"] > 0].copy()
    if real.empty:
        return []
    real["robust"] = real["close"].astype(float).rolling(3, center=True, min_periods=1).median()
    g = _fill_minute_grid(day)
    g = g.merge(real[["ts", "robust"]], on="ts", how="left")
    g["robust"] = g["robust"].ffill().bfill()
    g = g[g["ts"].dt.strftime("%H:%M") < skip_from].reset_index(drop=True) if skip_from else g
    if g.empty:
        return []
    ts = list(g["ts"])
    close = g["close"].astype(float).tolist()
    value = (g["close"].astype(float) * g["volume"].astype(float)).tolist()
    p = g["robust"].astype(float).tolist()
    if prev_close:
        ts = [ts[0] - pd.Timedelta(minutes=1)] + ts
        p = [float(prev_close)] + p
        close = [float(prev_close)] + close
        value = [0.0] + value
    n = len(p)
    trig = []   # (i, start_idx, direction)
    for i in range(1, n):
        lo = max(0, i - win)
        seg = p[lo:i + 1]
        jmin = lo + seg.index(min(seg))
        jmax = lo + seg.index(max(seg))
        if p[jmin] and p[i] / p[jmin] - 1 >= th:
            trig.append((i, jmin, "up"))
        elif p[jmax] and p[i] / p[jmax] - 1 <= -th:
            trig.append((i, jmax, "down"))
    # 이어지는 신호를 하나의 움직임으로 묶음
    groups = []
    for i, j, d in trig:
        if groups and groups[-1]["dir"] == d and i <= groups[-1]["end"] + 5:
            g0 = groups[-1]
            g0["end"] = i
            g0["start"] = min(g0["start"], j)
        else:
            groups.append({"dir": d, "start": j, "first": i, "end": i})
    out = []
    for gr in groups:
        a, f, e, d = gr["start"], gr["first"], gr["end"], gr["dir"]
        base = p[a]
        seg = p[a:min(n, e + 1)]
        k = a + (seg.index(max(seg)) if d == "up" else seg.index(min(seg)))
        move = p[k] / base - 1
        # 유지: 처음 기준을 넘은 뒤 10분 안에 '움직임의 60% 이상'을 지킨 분이 hold분 이상
        keep_lvl = base * (1 + 0.6 * th) if d == "up" else base * (1 - 0.6 * th)
        after = p[f:min(n, f + 10)]
        kept = sum(1 for x in after if (x >= keep_lvl if d == "up" else x <= keep_lvl))
        if kept < hold:
            if not day_over and len(after) < hold:
                continue          # 아직 진행 중: 다음 실행 때 다시 판단
            continue              # 금방 되돌아감 (한두 건 주문으로 튄 경우)
        val = sum(value[a + 1:e + 1])
        if val < min_val:
            continue
        start_label = "전일 종가" if (prev_close and a == 0) else ts[a].strftime("%H:%M")
        rule = (f"{win}분 안에 {move*100:+.1f}% ({start_label} {base:,.0f}원 → {ts[k]:%H:%M} {p[k]:,.0f}원), "
                f"{kept}분 이상 유지, 구간 거래대금 {_fmt_won(val)}")
        st = ts[a + 1] if (prev_close and a == 0) else ts[a]
        out.append({
            "event_type": "intraday", "direction": d,
            "start_ts": st.isoformat(timespec="minutes")[:16], "last_ts": ts[e].isoformat(timespec="minutes")[:16],
            "peak_ts": ts[k].isoformat(timespec="minutes")[:16], "peak_return_5m": move, "return_5m": move,
            "trade_value": val, "tier": _tier(move, main_th, big),
            "detection_rule": rule, "status": "closed" if (day_over or e < n - 15) else "open",
        })
    return out


def _tier(move: float, main_th: float, big: float) -> str:
    """대형(±10% 이상) / 일반(±5% 이상, 급등·급락) / 관찰(±3~5%, 기록만 하고 알림은 보내지 않음)."""
    return "대형" if abs(move) >= big else "일반" if abs(move) >= main_th else "관찰"


def daily_event(symbol: str, day: str, s: dict, bars: pd.DataFrame | None):
    main_th = float(s.get("event_move_pct", 5.0)) / 100
    th = min(main_th, float(s.get("event_watch_pct", main_th * 100)) / 100)
    big = float(s.get("event_big_pct", 10.0)) / 100
    pc = _prev_close(symbol, day)
    with connect() as conn:
        dr = conn.execute("SELECT * FROM daily_bars WHERE symbol=? AND date=?", (symbol, day)).fetchone()
    close = hi = lo = None
    if dr:                                  # 거래소 확정 일봉이 있으면 그것을 씀
        close, hi, lo = float(dr["close"]), float(dr["high"] or dr["close"]), float(dr["low"] or dr["close"])
    elif bars is not None and not bars.empty and bars["ts"].iloc[-1].strftime("%H:%M") >= "15:30":
        close = float(bars["close"].iloc[-1])
        hi, lo = float(bars["high"].max()), float(bars["low"].min())
    if not pc or not close:
        return None
    ret = close / pc - 1
    if abs(ret) < th:
        return None
    d = "up" if ret > 0 else "down"
    ext = (hi / pc - 1) if d == "up" else (lo / pc - 1)
    peak_ts = f"{day}T15:30"
    if bars is not None and not bars.empty:
        idx = bars["high"].idxmax() if d == "up" else bars["low"].idxmin()
        peak_ts = bars.loc[idx, "ts"].isoformat(timespec="minutes")[:16]
    rule = (f"종가 {ret*100:+.2f}% (전일 {pc:,.0f}원 → {close:,.0f}원), 장중 {'최고' if d == 'up' else '최저'} {ext*100:+.2f}%")
    val = float((bars["close"] * bars["volume"]).sum()) if bars is not None and not bars.empty else \
        (float(dr["close"]) * float(dr["volume"] or 0) if dr else 0.0)
    return {"event_type": "daily", "direction": d, "start_ts": f"{day}T09:00", "last_ts": f"{day}T15:30",
            "peak_ts": peak_ts, "peak_return_5m": ret, "return_5m": ext, "trade_value": val,
            "tier": _tier(ret, main_th, big), "detection_rule": rule, "status": "closed"}


def requested_event(symbol: str, day: str, t_from: str | None = None, t_to: str | None = None) -> dict:
    """사용자가 사이트에서 '이 날(이 시간대) 왜 움직였나' 분석을 요청했을 때 만드는 기록 (단계: 요청).

    시간대가 없으면 하루 기준(전일 종가 → 그날 종가, 장중이면 지금 가격), 있으면 그 시간대의 움직임.
    """
    pc = _prev_close(symbol, day)
    with connect() as conn:
        dr = conn.execute("SELECT close, volume FROM daily_bars WHERE symbol=? AND date=?", (symbol, day)).fetchone()
        mins = [dict(r) for r in conn.execute(
            "SELECT ts, close, volume FROM minute_bars WHERE symbol=? AND substr(ts,1,10)=? ORDER BY ts", (symbol, day))]
    if t_from or t_to:
        if not mins:
            raise ValueError(f"{day} 분봉 기록이 없어 시간대 분석을 할 수 없습니다 (분봉은 최근 약 4개월만 보관). 시간대를 비우면 하루 기준으로 분석합니다.")
        a = f"{day}T{t_from or '09:00'}"
        b = f"{day}T{t_to or '15:30'}"
        before = [m for m in mins if m["ts"] <= a]
        inside = [m for m in mins if a < m["ts"] <= b]
        if not inside:
            raise ValueError("그 시간대에는 거래 기록이 없습니다.")
        base = before[-1]["close"] if before else (pc if (t_from or "09:00") <= "09:00" else inside[0]["close"])
        end = inside[-1]["close"]
        move = end / base - 1 if base else 0.0
        val = sum((m["close"] or 0) * (m["volume"] or 0) for m in inside)
        st = inside[0]["ts"][:16] if not before else a
        ev = {"event_type": "intraday", "start_ts": st, "last_ts": inside[-1]["ts"][:16],
              "detection_rule": f"직접 분석 요청: {t_from or '09:00'}~{t_to or '15:30'} {move*100:+.2f}% ({base:,.0f}원 → {end:,.0f}원)"}
    else:
        close = float(dr["close"]) if dr else (float(mins[-1]["close"]) if mins else None)
        if not pc or not close:
            raise ValueError(f"{day}에는 거래 기록이 없습니다 (휴장일이거나 기록 기간 밖입니다).")
        move = close / pc - 1
        val = float(close * (dr["volume"] or 0)) if dr else sum((m["close"] or 0) * (m["volume"] or 0) for m in mins)
        ev = {"event_type": "daily", "start_ts": f"{day}T09:00", "last_ts": f"{day}T15:30",
              "detection_rule": f"직접 분석 요청: 하루 {move*100:+.2f}% (전일 {pc:,.0f}원 → {close:,.0f}원)"}
    ev.update({"direction": "up" if move >= 0 else "down", "peak_ts": ev["last_ts"], "peak_return_5m": move,
               "return_5m": move, "trade_value": val, "tier": "요청", "status": "closed"})
    with connect() as conn:
        same = conn.execute("SELECT id FROM events WHERE symbol=? AND event_type=? AND substr(start_ts,1,10)=? "
                            "AND start_ts<=? AND last_ts>=?", (symbol, ev["event_type"], day, ev["last_ts"], ev["start_ts"])).fetchone()
        if same:                           # 이미 기록된 움직임이면 그 기록을 다시 분석
            conn.execute("UPDATE events SET cause_stage=NULL WHERE id=?", (same["id"],))
            return {"id": same["id"], "existing": True, **ev}
        cur = conn.execute(
            "INSERT INTO events(symbol, start_ts, last_ts, event_type, direction, return_5m, peak_return_5m, peak_ts, "
            "status, detection_rule, trade_value, tier) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, ev["start_ts"], ev["last_ts"], ev["event_type"], ev["direction"], ev["return_5m"],
             ev["peak_return_5m"], ev["peak_ts"], ev["status"], ev["detection_rule"], ev["trade_value"], ev["tier"]))
        return {"id": cur.lastrowid, "existing": False, **ev}


def detect_symbol(symbol: str, target_date=None, rescan: bool = False, now: datetime | None = None, log=None) -> int:
    """급등·급락 찾기 (하루 단위로 다시 계산해서 기록을 맞춤).

    - 장중: 30분 안에 ±5% 이상 움직이고 유지된 구간 (거래대금 기준 이상)
    - 하루: 종가가 전일보다 ±5% 이상 (장 마감 뒤 기록)
    - ±10% 이상은 '대형', ±3~5%는 '관찰'(사이트 기록만, 알림 없음)
    처음 실행하거나 기준이 바뀌면 받아 둔 기간 전체를 다시 훑음. 이후에는 오늘(과 어제)만.
    Returns: 새로 생긴 이벤트 수
    """
    s = load_settings()
    from .feed import kst_now
    now = now or kst_now().replace(tzinfo=None)
    _ensure_version(symbol, log)
    st = get_state(_state_key(symbol))
    if rescan and target_date is not None:
        since = target_date.isoformat()
    elif st and st.get("value"):
        since = (datetime.fromisoformat(st["value"] + "T00:00") - timedelta(days=4)).date().isoformat()
    else:
        since = None
    df = load_bars(symbol, since + "T00:00" if since else None)
    days = sorted({d.isoformat() for d in df["date"]}) if not df.empty else []
    # 일봉만 있는 날 (분봉 보관 기간 밖)도 하루 기준은 확인
    lookback = (now - timedelta(days=int(s.get("daily_event_lookback_days", 90)))).date().isoformat()
    with connect() as conn:
        ddays = [r[0] for r in conn.execute("SELECT date FROM daily_bars WHERE symbol=? AND date>=? ORDER BY date",
                                            (symbol, max(lookback, since or lookback)))]
    today = now.date().isoformat()
    new = 0
    with connect() as conn:
        for day in sorted(set(days) | set(ddays)):
            bars = df[df["ts"].dt.date.astype(str) == day] if days else None
            if bars is not None and bars.empty:
                bars = None
            day_over = day < today or now.time() >= datetime.strptime("15:35", "%H:%M").time()
            evs = []
            if bars is not None:
                evs += intraday_events(bars, _prev_close(symbol, day), s, day_over)
            if day_over and (day < today or now.weekday() < 5):
                dv = daily_event(symbol, day, s, bars)
                if dv:
                    evs.append(dv)
            for ev in evs:
                new += _upsert(conn, symbol, ev)
    if days:
        set_state(_state_key(symbol), days[-1])
    return new
