#!/usr/bin/env python3
"""Regression tests for scripts/fetch_feed.py.

Spins up throwaway HTTP stubs on 127.0.0.1 (loopback only — no real
network) to prove the hardening behavior: timeouts fire, cross-domain
redirects are refused before anything is fetched, oversize bodies are cut
at the cap, wrong content types are rejected, and backoff delays grow
and cap.

Stdlib only. Run:  python3 tests/test_fetch_feed.py
"""

import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_feed import (  # noqa: E402
    ALLOWED_CONTENT_TYPES,
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    FetchError,
    SourceBackoff,
    fetch_url,
    registrable_domain,
)

RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title></channel></rss>'

failures = []


def check(name: str, fn) -> None:
    try:
        fn()
    except AssertionError as exc:
        failures.append(f"{name}: {exc}")
        print(f"FAIL {name}: {exc}")
    else:
        print(f"ok   {name}")


class StubHandler(BaseHTTPRequestHandler):
    """Routes by path; behavior set via the class-level ROUTES dict."""
    ROUTES: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        route = self.ROUTES.get(self.path.split("?")[0], {})
        status = route.get("status", 200)
        if "sleep" in route:
            time.sleep(route["sleep"])
        self.send_response(status)
        for header, value in route.get("headers", {}).items():
            self.send_header(header, value)
        body = route.get("body", b"")
        if not route.get("omit_content_length"):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # chunked write so the oversize test actually streams
        for i in range(0, len(body), 65536):
            try:
                self.wfile.write(body[i:i + 65536])
            except (BrokenPipeError, ConnectionResetError):
                break


def run_stub(routes: dict) -> tuple[HTTPServer, str]:
    StubHandler.ROUTES = routes
    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def stop_stub(server: HTTPServer) -> None:
    server.shutdown()
    server.server_close()


FEED_HEADERS = {"Content-Type": "application/rss+xml"}


def t_happy_path_returns_body():
    server, base = run_stub({"/feed.xml": {"headers": FEED_HEADERS,
                                           "body": RSS}})
    try:
        body, final_url, ctype = fetch_url(base + "/feed.xml", timeout=5)
        assert body == RSS, f"unexpected body: {body!r}"
        assert final_url == base + "/feed.xml", final_url
        assert ctype == "application/rss+xml", ctype
    finally:
        stop_stub(server)


def t_timeout_fires():
    server, base = run_stub({"/slow.xml": {"headers": FEED_HEADERS,
                                           "body": RSS, "sleep": 3.0}})
    try:
        try:
            fetch_url(base + "/slow.xml", timeout=0.5)
        except FetchError as exc:
            assert "timed out" in str(exc).lower() or "timeout" in str(exc).lower(), exc
        else:
            raise AssertionError("slow response should have timed out")
    finally:
        stop_stub(server)


def t_cross_domain_redirect_refused():
    # The redirect target is never connected to — refusal happens on the
    # Location header alone.
    server, base = run_stub({"/hop.xml": {"status": 302,
                                          "headers": {"Location":
                                                      "http://evil.example/feed.xml"}}})
    try:
        try:
            fetch_url(base + "/hop.xml", timeout=5)
        except FetchError as exc:
            assert "cross-domain redirect" in str(exc), exc
            assert "evil.example" in str(exc), exc
        else:
            raise AssertionError("redirect to evil host should be refused")
    finally:
        stop_stub(server)


def t_same_domain_relative_redirect_followed():
    server, base = run_stub({
        "/hop.xml": {"status": 301,
                     "headers": {"Location": "/feed.xml"}},
        "/feed.xml": {"headers": FEED_HEADERS, "body": RSS},
    })
    try:
        body, final_url, _ = fetch_url(base + "/hop.xml", timeout=5)
        assert body == RSS, f"unexpected body: {body!r}"
        assert final_url == base + "/feed.xml", final_url
    finally:
        stop_stub(server)


def t_redirect_chain_cap_enforced():
    routes = {}
    for i in range(1, 8):
        routes[f"/r{i}.xml"] = {"status": 302,
                                "headers": {"Location": f"/r{i + 1}.xml"}}
    server, base = run_stub(routes)
    try:
        try:
            fetch_url(base + "/r1.xml", timeout=5, max_redirects=3)
        except FetchError as exc:
            assert "more than 3 redirects" in str(exc), exc
        else:
            raise AssertionError("redirect chain over the cap should fail")
    finally:
        stop_stub(server)


def t_redirect_to_non_http_scheme_refused():
    server, base = run_stub({"/hop.xml": {"status": 302,
                                          "headers": {"Location":
                                                      "data:text/html,hi"}}})
    try:
        try:
            fetch_url(base + "/hop.xml", timeout=5)
        except FetchError as exc:
            assert "non-http(s)" in str(exc), exc
        else:
            raise AssertionError("redirect to data: should be refused")
    finally:
        stop_stub(server)


def t_oversize_body_cut_at_cap():
    big = b"x" * (256 * 1024)
    server, base = run_stub({"/big.xml": {"headers": FEED_HEADERS,
                                          "body": big,
                                          # no declared length: the streaming
                                          # cap must catch it mid-read
                                          "omit_content_length": True}})
    try:
        try:
            fetch_url(base + "/big.xml", timeout=5, max_bytes=64 * 1024)
        except FetchError as exc:
            assert "exceeded" in str(exc) and "cap" in str(exc), exc
        else:
            raise AssertionError("256 KiB body over a 64 KiB cap should fail")
    finally:
        stop_stub(server)


def t_oversize_declared_length_refused_early():
    # Lying Content-Length alone is enough to refuse — no need to read.
    server, base = run_stub({"/lie.xml": {
        "headers": {**FEED_HEADERS,
                    "Content-Length": str(10 * 1024 * 1024)},
        "body": b"short"}})
    try:
        try:
            fetch_url(base + "/lie.xml", timeout=5)
        except FetchError as exc:
            assert "Content-Length" in str(exc), exc
        else:
            raise AssertionError("lying Content-Length should be refused")
    finally:
        stop_stub(server)


def t_wrong_content_type_rejected():
    server, base = run_stub({"/page": {"headers": {"Content-Type":
                                                  "text/html; charset=utf-8"},
                                      "body": b"<html></html>"}})
    try:
        try:
            fetch_url(base + "/page", timeout=5)
        except FetchError as exc:
            assert "not a feed format" in str(exc), exc
            assert "text/html" in str(exc), exc
        else:
            raise AssertionError("text/html should be rejected")
    finally:
        stop_stub(server)


def t_non_http_scheme_refused():
    for url in ("file:///etc/passwd", "javascript:alert(1)",
                "data:text/html,hi", "ftp://example.com/feed.xml"):
        try:
            fetch_url(url, timeout=5)
        except FetchError as exc:
            assert "non-http(s)" in str(exc), (url, exc)
        else:
            raise AssertionError(f"{url} should be refused")


def t_json_feed_content_type_allowed():
    server, base = run_stub({"/feed.json": {
        "headers": {"Content-Type": "application/feed+json"},
        "body": b'{"version":"https://jsonfeed.org/version/1","title":"T"}'}})
    try:
        body, _, ctype = fetch_url(base + "/feed.json", timeout=5)
        assert ctype == "application/feed+json", ctype
        assert body.startswith(b'{"version"'), body
    finally:
        stop_stub(server)


def t_backoff_gates_fetch_url():
    clock = [1000.0]
    backoff = SourceBackoff(clock=lambda: clock[0])
    server, base = run_stub({"/feed.xml": {"headers": FEED_HEADERS,
                                           "body": RSS}})
    source = urllib.parse.urlsplit(base + "/feed.xml").netloc.lower()
    try:
        backoff.record_failure(source)
        try:
            fetch_url(base + "/feed.xml", timeout=5, backoff=backoff)
        except FetchError as exc:
            assert "backoff" in str(exc), exc
        else:
            raise AssertionError("fetch during backoff should be refused")
        # After the window passes, the fetch goes through and resets.
        clock[0] += BACKOFF_MAX_SECONDS + 1
        body, _, _ = fetch_url(base + "/feed.xml", timeout=5,
                               backoff=backoff)
        assert body == RSS, body
        assert backoff.consecutive_failures(source) == 0
    finally:
        stop_stub(server)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def t_backoff_delays_grow_and_cap():
    clock = FakeClock()
    backoff = SourceBackoff(clock=clock)
    src = "feeds.example.com"

    assert backoff.can_fetch(src)
    backoff.record_failure(src)
    assert not backoff.can_fetch(src)
    assert backoff.consecutive_failures(src) == 1
    # Exponential: 60, 120, 240, ...
    assert backoff.seconds_until_retry(src) == BACKOFF_BASE_SECONDS

    clock.now += BACKOFF_BASE_SECONDS  # window 1 passes
    assert backoff.can_fetch(src)
    backoff.record_failure(src)
    assert backoff.seconds_until_retry(src) == 2 * BACKOFF_BASE_SECONDS

    clock.now += 2 * BACKOFF_BASE_SECONDS
    backoff.record_failure(src)
    assert backoff.seconds_until_retry(src) == 4 * BACKOFF_BASE_SECONDS

    # Capped at BACKOFF_MAX_SECONDS no matter how many failures pile up.
    for _ in range(20):
        clock.now += 4 * BACKOFF_BASE_SECONDS  # keep the window expiring
        backoff.record_failure(src)
    assert backoff.seconds_until_retry(src) == BACKOFF_MAX_SECONDS, \
        backoff.seconds_until_retry(src)

    # Success resets the counter.
    backoff.record_success(src)
    assert backoff.consecutive_failures(src) == 0
    assert backoff.can_fetch(src)


def t_registrable_domain():
    assert registrable_domain("news.wearedogs.net") == "wearedogs.net"
    assert registrable_domain("wearedogs.net") == "wearedogs.net"
    assert registrable_domain("www.bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("127.0.0.1") == "127.0.0.1"
    assert registrable_domain("localhost") == "localhost"
    assert registrable_domain("a.b.c.example.com") == "example.com"
    assert registrable_domain("EXAMPLE.COM") == "example.com"


check("happy path returns body/url/content-type", t_happy_path_returns_body)
check("timeout fires on a slow response", t_timeout_fires)
check("redirect to an evil host is refused", t_cross_domain_redirect_refused)
check("same-domain relative redirect is followed",
      t_same_domain_relative_redirect_followed)
check("redirect chain cap is enforced", t_redirect_chain_cap_enforced)
check("redirect to a non-http(s) scheme is refused",
      t_redirect_to_non_http_scheme_refused)
check("oversize body is cut at the cap", t_oversize_body_cut_at_cap)
check("lying Content-Length is refused before reading",
      t_oversize_declared_length_refused_early)
check("wrong content type is rejected", t_wrong_content_type_rejected)
check("non-http(s) URL schemes are refused", t_non_http_scheme_refused)
check("JSON Feed content type is allowed", t_json_feed_content_type_allowed)
check("backoff gates fetch_url and resets on success",
      t_backoff_gates_fetch_url)
check("backoff delays grow exponentially and cap",
      t_backoff_delays_grow_and_cap)
check("registrable-domain helper", t_registrable_domain)

total = 14
if failures:
    print(f"\n{len(failures)} of {total} test(s) failed")
    sys.exit(1)
print(f"\nall {total} tests passed")
