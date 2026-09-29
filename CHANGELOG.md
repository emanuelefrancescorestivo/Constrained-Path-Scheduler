# Changelog

## Unreleased

### Fixed
- Exams are found in a real university timetable export (ADE / Hyperplanning
  style, `Course, Grp: EXAMEN ., Salle: Room`), tested on the owner's own:
  keywords match whole words, longest first (AUDIT.md item 29); the subject is the
  course name, not the whole title (item 30); cancelled classes no longer block
  time (item 31); assessments are searched for over a year, not 120 days, so the
  end of a semester is found (item 32).

## 0.3.0 (2026-09-29)

The planner's timing can be trusted, a web page and a service layer exist, and
the quality bar is enforced by CI on Ubuntu and Windows.

### Fixed
- The rolling planner procrastinated: its continuation counted remaining blocks,
  not remaining days. It now uses a value function with a clock, per subject
  (`clock.py`). First review of a weak subject: day 16 to 19 of 21 before, day 2.3
  after; both subjects ready in 35 to 38% of runs before, 90% ± 3% after
  (AUDIT.md item 20).
- State aggregation could carry a stability across its target, which also
  invalidated a published ten-day result (item 26).
- The memory model now matches FSRS-4.5 exactly: a post-lapse clamp that
  FSRS-4.5 does not have was removed (item 27). The old reference sequence
  "4, 14, 44, 125" was not FSRS-4.5's; the test is now exact against py-fsrs 2.5.1.
- The heuristic-grade SSP solve is below the analysis solve in every cell (item 22).
- `cps inspect ... | head` no longer prints a traceback (item 23).
- The exported calendar defines the time zone it uses (item 24).
- A console test assumed Linux line endings (item 25).

### Added
- Per-subject exam dates in the planner, the service, the CLI
  (`--subject "Name:S:D@2026-03-20"`) and the web page; a subject the calendar
  cannot prepare for is reported instead of scheduled.
- `cps.service`: analyse, plan, replan after a lapse or a skip, export, recall
  curve; JSON results; typed errors.
- `app.py`, a Streamlit page over the service, and hand-typed timetables
  (`calendar_io.busy_from_table`).
- Benchmarks against a fixed-0.90 scheduler and every-k scheduling, a calibration
  script, the design spike, and the py-fsrs reference script.
- `docs/REFERENCES.md`, checked, with a test; `docs/ARCHITECTURE.md`;
  `docs/WRITEUP.md`.
- CI on Ubuntu and Windows for Python 3.11 to 3.13; ruff, mypy, coverage; slow
  tests marked.

### Changed
- The CLI's horizon runs to the latest exam and includes its day.
- "Esame di Fisica" is found as the subject "Fisica", not "di Fisica".

### Withdrawn
- The claim that the optimal retention lies in "the band the FSRS literature
  reports": no source could be found (item 28).
- "19% fewer reviews than fixed retention" now comes with its context: 13% fewer
  than the best fixed retention, which is a sawtooth.

## 0.2.0

The rebuild of the January 2026 submission, imported as the baseline of this
repository: FSRS-4.5, SSP-MMC value iteration, AO* with verified admissible
heuristics, calendar ingestion and export, the CLI, and the audit.

## December 2025 prototype

The Streamlit prototype with greedy bin-packing and an A* attempt, kept in
`archive/2025-prototype/`.
