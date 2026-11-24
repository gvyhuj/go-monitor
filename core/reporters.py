"""기자명: 기사 원문 페이지를 열어 기자 이름을 읽어 둠 (찾으면 끝, 못 열거나 이름이 없으면 다음 실행에 다시, MAX_TRIES번까지).

네이버 뉴스는 위쪽 기자 칸을 나중에 스크립트로 채워서, 본문 글귀에서 찾습니다:
[이데일리 안유리 기자] · (서울=연합뉴스) 홍길동 기자 = · [서울=뉴시스] 홍길동 기자 = · 홍길동 기자 abc@… · 홍길동 기자가 보도합니다 · SBS Biz 윤지혜입니다
구글 뉴스 연결 주소(news.google.com)는 먼저 원래 기사 주소로 바꿔(gnews.resolve) 저장한 뒤 그 원문에서 읽습니다.
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import urllib.parse
import time as _time
from datetime import datetime, timedelta, timezone

from .db import connect

NAME = r"([가-힣]{2,4})"
TITLE = r"(?:기자|특파원|선임기자|객원기자|대기자|전문기자|수습기자)"
PATTERNS = [
    # 언론사 페이지 머리의 작성자 정보: <meta name="author" content="박기영 기자"> (dable:author, article:author도)
    re.compile(r'<meta[^>]+(?:name|property)="(?:author|article:author|og:article:author|dable:author)"[^>]*content="\s*' + NAME + r'(?:\s?' + TITLE + r')?\s*"'),
    re.compile(r'media_end_head_journalist_name[^>]*>\s*' + NAME),
    re.compile(r'byline_s[^>]*>\s*' + NAME),
    re.compile(r"\[[^\[\]=]{0,24}?\s" + NAME + r"\s?" + TITLE + r"\]"),
    re.compile(r"\([^()]{1,20}=[^()]{1,20}\)\s*" + NAME + r"\s?" + TITLE + r"\s*="),
    re.compile(r"\[[^\[\]]{1,20}=[^\[\]]{1,20}\]\s*" + NAME + r"\s?" + TITLE + r"\s*="),   # [서울=뉴시스] 김경택 기자 =
    re.compile(r"\[[^\[\]=]{1,20}=\s*" + NAME + r"\s?" + TITLE + r"\]"),                       # [버핏연구소=홍길동 기자]
    re.compile(NAME + r"\s?" + TITLE + r"\s*\(?[\w.+-]+@[\w-]+\.[\w.]+"),
    re.compile(NAME + r"\s?" + TITLE + r"(?:가|이) 보도합니다"),
    re.compile(r"(?:Biz|뉴스|TV|YTN|KBS|MBC|SBS|MBN|JTBC|채널A|연합뉴스TV)\s+" + NAME + r"입니다"),
]
STOP = {"이번", "관련", "해당", "담당", "전문", "증권", "취재", "사진", "영상", "편집", "그래픽", "기자", "본지", "온라인", "디지털",
        "편집국", "편집부", "리포터", "뉴스팀", "취재팀", "관리자", "운영자", "뉴스룸", "데스크"}
# 작성자 칸 원문 그대로 (앞에 부서명이 붙거나 성·이름이 띄어 있어도 읽음 → _person으로 이름만)
#   이투데이: "author":{"@type":"Person","name":"대구경북취재본부 이종팔"} · <meta property="dd:author" content="…">
#   벤처스퀘어(워드프레스): "author":{"@type":"Person","name":"한 혜선"} (= "한 혜선")
AUTHOR_RAW = [
    re.compile(r'"author"\s*:\s*\[?\s*\{[^{}]*?"name"\s*:\s*"([^"]{1,60})"'),
    re.compile(r'<meta[^>]+(?:name|property)="dd:author"[^>]*content="([^"]{1,60})"'),
    re.compile(r'class="reporter_name"[^>]*>(?:\s*<[^>]+>)*\s*([^<]{1,60})<'),
    re.compile(r'class="show-for-sr">기자명</span>\s*(?:</i>\s*)?([^<]{1,60})<'),    # 와이드경제·디지털투데이 같은 모양
]


def _person(raw: str) -> str | None:
    """작성자 칸 글에서 사람 이름(2~4글자)만. '대구경북취재본부 이종팔 기자' → 이종팔, '한 혜선' → 한혜선."""
    if "\\u" in raw:
        try:
            raw = json.loads(f'"{raw}"')
        except ValueError:
            pass
    raw = re.sub(r"\s*" + TITLE + r"\s*$", "", _html.unescape(raw).strip())
    toks = re.findall(r"[가-힣]+", raw)
    if not toks:
        return None
    if len(toks) >= 2 and len(toks[-2]) == 1 and len(toks[-2] + toks[-1]) <= 4:      # 성과 이름을 띄어 씀
        return toks[-2] + toks[-1]
    return toks[-1] if 2 <= len(toks[-1]) <= 4 else None
TIME_PATTERNS = [
    re.compile(r'property="article:published_time"\s+content="([^"]+)"'),
    re.compile(r'content="([^"]+)"\s+property="article:published_time"'),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"'),
    re.compile(r'(?:입력|등록|승인)\s*:?\s*(20\d\d[.-]\d\d[.-]\d\d\s+\d\d:\d\d)'),
]
MAX_TRIES = 10         # 기사 페이지를 못 열면 다음 실행에 다시 (10번까지)
BATCH = 15             # 실행 한 번에 여는 기사 수 (새 기사 먼저, 남으면 지난 기사)


def parse(page: str, office: str = "") -> str | None:
    """원문 HTML(또는 글)에서 기자 이름 하나. 못 찾으면 None.

    언론사 이름은 건너뜀: 뉴시스 원문은 작성자 정보(meta author)에 기자 대신 '뉴시스'를 적어 둠."""
    text = re.sub(r"<[^>]+>", " ", page)
    text = re.sub(r"\s+", " ", _html.unescape(text))
    off = re.sub(r"\s+", "", office or "")
    ok = lambda name: name and name not in STOP and not (off and (name in off or off in name))
    for i, pat in enumerate(PATTERNS):
        if i == 3:      # 페이지 머리 작성자 정보에서 못 찾았으면 본문 글귀보다 먼저 구조화된 작성자 칸
            for raw_pat in AUTHOR_RAW:
                for m in raw_pat.finditer(page):
                    name = _person(m.group(1))
                    if ok(name):
                        return name
        for m in pat.finditer(page if i < 3 else text):
            name = m.group(1)
            if ok(name):
                return name
    return None


def parse_time(page: str) -> str | None:
    """원문 페이지의 게시 시각 (한국 시각 'YYYY-MM-DDTHH:MM'). 못 찾으면 None."""
    for pat in TIME_PATTERNS:
        m = pat.search(page)
        if not m:
            continue
        raw = re.sub(r"(\d{4})[.](\d\d)[.](\d\d)", r"\1-\2-\3", m.group(1).strip()).replace(" ", "T")
        try:
            t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo:
            t = t.astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)
        return t.isoformat(timespec="minutes")
    return None


def _host(u: str) -> str:
    return re.sub(r"^(www|m|mobile|nwww)\.", "", urllib.parse.urlsplit(u or "").netloc.lower())


def naver_link(title: str, url: str) -> str | None:
    """원문 주소만 있는 기사의 네이버 뉴스판 주소 (네이버 검색 API로 같은 언론사·같은 제목을 찾음). 없으면 None.

    네이버 뉴스판은 언론사가 달라도 기자 칸 형식이 같아서 원문보다 확실히 읽힘."""
    if not os.environ.get("NAVER_CLIENT_ID") or not title:
        return None
    from . import newssearch
    key = lambda t: re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")[:30]
    for it in newssearch.search(title, display=20):
        if "news.naver.com" in it["url"] and key(it["title"]) == key(title) and _host(it.get("orig")) == _host(url):
            return it["url"]
    return None


def fetchable(url: str) -> bool:
    return bool(url) and url.startswith("http") and "news.google.com" not in url


def fill(symbol: str, limit: int = BATCH, fetch=None, resolve=None, log=None, naver=None) -> int:
    """아직 기자명을 확인하지 않은 기사를 limit개까지 열어 기자명을 채움. 찾은 개수.

    구글 뉴스 연결 주소는 원래 주소로 바꿔 url에 저장 (바꿔 본 기사는 resolved_at, 예전에 건너뛴 기사도 한 번 더 봄)."""
    from . import gnews
    if fetch is None:
        from .naver import http_get
        fetch = lambda u: http_get(u, timeout=10, retries=1).decode("utf-8", "ignore")   # 한 번 못 열면 잠깐 뒤 다시
    resolve = resolve or gnews.resolve
    naver = naver or naver_link
    now = datetime.now().isoformat(timespec="minutes")
    with connect() as conn:
        rows = conn.execute("SELECT id, url, ts, source, office, title FROM articles WHERE symbol=? AND ((reporter_at IS NULL "
                            "AND COALESCE(reporter_tries, 0) < ?) OR (url LIKE '%news.google.com/%' AND resolved_at IS NULL)) "
                            "ORDER BY COALESCE(reporter_tries, 0), ts DESC", (symbol, MAX_TRIES)).fetchall()
    found, tried = 0, 0
    for aid, url, ts, source, office, title in rows:
        if tried >= limit:
            break
        name, failed = None, False
        if gnews.is_google(url):
            tried += 1
            try:
                orig = resolve(url)
            except Exception as e:      # noqa: BLE001 - 한 기사가 안 풀려도 계속
                orig = None
                if log:
                    log(f"구글 뉴스 원래 주소 찾기 실패: {e}")
            with connect() as conn:
                conn.execute("UPDATE articles SET url=COALESCE(?, url), resolved_at=? WHERE id=?", (orig, now, aid))
            url = orig or url
            _time.sleep(0.3)
        if fetchable(url):
            tried += 1
            if "news.naver.com" not in url:      # 네이버 뉴스판이 있으면 그쪽을 먼저 (기자 칸 형식이 하나로 같음)
                try:
                    nv = naver(title, url)
                    if nv:
                        name = parse(fetch(nv), office or "")
                except Exception as e:      # noqa: BLE001 - 네이버판을 못 찾거나 못 열면 원문으로
                    if log:
                        log(f"네이버 뉴스판 찾기 실패: {e}")
            try:
                if name and source != "google":
                    raise StopIteration          # 이미 읽음 (구글 기사는 원문 게시 시각 때문에 원문도 엶)
                page = fetch(url)
                name = name or parse(page, office or "")
                if not name and "news.naver.com" in url:      # 네이버판에서 못 읽으면 '기사원문' 링크의 언론사 페이지에서 한 번 더
                    m = re.search(r'href="([^"]+)"[^>]*class="media_end_head_origin_link"', page)
                    orig = _html.unescape(m.group(1)) if m else None
                    if orig and fetchable(orig):
                        name = parse(fetch(orig), office or "")
                if source == "google":      # 구글 뉴스 시각은 날짜만 맞고 16:00·17:00으로 나오기도 함 → 원문 게시 시각
                    t = parse_time(page)
                    if t and ts and abs((datetime.fromisoformat(t) - datetime.fromisoformat(ts[:16])).days) <= 2:
                        with connect() as conn:
                            conn.execute("UPDATE articles SET ts=?, time_from='page' WHERE id=? "
                                         "AND COALESCE(time_from, '') != 'manual'", (t, aid))     # 직접 고친 시각은 그대로
            except StopIteration:
                pass
            except Exception as e:      # noqa: BLE001 - 한 기사가 안 열려도 계속
                failed = not name           # 네이버판에서 이미 읽었으면 원문을 못 열어도 됨
                if log:
                    log(f"기자명 읽기 실패: {e}")
            _time.sleep(0.3)
        with connect() as conn:
            if failed or (not name and fetchable(url)):
                # 못 열었거나, 열었는데 이름이 없으면(네이버가 잠깐 막아 빈 화면을 준 경우 등) '확인함'으로 두지 않고 다음 실행에 다시
                conn.execute("UPDATE articles SET reporter_tries=COALESCE(reporter_tries, 0)+1 WHERE id=?", (aid,))
            else:
                conn.execute("UPDATE articles SET reporter=?, reporter_at=? WHERE id=?", (name, now, aid))
        found += bool(name)
    return found


KEEP_WORDS = ("기자", "특파원", "입력", "수정", "등록", "승인",
              "author", "name", "writer", "byline", "reporter", "journalist", "Person", "@type")
KEEP_ATTRS = {"class", "id", "name", "property", "itemprop", "rel", "type"}


def _mask_text(t: str) -> str:
    parts = re.split("(" + "|".join(KEEP_WORDS) + ")", t)
    return "".join(p if p in KEEP_WORDS else re.sub(r"\d", "0", re.sub(r"[A-Za-z]", "a", re.sub(r"[가-힣]", "가", p)))
                   for p in parts)


def _mask_tag(tag: str) -> str:
    def attr(m):
        k, v = m.group(1), m.group(2)
        if k.lower() in KEEP_ATTRS:
            return f' {k}="{v}"'
        return f' {k}="{_mask_text(v)}"' if k.lower() == "content" else f' {k}="…"'
    return re.sub(r'\s([\w:-]+)="([^"]*)"', attr, tag)


def mask(html: str) -> str:
    """페이지 모양만 남김 (공개 실행 기록용): 글자는 가(한글)·a(영문)·0(숫자)로, 주소 같은 속성 값은 …로.
    태그·class·id와 '기자' 같은 표시 단어만 그대로."""
    return "".join(_mask_tag(p) if p.startswith("<") and p.endswith(">") else _mask_text(p)      # 잘린 태그 조각은 글처럼 가림
                   for p in re.split(r"(<[^>]*>)", html))


def _shape(page: str) -> list:
    """기자명이 있을 만한 자리 근처 모양 (가린 것) 최대 3곳: 처음·마지막 '기자' 글자, 작성자 칸처럼 생긴 곳(author·writer 등)."""
    hits = [m.start() for m in re.finditer(TITLE, page)]
    auth = re.search(r"(?i)writer|author|byline|reporter|journalist", page)
    picks = []
    for i in ([hits[0], hits[-1]] if hits else []) + ([auth.start()] if auth else []):
        if all(abs(i - j) > 250 for j in picks):
            picks.append(i)
    return [re.sub(r"\s+", " ", mask(page[max(0, i - 150):i + 100])) for i in picks]


def diagnose(symbol: str, limit: int = 20, fetch=None, samples: dict | None = None) -> dict:
    """다 읽어 보고도(MAX_TRIES번) 기자명이 빈 기사를 한 번씩 다시 열어 매체별 까닭을 셈. {매체: {까닭: 건수}}.
    samples를 주면 '기자 칸 모양 모름'인 매체마다 한 건의 가린 페이지 모양을 담음.

    실행 기록이 공개라서 주소·제목은 돌려주지 않음. 다시 열어 이름이 읽히면 그 이름은 저장함."""
    if fetch is None:
        from .naver import http_get
        fetch = lambda u: http_get(u, timeout=10, retries=1).decode("utf-8", "ignore")
    with connect() as conn:
        rows = conn.execute("SELECT id, url, office FROM articles WHERE symbol=? AND reporter IS NULL "
                            "AND COALESCE(reporter_none, 0) = 0 "
                            "AND COALESCE(reporter_tries, 0) >= ? ORDER BY ts DESC LIMIT ?",
                            (symbol, MAX_TRIES, limit)).fetchall()
    out: dict = {}
    for aid, url, office in rows:
        if not fetchable(url):
            why = "구글 주소를 못 풂" if "news.google.com" in (url or "") else "주소 없음"
        else:
            try:
                page = fetch(url)
            except Exception as e:      # noqa: BLE001 - 오류 글에 주소가 들어 있어 종류만 남김
                m = re.search(r"HTTP Error \d+|timed out", str(e))
                why = f"못 엶({m.group(0) if m else type(e).__name__})"
            else:
                text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)).strip()
                if len(text) < 300:
                    why = "빈 화면"
                elif name := parse(page, office or ""):      # 그때는 못 열었지만 지금은 읽힘 → 저장
                    with connect() as conn:
                        conn.execute("UPDATE articles SET reporter=?, reporter_at=? WHERE id=?",
                                     (name, datetime.now().isoformat(timespec="minutes"), aid))
                    why = "지금은 읽힘(저장함)"
                else:
                    why = "기자 칸 모양 모름(" + ("'기자' 글자 있음" if "기자" in text else "'기자' 글자 없음") + ")"
                    with connect() as conn:          # 열렸는데 이름이 없음 → '기자명 없음'으로 확정 (못 연 기사는 계속 다시 시도)
                        conn.execute("UPDATE articles SET reporter_none=1 WHERE id=?", (aid,))
                    if samples is not None and (office or "-") not in samples:
                        samples[office or "-"] = _shape(page) or ["단서 없음"]
            _time.sleep(0.3)
        box = out.setdefault(office or "-", {})
        box[why] = box.get(why, 0) + 1
    return out


def office_missing(symbol: str) -> tuple:
    """(전체 기사 수, 매체 이름이 빈 기사의 출처별 건수, 한글 이름 대신 주소로 남은 매체 {사이트 주소: 기사 수})."""
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM articles WHERE symbol=?", (symbol,)).fetchone()[0]
        rows = conn.execute("SELECT COALESCE(source, '-'), COUNT(*) FROM articles WHERE symbol=? "
                            "AND TRIM(COALESCE(office, '')) IN ('', '-') GROUP BY 1", (symbol,)).fetchall()
        offices_ = [r[0] for r in conn.execute("SELECT office FROM articles WHERE symbol=? AND office LIKE '%.%'", (symbol,))]
    from .offices import _host, _looks_like_host
    hosts: dict = {}
    for o in offices_:
        if _looks_like_host(o.replace(" (외신)", "")):
            h = _host(o.replace(" (외신)", ""))
            hosts[h] = hosts.get(h, 0) + 1
    return total, dict(rows), hosts


def no_address(text: str) -> str:
    """공개 실행 기록용: 글 속 인터넷 주소를 …로 (기사 주소로 종목이 드러나지 않게)."""
    return re.sub(r"(?:https?://|www\.)[^\s)]+", "…", text)
