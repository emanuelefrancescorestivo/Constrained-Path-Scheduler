"""
D139: the look the owner chose (2026-11-16): one type face, Plus Jakarta Sans, shipped
with its licence; every icon Lucide's, shipped with its licence, drawn inline by the
server on one grid and one stroke; each place's picture a line icon on a soft tile of
its colour, not a drawing on a bright gradient. What must hold: the app names only its
own font files; the icons it draws are the files it ships, unchanged, and nothing else;
the sidebar, the tab bar, the pictures and the badges are drawn by the one helper; a name
that is not an icon is refused.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import crests, service
from cps.web import Config, create_app, icons

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "src" / "cps" / "web"
NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
LUCIDE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def _plan(where: Path) -> str:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(ROOT / "examples" / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(where, service.finish_first_run(sub))
    return sub.token


def test_one_type_face_of_the_apps_own_with_its_licence():
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    faces = set(re.findall(r'@font-face \{\s*font-family: "([^"]+)"', css))
    assert faces == {"Plus Jakarta Sans"}
    assert re.search(r'--font-text: "Plus Jakarta Sans"', css) and re.search(
        r'--font-display: "Plus Jakarta Sans"', css
    )
    shipped = sorted(p.name for p in (WEB / "static" / "fonts").iterdir())
    assert shipped == [
        "OFL-PlusJakartaSans.txt",
        "plus-jakarta-sans-latin-ext.woff2",
        "plus-jakarta-sans-latin.woff2",
    ]
    base = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
    assert re.findall(r"asset\('fonts/([^']+)'\)", base) == ["plus-jakarta-sans-latin.woff2"]


def test_the_icons_shipped_are_lucides_unchanged_with_the_licence_and_only_those_drawn():
    folder = icons.ICONS
    licence = (folder / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("ISC License") and "Feather (MIT)" in licence
    files = {p.stem for p in folder.glob("*.svg")}
    assert files == icons.shipped()  # every icon drawn is shipped, and nothing else is
    assert {p.name for p in folder.iterdir()} == {f"{n}.svg" for n in files} | {"LICENSE"}
    for name in files:
        assert "@license lucide-static v0.460.0 - ISC" in (folder / f"{name}.svg").read_text(encoding="utf-8")
        assert str(icons.icon(name)).startswith(LUCIDE)
    # The diary's kinds of work, the crests (D189) and every picture a template names have one.
    assert {kind for kind, _ in service.WORK_WORDS} <= set(icons.ART)
    assert {f.icon for f in crests.FAMILIES} | {r.icon for r in crests.RARES} <= icons.shipped()
    named = set()
    for page in (WEB / "templates").glob("*.html"):
        text = page.read_text(encoding="utf-8")
        named |= set(re.findall(r'\bart\("([a-z]+)"', text))
        for icon in re.findall(r"lucide\('([a-z0-9-]+)'\)", text):
            assert icon in icons.shipped(), (page.name, icon)
    assert named and named <= set(icons.ART)


def test_a_name_that_is_not_an_icon_is_refused():
    for wrong in ("../app", "Settings", "settings.svg", ""):
        with pytest.raises(ValueError):
            icons.icon(wrong)
    with pytest.raises(FileNotFoundError):
        icons.icon("no-such-icon")


def test_tiles_are_soft_and_flat_in_each_places_colour():
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    tile = css[css.index("\n.art {") : css.index("}", css.index("\n.art {"))]
    assert "linear-gradient" not in tile and "box-shadow" not in tile
    assert "color-mix(in srgb, var(--hue) var(--t-mix), var(--surface))" in tile
    for hue in ("blue", "green", "orange", "purple", "pink", "amber", "teal", "red", "indigo", "grey"):
        assert css.count(f"--t-{hue}: #") == 3, hue  # light, and dark twice (device and plan)
    assert "linear-gradient" not in "".join(re.findall(r"\.art-[a-z]+ \{[^}]*\}", css))


def test_the_sidebar_the_tab_bar_and_the_pictures_are_lucides(web, tmp_path):
    token = _plan(tmp_path)
    you = unescape(web.get(f"/p/{token}/you").text)
    side = you[you.index('<aside class="sidebar"') : you.index("</aside>")]
    tabs = you[you.index('<nav class="tabbar"') :]
    tabs = tabs[: tabs.index("</nav>")]
    for part, count in ((side, 11), (tabs, 5)):
        drawn = re.findall(r"<svg[^>]*>", part)
        assert len(drawn) >= count and all(d.startswith(LUCIDE[:-1]) for d in drawn), drawn
    assert str(icons.icon("list-checks")) in side and str(icons.icon("settings")) in side
    pictures = re.findall(r'<span class="art art-([a-z]+)" aria-hidden="true">(<svg[^>]*>)', you)
    # Thirteen rows: D163 added Moodle's, D175 Connections.
    assert len(pictures) == 13 and all(svg.startswith(LUCIDE[:-1]) for _, svg in pictures)
    progress = unescape(web.get(f"/p/{token}/progress").text)
    assert "badge-art" not in progress or LUCIDE[:40] in progress[progress.index("badge-art") :]
