"""구글 뉴스 연결 주소(news.google.com/rss/articles/…) → 원래 기사 주소.

연결 주소는 브라우저에서 스크립트로 원문에 넘어가서, 그대로 열면 원문이 안 나옵니다.
기사 페이지의 서명(data-n-a-sg)·시각(data-n-a-ts)을 읽어 구글 뉴스가 쓰는 batchexecute 요청으로 원문 주소를 받습니다.
공식 API가 아니라 구글이 바꾸면 실패할 수 있고, 실패하면 None (연결 주소를 그대로 씀).
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"}
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


def is_google(url: str) -> bool:
    return "news.google.com/" in (url or "")


def _article_id(url: str) -> str | None:
    m = re.search(r"news\.google\.com/(?:rss/)?articles/([^?/#]+)", url or "")
    return m.group(1) if m else None


def _get(url: str, data: bytes | None = None, timeout: float = 10.0) -> str:
    headers = dict(UA)
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def parse_batch(text: str) -> str | None:
    """batchexecute 응답에서 원문 주소."""
    for chunk in text.split("\n\n")[1:]:
        try:
            inner = json.loads(json.loads(chunk)[0][2])
        except (ValueError, TypeError, IndexError):
            continue
        if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], str) and inner[1].startswith("http"):
            return inner[1]
    return None


def resolve(url: str, get=_get) -> str | None:
    aid = _article_id(url)
    if not aid:
        return None
    page = get(f"https://news.google.com/articles/{aid}")
    sg = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sg and ts):
        return None
    req = [["Fbv4je", f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                      f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{aid}",{ts.group(1)},"{sg.group(1)}"]']]
    body = "f.req=" + urllib.parse.quote(json.dumps([req]))
    found = parse_batch(get(BATCH_URL, body.encode("utf-8")))
    return found if found and not is_google(found) else None
