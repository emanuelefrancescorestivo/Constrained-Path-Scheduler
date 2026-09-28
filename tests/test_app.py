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
    app.radio[0].set_value("Type my week").run()
    assert not app.exception, app.exception
    assert any("free study blocks" in m.value for m in app.markdown)
