"""The student's own avatar (D115): made from parts on its page, kept as their names,
and the one who asks the questions on the science and code pages (D114)."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service, usage
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/cps/web/static"
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


def plan(options: dict) -> service.Subscription:
    return service.Subscription(
        token="t",
        created="2026-09-29",
        source_url=None,
        ics_text=None,
        busy_rows=(),
        subjects=(),
        options=options,
    )


MINE = {"skin": "s5", "hair": "curly", "colour": "auburn", "face": "beard", "glasses": "round", "top": "c3"}


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    config = Config(
        store=tmp_path_factory.mktemp("avatar"), clock=lambda: NOW, background=False, sweep_every=None
    )
    with TestClient(create_app(config)) as client:
        yield client


@pytest.fixture(scope="module")
def token(web) -> str:
    files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return start.headers["location"].split("/")[2]


def test_an_avatar_is_its_parts_names_and_nothing_else():
    empty = plan({})
    assert service.avatar_of(empty) is None and service.avatar_of(None) is None
    made = service.set_avatar(empty, MINE)
    assert service.avatar_of(made) == MINE and made.options["avatar"] == MINE
    # A part left out starts where the maker starts; a name it does not draw is refused.
    assert (
        service.avatar_of(service.set_avatar(empty, {"hair": "bun"}))["skin"] == service.AVATAR_START["skin"]
    )
    with pytest.raises(service.InvalidInput):
        service.set_avatar(empty, {**MINE, "hair": "mohawk"})
    with pytest.raises(service.InvalidInput):
        service.set_avatar(empty, {**MINE, "skin": '"><script>'})
    # Saved by an earlier version under a name no longer drawn: back to the start.
    old = plan({"avatar": {**MINE, "glasses": "monocle"}})
    assert service.avatar_of(old)["glasses"] == "none" and service.avatar_of(old)["hair"] == "curly"


def test_the_student_makes_it_and_then_it_asks_the_questions(web, token):
    you = web.get(f"/p/{token}/you").text
    assert "Make your avatar" in you and f'href="/p/{token}/avatar"' in you and "av-ghost" in you
    maker = web.get(f"/p/{token}/avatar").text
    for part, names in service.AVATAR_PARTS.items():
        assert len(re.findall(rf'type="radio" name="{part}"', maker)) == len(names)
        assert (
            f'id="av-{part}-{service.AVATAR_START[part]}" value="{service.AVATAR_START[part]}" checked'
            in maker
        )
    # The preview draws every part, for the radios to choose from without a script.
    for style in service.AVATAR_PARTS["hair"]:
        assert f"hs-{style}" in maker
    assert web.get(f"/p/{token}/how").text.count("av-ghost") >= 11  # not made yet
    saved = web.post(f"/p/{token}/avatar", data=MINE, follow_redirects=False)
    assert saved.status_code == 303 and saved.headers["location"] == f"/p/{token}/avatar?saved=1"
    assert "now asks the questions" in web.get(f"/p/{token}/avatar?saved=1").text
    assert "Your avatar" in web.get(f"/p/{token}/you").text
    for path in ("how", "science"):
        page = web.get(f"/p/{token}/{path}").text
        chats = page.count('class="chat scroll-fig"')
        drawn = page.count('class="av-face av skin-s5 col-auburn top-c3"')
        assert chats and drawn == chats and "av-ghost" not in page
        assert (
            "hs-curly" in page
            and "fh-beard" in page
            and "gl-round" in page
            and 'class="avo hs-long av-hair"' not in page
        )
    refused = web.post(f"/p/{token}/avatar", data={**MINE, "top": "gold"}, follow_redirects=False)
    assert refused.status_code == 400
    assert 'class="av-face av skin-s5 col-auburn top-c3"' in web.get(f"/p/{token}/how").text  # kept as it was
    assert web.get("/p/not-a-plan/avatar").status_code == 404


def test_the_preview_follows_the_radios_with_the_avatars_own_colours():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    word = {"skin": "skin", "col": "colour", "top": "top"}
    own = dict(
        ((k, v), d) for k, v, d in re.findall(r"^\.av\.(skin|col|top)-([a-z0-9]+) (\{[^}]*\})$", css, re.M)
    )
    shown = dict(
        ((k, v), d)
        for k, v, d in re.findall(
            r"^\.av-maker:has\(#av-(skin|colour|top)-([a-z0-9]+):checked\) \.av-preview (\{[^}]*\})$",
            css,
            re.M,
        )
    )
    for short, part in word.items():
        for name in service.AVATAR_PARTS[part]:
            assert own[(short, name)] == shown[(part, name)]  # the same colours both ways
    # Every style, beard, moustache and pair of glasses shows when its radio is checked.
    for part, prefix in (("hair", "hs"), ("face", "fh"), ("glasses", "gl")):
        for name in service.AVATAR_PARTS[part]:
            if name != "none":
                assert f".av-maker:has(#av-{part}-{name}:checked) .{prefix}-{name}" in css
    # A browser without :has keeps the avatar's colours and shows it as saved.
    assert not re.search(r"^\.av\.[^{]*,[^{]*:has", css, re.M)
    assert "@supports not selector(:has(*))" in css


def test_making_it_counts_as_you():
    assert usage.part_of("GET", "/avatar") == "you" and usage.part_of("POST", "/avatar") == "you"
