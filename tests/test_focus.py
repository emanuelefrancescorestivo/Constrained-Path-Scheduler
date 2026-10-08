"""
Focus sessions and the diary (DECISIONS.md D17, D18): the timer counts time away
from the page honestly, a session without the script says its focus was not
checked, a logged session makes its day studied, photos lose their metadata, and
deleting a plan deletes its posts and photos.
"""

from __future__ import annotations

import struct
import zlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cps import service, social
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Tuesday


@pytest.fixture
def sub() -> service.Subscription:
    return service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[service.TaskSpec("Stats report", "2026-10-10 18:00", 6.0, "Advanced Statistics")],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )


def _beats(sub, start, seconds):
    for s in seconds:
        sub = service.focus_beat(sub, now=start + timedelta(seconds=s))
    return sub


def test_the_timer_counts_time_away_from_the_page(sub):
    started = service.start_focus(sub, "Algebra 3", now=NOW)
    # A beat every 30 s for 20 min, then 10 min away (the phone on another app),
    # then 20 more min on the page.
    beats = [*range(0, 1200, 30), *range(1800, 3000, 30)]
    running = _beats(started, NOW, beats)
    view = service.focus_view(running, NOW + timedelta(minutes=50))
    assert view["running"] and view["course"] == "Algebra 3" and view["interruptions"] == 1
    done = service.finish_focus(running, now=NOW + timedelta(seconds=3000))
    draft = service.log_draft(done)
    assert draft["interruptions"] == 1 and draft["checked"] and draft["timed"]
    # 50 min on the clock, about 10 min away: 40 focused.
    assert draft["away_minutes"] == 10 and draft["minutes"] == 40
    assert "focus" not in done.options


def test_without_the_script_the_focus_is_not_checked(sub):
    started = service.start_focus(sub, "Algebra 3", now=NOW)
    draft = service.log_draft(service.finish_focus(started, now=NOW + timedelta(minutes=45)))
    assert draft["minutes"] == 45 and not draft["checked"] and draft["timed"]
    with pytest.raises(service.InvalidInput, match="no session running"):
        service.finish_focus(sub, now=NOW)
    with pytest.raises(service.InvalidInput, match="say what you studied"):
        service.start_focus(sub, "  ", now=NOW)


def test_a_logged_session_is_in_the_diary_and_makes_its_day_studied(sub, tmp_path):
    plan = service.PlanReport.from_dict(sub.plan)
    first = plan.sessions[0]
    start = datetime.fromisoformat(first.start).astimezone(UTC)
    running = service.start_focus(sub, "", sid=service.session_id(first), now=start)
    assert running.options["focus"]["course"] in (first.title, first.subject)
    finished = service.finish_focus(
        _beats(running, start, range(0, 3600, 30)), now=start + timedelta(hours=1)
    )
    form = {
        "title": "Problem sheet 2",
        "note": "Got stuck on 3b.",
        "effort": "7",
        "progress": "4",
        "visibility": "me",
    }
    logged, post = service.log_session(tmp_path, finished, form, now=start + timedelta(hours=1))
    assert post.data["minutes"] == 60 and post.data["checked"] and post.data["effort"] == 7
    assert "focus_draft" not in logged.options
    # The plan's session it was started from is reported done.
    assert logged.outcomes[service.session_id(first)] == "done"
    diary = service.diary_view(tmp_path, logged, start + timedelta(hours=1))
    card = diary["posts"][0]
    assert card["title"] == "Problem sheet 2" and card["focus"] == "focused the whole time"
    assert card["effort_word"] == "hard" and card["progress_word"] == "good"
    assert diary["week"]["sessions"] == 1 and diary["week"]["hours"] == "1"
    # A logged session on a day with nothing planned makes that day studied.
    sunday = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    extra, _ = service.log_session(
        tmp_path, logged, {"course": "Algebra 3", "effort": "5", "progress": "3", "minutes": "45"}, now=sunday
    )
    view = service.progress_view(extra, sunday, logged=service.logged_sessions(tmp_path, extra))
    days = {d["date"]: d["state"] for row in view["grid"] for d in row}
    assert days["2026-10-04"] == "studied"
    assert view["totals"]["logged"] == 2


def test_changing_the_minutes_drops_the_timed_label(sub, tmp_path):
    finished = service.finish_focus(
        service.start_focus(sub, "Algebra 3", now=NOW), now=NOW + timedelta(minutes=30)
    )
    _, post = service.log_session(
        tmp_path, finished, {"effort": "4", "progress": "3", "minutes": "90"}, now=NOW + timedelta(minutes=30)
    )
    assert post.data["minutes"] == 90 and not post.data["timed"] and not post.data["checked"]
    with pytest.raises(service.InvalidInput, match="Effort"):
        service.log_session(tmp_path, sub, {"course": "x", "effort": "11", "progress": "3"}, now=NOW)


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def _jpeg() -> bytes:
    sof = b"\x08" + struct.pack(">HH", 480, 640) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return (
        b"\xff\xd8"
        + _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        + _segment(0xE1, b"Exif\x00\x00GPSLatitude 48.8461 N, a secret place")
        + _segment(0xFE, b"taken by Somebody Real")
        + _segment(0xC0, sof)
        + b"\xff\xda"
        + struct.pack(">H", 8)
        + b"\x01\x01\x00\x00\x3f\x00"
        + b"image data \x12\x34"
        + b"\xff\xd9"
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 32, 16, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"tEXt", b"Author\x00Somebody Real")
        + _chunk(b"eXIf", b"GPS secret")
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 100))
        + _chunk(b"IEND", b"")
    )


def test_photos_lose_their_metadata_and_only_images_are_kept():
    jpeg, ext = social.clean_photo(_jpeg())
    assert ext == "jpg" and b"GPS" not in jpeg and b"Somebody" not in jpeg
    assert b"JFIF" in jpeg and b"image data" in jpeg and jpeg.endswith(b"\xff\xd9")
    png, ext = social.clean_photo(_png())
    assert ext == "png" and b"Somebody" not in png and b"GPS" not in png and b"IDAT" in png
    for bad in (b"<svg onload=alert(1)>", b"GIF89a....", b"\xff\xd8\xff"):
        with pytest.raises(social.SocialError):
            social.clean_photo(bad)
    with pytest.raises(social.SocialError, match="at most 3 MB"):
        social.clean_photo(b"\xff\xd8" + b"\x00" * (social.MAX_PHOTO_BYTES + 1))


def test_photos_are_shown_to_whom_the_post_allows_and_go_with_the_plan(sub, tmp_path):
    form = {"course": "Algebra 3", "effort": "6", "progress": "3", "minutes": "50", "visibility": "me"}
    _, private = service.log_session(tmp_path, sub, form, [_jpeg()], now=NOW)
    with pytest.raises(service.InvalidInput, match="choose a handle"):  # sharing needs a profile
        service.log_session(tmp_path, sub, {**form, "visibility": "everyone"}, now=NOW)
    service.save_profile(tmp_path, sub.token, {"handle": "ada", "university": "Lyon 1", "old_enough": "1"})
    _, public = service.log_session(tmp_path, sub, {**form, "visibility": "everyone"}, [_png()], now=NOW)
    mine, theirs = private.data["photos"][0], public.data["photos"][0]
    assert service.photo_for(tmp_path, sub.token, mine)[1] == "image/jpeg"
    assert service.photo_for(tmp_path, None, mine) is None  # "only me"
    assert service.photo_for(tmp_path, None, theirs)[1] == "image/png"  # "everyone"
    assert service.photo_for(tmp_path, None, "../../etc/passwd") is None
    store = Store(tmp_path)
    assert (store.photos / mine).is_file()
    # Deleting a post deletes its photo; deleting the plan deletes everything.
    assert service.delete_post(tmp_path, sub.token, private.id) and not (store.photos / mine).exists()
    assert not service.delete_post(tmp_path, "someone-else-entirely-xx", public.id)
    service.save_subscription(tmp_path, sub)
    assert service.delete_subscription(tmp_path, sub.token)
    assert not (store.photos / theirs).exists() and social.posts_of(store, sub.token) == []
