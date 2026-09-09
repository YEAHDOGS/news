#!/usr/bin/env python3
"""Sanity checks for the news.wearedogs.net static landing site.

Catches broken internal links, dangling fragment anchors, missing SEO
basics, and sitemap/robots.txt drift before anything ships.

Errors fail the run; warnings are informational.
Stdlib only — no dependencies to install.
"""

import re
import struct
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
MAX_FEED_BYTES = 512 * 1024  # aggregators choke on bigger files; cap it


def _safe_xml_root(path: Path, what: str):
    """Parse an XML file with bomb guards: size cap + no DOCTYPE.

    ElementTree never resolves *external* entities, but internal entity
    expansion ("billion laughs") still eats memory — so DTD declarations
    are banned outright. Returns the root element, or None after
    recording an error.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        err(f"{what} is unreadable: {exc}")
        return None
    if size > MAX_FEED_BYTES:
        err(f"{what} is {size} bytes — over the {MAX_FEED_BYTES}-byte cap; "
            "trim the feed")
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        err(f"{what} is not valid UTF-8 text: {exc}")
        return None
    if "<!doctype" in text.lower():
        err(f"{what} contains a DOCTYPE declaration — DTDs/entity "
            "declarations are not allowed (XML bomb risk)")
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        err(f"{what} does not parse: {exc}")
        return None


cname = (LANDING / "CNAME").read_text(encoding="utf-8").strip()
sitemap = LANDING / "sitemap.xml"
sitemap_root = _safe_xml_root(sitemap, "sitemap.xml")
locs = ([e.text.strip() for e in sitemap_root.iter()
         if e.tag.endswith("loc") and e.text and e.text.strip()]
        if sitemap_root is not None else [])
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

# feed_prefix is needed by §6, §7, and §8 — define it unconditionally so a
# feed.xml parse failure cannot cascade into a NameError later.
feed_prefix = f"https://{cname}/"

feed = LANDING / "feed.xml"
feed_root = _safe_xml_root(feed, "feed.xml")
# feed links must live on the canonical domain — and only via safe
# schemes. Aggregators render item <link>s as clickable, so anything but
# http(s) (javascript:, data:, ...) is a live XSS vector.
SCHEME_RE = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+.-]*):")
DANGEROUS_SCHEMES = {"javascript", "data", "vbscript", "file",
                     "about", "blob"}


def _dangerous_scheme(url: str):
    """Return the dangerous scheme name if `url` uses one, else None."""
    m = SCHEME_RE.match(url.strip())
    if m and m.group(1).lower() in DANGEROUS_SCHEMES:
        return m.group(1).lower()
    return None


def _check_url_scheme(where: str, url: str) -> None:
    # `where` reads like "item link" / "<channel><link>" / "item <guid>".
    bad = _dangerous_scheme(url)
    if bad:
        err(f"feed.xml: {where} {url.strip()} uses a dangerous URL "
            f"scheme ({bad}:) — http(s) only")
    elif (url.strip() and not url.strip().startswith(feed_prefix)
            and SCHEME_RE.match(url)):
        err(f"feed.xml: {where} {url.strip()} is not on "
            f"{feed_prefix}")


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
        chan_link = (channel.findtext("link") or "").strip()
        _check_url_scheme("<channel><link>", chan_link)
        atom_ns = "{http://www.w3.org/2005/Atom}"
        for atom_link in channel.findall(f"{atom_ns}link"):
            if atom_link.get("rel") == "self":
                href = (atom_link.get("href") or "").strip()
                if href != feed_prefix + "feed.xml":
                    err("feed.xml: atom:self link should be "
                        f"{feed_prefix}feed.xml")
        for item in channel.findall("item"):
            # item links must be absolute-on-canonical or relative (a
            # relative link is local by definition; §7 verifies it resolves)
            _check_url_scheme("item link", item.findtext("link") or "")
            # <guid> defaults to isPermaLink="true" per the RSS spec, so a
            # permalink guid is a link too — same scheme policy applies
            guid = item.find("guid")
            if guid is not None:
                gtext = (guid.text or "").strip()
                if (guid.get("isPermaLink", "true").lower() != "false"
                        and gtext):
                    _check_url_scheme("item <guid>", gtext)
            pub = (item.findtext("pubDate") or "").strip()
            if pub:
                try:
                    parsedate_to_datetime(pub)
                except (ValueError, TypeError):
                    err(f"feed.xml: item pubDate {pub!r} is not valid "
                        "RFC 822")
        built = (channel.findtext("lastBuildDate") or "").strip()
        if built:
            try:
                parsedate_to_datetime(built)
            except (ValueError, TypeError):
                err(f"feed.xml: lastBuildDate {built!r} is not valid "
                    "RFC 822")

    # --- 6b. feed titles/descriptions must not smuggle active markup (XSS) -----
    # Aggregators render <title>/<description> as HTML, so entities like
    # &#60;script&#62; sail through the XML parser as literal text that becomes
    # live markup downstream. Check the *parsed* text, not the raw source.
    active_tag = re.compile(
        r"<\s*(script|iframe|object|embed|link|style|form|input|button)\b", re.I)
    event_attr = re.compile(r"\bon\w+\s*=", re.I)
    bad_scheme = re.compile(r"(javascript|data|vbscript)\s*:", re.I)

    def _scan_feed_text(elem: ET.Element | None, where: str) -> None:
        text = (elem.text or "") if elem is not None else ""
        if not text.strip():
            return
        m = active_tag.search(text)
        if m:
            err(f"feed.xml: {where} contains active markup "
                f"<{m.group(1)}> — escape it (&lt;{m.group(1)}&gt;) or remove it")
            return
        if event_attr.search(text):
            err(f"feed.xml: {where} contains an on* event-handler attribute "
                "— escape it or remove it")
            return
        m = bad_scheme.search(text)
        if m:
            err(f"feed.xml: {where} contains a {m.group(1).lower()}: URL "
                "— escape it or remove it")

    if feed_root is not None and feed_root.tag == "rss":
        channel = feed_root.find("channel")
        if channel is not None:
            _scan_feed_text(channel.find("title"), "<channel><title>")
            _scan_feed_text(channel.find("description"), "<channel><description>")
            for i, item in enumerate(channel.findall("item"), start=1):
                _scan_feed_text(item.find("title"), f"item #{i} <title>")
                _scan_feed_text(item.find("description"),
                                f"item #{i} <description>")
                # aggregators render these as text/links too — same rules
                _scan_feed_text(item.find("guid"), f"item #{i} <guid>")
                _scan_feed_text(item.find("author"), f"item #{i} <author>")
                _scan_feed_text(item.find("source"), f"item #{i} <source>")
                for j, cat in enumerate(item.findall("category"), start=1):
                    _scan_feed_text(cat, f"item #{i} <category #{j}>")

# --- 7. feed item/channel links resolve to local files -------------------------
# Local-only link integrity: a link on the site's canonical domain must map
# to a real file under landing/ ("/" -> index.html, "/dir/" -> dir/index.html,
# and "#fragment" must name an id present in the target page). Relative links
# are resolved against landing/ too. Links to other domains are never
# fetched — offline policy — they are skipped with a note.


def _resolve_local(url: str) -> tuple:
    """Map a link to a local file. Returns (target, fragment, is_local).

    is_local False means the link points at another domain: skip it.
    """
    u = url.strip()
    if _dangerous_scheme(u):
        return None, None, False  # dangerous scheme — §6 already errors
    if u.startswith(feed_prefix):
        u = u[len(feed_prefix):]
    elif u.startswith("//") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        return None, None, False  # external — never fetched
    elif u.startswith("./"):
        u = u[2:]
    u = u.split("?", 1)[0]
    frag = None
    if "#" in u:
        u, frag = u.split("#", 1)
    if not u or u.endswith("/"):
        u = (u or "") + "index.html"
    target = (LANDING / u).resolve()
    return target, frag or None, True


def _check_feed_link(where: str, url: str) -> None:
    if not url.strip():
        return
    target, frag, is_local = _resolve_local(url)
    if not is_local:
        warn(f"feed.xml: {where} link {url.strip()} is external — "
             "not checked (offline)")
        return
    try:
        target.relative_to(LANDING)
    except ValueError:
        err(f"feed.xml: {where} link {url.strip()} escapes landing/")
        return
    if not target.exists():
        err(f"feed.xml: {where} link {url.strip()} has no matching file "
            "in landing/")
        return
    if frag and target.suffix.lower() == ".html":
        ids = set(ID_ATTR.findall(target.read_text(encoding="utf-8")))
        if frag not in ids:
            err(f"feed.xml: {where} link #{frag} has no matching id "
                f"in {target.name}")


if feed_root is not None and feed_root.tag == "rss":
    channel = feed_root.find("channel")
    if channel is not None:
        _check_feed_link("<channel>", channel.findtext("link") or "")
        for i, item in enumerate(channel.findall("item"), start=1):
            _check_feed_link(f"item #{i}", item.findtext("link") or "")

# --- 8. OG URL tags resolve to local files --------------------------------------
# Rule §5 only catches *unreferenced* assets; it never verified that
# og:image/og:url (the URLs social previews actually fetch) point at files
# that exist. Same offline policy as §7: absolute same-domain and relative
# URLs must map to a real file under landing/; other domains are skipped.
OG_URL_PROP = re.compile(
    r'<meta\s+property="(og:(?:image|video|audio|url))"\s+content="([^"]+)"', re.I)


def _check_og_url(page_name: str, prop: str, url: str) -> None:
    """`prop` arrives as the full property name (e.g. "og:image")."""
    target, frag, is_local = _resolve_local(url)
    if not is_local:
        warn(f"{page_name}: {prop} {url.strip()} is external — "
             "not checked (offline)")
        return
    try:
        target.relative_to(LANDING)
    except ValueError:
        err(f"{page_name}: {prop} {url.strip()} escapes landing/")
        return
    if not target.exists():
        err(f"{page_name}: {prop} {url.strip()} has no matching file "
            "in landing/")


for page in html_files:
    text = page.read_text(encoding="utf-8")
    for prop, url in OG_URL_PROP.findall(text):
        _check_og_url(page.name, prop, url)

# --- 8b. og:image dimension claims match the actual file ------------------------
# §8 verifies og:image *resolves* — but declared og:image:width/height that
# disagree with the real file are their own live defect: social scrapers
# reserve card layout from the declared numbers and render a broken card.
# Stdlib-only dimension readers for PNG/GIF/JPEG (per Open Graph, og:image
# should be a real raster file). Unsupported formats, external URLs, and
# already-§8-reported problems are skipped, never fetched.
OG_IMAGE_PROP = re.compile(
    r'<meta\s+property="og:image(?::(width|height))?"\s+content="([^"]+)"',
    re.I)


def _image_dims(path: Path):
    """Return (width, height) for PNG/GIF/JPEG files, else None."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > 24 and data[:8] == b"\x89PNG\r\n\x1a\n" \
            and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if len(data) > 10 and data[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", data[6:10])
    if len(data) > 2 and data[:2] == b"\xff\xd8":
        i, n = 2, len(data)
        while i + 1 < n:
            if data[i] != 0xFF:
                return None
            while i + 1 < n and data[i + 1] == 0xFF:
                i += 1  # fill bytes
            marker = data[i + 1]
            if marker == 0xD9:
                return None
            if marker == 0xD8 or 0xD0 <= marker <= 0xD7:
                i += 2  # standalone markers carry no length
                continue
            if i + 4 > n:
                return None
            seglen = int.from_bytes(data[i + 2:i + 4], "big")
            if seglen < 2 or i + 2 + seglen > n:
                return None
            if marker == 0xDA:
                return None  # start of scan data — no SOF to find
            if seglen >= 7 and marker not in (0xC4, 0xC8, 0xCC) \
                    and 0xC0 <= marker <= 0xCF:
                # SOF: [len][precision][height][width]
                h = int.from_bytes(data[i + 5:i + 7], "big")
                w = int.from_bytes(data[i + 7:i + 9], "big")
                return (w, h)
            i += 2 + seglen
    return None


for page in html_files:
    text = page.read_text(encoding="utf-8")
    img_url = None
    claims: dict[str, str | None] = {"width": None, "height": None}
    for kind, content in OG_IMAGE_PROP.findall(text):
        content = content.strip()
        if kind:
            claims[kind.lower()] = content
        else:
            img_url = content
    if img_url is None:
        continue
    target, _, is_local = _resolve_local(img_url)
    if not is_local or target is None:
        continue  # §8 already notes external — not checked (offline)
    try:
        target.relative_to(LANDING)
    except ValueError:
        continue  # §8 already errors on escape
    if not target.exists():
        continue  # §8 already errors on missing
    actual = _image_dims(target)
    if actual is None:
        continue  # unsupported format (e.g. SVG) — not checked
    w_claim, h_claim = claims["width"], claims["height"]
    if w_claim is None and h_claim is None:
        warn(f"{page.name}: og:image {target.name} declares no "
             "og:image:width/height — scrapers use them for card layout")
        continue
    bad = [(k, v) for k, v in (("width", w_claim), ("height", h_claim))
           if v is not None and not v.isdigit()]
    if bad:
        detail = ", ".join(f"og:image:{k}={v!r}" for k, v in bad)
        err(f"{page.name}: {detail} is not a number")
        continue
    if w_claim is None or h_claim is None:
        warn(f"{page.name}: og:image declares only one of "
             "og:image:width/height — declare both")
        continue
    if (int(w_claim), int(h_claim)) != actual:
        err(f"{page.name}: og:image:width/height {w_claim}x{h_claim} do not "
            f"match the actual {target.name} size {actual[0]}x{actual[1]}")

# --- 9. canonical link integrity --------------------------------------------------
# §2 only checks a canonical tag *exists*; it never verified the value.
# A canonical pointing at another domain, a relative path, or a different
# page (plus a second canonical tag, or a disagreeing og:url) is a live SEO
# defect: search engines and social scrapers act on it. Rules:
#   - exactly one rel="canonical" per page (§2 already errors when missing),
#   - href must be an absolute URL on the canonical domain — same offline
#     policy as §7/§8,
#   - href must name *this* page (index.html -> the bare domain root,
#     other.html -> /other.html), with no query string or fragment,
#   - og:url, when present, must equal the canonical href exactly,
#   - no two pages may share the same canonical href.
CANONICAL_TAG = re.compile(
    r'<link\b[^>]*\brel\s*=\s*["\']canonical["\'][^>]*>', re.I)
HREF_ATTR = re.compile(r'\bhref\s*=\s*"([^"]+)"', re.I)
OG_URL_TAG = re.compile(
    r'<meta\b[^>]*\bproperty\s*=\s*["\']og:url["\'][^>]*>', re.I)
CONTENT_ATTR = re.compile(r'\bcontent\s*=\s*"([^"]+)"', re.I)

canonical_owners: dict[str, list[str]] = {}
for page in html_files:
    text = page.read_text(encoding="utf-8")
    tags = CANONICAL_TAG.findall(text)
    if len(tags) > 1:
        err(f"{page.name}: {len(tags)} rel=canonical links — "
            "exactly one is allowed")
        continue
    if not tags:
        continue  # §2 already errors on the missing tag
    href_m = HREF_ATTR.search(tags[0])
    href = href_m.group(1).strip() if href_m else ""
    if not href:
        err(f"{page.name}: rel=canonical link has no href")
        continue
    rel_path = page.relative_to(LANDING).as_posix()
    expected = (feed_prefix if rel_path == "index.html"
                else feed_prefix + rel_path)
    on_domain = href.startswith(feed_prefix)
    if not on_domain:
        err(f"{page.name}: canonical href {href} is not an absolute URL on "
            f"the canonical domain {feed_prefix}")
    else:
        if "?" in href or "#" in href:
            err(f"{page.name}: canonical href {href} must not contain a "
                "query or fragment")
        if href != expected:
            err(f"{page.name}: canonical href {href} does not match this "
                f"page (expected {expected})")
        og_m = OG_URL_TAG.search(text)
        if og_m:
            content_m = CONTENT_ATTR.search(og_m.group(0))
            og_url = content_m.group(1).strip() if content_m else ""
            if og_url and og_url != href:
                err(f"{page.name}: og:url {og_url} does not match canonical "
                    f"href {href}")
    # register regardless of the above so duplicate claims across pages are
    # always caught even when a canonical is otherwise broken
    canonical_owners.setdefault(href, []).append(page.name)
for href, owners in canonical_owners.items():
    if len(owners) > 1:
        err(f"canonical href {href} is claimed by multiple pages: "
            f"{', '.join(owners)}")

# --- 10. feed date integrity (stale / future-dated / missing dates) ---------------
# A news feed whose newest story is months old is either broken or dead —
# and a pubDate in the future is always a generator bug. Offline-friendly:
# only compares the RFC 822 timestamps the feed already carries (already
# parsed by §6), so no network is involved. The stale threshold is
# configurable via the NEWS_STALE_DAYS environment variable (default 30).
#   - pubDate in the future (beyond a small clock-skew allowance) -> error
#   - pubDate/lastBuildDate older than the threshold -> warning
#   - item with no pubDate at all -> warning (aggregators sort by date)
from datetime import datetime, timedelta, timezone
import os

STALE_DAYS = int(os.environ.get("NEWS_STALE_DAYS", "30"))
FUTURE_SKEW = timedelta(minutes=15)  # tolerate minor clock skew


def _feed_datetime(raw: str):
    """Parse an RFC 822 date, normalizing naive datetimes to UTC."""
    try:
        dt = parsedate_to_datetime(raw)
    except (ValueError, TypeError):
        return None  # §6 already errors on unparsable dates
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


now = datetime.now(timezone.utc)
stale_after = now - timedelta(days=STALE_DAYS)

if feed_root is not None and feed_root.tag == "rss":
    channel = feed_root.find("channel")

    def _check_feed_date(where: str, raw: str) -> None:
        raw = (raw or "").strip()
        if not raw:
            return
        dt = _feed_datetime(raw)
        if dt is None:
            return
        if dt > now + FUTURE_SKEW:
            err(f"feed.xml: {where} date {raw!r} is in the future — "
                "likely a generator bug")
        elif dt < stale_after:
            warn(f"feed.xml: {where} date {raw!r} is older than "
                 f"{STALE_DAYS} days — feed may be stale")

    if channel is not None:
        _check_feed_date("<channel><lastBuildDate>",
                         channel.findtext("lastBuildDate"))
        for i, item in enumerate(channel.findall("item"), start=1):
            raw_pub = (item.findtext("pubDate") or "").strip()
            if not raw_pub:
                warn(f"feed.xml: item #{i} has no pubDate — "
                     "aggregators sort by date")
            else:
                _check_feed_date(f"item #{i} <pubDate>", raw_pub)

# --- 11. feed content size caps -------------------------------------------------
# Oversized feed content is a robustness hazard: aggregators truncate or
# drop long titles, and a runaway description bloats every subscriber's
# parser. Caps keep the feed aggregator-friendly and the checker fast.
MAX_FEED_ITEMS = 200
MAX_TITLE_CHARS = 300
MAX_DESCRIPTION_BYTES = 32 * 1024

if feed_root is not None and feed_root.tag == "rss":
    channel = feed_root.find("channel")
    if channel is not None:
        sized_items = channel.findall("item")
        if len(sized_items) > MAX_FEED_ITEMS:
            err(f"feed.xml: {len(sized_items)} items — over the "
                f"{MAX_FEED_ITEMS} item cap; trim or paginate the feed")
        for i, item in enumerate(sized_items, start=1):
            title = item.findtext("title") or ""
            if len(title) > MAX_TITLE_CHARS:
                err(f"feed.xml: item #{i} <title> is {len(title)} chars "
                    f"(limit {MAX_TITLE_CHARS}) — aggregators truncate long "
                    "titles")
            desc = item.findtext("description") or ""
            desc_bytes = len(desc.encode("utf-8"))
            if desc_bytes > MAX_DESCRIPTION_BYTES:
                err(f"feed.xml: item #{i} <description> is {desc_bytes} "
                    f"bytes (limit {MAX_DESCRIPTION_BYTES}) — trim it")

# --- 12. feed item identity integrity (guid presence + uniqueness) -----------
# Aggregators deduplicate and order on <guid>. A missing guid forces
# synthetic ids (flaky re-delivery); a duplicated guid makes aggregators
# collapse two distinct stories into one. Offline check, per the feed's
# existing identity fields — no fetching involved.
if feed_root is not None and feed_root.tag == "rss":
    channel = feed_root.find("channel")
    if channel is not None:
        seen_guids: dict[str, int] = {}
        for i, item in enumerate(channel.findall("item"), start=1):
            guid = item.find("guid")
            gtext = (guid.text or "").strip() if guid is not None else ""
            if not gtext:
                warn(f"feed.xml: item #{i} has no <guid> — aggregators "
                     "dedupe on guid; without one the item gets a flaky "
                     "synthetic id")
            elif gtext in seen_guids:
                err(f"feed.xml: item #{i} <guid> {gtext!r} duplicates "
                    f"item #{seen_guids[gtext]} — aggregators collapse "
                    "duplicate guids into one story")
            else:
                seen_guids[gtext] = i

# --- 13. feed item content completeness (title/description/link) ---------------
# RSS 2.0 requires every <item> to carry a <title> or a <description> (at
# least one, non-empty). An item with neither is invisible noise in every
# aggregator — and it means the generator is broken, so it is a hard
# error. <link> is technically optional in the spec, but a news item with
# no link is a dead end for readers; that is a warning, not an error.
if feed_root is not None and feed_root.tag == "rss":
    channel = feed_root.find("channel")
    if channel is not None:
        for i, item in enumerate(channel.findall("item"), start=1):
            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "").strip()
            if not title and not desc:
                err(f"feed.xml: item #{i} has neither a non-empty <title> "
                    "nor <description> — RSS requires at least one, and "
                    "aggregators render items as title + description")
            link = (item.findtext("link") or "").strip()
            if not link:
                warn(f"feed.xml: item #{i} has no <link> — readers have "
                     "nowhere to go")

# --- 14. feed item byline presence (<author>) ---------------------------------
# One of this site's stated principles is "clear bylines" — a news story
# with no named author is a byline defect. RSS carries authorship as
# <author> ("email address of the author of the item" per the RSS 2.0
# spec). Missing or empty is a warning, not an error: readers and
# aggregators want to know who wrote what, but the item is still
# technically valid without it.
if feed_root is not None and feed_root.tag == "rss":
    channel = feed_root.find("channel")
    if channel is not None:
        for i, item in enumerate(channel.findall("item"), start=1):
            author = (item.findtext("author") or "").strip()
            if not author:
                warn(f"feed.xml: item #{i} has no <author> — every story "
                     "should carry a byline")

# --- report -------------------------------------------------------------------
for w in warnings:
    print(f"warning: {w}")
for e in errors:
    print(f"error: {e}")
print(f"\n{len(html_files)} pages checked, "
      f"{len(errors)} error(s), {len(warnings)} warning(s)")
sys.exit(1 if errors else 0)
