#!/usr/bin/env python3
"""Sanity checks for the news.wearedogs.net static landing site.

Catches broken internal links, dangling fragment anchors, missing SEO
basics, sitemap/robots.txt drift, feed.xml problems, and broken
canonical-domain URLs hiding in <meta> content= attributes (og:image,
twitter:image) before anything ships.

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
        og = set(OG_TAG.findall(text))
        for tag in ("title", "description", "image"):
            if tag not in og:
                warn(f"{name}: missing og:{tag}")

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
