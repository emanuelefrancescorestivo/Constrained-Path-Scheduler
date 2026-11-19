"""
A last pass before each exam: does it keep more topics at 90 % on exam day? (DECISIONS.md D147)

The owner asked for a last revision before each exam, made explicit in the plan and
counted in the exam forecast, after How it works compared cramming with spaced revision
plus a last pass (D142). This measures the rule that would do it, before it is built: the
assistant's self-test rule (assistant.py, rule 3) with one more trigger. In the last `days`
before a topic's exam, the topic gets a self-test as soon as its predicted recall *at the
exam* would be under the target, not only once it is under the target now. Among the
topics open for a self-test, one on its last pass is ranked by that recall at the exam.

The same synthetic semester as `benchmarks/rule_vs_planner.py` (six courses, 69 topics, six
exams within five days), FSRS-6 as the product runs it, Sundays off, 4.5 hours of exam
practice per exam, every review assumed to succeed. The variant is patched into the
assistant for the run only: the product's rule is unchanged (D147 rejected it).

    python benchmarks/last_pass.py

Deterministic: one run, no error bar to give.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cps import assistant, service  # noqa: E402
from cps.memory import FSRS_6, Grade, MemoryState  # noqa: E402

START = date(2026, 9, 29)
WINDOWS = (0.0, 3.0, 7.0, 14.0, 21.0, 28.0)


def last_pass_rule(days: float) -> Callable:
    """The self-test rule with the last pass in the `days` before each exam (0: as the
    product has it). It returns the same sessions as `assistant._self_test`, with
    `why` starting "Last pass" for the ones the last pass chose."""

    def rule(
        block,
        topics: Sequence[assistant.Topic],
        state: dict[str, tuple[MemoryState, float]],
        reviewed: set[str],
        prefs: assistant.Preferences,
        held,
        room=None,
        needs_full=None,
    ):
        now = block.start_day
        waiting = needs_full if needs_full is not None else set()
        fading = []
        for t in topics:
            if not (t.available_day <= now < t.exam_day) or held("review", t.name, now):
                continue
            if room is not None and t.name in waiting:
                continue
            memory, last = state[t.name]
            r = prefs.model.retrievability(max(now - last, 0.0), memory.stability)
            if r <= prefs.review_below:
                fading.append((r, r, t, False))
            elif days > 0 and t.exam_day - now <= days:
                at_exam = prefs.model.retrievability(max(t.exam_day - last, 0.0), memory.stability)
                if at_exam < prefs.review_below:
                    fading.append((r, at_exam, t, True))
        if not fading:
            return None
        lowest = min(rank for _, rank, _, _ in fading)
        close = [f for f in fading if f[1] <= lowest + assistant.WEIGHT_TIE]
        recall, _rank, topic, last_pass = min(close, key=lambda f: (-f[2].weight, f[1], f[2].exam_day))
        memory, last = state[topic.name]
        after = prefs.model.review(memory, max(now - last, 0.0), Grade.GOOD)
        state[topic.name] = (after, now)
        first = topic.name not in reviewed
        reviewed.add(topic.name)
        if room is None:
            waiting.discard(topic.name)
        return assistant.Session(
            block,
            "first review" if first else "review",
            topic.course,
            topic.name,
            "",
            "Last pass" if last_pass else "",
            recall=recall,
            stability_before=memory.stability,
            stability_after=after.stability,
        )

    return rule


def results() -> list[dict]:
    report = service.analyse_calendar(
        (ROOT / "examples" / "sample-semester.ics").read_bytes(), start=START, tz="Europe/Paris"
    )
    subjects = [service.SubjectSpec(a.subject, None, familiarity=3) for a in report.assessments]
    original = assistant._self_test
    rows = []
    try:
        for hours in (12, 15, 18, None):
            for days in WINDOWS:
                assistant._self_test = last_pass_rule(days)
                plan = service.make_schedule(
                    report,
                    subjects,
                    weekly_hours=hours,
                    rest_days=("Sun",),
                    practice_hours=4.5,
                    model="fsrs-6",
                )
                state = service._replay(plan.specs, plan.sessions, FSRS_6)
                recall = [
                    FSRS_6.retrievability(
                        max(s["exam_day"] - state[s["name"]][1], 0.0), state[s["name"]][0].stability
                    )
                    for s in plan.specs
                ]
                rows.append(
                    {
                        "hours": hours,
                        "days": days,
                        "sessions": len(plan.sessions),
                        "last_passes": sum(1 for x in plan.sessions if x.rationale == "Last pass"),
                        "ready": sum(s.topics_ready for s in plan.subjects),
                        "topics": len(plan.specs),
                        "recall": mean(recall),
                        "lowest": min(recall),
                        "studied": sum(
                            (datetime.fromisoformat(x.end) - datetime.fromisoformat(x.start)).total_seconds()
                            for x in plan.sessions
                        )
                        / 3600,
                    }
                )
    finally:
        assistant._self_test = original
    return rows


def main() -> int:
    rows = results()
    print("the last pass: a self-test once recall at the exam would be under 90 %, in the last days")
    print(
        f"{'hours a week, last pass':30}{'sessions':>9}{'last passes':>13}{'at 90%+':>9}"
        f"{'mean':>8}{'lowest':>8}{'studied':>9}"
    )
    for r in rows:
        label = f"{'no limit' if r['hours'] is None else str(r['hours']) + ' h'}, " + (
            "none (the product)" if r["days"] == 0 else f"{r['days']:.0f} days"
        )
        print(
            f"{label:30}{r['sessions']:>9}{r['last_passes']:>13}{str(r['ready']) + '/' + str(r['topics']):>9}"
            f"{r['recall']:>8.3f}{r['lowest']:>8.3f}{r['studied']:>8.1f}h"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
