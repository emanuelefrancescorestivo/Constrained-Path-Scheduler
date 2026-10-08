"""
The pilot's measures (DECISIONS.md D15): counted on the server from the event log,
the plans and the network's tables; `cps metrics` prints them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cps import cli, service
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)  # a Wednesday evening


def _plan(tmp_path, weekly_hours=15.0) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences={**service.DEFAULT_PREFERENCES, "weekly_hours": weekly_hours},
        now=datetime(2026, 9, 29, 6, 0, tzinfo=UTC),
    )
    service.save_subscription(tmp_path, sub)
    return sub


def _event(store: Store, token: str, at: datetime, kind: str, detail: str = "") -> None:
    with store.connection() as db:
        db.execute(
            "INSERT INTO events (token, at, kind, detail) VALUES (?, ?, ?, ?)",
            (token, at.isoformat(timespec="seconds"), kind, detail),
        )


def test_the_pilot_measures(tmp_path, capsys):
    store = Store(tmp_path)
    keen, quiet, busy = _plan(tmp_path), _plan(tmp_path), _plan(tmp_path, weekly_hours=1.0)
    for sub, started in (
        (keen, NOW - timedelta(days=8)),
        (quiet, NOW - timedelta(days=40)),
        (busy, NOW - timedelta(days=2)),
    ):
        _event(store, sub.token, started, "start", "file")
    _event(store, keen.token, NOW - timedelta(days=1), "visit")  # day 7 after its start
    _event(store, quiet.token, NOW - timedelta(days=10), "visit")  # day 30 after its start
    # Keen confirms its sessions of the last week; busy logs more than its 1 h a week.
    recent = [
        s
        for s in service.PlanReport.from_dict(keen.plan).history
        + service.PlanReport.from_dict(keen.plan).sessions
        if NOW - timedelta(days=7) <= datetime.fromisoformat(s.start) <= NOW
    ]
    assert recent, "the sample semester plans sessions in that week"
    for s in recent:
        keen = service.report_session(keen, service.session_id(s), "done", now=NOW)
    service.save_subscription(tmp_path, keen)
    service.log_session(
        tmp_path, busy, {"course": "Algebra 3", "effort": "6", "progress": "3", "minutes": "120"}, now=NOW
    )
    m = service.engagement(tmp_path, NOW)
    assert m["plans"] == 3 and m["new_30"] == 2
    assert m["active_7"] == 1 and m["active_30"] == 2
    assert m["north_star"] == 2  # keen (done) and busy (a logged session); not quiet
    assert m["confirmed_7"] == len(recent) and m["planned_7"] >= len(recent)
    assert m["return_7"] == (1, 2) and m["return_30"] == (1, 1)
    assert m["over_limit"] == 1  # busy: 2 h in a week against its own 1 h limit
    assert m["network"]["sessions_private"] == 1 and m["network"]["profiles"] == 0
    assert m["ai"] == {"calls": 0, "used": 0, "dollars": 0.0, "cap": 10.0, "available": False}
    assert cli.main(["metrics", "--db", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    # The command reads the store at today's date, so only its lines are checked here.
    assert "plans set up             3 (" in printed
    assert "north star" in printed and "over their weekly limit" in printed and "AI this month" in printed
