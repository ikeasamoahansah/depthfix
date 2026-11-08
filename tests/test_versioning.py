import unittest

from depthfix.versioning import (
    best_match, bump_spec, compare, floor_version, replacement_spec,
    satisfies,
)


class CompareTests(unittest.TestCase):
    def test_ordering(self):
        pairs = [
            ("1.2.3", "1.2.4"),
            ("1.2", "1.2.1"),
            ("1.9.0", "1.10.0"),
            ("1.0.0", "2.0.0"),
            ("0.12.0", "1.0.0"),
            ("2020.1.1", "2024.9.1"),
        ]
        for low, high in pairs:
            self.assertLess(compare(low, high), 0, f"{low} < {high}")
            self.assertGreater(compare(high, low), 0)

    def test_prereleases_below_release(self):
        self.assertLess(compare("2.0.0-rc.1", "2.0.0"), 0)
        self.assertLess(compare("3.4.0rc2", "3.4.0"), 0)

    def test_zero_padding_equal(self):
        self.assertEqual(compare("1.2", "1.2.0"), 0)
        self.assertEqual(compare("1.2.0", "1.2"), 0)


class NpmTests(unittest.TestCase):
    def test_caret(self):
        self.assertTrue(satisfies("^1.5.0", "1.9.9", "npm"))
        self.assertFalse(satisfies("^1.5.0", "2.0.0", "npm"))
        self.assertTrue(satisfies("^0.2.3", "0.2.9", "npm"))
        self.assertFalse(satisfies("^0.2.3", "0.3.0", "npm"))

    def test_tilde(self):
        self.assertTrue(satisfies("~1.5.0", "1.5.9", "npm"))
        self.assertFalse(satisfies("~1.5.0", "1.6.0", "npm"))

    def test_x_and_wildcard(self):
        self.assertTrue(satisfies("1.x", "1.9.3", "npm"))
        self.assertFalse(satisfies("1.x", "2.1.0", "npm"))
        self.assertTrue(satisfies("*", "7.1.2", "npm"))

    def test_or_comparators(self):
        self.assertTrue(satisfies("1.2.3 || ^2.0.0", "2.3.1", "npm"))
        self.assertFalse(satisfies("^1.0.0 <1.9.0", "1.9.0", "npm"))

    def test_prerelease_not_matched(self):
        self.assertFalse(satisfies("^1.0.0", "1.9.0-beta.3", "npm"))
        self.assertTrue(satisfies("^1.0.0-beta", "1.9.0-beta.3", "npm"))

    def test_best_match_prefers_stable(self):
        got = best_match(["1.9.0-beta.3", "1.8.2", "1.10.0-rc1"], "^1.2.3")
        # prerelease candidates never satisfy a plain caret
        self.assertEqual(got, "1.8.2")

    def test_bump_preserves_style(self):
        self.assertEqual(bump_spec("npm", "^2.6.12", "3.51.0"), "^3.51.0")
        self.assertEqual(bump_spec("npm", "~1.2.0", "1.4.5"), "~1.4.5")
        self.assertEqual(bump_spec("npm", "2.88.2", "3.0.1"), "^3.0.1")

    def test_go_and_pypi_bumps(self):
        self.assertEqual(bump_spec("go", "v1.2.3", "1.4.0"), "v1.4.0")
        self.assertEqual(bump_spec("pypi", "==2.6.1", "4.4.0"), "==4.4.0")
        self.assertEqual(bump_spec("pypi", ">=1.0", "1.9.1"), ">=1.9.1")
        self.assertEqual(replacement_spec("pypi", None, "1.4.2"), "==1.4.2")


class PyPITests(unittest.TestCase):
    def test_pinned(self):
        self.assertTrue(satisfies("==2.6.1", "2.6.1", "pypi"))
        self.assertFalse(satisfies("==2.6.1", "2.6.0", "pypi"))
        self.assertTrue(satisfies("==2.6", "2.6.0", "pypi"))

    def test_wildcard_and_compatible(self):
        self.assertTrue(satisfies("==1.2.*", "1.2.9", "pypi"))
        self.assertFalse(satisfies("==1.2.*", "1.3.0", "pypi"))
        # ~=1.4.2  ==  >=1.4.2, ==1.4.*
        self.assertTrue(satisfies("~=1.4.2", "1.4.9", "pypi"))
        self.assertFalse(satisfies("~=1.4.2", "1.9.0", "pypi"))
        self.assertFalse(satisfies("~=1.4.2", "2.0.0", "pypi"))
        # ~=1.4  ==  >=1.4, ==1.*
        self.assertTrue(satisfies("~=1.4", "1.9.0", "pypi"))
        self.assertFalse(satisfies("~=1.4", "2.0.0", "pypi"))

    def test_floor(self):
        self.assertEqual(floor_version("^4.17.20"), "4.17.20")
        self.assertEqual(floor_version(">=2.26"), "2.26")
        self.assertIsNone(floor_version(None))


class CargoTests(unittest.TestCase):
    def test_bare_is_caret(self):
        self.assertTrue(satisfies("1.0", "1.4.3", "cargo"))
        self.assertFalse(satisfies("1.0", "2.0.0", "cargo"))


class GoTests(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(satisfies("v3.2.0+incompatible", "3.2.0", "go"))
        self.assertFalse(satisfies("v3.2.0+incompatible", "3.3.0", "go"))


if __name__ == "__main__":
    unittest.main()
