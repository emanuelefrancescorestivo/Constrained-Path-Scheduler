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
from dataclasses import replace
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
    assert {p.name for p in tmp_path.iterdir()} <= {"cps.sqlite", "cps.sqlite-wal", "cps.sqlite-shm"}
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
    # The calendar last read is kept, so that a report replans without the network.
    assert linked.ics_text.startswith("BEGIN:VCALENDAR") and linked.plan is not None
    assert service.feed_ics(linked).startswith(b"BEGIN:VCALENDAR")


# --------------------------------------------------------------------------- #
# What the student reports
# --------------------------------------------------------------------------- #

REPORT = service.TaskSpec("Analysis problem sheet", "2026-03-13 18:00", 4.5, "Analysis")


@pytest.fixture(scope="module")
def assisted() -> service.Subscription:
    return _subscription(engine="assistant", tasks=[REPORT], preferences={"weekly_hours": 12})


def _first(sub: service.Subscription, kind: str) -> service.SessionView:
    return next(s for s in service.PlanReport.from_dict(sub.plan).sessions if s.kind == kind)


def _after(session: service.SessionView) -> datetime:
    return datetime.fromisoformat(session.start).replace(tzinfo=ZoneInfo(TZ)) + timedelta(minutes=5)


def test_a_session_id_is_stable_across_a_refresh(assisted):
    plan = service.PlanReport.from_dict(assisted.plan)
    later = service.refresh_subscription(assisted, now=datetime(2026, 3, 4, tzinfo=UTC))
    after = service.PlanReport.from_dict(later.plan)
    ids = {service.session_id(s) for s in after.history}
    assert ids and ids <= {service.session_id(s) for s in plan.sessions}
    assert service.find_session(later, next(iter(ids))) is not None


def test_a_skipped_task_block_comes_back_later(assisted):
    task = _first(assisted, "task")
    before = sum(s.kind == "task" for s in service.PlanReport.from_dict(assisted.plan).sessions)
    now = _after(task)
    reported = service.report_session(assisted, service.session_id(task), "skipped", now=now)
    plan = service.PlanReport.from_dict(reported.plan)
    skipped = [s for s in plan.history if service.session_id(s) == service.session_id(task)]
    assert [s.outcome for s in skipped] == ["skipped"]
    assert reported.outcomes == {service.session_id(task): "skipped"}
    # The work still has to be done: as many task blocks as before, the skipped one aside.
    done = sum(s.kind == "task" and s.outcome != "skipped" for s in plan.history)
    assert done + sum(s.kind == "task" for s in plan.sessions) == before


def test_a_hard_task_gets_one_more_block(assisted):
    task = _first(assisted, "task")
    reported = service.report_session(assisted, service.session_id(task), "struggled", now=_after(task))
    assert reported.options["tasks"][0]["hours"] == REPORT.hours + 1.5
    view = service.PlanReport.from_dict(reported.plan).tasks[0]
    assert view.blocks == service.PlanReport.from_dict(assisted.plan).tasks[0].blocks + 1


def test_a_hard_review_counts_as_forgotten(assisted):
    review = _first(assisted, "first review")
    reported = service.report_session(assisted, service.session_id(review), "struggled", now=_after(review))
    kept = service.find_session(reported, service.session_id(review))
    assert kept.outcome == "lapsed"
    fine = service.report_session(assisted, service.session_id(review), "done", now=_after(review))
    assert service.find_session(fine, service.session_id(review)).outcome == "recalled"


def test_a_report_needs_no_network_and_a_started_session(assisted):
    # A link that can never be read (.invalid is reserved): a report that tried
    # would come back with an error.
    linked = replace(assisted, source_url="https://timetable.invalid/x.ics")
    task = _first(linked, "task")
    sid = service.session_id(task)
    with pytest.raises(service.InvalidInput, match="not started"):
        service.report_session(linked, sid, "done", now=_after(task) - timedelta(hours=1))
    with pytest.raises(service.InvalidInput, match="no longer in your plan"):
        service.report_session(linked, "0" * 12, "done", now=_after(task))
    with pytest.raises(service.InvalidInput):
        service.report_session(linked, sid, "maybe", now=_after(task))
    reported = service.report_session(linked, sid, "done", now=_after(task))
    assert reported.error is None and reported.outcomes == {sid: "done"}
    assert service.refresh_subscription(linked, now=_after(task)).error  # while a refresh reads it


def test_a_revision_replans_at_once(assisted):
    revised = service.revise_subscription(assisted, preferences={"rest_days": ["Sat", "Sun"]}, now=BEFORE)
    days = {
        datetime.fromisoformat(s.start).weekday() for s in service.PlanReport.from_dict(revised.plan).sessions
    }
    assert days.isdisjoint({5, 6})
    assert revised.options["preferences"]["weekly_hours"] == 12  # the rest is kept
    fewer = service.revise_subscription(assisted, tasks=[], now=BEFORE)
    assert not any(s.kind == "task" for s in service.PlanReport.from_dict(fewer.plan).sessions)
    with pytest.raises(service.InvalidInput, match="unknown"):
        service.revise_subscription(assisted, colour="red")


def test_a_subscription_expires_a_month_after_its_last_exam_or_deadline(assisted):
    plan = service.PlanReport.from_dict(assisted.plan)
    last = max(datetime.fromisoformat(s.exam) for s in plan.subjects)
    assert service.subscription_expiry(assisted) == last + service.RETENTION


def test_a_feed_with_an_address_carries_the_report_link(assisted):
    text = service.feed_ics(assisted, "https://cps.example.org/").decode("utf-8").replace("\r\n ", "")
    task = _first(assisted, "task")
    assert f"https://cps.example.org/s/{assisted.token}/{service.session_id(task)}" in text
    assert "/s/" not in service.feed_ics(assisted).decode("utf-8")


def test_reports_and_refreshes_do_not_overwrite_each_other(assisted, tmp_path):
    """A refresh that started before a report must not erase it (`Store.update`)."""
    service.save_subscription(tmp_path, assisted)
    task = _first(assisted, "task")
    sid = service.session_id(task)

    calls = []

    def refresh_during_which_a_report_arrives(current):
        calls.append(dict(current.outcomes))
        if len(calls) == 1:
            service.update_subscription(
                tmp_path, current.token, lambda s: service.report_session(s, sid, "skipped", now=_after(task))
            )
        return service.refresh_subscription(current, now=_after(task), reread=False)

    service.update_subscription(tmp_path, assisted.token, refresh_during_which_a_report_arrives)
    assert calls == [{}, {sid: "skipped"}]  # the refresh was made again, from the report
    assert service.load_subscription(tmp_path, assisted.token).outcomes == {sid: "skipped"}
