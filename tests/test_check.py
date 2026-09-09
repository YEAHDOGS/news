#!/usr/bin/env python3
"""Regression tests for scripts/check.py.

Copies the repo's scripts/ + landing/ into a temp dir (the checker derives
its root from its own path, so the copy behaves like the real repo),
injects a fault into landing/feed.xml or a landing page, and asserts the
checker fails with the expected error message.

Stdlib only. Run:  python3 tests/test_check.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check.py"


def run_check(feed_text: str | None = None, page_edits: dict | None = None,
              file_writes: dict | None = None):
    with tempfile.TemporaryDirectory(prefix="news-check-test-") as tmp:
        tmp = Path(tmp)
        shutil.copytree(ROOT / "scripts", tmp / "scripts")
        shutil.copytree(ROOT / "landing", tmp / "landing")
        if feed_text is not None:
            (tmp / "landing" / "feed.xml").write_text(feed_text, encoding="utf-8")
        if page_edits:
            for name, (old, new) in page_edits.items():
                page = tmp / "landing" / name
                text = page.read_text(encoding="utf-8")
                assert old in text, f"test fixture drift: {old!r} not in {name}"
                page.write_text(text.replace(old, new, 1), encoding="utf-8")
        if file_writes:
            for rel, content in file_writes.items():
                (tmp / "landing" / rel).write_text(content, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(tmp / "scripts" / "check.py")],
            capture_output=True, text=True, timeout=60,
        )
        return proc.returncode, proc.stdout + proc.stderr


def real_feed() -> str:
    return (ROOT / "landing" / "feed.xml").read_text(encoding="utf-8")


failures = []


def check(name: str, fn) -> None:
    try:
        fn()
    except AssertionError as exc:
        failures.append(f"{name}: {exc}")
        print(f"FAIL {name}: {exc}")
    else:
        print(f"ok   {name}")


def t_clean_feed_passes():
    code, out = run_check(real_feed())
    assert code == 0, f"expected exit 0 on the real feed, got {code}:\n{out}"


def t_off_domain_channel_link_fails():
    code, out = run_check(real_feed().replace(
        "https://news.wearedogs.net/</link>",
        "https://evil.example/</link>"))
    assert code != 0, "off-domain <channel><link> should fail"
    assert "is not on https://news.wearedogs.net/" in out, out


def t_wrong_atom_self_link_fails():
    code, out = run_check(real_feed().replace(
        'href="https://news.wearedogs.net/feed.xml"',
        'href="https://news.wearedogs.net/other.xml"'))
    assert code != 0, "wrong atom:self href should fail"
    assert "atom:self link" in out, out


def t_bad_last_build_date_fails():
    code, out = run_check(real_feed().replace(
        "Wed, 09 Sep 2026 04:00:00 -0500", "yesterday-ish"))
    assert code != 0, "unparsable lastBuildDate should fail"
    assert "lastBuildDate" in out and "RFC 822" in out, out


def t_off_domain_item_link_fails():
    feed = real_feed().replace(
        "</channel>",
        '  <item><title>T</title><link>https://evil.example/x</link>'
        "<description>D</description></item>\n  </channel>")
    code, out = run_check(feed)
    assert code != 0, "off-domain item link should fail"
    assert "item link https://evil.example/x is not on" in out, out


RSS_LINK = ('  <link rel="alternate" type="application/rss+xml"'
            ' title="DOGS NEWS" href="./feed.xml">\n')


def t_missing_rss_autodiscovery_fails():
    code, out = run_check(page_edits={"thanks.html": (RSS_LINK, "")})
    assert code != 0, "page without RSS autodiscovery should fail"
    assert "thanks.html: missing RSS autodiscovery link" in out, out


def t_wrong_rss_href_fails():
    code, out = run_check(page_edits={
        "privacy.html": ('href="./feed.xml"', 'href="./feed2.xml"')})
    assert code != 0, "RSS autodiscovery pointing off feed.xml should fail"
    assert "does not point at ./feed.xml" in out, out


def _feed_with_item(title: str, description: str) -> str:
    return real_feed().replace(
        "</channel>",
        f"  <item><title>{title}</title>"
        f"<link>https://news.wearedogs.net/</link>"
        f"<description>{description}</description></item>\n  </channel>")


def _feed_with_item_link(link: str) -> str:
    return real_feed().replace(
        "</channel>",
        f"  <item><title>T</title><link>{link}</link>"
        f"<description>D</description></item>\n  </channel>")


def t_script_entity_in_description_fails():
    feed = _feed_with_item(
        "T", "&#60;script&#62;alert(1)&#60;/script&#62; headline")
    code, out = run_check(feed)
    assert code != 0, "entity-encoded <script> in description should fail"
    assert "active markup <script>" in out, out


def t_event_handler_entity_in_title_fails():
    feed = _feed_with_item(
        "&#60;img src=x onerror=alert(1)&#62; photo", "D")
    code, out = run_check(feed)
    assert code != 0, "entity-encoded onerror attribute in title should fail"
    assert "event-handler attribute" in out, out


def t_javascript_scheme_in_description_fails():
    feed = _feed_with_item("T", "read more: javascript:alert(1)")
    code, out = run_check(feed)
    assert code != 0, "javascript: URL in description should fail"
    assert "javascript: URL" in out, out


def t_benign_entities_pass():
    feed = _feed_with_item(
        "Q&#38;A: how &#60;code&#62; tags work",
        "Plain &#38; boring text — no active markup here.")
    code, out = run_check(feed)
    assert code == 0, f"benign entities should pass, got:\n{out}"


# --- §7 local link integrity ---------------------------------------------------

def t_good_relative_item_link_passes():
    code, out = run_check(_feed_with_item_link("./privacy.html"))
    assert code == 0, f"relative link to existing file should pass:\n{out}"


def t_broken_relative_item_link_fails():
    code, out = run_check(_feed_with_item_link("./nope.html"))
    assert code != 0, "relative link to missing file should fail"
    assert "has no matching file in landing/" in out, out


def t_external_item_link_skipped_offline():
    code, out = run_check(_feed_with_item_link("https://example.com/story"))
    assert "external — not checked (offline)" in out, out
    assert code != 0, "off-domain item link still fails §6 domain rule"


def t_missing_anchor_in_item_link_fails():
    code, out = run_check(_feed_with_item_link(
        "https://news.wearedogs.net/privacy.html#nope"))
    assert code != 0, "link fragment with no matching id should fail"
    assert "has no matching id in privacy.html" in out, out


def t_good_anchor_in_item_link_passes():
    code, out = run_check(_feed_with_item_link(
        "https://news.wearedogs.net/#roadmap"))
    assert code == 0, f"link to existing id should pass:\n{out}"


OG_BROKEN = {"index.html": ("https://news.wearedogs.net/og.png",
                            "https://news.wearedogs.net/og-missing.png")}
OG_REL_GOOD = {"index.html": ("https://news.wearedogs.net/og.png",
                              "./og.png")}
OG_EXTERNAL = {"index.html": ("https://news.wearedogs.net/og.png",
                              "https://example.com/og.png")}
OG_URL_BROKEN = {"privacy.html": ('property="og:url" content="https://news.wearedogs.net/privacy.html"',
                                  'property="og:url" content="https://news.wearedogs.net/privacy-missing.html"')}


def t_broken_og_image_fails():
    code, out = run_check(page_edits=OG_BROKEN)
    assert code != 0, "og:image pointing at a missing file should fail"
    assert "og:image" in out and "no matching file" in out, out


def t_relative_og_image_passes():
    code, out = run_check(page_edits=OG_REL_GOOD)
    assert code == 0, f"relative og:image to an existing file should pass:\n{out}"


def t_external_og_image_skipped_offline():
    code, out = run_check(page_edits=OG_EXTERNAL)
    assert code == 0, f"external og:image should not fail:\n{out}"
    assert "is external — not checked (offline)" in out, out


def t_broken_og_url_fails():
    code, out = run_check(page_edits=OG_URL_BROKEN)
    assert code != 0, "og:url pointing at a missing page should fail"
    assert "og:url" in out and "no matching file" in out, out


# --- §9 canonical link integrity ------------------------------------------------


def t_off_domain_canonical_fails():
    code, out = run_check(page_edits={"thanks.html": (
        'rel="canonical" href="https://news.wearedogs.net/thanks.html"',
        'rel="canonical" href="https://evil.example/thanks.html"')})
    assert code != 0, "off-domain canonical href should fail"
    assert "not an absolute URL on the canonical domain" in out, out


def t_relative_canonical_fails():
    code, out = run_check(page_edits={"thanks.html": (
        'rel="canonical" href="https://news.wearedogs.net/thanks.html"',
        'rel="canonical" href="/thanks.html"')})
    assert code != 0, "relative canonical href should fail"
    assert "not an absolute URL on the canonical domain" in out, out


def t_wrong_page_canonical_fails():
    code, out = run_check(page_edits={"thanks.html": (
        'rel="canonical" href="https://news.wearedogs.net/thanks.html"',
        'rel="canonical" href="https://news.wearedogs.net/privacy.html"')})
    assert code != 0, "canonical pointing at another page should fail"
    assert "does not match this page" in out, out


def t_canonical_query_fails():
    code, out = run_check(page_edits={"thanks.html": (
        'rel="canonical" href="https://news.wearedogs.net/thanks.html"',
        'rel="canonical" href="https://news.wearedogs.net/thanks.html?x=1"')})
    assert code != 0, "canonical with a query string should fail"
    assert "must not contain a query or fragment" in out, out


def t_multiple_canonicals_fail():
    code, out = run_check(page_edits={"thanks.html": (
        '<link rel="canonical" href="https://news.wearedogs.net/thanks.html">',
        '<link rel="canonical" href="https://news.wearedogs.net/thanks.html">\n'
        '  <link rel="canonical" href="https://news.wearedogs.net/thanks.html">')})
    assert code != 0, "two canonical tags should fail"
    assert "exactly one is allowed" in out, out


def t_ogurl_canonical_mismatch_fails():
    code, out = run_check(page_edits={"thanks.html": (
        'property="og:url" content="https://news.wearedogs.net/thanks.html"',
        'property="og:url" content="https://news.wearedogs.net/privacy.html"')})
    assert code != 0, "og:url disagreeing with canonical should fail"
    assert "og:url" in out and "does not match canonical" in out, out


def t_duplicate_canonical_fails():
    code, out = run_check(page_edits={
        "thanks.html": (
            'rel="canonical" href="https://news.wearedogs.net/thanks.html"',
            'rel="canonical" href="https://news.wearedogs.net/privacy.html"'),
    })
    # thanks.html now claims the same canonical as privacy.html
    assert code != 0, "two pages sharing a canonical should fail"
    assert "is claimed by multiple pages" in out, out


check("clean feed passes", t_clean_feed_passes)
check("off-domain channel link fails", t_off_domain_channel_link_fails)
check("wrong atom:self link fails", t_wrong_atom_self_link_fails)
check("bad lastBuildDate fails", t_bad_last_build_date_fails)
check("off-domain item link fails", t_off_domain_item_link_fails)
check("missing RSS autodiscovery fails", t_missing_rss_autodiscovery_fails)
check("wrong RSS href fails", t_wrong_rss_href_fails)
check("entity-encoded <script> in description fails",
      t_script_entity_in_description_fails)
check("entity-encoded onerror in title fails",
      t_event_handler_entity_in_title_fails)
check("javascript: URL in description fails",
      t_javascript_scheme_in_description_fails)
check("benign escaped entities pass", t_benign_entities_pass)
check("good relative item link passes", t_good_relative_item_link_passes)
check("broken relative item link fails", t_broken_relative_item_link_fails)
check("external item link skipped (offline)", t_external_item_link_skipped_offline)
check("missing anchor in item link fails", t_missing_anchor_in_item_link_fails)
check("good anchor in item link passes", t_good_anchor_in_item_link_passes)
check("broken og:image fails", t_broken_og_image_fails)
check("relative og:image passes", t_relative_og_image_passes)
check("external og:image skipped (offline)", t_external_og_image_skipped_offline)
check("broken og:url fails", t_broken_og_url_fails)
check("off-domain canonical fails", t_off_domain_canonical_fails)
check("relative canonical fails", t_relative_canonical_fails)
check("canonical pointing at another page fails", t_wrong_page_canonical_fails)
check("canonical with query string fails", t_canonical_query_fails)
check("multiple canonicals fail", t_multiple_canonicals_fail)
check("og:url disagreeing with canonical fails", t_ogurl_canonical_mismatch_fails)
check("duplicate canonical across pages fails", t_duplicate_canonical_fails)

# --- §10 feed date integrity ----------------------------------------------------

from datetime import datetime, timedelta, timezone


def _rfc822(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%a, %d %b %Y %H:%M:%S +0000")


def _feed_with_pubdate(pub: str) -> str:
    return real_feed().replace(
        "</channel>",
        f"  <item><title>T</title><link>https://news.wearedogs.net/</link>"
        f"<pubDate>{pub}</pubDate><description>D</description></item>\n  </channel>")


def t_fresh_item_passes():
    code, out = run_check(_feed_with_pubdate(_rfc822(1)))
    assert code == 0, f"fresh item should pass:\n{out}"


def t_stale_item_warns():
    code, out = run_check(_feed_with_pubdate(_rfc822(45)))
    assert code == 0, f"stale item should only warn, not fail:\n{out}"
    assert "older than" in out and "stale" in out, out


def t_future_item_fails():
    code, out = run_check(_feed_with_pubdate(_rfc822(-2)))
    assert code != 0, "future-dated item should fail"
    assert "in the future" in out, out


def t_missing_pubdate_warns():
    feed = _feed_with_item("T", "D")  # no pubDate on the item
    code, out = run_check(feed)
    assert code == 0, f"missing pubDate should only warn:\n{out}"
    assert "no pubDate" in out, out


def t_stale_channel_builddate_warns():
    feed = real_feed().replace(
        "Wed, 09 Sep 2026 04:00:00 -0500", _rfc822(45))
    code, out = run_check(feed)
    assert code == 0, f"stale lastBuildDate should only warn:\n{out}"
    assert "lastBuildDate" in out and "stale" in out, out


check("fresh item passes", t_fresh_item_passes)
check("stale item warns", t_stale_item_warns)
check("future-dated item fails", t_future_item_fails)
check("missing item pubDate warns", t_missing_pubdate_warns)
check("stale channel lastBuildDate warns", t_stale_channel_builddate_warns)

# --- §8b og:image dimension integrity ---------------------------------------------

OG_W = '<meta property="og:image:width" content="1200">'
OG_H = '<meta property="og:image:height" content="630">'


def t_og_image_dim_mismatch_fails():
    code, out = run_check(page_edits={"index.html": (OG_W, OG_W.replace(
        'content="1200"', 'content="800"'))})
    assert code != 0, "og:image:width disagreeing with og.png should fail"
    assert "do not match the actual og.png size 1200x630" in out, out


def t_og_image_height_mismatch_fails():
    code, out = run_check(page_edits={"privacy.html": (OG_H, OG_H.replace(
        'content="630"', 'content="600"'))})
    assert code != 0, "og:image:height disagreeing with og.png should fail"
    assert "do not match the actual og.png size" in out, out


def t_og_image_dims_missing_warns():
    code, out = run_check(page_edits={"thanks.html": (
        OG_W + "\n  " + OG_H + "\n", "")})
    assert code == 0, f"missing og:image dims should only warn:\n{out}"
    assert "no og:image:width/height" in out, out


def t_og_image_half_declared_warns():
    code, out = run_check(page_edits={"derp.html": (OG_W + "\n  ", "")})
    assert code == 0, f"one-sided og:image dims should only warn:\n{out}"
    assert "declares only one of" in out, out


def t_og_image_dim_nonnumeric_fails():
    code, out = run_check(page_edits={"404.html": (OG_W, OG_W.replace(
        'content="1200"', 'content="wide"'))})
    assert code != 0, "non-numeric og:image:width should fail"
    assert "is not a number" in out, out


check("og:image:width disagreeing with the file fails",
      t_og_image_dim_mismatch_fails)
check("og:image:height disagreeing with the file fails",
      t_og_image_height_mismatch_fails)
check("missing og:image dimensions warn", t_og_image_dims_missing_warns)
check("half-declared og:image dimensions warn",
      t_og_image_half_declared_warns)
check("non-numeric og:image dimension fails",
      t_og_image_dim_nonnumeric_fails)

# --- §11a XML bomb guards (DOCTYPE ban + size cap) -----------------------------


def t_feed_doctype_rejected():
    feed = real_feed().replace(
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE rss [<!ENTITY x "expanded">]>')
    code, out = run_check(feed)
    assert code != 0, "DOCTYPE in feed.xml should fail"
    assert "DOCTYPE declaration" in out and "XML bomb" in out, out


def t_feed_over_size_cap_rejected():
    padding = "x" * (600 * 1024)  # 600 KiB > 512 KiB cap
    feed = real_feed().replace(
        "Reader-first reporting with no middlemen and no noise.",
        "Reader-first reporting." + padding)
    code, out = run_check(feed)
    assert code != 0, "oversized feed.xml should fail"
    assert "over the" in out and "cap" in out, out


def t_sitemap_doctype_rejected():
    bad = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<!DOCTYPE urlset [<!ENTITY x "expanded">]>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           '<url><loc>https://news.wearedogs.net/</loc></url>\n'
           "</urlset>\n")
    code, out = run_check(file_writes={"sitemap.xml": bad})
    assert code != 0, "DOCTYPE in sitemap.xml should fail"
    assert "sitemap.xml" in out and "DOCTYPE declaration" in out, out


check("DOCTYPE in feed.xml is rejected", t_feed_doctype_rejected)
check("oversized feed.xml is rejected", t_feed_over_size_cap_rejected)
check("DOCTYPE in sitemap.xml is rejected", t_sitemap_doctype_rejected)

# --- §6b URL scheme policy (http(s) only, dangerous schemes rejected) -------


def t_javascript_item_link_fails():
    code, out = run_check(_feed_with_item_link("javascript:alert(1)"))
    assert code != 0, "javascript: item link should fail"
    assert "dangerous URL scheme (javascript:)" in out, out


def t_data_item_link_fails():
    code, out = run_check(_feed_with_item_link(
        "data:text/html;base64,PGI+"))
    assert code != 0, "data: item link should fail"
    assert "dangerous URL scheme (data:)" in out, out


def t_mailto_item_link_fails():
    code, out = run_check(_feed_with_item_link("mailto:news@example.com"))
    assert code != 0, "mailto: item link should fail"
    assert "item link mailto:news@example.com is not on" in out, out


def t_javascript_channel_link_fails():
    feed = real_feed().replace(
        "https://news.wearedogs.net/</link>",
        "javascript:alert(1)</link>")
    code, out = run_check(feed)
    assert code != 0, "javascript: channel link should fail"
    assert "dangerous URL scheme (javascript:)" in out, out


def _feed_with_guid(guid_xml: str) -> str:
    return real_feed().replace(
        "</channel>",
        f"  <item><title>T</title><link>https://news.wearedogs.net/</link>"
        f"{guid_xml}<description>D</description></item>\n  </channel>")


def t_guid_javascript_permalink_fails():
    feed = _feed_with_guid(
        '<guid isPermaLink="true">javascript:alert(1)</guid>')
    code, out = run_check(feed)
    assert code != 0, "javascript: permalink guid should fail"
    assert "dangerous URL scheme (javascript:)" in out, out


def t_guid_default_is_permalink_fails():
    # isPermaLink defaults to true per the RSS spec
    feed = _feed_with_guid("<guid>javascript:alert(1)</guid>")
    code, out = run_check(feed)
    assert code != 0, "guid without isPermaLink should be treated as permalink"
    assert "dangerous URL scheme (javascript:)" in out, out


def t_guid_nonpermalink_passes():
    feed = _feed_with_guid(
        '<guid isPermaLink="false">episode-123</guid>')
    code, out = run_check(feed)
    assert code == 0, f"non-permalink guid should pass:\n{out}"


check("javascript: item link is rejected", t_javascript_item_link_fails)
check("data: item link is rejected", t_data_item_link_fails)
check("mailto: item link is rejected", t_mailto_item_link_fails)
check("javascript: channel link is rejected", t_javascript_channel_link_fails)
check("javascript: permalink guid is rejected", t_guid_javascript_permalink_fails)
check("guid defaults to isPermaLink=true", t_guid_default_is_permalink_fails)
check("non-permalink guid passes", t_guid_nonpermalink_passes)

# --- §11b feed content size caps ------------------------------------------------


def _feed_with_n_items(n: int, title: str = "T", desc: str = "D") -> str:
    items = "".join(
        f"  <item><title>{title}</title>"
        f"<link>https://news.wearedogs.net/</link>"
        f"<description>{desc}</description></item>\n"
        for _ in range(n))
    return real_feed().replace("</channel>", items + "  </channel>")


def t_too_many_items_fails():
    code, out = run_check(_feed_with_n_items(201))
    assert code != 0, "201 items should fail the item cap"
    assert "201 items" in out and "item cap" in out, out


def t_item_cap_boundary_passes():
    code, out = run_check(_feed_with_n_items(200))
    assert code == 0, f"exactly 200 items should pass:\n{out}"


def t_long_title_fails():
    code, out = run_check(_feed_with_n_items(1, title="T" * 400))
    assert code != 0, "400-char title should fail"
    assert "400 chars" in out and "limit 300" in out, out


def t_title_boundary_passes():
    code, out = run_check(_feed_with_n_items(1, title="T" * 300))
    assert code == 0, f"exactly 300-char title should pass:\n{out}"


def t_long_description_fails():
    code, out = run_check(_feed_with_n_items(1, desc="D" * (40 * 1024)))
    assert code != 0, "40 KiB description should fail"
    assert "limit 32768" in out, out


check("too many feed items fail", t_too_many_items_fails)
check("item cap boundary passes", t_item_cap_boundary_passes)
check("overlong item title fails", t_long_title_fails)
check("title length boundary passes", t_title_boundary_passes)
check("overlong item description fails", t_long_description_fails)

# --- §6b extended XSS scan (guid/category/author/source, data:/vbscript:) ----


def t_data_scheme_in_description_fails():
    feed = _feed_with_item("T", "see data:text/html;base64,PGI+")
    code, out = run_check(feed)
    assert code != 0, "data: URL in description should fail"
    assert "data: URL" in out, out


def t_vbscript_scheme_in_title_fails():
    feed = _feed_with_item("vbscript:msgbox(1) here", "D")
    code, out = run_check(feed)
    assert code != 0, "vbscript: URL in title should fail"
    assert "vbscript: URL" in out, out


def t_script_entity_in_guid_fails():
    feed = _feed_with_guid(
        '<guid isPermaLink="false">&#60;script&#62;x&#60;/script&#62;</guid>')
    code, out = run_check(feed)
    assert code != 0, "entity-encoded <script> in guid should fail"
    assert "active markup <script>" in out, out


def t_event_handler_entity_in_category_fails():
    feed = real_feed().replace(
        "</channel>",
        '  <item><title>T</title><link>https://news.wearedogs.net/</link>'
        '<category>&#60;img src=x onerror=alert(1)&#62;</category>'
        "<description>D</description></item>\n  </channel>")
    code, out = run_check(feed)
    assert code != 0, "entity-encoded onerror in category should fail"
    assert "event-handler attribute" in out, out


def t_benign_author_passes():
    feed = real_feed().replace(
        "</channel>",
        '  <item><title>T</title><link>https://news.wearedogs.net/</link>'
        "<author>user@wearedogs.net (the founder)</author>"
        "<description>D</description></item>\n  </channel>")
    code, out = run_check(feed)
    assert code == 0, f"benign author should pass:\n{out}"


check("data: URL in description fails", t_data_scheme_in_description_fails)
check("vbscript: URL in title fails", t_vbscript_scheme_in_title_fails)
check("entity-encoded <script> in guid fails", t_script_entity_in_guid_fails)
check("entity-encoded onerror in category fails",
      t_event_handler_entity_in_category_fails)
check("benign author passes", t_benign_author_passes)

# --- §12 feed item identity integrity (guid presence + uniqueness) -------------


def _feed_with_items(guid_xmls: list) -> str:
    items = "".join(
        f'  <item><title>T{i}</title><link>https://news.wearedogs.net/</link>'
        f"{g}<description>D</description></item>\n"
        for i, g in enumerate(guid_xmls, start=1))
    return real_feed().replace("</channel>", items + "  </channel>")


def t_duplicate_guid_fails():
    feed = _feed_with_items([
        '<guid isPermaLink="false">story-1</guid>',
        '<guid isPermaLink="false">story-1</guid>',
    ])
    code, out = run_check(feed)
    assert code != 0, "duplicate guids should fail"
    assert "duplicates item #1" in out, out


def t_missing_guid_warns():
    code, out = run_check(_feed_with_items([""]))
    assert code == 0, f"missing guid should only warn, not fail:\n{out}"
    assert "has no <guid>" in out, out


def t_unique_guids_pass():
    feed = _feed_with_items([
        '<guid isPermaLink="false">story-1</guid>',
        '<guid isPermaLink="false">story-2</guid>',
    ])
    code, out = run_check(feed)
    assert code == 0, f"unique guids should pass:\n{out}"


check("duplicate item guids fail", t_duplicate_guid_fails)
check("missing item guid warns", t_missing_guid_warns)
check("unique item guids pass", t_unique_guids_pass)

# --- §13 feed item content completeness --------------------------------------

def _feed_with_custom_items(items_xml: list) -> str:
    items = "".join(f"  <item>{x}</item>\n" for x in items_xml)
    return real_feed().replace("</channel>", items + "  </channel>")


def t_item_no_title_no_description_fails():
    feed = _feed_with_custom_items([
        '<link>https://news.wearedogs.net/</link>'
        '<guid isPermaLink="false">s1</guid>'])
    code, out = run_check(feed)
    assert code != 0, "item with neither title nor description should fail"
    assert "has neither a non-empty <title> nor <description>" in out, out


def t_item_empty_title_and_description_fails():
    feed = _feed_with_custom_items([
        "<title>  </title><description></description>"
        '<link>https://news.wearedogs.net/</link>'])
    code, out = run_check(feed)
    assert code != 0, "item with empty title+description should fail"
    assert "has neither a non-empty <title> nor <description>" in out, out


def t_item_title_only_passes():
    feed = _feed_with_custom_items([
        "<title>Only a title</title>"
        '<link>https://news.wearedogs.net/</link>'])
    code, out = run_check(feed)
    assert code == 0, f"title-only item should pass:\n{out}"


def t_item_description_only_passes():
    feed = _feed_with_custom_items([
        "<description>Only a description</description>"
        '<link>https://news.wearedogs.net/</link>'])
    code, out = run_check(feed)
    assert code == 0, f"description-only item should pass:\n{out}"


def t_item_no_link_warns():
    feed = _feed_with_custom_items([
        "<title>T</title><description>D</description>"])
    code, out = run_check(feed)
    assert code == 0, f"linkless item should only warn, not fail:\n{out}"
    assert "has no <link>" in out, out


check("item with no title/description fails", t_item_no_title_no_description_fails)
check("item with empty title+description fails", t_item_empty_title_and_description_fails)
check("title-only item passes", t_item_title_only_passes)
check("description-only item passes", t_item_description_only_passes)
check("linkless item warns", t_item_no_link_warns)

if failures:
    print(f"\n{len(failures)} test(s) failed")
    sys.exit(1)
print(f"\nall {37 + 3 + 7 + 5 + 5 + 3 + 5} tests passed")
