"""
Discrete time grid with a real bitmask.

Section 3.1 of the January report claims the schedule is stored as "a binary
bitmask" giving "O(1) constant-time" constraint checks. The code did this:

    self.grid = np.zeros(self.total_slots, dtype=int)
    ...
    return np.sum(self.grid[idx: idx + duration]) == 0

That is an int64 occupancy *array*, and the check is O(duration) with a NumPy
call overhead per node. The claim was wrong on both the data structure and the
complexity.

This module implements what the report described. A Python `int` is an
arbitrary-precision bit vector, so the entire 60-day calendar is one integer and
an availability check is a single mask-and-compare:

    (busy & window) == 0

Two consequences that matter more than the constant factor:

  * The state is immutable and hashable for free. A* holds thousands of
    candidate schedules on the frontier at once; with a mutable NumPy array you
    must deep-copy per node or risk one branch corrupting another. With an int
    you cannot make that mistake.
  * Whole-calendar set operations (union of constraints, intersection of two
    students' free time) become single bitwise ops.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterator, Sequence

SLOTS_PER_DAY: int = 48  # 30-minute quanta
SLOTS_PER_HOUR: int = 2


@dataclass(frozen=True, slots=True)
class TimeGrid:
    """Occupancy over `days` days. Bit i set == slot i is busy."""

    days: int
    busy: int = 0
    slots_per_day: int = SLOTS_PER_DAY

    def __post_init__(self) -> None:
        if self.days <= 0:
            raise ValueError("days must be positive")
        if self.busy < 0:
            raise ValueError("busy mask must be non-negative")
        if self.busy >> self.total_slots:
            raise ValueError("busy mask has bits set beyond the horizon")

    # -- geometry ------------------------------------------------------------ #

    @property
    def total_slots(self) -> int:
        return self.days * self.slots_per_day

    def slot(self, day: int, hour: int, minute: int = 0) -> int:
        """Absolute slot index for a wall-clock time. Raises on out-of-range so
        that a bad constraint fails loudly instead of silently wrapping."""
        if not 0 <= day < self.days:
            raise ValueError(f"day {day} outside horizon of {self.days} days")
        if not 0 <= hour < 24 or minute not in (0, 30):
            raise ValueError(f"unsupported wall-clock time {hour:02d}:{minute:02d}")
        return day * self.slots_per_day + hour * SLOTS_PER_HOUR + minute // 30

    def _window(self, start: int, duration: int) -> int:
        return ((1 << duration) - 1) << start

    # -- queries (O(1) in the number of existing events) --------------------- #

    def is_free(self, start: int, duration: int = 1) -> bool:
        if duration <= 0:
            raise ValueError("duration must be positive")
        if start < 0 or start + duration > self.total_slots:
            return False
        return self.busy & self._window(start, duration) == 0

    def free_starts(self, duration: int) -> Iterator[int]:
        """Every slot index at which a block of `duration` fits."""
        for start in range(self.total_slots - duration + 1):
            if self.busy & self._window(start, duration) == 0:
                yield start

    def busy_slots(self) -> int:
        return self.busy.bit_count()

    # -- transitions (return new grids; never mutate) ------------------------ #

    def occupy(self, start: int, duration: int = 1) -> "TimeGrid":
        """Mark a block busy. Raises if it does not fit, rather than clipping:
        a scheduler that silently drops half a study block is worse than one
        that crashes."""
        if not self.is_free(start, duration):
            raise ValueError(f"slots [{start}, {start + duration}) are not free")
        return replace(self, busy=self.busy | self._window(start, duration))

    def block(self, start: int, duration: int) -> "TimeGrid":
        """Mark a block busy idempotently, clipped to the horizon. For external
        constraints (sleep, classes) that may legitimately overlap each other
        and may run past the end of the planning window."""
        if duration <= 0:
            return self
        # A constraint beginning before the horizon still blocks its tail.
        start_c = max(0, start)
        end_c = min(self.total_slots, start + duration)
        if end_c <= start_c:
            return self
        return replace(self, busy=self.busy | self._window(start_c, end_c - start_c))

    def block_daily(self, start_hour: float, end_hour: float, days: Sequence[int] | None = None) -> "TimeGrid":
        """Block a recurring wall-clock window on the given days.

        Handles windows that cross midnight (23:00-07:00 sleep) by treating them
        as running into the following day and clipping at the horizon. The
        January code had no wrap handling at all, so an overnight constraint
        either silently vanished or -- with a modulo -- reappeared at 00:00 of
        the *same* day, freeing the night it was meant to protect.
        """
        # Starting at -1, not 0. A window that crosses midnight also arrives from
        # the day *before* the horizon, and `block` clips the negative start. Without
        # this, 00:00-07:00 on day 0 stayed free: the scheduler was offered a study
        # block at midnight on the first night, which -- with zero elapsed time since
        # the topic's last review -- is a block that costs one unit and teaches
        # nothing. On a five-block instance that was enough to make "do nothing" the
        # optimal plan. Harmless for non-wrapping windows, which clip to empty.
        target_days = range(-1, self.days) if days is None else days
        span_hours = end_hour - start_hour
        if span_hours <= 0:
            span_hours += 24.0  # crosses midnight
        duration = int(round(span_hours * SLOTS_PER_HOUR))
        grid = self
        for day in target_days:
            start = day * self.slots_per_day + int(round(start_hour * SLOTS_PER_HOUR))
            grid = grid.block(start, duration)
        return grid

    # -- convenience --------------------------------------------------------- #

    def day_of(self, slot: int) -> int:
        return slot // self.slots_per_day

    def days_from_start(self, slot: int) -> float:
        """Slot index expressed in days, for feeding elapsed time to the memory
        model. Keeping this conversion in one place is deliberate: the January
        code mixed slot indices and days in the same arithmetic."""
        return slot / self.slots_per_day
