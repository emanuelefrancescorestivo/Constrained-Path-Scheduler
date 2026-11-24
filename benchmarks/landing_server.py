"""The front page's screenshots (D188), part one: the demo's made-up class, its clock held.

    python benchmarks/landing_server.py <empty directory> <port>

Makes the public demo's world (cps.showcase) in the directory as it stood on
2026-11-24 at 10:40 in Paris, a Tuesday morning in its week of 16 November, with
one student in English, French and Italian in their second year and one in their
first, then serves it with the app's clock held at that moment, so that the pictures
are the same whenever they are taken. It prints one line of JSON, the students'
plans by language and year, then serves until stopped. benchmarks/landing_shots.mjs
starts it and takes the pictures. Everyone in it is made up.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from cps.showcase import add_student, seed_world
from cps.web import Config, create_app

MOMENT = datetime(2026, 11, 24, 9, 40, tzinfo=UTC)  # 10:40 in Paris
HANDLES = {"en": ("alex", "sam"), "fr": ("camille", "sacha"), "it": ("giulia", "matteo")}


def main() -> None:
    where, port = Path(sys.argv[1]), int(sys.argv[2])
    where.mkdir(parents=True, exist_ok=True)
    if any(where.iterdir()):
        raise SystemExit(f"{where} is not empty")
    world = seed_world(where, real_now=MOMENT)
    plans: dict[str, dict[str, str]] = {}
    for n, (lang, (second, first)) in enumerate(HANDLES.items()):
        plans[lang] = {
            "2": add_student(where, world, second, real_now=MOMENT, seed=2 * n, lang=lang, year="2").token,
            "1": add_student(where, world, first, real_now=MOMENT, seed=2 * n + 1, lang=lang, year="1").token,
        }
    print(json.dumps(plans), flush=True)
    config = Config.from_env()
    config.store = where
    config.background = False
    config.sweep_every = None
    config.remind_every = None
    config.clock = lambda: MOMENT
    uvicorn.run(create_app(config), host="127.0.0.1", port=port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
