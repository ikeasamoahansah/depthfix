"""Upstream registry clients: latest versions + deprecation flags.

Providers (hybrid model — static registry first, live API second):

* npm      https://registry.npmjs.org        (per-version `deprecated` flags)
* pypi     https://pypi.org/pypi/<name>/json
* go       https://proxy.golang.org/<mod>/@latest
* cargo    https://crates.io/api/v1/crates/<name>
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List, Optional

from . import models


def go_path(module: str) -> str:
    """Go module proxy escaping of uppercase letters (case-encode)."""
    return re.sub(r"[A-Z]", lambda m: "!" + m.group(0).lower(), module)


class Upstream:
    def __init__(self, net) -> None:
        self.net = net
        self._latest: Dict[str, Optional[str]] = {}
        self._packuments: Dict[str, Optional[dict]] = {}

    # -- npm ---------------------------------------------------------------
    def packument(self, name: str) -> Optional[dict]:
        if name not in self._packuments:
            url = "https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@/%")
            self._packuments[name] = self.net.get_json(url)
        return self._packuments[name]

    def npm_versions(self, name: str) -> List[str]:
        pack = self.packument(name)
        return list(pack.get("versions", {}).keys()) if pack else []

    def npm_deprecated_message(self, name: str, version: str) -> Optional[str]:
        pack = self.packument(name)
        if not pack:
            return None
        info = (pack.get("versions") or {}).get(version) or {}
        msg = info.get("deprecated")
        return str(msg) if msg else None

    def npm_latest(self, name: str) -> Optional[str]:
        pack = self.packument(name)
        if not pack:
            return None
        tags = pack.get("dist-tags") or {}
        return tags.get("latest")

    # -- pypi --------------------------------------------------------------
    def pypi_latest(self, name: str) -> Optional[str]:
        url = "https://pypi.org/pypi/" + urllib.parse.quote(name) + "/json"
        data = self.net.get_json(url)
        if not data:
            return None
        info = data.get("info") or {}
        return info.get("version")

    # -- go ----------------------------------------------------------------
    def go_latest(self, module: str) -> Optional[str]:
        url = ("https://proxy.golang.org/" +
               urllib.parse.quote(go_path(module), safe="/!") + "/@latest")
        data = self.net.get_json(url)
        if not data:
            return None
        version = data.get("Version")
        return version.split("+", 1)[0] if version else None

    # -- cargo ----------------------------------------------------------------
    def cargo_latest(self, name: str) -> Optional[str]:
        url = "https://crates.io/api/v1/crates/" + urllib.parse.quote(name, safe="")
        data = self.net.get_json(url)
        if not data:
            return None
        crate = data.get("crate") or {}
        return crate.get("max_stable_version") or crate.get("max_version")

    # -- dispatch ----------------------------------------------------------
    def latest(self, eco: str, name: str) -> Optional[str]:
        key = (eco, name)
        if key not in self._latest:
            if eco == models.NPM:
                self._latest[key] = self.npm_latest(name)
            elif eco == models.PYPI:
                self._latest[key] = self.pypi_latest(name)
            elif eco == models.GO:
                self._latest[key] = self.go_latest(name)
            elif eco == models.CARGO:
                self._latest[key] = self.cargo_latest(name)
            else:
                self._latest[key] = None
        return self._latest[key]
