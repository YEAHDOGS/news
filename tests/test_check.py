#!/usr/bin/env python3
"""Regression tests for scripts/check.py.

Copies the repo's scripts/ + landing/ into a temp dir (the checker derives
its root from its own path, so the copy behaves like the real repo),
injects a fault into landing/feed.xml or a landing page, and asserts the
checker fails with the expected error message.

Stdlib only. Run:  python3 tests/test_check.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check.py"


def run_check(feed_text: str | None = None, page_edits: dict | None = None):
    with tempfile.TemporaryDirectory(prefix="news-check-test-") as tmp:
        tmp = Path(tmp)
        shutil.copytree(ROOT / "scripts", tmp / "scripts")
        shutil.copytree(ROOT / "landing", tmp / "landing")
        if feed_text is not None:
            (tmp / "landing" / "feed.xml").write_text(feed_text, encoding="utf-8")
        if page_edits:
            for name, (old, new) in page_edits.items():
                page = tmp / "landing" / name
                text = page.read_text(encoding="utf-8")
                assert old in text, f"test fixture drift: {old!r} not in {name}"
                page.write_text(text.replace(old, new, 1), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(tmp / "scripts" / "check.py")],
            capture_output=True, text=True, timeout=60,
        )
        return proc.returncode, proc.stdout + proc.stderr


def real_feed() -> str:
    return (ROOT / "landing" / "feed.xml").read_text(encoding="utf-8")


failures = []


def check(name: str, fn) -> None:
    try:
        fn()
    except AssertionError as exc:
        failures.append(f"{name}: {exc}")
        print(f"FAIL {name}: {exc}")
    else:
        print(f"ok   {name}")


def t_clean_feed_passes():
    code, out = run_check(real_feed())
    assert code == 0, f"expected exit 0 on the real feed, got {code}:\n{out}"


def t_off_domain_channel_link_fails():
    code, out = run_check(real_feed().replace(
        "https://news.wearedogs.net/</link>",
        "https://evil.example/</link>"))
    assert code != 0, "off-domain <channel><link> should fail"
    assert "is not on https://news.wearedogs.net/" in out, out


def t_wrong_atom_self_link_fails():
    code, out = run_check(real_feed().replace(
        'href="https://news.wearedogs.net/feed.xml"',
        'href="https://news.wearedogs.net/other.xml"'))
    assert code != 0, "wrong atom:self href should fail"
    assert "atom:self link" in out, out


def t_bad_last_build_date_fails():
    code, out = run_check(real_feed().replace(
        "Wed, 09 Sep 2026 04:00:00 -0500", "yesterday-ish"))
    assert code != 0, "unparsable lastBuildDate should fail"
    assert "lastBuildDate" in out and "RFC 822" in out, out


def t_off_domain_item_link_fails():
    feed = real_feed().replace(
        "</channel>",
        '  <item><title>T</title><link>https://evil.example/x</link>'
        "<description>D</description></item>\n  </channel>")
    code, out = run_check(feed)
    assert code != 0, "off-domain item link should fail"
    assert "item link https://evil.example/x is not on" in out, out


RSS_LINK = ('  <link rel="alternate" type="application/rss+xml"'
            ' title="DOGS NEWS" href="./feed.xml">\n')


def t_missing_rss_autodiscovery_fails():
    code, out = run_check(page_edits={"thanks.html": (RSS_LINK, "")})
    assert code != 0, "page without RSS autodiscovery should fail"
    assert "thanks.html: missing RSS autodiscovery link" in out, out


def t_wrong_rss_href_fails():
    code, out = run_check(page_edits={
        "privacy.html": ('href="./feed.xml"', 'href="./feed2.xml"')})
    assert code != 0, "RSS autodiscovery pointing off feed.xml should fail"
    assert "does not point at ./feed.xml" in out, out


check("clean feed passes", t_clean_feed_passes)
check("off-domain channel link fails", t_off_domain_channel_link_fails)
check("wrong atom:self link fails", t_wrong_atom_self_link_fails)
check("bad lastBuildDate fails", t_bad_last_build_date_fails)
check("off-domain item link fails", t_off_domain_item_link_fails)
check("missing RSS autodiscovery fails", t_missing_rss_autodiscovery_fails)
check("wrong RSS href fails", t_wrong_rss_href_fails)

if failures:
    print(f"\n{len(failures)} test(s) failed")
    sys.exit(1)
print("\nall 7 tests passed")
