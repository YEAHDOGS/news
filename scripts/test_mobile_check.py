#!/usr/bin/env python3
"""Regression tests for scripts/mobile_check.py.

Builds throwaway fixture sites under temp dirs and asserts run_mobile_checks()
flags exactly what it should. Stdlib only.

Run:
    python3 -m unittest discover -s scripts -p 'test_*.py'
or:
    python3 scripts/test_mobile_check.py
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DOGS NEWS</title>
<link rel="stylesheet" href="./styles.css">
</head>
<body>
<h1>DOGS NEWS</h1>
</body>
</html>
"""

GOOD_CSS = """
img { max-width: 100%; height: auto; }
header.site { position: fixed; inset: 0 0 auto 0; }
.wrap { max-width: 72rem; margin: 0 auto; padding: 0 1.5rem; }
.grid { display: grid; grid-template-columns: repeat(3, 1fr); }
@media (max-width: 880px) {
  .grid { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  header.site { padding: 0; }
}
"""


def _load_mobile_check():
    spec = importlib.util.spec_from_file_location(
        "news_mobile_check", Path(__file__).resolve().parent / "mobile_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mobile_check = _load_mobile_check()


class FixtureSite(unittest.TestCase):
    """Each test gets a fresh, fully valid fixture site."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.landing = self.root / "landing"
        self.landing.mkdir()
        self.write("index.html", PAGE_HTML)
        self.write("styles.css", GOOD_CSS)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, content):
        path = self.landing / name
        path.write_text(content, encoding="utf-8")
        return path

    def checks(self):
        return mobile_check.run_mobile_checks(self.root)

    def assertClean(self):
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)

    def test_clean_tree_passes(self):
        self.assertClean()

    def test_missing_viewport_meta(self):
        self.write("index.html", PAGE_HTML.replace(
            '<meta name="viewport" content="width=device-width, initial-scale=1">', ""))
        errors, warnings = self.checks()
        self.assertTrue(any("viewport" in e for e in errors), errors)

    def test_viewport_without_device_width(self):
        self.write("index.html", PAGE_HTML.replace(
            "width=device-width", "width=1024"))
        errors, warnings = self.checks()
        self.assertTrue(any("viewport" in e for e in errors), errors)

    def test_fixed_px_width_at_target(self):
        self.write("styles.css", GOOD_CSS + ".banner { width: 420px; }")
        errors, warnings = self.checks()
        self.assertTrue(any("420px" in e for e in errors), errors)

    def test_min_width_at_target(self):
        self.write("styles.css", GOOD_CSS + ".banner { min-width: 390px; }")
        errors, warnings = self.checks()
        self.assertTrue(any("390px" in e for e in errors), errors)

    def test_small_fixed_width_passes(self):
        self.write("styles.css", GOOD_CSS + ".dot { width: 12px; }")
        self.assertClean()

    def test_missing_img_guard(self):
        self.write("styles.css", GOOD_CSS.replace(
            "img { max-width: 100%; height: auto; }", ""))
        errors, warnings = self.checks()
        self.assertTrue(any("max-width: 100%" in e for e in errors), errors)

    def test_wide_img_width_attr(self):
        self.write("index.html", PAGE_HTML.replace(
            "</body>", '<img src="./x.png" alt="" width="800">\n</body>'))
        errors, warnings = self.checks()
        self.assertTrue(any("800" in e for e in errors), errors)

    def test_no_small_breakpoint(self):
        self.write("styles.css",
                   GOOD_CSS.replace("@media (max-width: 880px)", "@media (max-width: 1200px)")
                   .replace("@media (max-width: 640px)", "@media (max-width: 1200px)"))
        errors, warnings = self.checks()
        self.assertTrue(any("breakpoint" in e for e in errors), errors)

    def test_fixed_header_without_mobile_adjustment(self):
        css = GOOD_CSS.replace(
            "@media (max-width: 640px) {\n  header.site { padding: 0; }\n}", "")
        self.write("styles.css", css)
        errors, warnings = self.checks()
        self.assertTrue(any("header.site" in e for e in errors), errors)

    def test_fixed_header_adjusted_via_descendant(self):
        # The fixed bar itself is untouched, but .nav inside it is restyled
        # at small widths — that counts as a mobile adjustment.
        self.write("index.html", PAGE_HTML.replace(
            "</body>",
            '<header class="site"><div class="nav"><a href="./">x</a></div></header>\n</body>'))
        css = GOOD_CSS.replace(
            "@media (max-width: 640px) {\n  header.site { padding: 0; }\n}",
            "@media (max-width: 640px) {\n  .nav { padding: 0 1rem; }\n}")
        self.write("styles.css", css)
        self.assertClean()

    def test_nowrap_with_fixed_width_warns(self):
        self.write("styles.css", GOOD_CSS +
                   ".label { width: 320px; white-space: nowrap; }")
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertTrue(any("nowrap" in w for w in warnings), warnings)

    def test_missing_styles_css(self):
        (self.landing / "styles.css").unlink()
        errors, warnings = self.checks()
        self.assertTrue(errors, errors)


class RealRepo(unittest.TestCase):
    """The check must pass against the actual repo landing directory."""

    def test_real_site_passes(self):
        root = Path(__file__).resolve().parent.parent
        errors, warnings = mobile_check.run_mobile_checks(root)
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)


if __name__ == "__main__":
    unittest.main()
