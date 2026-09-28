"""
Command line interface.

Two subcommands, deliberately separated by how much you can trust them.

`inspect` reads a calendar and reports what the ingestion layer saw: expanded
occurrences, busy hours, candidate study blocks, detected assessments. This part
is finished and tested, and it is the useful thing to run against your own
export first — if the free blocks it lists are wrong, nothing downstream can be
right.

`plan` runs the whole pipeline and prints a schedule. Each subject is planned
towards its own exam: pass the date with `--subject "Analysis:2:7@2026-03-20"`, or
leave it out and the date of the matching assessment found in the calendar is
used. The planner reports a subject whose exam the calendar cannot prepare for
instead of scheduling it anyway.

What it does not know, and says so in its output: the memory model uses
population-default FSRS parameters, and each subject's starting stability and
difficulty are your own guesses.
"""

from __future__ import annotations

import argparse
import sys
import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from .calendar_io import decode_ics, find_deadlines, load_availability, plan_to_ics
from .console import ensure_utf8_output
from .memory import MemoryState
from .plan import tile_free_time
from .rolling import DEFAULT_FAILURE_PENALTY, Subject, run_rolling

DEFAULT_STABILITY = 2.0
DEFAULT_DIFFICULTY = 6.0


def _study_window(text: str) -> tuple[float, float]:
    try:
        earliest, latest = (float(part) for part in text.split("-", 1))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use HH-HH, for example 8-22") from exc
    return earliest, latest


def _subject(text: str) -> tuple[str, MemoryState, datetime | None]:
    """`Name[:stability[:difficulty]][@YYYY-MM-DD[THH:MM]]`.

    The date is read in the zone given by --tz. A bare date means midnight at the
    start of that day, so nothing is scheduled on the exam day itself.
    """
    body, _, when = text.partition("@")
    parts = body.split(":")
    name = parts[0].strip()
    if not name:
        raise argparse.ArgumentTypeError("a subject needs a name")
    try:
        stability = float(parts[1]) if len(parts) > 1 and parts[1] else DEFAULT_STABILITY
        difficulty = float(parts[2]) if len(parts) > 2 and parts[2] else DEFAULT_DIFFICULTY
        memory = MemoryState(stability, difficulty)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"bad stability or difficulty in {text!r}: {exc}") from exc
    exam = None
    if when.strip():
        try:
            exam = datetime.fromisoformat(when.strip())
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"use @YYYY-MM-DD or @YYYY-MM-DDTHH:MM, not {when!r}") from exc
    return name, memory, exam


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cps", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("inspect", "plan"):
        p = sub.add_parser(name)
        p.add_argument("ics", type=Path, help="calendar export (.ics)")
        p.add_argument("--from", dest="start", type=date.fromisoformat, default=None,
                       help="first day of the horizon (default: today)")
        p.add_argument("--days", type=int, default=None,
                       help="horizon length (default: up to the earliest assessment)")
        p.add_argument("--tz", default="UTC", help="IANA zone, e.g. Europe/Rome")
        p.add_argument("--study-window", type=_study_window, default=(8.0, 22.0),
                       help="hours you are willing to study between, default 8-22")
        p.add_argument("--blocks-per-day", type=int, default=2)
        p.add_argument("--block-minutes", type=int, default=90)

    plan = sub.choices["plan"]
    plan.add_argument("--subject", type=_subject, action="append", default=[],
                      help="Name[:stability[:difficulty]][@exam date], repeatable; without a "
                           "date the matching assessment in the calendar is used; without "
                           "any --subject, every assessment found is planned")
    plan.add_argument("--retention", type=float, default=0.9,
                      help="recall probability you want on the day, default 0.9")
    plan.add_argument("--window", type=int, default=6, help="blocks planned exactly per solve")
    plan.add_argument("--penalty", type=float, default=DEFAULT_FAILURE_PENALTY,
                      help="cost, in study blocks, of reaching an exam unready (default 40)")
    plan.add_argument("--seed", type=int, default=None,
                      help="simulate outcomes stochastically instead of assuming every recall works")
    plan.add_argument("--out", type=Path, default=None, help="write the plan as .ics")
    return parser


def _load(args) -> tuple:
    start = args.start or date.today()
    text = decode_ics(args.ics.read_bytes())

    probe_days = args.days or 120
    _, probe_events = load_availability(text, start, probe_days, args.tz, study_window=None)
    deadlines = find_deadlines(probe_events)

    if args.days:
        days = args.days
    else:
        exams = [e for e in getattr(args, "exams", [])] or [d.when for d in deadlines]
        latest = max((_days_after(start, e) for e in exams), default=21.0)
        days = max(1, math.ceil(latest - 1e-9))

    grid, events = load_availability(text, start, days, args.tz, study_window=args.study_window)
    blocks = tile_free_time(
        grid,
        block_slots=max(1, args.block_minutes // (24 * 60 // grid.slots_per_day)),
        max_blocks_per_day=args.blocks_per_day,
    )
    return start, days, grid, events, blocks, deadlines


def _days_after(start: date, when: datetime) -> float:
    """Days from midnight at `start` to `when`, in wall-clock time.

    Blocks are placed on the local wall clock (`TimeGrid.days_from_start`), so the
    exam has to be measured the same way; both datetimes share the zone, and
    Python subtracts same-zone datetimes on the wall clock.
    """
    midnight = datetime.combine(start, time(0, 0), tzinfo=when.tzinfo)
    return (when - midnight) / timedelta(days=1)


def _resolve_subjects(args, start: date, deadlines) -> list[Subject]:
    """Attach an exam date to every subject, or fail with a message that says how."""
    zone = ZoneInfo(args.tz)
    by_name = {d.subject.casefold(): d.when for d in deadlines}
    requested = list(args.subject) or [
        (d.subject, MemoryState(DEFAULT_STABILITY, DEFAULT_DIFFICULTY), None) for d in deadlines
    ]
    subjects = []
    for name, memory, exam in requested:
        if exam is None:
            exam = by_name.get(name.casefold())
            if exam is None:
                raise SystemExit(
                    f"No exam date for {name!r}: none of the assessments in the calendar is "
                    f"called that. Add it, for example --subject \"{name}:2:6@2026-06-15\"."
                )
        elif exam.tzinfo is None:
            exam = exam.replace(tzinfo=zone)
        exam_day = _days_after(start, exam.astimezone(zone))
        if exam_day <= 0:
            raise SystemExit(f"The exam for {name!r} ({exam:%Y-%m-%d %H:%M}) is not after {start}.")
        subjects.append(Subject(name, memory, exam_day))
    return subjects


def _clock(grid, slot: int) -> str:
    minutes = (slot % grid.slots_per_day) * (24 * 60 // grid.slots_per_day)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def command_inspect(args) -> int:
    start, days, grid, events, blocks, deadlines = _load(args)
    busy_hours = grid.busy_slots() / 2
    print(f"horizon        {start} plus {days} days, zone {args.tz}")
    print(f"occurrences    {len(events)} (recurrences expanded)")
    print(f"blocked        {busy_hours:.0f} h of {grid.total_slots / 2:.0f} h "
          f"(includes your {args.study_window[0]:g}-{args.study_window[1]:g} study window)")
    print(f"study blocks   {len(blocks)} of {args.block_minutes} min, "
          f"max {args.blocks_per_day} per day")
    if blocks:
        print(f"first / last   day {blocks[0].day} at {_clock(grid, blocks[0].slot)} / "
              f"day {blocks[-1].day} at {_clock(grid, blocks[-1].slot)}")
    print(f"assessments    {len(deadlines) or 'none found'}")
    for deadline in deadlines:
        print(f"  {deadline.when:%Y-%m-%d %H:%M}  {deadline.subject}   ({deadline.summary})")
    if not blocks:
        print("\nNo candidate blocks. Widen --study-window or raise --blocks-per-day.")
    return 0


def command_plan(args) -> int:
    start = args.start or date.today()
    text = decode_ics(args.ics.read_bytes())
    _, probe_events = load_availability(text, start, args.days or 120, args.tz, study_window=None)
    try:
        subjects = _resolve_subjects(args, start, find_deadlines(probe_events))
    except SystemExit as stop:
        print(stop, file=sys.stderr)
        return 1
    if not subjects:
        print("Nothing to schedule: no --subject given and no assessment found.", file=sys.stderr)
        return 1
    args.exams = []
    args.days = args.days or max(1, math.ceil(max(s.exam_day for s in subjects) - 1e-9))
    start, days, grid, events, blocks, _ = _load(args)
    if not blocks:
        print("No candidate study blocks; run `inspect` and widen the study window.", file=sys.stderr)
        return 1

    rng = None
    if args.seed is not None:
        import numpy as np

        rng = np.random.default_rng(args.seed)

    result = run_rolling(
        blocks, subjects, window=args.window, retention=args.retention,
        failure_penalty=args.penalty, rng=rng,
    )

    print(f"horizon {days} days, {len(blocks)} candidate blocks, "
          f"{args.retention:.0%} recall wanted, a missed exam priced at {args.penalty:g} blocks")
    for subject, target in zip(subjects, result.targets):
        print(f"  {subject.name:<18} exam on day {subject.exam_day:5.1f}, "
              f"target stability {target:.0f} days")
    print(f"solves {result.solves}, peak expansions {result.peak_expansions}\n")
    if result.sessions:
        print(f"{'day':>4} {'time':>6}  {'subject':<18}{'recall':>7}{'outcome':>9}{'stability':>19}")
        for session in result.sessions:
            arrow = f"{session.stability_before:.1f} -> {session.stability_after:.1f}"
            print(f"{session.block.day:>4} {_clock(grid, session.block.slot):>6}  "
                  f"{session.subject:<18}{session.retrievability_at_review:>7.2f}"
                  f"{session.outcome.name.lower():>9}{arrow:>19}")
    else:
        print("(no sessions scheduled)")

    print(f"\nblocks used {result.blocks_used} of {len(blocks)}, lapses {result.lapses}")
    for name, ready, lost, recall in zip(
        result.subjects, result.ready, result.unreachable, result.recall_at_exam
    ):
        status = "ready    " if ready else ("CANNOT   " if lost else "NOT ready")
        print(f"  {status}  {name:<18} recall at the exam {recall:.0%}")
    for name in result.unreachable_subjects():
        print(f"\n{name}: even if every review succeeded, the free blocks before this exam "
              f"cannot build the target stability, so none were spent on it. More free time "
              f"spread over more days, or a later exam date, would change that.")

    if rng is None:
        print("\nThis is the plan if every review succeeds. Add --seed N to simulate one run in")
        print("which some reviews fail, and see the plan adapt.")
    print("\nWhat this does not know: the memory model uses population-default FSRS")
    print("parameters, and each subject's starting stability and difficulty are your")
    print("own estimates. Treat the plan as a reasoned suggestion, not a measurement.")

    if args.out:
        # Bytes, not text. icalendar already emits CRLF line endings, and a
        # text-mode write on Windows would translate each LF again, leaving CR CR LF.
        args.out.write_bytes(
            plan_to_ics(
                result.to_ics_sessions(),
                start,
                args.tz,
                slots_per_day=grid.slots_per_day,
                block_slots=max(1, args.block_minutes // (24 * 60 // grid.slots_per_day)),
            ).encode("utf-8")
        )
        print(f"\nwrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_output()
    args = _build_parser().parse_args(argv)
    return {"inspect": command_inspect, "plan": command_plan}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
