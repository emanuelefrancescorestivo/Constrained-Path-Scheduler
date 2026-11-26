"""
A learning platform's link that is not a calendar says what it is and what to copy instead
(AUDIT 138). The owner pasted a Moodle address and met one sentence for every case ("the
link does not lead to a calendar (.ics)") with no word of where the right link is.

What must hold: a Moodle page's address (the Export calendar page itself, a course, the
dashboard) is told apart before anything is fetched, in Moodle's own words in the student's
language, with the export page of their own Moodle; a link that answers with a sign-in page,
or with a short refusal such as Moodle's "Invalid authentication", says so; anything else
keeps the general sentence. The export link itself, and any other calendar's, passes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cps import i18n, service, sources

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)
SITE = "https://moodle.univ.example"
EXPORT = (
    f"{SITE}/calendar/export_execute.php?userid=7&authtoken=abc&preset_what=all&preset_time=recentupcoming"
)


def test_a_moodle_page_is_told_apart_with_the_way_to_its_calendar():
    # The Export calendar page itself, its address bar copied after the button.
    said = service.moodle_page_refusal(f"{SITE}/calendar/export.php?course=1")
    assert said is not None and "«Export calendar»" in said and "«Get calendar URL»" in said
    assert "below the button" in said and "export_execute.php" in said
    # Any other page of it: its own export page named.
    for page in (
        "/course/view.php?id=12",
        "/my/",
        "/my/courses.php",
        "/calendar/view.php?view=month",
        "/mod/resource/view.php?id=3",
    ):
        said = service.moodle_page_refusal(SITE + page)
        assert said is not None and f"{SITE}/calendar/export.php" in said and "«All events»" in said, page
    # In a subfolder too, and in the student's language, Moodle's own words.
    with i18n.use("fr"):
        said = service.moodle_page_refusal("https://ecampus.example.fr/moodle/course/view.php?id=4")
    assert "https://ecampus.example.fr/moodle/calendar/export.php" in said and "Tous les événements" in said
    assert "URL du calendrier" in said
    # The calendar's own link, and other calendars, pass.
    for fine in (
        EXPORT,
        "https://ade.univ.example/jsp/custom/modules/plannings/anonymous_cal.jsp?resources=1",
        "webcal://p01-calendars.icloud.com/published/2/x",
    ):
        assert service.moodle_page_refusal(fine) is None, fine


def test_an_answer_that_is_not_a_calendar_says_what_it_was():
    page = b"<!DOCTYPE html><html><body><form><input type='password' name='password'></form></body></html>"
    assert "sign-in page" in sources.not_a_calendar(f"{SITE}/calendar/export_execute.php", page)
    sso = b"<html><body>Redirecting</body></html>"
    assert "sign-in page" in sources.not_a_calendar("https://idp.univ.example/cas/login?service=x", sso)
    assert "sign-in page" in sources.not_a_calendar(f"{SITE}/login/index.php", sso)
    refused = sources.not_a_calendar(EXPORT, b"Invalid authentication")
    assert "«Invalid authentication»" in refused and "get a new one" in refused
    assert "«no export»" in sources.not_a_calendar(EXPORT, b"no export")
    other = sources.not_a_calendar("https://example.org/page", b"<html><body><h1>Welcome</h1></body></html>")
    assert "does not lead to a calendar" in other


@pytest.fixture
def subscription() -> service.Subscription:
    ics = (EXAMPLES / "sample-semester.ics").read_bytes()
    sub = service.new_subscription(
        subjects=[], start=date(2026, 9, 29), tz="Europe/Paris", ics=ics, now=NOW, require_plan=False
    )
    return replace(sub, options={**sub.options, "deadlines_url": None})


def test_saving_or_previewing_a_moodle_page_fetches_nothing(subscription):
    asked = []

    def fetch(url):
        asked.append(url)
        return b""

    with pytest.raises(service.InvalidInput, match="not the calendar's link"):
        service.revise_subscription(
            subscription, deadlines_url=f"{SITE}/calendar/export.php", now=NOW, fetch=fetch
        )
    preview = service.deadlines_preview(f"{SITE}/course/view.php?id=9", "Europe/Paris", now=NOW, fetch=fetch)
    assert preview["count"] == 0 and f"{SITE}/calendar/export.php" in preview["error"]
    assert asked == []
    # The export link itself is read.
    moodle = (EXAMPLES / "sample-moodle.ics").read_bytes()
    revised = service.revise_subscription(
        subscription, deadlines_url=EXPORT, now=NOW, fetch=lambda url: moodle
    )
    assert len(revised.options["tasks"]) == 3


def test_the_settings_say_which_link_to_copy_in_moodle_s_words(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    ics = (EXAMPLES / "sample-semester.ics").read_bytes()
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=ics,
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    sub = service.set_language(service.finish_first_run(sub), "fr", now=NOW)
    service.save_subscription(tmp_path, sub)
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as web:
        page = web.get(f"/p/{sub.token}/settings/exams").text
    assert "<b>URL du calendrier</b>" in page and "<b>Tous les événements</b>" in page
