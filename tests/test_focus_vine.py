"""
Focus, the owner's B (DECISIONS.md D179): the clock's ring is the vine, and the clock, the
vine and the diary read one number, the minutes on the page.

What must hold: the clock shows the time on the page, the time away taken off (AUDIT 119:
it showed the time since the start, eight minutes ahead of the stem on the owner's phone);
the vine's next flower is counted from the same number; its buds and leaves sit on the ring,
no leaf on a bud. A session started in one tap (Today, Join, a session's card) keeps the
25 + 5 rhythm the student last chose, on to start (AUDIT 120: those sessions had none). The
line of classmates draws its chevron itself, not through the summary's pseudo-elements
(AUDIT 121).
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, charts, create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 18:00 in Paris


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    sub = service.finish_first_run(sub)
    service.save_subscription(root, sub)
    return service.load_subscription(root, sub.token)


def test_the_clock_shows_the_minutes_on_the_page_as_the_vine_counts_them(tmp_path):
    sub = service.start_focus(_plan(tmp_path), "Algebra 3", goal=25, now=NOW)
    sub = service.focus_beat(sub, now=NOW + timedelta(minutes=1))
    # Ten minutes off the page, then back: 9.5 of them away (a heartbeat's grace kept).
    sub = service.focus_beat(sub, now=NOW + timedelta(minutes=11))
    for m in range(12, 26):
        sub = service.focus_beat(sub, now=NOW + timedelta(minutes=m))
    view = service.focus_view(sub, NOW + timedelta(minutes=25))
    assert view["elapsed"] == 25 * 60 and view["away"] == 570 and view["focused"] == 25 * 60 - 570
    assert view["stem"]["next"] == 10 and view["grace"] == 75  # 15.5 minutes in: the length at 25
    service.save_subscription(tmp_path, sub)
    config = Config(
        store=tmp_path, clock=lambda: NOW + timedelta(minutes=25), background=False, sweep_every=None
    )
    with TestClient(create_app(config)) as web:
        page = unescape(web.get(f"/p/{sub.token}/focus").text)
    assert 'id="focus-timer" aria-live="off">15:30<' in page and "Next flower in 10 min" in page
    assert 'data-grace="75"' in page and 'class="focus-vine"' in page and "focus-sweep" not in page
    # Past an hour, hours come first.
    long = service.start_focus(_plan(tmp_path), "Algebra 3", goal=90, now=NOW)
    service.save_subscription(tmp_path, long)
    config = Config(
        store=tmp_path,
        clock=lambda: NOW + timedelta(minutes=62, seconds=5),
        background=False,
        sweep_every=None,
    )
    with TestClient(create_app(config)) as web:
        page = unescape(web.get(f"/p/{long.token}/focus").text)
    assert 'aria-live="off">1:02:05<' in page


def test_the_vines_buds_and_leaves_sit_on_the_ring():
    stem = service._focus_stem(25, 24 * 60)
    vine = charts.vine(stem)
    assert [b["label"] for b in vine["buds"]] == ["25 min", "30 min", "1 h"]
    for bud in vine["buds"]:
        assert math.hypot(bud["x"] - 100, bud["y"] - 100) == pytest.approx(charts.VINE_R, abs=0.1)
        label = math.hypot(bud["lx"] - 100, bud["ly"] - 4 - 100)
        assert label == pytest.approx(charts.VINE_R + charts.VINE_LABEL, abs=0.1)
    # The hour's bud sits just short of the top, where the vine starts.
    top = vine["buds"][-1]
    assert top["x"] < 100 and top["y"] < 10
    shares = [float(b["share"]) for b in stem["buds"]]
    assert vine["leaves"] and all(
        min(abs(float(leaf["share"]) - s) for s in shares) >= charts.VINE_CLEAR for leaf in vine["leaves"]
    )
    # A length whose rewards fall between the leaves' places keeps them all; one whose rewards
    # fall on them (1 h 15: a bud at 30 minutes, two fifths of the ring) leaves those out.
    assert len(vine["leaves"]) == len(service.FOCUS_STEM_LEAVES)
    assert len(charts.vine(service._focus_stem(75, 0))["leaves"]) == len(service.FOCUS_STEM_LEAVES) - 2
    assert vine["percent"] == 40  # 24 of 60 minutes round the ring


def test_a_session_started_in_one_tap_keeps_the_rhythm_last_chosen(tmp_path):
    sub = _plan(tmp_path)
    assert service.focus_rhythm_default(sub)
    clock = [NOW]
    config = Config(store=tmp_path, clock=lambda: clock[0], background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        start = unescape(web.get(f"/p/{sub.token}/focus").text)
        assert 'name="rhythm_set" value="1"' in start and 'name="rhythm" value="1" checked' in start
        # Today's one tap sends only the course and the length: the rhythm comes with it.
        web.post(f"/p/{sub.token}/focus/start", data={"course": "Algebra 3", "goal": "25"})
        assert service.load_subscription(tmp_path, sub.token).options["focus"]["rhythm"] is True
        web.post(f"/p/{sub.token}/focus/cancel")
        # The switch turned off on the focus page: off, and remembered.
        web.post(f"/p/{sub.token}/focus/start", data={"course": "Algebra 3", "goal": "25", "rhythm_set": "1"})
        after = service.load_subscription(tmp_path, sub.token)
        assert after.options["focus"]["rhythm"] is False and not service.focus_rhythm_default(after)
        web.post(f"/p/{sub.token}/focus/cancel")
        web.post(f"/p/{sub.token}/focus/start", data={"course": "Algebra 3", "goal": "25"})
        assert service.load_subscription(tmp_path, sub.token).options["focus"]["rhythm"] is False
        web.post(f"/p/{sub.token}/focus/cancel")
        start = unescape(web.get(f"/p/{sub.token}/focus").text)
        assert 'name="rhythm" value="1">' in start  # the switch shows the choice


def test_the_line_of_classmates_draws_its_own_chevron():
    page = (ROOT / "src/cps/web/templates/focus.html").read_text(encoding="utf-8")
    assert '<span class="live-chev" aria-hidden="true">›</span>' in page
    css = (ROOT / "src/cps/web/static/style.css").read_text(encoding="utf-8")
    assert ".focus-live > summary::marker" in css and '.focus-live > summary::after { content: "›"' not in css
