"""Tests for the bitmask time grid."""

from __future__ import annotations

import pytest

from cps.timegrid import SLOTS_PER_DAY, TimeGrid


def test_slot_arithmetic():
    grid = TimeGrid(days=7)
    assert grid.total_slots == 7 * 48
    assert grid.slot(0, 0, 0) == 0
    assert grid.slot(0, 9, 30) == 19
    assert grid.slot(1, 0, 0) == 48
    with pytest.raises(ValueError):
        grid.slot(7, 0)  # day past the horizon
    with pytest.raises(ValueError):
        grid.slot(0, 9, 15)  # not on a 30-minute boundary


def test_empty_grid_is_entirely_free():
    grid = TimeGrid(days=3)
    assert grid.busy_slots() == 0
    assert grid.is_free(0, grid.total_slots)


def test_occupy_is_non_mutating():
    """A* holds thousands of candidate schedules at once; a mutating grid means
    one branch of the search can silently corrupt another."""
    original = TimeGrid(days=3)
    modified = original.occupy(10, 3)
    assert original.busy == 0
    assert modified.busy_slots() == 3
    assert original is not modified


def test_grid_is_hashable_and_usable_as_a_closed_set_key():
    a = TimeGrid(days=2).occupy(4, 3)
    b = TimeGrid(days=2).occupy(4, 3)
    assert a == b and hash(a) == hash(b)
    assert len({a, b}) == 1


def test_overlapping_occupy_is_rejected():
    grid = TimeGrid(days=2).occupy(10, 3)
    assert not grid.is_free(12, 2)
    with pytest.raises(ValueError):
        grid.occupy(12, 2)


def test_block_out_of_range_is_rejected_not_wrapped():
    grid = TimeGrid(days=1)
    assert not grid.is_free(46, 4)  # would run past midnight of the last day
    with pytest.raises(ValueError):
        grid.occupy(46, 4)


def test_overnight_constraint_does_not_wrap_to_the_same_morning():
    """The 23:00-07:00 sleep block regression.

    A naive implementation that takes the window modulo the day length blocks
    00:00-07:00 *and* 23:00-24:00 of the same day, which leaves the night
    between them free -- the algorithm then cheerfully schedules study at 02:00.
    """
    grid = TimeGrid(days=3).block_daily(start_hour=23, end_hour=7)

    # Night between day 0 and day 1 must be fully blocked.
    for hour in (23, 0, 3, 6):
        day = 0 if hour == 23 else 1
        assert not grid.is_free(grid.slot(day, hour)), f"day {day} {hour:02d}:00 should be blocked"

    # Daytime on day 1 must survive.
    assert grid.is_free(grid.slot(1, 12), 4)

    # The night arriving from before the horizon must be blocked too.
    assert not grid.is_free(grid.slot(0, 3)), "day 0 at 03:00 should be blocked"
    assert not grid.is_free(0, 1), "the very first slot of the horizon should be blocked"
    # 14 slots inherited from day -1, three full 16-slot nights, tail clipped: 48.
    assert grid.busy_slots() == 14 + 16 + 16 + 2


def test_daytime_constraint_blocks_only_weekdays_when_asked():
    grid = TimeGrid(days=7).block_daily(9, 17, days=[0, 1, 2, 3, 4])
    assert not grid.is_free(grid.slot(2, 10))
    assert grid.is_free(grid.slot(5, 10), 4)  # Saturday


def test_free_starts_respects_block_length():
    grid = TimeGrid(days=1).block_daily(0, 22)  # only 22:00-24:00 free
    starts_for_3h = list(grid.free_starts(6))
    starts_for_2h = list(grid.free_starts(4))
    assert starts_for_3h == []
    assert starts_for_2h == [grid.slot(0, 22)]


def test_realistic_student_week_leaves_plausible_study_time():
    """Sleep 23-07, classes 09-17 on weekdays, 1h commute each side."""
    grid = TimeGrid(days=7)
    grid = grid.block_daily(23, 7)
    grid = grid.block_daily(8, 18, days=[0, 1, 2, 3, 4])
    free_hours = (grid.total_slots - grid.busy_slots()) / 2
    assert 30 < free_hours < 80, free_hours
    # At least one 1.5-hour deep-work block must exist on a weekday evening.
    assert any(grid.day_of(s) == 2 for s in grid.free_starts(3))


def test_days_from_start_conversion():
    grid = TimeGrid(days=10)
    assert grid.days_from_start(0) == 0.0
    assert grid.days_from_start(SLOTS_PER_DAY) == 1.0
    assert grid.days_from_start(grid.slot(3, 12)) == pytest.approx(3.5)


def test_mask_cannot_exceed_the_horizon():
    with pytest.raises(ValueError):
        TimeGrid(days=1, busy=1 << 48)
