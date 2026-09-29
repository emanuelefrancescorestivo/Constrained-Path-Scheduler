"""
Reading the settings form. Input handling only: every value is checked by
`cps.service`, which says what is wrong in a sentence the page shows.

The form has no JavaScript: lists (exams, deadlines, activities) are rows named
`s0_name`, `s0_exam`, ..., with a few empty rows to add to, and a "remove" box on
each. A row without a name is ignored.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cps import service

EMPTY_ROWS = 2


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
        raise service.InvalidInput(f"{name}: {value!r} is not a number") from None


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
            }
            for r in _rows(form, "s", ("name", "exam", "found", "found_when", "familiarity"))
            if r["name"]
        ],
        "tasks": [r for r in _rows(form, "t", ("name", "course", "due", "hours")) if r["name"]],
        "activities": [
            {
                "label": r["label"],
                "weekday": "" if r["date"] else r["weekday"],
                "date": r["date"],
                "start": r["start"],
                "end": r["end"],
            }
            for r in _rows(form, "a", ("label", "weekday", "date", "start", "end"))
            if r["label"] or r["start"]
        ],
        "weekly_hours": form.get("weekly_hours", "").strip(),
        "rest_days": rest_days,
        "practice_hours": form.get("practice_hours", "").strip(),
        "study_window": [form.get("from_hour", "8"), form.get("to_hour", "22")],
        "blocks_per_day": form.get("blocks_per_day", "2"),
        "block_minutes": form.get("block_minutes", "90"),
    }


def revision(values: Mapping[str, Any]) -> dict[str, Any]:
    """The keyword arguments `service.revise_subscription` takes."""
    subjects = []
    for s in values["subjects"]:
        if s["found"] and not s["exam"]:
            exam = None  # follow the timetable
        elif s["exam"]:
            exam = s["exam"]
        else:
            raise service.InvalidInput(f"{s['name']}: give the exam's date")
        subjects.append(service.SubjectSpec(s["name"], exam, familiarity=int(s["familiarity"])))
    tasks = []
    for t in values["tasks"]:
        if not t["due"]:
            raise service.InvalidInput(f"{t['name']}: give the date it is due")
        hours = _number(t["hours"] or "2", t["name"])
        tasks.append(service.TaskSpec(t["name"], t["due"].replace("T", " "), hours, t["course"]))
    weekly = values["weekly_hours"]
    weekly_hours = _number(str(weekly), "weekly hours") if str(weekly).strip() else None
    return {
        "subjects": subjects,
        "tasks": tasks,
        "busy_rows": [{**a, "date": a["date"] or None} for a in values["activities"]],
        "preferences": {
            "weekly_hours": weekly_hours or None,
            "rest_days": list(values["rest_days"]),
            "practice_hours": _number(str(values["practice_hours"] or 0), "exam practice"),
        },
        "study_window": (
            _number(str(values["study_window"][0]), "from"),
            _number(str(values["study_window"][1]), "to"),
        ),
        "blocks_per_day": int(_number(str(values["blocks_per_day"]), "blocks a day")),
        "block_minutes": int(_number(str(values["block_minutes"]), "block length")),
    }
