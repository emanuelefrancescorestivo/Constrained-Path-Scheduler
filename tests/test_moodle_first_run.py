"""
Moodle in the first run, and the link to its Security keys that lands there (DECISIONS.md D216).
The owner, 2026-11-27: "it takes too many steps to upload moodle, it should be proposed in the very
first tutorial if a student wants to upload it, also when clicking on "take me to security keys" it
does not take you there".

What must hold: a Moodle's site is read from the address of any of its pages, in a subfolder too,
and its Security keys page built on the site, never on the page pasted (AUDIT 146); a site kept
before is read again the same way; the Moodle itself is asked where it is (its wwwroot, on the
host asked only), how it signs in and whether it is open to apps, the host's `/moodle` tried when
the host alone does not answer; a university's usual addresses are guessed from its domains. The
first run asks for Moodle after the courses, on one page: the address, the key, and the courses
then found, linked by name and planned without another tap, or skipped. A key refused is said on
the page it was given on, with what was typed. The demo reads Moodle in its queue (AUDIT 147).
"""

from __future__ import annotations

import threading
import time as clock
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from dataclasses import replace

from fastapi.testclient import TestClient
from test_first_run import ALEX, EVENING, Clock, _courses, _start, _web

from cps import service, showcase
from cps.sources import SourceError
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
SITE = showcase.DEMO_MOODLE
KEY = showcase.DEMO_KEY


@pytest.mark.parametrize(
    ("typed", "site"),
    [
        ("moodle.example.edu", "https://moodle.example.edu"),
        ("  https://Moodle.Example.edu/  ", "https://moodle.example.edu"),
        ("https://moodle.example.edu/my/", "https://moodle.example.edu"),
        ("https://moodle.example.edu/my/courses.php", "https://moodle.example.edu"),
        ("https://moodle.example.edu/user/preferences.php", "https://moodle.example.edu"),
        ("https://moodle.example.edu/user/managetoken.php", "https://moodle.example.edu"),
        ("moodle.example.edu/calendar/view.php?view=month#top", "https://moodle.example.edu"),
        ("https://moodle.example.edu/course/view.php?id=4127", "https://moodle.example.edu"),
        ("https://moodle.example.edu/index.php", "https://moodle.example.edu"),
        ("http://elearning.uni.it/moodle/my/", "http://elearning.uni.it/moodle"),
        ("https://lms.example.fr/fac/2026/login/index.php", "https://lms.example.fr/fac/2026"),
        ("https://x.edu:8443/moodle/user/profile.php?id=3", "https://x.edu:8443/moodle"),
    ],
)
def test_a_moodle_s_site_is_read_from_any_of_its_pages(typed, site):
    assert service.moodle_site_of(typed) == site


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "moodle",
        "javascript:alert(1)",
        "ftp://moodle.example.edu",
        "https://a@b.example.edu/",
        "https://drive.google.com/file/d/abc",
        "a b.example.edu",
    ],
)
def test_what_is_not_a_web_page_s_address_gives_no_site(typed):
    assert service.moodle_site_of(typed) is None


def test_a_page_s_address_without_a_query_links_the_site_and_a_site_kept_before_is_read_again(tmp_path):
    # AUDIT 146: before, `…/user/preferences.php` was kept as a folder the Moodle was under.
    assert service.read_moodle_address("https://moodle.example.edu/user/preferences.php") == (SITE, None)
    with pytest.raises(service.InvalidInput):
        service.read_moodle_address("https://drive.google.com/file/d/abc")  # deeper than any Moodle
    sub = _plan(tmp_path)
    old = replace(sub, options={**sub.options, "moodle_site": f"{SITE}/user/preferences.php"})
    assert service.moodle_site(old) == SITE
    assert service.moodle_key_view(old)["page"] == f"{SITE}/user/managetoken.php"


class Probe:
    """A Moodle's public answer (`tool_mobile_get_public_config`) by the site asked."""

    def __init__(self, answers: dict[str, dict]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, site: str) -> dict:
        self.asked.append(site)
        if site not in self.answers:
            raise SourceError("the link answered with an error (404 Not Found)")
        return self.answers[site]


OPEN = {"enablewebservices": 1, "enablemobilewebservice": 1}


def test_the_moodle_says_where_it_is_and_how_it_signs_in():
    found = service.moodle_where(
        "https://uni.example.edu/moodle/my/",
        Probe(
            {
                "https://uni.example.edu/moodle": {
                    **OPEN,
                    "wwwroot": "https://uni.example.edu/moodle/",
                    "sitename": '<span lang="en" class="multilang">UniX</span> Moodle',
                    "typeoflogin": 1,
                }
            }
        ),
    )
    assert found["found"] and found["site"] == "https://uni.example.edu/moodle"
    assert found["page"] == "https://uni.example.edu/moodle/user/managetoken.php"
    assert found["name"] == "UniX Moodle" and found["says"] == "Found: «UniX Moodle»."
    # Single sign-on: the line says to tap again if Moodle opens its home page after signing in.
    sso = service.moodle_where(
        "moodle.example.edu", Probe({SITE: {**OPEN, "sitename": "M", "typeoflogin": 2}})
    )
    assert sso["sso"] and "tap the link again" in sso["says"]
    closed = service.moodle_where(
        "moodle.example.edu", Probe({SITE: {"sitename": "M", "enablewebservices": 0}})
    )
    assert not closed["apps"] and "closed to apps" in closed["says"]
    # The host alone does not answer: its /moodle does, and Moodle names its own folder.
    probe = Probe({"https://lms.example.edu/moodle": {**OPEN, "wwwroot": "https://lms.example.edu/moodle"}})
    deep = service.moodle_where("lms.example.edu", probe)
    assert probe.asked == ["https://lms.example.edu", "https://lms.example.edu/moodle"]
    assert deep["page"] == "https://lms.example.edu/moodle/user/managetoken.php"
    # A Moodle cannot send the student to another host.
    elsewhere = service.moodle_where(
        "moodle.example.edu", Probe({SITE: {**OPEN, "wwwroot": "https://evil.example"}})
    )
    assert elsewhere["site"] == SITE
    # No answer: the site the address gives, said so; junk: no site at all.
    silent = service.moodle_where("https://moodle.example.edu/my/", Probe({}))
    assert not silent["found"] and silent["page"] == f"{SITE}/user/managetoken.php"
    assert "did not answer as a Moodle" in silent["says"]
    assert service.moodle_where("not an address", Probe({}))["site"] is None


def test_a_university_s_moodle_is_guessed_from_its_domains(tmp_path):
    sub = _plan(tmp_path)
    assert service.moodle_guesses(tmp_path, sub.token, "Europe/Rome") == []  # no profile yet
    service.save_profile(
        tmp_path, sub.token, {**ALEX, "university": "Polytechnic Institute of Turin"}, tz="Europe/Rome"
    )
    sites = service.moodle_guesses(tmp_path, sub.token, "Europe/Rome")
    assert sites[:4] == [
        "https://moodle.polito.it",
        "https://elearning.polito.it",
        "https://e-learning.polito.it",
        "https://polito.it/moodle",
    ]
    probe = Probe(
        {
            "https://elearning.polito.it": {**OPEN, "sitename": "eLearning"},
            "https://moodle.polito.it": {"sitename": "closed"},
        }
    )
    found = service.moodle_guess(sites, probe)
    assert found["site"] == "https://elearning.polito.it" and found["guessed"]
    assert "Found from your university" in found["says"]
    assert service.moodle_guess(sites, Probe({})) is None


def test_the_key_keeps_the_site_as_moodle_names_it(tmp_path):
    sub = _plan(tmp_path)

    def call(site, key, function, params=None):
        if function == "core_webservice_get_site_info":
            return {
                "sitename": "U",
                "siteurl": "https://uni.example.edu/moodle",
                "functions": [{"name": "core_course_get_contents"}],
            }
        raise AssertionError(function)

    kept = service.connect_moodle_key(sub, KEY, call=call, now=NOW, site_text="uni.example.edu")
    assert kept.options["moodle_key"]["site"] == "https://uni.example.edu/moodle"
    assert service.moodle_site(kept) == "https://uni.example.edu/moodle"
    with pytest.raises(service.InvalidInput, match="Type your Moodle's address"):
        service.connect_moodle_key(sub, KEY, call=call, now=NOW, site_text="not an address")


def test_the_first_run_asks_for_moodle_after_the_courses_and_plans_them_at_once(tmp_path):
    with _web(
        tmp_path,
        Clock(EVENING),
        moodle_call=showcase.demo_moodle_call,
        fetch_file=showcase.demo_fetch_file,
        moodle_probe=showcase.demo_moodle_probe,
        moodle_demo=("moodle.example.edu", KEY),
    ) as web:
        where = _start(web)
        token = where.split("/")[2]
        web.post(where, data=ALEX)
        assert _courses(web, token) == f"/p/{token}/start/moodle"
        page = unescape(web.get(f"/p/{token}/start/moodle").text)
        assert "Plan from your Moodle" in page and "Setting up, step 4 of 6" in page
        assert f'data-moodle-where="/p/{token}/moodle/where" data-moodle-guess' in page
        assert 'name="back" value="start"' in page and "Save and plan my courses" in page
        assert "Not on Moodle? Skip" in page and f"with the key {KEY}" in page  # the demo's own
        # The server's check, as the script asks it while the address is typed.
        found = web.get(f"/p/{token}/moodle/where", params={"site": "https://moodle.example.edu/my/"}).json()
        assert found["found"] and found["page"] == f"{SITE}/user/managetoken.php"
        # A key refused: said on this page, with the address typed kept.
        refused = web.post(
            f"/p/{token}/moodle/key",
            data={"key": "0" * 31, "site": "moodle.example.edu/my/", "back": "start"},
        )
        assert refused.status_code == 400
        shown = unescape(refused.text)
        assert "A Moodle key is 32 letters" in shown and 'value="moodle.example.edu/my/"' in shown
        assert "Plan from your Moodle" in shown
        # The key: the courses found, linked by their names and planned, then the class.
        answer = web.post(
            f"/p/{token}/moodle/key",
            data={
                "key": f" {KEY} ",
                "site": "https://moodle.example.edu/user/preferences.php",
                "back": "start",
            },
            follow_redirects=False,
        )
        assert answer.status_code == 303
        kept = service.load_subscription(tmp_path, token)
        planned = set(kept.options.get("study_plans") or {})
        linked = set(kept.options.get("moodle_courses") or {})
        assert linked and planned == linked  # every course linked was planned, no tap more
        assert answer.headers["location"] == f"/p/{token}/start/class?moodle={len(linked)}"
        mates = unescape(web.get(answer.headers["location"]).text)
        assert "Setting up, step 5 of 6" in mates and "Moodle connected:" in mates
        assert service.first_run(kept)["step"] == "class"


def test_the_moodle_step_can_be_skipped_and_works_without_a_script(tmp_path):
    probe = Probe({SITE: {**OPEN, "sitename": "Moodle", "wwwroot": SITE}})
    with _web(tmp_path, Clock(EVENING), moodle_probe=probe) as web:
        where = _start(web)
        token = where.split("/")[2]
        web.post(where, data=ALEX)
        _courses(web, token)
        # Without the script: "Find my Moodle" keeps the address, and the page has the link.
        kept = web.post(
            f"/p/{token}/moodle/site",
            data={"site": "moodle.example.edu/my/courses.php", "back": "start"},
            follow_redirects=False,
        )
        assert kept.headers["location"] == f"/p/{token}/start/moodle"
        page = unescape(web.get(f"/p/{token}/start/moodle").text)
        assert f'href="{SITE}/user/managetoken.php"' in page and "data-moodle-site" not in page
        bad = web.post(f"/p/{token}/moodle/site", data={"site": "nowhere", "back": "start"})
        assert bad.status_code == 400 and "Type your Moodle's address" in unescape(bad.text)
        skipped = web.post(f"/p/{token}/start/moodle", data={"skip": "1"}, follow_redirects=False)
        assert skipped.headers["location"] == f"/p/{token}/start/class"
        assert "Moodle connected" not in unescape(web.get(f"/p/{token}/start/class").text)


def test_the_guide_says_a_refused_key_on_its_own_page_and_plans_after_the_key(tmp_path):
    sub = _plan(tmp_path)
    config = Config(
        store=tmp_path,
        clock=lambda: NOW,
        background=False,
        sweep_every=None,
        remind_every=None,
        moodle_call=showcase.demo_moodle_call,
        fetch_file=showcase.demo_fetch_file,
        moodle_probe=showcase.demo_moodle_probe,
    )
    with TestClient(create_app(config)) as web:
        token = sub.token
        refused = web.post(f"/p/{token}/moodle/key", data={"key": "f" * 32, "site": SITE, "back": "guide"})
        assert refused.status_code == 400 and "Plan your study from Moodle" in unescape(refused.text)
        assert "Moodle did not accept this key" in unescape(refused.text)
        answer = web.post(
            f"/p/{token}/moodle/key", data={"key": KEY, "site": SITE, "back": "guide"}, follow_redirects=False
        )
        assert answer.headers["location"] == f"/p/{token}/guide/moodle#step-read"
        kept = service.load_subscription(tmp_path, token)
        assert set(kept.options["study_plans"]) == set(kept.options["moodle_courses"])
        # The JSON check counts against its own allowance and answers 404 for no plan.
        assert web.get("/p/nobody/moodle/where", params={"site": SITE}).status_code == 404


def test_the_demo_reads_moodle_in_its_queue_though_its_feeds_are_not_in_the_background(tmp_path):
    """AUDIT 147: the demo turns `background` off for its feeds; D215's queue followed it, so the
    owner's own Moodle was read inside the request again on the demo, where D215 was to help."""
    sub = _plan(tmp_path)
    gate = threading.Event()

    def slow(site, key, function, params=None):
        if function == "core_course_get_contents":
            gate.wait(10)
        return showcase.demo_moodle_call(site, key, function, params)

    config = Config(
        store=tmp_path,
        clock=lambda: NOW,
        background=False,
        moodle_inline=False,
        sweep_every=None,
        remind_every=None,
        moodle_call=slow,
        fetch_file=showcase.demo_fetch_file,
    )
    with TestClient(create_app(config)) as web:
        started = clock.monotonic()
        answer = web.post(
            f"/p/{sub.token}/moodle/key",
            data={"key": KEY, "site": SITE, "back": "guide"},
            follow_redirects=False,
        )
        assert answer.status_code == 303 and clock.monotonic() - started < 5  # Moodle has not answered
        gate.set()
        for _ in range(100):
            kept = service.load_subscription(tmp_path, sub.token)
            if not service.moodle_readings(tmp_path, kept, NOW)["reading"] and kept.options.get(
                "study_plans"
            ):
                break
            clock.sleep(0.1)
        assert kept.options.get("study_plans")


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec(c, None, familiarity=3) for c in ("Algebra 3", "Analysis 3")],
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
