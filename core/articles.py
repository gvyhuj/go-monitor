"""이 종목이 다뤄진 기사 전체를 모으고, 기사마다 주가 반응을 계산.

회사가 낸 보도자료뿐 아니라 언론의 분석·비판 기사, 리서치, 테마·수혜주 기사도 포함합니다.
(예: '이 종목, 외형 커졌지만 재고 부담도' 는 회사가 낸 기사가 아니지만 주가에 영향을 줄 수 있음)

분류
  회사 발표    수주·계약·MOU·과제 선정·실적·전시회 참가 등 회사 소식
  언론 분석    언론사의 분석·기획·비판 기사
  리서치       증권사·독립리서치 보고서를 다룬 기사
  테마·수혜주  이 종목이 테마·수혜 종목으로 언급된 기사
  시황·특징주  주가가 움직인 뒤 그 움직임을 다룬 기사 (원인보다 '결과')
  단순 언급    본문에 이름만 나오거나 네이버가 연결만 한 기사

반응 계산
  분봉(최근 기록이 있을 때): 공개 후 5·15·30·60·120분, KOSDAQ·같은 업종 평균과 비교 → 초과 반응
  일봉(몇 달 전 기사도 가능): 반응일 당일·다음날·3거래일 누적, KOSDAQ·같은 업종 평균과 비교, 거래량 배수
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, time, timedelta

from . import naver   # analysis(pandas)는 필요할 때만 불러옴 → 설치 전 연결 점검에서도 동작
from .db import connect

KOSDAQ = "KOSDAQ"
NAME = "이 종목"
CATEGORIES = ["회사 발표", "언론 분석", "리서치", "테마·수혜주", "시황·특징주", "단순 언급"]

MARKET_STRONG = ("특징주", "상한가", "하한가", "핫종목", "급등", "급락", "신고가", "52주", "주가", "초고수", "상승률", "하락률",
                 "들썩", "껑충", "줄상승", "[종목NOW]", "서울데이터랩")
MARKET_WEAK = ("↑", "↓", "강세", "약세", "오름세", "내림세")
RESEARCH = ("리서치", "밸류파인더", "목표주가", "투자의견", "리포트", "보고서", "연구원", "커버리지", "기업분석")
COMPANY = ("수주", "계약", "공급", "MOU", "협약", "체결", "선정", "참여", "참가", "개발", "출시", "양산", "인증", "실적",
           "매출", "영업익", "영업이익", "흑자", "전시", "과제", "진입", "특허", "협력", "동맹", "손잡", "납품", "공시",
           "IR", "기업설명회", "유상증자", "무상증자", "자사주", "수상", "준공", "증설", "착공")
THEME = ("수혜", "테마", "관련주", "유망", "주목", "대장주", "밸류체인", "방산주", "우주주", "투심", "株", "기업들", "주末머니")
POS = ("수주", "계약", "공급", "협약", "MOU", "선정", "개발", "출시", "흑자", "최대", "증가", "확대", "양산", "수출", "승인",
       "인증", "특허", "호실적", "상향", "수혜", "강세", "급등", "신고가", "성장", "진입", "동맹", "효자", "정조준", "참여", "협력",
       "돌파", "국산화", "1028%")
NEG = ("유상증자", "적자", "손실", "소송", "해지", "취소", "블록딜", "오버행", "보호예수", "매각", "감소", "하향", "부진", "약세",
       "급락", "전환사채", "CB", "BW", "감사의견", "거래정지", "불성실", "부담", "우려", "리스크", "경고", "지연", "차질",
       "재고", "하락", "논란", "고평가", "굴욕", "민원")


# 매주 자동으로 만들어지는 종목 분석 글, 여러 종목 모음 기사 → '단순 언급'으로 (목록 기본 화면에서 숨김)
NOISE_TITLE = re.compile(r"투자분석\s*\d{4}|브리핑|모음|마감\s*시황|오늘의\s*(특징주|종목|공시)|주간\s*(유망|추천)"
                         r"|^\s*\[(부고|부음|인사|동정|게시판|결혼|화촉|알림)\]|별세|모친상|부친상|빙모상|빙부상|장모상|장인상|조모상|조부상"
                         # 테마주 등락 나열: "우주항공산업 관련주, '웃음꽃' 퍼스텍·태웅… '한숨' 센서뷰" (현대경제신문 등)
                         r"|관련주\s*,\s*['‘\"“]")
NOISE_OFFICE = ("주달",)
# 외부 기사에 '악재'를 붙이는 뚜렷한 단어 (그 밖에는 호재·악재를 자동으로 붙이지 않음)
STRONG_NEG = ("유상증자", "소송", "적자", "오버행", "블록딜", "전환사채", "감사의견", "거래정지", "불성실", "계약 해지",
              "해지", "취소", "보호예수 해제", "횡령", "배임", "상장폐지", "관리종목", "하한가")


def company_name() -> str:
    """회사 이름 (비공개 설정의 display_name). 공개 저장소에는 이름이 없어서 실행할 때 읽음."""
    try:
        from .detector import load_settings
        n = (load_settings().get("display_name") or "").strip()
    except Exception:          # noqa: BLE001 - 설정을 못 읽으면 예전처럼 동작
        n = ""
    return n or NAME


def classify(title: str, body: str, name: str | None = None, office: str = "") -> tuple[str, str]:
    name = name or company_name()
    t, b = title or "", body or ""
    in_title = name in t
    mention = "제목" if in_title else ("본문" if name in b else "연결")
    if office in NOISE_OFFICE or NOISE_TITLE.search(t):
        return "단순 언급", mention
    has_company = any(w in t for w in COMPANY)
    if any(w in t for w in MARKET_STRONG) or (in_title and any(w in t for w in MARKET_WEAK) and not has_company):
        return "시황·특징주", mention
    if any(w in t for w in RESEARCH) or (in_title and any(w in b[:90] for w in RESEARCH) and not has_company):
        return "리서치", mention
    if in_title:
        if t.lstrip().startswith("[") and not t.lstrip().startswith("[단독]"):
            return "언론 분석", mention          # 기획 연재 ([빛을 담는 기업들] 등)
        # 회사 발표는 이 회사가 제목의 주어일 때만 (첫 쉼표·말줄임표 앞에 이름이 있을 때).
        # 예: '충북○○위원회, 우수 중소기업 탐방…그린광학 방문'은 다른 기관이 주어라 외부 기사 (언론 분석)
        subject = re.split(r"[,，…]|\.\.\.", t, 1)[0]
        return ("회사 발표" if has_company and name in subject else "언론 분석"), mention
    if name in b[:90]:
        return "언론 분석", mention              # 제목엔 없지만 이 종목을 다룬 기사
    if any(w in t for w in THEME):
        return "테마·수혜주", mention
    return "단순 언급", mention


# 악재 단어가 다른 말 속에 우연히 들어 있는 경우 (예: '인적자원'의 '적자') — 악재 판단 전에 지움
FALSE_NEG = ("인적자원", "인적 자원")


def neg_text(s: str) -> str:
    for w in FALSE_NEG:
        s = s.replace(w, " ")
    return s


def tone_of(title: str, body: str = "") -> int:
    def score(s):
        p = sum(w in s for w in POS)
        n = sum(w in neg_text(s) for w in NEG)
        return (p > n) - (n > p)
    t = score(title or "")
    return t if t else score((body or "")[:120])


def _tokens(title: str, name: str = NAME) -> set[str]:
    s = re.sub(r"\[[^\]]*\]|[\"'“”‘’…·,.!?()<>「」『』~\-–—/|]", " ", (title or "").replace(name, " "))
    return {w for w in s.split() if len(w) >= 2}


# ------------------------------------------------------------------ 수집

def fetch_stock_news(code: str, page: int = 1, size: int = 50) -> list[dict]:
    d = naver._json(f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={size}&page={page}")
    out = []
    for group in d or []:
        for it in (group.get("items") or [])[:1]:     # 묶음의 대표 기사
            dt = str(it.get("datetime") or "")
            if len(dt) < 12:
                continue
            out.append({
                "id": str(it.get("id") or f"{it.get('officeId')}{it.get('articleId')}"),
                "ts": f"{dt[0:4]}-{dt[4:6]}-{dt[6:8]}T{dt[8:10]}:{dt[10:12]}",
                "office": it.get("officeName") or "",
                "title": html.unescape(it.get("titleFull") or it.get("title") or "").strip(),
                "body": html.unescape(it.get("body") or "").strip(),
                "url": it.get("mobileNewsUrl") or "",
                "outlets": int(group.get("total") or 1),
            })
    return out


def _key_title(t: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")[:30]


def _key_office(o: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", o or "")


RECLASS_VERSION = "v3"   # 분류 규칙이 바뀌면 올림 → 모은 기사 전체를 한 번 다시 분류 (v2: 회사가 제목의 주어일 때만 회사 발표, '인적자원'은 악재 아님)


def _category(title: str, body: str, name: str, office: str, source: str | None) -> tuple[str, str]:
    cat, mention = classify(title, body, name, office)
    if source == "naver_search" and cat == "단순 언급" and name in (body or "") and not NOISE_TITLE.search(title or ""):
        cat = "언론 분석"      # 요약 뒷부분에만 이름이 나와도 받고, 원문을 열어 '이름만 언급'인지 따로 봄 (relevance.check)
    return cat, mention


def reclassify(code: str) -> int:
    """규칙이 바뀌면 한 번만: 모은 기사 전체의 분류·호재/악재를 지금 규칙으로 다시 매김."""
    from .db import get_state, set_state
    name = company_name()
    key = f"articles:reclass_{RECLASS_VERSION}"
    if name == NAME or get_state(key):
        return 0
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, title, body, office, source FROM articles WHERE symbol=?", (code,))]
        for r in rows:
            cat, mention = _category(r["title"], r["body"], name, r["office"] or "", r["source"])
            conn.execute("UPDATE articles SET category=?, mention=?, tone=? WHERE id=?",
                         (cat, mention, tone_of(r["title"], r["body"]), r["id"]))
    set_state(key, datetime.now().isoformat(timespec="minutes"))
    return len(rows)


def _save(code: str, items: list[dict], source: str) -> int:
    name, now = company_name(), datetime.now().isoformat(timespec="minutes")
    from . import offices
    items = [{**i, "office": offices.canonical(i.get("office"))} for i in items]      # 언론사 이름을 한글 이름 하나로
    with connect() as conn:
        if source != "naver" and items:   # 이미 받은 기사·같은 매체 같은 제목은 다시 넣지 않음 (다른 매체의 같은 제목은 링크를 위해 남김)
            got = conn.execute("SELECT id, title, office FROM articles WHERE symbol=? AND ts >= ?",
                               (code, min(i["ts"] for i in items)[:10])).fetchall()
            have = {_key_title(r[1]) + "|" + _key_office(r[2]) for r in got}
            ids = {r[0] for r in got}          # 네이버 뉴스판 기사는 종목 뉴스와 번호가 같음 → 묶음 매체 수(outlets)를 덮어쓰지 않게
            items = [i for i in items if i["id"] not in ids and _key_title(i["title"]) + "|" + _key_office(i.get("office")) not in have]
        items = [i for i in items if (i.get("ts") or "") >= COVERAGE_FROM]     # 모으는 기간 전 기사는 넣지 않음
        from . import relevance
        gone = relevance.dropped_ids(conn)                                     # 막은 매체·이름만 언급으로 지운 기사
        items = [i for i in items if i["id"] not in gone and not relevance.blocked(i.get("office"), i.get("url"))]
        rows = []
        for i in items:
            cat, mention = _category(i["title"], i["body"], name, i.get("office") or "", source)
            if cat in relevance.NOISE_CATEGORIES:      # 단순 언급·시황 기사는 모으지 않음
                relevance.drop(conn, {**i, "symbol": code}, "noise")
                continue
            rows.append((i["id"], code, i["ts"], i["office"], i["title"], i["body"], i["url"], i.get("outlets") or 1,
                         cat, tone_of(i["title"], i["body"]), mention, now, source))
        before = conn.total_changes
        conn.executemany(
            "INSERT INTO articles(id, symbol, ts, office, title, body, url, outlets, category, tone, mention, first_seen, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET outlets=excluded.outlets",
            rows)
        return conn.total_changes - before


GOOGLE_FROM = date(2026, 1, 1)          # 기사를 빠짐없이 모으기 시작한 날 (이날부터 보도자료·외부 기사를 판정)
COVERAGE_FROM = GOOGLE_FROM.isoformat()    # 이보다 앞선 기사는 모으지 않음 (지난 기사를 거슬러 채웠다가 필요 없어 지움)


def sync_google(code: str, now: datetime | None = None, max_queries: int = 6, log=None) -> int:
    """구글 뉴스에서 회사 이름으로 검색해 네이버 종목 뉴스에 빠진 기사(지역지·전문지 등)를 보충.

    처음에는 GOOGLE_FROM부터 반달씩 나눠 지난 기사를 채우고(한 번에 max_queries개), 그 뒤로는 최근 7일만.
    """
    from . import newssearch
    from .db import get_state, set_state
    name = company_name()
    if name == NAME:
        return 0
    now = now or datetime.now()
    st = get_state("articles:google_backfill_v2")
    done = set(json.loads(st["value"])) if st and st.get("value") else set()
    queries, todo = [], []
    d = GOOGLE_FROM
    while d <= now.date():
        mid = d.replace(day=16)
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        for a, b in ((d, mid), (mid, nxt)):
            if a <= now.date() and a.isoformat() not in done:
                todo.append(a.isoformat())
                queries.append(f'"{name}" after:{a.isoformat()} before:{b.isoformat()}')
        d = nxt
    queries, todo = queries[:max_queries], todo[:max_queries]
    if not newssearch._throttled("articles:google", 20):
        queries.append(f'"{name}" when:7d')
    n = 0
    for i, q in enumerate(queries):
        try:
            items = newssearch.google(q)
        except RuntimeError as e:
            if log:
                log(f"구글 뉴스 기사 수집 실패: {e}")
            break
        n += _save(code, items, "google") if items else 0
        if i < len(todo):
            done.add(todo[i])
            set_state("articles:google_backfill_v2", json.dumps(sorted(done)))
    if n:
        group_stories(code)
    return n


NAVER_SEARCH_PAGES = 10        # 처음 한 번 거슬러 받는 쪽수 (한 쪽 100건, 네이버 검색 API는 1000건까지)


def sync_naver_search(code: str, log=None) -> int:
    """네이버 검색 API(뉴스)로 회사 이름을 검색해 빠진 기사를 보충.

    네이버 종목 뉴스는 종목에 연결된 기사만, 구글 뉴스는 일부만 나와서 지역지 기사 등이 빠짐
    (예: 제목에 회사 이름이 없는 탐방 기사). 검색 API는 본문 요약도 주므로 제목에 이름이 없어도 분류할 수 있음.
    처음 한 번은 NAVER_SEARCH_PAGES쪽까지 거슬러 받고, 그 뒤로는 실행마다(10분 간격) 최신 100건.
    """
    from . import newssearch
    from .db import get_state, set_state
    name = company_name()
    if name == NAME or not newssearch.naver_enabled():
        return 0
    first = not get_state("articles:naver_search_backfill_v2")
    if first:     # v2: 요약 뒷부분에만 이름이 나와 '단순 언급'으로 버렸던 기사를 다시 받을 수 있게
        with connect() as conn:
            conn.execute("DELETE FROM dropped_articles WHERE reason='noise' AND dropped_at >= '2026-10-26'")
    if newssearch._throttled("articles:naver_search", 10) and not first:      # 부를 때마다 시각을 남김
        return 0
    with connect() as conn:          # 주소의 매체 이름을 이미 아는 경우(구글 뉴스로 받은 기사 등)는 그 이름을 씀
        known = {}
        for office, url in conn.execute("SELECT office, url FROM articles WHERE symbol=? AND url NOT LIKE '%news.google.com%'", (code,)):
            h = newssearch._host(url)
            if h and office and "." not in office:
                known.setdefault(h, office)
    n = 0
    for page in range(NAVER_SEARCH_PAGES if first else 1):
        try:
            items = newssearch.search(name, start=1 + page * 100, display=100)
        except RuntimeError as e:
            if log:
                log(f"네이버 검색 기사 수집 실패: {e}")
            return n
        for i in items:
            h = newssearch._host(i.get("orig") or "")
            if h in known and "." in (i.get("office") or ""):
                i["office"] = known[h]
        items = [i for i in items if (i.get("ts") or "") >= COVERAGE_FROM]
        n += _save(code, items, "naver_search") if items else 0
        if len(items) < 100:
            break
    if first:
        set_state("articles:naver_search_backfill_v2", datetime.now().isoformat(timespec="minutes"))
    if n:
        group_stories(code)
    return n


def drop_manual_duplicates(code: str) -> int:
    """한 번만: 직접 등록한 기사 중 자동으로 모은 기사와 같은 기사(주소나 제목이 같음)를 지움.
    (이후 새로 생기는 중복은 사이트의 '중복이라 삭제' 버튼으로 지움)"""
    from .db import get_state, set_state
    if get_state("articles:pr_dedupe_v1"):
        return 0
    n = 0
    with connect() as conn:
        prs = [dict(r) for r in conn.execute("SELECT id, title, url FROM pr_events")]
        arts = [dict(r) for r in conn.execute("SELECT title, url FROM articles WHERE symbol=?", (code,))]
        ku = lambda u: (re.search(r"/article/(\d+)/(\d+)", u or "") or [None])[0] or (u or "").split("?")[0].rstrip("/")
        kt = lambda t: re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")[:40]
        for p in prs:
            if any((p["url"] and ku(a["url"]) == ku(p["url"])) or (kt(p["title"]) and kt(a["title"]) == kt(p["title"]))
                   for a in arts):
                n += conn.execute("DELETE FROM pr_events WHERE id=?", (p["id"],)).rowcount
    set_state("articles:pr_dedupe_v1", datetime.now().isoformat(timespec="minutes"))
    return n


def sync_articles(code: str, pages: int = 1) -> int:
    reclassify(code)
    n = 0
    for p in range(1, pages + 1):
        try:
            items = fetch_stock_news(code, p)
        except naver.DataSourceError:
            if p == 1:
                raise
            break
        if not items:
            break
        n += _save(code, items, "naver")
    group_stories(code)
    drop_manual_duplicates(code)
    try:
        from . import reporters
        reporters.fill(code)
    except Exception as e:      # noqa: BLE001 - 기자명은 부가 정보라 실패해도 수집은 그대로
        print(f"::warning title=기자명::{type(e).__name__}: {str(e)[:150]}", flush=True)
    try:
        n += sync_google(code)
    except Exception as e:      # noqa: BLE001 - 구글이 막혀도 네이버 수집은 그대로
        print(f"::warning title=구글 뉴스 기사::{type(e).__name__}: {str(e)[:150]}", flush=True)
    try:
        n += sync_naver_search(code, log=lambda m: print(f"::warning title=네이버 검색 기사::{m[:150]}", flush=True))
    except Exception as e:      # noqa: BLE001 - 검색 API가 막혀도 다른 수집은 그대로
        print(f"::warning title=네이버 검색 기사::{type(e).__name__}: {str(e)[:150]}", flush=True)
    try:
        from . import offices
        if offices.normalize(code):              # 예전에 다른 표기로 들어간 이름도 맞추고, 매체 수를 다시 셈
            group_stories(code)
    except Exception as e:      # noqa: BLE001 - 이름 맞추기는 부가 작업
        print(f"::warning title=언론사 이름::{type(e).__name__}: {str(e)[:150]}", flush=True)
    try:
        from . import relevance
        gone = relevance.purge_blocked(code) + relevance.check(code, company_name())
        if gone:
            print(f"::notice title=기사 정리::막은 매체·이름만 언급한 기사 {gone}건 삭제", flush=True)
            group_stories(code)
    except Exception as e:      # noqa: BLE001 - 정리가 실패해도 수집은 그대로
        print(f"::warning title=기사 정리::{type(e).__name__}: {str(e)[:150]}", flush=True)
    return n


def group_stories(code: str):
    """같은 내용을 여러 매체가 낸 기사를 하나로 묶음 (가장 먼저 나온 기사 기준)."""
    name = company_name()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ts, title FROM articles WHERE symbol=? ORDER BY ts DESC LIMIT 2000", (code,))]
    rows.sort(key=lambda r: r["ts"])
    stories = []   # (story_id, ts, tokens)
    assign = {}
    for r in rows:
        tk = _tokens(r["title"], name)
        t = datetime.fromisoformat(r["ts"])
        best = None
        for sid, st, stk in stories:
            if t - st > timedelta(hours=72) or not tk or not stk:
                continue
            shared = len(tk & stk)
            j = shared / len(tk | stk)
            if (j >= 0.3 or (shared >= 2 and j >= 0.2)) and (best is None or j > best[1]):
                best = (sid, j)
        if best:
            assign[r["id"]] = best[0]
        else:
            stories.append((r["id"], t, tk))
            assign[r["id"]] = r["id"]
    # 사이트에서 직접 묶은 기사: '이 두 기사는 같은 내용'이라는 연결로 보고 연결된 묶음을 모두 하나로 (방향 무관).
    # 예전에는 대상을 따라가며 옮겨서, 같은 두 기사를 서로 반대 방향으로 합치면(A에 B, B에 A) 순환이 되어 아무것도 안 합쳐졌음
    with connect() as conn:
        links = [(r[0], r[1]) for r in conn.execute("SELECT a, b FROM article_links")] + [(r[0], r[1]) for r in conn.execute(
            "SELECT id, story_with FROM articles WHERE symbol=? AND story_with IS NOT NULL", (code,))]   # story_with: 예전 방식(한 기사에 하나)
    parent = {}
    def root(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    first = {}                                   # 묶음 번호 → 그 묶음의 가장 이른 시각 (합친 묶음의 번호는 가장 이른 묶음 것으로)
    ts_of = {r["id"]: r["ts"] for r in rows}
    for k, v in assign.items():
        first[v] = min(first.get(v, ts_of[k]), ts_of[k])
    for aid, target in links:
        if aid in assign and target in assign:
            a, b = root(assign[aid]), root(assign[target])
            if a != b:
                keep, drop = (a, b) if (first[a], a) <= (first[b], b) else (b, a)
                parent[drop] = keep
    if parent:
        assign = {k: root(v) for k, v in assign.items()}     # 그 기사와 자동으로 같이 묶여 있던 기사도 함께
    with connect() as conn:
        conn.executemany("UPDATE articles SET story=? WHERE id=?", [(s, i) for i, s in assign.items()])


# ------------------------------------------------------------------ 반응 계산

def _daily(symbol: str, start: str, end: str) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM daily_bars WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date", (symbol, start, end))]


def trading_days(symbol: str) -> list[str]:
    with connect() as conn:
        d = [r[0] for r in conn.execute("SELECT date FROM daily_bars WHERE symbol=? ORDER BY date", (symbol,))]
        m = [r[0] for r in conn.execute("SELECT DISTINCT substr(ts,1,10) FROM minute_bars WHERE symbol=?", (symbol,))]
    return sorted(set(d) | set(m))


def reaction_day(ts: datetime, days: list[str]) -> str | None:
    """기사가 처음 반영될 수 있는 거래일 (장 마감 뒤 공개면 다음 거래일)."""
    d = ts.date().isoformat()
    if ts.time() > time(15, 30):
        later = [x for x in days if x > d]
    else:
        later = [x for x in days if x >= d]
    if not later or (date.fromisoformat(later[0]) - ts.date()).days > 6:
        return None          # 그 무렵 시세 기록이 없음
    return later[0]


def _ret_path(symbol: str, d0: str, n_after: int = 3):
    """반응일 전 종가 대비 D0, D+1, D+3 누적 변화율과 거래량 배수."""
    rows = _daily(symbol, (date.fromisoformat(d0) - timedelta(days=100)).isoformat(),
                  (date.fromisoformat(d0) + timedelta(days=12)).isoformat())
    idx = next((i for i, r in enumerate(rows) if r["date"] == d0), None)
    if idx is None or idx == 0:
        return None
    prev = rows[idx - 1]["close"]
    out = {"prev_close": prev, "d0_close": rows[idx]["close"],
           "gap": rows[idx]["open"] / prev - 1 if rows[idx].get("open") and prev else None}   # 시초가 갭
    for k, off in (("d0", 0), ("d1", 1), ("d3", 3)):
        j = idx + off
        out[k] = rows[j]["close"] / prev - 1 if j < len(rows) and prev else None
    vols = [r["volume"] for r in rows[max(0, idx - 20):idx] if r["volume"]]
    out["vol_ratio"] = rows[idx]["volume"] / (sum(vols) / len(vols)) if vols and rows[idx]["volume"] else None
    # 거래량이 얼마나 드문가: 직전 60거래일 중 이날 이상 거래된 날의 비율 (시장의 관심 정도)
    past = [r["volume"] for r in rows[max(0, idx - 60):idx] if r["volume"]]
    out["vol_rank"] = (1 + sum(1 for v in past if v >= rows[idx]["volume"])) / (1 + len(past))         if len(past) >= 20 and rows[idx]["volume"] else None
    out["volume"] = rows[idx]["volume"]
    return out


def _peer_codes() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT code, name FROM related_stocks")]


def _minute_change(code: str, t0: datetime, t1: datetime):
    from . import analysis
    df = analysis.load_range(code, t0 - timedelta(days=4), t1)
    if df.empty:
        return None
    day = df[df["ts"].dt.date == t1.date()]
    a = day[day["ts"] < t0]
    b = day[(day["ts"] >= t0) & (day["ts"] <= t1)]
    if b.empty:
        return None
    base = float(a["close"].iloc[-1]) if not a.empty else float(b["open"].iloc[0])
    return float(b["close"].iloc[-1]) / base - 1 if base else None



def _close_series(code: str, lo: str, hi: str) -> dict:
    return {r["date"]: r["close"] for r in _daily(code, lo, hi) if r.get("close")}


def decompose(symbol: str, d0: str, peers: list[dict] | None = None) -> dict | None:
    """반응일 주가 변동을 '시장 몫 + 분야별 몫 + 고유(기사) 몫'으로 나눔. 이벤트 상세(하루)와 같은 계산(causal.day_split)."""
    from . import causal
    from .detector import load_settings
    return causal.day_split(symbol, d0, load_settings().get("factor_etfs") or [])


def timing(symbol: str, start: datetime, peers: list[dict], minutes: int = 30) -> dict | None:
    """기사 직후 이 종목이 업종보다 먼저 움직였는지 (분봉)."""
    from . import analysis
    t1 = start + timedelta(minutes=minutes)
    df = analysis.load_range(symbol, start - timedelta(days=4), t1)
    if df.empty:
        return None
    day = df[df["ts"].dt.date == start.date()]
    before = day[day["ts"] < start]
    after = day[(day["ts"] >= start) & (day["ts"] <= t1)]
    if after.empty:
        return None
    base = float(before["close"].iloc[-1]) if not before.empty else float(after["open"].iloc[0])
    path = [(r.ts, float(r.close) / base - 1) for r in after.itertuples()]
    peak = max(path, key=lambda x: abs(x[1]))
    first = next(((t, v) for t, v in path if abs(v) >= 0.01), None)
    pch = [c for c in (_minute_change(p["code"], start, peak[0].to_pydatetime()) for p in peers) if c is not None]
    peer_move = sum(pch) / len(pch) if len(pch) >= 2 else None
    own_move = peak[1]
    if peer_move is None:
        label, cls = "같은 업종 분봉 기록이 없어 시간 순서를 확인하지 못했습니다.", "none"
    elif abs(own_move) < 0.01:
        label, cls = f"기사 후 {minutes}분 동안 이 종목이 1% 이상 움직이지 않았습니다.", "flat"
    elif abs(peer_move) < 0.3 * abs(own_move) or peer_move * own_move < 0:
        mins = int((first[0].to_pydatetime() - start).total_seconds() // 60) if first else None
        label = (f"기사 후 {mins}분 만에 이 종목이 먼저 움직였고(최대 {own_move*100:+.1f}%), "
                 f"같은 시각 업종 평균은 {peer_move*100:+.1f}%였습니다 → 기사 영향 쪽") if mins is not None else \
                f"이 종목 {own_move*100:+.1f}%, 같은 시각 업종 평균 {peer_move*100:+.1f}% → 기사 영향 쪽"
        cls = "lead"
    else:
        label, cls = (f"이 종목({own_move*100:+.1f}%)과 업종 평균({peer_move*100:+.1f}%)이 같은 시각에 함께 움직였습니다 "
                      "→ 업종 흐름 쪽"), "together"
    return {"label": label, "cls": cls, "own": own_move, "peers": peer_move,
            "peak_at": peak[0].isoformat(timespec="minutes") if hasattr(peak[0], "isoformat") else str(peak[0])}


# 기사와 같은 내용의 공시: 공시가 먼저 나왔으면 공시 시각부터 잼 (공시 종류 → 기사 제목에 나올 단어)
DISCLOSURE_MATCH = {"공급계약": ("계약", "공급", "수주", "납품"), "손익구조": ("매출", "영업익", "영업이익", "실적", "흑자"),
                    "기업설명회": ("IR", "기업설명회"), "증자": ("증자",), "전환사채": ("전환사채", "CB")}


def earlier_disclosure(symbol: str, title: str, ts: datetime) -> dict | None:
    """기사보다 먼저(36시간 안) 나온 같은 내용의 공시."""
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT date, ts, title, kind FROM disclosures WHERE symbol=? AND ts IS NOT NULL AND ts < ? AND ts >= ? "
            "ORDER BY ts DESC", (symbol, ts.isoformat(timespec="minutes"),
                                 (ts - timedelta(hours=36)).isoformat(timespec="minutes")))]
    for r in rows:
        src = f"{r['kind'] or ''} {r['title'] or ''}"
        for key, words in DISCLOSURE_MATCH.items():
            if key in src and any(w in (title or "") for w in words):
                return r
    return None


def disclosure_rows(symbol: str) -> list[dict]:
    """공시 목록 (DART 접수번호 앞에 d를 붙여 기사 번호와 겹치지 않게). 시각을 모르면 그날 15:30으로 두고 untimed."""
    from . import dart, kind
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT no, date, ts, title, kind, pblntf, filer, rm FROM disclosures WHERE symbol=? ORDER BY date DESC, no DESC",
            (symbol,))]
    for r in rows:
        r["id"] = f"d{r['no']}"
        r["untimed"] = not r["ts"]
        r["ts"] = r["ts"] or f"{r['date']}T15:30"
        r["url"] = kind.viewer_url(r["no"][1:]) if r.get("pblntf") == "K" else \
            dart.viewer_url(r["no"]) if r.get("pblntf") else ""
    return rows


def effect(symbol: str, ts: datetime, tone: int = 0, category: str = "", title: str = "",
           untimed: bool = False) -> dict:
    """untimed: 공개 시각을 모름(날짜만) → 그날(전일 종가 → 종가) 하루로만 잼."""
    from . import analysis
    days = trading_days(KOSDAQ)
    disc = earlier_disclosure(symbol, title, ts) if title else None
    if disc:                                # 공시가 먼저: 시장이 처음 안 시각부터 잼
        ts = datetime.fromisoformat(disc["ts"])
    d0 = reaction_day(ts, days)
    if untimed and d0 != ts.date().isoformat():      # 날짜만 아는데 그날 거래가 없었음 (상장 전 등)
        d0 = None
    # 장 마감 뒤나 휴장일에 나온 기사: 다음 거래일 움직임에 다른 요인이 섞일 수 있음
    # (같은 날 장 시작 전 기사는 그날 시초가부터 반영되므로 해당 없음)
    off = ts.time() > time(15, 30) or ts.date().isoformat() not in days
    # 14시 이후~장 마감 전 기사: 그날 남은 거래 시간이 짧아 반응일+다음날 2일 누적으로 봄
    late = bool(d0) and not off and ts.time() >= time(14, 0) and not untimed
    out = {"reaction_day": d0, "minute": None, "daily": None, "verdict": None, "verdict_cls": "none",
           "notes": [], "off_session": bool(d0) and off, "late": late, "base_ts": ts.isoformat(timespec="minutes"),
           "disclosure": {"ts": disc["ts"], "title": disc["title"], "kind": disc["kind"]} if disc else None}
    peers = _peer_codes()

    # 분봉 반응 (최근 기록이 있을 때)
    r = analysis.analyze_pr(symbol, ts)
    if r.ok and r.returns and not untimed:
        bench, bench_name = {}, "KOSDAQ"
        peer_avg = {}
        for h in r.returns:
            t1 = r.reaction_start + timedelta(minutes=h)
            ch = [c for c in (_minute_change(p["code"], r.reaction_start, t1) for p in peers) if c is not None]
            if len(ch) >= 2:
                peer_avg[h] = sum(ch) / len(ch)
        if peer_avg:
            bench, bench_name = peer_avg, "같은 업종 평균"
        else:
            bench = r.kosdaq_returns
        out["minute"] = {
            "reaction_start": r.reaction_start.isoformat(timespec="minutes"), "base_price": r.base_price,
            "returns": {str(k): v for k, v in r.returns.items()},
            "kosdaq": {str(k): v for k, v in r.kosdaq_returns.items()},
            "peers": {str(k): v for k, v in peer_avg.items()},
            "excess": {str(k): r.returns[k] - bench[k] for k in r.returns if k in bench},
            "bench_name": bench_name,
            "max_up": r.max_up, "max_up_at": r.max_up_at.isoformat(timespec="minutes") if r.max_up_at else None,
            "max_down": r.max_down, "max_down_at": r.max_down_at.isoformat(timespec="minutes") if r.max_down_at else None,
            "vol_after30": r.vol_after30, "vol_usual30": r.vol_usual30,
            "outside_session": r.reaction_start.isoformat(timespec="minutes") != ts.isoformat(timespec="minutes"),
        }

    # 일봉 반응 (몇 달 전 기사도)
    if d0:
        own = _ret_path(symbol, d0)
        if own:
            kq = _ret_path(KOSDAQ, d0) or {}
            pp = [x for x in (_ret_path(p["code"], d0) for p in peers) if x]
            pavg = {k: sum(x[k] for x in pp if x.get(k) is not None) / max(1, len([x for x in pp if x.get(k) is not None]))
                    for k in ("d0", "d1", "d3")} if len(pp) >= 2 else {}
            bench = pavg if pavg else kq
            out["daily"] = {
                **{k: own.get(k) for k in ("d0", "d1", "d3", "vol_ratio", "vol_rank", "volume", "prev_close", "gap")},
                "kosdaq": {k: kq.get(k) for k in ("d0", "d1", "d3")},
                "peers": pavg,
                "excess": {k: (own[k] - bench[k]) if own.get(k) is not None and bench.get(k) is not None else None
                           for k in ("d0", "d1", "d3")},
                "bench_name": "같은 업종 평균" if pavg else "KOSDAQ",
            }
        # 15시 넘어 공개(장 마감 전): 발표 직후 반응은 그날 종가 → 다음 거래일로 재므로, 일봉도 그날 종가 대비 다음 거래일부터
        # (반응일 종가를 공개 전 움직임까지 담은 '그날 종가'로 보면 +30분 숫자와 기준이 어긋남)
        if not off and ts.date().isoformat() == d0 and ts.time() > time(15, 0):
            nxt = next((x for x in days if x > d0), None)
            own2 = _ret_path(symbol, nxt) if nxt else None
            if own2:
                kq2 = _ret_path(KOSDAQ, nxt) or {}
                out["daily_next"] = {"day": nxt, **{k: own2.get(k) for k in ("d0", "d1", "d3")},
                                     "kosdaq": {k: kq2.get(k) for k in ("d0", "d1", "d3")}}

    if d0:
        from . import causal
        out["event_study"] = causal.event_study(symbol, d0)
        from .feed import kst_now
        now = kst_now()
        if d0 >= now.date().isoformat() and (d0 > now.date().isoformat() or now.time().replace(tzinfo=None) <= time(15, 30)):
            out["event_study"] = {"ok": False, "pending": True, "reason": "반응일 장 마감 후 종가가 있어야 계산합니다."}
    out["verdict"], out["verdict_cls"], out["measure"] = _verdict(out, tone, category)
    return out


def _window_split(symbol: str, dc: dict, t0: datetime | None, t1: datetime, rarity: bool = True) -> dict | None:
    """t0 → t1 구간(t0가 없으면 전일 종가 → t1)을 시장 몫 + 분야 몫 + 고유 몫으로 나누고, 평소 같은 구간과 비교."""
    from . import causal
    day = t1.date()
    ret = lambda code: causal.window_return(code, day, t1.time(), t0)
    own, kq = ret(symbol), ret(KOSDAQ)
    if own is None or kq is None:
        return None
    with connect() as conn:
        rel = [dict(x) for x in conn.execute("SELECT code, source FROM related_stocks")]
    sector, used = 0.0, 0
    for part in dc.get("parts") or []:
        if part.get("kind") != "seg":
            continue
        ch = [c for c in (ret(x["code"]) for x in rel if (x["source"] or "비교 종목") == part["name"]) if c is not None]
        if ch:
            sector += part["beta"] * (sum(ch) / len(ch) - part["slope"] * kq)
            used += 1
    market = dc["beta_market"] * kq
    abn = own - market - sector
    if not rarity:                  # 시간대별 표의 다른 줄: 평소 비교(무거운 계산)는 대표 30분에만
        rr = None
    elif t0 is None:
        rr = causal.abnormal_rarity(symbol, day, abn, dc["beta_market"], minutes=None, until=t1.time())
    else:
        rr = causal.abnormal_rarity(symbol, day, abn, dc["beta_market"], minutes=int((t1 - t0).total_seconds() // 60))
    ok = rr and rr.get("ok")
    return {"own": own, "market": market, "sector": sector, "abnormal": abn, "kosdaq": kq, "segments_used": used,
            "rarity": rr["p"] if ok else None, "n_null": rr["n"] if ok else None,
            "start": t0.isoformat(timespec="minutes") if t0 else None, "end": t1.isoformat(timespec="minutes"),
            "basis": f"{int((t1 - t0).total_seconds() // 60)}분" if t0 else "개장 후 " + t1.strftime("%H:%M") + "까지"}

INTRADAY_MINUTES = 30
INTRADAY_STEPS = (5, 15, 30, 60, 120)      # 상세 화면 시간대별 표 (모두 같은 방식: 시장 몫 + 업종 몫 + 고유 움직임)


def intraday_reaction(symbol: str, eff: dict, time_ok: bool) -> dict | None:
    """공개 시각이 확실하고 분봉이 있으면 발표 직후 30분 반응 (상세 화면의 대표 숫자).
    - 장중 공개(15:00까지): 공개 시각 → 30분 뒤
    - 장 시작 전·전날 장 마감 뒤 공개: 전일 종가 → 반응일 09:30 (처음 거래된 30분)
    - 15시 넘어 공개(남은 거래가 30분도 안 됨): 그날 종가 → 다음 거래일 09:30 (다음 거래일 분봉이 쌓인 뒤)
    - 시각을 모름, 분봉 없음 → None (하루 단위를 대표로)"""
    d0 = eff.get("reaction_day")
    if not time_ok or not d0 or not eff.get("base_ts"):
        return None
    ts = datetime.fromisoformat(eff["base_ts"])
    open_t = datetime.combine(date.fromisoformat(d0), time(9, 0))
    if ts < open_t:
        t0, t1, kind = None, open_t + timedelta(minutes=INTRADAY_MINUTES), "open"
    elif ts.date().isoformat() == d0 and ts.time() <= time(15, 0):
        t0, t1, kind = ts, ts + timedelta(minutes=INTRADAY_MINUTES), "session"
    elif ts.date().isoformat() == d0:
        nxt = [d for d in trading_days(symbol) if d > d0]
        if not nxt:
            return None
        d0 = nxt[0]
        open_t = datetime.combine(date.fromisoformat(d0), time(9, 0))
        t0, t1, kind = None, open_t + timedelta(minutes=INTRADAY_MINUTES), "late"
    else:
        return None
    dc = decompose(symbol, d0)
    if not dc or dc.get("beta_market") is None:
        # 반응일이 오늘이면 일봉이 장 마감 뒤(15:50)에야 생겨 그날 몫 나누기가 안 됨. 30분 반응에 쓰는 건
        # 그 전 거래일들로 추정한 민감도뿐이라, 직전 거래일 기준 민감도로 계산 (장중에도 그래프가 나오게)
        prev = [d for d in trading_days(symbol) if d < d0]
        dc = decompose(symbol, prev[-1]) if prev else None
        if not dc or dc.get("beta_market") is None:
            return None
    w = _window_split(symbol, dc, t0, t1)
    if not w or w.get("rarity") is None:
        return None
    start = t0 or open_t
    rows = []
    for m in INTRADAY_STEPS:
        end = start + timedelta(minutes=m)
        if end.time() > time(15, 30):
            break
        x = w if m == INTRADAY_MINUTES else _window_split(symbol, dc, t0, end, rarity=False)
        if x:
            rows.append({"minutes": m, "end": end.isoformat(timespec="minutes"),
                         **{k: x[k] for k in ("own", "market", "sector", "abnormal", "kosdaq")}})
    # start: 그래프의 기준 시각 (장 전·15시 넘어 공개는 반응일 장 시작). 비어 있으면 그래프를 못 그려 '09:30 뒤 나옴'만 보였음
    return {**w, "start": start.isoformat(timespec="minutes"), "rows": rows, "kind": kind, "day": d0, "minutes": INTRADAY_MINUTES, "n_segments": len([p for p in dc.get("parts") or []
                                                                             if p.get("kind") == "seg"])}


def _verdict(eff: dict, tone: int, category: str):
    """대표 수치는 반응일 사건연구의 예상 대비 차이와 95% 범위만 사용. 계산이 된 기사엔 판정 문구 없음."""
    study = eff.get("event_study")
    if not study or not study.get("ok"):
        reason = (study or {}).get("reason") or "반응일 또는 평소 시세 기록이 부족합니다."
        return f"{'분석 대기' if (study or {}).get('pending') else '계산 자료 부족'}: {reason}", "none", None
    measure = {"label": "반응일 예상 대비 차이", "value": study["ar"], "method": "event-study-v2",
               "basis": "반응일", "own": study["actual"], "expected": study["expected"],
               "ci_low": study["ci_low"], "ci_high": study["ci_high"], "p": study["p"],
               "n_est": study["n_est"], "vol_ratio": (eff.get("daily") or {}).get("vol_ratio")}
    if category == "시황·특징주":
        return "주가 움직임을 다룬 기사", "result", measure
    if category == "단순 언급":
        return "이름만 언급된 기사 (효과 판단 제외)", "mention", measure
    # 판정 문구는 붙이지 않음: 숫자(실제·예상·차이·95% 범위)를 보고 사용자가 판단. 분류(색 표시)만 남김
    cls = "up" if study["ci_low"] > 0 else "down" if study["ci_high"] < 0 else "flat"
    return None, cls, measure


def confounders(symbol: str, eff: dict, art_id: str, story: str | None) -> list[str]:
    """같은 날 기사 효과와 섞였을 수 있는 다른 요인."""
    d0 = eff.get("reaction_day")
    if not d0:
        return []
    out = []
    with connect() as conn:
        others = [dict(r) for r in conn.execute(
            "SELECT id, ts, title, category FROM articles WHERE symbol=? AND hidden=0 AND ts BETWEEN ? AND ? "
            "AND id != ? AND COALESCE(story,'') != ? AND category NOT IN ('시황·특징주','단순 언급') ORDER BY ts",
            (symbol, (date.fromisoformat(d0) - timedelta(days=1)).isoformat() + "T15:30", d0 + "T15:30", art_id, story or ""))]
        themes = [dict(r) for r in conn.execute(
            "SELECT name, change_rate, rank, n_themes FROM theme_daily WHERE date=? AND mine=1 ORDER BY ABS(change_rate) DESC",
            (d0,))]
        evs = [dict(r) for r in conn.execute(
            "SELECT id, start_ts, direction, peak_return_5m FROM events WHERE symbol=? AND substr(start_ts,1,10)=? ORDER BY start_ts",
            (symbol, d0))]
    if others:
        out.append(f"같은 시기 이 종목 기사 {len(others)}건 더: "
                   + ", ".join(f"「{o['title'][:28]}」({o['category']})" for o in others[:3]))
    hot = [t for t in themes if abs(t["change_rate"]) >= 2]
    if hot:
        out.append("이날 이 종목 테마 등락: " + ", ".join(f"{t['name']} {t['change_rate']:+.1f}%" for t in hot[:3]))
    d = eff.get("daily") or {}
    kq = (d.get("kosdaq") or {}).get("d0")
    if kq is not None and abs(kq) >= 0.02:
        out.append(f"이날 KOSDAQ {kq * 100:+.1f}%")
    eff["events"] = evs
    return out


LEAD_PICKS = "article_lead_picks"


def time_source(a: dict) -> str:
    """공개 시각을 어디서 읽었나 (화면 표시용)."""
    if a.get("manual"):
        return "직접 등록"
    if a.get("time_from") == "manual":
        return "직접 고침"
    if a.get("time_from") == "page":
        return "원문 페이지"
    return {"google": "구글 뉴스", "naver": "네이버 뉴스"}.get(a.get("source") or "", a.get("source") or "수집 출처")


def lead_picks(conn) -> set:
    """사이트에서 '대표로' 고른 기사 id (묶음마다 하나)."""
    r = conn.execute("SELECT value FROM system_state WHERE key=?", (LEAD_PICKS,)).fetchone()
    try:
        return set(json.loads(r[0])) if r and r[0] else set()
    except ValueError:
        return set()


def article_rows(symbol: str, include_hidden: bool = False) -> list[dict]:
    with connect() as conn:
        arts = [dict(r) for r in conn.execute(
            "SELECT * FROM articles WHERE symbol=? " + ("" if include_hidden else "AND hidden=0 ") + "ORDER BY ts DESC",
            (symbol,))]
        prs = [dict(r) for r in conn.execute("SELECT * FROM pr_events ORDER BY published_ts DESC")]
        picks = lead_picks(conn)
    import re as _re

    def _key_url(u):
        m = _re.search(r"/article/(\d+)/(\d+)", u or "")
        return m.group(1) + m.group(2) if m else (u or "").split("?")[0].rstrip("/")

    def _key_title(t):
        return _re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")[:40]
    for p in prs:                                    # 직접 등록한 기사와 같은 기사(주소·제목 같음)는 하나로 묶음
        ku, kt = _key_url(p.get("url")), _key_title(p["title"])
        for a in arts:
            if (ku and _key_url(a.get("url")) == ku) or (kt and _key_title(a["title"]) == kt):
                a["story"] = f"pr{p['id']}"
    for p in prs:
        arts.append({"id": f"pr{p['id']}", "symbol": symbol, "ts": p["published_ts"], "office": "직접 등록",
                     "title": p["title"], "body": "", "url": p.get("url") or "", "outlets": 1, "category": "회사 발표",
                     "tone": tone_of(p["title"]), "mention": "제목", "story": f"pr{p['id']}", "manual": True,
                     "pr_type": p.get("pr_type")})
    arts.sort(key=lambda a: a["ts"], reverse=True)
    # 같은 내용 묶음: 대표(가장 먼저 나온 기사)만 남기고 나머지 수를 더함
    by_story = {}
    for a in arts:
        by_story.setdefault(a.get("story") or a["id"], []).append(a)
    from . import topics
    table = topics.rules()
    out = []
    for sid, group in by_story.items():
        # 대표: 사이트에서 직접 고른 기사 → 직접 등록한 기사 → 가장 먼저 나온 기사
        group.sort(key=lambda a: (a["id"] not in picks, not a.get("manual"), a["ts"]))
        lead = dict(group[0])
        first_ts = min(g["ts"] for g in group)
        if lead["id"] in picks and len(group) > 1:
            # 고른 기사의 제목·언론사로 보여 주되, 반응 계산의 기준 시각은 묶음에서 가장 먼저 나온 때 그대로
            lead["picked"], lead["own_ts"], lead["ts"] = True, lead["ts"], first_ts
        # 묶음의 성격은 가장 '원천'에 가까운 기사 기준 (회사 발표 > 리서치 > 언론 분석 > …)
        best = min(group, key=lambda g: CATEGORIES.index(g["category"]) if g["category"] in CATEGORIES else 9)
        lead["category"] = best["category"]
        if not lead.get("tone"):
            lead["tone"] = best.get("tone") or 0
        # 실린 매체 수: 네이버 묶음 수(링크는 대표 1개뿐)와 실제로 모은 매체 수 중 큰 값 (더하면 같은 매체를 두 번 셈)
        lead["outlets_total"] = max(max(int(g.get("outlets") or 1) for g in group),
                                    len({_key_office(g.get("office")) for g in group}))
        lead["related_titles"] = [{"id": g["id"], "manual": bool(g.get("manual")), "merged": bool(g.get("story_with")),
                                   "ts": g["ts"], "office": g["office"], "time_source": time_source(g),
                                   "title": g["title"], "url": g["url"], "reporter": g.get("reporter"),
                                   "topic": topics.classify(g["title"], g.get("body") or "", table)[0]}
                                  for g in group[1:]]
        lead["kind_override"] = next((g.get("kind_override") for g in group if g.get("kind_override")), None)
        lead["tone_override"] = next((g.get("tone_override") for g in group if g.get("tone_override") is not None), None)
        out.append(lead)
    # '회사 발표'는 기사 내용의 유형일 뿐, 그린광학이 배포한 보도자료의 증거가 아닙니다.
    # 자체 배포는 직접 등록 또는 담당자가 pr로 명시한 경우만 확정 분류합니다.
    from .article_integrity import time_quality
    for a in out:
        a["kind_auto"] = "company" if a["category"] == "회사 발표" else "ext"
        a["kind"] = a.get("kind_override") or ("pr" if a.get("manual") else a["kind_auto"])
        a["time_quality"] = time_quality(a)
        a["time_source"] = time_source(a)
        a["topic"], a["topics"] = topics.classify(a["title"], a.get("body") or "", table)
        # 호재·악재: 보도자료는 표시하지 않고, 외부 기사는 뚜렷한 악재 단어가 있을 때만 '악재'
        auto_tone = 0 if a["kind"] == "pr" else (-1 if any(w in neg_text(a["title"] or "") for w in STRONG_NEG) else 0)
        a["tone"] = a["tone_override"] if a.get("tone_override") is not None else auto_tone
    out.sort(key=lambda a: a["ts"], reverse=True)
    return out


def group_events(items: list[dict]) -> list[dict]:
    """같은 반응일의 기사들은 주가 반응을 나눌 수 없으므로 한 사건으로 묶음 (통계도 사건 수 기준).
    items: api가 만든 기사 목록 (effect 포함). 대표는 우리 보도자료 → 가장 먼저 나온 기사 순."""
    by_day: dict[str, list[dict]] = {}
    for a in items:
        day = (a.get("effect") or {}).get("reaction_day") or f"x{a['id']}"
        by_day.setdefault(day, []).append(a)
    out = []
    for day, g in by_day.items():
        g.sort(key=lambda a: a["ts"])
        main = [a for a in g if a["category"] not in ("단순 언급", "시황·특징주")] or g
        lead = next((a for a in main if a["kind"] == "pr"),
                    next((a for a in main if a["kind"] == "company"), main[0]))
        kind = "pr" if any(a["kind"] == "pr" for a in main) else \
               "company" if any(a["kind"] == "company" for a in main) else "ext"
        out.append({"day": None if day.startswith("x") else day, "lead": lead["id"],
                    "kind": kind, "ids": [a["id"] for a in g],
                    "outlets": sum(a.get("outlets_total") or 1 for a in main),
                    "n_pr": sum(1 for a in main if a["kind"] == "pr"),
                    "noise": all(a["category"] in ("단순 언급", "시황·특징주") for a in g),
                    "time_check": all(a.get("time_quality") == "check_time" for a in main),
                    "followup_of": None, "followup_candidate_of": None})
    # One day's price move must not count repeatedly for a story copied to another day.
    # Fuzzy similarity only creates review flags; it must not silently merge new information.
    from .article_integrity import likely_followup
    ordered = sorted(out, key=lambda e: e["day"] or "9999")
    by_id = {a["id"]: a for a in items}
    seen_stories = {}
    preceding = []
    for ev in ordered:
        articles_here = [by_id[i] for i in ev["ids"] if i in by_id
                         and by_id[i]["category"] not in ("단순 언급", "시황·특징주")]
        if not articles_here:
            articles_here = [by_id[i] for i in ev["ids"] if i in by_id]
        stories = {a.get("story") or a["id"] for a in articles_here}
        if stories and all(s in seen_stories for s in stories):
            ev["followup_of"] = min((seen_stories[s] for s in stories), key=lambda e: e["day"] or "9999")["lead"]
        elif ev["day"] and not ev["noise"]:
            for older in reversed(preceding):
                if older["day"] == ev["day"]:
                    continue
                if likely_followup(by_id[older["lead"]], by_id[ev["lead"]]):
                    ev["followup_candidate_of"] = older["lead"]
                    break
        for s in stories:
            seen_stories.setdefault(s, ev)
        if ev["day"] and not ev["noise"]:
            preceding.append(ev)
    out.sort(key=lambda e: max(a["ts"] for a in by_day[e["day"] or f"x{e['lead']}"]), reverse=True)
    return out


def find(symbol: str, art_id: str) -> dict | None:
    """기사 하나. 묶음 대표가 아닌 기사(같은 내용을 실은 다른 매체·합친 기사)면 그 묶음의 대표 기사."""
    rows = article_rows(symbol, include_hidden=True)
    return next((a for a in rows if a["id"] == art_id), None) or         next((a for a in rows if any(r["id"] == art_id for r in a.get("related_titles") or [])), None)


def before_event(symbol: str, t_start: datetime, t_end: datetime) -> dict:
    """이상변동 직전·직후 이 종목 기사 (원인 분석 맨 위에 보여줌)."""
    rows = article_rows(symbol)
    lead, late = [], []
    for a in rows:
        t = datetime.fromisoformat(a["ts"])
        if a["category"] == "단순 언급":
            continue
        if t_start - timedelta(days=3) <= t <= t_start + timedelta(minutes=1):
            lead.append(a)
        elif t_start < t <= t_end + timedelta(hours=3):
            late.append(a)
    lead.sort(key=lambda a: a["ts"], reverse=True)
    out_lead = []
    for a in lead[:4]:
        eff = effect(symbol, datetime.fromisoformat(a["ts"]), a["tone"], a["category"], a["title"])
        out_lead.append({**{k: a.get(k) for k in ("id", "ts", "office", "title", "url", "category", "tone",
                                                   "outlets_total", "manual")},
                         "verdict": eff["verdict"], "verdict_cls": eff["verdict_cls"], "measure": eff.get("measure"),
                         "gap_min": int((t_start - datetime.fromisoformat(a["ts"])).total_seconds() // 60)})
    last_any = None
    if not lead:
        older = [a for a in rows if datetime.fromisoformat(a["ts"]) < t_start and a["category"] not in ("단순 언급",)]
        if older:
            last_any = {k: older[0].get(k) for k in ("id", "ts", "title", "category")}
    return {"lead": out_lead, "late": [{k: a.get(k) for k in ("id", "ts", "office", "title", "url", "category")}
                                       for a in late[:5]], "last_before": last_any}
