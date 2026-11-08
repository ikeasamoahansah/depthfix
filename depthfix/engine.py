"""Engine: drive scanning, evaluation, fixing and installation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import analysis, fixer, installers, models, scanners
from .models import Dependency, Finding, STATUS_DEPRECATED, STATUS_OUTDATED
from .net import Net
from .registry import Registry
from .upstream import Upstream


@dataclass
class Options:
    offline: bool = False
    refresh: bool = False
    dry_run: bool = False          # report only, no writes / installs
    check: bool = False            # --check implies dry run + findings exit code
    install: bool = True           # run package managers after rewriting
    verbose: bool = False
    registries: List[str] = field(default_factory=list)


@dataclass
class Result:
    roots: List[str] = field(default_factory=list)
    files_scanned: List[str] = field(default_factory=list)
    deps_scanned: int = 0
    warnings: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    fixed_files: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    install_ok: int = 0
    install_fail: int = 0

    def counts(self) -> dict:
        def has_fix(f: Finding) -> bool:
            return bool(f.new_spec) or bool(f.new_name and f.new_name != f.dep.name)

        applied = sum(1 for f in self.findings if f.fixed and has_fix(f))
        planned = sum(1 for f in self.findings if not f.fixed and has_fix(f))
        attention = sum(1 for f in self.findings if not has_fix(f))
        return {
            "deprecated": sum(1 for f in self.findings if f.status == STATUS_DEPRECATED),
            "outdated": sum(1 for f in self.findings if f.status == STATUS_OUTDATED),
            "fixed": applied,
            "planned": planned,
            "not_fixed": attention,
        }


def run(roots: List[Path], opts: Options) -> Result:
    registry = Registry()
    registry.load_default()
    for path in opts.registries:
        registry.add_file(Path(path))

    net = Net(offline=opts.offline, refresh=opts.refresh, verbose=opts.verbose)
    upstream = Upstream(net)

    result = Result(roots=[str(r) for r in roots])
    if registry.missing:
        result.warnings.append("one or more --registry files could not be read")

    manifests: List[Path] = []
    for root in roots:
        manifests.extend(scanners.find_manifests(root))

    deps: List[Dependency] = []
    for path in manifests:
        try:
            found = scanners.scan_file(path)
        except Exception as exc:  # noqa: BLE001 - per-file errors shouldn't kill a scan
            result.warnings.append(f"skipped {path}: {exc}")
            continue
        result.files_scanned.append(str(path))
        deps.extend(found)
    result.deps_scanned = len(deps)
    result.warnings.extend(net.warnings)

    findings: List[Finding] = []
    lock_cache: dict = {}
    for dep in deps:
        lock = None
        if dep.ecosystem == "cargo":
            if dep.file not in lock_cache:
                lock_cache[dep.file] = analysis.cargo_lock_versions(dep.file)
            lock = lock_cache[dep.file]
        try:
            finding = analysis.evaluate(dep, registry, upstream, lock)
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"evaluation failed for {dep.name}: {exc}")
            continue
        if finding and finding.status == models.STATUS_UNRESOLVED:
            result.warnings.append(f"{dep.file}:{dep.line} {dep.name}: {finding.reason}")
        elif finding:
            findings.append(finding)

    result.findings = [f for f in findings
                       if f.status in (STATUS_DEPRECATED, STATUS_OUTDATED)]

    write_mode = not (opts.dry_run or opts.check)
    changed = fixer.apply_fixes(result.findings, dry_run=not write_mode)
    if write_mode:
        result.fixed_files = sorted(str(p) for p in changed)

    if write_mode and opts.install and changed:
        plans = installers.plan_for(list(changed), result.findings)
        ok, fail = installers.run_plans(plans, verbose=opts.verbose)
        result.install_ok, result.install_fail = ok, fail
        for directory, cmds in plans:
            for cmd in cmds:
                result.commands.append(f"({directory}) {' '.join(cmd)}")
    return result
