# DOGS NEWS — Aggregator design (spec, not built yet)

Status: prototype. The briefing page (`landing/briefing.html`) runs on sample
stories with localStorage votes. This doc specs the real pipeline. Nothing here
is wired to live feeds yet.

## Sources (RSS-first, zero new paid deps)

1. **RSS/Atom feeds** — primary. Curated list of publisher + independent feeds,
   fetched server-side (never from the browser; avoids CORS).
2. **Publisher sitemaps** (news sitemaps) — secondary, for outlets with weak RSS.
3. **Manual curation queue** — An editor can pin/inject a story via
   a tiny admin form. Pins always outrank algorithmic picks.

No scrapers against sites that forbid it. No paid APIs. Respect robots.txt.

### Source decisions needed from the founder
- Which outlets/topics make the curated feed list (and which are banned)?
- Politics coverage: in scope or out?
- How many sources to start (suggest 20–40)?

## Pipeline

```
fetch (hourly) → normalize → dedupe → score → emit static JSON → briefing page renders
```

- **Fetch:** GitHub Action on a schedule (hourly), Python stdlib
  (`urllib` + `xml.etree` — no new dependencies). Timeout 15s per feed.
  Failures logged, never fatal; a dead feed just drops out of that run.
- **Normalize:** title, link, published_at (UTC), source name, summary (first
  200 chars, stripped of HTML). Canonicalize URLs (strip utm_*, fbclid, trailing
  slashes, http→https).
- **Dedupe (two layers):**
  1. Exact: SHA-256 of canonical URL. Same URL = same story, always.
  2. Near: normalized title similarity. Lowercase, strip punctuation/source
     suffixes (" - CNN"), then token-set Jaccard ≥ 0.8 within a 48h window =
     same story. Keep the earliest published, merge source list ("3 sources").
- **Score (morning briefing rank):** `recency_decay * (1 + log(votes))`.
  Recency half-life: 12h. Editor pins get a fixed boost above everything.
  Votes come from the client tally (see below) — server counts only what the
  action has seen; honest about being approximate until a real backend exists.
- **Emit:** `landing/feed.json` — top 30 stories, rebuilt hourly, committed by
  the Action. The briefing page fetches it and falls back to bundled samples
  if the fetch fails. Static JSON = no backend, no cost, CDN-cached.

## Refresh cadence

- Feed rebuild: **hourly** via scheduled GitHub Action (free tier).
- Morning briefing "edition": pinned at 06:00 America/Chicago; the page shows
  "Today's edition" vs "Updating…" based on the JSON timestamp.
- Breaking override: manual dispatch of the Action for big stories.

### Cadence decisions needed from the founder
- Hourly OK, or every 30 min? (Actions minutes are free here, but noise.)
- 06:00 Chicago edition time OK?

## Voting (reader ranking)

- Prototype: localStorage per-device tally, exactly as built. Honest about it.
- Real: Cloudflare (his shared-waitlist plan) — a Worker + KV/D1 counter per
  story ID, one vote per device fingerprint per day, rate-limited. Not built.
- Anti-gaming v1: dedupe by IP+UA hash server-side, cap votes per story per
  day. Full fraud resistance is a later problem.

## Distribution ("a way to distribute things")

1. **Share sheet per story** (built in prototype): `navigator.share` on mobile,
   clipboard fallback on desktop.
2. **RSS out:** generate `landing/briefing.xml` from the same JSON so readers
   can subscribe to the briefing itself. (Spec'd, not built.)
3. **Copy-link with story anchor:** `briefing.html#<story-id>` scrolls to and
   highlights the story. (Spec'd, not built.)
4. Later, only with his say-so: email digest, push, social auto-posting.

## What stays out (his rules)

- No cookies, no consent banners. No trackers in the pipeline.
- No names/handles in the repo or on the page. Attribution is the badge image.
- No new paid dependencies. Python stdlib + GitHub Actions only.
