# news

**DOGS NEWS** — an independent news platform by [DOGS](https://wearedogs.net).

Live: <https://news.wearedogs.net>

> News that bites. Reader-first reporting with no middlemen and no noise.

Right now this repo is the public landing page for the project. The full
platform is on the roadmap for 2027 — this page is the stake in the ground.

## What's here

| Path | What it is |
|---|---|
| `landing/` | The whole site. Static HTML/CSS/JS, deployed as-is to GitHub Pages |
| `landing/index.html` | Landing page: hero, roadmap timeline, principles, notify form |
| `landing/app.js` | Snap-panel dot nav + front-end-only notify form validation |
| `landing/styles.css` | All styling (Poppins/Lora, cream/ink DOGS theme) |
| `landing/privacy.html` | Baseline privacy policy: no cookies, no analytics, no data collected |
| `landing/derp.html` | The official derp page. Every good dog site needs one |
| `landing/404.html`, `landing/thanks.html` | Error and form-confirmation stages |
| `landing/feed.xml` | RSS feed skeleton — channel metadata now, stories at launch |
| `landing/humans.txt` | The humans behind the page |
| `landing/robots.txt`, `landing/sitemap.xml` | Crawler config |
| `landing/CNAME` | Custom domain: `news.wearedogs.net` |
| `scripts/check.py` | Sanity checker (internal links, anchors, SEO tags, sitemap/robots/feed.xml consistency) |
| `tests/test_check.py` | Regression tests for the sanity checker (run with `python3 tests/test_check.py`) |
| `.github/workflows/` | `landing-page.yml` (Pages deploy) and `checks.yml` (sanity checks on push/PR) |

## Run it locally

No build step. Serve the `landing/` directory with any static server:

```sh
cd landing
python3 -m http.server 8080
# open http://localhost:8080
```

Or on Windows PowerShell:

```powershell
cd landing
python -m http.server 8080
```

## How deploys work

Pushes to `master` that touch `landing/**` trigger
`.github/workflows/landing-page.yml`, which publishes `landing/` to GitHub
Pages on the custom domain `news.wearedogs.net` (see `landing/CNAME`).

The `checks.yml` workflow runs `scripts/check.py` on every push and PR to
catch broken internal links, dangling anchors, missing SEO tags,
sitemap/robots.txt drift, feed.xml validation failures (including active
markup smuggled into titles/descriptions as entities — an XSS guard), and
missing RSS autodiscovery links before anything ships.

### Canonical domain and rule §7 (feed link integrity)

The canonical domain is read from `landing/CNAME` (currently
`news.wearedogs.net`) — no domain is hardcoded in the checker. Rule §7
extends the feed checks: every `<channel><link>` and item `<link>` must
resolve to a real page in `landing/`:

- absolute same-domain links map to files (`https://news.wearedogs.net/`
  → `index.html`, `/about/` → `about/index.html`),
- relative links resolve against `landing/`,
- `#fragment` links must name an `id` that exists in the target page.

Links to other domains are **never fetched** — the checker is fully
offline by design (SECURITY.md). They are reported as
`external — not checked (offline)` and skipped.

### Rule §8 (social-preview URL integrity)

Rule §5 catches *unreferenced* assets, but it never verified that the URLs
social previews actually fetch resolve to real files. Rule §8 closes that
gap: every `og:image`, `og:video`, `og:audio`, and `og:url` meta `content`
URL must map to a real file under `landing/` when it is absolute on the
canonical domain or relative (`https://news.wearedogs.net/og.png` → `og.png`).
Other domains are skipped offline, same as §7.

### Rule §9 (canonical link integrity)

Rule §2 only checked that a canonical tag *exists* — it never verified the
value, and search engines and social scrapers act on it. Rule §9 closes that
gap: each page must carry exactly one `rel="canonical"` whose `href` is an
absolute URL on the canonical domain naming *that page*
(`index.html` → the bare domain root, `other.html` → `/other.html`), with no
query string or fragment; `og:url`, when present, must equal the canonical
href exactly; and no two pages may share the same canonical.

### Rule §10 (feed date integrity)

A news feed whose newest story is months old is either broken or dead —
and a `pubDate` in the future is always a generator bug. Rule §10 flags
timestamp problems using only the RFC 822 dates the feed already carries
(still fully offline, no fetching):

- a `pubDate` in the future (beyond a 15-minute clock-skew allowance) —
  **error**,
- an item `pubDate` or channel `lastBuildDate` older than the stale
  threshold — **warning**,
- an item with no `pubDate` at all — **warning** (aggregators sort by date).

The threshold defaults to 30 days and is configurable via the
`NEWS_STALE_DAYS` environment variable.

## Roadmap

From the landing page timeline:

1. **Repo live** — done (2026)
2. **Building** — in progress now
3. **More information** — estimated 2027

The guiding principles: independent (no parent company, no sponsors steering
coverage), reader-first (fast pages, clear bylines, no dark patterns), and
signal over noise (fewer stories, better sourced).

## The notify form

The email form on the home page is a **front-end placeholder** — it validates
locally and lands on `thanks.html`, which says so plainly. There is no
backend, no mailing list, and nothing is transmitted or stored. When a real
signup path exists, the privacy policy must be replaced with a complete one
*before* any data is collected.

## Contributing

Issues and PRs are welcome at <https://github.com/YEAHDOGS/news>.
Keep it dependency-free and fast — this site ships raw HTML/CSS/JS on purpose.

## License

MIT — see [LICENSE](LICENSE).
