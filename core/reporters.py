"""기자명: 기사 원문 페이지를 한 번 열어 기자 이름을 읽어 둠 (기사마다 한 번만, 실패해도 다시 묻지 않음).

네이버 뉴스는 위쪽 기자 칸을 나중에 스크립트로 채워서, 본문 글귀에서 찾습니다:
[이데일리 안유리 기자] · (서울=연합뉴스) 홍길동 기자 = · 홍길동 기자 abc@… · 홍길동 기자가 보도합니다 · SBS Biz 윤지혜입니다
구글 뉴스 연결 주소(news.google.com)는 먼저 원래 기사 주소로 바꿔(gnews.resolve) 저장한 뒤 그 원문에서 읽습니다.
"""
from __future__ import annotations

import html as _html
import re
import time as _time
from datetime import datetime, timedelta, timezone

from .db import connect

NAME = r"([가-힣]{2,4})"
TITLE = r"(?:기자|특파원|선임기자|객원기자)"
PATTERNS = [
    re.compile(r'media_end_head_journalist_name[^>]*>\s*' + NAME),
    re.compile(r'byline_s[^>]*>\s*' + NAME),
    re.compile(r"\[[^\[\]=]{0,24}?\s" + NAME + r"\s?" + TITLE + r"\]"),
    re.compile(r"\([^()]{1,20}=[^()]{1,20}\)\s*" + NAME + r"\s?" + TITLE + r"\s*="),
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
BATCH = 15             # 실행 한 번에 여는 기사 수 (새 기사 먼저, 남으면 지난 기사)


def parse(page: str) -> str | None:
    """원문 HTML(또는 글)에서 기자 이름 하나. 못 찾으면 None."""
    text = re.sub(r"<[^>]+>", " ", page)
    text = re.sub(r"\s+", " ", _html.unescape(text))
    for i, pat in enumerate(PATTERNS):
        for m in pat.finditer(page if i < 2 else text):
            name = m.group(1)
            if name not in STOP:
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


def fetchable(url: str) -> bool:
    return bool(url) and url.startswith("http") and "news.google.com" not in url


def fill(symbol: str, limit: int = BATCH, fetch=None, resolve=None, log=None) -> int:
    """아직 기자명을 확인하지 않은 기사를 limit개까지 열어 기자명을 채움. 찾은 개수.

    구글 뉴스 연결 주소는 원래 주소로 바꿔 url에 저장 (바꿔 본 기사는 resolved_at, 예전에 건너뛴 기사도 한 번 더 봄)."""
    from . import gnews
    if fetch is None:
        from .naver import http_get
        fetch = lambda u: http_get(u, timeout=8, retries=0).decode("utf-8", "ignore")
    resolve = resolve or gnews.resolve
    now = datetime.now().isoformat(timespec="minutes")
    with connect() as conn:
        rows = conn.execute("SELECT id, url, ts, source FROM articles WHERE symbol=? AND (reporter_at IS NULL OR "
                            "(url LIKE '%news.google.com/%' AND resolved_at IS NULL)) ORDER BY ts DESC",
                            (symbol,)).fetchall()
    found, tried = 0, 0
    for aid, url, ts, source in rows:
        if tried >= limit:
            break
        name = None
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
            try:
                page = fetch(url)
                name = parse(page)
                if source == "google":      # 구글 뉴스 시각은 날짜만 맞고 16:00·17:00으로 나오기도 함 → 원문 게시 시각
                    t = parse_time(page)
                    if t and ts and abs((datetime.fromisoformat(t) - datetime.fromisoformat(ts[:16])).days) <= 2:
                        with connect() as conn:
                            conn.execute("UPDATE articles SET ts=?, time_from='page' WHERE id=?", (t, aid))
            except Exception as e:      # noqa: BLE001 - 한 기사가 안 열려도 계속
                if log:
                    log(f"기자명 읽기 실패: {e}")
            _time.sleep(0.3)
        with connect() as conn:
            conn.execute("UPDATE articles SET reporter=?, reporter_at=? WHERE id=?", (name, now, aid))
        found += bool(name)
    return found
