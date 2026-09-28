"""
The January 2026 heuristic, preserved verbatim.

This is not dead code and it is not here for nostalgia. It is here so that the
central defect of the first version is a *failing test* rather than a claim in a
post-mortem. `tests/test_memory.py` runs the same property suite against both
this model and the FSRS model; the legacy model is marked `xfail(strict=True)`,
which means the build breaks if it ever starts passing.

Transcribed from the final report, Appendix A.2.
"""

from __future__ import annotations

import math

from .memory import Grade, MemoryState, Weights, DEFAULT_WEIGHTS, review


class LegacyHeuristic:
    """Verbatim reimplementation of `FSRSHeuristic` from Appendix A.2."""

    def __init__(self, target_stability: float = 14.0) -> None:
        self.target = target_stability

    def calculate_deficit(self, stability: float) -> float:
        if stability >= self.target:
            return 0.0
        return self.target - stability

    def predict_next_stability(self, current_S: float, difficulty: float) -> float:
        factor = 1 + (math.exp(difficulty / 10) * 0.2)
        return current_S * factor

    # -- adapter onto the interface the property tests use ------------------- #

    def stability_after_good_review(self, state: MemoryState, elapsed_days: float) -> float:
        """Note the signature: `elapsed_days` is accepted and then discarded,
        because the underlying formula has nowhere to put it. That is the bug."""
        del elapsed_days
        return self.predict_next_stability(state.stability, state.difficulty)


class FSRSModel:
    """Adapter exposing the corrected model through the same interface."""

    def __init__(self, weights: Weights = DEFAULT_WEIGHTS) -> None:
        self.weights = weights

    def stability_after_good_review(self, state: MemoryState, elapsed_days: float) -> float:
        return review(state, elapsed_days, Grade.GOOD, self.weights).stability
