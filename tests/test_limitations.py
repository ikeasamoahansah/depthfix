"""Tests for the fixed known limitations.

1. lockfile-aware resolution for npm/pnpm/yarn (engine.analysis lock plumbing)
2. unpinned dependencies can now be pinned automatically
3. exotic specifiers (URL / VCS / local path / alias) surface a note and are
   never rewritten
4. PEP 508 environment markers survive PEP-bumps and package swaps
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from depthfix import analysis, registry, scanners, versioning
from depthfix.fixer import apply_fixes
from depthfix.models import Dependency, Edit, Finding


def _manifest(name, text):
    path = Path(tempfile.mkdtemp(prefix="depthfix-lim-")) / name
    path.write_text(text, encoding="utf-8")
    return path


def _registry():
    reg = registry.Registry()
    reg.load_default()
    return reg


class MarkerTests(unittest.TestCase):
    def test_bump_keeps_marker(self):
        self.assertEqual(
            versioning.bump_spec("pypi", ">=2 ; python_version < '3.11'", "2.5.0"),
            ">=2.5.0 ; python_version < '3.11'")
        self.assertEqual(versioning.bump_spec("pypi", ">=2", "2.5.0"), ">=2.5.0")

    def test_replacement_keeps_marker(self):
        self.assertEqual(
            versioning.replacement_spec(
                "pypi", "==2.6.1 ; python_version >= '3.0'", "4.4.0"),
            "==4.4.0 ; python_version >= '3.0'")
        self.assertEqual(
            versioning.replacement_spec("pypi", None, "4.4.0"), "==4.4.0")

    def test_pyproject_fix_preserves_marker(self):
        path = _manifest("pyproject.toml", """\
[project]
dependencies = [
    "tomli >=2 ; python_version < '3.11'",
]
""")
        dep = scanners.scan_file(path)[0]
        self.assertEqual(dep.name, "tomli")
        new_spec = versioning.bump_spec("pypi", dep.spec, "2.5.0")
        self.assertEqual(new_spec, ">=2.5.0 ; python_version < '3.11'")
        f = Finding(dep=dep, status="outdated", new_spec=new_spec)
        apply_fixes([f])
        self.assertEqual(path.read_text(), """\
[project]
dependencies = [
    "tomli >=2.5.0 ; python_version < '3.11'",
]
""")


class UnpinnedTests(unittest.TestCase):
    def test_requirements_unpinned_pinned_and_swapped(self):
        path = _manifest("requirements.txt", "pycrypto\nrequests>=2.6\n")
        dep = scanners.scan_file(path)[0]
        self.assertIsNone(dep.spec)
        self.assertIsNotNone(dep.spec_edit)
        f = Finding(dep=dep, status="deprecated", new_name="pycryptodome",
                    new_spec="==4.4.0")
        apply_fixes([f])
        self.assertEqual(path.read_text(), "pycryptodome==4.4.0\nrequests>=2.6\n")

    def test_pyproject_unpinned_pinned_and_swapped(self):
        path = _manifest("pyproject.toml",
                         '[project]\ndependencies = ["pycrypto"]\n')
        dep = scanners.scan_file(path)[0]
        self.assertIsNone(dep.spec)
        self.assertIsNotNone(dep.spec_edit)
        f = Finding(dep=dep, status="deprecated", new_name="pycryptodome",
                    new_spec="==4.4.0")
        apply_fixes([f])
        self.assertEqual(path.read_text(),
                         '[project]\ndependencies = ["pycryptodome==4.4.0"]\n')

    def test_npm_unpinned_pinned(self):
        path = _manifest("package.json", '{"dependencies": {"core-js": ""}}')
        dep = scanners.scan_file(path)[0]
        self.assertIsNone(dep.spec)
        self.assertIsNotNone(dep.spec_edit)
        f = Finding(dep=dep, status="deprecated", new_spec="^3.42.0")
        apply_fixes([f])
        self.assertEqual(path.read_text(), '{"dependencies": {"core-js": "^3.42.0"}}')


class LockResolutionTests(unittest.TestCase):
    """analysis._resolved prefers the actual lockfile-resolved version."""

    def _upstream(self, versions, latest):
        """Upstream stub answering everything from `versions`."""
        class _Net:
            warnings = []
        up = analysis.Upstream(_Net())
        with mock.patch.object(up, "npm_versions", return_value=versions), \
             mock.patch.object(up, "packument",
                               return_value={"dist-tags": {"latest": latest},
                                             "versions": {}}):
            with mock.patch.object(up, "latest", return_value=latest):
                yield up

    def test_lock_used_when_it_satisfies_spec(self):
        path = _manifest("package.json", "{}")
        dep = Dependency(ecosystem="npm", name="node-sass", spec="^7.0.0",
                         file=path, line=1)
        for up in self._upstream(["4.0.0", "7.9.0", "9.0.0"], "9.0.0"):
            got = analysis._resolved("npm", dep, up, {"node-sass": "7.9.0"})
        self.assertEqual(got, "7.9.0")

    def test_lock_wins_even_when_spec_allows_newer(self):
        path = _manifest("package.json", "{}")
        dep = Dependency(ecosystem="npm", name="node-sass", spec="^7.0.0",
                         file=path, line=1)
        for up in self._upstream(["7.0.0", "7.9.0"], "7.9.0"):
            got = analysis._resolved("npm", dep, up, {"node-sass": "7.0.0"})
        self.assertEqual(got, "7.0.0")          # lockfile is the installed truth

    def test_spec_cannot_be_satisfied_by_lock_portions(self):
        path = _manifest("package.json", "{}")
        dep = Dependency(ecosystem="npm", name="node-sass", spec="^9.0.0",
                         file=path, line=1)
        for up in self._upstream(["7.0.0", "7.9.0", "9.3.1"], "9.3.1"):
            got = analysis._resolved("npm", dep, up, {"node-sass": "7.0.0"})
        self.assertEqual(got, "9.3.1")          # stale lock ignored, spec wins

    def test_unpinned_dep_resolved_from_lockfile(self):
        path = _manifest("package.json", '{"dependencies": {"core-js": "*"}}')
        dep = Dependency(ecosystem="npm", name="core-js", spec=None,
                         file=path, line=1, spec_edit=Edit(34, 34, ""))
        for up in self._upstream(["1.0.0", "2.6.12", "3.42.0"], "3.42.0"):
            got = analysis._resolved("npm", dep, up, {"core-js": "2.6.12"})

        self.assertEqual(got, "2.6.12")         # the version actually installed
        reg = _registry()
        for up in self._upstream(["1.0.0", "2.6.12", "3.42.0"], "3.42.0"):
            f = analysis.evaluate(dep, reg, up, {"core-js": "2.6.12"})
        self.assertIsNotNone(f)
        self.assertEqual(f.status, "deprecated")
        self.assertEqual(f.resolved, "2.6.12")  # 2.x EOL entry fires
        self.assertEqual(f.new_spec, "^3.42.0")  # pinned to the supported line

    def test_deprecation_checked_against_locked_version_not_latest(self):
        path = _manifest("package.json", '{"dependencies": {"left-pad": "*"}}')
        dep = Dependency(ecosystem="npm", name="left-pad", spec=None,
                         file=path, line=1)
        # packument carries a `deprecated` flag on the *installed* version only
        for up in self._upstream(["1.0.0"], "1.0.0"):
            with mock.patch.object(up, "packument", return_value={"dist-tags":
                {"latest": "1.0.0"}, "versions": {"1.0.0": {"deprecated": "use it!"}}}):
                reg = _registry()
                f = analysis.evaluate(dep, reg, up, {"left-pad": "1.0.0"})
        self.assertIsNotNone(f)
        self.assertEqual(f.status, "deprecated")


class ExoticTests(unittest.TestCase):
    def test_exotic_shapes(self):
        for spec in ("npm:left-pad@^1", "github:foo/bar#v1", "git+https://x",
                     "https://x/y.tgz", "workspace:^1", "link:../x", "file:./x",
                     "foo-1.0.0.tgz"):
            self.assertTrue(versioning.is_exotic_spec("npm", spec), spec)
        for spec in ("^1.2.3", "~2.0.0", ">=1.0.0", "1.x", "*", "latest", ""):
            self.assertFalse(versioning.is_exotic_spec("npm", spec), spec)
        self.assertTrue(versioning.is_exotic_spec("pypi", "pkg @ https://x/y.tar.gz"))
        self.assertFalse(versioning.is_exotic_spec("pypi", "pkg>=1.0"))

    def test_scanner_flags_exotic_npm(self):
        path = _manifest(
            "package.json",
            '{"dependencies": {"do-not-use": "^1.0.0", "fs": "github:foo/bar#v1"}}')
        deps = scanners.scan_file(path)
        self.assertEqual({d.name: d.exotic for d in deps},
                         {"do-not-use": False, "fs": True})

    def test_exotic_not_rewritten(self):
        path = _manifest(
            "package.json",
            '{"dependencies": {"node-sass": "github:foo/node-sass#v7"}}')
        dep = scanners.scan_file(path)[0]
        reg = _registry()

        class _Net:
            warnings = []
        up = analysis.Upstream(_Net())
        with mock.patch.object(up, "npm_versions", return_value=["1.0.0"]), \
             mock.patch.object(up, "packument", return_value=None), \
             mock.patch.object(up, "latest", return_value="1.1.2"), \
             mock.patch.object(up, "npm_deprecated_message", return_value=None):
            f = analysis.evaluate(dep, reg, up, {})
        self.assertIsNotNone(f)
        self.assertIsNone(f.new_name)
        self.assertIsNone(f.new_spec)
        self.assertIn("exotic specifier", f.note)
        changed = apply_fixes([f])
        self.assertEqual(changed, {})
        self.assertEqual(
            path.read_text(),
            '{"dependencies": {"node-sass": "github:foo/node-sass#v7"}}')

    def test_cargo_git_source_flagged_exotic(self):
        path = _manifest("Cargo.toml", """\
[dependencies]
log = { git = "https://github.com/rust-lang/log" }
""")
        dep = scanners.scan_file(path)[0]
        self.assertTrue(dep.exotic)


if __name__ == "__main__":
    unittest.main()
