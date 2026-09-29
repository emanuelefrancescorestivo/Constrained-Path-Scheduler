"""
Constrained-Path Scheduler: a thin Streamlit front end.

    pip install -e ".[app]"
    streamlit run app.py

Everything this page shows is computed by `cps.service`; the page only collects
input and lays results out. A test (tests/test_app.py) fails if this file imports
anything from `cps` other than `service`.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from cps import service
from widgets import (
    EXAMPLE_WEEK,
    activity_table,
    calendar_source,
    feed_panel,
    recall_chart,
    settings_panel,
    task_table,
    week_calendar,
)

SAMPLE = Path(__file__).parent / "examples" / "sample-timetable.ics"

st.set_page_config(page_title="Study planner", layout="wide")
st.title("When should I study?")
st.caption(
    "Reads your calendar, finds your exams, and times your reviews with the FSRS "
    "memory model and a search over your free time."
)
st.info("**What this does not know.** " + " ".join(service.LIMITATIONS))

# 1. Input ------------------------------------------------------------------ #
st.header("1. Your calendar")
source, ics, link = calendar_source(SAMPLE)
st.session_state.setdefault("activities", [])
if source == "I have no calendar file" and not st.session_state.get("seeded"):
    st.session_state["activities"] = st.session_state["activities"] or list(EXAMPLE_WEEK)
    st.session_state["seeded"] = True

# 2. Settings --------------------------------------------------------------- #
opts = settings_panel(sample=source == "Sample calendar")
start, tz, retention = opts["start"], opts["tz"], opts["retention"]
earliest, latest = opts["window_hours"]
per_day, minutes = opts["per_day"], opts["minutes"]

if source in ("Upload an .ics file", "Paste a calendar link") and ics is None:
    st.stop()
activities = st.session_state["activities"]
try:
    report = service.analyse_calendar(
        ics,
        start=start,
        tz=tz,
        study_window=(earliest, latest),
        blocks_per_day=int(per_day),
        block_minutes=int(minutes),
        busy_rows=activities,
    )
except service.ServiceError as error:
    st.error(str(error))
    st.stop()
st.write(
    f"{len(report.events)} busy events over {report.days} days; "
    f"**{len(report.blocks)} free study blocks** of {minutes} minutes."
)

# 2. Your week --------------------------------------------------------------- #
st.header("2. Your week")
st.caption(
    "Drag on a day to block time for training, a commute, work or time off. Click a block "
    "to rename it, make it weekly or one-off, or delete it. Grey blocks come from your "
    "calendar file; hatched hours are outside the hours you study."
)


def _edited() -> None:
    st.session_state["activities"] = st.session_state["week-editor"]["edit"]


shown = st.selectbox(
    "Week",
    range(service.calendar_week_count(report)),
    format_func=lambda w: f"Week {w + 1}, from {report.start + timedelta(days=7 * w):%a %d %b}",
    key="editor-week",
)
week_calendar(
    service.calendar_week(report, shown, typed=False),
    key="week-editor",
    activities=activities,
    editable=True,
    study_window=(earliest, latest),
    on_edit=_edited,
)
edited = activity_table(activities, key="activity-table")
if edited is not None:
    st.session_state["activities"] = edited
    st.rerun()

# 3. Subjects ----------------------------------------------------------------- #
st.header("3. Subjects and exams")
st.caption(
    "Familiarity, for what was taught before the plan starts: 1 = new to me, 2 = seen it but shaky, "
    "3 = partly know it, 4 = know it well, 5 = very well. Each later week of a subject's lectures "
    "in your calendar becomes a topic of its own, studied once it has been taught."
)
found = [
    {"subject": a.subject, "exam": a.when[:16].replace("T", " "), "familiarity": 3}
    for a in report.assessments
]
table = st.data_editor(
    pd.DataFrame(found or [{"subject": "", "exam": "", "familiarity": 3}]),
    num_rows="dynamic",
    use_container_width=True,
    key=f"subjects-{source}",
    column_config={
        "familiarity": st.column_config.SelectboxColumn(options=[1, 2, 3, 4, 5], required=True),
        "exam": st.column_config.TextColumn(help="YYYY-MM-DD, or YYYY-MM-DD HH:MM"),
    },
)
specs = [
    service.SubjectSpec(str(r["subject"]).strip(), str(r["exam"]).strip(), familiarity=int(r["familiarity"]))
    for r in table.to_dict("records")
    if str(r.get("subject") or "").strip()
]

assistant = opts["engine"] == "assistant"
prefs = {k: opts[k] for k in ("weekly_hours", "rest_days", "practice_hours")} if assistant else {}
if assistant:
    st.subheader("Deadlines")
    tasks = task_table("tasks")

# 4. Plan --------------------------------------------------------------------- #
if st.button("Plan my study", type="primary"):
    try:
        if assistant:
            plan = service.make_schedule(report, specs, tasks=tasks, retention=retention, **prefs)
        else:
            plan = service.make_plan(report, specs, retention=retention, window=opts["window"])
        st.session_state["plan"] = plan.to_dict()
        st.session_state.pop("replanned", None)
    except service.ServiceError as error:
        st.session_state.pop("plan", None)
        st.error(str(error))
if "plan" not in st.session_state:
    st.stop()
plan = service.PlanReport.from_dict(st.session_state["plan"])

st.header("4. Your plan")
cols = st.columns(max(1, len(plan.subjects)))
for col, subject in zip(cols, plan.subjects, strict=False):
    col.metric(
        subject.name,
        f"{subject.recall_at_exam:.0%} recall at the exam",
        service.subject_status(subject),
        delta_color="normal" if subject.ready else "inverse",
    )
for warning in plan.warnings:
    st.warning(warning)

if plan.tasks:
    st.subheader("Deadlines")
    st.dataframe(pd.DataFrame(service.task_rows(plan)), use_container_width=True, hide_index=True)
st.subheader("Sessions")
st.dataframe(
    pd.DataFrame(service.session_rows(plan)), use_container_width=True, hide_index=True, key="sessions"
)
st.download_button("Download plan.ics", service.export_ics(plan), file_name="plan.ics", mime="text/calendar")
feed_panel(
    plan,
    link=link,
    ics=ics,
    inputs=dict(
        subjects=specs,
        start=start,
        tz=tz,
        busy_rows=activities,
        study_window=(earliest, latest),
        blocks_per_day=int(per_day),
        block_minutes=int(minutes),
        retention=retention,
        window=opts.get("window", 4),
        engine=opts["engine"],
        tasks=tasks if assistant else (),
        preferences=prefs,
    ),
)

recall_chart(plan)

st.subheader("Week by week")
week = st.selectbox(
    "Week",
    range(service.calendar_week_count(plan)),
    format_func=lambda w: f"Week {w + 1}, from {plan.settings.start + timedelta(days=7 * w):%a %d %b}",
)
week_calendar(service.calendar_week(plan, week), key="plan-week", study_window=(earliest, latest))

# 5. What if ------------------------------------------------------------------ #
st.header("5. What if I miss a session?")
if plan.sessions:
    labels = {row["#"]: f"{row['when']}  {row['subject']}" for row in service.session_rows(plan)}
    chosen = st.selectbox("Session", list(labels), format_func=labels.get)
    outcome = st.radio(
        "What happened",
        ["lapsed", "skipped"],
        horizontal=True,
        format_func={"lapsed": "I studied but forgot it", "skipped": "I did not study"}.get,
    )
    if st.button("Replan from there"):
        st.session_state["replanned"] = service.replan_after(plan, chosen, outcome).to_dict()
    if "replanned" in st.session_state:
        after = service.PlanReport.from_dict(st.session_state["replanned"])
        st.dataframe(
            pd.DataFrame(service.session_rows(after)),
            use_container_width=True,
            hide_index=True,
            key="replanned-sessions",
        )
        for warning in after.warnings:
            st.warning(warning)
        st.download_button(
            "Download the replanned plan.ics",
            service.export_ics(after),
            file_name="plan.ics",
            mime="text/calendar",
            key="replanned-ics",
        )
