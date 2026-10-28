"""
Tips with their sources (DECISIONS.md D62): every tip rests on sources that were
read at the source, says how strong its evidence is and what it does not show, and
states no number its sources do not; the tips page reads in each language without
its scripts, Today shows one tip a day, and opening the page is counted for the
pilot's measures.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import i18n, service, tips, weekly
from cps.locales.fr import MESSAGES as FRENCH
from cps.locales.it import MESSAGES as ITALIAN

ROOT = Path(__file__).resolve().parent.parent
BIBLIOGRAPHY = ROOT / "docs" / "REFERENCES.md"
SEMESTER = ROOT / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


def entries() -> dict[str, dict[str, str]]:
    """docs/REFERENCES.md, as `tests/test_references.py` reads it."""
    text = BIBLIOGRAPHY.read_text(encoding="utf-8")
    out = {}
    for block in re.split(r"^### ", text, flags=re.M)[1:]:
        key, _, body = block.partition("\n")
        out[key.strip()] = dict(re.findall(r"^- ([a-z ]+): (.+)$", body, flags=re.M))
    return out


def test_every_tip_has_advice_evidence_limits_and_sources():
    keys = [t.key for t in tips.TIPS]
    assert len(keys) == len(set(keys)) >= 10
    for tip in tips.TIPS:
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", tip.key), tip.key  # an anchor on the page
        assert tip.topic in tips.TOPICS and tip.strength in tips.STRENGTHS, tip.key
        assert tip.advice.endswith(".") and tip.why.strip() and tip.limits.strip(), tip.key
        assert tip.sources and all(key in tips.SOURCES for key in tip.sources), tip.key
    assert {t.topic for t in tips.TIPS} == set(tips.TOPICS)  # no empty topic
    cited = {key for t in (*tips.TIPS, *weekly.NOTES) for key in t.sources}
    assert cited == set(tips.SOURCES)  # no source unused, by a tip or a weekly note (D64)
    assert {t.strength for t in tips.TIPS} == set(tips.STRENGTHS)  # every level is used


def test_every_source_is_a_checked_entry_read_on_the_day_shown():
    """A tip may cite only what was read at the source, never from memory or from a
    search engine's summary (docs/REFERENCES.md: "checked")."""
    found = entries()
    for key, source in tips.SOURCES.items():
        entry = found[key]
        assert entry["status"] == "checked", key
        assert source.checked.isoformat() in entry["checked against"], key
        assert entry["year"] in source.cite, key
        surname = entry["authors"].split(";")[0].split()[-1]
        assert surname in source.cite or key.startswith("efsa"), key
        if "doi" in entry:
            assert source.url.lower() == f"https://doi.org/{entry['doi']}".lower(), key
        assert source.url.startswith("https://"), key


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(n.rstrip(",").replace(",", "")) for n in NUMBER.findall(text)]


def test_every_number_in_a_tip_or_a_note_is_one_its_sources_state():
    """Each figure a tip or a weekly note gives (in digits) is in what was read of
    its sources, or within 5 % of one of them ("over 21,000" for 21,195): no number
    from memory."""
    found = entries()
    texts = [(t.key, t.sources, f"{t.why} {t.limits}") for t in tips.TIPS]
    for n in weekly.NOTES:
        texts.append((n.key, n.sources, " ".join((n.title, *n.paragraphs, n.for_you, n.limits))))
    for key, sources, text in texts:
        read = " ".join(
            f"{found[s]['year']} {found[s]['title']} "
            + re.sub(r"\d{4}-\d{2}-\d{2}", " ", found[s]["checked against"])
            for s in sources
        )
        stated = _numbers(read)
        for n in _numbers(text):
            assert any(abs(x - n) <= 0.05 * n for x in stated), (key, n)


def test_the_check_would_catch_a_number_from_memory():
    stated = _numbers("222 independent studies, 48,478 students")
    assert not any(abs(x - 66) <= 0.05 * 66 for x in stated)  # "a median of 66 days" is not there


def test_every_tip_reads_in_french_and_italian():
    for catalogue in (FRENCH, ITALIAN):
        assert not tips.sentences() - catalogue.keys()
    with i18n.use("it"):
        view = service.tips_view("studying")
    first = view["tips"][0]
    assert first["advice"] == ITALIAN[tips.TIPS[0].advice]
    assert first["strength_label"] == "Prove solide"
    assert first["sources"][0]["checked"] == "15 ott 2026"


def test_one_tip_a_day_and_every_tip_in_turn():
    start = date(2026, 10, 15)
    days = [tips.of_the_day(start + timedelta(days=n)).key for n in range(len(tips.TIPS))]
    assert sorted(days) == sorted(t.key for t in tips.TIPS)
    assert tips.of_the_day(start) == tips.of_the_day(start + timedelta(days=len(tips.TIPS)))


def test_the_spark_has_a_mood_for_every_tip_and_a_pose_for_every_mood():
    # D66: the server picks the mood; each one but idle is a pose in the style sheet.
    assert set(service.TIP_MOODS) == {t.key for t in tips.TIPS}
    assert set(service.TIP_MOODS.values()) <= set(service.MOODS)
    css = (Path(__file__).parent.parent / "src/cps/web/static/style.css").read_text(encoding="utf-8")
    assert all(f".mascot.is-{mood} " in css for mood in service.MOODS if mood != "idle")


# Tips at the right moment (D63): the plan's day picks the tip, and says why.

STATS = ("2026-09-29T09:00+02:00", "Advanced Statistics, Grp: CM ., Salle: Salle 3")  # Tue 09:00
ALGEBRA = ("2026-10-01T16:00+02:00", "Algebra 3, Grp: TD ., Salle: Salle 2")  # Thu 16:00


@pytest.fixture(scope="module")
def semester():
    return service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)


def test_a_lecture_later_today_brings_the_questions_before_it(semester):
    tip = service.tip_of_the_day(semester, MORNING)  # 08:00 in Paris, Statistics at 09:00
    assert tip["key"] == "pretest" and tip["reason"] == "A lecture later today: Advanced Statistics at 09:00."
    warmed = service.revise_subscription(semester, warm_up=True, now=MORNING)
    assert service.tip_of_the_day(warmed, MORNING)["key"] != "pretest"  # Today already asks


def test_what_cannot_wait_comes_first(semester):
    quiz = service.change_lecture(semester, *ALGEBRA, "quiz", now=MORNING)
    tip = service.tip_of_the_day(quiz, MORNING)
    assert tip["key"] == "class-quizzes"
    assert tip["reason"] == "A test is coming: Quiz: Algebra 3, Thu 1 Oct 16:00."
    skipped = service.change_lecture(semester, *STATS, "skip", now=MORNING)
    tip = service.tip_of_the_day(skipped, MORNING)
    assert tip["key"] == "attend" and "Advanced Statistics" in tip["reason"]
    # A catch-up is planned: nothing to say about going to class.
    caught_up = service.change_lecture(semester, *STATS, "self", now=MORNING)
    assert service.tip_of_the_day(caught_up, MORNING)["key"] != "attend"
    before_exam = datetime(2027, 1, 23, 9, 0, tzinfo=UTC)  # two days before Computer Programming 3
    tip = service.tip_of_the_day(semester, before_exam)
    assert tip["key"] == "sleep-regular" and tip["reason"].startswith("Exam soon: Computer Programming 3,")


def test_a_skipped_session_yesterday_and_an_evening_session(semester):
    plan = service.PlanReport.from_dict(semester.plan)
    first = plan.sessions[0]  # Thu 1 Oct 08:00
    thursday = datetime.fromisoformat(first.end).astimezone(UTC) + timedelta(hours=1)
    missed = service.report_session(semester, service.session_id(first), "skipped", now=thursday)
    tip = service.tip_of_the_day(missed, thursday + timedelta(days=1))
    assert tip["key"] == "habit" and tip["reason"] == "A session was skipped yesterday."

    evenings = service.revise_subscription(semester, study_time="evening", now=MORNING)
    late = next(
        s
        for s in service.PlanReport.from_dict(evenings.plan).sessions
        if datetime.fromisoformat(s.start).hour >= 18
    )
    start = datetime.fromisoformat(late.start)
    tip = service.tip_of_the_day(evenings, start.replace(hour=13))
    assert tip["key"] == "caffeine" and tip["reason"] == f"You study this evening at {start:%H:%M}."


def test_training_today_is_one_of_the_ordinary_moments(semester):
    gym = {"label": "Gym", "weekday": 1, "start": "19:00", "end": "20:30", "kind": "training"}  # Tuesdays
    trained = service.revise_subscription(semester, busy_rows=[gym], now=MORNING)
    local = MORNING.astimezone(service._zone("Europe/Paris"))
    urgent, usual = service._tip_moments(trained, local)
    assert not urgent and ("exercise", "Training today: Gym at 19:00.") in usual
    # Ordinary moments take turns day by day; tips the day does not call for, in turn.
    keys = {service.tip_of_the_day(trained, MORNING + timedelta(days=7 * n))["key"] for n in range(6)}
    assert "exercise" in keys


def test_tips_can_be_turned_off(semester):
    off = service.set_tips(semester, "off")
    assert service.tip_of_the_day(off, MORNING) is None and service.focus_tip(off) is None
    with pytest.raises(service.InvalidInput):
        service.set_tips(semester, "sometimes")
    with i18n.use("fr"):
        reason = service.tip_of_the_day(semester, MORNING)["reason"]
    assert reason.startswith("Un cours plus tard aujourd'hui")


# The weekly note (D64)


def test_a_note_a_week_from_the_plan_s_first_day():
    start = date(2026, 9, 29)  # a Tuesday
    assert weekly.unlocked(start, start - timedelta(days=1)) == 0
    assert weekly.unlocked(start, start) == weekly.unlocked(start, date(2026, 10, 4)) == 1  # to Sunday
    assert weekly.unlocked(start, date(2026, 10, 5)) == 2  # the next Monday
    assert weekly.unlocked(start, date(2027, 6, 1)) == len(weekly.NOTES)  # all of them, no more
    assert len({n.key for n in weekly.NOTES}) == len(weekly.NOTES) >= 8
    for note in weekly.NOTES:
        assert note.sources and len(note.paragraphs) >= 2 and note.for_you and note.limits, note.key


def test_this_week_s_note_on_today_until_it_is_read(semester):
    view = service.weekly_view(semester, MORNING)
    assert [n["week"] for n in view["notes"]] == [1] and view["next"] == "Monday 5 October"
    assert service.note_of_the_week(semester, MORNING)["key"] == weekly.NOTES[0].key
    read = service.mark_note_read(semester, weekly.NOTES[0].key)
    assert service.note_of_the_week(read, MORNING) is None
    later = MORNING + timedelta(days=7)  # a new note on Monday
    assert service.note_of_the_week(read, later)["key"] == weekly.NOTES[1].key
    assert service.weekly_note(read, weekly.NOTES[5].key, later) is None  # not yet
    with i18n.use("it"):
        assert service.weekly_view(semester, MORNING)["notes"][0]["title"] == ITALIAN[weekly.NOTES[0].title]


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402


@pytest.mark.parametrize(("lang", "catalogue"), [("en", None), ("fr", FRENCH), ("it", ITALIAN)])
def test_the_tips_page_without_its_scripts_and_today_s_tip(tmp_path, lang, catalogue):
    config = Config(store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        start = web.post(
            "/start", data={"tz": "Europe/Paris", "lang": lang}, files=files, follow_redirects=False
        )
        token = start.headers["location"].split("/")[2]

        # The page lists the topics (D91); each topic's page holds its tips, and no other.
        page = web.get(f"/p/{token}/tips")
        assert page.status_code == 200 and 'id="tip-' not in page.text
        listed = unescape(page.text)
        text = ""
        for key, name in tips.TOPICS.items():
            assert f'href="/p/{token}/tips?topic={key}"' in listed
            assert (catalogue or {}).get(name, name) in listed
            shown = unescape(web.get(f"/p/{token}/tips?topic={key}").text)
            assert 'aria-current="page">' + (catalogue or {}).get(name, name) in shown
            for tip in tips.TIPS:
                assert (f'id="tip-{tip.key}"' in shown) == (tip.topic == key)
            text += shown
        for tip in tips.TIPS:
            assert (catalogue or {}).get(tip.advice, tip.advice) in text
            assert (catalogue or {}).get(tip.limits, tip.limits) in text
        for source in {tips.SOURCES[key] for tip in tips.TIPS for key in tip.sources}:
            assert f'href="{source.url}"' in text and source.cite in text
        assert 'id="tip-' not in web.get(f"/p/{token}/tips?topic=nonsense").text  # the list again

        # Today shows one message (D97): the question about counting what is used comes
        # first on a new plan; once it is answered, the day's tip.
        web.post(f"/p/{token}/usage", data={"yes": "0"})
        today = unescape(web.get(f"/p/{token}").text)
        with i18n.use(lang):  # the day's tip and why, in the plan's language
            tip = service.tip_of_the_day(service.load_subscription(tmp_path, token), MORNING)
        assert f"/p/{token}/tips?topic={tip['topic']}#tip-{tip['key']}" in today
        assert tip["advice"] in today and tip["reason"] and tip["reason"] in today
        assert f'href="/p/{token}/tips"' in web.get(f"/p/{token}/focus").text

    measures = service.engagement(tmp_path)
    assert measures["tips_7"] == measures["tips_30"] == 1 and measures["plans"] == 1


def test_the_spark_on_today_and_focus_and_the_switch(tmp_path):
    clock = {"now": MORNING}
    config = Config(store=tmp_path, clock=lambda: clock["now"], background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        web.post(f"/p/{token}/usage", data={"yes": "0"})  # one message at a time (D97)
        today = unescape(web.get(f"/p/{token}").text)
        assert "A lecture later today: Advanced Statistics at 09:00." in today
        assert f"/p/{token}/tips?topic=studying#tip-pretest" in today
        assert today.count('class="mascot is-think"') == 1  # questions before a lecture: it ponders
        assert today.count('id="mascot-pretest-body"') == 1  # its gradients' ids are its own

        focus = unescape(web.get(f"/p/{token}/focus").text)
        assert "Before you start" in focus and f"/p/{token}/tips?topic=breaks#tip-breaks" in focus
        assert 'class="mascot is-wave"' in focus
        web.post(f"/p/{token}/focus/start", data={"course": "Algebra 3"})
        running = web.get(f"/p/{token}/focus").text
        assert 'data-break-at="1440" hidden' in running  # shown by the clock at 24 minutes
        assert 'class="mascot is-rest"' in running
        clock["now"] = MORNING + timedelta(minutes=30)
        assert 'data-break-at="1440">' in web.get(f"/p/{token}/focus").text  # past it: shown

        switched = web.post(f"/p/{token}/tip-setting", data={"tips": "off"}, follow_redirects=False)
        assert switched.status_code == 303
        off = web.get(f"/p/{token}").text
        # The tip is gone; Spark is still in the top bar, as the assistant, not a tip (D71, D97).
        assert "tip-today" not in off and 'class="mascot is-think"' not in off and "mascot-top" in off
        assert 'aria-pressed="true">Off' in web.get(f"/p/{token}/settings/look").text


def test_the_weekly_note_pages(tmp_path):
    config = Config(store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None)
    first, locked = weekly.NOTES[0], weekly.NOTES[3]
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        today = unescape(web.get(f"/p/{token}").text)
        assert "This week's note" in today and f"/p/{token}/weekly/{first.key}" in today
        assert f'href="/p/{token}/weekly"' in web.get(f"/p/{token}/tips").text
        listed = unescape(web.get(f"/p/{token}/weekly").text)
        assert first.title in listed and "New" in listed and locked.title not in listed

        note = unescape(web.get(f"/p/{token}/weekly/{first.key}").text)
        assert all(p in note for p in first.paragraphs) and first.for_you in note and first.limits in note
        assert 'class="mascot is-think"' in note
        for key in first.sources:
            assert f'href="{tips.SOURCES[key].url}"' in note
        assert "This week's note" not in unescape(web.get(f"/p/{token}").text)  # read
        assert web.get(f"/p/{token}/weekly/{locked.key}").status_code == 404
    measures = service.engagement(tmp_path)
    assert measures["weekly_7"] == 1
