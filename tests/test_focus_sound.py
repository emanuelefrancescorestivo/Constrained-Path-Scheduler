"""
Sounds to focus, and the focus page that fits full screen (DECISIONS.md D207, D210).

What must hold: the eight sounds the owner listed and lo-fi are offered; since D210 each but
binaural a recording in the public domain from the app's own files, binaural made by the page's
own script, and nothing from an address outside the app; the one tapped and its volume are
saved on the plan as they change and offered again in one tap; a name this version does not
make, or a volume outside 0 to 100, is refused. The focus page starts at the top when it is
taller than the screen (AUDIT 137), and the clock's size follows the dial's.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import focussounds, service
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
STATIC = Path(__file__).resolve().parent.parent / "src" / "cps" / "web" / "static"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)


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
    return sub


def test_the_eight_sounds_are_offered_and_the_choice_is_kept(tmp_path):
    sub = _plan(tmp_path)
    first = service.focus_sound(sub)
    assert first["key"] == "" and first["volume"] == service.FOCUS_VOLUME
    keys = [s["key"] for s in first["sounds"]]
    assert keys == ["nature", "rain", "binaural", "sea", "cafe", "lofi", "jazz", "classical", "fireplace"]
    chosen = service.set_focus_sound(sub, "rain", "35")
    assert service.focus_sound(chosen) | {"sounds": None} == {
        "key": "rain",
        "name": "Rain",
        "volume": 35,
        "pad": focussounds.PAD,  # D210: the file's margin around each recording's loop
        "sounds": None,
    }
    # The volume alone keeps the sound; out of range is held to 0..100.
    assert service.focus_sound(service.set_focus_sound(chosen, "rain", 140))["volume"] == 100
    with pytest.raises(service.InvalidInput):
        service.set_focus_sound(sub, "techno")
    with pytest.raises(service.InvalidInput):
        service.set_focus_sound(sub, "rain", "loud")
    # A sound saved under a name this version does not make is none.
    odd = service.replace(sub, options={**sub.options, "focus_sound": {"key": "whale", "volume": "x"}})
    assert (
        service.focus_sound(odd)["key"] == "" and service.focus_sound(odd)["volume"] == service.FOCUS_VOLUME
    )


def test_each_sound_is_a_recording_in_the_public_domain_or_made_by_the_script():
    """D210: every sound but binaural is a recording under CC0, its file among the app's own
    and in the notice; the browser's own version of each stays for a device offline, lo-fi
    playing the jazz's; nothing is fetched from anywhere but the app."""
    script = (STATIC / "sound.js").read_text(encoding="utf-8")
    made = set(re.findall(r"^    (\w+)\(out\) \{", script, re.M))
    keys = {key for key, _name, _icon in service.FOCUS_SOUNDS}
    recorded = set(focussounds.RECORDED)
    assert recorded == keys - {"binaural"}
    assert made | {"lofi"} == keys and 'STAND_IN = { lofi: "jazz" }' in script
    assert "http" not in script.replace("https://", "") and "new Audio(" not in script
    assert "createDynamicsCompressor" in script  # no sound at full volume clips
    notice = (STATIC / "sounds" / "NOTICE.md").read_text(encoding="utf-8")
    for source in focussounds.SOURCES:
        file = STATIC / "sounds" / f"{source.key}.mp3"
        assert file.exists() and file.stat().st_size < 1_600_000, source.key  # fetched only when played
        assert file.read_bytes()[:2] in (b"\xff\xfb", b"\xff\xf3") or file.read_bytes()[:3] == b"ID3"
        assert source.licence == focussounds.CC0 and f"## {source.key}.mp3" in notice
        assert source.page in notice and source.author in notice
        assert source.loop >= 45  # a minute or so: its seam is not heard every few seconds


def test_the_page_offers_the_sounds_and_saves_the_choice(tmp_path):
    sub = _plan(tmp_path)
    service.save_subscription(tmp_path, service.start_focus(sub, "Algebra 3", goal=60, now=NOW))
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as web:
        page = unescape(web.get(f"/p/{sub.token}/focus").text)
        assert 'id="focus-sound"' in page and "/static/" in page and "sound.js" in page
        box = page[
            page.index('id="focus-sound"') - 30 : page.index("</details>", page.index('id="focus-sound"'))
        ]
        assert " hidden>" in box  # shown by its script only: without it there is no sound
        assert box.count("data-sound-key=") == 9 and 'id="sound-toggle" aria-pressed="false" hidden' in box
        # D210: each recording from the app's own files, with its loop; binaural made by the script.
        assert box.count('data-src="/static/v/') == 8 and "/sounds/cafe.mp3" in box
        assert re.search(r'data-sound-key="binaural"[^>]*aria-pressed', box) and 'data-pad="0.25"' in page
        assert "Where the recordings come from" in box and "freesound.org" in box
        assert web.post(f"/p/{sub.token}/focus/sound", data={"key": "sea", "volume": "60"}).status_code == 204
        page = unescape(web.get(f"/p/{sub.token}/focus").text)
        assert (
            'data-sound="sea"' in page and '<span id="sound-name">Sea</span>' in page and 'value="60"' in page
        )
        assert web.post(f"/p/{sub.token}/focus/sound", data={"key": "techno"}).status_code == 400


def test_the_focus_page_fits_full_screen_and_the_clock_follows_the_dial():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    focus = css[css.index("\n.focus {") : css.index("}", css.index("\n.focus {"))]
    assert "justify-content: safe center" in focus  # AUDIT 137: never spilling above the top
    full = css[css.index(".focus:fullscreen {") : css.index("}", css.index(".focus:fullscreen {"))]
    assert "overflow-y: auto" in full
    assert (
        "container-type: inline-size"
        in css[css.index(".focus-dial {") : css.index("}", css.index(".focus-dial {"))]
    )
    assert re.search(r"\.focus-dial \.focus-timer \{[^}]*font-size: 19cqi", css)
    assert ".focus-timer.is-hours { font-size: 13cqi; }" in css
