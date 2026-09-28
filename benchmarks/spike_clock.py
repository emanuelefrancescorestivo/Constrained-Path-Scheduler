#!/usr/bin/env python3
"""
Spike for milestone M1: which value function gives the planner a clock?

This is a throwaway-quality experiment kept for the record, because the decision
it supports is recorded in docs/PROCESS.md and a reader should be able to rerun
it. It is not imported by the package or the tests.

    python benchmarks/spike_clock.py            # about a minute

Every candidate continuation is plugged into the same one-block-lookahead loop
(window 1) on the benchmark calendar of benchmarks/replanning.py, so the only
thing that changes between rows is the value function:

  budget      the current V(D, S, b): remaining blocks, no clock   (AUDIT item 20)
  A           design A: V(D, S, b, t), skipping a block also consumes the mean
              gap between blocks, a review at retention r consumes its delay
  B-noelapsed design B, V(D, S, t) after a review, queried at the time of the
              last review: the relaxation that drops time-since-last-review
  B-exam      design B with the goal "recall at the exam >= 0.9", i.e.
              S >= stability_for_interval(t, 0.9), queried with elapsed time
  B           design B with the fixed stability target, queried with elapsed
              time: W(D, S, e, t) = min over a >= e of the value of reviewing
              at elapsed a

All solves here are bilinear (accurate, not admissible). Admissibility is the
job of the real implementation; the spike only asks which objective behaves.
"""

from __future__ import annotations

import math
import time
from datetime import date

import numpy as np

from cps.budget import BudgetConfig
from cps.budget import solve as budget_solve
from cps.calendar_io import load_availability
from cps.memory import (
    D_MAX,
    D_MIN,
    Grade,
    MemoryState,
    retrievability,
    review,
    stability_for_interval,
)
from cps.plan import tile_free_time
from cps.ssp import (
    _interp_index,
    _vec_next_difficulty,
    _vec_stability_on_lapse,
    _vec_stability_on_recall,
)
from cps.memory import DEFAULT_WEIGHTS as W

TIMETABLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//benchmark//EN
BEGIN:VEVENT
UID:lecture@benchmark
SUMMARY:Analysis lecture
DTSTART;TZID=Europe/London:20260302T090000
DTEND;TZID=Europe/London:20260302T110000
RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20260525T235959Z
END:VEVENT
END:VCALENDAR
"""
EXAM = 21.0
RHO = 0.9
TARGET = stability_for_interval(EXAM, RHO)
PENALTY = 40.0
TOPICS = [("Analysis", MemoryState(2.0, 7.0)), ("Algebra", MemoryState(4.0, 5.0))]

D_GRID = np.linspace(D_MIN, D_MAX, 19)
S_GRID = np.geomspace(0.05, TARGET, 96)
LOG_S = np.log(S_GRID)


def interp2(table: np.ndarray, d: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Bilinear in (D, log S) over the last two axes of `table`, vectorised."""
    i, wd = _interp_index(D_GRID, np.clip(d, D_MIN, D_MAX))
    j, ws = _interp_index(LOG_S, np.log(np.clip(s, S_GRID[0], S_GRID[-1])))
    return (
        (1 - wd) * (1 - ws) * table[..., i, j]
        + (1 - wd) * ws * table[..., i, j + 1]
        + wd * (1 - ws) * table[..., i + 1, j]
        + wd * ws * table[..., i + 1, j + 1]
    )


# --------------------------------------------------------------------------- #
# Design B: backward sweep over time left
# --------------------------------------------------------------------------- #


class ClockB:
    def __init__(self, goal: str, dt: float = 0.25, horizon: float = EXAM + 1):
        self.goal, self.dt = goal, dt
        n_t = int(round(horizon / dt)) + 1
        self.t_grid = np.arange(n_t) * dt
        d = D_GRID[:, None]
        s = S_GRID[None, :]
        self.V = np.empty((n_t, len(D_GRID), len(S_GRID)))
        d_rec = _vec_next_difficulty(d, Grade.GOOD, W) * np.ones_like(s)
        d_lap = _vec_next_difficulty(d, Grade.AGAIN, W) * np.ones_like(s)
        for j, t in enumerate(self.t_grid):
            done = self._done(s, t) * np.ones_like(d, dtype=bool)
            best = np.full(done.shape, PENALTY)
            for k in range(1, j + 1):
                a = k * dt
                r = (1 + 19 / 81 * a / s) ** -0.5 * np.ones_like(d)
                s_rec = _vec_stability_on_recall(s, d, r, W)
                s_lap = _vec_stability_on_lapse(s, d, r, W)
                nxt = self.V[j - k]
                q = 1 + r * self._value_at(nxt, d_rec, s_rec, t - a) + (1 - r) * self._value_at(
                    nxt, d_lap, s_lap, t - a
                )
                best = np.minimum(best, q)
            best[done] = 0.0
            self.V[j] = best

    def _done(self, s, t):
        if self.goal == "exam":
            return s >= stability_for_interval(max(t, 1e-9), RHO)
        return s >= TARGET

    def _value_at(self, table, d, s, t_left):
        out = interp2(table, d, s)
        return np.where(self._done(s, t_left), 0.0, out)

    def post_review(self, d: float, s: float, t_left: float) -> float:
        """V(D, S, t): value right after a review, t days before the exam."""
        if t_left <= 0:
            return 0.0 if self._done(s, 0.0) else PENALTY
        if self._done(s, t_left):
            return 0.0
        pos = min(t_left / self.dt, len(self.t_grid) - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(self.t_grid) - 1)
        f = pos - lo
        a = float(interp2(self.V[lo], np.array([d]), np.array([s]))[0])
        b = float(interp2(self.V[hi], np.array([d]), np.array([s]))[0])
        return (1 - f) * a + f * b

    def waiting(self, d: float, s: float, elapsed: float, t_left: float) -> float:
        """W(D, S, e, t): not reviewed for `elapsed` days, `t_left` to the exam.
        The next review can only happen at an elapsed time of at least `e`."""
        if self._done(s, 0.0 if t_left <= 0 else 0.0) and self.goal == "fixed":
            return 0.0
        if t_left <= 0:
            return 0.0 if (self.goal == "exam" and retrievability(elapsed, s) >= RHO) else PENALTY
        if self.goal == "exam" and retrievability(elapsed + t_left, s) >= RHO:
            return 0.0
        best = PENALTY
        state = MemoryState(s, d)
        for m in range(int(t_left / self.dt) + 1):
            a = elapsed + m * self.dt
            left = t_left - m * self.dt
            r = retrievability(a, s)
            good = review(state, a, Grade.GOOD)
            bad = review(state, a, Grade.AGAIN)
            q = 1 + r * self.post_review(good.difficulty, good.stability, left) + (
                1 - r
            ) * self.post_review(bad.difficulty, bad.stability, left)
            best = min(best, q)
        return best


# --------------------------------------------------------------------------- #
# Design A: budget recursion plus a days-left dimension
# --------------------------------------------------------------------------- #


class ClockA:
    """V(D, S, b, t). A skip consumes one block and the mean gap between blocks;
    a review at retention r consumes one block and its delay. Memory does not age
    on a skip, because (D, S) has no elapsed-time component: that inconsistency is
    part of what the spike is testing."""

    def __init__(self, n_blocks: int, gap: float, dt: float = 0.5, horizon: float = EXAM + 1):
        self.gap, self.dt = gap, dt
        self.t_grid = np.arange(int(round(horizon / dt)) + 1) * dt
        n_t = len(self.t_grid)
        retentions = np.linspace(0.05, 0.999, 40)
        d = D_GRID[None, :, None]
        s = S_GRID[None, None, :]
        r = retentions[:, None, None]
        delay = s / (19 / 81) * (r ** -2 - 1)
        s_rec = _vec_stability_on_recall(s, d, r, W) * np.ones_like(r)
        s_lap = _vec_stability_on_lapse(s, d, r, W) * np.ones_like(r)
        d_rec = _vec_next_difficulty(d, Grade.GOOD, W) * np.ones_like(s_rec)
        d_lap = _vec_next_difficulty(d, Grade.AGAIN, W) * np.ones_like(s_rec)
        at_goal = S_GRID >= TARGET
        self.V = np.empty((n_blocks + 1, n_t, len(D_GRID), len(S_GRID)))
        self.V[0] = PENALTY
        self.V[0][..., at_goal] = 0.0
        for b in range(1, n_blocks + 1):
            prev = self.V[b - 1]
            for j, t in enumerate(self.t_grid):
                skip_j = max(int(math.floor((t - gap) / dt)), 0)
                skip = prev[skip_j] if t - gap >= 0 else self.V[0][0]
                left = t - delay
                feasible = left >= 0
                lj = np.clip(np.floor(left / dt).astype(int), 0, n_t - 1)
                v_rec = self._gather(prev, lj, d_rec, s_rec)
                v_lap = self._gather(prev, lj, d_lap, s_lap)
                q = 1 + r * v_rec + (1 - r) * v_lap
                q = np.where(feasible, q, np.inf)
                best = np.minimum(q.min(axis=0), skip)
                best[:, at_goal] = 0.0
                self.V[b, j] = best

    @staticmethod
    def _gather(prev, lj, d, s):
        i, wd = _interp_index(D_GRID, np.clip(d, D_MIN, D_MAX))
        k, ws = _interp_index(LOG_S, np.log(np.clip(s, S_GRID[0], S_GRID[-1])))
        out = (
            (1 - wd) * (1 - ws) * prev[lj, i, k]
            + (1 - wd) * ws * prev[lj, i, k + 1]
            + wd * (1 - ws) * prev[lj, i + 1, k]
            + wd * ws * prev[lj, i + 1, k + 1]
        )
        return np.where(s >= TARGET, 0.0, out)

    def value(self, d, s, b, t_left):
        if s >= TARGET:
            return 0.0
        b = int(np.clip(b, 0, self.V.shape[0] - 1))
        j = int(np.clip(math.floor(max(t_left, 0) / self.dt), 0, len(self.t_grid) - 1))
        return float(interp2(self.V[b, j], np.array([d]), np.array([s]))[0])


# --------------------------------------------------------------------------- #
# One-block lookahead with a pluggable continuation
# --------------------------------------------------------------------------- #


def run(blocks, cost, rng, ready_target=TARGET):
    """cost(topic_state, now, blocks_left_after) -> continuation for one topic.
    topic_state is (MemoryState, last_review_day)."""
    state = [(m, 0.0) for _, m in TOPICS]
    log = []
    for n, block in enumerate(blocks):
        if all(m.stability >= TARGET for m, _ in state):
            break
        now = block.start_day
        nxt = blocks[n + 1].start_day if n + 1 < len(blocks) else EXAM
        left = len(blocks) - n - 1
        base = [cost(st, nxt, left) for st in state]
        options = [(sum(base), None)]
        for i, (m, last) in enumerate(state):
            if m.stability >= TARGET:
                continue
            e = now - last
            r = retrievability(e, m.stability)
            exp = 0.0
            for p, g in ((r, Grade.GOOD), (1 - r, Grade.AGAIN)):
                after = review(m, e, g)
                exp += p * cost((after, now), nxt, left)
            options.append((1 + exp + sum(base) - base[i], i))
        best = min(options, key=lambda o: o[0])[1]
        if best is None:
            continue
        m, last = state[best]
        e = now - last
        r = retrievability(e, m.stability)
        ok = rng.random() < r if rng is not None else True
        after = review(m, e, Grade.GOOD if ok else Grade.AGAIN)
        log.append((block.day, best, r, ok))
        state[best] = (after, now)
    ready = all(m.stability >= ready_target for m, _ in state)
    exam_recall = [retrievability(EXAM - last, m.stability) for m, last in state]
    return log, ready, exam_recall


def main() -> None:
    grid, _ = load_availability(TIMETABLE, date(2026, 3, 2), int(EXAM), "Europe/London")
    blocks = tile_free_time(grid, block_slots=3, max_blocks_per_day=2)
    gap = EXAM / len(blocks)
    print(f"{len(blocks)} blocks, mean gap {gap:.2f} days, target S={TARGET:.0f}, penalty {PENALTY:g}")

    started = time.perf_counter()
    budget = budget_solve(BudgetConfig(target_stability=TARGET, failure_penalty=PENALTY))
    b_fixed = ClockB("fixed")
    b_exam = ClockB("exam")
    a = ClockA(len(blocks), gap)
    print(f"solves took {time.perf_counter() - started:.0f} s")

    candidates = {
        "budget": lambda st, now, left: budget.expected_blocks(st[0].difficulty, st[0].stability, left),
        "A": lambda st, now, left: a.value(st[0].difficulty, st[0].stability, left, EXAM - now),
        "B-noelapsed": lambda st, now, left: b_fixed.post_review(
            st[0].difficulty, st[0].stability, EXAM - st[1]
        ),
        "B-exam": lambda st, now, left: b_exam.waiting(
            st[0].difficulty, st[0].stability, now - st[1], EXAM - now
        ),
        "B": lambda st, now, left: b_fixed.waiting(
            st[0].difficulty, st[0].stability, now - st[1], EXAM - now
        ),
    }

    print("\ndeterministic run (every recall succeeds): (day, topic, recall at review)")
    for name, cost in candidates.items():
        log, ready, recall = run(blocks, cost, None)
        sessions = ", ".join(f"d{d} {TOPICS[i][0][:3]} {r:.2f}" for d, i, r, _ in log)
        print(f"  {name:<12} {sessions or 'nothing'}")

    seeds = 100
    print(f"\nstochastic ({seeds} seeds): ready means S >= {TARGET:.0f} for both topics")
    print(f"  {'':<12} {'blocks':>6} {'first day':>9} {'ready':>12} {'recall at exam':>15}")
    for name, cost in candidates.items():
        used, firsts, ok, recalls = [], [], 0, []
        for seed in range(seeds):
            log, ready, recall = run(blocks, cost, np.random.default_rng(seed))
            used.append(len(log))
            if log:
                firsts.append(log[0][0])
            ok += ready
            recalls.append(np.mean(recall))
        p = ok / seeds
        se = math.sqrt(p * (1 - p) / seeds)
        first = f"{np.mean(firsts):.1f}" if firsts else "-"
        print(f"  {name:<12} {np.mean(used):>6.2f} {first:>9} {p:>7.0%} ± {se:.0%} "
              f"{np.mean(recalls):>10.3f}")


if __name__ == "__main__":
    main()
