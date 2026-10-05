"""공유 링크 (Cloudflare 무료 임시 터널).

- 화면에서 '공유 링크 만들기'를 누르면 cloudflared 프로그램으로 https://xxxx.trycloudflare.com 주소를 엽니다.
  (가입·결제·서버 필요 없음. 처음 한 번 cloudflared.exe 를 GitHub 공식 배포처에서 tools 폴더로 받습니다)
- 링크를 받은 사람은 읽기 전용으로만 볼 수 있습니다 (기사 등록·삭제, 다시 분석, 공유 설정 불가).
- 링크에는 비밀 열쇠가 붙어 있어서 주소만 짐작해서는 들어올 수 없습니다.
- '공유 중지'를 누르거나 프로그램을 끄면 링크는 바로 끊기고, 다시 만들면 새 주소가 생깁니다.
- 이 PC가 꺼져 있으면 링크로도 볼 수 없습니다.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
RUN = ROOT / "run"
LOGS = ROOT / "logs"
STATE = RUN / "share.json"
IS_WIN = os.name == "nt"
EXE = TOOLS / ("cloudflared.exe" if IS_WIN else "cloudflared")
DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
URL_RE = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")

_lock = threading.Lock()
_proc = None


def _read() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(d: dict):
    RUN.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _alive(pid: int) -> bool:
    if not pid:
        return False
    if IS_WIN:
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, timeout=8, creationflags=0x08000000).stdout
            return "cloudflared" in out.lower()
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _binary() -> Path | None:
    if EXE.exists():
        return EXE
    found = shutil.which("cloudflared")
    return Path(found) if found else None


def status() -> dict:
    d = _read()
    if d.get("status") in ("running", "starting") and not _alive(d.get("pid", 0)):
        d = {"status": "off", "message": "공유가 끊겼습니다. 다시 만들어 주세요." if d.get("status") == "running" else ""}
        _write(d)
    out = {k: d.get(k) for k in ("status", "url", "started_at", "message", "progress")}
    out["status"] = out["status"] or "off"
    if d.get("status") == "running" and d.get("url") and d.get("token"):
        out["link"] = f"{d['url']}/s/{d['token']}"
    return out


def token() -> str | None:
    d = _read()
    return d.get("token") if d.get("status") == "running" else None


def _download(log):
    TOOLS.mkdir(exist_ok=True)
    tmp = EXE.with_suffix(".part")
    _write({"status": "starting", "message": "공유 프로그램(cloudflared)을 처음 한 번 내려받는 중입니다…", "progress": 0})
    req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        last = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total and time.time() - last > 0.5:
                last = time.time()
                _write({"status": "starting", "message": "공유 프로그램(cloudflared)을 처음 한 번 내려받는 중입니다…",
                        "progress": round(got / total * 100)})
    if got < 1_000_000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("내려받은 파일이 올바르지 않습니다.")
    tmp.replace(EXE)
    if log:
        log(f"cloudflared 내려받음 ({got:,} bytes)")


def _run(port: int, log):
    global _proc
    try:
        exe = _binary()
        if not exe:
            if not IS_WIN:
                raise RuntimeError("cloudflared 가 없습니다.")
            _download(log)
            exe = EXE
        tok = secrets.token_urlsafe(9)
        LOGS.mkdir(exist_ok=True)
        logf = open(LOGS / "share.log", "a", encoding="utf-8", errors="replace")
        logf.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} 공유 시작 (port {port})\n")
        logf.flush()
        flags = 0x08000000 if IS_WIN else 0   # CREATE_NO_WINDOW
        _proc = subprocess.Popen([str(exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                 errors="replace", creationflags=flags)
        _write({"status": "starting", "pid": _proc.pid, "token": tok, "message": "공유 주소를 만드는 중입니다… (10~30초)"})
        url = None
        t0 = time.time()
        for line in _proc.stdout:
            logf.write(line)
            logf.flush()
            m = URL_RE.search(line)
            if m and not url:
                url = m.group(0)
                _write({"status": "running", "pid": _proc.pid, "token": tok, "url": url,
                        "started_at": datetime.now().isoformat(timespec="minutes")})
                if log:
                    log(f"공유 시작: {url}")
            if not url and time.time() - t0 > 90:
                break
        if not url:
            raise RuntimeError("공유 주소를 받지 못했습니다. 회사 네트워크에서 막혀 있을 수 있습니다. (logs/share.log 참고)")
        # 프로세스가 끝나면(중지·끊김) 상태 정리
        _proc.wait()
        if _read().get("pid") == _proc.pid:
            _write({"status": "off", "message": "공유가 끊겼습니다. 다시 만들어 주세요."})
    except Exception as e:
        try:
            if _proc and _proc.poll() is None:
                _proc.kill()
        except Exception:
            pass
        _write({"status": "error", "message": str(e)})
        if log:
            log(f"공유 실패: {e}")


def start(port: int, log=None) -> dict:
    with _lock:
        st = status()
        if st["status"] in ("running", "starting"):
            return st
        _write({"status": "starting", "message": "준비 중…"})
        threading.Thread(target=_run, args=(port, log), daemon=True).start()
    time.sleep(0.3)
    return status()


def stop() -> dict:
    global _proc
    d = _read()
    pid = d.get("pid")
    _write({"status": "off", "message": ""})
    try:
        if _proc and _proc.poll() is None:
            _proc.terminate()
        elif pid and _alive(pid):
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=0x08000000)
            else:
                os.kill(pid, 15)
    except Exception:
        pass
    return status()
