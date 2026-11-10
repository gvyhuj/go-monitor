"""올린 사이트 묶음(Pages artifact)을 이 실행만의 판 번호로 게시.

actions/deploy-pages는 판 번호(pages_build_version)로 늘 커밋 번호를 보냄. 같은 커밋으로 하루 수백 번 올리면
Pages가 '성공'이라 하고도 옛 판을 계속 내려준 적이 있음 (2026-11-09 14:18~, 11-10 11:02~ verify_publication 실측;
커밋이 바뀐 배포는 바로 반영). 그래서 판 번호를 '커밋-실행번호-시도-순번'으로 매번 다르게 붙임.
사용: python deploy_pages.py <artifact_id> <순번>   (성공하면 page_url을 GITHUB_OUTPUT에)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
FAILED = {"deployment_failed", "deployment_content_failed", "deployment_cancelled", "deployment_lost"}


def _req(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def _oidc_token() -> str:
    r = urllib.request.Request(os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"],
                               headers={"Authorization": f"bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read())["value"]


def deploy(artifact_id: str, tag: str, *, req=_req, token_fn=_oidc_token, env=os.environ, wait: float = 5,
           timeout: float = 600) -> str | None:
    repo, token = env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"]
    version = f"{env['GITHUB_SHA']}-{env['GITHUB_RUN_ID']}-{env.get('GITHUB_RUN_ATTEMPT', '1')}-{tag}"
    d = req("POST", f"{API}/repos/{repo}/pages/deployments", token,
            {"artifact_id": int(artifact_id), "pages_build_version": version, "oidc_token": token_fn()})
    dep = d.get("id") or version
    print(f"Pages 게시 시작: 판 {version}", flush=True)
    end = time.monotonic() + timeout
    while True:
        status = req("GET", f"{API}/repos/{repo}/pages/deployments/{dep}", token).get("status")
        if status == "succeed":
            print("Pages 게시 완료", flush=True)
            return d.get("page_url")
        if status in FAILED:
            raise RuntimeError(f"Pages 게시 실패: {status}")
        if time.monotonic() > end:
            raise TimeoutError(f"Pages 게시가 {timeout:.0f}초 안에 끝나지 않음 (마지막 상태 {status})")
        time.sleep(wait)


def main() -> None:
    url = deploy(sys.argv[1], sys.argv[2])
    if url and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"page_url={url}\n")


if __name__ == "__main__":
    main()
