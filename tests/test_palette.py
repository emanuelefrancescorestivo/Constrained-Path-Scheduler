"""
D156: themes for light and dark, and an accent (the owner, 2026-11-19: "allow the user to pick
from a colour palette, maintaining as primary objective to make frictionless using the app,
comfortable to navigate the sections and look at the screen"). What must hold: every theme's
text, secondary text and accent keep 4.5:1 against its page and its cards, and so does every
accent on every theme of its mode, a tinted button included; a choice is one tap and is kept;
the page carries it; a flat theme needs no sky.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "src" / "cps" / "web" / "static" / "style.css").read_text(encoding="utf-8")
BLOCK = CSS[
    CSS.index("/* -- D156: themes and accents") : CSS.index("/* -- end of D156's themes and accents */")
]
NOW = datetime(2026, 9, 29, 7, 0, tzinfo=UTC)
# The themes there were before D156, which the accents also sit on: the sky's light page and
# cards, and the app's dark page and cards (style.css's tokens).
SKY = {"bg": "#eef5ff", "surface": "#ffffff"}
NIGHT = {"bg": "#0e1015", "surface": "#1a1c23"}


def _lum(colour: str) -> float:
    h = colour.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mix(top: str, under: str, share: float) -> str:
    t, u = top.lstrip("#"), under.lstrip("#")
    return "#" + "".join(
        f"{round(int(t[i : i + 2], 16) * share + int(u[i : i + 2], 16) * (1 - share)):02x}" for i in (0, 2, 4)
    )


def _tokens(selector: str) -> dict[str, str]:
    """The tokens one rule of the block sets, by its first selector."""
    found = re.search(re.escape(selector) + r"[^{]*\{([^}]*)\}", BLOCK)
    assert found, selector
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", found.group(1)))


def _theme(kind: str, name: str) -> dict[str, str]:
    return _tokens(f':root[data-{kind}="{name}"][data-theme="{"light" if kind == "light" else "dark"}"]')


def _accent(name: str, mode: str) -> dict[str, str]:
    return _tokens(f':root[data-accent="{name}"][data-theme="{mode}"]')


def _pairs() -> list[tuple[float, str]]:
    """Every text against what it sits on, in every theme and with every accent of its mode."""
    out = []
    themes = [("light", k) for k, _n, _l in service.LIGHT_THEMES if k != "sky"]
    themes += [("dark", k) for k, _n, _l in service.DARK_THEMES if k != "night"]
    assert len(themes) == 6
    for kind, name in themes:
        t = _theme(kind, name)
        for ground in (t["bg"], t["surface"]):
            out += [
                (_ratio(t[ink], ground), f"{name} {ink} on {ground}")
                for ink in ("text", "muted", "accent-ink")
            ]
        out.append((_ratio(t["accent"], t["on-accent"]), f"{name} button"))
        soft = _mix(t["accent"], t["surface"], 0.12 if kind == "light" else 0.22)
        out.append((_ratio(t["accent-ink"], soft), f"{name} tinted button"))
    lights = [SKY] + [_theme("light", k) for k, _n, _l in service.LIGHT_THEMES if k != "sky"]
    darks = [NIGHT] + [_theme("dark", k) for k, _n, _l in service.DARK_THEMES if k != "night"]
    for name in [k for k, _n in service.ACCENTS if k != "auto"]:
        for mode, grounds in (("light", lights), ("dark", darks)):
            a = _accent(name, mode)
            out.append((_ratio(a["accent"], a["on-accent"]), f"{name} {mode} button"))
            for g in grounds:
                out += [
                    (_ratio(a["accent-ink"], ground), f"{name} {mode} on {ground}")
                    for ground in (g["bg"], g["surface"])
                ]
                soft = _mix(a["accent"], g["surface"], 0.12 if mode == "light" else 0.22)
                out.append((_ratio(a["accent-ink"], soft), f"{name} {mode} tinted on {g['surface']}"))
    return out


def test_every_text_is_readable_in_every_theme_with_every_accent():
    pairs = _pairs()
    # Six themes, eight pairs each; six accents, on three light grounds and five dark ones.
    assert len(pairs) == 6 * 8 + 6 * ((1 + 3 * 3) + (1 + 5 * 3))
    assert all(ratio >= 4.5 for ratio, _label in pairs), [p for p in pairs if p[0] < 4.5]
    # D156 quotes the lowest: Soft day's secondary text on its page.
    lowest = min(pairs)
    assert round(lowest[0], 2) == 4.66 and lowest[1] == "softday muted on #ebe8e3"
    accents = [k for k, _n in service.ACCENTS if k != "auto"]
    assert accents == ["blue", "teal", "green", "amber", "rose", "violet"]  # red stays for exams and today


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
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


def test_a_choice_is_one_tap_kept_and_carried_by_every_page(web, tmp_path):
    sub = _plan(tmp_path)
    assert service.palette_of(sub) == {"light": "sky", "dark": "night", "accent": "auto"}
    page = unescape(web.get(f"/p/{sub.token}/settings/look").text)
    assert page.count('class="swatch-btn"') == 8 and page.count('class="accent-btn"') == 7
    assert re.search(r'value="sky" class="swatch-btn" aria-pressed="true"', page)
    today = web.get(f"/p/{sub.token}").text
    assert "data-light=" not in today and "data-accent=" not in today and "data-sky-sheet" in today
    for part, value in (("light", "paper"), ("dark", "graphite"), ("accent", "teal")):
        answer = web.post(
            f"/p/{sub.token}/palette", data={"part": part, "value": value}, follow_redirects=False
        )
        assert answer.status_code == 303 and answer.headers["location"].endswith(
            "/settings/look?saved=theme#themes"
        )
    stored = service.load_subscription(tmp_path, sub.token)
    assert service.palette_of(stored) == {"light": "paper", "dark": "graphite", "accent": "teal"}
    today = web.get(f"/p/{sub.token}").text
    assert 'data-light="paper" data-dark="graphite" data-accent="teal"' in today
    assert "data-sky-sheet" not in today  # both themes flat: no sky to fetch
    web.post(f"/p/{sub.token}/palette", data={"part": "dark", "value": "night"})
    assert "data-sky-sheet" in web.get(f"/p/{sub.token}").text  # the night's sky is back in dark
    look = web.get(f"/p/{sub.token}/settings/look").text
    assert re.search(r'value="teal" class="accent-btn" aria-pressed="true"', look)
    # Anything not shown is refused, and nothing changes.
    for part, value in (("light", "neon"), ("accent", "red"), ("font", "paper")):
        assert web.post(f"/p/{sub.token}/palette", data={"part": part, "value": value}).status_code == 400
    assert service.palette_of(service.load_subscription(tmp_path, sub.token))["accent"] == "teal"
