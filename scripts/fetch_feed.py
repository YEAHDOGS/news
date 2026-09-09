#!/usr/bin/env python3
"""Hardened HTTP fetch layer for feed retrieval.

The offline sanity checker (`check.py`) never fetches anything. When this
project grows a feed aggregator that pulls remote RSS/Atom feeds, every
request must go through :func:`fetch_url` here — never through raw
``urllib``/``requests`` calls. The wrapper enforces:

- http(s) scheme only (no ``file:``, ``data:``, ``javascript:``, ...),
- a hard connect/read timeout,
- a redirect cap (max 3 hops) that never leaves the original request's
  registrable domain — no open redirects to attacker-controlled hosts,
- a response size cap enforced during the streaming read (so a 10 GB
  ``Content-Length`` lie cannot blow up memory),
- a content-type whitelist (feed formats only — HTML pages and other
  document types are rejected before the body is read),
- per-source failure backoff (:class:`SourceBackoff`) so a flapping or
  hostile source cannot turn the aggregator into a retry hammer.

Stdlib only — no dependencies to install.
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.parse
import urllib.request

# --- tunables ---------------------------------------------------------------
DEFAULT_TIMEOUT = 10.0          # seconds, applies to connect AND each read
MAX_REDIRECTS = 3               # hops; never leaves the registrable domain
MAX_BODY_BYTES = 512 * 1024     # 512 KiB — matches the parse-layer cap in check.py
CHUNK_BYTES = 64 * 1024         # streaming read granularity

# Feed formats only. Checked on the *response* Content-Type header before
# the body is read; the parse layer (§11a/§11b in check.py) re-validates.
ALLOWED_CONTENT_TYPES = frozenset({
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
    "application/feed+json",
    "application/json",
})

BACKOFF_BASE_SECONDS = 60.0     # first failure waits 1 minute
BACKOFF_MAX_SECONDS = 3600.0    # never back off longer than 1 hour

# Multi-label public suffixes where "last two labels" would mis-split the
# domain (e.g. bbc.co.uk's registrable domain is bbc.co.uk, not co.uk).
# This is a deliberately small, documented list — not a full PSL — covering
# the common cases an aggregator's redirect gate needs to get right.
MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk",
    "co.jp", "co.kr", "co.in", "co.nz", "co.za", "co.au", "com.au",
    "com.br", "com.mx", "com.tr", "com.sg",
})


class FetchError(Exception):
    """Raised when a hardened fetch is refused or fails."""


def registrable_domain(host: str) -> str:
    """Return the approximate registrable domain (eTLD+1) of ``host``.

    ``news.wearedogs.net`` -> ``wearedogs.net``; ``www.bbc.co.uk`` ->
    ``bbc.co.uk``. IP literals and ``localhost`` are returned as-is. This
    is a small embedded heuristic, not the full public suffix list — it is
    used only for the redirect gate, where erring toward *stricter* is safe.
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return host
    # IPv6 literal or IPv4 literal: not a domain, gate on the literal itself.
    if host.startswith("[") or all(
            ch.isdigit() or ch == "." for ch in host):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return last_two


def _base_content_type(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


class _NoAutoRedirect(urllib.request.HTTPRedirectHandler):
    """Block urllib's automatic redirect following.

    Redirects are handled manually in :func:`fetch_url` so every hop can be
    gated (count, scheme, registrable domain) before anything is fetched.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoAutoRedirect)


def _read_capped(resp, max_bytes: int) -> bytes:
    """Read the response body in chunks, refusing to exceed ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise FetchError(
                f"response body exceeded the {max_bytes}-byte cap "
                "during streaming read — refusing the rest")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_url(url: str, *, timeout: float = DEFAULT_TIMEOUT,
              max_bytes: int = MAX_BODY_BYTES,
              max_redirects: int = MAX_REDIRECTS,
              backoff: "SourceBackoff | None" = None,
              _clock=time.monotonic) -> tuple[bytes, str, str]:
    """Fetch ``url`` under the hardening policy.

    Returns ``(body, final_url, content_type)``. Raises :class:`FetchError`
    on any refusal or failure (bad scheme, timeout, too many or
    cross-domain redirects, wrong content type, oversize body, backoff).
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise FetchError(
            f"refusing to fetch non-http(s) URL: {url.strip()!r}")
    if not parsed.hostname:
        raise FetchError(f"refusing to fetch URL with no host: {url.strip()!r}")

    origin_domain = registrable_domain(parsed.hostname)
    source = parsed.netloc.lower()
    if backoff is not None and not backoff.can_fetch(source):
        raise FetchError(
            f"source {source} is in backoff for "
            f"{backoff.seconds_until_retry(source):.0f}s more — not fetching")

    current = url
    hops = 0
    body: bytes | None = None
    content_type = ""
    while True:
        req = urllib.request.Request(current, headers={
            "User-Agent": "DOGS-NEWS feed aggregator (hardened fetch)",
            "Accept": ("application/rss+xml, application/atom+xml, "
                       "application/xml;q=0.9, application/json;q=0.8, "
                       "*/*;q=0.1"),
        })
        try:
            resp = _opener.open(req, timeout=timeout)
            try:
                status = getattr(resp, "status", 200)
                if status in (301, 302, 303, 307, 308):
                    raise FetchError(
                        f"unexpected redirect for {current!r} (opener "
                        "should not auto-follow)")
                content_type = _base_content_type(
                    resp.headers.get("Content-Type"))
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise FetchError(
                        f"refusing {current!r}: Content-Type "
                        f"{content_type!r} is not a feed format "
                        f"(expected one of: "
                        f"{', '.join(sorted(ALLOWED_CONTENT_TYPES))})")
                declared = resp.headers.get("Content-Length")
                if declared is not None and declared.strip().isdigit() \
                        and int(declared) > max_bytes:
                    raise FetchError(
                        f"refusing {current!r}: declared Content-Length "
                        f"{declared} exceeds the {max_bytes}-byte cap")
                body = _read_capped(resp, max_bytes)
                break
            finally:
                resp.close()
        except urllib.error.HTTPError as exc:
            # Redirect statuses surface here since auto-follow is disabled.
            if exc.code in (301, 302, 303, 307, 308):
                hops += 1
                if hops > max_redirects:
                    raise FetchError(
                        f"refusing {current!r}: more than "
                        f"{max_redirects} redirects") from exc
                location = exc.headers.get("Location", "")
                target = urllib.parse.urljoin(current, location)
                tparsed = urllib.parse.urlsplit(target)
                if tparsed.scheme.lower() not in ("http", "https"):
                    raise FetchError(
                        f"refusing redirect to non-http(s) target: "
                        f"{target!r}") from exc
                if not tparsed.hostname:
                    raise FetchError(
                        f"refusing redirect to target with no host: "
                        f"{target!r}") from exc
                target_domain = registrable_domain(tparsed.hostname)
                if target_domain != origin_domain:
                    raise FetchError(
                        f"refusing cross-domain redirect: {current!r} -> "
                        f"{target!r} (registrable domain "
                        f"{target_domain!r} != {origin_domain!r})") from exc
                current = target
                continue
            failure = FetchError(
                f"fetch of {current!r} failed with HTTP {exc.code}")
            if backoff is not None:
                backoff.record_failure(source)
            raise failure from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError,
                ConnectionError, OSError) as exc:
            failure = FetchError(
                f"fetch of {current!r} failed: "
                f"{type(exc).__name__}: {exc}")
            if backoff is not None:
                backoff.record_failure(source)
            raise failure from exc

    if backoff is not None:
        backoff.record_success(source)
    return body, current, content_type


class SourceBackoff:
    """Per-source exponential failure backoff.

    Each source (a feed host, keyed by netloc) tracks consecutive failures.
    After failure *n*, the next fetch must wait
    ``min(base * 2**(n-1), max)`` seconds. A success resets the counter.
    ``clock`` defaults to ``time.monotonic`` and is injectable for tests.
    """

    def __init__(self, base_seconds: float = BACKOFF_BASE_SECONDS,
                 max_seconds: float = BACKOFF_MAX_SECONDS,
                 clock=time.monotonic):
        self.base = base_seconds
        self.max = max_seconds
        self._clock = clock
        self._state: dict[str, tuple[int, float]] = {}  # source -> (failures, next_allowed_at)

    def record_failure(self, source: str) -> None:
        failures, _ = self._state.get(source, (0, 0.0))
        failures += 1
        delay = min(self.base * (2 ** (failures - 1)), self.max)
        self._state[source] = (failures, self._clock() + delay)

    def record_success(self, source: str) -> None:
        self._state.pop(source, None)

    def seconds_until_retry(self, source: str) -> float:
        _, next_allowed = self._state.get(source, (0, 0.0))
        return max(0.0, next_allowed - self._clock())

    def can_fetch(self, source: str) -> bool:
        return self.seconds_until_retry(source) <= 0.0

    def consecutive_failures(self, source: str) -> int:
        failures, _ = self._state.get(source, (0, 0.0))
        return failures
