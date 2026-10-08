"""
Command line interface.

Two subcommands, deliberately separated by how much you can trust them.

`inspect` reads a calendar and reports what the ingestion layer saw: expanded
occurrences, busy hours, candidate study blocks, detected assessments. Run it
against your own export first: if the free blocks it lists are wrong, nothing
downstream can be right.

Both take a calendar file or a link to one: a university timetable's export
address, Google Calendar's secret iCal address, a `webcal://` subscription.

`plan` runs the whole pipeline and prints a schedule. Each subject is planned
towards its own exam: pass the date with `--subject "Analysis:2:7@2026-03-20"`, or
leave it out and the date of the matching assessment found in the calendar is
used. The planner reports a subject whose exam the calendar cannot prepare for
instead of scheduling it anyway.

What it does not know, and says so in its output: the memory model uses
population-default FSRS parameters, and each subject's starting stability and
difficulty are your own guesses.

`serve` runs the feed server (`cps.feed`), which answers the calendar subscriptions
the Streamlit page publishes. `web` runs the hosted product (`cps.web`, which needs
the `web` extra): pages, feeds and reports in one server. `sweep` deletes expired
plans and `backup` copies the store, for a deployment's scheduled jobs.

All the work happens in `cps.service`; this module parses arguments and prints.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from . import service
from .console import ensure_utf8_output
from .rolling import DEFAULT_FAILURE_PENALTY

DEFAULT_STABILITY = 2.0
DEFAULT_DIFFICULTY = 6.0


def _study_window(text: str) -> tuple[float, float]:
    try:
        earliest, latest = (float(part) for part in text.split("-", 1))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use HH-HH, for example 8-22") from exc
    return earliest, latest


def _subject(text: str) -> service.SubjectSpec:
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
        spec = service.SubjectSpec(name, None, stability=stability, difficulty=difficulty)
        spec.memory()
    except (ValueError, service.ServiceError) as exc:
        raise argparse.ArgumentTypeError(f"bad stability or difficulty in {text!r}: {exc}") from exc
    if when.strip():
        try:
            exam = datetime.fromisoformat(when.strip())
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"use @YYYY-MM-DD or @YYYY-MM-DDTHH:MM, not {when!r}") from exc
        spec = service.SubjectSpec(name, exam, stability=stability, difficulty=difficulty)
    return spec


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cps", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("inspect", "plan"):
        p = sub.add_parser(name)
        p.add_argument("ics", help="calendar export (.ics), or a link to one (https://, webcal://)")
        p.add_argument(
            "--from",
            dest="start",
            type=date.fromisoformat,
            default=None,
            help="first day of the horizon (default: today)",
        )
        p.add_argument(
            "--days",
            type=int,
            default=None,
            help="horizon length (default: up to the last assessment or exam)",
        )
        p.add_argument("--tz", default="UTC", help="IANA zone, e.g. Europe/Rome")
        p.add_argument(
            "--study-window",
            type=_study_window,
            default=(8.0, 22.0),
            help="hours you are willing to study between, default 8-22",
        )
        p.add_argument("--blocks-per-day", type=int, default=2)
        p.add_argument("--block-minutes", type=int, default=90)

    plan = sub.choices["plan"]
    plan.add_argument(
        "--subject",
        type=_subject,
        action="append",
        default=[],
        help="Name[:stability[:difficulty]][@exam date], repeatable; without a "
        "date the matching assessment in the calendar is used; without "
        "any --subject, every assessment found is planned",
    )
    plan.add_argument(
        "--retention", type=float, default=0.9, help="recall probability you want on the day, default 0.9"
    )
    plan.add_argument("--window", type=int, default=6, help="blocks planned exactly per solve")
    plan.add_argument(
        "--penalty",
        type=float,
        default=DEFAULT_FAILURE_PENALTY,
        help="cost, in study blocks, of reaching an exam unready (default 40)",
    )
    plan.add_argument(
        "--seed",
        type=int,
        default=None,
        help="simulate outcomes stochastically instead of assuming every recall works",
    )
    plan.add_argument(
        "--whole-subjects",
        action="store_true",
        help="plan each subject as one topic, ignoring its lectures in the calendar",
    )
    plan.add_argument("--out", type=Path, default=None, help="write the plan as .ics")

    serve = sub.add_parser("serve", help="answer the calendar feeds published from the web page")
    serve.add_argument("--store", type=Path, default=service.FEED_STORE, help="where the feeds are kept")
    serve.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this machine)")
    serve.add_argument("--port", type=int, default=8765)

    web = sub.add_parser("web", help="run the hosted product: pages, feeds and reports (needs [web])")
    web.add_argument("--db", type=Path, default=service.FEED_STORE, help="the store (file or directory)")
    web.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this machine)")
    web.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000))
    web.add_argument(
        "--behind-proxy",
        action="store_true",
        help="trust X-Forwarded-For and -Proto from any address: only behind a host's own proxy",
    )

    sweep = sub.add_parser("sweep", help="delete the plans whose retention has passed")
    sweep.add_argument("--db", type=Path, default=service.FEED_STORE)

    metrics = sub.add_parser("metrics", help="the pilot's measures, from the store (DECISIONS.md D15)")
    metrics.add_argument("--db", type=Path, default=service.FEED_STORE)
    metrics.add_argument("--json", action="store_true", help="print them as JSON")

    backup = sub.add_parser("backup", help="copy the store while it is in use; keep a week of copies")
    backup.add_argument("--db", type=Path, default=service.FEED_STORE)
    backup.add_argument("--to", type=Path, required=True, help="directory for the copies")
    backup.add_argument("--keep-days", type=int, default=7)
    return parser


def _read(source: str) -> bytes:
    """A calendar file's bytes, or the calendar behind a link."""
    if source.lower().startswith(("http://", "https://", "webcal://")):
        return service.fetch_calendar(source)
    return Path(source).read_bytes()


def _analyse(args) -> service.CalendarReport:
    return service.analyse_calendar(
        _read(args.ics),
        start=args.start or date.today(),
        tz=args.tz,
        days=args.days,
        study_window=args.study_window,
        blocks_per_day=args.blocks_per_day,
        block_minutes=args.block_minutes,
    )


def _hhmm(iso: str) -> str:
    return iso[11:16]


def command_inspect(args) -> int:
    report = _analyse(args)
    print(f"horizon        {report.start} plus {report.days} days, zone {report.tz}")
    print(f"occurrences    {len(report.events)} (recurrences expanded)")
    print(
        f"blocked        {report.busy_hours:.0f} h of {report.total_hours:.0f} h "
        f"(includes your {args.study_window[0]:g}-{args.study_window[1]:g} study window)"
    )
    print(
        f"study blocks   {len(report.blocks)} of {args.block_minutes} min, max {args.blocks_per_day} per day"
    )
    if report.blocks:
        first, last = report.blocks[0], report.blocks[-1]
        print(
            f"first / last   day {first.day} at {_hhmm(first.start)} / day {last.day} at {_hhmm(last.start)}"
        )
    print(f"assessments    {len(report.assessments) or 'none found'}")
    for found in report.assessments:
        print(f"  {found.when[:10]} {found.when[11:16]}  {found.subject}   ({found.summary})")
    if not report.blocks:
        print("\nNo candidate blocks. Widen --study-window or raise --blocks-per-day.")
    return 0


def command_plan(args) -> int:
    report = _analyse(args)
    subjects = list(args.subject) or [
        service.SubjectSpec(a.subject, None, stability=DEFAULT_STABILITY, difficulty=DEFAULT_DIFFICULTY)
        for a in report.assessments
    ]
    if not subjects:
        print("Nothing to schedule: no --subject given and no assessment found.", file=sys.stderr)
        return 1
    plan = service.make_plan(
        report,
        subjects,
        retention=args.retention,
        window=args.window,
        seed=args.seed,
        failure_penalty=args.penalty,
        lectures_as_topics=not args.whole_subjects,
    )

    print(
        f"horizon {plan.settings.horizon_days} days, {len(plan.blocks)} candidate blocks, "
        f"{args.retention:.0%} recall wanted, a missed exam priced at {args.penalty:g} blocks"
    )
    for subject in plan.subjects:
        if subject.topics > 1:
            print(
                f"  {subject.name:<18} exam on day {subject.exam_day:5.1f}, "
                f"{subject.topics} topics, one per week of lectures, each with its own target"
            )
        else:
            print(
                f"  {subject.name:<18} exam on day {subject.exam_day:5.1f}, "
                f"target stability {subject.target:.0f} days"
            )
    print()
    if plan.sessions:
        width = max(18, *(len(s.title) + 2 for s in plan.sessions))
        print(f"{'day':>4} {'time':>6}  {'subject':<{width}}{'recall':>7}{'outcome':>9}{'stability':>19}")
        for session in plan.sessions:
            arrow = f"{session.stability_before:.1f} -> {session.stability_after:.1f}"
            outcome = "good" if session.outcome == "recalled" else "again"
            print(
                f"{session.day:>4} {_hhmm(session.start):>6}  {session.title:<{width}}"
                f"{session.recall:>7.2f}{outcome:>9}{arrow:>19}"
            )
    else:
        print("(no sessions scheduled)")

    lapses = sum(1 for s in plan.sessions if s.outcome == "lapsed")
    print(f"\nblocks used {plan.blocks_used} of {len(plan.blocks)}, lapses {lapses}")
    for subject in plan.subjects:
        status = "ready    " if subject.ready else ("CANNOT   " if subject.unreachable else "NOT ready")
        topics = f", {subject.topics_ready} of {subject.topics} topics ready" if subject.topics > 1 else ""
        print(f"  {status}  {subject.name:<18} recall at the exam {subject.recall_at_exam:.0%}{topics}")
    for warning in plan.warnings:
        print(f"\n{warning}")

    if args.seed is None:
        print("\nThis is the plan if every review succeeds. Add --seed N to simulate one run in")
        print("which some reviews fail, and see the plan adapt.")
    print("\nWhat this does not know: the memory model uses population-default FSRS")
    print("parameters, and each subject's starting stability and difficulty are your")
    print("own estimates. Treat the plan as a reasoned suggestion, not a measurement.")

    if args.out:
        # Bytes, not text. icalendar already emits CRLF line endings, and a
        # text-mode write on Windows would translate each LF again, leaving CR CR LF.
        try:
            args.out.write_bytes(service.export_ics(plan))
        except OSError as error:
            # Reported here, so that the closed-stdout handling in `main` can
            # never swallow a plan that was not written.
            print(f"could not write {args.out}: {error.strerror or error}", file=sys.stderr)
            return 1
        print(f"\nwrote {args.out}")
    return 0


def command_serve(args) -> int:
    from .feed import serve

    with contextlib.suppress(KeyboardInterrupt):
        serve(args.store, args.host, args.port)
    return 0


def command_web(args) -> int:
    import logging

    import uvicorn

    from .web import Config, create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config.from_env()
    config.store = args.db
    print(f"serving on http://{args.host}:{args.port}", flush=True)
    uvicorn.run(
        create_app(config),
        host=args.host,
        port=args.port,
        access_log=False,  # the app logs the route, never the path: it holds the secret
        proxy_headers=args.behind_proxy,
        forwarded_allow_ips="*" if args.behind_proxy else None,
        server_header=False,
    )
    return 0


def command_sweep(args) -> int:
    from .store import Store

    print(f"deleted {Store(args.db).sweep()} expired plans")
    return 0


def _share(part: int, whole: int) -> str:
    return f"{part} of {whole}" + (f" ({100 * part / whole:.0f}%)" if whole else "")


def command_metrics(args) -> int:
    """The pilot's numbers, week by week (D15). With 10 to 20 students they are
    counts to read and ask about, not rates to test."""
    m = service.engagement(args.db)
    if args.json:
        print(json.dumps(m, indent=2))
        return 0
    net, ai, st = m["network"], m["ai"], m["streaks"]
    lines = [
        f"at {m['at']} (UTC)",
        f"plans set up             {m['plans']} ({m['new_30']} in the last 30 days)",
        f"active, last 7 days      {_share(m['active_7'], m['plans'])}",
        f"active, last 30 days     {_share(m['active_30'], m['plans'])}",
        f"north star               {_share(m['north_star'], m['plans'])} confirmed a session in 7 days",
        f"sessions confirmed       {_share(m['confirmed_7'], m['planned_7'])} planned in the last 7 days",
        f"back on day 7            {_share(*m['return_7'])} of those set up 7 or more days ago",
        f"back on day 30           {_share(*m['return_30'])} of those set up 30 or more days ago",
        f"streaks                  median {st['median']}, longest {st['longest']}, "
        f"{st['at_least_3']} at 3 days or more, {st['at_least_7']} at 7 or more",
        f"over their weekly limit  {m['over_limit']} (should be 0)",
        f"network                  {net['profiles']} profiles, {net['follows']} follows",
        f"  last 7 days            {net['sessions_shared']} sessions shared, {net['sessions_private']} kept "
        f"private, {net['explanations']} explanations, {net['notes']} notes, {net['kudos']} kudos and "
        f"helpful marks, {net['comments']} comments",
        f"  focus, last 7 days     {net['timed']} timed, {net['focus_checked']} with the focus checked",
        f"  reports open           {net['reports_open']}",
        f"AI this month            {ai['calls']} calls, {ai['used']} answers used, "
        f"${ai['dollars']:.2f} of ${ai['cap']:.2f}"
        + ("" if ai["available"] else " (no key on this machine)"),
    ]
    print("\n".join(lines))
    return 0


def command_backup(args) -> int:
    from .store import Store

    before = set(args.to.glob("cps-*.sqlite")) if args.to.exists() else set()
    target = Store(args.db).backup_rotating(args.to, args.keep_days)
    removed = len(before - set(args.to.glob("cps-*.sqlite")))
    print(f"wrote {target}; removed {removed} copies older than {args.keep_days} days")
    return 0


def _stdout_closed(error: OSError) -> bool:
    """A reader that left early: `| head` on Linux and macOS raises EPIPE; on
    Windows a closed pipe can surface as EINVAL instead. Any other OSError, and
    this one from anything but printing, is a real failure."""
    return isinstance(error, BrokenPipeError) or (os.name == "nt" and error.errno == errno.EINVAL)


def _silence_stdout() -> None:
    """Point stdout at the null device, so the interpreter's final flush of the
    buffer that could not be delivered does not raise again on the way out."""
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, sys.stdout.fileno())
    finally:
        os.close(null)


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_output()
    args = _build_parser().parse_args(argv)
    command = {
        "inspect": command_inspect,
        "plan": command_plan,
        "serve": command_serve,
        "web": command_web,
        "sweep": command_sweep,
        "backup": command_backup,
        "metrics": command_metrics,
    }[args.command]
    try:
        code = command(args)
        sys.stdout.flush()
        return code
    except OSError as error:
        # AUDIT.md item 23. Treated as a normal exit: the reader got what it
        # wanted. The --out write is handled in command_plan and never lands here.
        if not _stdout_closed(error):
            raise
        _silence_stdout()
        return 0
    except service.MissingExam as error:
        print(f'{error} Add it, for example --subject "{error.subject}:2:6@2026-06-15".', file=sys.stderr)
        return 1
    except service.ServiceError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
