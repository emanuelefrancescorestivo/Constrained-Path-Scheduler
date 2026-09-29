"""
Tests for the Streamlit front end.

Two properties, and neither is about looks. The page contains no business
logic: it may import nothing from `cps` except `cps.service` (CLAUDE.md
invariant 11), checked by reading its syntax tree. And what it displays is what
the service computes: a headless run on the sample calendar must show exactly the
session table that `service.make_plan` returns for the same inputs.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from cps import service

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"


def test_the_app_imports_nothing_from_cps_but_the_service():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name == "cps.service" or not alias.name.startswith("cps"), alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "cps":
            if node.module == "cps":
                assert [a.name for a in node.names] == ["service"], ast.dump(node)
            else:
                assert node.module == "cps.service", node.module


def test_the_app_stays_thin():
    assert len(APP.read_text(encoding="utf-8").splitlines()) <= 250


@pytest.mark.slow
def test_the_displayed_sessions_are_the_service_plan():
    pytest.importorskip("streamlit")
    import pandas as pd
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP), default_timeout=300)
    app.run()
    app.radio[0].set_value("Sample calendar").run()
    assert not app.exception, app.exception
    next(b for b in app.button if b.label == "Plan my study").click().run()
    assert not app.exception, app.exception

    shown = next(df.value for df in app.dataframe if list(df.value.columns)[:2] == ["#", "when"])

    report = service.analyse_calendar(
        (ROOT / "examples" / "sample-timetable.ics").read_bytes(), start=date(2026, 3, 2), tz="Europe/Rome"
    )
    expected = service.make_plan(
        report,
        [service.SubjectSpec("Analysis", "2026-03-20 09:00", familiarity=3)],
        retention=0.9,
        window=4,
    )
    pd.testing.assert_frame_equal(
        shown.reset_index(drop=True), pd.DataFrame(service.session_rows(expected)), check_dtype=False
    )
    assert len(shown) == len(expected.sessions) > 0


@pytest.mark.slow
def test_what_if_i_miss_a_session_shows_the_service_replan():
    pytest.importorskip("streamlit")
    import pandas as pd
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP), default_timeout=300)
    app.run()
    app.radio[0].set_value("Sample calendar").run()
    next(b for b in app.button if b.label == "Plan my study").click().run()
    outcome = next(r for r in app.radio if r.label == "What happened")
    outcome.set_value("lapsed").run()
    next(b for b in app.button if b.label == "Replan from there").click().run()
    assert not app.exception, app.exception

    tables = [df.value for df in app.dataframe if list(df.value.columns)[:2] == ["#", "when"]]
    assert len(tables) == 2
    report = service.analyse_calendar(
        (ROOT / "examples" / "sample-timetable.ics").read_bytes(), start=date(2026, 3, 2), tz="Europe/Rome"
    )
    plan = service.make_plan(
        report, [service.SubjectSpec("Analysis", "2026-03-20 09:00", familiarity=3)], retention=0.9, window=4
    )
    expected = service.replan_after(plan, plan.sessions[0].index, "lapsed")
    pd.testing.assert_frame_equal(
        tables[1].reset_index(drop=True), pd.DataFrame(service.session_rows(expected)), check_dtype=False
    )


def test_a_typed_week_runs_without_errors():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP), default_timeout=300)
    app.run()
    app.radio[0].set_value("I have no calendar file").run()
    assert not app.exception, app.exception
    assert any("free study blocks" in m.value for m in app.markdown)
    # The example week is there to be edited, and it is what the planner sees.
    assert len(app.session_state["activities"]) == 3
    assert any(m.value.startswith("9 busy events over 21 days") for m in app.markdown)


@pytest.mark.slow
def test_a_calendar_link_is_read_and_the_plan_can_be_published(timetable_server, tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(service, "LINKS_MAY_BE_PRIVATE", True)
    monkeypatch.setattr(service, "FEED_STORE", tmp_path)
    app = AppTest.from_file(str(APP), default_timeout=300)
    app.run()
    app.radio[0].set_value("Paste a calendar link").run()
    app.text_input[0].set_value(f"{timetable_server}/sample-timetable.ics").run()
    app.date_input[0].set_value(date(2026, 3, 2)).run()
    assert not app.exception, app.exception
    assert any("free study blocks" in m.value for m in app.markdown)
    next(b for b in app.button if b.label == "Plan my study").click().run()
    next(b for b in app.button if b.label == "Publish as a calendar feed").click().run()
    assert not app.exception, app.exception
    token = app.session_state["feed-token"]
    stored = service.load_subscription(tmp_path, token)
    assert stored.source_url == f"{timetable_server}/sample-timetable.ics" and stored.plan is not None
    assert any(service.feed_url(service.FEED_URL, token) in c.value for c in app.code)
    next(b for b in app.button if b.label == "Stop publishing").click().run()
    assert service.load_subscription(tmp_path, token) is None
