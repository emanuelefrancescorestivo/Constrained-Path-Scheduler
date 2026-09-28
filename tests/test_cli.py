"""Tests for the command line interface.

The CLI is the only place a real calendar meets the solver, so what is checked
here is that it fails loudly rather than quietly: an unusable study window, a
calendar with nothing to schedule, a malformed argument.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cps.cli import main

TIMETABLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:l1@t
SUMMARY:Analysis lecture
DTSTART;TZID=Europe/Rome:20260302T090000
DTEND;TZID=Europe/Rome:20260302T110000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20260401T235959Z
END:VEVENT
BEGIN:VEVENT
UID:e1@t
SUMMARY:Analysis exam
DTSTART;TZID=Europe/Rome:20260320T090000
DTEND;TZID=Europe/Rome:20260320T120000
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture()
def calendar(tmp_path: Path) -> Path:
    path = tmp_path / "calendar.ics"
    path.write_text(TIMETABLE, encoding="utf-8")
    return path


BASE = ["--from", "2026-03-02", "--tz", "Europe/Rome"]


def test_inspect_reports_what_ingestion_saw(calendar, capsys):
    assert main(["inspect", str(calendar), *BASE]) == 0
    out = capsys.readouterr().out
    assert "recurrences expanded" in out
    assert "study blocks" in out
    assert "Analysis" in out, "the exam should be detected and named"


def test_the_horizon_defaults_to_the_first_assessment(calendar, capsys):
    main(["inspect", str(calendar), *BASE])
    assert "plus 18 days" in capsys.readouterr().out


def test_plan_runs_and_states_its_known_defect(calendar, capsys):
    assert main(["plan", str(calendar), *BASE, "--subject", "Analysis:2:7", "--window", "3"]) == 0
    out = capsys.readouterr().out
    assert "blocks used" in out
    assert "AUDIT.md item 20" in out, "a plan that is known to be late must say so"


def test_plan_writes_an_importable_calendar(calendar, tmp_path, capsys):
    out_path = tmp_path / "plan.ics"
    main(["plan", str(calendar), *BASE, "--subject", "Analysis:2:7", "--window", "3",
          "--out", str(out_path)])
    text = out_path.read_text(encoding="utf-8")
    assert text.startswith("BEGIN:VCALENDAR") and "Study: Analysis" in text


def test_subjects_default_to_the_detected_assessments(calendar, capsys):
    assert main(["plan", str(calendar), *BASE, "--window", "3"]) == 0
    assert "Analysis" in capsys.readouterr().out


def test_a_study_window_that_leaves_no_time_fails_loudly(calendar, capsys):
    code = main(["plan", str(calendar), *BASE, "--study-window", "9-10",
                 "--subject", "Analysis:2:7"])
    assert code == 1
    assert "widen the study window" in capsys.readouterr().err


def test_a_malformed_study_window_is_rejected(calendar):
    with pytest.raises(SystemExit):
        main(["inspect", str(calendar), "--study-window", "morning"])


def test_a_subject_without_a_name_is_rejected(calendar):
    with pytest.raises(SystemExit):
        main(["plan", str(calendar), *BASE, "--subject", ":2:7"])


def test_a_calendar_saved_with_a_bom_is_read(tmp_path, capsys):
    path = tmp_path / "bom.ics"
    path.write_bytes(b"\xef\xbb\xbf" + TIMETABLE.encode("utf-8"))
    assert main(["inspect", str(path), *BASE]) == 0
    assert "recurrences expanded" in capsys.readouterr().out


def test_the_plan_is_written_in_binary_mode(calendar, tmp_path, monkeypatch, capsys):
    """icalendar emits CRLF line endings. A text-mode write on Windows translates
    every LF to CRLF again, so the file ends up with CR CR LF and stricter calendar
    importers reject it. Writing bytes is immune, and forbidding write_text here
    makes the guard effective on Linux, where the corruption cannot be reproduced.
    """
    from pathlib import Path

    def forbidden(self, *args, **kwargs):
        raise AssertionError("an .ics must not be written through text mode")

    monkeypatch.setattr(Path, "write_text", forbidden)
    out_path = tmp_path / "plan.ics"
    main(["plan", str(calendar), *BASE, "--subject", "Analysis:2:7", "--window", "3",
          "--out", str(out_path)])
    data = out_path.read_bytes()
    assert data.startswith(b"BEGIN:VCALENDAR\r\n")
    assert b"\r\r\n" not in data
