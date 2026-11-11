"""올린 사이트 묶음(Pages artifact)을 게시마다 새로 만든 커밋 번호로 게시.

actions/deploy-pages는 판 번호(pages_build_version)로 늘 실행의 커밋 번호를 보냄. 같은 커밋으로 하루 수백 번 올리면
Pages가 '성공'이라 하고도 옛 판을 계속 내려줌 (2026-11-09~11 verify_publication 실측, actions/deploy-pages#383).
판 번호는 이 저장소의 커밋이어야 함 (다른 글자는 404, #213). 그래서 게시마다 내용이 같은 빈 커밋을 만들어
pages-version 가지에 덮어쓰고 그 번호를 보냄 (main은 건드리지 않음).
사용: python deploy_pages.py <artifact_id>   (성공하면 page_url을 GITHUB_OUTPUT에)
"""
from __future__ import annotations

import json
import os
import subprocess
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


def _fresh_commit() -> str:
    """지금 코드와 내용이 같은 새 커밋을 만들어 pages-version 가지에 덮어씀 (옛 것은 가지에서 빠져 쌓이지 않음)."""
    who = ["-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]
    sha = subprocess.run(["git", *who, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", f"사이트 게시 {time.time():.0f}"],
                         check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "push", "-q", "-f", "origin", f"{sha}:refs/heads/pages-version"], check=True)
    return sha


def deploy(artifact_id: str, *, req=_req, token_fn=_oidc_token, commit_fn=_fresh_commit, env=os.environ, wait: float = 5,
           timeout: float = 180) -> str | None:
    repo, token = env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"]
    version = commit_fn()
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
    url = deploy(sys.argv[1])
    if url and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"page_url={url}\n")


if __name__ == "__main__":
    main()
