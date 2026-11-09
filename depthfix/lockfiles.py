"""Installed-version resolution from JavaScript lockfiles.

Parses the three major lockfile formats into a flat
``package name -> resolved version`` map:

* `package-lock.json` / `npm-shrinkwrap.json`  (npm v1 and v2/v3 layouts)
* `pnpm-lock.yaml`                             (pnpm 7/8/9)
* `yarn.lock`                                  (yarn 1 "classic")

Only registry installs are considered; tarball, git, link and file
entries are skipped — they are handled as exotic specifiers by analysis.
The parsers are deliberately regex/JSON based (no yaml dependency) and
tolerant: unparsable sections simply yield no entry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

NPM_LOCKS = ("package-lock.json", "npm-shrinkwrap.json")

_YARN_KEY = re.compile(r"^[^:\s].*:\s*$")
_YARN_VERSION = re.compile(r'^\s*version:?\s*"?([^"\s]+)"?\s*$')
_PNPM_PKG = re.compile(
    r"^\s*/?['\"]?((?:@[\w.~\-]+/)?[\w.~\-]+)@(?P<ver>[^ )('\"]+)(?:\([^)]*\))*['\"]?:\s*$")
_YARN_NPM_SPEC = re.compile(r"^(?P<name>.+?)@npm:")


def load(directory: Path) -> Dict[str, str]:
    """Versions map for the project in `directory` ({} if no lockfiles)."""
    for name in NPM_LOCKS:
        path = directory / name
        if path.exists():
            got = npm(path)
            if got:
                return got
    for name, fn in (("yarn.lock", yarn), ("pnpm-lock.yaml", pnpm)):
        path = directory / name
        if path.exists():
            got = fn(path)
            if got:
                return got
    return {}


def _norm(name: str) -> str:
    return name.strip().lower()


# ---------------------------------------------------------------------------
# package-lock.json / npm-shrinkwrap.json
# ---------------------------------------------------------------------------

def npm(path: Path) -> Dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    if isinstance(data.get("packages"), dict):          # npm v2/v3 layout
        packages: Dict[str, dict] = data["packages"]
        for key in sorted(packages, key=len):           # shallow wins
            info = packages[key] or {}
            version = info.get("version")
            if not isinstance(version, str):
                continue
            if key == "" or info.get("link"):
                continue
            if "node_modules/" not in key:              # workspaces, roots
                continue
            name = key.rsplit("node_modules/", 1)[-1]
            if not name or name.count("/") > 1:         # workspace deps etc.
                continue
            out.setdefault(_norm(name), version)
    elif isinstance(data.get("dependencies"), dict):    # npm v1 layout
        out = _npm_v1(data["dependencies"])
    return out


def _npm_v1(tree: dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, info in tree.items():
        if not isinstance(info, dict):
            continue
        version = info.get("version")
        if isinstance(version, str) and "://" not in version.split("@")[-1]:
            out.setdefault(_norm(name), version)
        if isinstance(info.get("dependencies"), dict):
            for k, v in _npm_v1(info["dependencies"]).items():
                out.setdefault(k, v)
    return out


# ---------------------------------------------------------------------------
# yarn.lock (yarn 1)
# ---------------------------------------------------------------------------

def yarn(path: Path) -> Dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}

    out: Dict[str, str] = {}
    pending: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if pending:
            m = _YARN_VERSION.match(line)
            if m:
                for name in pending:
                    out.setdefault(_norm(name), m.group(1))
                pending = []
                continue
            if _YARN_KEY.match(line):
                pending = _pending_from(line)
            elif not line.startswith((" ", "\t")):
                pending = []                       # entry ended unexpectedly
            continue
        if _YARN_KEY.match(line):
            pending = _pending_from(line)
    return out


def _pending_from(line: str) -> List[str]:
    """'"a@^1.0", "b@^2.0":' -> ['a', 'b'] (empty list if unparsable)."""
    raw = line.strip().rstrip(":").strip()
    names = [_yarn_name(t) for t in raw.split(",")]
    return names if names and all(names) else []


def _yarn_name(token: str) -> str:
    """'left-pad@^1.0.0' / '@babel/core@^7.0' / 'pkg@npm:^1' -> package name."""
    token = token.strip().strip('"')
    m = _YARN_NPM_SPEC.match(token)                 # yarn berry 'pkg@npm:^1'
    if m:
        token = m.group("name")
    at = token.rfind("@")
    if at == 0:                                     # '@scope' alone: malformed
        at = token.find("@", 1)
    return token[:at] if at > 0 else token


# ---------------------------------------------------------------------------
# pnpm-lock.yaml
# ---------------------------------------------------------------------------

def pnpm(path: Path) -> Dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}

    out: Dict[str, str] = {}
    for line in lines:
        m = _PNPM_PKG.match(line)
        if not m:
            continue
        ver = m.group("ver")
        if not ver[0].isdigit():            # tarball / git / file installs
            continue
        out.setdefault(_norm(m.group(1)), ver)
    return out
