"""The front page's week (DECISIONS.md D191), made by the app's own rules.

    python benchmarks/landing_week.py

Plans the sample second year (examples/sample-semester.ics) from its first day, reports
every session done until the Tuesday morning of its fourth week, takes that Tuesday off,
and writes the week before and after to src/cps/data/landing_week.json, which the front
page draws. A server's package has no examples, so the page reads the file;
tests/test_landing.py makes the week again and checks the file is the same. Run this
when the planner changes.
"""

from __future__ import annotations

import json

from cps import service
from cps.showcase import sample_path


def main() -> None:
    week = service.landing_week_compute(sample_path().read_bytes())
    service.LANDING_WEEK_FILE.write_text(json.dumps(week, indent=1) + "\n", encoding="utf-8")
    moved = sum(1 for s in week["sessions"] if s["to"] and (s["to"][0], s["to"][1]) != (s["day"], s["start"]))
    print(
        f"{len(week['sessions'])} sessions, {moved} moved, {len(week['busy'])} classes -> {service.LANDING_WEEK_FILE}"
    )


if __name__ == "__main__":
    main()
