"""Console reporting: compiler-style output + JSON."""

from __future__ import annotations

import json
import os
import sys
from typing import List, Optional

from . import __version__
from .models import Finding, STATUS_DEPRECATED, STATUS_OUTDATED

_COLOR = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "red": "\x1b[31m",
    "yellow": "\x1b[33m",
    "green": "\x1b[32m",
    "cyan": "\x1b[36m",
}


def colors_enabled() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR", "") == ""


def paint(text: str, *codes: str) -> str:
    if not colors_enabled():
        return text
    return "".join(_COLOR.get(c, "") for c in codes) + text + _COLOR["reset"]


def _status_style(status: str, f: Finding) -> str:
    if status == STATUS_DEPRECATED:
        tag = "DEPRECATED" if not f.fixed else "DEPRECATED ✓ FIXED"
    elif status == STATUS_OUTDATED:
        tag = "OUTDATED ✓ FIXED" if f.fixed else "OUTDATED"
    else:
        tag = status.upper()
    return tag


def print_report(result, verbose: bool = False, out=sys.stdout,
                 note: str = "") -> None:
    p = lambda *a, **k: print(*a, **k, file=out)  # noqa: E731

    p(paint(f"depthfix {__version__} — deprecation registry & fixer", "bold"))
    roots = ", ".join(result.roots)
    p(f"scanning: {roots}" + (f"  [{note}]" if note else ""))

    findings: List[Finding] = sorted(result.findings,
                                     key=lambda f: (str(f.dep.file), f.dep.line))
    by_file = {}
    for f in findings:
        by_file.setdefault(str(f.dep.file), []).append(f)

    if not findings:
        p(paint("no deprecated or outdated dependencies found", "green"))

    for path, fs in by_file.items():
        p()
        p(paint(path, "bold"))
        for f in fs:
            dep = f.dep
            spec = f.dep.spec or "(unpinned)"
            status = _status_style(f.status, f)
            tag_color = "red" if f.status == STATUS_DEPRECATED else "yellow"
            head = f"  {dep.name} {spec}" + (f'  [{dep.section}]' if dep.section else "")
            p(f"  {paint(status, tag_color)} {paint(head, 'bold')} "
              f"{paint(f'(line {dep.line})', 'dim')}")
            reason = f.reason
            if f.eol:
                reason += f"  (EOL {f.eol})"
            if reason:
                p(f"      {reason}")
            planned = f.new_spec or (f.new_name and f.new_name != dep.name)
            if planned:
                arrow = f"fix:  {dep.name} "
                if f.new_name and f.new_name != dep.name:
                    arrow += f"→ {f.new_name}"
                    if f.new_spec:
                        arrow += ", "
                if f.new_spec:
                    arrow += f"'{spec}' → '{f.new_spec}'"
                marker = "✓ applied" if f.fixed else "planned"
                p(f"      {paint(f'{arrow}  [{marker}]', 'green' if f.fixed else 'cyan')}")
            elif f.note:
                p(f"      {paint(f.note, 'dim')}")
            elif f.suggest:
                p(f"      {paint('advisory only; consider: ' + ', '.join(f.suggest), 'dim')}")

    warnings = result.warnings
    if warnings:
        for w in warnings:
            p(paint(f"note: {w}", "dim"))

    c = result.counts()
    p()
    summary = (f"{c['deprecated']} deprecated · {c['outdated']} outdated · "
               f"{c['fixed']} filed/applied · {c['planned']} planned · "
               f"{c['not_fixed']} need attention")
    p(paint("summary: ", "bold") + summary)
    if result.fixed_files:
        p(f"rewrote {len(result.fixed_files)} manifest(s): "
          + ", ".join(result.fixed_files))
    if result.commands:
        p(f"install steps run: {result.install_ok} ok, {result.install_fail} failed")
    if result.install_fail:
        p(paint("re-run the failed installer commands manually.", "yellow"))


def to_json(result, mode: str) -> dict:
    return {
        "depthfix": __version__,
        "mode": mode,
        "roots": result.roots,
        "files_scanned": result.files_scanned,
        "dependencies_scanned": result.deps_scanned,
        "warnings": result.warnings,
        "findings": [f.to_dict() for f in
                     sorted(result.findings, key=lambda f: (str(f.dep.file), f.dep.line))],
        "files_rewritten": result.fixed_files,
        "install_commands": result.commands,
        "install_ok": result.install_ok,
        "install_fail": result.install_fail,
        "summary": result.counts(),
    }


def print_json(result, mode: str) -> None:
    print(json.dumps(to_json(result, mode), indent=2))
