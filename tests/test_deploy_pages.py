import pytest

import deploy_pages


ENV = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "GITHUB_SHA": "abc123", "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}


def test_each_deploy_gets_a_unique_build_version_and_waits_for_success(monkeypatch):
    # 같은 커밋으로 하루 수백 번 올릴 때 판 번호(=커밋)가 같으면 Pages가 '성공'이라 하고 옛 판을 계속 내려줌 (11/9·11/10)
    calls = []
    states = iter(["deployment_in_progress", "succeed"])
    def req(method, url, token, body=None):
        calls.append((method, url, body))
        return {"id": 7, "page_url": "https://o.github.io/r/"} if method == "POST" else {"status": next(states)}
    monkeypatch.setattr(deploy_pages.time, "sleep", lambda s: None)
    url = deploy_pages.deploy("99", "1", req=req, token_fn=lambda: "oidc", env=ENV)
    url2 = deploy_pages.deploy("99", "2", req=lambda m, u, t, b=None: {"id": 8} if m == "POST" else {"status": "succeed"},
                               token_fn=lambda: "oidc", env=ENV)
    assert url == "https://o.github.io/r/" and url2 is None
    post = calls[0]
    assert post[:2] == ("POST", "https://api.github.com/repos/o/r/pages/deployments")
    assert post[2] == {"artifact_id": 99, "pages_build_version": "abc123-42-1-1", "oidc_token": "oidc"}
    assert [c[1] for c in calls[1:]] == ["https://api.github.com/repos/o/r/pages/deployments/7"] * 2


def test_failed_deploy_stops_the_step():
    with pytest.raises(RuntimeError, match="deployment_failed"):
        deploy_pages.deploy("99", "1", req=lambda m, u, t, b=None: {"id": 7} if m == "POST" else {"status": "deployment_failed"},
                            token_fn=lambda: "oidc", env=ENV)
