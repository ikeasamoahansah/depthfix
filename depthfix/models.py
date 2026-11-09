"""Shared data types for scanners, findings and fixes."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List

# ecosystems
NPM = "npm"
PYPI = "pypi"
GO = "go"
CARGO = "cargo"

STATUS_DEPRECATED = "deprecated"   # package or version line is deprecated/EOL
STATUS_OUTDATED = "outdated"       # newer supported version exists
STATUS_UNRESOLVED = "unresolved"   # registry could not tell supporting status


@dataclass
class Edit:
    """A replacement of text[start:end] with `text`."""
    start: int
    end: int
    text: str

    def overlaps(self, other: "Edit") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass
class Dependency:
    ecosystem: str
    name: str            # normalised name as found in the manifest
    spec: Optional[str]  # version spec as written, e.g. "^1.2.3" / "==2.01"
    file: Path
    line: int            # 1-based line number
    section: str = ""    # manifest section, e.g. "dependencies" / "[project]"
    name_edit: Optional[Edit] = None  # span of the bare name in the file
    spec_edit: Optional[Edit] = None  # span of the bare spec in the file
    exotic: bool = False  # URL/VCS/path/alias source, never rewritten


@dataclass
class Finding:
    dep: Dependency
    status: str
    reason: str = ""
    replacement: Optional[str] = None
    suggest: List[str] = field(default_factory=list)
    eol: Optional[str] = None
    latest: Optional[str] = None
    resolved: Optional[str] = None
    new_name: Optional[str] = None
    new_spec: Optional[str] = None
    fixed: bool = False
    note: Optional[str] = None

    @property
    def file(self) -> Path:
        return self.dep.file

    @property
    def line(self) -> int:
        return self.dep.line

    def to_dict(self) -> dict:
        d = {
            "file": str(self.dep.file),
            "line": self.line,
            "ecosystem": self.dep.ecosystem,
            "name": self.dep.name,
            "spec": self.dep.spec,
            "section": self.dep.section,
            "status": self.status,
            "reason": self.reason,
            "resolved_version": self.resolved,
            "latest_version": self.latest,
            "replacement": self.replacement,
            "suggest": list(self.suggest),
            "eol": self.eol,
            "fix": None,
            "fixed": self.fixed,
            "note": self.note,
        }
        if self.fixed:
            fix = {"new_spec": self.new_spec}
            if self.new_name:
                fix["new_name"] = self.new_name
            d["fix"] = fix
        return d
