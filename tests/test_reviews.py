"""
benchmarks/reviews.py, the review mining behind docs/MARKET.md: the feed is parsed
as Apple documents it, themes are coded in English and French, reviewers' names
never leave the script, and the summary's numbers add up.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _reviews():
    spec = importlib.util.spec_from_file_location("reviews", ROOT / "benchmarks" / "reviews.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["reviews"] = module  # its dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


def _entry(rating, title, text, day="2026-09-01"):
    return {
        "author": {"name": {"label": "Somebody Real"}, "uri": {"label": "https://example.org/u/1"}},
        "im:rating": {"label": str(rating)},
        "im:version": {"label": "7.3.5"},
        "title": {"label": title},
        "content": {"label": text},
        "updated": {"label": f"{day}T10:00:00-07:00"},
    }


PAGE = {
    "feed": {
        "entry": [
            {"im:name": {"label": "The app itself, no rating"}},
            _entry(1, "Lost everything", "After the update it crashes and my timetable is gone."),
            _entry(2, "Trop cher", "L'abonnement est trop cher pour un étudiant."),
            _entry(5, "Love it", "Studying with my friends keeps me motivated every day."),
            _entry(4, "Nice", "Good."),
        ]
    }
}


def test_a_page_of_the_feed_becomes_reviews_and_the_app_entry_is_skipped():
    r = _reviews()
    reviews = r.parse_feed(PAGE, "Some app", "fr")
    assert [x.rating for x in reviews] == [1, 2, 5, 4]
    assert reviews[0].date == "2026-09-01" and reviews[0].version == "7.3.5"
    # A page with a single entry is an object, not a list.
    one = r.parse_feed({"feed": {"entry": _entry(3, "ok", "fine")}}, "Some app", "us")
    assert len(one) == 1 and one[0].rating == 3


def test_themes_are_found_in_english_and_french_on_whole_words():
    r = _reviews()
    a, b, c, d = r.parse_feed(PAGE, "Some app", "fr")
    assert r.themes(a) == ["bugs and data loss", "sync and calendar"]
    assert r.themes(b) == ["price and paywall"]
    assert r.themes(c) == ["motivation and streaks", "friends and social"]
    assert r.themes(d) == ["other"]
    # "paid" is a word; "unpaid" is not "paid", and "pain" is not "ai".
    x = r.Review("x", "us", 3, "", "unpaid pain", "2026-09-01")
    assert r.themes(x) == ["other"]


def test_the_summary_adds_up():
    r = _reviews()
    reviews = r.parse_feed(PAGE, "Some app", "fr")
    rows = {c.theme: c for c in r.summarise(reviews)["Some app"]}
    assert rows["price and paywall"].reviews == 1
    assert rows["price and paywall"].share == 0.25
    assert rows["price and paywall"].share_of_low == 0.5  # one of the two low ratings
    assert rows["bugs and data loss"].mean_rating == 1.0
    text = r.report(reviews)
    assert "4 reviews, 2026-09-01 to 2026-09-01, mean rating 3.00." in text
    assert "| price and paywall | 1 | 25% | 2.00 | 50% |" in text


def test_reviewers_names_are_not_kept_or_printed(tmp_path, monkeypatch, capsys):
    r = _reviews()
    monkeypatch.setattr(r, "fetch", lambda app, country, pages: [PAGE])
    assert r.main(["--app", "910639339", "--country", "fr", "--save", str(tmp_path)]) == 0
    saved = (tmp_path / "910639339-fr.json").read_text(encoding="utf-8")
    assert "Somebody Real" not in saved and "example.org" not in saved
    assert "Somebody Real" not in capsys.readouterr().out
    # And the saved answers can be coded again without the network.
    assert len(r.load(tmp_path)) == 4
    assert json.loads(saved)["app"] == "My Study Life"
