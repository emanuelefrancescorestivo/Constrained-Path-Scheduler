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


def _last_change(web, token):
    """The kind of the last event that changed the plan (a page's daily `visit` is
    not a change)."""
    return [e for e in web.store.events(token) if e["kind"] != "visit"][-1]["kind"]


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
    assert _last_change(web, planned) == "activities"


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
    assert _last_change(web, planned) == "move"
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
    assert "Tuesday 29 September" in panel and 'class="strip"' in panel and "this week" in panel
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
    assert _last_change(web, planned) == "finished"
    web.post(f"/p/{planned}/tasks/done", data={"name": "Stats report", "done": "0", "back": "today"})
    assert "done" not in _subscription(web, planned).options["tasks"][0]
    assert _last_change(web, planned) == "reopened"


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


# --------------------------------------------------------------------------- #
# Progress, the streak and the weekly review (DECISIONS.md, D8 and D9)
# --------------------------------------------------------------------------- #

MONDAY = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)  # 08:00 in Paris, the second Monday


def _first_week(web, token):
    plan = service.PlanReport.from_dict(_subscription(web, token).plan)
    return [s for s in plan.sessions if datetime.fromisoformat(s.start) < MONDAY]


def test_progress_counts_reported_sessions_and_never_scolds(web, planned, clock):
    clock.now = MONDAY
    sessions = _first_week(web, planned)
    assert len(sessions) >= 3
    empty = web.get(f"/p/{planned}/progress")
    assert empty.status_code == 200 and '<b class="big">0</b>' in empty.text
    for s in sessions:
        answer = web.post(
            f"/s/{planned}/{service.session_id(s)}", data={"outcome": "done"}, follow_redirects=False
        )
        assert answer.status_code == 303
    page = web.get(f"/p/{planned}/progress")
    text = unescape(page.text)
    days = len({datetime.fromisoformat(s.start).date() for s in sessions})
    assert f'<b class="big">{days}</b>' in text and f"{days} days studied" in text
    assert "First session done" in text and 'class="reached"' in text and "Since 28 September" in text
    assert text.count('<td class="d d-') == 4 * 7 and "d-studied" in text
    assert "Week of 28 September" in text and "Every session done." in text
    for word in ("lost", "broke", "missed your"):  # D9: a lost streak is never announced
        assert word not in text.lower()
    # This week's ring has no arc until a session of this week is done; then the arc
    # is an SVG attribute, which the Content-Security-Policy allows, not a style.
    assert 'class="ring-arc"' not in page.text
    plan = service.PlanReport.from_dict(_subscription(web, planned).plan)
    this_week = next(s for s in plan.sessions if datetime.fromisoformat(s.start) >= MONDAY)
    clock.now = datetime.fromisoformat(this_week.end).astimezone(UTC)
    web.post(f"/s/{planned}/{service.session_id(this_week)}", data={"outcome": "done"})
    page = web.get(f"/p/{planned}/progress")
    assert 'class="ring-arc"' in page.text and "stroke-dasharray=" in page.text
    assert " style=" not in page.text


def test_today_shows_the_streak_and_on_monday_last_week_s_review_until_hidden(web, planned, clock):
    clock.now = MONDAY
    for s in _first_week(web, planned):
        web.post(f"/s/{planned}/{service.session_id(s)}", data={"outcome": "done"})
    today = unescape(web.get(f"/p/{planned}").text)
    assert 'class="strip"' in today and "in a row" in today
    assert "Week of 28 September" in today and "Hide until next week" in today
    # The panel the page fetches again after each change carries the same strip.
    assert 'class="strip"' in web.get(f"/p/{planned}/panel").text
    hidden = web.post(f"/p/{planned}/review/seen", data={"week": "2026-09-28"}, follow_redirects=False)
    assert hidden.status_code == 303
    assert "Hide until next week" not in web.get(f"/p/{planned}").text
    assert web.post(f"/p/{planned}/review/seen", data={"week": "soon"}).status_code == 400
    # From Wednesday, the review lives on the Progress page only.
    clock.now = MONDAY + timedelta(days=2)
    assert "Hide until next week" not in web.get(f"/p/{planned}").text


def test_a_visit_is_counted_once_a_day(web, planned, clock):
    for _ in range(3):
        web.get(f"/p/{planned}")
    web.get(f"/p/{planned}/progress")
    visits = [e for e in web.store.events(planned) if e["kind"] == "visit"]
    assert len(visits) == 1 and visits[0]["detail"] == ""


# --------------------------------------------------------------------------- #
# French (DECISIONS.md, D10)
# --------------------------------------------------------------------------- #


def test_a_plan_started_in_french_is_french_and_can_switch(web, clock):
    home = web.get("/", headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}).text
    assert '<html lang="fr">' in home and "Planifier mon semestre" in home
    assert 'value="fr"' in home  # the start form carries the language
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    started = web.post(
        "/start", data={"tz": "Europe/Paris", "lang": "fr"}, files=files, follow_redirects=False
    )
    token = started.headers["location"].split("/")[2]
    assert _subscription(web, token).options["lang"] == "fr"
    _setup(web, token, t0_name="Rapport de stats", t0_due="2026-10-10T18:00", t0_hours="6")
    # Every page of the plan speaks French, whatever the browser says.
    for path in ("", "/tasks", "/progress", "/settings", "/feed"):
        page = unescape(web.get(f"/p/{token}{path}", headers={"Accept-Language": "en"}).text)
        assert '<html lang="fr">' in page, path
        for english in (">Settings<", ">Tasks<", ">Today<", "New task<"):
            assert english not in page, (path, english)
    today = unescape(web.get(f"/p/{token}").text)
    assert "Aujourd'hui" in today and "mardi 29 septembre" in today
    # What each session says to do is written in French when the plan is made.
    plan = service.PlanReport.from_dict(_subscription(web, token).plan)
    first = plan.sessions[0]
    assert first.detail.startswith(("Travailler sur", "Première révision", "Teste-toi")), first.detail
    assert "Pour le" in unescape(web.get(f"/p/{token}/tasks").text)
    # The calendar feed is French too.
    feed = web.get(f"/feed/{token}.ics").content.decode("utf-8")
    assert "Travailler sur" in feed or "Première révision" in feed
    # A report survives a change of language: sessions are named by language-free ids.
    clock.now = datetime.fromisoformat(first.end).astimezone(UTC)
    sid = service.session_id(first)
    web.post(f"/s/{token}/{sid}", data={"outcome": "done"})
    switched = web.post(f"/p/{token}/language", data={"lang": "en"}, follow_redirects=False)
    assert switched.status_code == 303 and switched.headers["location"].endswith("/settings?saved=language")
    after = _subscription(web, token)
    assert after.options["lang"] == "en" and after.outcomes[sid] == "done"
    page = unescape(web.get(switched.headers["location"]).text)
    assert '<html lang="en">' in page and "Language changed." in page
    assert (
        service.PlanReport.from_dict(after.plan)
        .sessions[0]
        .detail.startswith(("Work on", "First review", "Test yourself"))
    )
    assert web.post(f"/p/{token}/language", data={"lang": "de"}).status_code == 400


def test_pages_without_a_plan_follow_the_browser_or_the_address(web):
    assert '<html lang="en">' in web.get("/privacy").text
    french = unescape(web.get("/privacy?lang=fr").text)
    assert '<html lang="fr">' in french and "Ce qui est conservé" in french
    assert '<html lang="fr">' in web.get("/", headers={"Accept-Language": "fr"}).text
    # Nothing is remembered: no cookie is set to carry the choice.
    assert "set-cookie" not in web.get("/?lang=fr").headers


# --------------------------------------------------------------------------- #
# Focus sessions and the diary (DECISIONS.md D17, D18)
# --------------------------------------------------------------------------- #

TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xe1\x00\x14Exif\x00\x00GPS 48.84 N\x00"
    b"\xff\xc0\x00\x11\x08\x00\x10\x00\x10\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00pixels\xff\xd9"
)


def test_a_focus_session_is_timed_logged_and_kept_in_the_diary(web, planned, clock):
    page = unescape(web.get(f"/p/{planned}/focus").text)
    assert "Start this session" in page or "Start the timer" in page
    web.post(f"/p/{planned}/focus/start", data={"course": "Algebra 3"})
    running = unescape(web.get(f"/p/{planned}/focus").text)
    assert "Finish" in running and "/static/focus.js" in running and "Algebra 3" in running
    assert "A session is running" in unescape(web.get(f"/p/{planned}").text)
    for minute in range(0, 50):
        clock.now = MORNING + timedelta(seconds=30 * minute)
        assert web.post(f"/p/{planned}/focus/beat").status_code == 200
    clock.now = MORNING + timedelta(minutes=45)  # 20 minutes without a beat: away
    beat = web.post(f"/p/{planned}/focus/beat").json()
    assert beat["interruptions"] == 1 and beat["away_minutes"] >= 19
    finished = web.post(f"/p/{planned}/focus/finish", follow_redirects=False)
    assert finished.headers["location"].endswith("/log")
    form = unescape(web.get(f"/p/{planned}/log").text)
    assert "left the app 1 time" in form and 'name="effort"' in form
    # Without a handle only "Only me" can be chosen; sharing needs a profile (D16).
    assert 'value="followers" disabled' in form
    refused = web.post(
        f"/p/{planned}/log",
        data={"course": "Algebra 3", "effort": "6", "progress": "4", "visibility": "everyone"},
    )
    assert refused.status_code == 400 and "choose a handle" in unescape(refused.text)
    joined = web.post(
        f"/p/{planned}/profile",
        data={"handle": "@Ada.L", "university": "Lyon 1", "programme": "L2 Maths", "old_enough": "1"},
        follow_redirects=False,
    )
    assert joined.headers["location"].endswith("/community?tab=explore&saved=joined")
    saved = web.post(
        f"/p/{planned}/log",
        data={
            "course": "Algebra 3",
            "title": "Sheet 2",
            "effort": "6",
            "progress": "4",
            "visibility": "followers",
        },
        files=[("photos", ("notes.jpg", TINY_JPEG, "image/jpeg"))],
        follow_redirects=False,
    )
    assert saved.status_code == 303 and saved.headers["location"].endswith("?saved=logged#diary")
    diary = unescape(web.get(saved.headers["location"]).text)
    assert "Saved in your diary." in diary and "Sheet 2" in diary and "6/10" in diary
    photo = re.search(r'src="(/p/[^"]+/m/[^"]+)"', diary).group(1)
    shown = web.get(photo)
    assert shown.status_code == 200 and shown.headers["content-type"] == "image/jpeg"
    assert b"GPS" not in shown.content and shown.headers["x-content-type-options"] == "nosniff"
    # Followers-only: not at the public address, and not to another plan.
    name = photo.rsplit("/", 1)[1]
    assert web.get(f"/m/{name}").status_code == 404
    other = _start(web)
    assert web.get(f"/p/{other}/m/{name}").status_code == 404
    # The day counts for the streak even with nothing planned; the post can go.
    post = re.search(r'id="post-([^"]+)"', diary).group(1)
    web.post(f"/p/{planned}/posts/{post}/visibility", data={"visibility": "everyone"})
    assert web.get(f"/m/{name}").status_code == 200
    web.post(f"/p/{planned}/posts/{post}/delete")
    assert web.get(f"/m/{name}").status_code == 404
    assert "Nothing logged yet" in unescape(web.get(f"/p/{planned}/progress").text)


def test_a_log_with_a_mistake_is_shown_again_as_typed(web, planned):
    answer = web.post(
        f"/p/{planned}/log", data={"course": "Algebra 3", "title": "Kept", "effort": "12", "progress": "3"}
    )
    text = unescape(answer.text)
    assert answer.status_code == 400 and "Effort is a number from 1 to 10" in text and 'value="Kept"' in text
    bad = web.post(
        f"/p/{planned}/log",
        data={"course": "x", "effort": "5", "progress": "3"},
        files=[("photos", ("x.svg", b"<svg onload=alert(1)>", "image/svg+xml"))],
    )
    assert bad.status_code == 400 and "JPEG or a PNG" in unescape(bad.text)


def _profile(web, token, handle, **extra):
    form = {"handle": handle, "university": "Lyon 1", "programme": "L2 Maths", "old_enough": "1", **extra}
    return web.post(f"/p/{token}/profile", data=form, follow_redirects=False)


def test_two_students_follow_and_cheer_each_other(web, planned):
    page = unescape(web.get(f"/p/{planned}/community").text)
    assert "Choose a handle" in page and 'aria-current="page">Community' in page
    refused = _profile(web, planned, "ab")
    assert refused.status_code == 400 and "3 to 20 letters" in unescape(refused.text)
    assert 'value="ab"' in refused.text  # what was typed is kept
    assert _profile(web, planned, "ada").headers["location"].endswith("tab=explore&saved=joined")
    other = _start(web)
    assert _profile(web, other, "Bob", university="Politecnico di Milano").status_code == 303
    assert _profile(web, other, "ADA").status_code == 400  # taken, whatever the case
    web.post(
        f"/p/{planned}/log",
        data={
            "course": "Algebra 3",
            "title": "Sheet 3",
            "effort": "7",
            "progress": "4",
            "visibility": "followers",
        },
    )
    # Bob finds Ada, sees nothing of hers, asks to follow; she accepts.
    found = unescape(web.get(f"/p/{other}/people?q=lyon").text)
    assert "@ada" in found and f"/p/{other}/u/ada" in found
    profile = unescape(web.get(f"/p/{other}/u/ada").text)
    assert "Lyon 1 · L2 Maths" in profile and "Sheet 3" not in profile and ">Follow<" in profile
    web.post(f"/p/{other}/u/ada/follow", data={"next": f"/p/{other}/u/ada"})
    assert "Requested" in unescape(web.get(f"/p/{other}/u/ada").text)
    circle = unescape(web.get(f"/p/{planned}/people").text)
    assert "Asking to follow you" in circle and "@bob" in circle
    assert 'class="count"' in web.get(f"/p/{planned}/community").text
    web.post(f"/p/{planned}/requests/bob", data={"answer": "accept"})
    feed = unescape(web.get(f"/p/{other}/community").text)
    assert "Sheet 3" in feed and "@ada" in feed
    post = re.search(r'id="post-([^"]+)"', feed).group(1)
    # Kudos, back to the page it came from; a foreign "next" is ignored.
    given = web.post(
        f"/p/{other}/post/{post}/kudos",
        data={"next": f"/p/{other}/community#post-{post}"},
        follow_redirects=False,
    )
    assert given.headers["location"] == f"/p/{other}/community#post-{post}"
    away = web.post(
        f"/p/{other}/post/{post}/kudos", data={"next": "https://evil.example/"}, follow_redirects=False
    )
    assert away.headers["location"] == f"/p/{other}/post/{post}"
    web.post(f"/p/{other}/post/{post}/kudos", data={"next": "//evil.example/"})
    assert 'aria-pressed="true"' in web.get(f"/p/{other}/community").text
    commented = web.post(f"/p/{other}/post/{post}/comments", data={"body": "Bravo, keep going!"})
    assert "Bravo, keep going!" in unescape(commented.text)
    empty = web.post(f"/p/{other}/post/{post}/comments", data={"body": "  "})
    assert empty.status_code == 400 and "write something" in unescape(empty.text)
    # Ada sees the comment and the kudos; she deletes the comment on her post.
    mine = unescape(web.get(f"/p/{planned}/post/{post}").text)
    assert "Bravo, keep going!" in mine and "1 comment" in mine and 'act-count">1<' in mine
    comment = re.search(r"/comments/([^/]+)/delete", mine).group(1)
    web.post(f"/p/{planned}/comments/{comment}/delete", data={"next": f"/p/{planned}/post/{post}"})
    assert "Bravo" not in unescape(web.get(f"/p/{planned}/post/{post}").text)
    # Removed, Bob no longer sees the post; its address says so.
    web.post(f"/p/{planned}/followers/bob/remove")
    gone = web.get(f"/p/{other}/post/{post}")
    assert gone.status_code == 404 and "no longer, visible" in unescape(gone.text)


def test_explain_it_simply_and_leaving_the_network(web, planned):
    form = unescape(web.get(f"/p/{planned}/explain").text)
    assert 'value="everyone" disabled' in form and "choose a handle" in form
    _profile(web, planned, "ada")
    posted = web.post(
        f"/p/{planned}/explain",
        data={
            "concept": "Eigenvalues",
            "course": "Algebra 3",
            "text": "A direction a matrix only stretches.",
        },
        follow_redirects=False,
    )
    assert posted.status_code == 303 and posted.headers["location"].endswith("?saved=posted")
    page = unescape(web.get(posted.headers["location"]).text)
    assert "Posted." in page and "Eigenvalues" in page and "I got it" in page and "Explained simply" in page
    other = _start(web)
    explore = unescape(web.get(f"/p/{other}/community?tab=explore&kind=explain&uni=lyon").text)
    assert "Eigenvalues" in explore and 'value="lyon"' in explore
    assert "Eigenvalues" not in unescape(web.get(f"/p/{other}/community?tab=explore&kind=session").text)
    left = web.post(f"/p/{planned}/profile/leave", follow_redirects=False)
    assert left.headers["location"].endswith("saved=left#diary")
    diary = unescape(web.get(left.headers["location"]).text)
    assert "You have left the network" in diary and "Eigenvalues" in diary
    assert "Eigenvalues" not in unescape(web.get(f"/p/{other}/community?tab=explore").text)
    assert web.get(f"/p/{other}/u/ada").status_code == 404


def test_the_notes_library_from_sharing_to_the_months_top(web, planned):
    form = unescape(web.get(f"/p/{planned}/notes/new").text)
    assert 'data-max-photos="8"' in form and 'value="everyone" disabled' in form
    _profile(web, planned, "ada")
    page = unescape(web.get(f"/p/{planned}/notes").text)
    assert "No notes here yet" in page and f'href="/p/{planned}/notes/new"' in page
    assert 'aria-current="page">Notes' in page
    fields = {"course": "Algebra 3", "title": "Eigenvalues in one page", "note": "Definitions, two examples."}
    refused = web.post(
        f"/p/{planned}/notes/new", data=fields, files=[("photos", ("p1.jpg", TINY_JPEG, "image/jpeg"))]
    )
    assert refused.status_code == 400 and "your own notes, in your own words" in unescape(refused.text)
    assert 'value="Eigenvalues in one page"' in refused.text  # what was typed is kept
    posted = web.post(
        f"/p/{planned}/notes/new",
        data={**fields, "own_work": "1"},
        files=[("photos", (f"p{i}.jpg", TINY_JPEG, "image/jpeg")) for i in range(2)],
        follow_redirects=False,
    )
    assert posted.status_code == 303 and posted.headers["location"].endswith("?saved=posted")
    mine = unescape(web.get(posted.headers["location"]).text)
    assert "Eigenvalues in one page" in mine and "2 pages" in mine and "0 helpful marks" in mine
    assert "/kudos" not in mine  # nobody marks their own notes
    post_id = posted.headers["location"].split("/")[-1].split("?")[0]
    readers = []
    for handle in ("bob", "cleo"):
        reader = _start(web)
        _profile(web, reader, handle)
        readers.append(reader)
        library = unescape(web.get(f"/p/{reader}/notes?course=algebra").text)
        assert "Eigenvalues in one page" in library and "@ada" in library and "Helpful" in library
        marked = web.post(
            f"/p/{reader}/post/{post_id}/kudos",
            data={"next": f"/p/{reader}/notes"},
            follow_redirects=False,
        )
        assert marked.status_code == 303 and marked.headers["location"] == f"/p/{reader}/notes"
    library = unescape(web.get(f"/p/{readers[0]}/notes").text)
    assert "No. 1 this month" in library and 'aria-pressed="true"' in library
    assert "Eigenvalues" not in unescape(web.get(f"/p/{readers[0]}/notes?uni=milano").text)
    newest = unescape(web.get(f"/p/{readers[0]}/notes?sort=new").text)
    assert '<option value="new" selected>' in newest
    explore = unescape(web.get(f"/p/{readers[0]}/community?tab=explore&kind=notes").text)
    assert "Eigenvalues in one page" in explore and f'href="/p/{readers[0]}/notes"' in explore
    profile = unescape(web.get(f"/p/{readers[0]}/u/ada").text)
    assert "Shared 1 set of notes · marked helpful 2 times" in profile
    guidelines = unescape(web.get("/guidelines").text)
    assert 'id="notes"' in guidelines and "owner@example.org" in guidelines
    web.post(f"/p/{readers[1]}/language", data={"lang": "fr"})
    french = unescape(web.get(f"/p/{readers[1]}/notes").text)
    assert "Partager des notes" in french and "N° 1 du mois" in french
    assert [e["detail"] for e in web.store.events(planned) if e["kind"] == "notes"] == ["everyone"]


def test_the_network_pages_in_french(web, planned):
    web.post(f"/p/{planned}/language", data={"lang": "fr"})
    _profile(web, planned, "ada")
    page = unescape(web.get(f"/p/{planned}/community?tab=explore").text)
    assert "Communauté" in page and "Explorer" in page and "Filière" in page
    assert "Règles de la communauté" in unescape(web.get("/guidelines?lang=fr").text)


def test_report_block_and_the_owners_review(tmp_path, clock):
    key = "k" * 30
    config = Config(store=tmp_path, clock=clock, background=False, sweep_every=None, admin_token=key)
    with TestClient(create_app(config)) as web:
        authors = [_start(web) for _ in range(4)]
        for token, handle in zip(authors, ("ada", "bob", "cleo", "eve"), strict=True):
            _profile(web, token, handle)
        ada = authors[0]
        web.post(
            f"/p/{ada}/log",
            data={
                "course": "Algebra 3",
                "title": "Past paper",
                "effort": "5",
                "progress": "3",
                "visibility": "everyone",
            },
        )
        feed = unescape(web.get(f"/p/{authors[1]}/community?tab=explore").text)
        post = re.search(r'id="post-([^"]+)"', feed).group(1)
        assert f"/p/{authors[1]}/report/post/{post}" in feed  # in the post's menu
        assert f"/p/{ada}/report/post/" not in unescape(web.get(f"/p/{ada}/community?tab=explore").text)
        form = unescape(web.get(f"/p/{authors[1]}/report/post/{post}").text)
        assert "Exam papers or answers" in form and "Also block @ada" in form
        none = web.post(f"/p/{authors[1]}/report/post/{post}", data={})
        assert none.status_code == 400 and "choose a reason" in unescape(none.text)
        sent = web.post(
            f"/p/{authors[1]}/report/post/{post}",
            data={"reason": "exam", "block": "1"},
            follow_redirects=False,
        )
        assert sent.headers["location"].endswith("saved=blocked")
        assert "Past paper" not in unescape(web.get(f"/p/{authors[1]}/community?tab=explore").text)
        assert "@ada" in unescape(web.get(f"/p/{authors[1]}/people").text)  # under Blocked, to undo
        for token in authors[2:]:
            web.post(f"/p/{token}/report/post/{post}", data={"reason": "exam"})
        assert "Past paper" not in unescape(web.get(f"/p/{authors[2]}/community?tab=explore").text)
        assert "Hidden while it is reviewed" in unescape(web.get(f"/p/{ada}/progress").text)
        # The review page exists only at the owner's key.
        assert web.get("/admin/" + "x" * 30).status_code == 404
        queue = unescape(web.get(f"/admin/{key}").text)
        assert "Past paper" in queue and "3 people" in queue and "Exam papers or answers" in queue
        web.post(f"/admin/{key}/review", data={"kind": "post", "target": post, "decision": "keep"})
        assert "Nothing to review" in unescape(web.get(f"/admin/{key}").text)
        assert "Past paper" in unescape(web.get(f"/p/{authors[2]}/community?tab=explore").text)
    with TestClient(
        create_app(Config(store=tmp_path, clock=clock, background=False, sweep_every=None))
    ) as web:
        assert web.get(f"/admin/{key}").status_code == 404  # no key set, no page


def test_a_task_typed_in_one_line_is_read_into_the_form(web, planned):
    sheet = unescape(web.get(f"/p/{planned}/tasks").text)
    assert 'formaction="/p/' in sheet and "data-understand" in sheet and "Fill in" in sheet
    # With the script: JSON, to fill the sheet's fields in place.
    read = web.post(
        f"/p/{planned}/tasks/understand",
        data={"words": "stats report for Friday, about 6 h"},
        headers={"Accept": "application/json"},
    ).json()
    assert read["name"] == "Stats report" and read["due"] == "2026-10-02T23:59" and read["hours"] == 6
    assert read["course"] == "Advanced Statistics" and read["by"] == "rules"
    assert (
        web.post(
            f"/p/{planned}/tasks/understand", data={"words": " "}, headers={"Accept": "application/json"}
        ).status_code
        == 400
    )
    # Without it: a page with the form filled in, which adds the task.
    page = unescape(
        web.post(f"/p/{planned}/tasks/understand", data={"words": "essay due 12/10 (4 hours)"}).text
    )
    assert 'value="Essay"' in page and 'value="2026-10-12T23:59"' in page and 'value="4.0"' in page
    added = web.post(
        f"/p/{planned}/tasks",
        data={"back": "tasks", "name": "Essay", "due": "2026-10-12T23:59", "hours_other": "4"},
        follow_redirects=False,
    )
    assert added.status_code == 303 and "Essay" in unescape(web.get(f"/p/{planned}/tasks").text)


def test_ai_is_off_until_turned_on_and_only_where_set_up(web, planned, monkeypatch):
    from cps import ai

    assert "AI is not set up on this server" in unescape(web.get(f"/p/{planned}/settings").text)

    class Model:
        model = ai.DEFAULT_MODEL

        def ask(self, system, prompt, schema, max_tokens):
            return ai.fake_answer(
                {"name": "Statistics report", "due": "2026-10-09T12:00", "hours": 5, "course": ""}
            )

    monkeypatch.setattr(ai, "from_env", lambda: Model())
    settings = unescape(web.get(f"/p/{planned}/settings").text)
    assert "AI is off." in settings and "Turn AI on" in settings and "Anthropic" in settings
    ask = {"words": "stats report", "headers": {"Accept": "application/json"}}
    assert (
        web.post(
            f"/p/{planned}/tasks/understand", data={"words": ask["words"]}, headers=ask["headers"]
        ).json()["by"]
        == "rules"
    )
    web.post(f"/p/{planned}/ai", data={"on": "1"})
    assert "AI is on." in unescape(web.get(f"/p/{planned}/settings").text)
    read = web.post(
        f"/p/{planned}/tasks/understand", data={"words": ask["words"]}, headers=ask["headers"]
    ).json()
    assert read["by"] == "ai" and read["name"] == "Statistics report" and "Read by AI" in read["note"]
    web.post(f"/p/{planned}/ai", data={"on": "0"})
    assert "AI is off." in unescape(web.get(f"/p/{planned}/settings").text)


def test_appearance_is_automatic_light_or_dark_per_plan(web, planned):
    page = web.get(f"/p/{planned}/settings").text
    assert "data-theme" not in page.split("<head>")[0] and 'content="light dark"' in page
    assert 'name="theme" value="auto" class="btn btn-sm" aria-pressed="true"' in page
    web.post(f"/p/{planned}/appearance", data={"theme": "dark"})
    for path in ("", "/settings", "/progress", "/community", "/tasks"):
        html = web.get(f"/p/{planned}{path}").text
        assert (
            '<html lang="en" data-theme="dark">' in html
            and '<meta name="color-scheme" content="dark">' in html
        )
    web.post(f"/p/{planned}/appearance", data={"theme": "light"})
    assert 'data-theme="light"' in web.get(f"/p/{planned}").text
    bad = web.post(f"/p/{planned}/appearance", data={"theme": "sepia"})
    assert bad.status_code == 400 and "Automatic, Light or Dark" in unescape(bad.text)
    web.post(f"/p/{planned}/appearance", data={"theme": "auto"})
    assert "data-theme" not in web.get(f"/p/{planned}").text
    assert "data-theme" not in web.get("/").text  # without a plan, the device decides


def test_the_trends_page_and_the_trajectory_card(web, planned):
    progress = unescape(web.get(f"/p/{planned}/progress").text)
    assert "Your trajectory" in progress and f"/p/{planned}/trends" in progress
    page = unescape(web.get(f"/p/{planned}/trends").text)
    for words in (
        "Trends",
        "Studied this week",
        "Exam forecast",
        "With your plan",
        "If you stopped today",
        "Hours studied per week",
        "Sessions kept",
        "Study load",
        "See the numbers",
        "<svg",
        "<table",
    ):
        assert words in page, words
    assert 'aria-current="page">12 weeks' in page and "style=" not in page  # nothing the CSP would block
    assert 'aria-current="page">4 weeks' in unescape(web.get(f"/p/{planned}/trends?span=4").text)
    # A self-test's page says what it adds to exam day, in whole fives.
    plan = service.PlanReport.from_dict(_subscription(web, planned).plan)
    test = next(s for s in plan.sessions if s.kind in ("review", "first review"))
    shown = unescape(web.get(f"/s/{planned}/{service.session_id(test)}").text)
    gain = re.search(r"this topic: <b>(\d+)\xa0%</b> without this session, <b>(\d+)\xa0%</b> with it", shown)
    assert gain and int(gain.group(2)) >= int(gain.group(1)) and int(gain.group(1)) % 5 == 0


def test_a_self_test_asks_how_much_was_recalled(web, planned, clock):
    plan = service.PlanReport.from_dict(_subscription(web, planned).plan)
    test = next(s for s in plan.sessions if s.kind in ("review", "first review"))
    task = next(s for s in plan.sessions if s.kind == "task")
    clock.now = datetime.fromisoformat(max(test.end, task.end)) + timedelta(minutes=5)
    shown = unescape(web.get(f"/s/{planned}/{service.session_id(test)}").text)
    assert "How much could you recall, without your notes?" in shown
    for value in ("forgot", "some", "most", "all", "skipped"):
        assert f'name="outcome" value="{value}"' in shown
    assert 'value="struggled"' not in shown  # "hard" is for tasks and practice
    other = unescape(web.get(f"/s/{planned}/{service.session_id(task)}").text)
    assert 'value="struggled"' in other and 'value="most"' not in other
    answered = web.post(
        f"/s/{planned}/{service.session_id(test)}", data={"outcome": "most"}, follow_redirects=False
    )
    assert answered.headers["location"].endswith("?reported=most")
    assert "Recorded: Most" in unescape(web.get(answered.headers["location"]).text)
    assert _subscription(web, planned).outcomes[service.session_id(test)] == "most"
    wrong = web.post(f"/s/{planned}/{service.session_id(task)}", data={"outcome": "all"})
    assert wrong.status_code == 400 and "only a self-test" in unescape(wrong.text)
