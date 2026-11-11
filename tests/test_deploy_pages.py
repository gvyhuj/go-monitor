import pytest

import deploy_pages


ENV = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "GITHUB_SHA": "abc123", "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}


def test_each_deploy_uses_a_fresh_commit_as_build_version_and_waits_for_success(monkeypatch):
    # 판 번호가 같은 커밋이면 Pages가 '성공'이라 하고 옛 판을 계속 내려줌 (11/10~11: 23:25 뒤 8번 연속),
    # 커밋이 아닌 글자를 보내면 404 (#213) → 게시마다 새로 만든 커밋 번호를 보내야 함 (actions/deploy-pages#383)
    calls = []
    commits = iter(["c1", "c2"])
    states = iter(["deployment_in_progress", "succeed", "succeed"])
    def req(method, url, token, body=None):
        calls.append((method, url, body))
        return {"id": 7, "page_url": "https://o.github.io/r/"} if method == "POST" else {"status": next(states)}
    monkeypatch.setattr(deploy_pages.time, "sleep", lambda s: None)
    kw = dict(req=req, token_fn=lambda: "oidc", commit_fn=lambda: next(commits), env=ENV)
    assert deploy_pages.deploy("99", **kw) == "https://o.github.io/r/"
    deploy_pages.deploy("99", **kw)
    posts = [c for c in calls if c[0] == "POST"]
    assert posts[0][1] == "https://api.github.com/repos/o/r/pages/deployments"
    assert posts[0][2] == {"artifact_id": 99, "pages_build_version": "c1", "oidc_token": "oidc"}
    assert posts[1][2]["pages_build_version"] == "c2"
    assert calls[1][1] == "https://api.github.com/repos/o/r/pages/deployments/7"


def test_failed_deploy_stops_the_step():
    with pytest.raises(RuntimeError, match="deployment_failed"):
        deploy_pages.deploy("99", req=lambda m, u, t, b=None: {"id": 7} if m == "POST" else {"status": "deployment_failed"},
                            token_fn=lambda: "oidc", commit_fn=lambda: "c1", env=ENV)
