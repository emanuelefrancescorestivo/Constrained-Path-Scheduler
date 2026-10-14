"""
How many answers before a student's own FSRS-6 parameters beat the population's
(DECISIONS.md D53)?

Made-up students, each with parameters of their own (`fitting.student`: every
parameter the population's times e^N(0, spread)), study a flashcard deck as the
product schedules it, ten new cards a day for 120 days (`fitting.answers`). Their
parameters are fitted (`fitting.fit`) to the answers they gave until their N-th
scored answer, and tested on the answers of the three weeks after it (the product
fits again as answers accrue): the mean log loss of its predictions there, against
the population's parameters and the student's true ones. With `spread` 0 the
student is the population, and the fit must do no harm.

    python benchmarks/personal_fsrs.py              # the published table, about 4 minutes
    python benchmarks/personal_fsrs.py --prior      # the prior's scale, on other students

Simulated students, so this measures the fitter, not people: how far real students
are from the population, and how they choose a grade, are assumptions stated in
`cps/fitting.py`. Fixed seeds; each row is a mean over students with its standard
error.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from random import Random
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cps import fitting  # noqa: E402
from cps.console import ensure_utf8_output  # noqa: E402
from cps.memory import DEFAULT_WEIGHTS_6  # noqa: E402

DAYS = 120
AHEAD = 21.0  # days after the fit whose answers it is tested on
SIZES = (100, 200, 400, 800, 1600)
STUDENTS = range(20)  # the published table's seeds
TUNING = range(100, 116)  # the prior's scale is chosen on these, not on the table's
PRIORS = (0.02, 0.05, 0.1, 0.2)


def _cut(logs, n: int) -> float:
    """The time of the n-th scored answer: fitting on what came before it."""
    times = sorted(
        at for log in logs for j, (at, e, _) in enumerate(log) if j > 0 and e is not None and e >= 1
    )
    return times[n - 1] + 1e-9 if len(times) >= n else float("inf")


def one(seed: int, spread: float, sizes=SIZES, prior: float = fitting.PRIOR_SCALE) -> list[dict]:
    rng = Random(seed)
    truth = fitting.student(rng, spread) if spread > 0 else DEFAULT_WEIGHTS_6
    logs = fitting.answers(truth, rng, days=DAYS)
    rows = []
    for n in sizes:
        cut = _cut(logs, n)
        if cut + AHEAD > DAYS:
            raise ValueError(f"seed {seed}: {n} scored answers come too late to test three weeks after")
        train, test, mask = fitting.split(logs, cut, cut + AHEAD)
        fitted = fitting.fit(train, prior_scale=prior)
        rows.append(
            {
                "n": fitted.reviews,
                "size": n,
                "default": fitting.log_loss(DEFAULT_WEIGHTS_6, test, mask),
                "fitted": fitting.log_loss(fitted.weights, test, mask),
                "truth": fitting.log_loss(truth, test, mask),
            }
        )
    return rows


def table(seeds, spread: float, sizes=SIZES, prior: float = fitting.PRIOR_SCALE) -> list[dict]:
    runs = [one(seed, spread, sizes, prior) for seed in seeds]
    out = []
    for k, size in enumerate(sizes):
        rows = [run[k] for run in runs]
        gain = [r["fitted"] - r["default"] for r in rows]
        closed = [
            (r["default"] - r["fitted"]) / (r["default"] - r["truth"])
            for r in rows
            if r["default"] - r["truth"] > 1e-4
        ]
        out.append(
            {
                "size": size,
                "change": mean(gain),
                "se": stdev(gain) / len(gain) ** 0.5,
                "better": sum(g < 0 for g in gain) / len(gain),
                "closed": mean(closed) if closed else float("nan"),
                "default": mean(r["default"] for r in rows),
                "truth": mean(r["truth"] for r in rows),
            }
        )
    return out


def threshold(own: list[dict], same: list[dict]) -> int | None:
    """The fewest answers at which the fit is better by more than two standard
    errors, for at least three students in four, and costs a student who is the
    population less than 0.001 (stated choices)."""
    for a, b in zip(own, same, strict=True):
        if a["change"] + 2 * a["se"] < 0 and a["better"] >= 0.75 and b["change"] < 0.001:
            return int(a["size"])
    return None


def _print(title: str, rows: list[dict]) -> None:
    print(title)
    print(
        f"{'answers':>8}{'change in log loss':>24}{'students better':>17}{'gap closed':>12}"
        f"{'population':>12}{'own':>8}"
    )
    for r in rows:
        print(
            f"{r['size']:>8}{r['change']:>+15.4f} ± {r['se']:.4f}{r['better']:>17.0%}"
            f"{'-' if r['closed'] != r['closed'] else format(r['closed'], '.0%'):>12}"
            f"{r['default']:>12.4f}{r['truth']:>8.4f}"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--prior", action="store_true", help="compare the prior's scales on the tuning students"
    )
    args = parser.parse_args(argv)
    ensure_utf8_output()
    begin = time.perf_counter()
    if args.prior:
        for prior in PRIORS:
            sizes = (200, 400, 800)
            _print(f"prior scale {prior}, spread 0.3 (tuning students)", table(TUNING, 0.3, sizes, prior))
            _print(f"prior scale {prior}, spread 0 (tuning students)", table(TUNING, 0.0, sizes, prior))
    else:
        own = table(STUDENTS, 0.3)
        same = table(STUDENTS, 0.0)
        _print(
            f"{len(STUDENTS)} students whose parameters differ (spread 0.3), prior {fitting.PRIOR_SCALE}", own
        )
        _print(f"{len(STUDENTS)} students who are the population (spread 0)", same)
        found = threshold(own, same)
        said = found if found else f"none in {SIZES}"
        print(f"threshold: {said} scored answers (fitting.MIN_REVIEWS = {fitting.MIN_REVIEWS})")
    print(f"in {time.perf_counter() - begin:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
