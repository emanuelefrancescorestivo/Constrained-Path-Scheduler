"""
D147: hours that fit the exams (the owner, 2026-11-19, after a last pass before each exam
was measured and changed nothing). What must hold: when the week's hours leave topics under
the target on their exam day, Today says how many, and the fewest more hours, a block at a
time, that keep the most; the numbers are the plans made with those hours; it is not said
on the plan's first day, with no weekly limit, when every topic is kept, or for two weeks
after "Not now"; taking it plans those hours.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 18:00 in Paris, a Tuesday
NEXT_DAY = NOW + timedelta(days=7)  # the plan's first week is for its questions (D98)
COURSES = ("Algebra 3", "Advanced Statistics", "Analysis 3")


def _plan(root: Path, hours: float | None, courses: tuple[str, ...] = COURSES) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec(n, None, familiarity=3) for n in courses],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences={**service.DEFAULT_PREFERENCES, "weekly_hours": hours},
        now=NOW,
    )
    service.save_subscription(root, service.finish_first_run(sub))
    return service.load_subscription(root, sub.token)


def _below(sub: service.Subscription, hours: float) -> int:
    """Topics under the target on exam day in the plan made with `hours` a week."""
    preferences = {**sub.options["preferences"], "weekly_hours": hours}
    tried = service._refresh(
        replace(sub, options={**sub.options, "preferences": preferences}), now=NEXT_DAY, reread=False
    )
    subjects = service.PlanReport.from_dict(tried.plan).subjects
    return sum(s.topics - s.topics_ready for s in subjects)


def test_the_offer_is_the_fewest_more_hours_that_keep_the_most(tmp_path):
    sub = _plan(tmp_path, 6.0)
    offer = service.hours_offer(sub, NEXT_DAY)
    assert offer is not None
    plan = service.PlanReport.from_dict(sub.plan)
    assert offer["hours_now"] == 6.0 and offer["target"] == 90
    assert offer["topics"] == sum(s.topics for s in plan.subjects)
    assert offer["below_now"] == sum(s.topics - s.topics_ready for s in plan.subjects) > 0
    # A block at a time: the hours offered are the plan's own block lengths above now's.
    step = plan.settings.block_minutes / 60
    steps = [6.0 + k * step for k in range(1, service.HOURS_STEPS + 1)]
    assert offer["hours"] in steps
    belows = [_below(sub, h) for h in steps]
    # Its numbers are the plan made with those hours, the fewest that keep the most.
    assert offer["below"] == _below(sub, offer["hours"]) < offer["below_now"]
    best = min(belows)
    assert offer["below"] == best and offer["hours"] == steps[belows.index(best)]
    # Every step that keeps more, for the student to choose; the numbers DECISIONS.md D147
    # quotes, a week into the plan: at 6 h, 21 of 34 under 90 %; 12 at 7.5 h, 4 at 9 h, none at 10.5 h.
    # Each step is the plan made with its hours, in order, up to the first that keeps all.
    assert all(x["below"] == belows[steps.index(x["hours"])] for x in offer["steps"])
    assert (offer["below_now"], offer["topics"]) == (21, 34)
    assert [(x["hours"], x["below"]) for x in offer["steps"]] == [(7.5, 12), (9.0, 4), (10.5, 0)]


def test_the_offer_waits_and_stays_quiet_when_more_hours_cannot_help(tmp_path):
    sub = _plan(tmp_path, 6.0)
    assert service.hours_offer(sub, NEXT_DAY - timedelta(days=1)) is None  # not in the plan's first week
    later = service.hours_later(sub, NEXT_DAY)
    assert service.hours_offer(later, NEXT_DAY + timedelta(days=service.HOURS_LATER_DAYS - 1)) is None
    assert service.hours_offer(later, NEXT_DAY + timedelta(days=service.HOURS_LATER_DAYS)) is not None
    # No weekly limit: more hours mean nothing.
    assert service.hours_offer(_plan(tmp_path / "free", None), NEXT_DAY) is None
    # Every topic kept: nothing to say.
    one = _plan(tmp_path / "one", 15.0, COURSES[:1])
    plan = service.PlanReport.from_dict(one.plan)
    assert all(s.topics_ready == s.topics for s in plan.subjects)
    assert service.hours_offer(one, NEXT_DAY) is None
    # Too late for every exam: within a week of the last one, nothing is offered.
    last = max(datetime.fromisoformat(s.exam) for s in service.PlanReport.from_dict(sub.plan).subjects)
    assert service.hours_offer(sub, last - timedelta(days=service.HOURS_LEAD_DAYS - 1)) is None


def test_today_offers_the_hours_and_one_tap_plans_them(tmp_path):
    sub = _plan(tmp_path, 6.0)
    offer = service.hours_offer(sub, NEXT_DAY)
    config = Config(
        store=tmp_path, clock=lambda: NEXT_DAY, background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        client.post(f"/p/{sub.token}/usage", data={"yes": "0"})  # the questions before it (D96),
        holiday = service.holiday_ask(sub, NEXT_DAY)  # and All Saints' week, four weeks ahead (D184)
        assert holiday is not None and holiday["from"] == "2026-10-24"
        client.post(f"/p/{sub.token}/holiday", data={"from": holiday["from"], "answer": "study"})
        offer = service.hours_offer(service.load_subscription(tmp_path, sub.token), NEXT_DAY)  # planned again
        page = unescape(client.get(f"/p/{sub.token}").text)
        card = re.search(r'<div class="card nudge-card section hours-offer">.*?</div>\s*</div>', page, re.S)
        assert card is not None
        words = card.group(0)
        assert f"{offer['below_now']} topics are predicted under 90 % on their exam day" in words
        assert 'name="from" value="hours"' in words
        assert words.count('name="hours" value=') == len(offer["steps"]) == 3
        assert "every topic at 90 %" in words and "12 topics under 90 %" in words
        taken = client.post(
            f"/p/{sub.token}/budget",
            data={"hours": str(offer["hours"]), "from": "hours"},
            follow_redirects=False,
        )
        assert taken.status_code == 303
        fresh = service.load_subscription(tmp_path, sub.token)
        assert fresh.options["preferences"]["weekly_hours"] == offer["hours"]
        plan = service.PlanReport.from_dict(fresh.plan)
        assert sum(s.topics - s.topics_ready for s in plan.subjects) == offer["below"]
        # "Not now" puts it off.
        other = _plan(tmp_path, 6.0)
        client.post(f"/p/{other.token}/usage", data={"yes": "0"})
        client.post(f"/p/{other.token}/holiday", data={"from": "2026-10-24", "answer": "study"})
        client.post(f"/p/{other.token}/hours/later")
        assert service.load_subscription(tmp_path, other.token).options["hours_later"] == "2026-10-06"
        assert "hours-offer" not in client.get(f"/p/{other.token}").text
