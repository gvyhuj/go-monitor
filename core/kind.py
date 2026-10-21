"""거래소 공시 사이트(KIND)의 공시 접수 시각.

네이버 공시 목록에는 날짜만 있고 시각이 없어서, 기사보다 공시가 먼저 나왔는지 알 수 없음.
KIND 상세 검색 화면의 목록(공개 화면)에서 '2026-08-06 11:11'처럼 접수 시각을 읽어 disclosures.ts를 채움.
https://kind.krx.co.kr/disclosure/details.do (method=searchDetailsSub)
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from datetime import date

from .db import connect

URL = "https://kind.krx.co.kr/disclosure/details.do"


def fetch(code: str, since: date, until: date, size: int = 100) -> list[dict]:
    form = {"method": "searchDetailsSub", "currentPageSize": str(size), "pageIndex": "1", "orderMode": "1",
            "orderStat": "D", "forward": "details_sub", "repIsuSrtCd": f"A{code}",
            "fromDate": since.isoformat(), "toDate": until.isoformat()}
    req = urllib.request.Request(URL, data=urllib.parse.urlencode(form).encode(), headers={
        "User-Agent": "Mozilla/5.0", "Referer": f"{URL}?method=searchDetailsMain"})
    with urllib.request.urlopen(req, timeout=15) as r:
        text = r.read().decode("utf-8", errors="replace")
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 4 and re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d$", cells[1]):
            out.append({"ts": cells[1].replace(" ", "T"), "title": cells[3]})
    return out


def _norm(t: str) -> str:
    return re.sub(r"\(주\)|㈜|[^0-9A-Za-z가-힣]", "", t or "")


def fill_times(code: str, since: date, until: date) -> int:
    """네이버로 받은 공시(날짜만)에 KIND 접수 시각을 붙임. 같은 날짜·제목(회사 이름 앞부분 제외)으로 맞춤."""
    rows = fetch(code, since, until)
    n = 0
    with connect() as conn:
        mine = [dict(r) for r in conn.execute(
            "SELECT no, date, title FROM disclosures WHERE symbol=? AND date BETWEEN ? AND ?",
            (code, since.isoformat(), until.isoformat()))]
        for k in rows:
            kt = _norm(k["title"])
            hit = next((m for m in mine if m["date"] == k["ts"][:10] and kt and kt in _norm(m["title"])), None)
            if hit:
                n += conn.execute("UPDATE disclosures SET ts=? WHERE no=? AND (ts IS NULL OR ts != ?)",
                                  (k["ts"], hit["no"], k["ts"])).rowcount
    return n
