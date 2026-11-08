"""Cargo.toml scanner (crates.io)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from .. import models
from ..models import Dependency

_SECTION = re.compile(r"^\s*\[\s*(.*?)\s*\]\s*$")


def _is_dep_section(header: str) -> bool:
    parts = [p.strip().strip('"\'') for p in header.split(".")]
    return bool(parts) and parts[-1] in (
        "dependencies", "dev-dependencies", "build-dependencies")


def scan_file(path: Path) -> List[Dependency]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out: List[Dependency] = []
    pos = 0
    section: Optional[str] = None

    for raw in text.splitlines(keepends=True):
        line_no = text.count("\n", 0, pos) + 1
        line = raw.rstrip("\n")
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            pos += len(raw)
            continue

        head = _SECTION.match(stripped)
        if head:
            section = head.group(1) if _is_dep_section(head.group(1)) else None
            pos += len(raw)
            continue

        if section and "=" in stripped and not stripped.startswith("["):
            dep = _entry(path, text, pos, line_no, line, section)
            if dep:
                out.append(dep)
        pos += len(raw)
    return out


def _entry(path: Path, text: str, pos: int, line_no: int, line: str,
           section: str) -> Optional[Dependency]:
    simple = re.match(r"^\s*(?P<name>[A-Za-z0-9_\-.]+)\s*=\s*\"(?P<spec>[^\"]*)\"", line)
    if simple:
        name, spec = simple.group("name"), simple.group("spec")
    else:
        m = re.match(
            r"^\s*(?P<name>[A-Za-z0-9_\-.]+)\s*=\s*\{[^}]*?version\s*=\s*\"(?P<spec>[^\"]*)\"",
            line)
        if not m:
            return None
        name, spec = m.group("name"), m.group("spec")

    name_start = pos + line.find(name)
    return Dependency(
        ecosystem=models.CARGO,
        name=re.sub(r"[-_]+", "-", name.strip()).lower(),  # cargo names are hyphen-normalised
        spec=spec or None,
        file=path,
        line=line_no,
        section=f"[{section}]",
        name_edit=models.Edit(name_start, name_start + len(name), name),
        spec_edit=models.Edit(pos + line.find(spec), pos + line.find(spec) + len(spec),
                              spec) if spec else None,
    )
