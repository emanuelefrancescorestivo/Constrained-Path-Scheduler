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
    assert "Check what was found" in page and "Exam Mon 25 Jan 2027, 13:45 · from your timetable<" in page


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
    # Setup goes on to the week, to draw busy times, and from there to the links.
    assert answer.headers["location"] == f"/p/{token}/week?new=1"
    week = web.get(answer.headers["location"]).text
    assert "Your week." in week and f'href="/p/{token}/feed?new=1"' in week
    feed_page = web.get(f"/p/{token}/feed?new=1").text
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
        {"label": "Training", "start": "18:00", "end": "20:00", "weekday": 2, "date": None, "kind": "other"},
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
    assert answer.headers["location"] == f"/p/{planned}?saved=added"
    assert "Added. The plan has made room for it." in web.get(answer.headers["location"]).text
    assert [t["name"] for t in _subscription(web, planned).options["tasks"]] == ["Stats report", "Reading"]
    bad = web.post(f"/p/{planned}/tasks", data={"name": "Late", "due": "2020-01-01T09:00", "hours": "1"})
    assert bad.status_code == 400 and "before the start" in unescape(bad.text)


def test_the_week_is_a_list_of_days(web, planned):
    page = web.get(f"/p/{planned}/agenda").text  # the calendar for a browser without scripts
    assert page.count('<section class="section">') == 7
    assert "Work on: Stats report" in page and "Self-test: " in page
    # Timetable titles as a person reads them, not as ADE writes them.
    assert "Advanced Statistics · CM · Salle 3" in page and "Grp:" not in page
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
    assert "Recorded: Hard" in today
    assert 'value="struggled" aria-pressed="true"' in web.get(f"/s/{planned}/{sid}").text


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


def test_the_running_app_backs_up_the_store(tmp_path):
    config = Config(
        store=tmp_path / "db", background=False, sweep_every=None, backup_dir=tmp_path / "backups"
    )
    Store(tmp_path / "db").put("k" * 32, {"x": 1})
    with TestClient(create_app(config)):
        deadline = time.monotonic() + 5
        while not list((tmp_path / "backups").glob("cps-*.sqlite")) and time.monotonic() < deadline:
            time.sleep(0.02)
    (copy,) = (tmp_path / "backups").glob("cps-*.sqlite")
    assert Store(copy).get("k" * 32) == {"x": 1}


def test_the_blueprint_starts_a_command_that_exists(monkeypatch):
    """render.yaml's start command parses with the CLI it names, and the settings
    it gives are the ones the app reads."""
    import shlex

    from cps.cli import _build_parser

    text = (Path(__file__).resolve().parent.parent / "render.yaml").read_text(encoding="utf-8")
    command = re.search(r"startCommand: (.+)", text).group(1)
    words = shlex.split(command)
    assert words[0] == "cps"
    args = _build_parser().parse_args(words[1:])
    assert args.command == "web" and args.behind_proxy and args.db == Path("/var/data/cps.sqlite")
    assert "healthCheckPath: /health" in text and "region: frankfurt" in text
    monkeypatch.setenv("CPS_BACKUP_DIR", "/var/data/backups")
    monkeypatch.setenv("CPS_CONTACT", "owner@example.org")
    monkeypatch.delenv("CPS_BASE_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://study-plan.example.onrender.com")
    config = Config.from_env()
    assert config.backup_dir == Path("/var/data/backups") and config.contact == "owner@example.org"
    assert config.base_url == "https://study-plan.example.onrender.com"


def test_the_web_app_imports_nothing_from_cps_but_the_service():
    """Invariant 11: the pages read forms and fill templates; every decision, and
    every access to the store, goes through `cps.service`."""
    import ast

    root = Path(__file__).resolve().parent.parent / "src" / "cps" / "web"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("cps"):
                assert node.module == "cps" and [a.name for a in node.names] == ["service"], (
                    path.name,
                    node.module,
                )
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("cps") for a in node.names), path.name


def test_the_link_of_any_session_opens_it(web, planned):
    """A calendar shows weeks ahead: the link of a session a month away opens it."""
    plan = service.PlanReport.from_dict(_subscription(web, planned).plan)
    far = plan.sessions[-1]
    page = web.get(f"/s/{planned}/{service.session_id(far)}")
    assert page.status_code == 200 and unescape(far.subject) in unescape(page.text)
    assert "once it has started" in page.text
    gone = web.get(f"/s/{planned}/000000000000")
    assert gone.status_code == 404 and "no longer in your plan" in gone.text


# --------------------------------------------------------------------------- #
# The calendar to drag on
# --------------------------------------------------------------------------- #


def test_the_calendar_data_has_the_timetable_the_sessions_and_the_busy_times(web, planned):
    data = web.get(f"/p/{planned}/calendar.json?start=2026-09-29&days=3").json()
    assert [d["date"] for d in data["days"]] == ["2026-09-29", "2026-09-30", "2026-10-01"]
    kinds = {i["kind"] for i in data["items"]}
    assert {"calendar", "study"} <= kinds
    study = [i for i in data["items"] if i["kind"] == "study"]
    assert all(i["id"] and i["label"].split(":")[0] in ("Self-test", "Work on", "Practice") for i in study)
    assert all("Grp:" not in i["label"] for i in data["items"])
    assert data["activities"] == [] and data["planned"] and data["window"] == [8.0, 22.0]
    assert [k["id"] for k in data["kinds"]] == ["training", "commute", "work", "timeoff", "other"]
    assert web.get(f"/p/{planned}/calendar.json?start=nonsense").status_code == 400
    assert web.get("/p/" + "x" * 32 + "/calendar.json").status_code == 404


def test_a_busy_time_drawn_on_the_calendar_moves_the_plan(web, planned):
    before = web.get(f"/p/{planned}/calendar.json?start=2026-09-29&days=7").json()
    first = next(i for i in before["items"] if i["kind"] == "study")
    day = before["days"][first["day"]]
    rows = [
        {
            "label": "Away",
            "kind": "timeoff",
            "weekday": None,
            "date": day["date"],
            "start": "00:00",
            "end": "24:00",
        }
    ]
    answer = web.post(f"/p/{planned}/activities?start=2026-09-29&days=7", json=rows)
    assert answer.status_code == 200, answer.text
    after = answer.json()
    assert after["activities"] == [
        {
            "label": "Away",
            "kind": "timeoff",
            "weekday": None,
            "date": day["date"],
            "start": "00:00",
            "end": "24:00",
        }
    ]
    assert not [i for i in after["items"] if i["kind"] == "study" and i["day"] == first["day"]]
    assert [i for i in after["items"] if i["kind"] == "study"], "the work moves to other days"
    stored = _subscription(web, planned)
    assert stored.busy_rows[0]["kind"] == "timeoff" and stored.busy_rows[0]["date"] == day["date"]
    assert web.store.events(planned)[-1]["kind"] == "activities"


def test_busy_times_can_be_drawn_before_any_exam_is_known(web, planned):
    form = _form(web.get(f"/p/{planned}/settings").text)
    # A plan whose exams are dropped: nothing to plan, but the week can be drawn.
    subscription = _subscription(web, planned)
    bare = service.revise_subscription(subscription, subjects=[], tasks=[], strict=False)
    service.save_subscription(web.store.path, bare)
    rows = [
        {"label": "Job", "kind": "work", "weekday": "Sat", "date": None, "start": "10:00", "end": "18:00"}
    ]
    answer = web.post(f"/p/{planned}/activities", json=rows)
    assert answer.status_code == 200 and "add a subject" in answer.json()["error"]
    assert _subscription(web, planned).busy_rows[0]["label"] == "Job"
    assert form  # the settings page still renders for such a plan


def test_nonsense_busy_times_are_refused_and_change_nothing(web, planned):
    for body in (
        {"not": "a list"},
        [1, 2],
        [{"label": "x", "start": "25:00", "end": "26:00", "weekday": "Mon"}],
    ):
        answer = web.post(f"/p/{planned}/activities", json=body)
        assert answer.status_code == 400 and answer.json()["message"]
    bad_json = web.post(
        f"/p/{planned}/activities", content=b"{", headers={"content-type": "application/json"}
    )
    assert bad_json.status_code == 400
    assert _subscription(web, planned).busy_rows == ()


def test_the_week_page_loads_the_calendar_script_under_the_policy(web, planned):
    page = web.get(f"/p/{planned}/week")
    assert 'type="module" src="/static/app.js"' in page.text
    assert 'data-first="2026-09-29"' in page.text
    policy = page.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "unsafe-inline" not in policy
    for name in ("app.js", "calendar.js", "style.css"):
        asset = web.get(f"/static/{name}")
        assert asset.status_code == 200 and asset.text
    # No inline script or style attribute that the policy would refuse.
    assert "<script>" not in page.text and " style=" not in page.text


# --------------------------------------------------------------------------- #
# Moving sessions, and the panel beside the calendar
# --------------------------------------------------------------------------- #


def _movable(web, token) -> dict:
    data = web.get(f"/p/{token}/calendar.json?start=2026-09-29&days=7").json()
    return next(i for i in data["items"] if i["kind"] == "study" and i["movable"])


def test_the_calendar_colours_each_course_and_says_what_can_move(web, planned):
    data = web.get(f"/p/{planned}/calendar.json?start=2026-09-29&days=7").json()
    legend = {entry["course"]: entry["color"] for entry in data["legend"]}
    assert legend["Computer Programming 3"] == 0 and len(set(legend.values())) == len(legend) <= 7
    lectures = [i for i in data["items"] if i["kind"] == "calendar" and i["course"] == "Algebra 3"]
    reviews = [i for i in data["items"] if i["kind"] == "study" and i["course"] == "Algebra 3"]
    assert lectures and reviews and {i["color"] for i in lectures + reviews} == {legend["Algebra 3"]}
    assert data["now"] == {"date": "2026-09-29", "minute": 8 * 60}
    study = [i for i in data["items"] if i["kind"] == "study"]
    assert all(i["movable"] and not i["started"] for i in study)  # nothing has started at 08:00


def test_a_session_dragged_on_the_calendar_moves_and_stays(web, planned):
    item = _movable(web, planned)
    answer = web.post(
        f"/p/{planned}/sessions/{item['id']}/move?start=2026-09-29&days=7", json={"to": "2026-10-04T10:00"}
    )
    assert answer.status_code == 200, answer.text
    moved = [i for i in answer.json()["items"] if i["kind"] == "study" and i["pinned"]]
    assert [(i["label"], i["at"][:16]) for i in moved] == [(item["label"], "2026-10-04T10:00")]
    assert web.store.events(planned)[-1]["kind"] == "move"
    panel = web.get(f"/p/{planned}/panel").text
    assert "<html" not in panel  # a fragment, for the page to put in place
    assert "Placed by you" in panel or ">placed<" in panel
    back = web.post(f"/p/{planned}/sessions/{moved[0]['id']}/unpin")
    assert back.status_code == 200 and not [i for i in back.json()["items"] if i.get("pinned")]


def test_a_move_the_plan_cannot_take_is_refused_with_the_reason(web, planned):
    item = _movable(web, planned)
    lecture = web.post(f"/p/{planned}/sessions/{item['id']}/move", json={"to": "2026-09-30T11:30"})
    assert lecture.status_code == 400 and "that time is taken" in lecture.json()["message"]
    past = web.post(f"/p/{planned}/sessions/{item['id']}/move", json={"to": "2026-09-28T10:00"})
    assert past.status_code == 400 and "past" in past.json()["message"]
    nonsense = web.post(f"/p/{planned}/sessions/{item['id']}/move", json=["not", "a", "change"])
    assert nonsense.status_code == 400
    gone = web.post(f"/p/{planned}/sessions/000000000000/move", json={"to": "2026-10-04T10:00"})
    assert gone.status_code == 400 and "no longer in your plan" in gone.json()["message"]
    assert _subscription(web, planned).options.get("pins", []) == []


def test_a_session_is_reported_from_the_calendar(web, planned, clock):
    item = _movable(web, planned)
    clock.now = datetime.fromisoformat(item["at"]).astimezone(UTC) + timedelta(minutes=10)
    answer = web.post(f"/p/{planned}/sessions/{item['id']}/report", json={"outcome": "done"})
    assert answer.status_code == 200
    reported = next(i for i in answer.json()["items"] if i.get("id") == item["id"])
    assert reported["reported"] == "done" and reported["started"] and not reported["movable"]
    refused = web.post(f"/p/{planned}/sessions/{item['id']}/move", json={"to": "2026-10-04T10:00"})
    assert refused.status_code == 400 and "already started" in refused.json()["message"]


def test_the_panel_is_the_plan_pages_panel(web, planned):
    page = web.get(f"/p/{planned}").text
    panel = web.get(f"/p/{planned}/panel").text
    assert panel.strip() and panel.strip().split("\n")[0] in page
    assert "Tuesday 29 September" in panel and "sessions in the next 7 days" in panel
    assert "Stats report" in panel and ">Tasks<" in panel


# --------------------------------------------------------------------------- #
# Tasks, as in Motion: a list, a new-task sheet, ticked off when done
# --------------------------------------------------------------------------- #


def test_the_tasks_page_lists_every_task_with_where_it_stands(web, planned):
    page = web.get(f"/p/{planned}/tasks")
    assert page.status_code == 200
    text = unescape(page.text)
    assert "<h1>Tasks</h1>" in text and "1 open" in text and "Stats report" in text
    assert "0 of 4 sessions done" in text and "next " in text
    assert f'action="/p/{planned}/tasks/done"' in page.text


def test_every_page_of_a_plan_has_the_new_task_sheet(web, planned):
    for path in ("", "/week", "/tasks", "/settings", "/feed"):
        page = web.get(f"/p/{planned}{path}").text
        assert 'id="quick-add" class="sheet" popover' in page, path
        assert 'popovertarget="quick-add"' in page
        assert '<option value="Computer Programming 3">' in page  # courses suggested
    assert 'id="quick-add"' not in web.get("/").text  # not before there is a plan


def test_a_task_added_from_the_sheet_with_a_duration_pill_or_other_hours(web, planned):
    first = web.post(
        f"/p/{planned}/tasks",
        data={"name": "Reading", "due": "2026-10-05T23:59", "hours": "4", "hours_other": "", "back": "tasks"},
        follow_redirects=False,
    )
    assert first.headers["location"] == f"/p/{planned}/tasks?saved=added"
    second = web.post(
        f"/p/{planned}/tasks",
        data={
            "name": "Slides",
            "due": "2026-10-06T12:00",
            "hours": "2",
            "hours_other": "1.5",
            "back": "calendar",
        },
        follow_redirects=False,
    )
    assert second.headers["location"] == f"/p/{planned}/week?saved=added"
    hours = {t["name"]: t["hours"] for t in _subscription(web, planned).options["tasks"]}
    assert hours == {"Stats report": 6.0, "Reading": 4.0, "Slides": 1.5}
    bad = web.post(f"/p/{planned}/tasks", data={"name": "", "due": "2026-10-06T12:00", "back": "tasks"})
    assert bad.status_code == 400 and "give the task a name" in bad.text and "<h1>Tasks</h1>" in bad.text


def test_a_task_ticked_off_frees_its_sessions_and_can_be_reopened(web, planned):
    before = web.get(f"/p/{planned}/calendar.json?start=2026-09-29&days=7").json()
    assert any(i.get("session_kind") == "task" for i in before["items"])
    done = web.post(
        f"/p/{planned}/tasks/done",
        data={"name": "Stats report", "done": "1", "back": "tasks"},
        follow_redirects=False,
    )
    assert done.headers["location"] == f"/p/{planned}/tasks?saved=finished"
    after = web.get(f"/p/{planned}/calendar.json?start=2026-09-29&days=7").json()
    assert not any(i.get("session_kind") == "task" for i in after["items"])
    page = unescape(web.get(done.headers["location"]).text)
    assert "Done. Its remaining sessions are free time again." in page and "0 open" in page
    assert web.store.events(planned)[-1]["kind"] == "finished"
    web.post(f"/p/{planned}/tasks/done", data={"name": "Stats report", "done": "0", "back": "today"})
    assert "done" not in _subscription(web, planned).options["tasks"][0]
    assert web.store.events(planned)[-1]["kind"] == "reopened"


def test_a_task_is_deleted_and_an_unknown_one_is_refused(web, planned):
    gone = web.post(
        f"/p/{planned}/tasks/delete", data={"name": "Stats report", "back": "tasks"}, follow_redirects=False
    )
    assert gone.headers["location"] == f"/p/{planned}/tasks?saved=deleted"
    assert _subscription(web, planned).options["tasks"] == []
    again = web.post(f"/p/{planned}/tasks/delete", data={"name": "Stats report"})
    assert again.status_code == 400 and "no task called" in unescape(again.text)


def test_task_forms_go_back_only_to_the_plans_own_pages(web, planned):
    answer = web.post(
        f"/p/{planned}/tasks/done",
        data={"name": "Stats report", "done": "1", "back": "https://evil.example/"},
        follow_redirects=False,
    )
    assert answer.headers["location"] == f"/p/{planned}?saved=finished"
