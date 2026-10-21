"""D76: the sky behind a plan's pages follows the real sun where the plan lives:
the sun's height from NOAA's equations, folded into the day's position (0 night, 1
sunrise, 2 the highest sun, 3 sunset), and the sky's colours along it."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from cps import daylight
from cps.web import Config, create_app, sky

PARIS = ZoneInfo("Europe/Paris")
SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"


def test_a_time_zone_is_placed_at_its_main_city():
    latitude, longitude = daylight.place("Europe/Paris")
    assert latitude == pytest.approx(48.87, abs=0.01) and longitude == pytest.approx(2.33, abs=0.01)
    latitude, longitude = daylight.place("Australia/Sydney")
    assert latitude == pytest.approx(-33.87, abs=0.01) and longitude == pytest.approx(151.22, abs=0.01)
    # A zone the tables do not place: its UTC offset gives the longitude, at latitude 45.
    assert daylight.place("Etc/GMT-2", datetime(2026, 1, 1, tzinfo=UTC)) == (45.0, 30.0)


@pytest.mark.parametrize(
    ("tz", "day", "expected"),
    [
        # 90 - |latitude - declination|, the declination at noon being +23.44 at the
        # June solstice, about 0 at the March equinox and -23.44 at the December one.
        ("Europe/Paris", datetime(2026, 6, 21, 12, tzinfo=UTC), 90 - (48.87 - 23.44)),
        ("Europe/Paris", datetime(2026, 3, 20, 12, tzinfo=UTC), 90 - 48.87),
        ("Australia/Sydney", datetime(2026, 12, 21, 2, tzinfo=UTC), 90 - (-23.44 + 33.87)),
    ],
)
def test_the_suns_highest_point_matches_the_solstices_and_the_equinox(tz, day, expected):
    latitude, longitude = daylight.place(tz)
    # The highest point found by walking the day a minute at a time.
    walked = max(
        daylight.elevation(latitude, longitude, day - timedelta(hours=12) + timedelta(minutes=m))
        for m in range(24 * 60)
    )
    assert walked == pytest.approx(expected, abs=0.5)
    assert daylight.highest(latitude, day) == pytest.approx(walked, abs=0.2)


def test_sunrise_and_sunset_in_paris_fall_where_a_published_table_puts_them():
    # 19 October 2026 in Paris, local time (CEST): sunrise 08:16 and sunset 18:54 in
    # generalblue.com's table for Paris (read 19 October 2026), so the highest sun at
    # 13:35, half way. Within three minutes; the table counts the disc's upper rim
    # with refraction, 0.833 degrees below the horizon.
    latitude, longitude = daylight.place("Europe/Paris")
    day = datetime(2026, 10, 19, tzinfo=PARIS)
    minutes = [day + timedelta(minutes=m) for m in range(24 * 60)]
    up = [m for m in minutes if daylight.elevation(latitude, longitude, m) > -0.833]
    sunrise, sunset = up[0], up[-1]
    assert abs((sunrise - day.replace(hour=8, minute=16)).total_seconds()) <= 180
    assert abs((sunset - day.replace(hour=18, minute=54)).total_seconds()) <= 180
    noon = max(minutes, key=lambda m: daylight.elevation(latitude, longitude, m))
    assert abs((noon - day.replace(hour=13, minute=35)).total_seconds()) <= 180


def test_the_days_position_runs_from_night_through_sunrise_noon_and_sunset():
    latitude, longitude = daylight.place("Europe/Paris")
    day = datetime(2026, 10, 19, tzinfo=PARIS)

    def at(hour: int, minute: int = 0) -> float:
        return daylight.day_position(latitude, longitude, day.replace(hour=hour, minute=minute))

    assert at(2) == 0.0  # deep night
    assert 0 < at(7, 30) < 1  # dawn
    assert 1 < at(9) < 1.5  # morning
    assert at(13, 35) == pytest.approx(2.0, abs=0.01)  # the highest sun
    assert 2 < at(16, 30) < 2.5  # afternoon: the sun still about 20 degrees up
    assert 2.5 < at(18) < 3  # late afternoon, golden
    assert 3 < at(19, 30) < 4  # dusk
    assert at(23) == 0.0
    # From deep night until dusk ends (about 20:40), it only moves forwards; then it
    # folds from 4 back to 0, the same sky.
    positions = [at(h, m) for h in range(4, 20) for m in range(0, 60, 5)]
    assert positions == sorted(positions)


def test_a_winter_noon_is_still_noon():
    # Measured against that day's highest sun: a low December sun in Oslo is noon too.
    latitude, longitude = daylight.place("Europe/Oslo")
    day = datetime(2026, 12, 21, tzinfo=ZoneInfo("Europe/Oslo"))
    minutes = [day + timedelta(minutes=m) for m in range(24 * 60)]
    noon = max(minutes, key=lambda m: daylight.elevation(latitude, longitude, m))
    assert daylight.elevation(latitude, longitude, noon) < 10
    assert daylight.day_position(latitude, longitude, noon) == pytest.approx(2.0, abs=0.02)


@pytest.mark.parametrize(("position", "step"), [(0.0, 0), (1.04, 104), (2.0, 200), (3.995, 0), (4.0, 0)])
def test_a_position_falls_in_one_step_of_the_sky(position, step):
    assert sky.step_of(position) == step


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_the_sky_turns_slowly_and_comes_back_to_where_it_started(mode):
    steps = [sky.colours(step, mode) for step in range(sky.STEPS)]
    jumps = [
        max(abs(a - b) for x, y in zip(before, after, strict=True) for a, b in zip(x, y, strict=True))
        for before, after in zip(steps, [*steps[1:], steps[0]], strict=True)
    ]
    assert max(jumps) <= 12  # no step changes a channel by more than 12 of 255


def test_light_skies_are_light_and_dark_skies_are_dark():
    for step in range(0, sky.STEPS, 10):
        assert min(sky.colours(step, "light")[0]) > 200
        assert max(sky.colours(step, "dark")[0]) < 50


def test_the_day_is_blue_sunrise_and_sunset_are_warm_and_the_night_is_deep():
    def warmth(step: int) -> int:
        _, first, second, third, fourth = sky.colours(step, "dark")
        lights = (first, second, third, fourth)
        return sum(r for r, _, _ in lights) - sum(b for _, _, b in lights)

    noon, sunrise, sunset, night = sky.step_of(2.0), sky.step_of(1.05), sky.step_of(3.03), sky.step_of(0.0)
    assert warmth(noon) < 0  # more blue than red
    assert warmth(sunrise) > warmth(noon) and warmth(sunset) > warmth(noon)
    assert sum(sky.colours(night, "dark")[0]) < sum(sky.colours(noon, "dark")[0])


@pytest.mark.parametrize(("step", "mode"), [(-1, "light"), (sky.STEPS, "dark"), (10, "sepia")])
def test_a_sky_that_does_not_exist_is_refused(step, mode):
    with pytest.raises(ValueError):
        sky.colours(step, mode)


def test_the_skys_style_sheet_follows_the_device_and_the_plans_own_setting():
    sheet = sky.stylesheet(236)
    light = re.search(r"^body\[data-sky\] \{ (.*) \}$", sheet, re.M)
    assert light and light[1].count("--sky-") == 5
    assert '@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) body[data-sky] {' in sheet
    assert ':root[data-theme="dark"] body[data-sky] {' in sheet
    base = sky.colours(236, "dark")[0]
    assert f"--sky-base: rgb({base[0]} {base[1]} {base[2]});" in sheet


@pytest.fixture
def web(tmp_path):
    now = datetime(2026, 9, 29, 6, tzinfo=UTC)  # 08:00 in Paris
    config = Config(store=tmp_path, clock=lambda: now, background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def test_the_sky_is_served_as_a_style_sheet_kept_a_day(web):
    answer = web.get("/sky/236.css")
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("text/css")
    assert answer.headers["cache-control"] == "public, max-age=86400"
    assert answer.text == sky.stylesheet(236)
    for missing in ("/sky/400.css", "/sky/-1.css", "/sky/noon.css"):
        assert web.get(missing).status_code == 404, missing


def test_an_open_page_asks_where_the_sun_is_now(web):
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    token = start.headers["location"].split("/")[2]
    page = web.get(f"/p/{token}").text
    look = web.get(f"/look/{token}")
    assert look.status_code == 200 and look.headers["cache-control"] == "no-store"
    assert look.json() == {"sky": "morning", "weekday": 2, "sun": 104}
    assert '<link rel="stylesheet" href="/sky/104.css" data-sky-sheet>' in page
    assert web.get("/look/nothing-here").status_code == 404


def test_the_afternoon_of_19_october_in_paris_as_decisions_d76_tells_it():
    # The times DECISIONS.md D76 quotes, to the minute.
    latitude, longitude = daylight.place("Europe/Paris")
    day = datetime(2026, 10, 19, tzinfo=PARIS)
    half_past_four = day.replace(hour=16, minute=30)
    assert daylight.elevation(latitude, longitude, half_past_four) == pytest.approx(20.0, abs=0.1)
    assert sky.step_of(daylight.day_position(latitude, longitude, half_past_four)) == 236
    minutes = [day + timedelta(minutes=m) for m in range(12 * 60, 24 * 60)]

    def reached(position: float) -> str:
        found = next(m for m in minutes if daylight.day_position(latitude, longitude, m) >= position)
        return found.strftime("%H:%M")

    moments = {"late afternoon": 2.45, "golden": 2.70, "sunset": 3.03, "dusk": 3.35, "late dusk": 3.70}
    assert {name: reached(at) for name, at in moments.items()} == {
        "late afternoon": "16:52",
        "golden": "17:49",
        "sunset": "18:54",
        "dusk": "19:30",
        "late dusk": "20:08",
    }
    night = next(m for m in minutes if daylight.elevation(latitude, longitude, m) < -daylight.TWILIGHT)
    assert night.strftime("%H:%M") == "20:41"


def test_a_student_far_from_the_zones_main_city_sees_its_sun():
    # Brest is under Europe/Paris, 6.8 degrees west of Paris: its sunset comes about
    # half an hour later than the sky shows (D76, disclosed).
    def sunset(latitude: float, longitude: float) -> datetime:
        day = datetime(2026, 10, 19, 12, tzinfo=PARIS)
        return next(
            day + timedelta(minutes=m)
            for m in range(12 * 60)
            if daylight.elevation(latitude, longitude, day + timedelta(minutes=m)) < -0.833
        )

    late = sunset(48.39, -4.49) - sunset(*daylight.place("Europe/Paris"))
    assert timedelta(minutes=25) <= late <= timedelta(minutes=32)


def _hue_of(colour: tuple[int, int, int]) -> float:
    import colorsys

    return colorsys.rgb_to_hls(*(c / 255 for c in colour))[0] * 360


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_the_accent_follows_the_sky_and_stays_readable_at_every_step(mode):
    # Buttons and links take a hue of the moment (D76), never red or orange (red is
    # for exams and today); their lightness keeps white text and accent text legible.
    for step in range(sky.STEPS):
        button, hover, ink = sky.accent(step, mode)
        base = sky.colours(step, mode)[0]
        assert 200 <= _hue_of(button) <= 290, (step, button)
        if mode == "light":
            assert sky.contrast(button, sky.WHITE) >= 4.5, step  # white text on a button
            assert sky.contrast(ink, base) >= 4.5, step  # accent text on the sky
            assert sky.contrast(hover, sky.WHITE) > sky.contrast(button, sky.WHITE)  # pressed: darker
        else:
            assert sky.contrast(button, sky.WHITE) >= 3.0, step  # as Apple's dark blue, 3.65
            assert sky.contrast(ink, base) >= 7.0, step


def test_the_accent_is_blue_by_day_and_purple_at_sunset():
    noon, sunset = sky.step_of(2.0), sky.step_of(3.03)
    assert 205 <= _hue_of(sky.accent(noon, "light")[0]) <= 220
    assert 265 <= _hue_of(sky.accent(sunset, "light")[0]) <= 280


def test_the_skys_sheet_carries_the_accent_and_text_uses_its_ink():
    sheet = sky.stylesheet(300)
    for name in ("--accent:", "--accent-hover:", "--accent-soft:", "--accent-ink:"):
        assert sheet.count(name) == 3, name  # light, the device's dark, the plan's dark
    css = (
        Path(__file__).resolve().parent.parent / "src" / "cps" / "web" / "static" / "style.css"
    ).read_text()
    # Text in the accent's hue reads the ink, so a sky can lighten it in dark mode.
    assert not re.search(r"(?<![-\w])color: var\(--accent\)", css)
    # Full screen shows the focus timer alone: it carries the sky with it (AUDIT 71).
    assert re.search(r"body\[data-sky\] \.focus:fullscreen \{[^}]*var\(--sky-base\)", css)


def test_apples_blues_as_decisions_d78_quotes_them():
    # The fixed accent before D78: #007aff misses WCAG's 4.5 for white text; the dark
    # #0a84ff is the bar set for dark mode's buttons.
    assert round(sky.contrast((0, 122, 255), sky.WHITE), 1) == 4.0
    assert round(sky.contrast((10, 132, 255), sky.WHITE), 2) == 3.65
