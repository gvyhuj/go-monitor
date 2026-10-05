"""거시·정치·테마 배경 수집과 해석.

'JP모간 창구가 왜 샀을까'를 종목 밖에서 찾기 위한 자료:
- 시장 전체 뉴스(네이버 증권 주요뉴스·실시간 속보)를 주제별로 분류
    중동·이란, 러시아·우크라이나, 북한·안보, 트럼프·미국 정책, 방산 수출·국방, 우주·위성, 반도체, 유가·금리·환율
- 네이버 테마: 이 종목이 속한 테마(우주항공, 방위산업 등)의 그날 등락과 순위
- 전날 밤 미국 방산주(록히드마틴·노스롭그루먼·RTX·제너럴다이내믹스)와 국제유가
- 시장 일정(옵션 만기일, 연휴 직전 등)

사용하는 주소 (2026-10-02 확인)
  시장 뉴스   https://m.stock.naver.com/api/news/list?category=mainnews|flashnews&page=N&pageSize=50
  테마 목록   https://m.stock.naver.com/api/stocks/theme?page=N&pageSize=100
  테마 종목   https://m.stock.naver.com/api/stocks/theme/{no}?page=1&pageSize=100
  일봉        https://api.stock.naver.com/chart/domestic/item/{code}?periodType=dayCandle  (index/KOSDAQ 도 같은 형식)
  해외 일봉   https://api.stock.naver.com/chart/foreign/item/{sym}?periodType=dayCandle
  국제유가    https://m.stock.naver.com/front-api/marketIndex/prices?category=energy&reutersCode=CLcv1&page=1&pageSize=20

원칙: 이 자료들은 '같은 시기에 이런 일이 있었다'는 배경입니다. 특정 증권사 고객의 매수 이유를 확정하지 않습니다.
"""
from __future__ import annotations

import html
import json
import re
import time as _time
from datetime import date, datetime, timedelta

from . import naver
from .db import connect

KOSDAQ = "KOSDAQ"

# 주제, 찾는 단어(기사 제목에서만 찾음), 이 종목 사업과의 연결
TOPICS = [
    ("중동·이란", ("중동", "이란", "이스라엘", "가자", "하마스", "헤즈볼라", "후티", "호르무즈", "사우디",
               "Iran", "Iranian", "Israel", "Israeli", "Gaza", "Hamas", "Hezbollah", "Houthi", "Hormuz", "Middle East", "Saudi", "Tehran"), "방산"),
    ("러시아·우크라이나", ("러시아", "우크라이나", "푸틴", "젤렌스키", "나토", "NATO", "Russia", "Russian", "Ukraine", "Ukrainian", "Putin", "Zelensky", "Kremlin"), "방산"),
    ("북한·안보", ("북한", "김정은", "김여정", "미사일 발사", "도발", "핵실험", "합참", "DMZ", "지뢰", "군사분계선",
                  "North Korea", "Pyongyang", "Kim Jong Un"), "방산"),
    ("트럼프·미국 정책", ("트럼프", "백악관", "행정명령", "관세", "Trump", "White House", "tariff"), "시장"),
    ("미국 국방", ("펜타곤", "미 국방부", "국방수권법", "미군", "항모", "항공모함", "Pentagon", "aircraft carrier", "US troops", "defense budget"), "방산"),
    ("방산 수출·국방", ("방산", "방위산업", "K방산", "폴란드", "루마니아", "K9", "천무", "레드백", "방위사업청", "유도무기",
                     "요격", "드론", "무기", "국방예산", "국방비", "방위비", "전투기", "KF-21",
                     "defense stocks", "defence stocks", "arms sales", "weapons", "military spending"), "방산"),
    ("우주·위성", ("우주", "위성", "NASA", "나사", "누리호", "우주항공청", "스페이스X", "발사체", "망원경", "저궤도"), "우주"),
    ("광학·레이저·적외선", ("광학", "레이저", "적외선", "열화상", "열상", "렌즈", "라이다", "SWIR", "양자점", "카메라 모듈"), "광학"),
    ("소재·수출통제", ("게르마늄", "갈륨", "희토류", "핵심광물", "수출통제", "수출 통제", "수출 제한", "광물"), "소재"),
    ("반도체·디스플레이", ("반도체", "HBM", "노광", "파운드리", "D램", "소부장", "OLED", "디스플레이"), "반도체"),
    ("정부 정책·예산", ("예산안", "추경", "국산화", "정책자금", "국민성장펀드", "정부 지원", "규제 완화", "세제 개편",
                     "밸류업", "산업부", "과기정통부"), "정책"),
    ("코스닥·수급", ("코스닥", "천스닥", "외국인 매도", "외국인 순매도", "공매도", "신용융자", "반대매매", "상장폐지"), "시장"),
    ("유가·금리·환율", ("유가", "WTI", "국채", "금리", "연준", "Fed", "FOMC", "환율", "고용지표", "물가", "oil price", "Brent", "Treasury yield"), "시장"),
]
# 이 종목 사업과 얼마나 직접 이어지는가 (원인 후보 점수에 씀)
RELEVANCE = {"방산": 1.0, "광학": 1.0, "우주": 0.85, "소재": 0.8, "방산·시장": 0.7, "반도체": 0.55, "정책": 0.5, "시장": 0.6}
RELEVANT = {"방산", "우주", "반도체", "방산·시장", "광학", "소재"}
PRIORITY = {"방산": 0, "광학": 1, "방산·시장": 2, "우주": 3, "소재": 4, "반도체": 5, "정책": 6, "시장": 7}
DEESCALATE = ("휴전", "종전", "평화", "합의", "협상 타결", "철수", "완화")
UP_WORDS = ("급등", "강세", "상승", "반등", "훈풍", "수혜", "랠리", "신고가", "호재", "기대", "rally", "surge", "soar", "jump", "gain")
DOWN_WORDS = ("급락", "약세", "하락", "폭락", "우려", "악재", "매도", "충격", "쇼크", "경고", "slump", "plunge", "tumble", "fall", "drop")
# 업종 움직임을 설명하는 시황 기사를 찾는 말
SECTOR_WORDS = ("방산주", "방산株", "우주항공주", "우주주", "광학주", "레이저", "드론주", "국방", "방산 ")

US_DEFENSE = [("LMT", "록히드마틴"), ("NOC", "노스롭그루먼"), ("RTX", "RTX"), ("GD", "제너럴다이내믹스")]


# 우리말 어미·동사와 겹치는 짧은 말은 앞뒤 글자를 보고 판단 ('~이란'(어미), '가자'(동사) 등)
_STRICT = {
    "이란": re.compile(r"(?<![가-힣])이란(?=$|[^가-힣]|[은이의과에측산핵군전도]|정부|대통령|혁명|외무|국영)"),
    "가자": re.compile(r"(?<![가-힣])가자\s?(?=지구|전쟁|지역|휴전)"),
}


_EN = {}


def _has(text: str, w: str) -> bool:
    rx = _STRICT.get(w)
    if rx:
        return bool(rx.search(text))
    if w.isascii() and any(ch.isalpha() for ch in w) and not w.isupper():
        r = _EN.get(w) or _EN.setdefault(w, re.compile(r"\b" + re.escape(w) + r"s?\b", re.I))   # 영어는 단어 단위로
        return bool(r.search(text))
    return w in text


def topics_of(text: str) -> list[str]:
    return [name for name, words, _ in TOPICS if any(_has(text, w) for w in words)]


def _ts14(s: str) -> str:
    s = str(s)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:{s[10:12]}"


# ------------------------------------------------------------------ 수집

def fetch_market_news(category: str, page: int = 1, size: int = 50) -> list[dict]:
    d = naver._json(f"https://m.stock.naver.com/api/news/list?category={category}&page={page}&pageSize={size}")
    out = []
    for it in d or []:
        dt = str(it.get("dt") or "")
        if len(dt) < 12:
            continue
        oid, aid = it.get("oid") or "", it.get("aid") or ""
        out.append({"id": f"{oid}{aid}", "ts": _ts14(dt), "office": it.get("ohnm") or "",
                    "title": html.unescape(it.get("tit") or "").strip(), "body": html.unescape(it.get("subcontent") or "").strip(),
                    "url": f"https://n.news.naver.com/mnews/article/{oid}/{aid}"})
    return out


def sync_macro_news(pages: int = 1, categories=("flashnews", "mainnews")) -> int:
    now = datetime.now().isoformat(timespec="minutes")
    n = 0
    for cat in categories:
        for p in range(1, pages + 1):
            try:
                items = fetch_market_news(cat, p)
            except naver.DataSourceError:
                break
            if not items:
                break
            with connect() as conn:
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO macro_news(id, ts, office, title, body, url, topics, first_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(i["id"], i["ts"], i["office"], i["title"], i["body"], i["url"],
                      ",".join(topics_of(i["title"])), now) for i in items])
                n += conn.total_changes - before
            if pages > 1:
                _time.sleep(0.15)
    return n


def backfill_macro_news(day: date, max_pages: int = 60, log=None) -> int:
    """지난 날짜의 시장 뉴스를 거슬러 올라가 채움 (그날 기록이 거의 없을 때만).

    네이버 시장 뉴스 목록은 날짜로 고를 수 없어서 1쪽부터 뒤로 넘기며 그날까지 내려감.
    """
    if (date.today() - day).days > 14:
        return 0                     # 목록을 너무 깊이 넘겨야 해서 2주 안쪽만 보충
    start = datetime.combine(day - timedelta(days=3 if day.weekday() == 0 else 1), datetime.min.time()) \
        + timedelta(hours=15)
    end = datetime.combine(day, datetime.min.time()) + timedelta(hours=23, minutes=59)
    if coverage(start, end) >= 30:
        return 0
    now = datetime.now().isoformat(timespec="minutes")
    total = 0
    for cat in ("mainnews", "flashnews"):
        for pg in range(1, max_pages + 1):
            try:
                items = fetch_market_news(cat, pg)
            except naver.DataSourceError:
                break
            if not items:
                break
            keep = [i for i in items if start.isoformat(timespec="minutes") <= i["ts"] <= end.isoformat(timespec="minutes")]
            if keep:
                with connect() as conn:
                    before = conn.total_changes
                    conn.executemany(
                        "INSERT OR IGNORE INTO macro_news(id, ts, office, title, body, url, topics, first_seen) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [(i["id"], i["ts"], i["office"], i["title"], i["body"], i["url"],
                          ",".join(topics_of(i["title"])), now) for i in keep])
                    total += conn.total_changes - before
            if min(i["ts"] for i in items) < start.isoformat(timespec="minutes"):
                break
            _time.sleep(0.12)
    if log:
        log(f"{day} 시장 뉴스 보충 {total}건")
    return total


def sector_news(t_from: datetime, t_to: datetime, names: list[str]) -> list[dict]:
    """같은 업종 종목 이름이나 '방산주' 같은 업종 말이 제목에 들어간 시황 기사 (업종이 왜 움직였는지 설명)."""
    words = [n for n in names if n and len(n) >= 2] + list(SECTOR_WORDS)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM macro_news WHERE ts BETWEEN ? AND ? ORDER BY ts DESC",
            (t_from.isoformat(timespec="minutes"), t_to.isoformat(timespec="minutes")))]
    out = []
    for r in rows:
        hit = [w for w in words if w in r["title"]]
        if hit:
            out.append({**r, "hit": hit[:3], "tone": direction_of(r["title"])})
    return out


# ---- 같은 내용의 한국어 기사가 있으면 영어 기사 대신 한국어 기사를 보여줌 ----
_ENTITIES = [
    (r"\biran", ("이란",)), (r"\bisrael", ("이스라엘",)), (r"hormuz", ("호르무즈",)), (r"houthi", ("후티",)),
    (r"\bsaudi", ("사우디",)), (r"\bgaza", ("가자",)), (r"\bhamas", ("하마스",)), (r"hezbollah", ("헤즈볼라",)),
    (r"middle east", ("중동",)), (r"\bcarrier", ("항모", "항공모함")), (r"\btroops|\bsoldiers|deploy", ("병력", "파병", "배치")),
    (r"\btanker", ("유조선",)), (r"\boil\b|\bbrent|\bcrude", ("유가", "원유")), (r"\brussia", ("러시아",)),
    (r"ukrain", ("우크라이나",)), (r"\bputin", ("푸틴",)), (r"zelensk", ("젤렌스키",)), (r"\bnato\b", ("나토", "NATO")),
    (r"north korea|pyongyang", ("북한",)), (r"kim jong", ("김정은",)), (r"missile", ("미사일",)), (r"\bdrone", ("드론",)),
    (r"pentagon", ("펜타곤", "국방부")), (r"\btrump", ("트럼프",)), (r"tariff", ("관세",)), (r"sanction", ("제재",)),
    (r"ceasefire|truce", ("휴전",)), (r"airstrike|strikes? on|attack", ("공습", "공격", "타격")),
    (r"defen[cs]e stocks", ("방산주",)), (r"\bchina|beijing", ("중국",)), (r"taiwan", ("대만",)), (r"nuclear", ("핵",)),
    (r"midterm|election", ("선거",)), (r"federal reserve|\bfed\b", ("연준",)), (r"treasury|yield", ("국채", "금리")),
    (r"semiconductor|chip", ("반도체",)), (r"micron", ("마이크론",)), (r"nvidia", ("엔비디아",)),
]
_ENT_RX = [(re.compile(a, re.I), b) for a, b in _ENTITIES]


def _is_korean(r: dict) -> bool:
    return bool(re.search(r"[가-힣]", r.get("title") or "")) and not (r.get("office") or "").endswith("(외신)")


def _ents_en(text: str) -> set:
    return {i for i, (rx, _) in enumerate(_ENT_RX) if rx.search(text)}


def _ents_ko(text: str) -> set:
    return {i for i, (_, ko) in enumerate(_ENT_RX) if any(w in text for w in ko)}


def prefer_korean(items: list[dict], pool: list[dict]) -> list[dict]:
    """영어(외신) 기사마다 같은 이야기를 다룬 한국어 기사(앞뒤 하루 안, 핵심 낱말 2개 이상 겹침)를 찾아 바꿔 보여줌.
    이미 목록에 같은 이야기의 한국어 기사가 있으면 영어 기사는 빼고 그 한국어 기사에 '원문'으로 붙임.
    시각(ts)은 원래 기사 것을 유지 (움직임 전·후 판단이 바뀌지 않게). 한국어 기사를 앞에 둠."""
    kos = [r for r in pool if _is_korean(r)]
    out = [dict(it) for it in items if _is_korean(it)]
    seen_urls = set()
    out = [x for x in out if not (x.get("url") in seen_urls or seen_urls.add(x.get("url")))]
    by_url = {x.get("url"): x for x in out}
    for it in items:
        if _is_korean(it):
            continue
        e = _ents_en((it.get("title") or "") + " " + (it.get("body") or ""))
        best, best_key = None, None
        if e:
            t0 = datetime.fromisoformat(it["ts"][:16])
            for k in list(out) + kos:
                try:
                    dt = (datetime.fromisoformat(k["ts"][:16]) - t0).total_seconds() / 3600
                except (ValueError, KeyError):
                    continue
                if not -12 <= dt <= 24:
                    continue
                n = len(e & _ents_ko(k["title"] + " " + (k.get("body") or "")[:120]))
                key = (n, k.get("url") in by_url, -abs(dt))
                if n >= 2 and (best_key is None or key > best_key):
                    best, best_key = k, key
        en_office = (it.get("office") or "").replace(" (외신)", "")
        orig = {"title": it.get("title"), "url": it.get("url"), "office": en_office, "ts": it["ts"]}
        if best is not None and best.get("url") in by_url:
            by_url[best["url"]].setdefault("orig", orig)        # 이미 있는 한국어 기사에 원문으로 붙이고 영어 기사는 뺌
        elif best is not None:
            x = {**it, "title": best["title"], "url": best["url"], "office": f"{best['office']} · 원문 {en_office}", "orig": orig}
            by_url[best["url"]] = x
            out.append(x)
        else:
            out.append(dict(it))
    out.sort(key=lambda x: 0 if (_is_korean(x) or x.get("orig")) else 1)
    return out


# 업종(사업 분야)별로 그 업종을 움직이는 이슈 주제
# 업종(사업 분야) 이름에 들어 있는 말로 분류 → 그 업종을 움직이는 이슈 주제
SEG_TOPICS = {
    "방산": ("중동·이란", "러시아·우크라이나", "북한·안보", "미국 국방", "방산 수출·국방"),
    "우주": ("우주·위성",),
    "반도체": ("반도체·디스플레이", "소재·수출통제"),
    "디스플레이": ("반도체·디스플레이",),
    "광학": ("광학·레이저·적외선", "소재·수출통제"),
    "시장": ("반도체·디스플레이", "유가·금리·환율", "트럼프·미국 정책", "코스닥·수급", "정부 정책·예산", "중동·이란"),
}
SEG_WORDS = {"방산": ("방산", "방위산업", "defense stocks", "defence stocks"), "우주": ("우주항공", "우주주", "위성주"),
             "반도체": ("반도체 장비", "반도체주", "소부장"), "디스플레이": ("디스플레이",),
             "광학": ("광학주", "광학"), "시장": ("코스닥", "코스피", "증시")}


def seg_category(seg: str | None) -> str | None:
    for k in SEG_TOPICS:
        if seg and k in seg:
            return k
    return None


def drivers(seg: str, t_from: datetime, t_move: datetime, t_end: datetime, sign: int) -> dict | None:
    """업종이 왜 움직였나 (추정): 움직임 전에 나온 그 업종 관련 이슈 기사 + 그날 업종 시황 기사가 꼽은 이유.

    - lead: t_from(전 거래일 오후) ~ t_move(움직임 시작) 사이 업종 관련 주제 기사, 주제별로 묶어 건수가 많은 순
    - explain: 그날 업종 등락을 다룬 시황 기사('방산주 강세' 등) — 기사가 꼽은 이유의 주제를 함께 셈
    """
    cat = seg_category(seg)
    want = SEG_TOPICS.get(cat)
    if not want:
        return None
    day_end = datetime.combine(t_end.date(), datetime.min.time()) + timedelta(hours=23, minutes=59)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT ts, office, title, body, url FROM macro_news WHERE ts BETWEEN ? AND ? ORDER BY ts",
            (t_from.isoformat(timespec="minutes"), day_end.isoformat(timespec="minutes")))]
    move = t_move.isoformat(timespec="minutes")
    lead, explain, seen = {}, [], set()

    def tp(text):                      # '방산주' 같은 업종 이름 자체는 이유가 아니므로 빼고 주제를 찾음
        for w in SEG_WORDS.get(cat, ()):
            text = text.replace(w, " ")
        return [t for t in topics_of(text) if t in want]
    for r in rows:
        key = r["title"][:18]
        if key in seen:
            continue
        seen.add(key)
        tps = tp(r["title"] + " " + (r.get("body") or "")[:80])
        if r["ts"] <= move and tps:
            for t in tps:
                lead.setdefault(t, []).append(r)
        if r["ts"] >= t_move.date().isoformat() and any(w in r["title"] for w in SEG_WORDS.get(cat, ())) \
                and direction_of(r["title"]) * sign > 0:
            explain.append({**r, "cited": tp(r["title"] + " " + (r.get("body") or ""))})
    if not lead and not explain:
        return None
    cited = {}
    for e in explain:
        for t in e["cited"]:
            cited[t] = cited.get(t, 0) + 1
    order = sorted(set(lead) | set(cited), key=lambda t: (-cited.get(t, 0), -len(lead.get(t, []))))
    topics = [{"topic": t, "n_lead": len(lead.get(t, [])), "n_cited": cited.get(t, 0),
               "items": [{k: x[k] for k in ("ts", "office", "title", "url", "orig") if k in x}
                         for x in prefer_korean(sorted(lead.get(t, []), key=lambda x: x["ts"], reverse=True)[:6], rows)[:3]]}
              for t in order]
    return {"segment": seg, "topics": topics, "top": topics[0] if topics else None,
            "explain": [{k: x[k] for k in ("ts", "office", "title", "url", "cited", "orig") if k in x}
                        for x in prefer_korean(explain, rows)[:4]]}


def direction_of(title: str) -> int:
    up = sum(w in title for w in UP_WORDS)
    dn = sum(w in title for w in DOWN_WORDS)
    return (up > dn) - (dn > up)


def fetch_theme_groups() -> list[dict]:
    out = []
    for p in range(1, 5):
        d = naver._json(f"https://m.stock.naver.com/api/stocks/theme?page={p}&pageSize=100")
        groups = d.get("groups") or []
        out += groups
        if len(out) >= int(d.get("totalCount") or 0) or not groups:
            break
    for i, g in enumerate(sorted(out, key=lambda g: -float(g.get("changeRate") or 0)), start=1):
        g["rank"] = i
    return out


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def fetch_theme_detail(no: int) -> dict:
    d = naver._json(f"https://m.stock.naver.com/api/stocks/theme/{no}?page=1&pageSize=100")
    stocks = [{"code": s.get("itemCode"), "name": s.get("stockName"),
               "chg": float(str(s.get("fluctuationsRatio") or 0).replace(",", "") or 0),
               "cap": _num(s.get("marketValue")), "value": _num(s.get("accumulatedTradingValue"))}
              for s in d.get("stocks") or []]
    return {"info": d.get("groupInfo") or {}, "stocks": stocks, "reasons": d.get("themeItemInfoMap") or {}}


def sync_stock_themes(code: str, log=None) -> list[dict]:
    """이 종목이 속한 테마 찾기 (전체 테마를 한 번 훑음, 1~2분 걸림. 일주일에 한 번)."""
    groups = fetch_theme_groups()
    found = []
    for g in groups:
        try:
            det = fetch_theme_detail(int(g["no"]))
        except naver.DataSourceError:
            continue
        if any(s["code"] == code for s in det["stocks"]):
            found.append({"no": int(g["no"]), "name": g["name"], "reason": det["reasons"].get(code, "")})
        _time.sleep(0.12)
    if found:
        now = datetime.now().isoformat(timespec="minutes")
        with connect() as conn:
            conn.execute("DELETE FROM stock_themes WHERE code=?", (code,))
            conn.executemany("INSERT INTO stock_themes(code, theme_no, theme_name, reason, updated_at) VALUES (?, ?, ?, ?, ?)",
                             [(code, f["no"], f["name"], f["reason"], now) for f in found])
    if log:
        log.info("이 종목 테마 %d개: %s", len(found), ", ".join(f["name"] for f in found))
    return found


# 묶음 매수(바스켓) 확인용: 업종 이름 → 네이버 테마 이름에 들어가는 말
BASKET_THEMES = {"방산": "방위산업", "우주항공": "우주항공"}


def sync_basket_universe(code: str, per_theme: int = 15, log=None) -> dict:
    """이 종목이 속한 네이버 테마(방위산업, 우주항공)의 대형 종목 목록.

    '같은 창구가 업종을 묶어 샀나'를 내가 고른 종목 몇 개가 아니라 네이버 테마 구성 종목으로 확인하기 위함.
    시가총액(없으면 거래대금) 큰 순서로 테마마다 최대 per_theme개.
    """
    from .db import set_state
    out = {}
    for t in my_themes(code):
        seg = next((k for k, w in BASKET_THEMES.items() if w in (t["theme_name"] or "")), None)
        if not seg:
            continue
        try:
            det = fetch_theme_detail(int(t["theme_no"]))
        except naver.DataSourceError:
            continue
        st = [x for x in det["stocks"] if x["code"] and x["code"] != code]
        st.sort(key=lambda x: -(x["cap"] or x["value"]))
        out[seg] = {"theme": t["theme_name"], "n_theme": len(det["stocks"]),
                    "stocks": [{"code": x["code"], "name": x["name"]} for x in st[:per_theme]]}
    if out:
        set_state("basket_universe", json.dumps(out, ensure_ascii=False))
        if log:
            log.info("묶음 매수 확인 종목: %s", ", ".join(f"{k} {len(v['stocks'])}개" for k, v in out.items()))
    return out


def basket_universe() -> dict:
    from .db import get_state
    st = get_state("basket_universe")
    try:
        return json.loads(st["value"]) if st else {}
    except (ValueError, TypeError):
        return {}


def basket_codes() -> list[dict]:
    """[{code, name, segment}] — 테마 구성 종목 (중복 제거)."""
    seen, out = set(), []
    for seg, v in basket_universe().items():
        for x in v.get("stocks") or []:
            if x["code"] not in seen:
                seen.add(x["code"])
                out.append({**x, "segment": seg})
    return out


def my_themes(code: str) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM stock_themes WHERE code=? ORDER BY theme_no", (code,))]


def themes_age_days(code: str) -> float:
    with connect() as conn:
        r = conn.execute("SELECT MAX(updated_at) FROM stock_themes WHERE code=?", (code,)).fetchone()
    if not r or not r[0]:
        return 1e9
    return (datetime.now() - datetime.fromisoformat(r[0])).total_seconds() / 86400


def snapshot_themes(code: str, now: datetime, trading_day: str) -> int:
    """테마 등락 기록. 장중에는 시각별(theme_snap), 날짜별 마지막 값(theme_daily)."""
    groups = fetch_theme_groups()
    if not groups:
        return 0
    mine = {t["theme_no"] for t in my_themes(code)}
    n_all = len(groups)
    keep = [g for g in groups if int(g["no"]) in mine or g["rank"] <= 15]
    ts = now.replace(second=0, microsecond=0).isoformat(timespec="minutes")
    leaders = {}
    for g in keep:
        if int(g["no"]) in mine:
            try:
                det = fetch_theme_detail(int(g["no"]))
                top = sorted(det["stocks"], key=lambda s: -s["chg"])[:3]
                me = next((s for s in det["stocks"] if s["code"] == code), None)
                leaders[int(g["no"])] = json.dumps({"top": top, "me": me}, ensure_ascii=False)
            except naver.DataSourceError:
                pass
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO theme_snap(ts, theme_no, name, change_rate, rank, n_themes, rise, total) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(ts, int(g["no"]), g["name"], float(g.get("changeRate") or 0), g["rank"], n_all,
              int(g.get("riseCount") or 0), int(g.get("totalCount") or 0)) for g in keep])
        for g in keep:
            no = int(g["no"])
            conn.execute(
                "INSERT INTO theme_daily(date, theme_no, name, change_rate, rank, n_themes, rise, total, leaders, mine, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(date, theme_no) DO UPDATE SET "
                "change_rate=excluded.change_rate, rank=excluded.rank, n_themes=excluded.n_themes, rise=excluded.rise, "
                "total=excluded.total, leaders=COALESCE(excluded.leaders, theme_daily.leaders), mine=excluded.mine, "
                "updated_at=excluded.updated_at",
                (trading_day, no, g["name"], float(g.get("changeRate") or 0), g["rank"], n_all,
                 int(g.get("riseCount") or 0), int(g.get("totalCount") or 0), leaders.get(no), int(no in mine), ts))
        conn.execute("DELETE FROM theme_snap WHERE ts < ?", ((now - timedelta(days=60)).isoformat(timespec="minutes"),))
    return len(keep)


def _price_infos(d) -> list[dict]:
    out = []
    for r in (d or {}).get("priceInfos") or []:
        ld = str(r.get("localDate") or "")
        if len(ld) != 8:
            continue
        out.append({"date": f"{ld[0:4]}-{ld[4:6]}-{ld[6:8]}", "open": r.get("openPrice"), "high": r.get("highPrice"),
                    "low": r.get("lowPrice"), "close": r.get("closePrice"),
                    "volume": int(r.get("accumulatedTradingVolume") or 0)})
    return out


def fetch_daily(code: str, days: int = 420) -> list[dict]:
    """일봉. fchart(1년 이상, KOSDAQ 지수 포함). 실패하면 api.stock.naver.com(약 6개월)."""
    if True:
        try:
            end = date.today()
            raw = naver.http_get("https://fchart.stock.naver.com/siseJson.nhn?symbol=%s&requestType=1&startTime=%s"
                                 "&endTime=%s&timeframe=day" % (code, (end - timedelta(days=days)).strftime("%Y%m%d"),
                                                               end.strftime("%Y%m%d")))
            text = raw.decode("euc-kr", errors="replace")
            out = []
            for line in text.splitlines():
                line = line.strip().rstrip(",")
                if not line.startswith('["'):
                    continue
                r = json.loads(line)
                d = str(r[0])
                out.append({"date": f"{d[0:4]}-{d[4:6]}-{d[6:8]}", "open": r[1], "high": r[2], "low": r[3],
                            "close": r[4], "volume": int(r[5] or 0)})
            if out:
                return out
        except (naver.DataSourceError, ValueError, IndexError):
            pass
    kind = "index" if code == KOSDAQ else "item"
    return _price_infos(naver._json(f"https://api.stock.naver.com/chart/domestic/{kind}/{code}?periodType=dayCandle"))


def sync_daily_bars(codes: list[str]) -> int:
    n = 0
    for c in codes:
        try:
            rows = fetch_daily(c)
        except naver.DataSourceError:
            continue
        with connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO daily_bars(symbol, date, open, high, low, close, volume) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [(c, r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows])
        n += len(rows)
    return n


def sync_overseas() -> int:
    n = 0
    for sym, name in US_DEFENSE:
        try:
            rows = _price_infos(naver._json(f"https://api.stock.naver.com/chart/foreign/item/{sym}?periodType=dayCandle"))
        except naver.DataSourceError:
            continue
        rows = rows[-40:]
        with connect() as conn:
            prev = None
            for r in rows:
                chg = (r["close"] / prev - 1) * 100 if prev else None
                conn.execute("INSERT OR REPLACE INTO overseas_daily(sym, date, name, close, chg) VALUES (?, ?, ?, ?, ?)",
                             (sym, r["date"], name, r["close"], chg))
                prev = r["close"]
        n += len(rows)
    try:
        d = naver._json("https://m.stock.naver.com/front-api/marketIndex/prices?category=energy&reutersCode=CLcv1&page=1&pageSize=20")
        with connect() as conn:
            for r in d.get("result") or []:
                conn.execute("INSERT OR REPLACE INTO overseas_daily(sym, date, name, close, chg) VALUES (?, ?, ?, ?, ?)",
                             ("WTI", str(r.get("localTradedAt"))[:10], "국제유가(WTI)",
                              float(str(r.get("closePrice")).replace(",", "")), float(r.get("fluctuationsRatio") or 0)))
                n += 1
    except (naver.DataSourceError, ValueError, TypeError):
        pass
    return n


# ------------------------------------------------------------------ 해석

def _second_thursday(y: int, m: int) -> date:
    d = date(y, m, 1)
    first_thu = d + timedelta(days=(3 - d.weekday()) % 7)
    return first_thu + timedelta(days=7)


def calendar_notes(day: date) -> list[str]:
    notes = []
    if day == _second_thursday(day.year, day.month):
        notes.append(("선물·옵션 동시 만기일" if day.month in (3, 6, 9, 12) else "옵션 만기일")
                     + "입니다. 외국계·기관의 차익거래·포지션 정리 물량이 평소보다 많을 수 있습니다.")
    with connect() as conn:
        nxt = conn.execute("SELECT MIN(date) FROM daily_bars WHERE symbol=? AND date > ?", (KOSDAQ, day.isoformat())).fetchone()
    gap_next = None
    if nxt and nxt[0]:
        gap_next = (date.fromisoformat(nxt[0]) - day).days
    else:
        # 다음 거래일을 아직 모르면 주말·연휴 직전 여부를 요일로만 짐작하지 않음
        gap_next = None
    if gap_next and gap_next >= 4:
        notes.append(f"다음 거래일까지 {gap_next - 1}일 쉬는 연휴 직전입니다. 연휴 리스크를 줄이거나 미리 담는 매매가 섞일 수 있습니다.")
    return notes


def _theme_rows(code: str, day: str):
    with connect() as conn:
        mine = [dict(r) for r in conn.execute(
            "SELECT * FROM theme_daily WHERE date=? AND mine=1 ORDER BY change_rate DESC", (day,))]
        top = [dict(r) for r in conn.execute(
            "SELECT * FROM theme_daily WHERE date=? ORDER BY rank LIMIT 8", (day,))]
    for r in mine + top:
        try:
            r["leaders"] = json.loads(r["leaders"]) if r.get("leaders") else None
        except ValueError:
            r["leaders"] = None
    return mine, top


def _theme_move_in_window(theme_no: int, t0: datetime, t1: datetime):
    """장중 기록이 있으면 구간 동안 테마 등락률 변화(%p)."""
    with connect() as conn:
        a = conn.execute("SELECT change_rate, ts FROM theme_snap WHERE theme_no=? AND ts<=? AND substr(ts,1,10)=? "
                         "ORDER BY ts DESC LIMIT 1", (theme_no, t0.isoformat(timespec="minutes"), t0.date().isoformat())).fetchone()
        b = conn.execute("SELECT change_rate, ts FROM theme_snap WHERE theme_no=? AND ts>=? AND substr(ts,1,10)=? "
                         "ORDER BY ts LIMIT 1", (theme_no, t1.isoformat(timespec="minutes"), t1.date().isoformat())).fetchone()
    if not a or not b or datetime.fromisoformat(b[1]) - t1 > timedelta(minutes=20):
        return None          # 구간 앞뒤 기록이 없으면 계산하지 않음 (장중에 엔진이 켜져 있어야 쌓임)
    return {"from": a[0], "to": b[0], "delta": b[0] - a[0], "from_ts": a[1], "to_ts": b[1]}


def _overnight(day: date) -> list[dict]:
    out = []
    with connect() as conn:
        for sym, name in US_DEFENSE + [("WTI", "국제유가(WTI)")]:
            r = conn.execute("SELECT * FROM overseas_daily WHERE sym=? AND date < ? ORDER BY date DESC LIMIT 1",
                             (sym, day.isoformat())).fetchone()
            if r and r["chg"] is not None:
                out.append({"sym": sym, "name": r["name"] or name, "date": r["date"], "chg": r["chg"]})
    return out


def news_hits(t_from: datetime, t_to: datetime) -> dict:
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM macro_news WHERE ts BETWEEN ? AND ? ORDER BY ts DESC",
            (t_from.isoformat(timespec="minutes"), t_to.isoformat(timespec="minutes")))]
    by = {}
    for r in rows:
        for t in topics_of(r["title"]):          # 주제 목록이 바뀌어도 맞게, 읽을 때 다시 분류
            by.setdefault(t, []).append(r)
    return by


def coverage(t_from: datetime, t_to: datetime) -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM macro_news WHERE ts BETWEEN ? AND ?",
                            (t_from.isoformat(timespec="minutes"), t_to.isoformat(timespec="minutes"))).fetchone()[0]


def context(code: str, t_start: datetime, t_end: datetime, sign: int, buyer: dict | None = None,
            own_change: float | None = None) -> dict:
    """이상변동 시점의 거시·정치·테마 배경."""
    day = t_end.date()
    dstr = day.isoformat()
    lines = []
    themes_mine, themes_top = _theme_rows(code, dstr)
    mine_def = my_themes(code)

    # 1) 테마
    strong_theme = None
    for t in themes_mine:
        mv = _theme_move_in_window(t["theme_no"], t_start - timedelta(minutes=5), t_end)
        t["window"] = mv
        hot = t["change_rate"] * sign >= 1.5 and t["rank"] <= max(20, t["n_themes"] * 0.1)
        if mv and mv["delta"] * sign >= 0.8:
            hot = True
        t["hot"] = hot
        if hot and (strong_theme is None or abs(t["change_rate"]) > abs(strong_theme["change_rate"])):
            strong_theme = t
    if themes_mine:
        parts = []
        for t in themes_mine[:4]:
            s = f"{t['name']} {t['change_rate']:+.2f}% ({t['n_themes']}개 테마 중 {t['rank']}위)"
            if t.get("window"):
                s += f", 이 구간 {t['window']['delta']:+.2f}%p"
            parts.append(s)
        big = max(themes_mine, key=lambda t: abs(t["change_rate"]))
        if strong_theme:
            tail = ". 이 종목이 속한 테마 전체가 " + ("강했던" if strong_theme["change_rate"] > 0 else "약했던") + " 날이고, 이 움직임과 방향이 같습니다."
        elif abs(big["change_rate"]) >= 1.5:
            tail = (f". 이날 테마는 {'강세' if big['change_rate'] > 0 else '약세'}였지만 이 움직임은 반대 방향이라, "
                    "테마보다 다른 요인(차익 실현·개별 수급 등)을 먼저 볼 만합니다.")
        else:
            tail = ". 테마 전체는 크게 움직이지 않았습니다."
        lines.append({"kind": "이 종목 테마", "strong": bool(strong_theme), "text": " / ".join(parts) + tail})
    elif mine_def:
        lines.append({"kind": "이 종목 테마", "strong": False,
                      "text": f"이 종목 테마({', '.join(t['theme_name'] for t in mine_def[:4])})의 이날 등락 기록이 없습니다. "
                              "테마 등락은 감시 엔진이 켜진 날부터 쌓입니다."})
    if themes_top:
        lines.append({"kind": "이날 강한 테마", "strong": False,
                      "text": ", ".join(f"{t['name']} {t['change_rate']:+.1f}%" for t in themes_top[:5])})

    # 2) 거시·정치 뉴스 (전날 장 마감 뒤 ~ 구간 끝)
    prev_close = datetime.combine(day - timedelta(days=1), datetime.min.time()) + timedelta(hours=15, minutes=30)
    if day.weekday() == 0:
        prev_close -= timedelta(days=2)
    hits = news_hits(prev_close, t_end)
    covered = coverage(prev_close, t_end)
    topic_rows = []
    for name, words, rel in TOPICS:
        items = hits.get(name) or []
        if not items:
            continue
        items = prefer_korean(items[:8], [r for v in hits.values() for r in v]) + items[8:]
        before = [i for i in items if i["ts"] <= t_start.isoformat(timespec="minutes")]
        calm = sum(1 for i in before if any(w in i["title"] for w in DEESCALATE))
        topic_rows.append({"topic": name, "relevance": rel, "count": len(items), "before": len(before),
                           "calm": calm,
                           "items": [{"ts": i["ts"], "title": i["title"], "office": i["office"], "url": i["url"],
                                      "lead": i["ts"] <= t_start.isoformat(timespec="minutes")} for i in items[:4]]})
    topic_rows.sort(key=lambda r: (PRIORITY.get(r["relevance"], 9), -r["before"], -r["count"]))
    support = {r["topic"] for r in supporting_topics(topic_rows, sign)}
    for r in topic_rows[:4]:
        lead = [i for i in r["items"] if i["lead"]]
        sample = (lead or r["items"])[0]
        lines.append({"kind": r["topic"], "strong": r["topic"] in support,
                      "text": f"움직임 전 관련 기사 {r['before']}건(같은 날 전체 {r['count']}건). 예: 「{sample['title']}」 "
                              f"{sample['office']} {sample['ts'][5:16].replace('T', ' ')}"
                              + ("" if r["relevance"] in RELEVANT else " — 시장 전체에 영향을 주는 이슈입니다."),
                      "url": sample["url"]})
    if not covered:
        lines.append({"kind": "시장 뉴스", "strong": False,
                      "text": "이 시간대 시장 전체 뉴스 기록이 없습니다. (감시 엔진이 켜진 뒤부터 쌓이며, 시작할 때 최근 하루치를 보충합니다)"})

    # 3) 전날 밤 해외
    ov = _overnight(day)
    if ov:
        us = [o for o in ov if o["sym"] != "WTI"]
        wti = next((o for o in ov if o["sym"] == "WTI"), None)
        avg = sum(o["chg"] for o in us) / len(us) if us else None
        txt = ""
        if us:
            txt += f"전날 밤 미국 방산주 평균 {avg:+.2f}% (" + ", ".join(f"{o['name']} {o['chg']:+.1f}%" for o in us) + ")"
        if wti:
            txt += (", " if txt else "") + f"국제유가(WTI) {wti['chg']:+.2f}%"
        lines.append({"kind": "전날 밤 해외", "strong": bool(avg is not None and abs(avg) >= 1.5 and avg * sign > 0),
                      "text": txt + "."})

    # 4) 시장 일정
    for n in calendar_notes(day):
        lines.append({"kind": "시장 일정", "strong": False, "text": n})

    # 종합 판단 (단정하지 않고 가능성 순서로)
    verdict = _verdict(strong_theme, topic_rows, ov, buyer, sign)
    return {"themes_mine": themes_mine, "themes_top": themes_top, "topics": topic_rows, "overnight": ov,
            "lines": lines, "verdict": verdict, "strong_theme": strong_theme,
            "theme_names": [t["theme_name"] for t in mine_def]}


def supporting_topics(topic_rows, sign) -> list[dict]:
    """주가 방향과 맞는 거시·정치 주제만.

    - 방산: 긴장 고조 → 상승, 휴전·합의 → 하락
    - 우주: 상승 쪽 재료
    - 광학·소재·반도체·정책: 방향을 제목만으로 알기 어려워, 기사 제목의 강세/약세 말로 판단 (없으면 양쪽 모두 후보)
    - 시장(코스닥·수급·금리): 제목의 강세/약세 말과 주가 방향이 같을 때만
    """
    out = []
    for r in topic_rows:
        if r["before"] < 1:
            continue
        rel = r["relevance"]
        if rel in ("방산", "방산·시장"):
            if r["before"] < 2:
                continue
            escal = r["before"] - r["calm"] > r["calm"]
            if (sign > 0 and escal) or (sign < 0 and r["calm"] > 0 and not escal):
                out.append(r)
        elif rel == "우주":
            if sign > 0:
                out.append(r)
        else:
            tones = [direction_of(i["title"]) for i in r["items"] if i["lead"]]
            net = sum(tones)
            if rel == "시장":
                if net * sign > 0 and r["before"] >= 2:
                    out.append(r)
            elif net * sign >= 0:
                out.append(r)
    return out


def _verdict(strong_theme, topic_rows, ov, buyer, sign) -> str | None:
    side = "매수" if sign >= 0 else "매도"
    geo = supporting_topics(topic_rows, sign)
    th = f"이 종목이 속한 '{strong_theme['name']}' 테마가 함께 움직였" if strong_theme else None
    gt = f"움직임 전에 {', '.join(r['topic'] for r in geo[:2])} 관련 기사가 이어졌습니다." if geo else None
    if th and gt:
        s = th + "고, " + gt
    elif th:
        s = th + "습니다."
    elif gt:
        s = gt
    else:
        return None
    if buyer:
        kinds = {l["kind"] for l in buyer.get("lines", [])}
        b = buyer["broker"]
        if "업종 묶음" in kinds:
            s += f" {b} 창구가 같은 업종 종목도 함께 {side}한 점을 보면, 이 종목 개별 호재보다 이런 업종·테마 흐름에 따라 묶어서 {side}했을 가능성이 큽니다."
        elif "업종 일부" in kinds:
            s += f" {b} 창구가 같은 업종 일부 종목에서도 {side}해, 업종·테마 흐름과 이 종목 개별 관심이 섞였을 수 있습니다."
        elif "개별 관심" in kinds:
            s += f" 다만 {b} 창구는 같은 업종 종목에선 {side}하지 않아, 업종 흐름보다 이 종목에 대한 개별 관심일 가능성도 있습니다."
        else:
            s += f" {b} 창구의 같은 업종 거래 기록이 없어 묶음 {side}인지는 확인하지 못했습니다."
    s += " (배경 정보이며, 실제 주문 이유는 공개되지 않습니다)"
    return s
