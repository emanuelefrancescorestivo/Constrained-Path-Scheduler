"""
The hosted web app (`cps.web`): what a student does with it, and what it must
never do (change anything on a GET, write a secret address to a log, accept a
form from another site, keep data after deletion).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps.store import Store
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SEMESTER = EXAMPLES / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, the semester's first Tuesday


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(MORNING)


@pytest.fixture
def web(tmp_path, clock):
    config = Config(
        store=tmp_path, clock=clock, background=False, sweep_every=None, contact="owner@example.org"
    )
    with TestClient(create_app(config)) as client:
        client.store = Store(tmp_path)
        yield client


def _start(client, **data) -> str:
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = client.post("/start", data={"tz": "Europe/Paris", **data}, files=files, follow_redirects=False)
    assert answer.status_code == 303, answer.text
    assert answer.headers["location"].endswith("/settings?new=1")
    return answer.headers["location"].split("/")[2]


def _form(html: str) -> dict:
    """The settings form as a browser would submit it untouched."""
    form: dict = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        if 'type="checkbox"' in tag:
            if " checked" in tag:
                form.setdefault(name.group(1), []).append(unescape(value.group(1)))
            continue
        form[name.group(1)] = unescape(value.group(1)) if value else ""
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        chosen = re.search(r'<option(?: value="([^"]*)")? selected>([^<]*)', body)
        form[name] = (chosen.group(1) or chosen.group(2)) if chosen else ""
    return form


def _setup(client, token: str, **changes) -> dict:
    form = {**_form(client.get(f"/p/{token}/settings?new=1").text), **changes}
    answer = client.post(f"/p/{token}/settings", data=form, follow_redirects=False)
    assert answer.status_code == 303, re.findall(r'class="error"[^>]*>([^<]*)', answer.text)
    return form


@pytest.fixture
def planned(web):
    token = _start(web)
    _setup(web, token, t0_name="Stats report", t0_due="2026-10-10T18:00", t0_hours="6")
    return token


def _subscription(web, token) -> service.Subscription:
    return service.load_subscription(web.store.path, token)


# --------------------------------------------------------------------------- #
# Starting
# --------------------------------------------------------------------------- #


def test_the_home_page_and_health(web):
    home = web.get("/")
    assert home.status_code == 200 and 'action="/start"' in home.text
    assert web.get("/health").text == "ok\n"
    assert web.get("/docs").status_code == 404  # no API explorer on a public server


def test_every_answer_carries_the_security_headers(web):
    for path in ("/", "/privacy", "/p/" + "x" * 32, "/static/style.css"):
        headers = web.get(path).headers
        assert headers["referrer-policy"] == "no-referrer"
        assert "default-src 'none'" in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
    assert web.get("/").headers["cache-control"] == "no-store"
    assert "set-cookie" not in web.get("/").headers


def test_starting_from_a_file_finds_the_exams(web):
    token = _start(web)
    assert web.store.events(token)[0]["kind"] == "start"
    subscription = _subscription(web, token)
    assert [s["name"] for s in subscription.subjects] == [
        "Computer Programming 3",
        "Algebra 3",
        "Advanced Statistics",
        "Analysis 3",
        "Ethics & Philosophy of AI",
        "Deep Learning 1",
    ]
    assert subscription.options["start"] == "2026-09-29"
    assert subscription.options["preferences"] == service.DEFAULT_PREFERENCES
    page = web.get(f"/p/{token}/settings?new=1").text
    assert "Check what was found" in page and "Exam from your timetable: 2027-01-25 13:45" in page


def test_starting_from_a_link_reads_it(web, timetable_server, monkeypatch):
    monkeypatch.setattr(service, "LINKS_MAY_BE_PRIVATE", True)
    answer = web.post(
        "/start",
        data={"link": f"{timetable_server}/sample-semester.ics", "tz": "Europe/Paris"},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    subscription = _subscription(web, answer.headers["location"].split("/")[2])
    assert subscription.source_url.endswith("/sample-semester.ics")
    assert subscription.ics_text.startswith("BEGIN:VCALENDAR")


def test_starting_with_nothing_or_nonsense_says_why(web, timetable_server, monkeypatch):
    empty = web.post("/start", data={"tz": "Europe/Paris"})
    assert empty.status_code == 400 and "timetable" in empty.text
    zone = web.post("/start", data={"tz": "Mars/Olympus"}, files={"file": ("t.ics", SEMESTER.read_bytes())})
    assert zone.status_code == 400 and "time zone" in zone.text
    monkeypatch.setattr(service, "LINKS_MAY_BE_PRIVATE", True)
    page = web.post("/start", data={"link": f"{timetable_server}/web-page", "tz": "Europe/Paris"})
    assert page.status_code == 400
    assert Store(web.store.path).count() == 0


def test_a_file_too_large_is_refused(web):
    huge = b"BEGIN:VCALENDAR\r\n" + b"X" * (5 * 1024 * 1024 + 10)
    answer = web.post("/start", data={"tz": "Europe/Paris"}, files={"file": ("t.ics", huge)})
    assert answer.status_code == 413


def test_starting_is_rate_limited_per_address(tmp_path, clock):
    config = Config(store=tmp_path, clock=clock, background=False, sweep_every=None, start_limit=(2, 3600))
    with TestClient(create_app(config)) as client:
        _start(client)
        _start(client)
        assert client.post("/start", data={"tz": "Europe/Paris"}).status_code == 429


# --------------------------------------------------------------------------- #
# Settings and Today
# --------------------------------------------------------------------------- #


def test_setup_makes_the_plan_and_shows_both_addresses(web):
    token = _start(web)
    assert _subscription(web, token).plan is not None  # exams were found: planned at once
    form = _form(web.get(f"/p/{token}/settings?new=1").text)
    answer = web.post(
        f"/p/{token}/settings",
        data={
            **form,
            "t0_name": "Stats report",
            "t0_due": "2026-10-10T18:00",
            "t0_hours": "6",
            "rest": ["Sat", "Sun"],
        },
        follow_redirects=False,
    )
    assert answer.headers["location"] == f"/p/{token}/feed?new=1"
    feed_page = web.get(answer.headers["location"]).text
    assert f"http://testserver/feed/{token}.ics" in feed_page
    assert f"webcal://testserver/feed/{token}.ics" in feed_page
    assert f"http://testserver/p/{token}" in feed_page
    subscription = _subscription(web, token)
    assert subscription.options["tasks"] == [
        {"name": "Stats report", "due": "2026-10-10 18:00", "hours": 6.0, "course": ""}
    ]
    assert subscription.options["preferences"]["rest_days"] == ["Sat", "Sun"]
    plan = service.PlanReport.from_dict(subscription.plan)
    assert all(datetime.fromisoformat(s.start).weekday() < 5 for s in plan.sessions)
    assert "Stats report" in web.get(f"/p/{token}").text


def test_a_mistake_in_the_settings_keeps_what_was_typed(web, planned):
    form = _form(web.get(f"/p/{planned}/settings").text)
    form.update(s8_name="Quantum Basket Weaving", s8_exam="", t1_name="Essay", t1_due="2026-10-20T12:00")
    answer = web.post(f"/p/{planned}/settings", data=form)
    assert answer.status_code == 400
    assert "Quantum Basket Weaving: give the exam" in unescape(answer.text)
    assert 'value="Quantum Basket Weaving"' in answer.text and 'value="Essay"' in answer.text
    assert [t["name"] for t in _subscription(web, planned).options["tasks"]] == ["Stats report"]


def test_an_exam_can_be_dropped_and_a_busy_time_added(web, planned):
    form = _form(web.get(f"/p/{planned}/settings").text)
    form.update(s0_remove="1", a0_label="Training", a0_weekday="Wed", a0_start="18:00", a0_end="20:00")
    assert web.post(f"/p/{planned}/settings", data=form, follow_redirects=False).status_code == 303
    subscription = _subscription(web, planned)
    assert "Computer Programming 3" not in [s["name"] for s in subscription.subjects]
    assert subscription.busy_rows == (
        {"label": "Training", "start": "18:00", "end": "20:00", "weekday": 2, "date": None},
    )
    page = web.get(f"/p/{planned}/settings").text
    assert "Also in your timetable: <strong>Computer Programming 3</strong>" in page
    assert 'value="Training"' in page


def test_a_deadline_added_from_today(web, planned):
    answer = web.post(
        f"/p/{planned}/tasks",
        data={"name": "Reading", "due": "2026-10-05T09:00", "hours": "3", "course": "Ethics"},
        follow_redirects=False,
    )
    assert answer.headers["location"] == f"/p/{planned}?saved=1"
    assert [t["name"] for t in _subscription(web, planned).options["tasks"]] == ["Stats report", "Reading"]
    bad = web.post(f"/p/{planned}/tasks", data={"name": "Late", "due": "2020-01-01T09:00", "hours": "1"})
    assert bad.status_code == 400 and "before the start" in unescape(bad.text)


def test_the_week_is_a_list_of_days(web, planned):
    page = web.get(f"/p/{planned}/week").text
    assert page.count('<section class="day">') == 7
    assert "Deadline work: Stats report" in page and "Advanced Statistics" in page
    assert web.get(f"/p/{planned}/week?w=3").status_code == 200


def test_a_plan_with_nothing_to_plan_says_what_to_add(web, planned):
    form = _form(web.get(f"/p/{planned}/settings").text)
    for i in range(6):
        form[f"s{i}_remove"] = "1"
    form["t0_remove"] = "1"
    answer = web.post(f"/p/{planned}/settings", data=form)
    assert answer.status_code == 400 and "add a subject" in answer.text


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


def _first_task(web, token) -> service.SessionView:
    plan = service.PlanReport.from_dict(_subscription(web, token).plan)
    return next(s for s in plan.sessions if s.kind == "task")


def test_the_link_in_an_event_asks_and_only_the_button_records(web, planned, clock):
    session = _first_task(web, planned)
    sid = service.session_id(session)
    clock.now = datetime.fromisoformat(session.start).astimezone(UTC) + timedelta(minutes=10)
    before = _subscription(web, planned)
    page = web.get(f"/s/{planned}/{sid}")
    assert page.status_code == 200 and "How did it go?" in page.text
    assert _subscription(web, planned) == before  # a GET changes nothing
    answer = web.post(f"/s/{planned}/{sid}", data={"outcome": "struggled"}, follow_redirects=False)
    assert answer.headers["location"] == f"/p/{planned}?reported=struggled"
    after = _subscription(web, planned)
    assert after.outcomes == {sid: "struggled"}
    assert after.options["tasks"][0]["hours"] == 7.5  # one more block of 90 minutes
    assert [e["kind"] for e in web.store.events(planned)][-1] == "report"
    today = web.get(f"/p/{planned}?reported=struggled").text
    assert "Recorded: Hard" in today and "Reported: Hard" in today


def test_a_session_to_come_cannot_be_reported(web, planned):
    sid = service.session_id(_first_task(web, planned))
    answer = web.post(f"/s/{planned}/{sid}", data={"outcome": "done"})
    assert answer.status_code == 400 and "not started" in answer.text
    assert _subscription(web, planned).outcomes == {}


def test_today_asks_about_the_past_week(web, planned, clock):
    clock.now = MORNING + timedelta(days=3)
    page = web.get(f"/p/{planned}").text
    assert "How did these go?" in page
    assert page.count('name="outcome" value="done"') >= 4


def test_a_form_from_another_site_is_refused(web, planned, clock):
    session = _first_task(web, planned)
    sid = service.session_id(session)
    clock.now = datetime.fromisoformat(session.start).astimezone(UTC) + timedelta(minutes=10)
    for headers in (
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://evil.example"},
    ):
        answer = web.post(f"/s/{planned}/{sid}", data={"outcome": "skipped"}, headers=headers)
        assert answer.status_code == 403
    assert _subscription(web, planned).outcomes == {}
    # What Chromium sends with the site's own form under Referrer-Policy: no-referrer
    # (AUDIT.md item 37).
    own = {"Sec-Fetch-Site": "same-origin", "Origin": "null"}
    assert (
        web.post(
            f"/s/{planned}/{sid}", data={"outcome": "done"}, headers=own, follow_redirects=False
        ).status_code
        == 303
    )


def test_reports_are_rate_limited_per_plan(tmp_path, clock):
    config = Config(store=tmp_path, clock=clock, background=False, sweep_every=None, change_limit=(1, 3600))
    with TestClient(create_app(config)) as client:
        client.store = Store(tmp_path)
        token = _start(client)
        _setup(client, token)
        assert (
            client.post(f"/p/{token}/tasks", data={"name": "x", "due": "2026-10-09T09:00"}).status_code == 429
        )


# --------------------------------------------------------------------------- #
# Feeds, privacy, deletion, logs
# --------------------------------------------------------------------------- #


def test_the_feed_carries_report_links(web, planned):
    answer = web.get(f"/feed/{planned}.ics")
    assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/calendar")
    text = answer.text.replace("\r\n ", "")
    sid = service.session_id(_first_task(web, planned))
    assert f"http://testserver/s/{planned}/{sid}" in text
    assert web.get(f"/feed/{planned}").status_code == 404
    assert web.get("/feed/" + "x" * 32 + ".ics").status_code == 404


def test_a_configured_address_is_used_for_links(tmp_path, clock):
    config = Config(
        store=tmp_path, clock=clock, background=False, sweep_every=None, base_url="https://plan.example.org/"
    )
    with TestClient(create_app(config)) as client:
        client.store = Store(tmp_path)
        token = _start(client)
        assert f"https://plan.example.org/feed/{token}.ics" in client.get(f"/p/{token}/feed").text
        assert f"https://plan.example.org/s/{token}/" in client.get(f"/feed/{token}.ics").text.replace(
            "\r\n ", ""
        )


def test_the_privacy_page_names_the_contact_and_the_retention(web):
    page = web.get("/privacy").text
    assert "owner@example.org" in page and "30 days after your last exam" in page


def test_unknown_addresses_are_not_found(web):
    for path in (
        "/p/" + "x" * 32,
        "/p/short",
        "/p/../../etc/passwd",
        "/s/" + "x" * 32 + "/abc",
        "/p/" + "x" * 32 + "/week",
    ):
        assert web.get(path).status_code == 404


def test_deletion_is_confirmed_then_complete(web, planned):
    assert "Delete everything?" in web.get(f"/p/{planned}/delete").text
    assert _subscription(web, planned) is not None  # asking deletes nothing
    assert web.post(f"/p/{planned}/delete").status_code == 200
    assert _subscription(web, planned) is None and web.store.events(planned) == []
    assert web.get(f"/p/{planned}").status_code == 404
    assert web.get(f"/feed/{planned}.ics").status_code == 404
    assert web.post(f"/p/{planned}/delete").status_code == 404


def test_the_log_names_routes_not_secrets(web, caplog):
    with caplog.at_level(logging.INFO, logger="cps.web"):
        token = _start(web)
        _setup(web, token)
        web.get(f"/p/{token}")
        web.get(f"/feed/{token}.ics")
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "POST /start 303" in logged and "GET /p/{token} 200" in logged
    assert token not in logged


def test_expired_plans_are_swept_while_the_app_runs(tmp_path):
    config = Config(
        store=tmp_path,
        clock=lambda: datetime(2028, 1, 1, tzinfo=UTC),
        background=False,
        sweep_every=timedelta(seconds=0.05),
    )
    Store(tmp_path).put("k" * 32, {}, "2027-03-01T00:00:00+00:00")
    Store(tmp_path).put("j" * 32, {}, "2028-03-01T00:00:00+00:00")
    with TestClient(create_app(config)):
        deadline = time.monotonic() + 5
        while Store(tmp_path).get("k" * 32) is not None and time.monotonic() < deadline:
            time.sleep(0.02)
    assert Store(tmp_path).get("k" * 32) is None and Store(tmp_path).get("j" * 32) == {}


def test_the_stored_expiry_follows_the_last_exam(web, planned):
    subscription = _subscription(web, planned)
    assert service.subscription_expiry(subscription) == datetime.fromisoformat(
        "2027-01-29T13:45:00+01:00"
    ) + timedelta(days=30)


def test_a_learning_platform_link_brings_its_deadlines(web, planned, timetable_server, monkeypatch):
    monkeypatch.setattr(service, "LINKS_MAY_BE_PRIVATE", True)
    form = _form(web.get(f"/p/{planned}/settings").text)
    form["deadlines_url"] = f"{timetable_server}/sample-moodle.ics"
    assert web.post(f"/p/{planned}/settings", data=form, follow_redirects=False).status_code == 303
    names = [t["name"] for t in _subscription(web, planned).options["tasks"]]
    assert names == ["Stats report", "Week 4 quiz", "Problem sheet 3", "Essay on fairness"]
    page = web.get(f"/p/{planned}/settings").text
    assert page.count("From your learning platform") == 3
