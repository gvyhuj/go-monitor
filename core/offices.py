"""언론사 이름을 한글 이름 하나로 맞춤.

같은 매체가 수집 경로마다 다르게 들어옴:
- 네이버 종목 뉴스: 한글 이름 ('뉴시스')
- 구글 뉴스: 매체가 정한 이름이라 영문·부제·주소가 섞임 ('Chosunbiz', '매일경제 마켓', 'https://www.2news.co.kr/')
- 네이버 검색 API: 주소만 줌 → 아는 주소는 이름으로, 모르는 주소는 'ebn.co.kr' 그대로였음

모르는 주소는 그 사이트 첫 화면의 매체 이름(og:site_name, 없으면 <title> 앞부분)을 한 번 읽어 state에 저장해 둠.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.parse
import urllib.request

from .db import connect, get_state, set_state

# 같은 매체의 다른 표기 → 한글 이름 (구글 뉴스·사이트 제목에서 실제로 본 것)
ALIASES = {
    "Chosunbiz": "조선비즈", "ChosunBiz": "조선비즈", "조선비즈 - Chosunbiz": "조선비즈",
    "매일경제 마켓": "매일경제", "MK": "매일경제", "Maeil Business Newspaper": "매일경제",
    "프리미엄콘텐츠": "네이버 프리미엄콘텐츠",
    "한경매거진&북": "한경매거진", "한국경제 매거진": "한경매거진",
    "Investing.com 한국어": "인베스팅닷컴", "Investing.com": "인베스팅닷컴",
    "Newsis": "뉴시스", "NEWSIS": "뉴시스", "News1": "뉴스1", "NEWS1": "뉴스1", "news1": "뉴스1",
    "Yonhap News": "연합뉴스", "연합뉴스 (Yonhap News)": "연합뉴스",
    "Edaily": "이데일리", "Money Today": "머니투데이", "Asia Economy": "아시아경제", "Herald Economy": "헤럴드경제",
    "The Fact": "더팩트", "Bloter": "블로터", "ZDNet Korea": "지디넷코리아", "ZDNET Korea": "지디넷코리아",
    "Etoday": "이투데이", "inews24": "아이뉴스24", "iNews24": "아이뉴스24", "Newspim": "뉴스핌", "NEWSPIM": "뉴스핌",
    "Seoul Economic Daily": "서울경제", "Hankyung": "한국경제", "The Korea Economic Daily": "한국경제",
    "Financial News": "파이낸셜뉴스", "Digital Times": "디지털타임스", "Electronic Times": "전자신문", "etnews": "전자신문",
    "이비엔(EBN)뉴스센터": "이비엔", "EBN": "이비엔",
}
# 사이트 이름에 붙는 꼬리 (제목에서 매체 이름만 남길 때)
_TAIL = re.compile(r"\s*(?:[-|:·]|::|ㅣ).*$")
_KEY = "office_names"           # state: {주소: 한글 이름}


def _host(s: str) -> str:
    s = (s or "").strip()
    if "://" not in s:
        s = "http://" + s
    h = urllib.parse.urlparse(s).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _looks_like_host(s: str) -> bool:
    return bool(re.fullmatch(r"(?:https?://)?[\w.-]+\.[a-z]{2,}(?:/.*)?", (s or "").strip(), re.I))


def _known() -> dict:
    st = get_state(_KEY)
    try:
        return json.loads(st["value"]) if st and st.get("value") else {}
    except (ValueError, TypeError):
        return {}


def _by_host(host: str, known: dict) -> str | None:
    from .newssearch import OFFICES
    for dom, name in OFFICES.items():
        if host == dom or host.endswith("." + dom):
            return name
    return known.get(host)


def canonical(office: str | None, known: dict | None = None) -> str:
    """한 언론사 이름 → 맞춘 이름. 모르는 주소는 그대로 둠 (resolve()가 이름을 찾으면 다음부터 바뀜)."""
    o = (office or "").strip()
    if not o or o in ("-", "직접 등록"):
        return o
    suffix = ""
    if o.endswith(" (외신)"):
        o, suffix = o[:-5].strip(), " (외신)"
    if _looks_like_host(o):
        o = _by_host(_host(o), known if known is not None else _known()) or _host(o)
    o = ALIASES.get(o, o)
    return o + suffix


def _fetch_name(host: str) -> str | None:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE      # 지역 매체는 인증서가 잘못된 곳이 많음 (이름만 읽음)
    req = urllib.request.Request("https://" + host, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            raw = r.read(300000)
    except Exception:  # noqa: BLE001 - 못 읽으면 주소 그대로
        return None
    text = raw.decode("utf-8", "replace")
    if text[:5000].count("�") > 5:
        text = raw.decode("euc-kr", "replace")
    m = (re.search(r"""property=["']og:site_name["'][^>]*content=["']([^"']+)""", text)
         or re.search(r"""content=["']([^"']+)["'][^>]*property=["']og:site_name""", text))
    name = m.group(1) if m else None
    if not name:
        t = re.search(r"<title[^>]*>([^<]+)", text)
        name = _TAIL.sub("", t.group(1)).strip() if t else None
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name or len(name) > 20 or not re.search(r"[가-힣]", name):      # 한글 이름이 아니면 쓰지 않음
        return None
    return ALIASES.get(name, name)


def normalize(code: str, limit: int = 15) -> int:
    """모은 기사의 언론사 이름을 맞춤. 주소로만 남은 매체는 한 번에 limit곳까지 이름을 찾아 둠. 바꾼 기사 수."""
    known = _known()
    with connect() as conn:
        rows = conn.execute("SELECT id, office FROM articles WHERE symbol=?", (code,)).fetchall()
    tried = set(known.get("_failed", []))
    todo = sorted({_host(o) for _, o in rows if o and _looks_like_host(o.replace(" (외신)", ""))
                   and not _by_host(_host(o), known) and _host(o) not in tried})
    for h in todo[:limit]:
        name = _fetch_name(h)
        if name:
            known[h] = name
        else:
            tried.add(h)
    if todo:
        known["_failed"] = sorted(tried)
        set_state(_KEY, json.dumps(known, ensure_ascii=False))
    changes = [(canonical(o, known), i) for i, o in rows if o and canonical(o, known) != o]
    if changes:
        with connect() as conn:
            conn.executemany("UPDATE articles SET office=? WHERE id=?", changes)
    return len(changes)
