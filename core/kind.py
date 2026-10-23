"""거래소 공시 사이트(KIND)의 공시 접수 시각.

DART·네이버 공시 목록에는 날짜만 있고 시각이 없어서, 기사보다 공시가 먼저 나왔는지 알 수 없음.
KIND 상세 검색 화면의 목록(공개 화면)에서 '2026-08-06 11:11'처럼 접수 시각을 읽어 disclosures.ts를 채움.
거래소가 낸 안내(투자주의·투자경고·신규상장 등, 제출인이 시장감시위원회·코스닥시장본부 등)는 DART 목록에 없어서
KIND에서 이 종목으로 검색된 것을 공시 목록에 '거래소 조치'(종류 K)로 더함.
https://kind.krx.co.kr/disclosure/details.do (method=searchDetailsSub)
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from datetime import date, timedelta

from .db import connect

URL = "https://kind.krx.co.kr/disclosure/details.do"
VIEWER = "https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={}"
EXCHANGE = ("시장감시위원회", "코스닥시장본부", "유가증권시장본부", "코넥스시장본부", "한국거래소")


def viewer_url(acptno: str) -> str:
    return VIEWER.format(acptno)


def fetch(code: str, since: date, until: date, size: int = 100) -> list[dict]:
    """KIND는 검색 기간이 길면(약 2년 넘게) 빈 결과를 줘서 1년씩 나눠 물어봄."""
    out, lo = [], since
    while lo <= until:
        hi = min(until, lo + timedelta(days=364))
        page = 1
        while True:
            rows = _fetch_page(code, lo, hi, size, page)
            out += rows
            if len(rows) < size:
                break
            page += 1
        lo = hi + timedelta(days=1)
    return out


def _fetch_page(code: str, since: date, until: date, size: int, page: int) -> list[dict]:
    form = {"method": "searchDetailsSub", "currentPageSize": str(size), "pageIndex": str(page), "orderMode": "1",
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
            no = re.search(r"openDisclsViewer\('(\d+)'", row)
            out.append({"ts": cells[1].replace(" ", "T"), "title": cells[3], "no": no.group(1) if no else "",
                        "filer": cells[4] if len(cells) > 4 else ""})
    return out


def _norm(t: str) -> str:
    # [정정]·[기재정정]·[첨부추가]·[연결포함] 같은 머리표, (일반법인) 같은 꼬리표는 두 사이트가 다르게 붙임
    t = re.sub(r"\[[^\]]*\]|\(일반법인\)|\(주\)|㈜", "", t or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t)


def fill_times(code: str, since: date, until: date, add_exchange: bool = False) -> int:
    """DART·네이버로 받은 공시(날짜만)에 KIND 접수 시각을 붙임. 같은 날짜에 제목이 한쪽에 다른 쪽이 들어 있으면 같은 공시."""
    rows = fetch(code, since, until)
    n = 0
    with connect() as conn:
        mine = [dict(r) for r in conn.execute(
            "SELECT no, date, title FROM disclosures WHERE symbol=? AND date BETWEEN ? AND ?",
            (code, since.isoformat(), until.isoformat()))]
        for k in rows:
            kt = _norm(k["title"])
            hit = next((m for m in mine if m["date"] == k["ts"][:10] and kt and _norm(m["title"])
                        and (kt in _norm(m["title"]) or _norm(m["title"]) in kt)), None)
            if hit:
                n += conn.execute("UPDATE disclosures SET ts=? WHERE no=? AND (ts IS NULL OR ts != ?)",
                                  (k["ts"], hit["no"], k["ts"])).rowcount
            elif add_exchange and k["no"] and k.get("filer") in EXCHANGE:     # 거래소 조치 (DART에 없음)
                n += conn.execute(
                    "INSERT OR IGNORE INTO disclosures(no, symbol, date, ts, title, kind, summary, first_seen, pblntf, filer) "
                    "VALUES (?, ?, ?, ?, ?, '거래소 조치', '', ?, 'K', ?)",
                    (f"K{k['no']}", code, k["ts"][:10], k["ts"], k["title"], k["ts"], k["filer"])).rowcount
    return n
