"""기자명: 기사 원문 페이지를 열어 기자 이름을 읽어 둠 (찾으면 끝, 못 열거나 이름이 없으면 다음 실행에 다시, MAX_TRIES번까지).

네이버 뉴스는 위쪽 기자 칸을 나중에 스크립트로 채워서, 본문 글귀에서 찾습니다:
[이데일리 안유리 기자] · (서울=연합뉴스) 홍길동 기자 = · [서울=뉴시스] 홍길동 기자 = · 홍길동 기자 abc@… · 홍길동 기자가 보도합니다 · SBS Biz 윤지혜입니다
구글 뉴스 연결 주소(news.google.com)는 먼저 원래 기사 주소로 바꿔(gnews.resolve) 저장한 뒤 그 원문에서 읽습니다.
"""
from __future__ import annotations

import html as _html
import os
import re
import urllib.parse
import time as _time
from datetime import datetime, timedelta, timezone

from .db import connect

NAME = r"([가-힣]{2,4})"
TITLE = r"(?:기자|특파원|선임기자|객원기자)"
PATTERNS = [
    # 언론사 페이지 머리의 작성자 정보: <meta name="author" content="박기영 기자"> (dable:author, article:author도)
    re.compile(r'<meta[^>]+(?:name|property)="(?:author|article:author|dable:author)"[^>]*content="\s*' + NAME + r'(?:\s?' + TITLE + r')?\s*"'),
    re.compile(r'media_end_head_journalist_name[^>]*>\s*' + NAME),
    re.compile(r'byline_s[^>]*>\s*' + NAME),
    re.compile(r"\[[^\[\]=]{0,24}?\s" + NAME + r"\s?" + TITLE + r"\]"),
    re.compile(r"\([^()]{1,20}=[^()]{1,20}\)\s*" + NAME + r"\s?" + TITLE + r"\s*="),
    re.compile(r"\[[^\[\]]{1,20}=[^\[\]]{1,20}\]\s*" + NAME + r"\s?" + TITLE + r"\s*="),   # [서울=뉴시스] 김경택 기자 =
    re.compile(NAME + r"\s?" + TITLE + r"\s*\(?[\w.+-]+@[\w-]+\.[\w.]+"),
    re.compile(NAME + r"\s?" + TITLE + r"(?:가|이) 보도합니다"),
    re.compile(r"(?:Biz|뉴스|TV|YTN|KBS|MBC|SBS|MBN|JTBC|채널A|연합뉴스TV)\s+" + NAME + r"입니다"),
]
STOP = {"이번", "관련", "해당", "담당", "전문", "증권", "취재", "사진", "영상", "편집", "그래픽", "기자", "본지", "온라인", "디지털"}
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
    for i, pat in enumerate(PATTERNS):
        for m in pat.finditer(page if i < 3 else text):
            name = m.group(1)
            if name not in STOP and not (off and (name in off or off in name)):
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
