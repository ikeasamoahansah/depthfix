"""go.mod scanner (Go modules)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from .. import models
from ..models import Dependency

# module paths must contain a dot (except the stdlib pseudo-module), and
# version literals always carry the leading 'v'
_ENTRY = re.compile(
    r"^\s*(?P<mod>[A-Za-z0-9._\-/]+\.[A-Za-z0-9._\-/]+(?:/v[2-9])?)\s+"
    r"(?P<ver>v[A-Za-z0-9.\-+]+)\s*(?P<comment>//.*)?$"
)
_BLOCK_OPEN = re.compile(r"^\s*(require|replace|exclude|retract)\s*\(\s*$")
_BLOCK_CLOSE = re.compile(r"^\s*\)\s*$")
_GO_DIRECTIVE = re.compile(r"^\s*(go|module|toolchain)\s")


def scan_file(path: Path) -> List[Dependency]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out: List[Dependency] = []
    pos = 0
    block_kind: Optional[str] = None

    for raw in text.splitlines(keepends=True):
        line_no = text.count("\n", 0, pos) + 1
        line = raw.rstrip("\n")

        if block_kind:
            if _BLOCK_CLOSE.match(line):
                block_kind = None
            elif block_kind == "require":
                dep = _entry(path, text, pos, line_no, line)
                if dep:
                    out.append(dep)
        else:
            opened = _BLOCK_OPEN.match(line)
            if opened:
                block_kind = opened.group(1)
            elif line.strip().startswith("require "):
                dep = _entry(path, text, pos, line_no, line)
                if dep:
                    out.append(dep)
        pos += len(raw)
    return out


def _entry(path: Path, text: str, pos: int, line_no: int,
           line: str) -> Optional[Dependency]:
    if line.strip().startswith("//") or _GO_DIRECTIVE.match(line):
        return None
    m = _ENTRY.match(line)
    if not m:
        return None
    comment = m.group("comment") or ""
    indirect = "// indirect" in comment

    mod = m.group("mod")
    ver = m.group("ver")
    # spans may overflow into the comment on '<mod> <v1.2 // comment'
    if comment:
        overflow = pos + m.start("comment") - (pos + m.start("ver"))
        if 0 <= overflow < len(ver):
            ver = ver[:overflow].rstrip()

    section = "require (indirect)" if indirect else "require"
    return Dependency(
        ecosystem=models.GO,
        name=mod.strip().lower(),
        spec=ver.strip(),
        file=path,
        line=line_no,
        section=section,
        name_edit=models.Edit(pos + m.start("mod"), pos + m.start("mod") + len(mod.strip()), mod.strip()),
        spec_edit=models.Edit(pos + m.start("ver"), pos + m.start("ver") + len(ver.strip()), ver.strip()),
    )
