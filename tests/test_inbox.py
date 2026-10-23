"""
Before friends try it (DECISIONS.md D43): a message to the owner from any page, a
copy of everything a plan keeps, the terms in draft, and an error log that names no
plan.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import inbox, service
from cps.store import _EXPORT, Store

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SEMESTER = EXAMPLES / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-ab"


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
        store=tmp_path,
        clock=clock,
        background=False,
        sweep_every=None,
        contact="owner@example.org",
        admin_token="owner-secret-key-0123456789",
    )
    with TestClient(create_app(config), raise_server_exceptions=False) as client:
        client.path = tmp_path
        yield client


def _start(web, lang: str = "en") -> str:
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = web.post(
        "/start", data={"tz": "Europe/Paris", "lang": lang}, files=files, follow_redirects=False
    )
    return answer.headers["location"].split("/")[2]


def _all_text(path: Path, table: str) -> str:
    with closing(sqlite3.connect(Store(path).path)) as db:
        return json.dumps(db.execute(f"SELECT * FROM {table}").fetchall())


# --------------------------------------------------------------------------- #
# The error log
# --------------------------------------------------------------------------- #


def test_a_plan_s_address_and_an_e_mail_are_cut_out_of_what_is_kept():
    text = f"KeyError at /p/{TOKEN}/week for {TOKEN}, from sam@example.org"
    kept = inbox.scrub(text)
    assert TOKEN not in kept and "sam@example.org" not in kept
    assert kept.startswith("KeyError at /p/…/week")
    assert len(inbox.scrub("x" * 1000)) == 300


def test_the_same_error_is_one_row_counted_and_old_ones_go(tmp_path):
    store = Store(tmp_path)
    for minutes in (0, 5, 9):
        inbox.record_error(
            store,
            source="server",
            route=f"/p/{TOKEN}",
            kind="KeyError",
            message="'start'",
            now=MORNING + timedelta(minutes=minutes),
        )
    inbox.record_error(store, source="browser", route="/p/…/week", kind="TypeError", message="x", now=MORNING)
    rows = inbox.errors(store)
    assert [(r["kind"], r["count"]) for r in rows] == [("KeyError", 3), ("TypeError", 1)]
    assert rows[0]["route"] == "/p/…" and rows[0]["last"] > rows[0]["first"]
    assert inbox.errors_since(store, MORNING) == 4
    inbox.record_error(
        store, source="server", route="/", kind="Late", message="y", now=MORNING + timedelta(days=31)
    )
    assert [r["kind"] for r in inbox.errors(store)] == ["Late"]


def test_an_unexpected_server_error_is_logged_and_the_student_gets_a_page(web, monkeypatch):
    token = _start(web)

    def broken(*args, **kwargs):
        raise RuntimeError(f"no luck with /p/{token}")

    monkeypatch.setattr(service, "today_view", broken)
    answer = web.get(f"/p/{token}")
    assert answer.status_code == 500
    page = unescape(answer.text)
    assert "This is a mistake in the app, not yours." in page and f'href="/p/{token}"' in page
    (row,) = inbox.errors(Store(web.path))
    assert (row["source"], row["route"], row["kind"]) == ("server", "/p/{token}", "RuntimeError")
    assert row["message"] == "no luck with /p/…" and "app.py:" in row["detail"]
    assert token not in _all_text(web.path, "errors")


def test_a_browser_error_is_logged_without_the_address_and_not_endlessly(web):
    token = _start(web)
    report = {
        "kind": "TypeError",
        "message": "box is undefined",
        "file": "/static/calendar.js",
        "line": 412,
        "column": 9,
        "stack": "box@/static/calendar.js:412:9",
        "page": f"/p/{token}/week",
    }
    assert web.post("/errors", json=report).status_code == 204
    (row,) = inbox.errors(Store(web.path))
    assert (row["source"], row["route"], row["kind"]) == ("browser", "/p/…/week", "TypeError")
    assert "/static/calendar.js:412:9" in row["detail"]
    assert token not in _all_text(web.path, "errors")
    assert (
        web.post("/errors", content=b"not json", headers={"content-type": "application/json"}).status_code
        == 400
    )
    answers = [web.post("/errors", json=report).status_code for _ in range(12)]
    assert 429 in answers  # ten a minute per address
    assert web.post("/errors", json=report, headers={"sec-fetch-site": "cross-site"}).status_code == 403


def test_every_page_hears_its_scripts_errors(web):
    token = _start(web)
    for path in ("/", f"/p/{token}", "/privacy"):
        page = web.get(path).text
        first = re.search(r'<script type="module" src="([^"]+)"', page)
        assert first and first.group(1) == "/static/errors.js", path
    script = web.get("/static/errors.js").text
    assert 'sendBeacon("/errors"' in script and "location.pathname" in script


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #


def test_feedback_from_any_page_reaches_the_owner_without_the_address(web):
    token = _start(web)
    today = web.get(f"/p/{token}").text
    assert f'href="/p/{token}/feedback?from=/p/{token}"' in today  # the Today panel
    progress = web.get(f"/p/{token}/progress").text  # and the footer of every other page
    assert f'href="/p/{token}/feedback?from=/p/{token}/progress"' in progress.replace("%2F", "/")
    form = web.get(f"/p/{token}/feedback?from=/p/{token}/week").text
    assert f'name="from" value="/p/{token}/week"' in form
    sent = web.post(
        f"/p/{token}/feedback",
        data={"kind": "confusing", "body": "  The week view hides Sunday.  ", "from": f"/p/{token}/week"},
    )
    assert sent.status_code == 200 and "Thank you" in sent.text
    (message,) = inbox.feedback(Store(web.path))
    assert (message["kind"], message["body"], message["page"], message["lang"]) == (
        "confusing",
        "The week view hides Sunday.",
        "/p/…/week",
        "en",
    )
    owner = unescape(web.get("/admin/owner-secret-key-0123456789/inbox").text)
    assert "The week view hides Sunday." in owner and "Something is confusing" in owner
    assert token not in owner
    assert web.get("/admin/wrong-key/inbox").status_code == 404
    web.post(f"/admin/owner-secret-key-0123456789/feedback/{message['id']}/seen")
    assert inbox.unseen(Store(web.path)) == 0


def test_a_message_must_say_something_and_what_about(web):
    token = _start(web)
    empty = web.post(f"/p/{token}/feedback", data={"kind": "idea", "body": " "})
    assert empty.status_code == 400 and "write a few words" in empty.text
    unsure = web.post(f"/p/{token}/feedback", data={"kind": "rant", "body": "Kept as typed"})
    assert unsure.status_code == 400 and "Kept as typed" in unsure.text
    assert web.post(f"/p/{token}/feedback", data={"kind": "idea", "body": "x" * 2001}).status_code == 400
    assert inbox.feedback(Store(web.path)) == []


def test_feedback_goes_with_the_plan(web):
    token = _start(web)
    service.send_feedback(web.path, token, kind="like", body="Lovely calendar", now=MORNING)
    assert Store(web.path).delete(token)
    assert inbox.feedback(Store(web.path)) == []


# --------------------------------------------------------------------------- #
# A copy of one's data
# --------------------------------------------------------------------------- #

# Every table, and where a plan's rows of it are in the copy: under its `_EXPORT`
# name, in "plan" (the document), or left out with the reason.
TABLES = {
    "subscriptions": "plan",
    "events": "events",
    "profiles": "profile",
    "posts": "posts",
    "photos": "photos",
    "follows": "follows",
    "kudos": "kudos",
    "comments": "comments",
    "reports": "reports",
    "ai_usage": "ai_usage",
    "blocks": "blocks",
    "study_groups": "groups",
    "group_members": "groups",
    "cards": "cards",
    "card_reviews": "card_reviews",
    "card_fits": "card_fit",
    "passkeys": "passkeys",
    "recovery_codes": "recovery_code",
    "push_devices": "reminder_devices",
    "feedback": "feedback",
    "course_topics": "shared_topics",
    "accounts": "account",
    "sessions": "signed_in",
    "usage_consent": "usage_answer",
    "usage": "usage",
    "mail_links": None,  # a link's random value, used once and gone within a week
    "challenges": None,  # a ceremony's random value, gone in minutes
    "push_sent": None,  # which sessions were reminded, for three days
    "server_keys": None,  # the server's own key
    "errors": None,  # names no plan
}


def test_every_table_is_in_the_copy_or_left_out_on_purpose(tmp_path):
    with closing(sqlite3.connect(Store(tmp_path).path)) as db:
        tables = {
            name
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if not name.startswith("sqlite_")
        }
    assert tables <= set(TABLES), (
        f"a new table: add it to the copy (store._EXPORT) or say why not: {tables - set(TABLES)}"
    )
    assert {v for v in TABLES.values() if v} - {"plan", "photos"} == set(_EXPORT)


def test_the_copy_holds_the_plan_and_everything_kept_under_it(web):
    token = _start(web)
    other = _start(web)
    service.save_profile(
        web.path, token, {"handle": "sam_c", "university": "University of Bologna", "old_enough": "1"}
    )
    service.save_profile(
        web.path, other, {"handle": "lea_m", "university": "University of Bologna", "old_enough": "1"}
    )
    service.follow(web.path, token, "lea_m")
    subscription = service.load_subscription(web.path, token)
    assert subscription is not None
    service.add_cards(
        web.path, subscription, {"course": "Algebra 3", "front": "Rank?", "back": "dim of image"}, MORNING
    )
    service.send_feedback(web.path, token, kind="idea", body="Dark calendar please", now=MORNING)
    settings = web.get(f"/p/{token}/settings").text
    assert f'href="/p/{token}/export" download' in settings
    answer = web.get(f"/p/{token}/export")
    assert answer.status_code == 200
    assert answer.headers["content-disposition"] == 'attachment; filename="study-plan-2026-09-29.json"'
    data = answer.json()
    assert data["plan"]["token"] == token and data["plan"]["ics_text"]
    assert [e["kind"] for e in data["events"]][:1] == ["start"]
    assert data["profile"][0]["handle"] == "sam_c"
    assert [(f["direction"], f["handle"]) for f in data["follows"]] == [("you follow", "lea_m")]
    assert [c["front"] for c in data["cards"]] == ["Rank?"]
    assert [f["body"] for f in data["feedback"]] == ["Dark calendar please"]
    assert other not in answer.text  # someone else appears by their handle only
    assert web.get("/p/" + "x" * 32 + "/export").status_code == 404


# --------------------------------------------------------------------------- #
# The terms, in draft
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("lang", "title", "draft"),
    [
        ("en", "Terms of use", "not yet read by a lawyer"),
        ("fr", "Conditions d'utilisation", "pas encore relu par un juriste"),
        ("it", "Condizioni d'uso", "non ancora letta da un avvocato"),
    ],
)
def test_the_terms_say_they_are_a_draft_in_each_language(web, lang, title, draft):
    page = unescape(web.get(f"/terms?lang={lang}").text)
    assert title in page and draft in page and "owner@example.org" in page
    assert 'href="/terms' in unescape(web.get(f"/?lang={lang}").text)
