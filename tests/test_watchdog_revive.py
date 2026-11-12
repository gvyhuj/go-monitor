"""사이트 지킴이: '주가 감시' 이어달리기가 끊기면(실행 하나가 시작에 실패, 2026-11-12 05:23) 다시 시작시킴."""
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

FAKE_GH = """#!/bin/bash
case "$*" in
  *"workflow run"*) echo "$*" >> "$CALLS" ;;
  *"--status success"*) echo "$FAKE_SUCCESS" ;;
  *"run list"*) echo "$FAKE_NEWEST" ;;
esac
"""


def _run(tmp_path, newest_min, status, success_min=5):
    steps = yaml.safe_load(Path(".github/workflows/health.yml").read_text())["jobs"]["watchdog"]["steps"]
    script = "\n".join(s["run"] for s in steps if "run" in s)
    gh = tmp_path / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    calls = tmp_path / "calls"
    ago = lambda m: (datetime.now(timezone.utc) - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "CALLS": str(calls), "GITHUB_REPOSITORY": "o/r",
           "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"), "FAKE_NEWEST": f"{ago(newest_min)} {status}", "FAKE_SUCCESS": ago(success_min)}
    r = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)
    return r, calls.read_text() if calls.exists() else ""


def test_broken_chain_is_restarted(tmp_path):
    r, calls = _run(tmp_path, newest_min=45, status="completed", success_min=60)
    assert r.returncode == 0, r.stderr
    assert "workflow run monitor.yml" in calls and "--ref main" in calls


def test_running_or_recent_chain_is_left_alone(tmp_path):
    assert _run(tmp_path, newest_min=45, status="in_progress")[1] == ""     # 다음 실행을 기다리는 중 (이어달리기 정상)
    assert _run(tmp_path, newest_min=10, status="completed")[1] == ""       # 방금 끝남


def test_long_outage_still_fails(tmp_path):
    r, calls = _run(tmp_path, newest_min=600, status="completed", success_min=600)
    assert "workflow run monitor.yml" in calls and r.returncode == 1
