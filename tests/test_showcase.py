"""
`cps demo` (cps.showcase): a store of made-up students for trying the hosted app.
Fast checks of its clock and its drawn pages; one slow run of the whole command,
then the app on the store it made.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from html import unescape

import pytest

from cps import service, showcase, social
from cps.cli import main


def test_the_demo_stands_inside_the_sample_semester():
    assert showcase.offset_for(date(2026, 11, 20)) == 0  # weeks of history behind, exams ahead
    early, late = showcase.offset_for(date(2026, 10, 9)), showcase.offset_for(date(2027, 3, 1))
    assert date(2026, 10, 9) - timedelta(days=early) == showcase.MIDDLE
    assert date(2027, 3, 1) - timedelta(days=late) == showcase.MIDDLE
    assert showcase.MIDDLE.weekday() == 3  # a Thursday: the week has begun


def test_the_drawn_pages_of_notes_are_images_the_network_keeps():
    page = showcase.page_png(random.Random(1))
    kept, kind = social.clean_photo(page)
    assert kind == "png" and kept.startswith(b"\x89PNG") and len(page) < social.MAX_PHOTO_BYTES
    assert showcase.page_png(random.Random(1)) == page  # deterministic


@pytest.mark.slow
def test_cps_demo_makes_a_store_to_look_around_and_serves_it(tmp_path, capsys):
    store = tmp_path / "demo"
    assert main(["demo", "--store", str(store), "--no-serve"]) == 0
    said = capsys.readouterr().out
    found = showcase.Showcase.load(store)
    assert found is not None and f"/p/{found.you}" in said and "@lena" in said and found.admin in said
    assert "everyone in it is made up" in said
    # Run again: the same store, not a new one.
    assert main(["demo", "--store", str(store), "--no-serve"]) == 0
    assert showcase.Showcase.load(store) == found
    # Never a directory it did not make.
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "thesis.tex").write_text("keep me")
    assert main(["demo", "--store", str(tmp_path / "mine"), "--no-serve"]) == 2
    assert (tmp_path / "mine" / "thesis.tex").exists()

    you = service.load_subscription(store, found.you)
    now = found.clock()
    view = service.progress_view(you, now, logged=service.logged_sessions(store, you))
    assert view["streak"]["current"] > 0 and view["week"]["planned"] > 0
    assert service.cards_due(store, you, now) > 0
    assert len(service.library_view(store, you, now=now)["notes"]) == 4
    assert len(service.groups_view(store, you, now)["invitations"]) == 1
    assert service.group_view(store, you, found.group, now)["size"] == 4

    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    config = Config(
        store=store, clock=found.clock, background=False, sweep_every=None, admin_token=found.admin
    )
    with TestClient(create_app(config)) as web:
        for path in (
            "",
            "/progress",
            "/trends",
            "/community",
            "/notes",
            "/groups",
            "/cards/review",
            "/focus",
        ):
            page = web.get(f"/p/{found.you}{path}")
            assert page.status_code == 200, path
        assert "@alex" in unescape(web.get(f"/p/{found.people['marco']}/u/alex").text)
        assert web.get(f"/admin/{found.admin}").status_code == 200
