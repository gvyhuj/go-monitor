"""국제·정치·업종 이슈 기사 수집 (열쇠 없이 구글 뉴스 RSS, 국내 + 외신).

네이버 증권 뉴스 목록에는 국제·정치 기사가 거의 없어서, '외국계가 방산주를 왜 샀을까' 같은
업종 움직임의 배경(중동·이란, 러시아·우크라이나, 북한, 미국 국방, 방산 수출 등)을 놓칩니다.
주제어별 최신 기사를 모아 macro_news 표에 함께 저장합니다.

출처
- 기본: 구글 뉴스 RSS (열쇠 필요 없음, 무료). 한국어 검색어 + 영어 검색어(로이터·블룸버그 등 외신)
    https://news.google.com/rss/search?q=...&hl=ko&gl=KR&ceid=KR:ko
  지난 날짜는 검색어에 'after:YYYY-MM-DD before:YYYY-MM-DD' 를 붙여 그 기간만 받음.
- 선택: NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 비밀값이 있으면 네이버 검색 API도 함께 사용.
구글이 막거나 형식을 바꾸면 이 수집만 멈추고 나머지 감시는 계속됩니다.
"""
from __future__ import annotations

import email.utils
import hashlib
import html
import json
import os
import re
import time as _time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

from .db import connect

API = "https://openapi.naver.com/v1/search/news.json"
KST = timezone(timedelta(hours=9))

# 업종을 움직이는 이슈를 찾는 검색어 (settings.json 'driver_queries' 로 바꿀 수 있음)
DEFAULT_QUERIES = [
    "방산주", "방산 ETF", "이란 미국", "중동 긴장", "호르무즈", "러시아 우크라이나", "북한 미사일",
    "방산 수출", "국방예산", "미 국방부", "우주항공주", "반도체 장비주",
]

OFFICES = {
    "yna.co.kr": "연합뉴스", "yonhapnewstv.co.kr": "연합뉴스TV", "hankookilbo.com": "한국일보", "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보", "donga.com": "동아일보", "hani.co.kr": "한겨레", "khan.co.kr": "경향신문",
    "mk.co.kr": "매일경제", "hankyung.com": "한국경제", "sedaily.com": "서울경제", "edaily.co.kr": "이데일리",
    "mt.co.kr": "머니투데이", "fnnews.com": "파이낸셜뉴스", "asiae.co.kr": "아시아경제", "heraldcorp.com": "헤럴드경제",
    "newsis.com": "뉴시스", "news1.kr": "뉴스1", "etoday.co.kr": "이투데이", "newspim.com": "뉴스핌",
    "kbs.co.kr": "KBS", "imnews.imbc.com": "MBC", "news.sbs.co.kr": "SBS", "ytn.co.kr": "YTN", "jtbc.co.kr": "JTBC",
    "g-enews.com": "글로벌이코노믹", "seoul.co.kr": "서울신문", "kmib.co.kr": "국민일보", "segye.com": "세계일보",
    "dt.co.kr": "디지털타임스", "etnews.com": "전자신문", "biz.chosun.com": "조선비즈", "bizwatch.co.kr": "비즈워치",
    "jkn.co.kr": "재경일보", "nocutnews.co.kr": "노컷뉴스", "ohmynews.com": "오마이뉴스", "inews24.com": "아이뉴스24",
}


# ---- 출처 제한: 한국 언론 + 영어권 주요 언론만 (베트남·중국 등 다른 나라 매체는 받지 않음) ----
EN_DOMAINS = {
    "reuters.com": "Reuters", "bloomberg.com": "Bloomberg", "apnews.com": "AP", "wsj.com": "WSJ", "ft.com": "Financial Times",
    "cnbc.com": "CNBC", "nytimes.com": "New York Times", "washingtonpost.com": "Washington Post", "bbc.com": "BBC",
    "bbc.co.uk": "BBC", "theguardian.com": "The Guardian", "cnn.com": "CNN", "politico.com": "Politico", "axios.com": "Axios",
    "defensenews.com": "Defense News", "breakingdefense.com": "Breaking Defense", "defenseone.com": "Defense One",
    "militarytimes.com": "Military Times", "news.usni.org": "USNI News", "stripes.com": "Stars and Stripes",
    "janes.com": "Janes", "economist.com": "The Economist", "marketwatch.com": "MarketWatch", "barrons.com": "Barron's",
    "foxnews.com": "Fox News", "foxbusiness.com": "Fox Business", "nbcnews.com": "NBC News", "abcnews.go.com": "ABC News",
    "cbsnews.com": "CBS News", "npr.org": "NPR", "thehill.com": "The Hill", "forbes.com": "Forbes", "fortune.com": "Fortune",
    "businessinsider.com": "Business Insider", "time.com": "TIME", "newsweek.com": "Newsweek", "latimes.com": "LA Times",
    "usatoday.com": "USA Today", "thetimes.co.uk": "The Times", "telegraph.co.uk": "The Telegraph", "news.sky.com": "Sky News",
    "theglobeandmail.com": "Globe and Mail", "abc.net.au": "ABC Australia", "asia.nikkei.com": "Nikkei Asia",
    "koreaherald.com": "Korea Herald", "koreatimes.co.kr": "Korea Times", "koreajoongangdaily.joins.com": "Korea JoongAng Daily",
    "en.yna.co.kr": "Yonhap", "finance.yahoo.com": "Yahoo Finance",
}
EN_NAMES = {v.lower() for v in EN_DOMAINS.values()} | {
    "the associated press", "ap news", "the wall street journal", "bloomberg.com", "the new york times",
    "the washington post", "bbc news", "cnn international", "politico europe", "the korea herald", "the korea times",
    "yonhap news agency", "financial times", "the economist", "barron's", "fox business"}
KO_COM = {
    "hankookilbo.com", "chosun.com", "donga.com", "hankyung.com", "sedaily.com", "fnnews.com", "heraldcorp.com",
    "newsis.com", "newspim.com", "g-enews.com", "segye.com", "ohmynews.com", "inews24.com", "imbc.com", "ajunews.com",
    "kukinews.com", "newstomato.com", "viva100.com", "munhwa.com", "naeil.com", "etnews.com", "bloter.net", "sisajournal.com",
    "businesspost.co.kr", "newsway.co.kr", "v.daum.net", "news.nate.com", "m.news.nate.com", "n.news.naver.com", "msn.com",
}
FOREIGN_HINTS = ("vietnam", "viet", ".vn", "vnexpress", "baomoi", "xinhua", "people.cn", "chinadaily", "cgtn", "cctv", "china",
                 "nhk", "japan", "sputnik", "tass", "rodong", "kcna", "베트남", "인민", "신화", "중국", "조선중앙")


def _host(url: str) -> str:
    h = urllib.parse.urlparse(url or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _match(host: str, domains) -> str | None:
    for d in domains:
        if host == d or host.endswith("." + d):
            return d
    return None


def allowed(host: str, office: str, title: str, english: bool) -> bool:
    """한국 언론(한국어) 또는 영어권 주요 언론만."""
    low = (host + " " + office).lower()
    if any(h in low for h in FOREIGN_HINTS):
        return False
    if english:
        return bool(_match(host, EN_DOMAINS)) if host else office.lower() in EN_NAMES
    if not re.search(r"[가-힣]", title):
        return False
    if host:
        return host.endswith(".kr") or bool(_match(host, KO_COM)) or bool(re.search(r"[가-힣]", office))
    return bool(re.search(r"[가-힣]", office)) or office.lower() in EN_NAMES


def purge_disallowed() -> int:
    """이전에 받아둔 기사 중 허용 출처가 아닌 것(베트남 매체 등)을 지움 (구글 뉴스로 받은 것만)."""
    with connect() as conn:
        rows = conn.execute("SELECT id, office, title FROM macro_news WHERE id LIKE 'g%'").fetchall()
        bad = []
        for r in rows:
            office = r[1] or ""
            en = office.endswith(" (외신)")
            name = office[:-5] if en else office
            if not allowed("", name, r[2] or "", en):
                bad.append((r[0],))
        conn.executemany("DELETE FROM macro_news WHERE id=?", bad)
    return len(bad)


# 외신용 영어 검색어
EN_QUERIES = ["Iran US military", "Middle East tensions", "Strait of Hormuz", "Russia Ukraine war",
              "North Korea missile", "Pentagon defense spending", "defense stocks"]
GOOGLE = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{lang}"


def naver_enabled() -> bool:
    return bool(os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET"))


def enabled() -> bool:
    return os.environ.get("GO_ISSUE_NEWS", "1") != "0"     # 구글 뉴스는 열쇠가 필요 없어서 기본으로 켬


def google(query: str, english: bool = False) -> list[dict]:
    hl, gl, lang = ("en-US", "US", "en") if english else ("ko", "KR", "ko")
    url = GOOGLE.format(q=urllib.parse.quote(query), hl=hl, gl=gl, lang=lang)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; go-monitor/1.0)"})
    last = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                root = ET.fromstring(r.read())
            break
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 429):
                raise RuntimeError(f"구글 뉴스가 요청을 막았습니다 (HTTP {e.code})")
        except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError) as e:
            last = e
        _time.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError(f"구글 뉴스 접속 실패: {last}")
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = it.find("source")
        office = (src.text or "").strip() if src is not None else ""
        host = _host(src.get("url")) if src is not None else ""
        if office and title.endswith(" - " + office):
            title = title[: -len(office) - 3].strip()
        try:
            ts = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(KST).replace(tzinfo=None)
        except (TypeError, ValueError):
            continue
        if not allowed(host, office, title, english):
            continue
        if english and _match(host, EN_DOMAINS):
            office = EN_DOMAINS[_match(host, EN_DOMAINS)]
        desc = re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or ""))
        desc = re.sub(r"\s+", " ", desc).strip()
        if desc.startswith(title):
            desc = desc[len(title):].strip(" -")
        out.append({"id": "g" + hashlib.md5((title + office).encode()).hexdigest()[:18],
                    "ts": ts.isoformat(timespec="minutes"), "office": (office or "-") + (" (외신)" if english else ""),
                    "title": html.unescape(title), "body": desc[:300], "url": it.findtext("link") or ""})
    return out


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"</?b>", "", s or "")).strip()


def _office(url: str) -> str:
    host = urllib.parse.urlparse(url or "").netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    for dom, name in OFFICES.items():
        if host == dom or host.endswith("." + dom):
            return name
    return host or "-"


def _id(link: str, original: str) -> str:
    m = re.search(r"/article/(\d+)/(\d+)", link or "")
    if m:
        return m.group(1) + m.group(2)              # 네이버 증권 뉴스와 같은 번호 → 중복 저장 안 됨
    return "s" + hashlib.md5((original or link).encode()).hexdigest()[:18]


def search(query: str, start: int = 1, display: int = 100) -> list[dict]:
    url = f"{API}?query={urllib.parse.quote(query)}&display={display}&start={start}&sort=date"
    req = urllib.request.Request(url, headers={"X-Naver-Client-Id": os.environ.get("NAVER_CLIENT_ID", ""),
                                               "X-Naver-Client-Secret": os.environ.get("NAVER_CLIENT_SECRET", "")})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(f"네이버 검색 API 열쇠가 맞지 않습니다 (HTTP {e.code})")
            last = e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            last = e
        _time.sleep(1.2 * (attempt + 1))
    else:
        raise RuntimeError(f"네이버 검색 API 접속 실패: {last}")
    out = []
    for it in d.get("items") or []:
        try:
            ts = email.utils.parsedate_to_datetime(it["pubDate"]).astimezone(KST).replace(tzinfo=None)
        except (KeyError, TypeError, ValueError):
            continue
        link, orig = it.get("link") or "", it.get("originallink") or ""
        out.append({"id": _id(link, orig), "ts": ts.isoformat(timespec="minutes"), "office": _office(orig or link),
                    "title": _clean(it.get("title")), "body": _clean(it.get("description"))[:300],
                    "url": link if "news.naver.com" in link else (orig or link)})
    return out


def _save(items: list[dict]) -> int:
    from .macro import topics_of
    if not items:
        return 0
    now = datetime.now().isoformat(timespec="minutes")
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO macro_news(id, ts, office, title, body, url, topics, first_seen) VALUES (?,?,?,?,?,?,?,?)",
            [(i["id"], i["ts"], i["office"], i["title"], i["body"], i["url"],
              ",".join(topics_of(i["title"] + " " + i["body"][:80])), now) for i in items])
        return conn.total_changes - before


def queries() -> list[str]:
    try:
        from .detector import load_settings
        q = load_settings().get("driver_queries")
        return list(q) if q else DEFAULT_QUERIES
    except Exception:
        return DEFAULT_QUERIES


def _throttled(key: str, minutes: int) -> bool:
    """너무 자주 요청하지 않도록 (구글 뉴스는 장중 15분, 장 밖 60분 간격)."""
    from .db import get_state, set_state
    now = datetime.now()
    st = get_state(key)
    if st:
        try:
            if now - datetime.fromisoformat(st["value"]) < timedelta(minutes=minutes):
                return True
        except ValueError:
            pass
    set_state(key, now.isoformat(timespec="minutes"))
    return False


def sync_recent(log=None) -> int:
    """주제어별 최신 기사. 구글 뉴스(국내·외신) + 네이버 검색 API(열쇠가 있을 때)."""
    if not enabled():
        return 0
    now = datetime.now()
    market = now.weekday() < 5 and 8 <= now.hour < 16
    n = 0
    if not _throttled("issue_news:google", 15 if market else 60):
        jobs = [(q, False) for q in queries()] + [(q, True) for q in EN_QUERIES]
        for q, en in jobs:
            try:
                n += _save(google(q + " when:2d", en))
            except RuntimeError as e:
                if log:
                    log(f"이슈 기사 검색 실패({q}): {e}")
                if "막았" in str(e):
                    break
            _time.sleep(0.6)
    if naver_enabled():
        for q in queries():
            try:
                n += _save(search(q))
            except RuntimeError as e:
                if log:
                    log(f"네이버 검색 실패({q}): {e}")
                if "열쇠" in str(e):
                    break
            _time.sleep(0.1)
    return n


def backfill(day: date, log=None) -> int:
    """지난 이상변동 분석용: 그 날짜(전날 오후 포함) 기사만 받아 채움."""
    if not enabled():
        return 0
    prev = day - timedelta(days=3 if day.weekday() == 0 else 1)
    start = (datetime.combine(prev, datetime.min.time()) + timedelta(hours=15)).isoformat(timespec="minutes")
    end = (datetime.combine(day, datetime.min.time()) + timedelta(hours=23, minutes=59)).isoformat(timespec="minutes")
    from .db import get_state, set_state
    key = f"issue_news:backfill:{day.isoformat()}"
    if get_state(key):
        return 0
    rng = f" after:{(prev - timedelta(days=1)).isoformat()} before:{(day + timedelta(days=1)).isoformat()}"
    total = 0
    jobs = [(q, False) for q in queries()] + [(q, True) for q in EN_QUERIES]
    for q, en in jobs:
        try:
            total += _save([i for i in google(q + rng, en) if start <= i["ts"] <= end])
        except RuntimeError as e:
            if log:
                log(f"지난 이슈 기사 검색 실패({q}): {e}")
            if "막았" in str(e):
                return total
        _time.sleep(0.6)
    set_state(key, datetime.now().isoformat(timespec="minutes"))
    if log:
        log(f"{day} 이슈 기사 보충 {total}건 (구글 뉴스)")
    return total
