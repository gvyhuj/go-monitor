"""기사 주제: 제목(없으면 요약 앞부분)의 단어로 자동 분류.

앞에 있는 주제가 우선입니다(대표 주제). 한 기사가 여러 주제에 걸리면 topics에 모두 담습니다.
제목이나 본문 어디든 MOU(양해각서)가 있으면 대표 주제는 'MOU'이고, 'MOU 체결'만으로는 수주·계약에 넣지 않습니다.
비공개 설정의 'article_topics'({"주제": ["단어", ...], ...}, 순서 = 우선순위)로 목록을 바꿀 수 있습니다.
"""
from __future__ import annotations

import re

OTHER = "기타"
MOU = "MOU"
# 영문 MOU(대소문자·M.O.U 표기 포함, 앞뒤가 영문자가 아닐 때) 또는 '양해각서'
MOU_RE = re.compile(r"(?<![A-Za-z])M\.?O\.?U(?![A-Za-z])|양해각서", re.IGNORECASE)

DEFAULT_TOPICS: list[tuple[str, tuple[str, ...]]] = [
    ("실적", ("실적", "매출", "영업이익", "영업익", "순이익", "흑자", "적자", "어닝")),
    ("수주·계약", ("수주", "계약", "공급", "납품", "체결")),
    ("자금·주식", ("유상증자", "무상증자", "증자", "전환사채", "CB", "BW", "자사주", "공모", "상장", "보호예수",
               "블록딜", "오버행", "투자 유치", "지분")),
    ("정부·기관 지원", ("지원", "과제", "국책", "테크노파크", "TP,", "TP ", "정부", "산업부", "중기부", "과기정통부",
                   "방사청", "지자체", "선정")),
    ("방산", ("방산", "국방", "방위", "무기", "군수", "유도", "전투", "이스라엘")),
    ("우주·위성", ("우주", "위성", "발사체", "누리호")),
    ("반도체", ("반도체", "웨이퍼", "노광", "EUV", "파운드리")),
    ("기술·제품", ("개발", "기술", "특허", "국산화", "인증", "양산", "출시", "신제품", "고도화", "코팅", "렌즈", "광학계")),
    ("전시·IR·행사", ("전시", "박람회", "IR", "기업설명회", "행사", "수상", "참가")),
    ("주가·시황", ("특징주", "급등", "급락", "상한가", "하한가", "주가", "신고가", "관련주", "테마", "수혜", "강세", "약세")),
]


def rules() -> list[tuple[str, tuple[str, ...]]]:
    try:
        from .detector import load_settings
        custom = load_settings().get("article_topics")
    except Exception:          # noqa: BLE001 - 설정을 못 읽으면 기본 목록
        custom = None
    if isinstance(custom, dict) and custom:
        return [(str(k), tuple(str(w) for w in v)) for k, v in custom.items() if v]
    return DEFAULT_TOPICS


def classify(title: str, body: str = "", table=None) -> tuple[str, list[str]]:
    """(대표 주제, 걸린 주제 전부). 제목에서 하나도 안 걸리면 요약 앞 80자로 한 번 더 봄.
    MOU는 제목·본문 전체에서 찾고, 있으면 대표 주제. 이때 'MOU 체결'의 '체결'은 다른 주제 판단에서 뺌."""
    table = table or rules()
    mou = bool(MOU_RE.search(title or "") or MOU_RE.search(body or ""))
    for text in (title or "", (body or "")[:80]):
        if mou:
            text = MOU_RE.sub(" ", text).replace("체결", " ")
        hits = [name for name, words in table if name != MOU and any(w in text for w in words)]
        if hits:
            return (MOU, [MOU] + hits) if mou else (hits[0], hits)
    return (MOU, [MOU]) if mou else (OTHER, [])
