"""
The assistant: a student's week, scheduled in milliseconds.

Why a rule and not the search
-----------------------------
`rolling.py` plans with AO* over a value function, exactly within each window. On a
semester (`benchmarks/rule_vs_planner.py`) a one-line rule, *study the taught topic
you remember least*, reaches as many topics at their target with a few more
sessions and higher predicted recall at the exams, in a hundredth of a second
instead of half a minute (AUDIT.md item 36). The objective is flat where plans
differ (METHOD.md §5), so the search buys little. What a busy student needs is
elsewhere: deadlines, a workload they choose, days off, and an answer the moment
something changes. So the product schedules with rules whose reasons can be said in
one sentence, and the search stays as the reference that justifies them.

The rules, in the order a block is offered to them
--------------------------------------------------
A block on a rest day, or beyond the week's budget, is left free. Otherwise:

1. **A deadline that is getting close.** Tasks are checked in order of their due
   date: if the blocks left before some deadline barely cover the work due by then
   (all earlier tasks included), the earliest-due task gets the block. This is the
   classical earliest-deadline-first test, and it is what makes "every deadline
   met" hold whenever the calendar allows it.
2. **Exam practice.** In the last `practice_days` before an exam, past papers or
   exam-style problems, a set number of blocks per exam, nearest exam first.
3. **Self-testing on what is being forgotten.** Among the topics taught so far whose
   exam is still ahead, the one with the lowest predicted recall (FSRS-4.5), if it
   has fallen to `review_below`. A topic just taught is reviewed first when it
   fades, a few days later, which is also what the evidence on spacing says.
4. **Working ahead** on the task due soonest.
5. Otherwise the block stays free. A plan that fills every free hour is a plan
   nobody follows; this one uses time only when something needs it.

Sessions say what to do, not only when: practice testing (recall first, then check)
and distributed practice are the two techniques rated high utility in the review
of ten study techniques by Dunlosky et al. (2013) [ref:dunlosky2013]; rereading and
highlighting were rated low, so no session asks for them.

Every review is assumed to succeed, as in the rest of the project, until the
student reports otherwise; reported sessions are replayed with their outcomes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from .memory import Grade, MemoryState, retrievability, review
from .plan import Block


@dataclass(frozen=True, slots=True)
class Task:
    """Work with a deadline: homework, a report, a project, a reading. Times are
    days from midnight at the start of the plan, like `Block.start_day`."""

    name: str
    due_day: float
    blocks: int  # still to do, in study blocks
    course: str = ""
    available_day: float = 0.0


@dataclass(frozen=True, slots=True)
class Topic:
    """Material to keep in memory until its exam: a week of lectures, or what was
    taught before the plan started."""

    name: str
    course: str
    memory: MemoryState
    last_review_day: float
    available_day: float
    exam_day: float
    note: str = ""  # "the lectures of the week of 05 Oct"


@dataclass(frozen=True, slots=True)
class Exam:
    course: str
    day: float
    practice_blocks: int = 3


@dataclass(frozen=True, slots=True)
class Preferences:
    weekly_blocks: int | None = None  # at most this many study blocks a week
    rest_weekdays: tuple[int, ...] = ()  # 0 = Monday
    practice_days: float = 14.0
    review_below: float = 0.9  # self-test a topic once its recall falls to this


@dataclass(frozen=True, slots=True)
class Session:
    block: Block
    kind: str  # "task", "practice", "first review", "review"
    course: str
    title: str  # the task or topic name, or "Exam practice: <course>"
    what: str  # what to do in the block
    why: str  # why this block, in one sentence
    recall: float = 1.0  # predicted recall of a topic at the start of the block
    stability_before: float = 0.0
    stability_after: float = 0.0


@dataclass
class Schedule:
    sessions: list[Session] = field(default_factory=list)
    topics: dict[str, tuple[MemoryState, float]] = field(default_factory=dict)  # final state
    task_left: dict[str, int] = field(default_factory=dict)  # blocks not scheduled by the due date
    task_finish: dict[str, float] = field(default_factory=dict)  # day the last block starts
    practice_left: dict[str, int] = field(default_factory=dict)


def schedule(
    blocks: Sequence[Block],
    *,
    first_weekday: int,
    topics: Sequence[Topic] = (),
    tasks: Sequence[Task] = (),
    exams: Sequence[Exam] = (),
    preferences: Preferences | None = None,
    week_used: dict[int, int] | None = None,
) -> Schedule:
    """Fill `blocks` by the rules in the module docstring.

    `first_weekday` is the weekday of day 0 (0 = Monday), which places each block in
    its calendar week for the budget. `week_used` counts blocks already spent in each
    week (by sessions reported before a replan)."""
    prefs = preferences or Preferences()
    order = sorted(blocks, key=lambda b: b.start_day)
    used = dict(week_used or {})

    def week(block: Block) -> int:
        return (first_weekday + block.day) // 7

    def resting(block: Block) -> bool:
        return (first_weekday + block.day) % 7 in prefs.rest_weekdays

    eligible = [b for b in order if not resting(b)]
    left = {t.name: t.blocks for t in tasks}
    finish: dict[str, float] = {}
    practice = {e.course: e.practice_blocks for e in exams}
    state = {t.name: (t.memory, t.last_review_day) for t in topics}
    reviewed: set[str] = set()
    result = Schedule()

    def tight(after: int, open_tasks: Sequence[Task]) -> bool:
        """Earliest-deadline-first test: do the blocks from eligible[after] on,
        within each week's budget, barely cover the work due by some deadline?
        One pass over the blocks, whatever the number of tasks."""
        per_week: dict[int, int] = {}
        owed, index = 0, 0
        blocks_ahead = eligible[after:]
        for task in open_tasks:
            owed += left[task.name]
            while index < len(blocks_ahead) and blocks_ahead[index].start_day < task.due_day:
                w = week(blocks_ahead[index])
                per_week[w] = per_week.get(w, 0) + 1
                index += 1
            cap = prefs.weekly_blocks
            if cap is None:
                capacity = sum(per_week.values())
            else:
                capacity = sum(min(n, max(cap - used.get(w, 0), 0)) for w, n in per_week.items())
            if capacity <= owed:
                return True
        return False

    position = 0
    for block in order:
        now = block.start_day
        if resting(block):
            continue
        here = position
        position += 1
        if prefs.weekly_blocks is not None and used.get(week(block), 0) >= prefs.weekly_blocks:
            continue

        open_tasks = sorted(
            (t for t in tasks if left[t.name] > 0 and t.available_day <= now < t.due_day),
            key=lambda t: t.due_day,
        )
        choice: Session | None = None

        # 1. A deadline that is getting close: earliest-deadline-first feasibility.
        if open_tasks and tight(here, open_tasks):
            choice = _task_session(block, open_tasks[0], left, "a deadline is close")

        # 2. Exam practice in the last days before an exam.
        if choice is None:
            due_exams = sorted(
                (e for e in exams if practice[e.course] > 0 and e.day - prefs.practice_days <= now < e.day),
                key=lambda e: e.day,
            )
            if due_exams:
                exam = due_exams[0]
                practice[exam.course] -= 1
                choice = Session(
                    block,
                    "practice",
                    exam.course,
                    f"Exam practice: {exam.course}",
                    "Do a past paper or exam-style problems under exam conditions, timed and "
                    "without notes. Then mark it and write down what you missed: that list is "
                    "what the next sessions are for.",
                    f"The exam is in {exam.day - now:.0f} days; practice under exam conditions "
                    f"is what prepares for it ({exam.practice_blocks - practice[exam.course]} of "
                    f"{exam.practice_blocks}).",
                )

        # 3. Self-testing on the taught topic that is being forgotten most.
        if choice is None:
            fading = [
                (retrievability(max(now - state[t.name][1], 0.0), state[t.name][0].stability), t)
                for t in topics
                if t.available_day <= now < t.exam_day
            ]
            fading = [(r, t) for r, t in fading if r <= prefs.review_below]
            if fading:
                recall, topic = min(fading, key=lambda pair: (pair[0], pair[1].exam_day))
                memory, last = state[topic.name]
                after = review(memory, max(now - last, 0.0), Grade.GOOD)
                state[topic.name] = (after, now)
                first = topic.name not in reviewed
                reviewed.add(topic.name)
                choice = Session(
                    block,
                    "first review" if first else "review",
                    topic.course,
                    topic.name,
                    _review_text(topic, first),
                    f"Predicted recall of {topic.note or topic.name} is down to about "
                    f"{recall:.0%}; testing yourself now is when it helps most.",
                    recall=recall,
                    stability_before=memory.stability,
                    stability_after=after.stability,
                )

        # 4. Working ahead on the task due soonest.
        if choice is None and open_tasks:
            choice = _task_session(block, open_tasks[0], left, "nothing else needs this block")

        if choice is None:
            continue  # 5. free time
        if choice.kind == "task":
            finish[choice.title] = now
        used[week(block)] = used.get(week(block), 0) + 1
        result.sessions.append(choice)

    result.topics = state
    result.task_left = {name: n for name, n in left.items() if n > 0}
    result.task_finish = finish
    result.practice_left = {course: n for course, n in practice.items() if n > 0}
    return result


def _task_session(block: Block, task: Task, left: dict[str, int], reason: str) -> Session:
    left[task.name] -= 1
    remaining = left[task.name]
    days = task.due_day - block.start_day
    return Session(
        block,
        "task",
        task.course,
        task.name,
        f"Work on {task.name}."
        + (
            f" {remaining} more block{'s' if remaining != 1 else ''} planned before it is due."
            if remaining
            else " This is the last block planned for it."
        ),
        f"Due in {days:.0f} day{'s' if round(days) != 1 else ''}; {reason}.",
    )


def _review_text(topic: Topic, first: bool) -> str:
    what = topic.note or topic.name
    if first:
        return (
            f"First review of {what}. Without your notes, write down the main ideas, definitions "
            "and methods you remember; then check against your notes and fill the gaps. Redo one "
            "worked example."
        )
    return (
        f"Test yourself on {what}: recall first, from a blank page or by explaining it aloud, "
        "then check. Do one or two exercises from that week's tutorial."
    )


def weekday_of(day: int, first_weekday: int) -> int:
    return (first_weekday + day) % 7


def blocks_for_hours(hours: float, block_minutes: int) -> int:
    """A workload in hours as whole study blocks, rounded up: an estimate of two
    hours in 90-minute blocks is two blocks, not one and a third."""
    return max(1, math.ceil(hours * 60 / block_minutes - 1e-9))
