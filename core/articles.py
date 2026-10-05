"""이 종목이 다뤄진 기사 전체를 모으고, 기사마다 주가 반응을 계산.

회사가 낸 보도자료뿐 아니라 언론의 분석·비판 기사, 리서치, 테마·수혜주 기사도 포함합니다.
(예: '이 종목, 외형 커졌지만 재고 부담도' 는 회사가 낸 기사가 아니지만 주가에 영향을 줄 수 있음)

분류
  회사 발표    수주·계약·MOU·과제 선정·실적·전시회 참가 등 회사 소식
  언론 분석    언론사의 분석·기획·비판 기사
  리서치       증권사·독립리서치 보고서를 다룬 기사
  테마·수혜주  이 종목이 테마·수혜 종목으로 언급된 기사
  시황·특징주  주가가 움직인 뒤 그 움직임을 다룬 기사 (원인보다 '결과')
  단순 언급    본문에 이름만 나오거나 네이버가 연결만 한 기사

반응 계산
  분봉(최근 기록이 있을 때): 공개 후 5·15·30·60·120분, KOSDAQ·같은 업종 평균과 비교 → 초과 반응
  일봉(몇 달 전 기사도 가능): 반응일 당일·다음날·3거래일 누적, KOSDAQ·같은 업종 평균과 비교, 거래량 배수
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, time, timedelta

from . import naver   # analysis(pandas)는 필요할 때만 불러옴 → 설치 전 연결 점검에서도 동작
from .db import connect

KOSDAQ = "KOSDAQ"
NAME = "이 종목"
CATEGORIES = ["회사 발표", "언론 분석", "리서치", "테마·수혜주", "시황·특징주", "단순 언급"]

MARKET_STRONG = ("특징주", "상한가", "하한가", "핫종목", "급등", "급락", "신고가", "52주", "주가", "초고수", "상승률", "하락률",
                 "들썩", "껑충", "줄상승", "[종목NOW]", "서울데이터랩")
MARKET_WEAK = ("↑", "↓", "강세", "약세", "오름세", "내림세")
RESEARCH = ("리서치", "밸류파인더", "목표주가", "투자의견", "리포트", "보고서", "연구원", "커버리지", "기업분석")
COMPANY = ("수주", "계약", "공급", "MOU", "협약", "체결", "선정", "참여", "참가", "개발", "출시", "양산", "인증", "실적",
           "매출", "영업익", "영업이익", "흑자", "전시", "과제", "진입", "특허", "협력", "동맹", "손잡", "납품", "공시",
           "IR", "기업설명회", "유상증자", "무상증자", "자사주", "수상", "준공", "증설", "착공")
THEME = ("수혜", "테마", "관련주", "유망", "주목", "대장주", "밸류체인", "방산주", "우주주", "투심", "株", "기업들", "주末머니")
POS = ("수주", "계약", "공급", "협약", "MOU", "선정", "개발", "출시", "흑자", "최대", "증가", "확대", "양산", "수출", "승인",
       "인증", "특허", "호실적", "상향", "수혜", "강세", "급등", "신고가", "성장", "진입", "동맹", "효자", "정조준", "참여", "협력",
       "돌파", "국산화", "1028%")
NEG = ("유상증자", "적자", "손실", "소송", "해지", "취소", "블록딜", "오버행", "보호예수", "매각", "감소", "하향", "부진", "약세",
       "급락", "전환사채", "CB", "BW", "감사의견", "거래정지", "불성실", "부담", "우려", "리스크", "경고", "지연", "차질",
       "재고", "하락", "논란", "고평가", "굴욕", "민원")


def classify(title: str, body: str) -> tuple[str, str]:
    t, b = title or "", body or ""
    in_title = NAME in t
    mention = "제목" if in_title else ("본문" if NAME in b else "연결")
    has_company = any(w in t for w in COMPANY)
    if any(w in t for w in MARKET_STRONG) or (in_title and any(w in t for w in MARKET_WEAK) and not has_company):
        return "시황·특징주", mention
    if any(w in t for w in RESEARCH) or (in_title and any(w in b[:90] for w in RESEARCH) and not has_company):
        return "리서치", mention
    if in_title:
        if t.lstrip().startswith("[") and not t.lstrip().startswith("[단독]"):
            return "언론 분석", mention          # 기획 연재 ([빛을 담는 기업들] 등)
        return ("회사 발표" if has_company else "언론 분석"), mention
    if NAME in b[:90]:
        return "언론 분석", mention              # 제목엔 없지만 이 종목을 다룬 기사
    if any(w in t for w in THEME):
        return "테마·수혜주", mention
    return "단순 언급", mention


def tone_of(title: str, body: str = "") -> int:
    def score(s):
        p = sum(w in s for w in POS)
        n = sum(w in s for w in NEG)
        return (p > n) - (n > p)
    t = score(title or "")
    return t if t else score((body or "")[:120])


def _tokens(title: str) -> set[str]:
    s = re.sub(r"\[[^\]]*\]|[\"'“”‘’…·,.!?()<>「」『』~\-–—/|]", " ", (title or "").replace(NAME, " "))
    return {w for w in s.split() if len(w) >= 2}


# ------------------------------------------------------------------ 수집

def fetch_stock_news(code: str, page: int = 1, size: int = 50) -> list[dict]:
    d = naver._json(f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={size}&page={page}")
    out = []
    for group in d or []:
        for it in (group.get("items") or [])[:1]:     # 묶음의 대표 기사
            dt = str(it.get("datetime") or "")
            if len(dt) < 12:
                continue
            out.append({
                "id": str(it.get("id") or f"{it.get('officeId')}{it.get('articleId')}"),
                "ts": f"{dt[0:4]}-{dt[4:6]}-{dt[6:8]}T{dt[8:10]}:{dt[10:12]}",
                "office": it.get("officeName") or "",
                "title": html.unescape(it.get("titleFull") or it.get("title") or "").strip(),
                "body": html.unescape(it.get("body") or "").strip(),
                "url": it.get("mobileNewsUrl") or "",
                "outlets": int(group.get("total") or 1),
            })
    return out


def sync_articles(code: str, pages: int = 1) -> int:
    now = datetime.now().isoformat(timespec="minutes")
    n = 0
    for p in range(1, pages + 1):
        try:
            items = fetch_stock_news(code, p)
        except naver.DataSourceError:
            if p == 1:
                raise
            break
        if not items:
            break
        rows = []
        for i in items:
            cat, mention = classify(i["title"], i["body"])
            rows.append((i["id"], code, i["ts"], i["office"], i["title"], i["body"], i["url"], i["outlets"],
                         cat, tone_of(i["title"], i["body"]), mention, now))
        with connect() as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT INTO articles(id, symbol, ts, office, title, body, url, outlets, category, tone, mention, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET outlets=excluded.outlets",
                rows)
            n += conn.total_changes - before
    group_stories(code)
    return n


def group_stories(code: str):
    """같은 내용을 여러 매체가 낸 기사를 하나로 묶음 (가장 먼저 나온 기사 기준)."""
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ts, title FROM articles WHERE symbol=? ORDER BY ts DESC LIMIT 500", (code,))]
    rows.sort(key=lambda r: r["ts"])
    stories = []   # (story_id, ts, tokens)
    assign = {}
    for r in rows:
        tk = _tokens(r["title"])
        t = datetime.fromisoformat(r["ts"])
        best = None
        for sid, st, stk in stories:
            if t - st > timedelta(hours=72) or not tk or not stk:
                continue
            shared = len(tk & stk)
            j = shared / len(tk | stk)
            if (j >= 0.3 or (shared >= 2 and j >= 0.2)) and (best is None or j > best[1]):
                best = (sid, j)
        if best:
            assign[r["id"]] = best[0]
        else:
            stories.append((r["id"], t, tk))
            assign[r["id"]] = r["id"]
    with connect() as conn:
        conn.executemany("UPDATE articles SET story=? WHERE id=?", [(s, i) for i, s in assign.items()])


# ------------------------------------------------------------------ 반응 계산

def _daily(symbol: str, start: str, end: str) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM daily_bars WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date", (symbol, start, end))]


def trading_days(symbol: str) -> list[str]:
    with connect() as conn:
        d = [r[0] for r in conn.execute("SELECT date FROM daily_bars WHERE symbol=? ORDER BY date", (symbol,))]
        m = [r[0] for r in conn.execute("SELECT DISTINCT substr(ts,1,10) FROM minute_bars WHERE symbol=?", (symbol,))]
    return sorted(set(d) | set(m))


def reaction_day(ts: datetime, days: list[str]) -> str | None:
    """기사가 처음 반영될 수 있는 거래일 (장 마감 뒤 공개면 다음 거래일)."""
    d = ts.date().isoformat()
    if ts.time() > time(15, 30):
        later = [x for x in days if x > d]
    else:
        later = [x for x in days if x >= d]
    if not later or (date.fromisoformat(later[0]) - ts.date()).days > 6:
        return None          # 그 무렵 시세 기록이 없음
    return later[0]


def _ret_path(symbol: str, d0: str, n_after: int = 3):
    """반응일 전 종가 대비 D0, D+1, D+3 누적 변화율과 거래량 배수."""
    rows = _daily(symbol, (date.fromisoformat(d0) - timedelta(days=45)).isoformat(),
                  (date.fromisoformat(d0) + timedelta(days=12)).isoformat())
    idx = next((i for i, r in enumerate(rows) if r["date"] == d0), None)
    if idx is None or idx == 0:
        return None
    prev = rows[idx - 1]["close"]
    out = {"prev_close": prev, "d0_close": rows[idx]["close"]}
    for k, off in (("d0", 0), ("d1", 1), ("d3", 3)):
        j = idx + off
        out[k] = rows[j]["close"] / prev - 1 if j < len(rows) and prev else None
    vols = [r["volume"] for r in rows[max(0, idx - 20):idx] if r["volume"]]
    out["vol_ratio"] = rows[idx]["volume"] / (sum(vols) / len(vols)) if vols and rows[idx]["volume"] else None
    out["volume"] = rows[idx]["volume"]
    return out


def _peer_codes() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT code, name FROM related_stocks")]


def _minute_change(code: str, t0: datetime, t1: datetime):
    from . import analysis
    df = analysis.load_range(code, t0 - timedelta(days=4), t1)
    if df.empty:
        return None
    day = df[df["ts"].dt.date == t1.date()]
    a = day[day["ts"] < t0]
    b = day[(day["ts"] >= t0) & (day["ts"] <= t1)]
    if b.empty:
        return None
    base = float(a["close"].iloc[-1]) if not a.empty else float(b["open"].iloc[0])
    return float(b["close"].iloc[-1]) / base - 1 if base else None



def _close_series(code: str, lo: str, hi: str) -> dict:
    return {r["date"]: r["close"] for r in _daily(code, lo, hi) if r.get("close")}


def decompose(symbol: str, d0: str, peers: list[dict]) -> dict | None:
    """반응일 주가 변동을 '시장 몫 + 업종 몫 + 고유(기사) 몫'으로 나눔 (사건 연구 방식).

    - 민감도: 반응일 6거래일 전까지 약 120거래일의 일별 수익률로 회귀
        이 종목 = α + β시장 × KOSDAQ + β업종 × (업종 평균 − 업종의 시장 몫) + 잔차
    - 반응일 고유 몫 = 실제 − α − 시장 몫 − 업종 몫
    - 평소 고유 변동폭(잔차 표준편차)과 비교해 몇 배인지(z)로 '뚜렷한지' 판단
    """
    import numpy as np
    day0 = date.fromisoformat(d0)
    lo, hi = (day0 - timedelta(days=260)).isoformat(), (day0 + timedelta(days=10)).isoformat()
    own, mkt = _close_series(symbol, lo, hi), _close_series(KOSDAQ, lo, hi)
    pser = [_close_series(p["code"], lo, hi) for p in peers]
    dates = sorted(d for d in own if d in mkt)
    if d0 not in dates:
        return None
    rows = []
    for a, b in zip(dates, dates[1:]):
        ro, rm = own[b] / own[a] - 1, mkt[b] / mkt[a] - 1
        ps = [p[b] / p[a] - 1 for p in pser if a in p and b in p and p[a]]
        rs = sum(ps) / len(ps) if len(ps) >= 2 else None
        rows.append((b, ro, rm, rs))
    idx = next((i for i, r in enumerate(rows) if r[0] == d0), None)
    if idx is None:
        return None
    est = [r for r in rows[max(0, idx - 125):max(0, idx - 5)] if r[3] is not None]
    has_sector = rows[idx][3] is not None
    out = {"n_obs": len(est), "window": [est[0][0], est[-1][0]] if est else None, "has_sector": has_sector}
    if len(est) >= 40:
        m = np.array([r[2] for r in est])
        sct = np.array([r[3] for r in est])
        y = np.array([r[1] for r in est])
        b_sm = float(np.polyfit(m, sct, 1)[0])                     # 업종이 시장을 따라가는 정도
        sx = sct - b_sm * m                                         # 업종 고유 움직임
        X = np.column_stack([np.ones(len(y)), m, sx]) if has_sector else np.column_stack([np.ones(len(y)), m])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        alpha, b_m = float(coef[0]), float(coef[1])
        b_s = float(coef[2]) if has_sector else 0.0
        sigma = float(np.std(resid, ddof=X.shape[1]))
        out.update({"method": "회귀", "alpha": alpha, "beta_market": b_m, "beta_sector": b_s, "beta_sector_market": b_sm,
                    "sigma": sigma})
    else:
        # 기록이 짧으면 민감도 1로 단순 비교 (시장 1배, 업종 1배)
        diffs = [r[1] - (r[3] if r[3] is not None else r[2]) for r in rows[max(0, idx - 60):idx]]
        sigma = float(np.std(diffs)) if len(diffs) >= 10 else None
        out.update({"method": "단순", "alpha": 0.0, "beta_market": 1.0, "beta_sector": 1.0 if has_sector else 0.0,
                    "beta_sector_market": 1.0, "sigma": sigma})

    def split(i_from: int, i_to: int) -> dict | None:
        seg = rows[i_from:i_to + 1]
        if not seg or any(r[0] is None for r in seg):
            return None
        own_r = float(np.prod([1 + r[1] for r in seg]) - 1)
        mkt_part = sum(out["beta_market"] * r[2] for r in seg)
        sec_part = sum(out["beta_sector"] * ((r[3] - out["beta_sector_market"] * r[2]) if r[3] is not None else 0.0)
                       for r in seg)
        abn = own_r - out["alpha"] * len(seg) - mkt_part - sec_part
        sig = out.get("sigma")
        z = abn / (sig * len(seg) ** 0.5) if sig else None
        sector_r = (float(np.prod([1 + r[3] for r in seg]) - 1) if all(r[3] is not None for r in seg) else None)
        return {"own": own_r, "market": float(mkt_part), "sector": float(sec_part), "abnormal": float(abn),
                "z": float(z) if z is not None else None,
                "kosdaq": float(np.prod([1 + r[2] for r in seg]) - 1), "sector_avg": sector_r,
                "days": len(seg)}

    out["d0"] = split(idx, idx)
    out["d01"] = split(idx, min(idx + 1, len(rows) - 1)) if idx + 1 < len(rows) else None
    return out


def timing(symbol: str, start: datetime, peers: list[dict], minutes: int = 30) -> dict | None:
    """기사 직후 이 종목이 업종보다 먼저 움직였는지 (분봉)."""
    from . import analysis
    t1 = start + timedelta(minutes=minutes)
    df = analysis.load_range(symbol, start - timedelta(days=4), t1)
    if df.empty:
        return None
    day = df[df["ts"].dt.date == start.date()]
    before = day[day["ts"] < start]
    after = day[(day["ts"] >= start) & (day["ts"] <= t1)]
    if after.empty:
        return None
    base = float(before["close"].iloc[-1]) if not before.empty else float(after["open"].iloc[0])
    path = [(r.ts, float(r.close) / base - 1) for r in after.itertuples()]
    peak = max(path, key=lambda x: abs(x[1]))
    first = next(((t, v) for t, v in path if abs(v) >= 0.01), None)
    pch = [c for c in (_minute_change(p["code"], start, peak[0].to_pydatetime()) for p in peers) if c is not None]
    peer_move = sum(pch) / len(pch) if len(pch) >= 2 else None
    own_move = peak[1]
    if peer_move is None:
        label, cls = "같은 업종 분봉 기록이 없어 시간 순서를 확인하지 못했습니다.", "none"
    elif abs(own_move) < 0.01:
        label, cls = f"기사 후 {minutes}분 동안 이 종목이 1% 이상 움직이지 않았습니다.", "flat"
    elif abs(peer_move) < 0.3 * abs(own_move) or peer_move * own_move < 0:
        mins = int((first[0].to_pydatetime() - start).total_seconds() // 60) if first else None
        label = (f"기사 후 {mins}분 만에 이 종목이 먼저 움직였고(최대 {own_move*100:+.1f}%), "
                 f"같은 시각 업종 평균은 {peer_move*100:+.1f}%였습니다 → 기사 영향 쪽") if mins is not None else \
                f"이 종목 {own_move*100:+.1f}%, 같은 시각 업종 평균 {peer_move*100:+.1f}% → 기사 영향 쪽"
        cls = "lead"
    else:
        label, cls = (f"이 종목({own_move*100:+.1f}%)과 업종 평균({peer_move*100:+.1f}%)이 같은 시각에 함께 움직였습니다 "
                      "→ 업종 흐름 쪽"), "together"
    return {"label": label, "cls": cls, "own": own_move, "peers": peer_move,
            "peak_at": peak[0].isoformat(timespec="minutes") if hasattr(peak[0], "isoformat") else str(peak[0])}


def effect(symbol: str, ts: datetime, tone: int = 0, category: str = "") -> dict:
    from . import analysis
    days = trading_days(symbol)
    d0 = reaction_day(ts, days)
    out = {"reaction_day": d0, "minute": None, "daily": None, "verdict": None, "verdict_cls": "none",
           "notes": []}
    peers = _peer_codes()

    # 분봉 반응 (최근 기록이 있을 때)
    r = analysis.analyze_pr(symbol, ts)
    if r.ok and r.returns:
        bench, bench_name = {}, "KOSDAQ"
        peer_avg = {}
        for h in r.returns:
            t1 = r.reaction_start + timedelta(minutes=h)
            ch = [c for c in (_minute_change(p["code"], r.reaction_start, t1) for p in peers) if c is not None]
            if len(ch) >= 2:
                peer_avg[h] = sum(ch) / len(ch)
        if peer_avg:
            bench, bench_name = peer_avg, "같은 업종 평균"
        else:
            bench = r.kosdaq_returns
        out["minute"] = {
            "reaction_start": r.reaction_start.isoformat(timespec="minutes"), "base_price": r.base_price,
            "returns": {str(k): v for k, v in r.returns.items()},
            "kosdaq": {str(k): v for k, v in r.kosdaq_returns.items()},
            "peers": {str(k): v for k, v in peer_avg.items()},
            "excess": {str(k): r.returns[k] - bench[k] for k in r.returns if k in bench},
            "bench_name": bench_name,
            "max_up": r.max_up, "max_up_at": r.max_up_at.isoformat(timespec="minutes") if r.max_up_at else None,
            "max_down": r.max_down, "max_down_at": r.max_down_at.isoformat(timespec="minutes") if r.max_down_at else None,
            "vol_after30": r.vol_after30, "vol_usual30": r.vol_usual30,
            "outside_session": r.reaction_start.isoformat(timespec="minutes") != ts.isoformat(timespec="minutes"),
        }

    # 일봉 반응 (몇 달 전 기사도)
    if d0:
        own = _ret_path(symbol, d0)
        if own:
            kq = _ret_path(KOSDAQ, d0) or {}
            pp = [x for x in (_ret_path(p["code"], d0) for p in peers) if x]
            pavg = {k: sum(x[k] for x in pp if x.get(k) is not None) / max(1, len([x for x in pp if x.get(k) is not None]))
                    for k in ("d0", "d1", "d3")} if len(pp) >= 2 else {}
            bench = pavg if pavg else kq
            out["daily"] = {
                **{k: own.get(k) for k in ("d0", "d1", "d3", "vol_ratio", "volume", "prev_close")},
                "kosdaq": {k: kq.get(k) for k in ("d0", "d1", "d3")},
                "peers": pavg,
                "excess": {k: (own[k] - bench[k]) if own.get(k) is not None and bench.get(k) is not None else None
                           for k in ("d0", "d1", "d3")},
                "bench_name": "같은 업종 평균" if pavg else "KOSDAQ",
            }

    if d0:
        try:
            out["decomp"] = decompose(symbol, d0, peers)
        except Exception as e:          # 보조 분석
            out["notes"].append(f"분해 계산 실패: {e}")
    if out.get("minute"):
        try:
            out["timing"] = timing(symbol, datetime.fromisoformat(out["minute"]["reaction_start"]), peers)
        except Exception as e:
            out["notes"].append(f"시간 순서 계산 실패: {e}")
    out["verdict"], out["verdict_cls"], out["measure"] = _verdict(out, tone, category)
    return out


def _verdict(eff: dict, tone: int, category: str):
    """판정 순서: (1) 시장·업종 몫을 뺀 고유 몫과 평소 변동폭 대비 크기, (2) 분봉 시간 순서, (3) 단순 초과 반응."""
    m, d, dc, tm = eff.get("minute"), eff.get("daily"), eff.get("decomp"), eff.get("timing")
    vol = d.get("vol_ratio") if d else None
    measure = None
    if dc and dc.get("d0"):
        part = dc["d0"]
        measure = {"label": "반응일 기사(고유) 몫", "value": part["abnormal"], "z": part.get("z"), "vol_ratio": vol,
                   "own": part["own"], "market": part["market"], "sector": part["sector"]}
    elif m and m.get("excess"):
        h = "60" if "60" in m["excess"] else max(m["excess"], key=int)
        measure = {"label": f"공개 후 {h}분 초과 반응", "value": m["excess"][h], "vol_ratio": vol}
    elif d and d["excess"].get("d0") is not None:
        measure = {"label": "반응일 초과 반응", "value": d["excess"]["d0"], "vol_ratio": vol}
    if measure is None:
        return "반응 계산 불가", "none", None
    if category == "시황·특징주":
        return "주가 움직임을 다룬 기사", "result", measure
    if category == "단순 언급":
        return "이름만 언급된 기사 (효과 판단 제외)", "mention", measure
    x, z = measure["value"], measure.get("z")
    strong = abs(x) >= 0.02 and (z is None or abs(z) >= 2)
    weak = abs(x) >= 0.01 and (z is None or abs(z) >= 1)
    if tm and tm.get("cls") == "lead" and abs(x) >= 0.01:
        strong = strong or (z is not None and abs(z) >= 1.5)
        weak = True
    if tm and tm.get("cls") == "together" and not strong:
        weak = False
    if tone and (strong or weak) and x * tone < 0:
        return "기사 방향과 반대로 움직임", "opposite", measure
    if strong:
        return ("뚜렷한 기사 효과 (상승)" if x > 0 else "뚜렷한 기사 효과 (하락)"), ("up" if x > 0 else "down"), measure
    if weak:
        return ("약한 기사 효과 (상승)" if x > 0 else "약한 기사 효과 (하락)"), ("up-weak" if x > 0 else "down-weak"), measure
    if measure.get("own") is not None and abs(measure["own"]) >= 0.03:
        return "업종·시장 영향이 대부분", "flat", measure
    return "뚜렷한 기사 효과 없음", "flat", measure


def confounders(symbol: str, eff: dict, art_id: str, story: str | None) -> list[str]:
    """같은 날 기사 효과와 섞였을 수 있는 다른 요인."""
    d0 = eff.get("reaction_day")
    if not d0:
        return []
    out = []
    with connect() as conn:
        others = [dict(r) for r in conn.execute(
            "SELECT id, ts, title, category FROM articles WHERE symbol=? AND hidden=0 AND ts BETWEEN ? AND ? "
            "AND id != ? AND COALESCE(story,'') != ? AND category NOT IN ('시황·특징주','단순 언급') ORDER BY ts",
            (symbol, (date.fromisoformat(d0) - timedelta(days=1)).isoformat() + "T15:30", d0 + "T15:30", art_id, story or ""))]
        themes = [dict(r) for r in conn.execute(
            "SELECT name, change_rate, rank, n_themes FROM theme_daily WHERE date=? AND mine=1 ORDER BY ABS(change_rate) DESC",
            (d0,))]
        evs = [dict(r) for r in conn.execute(
            "SELECT id, start_ts, direction, peak_return_5m FROM events WHERE symbol=? AND substr(start_ts,1,10)=? ORDER BY start_ts",
            (symbol, d0))]
    if others:
        out.append(f"같은 시기 이 종목 기사 {len(others)}건이 더 있어 효과가 섞였을 수 있습니다: "
                   + ", ".join(f"「{o['title'][:28]}」({o['category']})" for o in others[:3]))
    hot = [t for t in themes if abs(t["change_rate"]) >= 2]
    if hot:
        out.append("이날 이 종목 테마가 크게 움직였습니다: " + ", ".join(f"{t['name']} {t['change_rate']:+.1f}%" for t in hot[:3])
                   + " — 업종 평균과 비교한 '초과 반응'을 함께 보세요.")
    d = eff.get("daily") or {}
    kq = (d.get("kosdaq") or {}).get("d0")
    if kq is not None and abs(kq) >= 0.02:
        out.append(f"이날 KOSDAQ 자체가 {kq * 100:+.1f}% 움직였습니다.")
    eff["events"] = evs
    return out


def article_rows(symbol: str, include_hidden: bool = False) -> list[dict]:
    with connect() as conn:
        arts = [dict(r) for r in conn.execute(
            "SELECT * FROM articles WHERE symbol=? " + ("" if include_hidden else "AND hidden=0 ") + "ORDER BY ts DESC",
            (symbol,))]
        prs = [dict(r) for r in conn.execute("SELECT * FROM pr_events ORDER BY published_ts DESC")]
    for p in prs:
        arts.append({"id": f"pr{p['id']}", "symbol": symbol, "ts": p["published_ts"], "office": "직접 등록",
                     "title": p["title"], "body": "", "url": p.get("url") or "", "outlets": 1, "category": "회사 발표",
                     "tone": tone_of(p["title"]), "mention": "제목", "story": f"pr{p['id']}", "manual": True,
                     "pr_type": p.get("pr_type")})
    arts.sort(key=lambda a: a["ts"], reverse=True)
    # 같은 내용 묶음: 대표(가장 먼저 나온 기사)만 남기고 나머지 수를 더함
    by_story = {}
    for a in arts:
        by_story.setdefault(a.get("story") or a["id"], []).append(a)
    out = []
    for sid, group in by_story.items():
        group.sort(key=lambda a: a["ts"])
        lead = dict(group[0])
        # 묶음의 성격은 가장 '원천'에 가까운 기사 기준 (회사 발표 > 리서치 > 언론 분석 > …)
        best = min(group, key=lambda g: CATEGORIES.index(g["category"]) if g["category"] in CATEGORIES else 9)
        lead["category"] = best["category"]
        if not lead.get("tone"):
            lead["tone"] = best.get("tone") or 0
        lead["outlets_total"] = sum(int(g.get("outlets") or 1) for g in group)
        lead["related_titles"] = [{"ts": g["ts"], "office": g["office"], "title": g["title"], "url": g["url"]} for g in group[1:]]
        out.append(lead)
    out.sort(key=lambda a: a["ts"], reverse=True)
    return out


def find(symbol: str, art_id: str) -> dict | None:
    return next((a for a in article_rows(symbol, include_hidden=True) if a["id"] == art_id), None)


def summarize(rows: list[dict]) -> list[dict]:
    """유형별 평균 반응."""
    out = []
    for cat in CATEGORIES:
        g = [r for r in rows if r["category"] == cat and r.get("effect") and r["effect"].get("measure")]
        n_all = len([r for r in rows if r["category"] == cat])
        if not n_all:
            continue
        xs = [r["effect"]["measure"]["value"] for r in g]
        pos = [r["effect"]["measure"]["value"] for r in g if r["tone"] > 0]
        neg = [r["effect"]["measure"]["value"] for r in g if r["tone"] < 0]
        strong = len([r for r in g if r["effect"]["verdict_cls"] in ("up", "down")])
        out.append({"category": cat, "count": n_all, "measured": len(g),
                    "avg": sum(xs) / len(xs) if xs else None,
                    "avg_abs": sum(abs(v) for v in xs) / len(xs) if xs else None,
                    "pos_avg": sum(pos) / len(pos) if pos else None, "pos_n": len(pos),
                    "neg_avg": sum(neg) / len(neg) if neg else None, "neg_n": len(neg),
                    "strong": strong})
    return out


def before_event(symbol: str, t_start: datetime, t_end: datetime) -> dict:
    """이상변동 직전·직후 이 종목 기사 (원인 분석 맨 위에 보여줌)."""
    rows = article_rows(symbol)
    lead, late = [], []
    for a in rows:
        t = datetime.fromisoformat(a["ts"])
        if a["category"] == "단순 언급":
            continue
        if t_start - timedelta(days=3) <= t <= t_start + timedelta(minutes=1):
            lead.append(a)
        elif t_start < t <= t_end + timedelta(hours=3):
            late.append(a)
    lead.sort(key=lambda a: a["ts"], reverse=True)
    out_lead = []
    for a in lead[:4]:
        eff = effect(symbol, datetime.fromisoformat(a["ts"]), a["tone"], a["category"])
        out_lead.append({**{k: a.get(k) for k in ("id", "ts", "office", "title", "url", "category", "tone",
                                                   "outlets_total", "manual")},
                         "verdict": eff["verdict"], "verdict_cls": eff["verdict_cls"], "measure": eff.get("measure"),
                         "gap_min": int((t_start - datetime.fromisoformat(a["ts"])).total_seconds() // 60)})
    last_any = None
    if not lead:
        older = [a for a in rows if datetime.fromisoformat(a["ts"]) < t_start and a["category"] not in ("단순 언급",)]
        if older:
            last_any = {k: older[0].get(k) for k in ("id", "ts", "title", "category")}
    return {"lead": out_lead, "late": [{k: a.get(k) for k in ("id", "ts", "office", "title", "url", "category")}
                                       for a in late[:5]], "last_before": last_any}
