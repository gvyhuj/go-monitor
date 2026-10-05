"""이상변동 원인 판단 (실무 3단계).

① 몫 나누기   실제 = 시장 몫 + 업종 몫 + 이 종목 고유 몫 (고유 몫이 평소의 몇 배인지 = 이례성)
② 기사 직후 검증   기사·PR 직후 30분(또는 다음 날 시초가)이 평소보다 드물게 움직였나
③ 수급   거래량 배수, 증권사 창구(업종 묶음 매수 / 이 종목 집중 매수), 외국인·기관 순매수

판단 순서
- 바깥 몫이 절반 이상 → 시장·업종 흐름 (그 흐름을 만든 묶음 매수·업종 소식을 근거로)
- 고유 몫이 이례적 + 기사가 ②통과 → 그 기사
- 고유 몫이 이례적 + 수급만 뚜렷 → 수급 ('누가'만 알고 '왜'는 모르므로 신뢰도 최대 중간)
- 모두 아니면 → 원인 미확인
신뢰도: 후보의 핵심 근거가 맞아야 중간 이상, ①②③ 중 2개 이상 맞으면 높음.
토론방·큰 주문·회사 일정은 순위에 넣지 않고 참고로만 표시.
주가가 먼저 움직이고 나온 기사는 원인으로 보지 않으며, '누가'는 공개 범위(창구·투자자 유형)까지만 말한다.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

import pandas as pd

from . import analysis
from .db import connect

KOSDAQ = "KOSDAQ"
CAUSE_VERSION = "14"   # 분석 항목이 바뀌면 올림 → 최근 이상변동을 자동 재분석

POS_WORDS = ("수주", "계약", "공급", "협약", "MOU", "선정", "개발", "출시", "흑자", "최대", "증가", "확대", "양산",
             "수출", "승인", "인증", "특허", "투자 유치", "호실적", "상향", "수혜", "강세", "급등", "신고가")
NEG_WORDS = ("유상증자", "적자", "손실", "소송", "해지", "취소", "블록딜", "오버행", "보호예수", "매각", "감소",
             "하향", "부진", "약세", "급락", "전환사채", "CB", "BW", "감사의견", "거래정지", "불성실")

TYPE_LABEL = {
    "pr": "등록한 PR",
    "disclosure": "공시",
    "news": "직접 뉴스",
    "article": "이 종목 기사",
    "theme": "테마 동반",
    "macro": "거시·정치 이슈",
    "sector": "업종 동반 움직임",
    "market": "시장 전체 흐름",
    "flow": "수급",
    "board": "종목토론방",
    "calendar": "회사 일정",
    "orders": "소수 대량 주문",
    "own": "이 종목 개별 매매",
}


# ------------------------------------------------------------------ helpers

def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s[:16])


def _fmt_pct(v):
    return "-" if v is None else f"{v*100:+.2f}%"


def _change(df: pd.DataFrame, t0: datetime, t1: datetime):
    """t0 시점 종가 → t1 시점 종가. 같은 날 데이터가 t0 이전에 없으면 그날 시가 기준."""
    if df is None or df.empty:
        return None
    day = df[df["ts"].dt.date == t1.date()]
    a = day[day["ts"] <= t0]
    b = day[(day["ts"] > t0) & (day["ts"] <= t1)]
    if b.empty:
        return None
    base = float(a["close"].iloc[-1]) if not a.empty else float(b["open"].iloc[0])
    return float(b["close"].iloc[-1]) / base - 1 if base else None


def _tone(text: str) -> int:
    """기사 제목·요약의 방향성: +1 호재 쪽, -1 악재 쪽, 0 중립."""
    p = sum(w in text for w in POS_WORDS)
    n = sum(w in text for w in NEG_WORDS)
    return (p > n) - (n > p)


def _gap_text(minutes: int) -> str:
    h, m = divmod(abs(minutes), 60)
    return (f"{h}시간 {m}분" if h else f"{m}분")


def _lead_score(pub: datetime, start: datetime, near: int, mid: int, far: int, overnight: int) -> int:
    """공개 시각이 움직임 시작에 가까울수록 높게. 장 시작 직후 움직임은 전날 저녁·아침 공개분도 높게."""
    mins = (start - pub).total_seconds() / 60
    session_open = datetime.combine(start.date(), datetime.min.time()) + timedelta(hours=9)
    opening_move = start - session_open <= timedelta(minutes=20)
    if pub < session_open:
        return overnight if opening_move else far
    if mins <= 30:
        return near
    if mins <= 90:
        return mid
    return far


def _prev_trading_day(symbol: str, day) -> str | None:
    with connect() as conn:
        r = conn.execute(
            "SELECT MAX(substr(ts,1,10)) FROM minute_bars WHERE symbol=? AND substr(ts,1,10) < ?",
            (symbol, day.isoformat()),
        ).fetchone()
    return r[0] if r else None


# ------------------------------------------------------------------ evidence

def _flows(symbol: str, t0: datetime, t1: datetime, window_volume: int):
    """거래원(증권사 창구) 증감. 구간 전후 스냅샷 차이, 없으면 당일 누적."""
    def _norm(name):
        return (name or "").replace(" ", "").replace("증권", "").replace("투자", "")
    day = t1.date().isoformat()
    with connect() as conn:
        snaps = [r[0] for r in conn.execute(
            "SELECT DISTINCT ts FROM trader_snapshots WHERE symbol=? AND substr(ts,1,10)=? ORDER BY ts",
            (symbol, day))]
        if not snaps:
            # 장중 1분 기록이 없으면 장 마감 시점 기록(네이버 → 다음 금융 순)으로 대신
            for src in ("네이버", "다음 금융"):
                got = [dict(x) for x in conn.execute(
                    "SELECT name AS trader_no, name, buy, sell, is_foreign, buy_top5, sell_top5 FROM trader_daily "
                    "WHERE symbol=? AND date=? AND source=?", (symbol, day, src))]
                if got:
                    break
            if not got:
                return None
            snaps, eod_rows = [f"{day}T15:30"], {r["trader_no"]: r for r in got}
        else:
            eod_rows = None
        before = [s for s in snaps if s <= t0.isoformat(timespec="minutes")] if eod_rows is None else []
        after = [s for s in snaps if s >= t1.isoformat(timespec="minutes")]
        s1 = after[0] if after else snaps[-1]
        s0 = before[-1] if before else None

        def load(ts):
            return {r["trader_no"]: dict(r) for r in conn.execute(
                "SELECT * FROM trader_snapshots WHERE symbol=? AND ts=?", (symbol, ts))}

        b1 = eod_rows if eod_rows is not None else load(s1)
        b0 = load(s0) if (s0 and eod_rows is None) else {}
    rows = []
    for no, r in b1.items():
        p = b0.get(no, {"buy": 0, "sell": 0})
        buy, sell = r["buy"] - p["buy"], r["sell"] - p["sell"]
        if s0 and buy == 0 and sell == 0:
            continue
        bt = r.get("buy_top5")
        st = r.get("sell_top5")
        rows.append({
            "name": r["name"], "buy": buy, "sell": sell, "net": buy - sell, "foreign": bool(r["is_foreign"]),
            # 상위 5위 안 = 실시간 반영 / 밖 = 밀려난 시점에서 멈춤 / 0 이고 5위 밖 = 집계 안 됨
            "buy_state": ("top5" if bt else "out" if r["buy"] > 0 else "none") if bt is not None else "unknown",
            "sell_state": ("top5" if st else "out" if r["sell"] > 0 else "none") if st is not None else "unknown",
        })
    if not rows:
        return None
    for r in rows:
        r["net_is_min"] = (r["net"] > 0 and r["sell_state"] == "none") or (r["net"] < 0 and r["buy_state"] == "none")
    basis = "구간" if s0 else ("장 마감 기록" if eod_rows is not None else "당일 누적")
    foreign_net = sum(r["net"] for r in rows if r["foreign"])
    foreign_buy = sum(r["buy"] for r in rows if r["foreign"])
    top_buyers = sorted([r for r in rows if r["net"] > 0], key=lambda r: -r["net"])[:5]
    top_sellers = sorted([r for r in rows if r["net"] < 0], key=lambda r: r["net"])[:5]
    from .feed import last_crosscheck
    cc = last_crosscheck(symbol)
    if cc and cc.get("ts", "")[:10] != day:
        cc = None
    if cc and basis != "구간":
        # 네이버 목록에 빠진 상위 창구(예: 토스증권)를 다음 금융 값으로 보충
        have = {_norm(r["name"]) for r in rows}
        for m in cc.get("missing_in_naver") or []:
            if _norm(m["name"]) in have:
                continue
            r = {"name": m["name"], "buy": m["buy"], "sell": m["sell"], "net": m["buy"] - m["sell"],
                 "foreign": bool(m["foreign"]), "from_daum": True,
                 "buy_state": "top5" if m["buy_top5"] else ("out" if m["buy"] else "none"),
                 "sell_state": "top5" if m["sell_top5"] else ("out" if m["sell"] else "none")}
            r["net_is_min"] = (r["net"] > 0 and r["sell_state"] == "none") or (r["net"] < 0 and r["buy_state"] == "none")
            rows.append(r)
        top_buyers = sorted([r for r in rows if r["net"] > 0], key=lambda r: -r["net"])[:5]
        top_sellers = sorted([r for r in rows if r["net"] < 0], key=lambda r: r["net"])[:5]
    with connect() as conn:
        eod = {}
        for src in ("네이버", "다음 금융"):
            got = [dict(x) for x in conn.execute(
                "SELECT name, buy, sell, is_foreign, buy_top5, sell_top5 FROM trader_daily "
                "WHERE symbol=? AND date=? AND source=?", (symbol, day, src))]
            if got:
                eod[src] = got
    return {
        "basis": basis, "from_ts": s0, "to_ts": s1, "window_volume": window_volume,
        "foreign_net": foreign_net, "foreign_buy": foreign_buy,
        "top_buyers": top_buyers, "top_sellers": top_sellers,
        "crosscheck": cc, "eod": eod,
        "source": "한국거래소 → 코스콤 '종목별 매매 상위 5개 증권사' 자료 (네이버 증권에서 수집)",
        "note": ("구간 시작 전·후 거래원 기록의 차이입니다. " if s0 else "구간 시작 전 기록이 없어 그날 누적 기준입니다. ")
                + "매수·매도 각각 상위 5개 증권사만 공개되는 추정치라, 5위 밖 거래는 보이지 않습니다.",
    }


def _norm(name: str) -> str:
    return (name or "").replace(" ", "").replace("증권", "").replace("투자", "")


def _theme_names(code: str) -> list[str]:
    with connect() as conn:
        return [r[0] for r in conn.execute("SELECT theme_name FROM stock_themes WHERE code=?", (code,))]


def _daily_close(code: str, day: str):
    with connect() as conn:
        r = conn.execute("SELECT close FROM daily_bars WHERE symbol=? AND date=?", (code, day)).fetchone()
    return float(r[0]) if r and r[0] else None


def _price_on(code: str, day: str):
    with connect() as conn:
        r = conn.execute("SELECT close FROM minute_bars WHERE symbol=? AND substr(ts,1,10)=? ORDER BY ts DESC LIMIT 1",
                         (code, day)).fetchone()
    return float(r[0]) if r else None


def _eok(v):
    if v is None:
        return "-"
    a = abs(v) / 1e8
    return f"{'-' if v < 0 else ''}{a:,.1f}억 원" if a >= 0.1 else f"{'-' if v < 0 else ''}{abs(v) / 1e4:,.0f}만 원"


def _buyer_context(symbol: str, day, flows: dict, sign: int, related: list, fetch) -> dict | None:
    """'그 창구는 왜 샀(팔았)을까'에 대한 공개 자료 단서.

    누가 주문했는지·왜 주문했는지는 공개되지 않으므로 '단서'만 모은다.
    """
    pool = flows["top_buyers"] if sign >= 0 else flows["top_sellers"]
    if not pool:
        return None
    dstr = day.isoformat()

    # 같은 날 같은 업종 종목의 거래원 (오늘이면 지금 받아오고, 지난 날짜는 장 마감 기록)
    peer_infos = {}
    if fetch:
        try:
            peer_infos = fetch("peer_traders", [r["code"] for r in related], day) or {}
        except Exception:
            peer_infos = {}
    peer_rows = {}
    for r in related:
        info = peer_infos.get(r["code"])
        if info:
            peer_rows[r["code"]] = info["traders"]
            continue
        with connect() as conn:
            got = [dict(x) for x in conn.execute(
                "SELECT name, buy, sell, is_foreign, buy_top5, sell_top5 FROM trader_daily "
                "WHERE symbol=? AND date=? AND source='네이버'", (r["code"], dstr))]
        if got:
            peer_rows[r["code"]] = got

    def peers_of(bname):
        out = []
        for r in related:
            rows = peer_rows.get(r["code"])
            if rows is None:
                continue
            hit = next((t for t in rows if _norm(t["name"]) == _norm(bname)), None)
            pp = _price_on(r["code"], dstr) or _daily_close(r["code"], dstr)
            pnet = (hit["buy"] - hit["sell"]) if hit else 0
            out.append({"code": r["code"], "name": r["name"], "net": pnet, "price": pp,
                        "amount": pnet * pp if (pp and hit) else None, "listed": bool(hit)})
        return out

    # 상위 창구 중 '업종 묶음 매매' 근거가 가장 강한 곳을 고름 (외국계 우선, 같은 방향 업종 종목 수)
    def strength(r):
        ps = peers_of(r["name"])
        same = sum(1 for x in ps if x["net"] * r["net"] > 0)
        return (same >= 2) * 10 + r["foreign"] * 5 + same, ps
    scored = [(strength(r), r) for r in pool[:6]]
    (best_s, peers), lead = max(scored, key=lambda x: (x[0][0], abs(x[1]["net"])))
    if best_s < 5:                      # 묶음 근거가 없으면 예전처럼 외국계 → 1위 창구
        foreign = [r for r in pool[:3] if r["foreign"]]
        lead = foreign[0] if foreign else pool[0]
        peers = peers_of(lead["name"])
    name, net = lead["name"], lead["net"]
    side = "매수" if net > 0 else "매도"
    price = _price_on(symbol, dstr) or _daily_close(symbol, dstr)
    own_amount = net * price if price else None
    lines = []
    if peers:
        same = [p for p in peers if p["net"] * net > 0]
        tot = sum(p["amount"] or 0 for p in same)
        opp = [p for p in peers if p["net"] * net < 0]
        if len(same) >= 2 and len(same) >= len(opp):
            share = (own_amount / (own_amount + tot) * 100) if (own_amount and tot) else None
            lines.append({"kind": "업종 묶음", "strong": True, "text":
                f"같은 날 {name} 창구가 확인한 비교 종목 {len(peers)}개 중 {len(same)}개도 순{side}했습니다"
                f"({', '.join(p['name'] for p in sorted(same, key=lambda p: -abs(p['amount'] or 0))[:5])})"
                f"(합계 약 {_eok(tot)})." + (f" 이 종목은 그중 {share:.0f}% 수준입니다." if share is not None else "")
                + " 이 종목만 겨냥했다기보다 업종을 묶어 사고판 흐름일 가능성이 큽니다."})
        elif not same:
            lines.append({"kind": "개별 관심", "strong": True, "text":
                f"같은 업종 종목의 상위 거래원에는 {name} 창구의 순{side}가 없습니다. "
                "업종 전체보다 이 종목에 대한 개별 관심일 가능성이 있습니다."})
        else:
            lines.append({"kind": "업종 일부", "strong": False, "text":
                f"{name} 창구가 같은 업종 {len(peers)}개 중 {len(same)}개 종목에서도 순{side}했습니다."})
    else:
        lines.append({"kind": "업종 비교", "strong": False, "text":
            "이날 같은 업종 종목의 거래원 기록이 없어 묶음 매매 여부를 확인하지 못했습니다."})

    # (b) 며칠째 같은 방향인가 (외국계 창구 추정 + 거래소 확정 외국인)
    with connect() as conn:
        side_rows = [dict(x) for x in conn.execute(
            "SELECT * FROM broker_side_daily WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 10", (symbol, dstr))]
        inv_rows = [dict(x) for x in conn.execute(
            "SELECT * FROM investor_daily WHERE symbol=? AND bizdate<=? ORDER BY bizdate DESC LIMIT 10",
            (symbol, dstr.replace("-", "")))]
    if lead["foreign"] and side_rows:
        streak = 0
        for r in side_rows:
            if r["foreign_net"] * net > 0:
                streak += 1
            else:
                break
        s10 = sum(r["foreign_net"] for r in side_rows)
        lines.append({"kind": "연속성", "strong": streak >= 3, "text":
            f"외국계 창구 합계로 {streak}거래일 연속 순{side}" + (f"이고 최근 {len(side_rows)}거래일 합계 {s10:+,}주입니다." if streak else
            f"는 아닙니다. 최근 {len(side_rows)}거래일 합계 {s10:+,}주입니다.") + " (다음 금융 추정치)"})
    if inv_rows:
        streak = 0
        for r in inv_rows:
            if (r["foreign_net"] or 0) * net > 0:
                streak += 1
            else:
                break
        ratio = f" 외국인 보유율 {inv_rows[-1]['foreign_ratio']:.2f}% → {inv_rows[0]['foreign_ratio']:.2f}%." \
            if len(inv_rows) > 1 and inv_rows[0].get("foreign_ratio") else ""
        lines.append({"kind": "외국인 확정치", "strong": streak >= 3, "text":
            f"거래소 확정 '외국인' 순매수는 {inv_rows[0]['bizdate'][4:6]}/{inv_rows[0]['bizdate'][6:]} 기준 "
            f"{inv_rows[0]['foreign_net']:+,}주, 같은 방향 {streak}거래일 연속입니다.{ratio}"})

    # (c) 직전 증권사 리포트
    with connect() as conn:
        reps = [dict(x) for x in conn.execute(
            "SELECT * FROM research WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date DESC",
            (symbol, (day - timedelta(days=14)).isoformat(), dstr))]
    if reps:
        for r in reps[:2]:
            lines.append({"kind": "리포트", "strong": True, "text": f"{r['date']} {r['broker']} 리포트 「{r['title']}」",
                          "url": r["url"]})
    else:
        lines.append({"kind": "리포트", "strong": False, "text": "최근 2주 안에 나온 증권사 리포트는 없습니다."})

    # (d) 5% 대량보유 보고 (실제 매수 주체·목적이 드러나는 유일한 공개 자료)
    with connect() as conn:
        fil = [dict(x) for x in conn.execute(
            "SELECT * FROM disclosures WHERE symbol=? AND date BETWEEN ? AND ? "
            "AND (title LIKE '%대량보유%' OR title LIKE '%소유상황%') ORDER BY date",
            (symbol, (day - timedelta(days=3)).isoformat(), (day + timedelta(days=14)).isoformat()))]
    if fil:
        for f in fil[:2]:
            lines.append({"kind": "5% 공시", "strong": True,
                          "text": f"{f['date']} {f['title']} — 보고 내용에서 실제 보유자와 보유 목적을 확인할 수 있습니다. "
                                  f"{(f.get('summary') or '')[:120]}"})
    else:
        lines.append({"kind": "5% 공시", "strong": False, "text":
            "아직 5% 대량보유 보고 공시는 없습니다. 어떤 투자자가 지분 5%를 넘기면 5영업일 안에 이름과 목적을 공시해야 해서, "
            "나오면 이 이상변동에 자동으로 연결됩니다."})

    lines.append({"kind": "확인 불가", "strong": False, "text":
        "공매도 상환을 위한 매수인지, 주문을 낸 고객이 누구인지는 공개 자료로 확인할 수 없습니다."})
    same_peers = [x for x in peers if x["net"] * net > 0]
    opp_peers = [x for x in peers if x["net"] * net < 0]
    return {"broker": name, "foreign": lead["foreign"], "side": side, "qty": abs(net), "qty_is_min": lead.get("net_is_min"),
            "amount": abs(own_amount) if own_amount else None, "peers": peers, "lines": lines,
            "basket": bool(peers) and len(same_peers) >= 2 and len(same_peers) >= len(opp_peers),
            "n_checked": sum(1 for x in peers if x.get("listed") is not None),
            "same_peers": same_peers, "basis": flows.get("basis")}


def followup_filings(symbol: str, day: str) -> list[dict]:
    """이상변동 뒤에 나온 5% 대량보유·소유상황 보고 (화면을 열 때마다 새로 확인)."""
    d = datetime.fromisoformat(day + "T00:00")
    with connect() as conn:
        return [dict(x) for x in conn.execute(
            "SELECT date, title, summary FROM disclosures WHERE symbol=? AND date BETWEEN ? AND ? "
            "AND (title LIKE '%대량보유%' OR title LIKE '%소유상황%') ORDER BY date",
            (symbol, (d - timedelta(days=3)).date().isoformat(), (d + timedelta(days=14)).date().isoformat()))]


def _investor_day(symbol: str, day: str):
    with connect() as conn:
        r = conn.execute("SELECT * FROM investor_daily WHERE symbol=? AND bizdate=?",
                         (symbol, day.replace("-", ""))).fetchone()
    return dict(r) if r else None



def _daily_change(code: str, day: str):
    with connect() as conn:
        rows = conn.execute("SELECT date, close FROM daily_bars WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 2",
                            (code, day)).fetchall()
    if len(rows) == 2 and rows[0][0] == day and rows[1][1]:
        return rows[0][1] / rows[1][1] - 1
    # 일봉이 아직 없으면 분봉으로 (전일 마지막 → 당일 마지막)
    with connect() as conn:
        a = conn.execute("SELECT close FROM minute_bars WHERE symbol=? AND ts<? ORDER BY ts DESC LIMIT 1",
                         (code, day + "T00:00")).fetchone()
        b = conn.execute("SELECT close FROM minute_bars WHERE symbol=? AND substr(ts,1,10)=? ORDER BY ts DESC LIMIT 1",
                         (code, day)).fetchone()
    return (b[0] / a[0] - 1) if a and b and a[0] else None


def _main_move_time(df: pd.DataFrame, day, move):
    """하루 움직임의 절반에 처음 닿은 분 (원인 기사가 그 전에 나왔는지 판단하는 기준)."""
    if df is None or df.empty or not move:
        return None
    prev = df[df["ts"].dt.date < day]
    cur = df[df["ts"].dt.date == day]
    if prev.empty or cur.empty:
        return None
    pc = float(prev["close"].iloc[-1])
    hit = cur[(cur["close"] / pc - 1) * (1 if move > 0 else -1) >= abs(move) / 2]
    return hit["ts"].iloc[0].to_pydatetime() if not hit.empty else None


def _concentration(df: pd.DataFrame, t_a: datetime, t_b: datetime, move):
    """움직임의 대부분이 몇 분 사이 큰 주문 몇 건으로 만들어졌는지."""
    if df is None or df.empty or not move:
        return None
    seg = df[(df["ts"] >= t_a - timedelta(minutes=1)) & (df["ts"] <= t_b)].sort_values("ts")
    seg = seg[seg["volume"] > 0]
    if len(seg) < 6:
        return None
    seg = seg.assign(d=seg["close"].diff())
    total_vol = float(seg["volume"].sum())
    base = float(seg["close"].iloc[0])
    if not base or not total_vol:
        return None
    best = None
    rows = seg.reset_index(drop=True)
    for i in range(1, len(rows)):
        j = i
        while j + 1 < len(rows) and (rows.loc[j + 1, "ts"] - rows.loc[i, "ts"]) <= timedelta(minutes=2):
            j += 1
        part = rows.loc[i:j]
        dp = float(part["d"].sum()) / base
        if dp * (1 if move > 0 else -1) <= 0:
            continue
        share_move = dp / move
        share_vol = float(part["volume"].sum()) / total_vol
        if best is None or share_move > best["share_move"]:
            best = {"from": part["ts"].iloc[0].strftime("%H:%M"), "to": part["ts"].iloc[-1].strftime("%H:%M"),
                    "share_move": share_move, "share_vol": share_vol, "move": dp,
                    "volume": int(part["volume"].sum()), "value": float((part["close"] * part["volume"]).sum())}
    return best


def _calendar(day, sign: int) -> list[dict]:
    """회사 일정(보호예수 해제 등) 중 이벤트 앞뒤 며칠 안에 있는 것."""
    from .detector import load_settings
    out = []
    for c in load_settings().get("calendar") or []:
        try:
            d = datetime.fromisoformat(c["date"]).date()
        except (KeyError, ValueError):
            continue
        gap = (d - day).days
        if -2 <= gap <= 5:
            out.append({**c, "gap": gap})
    return out


def _decompose_window(symbol: str, day, own, kq, related, daily: bool):
    """실제 변동 = 시장 몫 + 업종 몫 + 이 종목 고유 몫 (민감도는 일별 회귀값을 씀)."""
    from . import articles as _articles
    peers = [{"code": r["code"], "name": r["name"]} for r in related]
    try:
        dc = _articles.decompose(symbol, day.isoformat(), peers)
    except Exception:
        dc = None
    if daily and dc and dc.get("d0"):
        d0 = dc["d0"]
        return {"own": d0["own"], "market": d0["market"], "sector": d0["sector"], "specific": d0["abnormal"],
                "z": d0.get("z"), "kosdaq": d0.get("kosdaq"), "sector_avg": d0.get("sector_avg"),
                "method": dc.get("method"), "beta_market": dc.get("beta_market"), "beta_sector": dc.get("beta_sector"),
                "sigma": dc.get("sigma"), "basis": "하루"}
    if own is None:
        return None
    vals = [r["change"] for r in related if r.get("change") is not None]
    sec = sum(vals) / len(vals) if len(vals) >= 2 else None
    bm = (dc or {}).get("beta_market") or 1.0
    bs = (dc or {}).get("beta_sector") or (1.0 if sec is not None else 0.0)
    bsm = (dc or {}).get("beta_sector_market") or 1.0
    mk = bm * kq if kq is not None else 0.0
    sc = bs * (sec - bsm * (kq or 0.0)) if sec is not None else 0.0
    return {"own": own, "market": mk, "sector": sc, "specific": own - mk - sc, "z": None, "kosdaq": kq,
            "sector_avg": sec, "method": (dc or {}).get("method") or "단순", "beta_market": bm, "beta_sector": bs,
            "sigma": (dc or {}).get("sigma"), "basis": "구간"}


def _returns(code: str, lo: str, hi: str) -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT date, close FROM daily_bars WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date",
                            (code, lo, hi)).fetchall()
    out, prev = {}, None
    for d, c in rows:
        if prev and c:
            out[d] = c / prev - 1
        prev = c
    return out


def _segment_links(symbol: str, day, groups: dict) -> dict:
    """분야별 '평소 연동성': 이벤트 전 약 120거래일 동안 시장(KOSDAQ) 영향을 뺀 뒤
    이 종목과 그 분야 평균이 얼마나 함께 움직였는지 (상관계수, -1~1).
    사업상 연결이 있어도 주가에 실제로 나타나지 않으면 원인 판단에서 낮게 보기 위함."""
    import numpy as np
    hi = (day - timedelta(days=1)).isoformat()
    lo = (day - timedelta(days=200)).isoformat()
    own, kq = _returns(symbol, lo, hi), _returns(KOSDAQ, lo, hi)
    out = {}
    for seg, codes in groups.items():
        series = [_returns(c, lo, hi) for c in codes]
        dates = [d for d in own if d in kq and sum(1 for s in series if d in s) >= max(1, len(series) // 2)]
        if len(dates) < 40:
            continue
        o = np.array([own[d] for d in dates])
        m = np.array([kq[d] for d in dates])
        g = np.array([np.mean([s[d] for s in series if d in s]) for d in dates])
        X = np.column_stack([np.ones_like(m), m])
        ro = o - X @ np.linalg.lstsq(X, o, rcond=None)[0]
        rg = g - X @ np.linalg.lstsq(X, g, rcond=None)[0]
        if ro.std() > 0 and rg.std() > 0:
            r = float(np.corrcoef(ro, rg)[0, 1])
            n = len(dates)
            z, se = math.atanh(max(-0.999, min(0.999, r))), 1 / math.sqrt(n - 3)
            out[seg] = {"corr": r, "n": n, "lo": math.tanh(z - 1.96 * se), "hi": math.tanh(z + 1.96 * se)}
    return out


def _link_label(c, lo=None):
    """95% 범위의 아래쪽 값으로 판단: 0을 넘어야 '우연이 아닌 연동'으로 봄."""
    if c is None:
        return "-"
    if lo is None:
        return "강함" if c >= 0.4 else "보통" if c >= 0.2 else "약함"
    return "강함" if lo >= 0.25 else "보통" if lo > 0 else "확인 안 됨"


def _segments(related: list[dict], sign: int, links: dict | None = None) -> list[dict]:
    """사업 분야별 비교 종목 평균 변동 + 평소 연동성 (연동성 감안해 의미 있는 분야가 앞)."""
    links = links or {}
    by = {}
    for r in related:
        if r.get("change") is None:
            continue
        by.setdefault(r.get("source") or "비교 종목", []).append(r)
    out = []
    for name, ms in by.items():
        avg = sum(m["change"] for m in ms) / len(ms)
        L = links.get(name) or {}
        lk = L.get("corr")
        out.append({"name": name, "avg": avg, "n": len(ms), "link": lk, "link_label": _link_label(lk, L.get("lo")),
                    "link_lo": L.get("lo"), "link_hi": L.get("hi"), "link_n": L.get("n"),
                    "same": sum(1 for m in ms if m["change"] * sign > 0.003),
                    "members": sorted(ms, key=lambda m: -m["change"] * sign)})
    out.sort(key=lambda g: -(g["avg"] * sign) * (max(0.0, g["link"]) if g["link"] is not None else 0.3))
    return out


def _desk(split, ext_share, abn, unusual, ranked, chk3, basket, bz, conc, cal, sign) -> list[dict]:
    """한눈에 점검: ① 몫 나누기 → ② 기사 직후 검증 → ③ 수급, 그리고 참고 자료."""
    out = []

    def add(k, state, text):
        out.append({"k": k, "state": state, "text": text})
    if split and split.get("own") and ext_share is not None:
        txt = (f"실제 {_fmt_pct(split['own'])} = 시장 {_fmt_pct(split['market'])} + 업종 {_fmt_pct(split['sector'])} "
               f"+ 고유 {_fmt_pct(split['specific'])}")
        if ext_share >= 0.5:
            txt += f" → 바깥 흐름이 {min(100, ext_share*100):.0f}%"
            st = "yes"
        elif abn:
            txt += f" → 고유 몫이 평소의 {abs(abn['z']):.1f}배 (" + ("이례적" if unusual else "평소 범위") + ")"
            st = "yes" if unusual else "part"
        else:
            st = "part"
        add("① 몫 나누기", st, txt)
    else:
        add("① 몫 나누기", "no", "시장·업종 시세가 없어 나누지 못했습니다")
    news = [c for c in ranked if c["type"] in ("article", "pr", "disclosure")]
    passed = [c for c in news if c["checks"][1]["ok"]]
    if passed:
        add("② 기사 직후 검증", "yes", f"{passed[0]['title'][:50]} — {passed[0]['checks'][1]['text']}")
    elif news:
        add("② 기사 직후 검증", "part", f"직전 기사 {len(news)}건, 기사 직후 이례적 움직임은 확인 안 됨")
    else:
        add("② 기사 직후 검증", "no", "움직임 직전 이 종목 기사·공시 없음")
    if basket:
        add("③ 수급", "yes", f"{basket['who']} {basket['sector_word']} 묶음 매매 (이 종목 포함)")
    else:
        add("③ 수급", "yes" if chk3["ok"] else "no", chk3["text"][:80])
    if bz and bz.get("before", 0) >= 3 and (bz.get("ratio") or 0) >= 3:
        add("참고 · 토론방", "ref", f"움직임 전 2시간 글 {bz['before']}건 (평소의 {bz['ratio']:.0f}배)")
    if conc and conc["share_move"] >= 0.5 and conc["share_vol"] >= 0.3:
        add("참고 · 큰 주문", "ref", f"{conc['from']}~{conc['to']} 짧은 시간에 움직임의 {min(100, conc['share_move']*100):.0f}%")
    for c in cal or []:
        when = "당일" if c["gap"] == 0 else (f"{c['gap']}일 뒤" if c["gap"] > 0 else f"{-c['gap']}일 전")
        add("참고 · 일정", "ref", f"{c.get('kind', '회사 일정')} {when} ({c['date']})")
    return out


# ------------------------------------------------------------------ main

def analyze(event_id: int, stage: str = "1차", fetch=None) -> dict:
    """한 이상변동의 원인 후보를 계산해 events.cause_json 에 저장하고 돌려준다.

    fetch: 같은 업종 종목 분봉·뉴스를 그 자리에서 받아오는 함수 (감시 엔진이 넘겨줌).
    """
    with connect() as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not ev:
        raise LookupError("해당 이상변동이 없습니다.")
    ev = dict(ev)
    symbol = ev["symbol"]
    daily = ev.get("event_type") == "daily"
    t_start, t1 = _dt(ev["start_ts"]), _dt(ev["last_ts"])
    t0 = t_start - timedelta(minutes=5)            # 움직임 직전 기준
    direction = ev["direction"]                    # up / down
    day = t1.date()
    sign = 1 if direction == "up" else -1

    own_df = analysis.load_range(symbol, t1 - timedelta(days=5), t1 + timedelta(minutes=5))
    if daily:
        # 하루 이벤트: 전일 종가 → 종가. 원인 판단 기준 시각은 '하루 움직임의 절반에 처음 닿은 때'
        own = ev.get("peak_return_5m")
        kq = _daily_change(KOSDAQ, day.isoformat())
        t_start = _main_move_time(own_df, day, own) or t_start
        win = own_df[own_df["ts"].dt.date == day] if not own_df.empty else own_df
    else:
        own = _change(own_df, t0, t1)
        kq_df = analysis.load_range(KOSDAQ, t1 - timedelta(days=1), t1 + timedelta(minutes=5))
        kq = _change(kq_df, t0, t1)
        win = own_df[(own_df["ts"] > t0) & (own_df["ts"] <= t1)] if not own_df.empty else own_df
    window_volume = int(win["volume"].sum()) if not win.empty else 0
    prev_day = _prev_trading_day(symbol, day)
    look_from = min(t_start - timedelta(hours=18),
                    datetime.fromisoformat((prev_day or (day - timedelta(days=1)).isoformat()) + "T15:00"))

    cands = []
    notes = []

    # --- 1. 등록한 PR -------------------------------------------------------
    with connect() as conn:
        prs = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_events WHERE published_ts BETWEEN ? AND ?",
            (look_from.isoformat(timespec="minutes"),
             (t1 + timedelta(minutes=30)).isoformat(timespec="minutes")))]
    for p in prs:
        pt = _dt(p["published_ts"])
        lead = pt <= t_start + timedelta(minutes=1)
        gap = int((t_start - pt).total_seconds() // 60)
        score = _lead_score(pt, t_start, 95, 72, 52, 90) if lead else 35
        tone = _tone(p["title"])
        mismatch = bool(tone and sign and tone != sign)
        if mismatch:
            score -= 30
        summary = (f"PR 공개 {_gap_text(gap)} 뒤 움직임이 시작됐습니다." if lead
                   else f"PR({pt:%H:%M})보다 주가가 먼저 움직였습니다.")
        if mismatch:
            summary += " 다만 PR 내용과 주가 방향이 반대입니다."
        cands.append({
            "type": "pr", "ts": p["published_ts"], "score": score, "timing": "선행" if lead else "후행",
            "title": f"등록한 PR: {p['title']}",
            "summary": summary,
            "evidence": [f"공개 {pt:%m/%d %H:%M}, 움직임 시작 {t_start:%m/%d %H:%M}"],
            "links": [{"title": p["title"], "url": p.get("url")}] if p.get("url") else [],
        })

    # --- 2. 공시 ------------------------------------------------------------
    with connect() as conn:
        discl = [dict(r) for r in conn.execute(
            "SELECT * FROM disclosures WHERE symbol=? AND date IN (?, ?) ORDER BY date DESC",
            (symbol, day.isoformat(), prev_day or ""))]
    for d in discl:
        same_day = d["date"] == day.isoformat()
        seen = d.get("first_seen")
        seen_before = bool(seen) and _dt(seen) <= t_start and seen[:10] == d["date"]
        tone = _tone(d["title"] + " " + (d.get("summary") or ""))
        score = 85 if same_day else 70
        if tone and sign and tone != sign:
            score -= 30
        cands.append({
            "type": "disclosure", "score": score, "timing": "선행" if (seen_before or not same_day) else "확인 필요",
            "title": d["title"],
            "summary": ("이날 나온 공시입니다." if same_day else "직전 거래일에 나온 공시입니다.")
                       + (" 공시 정확한 시각은 DART에서 확인해 주세요." if not seen_before else ""),
            "evidence": [x for x in [
                f"공시일 {d['date']} ({d.get('kind') or '공시'})",
                (d.get("summary") or "")[:160] or None,
            ] if x],
            "links": [{"title": "DART에서 보기", "url": "https://dart.fss.or.kr/"}],
        })

    # --- 3. 이 종목 기사 (회사 발표·언론 분석·리서치·테마 기사 모두) ---------------
    from . import articles as _articles
    try:
        if fetch:
            fetch("articles", symbol, day)
    except Exception:
        pass
    with connect() as conn:
        arts = [dict(r) for r in conn.execute(
            "SELECT * FROM articles WHERE symbol=? AND hidden=0 AND ts BETWEEN ? AND ? ORDER BY ts",
            (symbol, look_from.isoformat(timespec="minutes"),
             (t1 + timedelta(minutes=90)).isoformat(timespec="minutes")))]
    news_view = []
    seen_story = set()
    CAT_ADJ = {"회사 발표": 0, "리서치": -2, "언론 분석": -4, "테마·수혜주": -14, "시황·특징주": -28, "단순 언급": -30}
    for n in arts:
        nt = _dt(n["ts"])
        lead = nt <= t_start + timedelta(minutes=1)
        mins = int((t_start - nt).total_seconds() // 60)
        cat = n.get("category") or "단순 언급"
        tone = n.get("tone") or 0
        news_view.append({"ts": n["ts"], "office": n["office"], "title": n["title"], "url": n["url"],
                          "timing": "선행" if lead else "후행", "direct": n.get("mention") == "제목",
                          "symbol": symbol, "category": cat, "tone": tone})
        if not lead or (n.get("story") and n["story"] in seen_story):
            continue
        seen_story.add(n.get("story") or n["id"])
        score = _lead_score(nt, t_start, 84, 68, 52, 78) + CAT_ADJ.get(cat, -20)
        peer_ipo = cat == "단순 언급" and any(w in n["title"] for w in ("IPO", "상장", "공모", "몸값"))
        if peer_ipo:
            score += 6
        if tone and sign:
            score += 8 if tone == sign else -25
        session_open = datetime.combine(t_start.date(), datetime.min.time()) + timedelta(hours=9)
        tone_txt = {1: "호재 성격", -1: "악재 성격"}.get(tone, "")
        cands.append({
            "type": "article", "ts": n["ts"], "category": cat, "score": score, "timing": "선행",
            "title": n["title"],
            "summary": (f"움직임 {_gap_text(mins)} 전에 나온 {cat} 기사입니다." if nt >= session_open
                        else f"장 시작 전(전날 저녁~아침)에 나온 {cat} 기사입니다.")
                       + (f" 내용은 {tone_txt}입니다." if tone_txt else "")
                       + (" 기사 방향과 주가 방향이 반대입니다." if tone and sign and tone != sign else "")
                       + (" 회사가 낸 기사가 아닌 언론사 자체 기사입니다." if cat == "언론 분석" else "")
                       + (" 이 종목이 비교 기업으로 언급된 동종업체 상장 기사입니다. 동종업체의 몸값 평가가 이 종목 재평가로 이어지기도 합니다."
                          if peer_ipo else ""),
            "evidence": [f"{n['office']} {nt:%m/%d %H:%M}" + (f", 같은 내용 {n['outlets']}개 매체" if (n.get('outlets') or 1) > 1 else "")]
                        + ([n["body"][:140]] if n.get("body") else []),
            "links": [{"title": n["title"], "url": n["url"]}],
        })
    late = [n for n in news_view if n["timing"] == "후행"]
    if late and not any(c["type"] == "article" for c in cands):
        notes.append(f"구간 뒤에 나온 기사 {len(late)}건은 주가가 먼저 움직인 뒤 확인된 정보라 원인 후보에서 뺐습니다.")

    # --- 4. 업종 동반 움직임 -------------------------------------------------
    with connect() as conn:
        rel = [dict(r) for r in conn.execute("SELECT * FROM related_stocks ORDER BY source DESC, code")]
    if fetch and rel:
        try:
            fetch("related_bars", [r["code"] for r in rel], day)
        except Exception as e:  # 보조 정보
            notes.append(f"같은 업종 종목 시세를 받지 못했습니다: {e}")
    related = []
    for r in rel:
        if daily:
            ch = _daily_change(r["code"], day.isoformat())
        else:
            df = analysis.load_range(r["code"], t1 - timedelta(days=1), t1 + timedelta(minutes=5))
            ch = _change(df, t0, t1)
        related.append({"code": r["code"], "name": r["name"], "change": ch, "source": r["source"]})
    valid = [r for r in related if r["change"] is not None]
    from .detector import load_settings as _ls
    _cfg = _ls()
    groups = {seg: [x["code"] for x in items] for seg, items in (_cfg.get("peer_groups") or {}).items()}
    try:
        seg_links = _segment_links(symbol, day, groups) if groups else {}
    except Exception:
        seg_links = {}
    segments = _segments(related, sign, seg_links)
    background = []          # 시장·업종이 왜 움직였는지 보여주는 소식 (② 바깥 흐름 쪽 근거)
    co_names = ""
    if valid and own is not None:
        same = [r for r in valid if r["change"] * (own if own else sign) > 0 and abs(r["change"]) >= 0.005]
        avg = sum(r["change"] for r in valid) / len(valid)
        frac = len(same) / len(valid)
        if frac >= 0.5 and avg * sign > 0 and abs(avg) >= max(0.005, 0.25 * abs(own)):
            co_names = ", ".join(f"{r['name']} {_fmt_pct(r['change'])}" for r in sorted(same, key=lambda r: -abs(r["change"]))[:4])
            # 같이 움직인 종목의 뉴스 중 구간 직전 기사 (업종·정책 이슈 단서)
            peer_news = []
            if fetch:
                for r in sorted(same, key=lambda r: -abs(r["change"]))[:3]:
                    try:
                        fetch("news", r["code"], day)
                    except Exception:
                        pass
            with connect() as conn:
                for r in same[:6]:
                    for n in conn.execute(
                            "SELECT * FROM news WHERE symbol=? AND ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 2",
                            (r["code"], look_from.isoformat(timespec="minutes"),
                             (t_start + timedelta(minutes=1)).isoformat(timespec="minutes"))):
                        peer_news.append({**dict(n), "peer": r["name"]})
            for n in peer_news:
                news_view.append({"ts": n["ts"], "office": n["office"], "title": n["title"], "url": n["url"],
                                  "timing": "선행", "direct": False, "symbol": n["symbol"], "peer": n["peer"]})
            background += [{"ts": n["ts"], "text": f"{n['peer']} 관련 기사: {n['title']}", "title": n["title"], "url": n["url"]}
                           for n in peer_news[:3]]
    elif not valid:
        notes.append("이 시간대 같은 업종 종목 시세가 없어 업종 비교를 하지 못했습니다.")
    if kq is None:
        notes.append("이 시간대 KOSDAQ 시세가 없어 시장 비교를 하지 못했습니다.")

    # --- 6. 수급: 누가 샀고 누가 팔았나 (③) --------------------------------------
    flows = _flows(symbol, _dt(ev["start_ts"]) if daily else t_start, t1, window_volume)
    inv = _investor_day(symbol, day.isoformat())
    # 창구 비교 종목: 사업 분야별 비교 종목 + 네이버 방위산업·우주항공 테마 대형 종목
    from . import macro as _macro
    trader_peers = [dict(r) for r in rel]
    for x in _macro.basket_codes():
        if x["code"] != symbol and all(x["code"] != r["code"] for r in trader_peers):
            trader_peers.append({"code": x["code"], "name": x["name"], "source": x["segment"]})
    buyer = None
    if flows:
        try:
            buyer = _buyer_context(symbol, day, flows, sign, trader_peers, fetch)
        except Exception as e:
            notes.append(f"매수 주체 단서를 정리하지 못했습니다: {e}")
    if flows and window_volume > 0:
        fn = flows["foreign_net"]
        if fn * sign > 0 and abs(fn) >= 0.2 * window_volume and flows["basis"] == "구간":
            cands.append({
                "type": "flow", "kind": "foreign", "timing": "동시",
                "title": f"외국계 증권사 창구 순{'매수' if fn > 0 else '매도'}",
                "summary": f"구간 거래량 {window_volume:,}주 중 외국계 창구가 {abs(fn):,}주를 순{'매수' if fn > 0 else '매도'}했습니다.",
                "evidence": [", ".join(f"{r['name']} {r['net']:+,}주" for r in
                                       (flows['top_buyers'] if fn > 0 else flows['top_sellers']) if r["foreign"]) or "-"],
                "links": [],
            })
        lead = (flows["top_buyers"] if sign >= 0 else flows["top_sellers"])[:1]
        if lead and flows["basis"] == "구간" and abs(lead[0]["net"]) >= 0.3 * window_volume and not lead[0]["foreign"]:
            r = lead[0]
            cands.append({
                "type": "flow", "kind": "broker", "timing": "동시",
                "title": f"{r['name']} 창구에 {'매수' if r['net'] > 0 else '매도'} 집중",
                "summary": f"구간 거래량의 {abs(r['net']) / window_volume * 100:.0f}%가 한 증권사 창구에서 순{'매수' if r['net'] > 0 else '매도'}됐습니다.",
                "evidence": [f"{r['name']} 매수 {r['buy']:,}주 / 매도 {r['sell']:,}주"], "links": [],
            })
    basket = None
    if buyer:
        vol = window_volume or (inv or {}).get("volume") or 0
        share = buyer["qty"] / vol if vol else 0
        who = f"{buyer['broker']} 창구" + ("(외국계)" if buyer["foreign"] else "")
        sp = sorted(buyer.get("same_peers") or [], key=lambda x: -abs(x["net"]))
        peers_txt = " · ".join(f"{x['name']} {x['net']:+,}주" for x in sp[:4])
        if buyer.get("basket"):
            seg_of = {r["code"]: r.get("source") for r in trader_peers}
            segs = []
            for x in sp:
                g = seg_of.get(x["code"])
                if g and g not in segs:
                    segs.append(g)
            sector_word = ("·".join(segs[:2]) + " 분야") if segs else "같은 업종"
            signed = buyer["qty"] if buyer["side"] == "매수" else -buyer["qty"]
            basket = {
                "type": "flow", "kind": "basket", "timing": "동시", "who": who, "sector_word": sector_word, "segments": segs,
                "title": f"{who}, {sector_word} 묶음 {buyer['side']} (이 종목 {signed:+,}주 · {peers_txt})",
                "summary": f"같은 날 {buyer['broker']} 창구가 이 종목과 함께, 거래원을 확인한 비교 종목 {len(buyer['peers'])}개 중 {len(sp)}개를 순{buyer['side']}했습니다. "
                           f"이 종목 하나가 아니라 {'외국계 자금이 ' if buyer['foreign'] else ''}업종을 묶어 사고판 흐름입니다. "
                           f"이 종목에서 이 창구 물량은 {'구간' if buyer.get('basis') == '구간' else '그날'} 거래량의 약 {share*100:.0f}%입니다.",
                "evidence": [f"이 종목 {buyer['side']} {buyer['qty']:,}주" + (f" (약 {_eok(buyer['amount'])})" if buyer.get("amount") else "")]
                            + [f"{x['name']} 순{'매수' if x['net'] > 0 else '매도'} {abs(x['net']):,}주" + (f" (약 {_eok(abs(x['amount']))})" if x.get("amount") else "") for x in sp[:4]]
                            + ([f"거래소 확정 외국인 순매수 {inv.get('foreign_net') or 0:+,}주"] if inv and buyer["foreign"] else []),
                "links": [],
            }
            cands.append(basket)
        elif share >= 0.1:
            cands.append({
                "type": "flow", "kind": "focus", "timing": "동시",
                "title": f"{who} 이 종목 집중 {buyer['side']} ({buyer['qty']:,}주, 거래량의 {share*100:.0f}%)",
                "summary": "같은 업종 종목에서는 이 창구의 같은 방향 매매가 보이지 않아, 업종보다 이 종목 자체에 대한 매매입니다.",
                "evidence": [f"같은 업종 확인 {len(buyer['peers'])}개 중 같은 방향 {len(sp)}개"], "links": [],
            })
    if inv and daily:
        vol = inv.get("volume") or window_volume or 0
        for key, who in (("organ_net", "기관"), ("foreign_net", "외국인")):
            v = inv.get(key) or 0
            if vol and v * sign > 0 and abs(v) >= 0.12 * vol:
                cands.append({
                    "type": "flow", "kind": "investor", "timing": "동시",
                    "title": f"{who} 순{'매수' if v > 0 else '매도'} (거래량의 {abs(v)/vol*100:.0f}%)",
                    "summary": f"이날 {who}이 {abs(v):,}주를 순{'매수' if v > 0 else '매도'}했습니다 (거래소 확정).",
                    "evidence": [f"외국인 {inv.get('foreign_net') or 0:+,} / 기관 {inv.get('organ_net') or 0:+,} / 개인 {inv.get('individual_net') or 0:+,}주"],
                    "links": [],
                })
    if not flows:
        notes.append("이 시간대 거래원 기록이 없습니다. 거래원은 감시 엔진이 켜져 있을 때 5분마다, 장 마감 기록은 매일 쌓입니다.")

    # --- ① 몫 나누기: 실제 = 시장 몫 + 업종 몫 + 이 종목 고유 몫 ---------------------
    split = _decompose_window(symbol, day, own, kq, related, daily)
    from . import causal as _causal
    try:
        factor = _causal.factor_attribution(symbol, day.isoformat(), _cfg.get("factor_etfs") or [])
    except Exception as e:
        factor = {"ok": False, "reason": str(e)}
    if daily and factor and factor.get("ok") and len(factor["parts"]) > 1:
        # 하루 이벤트: 시장(KOSDAQ) + 업종 ETF 회귀 (월스트리트 리스크 모델 방식)
        mk = factor["parts"][0]["contrib"]
        sc_ = sum(x["contrib"] for x in factor["parts"][1:])
        split = {"own": factor["actual"], "market": mk + factor["alpha"], "sector": sc_, "specific": factor["specific"],
                 "z": factor["z"], "kosdaq": factor["parts"][0]["factor"], "sector_avg": (split or {}).get("sector_avg"),
                 "method": "팩터", "beta_market": factor["parts"][0]["beta"], "beta_sector": None,
                 "sigma": factor["sigma"], "basis": "하루", "factor_parts": factor["parts"]}
    causal = {"factor": factor}
    try:
        causal["intensity"] = _causal.trading_intensity(symbol, day.isoformat())
    except Exception:
        causal["intensity"] = None
    es = None
    if daily and not (split and split.get("method") == "팩터"):
        try:
            es = _causal.event_study(symbol, day.isoformat())   # 업종 ETF 기록이 모자랄 때 시장만 감안한 대체 계산
        except Exception as e:
            es = {"ok": False, "reason": str(e)}
        causal["event_study"] = es
    move_own = (split or {}).get("own", own)
    spec = (split or {}).get("specific", own)
    # 고유 몫이 평소에 비해 얼마나 드문가 (|z| 2 이상 = 100번 중 5번 미만)
    abn = None
    if daily and split and split.get("z") is not None:
        abn = {"z": split["z"], "p": _causal._p_norm(split["z"]),
               "basis": f"평소 하루 고유 변동 ±{(split.get('sigma') or 0)*100:.1f}% 대비"}
    elif daily and es and es.get("ok"):
        abn = {"z": es["t"], "p": es["p"], "basis": f"평소 하루 변동 ±{es['sigma']*100:.1f}% 대비 (시장만 감안)"}
    elif not daily and spec is not None:
        mins = max(5, int((t1 - t0).total_seconds() // 60))
        try:
            rr = _causal.window_rarity(symbol, t1, mins, spec)
        except Exception:
            rr = None
        if rr and rr.get("ok"):
            abn = {"z": rr["z"], "p": rr["p"], "basis": f"평소 {mins}분 움직임 {rr['n']}개 대비"}
    causal["abnormal"] = abn
    ext_share = None
    if split and split.get("own"):
        ext_share = (split["market"] + split["sector"]) / split["own"]
        split["external_share"] = ext_share
        if abn:
            split["z"], split["abn_p"], split["abn_basis"] = abn["z"], abn["p"], abn["basis"]
    external_led = ext_share is not None and ext_share >= 0.5
    unusual = bool(abn and abn["p"] < 0.05 and spec is not None and spec * sign > 0)
    own_led = (not external_led) and unusual

    # --- 바깥 흐름의 배경 소식 (테마·업종 시황·거시 이슈) ----------------------------
    from . import macro as _macro
    if fetch:
        for k in ("macro_backfill", "board"):
            try:
                fetch(k, symbol, day)
            except Exception as e:
                notes.append(f"자료 보충 실패({k}): {e}")
    macro_ctx = None
    try:
        macro_ctx = _macro.context(symbol, t_start, t1, sign, buyer, own)
    except Exception as e:
        notes.append(f"거시·테마 배경을 정리하지 못했습니다: {e}")
    if macro_ctx:
        st = macro_ctx.get("strong_theme")
        if st:
            background.append({"ts": None, "text": f"'{st['name']}' 테마 전체 {st['change_rate']:+.2f}% ({st['n_themes']}개 테마 중 {st['rank']}위)",
                               "title": None, "url": None})
        geo = _macro.supporting_topics(macro_ctx.get("topics", []), sign)
        BIZ = {"방산", "광학", "우주", "소재", "반도체"}
        sector_moved = bool(split and split.get("sector") is not None and move_own
                            and split["sector"] * sign > 0 and abs(split["sector"]) >= 0.25 * abs(move_own)) or bool(st)
        market_wide = []
        for g in geo:
            if g["relevance"] not in BIZ:
                market_wide.append(g)
                continue
            if not sector_moved:
                continue                       # 업종이 실제로 같이 움직인 날만 업종 배경으로 인정
            for i in ([i for i in g["items"] if i["lead"]] or g["items"])[:2]:
                background.append({"ts": i["ts"], "text": f"{g['topic']}: {i['title']} ({i['office']})", "title": i["title"], "url": i["url"]})
        macro_ctx["market_wide"] = [{"topic": g["topic"], "items": [i for i in g["items"] if i["lead"]][:2]} for g in market_wide[:3]]
        if split and move_own and split["market"] * sign > 0 and abs(split["market"]) >= 0.3 * abs(move_own):
            for m in macro_ctx["market_wide"]:
                if m["items"]:
                    i = m["items"][0]
                    background.append({"ts": i["ts"], "text": f"시장 전체 이슈({m['topic']}): {i['title']}", "title": i["title"], "url": i.get("url")})
        try:
            sn = _macro.sector_news(look_from, t1, [r["name"] for r in rel])
        except Exception:
            sn = []
        sn_dir = [x for x in sn if x["tone"] * sign > 0 and x["ts"] <= t1.isoformat(timespec="minutes")]
        if sn:
            macro_ctx["sector_news"] = [{k: x[k] for k in ("ts", "title", "office", "url", "hit")} for x in sn[:6]]
        background = [{"ts": x["ts"], "text": f"업종 시황: {x['title']} ({x['office']})", "title": x["title"], "url": x["url"]}
                      for x in sn_dir[:3]] + background
    try:
        art_ctx = _articles.before_event(symbol, t_start, t1)
    except Exception as e:
        art_ctx = None
        notes.append(f"직전 기사 반응을 계산하지 못했습니다: {e}")

    # --- 업종이 왜 움직였나: 묶음 매수·시장업종 흐름의 배경 이슈 (추정) ---------------------
    from . import newssearch as _ns
    prev_close = datetime.fromisoformat((prev_day or (day - timedelta(days=1)).isoformat()) + "T15:30")
    seg_avg_all = {g["name"]: g["avg"] for g in segments}
    drv_seg = None
    if basket and basket.get("segments"):
        drv_seg = basket["segments"][0]
    elif split and move_own and split["market"] * sign > 0 and abs(split["market"]) >= 0.5 * abs(move_own) \
            and abs(split["market"]) >= abs(split["sector"]):
        drv_seg = "시장"                                    # 시장 전체가 끌고 간 날은 시장 전체 이슈를 찾음
    elif split and split.get("factor_parts"):
        big_f = max(split["factor_parts"][1:], key=lambda x: x["contrib"] * sign, default=None)
        if big_f and big_f["contrib"] * sign > 0:
            kw = "방산" if "방산" in big_f["name"] else "반도체" if "반도체" in big_f["name"] else None
            drv_seg = next((g["name"] for g in segments if kw and kw in g["name"]), kw)
    if not drv_seg:
        moved = [g for g in segments if g["avg"] * sign >= 0.01 and (g["link_lo"] is None or g["link_lo"] > 0)]
        drv_seg = max(moved, key=lambda g: g["avg"] * sign)["name"] if moved else None
    why, why_ok, why_text = None, False, None
    if drv_seg:
        try:
            why = _macro.drivers(drv_seg, min(prev_close, look_from), t_start, t1, sign)
        except Exception as e:
            notes.append(f"배경 이슈를 찾지 못했습니다: {e}")
    seg_move = (kq if drv_seg == "시장" else seg_avg_all.get(drv_seg)) if drv_seg else None
    if why and why.get("top"):
        top_t = why["top"]
        it = (top_t.get("items") or [None])[0]
        if top_t["n_lead"]:
            why_ok = seg_move is None or seg_move * sign >= 0.01
            why_text = (f"움직임 전 '{top_t['topic']}' 기사 {top_t['n_lead']}건"
                        + (f" — 「{it['title'][:45]}」({it['office']} {it['ts'][5:16].replace('T', ' ')})" if it else ""))
        else:
            why_text = f"그날 {'증시' if drv_seg == '시장' else drv_seg} 시황 기사가 꼽은 이유: '{top_t['topic']}' (움직임 전 기사는 없음)"
        if top_t["n_cited"] and top_t["n_lead"]:
            why_text += f" · 그날 {'증시' if drv_seg == '시장' else drv_seg} 시황 기사 {top_t['n_cited']}건도 이 이유를 꼽음"
        if seg_move is not None:
            why_text += (f" · KOSDAQ {_fmt_pct(seg_move)}" if drv_seg == "시장" else f" · {drv_seg} 비교 종목 평균 {_fmt_pct(seg_move)}")
    elif drv_seg and not _ns.enabled():
        notes.append("국제·정치 기사 수집이 꺼져 있어, 증권 뉴스에 나온 이슈만 배경으로 확인했습니다.")
    if buyer and why_text:
        buyer["lines"].insert(1, {"kind": "배경 이슈", "strong": why_ok, "text": f"{'시장 전체가' if drv_seg == '시장' else drv_seg + ' 업종이'} 움직인 배경(추정): {why_text}",
                                  **({"url": why['top']['items'][0]['url']} if why and why['top'].get('items') else {})})

    # --- 참고 자료 (순위에는 넣지 않음): 종목토론방 · 큰 주문 · 회사 일정 ---------------
    from . import board as _board
    try:
        bz = _board.buzz(symbol, t_start, t1)
    except Exception as e:
        bz = None
        notes.append(f"종목토론방 글을 확인하지 못했습니다: {e}")
    conc = _concentration(own_df, _dt(ev["start_ts"]) if daily else t_start, t1, own)
    cal = _calendar(day, sign)

    # --- ② 기사 직후 검증: 발표 직후 30분(장 마감 뒤면 다음 날 시초가)이 평소보다 드물게 움직였나 --
    for c in cands:
        if c["type"] in ("article", "pr", "disclosure") and c.get("ts"):
            try:
                nw = _causal.narrow_window(symbol, c["ts"])
            except Exception:
                nw = None
            if nw and nw.get("ok"):
                c["test"] = nw

    # --- 판단: 세 가지 근거로 순위와 신뢰도를 정함 -------------------------------------
    ti = causal.get("intensity") or {}
    heavy = bool(ti.get("vol_x") and ti["vol_x"] >= 2)
    own_flows = [c for c in cands if c["type"] == "flow" and c.get("kind") != "basket"]
    if spec is None:
        chk1_own = {"ok": False, "text": "고유 몫을 계산할 시세가 없습니다"}
    elif external_led:
        chk1_own = {"ok": False, "text": f"움직임의 {min(100, ext_share*100):.0f}%가 시장·업종 몫이라 회사 고유 움직임은 작습니다"}
    elif abn is None:
        chk1_own = {"ok": False, "text": f"고유 몫 {_fmt_pct(spec)} (평소와 비교할 기록 부족)"}
    else:
        chk1_own = {"ok": unusual, "text": f"고유 몫 {_fmt_pct(spec)}, 평소의 {abs(abn['z']):.1f}배 ({abn['basis']}) → "
                                          + ("이례적" if unusual else "평소에도 있을 만한 크기")}
    flow_bits = []
    if heavy:
        flow_bits.append(f"거래량 평소의 {ti['vol_x']:.1f}배")
    flow_bits += [c["title"] for c in own_flows[:2]]
    chk3_own = {"ok": bool(flow_bits), "text": " · ".join(flow_bits) if flow_bits else "뚜렷한 매수·매도 쏠림이나 거래 급증 없음"}

    ranked = []
    # 바깥 흐름(시장·업종) 후보
    if split and ext_share is not None and ext_share >= 0.2:
        parts = split.get("factor_parts") or []
        big = max(parts[1:], key=lambda x: x["contrib"] * sign, default=None) if len(parts) > 1 else None
        bg = sorted([b for b in background if b.get("ts")], key=lambda b: b["ts"])[:3] + [b for b in background if not b.get("ts")][:1]
        same_basket = basket if basket else None
        checks = [
            {"step": "①", "ok": external_led, "text": f"실제 {_fmt_pct(split['own'])} 중 시장 {_fmt_pct(split['market'])} + 업종 {_fmt_pct(split['sector'])} "
                                                       f"= 바깥 흐름 몫 {min(100, max(0, ext_share)*100):.0f}%"},
            {"step": "②", "ok": why_ok or bool(bg),
             "text": (f"{'시장 전체가' if drv_seg == '시장' else drv_seg + ' 업종이'} 움직인 배경: {why_text}" if why_ok else
                      ("업종·시장을 움직인 소식: " + bg[0]["text"]) if bg else
                      (why_text or "업종·시장이 왜 움직였는지 알려주는 기사는 찾지 못함"))},
            {"step": "③", "ok": bool(same_basket), "text": same_basket["title"] if same_basket else "업종을 묶어 사고판 창구는 확인 안 됨"},
        ]
        share_word = "대부분" if ext_share >= 0.75 else "절반가량" if ext_share >= 0.4 else "일부"
        if external_led and same_basket:
            title = f"{same_basket['who']}의 {same_basket['sector_word']} 묶음 {buyer['side']} → 업종 전체 {'강세' if sign > 0 else '약세'}에 함께 실림"
        else:
            title = f"시장·업종 전체 흐름 ({share_word})" + (f" — {big['name']} 몫이 가장 큼" if big and big["contrib"] * sign > 0 else "")
        ranked.append({
            "type": "market", "key": "①", "timing": "동시", "checks": checks, "title": title,
            "summary": f"실제 {_fmt_pct(split['own'])} 중 {share_word}({min(100, max(0, ext_share)*100):.0f}%)가 시장·업종 흐름으로 설명됩니다."
                       + ("" if ext_share >= 0.75 else f" 나머지 이 종목 고유 몫 {_fmt_pct(split['specific'])}는 다른 원인을 봐야 합니다."),
            "evidence": ([f"왜 움직였나(추정): {why_text}"] if why_text else [])
                        + ((same_basket or {}).get("evidence") or [])[:4] + ([f"같이 움직인 종목: {co_names}"] if co_names else [])
                        + [b["text"] for b in bg],
            "links": ([{"title": i["title"], "url": i["url"]} for i in (why["top"]["items"][:2] if why and why.get("top") else [])]
                      + [{"title": b["title"], "url": b["url"]} for b in bg if b.get("url")])[:4],
        })
        if external_led and same_basket:
            cands = [c for c in cands if c is not same_basket]      # 바깥 흐름 후보 안에 합침
    for c in cands:
        if c["type"] in ("article", "pr", "disclosure"):
            t = c.get("test")
            if c.get("category") in ("시황·특징주", "단순 언급") and not (t and t["p"] < 0.05):
                continue                       # 주가 움직임을 뒤따라 쓰는 기사 유형은 검증을 통과할 때만
            tone = _tone(c["title"])
            if t:
                passed = t["p"] < 0.05 and t["ret"] * sign > 0
                c2 = {"ok": passed, "text": f"{t['window']} {_fmt_pct(t['ret'])}: 평소 같은 길이 구간 {t['n_null']}개 중 이만큼 움직인 경우 "
                                            f"{t['p']*100:.0f}% → " + ("기사 직후 이례적 움직임" if passed else
                                                                         "방향이 반대" if t["ret"] * sign <= 0 else "평소에도 있는 움직임")}
            else:
                c2 = {"ok": False, "text": "기사 직후 시세 기록이 없어 검증하지 못함"}
            if tone and tone != sign:
                c2 = {"ok": False, "text": "기사 내용(호재·악재)과 주가 방향이 반대"}
            c["checks"] = [{"step": "①", **chk1_own}, {"step": "②", **c2}, {"step": "③", **chk3_own}]
            c["key"] = "②"
            ranked.append(c)
        elif c["type"] == "flow":
            if c.get("kind") == "basket":
                c2 = {"ok": why_ok, "text": (f"왜 샀을까(추정) — {why_text}" if why_text
                                             else "업종을 묶어 산 이유를 알려주는 이슈 기사는 찾지 못함")}
                if why_text:
                    c["evidence"] = [f"왜 샀을까(추정): {why_text}"] + c.get("evidence", [])
                    c["links"] = [{"title": i["title"], "url": i["url"]} for i in why["top"]["items"][:2]] + c.get("links", [])
            else:
                c2 = {"ok": False, "text": "누가 샀는지는 보이지만, 왜 샀는지 알려주는 기사·공시는 확인 안 됨"}
            c["checks"] = [{"step": "①", **chk1_own}, {"step": "②", **c2},
                           {"step": "③", "ok": True, "text": c["title"] + (f" · 거래량 평소의 {ti['vol_x']:.1f}배" if heavy else "")}]
            c["key"] = "①"
            ranked.append(c)
    RANK = {"높음": 3, "중간": 2, "낮음": 1}
    for c in ranked:
        ok = {x["step"]: x["ok"] for x in c["checks"]}
        n_ok = sum(ok.values())
        if not ok.get(c["key"]):
            conf = "낮음"
        elif c["type"] == "flow" and not (c.get("kind") == "basket" and ok.get("②")):
            conf = "중간"                       # '누가'만 알고 '왜'는 모름 → 최대 중간 (묶음 매수의 배경 이슈가 확인되면 예외)
        else:
            conf = "높음" if n_ok >= 2 else "중간"
        c["n_ok"] = n_ok
        c["confidence"] = conf
        prio = ({"market": 3, "article": 2, "pr": 2, "disclosure": 2, "flow": 1} if external_led
                else {"article": 3, "pr": 3, "disclosure": 3, "flow": 2, "market": 1}).get(c["type"], 0)
        c["score"] = RANK[conf] * 30 + n_ok * 3 + prio
        c["label"] = TYPE_LABEL.get(c["type"], c["type"])
        if c["type"] == "article":
            c["label"] = c.get("category") or "이 종목 기사"
        if c["type"] == "market":
            c["label"] = "시장·업종 흐름"
    ranked.sort(key=lambda c: -c["score"])
    top = ranked[:4]
    if not top or top[0]["confidence"] == "낮음":
        bits = [chk1_own["text"], chk3_own["text"]]
        if inv:
            bits.append(f"투자자별 순매수 외국인 {inv.get('foreign_net') or 0:+,} / 기관 {inv.get('organ_net') or 0:+,} / 개인 {inv.get('individual_net') or 0:+,}주")
        top = [{
            "type": "own", "label": "원인 미확인", "score": 0, "confidence": "낮음", "timing": "동시", "n_ok": 0,
            "title": "원인 미확인" + (f" (고유 움직임 {_fmt_pct(spec)}, 평소의 {abs(abn['z']):.1f}배)" if abn and spec is not None else ""),
            "summary": "시장·업종 흐름, 기사 직후 움직임, 수급 중 어느 것도 이 움직임을 충분히 설명하지 못했습니다. "
                       "거래가 적은 종목이라 일부 투자자의 매매만으로 움직였을 수 있습니다."
                       + (" 아래 후보는 근거가 약한 참고용입니다." if top else ""),
            "evidence": [b for b in bits if b], "links": [], "checks": [],
        }] + top[:3]
    best = top[0]
    headline = best["title"]
    desk = _desk(split, ext_share, abn, unusual, ranked, chk3_own, basket, bz, conc, cal, sign)
    lead_txt = ""
    if split and split.get("own"):
        lead_txt = (f"실제 {_fmt_pct(split['own'])} = 시장 {_fmt_pct(split['market'])} + 업종 {_fmt_pct(split['sector'])} "
                    f"+ 이 종목 고유 {_fmt_pct(split['specific'])}. ")
    if best["type"] == "own":
        summary_line = lead_txt + "세 가지 근거 모두 이 움직임을 충분히 설명하지 못했습니다."
    else:
        ck = " · ".join(f"{x['step']} {'확인' if x['ok'] else '없음'}" for x in best.get("checks") or [])
        summary_line = lead_txt + f"근거: {ck}."
    if split and ext_share is not None and not external_led and abn and not unusual:
        notes.append(f"고유 몫이 평소에도 있을 만한 크기입니다 ({abn['basis']} {abs(abn['z']):.1f}배). 특별한 원인 없이 생긴 움직임일 수 있습니다.")
    if es and es.get("ok") and es.get("pre_drift"):
        notes.append("이벤트 전 5거래일 동안 이미 같은 방향의 비정상적인 움직임이 있었습니다 (사전 누출·선반영 가능성).")

    result = {
        "stage": stage,
        "analyzed_at": datetime.now().isoformat(timespec="minutes"),
        "method": "3단계",
        "headline": headline,
        "confidence": best["confidence"],
        "summary_line": summary_line,
        "split": split,
        "causal": causal,
        "desk": desk,
        "segments": [{k: g[k] for k in ("name", "avg", "n", "same", "link", "link_label", "link_lo", "link_hi", "link_n")} for g in segments],
        "board": bz,
        "orders": conc,
        "calendar": cal,
        "move": {"own": own, "kosdaq": kq, "window_volume": window_volume,
                 "from": t0.isoformat(timespec="minutes"), "to": t1.isoformat(timespec="minutes")},
        "candidates": top,
        "flows": flows,
        "buyer": buyer,
        "investor_day": inv,
        "macro": macro_ctx,
        "drivers": why,
        "articles": art_ctx,
        "related": related,
        "news": sorted(news_view, key=lambda n: n["ts"]),
        "disclosures": discl,
        "notes": notes,
    }
    with connect() as conn:
        conn.execute("UPDATE events SET cause_json=?, cause_stage=?, cause_updated_at=? WHERE id=?",
                     (json.dumps(result, ensure_ascii=False, default=str), stage, result["analyzed_at"], event_id))
    return result


def stored(event_id: int):
    with connect() as conn:
        r = conn.execute("SELECT cause_json FROM events WHERE id=?", (event_id,)).fetchone()
    if not r or not r[0]:
        return None
    try:
        return json.loads(r[0])
    except ValueError:
        return None


def upgrade_reanalysis(symbol: str, now: datetime, log=None):
    """분석 방식이 바뀌면 저장된 이상변동 전체를 다시 분석하도록 표시."""
    from .db import get_state, set_state
    st = get_state("cause_version")
    if st and st.get("value") == CAUSE_VERSION:
        return
    with connect() as conn:
        n = conn.execute("UPDATE events SET cause_stage=NULL WHERE symbol=? AND start_ts >= ?",
                         (symbol, (now - timedelta(days=400)).isoformat(timespec="minutes"))).rowcount
    set_state("cause_version", CAUSE_VERSION)
    if log and n:
        log.info("분석 방식 업데이트: 이상변동 %d건을 다시 분석합니다", n)


def refresh_pending(symbol: str, now: datetime, fetch=None, log=None) -> int:
    """기록 단계 관리.
    1차    : 감지 직후
    준최종 : 움직임이 끝나고 15분 뒤 (추가 기사·공시·업종 흐름 반영)
    최종   : 그날 장 마감(18시) 이후 (투자자별 순매수 확정분 반영)
    """
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, start_ts, last_ts, status, cause_stage FROM events WHERE symbol=? "
            "AND (cause_stage IS NULL OR cause_stage != '최종') ORDER BY start_ts", (symbol,))]
    n = 0
    for r in rows:
        last = _dt(r["last_ts"])
        day_final = datetime.combine(last.date(), datetime.min.time()) + timedelta(hours=18)
        want = None
        if now >= day_final:
            want = "최종"
        elif r["status"] == "closed" and now - last >= timedelta(minutes=15):
            want = "준최종"
        elif r["cause_stage"] is None:
            want = "1차"
        if want and want != r["cause_stage"]:
            try:
                analyze(r["id"], want, fetch=fetch)
                n += 1
                if log:
                    log.info("원인 분석 %s: 이벤트 #%s", want, r["id"])
            except Exception as e:
                if log:
                    log.warning("원인 분석 실패 #%s: %s", r["id"], e)
    return n
