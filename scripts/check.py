#!/usr/bin/env python3
"""Sanity checks for the news.wearedogs.net static landing site.

Catches broken internal links, dangling fragment anchors, missing SEO
basics, and sitemap/robots.txt drift before anything ships.

Errors fail the run; warnings are informational.
Stdlib only — no dependencies to install.
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "landing"

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


REL_REF = re.compile(r'''(?:src|href)="(\./[^"]+)"''')
FRAGMENT = re.compile(r'''href="#([^"]+)"''')
ID_ATTR = re.compile(r'''\sid="([^"]+)"''')
OG_TAG = re.compile(r'''<meta\s+property="og:([^"]+)"''')

html_files = sorted(LANDING.glob("*.html"))
if not html_files:
    err("no HTML files found under landing/")
    sys.exit(1)

# --- 1. internal file references resolve -----------------------------------
for page in html_files:
    text = page.read_text(encoding="utf-8")
    ids = set(ID_ATTR.findall(text))
    for m in REL_REF.finditer(text):
        target = (LANDING / m.group(1)[2:]).resolve()
        try:
            target.relative_to(LANDING)
        except ValueError:
            err(f"{page.name}: reference {m.group(1)} escapes landing/")
            continue
        if not target.exists():
            err(f"{page.name}: missing referenced file {m.group(1)}")
    for frag in FRAGMENT.findall(text):
        if frag not in ids:
            err(f"{page.name}: anchor #{frag} has no matching id on the page")

# --- 2. SEO basics on every page --------------------------------------------
for page in html_files:
    text = page.read_text(encoding="utf-8")
    name = page.name
    if not re.search(r"<title>[^<]+</title>", text):
        err(f"{name}: missing <title>")
    if 'name="description"' not in text:
        err(f"{name}: missing meta description")
    if 'rel="canonical"' not in text:
        err(f"{name}: missing canonical link")
    og = set(OG_TAG.findall(text))
    for tag in ("title", "description", "image"):
        if tag not in og:
            warn(f"{name}: missing og:{tag}")

# --- 2b. RSS autodiscovery on every page ---------------------------------------
for page in html_files:
    text = page.read_text(encoding="utf-8")
    if 'rel="alternate"' not in text or 'application/rss+xml' not in text:
        err(f"{page.name}: missing RSS autodiscovery link "
            f'(rel="alternate", type="application/rss+xml")')
    elif 'href="./feed.xml"' not in text:
        err(f"{page.name}: RSS autodiscovery link does not point at ./feed.xml")

# --- 3. sitemap entries map to real files on the canonical domain -----------
cname = (LANDING / "CNAME").read_text(encoding="utf-8").strip()
sitemap = LANDING / "sitemap.xml"
try:
    locs = [e.text.strip() for e in ET.parse(sitemap).getroot().iter()
            if e.tag.endswith("loc")]
except ET.ParseError as exc:
    err(f"sitemap.xml does not parse: {exc}")
    locs = []
prefix = f"https://{cname}/"
for loc in locs:
    if not loc.startswith(prefix):
        err(f"sitemap.xml: {loc} is not on the canonical domain {prefix}")
        continue
    path = loc[len(prefix):] or "index.html"
    if not (LANDING / path).exists():
        err(f"sitemap.xml: {loc} has no matching file in landing/")

# --- 4. robots.txt vs noindex pages ------------------------------------------
robots = (LANDING / "robots.txt").read_text(encoding="utf-8")
disallowed = {m.strip("/") for m in re.findall(r"^Disallow:\s*(\S+)",
                                               robots, re.M)}
for page in html_files:
    text = page.read_text(encoding="utf-8")
    noindex = 'name="robots" content="noindex' in text
    in_sitemap = any(loc.rstrip("/").endswith("/" + page.name)
                     for loc in locs)
    if noindex and in_sitemap:
        err(f"{page.name}: marked noindex but listed in sitemap.xml")
    if noindex and page.name.strip("/") not in disallowed:
        warn(f"{page.name}: noindex but not Disallow'd in robots.txt")
    if not noindex and page.name in disallowed:
        warn(f"{page.name}: Disallow'd in robots.txt but not noindex")

# --- 5. warn on binary assets nothing references ------------------------------
referenced: set[str] = set()
abs_re = re.compile(r'https://' + re.escape(cname) + r'/([^"\s<>]+)')
for page in html_files:
    text = page.read_text(encoding="utf-8")
    referenced.update(m.group(1)[2:] for m in REL_REF.finditer(text))
    # absolute same-domain URLs (e.g. og:image) count as references too
    referenced.update(abs_re.findall(text))
css = LANDING / "styles.css"
if css.exists():
    referenced.update(re.findall(r"url\(([^)]+)\)", css.read_text(encoding="utf-8")))
for asset in LANDING.iterdir():
    if asset.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg", ".ico"}:
        if asset.name not in referenced:
            warn(f"unreferenced asset: landing/{asset.name}")

# --- 6. RSS feed stays valid XML and on the canonical domain --------------------
from email.utils import parsedate_to_datetime

feed = LANDING / "feed.xml"
try:
    feed_root = ET.parse(feed).getroot()
except (ET.ParseError, OSError) as exc:
    err(f"feed.xml does not parse: {exc}")
    feed_root = None
if feed_root is not None:
    if feed_root.tag != "rss":
        err(f"feed.xml: root element is <{feed_root.tag}>, expected <rss>")
    channel = feed_root.find("channel")
    if channel is None:
        err("feed.xml: missing <channel>")
    else:
        for tag in ("title", "link", "description"):
            child = channel.find(tag)
            if child is None or not (child.text or "").strip():
                err(f"feed.xml: <channel> missing non-empty <{tag}>")
        # feed links must live on the canonical domain
        feed_prefix = f"https://{cname}/"
        chan_link = (channel.findtext("link") or "").strip()
        if chan_link and not chan_link.startswith(feed_prefix):
            err(f"feed.xml: <channel><link> {chan_link} is not on {feed_prefix}")
        atom_ns = "{http://www.w3.org/2005/Atom}"
        for atom_link in channel.findall(f"{atom_ns}link"):
            if atom_link.get("rel") == "self":
                href = (atom_link.get("href") or "").strip()
                if href != feed_prefix + "feed.xml":
                    err(f"feed.xml: atom:self link should be {feed_prefix}feed.xml")
        for item in channel.findall("item"):
            item_link = (item.findtext("link") or "").strip()
            if item_link and not item_link.startswith(feed_prefix):
                err(f"feed.xml: item link {item_link} is not on {feed_prefix}")
            pub = (item.findtext("pubDate") or "").strip()
            if pub:
                try:
                    parsedate_to_datetime(pub)
                except (ValueError, TypeError):
                    err(f"feed.xml: item pubDate {pub!r} is not valid RFC 822")
        built = (channel.findtext("lastBuildDate") or "").strip()
        if built:
            try:
                parsedate_to_datetime(built)
            except (ValueError, TypeError):
                err(f"feed.xml: lastBuildDate {built!r} is not valid RFC 822")

# --- report -------------------------------------------------------------------
for w in warnings:
    print(f"warning: {w}")
for e in errors:
    print(f"error: {e}")
print(f"\n{len(html_files)} pages checked, "
      f"{len(errors)} error(s), {len(warnings)} warning(s)")
sys.exit(1 if errors else 0)
