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
| `scripts/check.py` | Sanity checker: internal links/anchors, SEO + social-card (Open Graph / Twitter Card) completeness, sitemap/robots consistency, feed.xml + item validity |
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
catch broken internal links, dangling anchors, missing SEO tags, and
sitemap/robots.txt drift before anything ships.

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
