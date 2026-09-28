"""
Command line interface.

Two subcommands, deliberately separated by how much you can trust them.

`inspect` reads a calendar and reports what the ingestion layer saw: expanded
occurrences, busy hours, candidate study blocks, detected assessments. This part
is finished and tested, and it is the useful thing to run against your own
export first — if the free blocks it lists are wrong, nothing downstream can be
right.

`plan` runs the whole pipeline and prints a schedule. It works mechanically and
the schedule it produces is *late*, because of the defect recorded as AUDIT.md
item 20: the value function is indexed by remaining blocks and not by remaining
time, so postponing is nearly free and the planner drifts toward the deadline. It
prints that warning rather than letting you discover it.

Known limitation, separate from the defect: one stability target for all
subjects, taken from the earliest assessment. Per-subject deadlines would need
the target to move into the topic state.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from .budget import BudgetConfig
from .budget import solve as budget_solve
from .calendar_io import decode_ics, find_deadlines, load_availability, plan_to_ics
from .console import ensure_utf8_output
from .memory import MemoryState, stability_for_interval
from .plan import tile_free_time
from .rolling import run_rolling

DEFAULT_STABILITY = 2.0
DEFAULT_DIFFICULTY = 6.0


def _study_window(text: str) -> tuple[float, float]:
    try:
        earliest, latest = (float(part) for part in text.split("-", 1))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use HH-HH, for example 8-22") from exc
    return earliest, latest


def _subject(text: str) -> tuple[str, MemoryState]:
    """`Name`, `Name:stability`, or `Name:stability:difficulty`."""
    parts = text.split(":")
    name = parts[0].strip()
    if not name:
        raise argparse.ArgumentTypeError("a subject needs a name")
    stability = float(parts[1]) if len(parts) > 1 and parts[1] else DEFAULT_STABILITY
    difficulty = float(parts[2]) if len(parts) > 2 and parts[2] else DEFAULT_DIFFICULTY
    return name, MemoryState(stability, difficulty)


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
                      help="Name[:stability[:difficulty]], repeatable; "
                           "defaults to the assessments found in the calendar")
    plan.add_argument("--retention", type=float, default=0.9,
                      help="recall probability you want on the day, default 0.9")
    plan.add_argument("--window", type=int, default=6, help="blocks planned exactly per solve")
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
    elif deadlines:
        days = max(1, int(deadlines[0].days_from(start)))
    else:
        days = 21

    grid, events = load_availability(text, start, days, args.tz, study_window=args.study_window)
    blocks = tile_free_time(
        grid,
        block_slots=max(1, args.block_minutes // (24 * 60 // grid.slots_per_day)),
        max_blocks_per_day=args.blocks_per_day,
    )
    return start, days, grid, events, blocks, deadlines


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
    start, days, grid, events, blocks, deadlines = _load(args)
    if not blocks:
        print("No candidate study blocks; run `inspect` and widen the study window.", file=sys.stderr)
        return 1

    subjects = list(args.subject) or [
        (d.subject, MemoryState(DEFAULT_STABILITY, DEFAULT_DIFFICULTY)) for d in deadlines
    ]
    if not subjects:
        print("Nothing to schedule: no --subject given and no assessment found.", file=sys.stderr)
        return 1

    target = stability_for_interval(days, args.retention)
    policy = budget_solve(BudgetConfig.for_heuristic(target))
    rng = None
    if args.seed is not None:
        import numpy as np

        rng = np.random.default_rng(args.seed)

    result = run_rolling(blocks, subjects, target, policy, window=args.window, rng=rng)

    print(f"horizon {days} days, {len(blocks)} candidate blocks, "
          f"target stability {target:.0f} days for {args.retention:.0%} recall")
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
    for name, ready in zip(result.subjects, result.ready):
        print(f"  {'ready    ' if ready else 'NOT ready'}  {name}")

    print("\nKnown defect (AUDIT.md item 20): the value function is indexed by remaining")
    print("blocks, not remaining time, so postponing costs almost nothing and this plan")
    print("sits later than it should. Treat the timing as indicative, not as advice.")

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
