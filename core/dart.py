"""금융감독원 전자공시(DART) 오픈API로 이 종목의 공시 목록을 받음.

https://opendart.fss.or.kr  (비밀값 DART_API_KEY 필요, 무료)
  GET /api/corpCode.xml        회사 고유번호 전체 (zip) → 종목코드로 이 회사 고유번호를 찾음
  GET /api/list.json           공시 목록 (회사 고유번호를 주면 기간 제한 없음, 한 번에 100건)
DART 목록에는 접수 날짜만 있고 시각이 없어서, 시각은 거래소 공시 사이트(KIND)에서 채움 (kind.fill_times).
공시 종류(정기·주요사항·발행·지분·기타·외부감사·거래소…)는 목록에 없어서 종류별로 나눠 물어봄.
"""
from __future__ import annotations

import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import timedelta

from .db import connect, get_state, set_state

API = "https://opendart.fss.or.kr/api"
TYPES = {"A": "정기공시", "B": "주요사항보고", "C": "발행공시", "D": "지분공시", "E": "기타공시",
         "F": "외부감사관련", "G": "펀드공시", "H": "자산유동화", "I": "거래소공시", "J": "공정위공시"}


class DartError(RuntimeError):
    pass


def key() -> str:
    return (os.environ.get("DART_API_KEY") or "").strip()


def enabled() -> bool:
    return bool(key())


def viewer_url(rcept_no: str) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def _get(path: str, **params) -> bytes:
    url = f"{API}/{path}?" + urllib.parse.urlencode({"crtfc_key": key(), **params})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
        return r.read()


def corp_code(stock_code: str) -> str:
    """종목코드 → DART 회사 고유번호 (한 번 찾으면 저장해 둠)."""
    st = get_state(f"dart_corp:{stock_code}")
    if st and st["value"]:
        return st["value"]
    z = zipfile.ZipFile(io.BytesIO(_get("corpCode.xml")))
    text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
    m = re.search(r"<corp_code>(\d+)</corp_code>(?:(?!</list>).)*?<stock_code>" + re.escape(stock_code) + r"</stock_code>",
                  text, re.S)
    if not m:
        raise DartError(f"DART에서 종목코드 {stock_code}의 회사를 찾지 못했습니다.")
    set_state(f"dart_corp:{stock_code}", m.group(1))
    return m.group(1)


def fetch_list(corp: str, since: str, until: str, ty: str | None = None) -> list[dict]:
    """since/until: YYYYMMDD. 공시가 없으면 빈 목록."""
    out, page = [], 1
    while True:
        params = {"corp_code": corp, "bgn_de": since, "end_de": until, "page_no": page, "page_count": 100}
        if ty:
            params["pblntf_ty"] = ty
        d = json.loads(_get("list.json", **params).decode("utf-8"))
        if d.get("status") == "013":            # 조회된 데이터가 없음
            return out
        if d.get("status") != "000":
            raise DartError(f"DART 오류 {d.get('status')}: {d.get('message')}")
        for r in d.get("list") or []:
            dt = r["rcept_dt"]
            out.append({"no": r["rcept_no"], "date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}",
                        "title": re.sub(r"\s+", " ", r.get("report_nm") or "").strip(),
                        "filer": (r.get("flr_nm") or "").strip(), "rm": (r.get("rm") or "").strip(), "type": ty})
        if page >= int(d.get("total_page") or 1):
            return out
        page += 1


def sync(stock_code: str) -> tuple[int, bool]:
    """새 공시를 저장. 처음 한 번은 DART에 있는 전체 기록을, 그 뒤로는 최근 2주를 받음.
    반환: (새로 저장한 건수, 전체 기록을 받았는지)."""
    from .feed import kst_now
    corp = corp_code(stock_code)
    full = not get_state(f"dart_backfill:{stock_code}")
    today = kst_now().date()
    until = today.strftime("%Y%m%d")
    since = "19990101" if full else (today - timedelta(days=14)).strftime("%Y%m%d")
    with connect() as conn:
        known = {r[0] for r in conn.execute("SELECT no FROM disclosures WHERE symbol=? AND pblntf IS NOT NULL",
                                            (stock_code,))}
    if not full and all(r["no"] in known for r in fetch_list(corp, since, until)):
        return 0, False                         # 평소: 한 번만 물어보고 새 공시가 없으면 끝
    rows = [r for ty in TYPES for r in fetch_list(corp, since, until, ty)]
    now = kst_now().isoformat(timespec="minutes")
    with connect() as conn:
        if full:                                # 예전 네이버 공시 목록(번호 형식이 다름)은 DART 목록으로 바꿈
            conn.execute("DELETE FROM disclosures WHERE symbol=? AND pblntf IS NULL", (stock_code,))
        conn.executemany(
            "INSERT INTO disclosures(no, symbol, date, title, kind, summary, first_seen, pblntf, filer, rm) "
            "VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?) ON CONFLICT(no) DO UPDATE SET title=excluded.title, "
            "kind=excluded.kind, pblntf=excluded.pblntf, filer=excluded.filer, rm=excluded.rm",
            [(r["no"], stock_code, r["date"], r["title"], TYPES[r["type"]], now, r["type"], r["filer"], r["rm"])
             for r in rows])
    added = len({r["no"] for r in rows} - known)
    if full:
        set_state(f"dart_backfill:{stock_code}", now)
    return added, full
