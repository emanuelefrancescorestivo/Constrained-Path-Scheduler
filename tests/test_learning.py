"""D82: Your memory. Every number rises with spaced recall and nothing else: the
strength of the taught topics, today's map, the memory days the week's reviews
added, and the recall reps."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import learning, service
from cps.memory import FSRS_6, Grade, MemoryState
from cps.web import Config, create_app
from cps.web.sky import contrast

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Tuesday
MONDAYS = [date(2026, 9, 21), date(2026, 9, 28)]


@pytest.mark.parametrize(
    ("recall", "name"), [(0.97, "strong"), (0.90, "strong"), (0.85, "good"), (0.7, "fading"), (0.4, "review")]
)
def test_four_levels_start_at_ninety_eighty_and_seventy(recall, name):
    assert learning.level(recall) == name


def test_the_map_groups_topics_by_course_and_counts_each_level():
    topics = [
        learning.Topic("Algebra", "Algebra · week of 05 Oct", 0.95, 12.4),
        learning.Topic("Algebra", "Algebra · week of 12 Oct", 0.62, 1.2),
        learning.Topic("Physics", "Physics", 0.83, 6.0),
    ]
    tiles = learning.topic_map(topics)
    assert [c["course"] for c in tiles["courses"]] == ["Algebra", "Physics"]
    assert tiles["courses"][0]["topics"][1] == {
        "name": "Algebra · week of 12 Oct",
        "recall": 62,
        "days": 1,
        "level": "review",
    }
    assert tiles["counts"] == {"strong": 1, "good": 1, "fading": 0, "review": 1}


def test_reps_and_memory_days_are_counted_by_week():
    reps = [
        learning.Rep(date(2026, 9, 22), "self-test"),
        learning.Rep(date(2026, 9, 28), "card"),
        learning.Rep(date(2026, 10, 4), "exercise"),
        learning.Rep(date(2026, 10, 5), "self-test"),  # the week after: not shown
    ]
    assert learning.reps_by_week(reps, MONDAYS) == [
        {"self-test": 1, "exercise": 0, "card": 0, "total": 1},
        {"self-test": 0, "exercise": 1, "card": 1, "total": 2},
    ]
    assert learning.by_week(
        [(date(2026, 9, 23), 4.0), (date(2026, 9, 30), 2.5), (date(2026, 10, 1), -1.0)], MONDAYS
    ) == [
        4.0,
        1.5,
    ]


def test_strength_growth_and_milestones():
    assert learning.strength([2.0, 30.0, 6.0]) == 6.0 and learning.strength([]) is None
    assert learning.growth([None, 0.5, 2.0, 5.0]) == 10.0
    assert learning.growth([3.0]) is None
    assert learning.milestone(6.9) is None and learning.milestone(7) == 7 and learning.milestone(45) == 30


def test_a_recall_after_a_gap_adds_more_than_one_crammed_at_once():
    # The claim the memory days card makes, held by the model the product runs (FSRS-6):
    # the same successful recall adds more stability after a gap than straight away.
    state = MemoryState(stability=5.0, difficulty=5.0)
    crammed = FSRS_6.review(state, 0.0, Grade.GOOD).stability - state.stability
    spaced = FSRS_6.review(state, 5.0, Grade.GOOD).stability - state.stability
    assert spaced > 2 * crammed


# -- through the service --------------------------------------------------------------


def _plan(tmp_path: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(tmp_path, sub)
    return sub


def test_an_answered_self_test_is_a_rep_and_a_better_answer_adds_more(tmp_path):
    sub = _plan(tmp_path)
    first = service.learning_view(tmp_path, sub, NOW)
    assert first is not None and first["reps_week"]["total"] == 0
    assert sum(first["map"]["counts"].values()) == first["topics"]
    plan = service.PlanReport.from_dict(sub.plan)
    test = next(s for s in plan.sessions if s.kind in learning.SELF_TESTS)
    after = datetime.fromisoformat(test.end) + timedelta(hours=1)
    views = {}
    for answer in ("forgot", "most"):
        answered = service.report_session(sub, service.session_id(test), answer, now=after)
        views[answer] = service.learning_view(tmp_path, answered, after)
    assert views["most"]["reps_week"]["self-test"] == views["forgot"]["reps_week"]["self-test"] == 1
    assert views["most"]["added_week"] > views["forgot"]["added_week"]

    def recall(view):
        tiles = [t for c in view["map"]["courses"] for t in c["topics"]]
        return next(t["recall"] for t in tiles if t["name"] == test.title)

    assert recall(views["most"]) > recall(views["forgot"])


def test_progress_shows_your_memory(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        page = web.get(f"/p/{token}/progress").text
        assert 'id="memory"' in page and "Memory strength" in page and "Recall reps" in page
        assert page.count('class="mtile lvl-') >= 1


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def test_the_text_on_every_tile_reads_at_four_and_a_half_to_one_in_both_themes():
    css = (Path(__file__).resolve().parent.parent / "src/cps/web/static/style.css").read_text()
    block = css[css.index("/* Your memory (D82)") :]
    themes = re.findall(r"--mem-review: [^}]*", block)[:2]  # light, then dark
    assert len(themes) == 2
    for theme in themes:
        tokens = dict(re.findall(r"--(mem-[a-z-]+): (#[0-9a-fA-F]{6})", theme))
        for name in ("review", "fading", "good", "strong"):
            ratio = contrast(_rgb(tokens[f"mem-{name}"]), _rgb(tokens[f"mem-ink-{name}"]))
            assert ratio >= 4.5, (name, ratio)
