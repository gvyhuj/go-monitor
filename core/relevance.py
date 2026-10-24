"""모으지 않는 기사: 막은 매체, 회사 이름만 스쳐 지나가는 기사.

- 막은 매체(BLOCKED_OFFICES)는 받지도 않고, 이미 있던 기사도 지움.
- 이름만 언급: 제목에 회사 이름이 없고, 기사 첫머리(네이버 요약·원문 meta description)에도 없고,
  원문 전체에 이름이 2번 이하로만 나오는 기사 (방산주 시황 기사 끝에 종목 나열로만 나오는 경우 등).
  원문을 못 열면 판단하지 않고 둠.
- 지운 기사는 dropped_articles에 남겨 다음 수집 때 다시 들어오지 않게 함.
"""
from __future__ import annotations

import html as _html
import re
import time as _time
from datetime import datetime

from .db import connect

BLOCKED_OFFICES = {"핀포인트뉴스": "pinpointnews.co.kr",       # 매체 이름: 주소
                   "Investing.com 한국어": "kr.investing.com"}    # 다른 매체 기사를 다시 싣기만 함 (원문도 접속 차단)
# 원문을 열 수 없어(접속 차단) 자동 판단이 안 되는 기사 중 사용자가 지우라고 한 것 (제목 일부)
DROP_TITLES = ("스페이스X(SPCX) 보호예수 해제 충격파",)
MAX_NAME_COUNT = 2         # 원문 전체에 이름이 이만큼 이하이면 '이름만 언급'
LEDE = 90                  # 네이버 요약에서 첫머리로 보는 글자 수
BATCH = 20                 # 실행 한 번에 여는 기사 수


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def blocked(office: str | None, url: str | None) -> bool:
    o, u = _norm(office), (url or "").lower()
    return any(_norm(name) in o or host in u for name, host in BLOCKED_OFFICES.items())


def dropped_ids(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM dropped_articles")}


def drop(conn, row: dict, reason: str) -> None:
    conn.execute("INSERT OR REPLACE INTO dropped_articles(id, symbol, url, office, title, reason, dropped_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (row["id"], row.get("symbol"), row.get("url"), row.get("office"), row.get("title"), reason,
                  datetime.now().isoformat(timespec="minutes")))
    conn.execute("DELETE FROM articles WHERE id=?", (row["id"],))


def purge_blocked(symbol: str) -> int:
    """막은 매체 기사와 지우라고 한 제목의 기사를 지움. 지운 개수."""
    n = 0
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, symbol, url, office, title FROM articles WHERE symbol=?", (symbol,))]
        for r in rows:
            if blocked(r["office"], r["url"]) or any(t in (r["title"] or "") for t in DROP_TITLES):
                drop(conn, r, "blocked")
                n += 1
    return n


def name_in_lede(title: str, body: str, name: str) -> bool:
    return name in (title or "") or name in (body or "")[:LEDE]


def name_only(title: str, page: str, name: str) -> bool:
    """원문 HTML 기준: 제목·첫머리(meta description)에 이름이 없고 본문 전체에 2번 이하."""
    if name in (title or ""):
        return False
    for m in re.finditer(r'<meta[^>]+(?:name|property)="(?:description|og:description)"[^>]*content="([^"]*)"', page):
        if name in _html.unescape(m.group(1)):
            return False
    text = _html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)))
    return text.count(name) <= MAX_NAME_COUNT


def check(symbol: str, name: str, limit: int = BATCH, fetch=None, log=None) -> int:
    """아직 살펴보지 않은 기사 중 제목·첫머리에 이름이 없는 것을 열어 '이름만 언급'이면 지움. 지운 개수."""
    from .reporters import fetchable
    if fetch is None:
        from .naver import http_get
        fetch = lambda u: http_get(u, timeout=8, retries=0).decode("utf-8", "ignore")
    now = datetime.now().isoformat(timespec="minutes")
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, symbol, url, office, title, body FROM articles WHERE symbol=? AND mention_checked_at IS NULL "
            "ORDER BY ts DESC", (symbol,))]
    n, opened = 0, 0
    for r in rows:
        if name_in_lede(r["title"], r["body"], name):
            verdict = False
        elif not fetchable(r["url"]):
            if "news.google.com" in (r["url"] or ""):
                continue            # 원래 주소로 바뀐 뒤에 봄
            verdict = False
        else:
            if opened >= limit:
                continue
            opened += 1
            try:
                verdict = name_only(r["title"], fetch(r["url"]), name)
            except Exception as e:      # noqa: BLE001 - 못 열면 그대로 둠
                if log:
                    log(f"기사 원문 열기 실패: {e}")
                verdict = False
            _time.sleep(0.3)
        with connect() as conn:
            if verdict:
                drop(conn, r, "name_only")
                n += 1
            else:
                conn.execute("UPDATE articles SET mention_checked_at=? WHERE id=?", (now, r["id"]))
    return n
