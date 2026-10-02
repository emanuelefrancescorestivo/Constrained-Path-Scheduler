"""
Tasks as a student handles them: added, finished early, reopened, deleted. A
finished task frees the time its remaining sessions held; a deleted one does not
come back.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris


@pytest.fixture(scope="module")
def planned() -> service.Subscription:
    return service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        engine="assistant",
        tasks=[
            service.TaskSpec("Stats report", "2026-10-10 18:00", 6.0, "Advanced Statistics"),
            service.TaskSpec("Essay", "2026-10-20 12:00", 3.0, "Ethics & Philosophy of AI"),
        ],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )


def _work(sub, name):
    plan = service.PlanReport.from_dict(sub.plan)
    return [s for s in plan.sessions if s.kind == "task" and s.title == name]


def test_a_finished_task_frees_its_sessions_and_can_be_reopened(planned):
    assert _work(planned, "Stats report")
    done = service.set_task_done(planned, "stats report", now=NOW)  # names match whatever the case
    assert _work(done, "Stats report") == [] and _work(done, "Essay")
    view = service.tasks_view(done, NOW)
    finished = [r for r in view["rows"] if r["finished"]]
    assert [r["name"] for r in finished] == ["Stats report"] and finished[0]["status"] == "done"
    assert not finished[0]["at_risk"] and view["finished"] == 1 and view["open"] == 1
    assert done.options["tasks"][0] == {
        "name": "Stats report",
        "due": "2026-10-10 18:00",
        "hours": 6.0,
        "course": "Advanced Statistics",
        "done": True,
    }
    # Finished tasks are not on the Today panel; open ones are.
    assert [t["name"] for t in service.today_view(done, NOW)["tasks"]] == ["Essay"]
    again = service.set_task_done(done, "Stats report", False, now=NOW)
    assert len(_work(again, "Stats report")) == len(_work(planned, "Stats report"))
    assert "done" not in again.options["tasks"][0]


def test_a_finished_task_drops_a_session_the_student_had_moved(planned):
    session = _work(planned, "Stats report")[0]
    moved = service.move_session(planned, service.session_id(session), "2026-10-04T20:00", now=NOW)
    assert moved.options["pins"]
    done = service.set_task_done(moved, "Stats report", now=NOW)
    assert not any(s.pinned for s in service.PlanReport.from_dict(done.plan).sessions)


def test_tasks_are_added_and_deleted_by_name(planned):
    added = service.add_task(
        planned, service.TaskSpec("Reading", "2026-10-05 09:00", 2.0, "Algebra 3"), now=NOW
    )
    assert [t["name"] for t in added.options["tasks"]] == ["Stats report", "Essay", "Reading"]
    assert _work(added, "Reading")
    with pytest.raises(service.InvalidInput, match="name of its own"):
        service.add_task(added, service.TaskSpec("reading", "2026-10-06 09:00", 1.0), now=NOW)
    with pytest.raises(service.InvalidInput, match="name"):
        service.add_task(added, service.TaskSpec(" ", "2026-10-06 09:00", 1.0), now=NOW)
    gone = service.delete_task(added, "Reading", now=NOW)
    assert [t["name"] for t in gone.options["tasks"]] == ["Stats report", "Essay"] and not _work(
        gone, "Reading"
    )
    with pytest.raises(service.InvalidInput, match="no task called"):
        service.delete_task(gone, "Reading", now=NOW)


def test_the_tasks_view_says_where_each_task_stands(planned):
    view = service.tasks_view(planned, NOW)
    rows = {r["name"]: r for r in view["rows"]}
    report = rows["Stats report"]
    assert report["status"] == "on track" and report["hours"] == 6.0 and report["blocks"] == 4
    assert report["sessions_planned"] == 4 and report["sessions_done"] == 0 and report["next"]
    assert report["due_value"] == "2026-10-10T18:00" and report["color"] is not None
    assert [r["name"] for r in view["rows"]] == ["Stats report", "Essay"]  # by due date
    assert view["open"] == 2 and view["this_week"] == 0 and view["at_risk"] == 0
    late = service.tasks_view(planned, datetime(2026, 10, 12, tzinfo=UTC))
    assert {r["name"]: r["status"] for r in late["rows"]}["Stats report"] == "late"
