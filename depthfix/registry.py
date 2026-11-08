"""Deprecation registry: bundled entries plus optional user-supplied files."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import models

DEFAULT_REGISTRY = Path(__file__).resolve().parent / "registries" / "deprecated.json"


def normalize_name(eco: str, name: str) -> str:
    name = name.strip()
    if eco == models.PYPI:
        return re.sub(r"[-_.]+", "-", name).lower()
    if eco == models.CARGO:
        return re.sub(r"[-_]+", "-", name).lower()
    return name.lower()


@dataclass(frozen=True)
class RegistryEntry:
    ecosystem: str
    name: str
    reason: str = ""
    replacement: Optional[str] = None
    suggest: Tuple[str, ...] = ()
    min_version: Optional[str] = None
    eol: Optional[str] = None

    @property
    def replacement_key(self) -> Optional[str]:
        if self.replacement:
            return normalize_name(self.ecosystem, self.replacement)
        return None


class Registry:
    """Keyed lookup table of (ecosystem, normalised name) -> entry."""

    def __init__(self) -> None:
        self._entries: Dict[Tuple[str, str], RegistryEntry] = {}
        self.sources: List[str] = []
        self.missing = 0

    # -- building ----------------------------------------------------------
    def add_file(self, path: Path, required: bool = False) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            if required:
                raise SystemExit(f"depthfix: cannot read registry '{path}': {exc}")
            self.sources.append(f"{path} (unreadable: {exc})")
            self.missing += 1
            return False
        count = 0
        for entry in data.get("entries", []):
            try:
                self.add_entry(entry)
                count += 1
            except (KeyError, TypeError):
                continue
        self.sources.append(f"{path} ({count} entries)")

    def add_entry(self, raw: dict) -> None:
        ecosystem = str(raw["ecosystem"]).lower()
        name = normalize_name(ecosystem, str(raw["name"]))
        if ecosystem not in (models.NPM, models.PYPI, models.GO, models.CARGO):
            return
        suggest = raw.get("suggest") or []
        entry = RegistryEntry(
            ecosystem=ecosystem,
            name=name,
            reason=str(raw.get("reason") or ""),
            replacement=raw.get("replacement"),
            suggest=tuple(str(s) for s in suggest),
            min_version=raw.get("min_version"),
            eol=raw.get("eol"),
        )
        # later files override the bundled registry on the same key
        self._entries[(ecosystem, name)] = entry

    def load_default(self) -> None:
        self.add_file(DEFAULT_REGISTRY, required=True)

    # -- querying ----------------------------------------------------------
    def lookup(self, eco: str, name: str) -> Optional[RegistryEntry]:
        return self._entries.get((eco, normalize_name(eco, name)))

    def __len__(self) -> int:
        return len(self._entries)
