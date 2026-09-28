"""
Constrained-Path Scheduler: a thin Streamlit front end.

    pip install -e ".[app]"
    streamlit run app.py

Everything this page shows is computed by `cps.service`; the page only collects
input and lays results out. A test (tests/test_app.py) fails if this file imports
anything from `cps` other than `service`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from cps import service

SAMPLE = Path(__file__).parent / "examples" / "sample-timetable.ics"
DEFAULT_WEEK = pd.DataFrame(
    [
        {"label": "Lectures", "weekday": "Mon", "date": None, "start": "09:00", "end": "13:00"},
        {"label": "Lectures", "weekday": "Wed", "date": None, "start": "09:00", "end": "13:00"},
        {"label": "Job", "weekday": "Fri", "date": None, "start": "14:00", "end": "19:00"},
    ]
)

st.set_page_config(page_title="Study planner", layout="wide")
st.title("When should I study?")
st.caption(
    "Reads your calendar, finds your exams, and times your reviews with the FSRS "
    "memory model and a search over your free time."
)
st.info("**What this does not know.** " + " ".join(service.LIMITATIONS))

# 1. Input ------------------------------------------------------------------ #
st.header("1. Your calendar")
source = st.radio(
    "Where is your timetable?", ["Upload an .ics file", "Type my week", "Sample calendar"], horizontal=True
)
ics, rows = None, []
if source == "Upload an .ics file":
    upload = st.file_uploader("Calendar export (.ics)", type=["ics"])
    st.caption("Google Calendar: Settings, Import and export, Export. Apple Calendar: File, Export.")
    ics = upload.getvalue() if upload else None
elif source == "Sample calendar":
    ics = SAMPLE.read_bytes()
    st.caption(
        "A synthetic timetable: lectures, gym, a weekend away, and an Analysis exam on "
        "20 March 2026. Start the plan on 2 March 2026 in Europe/Rome to see it."
    )
else:
    st.caption(
        "One row per busy block. Weekly rows take a weekday, one-off rows a date "
        "(YYYY-MM-DD). An end before the start runs past midnight. Name an exam "
        '"Physics exam" and it is found as an exam.'
    )
    edited = st.data_editor(DEFAULT_WEEK, num_rows="dynamic", use_container_width=True, key="week")
    rows = [r for r in edited.to_dict("records") if r.get("label")]

# 2. Settings --------------------------------------------------------------- #
with st.sidebar:
    st.header("2. Settings")
    sample = source == "Sample calendar"
    start = st.date_input("Plan from", value=date(2026, 3, 2) if sample else date.today())
    tz = st.text_input("Time zone", value="Europe/Rome")
    earliest, latest = st.slider("Hours you study between", 0, 24, (8, 22))
    per_day = st.number_input("Study blocks per day at most", 1, 6, 2)
    minutes = st.selectbox("Block length (minutes)", [60, 90, 120], index=1)
    retention = st.slider("Recall you want at the exam", 0.75, 0.97, 0.90, 0.01)
    window = st.number_input(
        "Blocks planned exactly at a time",
        1,
        8,
        4,
        help="Larger is slower and, measured, slightly better up to about 4.",
    )

if source == "Upload an .ics file" and ics is None:
    st.stop()
try:
    report = service.analyse_calendar(
        ics,
        start=start,
        tz=tz,
        study_window=(earliest, latest),
        blocks_per_day=int(per_day),
        block_minutes=int(minutes),
        busy_rows=rows,
    )
except service.ServiceError as error:
    st.error(str(error))
    st.stop()
st.write(
    f"{len(report.events)} busy events over {report.days} days; "
    f"**{len(report.blocks)} free study blocks** of {minutes} minutes."
)

# 3. Subjects ----------------------------------------------------------------- #
st.header("3. Subjects and exams")
st.caption(
    "Familiarity: 1 = new to me, 2 = seen it but shaky, 3 = partly know it, "
    "4 = know it well, 5 = know it very well. It is a guess and the plan treats it as one."
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

# 4. Plan --------------------------------------------------------------------- #
if st.button("Plan my study", type="primary"):
    try:
        plan = service.make_plan(report, specs, retention=retention, window=int(window))
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
    status = "ready" if subject.ready else ("out of reach" if subject.unreachable else "not ready")
    col.metric(
        subject.name,
        f"{subject.recall_at_exam:.0%} recall at the exam",
        f"{status}: stability {subject.stability_at_exam:.0f} of {subject.target:.0f} days",
        delta_color="normal" if subject.ready else "inverse",
    )
for warning in plan.warnings:
    st.warning(warning)

st.subheader("Sessions")
st.dataframe(
    pd.DataFrame(service.session_rows(plan)), use_container_width=True, hide_index=True, key="sessions"
)
st.download_button("Download plan.ics", service.export_ics(plan), file_name="plan.ics", mime="text/calendar")

st.subheader("Predicted recall")
curves = pd.DataFrame(
    [
        {"day": d, "recall": p, "subject": s.name}
        for s in plan.subjects
        for d, p in service.recall_curve(plan, s.name)
    ]
)
marks = pd.DataFrame([{"day": x.start_day, "recall": x.recall, "subject": x.subject} for x in plan.sessions])
chart = (
    alt.Chart(curves)
    .mark_line()
    .encode(
        x=alt.X("day", title="days from the start"),
        y=alt.Y("recall", scale=alt.Scale(domain=[0, 1])),
        color="subject",
    )
)
if not marks.empty:
    chart += alt.Chart(marks).mark_point(filled=True, size=70).encode(x="day", y="recall", color="subject")
st.altair_chart(chart, use_container_width=True)

st.subheader("Week by week")
week = st.selectbox("Week", range(service.week_count(plan)), format_func=lambda w: f"Week {w + 1}")
st.dataframe(
    pd.DataFrame(service.week_view(plan, week)), use_container_width=True, hide_index=True, height=420
)

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
