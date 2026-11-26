"""
Moodle without copying and pasting, and fast (DECISIONS.md D211). The owner, after D209: "it's
taking ages to plan this", "it takes time to copy and paste everything", "all the links from
all courses".

What must hold: the key finds the student's courses on Moodle (`core_enrol_get_users_courses`)
and links each plan course to the one whose name holds its words, the closest when several do,
none when two are as close, never over a course already linked; the rest are chosen from the
list, not pasted; the key may come first, with the Moodle's address. A course is read in two
goes: a plan from its sections and files' names at once, the files still to read counted; then
the files, several at a time, the progress kept on the plan for every page to show, a refusal
kept in its place, a reading that stopped moving shown as stopped; the pages follow it, and
"Plan all my courses" plans every linked course in one tap. A PDF is read for its first pages
only when quick.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from test_program import pdf

from cps import notefiles, pdftext, service, showcase, studyplan
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # the sample semester's second teaching week
SITE = showcase.DEMO_MOODLE
KEY = showcase.DEMO_KEY


def _plan(root: Path, courses: tuple[str, ...] = ("Algebra 3", "Analysis 3")) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec(c, None, familiarity=3) for c in courses],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    sub = service.finish_first_run(sub)
    service.save_subscription(root, sub)
    return sub


def _app(root: Path, **extra) -> TestClient:
    config = Config(
        store=root,
        clock=lambda: NOW,
        background=False,
        sweep_every=None,
        remind_every=None,
        moodle_call=showcase.demo_moodle_call,
        fetch_file=showcase.demo_fetch_file,
        **extra,
    )
    return TestClient(create_app(config))


def test_plan_courses_are_matched_to_moodle_s_by_the_words_of_their_names():
    moodle = [
        (1, "Algebra 1 · 2025–26 ALG1"),
        (3, "Algebra 3 · 2026–27 ALG3"),
        (4, "Algebra · 2026–27"),
        (5, "Analisi Matematica II (2026/27)"),
        (6, "Statistics A"),
        (7, "Statistics B"),
        (8, "Introduction à la Probabilité"),
    ]
    plan = ["Algebra", "Algebra 3", "Analisi matematica 2", "Statistics", "Introduction Probabilité"]
    plan.append("Optics")
    found = studyplan.match_courses(plan, moodle)
    # The most specific first: "Algebra 3" takes its own, "Algebra" the one left closest.
    assert found["Algebra 3"] == 3 and found["Algebra"] == 4
    assert found["Analisi matematica 2"] == 5  # a Roman numeral is its number, case and accents folded
    assert found["Introduction Probabilité"] == 8  # small words left out
    assert "Statistics" not in found  # two as close: the student chooses
    assert "Optics" not in found  # none
    assert studyplan.name_words("Analisi Matematica II") == {"analisi", "matematica", "2"}


def test_the_key_finds_the_courses_and_links_the_ones_with_the_same_name(tmp_path):
    sub = _plan(tmp_path)
    algebra = service.topic_courses(sub).index("Algebra 3")
    analysis = service.topic_courses(sub).index("Analysis 3")
    # No Moodle known and none typed: the key cannot be checked.
    with pytest.raises(service.InvalidInput, match="Type your Moodle's address"):
        service.connect_moodle_key(sub, KEY, call=showcase.demo_moodle_call, now=NOW)
    # A course linked by hand to another number stays as it is.
    sub = service.link_moodle_course(sub, algebra, {"address": f"{SITE}/course/view.php?id=9999"})
    sub = service.connect_moodle_key(
        sub, KEY, call=showcase.demo_moodle_call, now=NOW, site_text="https://moodle.example.edu/my/"
    )
    kept = sub.options["moodle_key"]
    assert kept["user"] == showcase.DEMO_USER and kept["lists"]
    listed = service.moodle_list(sub)
    assert len(listed) == 6 and {"id", "name", "short"} == set(listed[0])
    linked = sub.options["moodle_courses"]
    assert linked[service.course_key("Algebra 3")]["id"] == 9999  # not taken over
    assert linked[service.course_key("Analysis 3")] == {"site": SITE, "id": 4023, "found": True}
    # Another chosen from the list, not pasted; one not in the list is refused.
    sub = service.link_moodle_course(sub, algebra, {"moodle_id": "4021"})
    assert sub.options["moodle_courses"][service.course_key("Algebra 3")] == {"site": SITE, "id": 4021}
    with pytest.raises(service.InvalidInput):
        service.link_moodle_course(sub, analysis, {"moodle_id": "12345"})


def test_hidden_courses_are_left_out_and_a_moodle_without_the_list_still_takes_the_key(tmp_path):
    sub = _plan(tmp_path)
    answers = {
        "core_webservice_get_site_info": {
            "sitename": "U",
            "userid": 3,
            "functions": [{"name": "core_course_get_contents"}, {"name": "core_enrol_get_users_courses"}],
        },
        "core_enrol_get_users_courses": [
            {"id": 1, "fullname": "  Algebra   3 ", "shortname": "A3", "visible": 1},
            {"id": 2, "fullname": "Analysis 3", "shortname": "AN3", "visible": 0},
            {"id": "x", "fullname": "Broken"},
            {"id": 4, "fullname": ""},
        ],
    }
    asked = []

    def call(site, key, function, params=None):
        asked.append((function, params))
        return answers[function]

    found = service.connect_moodle_key(sub, KEY, call=call, now=NOW, site_text="moodle.u.example")
    assert asked[-1] == ("core_enrol_get_users_courses", {"userid": 3})
    assert service.moodle_list(found) == [{"id": 1, "name": "Algebra 3", "short": "A3"}]
    assert service.course_key("Analysis 3") not in found.options["moodle_courses"]  # hidden on Moodle
    # A Moodle whose mobile service does not list courses: the key is kept, the list is not read.
    answers["core_webservice_get_site_info"]["functions"] = [{"name": "core_course_get_contents"}]
    asked.clear()
    kept = service.connect_moodle_key(sub, KEY, call=call, now=NOW, site_text="moodle.u.example")
    assert service.moodle_key_view(kept)["linked"] and not kept.options["moodle_key"]["lists"]
    assert [f for f, _p in asked] == ["core_webservice_get_site_info"] and not service.moodle_list(kept)


def test_a_course_is_planned_from_its_files_names_at_once_then_from_their_text(tmp_path, monkeypatch):
    sub = _plan(tmp_path)
    sub = service.connect_moodle_key(sub, KEY, call=showcase.demo_moodle_call, now=NOW, site_text=SITE)
    service.save_subscription(tmp_path, sub)
    algebra = service.topic_courses(sub).index("Algebra 3")
    fetched = []

    def fetch(url, *, limit):
        fetched.append(url)
        return showcase.demo_fetch_file(url, limit=limit)

    # The first go: one call to Moodle, no file fetched, the plan from the names.
    summary = service.plan_moodle_course(
        tmp_path, sub.token, algebra, call=showcase.demo_moodle_call, now=NOW
    )
    assert summary["by"] == "names" and summary["pending"] == 24 and not fetched
    sub = service.load_subscription(tmp_path, sub.token)
    plan = service.study_plan_view(sub, algebra, NOW, store=tmp_path)
    assert plan["by_names"] and plan["pending"] == 24 and plan["weeks"]
    assert plan["weeks"][0]["topics"][0]["title"].startswith("Lecture 1 · ")
    assert plan["reading"] == {"error": "", "done": 0, "total": 24, "stopped": False, "percent": 0}
    readings = service.moodle_readings(tmp_path, sub, NOW)
    assert readings["reading"] and readings["courses"][0]["said"] == "0 of 24 files read"
    # The second go: the files, a few at a time, the progress kept on the plan as they come.
    # Each progress written, as written: every three files read, then gone once the plan is made.
    # (Recorded where it is kept, not seen from a download: the made-up files come at once, and
    # a download may finish before any progress is written, as it did on a CI runner.)
    kept = []
    keep = service._set_reading

    def recorded(store, token, key, state):
        kept.append(state and (state.get("done"), state.get("total")))
        keep(store, token, key, state)

    monkeypatch.setattr(service, "_set_reading", recorded)
    done = service.read_moodle_files(
        tmp_path, sub.token, algebra, call=showcase.demo_moodle_call, fetch=fetch, clock=lambda: NOW
    )
    assert done["by"] == "rules" and done["files"] == 24 and done["pending"] == 0
    assert len(fetched) == 24
    assert kept == [(n, 24) for n in range(3, 25, 3)] + [None]
    sub = service.load_subscription(tmp_path, sub.token)
    plan = service.study_plan_view(sub, algebra, NOW, store=tmp_path)
    assert not plan["by_names"] and plan["files"] == 24 and plan["reading"] is None
    assert service.moodle_readings(tmp_path, sub, NOW) == {"courses": [], "reading": False}
    assert len(service.course_texts(tmp_path, sub, "Algebra 3")) == 24


def test_an_edit_made_while_reading_stays_and_a_refusal_or_a_stop_is_said(tmp_path):
    sub = _plan(tmp_path)
    sub = service.connect_moodle_key(sub, KEY, call=showcase.demo_moodle_call, now=NOW, site_text=SITE)
    service.save_subscription(tmp_path, sub)
    algebra = service.topic_courses(sub).index("Algebra 3")
    service.plan_moodle_course(tmp_path, sub.token, algebra, call=showcase.demo_moodle_call, now=NOW)

    def fetch(url, *, limit):  # the student changes their language while the files are read
        if url.endswith("lecture-01.md?token=" + KEY):
            service.update_subscription(
                tmp_path, sub.token, lambda current: service.set_language(current, "it", now=NOW)
            )
        return showcase.demo_fetch_file(url, limit=limit)

    service.read_moodle_files(
        tmp_path, sub.token, algebra, call=showcase.demo_moodle_call, fetch=fetch, clock=lambda: NOW
    )
    assert service.language_of(service.load_subscription(tmp_path, sub.token)) == "it"

    # Moodle says no on the second go: the refusal is kept where the progress was.
    def refuse(site, key, function, params=None):
        raise service.MoodleError("Invalid token", "invalidtoken")

    assert service.read_moodle_files(tmp_path, sub.token, algebra, call=refuse, clock=lambda: NOW) is None
    sub = service.load_subscription(tmp_path, sub.token)
    state = service.moodle_reading(tmp_path, sub, "Algebra 3", NOW)
    assert state["error"].startswith("Moodle did not accept this key")
    assert not service.moodle_readings(tmp_path, sub, NOW)["reading"]
    # A reading that has not moved for ten minutes (the server restarted) is stopped.
    stale = (NOW - service.MOODLE_STALE - timedelta(seconds=1)).isoformat()
    service._set_reading(
        tmp_path, sub.token, service.course_key("Algebra 3"), {"done": 3, "total": 24, "at": stale}
    )
    assert service.moodle_reading(tmp_path, sub, "Algebra 3", NOW)["stopped"]
    with service.use("en"):
        said = service.moodle_readings(tmp_path, sub, NOW)["courses"][0]["said"]
    assert said == "The reading stopped: read it again."


def test_the_pages_show_the_reading_and_plan_every_course_in_one_tap(tmp_path):
    sub = _plan(tmp_path)
    token = sub.token
    algebra = service.topic_courses(sub).index("Algebra 3")
    analysis = service.topic_courses(sub).index("Analysis 3")
    with _app(tmp_path) as web:
        web.post(f"/p/{token}/moodle/key", data={"key": KEY, "site": SITE, "back": "guide"})
        # A course page reading its files: the plan from the names, the meter and its words.
        service.plan_moodle_course(tmp_path, token, analysis, call=showcase.demo_moodle_call, now=NOW)
        page = unescape(web.get(f"/p/{token}/courses/{analysis}").text)
        assert f'data-reading="/p/{token}/moodle/reading"' in page
        # Six weeks of lecture notes, five exercise sheets and a past paper.
        assert "0 of 12 files read" in page and "from the names of 12 files on Moodle" in page
        assert "while their text is read" in page
        state = web.get(f"/p/{token}/moodle/reading").json()
        assert state["reading"] and state["courses"][0]["index"] == analysis
        assert web.get("/p/nope/moodle/reading").status_code == 404
        guide = unescape(web.get(f"/p/{token}/guide/moodle").text)
        assert 'data-reading-course="' in guide and "See how far it is" in guide
        # "Plan all my courses": the one being read is left to its reading, the other planned.
        web.post(f"/p/{token}/moodle/read-all")
        sub = service.load_subscription(tmp_path, token)
        assert (
            service.study_plan_view(sub, algebra, NOW, store=tmp_path)["files"] == 24
        )  # its weeks and a past paper
        assert service.study_plan_view(sub, analysis, NOW, store=tmp_path)["by_names"]
        # A course not linked is chosen from the list on its page.
        web.post(f"/p/{token}/courses/{analysis}/moodle", data={"action": "unlink"})
        page = unescape(web.get(f"/p/{token}/courses/{analysis}").text)
        assert 'name="moodle_id"' in page and "Analysis 3 · 2026–27" in page
        chosen = web.post(
            f"/p/{token}/courses/{analysis}/moodle",
            data={"moodle_id": "4023", "back": "guide"},
            follow_redirects=False,
        )
        assert chosen.headers["location"] == f"/p/{token}/guide/moodle#step-link"
        found = web.post(f"/p/{token}/moodle/find", follow_redirects=False)
        assert found.headers["location"] == f"/p/{token}/guide/moodle#step-link"
        # Moodle's own words for its pages, in each language: the dictionary's entry, never its
        # method (`kw.keys` in a template is dict.keys, which a browser check caught).
        for lang, keys in (
            ("en", "Security keys"),
            ("fr", "Clefs de sécurité"),
            ("it", "Chiave di sicurezza"),
        ):
            service.update_subscription(
                tmp_path, token, lambda c, lang=lang: service.set_language(c, lang, now=NOW)
            )
            for path in ("guide/moodle", "connections", f"courses/{analysis}"):
                page = unescape(web.get(f"/p/{token}/{path}").text)
                assert "built-in method" not in page, path
            assert keys in unescape(web.get(f"/p/{token}/guide/moodle").text)


def test_a_quick_reading_of_a_pdf_stops_at_its_first_pages():
    pytest.importorskip("pypdf")
    pages = [[f"Page {p}, line {n}: a sentence of the course's notes." for n in range(40)] for p in range(30)]
    data = pdf(pages)
    whole = notefiles.text("notes.pdf", data)
    quick = notefiles.text("notes.pdf", data, quick=True)
    assert len(whole.text) > notefiles.QUICK_CHARS >= len(quick.text) > notefiles.QUICK_CHARS - 2000
    # Its first pages only, read as plain text (the layout's reading, for mathematics, is for cards).
    assert quick.text.startswith("Page 0, line 0") and "Page 29" not in quick.text and "Page 29" in whole.text
    assert pdftext.text(data, most=100).read == 1  # pages after the limit are not even read
