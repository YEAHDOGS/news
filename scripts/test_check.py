#!/usr/bin/env python3
"""Regression tests for scripts/check.py.

Builds throwaway fixture sites under temp dirs and asserts run_checks()
flags exactly what it should. Stdlib only.

Run:
    python3 -m unittest discover -s scripts -p 'test_*.py'
or:
    python3 scripts/test_check.py
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

CNAME = "news.wearedogs.net"

BASE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DOGS NEWS</title>
<meta name="description" content="Reader-first news by DOGS.">
<link rel="canonical" href="https://news.wearedogs.net/">
<meta property="og:title" content="DOGS NEWS">
<meta property="og:description" content="Reader-first news by DOGS.">
<meta property="og:image" content="https://news.wearedogs.net/og.png">
<link rel="stylesheet" href="./styles.css">
</head>
<body>
<h1 id="top">DOGS NEWS</h1>
<p><a href="#top">back to top</a></p>
</body>
</html>
"""

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://news.wearedogs.net/</loc></url>
</urlset>
"""

ROBOTS = """User-agent: *
Allow: /

Sitemap: https://news.wearedogs.net/sitemap.xml
"""

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>DOGS NEWS</title>
    <link>https://news.wearedogs.net/</link>
    <description>An independent news platform by DOGS.</description>
    <atom:link href="https://news.wearedogs.net/feed.xml" rel="self" type="application/rss+xml" />
    <lastBuildDate>{date}</lastBuildDate>
  </channel>
</rss>
"""


def _load_check():
    spec = importlib.util.spec_from_file_location(
        "news_check", Path(__file__).resolve().parent / "check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_check()


class FixtureSite(unittest.TestCase):
    """Each test gets a fresh, fully valid fixture site."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.landing = self.root / "landing"
        self.landing.mkdir()
        self.write("CNAME", CNAME)
        self.write("index.html", BASE_HTML)
        self.write("styles.css", "body { color: #111; }")
        self.write("og.png", b"\x89PNG\r\n\x1a\n")  # referenced via og:image
        self.write("sitemap.xml", SITEMAP)
        self.write("robots.txt", ROBOTS)
        self.write("feed.xml", FEED.format(date="Wed, 09 Sep 2026 04:00:00 -0500"))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, content):
        path = self.landing / name
        path.write_bytes(content if isinstance(content, bytes)
                         else content.encode("utf-8"))
        return path

    def checks(self):
        return check.run_checks(self.root)

    def test_clean_tree_passes(self):
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)

    def test_missing_referenced_file(self):
        self.write("index.html", BASE_HTML.replace(
            'href="./styles.css"', 'href="./gone.css"'))
        errors, _ = self.checks()
        self.assertTrue(any("missing referenced file" in e for e in errors), errors)

    def test_dangling_anchor(self):
        self.write("index.html", BASE_HTML.replace(
            'href="#top"', 'href="#nowhere"'))
        errors, _ = self.checks()
        self.assertTrue(any("anchor #nowhere" in e for e in errors), errors)

    def test_reference_escaping_landing(self):
        self.write("index.html", BASE_HTML.replace(
            'href="./styles.css"', 'href="./../outside.css"'))
        errors, _ = self.checks()
        self.assertTrue(any("escapes landing/" in e for e in errors), errors)

    def test_missing_seo_title(self):
        self.write("index.html", BASE_HTML.replace(
            "<title>DOGS NEWS</title>", ""))
        errors, _ = self.checks()
        self.assertTrue(any("missing <title>" in e for e in errors), errors)

    def test_noindex_page_listed_in_sitemap(self):
        self.write("index.html", BASE_HTML.replace(
            "<head>", '<head>\n<meta name="robots" content="noindex">'))
        errors, _ = self.checks()
        self.assertTrue(any("marked noindex but listed in sitemap" in e
                            for e in errors), errors)

    def test_sitemap_off_domain_loc(self):
        self.write("sitemap.xml", SITEMAP.replace(
            "https://news.wearedogs.net/",
            "https://example.com/"))
        errors, _ = self.checks()
        self.assertTrue(any("not on the canonical domain" in e
                            for e in errors), errors)

    def test_sitemap_points_at_missing_file(self):
        self.write("sitemap.xml", SITEMAP.replace(
            "https://news.wearedogs.net/",
            "https://news.wearedogs.net/ghost.html"))
        errors, _ = self.checks()
        self.assertTrue(any("no matching file" in e for e in errors), errors)

    def test_unreferenced_asset_warns(self):
        self.write("stray.png", b"\x89PNG\r\n\x1a\n")
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertTrue(any("unreferenced asset" in w for w in warnings),
                        warnings)

    def test_disallowed_but_indexable_warns(self):
        self.write("robots.txt", ROBOTS + "Disallow: /index.html\n")
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertTrue(any("Disallow'd in robots.txt but not noindex" in w
                            for w in warnings), warnings)

    # --- feed.xml ---------------------------------------------------------
    def test_feed_unparseable(self):
        self.write("feed.xml", "<rss><channel><unclosed>")
        errors, _ = self.checks()
        self.assertTrue(any("feed.xml does not parse" in e for e in errors),
                        errors)

    def test_feed_missing_channel_description(self):
        self.write("feed.xml", FEED.replace(
            "<description>An independent news platform by DOGS.</description>",
            "").format(date="Wed, 09 Sep 2026 04:00:00 -0500"))
        errors, _ = self.checks()
        self.assertTrue(any("feed.xml: channel is missing <description>" in e
                            for e in errors), errors)

    def test_feed_wrong_atom_self_link(self):
        self.write("feed.xml", FEED.format(
            date="Wed, 09 Sep 2026 04:00:00 -0500").replace(
            "https://news.wearedogs.net/feed.xml",
            "https://evil.example/feed.xml"))
        errors, _ = self.checks()
        self.assertTrue(any("atom self-link" in e and "canonical" in e
                            for e in errors), errors)

    def test_feed_stale_last_build_date(self):
        self.write("feed.xml", FEED.format(
            date="Mon, 01 Jan 2024 04:00:00 -0500"))
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertTrue(any("lastBuildDate" in w and "30 days" in w
                            for w in warnings), warnings)

    def test_feed_missing_file_warns(self):
        (self.landing / "feed.xml").unlink()
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertTrue(any("feed.xml is missing" in w for w in warnings),
                        warnings)


class RealTreeTest(unittest.TestCase):
    """The actual repo tree the CI ships must pass clean."""

    def test_real_tree_has_no_errors_or_warnings(self):
        root = Path(__file__).resolve().parents[1]
        errors, warnings = check.run_checks(root)
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)


if __name__ == "__main__":
    unittest.main()
