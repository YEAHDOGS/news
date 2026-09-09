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
<p class="feed-line">last updated <time datetime="2026-09-09">Sep 9, 2026</time></p>
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

    def test_feed_line_missing_errors(self):
        self.write("index.html", BASE_HTML.replace(
            '<p class="feed-line">last updated '
            '<time datetime="2026-09-09">Sep 9, 2026</time></p>', ""))
        errors, _ = self.checks()
        self.assertTrue(any("feed-line" in e for e in errors), errors)

    def test_feed_line_date_mismatch_errors(self):
        self.write("feed.xml", FEED.format(
            date="Tue, 08 Sep 2026 04:00:00 -0500"))
        errors, _ = self.checks()
        self.assertTrue(any("does not match" in e and "lastBuildDate" in e
                            for e in errors), errors)


class MissingFileTests(FixtureSite):
    """Missing, empty, or malformed core files must be *reported*, never crash.

    Before the hardening, a missing CNAME / sitemap.xml / robots.txt, or a
    page that isn't valid UTF-8, killed the checker with an unhandled
    traceback instead of a clean error line.
    """

    def test_missing_cname_reports_error(self):
        (self.landing / "CNAME").unlink()
        errors, warnings = self.checks()
        self.assertEqual(errors, ["CNAME is missing"], errors)

    def test_empty_cname_reports_error(self):
        self.write("CNAME", "  \n")
        errors, _ = self.checks()
        self.assertTrue(any("CNAME is empty" in e for e in errors), errors)

    def test_missing_sitemap_reports_error(self):
        (self.landing / "sitemap.xml").unlink()
        errors, _ = self.checks()
        self.assertEqual(errors, ["sitemap.xml is missing"], errors)

    def test_sitemap_unparseable(self):
        self.write("sitemap.xml", "<urlset><unclosed>")
        errors, _ = self.checks()
        self.assertTrue(any("sitemap.xml does not parse" in e for e in errors),
                        errors)

    def test_missing_robots_reports_error(self):
        (self.landing / "robots.txt").unlink()
        errors, _ = self.checks()
        self.assertEqual(errors, ["robots.txt is missing"], errors)

    def test_non_utf8_html_page_reports_error(self):
        self.write("index.html", b"\xff\xfe binary junk \x00\x01")
        errors, _ = self.checks()
        self.assertTrue(any("index.html is not valid UTF-8" in e for e in errors),
                        errors)

    def test_non_utf8_feed_reports_error(self):
        self.write("feed.xml", b"\xff\xfe binary junk \x00\x01")
        errors, _ = self.checks()
        self.assertTrue(any("feed.xml is not valid UTF-8" in e for e in errors),
                        errors)

    def test_missing_landing_dir_reports_error(self):
        import shutil
        shutil.rmtree(self.landing)
        errors, _ = self.checks()
        self.assertEqual(errors, ["no HTML files found under landing/"], errors)


class AbsoluteLinkTests(FixtureSite):
    """Absolute same-domain links (https://news.wearedogs.net/...) must
    resolve to real files, just like ./-relative ones. External links are
    never checked (the checker makes no network requests)."""

    def link(self, url):
        self.write("index.html", BASE_HTML.replace(
            "</body>", f'<a href="{url}">x</a>\n</body>'))

    def test_absolute_link_to_missing_file_errors(self):
        self.link("https://news.wearedogs.net/ghost.html")
        errors, _ = self.checks()
        self.assertTrue(any("ghost.html" in e and "no matching file" in e
                            for e in errors), errors)

    def test_absolute_link_to_real_file_passes(self):
        self.link("https://news.wearedogs.net/index.html")
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)

    def test_absolute_bare_host_resolves_to_index(self):
        self.link("https://news.wearedogs.net")
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)

    def test_absolute_link_strips_query_and_fragment(self):
        self.link("https://news.wearedogs.net/index.html?utm=x#top")
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)

    def test_absolute_link_escaping_landing_errors(self):
        self.link("https://news.wearedogs.net/../secret.html")
        errors, _ = self.checks()
        self.assertTrue(any("escapes landing/" in e for e in errors), errors)

    def test_external_links_are_not_checked(self):
        self.link("https://example.com/ghost.html")
        self.write("index.html", (self.landing / "index.html")
                   .read_text(encoding="utf-8").replace(
                       "</body>", '<a href="https://wearedogs.net/">org</a>\n</body>'))
        errors, warnings = self.checks()
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)

    def test_absolute_links_not_checked_when_cname_missing(self):
        (self.landing / "CNAME").unlink()
        self.link("https://news.wearedogs.net/ghost.html")
        errors, _ = self.checks()
        # only the CNAME problem is reported; no crash, no link check
        self.assertEqual(errors, ["CNAME is missing"], errors)


class RealTreeTest(unittest.TestCase):
    """The actual repo tree the CI ships must pass clean."""

    def test_real_tree_has_no_errors_or_warnings(self):
        root = Path(__file__).resolve().parents[1]
        errors, warnings = check.run_checks(root)
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)


if __name__ == "__main__":
    unittest.main()
