"""
Plans published as calendar feeds.

What a feed has to get right is what a person would notice in their calendar: it
answers with a calendar, it keeps the sessions already done, it goes on from today
when it is refreshed, it never goes blank because the timetable's server was down,
it does not come back after being deleted, and its secret address is not written to
a log.
"""

from __future__ import annotations

import threading
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from wsgiref.simple_server import make_server
from zoneinfo import ZoneInfo

import pytest

from cps import service
from cps.feed import FeedApp, _QuietHandler, _Server

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SAMPLE = EXAMPLES / "sample-timetable.ics"
START = date(2026, 3, 2)
TZ = "Europe/Rome"
BEFORE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
SUBJECTS = [service.SubjectSpec("Analysis", None, familiarity=3)]


def _subscription(**overrides) -> service.Subscription:
    options = dict(subjects=SUBJECTS, start=START, tz=TZ, ics=SAMPLE.read_bytes(), window=3, now=BEFORE)
    options.update(overrides)
    return service.new_subscription(**options)


@pytest.fixture(scope="module")
def published() -> service.Subscription:
    return _subscription()


def _same(a: bytes, b: bytes) -> bool:
    """Two feeds are the same calendar: equal but for DTSTAMP, the export's clock."""

    def strip(x: bytes) -> list[bytes]:
        return [line for line in x.splitlines() if not line.startswith(b"DTSTAMP")]

    return strip(a) == strip(b)


def _get(app, path, method="GET"):
    seen = {}
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path}
    body = b"".join(app(environ, lambda s, h: seen.update(s=s, h=dict(h))))
    return seen["s"], seen["h"], body


# --------------------------------------------------------------------------- #
# The subscription
# --------------------------------------------------------------------------- #


def test_a_new_subscription_holds_a_plan_and_a_secret_token(published):
    assert published.plan is not None and published.error is None
    assert len(published.token) >= 32
    assert published.source_url is None and published.ics_text.startswith("BEGIN:VCALENDAR")
    assert service.PlanReport.from_dict(published.plan).sessions


def test_a_subscription_round_trips_through_the_store(published, tmp_path):
    service.save_subscription(tmp_path, published)
    assert service.load_subscription(tmp_path, published.token) == published
    assert [p.name for p in tmp_path.iterdir()] == [f"{published.token}.json"]  # no temporary left
    assert service.delete_subscription(tmp_path, published.token)
    assert service.load_subscription(tmp_path, published.token) is None
    assert not service.delete_subscription(tmp_path, published.token)


@pytest.mark.parametrize("token", ["../../etc/passwd", "short", "a/b" * 12, "x" * 65])
def test_a_token_that_is_not_one_reads_nothing(tmp_path, token):
    assert service.load_subscription(tmp_path, token) is None
    assert not service.delete_subscription(tmp_path, token)


def test_the_feed_keeps_its_identifiers_and_asks_to_be_read_again(published):
    text = service.feed_ics(published).decode("utf-8")
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT6H" in text
    assert "X-PUBLISHED-TTL:PT6H" in text
    uids = [line for line in text.splitlines() if line.startswith("UID:")]
    assert uids and all(u.startswith(f"UID:cps-{published.token[:8]}-") for u in uids)
    assert service.feed_ics(published).count(b"BEGIN:VEVENT") == len(uids)


def test_a_refresh_goes_on_from_now_and_keeps_what_was_done(published):
    plan = service.PlanReport.from_dict(published.plan)
    now = datetime(2026, 3, 9, 6, 0, tzinfo=UTC)  # Monday of the second week
    cut = (now.astimezone(ZoneInfo(TZ)).replace(tzinfo=None) - datetime(2026, 3, 2)) / timedelta(days=1)
    fresh = service.refresh_subscription(published, now=now)
    after = service.PlanReport.from_dict(fresh.plan)
    done = [s for s in plan.sessions if s.start_day < cut]
    assert done, "the sample plan has sessions in its first week"
    assert [(s.start, s.title) for s in after.history] == [(s.start, s.title) for s in done]
    assert all(s.outcome == "recalled" for s in after.history)
    assert all(s.start_day > cut for s in after.sessions)
    assert fresh.refreshed == "2026-03-09T06:00:00+00:00"
    # Every session, done and to come, is in the feed.
    assert service.feed_ics(fresh).count(b"BEGIN:VEVENT") == len(after.history) + len(after.sessions)


def test_a_timetable_that_cannot_be_read_keeps_the_last_plan(published):
    def down(url):
        raise service.UnreadableLink("the link could not be read (timed out)")

    linked = service.Subscription.from_dict(
        {**published.to_dict(), "source_url": "https://example.org/x.ics"}
    )
    kept = service.refresh_subscription(linked, now=BEFORE + timedelta(days=3), fetch=down)
    assert kept.plan == linked.plan and kept.refreshed == linked.refreshed
    assert "timed out" in kept.error


def test_a_session_whose_topic_is_gone_is_dropped(published):
    """A week of lectures cancelled after the plan was made: its sessions in the
    past are not replayed against a topic that no longer exists."""
    plan = service.PlanReport.from_dict(published.plan)
    ghost = plan.sessions[0].__class__(**{**plan.sessions[0].to_dict(), "topic": "Analysis · week of 01 Jan"})
    report = service.analyse_calendar(SAMPLE.read_bytes(), start=START, tz=TZ)
    after = service.continue_plan(
        report, SUBJECTS, done=[ghost], now=datetime(2026, 3, 5, tzinfo=UTC), window=3
    )
    assert after.history == ()


def test_staleness():
    fresh = service.Subscription.from_dict(
        {
            "token": "t" * 32,
            "created": "2026-03-01T00:00:00+00:00",
            "source_url": None,
            "ics_text": None,
            "busy_rows": [],
            "subjects": [],
            "options": {},
            "refreshed": "2026-03-01T00:00:00+00:00",
        }
    )
    assert not service.is_stale(fresh, datetime(2026, 3, 1, 5, 59, tzinfo=UTC))
    assert service.is_stale(fresh, datetime(2026, 3, 1, 6, 0, tzinfo=UTC))


# --------------------------------------------------------------------------- #
# The server
# --------------------------------------------------------------------------- #


def test_the_server_answers_a_feed_and_nothing_else(published, tmp_path):
    service.save_subscription(tmp_path, published)
    app = FeedApp(tmp_path, background=False, clock=lambda: BEFORE)
    status, headers, body = _get(app, f"/feed/{published.token}.ics")
    assert status == "200 OK" and headers["Content-Type"].startswith("text/calendar")
    assert _same(body, service.feed_ics(published))
    assert _get(app, f"/feed/{published.token}.ics", "HEAD")[2] == b""
    assert _get(app, "/feed/" + "x" * 32 + ".ics")[0] == "404 Not Found"
    assert _get(app, "/feed/../../etc/passwd")[0] == "404 Not Found"
    assert _get(app, f"/feed/{published.token}.ics", "POST")[0] == "405 Method Not Allowed"
    assert _get(app, "/health")[0] == "200 OK"


def test_a_stale_feed_is_refreshed_and_the_next_read_has_it(published, tmp_path):
    service.save_subscription(tmp_path, published)
    later = datetime(2026, 3, 9, 6, 0, tzinfo=UTC)
    app = FeedApp(tmp_path, background=False, clock=lambda: later)
    first = _get(app, f"/feed/{published.token}.ics")[2]
    assert _same(first, service.feed_ics(published))  # never waits for a refresh
    stored = service.load_subscription(tmp_path, published.token)
    assert stored.refreshed == "2026-03-09T06:00:00+00:00"
    assert _same(_get(app, f"/feed/{published.token}.ics")[2], service.feed_ics(stored))


def test_a_feed_deleted_during_its_refresh_stays_deleted(published, tmp_path):
    linked = service.Subscription.from_dict(
        {**published.to_dict(), "source_url": "https://example.org/x.ics"}
    )
    service.save_subscription(tmp_path, linked)

    def deleting(url):
        service.delete_subscription(tmp_path, linked.token)
        return SAMPLE.read_bytes()

    app = FeedApp(tmp_path, background=False, clock=lambda: BEFORE + timedelta(days=1), fetch=deleting)
    app.refresh(linked.token)
    assert service.load_subscription(tmp_path, linked.token) is None


def test_the_access_log_leaves_out_the_secret_address(published, tmp_path, capfd):
    service.save_subscription(tmp_path, published)
    app = FeedApp(tmp_path, background=False, clock=lambda: BEFORE)
    server = make_server("127.0.0.1", 0, app, server_class=_Server, handler_class=_QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/feed/{published.token}.ics"
        with urllib.request.urlopen(url) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
    logged = capfd.readouterr().err
    assert "GET 200" in logged
    assert published.token not in logged


def test_a_subscription_from_a_link_reads_the_link(timetable_server, monkeypatch):
    monkeypatch.setattr(service, "LINKS_MAY_BE_PRIVATE", True)
    linked = _subscription(ics=None, source_url=f"{timetable_server}/sample-timetable.ics")
    assert linked.ics_text is None and linked.plan is not None
    assert service.feed_ics(linked).startswith(b"BEGIN:VCALENDAR")
