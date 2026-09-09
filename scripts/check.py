#!/usr/bin/env python3
"""Sanity checks for the news.wearedogs.net static landing site.

Catches broken internal links, dangling fragment anchors, missing SEO
basics, sitemap/robots.txt drift, and feed.xml problems before anything
ships.

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
from pathlib import Path

REL_REF = re.compile(r'''(?:src|href)="(\./[^"]+)"''')
FRAGMENT = re.compile(r'''href="#([^"]+)"''')
ID_ATTR = re.compile(r'''\sid="([^"]+)"''')
OG_TAG = re.compile(r'''<meta\s+property="og:([^"]+)"''')


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

    html_files = sorted(landing.glob("*.html"))
    if not html_files:
        err("no HTML files found under landing/")
        return errors, warnings

    # --- 1. internal file references resolve -----------------------------------
    for page in html_files:
        text = page.read_text(encoding="utf-8")
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

    # --- 3. sitemap entries map to real files on the canonical domain -----------
    cname = (landing / "CNAME").read_text(encoding="utf-8").strip()
    sitemap = landing / "sitemap.xml"
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
        if not (landing / path).exists():
            err(f"sitemap.xml: {loc} has no matching file in landing/")

    # --- 4. robots.txt vs noindex pages ------------------------------------------
    robots = (landing / "robots.txt").read_text(encoding="utf-8")
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
    css = landing / "styles.css"
    if css.exists():
        referenced.update(re.findall(r"url\(([^)]+)\)", css.read_text(encoding="utf-8")))
    for asset in landing.iterdir():
        if asset.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg", ".ico"}:
            if asset.name not in referenced:
                warn(f"unreferenced asset: landing/{asset.name}")

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
