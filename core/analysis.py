"""PR 효과 분석 · KOSDAQ 비교 계산.

원칙: '이 기사 때문에 올랐다'고 단정하지 않고, 시간적·시장적 연관성만 보여줌.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from .db import connect

HORIZONS = [5, 15, 30, 60, 120]
KOSDAQ = "KOSDAQ"


def load_range(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT ts, open, high, low, close, volume FROM minute_bars "
            "WHERE symbol=? AND ts >= ? AND ts <= ? ORDER BY ts",
            conn,
            params=(symbol, start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")),
        )
    df["ts"] = pd.to_datetime(df["ts"])      # 비어 있어도 날짜 형식으로 (빈 결과에서 .dt 오류 방지)
    return df


def close_at_or_before(df: pd.DataFrame, t: datetime):
    sub = df[df["ts"] <= t]
    return None if sub.empty else float(sub["close"].iloc[-1])


def pct_change_between(symbol: str, t0: datetime, t1: datetime):
    """t0 시점 종가 → t1 시점 종가 변화율. 데이터 없으면 None."""
    df = load_range(symbol, t0 - timedelta(days=5), t1)
    if df.empty:
        return None
    a = close_at_or_before(df, t0)
    b = close_at_or_before(df[df["ts"] > t0], t1) if not df[df["ts"] > t0].empty else None
    if a is None or b is None or a == 0:
        return None
    return b / a - 1


@dataclass
class PRResult:
    ok: bool
    message: str = ""
    published: datetime | None = None
    reaction_start: datetime | None = None      # 실제 반응 측정 시작 (장 외 공개면 다음 장 시작)
    base_price: float | None = None
    returns: dict = field(default_factory=dict)          # {5: 0.012, ...}
    kosdaq_returns: dict = field(default_factory=dict)
    max_up: float | None = None
    max_up_at: datetime | None = None
    max_down: float | None = None
    max_down_at: datetime | None = None
    vol_after30: int | None = None
    vol_usual30: float | None = None
    other_events: list = field(default_factory=list)
    bars: pd.DataFrame | None = None


def _first_bar_at_or_after(df: pd.DataFrame, t: datetime):
    sub = df[df["ts"] >= t]
    return None if sub.empty else sub["ts"].iloc[0].to_pydatetime()


def analyze_pr(symbol: str, published: datetime) -> PRResult:
    window = load_range(symbol, published - timedelta(days=7), published + timedelta(days=4))
    if window.empty:
        return PRResult(False, "해당 날짜의 분봉 데이터가 없습니다. "
                               "(네이버는 최근 약 7거래일 분봉만 제공합니다)", published=published)

    start = _first_bar_at_or_after(window, published)
    if start is None:
        return PRResult(False, "공개 시각 이후 분봉이 아직 없습니다. 장이 열린 뒤 다시 확인해 주세요.",
                        published=published)
    # 장중 공개면 공개 시각 그대로, 장 외 공개면 다음 장 첫 분봉부터
    reaction_start = published if start - published < timedelta(minutes=2) else start
    before = window[window["ts"] < reaction_start]
    if before.empty:
        return PRResult(False, "공개 직전 기준 가격 데이터가 없습니다.", published=published)
    base_price = float(before["close"].iloc[-1])

    after = window[(window["ts"] >= reaction_start)
                   & (window["ts"] <= reaction_start + timedelta(minutes=120))
                   & (window["ts"].dt.date == reaction_start.date())]
    res = PRResult(True, published=published, reaction_start=reaction_start, base_price=base_price)

    day_bars = window[window["ts"].dt.date == reaction_start.date()]
    day_last = day_bars["ts"].max()
    session_over = day_last.time() >= datetime.strptime("15:30", "%H:%M").time()
    for h in HORIZONS:
        t = reaction_start + timedelta(minutes=h)
        sub = after[after["ts"] <= t]
        # 아직 그 시각까지 데이터가 쌓이지 않았으면(장중 실시간) 비워 둠
        if not sub.empty and (day_last >= t or session_over):
            res.returns[h] = float(sub["close"].iloc[-1]) / base_price - 1

    if not after.empty:
        hi = after.loc[after["high"].idxmax()]
        lo = after.loc[after["low"].idxmin()]
        res.max_up, res.max_up_at = float(hi["high"]) / base_price - 1, hi["ts"].to_pydatetime()
        res.max_down, res.max_down_at = float(lo["low"]) / base_price - 1, lo["ts"].to_pydatetime()

    # 거래량: 공개 후 30분 vs 과거 거래일 같은 시각 30분의 중앙값
    a30 = after[after["ts"] < reaction_start + timedelta(minutes=30)]
    full30 = day_last >= reaction_start + timedelta(minutes=29) or session_over
    res.vol_after30 = int(a30["volume"].sum()) if (not a30.empty and full30) else None
    t_from, t_to = reaction_start.time(), (reaction_start + timedelta(minutes=30)).time()
    prior = window[(window["ts"].dt.date < reaction_start.date())]
    if not prior.empty:
        same_clock = prior[(prior["ts"].dt.time >= t_from) & (prior["ts"].dt.time < t_to)]
        per_day = same_clock.groupby(same_clock["ts"].dt.date)["volume"].sum()
        if len(per_day) >= 2:
            res.vol_usual30 = float(per_day.median())

    # KOSDAQ 같은 구간
    kq = load_range(KOSDAQ, reaction_start - timedelta(days=4), reaction_start + timedelta(minutes=125))
    if not kq.empty:
        kq_after = kq[(kq["ts"] >= reaction_start) & (kq["ts"].dt.date == reaction_start.date())]
        kq_base = close_at_or_before(kq, reaction_start - timedelta(minutes=1))
        if kq_base is None and not kq_after.empty:
            kq_base = float(kq_after["open"].iloc[0])   # 전날 지수 분봉이 없으면 당일 시가 기준
        if kq_base:
            for h in res.returns:
                sub = kq_after[kq_after["ts"] <= reaction_start + timedelta(minutes=h)]
                if not sub.empty:
                    res.kosdaq_returns[h] = float(sub["close"].iloc[-1]) / kq_base - 1

    # 같은 시간대 감지된 이상변동
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, start_ts, direction, peak_return_5m FROM events "
            "WHERE symbol=? AND start_ts BETWEEN ? AND ? ORDER BY start_ts",
            (symbol, (reaction_start - timedelta(minutes=30)).isoformat(timespec="minutes"),
             (reaction_start + timedelta(minutes=120)).isoformat(timespec="minutes")),
        ).fetchall()
    res.other_events = [dict(r) for r in rows]

    res.bars = window[(window["ts"] >= reaction_start - timedelta(minutes=30))
                      & (window["ts"] <= reaction_start + timedelta(minutes=120))]
    return res


def summary_sentence(r: PRResult) -> str:
    """사용자에게 보여줄 한 줄 요약 (인과 단정 없이 사실만)."""
    if not r.ok:
        return r.message
    parts = []
    h = 30 if 30 in r.returns else (max(r.returns) if r.returns else None)
    if h:
        parts.append(f"공개 후 {h}분간 {r.returns[h]*100:+.2f}%")
    if r.vol_after30 is not None and r.vol_usual30:
        parts.append(f"30분 거래량 평소의 {r.vol_after30 / r.vol_usual30:.1f}배")
    if h and h in r.kosdaq_returns:
        parts.append(f"같은 구간 KOSDAQ {r.kosdaq_returns[h]*100:+.2f}%")
    else:
        parts.append("KOSDAQ 비교 데이터 없음")
    others = [e for e in r.other_events]
    parts.append(f"같은 시간대 감지된 이상변동 {len(others)}건" if others else "같은 시간대 다른 이상변동 없음")
    lead = ""
    if r.reaction_start and r.published and r.reaction_start != r.published:
        lead = f"(장 외 공개 → {r.reaction_start:%m/%d %H:%M} 장 시작부터 측정) "
    return lead + ", ".join(parts)
