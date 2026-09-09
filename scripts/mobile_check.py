#!/usr/bin/env python3
"""Mobile-viewport regression checks for the news.wearedogs.net static landing site.

Catches horizontal-overflow and overlap *patterns* at a 390px target viewport
(the common phone width) using static analysis of the HTML/CSS: missing
viewport meta, fixed pixel widths at/above the target width, unguarded images,
absent small-screen breakpoints, and fixed-position elements (header, dot nav,
skip link) with no mobile adjustment.

Stdlib only — no dependencies, no headless browser, no network.

Honest limits: this cannot see rendered text. It will not catch overlapping
text caused by long words in narrow columns, font-loading swaps, or anything
that only appears at runtime. Pair it with a real phone-viewport QA pass
before calling a page done. What it *does* do is encode the lessons learned
so regressions (e.g. reintroducing a fixed 420px element) fail fast in CI.

Run from the repo root:
    python3 scripts/mobile_check.py
or point it at another repo root:
    python3 scripts/mobile_check.py --root /path/to/repo

Importable for tests: ``run_mobile_checks(root)`` returns (errors, warnings).
Errors fail the run; warnings are informational.
"""

import argparse
import re
import sys
from pathlib import Path

# The phone width we regression-test against. Everything static must fit.
TARGET_PX = 390
# A "small screen" breakpoint ceiling: responsive adjustments must exist at or below this.
SMALL_BP_PX = 880
MOBILE_BP_PX = 640

VIEWPORT_META = re.compile(r'<meta\s+name="viewport"\s+content="([^"]*)"', re.IGNORECASE)
PX_WIDTH = re.compile(r"(?:^|[;{])\s*(?:min-)?width\s*:\s*(\d+(?:\.\d+)?)\s*px", re.IGNORECASE)
MEDIA_QUERY = re.compile(r"@media\s*\(([^)]*)\)\s*\{", re.IGNORECASE)
MAX_WIDTH_IN_COND = re.compile(r"max-width\s*:\s*(\d+)\s*px", re.IGNORECASE)
IMG_WIDTH_ATTR = re.compile(r'<img\b[^>]*\bwidth="(\d+)"', re.IGNORECASE)
IMG_GUARD = re.compile(r"img\s*\{[^}]*max-width\s*:\s*100\s*%", re.IGNORECASE | re.DOTALL)
FIXED_POS = re.compile(r"position\s*:\s*fixed", re.IGNORECASE)
NOWRAP_WITH_PX = re.compile(
    r"([^{}]+)\{[^}]*(?:min-)?width\s*:\s*(\d+(?:\.\d+)?)\s*px[^}]*white-space\s*:\s*nowrap"
    r"|([^{}]+)\{[^}]*white-space\s*:\s*nowrap[^}]*(?:min-)?width\s*:\s*(\d+(?:\.\d+)?)\s*px",
    re.IGNORECASE | re.DOTALL,
)


def media_blocks(css: str) -> list[tuple[str, str]]:
    """Return (condition, body) for every @media block, brace-matched."""
    blocks = []
    for m in MEDIA_QUERY.finditer(css):
        depth = 1
        i = m.end()
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        blocks.append((m.group(1), css[m.end() : i - 1]))
    return blocks


def strip_media(css: str) -> str:
    """Return CSS with all @media blocks removed (top-level rules only)."""
    spans = []
    for m in MEDIA_QUERY.finditer(css):
        depth = 1
        i = m.end()
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        spans.append((m.start(), i))
    out, last = [], 0
    for start, end in spans:
        out.append(css[last:start])
        last = end
    out.append(css[last:])
    return "".join(out)


def strip_comments(css: str) -> str:
    """Remove /* ... */ comments so they can't pollute selector parsing."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def selector_tokens(selector: str) -> set[str]:
    """Class names, id names, and element name used by a selector."""
    toks = set()
    for m in re.finditer(r"\.([\w-]+)|#([\w-]+)", selector):
        toks.add(m.group(1) or m.group(2))
    base = re.split(r"[.#:\s>+~\[,]", selector.strip(), maxsplit=1)[0]
    if base and re.fullmatch(r"[a-zA-Z][\w-]*", base):
        toks.add(base)
    return toks


def css_tokens(css: str) -> set[str]:
    """Class/id names and element names referenced anywhere in *css*."""
    toks = set()
    for m in re.finditer(r"\.([\w-]+)|#([\w-]+)", css):
        toks.add(m.group(1) or m.group(2))
    for m in re.finditer(r"(?<![.#:\w-])([a-zA-Z][\w-]*)\s*[{,]", css):
        toks.add(m.group(1))
    return toks


OFFSCREEN_OFFSET = re.compile(r"(?:left|right|top|bottom)\s*:\s*(-?\d+(?:\.\d+)?)\s*px", re.IGNORECASE)

VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)(\s[^<>]*)?/?>", re.DOTALL)
CLASS_ATTR = re.compile(r'class="([^"]*)"')


def subtree_classes(html: str, cls: str) -> set[str]:
    """All class names inside elements carrying class *cls* (inclusive).

    Lets the mobile check treat a fixed container (e.g. ``header.site``) as
    adjusted when a *descendant* (e.g. ``.nav``) is restyled at small widths.
    """
    found: set[str] = set()
    for m in re.finditer(r"<([a-zA-Z][\w-]*)(\s[^<>]*)>", html):
        attrs = m.group(2) or ""
        cm = CLASS_ATTR.search(attrs)
        if not (cm and cls in cm.group(1).split()):
            continue
        found.update(cm.group(1).split())
        depth, i = 1, m.end()
        for t in TAG.finditer(html, i):
            closing, name, tattrs, raw = t.group(1), t.group(2).lower(), t.group(3) or "", t.group(0)
            if raw.endswith("/>") or name in VOID_ELEMENTS:
                pass
            elif closing:
                depth -= 1
            else:
                depth += 1
            tc = CLASS_ATTR.search(tattrs)
            if tc:
                found.update(tc.group(1).split())
            if depth == 0:
                break
    return found


def fixed_selectors(css: str) -> list[str]:
    """Selectors of top-level rules that use position: fixed.

    Rules that park the element fully off-screen (e.g. the skip link at
    left: -999px) are excluded — they can't crowd or overlap content.
    """
    top = strip_media(strip_comments(css))
    found = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", top):
        selector, decls = " ".join(m.group(1).split()), m.group(2)
        if not (selector and FIXED_POS.search(decls)):
            continue
        offscreen = any(
            abs(float(v)) >= TARGET_PX for v in OFFSCREEN_OFFSET.findall(decls)
        )
        if not offscreen:
            found.append(selector)
    return found


def run_mobile_checks(root: Path) -> tuple[list[str], list[str]]:
    """Run all mobile-viewport regression checks against the repo at *root*.

    Returns (errors, warnings). Safe to call on throwaway fixture trees.
    """
    landing = root / "landing"
    errors: list[str] = []
    warnings: list[str] = []

    pages = sorted(landing.glob("*.html")) if landing.is_dir() else []
    css_path = landing / "styles.css"
    css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""

    # 1. Every page must scale to the device width.
    for page in pages:
        html = page.read_text(encoding="utf-8")
        m = VIEWPORT_META.search(html)
        if not m:
            errors.append(f"{page.name}: missing <meta name=\"viewport\"> — page will not scale on phones")
        elif "width=device-width" not in m.group(1):
            errors.append(f"{page.name}: viewport meta lacks width=device-width")

    if not css:
        errors.append("landing/styles.css is missing")
        return errors, warnings

    # 2. No fixed pixel width at or above the 390px target viewport.
    for m in PX_WIDTH.finditer(css):
        if float(m.group(1)) >= TARGET_PX:
            errors.append(
                f"styles.css: fixed width {m.group(1)}px >= {TARGET_PX}px target viewport — "
                "will overflow on phones"
            )

    # 3. Images must be guarded against overflowing narrow viewports.
    if not IMG_GUARD.search(css):
        errors.append(
            "styles.css: missing `img { max-width: 100%; }` guard — images can overflow on phones"
        )
    for page in pages:
        html = page.read_text(encoding="utf-8")
        for m in IMG_WIDTH_ATTR.finditer(html):
            if int(m.group(1)) >= TARGET_PX:
                errors.append(
                    f"{page.name}: <img width=\"{m.group(1)}\"> >= {TARGET_PX}px target viewport"
                )

    # 4. Responsive breakpoints must exist for small screens.
    small_bps = [
        int(n)
        for cond, _ in media_blocks(css)
        for n in MAX_WIDTH_IN_COND.findall(cond)
        if int(n) <= SMALL_BP_PX
    ]
    if not small_bps:
        errors.append(
            f"styles.css: no @media (max-width) breakpoint at <= {SMALL_BP_PX}px — "
            "nothing adapts the layout for phones"
        )

    # 5. Fixed-position elements must be adjusted for small screens,
    #    otherwise they overlap or crowd content at 390px.
    blocks = media_blocks(css)
    small_bodies = [
        body
        for cond, body in blocks
        if any(int(n) <= SMALL_BP_PX for n in MAX_WIDTH_IN_COND.findall(cond))
    ]
    small_css = "\n".join(small_bodies)
    small_tokens = css_tokens(small_css)
    page_htmls = [p.read_text(encoding="utf-8") for p in pages]
    for selector in fixed_selectors(css):
        names = set(selector_tokens(selector))
        for token in selector_tokens(selector):
            for html in page_htmls:
                names |= subtree_classes(html, token)
        if not (names & small_tokens):
            errors.append(
                f"styles.css: fixed-position `{selector}` has no small-screen @media adjustment — "
                "risk of overlap/crowding at 390px"
            )

    # 6. nowrap + fixed width is a classic overlap trigger.
    for m in NOWRAP_WITH_PX.finditer(css):
        selector = (m.group(1) or m.group(3) or "").strip()
        width = m.group(2) or m.group(4)
        if float(width) >= 300:
            warnings.append(
                f"styles.css: `{selector}` combines white-space: nowrap with {width}px width — "
                "check for text overlap at 390px"
            )

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mobile-viewport regression checks (static, stdlib-only).")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)

    errors, warnings = run_mobile_checks(args.root)
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
