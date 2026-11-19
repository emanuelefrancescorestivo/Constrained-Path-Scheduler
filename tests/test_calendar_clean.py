"""
D157: the calendar, cleaner (the owner's pick, 2026-11-19: "A + C", after Notion Calendar).
What must hold: the header is one menu and the week's number, not four buttons; the sky of
each hour of today comes with the calendar, its accent under white text at 4.5:1 or more;
a title in its course's ink reads at 4.5:1 or more on its course's tint, in every theme, and
so does the muted title of what is over.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "src" / "cps" / "web" / "static" / "style.css").read_text(encoding="utf-8")
THEMES = CSS[
    CSS.index("/* -- D156: themes and accents") : CSS.index("/* -- end of D156's themes and accents */")
]
NOW = datetime(2026, 11, 19, 11, 0, tzinfo=UTC)  # noon in Paris


def _rgb(colour: str) -> tuple[int, int, int]:
    h = colour.strip().lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(c):02x}" for c in rgb)


def _lum(colour: str) -> float:
    channels = [c / 255 for c in _rgb(colour)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mix(top: str, under: str, share: float) -> str:
    """CSS's color-mix(in srgb, top share, under)."""
    t, u = _rgb(top), _rgb(under)
    return _hex(tuple(t[i] * share + u[i] * (1 - share) for i in range(3)))


def _block(selector: str, text: str = CSS) -> dict[str, str]:
    found = re.search(re.escape(selector) + r"[^{]*\{([^}]*)\}", text)
    assert found, selector
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", found.group(1)))


def _grounds() -> list[tuple[str, dict[str, str]]]:
    """Every theme's surface, text and muted text, by mode; the base themes' muted text is
    translucent, so it is painted over the background it sits on (below)."""
    out = [
        ("light", {"surface": "#ffffff", "text": "#000000", "muted": "rgb(60 60 67 / 75%)"}),
        ("dark", {"surface": "#1a1c23", "text": "#ffffff", "muted": "rgb(235 235 245 / 60%)"}),
    ]
    for key, _n, _l in service.LIGHT_THEMES[1:]:
        out.append(("light", _block(f':root[data-light="{key}"][data-theme="light"]', THEMES)))
    for key, _n, _l in service.DARK_THEMES[1:]:
        out.append(("dark", _block(f':root[data-dark="{key}"][data-theme="dark"]', THEMES)))
    return out


def _paint(colour: str, under: str) -> str:
    found = re.fullmatch(r"rgb\((\d+) (\d+) (\d+) / (\d+)%\)", colour.strip())
    if not found:
        return colour
    r, g, b, alpha = (int(x) for x in found.groups())
    return _mix(_hex((r, g, b)), under, alpha / 100)


def test_every_title_reads_on_its_tint_in_every_theme():
    base, dark = _block(":root"), _block(':root[data-theme="dark"]')
    courses = {
        "light": [base[f"c{k}"] for k in range(7)] + [base["danger"]],
        "dark": [dark[f"c{k}"] for k in range(7)] + [dark["now"]],
    }
    tints = {
        "light": (int(base["tint"].rstrip("%")) / 100, int(base["tint-lecture"].rstrip("%")) / 100),
        "dark": (int(dark["tint"].rstrip("%")) / 100, int(dark["tint-lecture"].rstrip("%")) / 100),
    }
    pairs = []
    for mode, ground in _grounds():
        surface = ground["surface"]
        for cc in courses[mode]:
            for tint in tints[mode]:
                # A title: 40 % of its course's colour, 60 % of the text (55 % of the course
                # fell to 3.8:1 on Soft day's cards).
                under = _mix(cc, surface, tint)
                title = _mix(cc, ground["text"], 0.4)
                pairs.append((_ratio(title, under), f"{mode} {cc} at {tint} on {surface}"))
            # What is over: the muted text on a 6 % tint.
            under = _mix(cc, surface, 0.06)
            pairs.append((_ratio(_paint(ground["muted"], under), under), f"{mode} past {cc} on {surface}"))
    # Six themes and the two before them, eight colours, three backgrounds each.
    assert len(pairs) == 8 * 8 * 3
    assert all(ratio >= 4.5 for ratio, _ in pairs), [p for p in pairs if p[0] < 4.5]
    lowest = min(pairs)
    # D157 quotes the lowest: the muted title of a past violet class on Soft day's cards.
    assert round(lowest[0], 2) == 4.77 and lowest[1] == "light past #4a3aa7 on #f7f5f1", lowest


def test_the_sun_of_each_hour_comes_with_the_calendar(web, tmp_path):
    hours = service.sun_hours("Europe/Paris", date(2026, 11, 19))
    assert len(hours) == 25 and all(0 <= h < 4 for h in hours)
    assert hours[0] == hours[24] == 0.0  # deep night at both midnights in November
    # Up around noon (solar noon in Paris is near 12:40 in November), the highest sun (2)
    # then; after sunset the position runs on towards 4.
    assert min(range(25), key=lambda k: abs(hours[k] - 2)) in (12, 13) and 3 < max(hours) < 4
    sub = _plan(tmp_path)
    answer = web.get(f"/p/{sub.token}/calendar.json?start=2026-11-19&days=7").json()
    sun = answer["sun"]
    assert len(sun) == 25 and [hour["up"] for hour in sun] == [1 <= h <= 3 for h in hours]
    for hour in sun:
        assert set(hour) == {"sky_light", "sky_dark", "light", "dark", "up"}
        # The line of now and its label: white text on the accent of the hour.
        assert _ratio(hour["light"], "#ffffff") >= 4.5 and _ratio(hour["dark"], "#ffffff") >= 4.5
    # The ribbon's sky turns warm at sunrise: more red than blue in its first light.
    sunrise = next(k for k, h in enumerate(hours) if h >= 1)
    morning = _rgb(sun[sunrise]["sky_light"])
    assert morning[0] > morning[2], sun[sunrise]


def test_the_header_is_one_menu_and_the_week(web, tmp_path):
    sub = _plan(tmp_path)
    page = web.get(f"/p/{sub.token}/week").text
    assert 'id="calendar-view"' in page and 'id="calendar-week"' in page
    assert re.findall(r'<option value="(\d+)">', page) == ["1", "3", "7", "30"]
    assert "data-view=" not in page and 'class="segmented"' not in page.split('id="calendar-bar"')[1][:2000]
    script = (ROOT / "src" / "cps" / "web" / "static" / "calendar.js").read_text(encoding="utf-8")
    # The courses hidden are kept on the device, by plan; the legend's chips are switches.
    assert "cps-hidden-${this.token}" in script and '"aria-pressed"' in script


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 11, 16),
        tz="Europe/Paris",
        ics=(ROOT / "examples" / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, service.finish_first_run(sub))
    return service.load_subscription(root, sub.token)


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client
