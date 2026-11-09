"""package.json scanner (npm)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

from .. import models
from ..models import Dependency
from ..versioning import is_exotic_spec

SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")


def scan_file(path: Path) -> List[Dependency]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path.name}: {exc}") from exc
    out: List[Dependency] = []
    for section in SECTIONS:
        deps = data.get(section) or {}
        if not isinstance(deps, dict) or not deps:
            continue
        block = _find_block(text, section)
        for name, spec in deps.items():
            if not isinstance(spec, str):
                continue
            line = _line_of_block(text, block, name, spec)
            located = _locate(text, block, name) if block else None
            out.append(Dependency(
                ecosystem=models.NPM,
                name=name.strip().lower(),
                spec=spec or None,
                file=path,
                line=line,
                section=section,
                name_edit=located and located[0],
                spec_edit=located and located[1],
                exotic=bool(spec) and is_exotic_spec(models.NPM, spec),
            ))
    return out


def _find_block(text: str, section: str) -> Optional[Tuple[int, int]]:
    m = re.search(rf'"{re.escape(section)}"\s*:\s*\{{', text)
    if not m:
        return None
    start = m.end() - 1  # position of '{'
    depth, i, n = 1, start + 1, len(text)
    while i < n and depth:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return (start, i)


def _locate(text: str, block: Tuple[int, int],
            name: str) -> Optional[Tuple[object, object]]:
    lo, hi = block
    pat = re.compile(rf'"{re.escape(name)}"\s*:\s*"(?P<spec>[^"]*)"')
    m = pat.search(text, lo, hi)
    if not m:
        return None, None
    name_start = m.start(0) + 1  # inside the opening quote
    name_edit = models.Edit(name_start, name_start + len(name), name)
    spec_edit = models.Edit(m.start("spec"), m.end("spec"), m.group("spec"))
    return name_edit, spec_edit


def _line_of_block(text: str, block, name: str, spec: str) -> int:
    if block:
        lo, hi = block
        anchor = text.find(f'"{name}"', lo, hi)
        if anchor >= 0:
            return text.count("\n", 0, anchor) + 1
    return 1
