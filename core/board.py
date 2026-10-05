"""네이버 종목토론방 — 이벤트 전후 글 수와 많이 본 글.

토론방 글은 사실 확인이 안 된 개인 의견입니다. 원인을 '확정'하는 자료가 아니라,
공식 소식이 없을 때 개인 투자자 사이에 어떤 이야기(소문·기대·불만)가 돌았는지 보는 단서로만 씁니다.

주소 (2026-10-04 확인)
  https://m.stock.naver.com/front-api/discussion/list?discussionType=domesticStock&itemCode={code}
      &isHolderOnly=false&excludesItemNews=false&isItemNewsOnly=false&isCleanbotPassedOnly=false&pageSize=50[&offset=...]
"""
from __future__ import annotations

import re
import time as _time
from datetime import datetime, timedelta

from . import naver
from .db import connect

URL = ("https://m.stock.naver.com/front-api/discussion/list?discussionType=domesticStock&itemCode={code}"
       "&isHolderOnly=false&excludesItemNews=false&isItemNewsOnly=false&isCleanbotPassedOnly=false&pageSize=50")
# 네이버가 자동으로 올리는 알림 글 ("5% 이상 상승했어요 🎉")
SYSTEM_RE = re.compile(r"^\s*\d+%\s*이상\s*(상승|하락)했어요")

TABLE = """CREATE TABLE IF NOT EXISTS board_posts (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, ts TEXT NOT NULL, title TEXT, body TEXT,
    views INTEGER, likes INTEGER, dislikes INTEGER, is_system INTEGER DEFAULT 0, holder INTEGER DEFAULT 0)"""


def _ensure():
    with connect() as conn:
        conn.execute(TABLE)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_board_ts ON board_posts(symbol, ts)")


def fetch_page(code: str, offset: str | None = None) -> tuple[list[dict], str | None]:
    d = naver._json(URL.format(code=code) + (f"&offset={offset}" if offset else ""))
    res = (d or {}).get("result") or {}
    out = []
    for p in res.get("posts") or []:
        if p.get("replyDepth"):
            continue
        title = (p.get("title") or "").strip()
        out.append({"id": str(p.get("id")), "ts": str(p.get("writtenAt") or "")[:16], "title": title,
                    "body": (p.get("contentSwReplacedButImg") or p.get("contentSwReplaced") or "").strip()[:400],
                    "views": int(p.get("viewCount") or 0), "likes": int(p.get("recommendCount") or 0),
                    "dislikes": int(p.get("notRecommendCount") or 0),
                    "is_system": int(bool(SYSTEM_RE.match(title))),
                    "holder": int(bool(p.get("isHolderVerified")))})
    nxt = res.get("lastOffset")
    return out, (str(nxt) if nxt and out else None)


def sync_board(code: str, since: datetime | None = None, max_pages: int = 3) -> int:
    """최근 글 저장. since가 있으면 그 시각까지 거슬러 올라감 (최대 max_pages쪽)."""
    _ensure()
    offset, n = None, 0
    for _ in range(max_pages):
        try:
            posts, offset = fetch_page(code, offset)
        except naver.DataSourceError:
            break
        if not posts:
            break
        with connect() as conn:
            for p in posts:
                cur = conn.execute(
                    "INSERT INTO board_posts(id, symbol, ts, title, body, views, likes, dislikes, is_system, holder) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET views=excluded.views, "
                    "likes=excluded.likes, dislikes=excluded.dislikes",
                    (p["id"], code, p["ts"], p["title"], p["body"], p["views"], p["likes"], p["dislikes"],
                     p["is_system"], p["holder"]))
                n += cur.rowcount
        oldest = min(p["ts"] for p in posts)
        if not offset or not since or oldest <= since.isoformat(timespec="minutes"):
            break
        _time.sleep(0.2)
    return n


def buzz(code: str, t_start: datetime, t_end: datetime) -> dict | None:
    """움직임 직전 2시간 글 수를 평소(최근 14일 같은 길이)와 비교 + 많이 본 글."""
    _ensure()
    a = (t_start - timedelta(hours=2)).isoformat(timespec="minutes")
    s = t_start.isoformat(timespec="minutes")
    e = (t_end + timedelta(minutes=60)).isoformat(timespec="minutes")
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM board_posts WHERE symbol=? AND ts BETWEEN ? AND ? AND is_system=0 ORDER BY ts",
            (code, a, e))]
        base_from = (t_start - timedelta(days=14)).isoformat(timespec="minutes")
        base_n = conn.execute("SELECT COUNT(*) FROM board_posts WHERE symbol=? AND ts BETWEEN ? AND ? AND is_system=0",
                              (code, base_from, a)).fetchone()[0]
        oldest = conn.execute("SELECT MIN(ts) FROM board_posts WHERE symbol=?", (code,)).fetchone()[0]
    if not oldest or oldest > a:
        return None                                   # 그 시기 글을 아직 모으지 못함
    before = [r for r in rows if r["ts"] < s]
    after = [r for r in rows if r["ts"] >= s]
    span_h = max(1.0, (datetime.fromisoformat(a) - max(datetime.fromisoformat(base_from),
                                                       datetime.fromisoformat(oldest))).total_seconds() / 3600)
    # 장중 위주로 글이 몰리므로 하루 24시간 평균 대신 '깨어 있는 16시간' 기준으로 2시간당 평균
    per2h = base_n / (span_h * 16 / 24) * 2 if span_h else 0
    top = sorted(rows, key=lambda r: -(r["views"] + 3 * r["likes"]))[:3]
    return {"before": len(before), "after": len(after), "baseline_2h": round(per2h, 2),
            "ratio": (len(before) / per2h) if per2h > 0 else None,
            "top": [{"ts": r["ts"], "title": r["title"], "views": r["views"], "likes": r["likes"],
                     "lead": r["ts"] < s} for r in top],
            "url": f"https://m.stock.naver.com/domestic/stock/{code}/discussion"}
