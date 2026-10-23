"""Article audit labels; these are conservative indicators, NOT source verification.

Don't move a news publication time to the open or close based on suspicion.
The source may publish multiple stories at the exact same clock minute.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re
import unicodedata


def time_quality(article: dict) -> str:
    """Return reported, manually_entered or check_time; never rewrite source ts on suspicion alone.

    ts is replaced only by the publisher's own page time (time_from='page', see reporters.fill)."""
    if article.get("manual"):
        return "manually_entered"
    if article.get("time_from") == "page":
        return "reported"
    ts = str(article.get("ts") or "")
    if article.get("source") == "google" and ts[11:16] in {"16:00", "17:00"}:
        return "check_time"
    return "reported"


def _tokens(title: str) -> set[str]:
    txt = unicodedata.normalize("NFKC", title or "").lower().replace("美", "미 ")
    txt = re.sub(r"그린광학|\(주\)|주식회사", " ", txt)
    return {t for t in re.findall(r"[가-힣a-z0-9]{2,}", txt)
            if t not in {"관련", "국내", "선정", "공급", "기업", "지원", "참여", "추진", "기술", "개발", "보도", "주가"}}


def likely_followup(a: dict, b: dict) -> bool:
    """Flag for human review, never automatically deduplicate on similarity alone."""
    try:
        early, late = datetime.fromisoformat(a["ts"]), datetime.fromisoformat(b["ts"])
    except (ValueError, KeyError, TypeError):
        return False
    if not timedelta(0) < late - early <= timedelta(hours=96):
        return False
    t1, t2 = _tokens(a.get("title", "")), _tokens(b.get("title", ""))
    common = t1 & t2
    if len(common) < 2:
        return False
    overlap = len(common) / min(len(t1), len(t2)) if t1 and t2 else 0
    return overlap >= 0.45 or (len(common) >= 3 and overlap >= 0.32)
