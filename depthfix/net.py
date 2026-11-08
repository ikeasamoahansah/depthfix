"""Minimal HTTP JSON client with a disk cache, offline mode and timeouts."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

USER_AGENT = "depthfix/0.1.0 (+https://github.com/depthfix; dependency deprecation scanner)"
DEFAULT_TTL = 6 * 3600  # seconds


def default_cache_dir() -> Path:
    env = os.environ.get("DEPTHFIX_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "depthfix"


class Net:
    def __init__(self, offline: bool = False, refresh: bool = False,
                 ttl: int = DEFAULT_TTL, timeout: float = 15.0,
                 cache_dir: Optional[Path] = None, verbose: bool = False) -> None:
        self.offline = offline
        self.refresh = refresh
        self.ttl = ttl
        self.timeout = timeout
        self.cache_dir = cache_dir or default_cache_dir()
        self.verbose = verbose
        self.warnings: list[str] = []
        self.hits = 0          # served from cache
        self.fetches = 0       # live HTTP fetches
        self.failures = 0

    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        self.warnings.append(msg)
        if self.verbose:
            print(f"depthfix: [net] {msg}")

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def get_json(self, url: str) -> Optional[Any]:
        path = self._cache_path(url)
        cached = self._read_cache(path)

        if self.offline:
            return cached["data"] if cached else None
        if cached and not self.refresh and time.time() - cached["ts"] < self.ttl:
            self.hits += 1
            return cached["data"]

        data = self._fetch(url)
        if data is not None:
            self._write_cache(path, data)
            return data
        # graceful degradation: fall back to a stale cache entry
        if cached:
            self._log(f"using stale cache for {url}")
            return cached["data"]
        return None

    def _fetch(self, url: str) -> Optional[Any]:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self.fetches += 1
                payload = resp.read()
            return json.loads(payload.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001 - network errors are data-plane issues
            self.failures += 1
            self._log(f"fetch failed: {url} ({exc})")
            return None

    def _read_cache(self, path: Path) -> Optional[dict]:
        try:
            wrapped = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(wrapped, dict) and "ts" in wrapped and "data" in wrapped:
                return wrapped
        except (OSError, ValueError):
            pass
        return None

    def _write_cache(self, path: Path, data: Any) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"ts": time.time(), "data": data}),
                            encoding="utf-8")
        except OSError as exc:
            self._log(f"cache write failed: {exc}")
