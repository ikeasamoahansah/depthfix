"""Apply fixes: splice per-file edits into manifest files."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .models import Edit, Finding


def edits_of(finding: Finding) -> List[Edit]:
    """Concrete edits a fixed finding contributes (name swap and/or spec bump)."""
    dep = finding.dep
    out: List[Edit] = []
    if finding.new_name and dep.name_edit and finding.new_name != dep.name:
        out.append(Edit(dep.name_edit.start, dep.name_edit.end, finding.new_name))
    if finding.new_spec and dep.spec_edit:
        out.append(Edit(dep.spec_edit.start, dep.spec_edit.end, finding.new_spec))
    return out


def fixes_for_paths(findings: List[Finding]) -> Set[Path]:
    return {f.dep.file for f in findings
            if f.fixed and (f.new_name or f.new_spec)}


def apply_fixes(findings: List[Finding], dry_run: bool = False) -> Dict[Path, str]:
    """Rewrite manifests in place. Returns {path: new_text} (empty on dry run)."""
    changed: Dict[Path, str] = {}
    per_file: Dict[Path, List[Finding]] = {}
    for f in findings:
        if f.new_name or f.new_spec:
            per_file.setdefault(f.dep.file, []).append(f)

    for path, fs in sorted(per_file.items()):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            for f in fs:
                f.fixed = False
                f.note = f"cannot rewrite file: {exc}"
            continue

        edits: List[Edit] = []
        planned_fixes: List[Finding] = []
        for f in fs:
            planned = edits_of(f)
            if not planned:
                f.note = ((f.note + "; " if f.note else "") +
                          "planned fix could not be located precisely in the "
                          "manifest — edit manually")
                continue
            ok = True
            for e in planned:
                if _overlaps_any(edits, e):
                    ok = False
                    break
                edits.append(e)
            if ok:
                planned_fixes.append(f)
            else:
                f.fixed = False
                f.note = "conflicting overlapping edits; fix skipped"

        new_text = _splice(text, sorted(edits, key=lambda e: e.start, reverse=True))
        if not dry_run:
            try:
                path.write_text(new_text, encoding="utf-8")
            except OSError as exc:
                for f in fs:
                    f.fixed = False
                    f.note = f"cannot rewrite file: {exc}"
                continue
            for f in planned_fixes:
                f.fixed = True
        if not dry_run:
            changed[path] = new_text
    return changed


def _overlaps_any(edits: List[Edit], candidate: Edit) -> bool:
    return any(candidate.start < e.end and e.start < candidate.end for e in edits)


def _splice(text: str, edits_desc: List[Edit]) -> str:
    result = text
    for e in edits_desc:
        result = result[:e.start] + e.text + result[e.end:]
    return result
