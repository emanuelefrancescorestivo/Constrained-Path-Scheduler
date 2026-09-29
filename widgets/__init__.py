"""
Front-end widgets for app.py. Layout and input only: nothing here knows about
memory, exams or plans, and the planner never imports this package.

`week_calendar` draws one week. Editable, it lets a person drag out their own
activities (training, commutes, work, time off) and returns them as busy rows,
the shape `cps.service.analyse_calendar` takes as `busy_rows`. Read-only, it
shows a plan's week. It is a Streamlit v2 component written in plain JavaScript,
so it needs no build step and no dependency beyond Streamlit itself.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pandas as pd
import streamlit as st

from cps import service

_HERE = Path(__file__).parent

# What a person can block out. The planner treats every kind as busy; the kind
# only colours the block and names it until the person types a name.
KINDS: tuple[tuple[str, str], ...] = (
    ("training", "Training"),
    ("commute", "Commute"),
    ("work", "Work"),
    ("timeoff", "Time off"),
    ("other", "Other"),
)

# What "I have no calendar file" starts from, so the page is not empty.
EXAMPLE_WEEK: tuple[dict, ...] = (
    {"label": "Lectures", "kind": "other", "weekday": "Mon", "date": None, "start": "09:00", "end": "13:00"},
    {"label": "Lectures", "kind": "other", "weekday": "Wed", "date": None, "start": "09:00", "end": "13:00"},
    {"label": "Job", "kind": "work", "weekday": "Fri", "date": None, "start": "14:00", "end": "19:00"},
)
_COLUMNS = ["label", "kind", "weekday", "date", "start", "end"]
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_JS = (_HERE / "week_calendar.js").read_text(encoding="utf-8")
_CSS = (_HERE / "week_calendar.css").read_text(encoding="utf-8")
_MOUNTED: list[tuple[object, Callable[..., object]]] = []


def _component() -> Callable[..., object]:
    """The component, registered once per Streamlit runtime. Registering at import
    time is not enough: a module is imported once per process, and a test harness
    (or a restarted server in the same process) starts a new runtime with an
    empty registry."""
    registry = st.components.v2.get_bidi_component_manager()
    for known, mount in _MOUNTED:
        if known is registry:
            return mount
    mount = st.components.v2.component("cps_week_calendar", js=_JS, css=_CSS)
    _MOUNTED.append((registry, mount))
    return mount


def week_calendar(
    week: dict,
    *,
    key: str,
    activities: Sequence[dict] = (),
    editable: bool = False,
    hours: tuple[int, int] = (7, 23),
    study_window: tuple[float, float] = (0, 24),
    on_edit: Callable[[], None] | None = None,
) -> None:
    """Draw `week` (from `service.calendar_week`) with `activities` on top.

    When editable, every change sends the full list of activities as the
    component's "edit" trigger and calls `on_edit`, which reads it from
    `st.session_state[key]["edit"]`.
    """
    _component()(
        key=key,
        data={
            "editable": editable,
            "days": week["days"],
            "items": week["items"],
            "activities": [dict(a) for a in activities],
            "kinds": [{"id": k, "label": label} for k, label in KINDS],
            "hours": list(hours),
            "window": list(study_window),
        },
        on_edit_change=on_edit or (lambda: None),
    )


def activity_table(activities: Sequence[dict], *, key: str) -> list[dict] | None:
    """The activities as an editable table, in an expander: the same data as the
    calendar, for someone using a keyboard or a screen reader. Returns the new
    list when the person changed something, else None."""
    with st.expander("The same activities as a table, for the keyboard or a screen reader"):
        table = st.data_editor(
            pd.DataFrame(list(activities), columns=_COLUMNS),
            num_rows="dynamic",
            use_container_width=True,
            # A new key whenever the calendar changed the rows, so the table
            # starts from them instead of replaying its own earlier edits.
            key=f"{key}-{hash(repr(list(activities)))}",
            column_config={
                "kind": st.column_config.SelectboxColumn(options=[k for k, _ in KINDS]),
                "weekday": st.column_config.SelectboxColumn(options=_WEEKDAYS),
                "date": st.column_config.TextColumn(help="YYYY-MM-DD for a one-off block, else empty"),
                "start": st.column_config.TextColumn(help="HH:MM"),
                "end": st.column_config.TextColumn(help="HH:MM; earlier than the start runs past midnight"),
            },
        )
    rows = [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in table.to_dict("records")]
    rows = [r for r in rows if r.get("start") and r.get("end")]
    return rows if rows != list(activities) else None


SOURCES = ("Upload an .ics file", "Paste a calendar link", "Sample calendar", "I have no calendar file")


def calendar_source(sample: Path) -> tuple[str, bytes | None, str | None]:
    """Where the timetable comes from: (choice, the calendar's bytes or None, the
    link or None). A link is read once and kept until it changes."""
    source = st.radio("Where is your timetable?", SOURCES, horizontal=True)
    if source == "Upload an .ics file":
        upload = st.file_uploader("Calendar export (.ics)", type=["ics"])
        st.caption("Google Calendar: Settings, Import and export, Export. Apple Calendar: File, Export.")
        return source, (upload.getvalue() if upload else None), None
    if source == "Paste a calendar link":
        link = st.text_input("Calendar link", placeholder="https://… or webcal://…").strip()
        st.caption(
            "Your university timetable's export or subscription address (ADE, Hyperplanning), or "
            "Google Calendar's secret address in iCal format (Settings, your calendar, Integrate "
            "calendar). A plan made from a link can follow the timetable when it changes."
        )
        if not link:
            return source, None, None
        cached = st.session_state.get("link-calendar")
        if not cached or cached[0] != link:
            try:
                cached = (link, service.fetch_calendar(link))
            except service.ServiceError as error:
                st.error(str(error))
                st.stop()
            st.session_state["link-calendar"] = cached
        return source, cached[1], link
    if source == "Sample calendar":
        st.caption(
            "A synthetic timetable: lectures, gym, a weekend away, and an Analysis exam on "
            "20 March 2026. Start the plan on 2 March 2026 in Europe/Rome to see it."
        )
        return source, sample.read_bytes(), None
    st.caption('Draw your week below. Name a one-off block "Physics exam" and it is found as an exam.')
    return source, None, None


def feed_panel(plan: service.PlanReport, *, link: str | None, ics: bytes | None, inputs: dict) -> None:
    """Publish the plan as a calendar subscription, or stop publishing it. `inputs`
    are the keyword arguments `service.new_subscription` needs besides the source."""
    st.subheader("Keep it in your calendar")
    st.caption(
        "Publish the plan as a calendar feed: your calendar app reads it again every few hours, "
        + ("and the plan follows your timetable's link as it changes. " if link else "")
        + "Sessions behind you count as done as planned: a session reported below as missed "
        "changes this page's plan, not the feed."
    )
    token = st.session_state.get("feed-token")
    if token and service.load_subscription(service.FEED_STORE, token) is None:
        token = None
    if token is None and st.button("Publish as a calendar feed"):
        subscription = service.new_subscription(source_url=link, ics=ics, plan=plan, **inputs)
        service.save_subscription(service.FEED_STORE, subscription)
        st.session_state["feed-token"] = subscription.token
        st.rerun()
    if token is None:
        return
    url = service.feed_url(service.FEED_URL, token)
    st.code(url, language=None)
    st.caption(
        "Google Calendar: Other calendars, +, From URL. Apple Calendar: File, New Calendar "
        "Subscription. Outlook: Add calendar, Subscribe from web. Notion Calendar: subscribe in "
        "the Google or Apple calendar it shows (not tested here). Anyone with this address can "
        "read the plan."
    )
    if service.FEED_URL.startswith(("http://localhost", "http://127.")):
        st.warning(
            "This address only works on this computer, while `cps serve` runs. Google Calendar "
            "and Notion Calendar read feeds from Google's servers, so they need the feed server "
            "on the internet: see Publishing feeds in the README."
        )
    if st.button("Stop publishing"):
        service.delete_subscription(service.FEED_STORE, token)
        st.session_state.pop("feed-token", None)
        st.rerun()
