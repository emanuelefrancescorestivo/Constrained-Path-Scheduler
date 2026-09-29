"""
Reading a calendar from a link, and what such a reader must refuse.

The refusals matter once the page runs on a server: a link that points at the
server itself or its private network must not be fetched (server-side request
forgery), including when a public link redirects there. The local test server is
itself private, so the tests that read it say `allow_private=True`, which is the
setting for a page running on the person's own machine.
"""

from __future__ import annotations

import pytest

from cps import sources
from cps.sources import SourceError, check_public, fetch_calendar, normalise


def test_webcal_is_https_and_other_schemes_are_refused():
    assert normalise(" webcal://example.org/cal.ics ") == "https://example.org/cal.ics"
    assert normalise("HTTP://example.org/a") == "http://example.org/a"
    for bad in ("ftp://example.org/cal.ics", "file:///etc/passwd", "example.org/cal.ics", "https:///cal.ics"):
        with pytest.raises(SourceError):
            normalise(bad)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/cal.ics",
        "http://localhost:8765/feed",
        "http://10.1.2.3/cal.ics",
        "http://192.168.0.10/cal.ics",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/cal.ics",
    ],
)
def test_addresses_that_are_not_public_are_refused(url):
    with pytest.raises(SourceError, match="not on the public internet"):
        check_public(url)


def test_a_host_that_does_not_exist_is_a_readable_error():
    with pytest.raises(SourceError, match="could not be found"):
        fetch_calendar("https://no-such-host.invalid/cal.ics")


def test_a_private_link_is_refused_unless_allowed(timetable_server):
    link = f"{timetable_server}/sample-timetable.ics"
    with pytest.raises(SourceError, match="not on the public internet"):
        fetch_calendar(link)
    assert fetch_calendar(link, allow_private=True).startswith(b"BEGIN:VCALENDAR")


def test_redirects_are_followed_and_checked_again(timetable_server, monkeypatch):
    assert fetch_calendar(f"{timetable_server}/moved", allow_private=True).startswith(b"BEGIN:VCALENDAR")
    # Treat the test server as public, so that only the redirect's target is checked.
    real = sources.check_public
    monkeypatch.setattr(
        sources, "check_public", lambda url: None if url.startswith(timetable_server) else real(url)
    )
    with pytest.raises(SourceError, match="not on the public internet"):
        fetch_calendar(f"{timetable_server}/moved-inside")


def test_a_link_to_a_web_page_or_an_error_says_so(timetable_server):
    with pytest.raises(SourceError, match="does not lead to a calendar"):
        fetch_calendar(f"{timetable_server}/web-page", allow_private=True)
    with pytest.raises(SourceError, match="404"):
        fetch_calendar(f"{timetable_server}/missing", allow_private=True)


def test_a_calendar_that_is_too_large_is_refused(timetable_server, monkeypatch):
    monkeypatch.setattr(sources, "MAX_BYTES", 100)
    with pytest.raises(SourceError, match="larger than"):
        fetch_calendar(f"{timetable_server}/sample-timetable.ics", allow_private=True)
