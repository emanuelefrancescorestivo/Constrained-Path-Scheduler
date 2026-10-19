"""D74, the app's icon: the week as a ring of seven days, live on a plan's pages
(today's weekday in the sky's colours), white or graphite where it is installed."""

from __future__ import annotations

import re
import struct
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app, icon

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
ICONS = Path(service.__file__).parent / "web" / "static" / "icons"
SVG = "{http://www.w3.org/2000/svg}"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Tuesday


def _days(drawing: str) -> list[ElementTree.Element]:
    """The seven days' arcs, Monday first."""
    root = ElementTree.fromstring(drawing)
    ring = next(g for g in root.iter(f"{SVG}g") if g.get("stroke-linecap") == "round")
    return list(ring.iter(f"{SVG}path"))


@pytest.mark.parametrize("look", sorted(icon.LOOKS))
@pytest.mark.parametrize("day", list(icon.DAYS))
def test_the_days_before_today_are_filled_and_the_days_to_come_are_faint(day, look):
    days = _days(icon.week(day, look))
    assert len(days) == 7
    faint = [d.get("stroke-opacity") is not None for d in days]
    assert faint == [False] * day + [True] * (7 - day)


@pytest.mark.parametrize("day", list(icon.DAYS))
def test_todays_gold_light_sits_at_the_end_of_today(day):
    drawing = icon.week(day, "night")
    end = _days(drawing)[day - 1].get("d").split(" ")[-2:]  # the arc's last point
    gold = re.search(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="27"', drawing)
    assert gold is not None
    assert [float(gold[1]), float(gold[2])] == pytest.approx([float(v) for v in end], abs=0.1)


def test_a_day_keeps_its_colour_whatever_the_day_is():
    # Colour follows the day, never how many days are filled: Monday is Monday's colour.
    mondays = {_days(icon.week(day, "evening"))[0].get("stroke") for day in icon.DAYS}
    assert len(mondays) == 1


def test_every_sky_of_the_hour_has_a_look_and_so_do_light_and_dark():
    assert set(service.SKIES) | {"light", "dark"} == set(icon.LOOKS)


@pytest.mark.parametrize(("day", "look"), [(0, "night"), (8, "night"), (3, "noon"), (3, "")])
def test_an_icon_that_does_not_exist_is_refused(day, look):
    with pytest.raises(ValueError):
        icon.week(day, look)


def test_the_installed_icons_drawings_are_the_modules(tmp_path):
    # The files in static/icons/ are written by icon.main(); a change to the
    # drawing that is not written out fails here.
    icon.main(tmp_path)
    for name in icon.STATIC:
        written = (tmp_path / name).read_text(encoding="utf-8")
        assert (ICONS / name).read_text(encoding="utf-8") == written, name


def test_the_tabs_icon_is_white_in_light_mode_and_graphite_in_dark_mode():
    drawing = (ICONS / "icon.svg").read_text(encoding="utf-8")
    root = ElementTree.fromstring(drawing)
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    assert len(ids) == len(set(ids))  # the two drawings do not share a gradient
    looks = {g.get("class") for g in root.findall(f"{SVG}g")}
    assert looks == {"light", "dark"}
    assert "@media (prefers-color-scheme: dark)" in drawing
    assert icon.LOOKS["light"].top in drawing and icon.LOOKS["dark"].top in drawing


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    width, height = struct.unpack(">II", head[16:24])
    return width, height


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("favicon-32.png", 32),
        ("apple-touch-icon.png", 180),
        ("apple-touch-icon-dark.png", 180),
        ("icon-192.png", 192),
        ("icon-192-dark.png", 192),
        ("icon-512.png", 512),
        ("icon-512-dark.png", 512),
        ("maskable-512.png", 512),
        ("maskable-512-dark.png", 512),
    ],
)
def test_the_installed_icons_exist_at_their_sizes(name, size):
    assert _png_size(ICONS / name) == (size, size)


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def _start(web) -> str:
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return start.headers["location"].split("/")[2]


def test_the_live_icon_is_served_and_may_be_kept_a_day(web):
    answer = web.get("/icon/week-3-evening.svg")
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "image/svg+xml"
    assert answer.headers["cache-control"] == "public, max-age=86400"
    assert answer.text == icon.week(3, "evening")
    for missing in ("/icon/week-8-evening.svg", "/icon/week-3-noon.svg", "/icon/week-x-night.svg"):
        assert web.get(missing).status_code == 404


def test_a_plans_pages_show_todays_icon_in_the_tab_and_the_top_bar(web):
    token = _start(web)
    page = web.get(f"/p/{token}").text
    # 08:00 in Paris on a Tuesday: day 2, the morning's colours.
    assert 'data-day="2"' in page
    assert '<link rel="icon" href="/icon/week-2-morning.svg" type="image/svg+xml" data-live-icon>' in page
    assert re.search(r'<img class="brand-mark" src="/icon/week-2-morning.svg"[^>]*data-live-icon>', page)
    home = web.get("/").text
    assert "/static/icons/icon.svg" in home and "data-live-icon" not in home


def test_a_plan_in_dark_mode_installs_the_graphite_icon(web):
    token = _start(web)
    page = web.get(f"/p/{token}").text
    assert f'href="/p/{token}/manifest.webmanifest"' in page  # Automatic: pwa.js decides on the device
    assert 'href="/static/icons/apple-touch-icon.png"' in page
    web.post(f"/p/{token}/appearance", data={"theme": "dark"})
    page = web.get(f"/p/{token}").text
    assert f'href="/p/{token}/manifest.webmanifest?look=dark"' in page
    assert 'href="/static/icons/apple-touch-icon-dark.png"' in page


@pytest.mark.parametrize("look", ["light", "dark"])
def test_the_manifest_lists_the_icons_of_its_look_and_they_exist(web, look):
    token = _start(web)
    for path in ("/manifest.webmanifest", f"/p/{token}/manifest.webmanifest"):
        body = web.get(path, params={"look": look}).json()
        sources = [i["src"] for i in body["icons"]]
        pictures = [s for s in sources if s.endswith(".png")]
        assert len(pictures) == 3, sources
        assert all(s.endswith("-dark.png") == (look == "dark") for s in pictures), sources
        for source in sources:
            assert web.get(source).status_code == 200, source
        assert body["background_color"] == ("#0e1015" if look == "dark" else "#f2f2f7")
