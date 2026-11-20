"""
Reading the settings form. Input handling only: every value is checked by
`cps.service`, which says what is wrong in a sentence the page shows.

The form has no JavaScript: lists (exams, deadlines, activities) are rows named
`s0_name`, `s0_exam`, ..., with a few empty rows to add to, and a "remove" box on
each. A row without a name is ignored.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time
from typing import Any

from cps import service

EMPTY_ROWS = 2
# The value of "Other…" in the new-task sheet's list of courses (D159), chosen so that it
# does not read as a course's name.
OTHER_COURSE = "*other"


def chosen_course(form: Mapping[str, object]) -> str:
    """The course a form names (D159): a course typed in the "Other…" field wins over the
    one picked, as on the share forms (D141), since typing it was the later act; "Other…"
    picked with nothing typed is no course."""
    other = str(form.get("course_other") or "").strip()
    picked = str(form.get("course") or "").strip()
    return other or ("" if picked == OTHER_COURSE else picked)


def _rows(form: Mapping[str, str], prefix: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    rows = []
    index = 0
    while any(f"{prefix}{index}_{f}" in form for f in fields):
        row = {f: str(form.get(f"{prefix}{index}_{f}", "")).strip() for f in fields}
        if not form.get(f"{prefix}{index}_remove"):
            rows.append(row)
        index += 1
    return rows


def _number(value: str, name: str) -> float:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        raise service.InvalidInput(
            service.translate("{name}: {value} is not a number", name=name, value=repr(value))
        ) from None


def settings_values(form: Mapping[str, str], rest_days: list[str]) -> dict[str, Any]:
    """The submitted form as the same plain values `service.setup_view` gives, so
    that a form with a mistake is shown again as it was typed."""
    return {
        "subjects": [
            {
                "name": r["name"],
                "exam": r["exam"].replace("T", " "),
                "found": r["found"] == "1",
                "found_when": r["found_when"],
                "familiarity": r["familiarity"] or service.DEFAULT_FAMILIARITY,
                "suggested": r["suggested"] == "1",
                "practice": r["practice"],
                "weight": r["weight"] or "1.0",
            }
            for r in _rows(
                form,
                "s",
                ("name", "exam", "found", "found_when", "familiarity", "suggested", "practice", "weight"),
            )
            if r["name"]
        ],
        "tasks": [r for r in _rows(form, "t", ("name", "course", "due", "hours")) if r["name"]],
        "activities": [
            {
                "label": r["label"],
                "kind": r["kind"] or "other",
                "weekday": "" if r["date"] else r["weekday"],
                "date": r["date"],
                "start": r["start"],
                "end": r["end"],
            }
            for r in _rows(form, "a", ("label", "kind", "weekday", "date", "start", "end"))
            if r["label"] or r["start"]
        ],
        "deadlines_url": form.get("deadlines_url", "").strip(),
        "weekly_hours": form.get("weekly_hours", "").strip(),
        "rest_days": rest_days,
        "practice_hours": form.get("practice_hours", "").strip(),
        "study_window": [form.get("from_hour", "8"), form.get("to_hour", "22")],
        # When the student gets up and goes to bed, and lunch (D69): the study
        # window is made from them, when the form asked.
        "day": _lunch_picked(
            {k: form.get(k, "") for k in ("wake", "bed", "lunch", "lunch_start", "lunch_end")}, form
        )
        if form.get("wake") is not None
        else None,
        "blocks_per_day": form.get("blocks_per_day", "2"),
        "block_minutes": form.get("block_minutes", "90"),
        # Absent while setting up: the next step asks about the trip (D42) and when
        # the student studies best (D45).
        "commute": form.get("commute"),
        "study_time": form.get("study_time"),
        # Check boxes say nothing when unticked: each is read only when its form
        # showed it (D48).
        "quick_tests": bool(form.get("quick_tests")) if form.get("quick_shown") else None,
        "warm_up": bool(form.get("warm_up")) if form.get("quick_shown") else None,
        "study_on_the_way": bool(form.get("study_on_the_way")) if form.get("commute") is not None else None,
    }


def _lunch_picked(day: dict[str, str], form: Mapping[str, str]) -> dict[str, str]:
    """Lunch as setting up's sentence offers it (D154): a time to start, kept free for as
    long as lunch was, or "study" for studying then; elsewhere the form's own fields."""
    pick = str(form.get("lunch_pick") or "")
    if not pick:
        return day
    if pick == "study":
        return {**day, "lunch": "study"}
    try:
        start, before, end = (time.fromisoformat(v) for v in (pick, day["lunch_start"], day["lunch_end"]))
    except ValueError:
        return day  # the service says what is wrong with the times
    length = (end.hour * 60 + end.minute) - (before.hour * 60 + before.minute)
    finish = (start.hour * 60 + start.minute + (length if length > 0 else 60)) % (24 * 60)
    return {**day, "lunch": "free", "lunch_start": pick, "lunch_end": f"{finish // 60:02d}:{finish % 60:02d}"}


def quick_busy(form: Any) -> list[dict[str, Any]]:
    """The busy times added in setting up's sentence (D154), as the questions about the
    week take them (`service.routine_rows`): a kind, a name, the days, the hours. Their
    hours come filled in, so one with no day chosen is no answer. Since D172 each holds
    up to `service.QUICK_SHIFTS` shifts, `q{i}` then `q{i}_{k}`, each its own days and
    hours, named as the first unless given a name of its own (another job)."""
    answers = []
    index = 0
    while f"q{index}_kind" in form and index < 10:
        kind = str(form.get(f"q{index}_kind") or "")
        name = str(form.get(f"q{index}_label") or "").strip()
        for shift in range(service.QUICK_SHIFTS):
            prefix = f"q{index}" if shift == 0 else f"q{index}_{shift}"
            days = [str(d) for d in form.getlist(f"{prefix}_days")]
            if not days:
                continue
            own = str(form.get(f"{prefix}_label") or "").strip() if shift else ""
            answers.append(
                {
                    "kind": kind,
                    "label": own or name,
                    "days": days,
                    "start": str(form.get(f"{prefix}_start") or "").strip(),
                    "end": str(form.get(f"{prefix}_end") or "").strip(),
                }
            )
        index += 1
    return answers


def weekly_busy(form: Any) -> list[dict[str, Any]]:
    """Settings' weekly busy times (D173), one card each, `b{i}`: its name, kind, days and
    hours, back to one row a day as they are kept. A card with Remove ticked, or with no
    day left, is gone; one that ends when it starts is refused, as in setting up (D172),
    where it would take the whole day."""
    rows = []
    index = 0
    while f"b{index}_start" in form and index < 60:
        prefix = f"b{index}"
        index += 1
        if form.get(f"{prefix}_remove"):
            continue
        label = str(form.get(f"{prefix}_label") or "").strip()
        start = str(form.get(f"{prefix}_start") or "").strip()
        end = str(form.get(f"{prefix}_end") or "").strip()
        days = list(dict.fromkeys(str(d) for d in form.getlist(f"{prefix}_days")))
        if days and start and start == end:
            raise service.InvalidInput(
                service.translate("{name}: it ends when it starts", name=label or "Busy")
            )
        rows += [
            {
                "label": label,
                "kind": str(form.get(f"{prefix}_kind") or "other"),
                "weekday": day,
                "date": "",
                "start": start,
                "end": end,
            }
            for day in days
        ]
    return rows


def _subjects(rows: list[Mapping[str, Any]]) -> list[service.SubjectSpec]:
    subjects = []
    for s in rows:
        if s.get("suggested") and not s["exam"]:
            continue  # a class drawn by hand, offered with its name: no date, no exam (D32)
        if s["found"] and not s["exam"]:
            exam = None  # follow the timetable
        elif s["exam"]:
            exam = s["exam"]
        else:
            raise service.InvalidInput(service.translate("{name}: give the exam's date", name=s["name"]))
        subjects.append(service.SubjectSpec(s["name"], exam, familiarity=int(s["familiarity"])))
    return subjects


def _per_exam(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Each exam's own practice hours (D46) and, only for an exam that does not weigh
    as usual, its weight (D49)."""
    return {
        "practice": {
            s["name"]: _number(str(s["practice"]), s["name"])
            for s in rows
            if str(s.get("practice") or "").strip()
        },
        "weights": {
            s["name"]: _number(str(s.get("weight") or "1"), s["name"])
            for s in rows
            if _number(str(s.get("weight") or "1"), s["name"]) != 1.0
        },
    }


def revision(values: Mapping[str, Any]) -> dict[str, Any]:
    """The keyword arguments `service.revise_subscription` takes."""
    subjects = _subjects(values["subjects"])
    tasks = []
    for t in values["tasks"]:
        if not t["due"]:
            raise service.InvalidInput(service.translate("{name}: give the date it is due", name=t["name"]))
        hours = _number(t["hours"] or "2", t["name"])
        tasks.append(service.TaskSpec(t["name"], t["due"].replace("T", " "), hours, t["course"]))
    weekly = values["weekly_hours"]
    weekly_hours = _number(str(weekly), service.translate("weekly hours")) if str(weekly).strip() else None
    return {
        "subjects": subjects,
        "tasks": tasks,
        "busy_rows": [{**a, "date": a["date"] or None} for a in values["activities"]],
        "deadlines_url": values["deadlines_url"],
        "preferences": {
            "weekly_hours": weekly_hours or None,
            "rest_days": list(values["rest_days"]),
            # Empty: sized by each course (D46).
            "practice_hours": _number(str(values["practice_hours"]), service.translate("exam practice"))
            if str(values["practice_hours"]).strip()
            else None,
            **_per_exam(values["subjects"]),
        },
        **(
            {"day": values["day"]}
            if values.get("day") is not None
            else {
                "study_window": (
                    _number(str(values["study_window"][0]), service.translate("Study from")),
                    _number(str(values["study_window"][1]), service.translate("until")),
                )
            }
        ),
        "blocks_per_day": int(
            _number(str(values["blocks_per_day"]), service.translate("Sessions a day at most"))
        ),
        "block_minutes": int(_number(str(values["block_minutes"]), service.translate("Session length"))),
        "commute_minutes": None
        if values.get("commute") in (None, "")
        else int(_number(str(values["commute"]), service.translate("Trip to university"))),
        "study_time": values.get("study_time") or None,
        "quick_tests": values.get("quick_tests"),
        "warm_up": values.get("warm_up"),
        "study_on_the_way": values.get("study_on_the_way"),
    }


def routine_answers(form: Any) -> tuple[int, list[dict[str, Any]]]:
    """The questions about the week (D42): the trip's minutes and the answers, as
    `service.answer_routine` takes them (when the student studies best, D45, is the
    form's `study_time`). `form` is the request's form, whose day boxes come as
    several values under one name."""
    raw = str(form.get("commute") or "0")
    commute = int(_number(raw, service.translate("Trip to university")))
    answers = []
    index = 0
    while f"x{index}_kind" in form and index < 20:
        answers.append(
            {
                "kind": str(form.get(f"x{index}_kind") or ""),
                "label": str(form.get(f"x{index}_label") or "").strip(),
                "days": [str(d) for d in form.getlist(f"x{index}_days")],
                "start": str(form.get(f"x{index}_start") or "").strip(),
                "end": str(form.get(f"x{index}_end") or "").strip(),
            }
        )
        index += 1
    return commute, answers


# D103: Settings is a list of pages, and a page saves only what it shows. Each part is
# the keyword arguments of `service.revise_subscription` it may change; the others are
# left out, which leaves them as they are. Rejected: posting every setting from every
# page as hidden fields, which a change made elsewhere meanwhile would overwrite.
WEEK_CHANGES = (
    "busy_rows",
    "day",
    "study_window",
    "blocks_per_day",
    "block_minutes",
    "commute_minutes",
    "study_time",
    "quick_tests",
    "warm_up",
    "study_on_the_way",
)
WEEK_PREFERENCES = ("weekly_hours", "rest_days", "practice_hours")


def week_revision(values: Mapping[str, Any]) -> dict[str, Any]:
    """Your week (D103): the hours, days off, the day, the sessions, exam practice
    before every exam, the self-tests, the trip and when the student is not free. Each
    exam's own practice and weight are on its course's page, so they are not touched."""
    change = revision({**values, "subjects": [], "tasks": []})
    out = {k: change[k] for k in WEEK_CHANGES if k in change}
    out["preferences"] = {k: change["preferences"][k] for k in WEEK_PREFERENCES}
    return out


def deadlines_revision(values: Mapping[str, Any]) -> dict[str, Any]:
    """The deadlines (D103): the learning platform's link and the tasks."""
    change = revision({**values, "subjects": [], "activities": []})
    return {"tasks": change["tasks"], "deadlines_url": change["deadlines_url"]}


def exam_revision(
    stored: list[Mapping[str, Any]], rows: list[Mapping[str, Any]], *, course: str | None = None
) -> dict[str, Any]:
    """The exams with one changed (D103): `course`'s row, as `service.setup_view` gives
    them, replaced by `rows` (none removes it), or `rows` added when `course` is None.
    Each exam's practice and weight are worked out again from all of them."""
    if course is None:
        merged = [*stored, *rows]
    else:
        merged = []
        for row in stored:
            merged.extend(rows if str(row["name"]).casefold() == course.casefold() else [row])
    return {"subjects": _subjects(merged), "preferences": _per_exam(merged)}
