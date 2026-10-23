"""
What a student uses, counted only with their yes (DECISIONS.md D96). What must hold:
nothing is counted before a yes or after a no; a count is a part's name and a number
a day, never what a page held; a no deletes what was counted; the student sees their
own counts, the owner only totals; counts go with the plan and after six months.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import cli, service, usage
from cps.store import Store
from cps.web import Config, create_app

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
ADMIN = "the-owners-key-for-these-tests"


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
        store=tmp_path, clock=clock, background=False, sweep_every=None, remind_every=None, admin_token=ADMIN
    )
    with TestClient(create_app(config)) as client:
        client.store = Store(tmp_path)
        client.root = tmp_path
        yield client


def _start(client) -> str:
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = client.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return answer.headers["location"].split("/")[2]


def _rows(client, token):
    with client.store.connection() as db:
        return db.execute("SELECT day, part, n FROM usage WHERE token = ? ORDER BY part", (token,)).fetchall()


@pytest.mark.parametrize(
    ("method", "rest", "part"),
    [
        ("GET", "", "today"),
        ("GET", "/", "today"),
        ("GET", "/week", "calendar"),
        ("GET", "/courses", "courses"),
        ("GET", "/courses/2", "course"),
        ("GET", "/courses/import", "program"),
        ("GET", "/cards/make", "cards_make"),
        ("GET", "/cards/review", "cards"),
        ("GET", "/u/ada", "people"),
        ("GET", "/weekly/2026-09-28", "weekly"),
        ("POST", "/sessions/abc/report", "report"),
        ("POST", "/focus/start", "timer"),
        ("POST", "/cards/c1/answer", "card_answer"),
        ("POST", "/post/p1/kudos", "kudos"),
        ("POST", "/glass", "appearance"),
        # Not parts of the app a student chose to open: never counted.
        ("GET", "/panel", None),
        ("GET", "/manifest.webmanifest", None),
        ("GET", "/m/abcdefghijkl.png", None),
        ("GET", "/usage", None),
        ("POST", "/usage", None),
        ("GET", "/calendar.json", None),
        ("DELETE", "", None),
    ],
)
def test_each_address_is_one_named_part_or_nothing(method, rest, part):
    assert usage.part_of(method, rest) == part


def test_every_part_has_words_and_every_rule_names_a_part():
    assert len(usage.LABELS) == len(usage.PARTS) == 39  # as DECISIONS.md D96 says
    assert {part for _, _, part in usage._RULES} <= set(usage.LABELS)


def test_nothing_is_counted_before_a_yes_and_today_asks_once(web):
    token = _start(web)
    page = web.get(f"/p/{token}").text
    assert "May the app count which of its parts you use?" in page
    web.get(f"/p/{token}/focus")
    assert _rows(web, token) == []
    with pytest.raises(ValueError):
        service.count_use(web.root, token, "not a part")


def test_a_yes_counts_each_part_by_name_and_day(web, clock):
    token = _start(web)
    answer = web.post(f"/p/{token}/usage", data={"yes": "1", "back": "today"}, follow_redirects=False)
    assert answer.status_code == 303 and "saved=usage-yes" in answer.headers["location"]
    page = web.get(answer.headers["location"]).text
    assert "What you use is counted from now on" in page
    assert "May the app count" not in page  # asked once
    web.get(f"/p/{token}/focus")
    web.get(f"/p/{token}/focus")
    web.get(f"/p/{token}/panel")  # the panel fetched again: not a part
    web.get(f"/p/{token}/nothing-here")  # a 404: not counted
    clock.now = MORNING + timedelta(days=1)
    web.get(f"/p/{token}/focus")
    assert _rows(web, token) == [
        ("2026-09-29", "focus", 2),
        ("2026-09-30", "focus", 1),
        ("2026-09-29", "today", 1),
    ]
    # Only names and numbers: the rows hold nothing from the plan.
    with web.store.connection() as db:
        assert [c[1] for c in db.execute("PRAGMA table_info(usage)")] == ["token", "day", "part", "n"]


def test_the_student_sees_their_own_counts_and_what_they_never_opened(web):
    token = _start(web)
    web.post(f"/p/{token}/usage", data={"yes": "1"})
    web.get(f"/p/{token}/progress")
    page = web.get(f"/p/{token}/usage").text
    assert "Progress" in page and "1 day" in page and "1 time" in page
    assert "Not opened since then" in page and "Flashcards" in page
    settings = web.get(f"/p/{token}/settings").text
    assert "On: the app counts" in settings and f"/p/{token}/usage" in settings


def test_a_no_counts_nothing_and_turning_off_deletes_what_was_counted(web):
    token = _start(web)
    web.post(f"/p/{token}/usage", data={"yes": "0", "back": "today"})
    web.get(f"/p/{token}/focus")
    assert _rows(web, token) == []
    assert "May the app count" not in web.get(f"/p/{token}").text
    web.post(f"/p/{token}/usage", data={"yes": "1"})
    web.get(f"/p/{token}/focus")
    assert _rows(web, token)
    answer = web.post(f"/p/{token}/usage", data={"yes": "0"}, follow_redirects=False)
    assert answer.headers["location"].endswith("/settings?saved=usage#usage")
    assert _rows(web, token) == []
    assert "Off: nothing is counted." in web.get(f"/p/{token}/settings").text


def test_the_owner_reads_totals_of_those_who_said_yes_never_a_plan(web):
    ana, ben, cleo = _start(web), _start(web), _start(web)
    for token in (ana, ben):
        web.post(f"/p/{token}/usage", data={"yes": "1"})
        web.get(f"/p/{token}/focus")
    web.get(f"/p/{ana}/focus")
    web.post(f"/p/{cleo}/usage", data={"yes": "0"})
    report = service.usage_report(web.root, MORNING)
    assert (report["yes"], report["no"]) == (2, 1)
    focus = next(p for p in report["parts"] if p["part"] == "focus")
    assert (focus["students"], focus["days"], focus["times"], focus["last_7"]) == (2, 2, 3, 2)
    assert "Flashcards" in report["unused"]
    assert web.get("/admin/not-the-key/usage").status_code == 404
    page = web.get(f"/admin/{ADMIN}/usage").text
    assert "2 said yes to counting, 1 said no." in page and "Focus" in page
    assert not any(token in page for token in (ana, ben, cleo))


def test_counts_go_with_the_plan_and_after_six_months(web, clock):
    token = _start(web)
    web.post(f"/p/{token}/usage", data={"yes": "1"})
    web.get(f"/p/{token}/tips")
    copy = web.get(f"/p/{token}/export").json()
    assert copy["usage_answer"][0]["answer"] == "yes"
    # The yes went back to Settings, a page opened like any other.
    assert [(r["part"], r["n"]) for r in copy["usage"]] == [("settings", 1), ("tips", 1)]
    service.sweep_subscriptions(web.root, MORNING + timedelta(days=180))
    assert _rows(web, token)  # six months to the day: still kept
    service.sweep_subscriptions(web.root, MORNING + timedelta(days=181))
    assert _rows(web, token) == []
    web.get(f"/p/{token}/tips")
    web.post(f"/p/{token}/delete")
    with web.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM usage").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM usage_consent").fetchone() == (0,)


def test_cps_metrics_prints_what_is_used(web, capsys):
    token = _start(web)
    web.post(f"/p/{token}/usage", data={"yes": "1"})
    web.get(f"/p/{token}/coach")
    assert cli.main(["metrics", "--db", str(web.root)]) == 0
    out = capsys.readouterr().out
    assert "1 said yes to counting, 0 said no" in out and "The coach" in out and "nobody used" in out
