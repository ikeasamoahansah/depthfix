"""Turn scanned dependencies into findings and fix plans."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from . import lockfiles, models, versioning
from .models import Dependency, Finding
from .registry import Registry
from .upstream import Upstream


def evaluate(dep: Dependency, registry: Registry, upstream: Upstream,
             lock: Optional[Dict[str, str]] = None) -> Optional[Finding]:
    eco = dep.ecosystem
    dep.resolved = _resolved(eco, dep, upstream, lock)

    entry = registry.lookup(eco, dep.name)
    npm_msg: Optional[str] = None
    if eco == models.NPM and dep.resolved:
        npm_msg = upstream.npm_deprecated_message(dep.name, dep.resolved)

    latest = upstream.latest(eco, dep.name)
    replacement_latest = upstream.latest(eco, entry.replacement_key) \
        if entry and entry.replacement else None

    status, reason = _status(dep, entry, npm_msg, latest)

    if not status:
        return None

    finding = Finding(
        dep=dep, status=status, reason=reason,
        replacement=(entry.replacement if entry and entry.replacement else None),
        suggest=list(entry.suggest) if entry else [],
        eol=(entry.eol if entry else None),
        latest=latest, resolved=dep.resolved,
    )
    _plan_fix(finding, entry, latest, replacement_latest)
    return finding


def _status(dep: Dependency, entry, npm_msg, latest) -> tuple:
    if entry:
        if entry.replacement:
            msg = entry.reason or (f"'{dep.name}' is deprecated in the bundled registry;")
            return models.STATUS_DEPRECATED, msg
        if entry.min_version:
            if dep.resolved is None:
                return models.STATUS_UNRESOLVED, (
                    f"registry flags {dep.name} < {entry.min_version} as deprecated, "
                    "but the installed version could not be resolved")
            if versioning.compare(dep.resolved, entry.min_version) < 0:
                return models.STATUS_DEPRECATED, (
                    entry.reason or f"{dep.name} < {entry.min_version} is deprecated")
            # this specific version line is fine; fall through, may still be outdated
        else:
            msg = entry.reason or f"'{dep.name}' is deprecated"
            return models.STATUS_DEPRECATED, msg
    if npm_msg:
        return models.STATUS_DEPRECATED, (
            f"npm registry marks {dep.name}@{dep.resolved} as deprecated: {npm_msg}")

    if entry:  # entry had min_version and the resolved version is above it
        return "", ""
    if dep.resolved and latest and versioning.compare(dep.resolved, latest) < 0:
        return models.STATUS_OUTDATED, (
            f"{dep.resolved} is outdated; {latest} is available")
    return "", ""


def _plan_fix(finding: Finding, entry, latest, replacement_latest) -> None:
    dep = finding.dep
    eco = dep.ecosystem

    if dep.exotic or versioning.is_exotic_spec(eco, dep.spec):
        finding.note = (
            f"exotic specifier ({dep.spec or '<none>'}) — downloads from a URL, "
            "VCS or local path; rewrite postponed, migrate manually")
        return

    if finding.replacement:
        if replacement_latest:
            finding.new_name = finding.replacement
            if dep.spec or dep.spec_edit:
                finding.new_spec = versioning.replacement_spec(
                    eco, dep.spec, replacement_latest)
            finding.latest = replacement_latest
        else:
            finding.note = (
                f"cannot determine latest version of '{finding.replacement}' "
                "(offline mode or registry unreachable)")
        return

    # version-scoped deprecation (entry.min_version) or npm deprecation flag:
    # the package itself is alive, so bumping to its latest is the right fix.
    version_scoped = (entry is not None and entry.min_version is not None) or \
        (entry is None)  # npm deprecation flag without a bundled entry
    whole_death = (entry is not None and not entry.min_version)

    if not version_scoped and whole_death:
        if finding.suggest:
            finding.note = ("advisory only, no automatic migration — "
                            f"consider: {', '.join(finding.suggest)}")
        else:
            finding.note = ("deprecated package without a direct successor; "
                            "migrate manually")
        return

    if latest:
        if dep.spec is None:
            # unpinned dependency: pin it (zero-width insert) when the
            # manifest has an anchor point for the new spec
            if dep.spec_edit is not None:
                finding.new_spec = versioning.bump_spec(eco, None, latest)
            else:
                finding.note = ("unpinned dependency; nothing to rewrite — "
                                "pin a version to let depthfix manage upgrades")
        else:
            if dep.resolved and versioning.compare(latest, dep.resolved) <= 0 and \
                    finding.status == models.STATUS_DEPRECATED:
                finding.note = "already on the latest published version"
            else:
                finding.new_spec = versioning.bump_spec(eco, dep.spec, latest)
                if entry and entry.min_version and \
                        versioning.compare(latest, entry.min_version) < 0:
                    finding.note = (f"latest ({latest}) is still below the recommended "
                                    f"{entry.min_version}")
    else:
        finding.note = "no upstream version data available (offline mode?)"


def _resolved(eco: str, dep: Dependency, upstream: Upstream,
              lock: Optional[Dict[str, str]]) -> Optional[str]:
    if eco == models.NPM:
        locked = (lock or {}).get(dep.name)
        if locked and (not dep.spec or
                       versioning.satisfies(dep.spec, locked, models.NPM)):
            return locked
        versions = upstream.npm_versions(dep.name)
        resolved = versioning.best_match(versions, dep.spec, models.NPM)
        return resolved or locked or versioning.floor_version(dep.spec)
    if eco == models.CARGO and lock:
        locked = lock.get(dep.name)
        if locked:
            return locked
    if dep.spec:
        sm = re.match(r"^\s*(===|~=|==|>=|v)\s*([0-9][\w.\-+]*)", dep.spec)
        return (sm.group(2) if sm else None) or versioning.floor_version(dep.spec)
    return None


def lock_versions(manifest: Path, eco: Optional[str] = None) -> Dict[str, str]:
    """Parse the lockfile(s) next to `manifest` for installed versions.

    Cargo manifests consult Cargo.lock; npm manifests consult
    package-lock.json / npm-shrinkwrap.json / yarn.lock / pnpm-lock.yaml.
    """
    eco = eco or (
        models.CARGO if manifest.name == "Cargo.toml"
        else models.NPM if manifest.name == "package.json" else None)
    if eco == models.CARGO:
        return cargo_lock_versions(manifest)
    if eco == models.NPM:
        return lockfiles.load(manifest.parent)
    return {}


def cargo_lock_versions(cargo_toml: Path) -> Dict[str, str]:
    """Parse an adjacent Cargo.lock for the actually-pinned versions."""
    lockfile = cargo_toml.parent / "Cargo.lock"
    if not lockfile.exists():
        return {}
    try:
        text = lockfile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    versions: Dict[str, str] = {}
    for block in re.finditer(r"\[\[package\]\](.*?)(?=\[\[package\]\]|\Z)", text, re.S):
        body = block.group(1)
        nm = re.search(r'^name\s*=\s*"([^"]+)"', body, re.M)
        vm = re.search(r'^version\s*=\s*"([^"]+)"', body, re.M)
        if nm and vm:
            versions[re.sub(r"[-_]+", "-", nm.group(1)).lower()] = vm.group(1)
    return versions
