"""
AI features (DECISIONS.md D12): the rules that always answer, in English and
French; the model only when AI is on, the server has a provider, the month is under
the cap and the student under the day's limit; every call in the ledger; any
failure gives the rules' answer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cps import ai, service
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TODAY = date(2026, 10, 8)  # a Thursday
NOW = datetime(2026, 10, 8, 8, 0, tzinfo=UTC)
COURSES = ["Advanced Statistics", "Algebra 3", "Analyse numérique", "Histoire moderne"]


@pytest.mark.parametrize(
    ("words", "name", "due", "hours", "course"),
    [
        (
            "stats report for Friday, about 6 h",
            "Stats report",
            "2026-10-09T23:59",
            6.0,
            "Advanced Statistics",
        ),
        (
            "rapport de stats pour vendredi 18h, 6 heures",
            "Rapport de stats",
            "2026-10-09T18:00",
            6.0,
            "Advanced Statistics",
        ),
        (
            "devoir d'analyse numérique à rendre le 15 octobre à 14h, 3h",
            "Devoir d'analyse numérique",
            "2026-10-15T14:00",
            3.0,
            "Analyse numérique",
        ),
        ("essay due 12/10 (4 hours)", "Essay", "2026-10-12T23:59", 4.0, ""),
        ("lab report 10/25 5pm", "Lab report", "2026-10-25T17:00", None, ""),  # 25 cannot be a month
        (
            "Algebra problem sheet tomorrow at 18:00",
            "Algebra problem sheet",
            "2026-10-09T18:00",
            None,
            "Algebra 3",
        ),
        (
            "a 2h session on module design next week",
            "A session on module design",
            "2026-10-15T23:59",
            2.0,
            "",
        ),
        ("read chapter 4 in 3 days, 90 min", "Read chapter 4", "2026-10-11T23:59", 1.5, ""),
        (
            "Mémoire d'histoire moderne 2026-12-01",
            "Mémoire d'histoire moderne",
            "2026-12-01T23:59",
            None,
            "Histoire moderne",
        ),
        ("projet pour lundi prochain", "Projet", "2026-10-12T23:59", None, ""),
        ("TD de jeudi", "TD", "2026-10-15T23:59", None, ""),  # Thursday, said on a Thursday: next week
        ("lab report October 20th 5pm", "Lab report", "2026-10-20T17:00", None, ""),
        ("revise for the exam 3 March", "Revise for the exam", "2027-03-03T23:59", None, ""),  # next March
        ("prepare slides", "Prepare slides", "", None, ""),
    ],
)
def test_the_rules_read_a_task_in_english_and_french(words, name, due, hours, course):
    read = ai.read_task(words, today=TODAY, courses=COURSES)
    assert (read.name, read.due, read.hours, read.course) == (name, due, hours, course)


def test_two_courses_named_at_once_is_neither():
    assert ai.read_task("Statistics and Algebra revision", today=TODAY, courses=COURSES).course == ""
    # "le" inside "module" is not a word that joins a date.
    assert ai.read_task("module 6 h", today=TODAY).name == "Module"


class Fake:
    """A provider that answers what it is told to, and counts its calls."""

    def __init__(self, data=None, model=ai.DEFAULT_MODEL, fail=False):
        self.data, self.model, self.fail, self.calls = data, model, fail, []

    def ask(self, system, prompt, schema, max_tokens):
        self.calls.append((system, prompt, schema, max_tokens))
        if self.fail:
            raise RuntimeError("network down")
        return ai.fake_answer(self.data, self.model)


@pytest.fixture
def sub(tmp_path) -> service.Subscription:
    made = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[service.TaskSpec("Stats report", "2026-10-10 18:00", 6.0, "Advanced Statistics")],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(tmp_path, made)
    return made


def test_the_model_is_asked_only_when_ai_is_on(tmp_path, sub):
    model = Fake(
        {"name": "Statistics report", "due": "2026-10-16T12:00", "hours": 5, "course": "Advanced Statistics"}
    )
    off = service.understand_task(
        tmp_path, sub, "stats report for Friday, about 6 h", now=NOW, provider=model
    )
    assert off["by"] == "rules" and off["due"] == "2026-10-09T23:59" and model.calls == []
    on = service.set_ai(sub, True)
    read = service.understand_task(
        tmp_path, on, "stats report next friday at noon, 5 h", now=NOW, provider=model
    )
    assert read["by"] == "ai" and read["name"] == "Statistics report" and read["due"] == "2026-10-16T12:00"
    assert read["hours"] == 5 and read["course"] == "Advanced Statistics" and "AI" in read["note"]
    system, prompt, schema, _ = model.calls[0]
    # What is sent: the line, today's date, the time zone and the course names; not the plan's token.
    assert (
        "stats report next friday" in prompt and "Europe/Paris" in prompt and "Advanced Statistics" in prompt
    )
    assert sub.token not in prompt + system and schema["additionalProperties"] is False
    assert ai.usage(Store(tmp_path), NOW - timedelta(days=1)) == {"calls": 1, "used": 1, "dollars": 0.0028}


@pytest.mark.parametrize(
    "data",
    [
        None,  # a refusal, or an answer cut short
        {"name": "x", "due": "next friday", "hours": 2, "course": ""},  # not a date
        {"name": "x", "due": "2025-01-01T10:00", "hours": 2, "course": ""},  # in the past
        {"name": "", "due": "2026-10-16T12:00", "hours": 2, "course": ""},
        {"name": "x", "due": "2026-10-16T12:00", "hours": 900, "course": ""},
        {"unexpected": True},
    ],
)
def test_an_unusable_answer_gives_the_rules_reading(tmp_path, sub, data):
    read = service.understand_task(
        tmp_path, service.set_ai(sub, True), "stats report for Friday, 6 h", now=NOW, provider=Fake(data)
    )
    assert read["by"] == "rules" and read["due"] == "2026-10-09T23:59" and read["hours"] == 6.0


def test_a_failing_model_gives_the_rules_reading_and_is_counted(tmp_path, sub):
    model = Fake(fail=True)
    read = service.understand_task(
        tmp_path, service.set_ai(sub, True), "essay due 12/10", now=NOW, provider=model
    )
    assert read["by"] == "rules" and read["due"] == "2026-10-12T23:59" and len(model.calls) == 1
    assert ai.usage(Store(tmp_path), NOW - timedelta(days=1))["calls"] == 1


def test_the_monthly_cap_and_the_daily_limit_stop_calls(tmp_path, sub, monkeypatch):
    on = service.set_ai(sub, True)
    model = Fake({"name": "Essay", "due": "2026-10-12T23:59", "hours": 4, "course": ""})
    for _ in range(ai.DEFAULT_DAILY_CALLS):
        assert service.understand_task(tmp_path, on, "essay due 12/10", now=NOW, provider=model)["by"] == "ai"
    assert service.understand_task(tmp_path, on, "essay due 12/10", now=NOW, provider=model)["by"] == "rules"
    assert len(model.calls) == ai.DEFAULT_DAILY_CALLS  # the 21st was not made
    tomorrow = NOW + timedelta(days=1, minutes=1)
    assert (
        service.understand_task(tmp_path, on, "essay due 12/10", now=tomorrow, provider=model)["by"] == "ai"
    )
    # The cap counts the month's spending plus the call's worst case.
    monkeypatch.setenv("CPS_AI_MONTHLY_CAP_USD", "0.06")
    assert ai.spent_this_month(Store(tmp_path), tomorrow) == pytest.approx(21 * 0.0028)
    before = len(model.calls)
    assert (
        service.understand_task(tmp_path, on, "essay due 12/10", now=tomorrow, provider=model)["by"]
        == "rules"
    )
    assert len(model.calls) == before
    # A new month starts from zero.
    november = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    service.understand_task(tmp_path, on, "essay due 12/10", now=november, provider=model)
    assert len(model.calls) == before + 1


def test_a_model_without_a_known_price_is_never_called(tmp_path, sub):
    model = Fake({"name": "Essay", "due": "", "hours": 0, "course": ""}, model="some-other-model")
    read = service.understand_task(tmp_path, service.set_ai(sub, True), "essay", now=NOW, provider=model)
    assert read["by"] == "rules" and model.calls == [] and "No date found" in read["note"]


def test_without_a_key_there_is_no_provider(monkeypatch):
    ai.from_env.cache_clear()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ai.from_env() is None
    ai.from_env.cache_clear()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CPS_AI_MODEL", "not-a-priced-model")
    assert ai.from_env() is None
    ai.from_env.cache_clear()


def test_the_review_paragraph_is_written_once_per_week_and_numbers(tmp_path, sub):
    on = service.set_ai(sub, True)
    service.save_subscription(tmp_path, on)
    monday = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
    review = service.weekly_review(on, monday)
    assert review["by"] == "rules"
    model = Fake({"paragraph": "You did what you planned on most days; keep the same rhythm this week."})
    written = service.review_with_ai(tmp_path, on, review, now=monday, provider=model)
    assert written["by"] == "ai" and written["text"].startswith("You did what you planned")
    system, prompt, _, _ = model.calls[0]
    assert "English" in system and str(review["sessions_planned"]) in prompt and on.token not in prompt
    kept = service.load_subscription(tmp_path, on.token)
    again = service.review_with_ai(tmp_path, kept, review, now=monday, provider=model)
    assert again["text"] == written["text"] and len(model.calls) == 1  # kept, not asked again
    # Other numbers (a late report), another paragraph; off, the template.
    changed = {**review, "sessions_done": review["sessions_done"] + 1}
    service.review_with_ai(tmp_path, kept, changed, now=monday, provider=model)
    assert len(model.calls) == 2
    assert service.review_with_ai(tmp_path, sub, review, now=monday, provider=model)["by"] == "rules"
    short = Fake({"paragraph": "Ok."})
    assert (
        service.review_with_ai(tmp_path, kept, {**review, "skipped": 9}, now=monday, provider=short)["by"]
        == "rules"
    )


def test_deleting_a_plan_keeps_the_cost_and_forgets_the_plan(tmp_path, sub):
    on = service.set_ai(sub, True)
    service.understand_task(tmp_path, on, "essay due 12/10", now=NOW, provider=Fake({"paragraph": "x"}))
    store = Store(tmp_path)
    assert ai.calls_today(store, sub.token, NOW) == 1
    assert service.delete_subscription(tmp_path, sub.token)
    assert ai.calls_today(store, sub.token, NOW) == 0
    assert ai.usage(store, NOW - timedelta(days=1))["calls"] == 1  # the month's spending still counts
