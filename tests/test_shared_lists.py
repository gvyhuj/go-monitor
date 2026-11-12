"""여러 곳에 손으로 복사해 둔 목록이 서로 어긋나지 않는지 (화면 · 서버 · 통계 스크립트)."""
import inspect
import re
from pathlib import Path

APP_JS = Path("web/app.js").read_text(encoding="utf-8")


def test_main_disclosure_types_match_everywhere():
    import react_stats
    from core import causal
    in_app = set(re.findall(r'"(\w)"', re.search(r"const MAIN_DISC = new Set\(\[(.*?)\]\)", APP_JS).group(1)))
    assert in_app == set(react_stats.MAIN_DISC) == set(causal.NEWS_DISC_TYPES)


def test_noise_categories_defined_once():
    from core import causal, relevance
    assert causal.NOISE_CATEGORIES is relevance.NOISE_CATEGORIES


def test_every_request_the_site_sends_is_accepted_and_handled():
    # 화면이 보내는 요청 종류 → ghinbox가 받아 주고(KNOWN) → cloud_job.apply_inbox가 처리해야 함.
    # (한 번 어긋나서 요청이 버려진 적이 있음: ghinbox._retry_rejected)
    import cloud_job
    from core import ghinbox
    sent = set(re.findall(r'\{ type: "([a-z_]+)", ', APP_JS)) - {"custom", "solid"}      # custom·solid는 차트 설정값
    sent |= set(re.findall(r'type: a\.hidden \? "([a-z_]+)" : "([a-z_]+)"', APP_JS)[0])
    assert {"pr", "hide", "show", "delete_pr", "article_merge", "cause_delete"} <= sent      # 뽑는 규칙이 살아 있는지
    assert sent <= set(ghinbox.KNOWN), sent - set(ghinbox.KNOWN)
    handler = inspect.getsource(cloud_job.apply_inbox)
    missing = [t for t in ghinbox.KNOWN if f'"{t}"' not in handler and f"'{t}'" not in handler]
    assert not missing, missing
