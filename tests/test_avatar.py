"""The student's own avatar (D115): made from parts on its page, kept as their names,
and the student who asks in How it works (D116)."""

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
# D137's parts, each away from where the maker starts.
MINE |= {"eyes": "happy", "brows": "thick", "mouth": "grin", "head": "cap", "detail": "freckles"}
MINE |= {"outfit": "hoodie", "bg": "b3"}


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
    # D143: and a flag, none until one is chosen.
    assert service.avatar_of(made) == {**MINE, "flag": ""} and made.options["avatar"] == {**MINE, "flag": ""}
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


def test_the_student_makes_it_and_then_it_is_them_in_how_it_works(web, token):
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
    assert web.get(f"/p/{token}/garden").text.count("av-ghost") >= 1  # not made yet
    saved = web.post(f"/p/{token}/avatar", data=MINE, follow_redirects=False)
    assert saved.status_code == 303 and saved.headers["location"] == f"/p/{token}/avatar?saved=1"
    assert "now the student in How it works" in web.get(f"/p/{token}/avatar?saved=1").text
    assert "Your avatar" in web.get(f"/p/{token}/you").text
    page = web.get(f"/p/{token}/garden").text
    chats = page.count('class="chat scroll-fig"')
    drawn = page.count('class="av-face av skin-s5 col-auburn top-c3 bg-b3"')
    assert chats == 1 and drawn == chats and "av-ghost" not in page
    assert "hs-curly" in page and "fh-beard" in page and "gl-round" in page
    for drawn_part in ("ey-happy", "mo-grin", "hd-cap", "dt-freckles", "ot-hoodie", "is-thick"):
        assert drawn_part in page, drawn_part
    assert "ey-wide" not in page and "ot-tee" not in page and "hd-beanie" not in page
    assert 'class="avo hs-long av-hair"' not in page
    # The technical pages are for the curious again (D116): no chat, no avatar.
    for path in ("how", "science"):
        assert "chat scroll-fig" not in web.get(f"/p/{token}/{path}").text
    refused = web.post(f"/p/{token}/avatar", data={**MINE, "top": "gold"}, follow_redirects=False)
    assert refused.status_code == 400
    assert 'class="av-face av skin-s5 col-auburn top-c3 bg-b3"' in web.get(f"/p/{token}/garden").text  # kept
    assert web.get("/p/not-a-plan/avatar").status_code == 404


def test_the_preview_follows_the_radios_with_the_avatars_own_colours():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    word = {"skin": "skin", "col": "colour", "top": "top", "bg": "bg"}
    own = dict(
        ((k, v), d) for k, v, d in re.findall(r"^\.av\.(skin|col|top|bg)-([a-z0-9]+) (\{[^}]*\})$", css, re.M)
    )
    shown = dict(
        ((k, v), d)
        for k, v, d in re.findall(
            r"^\.av-maker:has\(#av-(skin|colour|top|bg)-([a-z0-9]+):checked\) \.av-preview (\{[^}]*\})$",
            css,
            re.M,
        )
    )
    for short, part in word.items():
        for name in service.AVATAR_PARTS[part]:
            assert own[(short, name)] == shown[(part, name)]  # the same colours both ways
    # Every style, beard, moustache and pair of glasses shows when its radio is checked,
    # and every eye, brow, mouth, hat, detail and garment of D137.
    shapes = (("hair", "hs"), ("face", "fh"), ("glasses", "gl"), ("eyes", "ey"), ("brows", "br"))
    shapes += (("mouth", "mo"), ("head", "hd"), ("detail", "dt"), ("outfit", "ot"))
    for part, prefix in shapes:
        for name in service.AVATAR_PARTS[part]:
            if name != "none":
                assert f".av-maker:has(#av-{part}-{name}:checked) .{prefix}-{name}" in css
    # A browser without :has keeps the avatar's colours and shows it as saved.
    assert not re.search(r"^\.av\.[^{]*,[^{]*:has", css, re.M)
    assert "@supports not selector(:has(*))" in css


def test_making_it_counts_as_you():
    assert usage.part_of("GET", "/avatar") == "you" and usage.part_of("POST", "/avatar") == "you"


def test_a_flag_is_where_you_are_from_on_your_avatar_and_your_initial(web, token, tmp_path):
    """D143: a flag chosen on the avatar page (none until then; the university's and the
    time zone's countries offered first, then every country) is a round badge on the
    avatar, and on the student's initial in Community once they have a handle."""
    empty = plan({})
    assert service.avatar_of(service.set_avatar(empty, {**MINE, "flag": "it"}))["flag"] == "it"
    picked = service.set_avatar(empty, {**MINE, "flag": "it", "flag_other": "jp"})
    assert service.avatar_of(picked)["flag"] == "jp"  # the list, picked later, wins
    assert service.avatar_of(service.set_avatar(empty, {**MINE, "flag": "none"}))["flag"] == ""
    for bad in ("xx", "../../etc", "IT"):
        with pytest.raises(service.InvalidInput):
            service.set_avatar(empty, {**MINE, "flag": bad})
    assert service.avatar_of(plan({"avatar": {**MINE, "flag": "zz"}}))["flag"] == ""  # not on the list
    codes = [code for code, _name in service.flag_countries()]
    assert len(codes) == len(set(codes)) == 249 and "it" in codes and "fr" in codes
    for code in codes:  # every country offered has its flag shipped
        assert (STATIC / "vendor" / "flags" / "1x1" / f"{code}.svg").exists(), code
    assert (STATIC / "vendor" / "flags" / "LICENSE").read_text(encoding="utf-8").startswith("The MIT License")
    # The page: the countries first (Paris's time zone: France), every country in the list.
    maker = web.get(f"/p/{token}/avatar").text
    assert 'id="av-flag-none"' in maker and 'id="av-flag-fr"' in maker
    other = maker.split('id="av-flag-other"', 1)[1].split("</select>", 1)[0]
    assert other.count("<option value=") == 250  # every country, and "Choose a country"
    picked = {**MINE, "flag": "none", "flag_other": "it"}
    saved = web.post(f"/p/{token}/avatar", data=picked, follow_redirects=False)
    assert saved.status_code == 303
    you = web.get(f"/p/{token}/you").text
    assert re.search(r'<image href="/static/v/[0-9a-f]{10}/vendor/flags/1x1/it\.svg"', you)
    # Community: the badge comes with the handle, and follows the avatar's flag.
    person = web.get(f"/p/{token}/u/flagged")
    assert person.status_code == 404  # no handle yet: nothing in Community
    made = web.post(
        f"/p/{token}/profile",
        data={"handle": "flagged", "university": "Sapienza", "old_enough": "1"},
        follow_redirects=False,
    )
    assert made.status_code == 303

    def badge(code: str) -> str:
        return r'<img class="avatar-flag" src="/static/v/[0-9a-f]{10}/vendor/flags/1x1/' + code + r'\.svg"'

    assert re.search(badge("it"), web.get(f"/p/{token}/u/flagged").text)  # the avatar's, made before
    web.post(f"/p/{token}/avatar", data={**MINE, "flag": "fr"})
    assert re.search(badge("fr"), web.get(f"/p/{token}/u/flagged").text)  # and after
    web.post(f"/p/{token}/avatar", data={**MINE, "flag": "none"})
    assert 'class="avatar-flag"' not in web.get(f"/p/{token}/u/flagged").text  # none shows none
