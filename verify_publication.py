"""Verify the encrypted live payload actually served by Pages, without decrypting it."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import time
import urllib.error
import urllib.request


def verify(site_dir: Path, site_url: str, *, attempts: int = 6, delay: float = 5) -> None:
    expected = (site_dir / "d" / "live.bin").read_bytes()
    if not expected:
        raise ValueError("Built live payload is empty")
    digest = hashlib.sha256(expected).hexdigest()
    detail = "no response"
    for attempt in range(1, attempts + 1):
        url = f"{site_url.rstrip('/')}/d/live.bin?publication={digest}-{attempt}"
        request = urllib.request.Request(url, headers={
            "User-Agent": "go-monitor-publication-check", "Cache-Control": "no-cache",
        })
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                actual = response.read(len(expected) + 1)
                seen = {k: getattr(response, "headers", {}).get(k) for k in ("Last-Modified", "Age", "X-Cache", "X-Served-By")}
            if actual == expected:
                print(f"Published live payload verified: {digest[:12]}", flush=True)
                return
            detail = f"different payload ({hashlib.sha256(actual).hexdigest()[:12]}; expected {digest[:12]})"
            detail += f" {seen}"    # 원인 분석용: 옛 판이 중간 저장(CDN)인지 Pages 원본인지 구분
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            detail = type(error).__name__
        print(f"Waiting for Pages publication ({attempt}/{attempts}): {detail}", flush=True)
        if attempt < attempts:
            time.sleep(delay)
    raise RuntimeError(f"Pages did not serve the built live payload: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=Path("_site"))
    parser.add_argument("--site-url", required=True)
    parser.add_argument("--attempts", type=int, default=6)
    args = parser.parse_args()
    verify(args.site_dir, args.site_url, attempts=args.attempts)


if __name__ == "__main__":
    main()
