"""Python manifest scanners: requirements*.txt and pyproject.toml (pypi)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .. import models
from ..models import Dependency

try:  # Python 3.11+
    import tomllib as _toml
except ImportError:  # pragma: no cover
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ImportError:
        _toml = None  # type: ignore[assignment]

_NAME = r"[A-Za-z0-9][A-Za-z0-9._\-]*"

# requirements.txt style:  name[extra]==1.2.3  /  name>=1.0  /  name
_REQ_LINE = re.compile(
    rf"^(?P<indent>\s*)(?P<name>{_NAME})(?P<extra>\[[^\]]*\])?(?P<pre>\s*)"
    r"(?P<spec>(?:===|~=|!=|>=|<=|==|>|<)\s*[\w.\-*,]*|)(?P<rest>.*)$"
)
# PEP 508 entries inside pyproject strings: 'name[extra]==1.2.3 ; marker'
# plus direct references 'name @ https://...'
_DEP_STR = re.compile(
    rf"^(?P<name>{_NAME})(?P<extra>\[[^\]]*\])?\s*(?P<spec>[@=<>!~].*|)$"
)
_QUOTED = re.compile(r'"([^"\\\n]+)"')

_SKIP_PREFIXES = ("#", "-", "@", "//", "@")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


# ---------------------------------------------------------------------------
# requirements.txt
# ---------------------------------------------------------------------------

def scan_requirements(path: Path) -> List[Dependency]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out: List[Dependency] = []
    pos = 0
    for raw in text.splitlines(keepends=True):
        line_no = text.count("\n", 0, pos) + 1
        stripped = raw.strip()
        if not stripped or any(stripped.startswith(p) for p in _SKIP_PREFIXES):
            pos += len(raw)
            continue
        m = _REQ_LINE.match(raw)
        if m:
            name = m.group("name")
            rest = m.group("rest")
            extra = m.group("extra") or ""
            # direct reference 'pkg @ https://host/pkg-1.0.tar.gz' after the
            # name: never rewritten, advisory only
            exotic = bool(re.match(r"^\s*@\s*\S+", rest))
            spec: Optional[str] = ("@ " + rest.strip()[1:].strip()) if exotic \
                else (m.group("spec") or None)
            base = pos + len(m.group("indent"))
            name_edit = models.Edit(base, base + len(name), name)
            spec_edit = None
            if not exotic and spec:
                s_start = m.start("spec")
                # absolute offset: m.start() is relative to `raw`
                spec_edit = models.Edit(pos + s_start, pos + m.end("spec"), spec)
            elif not exotic:
                # unpinned dependency: anchor a zero-width insertion right
                # after the name (and any extras) so it can be pinned
                point = base + len(name) + len(extra)
                spec_edit = models.Edit(point, point, "")
            out.append(Dependency(
                ecosystem=models.PYPI,
                name=_norm(name),
                spec=spec,
                file=path,
                line=line_no,
                section=path.name,
                name_edit=name_edit,
                spec_edit=spec_edit,
                exotic=exotic,
            ))
        pos += len(raw)
    return out


# ---------------------------------------------------------------------------
# pyproject.toml
# ---------------------------------------------------------------------------

def scan_pyproject(path: Path) -> List[Dependency]:
    if _toml is None:
        raise RuntimeError(
            "scanning pyproject.toml requires Python 3.11+ or the 'tomli' package "
            "(pip install depthfix[toml])")
    text = path.read_text(encoding="utf-8", errors="replace")
    data = _toml.loads(text)

    wanted: List[Tuple[str, Optional[str], str, bool]] = []  # (name, spec, section, exotic)

    project = data.get("project") or {}
    for spec in project.get("dependencies") or []:
        _add_req_entry(wanted, spec, "[project].dependencies")
    group_deps = project.get("optional-dependencies") or {}
    if isinstance(group_deps, dict):
        for group, entries in group_deps.items():
            for spec in entries or []:
                _add_req_entry(wanted, spec, f"[project].optional-dependencies.{group}")

    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    for name, value in poetry.items():
        if name.lower() == "python":
            continue
        if isinstance(value, str):
            wanted.append((name, value or None, "[tool.poetry.dependencies]", False))
        else:
            info = value or {}
            if info.get("version"):
                wanted.append((name, info["version"], "[tool.poetry.dependencies]", False))
            elif any(k in info for k in ("git", "path", "url", "source")):
                # VCS / filesystem / alternative-index sources: advisory only
                wanted.append((name, None, "[tool.poetry.dependencies]", True))

    used: set = set()
    out: List[Dependency] = []
    for name, spec, section, is_exotic in wanted:
        located = _locate_quoted(text, name, spec, used)
        if not located:
            located = _locate_table(text, name, spec, used)
        line = (text.count("\n", 0, located[2]) + 1) if located else 1
        out.append(Dependency(
            ecosystem=models.PYPI,
            name=_norm(name),
            spec=spec,
            file=path,
            line=line,
            section=section,
            name_edit=located and located[0],
            spec_edit=located and located[1],
            exotic=is_exotic,
        ))
    return out


def _add_req_entry(wanted, entry: str, section: str) -> None:
    if not isinstance(entry, str):
        return
    m = _DEP_STR.match(entry.strip())
    if m:
        wanted.append((m.group("name"), m.group("spec") or None, section, False))


def _locate_quoted(text: str, name: str, spec: Optional[str], used: set):
    """Find the quoted string `"name<spec>"` corresponding to this dependency."""
    for m in _QUOTED.finditer(text):
        if m.start() in used:
            continue
        content = m.group(1)
        parsed = _DEP_STR.match(content.strip())
        if not parsed or _norm(parsed.group("name")) != _norm(name):
            continue
        spec_in = parsed.group("spec") or None
        if (spec or "") != (spec_in or ""):
            continue
        used.add(m.start())
        base = m.start(1)
        lead = len(content) - len(content.lstrip())
        n_start = base + lead
        name_edit = models.Edit(n_start, n_start + len(parsed.group("name")),
                                parsed.group("name"))
        spec_edit = None
        if spec_in:
            s_off = content.find(spec_in, n_start - base)
            spec_edit = models.Edit(base + s_off, base + s_off + len(spec_in), spec_in)
        elif not content_is_direct_ref(content):
            # unpinned entry like "requests": anchor a zero-width insertion
            # right after the name/extras so a fix can pin it
            extra_len = len(parsed.group("extra") or "")
            point = n_start + len(parsed.group("name")) + extra_len
            spec_edit = models.Edit(point, point, "")
        return name_edit, spec_edit, m.start()
    return None


def content_is_direct_ref(content: str) -> bool:
    return bool(re.match(r"^\s*\w[\w._\-]*\[[^\]]*\]?\s*@\s*\S+", content))


def _locate_table(text: str, name: str, spec: Optional[str], used: set):
    """Find table-form entries: `requests = ">=2.6"` / `requests = {version="…"}`."""
    pat = re.compile(
        rf"^\s*(?P<name>{re.escape(name)})\s*=\s*"
        rf"(?:\"(?P<spec>[^\"]*)\"|\{{[^}}\n]*?version\s*=\s*\"(?P<spec2>[^\"]*)\")",
        re.M)
    for m in pat.finditer(text):
        if m.start() in used:
            continue
        spec_in = m.group("spec") or m.group("spec2")
        if (spec or "") != (spec_in or ""):
            continue
        used.add(m.start())
        name_edit = models.Edit(m.start("name"), m.end("name"), m.group("name"))
        s_start = m.start("spec") if m.group("spec") else m.start("spec2")
        spec_edit = models.Edit(s_start, s_start + len(spec_in), spec_in) if spec_in else None
        return name_edit, spec_edit, m.start()
    return None
