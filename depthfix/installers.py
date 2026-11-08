"""Package-manager installer execution after manifest rewrites."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import models
from .models import Finding


def plan_for(changed_files: List[Path], findings: List[Finding]) -> List[Tuple[Path, List[List[str]]]]:
    """One (directory, commands) plan per touched manifest directory."""
    plans: Dict[Path, List[List[str]]] = {}
    go_gets: Dict[Path, List[List[str]]] = {}

    for path in changed_files:
        name = path.name.lower()
        directory = path.parent

        if name == "package.json":
            plans.setdefault(directory, []).append(_npm_cmd(directory))
        elif name.endswith(".txt"):
            plans.setdefault(directory, []).append(
                [sys.executable, "-m", "pip", "install", "-r", str(path)])
        elif name == "pyproject.toml":
            plans.setdefault(directory, []).append(_pyproject_cmd(directory))
        elif name == "cargo.toml":
            plans.setdefault(directory, []).append(["cargo", "update"])
        elif name == "go.mod":
            ok = _verify_tool("go", directory)
            if ok is True:
                for f in findings:
                    if f.dep.file == path and f.fixed:
                        spec = f.new_spec or ""
                        target = f.new_name or f.dep.name
                        if spec:
                            go_gets.setdefault(directory, []).append(
                                ["go", "get", f"{target}@{spec}"])
                go_gets.setdefault(directory, []).append(["go", "mod", "tidy"])
            elif isinstance(ok, str):
                _note_all(findings, path, ok)

    for directory, cmds in go_gets.items():
        plans.setdefault(directory, []).extend(cmds)
    return sorted(plans.items())


def _npm_cmd(directory: Path) -> List[str]:
    for lockfile, pm in (
        ("package-lock.json", "npm"),
        ("npm-shrinkwrap.json", "npm"),
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
    ):
        if (directory / lockfile).exists():
            return [pm, "install"]
    return ["npm", "install"]


def _pyproject_cmd(directory: Path) -> List[str]:
    if (directory / "uv.lock").exists():
        return ["uv", "sync"]
    if (directory / "poetry.lock").exists():
        return ["poetry", "install"]
    return [sys.executable, "-m", "pip", "install", "-e", "."]


def run_plans(plans, verbose: bool = False) -> Tuple[int, int]:
    """Execute install plans. Returns (succeeded, failed) command counts."""
    ok = fail = 0
    for directory, commands in plans:
        for cmd in commands:
            label = _label(cmd)
            print(f"  $ ({directory}) {label}")
            try:
                proc = subprocess.run(
                    cmd, cwd=str(directory), capture_output=True, text=True)
            except FileNotFoundError:
                print(f"    ✗ {cmd[0]} not found on PATH — skipped")
                fail += 1
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"    ✗ failed to run: {exc}")
                fail += 1
                continue
            if proc.returncode == 0:
                ok += 1
                print("    ✓ done")
                if verbose and proc.stdout.strip():
                    print("    " + proc.stdout.strip().replace("\n", "\n    "))
            else:
                fail += 1
                print(f"    ✗ exit {proc.returncode}")
                tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
                for line in tail:
                    print(f"    | {line}")
    return ok, fail


def _verify_tool(tool: str, directory: Path):
    """Return True if tool is usable, or a human reason string if not justified."""
    try:
        proc = subprocess.run(["which", tool], capture_output=True, text=True)
    except Exception:  # noqa: BLE001
        return f"{tool} unavailable — '{tool} mod tidy' must be re-run manually"
    if proc.returncode != 0:
        return f"{tool} not on PATH — go.sum must be updated manually (`go mod tidy`)"
    return True


def _label(cmd: List[str]) -> str:
    return " ".join(str(c) for c in cmd)


def _note_all(findings: List[Finding], path: Path, note: str) -> None:
    for f in findings:
        if f.dep.file == path:
            f.note = f"{f.note or ''} — {note}".strip()
