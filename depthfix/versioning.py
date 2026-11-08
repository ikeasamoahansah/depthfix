"""Tolerant, dependency-free version parsing, comparison and spec matching.

Covers the pragmatic subset depthfix needs:

* semver-ish versions (npm, cargo, go) and PEP 440-lite (pypi)
* specs: exact pins, caret ``^``, tilde ``~``, compatible-release ``~=``,
  comparators ``>= > <= < != ==``, x-ranges (``1.x``, ``1.*``, ``==1.2.*``),
  ``||`` alternates, ``a - b`` ranges and comma-separated PEP 440 clauses.

Everything is best-effort: exotic specs simply fail to match and the
dependency is reported as unresolvable rather than mis-fixed.
"""

from __future__ import annotations

import re
from functools import cmp_to_key
from typing import Iterable, List, Optional, Sequence, Tuple

_TOKEN_RE = re.compile(r"\d+|[A-Za-z]+")

Comparators = List[Tuple[str, str]]


# ---------------------------------------------------------------------------
# Version parsing / comparison
# ---------------------------------------------------------------------------

def _clean(v: str) -> str:
    """Normalise a version string for comparison."""
    v = v.strip()
    if len(v) > 1 and v[0] in "vV" and v[1].isdigit():
        v = v[1:]
    # strip build metadata ('+build' / '+incompatible')
    return v.split("+", 1)[0]


def _tokens(v: str) -> List[Tuple[int, object]]:
    out: List[Tuple[int, object]] = []
    for t in _TOKEN_RE.findall(v):
        out.append((1, int(t)) if t.isdigit() else (0, t.lower()))
    return out


def compare(a: str, b: str) -> int:
    """Compare two version strings. Returns <0, 0 or >0."""
    ta, tb = _tokens(_clean(a)), _tokens(_clean(b))
    for i in range(max(len(ta), len(tb))):
        x = ta[i] if i < len(ta) else None
        y = tb[i] if i < len(tb) else None
        if x == y:
            continue
        if x is None:
            if y[0] == 0:                      # '1.2' vs '1.2.0-rc': b smaller
                return 1
            if y[1] == 0:                      # zero padding: keep scanning
                continue
            return -1                          # numeric continuation: b bigger
        if y is None:
            if x[0] == 0:
                return -1
            if x[1] == 0:
                continue
            return 1
        return -1 if x < y else 1
    return 0


def sort_versions(versions: Sequence[str]) -> List[str]:
    """Return versions ordered lowest-to-highest."""
    return sorted(versions, key=cmp_to_key(compare))


def is_prerelease(v: str) -> bool:
    return any(t.isdigit() is False for t in _TOKEN_RE.findall(_clean(v)))


def _segments(v: str, minimum: int = 3) -> List[int]:
    segs: List[int] = []
    for part in _clean(v).split("."):
        m = re.match(r"(\d+)", part)
        segs.append(int(m.group(1)) if m else 0)
    while len(segs) < minimum:
        segs.append(0)
    return segs


def _bump(segs: List[int], k: int) -> str:
    out = list(segs[: max(len(segs), k + 1)])
    while len(out) < k + 1:
        out.append(0)
    out[k] += 1
    for j in range(k + 1, len(out)):
        out[j] = 0
    return ".".join(str(x) for x in out)


def floor_version(spec: Optional[str]) -> Optional[str]:
    """Extract a representative version from a spec ('^4.17.20' -> '4.17.20')."""
    if not spec:
        return None
    m = re.search(r"v?(\d+(?:\.\d+)*)", spec)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Spec matching
# ---------------------------------------------------------------------------

def satisfies(spec: Optional[str], version: str, kind: str = "npm") -> bool:
    """Does `version` satisfy the dependency spec `spec` of ecosystem `kind`?"""
    if kind == "go":
        return not spec or compare(spec, version) == 0
    if not spec or not spec.strip():
        return True
    s = spec.strip()
    if s in ("*", "latest"):
        return True
    return any(_clause(c.strip(), version, kind) for c in s.split("||"))


def _clause(clause: str, version: str, kind: str) -> bool:
    if not clause:
        return True
    m = re.match(r"^v?[\w.\-]+\s+-\s+v?[\w.\-]+$", clause)
    if m:  # "1.2 - 1.6" inclusive range
        lo, hi = re.split(r"\s+-\s+", clause, maxsplit=1)
        return _op(">=", version, lo, kind) and _op("<=", version, hi)
    if kind == "pypi":
        return all(_op_single(p.strip(), version, kind)
                   for p in clause.split(",") if p.strip())
    comps = _npm_comparators(clause, kind)
    if comps is None:
        return False
    return all(_op(op, version, want, kind) for op, want in comps)


def _npm_comparators(clause: str, kind: str) -> Optional[Comparators]:
    """Split an npm/cargo clause into (op, version) comparators."""
    out: Comparators = []
    for m in re.finditer(r"(~=|>=|<=|!=|==|>|<|~|\^|=)?\s*v?(\d[A-Za-z0-9._\-]*|\*)",
                         clause):
        op, ver = m.group(1), m.group(2)
        if ver in ("*", ""):
            if m.group(0).strip() == "*":
                out.append(("=", "*"))
            continue
        if not op:
            op = "^" if kind == "cargo" else "="
        out.append((op, ver))
    return out or None


def _op_single(token: str, version: str, kind: str) -> bool:
    comps = _npm_comparators(token, kind) if kind != "pypi" else None
    if kind == "pypi":
        m = re.match(r"^(===|~=|==|!=|>=|<=|>|<)\s*v?(\d[A-Za-z0-9._\-]*|\*)", token)
        if not m:
            return False
        comps = [(m.group(1), m.group(2))]
    if comps is None:
        return False
    for op, want in comps:
        if op == "=" and kind == "pypi":
            op = "=="
        if not _op(op, version, want, kind):
            return False
    return True


def _op(op: str, version: str, want: str, kind: str,
        allow_pre: bool = False) -> bool:
    if want == "*" or want == "":
        return op != "!="
    # a prerelease candidate never satisfies a comparator whose target carries
    # no prerelease itself (semver / PEP 440 resolve behaviour) — except for
    # upper bounds of caret/tilde/wildcard ranges where opt-in was granted
    if (not allow_pre and _has_pre(version) and not _has_pre(want)
            and op != "!="):
        return False

    want_clean = _clean(want)

    # wildcards: "1.x" / "1.*" / "==1.2.*" — segments before the first
    # wildcard are fixed; the wildcard segment and everything after is free
    wild_idx = next((i for i, s in enumerate(want_clean.split(".")[:4])
                     if s in ("x", "X", "*") or s == ""), None)
    if wild_idx is not None and op in ("=", "==", "^", "~"):
        if wild_idx == 0:
            return True
        lo_segs = _segments(re.sub(r"[xX*]+", "0", want_clean))
        lo = ".".join(str(x) for x in lo_segs)
        hi = _bump(lo_segs, wild_idx - 1)
        return _op(">=", version, lo, kind) and _op("<", version, hi, kind, allow_pre=True)

    if op == "~=":  # PEP 440 compatible release ~=X.Y.Z -> >=X.Y.Z <X.(Y+1).0
        seg = _segments(want_clean)
        k = 1 if len(want_clean.split(".")) >= 3 else 0
        return _op(">=", version, want_clean, kind) and \
            _op("<", version, _bump(seg, k), kind, allow_pre=True)
    if op == "^":  # caret: bump the first nonzero segment
        seg = _segments(want_clean)
        nonzero = next((i for i, v in enumerate(seg[:3]) if v), 0)
        return _op(">=", version, want_clean, kind) and \
            _op("<", version, _bump(seg, nonzero), kind, allow_pre=True)
    if op == "~":  # tilde: keep major.minor
        seg = _segments(want_clean)
        k = 1 if len([p for p in want_clean.split(".") if p.isdigit()]) >= 2 else 0
        return _op(">=", version, want_clean, kind) and \
            _op("<", version, _bump(seg, k), kind, allow_pre=True)

    c = compare(version, want_clean)
    if op in ("=", "==", "==="):
        return c == 0
    if op == "!=":
        return c != 0
    if op == ">":
        return c > 0
    if op == ">=":
        return c >= 0
    if op == "<":
        return c < 0
    if op == "<=":
        return c <= 0
    return False


def _has_pre(v: str) -> bool:
    return any(not t.isdigit() for t in _TOKEN_RE.findall(_clean(v)))


def best_match(candidates: Iterable[str], spec: Optional[str],
               kind: str = "npm") -> Optional[str]:
    """Pick the highest version satisfying `spec`, preferring stable releases."""
    cands = list(candidates)
    sat = [c for c in cands if satisfies(spec, c, kind)]
    if not sat:
        return None
    stable = [c for c in sat if not is_prerelease(c)] or sat
    return max(stable, key=cmp_to_key(compare))


def bump_spec(eco: str, spec: Optional[str], latest: str) -> str:
    """Build a new spec for the same package, preserving its operator style."""
    latest = latest.strip()
    if eco == "go":
        return latest if latest.startswith("v") else "v" + latest
    if eco == "pypi":
        m = re.match(r"^(===|~=|>=|<=|==|>|<)", (spec or "").strip())
        return (m.group(1) if m else "==") + latest
    # npm / cargo
    m = re.match(r"^(\^|~|>=|=|=~)", (spec or "").strip())
    prefix = m.group(1) if m else "^"
    return prefix + latest


def replacement_spec(eco: str, old_spec: Optional[str], latest: str) -> str:
    """Build a spec for a *different* package (a deprecated package's successor)."""
    latest = latest.strip()
    if eco == "go":
        return latest if latest.startswith("v") else "v" + latest
    if eco == "pypi":
        m = re.match(r"^(===|~=|>=|==)(\s*)", (old_spec or "").strip())
        op = m.group(1) if m else "=="
        return op + latest
    return "^" + latest
