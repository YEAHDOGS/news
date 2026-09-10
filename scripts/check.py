#!/usr/bin/env python3
"""Sanity checks for the news.wearedogs.net static landing site.

Catches broken internal links, dangling fragment anchors, missing SEO
basics, sitemap/robots.txt drift (sitemap entries must map to real files
*and* every indexable page must appear in the sitemap), feed.xml problems,
and broken canonical-domain URLs hiding in <meta> content= attributes
(og:image, twitter:image) before anything ships.

The canonical <link> itself is also validated (section 9): it must be
absolute, on the canonical domain, and self-referential — an off-domain
or wrong-page canonical quietly hands search indexing elsewhere.

Section 10 validates social-card completeness: og:title, og:description,
and an absolute og:image on every page, og:url matching the page's
canonical URL, and a twitter:card with an image when it promises a large
preview. Feed <item> entries are validated alongside the channel
(section 6): title/link/description, on-domain links, RFC 822 pubDates.

Section 11 checks image accessibility: every <img> must carry an alt
attribute (alt="" is the explicit decorative marker); a missing alt is
an error, a whitespace-only alt a warning.

Errors fail the run; warnings are informational.
Stdlib only — no dependencies to install.

Run from the repo root:
    python3 scripts/check.py
or point it at another repo root:
    python3 scripts/check.py --root /path/to/repo

Importable for tests: ``run_checks(root)`` returns (errors, warnings).
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REL_REF = re.compile(r'''(?:src|href)="(\./[^"]+)"''')
ABS_URL = re.compile(r'''(?:src|href)="(https?://[^"]+)"''')
FRAGMENT = re.compile(r'''href="#([^"]+)"''')
ID_ATTR = re.compile(r'''\sid="([^"]+)"''')
OG_TAG = re.compile(r'''<meta\s+property="og:([^"]+)"''')
META_CONTENT_URL = re.compile(
    r'''<meta[^>]*?content="(https?://[^"]+)"''', re.IGNORECASE)
CANONICAL_LINK = re.compile(
    r'''<link[^>]*?rel=["']canonical["'][^>]*?href=["']([^"']*)["']''',
    re.IGNORECASE)
CANONICAL_LINK_ALT = re.compile(
    r'''<link[^>]*?href=["']([^"']*)["'][^>]*?rel=["']canonical["']''',
    re.IGNORECASE)
IMG_TAG = re.compile(r'''<img\b[^>]*>''', re.IGNORECASE)
IMG_ALT = re.compile(r'''\balt\s*=\s*(?:"([^"]*)"|'([^']*)')''',
                     re.IGNORECASE)
IMG_SRC = re.compile(r'''\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')''',
                     re.IGNORECASE)


def meta_property_values(text: str, attr: str, prefix: str) -> dict:
    """Map <meta> *attr*="*prefix*:prop" names to their content= values.

    e.g. meta_property_values(html, "property", "og") -> {"title": "...",
    "image": "..."}. Works regardless of attribute order. A name that
    appears with no content= attribute maps to None; the first
    occurrence of a repeated name wins.
    """
    values: dict[str, str | None] = {}
    tag_re = re.compile(
        r'''<meta\s+[^>]*?''' + re.escape(attr) + r'''="'''
        + re.escape(prefix) + r''':([a-z_:]+)"[^>]*?>''',
        re.IGNORECASE)
    content_re = re.compile(r'''content="([^"]*)"''', re.IGNORECASE)
    for m in tag_re.finditer(text):
        prop = m.group(1).lower()
        if prop in values:
            continue
        c = content_re.search(m.group(0))
        values[prop] = c.group(1) if c else None
    return values


def run_checks(root: Path) -> tuple[list[str], list[str]]:
    """Run all sanity checks against the repo at *root*.

    Returns (errors, warnings). Each section only touches local lists,
    so this is safe to call on throwaway fixture trees from tests.
    """
    landing = root / "landing"
    errors: list[str] = []
    warnings: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    def warn(msg: str) -> None:
        warnings.append(msg)

    def read_text(path: Path) -> str | None:
        """Read a UTF-8 text file, turning I/O problems into errors.

        Returns None on any failure; the failure is recorded in *errors*
        so the checker reports it and moves on instead of crashing.
        """
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            err(f"{path.name} is missing")
            return None
        except UnicodeDecodeError:
            err(f"{path.name} is not valid UTF-8")
            return None
        except OSError as exc:
            err(f"{path.name} cannot be read: {exc}")
            return None

    html_files = sorted(landing.glob("*.html"))
    if not html_files:
        err("no HTML files found under landing/")
        return errors, warnings

    page_cache: dict[Path, str] = {}

    def page_text(page: Path) -> str | None:
        """Cached read of an HTML page; None if unreadable (error recorded)."""
        if page not in page_cache:
            text = read_text(page)
            if text is not None:
                page_cache[page] = text
        return page_cache.get(page)

    # --- 1. internal file references resolve -----------------------------------
    for page in html_files:
        text = page_text(page)
        if text is None:
            continue
        ids = set(ID_ATTR.findall(text))
        for m in REL_REF.finditer(text):
            target = (landing / m.group(1)[2:]).resolve()
            try:
                target.relative_to(landing)
            except ValueError:
                err(f"{page.name}: reference {m.group(1)} escapes landing/")
                continue
            if not target.exists():
                err(f"{page.name}: missing referenced file {m.group(1)}")
        for frag in FRAGMENT.findall(text):
            if frag not in ids:
                err(f"{page.name}: anchor #{frag} has no matching id on the page")

    # --- 1b. absolute same-domain links must resolve to real files --------------
    # Pages link the canonical domain outright
    # (e.g. https://news.wearedogs.net/thanks.html), and those bypass the
    # REL_REF scan in section 1 — verify them here instead. External links
    # are left alone: the checker never phones home.
    try:
        _cname = (landing / "CNAME").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        _cname = ""  # section 3 reports the read problem; don't double-report

    def resolve_same_domain(page: Path, url: str) -> None:
        """Verify one absolute same-domain URL maps to a real landing/ file.

        URLs off the canonical domain are ignored — the checker never
        phones home. A bare domain root maps to index.html.
        """
        prefix = f"https://{_cname}/"
        if url == prefix.rstrip("/"):
            rel = ""
        elif url.startswith(prefix):
            rel = url[len(prefix):]
        else:
            return  # external link, not ours to verify
        rel = rel.split("#", 1)[0].split("?", 1)[0] or "index.html"
        target = (landing / rel).resolve()
        try:
            target.relative_to(landing)
        except ValueError:
            err(f"{page.name}: internal link {url} escapes landing/")
            return
        if not target.exists():
            err(f"{page.name}: internal link {url} "
                f"has no matching file in landing/")

    if _cname:
        for page in html_files:
            text = page_text(page)
            if text is None:
                continue
            for m in ABS_URL.finditer(text):
                resolve_same_domain(page, m.group(1))

    # --- 2. SEO basics on every page --------------------------------------------
    for page in html_files:
        text = page_text(page)
        if text is None:
            continue
        name = page.name
        if not re.search(r"<title>[^<]+</title>", text):
            err(f"{name}: missing <title>")
        if 'name="description"' not in text:
            err(f"{name}: missing meta description")
        if 'rel="canonical"' not in text:
            err(f"{name}: missing canonical link")
        # og:* completeness (title/description/image) is enforced as
        # errors in section 10; nothing double-reports here.

    # --- 3. sitemap entries map to real files on the canonical domain -----------
    cname_text = read_text(landing / "CNAME")
    sitemap_text = read_text(landing / "sitemap.xml")
    canon_host: str | None = None
    if cname_text is not None:
        canon_host = cname_text.strip() or None
    prefix: str | None = None
    locs: list[str] = []
    if cname_text is not None and sitemap_text is not None:
        cname = cname_text.strip()
        if not cname:
            err("CNAME is empty; cannot determine the canonical domain")
        else:
            prefix = f"https://{cname}/"
            try:
                locs = [e.text.strip()
                        for e in ET.fromstring(sitemap_text).iter()
                        if e.tag.endswith("loc")]
            except ET.ParseError as exc:
                err(f"sitemap.xml does not parse: {exc}")
    if prefix is not None:
        for loc in locs:
            if not loc.startswith(prefix):
                err(f"sitemap.xml: {loc} is not on the canonical domain {prefix}")
                continue
            path = loc[len(prefix):] or "index.html"
            if not (landing / path).exists():
                err(f"sitemap.xml: {loc} has no matching file in landing/")

    # --- 3b. every indexable page is listed in the sitemap --------------------
    # Section 3 validates sitemap -> files; this is the reverse direction. A
    # new public page that never makes it into sitemap.xml still passes every
    # other check, so crawlers silently never get a hint it exists. Pages
    # crawlers are told to skip (noindex, or Disallow'd in robots.txt) are
    # exempt — listing those would contradict the exclusion. This is a
    # warning, not an error: nothing is broken, a crawl hint is just missing.
    sitemap_ok = prefix is not None and not any(
        e.startswith("sitemap.xml does not parse") for e in errors)
    if sitemap_ok:
        # robots.txt tells us which pages crawlers are told to skip; it is
        # read here (again) because section 4's read happens further down.
        # A missing/unreadable robots.txt is section 4's error — without it
        # we can't know the exemptions, so coverage checking is skipped.
        try:
            robots_source = (landing / "robots.txt").read_text(
                encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            robots_source = None
    if sitemap_ok and robots_source is not None:
        sitemap_paths = set()
        for loc in locs:
            if loc.startswith(prefix):
                p = loc[len(prefix):].split("#", 1)[0].split("?", 1)[0]
                sitemap_paths.add(p or "index.html")
        disallowed_paths = {m.strip("/")
                            for m in re.findall(r"^Disallow:\s*(\S+)",
                                                robots_source, re.M)}
        for page in html_files:
            text = page_text(page)
            if text is None:
                continue  # unreadable page is already an error
            if 'name="robots" content="noindex' in text:
                continue
            if page.name in disallowed_paths:
                continue
            key = "index.html" if page.name == "index.html" else page.name
            if key not in sitemap_paths:
                warn(f"{page.name}: indexable page not listed in sitemap.xml; "
                     f"add {prefix}{'' if key == 'index.html' else key}")

    # --- 4. robots.txt vs noindex pages ------------------------------------------
    robots_text = read_text(landing / "robots.txt")
    disallowed: set[str] = set()
    if robots_text is not None:
        disallowed = {m.strip("/") for m in re.findall(r"^Disallow:\s*(\S+)",
                                                       robots_text, re.M)}
    for page in html_files:
        text = page_text(page)
        if text is None:
            continue
        noindex = 'name="robots" content="noindex' in text
        # mirror section 3's normalization: a root URL means index.html
        in_sitemap = prefix is not None and any(
            (loc[len(prefix):] or "index.html") == page.name
            for loc in locs if loc.startswith(prefix))
        if noindex and in_sitemap:
            err(f"{page.name}: marked noindex but listed in sitemap.xml")
        if noindex and page.name.strip("/") not in disallowed:
            warn(f"{page.name}: noindex but not Disallow'd in robots.txt")
        if not noindex and page.name in disallowed:
            warn(f"{page.name}: Disallow'd in robots.txt but not noindex")

    # --- 5. warn on binary assets nothing references ------------------------------
    referenced: set[str] = set()
    abs_re = None
    if cname_text is not None and cname_text.strip():
        abs_re = re.compile(r'https://' + re.escape(cname_text.strip())
                            + r'/([^"\s<>]+)')
    for page in html_files:
        text = page_text(page)
        if text is None:
            continue
        referenced.update(m.group(1)[2:] for m in REL_REF.finditer(text))
        # absolute same-domain URLs (e.g. og:image) count as references too
        if abs_re is not None:
            referenced.update(abs_re.findall(text))
    css = landing / "styles.css"
    if css.exists():
        css_text = read_text(css)
        if css_text is not None:
            referenced.update(re.findall(r"url\(([^)]+)\)", css_text))
    for asset in landing.iterdir():
        if asset.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg", ".ico"}:
            if asset.name not in referenced:
                warn(f"unreferenced asset: landing/{asset.name}")

    # --- 6. feed.xml must be a valid RSS channel on the canonical domain --------
    feed = landing / "feed.xml"
    feed_source = None
    feed_built = None  # datetime from <lastBuildDate>, set when parseable
    try:
        feed_source = feed.read_text(encoding="utf-8")
    except FileNotFoundError:
        warn("feed.xml is missing; readers expect one at /feed.xml")
    except UnicodeDecodeError:
        err("feed.xml is not valid UTF-8")
    except OSError as exc:
        err(f"feed.xml cannot be read: {exc}")
    if feed_source is not None:
        try:
            channel = ET.fromstring(feed_source).find("channel")
            feed_parsed = True
        except ET.ParseError as exc:
            err(f"feed.xml does not parse: {exc}")
            channel = None
            feed_parsed = False
        if not feed_parsed:
            pass
        elif channel is None:
            err("feed.xml: no <channel> element found")
        elif channel is not None:
            for field in ("title", "link", "description"):
                if channel.findtext(field, default="").strip() == "":
                    err(f"feed.xml: channel is missing <{field}>")
            atom_link = channel.find(
                "{http://www.w3.org/2005/Atom}link")
            expected_self = (f"https://{canon_host}/feed.xml"
                             if canon_host is not None else None)
            if atom_link is None:
                if expected_self is not None:
                    warn("feed.xml: no atom self-link (expected "
                         f"<link href=\"{expected_self}\" rel=\"self\" />)")
                else:
                    warn("feed.xml: no atom self-link (CNAME unreadable, "
                         "can't compute the canonical URL)")
            elif expected_self is not None and atom_link.get("href") != expected_self:
                err(f"feed.xml: atom self-link {atom_link.get('href')!r} "
                    f"is not the canonical {expected_self!r}")
            build = channel.findtext("lastBuildDate", default="").strip()
            if not build:
                warn("feed.xml: no <lastBuildDate>; readers can't tell if it's fresh")
            else:
                try:
                    built = datetime.strptime(build, "%a, %d %b %Y %H:%M:%S %z")
                except ValueError:
                    try:
                        built = datetime.strptime(build, "%a, %d %b %Y %H:%M:%S %Z")
                    except ValueError:
                        built = None
                if built is None:
                    warn(f"feed.xml: <lastBuildDate> is not RFC 822: {build!r}")
                elif (datetime.now(timezone.utc) - built).days > 30:
                    warn(f"feed.xml: <lastBuildDate> {build!r} is over 30 days old")
                else:
                    feed_built = built
            # --- 6b. every <item> needs its own title/link/description -----
            # A channel-only feed is valid RSS (stories arrive at launch),
            # but once items exist each one must be self-sufficient: an
            # item with no link points readers nowhere, and an off-domain
            # link quietly syndicates someone else's content.
            for item in channel.findall("item"):
                item_link = item.findtext("link", default="").strip()
                for field in ("title", "link", "description"):
                    if item.findtext(field, default="").strip() == "":
                        err(f"feed.xml: <item> is missing <{field}>")
                if (item_link and canon_host is not None
                        and not item_link.startswith(f"https://{canon_host}/")):
                    err(f"feed.xml: <item> link {item_link!r} is not on the "
                        f"canonical domain {canon_host}")
                pub_date = item.findtext("pubDate", default="").strip()
                if pub_date:
                    try:
                        datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
                    except ValueError:
                        warn(f"feed.xml: <item> <pubDate> is not RFC 822: "
                             f"{pub_date!r}")

    # --- 7. footer "last updated" line must match feed.xml --------------------
    # The landing page shows a visible feed freshness line; its date must be
    # the same day as feed.xml's <lastBuildDate> so they can't drift apart.
    if feed_built is not None:
        index_source = read_text(landing / "index.html")
        if index_source is not None:
            m = re.search(
                r'class="[^"]*\bfeed-line\b[^"]*"[^>]*>'
                r'.*?<time\s+datetime="(\d{4}-\d{2}-\d{2})"',
                index_source, re.DOTALL)
            if not m:
                err('index.html: missing visible "last updated" feed line '
                    '(<p class="feed-line"> containing <time datetime="YYYY-MM-DD">)')
            elif m.group(1) != feed_built.strftime("%Y-%m-%d"):
                err(f"index.html: feed-line date {m.group(1)} does not match "
                    f'feed.xml <lastBuildDate> ({feed_built.strftime("%Y-%m-%d")})')

    # --- 8. absolute same-domain URLs in meta content= must resolve -----------
    # Section 1b only scans src=/href= attributes, so canonical-domain URLs
    # in <meta> tags (og:image, og:url, twitter:image ...) slip through.
    # A renamed og.png with a stale og:image means broken social previews.
    if _cname:
        for page in html_files:
            text = page_text(page)
            if text is None:
                continue
            for m in META_CONTENT_URL.finditer(text):
                resolve_same_domain(page, m.group(1))

    # --- 9. canonical links must be absolute, on-domain, self-referential -----
    # Section 2 only checks a canonical link *exists*. An off-domain
    # canonical quietly hands search indexing to someone else's URL, and a
    # canonical naming the wrong on-domain page marks this page as a
    # duplicate. Verify both from CNAME + the page list — no network needed.
    # (Section 1b already verifies an absolute canonical's target file
    # exists; this section validates the canonical's own meaning.)
    if canon_host is not None:
        canon_lower = canon_host.lower()
        for page in html_files:
            text = page_text(page)
            if text is None:
                continue
            cm = (CANONICAL_LINK.search(text)
                  or CANONICAL_LINK_ALT.search(text))
            if cm is None:
                continue  # section 2 already reported the missing canonical
            href = cm.group(1).strip()
            if not href:
                err(f"{page.name}: canonical link has an empty href")
                continue
            if href.lower().startswith(("http://", "https://")):
                host = href.split("://", 1)[1].split("/", 1)[0]
                if host.lower() != canon_lower:
                    err(f"{page.name}: canonical {href!r} is off the "
                        f"canonical domain {canon_host}")
                    continue
                parts = href.split("://", 1)[1].split("/", 1)
                canon_path = parts[1] if len(parts) > 1 else ""
            else:
                # relative canonicals are legal (they resolve against the
                # page URL) but best practice is absolute; keep this soft
                warn(f"{page.name}: canonical {href!r} is relative; "
                     "canonical URLs should be absolute")
                canon_path = href
            canon_path = canon_path.split("#", 1)[0].split("?", 1)[0]
            canon_path = canon_path.lstrip("./")  # "./x.html" -> "x.html"
            # a bare-domain canonical (https://host/) means index.html
            if canon_path == "":
                canon_path = "index.html"
            expected = "index.html" if page.name == "index.html" else page.name
            if canon_path != expected:
                err(f"{page.name}: canonical {href!r} is not self-referential "
                    f"(expected https://{canon_host}/{'' if expected == 'index.html' else expected})")

    # --- 10. social-card completeness (Open Graph + Twitter Card) ----------
    # Section 2 used to warn that og:title/description/image exist; here the
    # core social card is enforced as errors. A share with no title or a
    # relative og:image ships a broken preview to every platform — that's
    # not cosmetic, it's the page's public face. og:url must also name the
    # page's own canonical URL (normalized the same way as section 9's
    # self-referential check) so shares aggregate on the right link instead
    # of a duplicate.
    def normalize_canonical_url(href: str) -> str | None:
        """Return the landing-relative path a canonical-domain URL names.

        None when the URL is relative or off-domain (the relative and
        off-domain cases are already reported by section 9).
        """
        href = href.strip()
        if not href.lower().startswith(("http://", "https://")):
            return None
        host = href.split("://", 1)[1].split("/", 1)[0]
        if canon_host is None or host.lower() != canon_host.lower():
            return None
        parts = href.split("://", 1)[1].split("/", 1)
        path = parts[1] if len(parts) > 1 else ""
        path = path.split("#", 1)[0].split("?", 1)[0].lstrip("./")
        return path or "index.html"

    for page in html_files:
        text = page_text(page)
        if text is None:
            continue
        name = page.name
        og = meta_property_values(text, "property", "og")
        tw = meta_property_values(text, "name", "twitter")
        for tag in ("title", "description"):
            if tag not in og:
                err(f"{name}: missing og:{tag}")
            elif not (og[tag] or "").strip():
                err(f"{name}: og:{tag} has empty content")
        if "image" not in og:
            err(f"{name}: missing og:image")
        else:
            img = (og["image"] or "").strip()
            if not img:
                err(f"{name}: og:image has empty content")
            elif not img.lower().startswith(("http://", "https://")):
                err(f"{name}: og:image {img!r} is relative; social scrapers "
                    "need an absolute URL")
            # section 8 already verifies an absolute same-domain og:image
            # resolves to a real file in landing/
        if "url" not in og or not (og["url"] or "").strip():
            err(f"{name}: missing og:url")
        else:
            og_url = og["url"].strip()
            cm = (CANONICAL_LINK.search(text)
                  or CANONICAL_LINK_ALT.search(text))
            if cm is not None:
                canon_norm = normalize_canonical_url(cm.group(1))
                og_norm = normalize_canonical_url(og_url)
                # only compare when both are absolute and on-domain; the
                # other cases are section 9's job
                if (canon_norm is not None and og_norm is not None
                        and og_norm != canon_norm):
                    err(f"{name}: og:url {og_url!r} does not match the page's "
                        f"canonical URL {cm.group(1).strip()!r}")
        if "card" not in tw or not (tw["card"] or "").strip():
            err(f"{name}: missing twitter:card")
        elif tw["card"].strip() == "summary_large_image":
            has_image = (("image" in tw and (tw["image"] or "").strip())
                         or ("image" in og and (og["image"] or "").strip()))
            if not has_image:
                err(f"{name}: twitter:card is summary_large_image but no "
                    "twitter:image/og:image is set; the large preview will "
                    "render without an image")

    # --- 11. every <img> must carry an alt attribute -----------------------
    # Screen readers announce the src filename when alt is missing, which
    # is never the right announcement. An explicitly empty alt="" marks a
    # decorative image and passes; a missing alt fails the run, and a
    # whitespace-only alt warns (it announces nothing but was never
    # declared decorative — almost always an accident).
    for page in html_files:
        text = page_text(page)
        if text is None:
            continue
        name = page.name
        for img in IMG_TAG.finditer(text):
            tag = img.group(0)
            alt_m = IMG_ALT.search(tag)
            src_m = IMG_SRC.search(tag)
            src = ((src_m.group(1) if src_m.group(1) is not None
                    else src_m.group(2)) if src_m else "?")
            if alt_m is None:
                err(f"{name}: <img> is missing alt text (src={src!r}); "
                    'decorative images should use alt="" explicitly')
            else:
                alt = (alt_m.group(1) if alt_m.group(1) is not None
                       else alt_m.group(2))
                if alt and not alt.strip():
                    warn(f"{name}: <img> has a whitespace-only alt attribute "
                         f"(src={src!r}); use alt=\"\" only if the image is "
                         "purely decorative")

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]),
                        help="repo root containing the landing/ directory")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    html_files = sorted((root / "landing").glob("*.html"))
    errors, warnings = run_checks(root)
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print(f"\n{len(html_files)} pages checked, "
          f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
