"""
What moving the product to FSRS-6 changes (DECISIONS.md D52).

The assistant plans the synthetic semester of `benchmarks/rule_vs_planner.py` (six
courses, a topic per week of lectures, Sundays off, 4.5 hours of exam practice per
exam) at 12, 15 and 18 hours a week, once with each memory model. For each plan:
its sessions, its self-tests, the topics it self-tests at most once ("once"), the
hours studied; then the topics predicted at 90% or more at their exams, and the mean
and lowest predicted recall at the exams, every review assumed to succeed.

Each plan is scored by both models, each from its own starting states. Neither is
the truth: FSRS-6 predicts recall better than FSRS-4.5 on the open benchmark's
review logs (docs/ENGINE-REVIEW.md W3), which is why the product moves. The rows say
how much the choice of model moves the assistant's plan, and how far the two
models' forecasts are apart on the same sessions.

    python benchmarks/fsrs6_product.py

Deterministic: one run, no error bar to give.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cps import service  # noqa: E402
from cps.memory import MODELS  # noqa: E402

START = date(2026, 9, 29)
HOURS = (12, 15, 18)


def _score(plan: service.PlanReport, specs, model) -> dict:
    """Predicted recall at each topic's exam after `plan`'s sessions, replayed from
    the starting states in `specs` with `model`."""
    state = service._replay(specs, plan.sessions, model)
    recall = [
        model.retrievability(max(s["exam_day"] - state[s["name"]][1], 0.0), state[s["name"]][0].stability)
        for s in specs
    ]
    # The assistant's own measure: the topic's recall at its exam at 0.9 or more.
    return {"recall": mean(recall), "lowest": min(recall), "ready": sum(r >= 0.9 for r in recall)}


def results() -> dict:
    report = service.analyse_calendar(
        (ROOT / "examples" / "sample-semester.ics").read_bytes(), start=START, tz="Europe/Paris"
    )
    subjects = [service.SubjectSpec(a.subject, None, familiarity=3) for a in report.assessments]
    rows = []
    for hours in HOURS:
        plans = {
            name: service.make_schedule(
                report, subjects, weekly_hours=hours, rest_days=("Sun",), practice_hours=4.5, model=name
            )
            for name in MODELS
        }
        names = {name: sorted(s["name"] for s in p.specs) for name, p in plans.items()}
        assert names["fsrs-4.5"] == names["fsrs-6"], "both plans must have the same topics"
        for made in MODELS:
            plan = plans[made]
            minutes = sum(
                (datetime.fromisoformat(s.end) - datetime.fromisoformat(s.start)).total_seconds() / 60
                for s in plan.sessions
            )
            reviews = [s for s in plan.sessions if s.kind in ("review", "first review")]
            per_topic = Counter(s.title for s in reviews)
            row = {
                "hours": hours,
                "made": made,
                "sessions": len(plan.sessions),
                "reviews": len(reviews),
                "studied": minutes / 60,
                "topics": len(plan.specs),
                "planned_ready": sum(s.topics_ready for s in plan.subjects),
                "once": sum(per_topic[s["name"]] < 2 for s in plan.specs),
            }
            for scored in MODELS:
                # Each model's own starting states: a topic seen once and shaky is
                # a different memory state in each.
                row[scored] = _score(plan, plans[scored].specs, MODELS[scored])
            rows.append(row)
    return {"rows": rows}


def main() -> int:
    out = results()
    topics = out["rows"][0]["topics"]
    print(f"{topics} topics; Sundays off, 4.5 hours of practice per exam; every review assumed to succeed\n")
    print(
        f"{'hours, made with':22}{'sessions':>9}{'reviews':>9}{'once':>6}{'studied':>9}"
        f"{'scored by':>11}{'at 90%+':>9}{'recall at exam':>16}{'lowest':>8}"
    )
    for r in out["rows"]:
        for i, scored in enumerate(MODELS):
            label = f"{r['hours']} h, {r['made']}"
            head = (
                f"{label:22}{r['sessions']:>9}{r['reviews']:>9}{r['once']:>6}{r['studied']:>8.1f}h"
                if i == 0
                else " " * 55
            )
            s = r[scored]
            ready = f"{s['ready']}/{r['topics']}"
            print(f"{head}{scored:>11}{ready:>9}{s['recall']:>16.3f}{s['lowest']:>8.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
