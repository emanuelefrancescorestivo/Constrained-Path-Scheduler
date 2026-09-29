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


def test_the_horizon_runs_to_the_last_assessment_and_includes_its_day(calendar, capsys):
    """The exam is at 09:00 on day 18 (index from 0), so the horizon covers 19
    days. Before per-subject exams it stopped at the earliest assessment and
    rounded down, which dropped the morning of an afternoon exam."""
    main(["inspect", str(calendar), *BASE])
    assert "plus 19 days" in capsys.readouterr().out


def test_plan_runs_and_states_what_it_does_not_know(calendar, capsys):
    """The notice about AUDIT.md item 20 is gone with the defect. What stays is
    the honest part: population parameters and self-assessed starting points."""
    assert main(["plan", str(calendar), *BASE, "--subject", "Analysis:2:7", "--window", "3"]) == 0
    out = capsys.readouterr().out
    assert "blocks used" in out
    assert "AUDIT.md item 20" not in out
    assert "population-default" in out and "your" in out


def test_each_subject_can_carry_its_own_exam_date(calendar, capsys):
    """Analysis takes its date from the calendar, Algebra from the argument."""
    code = main(
        [
            "plan",
            str(calendar),
            *BASE,
            "--subject",
            "Analysis:2:7",
            "--subject",
            "Algebra:4:5@2026-03-27",
            "--window",
            "3",
            "--whole-subjects",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Analysis           exam on day  18.4, target stability 18 days" in out
    assert "Algebra            exam on day  25.0, target stability 25 days" in out
    assert "horizon 25 days" in out


def test_a_subject_with_lectures_in_the_calendar_is_planned_week_by_week(calendar, capsys):
    """Without --whole-subjects, Analysis's weekly lectures become topics: what was
    taught before the start, then one per week, each studied after it is taught."""
    assert main(["plan", str(calendar), *BASE, "--subject", "Analysis:2:7", "--window", "3"]) == 0
    out = capsys.readouterr().out
    assert "topics, one per week of lectures" in out
    assert "Analysis · week of" in out
    assert "topics ready" in out


def test_a_subject_without_an_exam_date_is_refused_with_a_remedy(calendar, capsys):
    assert main(["plan", str(calendar), *BASE, "--subject", "Physics:3:6"]) == 1
    err = capsys.readouterr().err
    assert "No exam date for 'Physics'" in err and "@" in err


def test_an_exam_date_before_the_plan_is_refused(calendar, capsys):
    assert main(["plan", str(calendar), *BASE, "--subject", "Algebra:4:5@2026-03-01"]) == 1
    assert "is not after" in capsys.readouterr().err


def test_a_malformed_exam_date_is_rejected(calendar):
    with pytest.raises(SystemExit):
        main(["plan", str(calendar), *BASE, "--subject", "Algebra:4:5@next-friday"])


def test_plan_writes_an_importable_calendar(calendar, tmp_path, capsys):
    out_path = tmp_path / "plan.ics"
    main(["plan", str(calendar), *BASE, "--subject", "Analysis:2:7", "--window", "3", "--out", str(out_path)])
    text = out_path.read_text(encoding="utf-8")
    assert text.startswith("BEGIN:VCALENDAR") and "Study: Analysis" in text


def test_subjects_default_to_the_detected_assessments(calendar, capsys):
    assert main(["plan", str(calendar), *BASE, "--window", "3"]) == 0
    assert "Analysis" in capsys.readouterr().out


def test_a_study_window_that_leaves_no_time_fails_loudly(calendar, capsys):
    code = main(["plan", str(calendar), *BASE, "--study-window", "9-10", "--subject", "Analysis:2:7"])
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
    main(["plan", str(calendar), *BASE, "--subject", "Analysis:2:7", "--window", "3", "--out", str(out_path)])
    data = out_path.read_bytes()
    assert data.startswith(b"BEGIN:VCALENDAR\r\n")
    assert b"\r\r\n" not in data


# --------------------------------------------------------------------------- #
# A reader that leaves early (AUDIT.md item 23)
# --------------------------------------------------------------------------- #


def test_a_closed_stdout_is_a_normal_exit(calendar, monkeypatch):
    """`cps inspect cal.ics | head -4` used to end in a BrokenPipeError traceback."""
    import sys

    class ClosedPipe:
        encoding = "utf-8"

        def write(self, text):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

        def fileno(self):
            return sys.__stdout__.fileno()

    monkeypatch.setattr(sys, "stdout", ClosedPipe())
    monkeypatch.setattr("cps.cli._silence_stdout", lambda: None)
    assert main(["inspect", str(calendar), *BASE]) == 0


def test_a_closed_pipe_prints_no_traceback(calendar):
    """The same through a real pipe, closed before the program writes a byte.
    On Windows a closed pipe can surface as OSError EINVAL instead; CI runs this
    there too."""
    import subprocess
    import sys

    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from cps.cli import main; sys.exit(main(sys.argv[1:]))",
            "inspect",
            str(calendar),
            *BASE,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    process.stdout.close()
    _, err = process.communicate(timeout=120)
    assert b"Traceback" not in err and b"Exception ignored" not in err, err.decode(errors="replace")
    assert process.returncode == 0


def test_a_plan_that_cannot_be_written_is_an_error_not_a_silent_exit(calendar, tmp_path, capsys):
    """Only a closed stdout is forgiven. A failed write of --out is reported."""
    missing = tmp_path / "no-such-directory" / "plan.ics"
    code = main(
        ["plan", str(calendar), *BASE, "--subject", "Analysis:2:7", "--window", "3", "--out", str(missing)]
    )
    assert code == 1
    assert "could not write" in capsys.readouterr().err
