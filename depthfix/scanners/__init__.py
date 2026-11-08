"""Manifest discovery + scanner dispatch."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..models import Dependency
from .golang import scan_file as scan_gomod
from .node import scan_file as scan_node
from .python_ import scan_pyproject, scan_requirements
from .rust import scan_file as scan_cargo

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "target",
    "dist", "build", "out", "__pycache__", ".venv", "venv", "env",
    "ENV", "virtualenv", ".tox", ".nox", "site-packages",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".hypothesis",
    ".coverage", "htmlcov", ".eggs", ".pixi", ".pdm-build",
    "__pypackages__", ".idea", ".vscode", "bower_components",
}

_EXACT: Dict[str, Callable] = {
    "package.json": scan_node,
    "go.mod": scan_gomod,
    "Cargo.toml": scan_cargo,
    "pyproject.toml": scan_pyproject,
}

_RE_PATTERNS: List[tuple] = [
    (re.compile(r"(?:requirements|constraints).*\.txt$", re.I), scan_requirements),
]


def handler_for(filename: str) -> Optional[Callable]:
    h = _EXACT.get(filename)
    if h:
        return h
    low = filename.lower()
    for pat, h in _RE_PATTERNS:
        if pat.match(low):
            return h
    return None


def find_manifests(root: Path) -> List[Path]:
    """All manifests under `root` (or `root` itself if it is a manifest file)."""
    if root.is_file():
        return [root]
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if handler_for(fn):
                out.append(Path(dirpath) / fn)
    return sorted(out)


def scan_file(path: Path) -> List[Dependency]:
    handler = handler_for(path.name)
    if not handler:
        return []
    return handler(path)
