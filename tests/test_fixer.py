import shutil
import tempfile
import unittest
from pathlib import Path

from depthfix import scanners
from depthfix.fixer import apply_fixes
from depthfix.models import Finding

# ---------------------------------------------------------------------------
# scanners
# ---------------------------------------------------------------------------

SAMPLES = {
    "package.json": """{
  "name": "x",
  "dependencies": {
    "node-sass": "^7.0.3"
  },
  "devDependencies": {
    "lodash": "~4.17.0"
  }
}""",
    "requirements.txt": """\
# comment
-r base.txt
pycrypto==2.6.1
lxml >= 2.0
numpy
""",
    "go.mod": """module example.com/demo

go 1.21

require (
\tgithub.com/dgrijalva/jwt-go v3.2.0+incompatible
\tgopkg.in/yaml.v3 v3.0.1 // indirect
)

require github.com/pkg/errors v0.9.1 // indirect
""",
    "Cargo.toml": """\
[package]
name = "demo"
version = "0.1.0"

[dependencies]
error-chain = "0.12.4"
serde = { version = "1.0", features = ["derive"] }
log = "0.4"
""",
    "pyproject.toml": """\
[project]
dependencies = [
    "pycrypto==2.6.1",
]

[tool.poetry.dependencies]
python = "^3.10"
requests = ">=2.6"
""",
}


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="depthfix-test-"))
        for name, text in SAMPLES.items():
            (self.dir / name).write_text(text, encoding="utf-8")
        # excluded dirs must not be scanned
        bad = self.dir / "node_modules" / "pkg"
        bad.mkdir(parents=True)
        (bad / "package.json").write_text('{"dependencies": {"left-pad": "1.0.0"}}',
                                          encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _deps_for(self, name):
        return {(d.name, d.spec): d for d in scanners.scan_file(self.dir / name)}

    def test_discovery_and_pruning(self):
        found = scanners.find_manifests(self.dir)
        names = {p.name for p in found}
        self.assertEqual(names, set(SAMPLES))

    def test_node(self):
        deps = self._deps_for("package.json")
        self.assertIn(("node-sass", "^7.0.3"), deps)
        self.assertIn(("lodash", "~4.17.0"), deps)
        d = deps[("node-sass", "^7.0.3")]
        self.assertEqual(d.ecosystem, "npm")
        self.assertEqual(d.section, "dependencies")
        text = (self.dir / "package.json").read_text()
        self.assertEqual(text[d.name_edit.start:d.name_edit.end], "node-sass")
        self.assertEqual(text[d.spec_edit.start:d.spec_edit.end], "^7.0.3")

    def test_requirements(self):
        deps = self._deps_for("requirements.txt")
        self.assertEqual(deps[("pycrypto", "==2.6.1")].file.name,
                         "requirements.txt")
        self.assertIn(("numpy", None), deps)          # unpinned
        self.assertIn(("lxml", ">= 2.0"), deps)       # comparator with space is kept

        # '-r base.txt' and comments must not become dependencies
        for name, _ in deps:
            self.assertNotIn(name, ("base", "", "comment"))

    def test_gomod(self):
        deps = self._deps_for("go.mod")
        self.assertIn(("github.com/dgrijalva/jwt-go", "v3.2.0+incompatible"), deps)
        self.assertTrue(deps[("gopkg.in/yaml.v3", "v3.0.1")].section.startswith("require"))
        self.assertNotIn(("module", None), deps)
        self.assertNotIn(("go", None), deps)
        names = {n for n, _ in deps}
        self.assertNotIn("example.com/demo", names)
        self.assertNotIn("example", names)

    def test_cargo(self):
        deps = self._deps_for("Cargo.toml")
        self.assertIn(("error-chain", "0.12.4"), deps)
        self.assertIn(("serde", "1.0"), deps)         # table form
        self.assertIn(("log", "0.4"), deps)
        self.assertNotIn(("demo", "0.1.0"), deps)
        self.assertNotIn(("version", "0.1.0"), deps)

    def test_pyproject(self):
        deps = self._deps_for("pyproject.toml")
        self.assertIn(("pycrypto", "==2.6.1"), deps)
        self.assertIn(("requests", ">=2.6"), deps)
        # the poetry runtime marker is not a dependency
        self.assertFalse(("python", "^3.10") in deps)


# ---------------------------------------------------------------------------
# fixer
# --------------------------------------------------------------------------

class FixerTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="depthfix-fixer-"))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_splice_preserves_formatting(self):
        path = self.dir / "package.json"
        original = """{
  "dependencies": {
    "node-sass": "^7.0.3"
  }
}"""
        path.write_text(original, encoding="utf-8")
        deps = scanners.scan_file(path)
        dep = deps[0]
        f = Finding(dep=dep, status="deprecated", new_name="sass",
                    new_spec="^1.108.1")
        apply_fixes([f])
        text = path.read_text(encoding="utf-8")
        self.assertEqual(
            text,
            """{
  "dependencies": {
    "sass": "^1.108.1"
  }
}""")
        self.assertTrue(f.fixed)

    def test_multiple_edits_same_file_reversed_splice(self):
        path = self.dir / "requirements.txt"
        path.write_text("pycrypto==2.6.1\nsklearn\n", encoding="utf-8")
        deps = scanners.scan_file(path)
        f1 = Finding(dep=deps[0], status="deprecated", new_name="pycryptodome",
                     new_spec="==4.4.0")
        f2 = Finding(dep=deps[1], status="deprecated", new_name="scikit-learn")
        apply_fixes([f1, f2])
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "pycryptodome==4.4.0\nscikit-learn\n")

    def test_dry_run_does_not_write(self):
        path = self.dir / "package.json"
        original = '{"dependencies": {"node-sass": "^7.0.3"}}'
        path.write_text(original, encoding="utf-8")
        dep = scanners.scan_file(path)[0]
        f = Finding(dep=dep, status="deprecated", new_name="sass")
        changed = apply_fixes([f], dry_run=True)
        self.assertEqual(list(changed), [])
        self.assertFalse(f.fixed)
        self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
