"""
E-mail accounts (DECISIONS.md D94): a student makes one before the plan, and the plan
then opens only for it. What must hold: the password is kept only as a salted scrypt
digest; a plan of an account opens for no one else, whatever address they have; wrong
passwords lock an account for a while; links sent by e-mail work once and in time; a
GET changes nothing; deleting the plan deletes the account.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import accounts, service
from cps.store import Store
from cps.web import Config, create_app

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
PASSWORD = "violet tram winter pencil"
EMAIL = "ana.rossi@example.org"
ADMIN = "the-owners-key-for-these-tests"  # 24 characters at least, or there is no page


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(MORNING)


@pytest.fixture
def mails() -> list:
    return []


def _app(tmp_path, clock, mails, **options):
    options = {"mailer": mails.append, **options}
    config = Config(
        store=tmp_path,
        clock=clock,
        background=False,
        sweep_every=None,
        remind_every=None,
        accounts=True,
        admin_token=ADMIN,
        **options,
    )
    return create_app(config)


@pytest.fixture
def app(tmp_path, clock, mails):
    return _app(tmp_path, clock, mails)


@pytest.fixture
def web(app, tmp_path):
    with TestClient(app) as client:
        client.store = Store(tmp_path)
        yield client


def _signup(client, email=EMAIL, password=PASSWORD):
    return client.post("/account/signup", data={"email": email, "password": password}, follow_redirects=False)


def _start(client) -> str:
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = client.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    assert answer.status_code == 303, answer.text
    return answer.headers["location"].split("/")[2]


@pytest.fixture
def owned(web):
    """A student with an account and its plan, signed in on `web`."""
    assert _signup(web).status_code == 303
    return _start(web)


def _row(client, sql, *args):
    with closing(sqlite3.connect(client.store.path)) as db:
        return db.execute(sql, args).fetchall()


def _link(mail, kind):
    return re.search(rf"/account/{kind}/(\S+)", mail.body).group(1)


# --------------------------------------------------------------------------- #
# The account first
# --------------------------------------------------------------------------- #


def test_the_home_page_asks_for_an_account_before_the_plan(web):
    home = web.get("/")
    assert 'action="/account/signup"' in home.text and 'action="/start"' not in home.text
    assert "set-cookie" not in home.headers  # nobody is signed in by looking
    refused = web.post(
        "/start",
        data={"tz": "Europe/Paris"},
        files={"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")},
        follow_redirects=False,
    )
    assert refused.status_code == 401 and "Make your account first" in refused.text
    assert web.store.count() == 0


def test_signing_up_signs_this_device_in_and_mails_a_link_to_confirm(web, mails):
    answer = _signup(web, email="  Ana.Rossi@Example.ORG ")
    assert answer.status_code == 303 and answer.headers["location"] == "/?made=1#start"
    cookie = answer.headers["set-cookie"]
    assert cookie.startswith("cps_session=") and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert f"Max-Age={90 * 86400}" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie  # plain http, as here: a Secure cookie would never come back
    assert [m.to for m in mails] == [EMAIL] and mails[0].subject == "Confirm your e-mail address"
    home = web.get("/?made=1").text
    assert f"Signed in as {EMAIL}" in home and 'action="/start"' in home
    assert 'action="/account/signup"' not in home


def test_over_https_the_cookie_is_secure(tmp_path, clock, mails):
    with TestClient(_app(tmp_path, clock, mails, base_url="https://plan.example.org")) as client:
        assert "Secure" in _signup(client).headers["set-cookie"]


@pytest.mark.parametrize(
    ("email", "password", "refusal"),
    [
        ("not an address", PASSWORD, "does not look like an e-mail address"),
        ("ana@localhost", PASSWORD, "does not look like an e-mail address"),
        (EMAIL, "eleven char", "at least 12 characters"),
        (EMAIL, "two    spaces", "at least 12 characters"),  # runs of spaces count as one
        (EMAIL, "x" * 129, "at most 128 characters"),
        (EMAIL, "motdepasse123", "one of the most common"),
        (EMAIL, "aaaabbbbaaaabbbb", "one of the most common"),
        (EMAIL, "ana.rossi@example.org!!", "does not contain your e-mail address"),
    ],
)
def test_an_account_needs_an_address_and_a_password_that_follows_the_rules(web, email, password, refusal):
    answer = _signup(web, email=email, password=password)
    assert answer.status_code == 400 and refusal in answer.text
    assert "set-cookie" not in answer.headers
    assert _row(web, "SELECT COUNT(*) FROM accounts") == [(0,)]


def test_one_account_per_address_whatever_its_case(web):
    assert _signup(web).status_code == 303
    again = _signup(TestClient(web.app), email=EMAIL.upper())
    assert again.status_code == 400 and "exists already" in again.text


def test_the_password_is_kept_only_as_a_salted_scrypt_digest(web):
    _signup(web)
    _signup(TestClient(web.app), email="other@example.org")
    digests = [d for (d,) in _row(web, "SELECT password FROM accounts")]
    assert len(set(digests)) == 2  # the same password, two salts
    for digest in digests:
        assert digest.startswith("scrypt$16384$8$5$") and PASSWORD not in digest
    assert accounts.password_matches(digests[0], PASSWORD)
    assert not accounts.password_matches(digests[0], PASSWORD + " ")
    assert not accounts.password_matches("md5$whatever", PASSWORD)


# --------------------------------------------------------------------------- #
# The plan opens only for its account
# --------------------------------------------------------------------------- #


def test_a_plan_made_while_signed_in_belongs_to_the_account(web, owned):
    assert _row(web, "SELECT token FROM accounts WHERE email = ?", EMAIL) == [(owned,)]
    assert web.get(f"/p/{owned}").status_code == 200
    assert web.get("/", follow_redirects=False).headers["location"] == f"/p/{owned}/open"
    again = web.post(
        "/start",
        data={"tz": "Europe/Paris"},
        files={"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")},
        follow_redirects=False,
    )
    assert again.headers["location"] == f"/p/{owned}/open"  # one plan an account, kept across semesters
    assert web.store.count() == 1


def test_nobody_else_opens_it_even_with_its_address(web, owned):
    other = TestClient(web.app)
    for path in (f"/p/{owned}", f"/p/{owned}/settings", f"/p/{owned}/export", f"/s/{owned}/s0"):
        answer = other.get(path, follow_redirects=False)
        assert answer.status_code == 303, path
        assert answer.headers["location"].startswith("/account/signin?next=")
    before = web.store.get(owned)
    change = other.post(f"/p/{owned}/delete", follow_redirects=False)
    assert change.status_code == 303 and web.store.get(owned) == before
    # What a browser fetches without cookies, and what a calendar app reads, still work.
    assert other.get(f"/p/{owned}/manifest.webmanifest").status_code == 200
    feed = re.search(r"/feed/([A-Za-z0-9_-]+)\.ics", web.get(f"/p/{owned}/feed").text)
    assert feed and other.get(f"/feed/{feed.group(1)}.ics").status_code == 200


def test_signing_in_leads_back_to_the_page_asked_for_and_nowhere_else(web, owned):
    other = TestClient(web.app)
    signin = other.get(f"/account/signin?next=/p/{owned}/week")
    assert f'name="next" value="/p/{owned}/week"' in signin.text
    answer = other.post(
        "/account/signin",
        data={"email": EMAIL.upper(), "password": PASSWORD, "next": f"/p/{owned}/week"},
        follow_redirects=False,
    )
    assert answer.status_code == 303 and answer.headers["location"] == f"/p/{owned}/week"
    assert other.get(f"/p/{owned}/week").status_code == 200
    for wanted in ("https://evil.example/p/x", "//evil.example", f"/p/{'A' * 32}/week"):
        third = TestClient(web.app)
        answer = third.post(
            "/account/signin",
            data={"email": EMAIL, "password": PASSWORD, "next": wanted},
            follow_redirects=False,
        )
        assert answer.headers["location"] == f"/p/{owned}/open", wanted


def test_a_wrong_address_and_a_wrong_password_get_the_same_answer(web, owned):
    other = TestClient(web.app)
    wrong_password = other.post("/account/signin", data={"email": EMAIL, "password": "not the right one"})
    nobody = other.post("/account/signin", data={"email": "nobody@example.org", "password": PASSWORD})
    assert wrong_password.status_code == nobody.status_code == 400
    assert (
        "Wrong e-mail address or password." in wrong_password.text
        and "Wrong e-mail address or password." in nobody.text
    )


def test_ten_wrong_passwords_lock_the_account_for_a_quarter_of_an_hour(web, owned, clock):
    other = TestClient(web.app)
    for _ in range(accounts.FAILURES):
        other.post("/account/signin", data={"email": EMAIL, "password": "not the right one"})
    locked = other.post(
        "/account/signin", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert locked.status_code == 400 and "Too many wrong passwords" in locked.text
    clock.now += accounts.LOCKED_FOR + timedelta(minutes=1)
    answer = other.post(
        "/account/signin", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert answer.status_code == 303 and _row(web, "SELECT failures FROM accounts") == [(0,)]


def test_signing_out_ends_the_session_for_good(web, owned):
    other = TestClient(web.app)
    other.post("/account/signin", data={"email": EMAIL, "password": PASSWORD})
    cookie = other.cookies.get("cps_session")
    out = other.post("/account/signout")
    assert out.status_code == 200 and 'data-forget="' in out.text  # its offline copies go too
    stale = TestClient(web.app, cookies={"cps_session": cookie})
    assert stale.get(f"/p/{owned}", follow_redirects=False).status_code == 303
    assert web.get(f"/p/{owned}").status_code == 200  # the other device stays signed in


def test_a_session_unused_for_90_days_ends_and_one_in_use_is_kept(web, owned, clock):
    clock.now += timedelta(days=2)
    renewed = web.get(f"/p/{owned}")
    assert "cps_session=" in renewed.headers.get("set-cookie", "")  # used: kept 90 days more
    assert "set-cookie" not in web.get(f"/p/{owned}").headers  # at most once a day
    clock.now += accounts.SESSION_LIFE + timedelta(days=1)
    assert web.get(f"/p/{owned}", follow_redirects=False).status_code == 303


def test_changing_the_password_needs_the_current_one_and_signs_out_the_others(web, owned):
    other = TestClient(web.app)
    other.post("/account/signin", data={"email": EMAIL, "password": PASSWORD})
    settings = web.get(f"/p/{owned}/settings").text
    assert 'id="login"' in settings and EMAIL in settings and "Sign out the other device" in settings
    refused = web.post(
        f"/p/{owned}/account/password",
        data={"current": "a wrong guess here", "password": "a new long passphrase"},
    )
    assert "not your current password" in refused.text
    web.post(f"/p/{owned}/account/password", data={"current": PASSWORD, "password": "a new long passphrase"})
    assert web.get(f"/p/{owned}").status_code == 200
    assert other.get(f"/p/{owned}", follow_redirects=False).status_code == 303
    fresh = TestClient(web.app)
    old = fresh.post("/account/signin", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False)
    assert old.status_code == 400
    new = fresh.post(
        "/account/signin", data={"email": EMAIL, "password": "a new long passphrase"}, follow_redirects=False
    )
    assert new.status_code == 303


# --------------------------------------------------------------------------- #
# The links sent by e-mail
# --------------------------------------------------------------------------- #


def test_the_confirmation_link_works_once_and_opening_it_changes_nothing(web, owned, mails, clock):
    value = _link(mails[0], "confirm")
    page = web.get(f"/account/confirm/{value}")
    assert page.status_code == 200 and 'method="post"' in page.text
    assert _row(web, "SELECT confirmed FROM accounts") == [
        (None,)
    ]  # a mail scanner opening it confirms nothing
    done = web.post(f"/account/confirm/{value}", follow_redirects=False)
    assert done.headers["location"] == f"/p/{owned}/settings?saved=confirmed#login"
    assert _row(web, "SELECT confirmed FROM accounts")[0][0] is not None
    assert web.post(f"/account/confirm/{value}").status_code == 410
    web.post(f"/p/{owned}/account/confirm")  # confirmed already: a new link is still sent on request
    clock.now += accounts.CONFIRM_LIFE + timedelta(hours=1)
    assert web.get(f"/account/confirm/{_link(mails[-1], 'confirm')}").status_code == 410


def test_a_reset_link_chooses_a_new_password_once_signs_out_everywhere_and_expires(web, owned, mails, clock):
    other = TestClient(web.app)
    asked = other.post("/account/forgot", data={"email": EMAIL})
    unknown = other.post("/account/forgot", data={"email": "nobody@example.org"})
    assert (
        asked.text.count("is on its way") == unknown.text.count("is on its way") == 1
    )  # says nothing of who exists
    assert [m.subject for m in mails].count("Choose a new password") == 1
    value = _link(mails[-1], "reset")
    weak = other.post(f"/account/reset/{value}", data={"password": "short"})
    assert weak.status_code == 400 and "at least 12" in weak.text
    done = other.post(
        f"/account/reset/{value}", data={"password": "brand new long secret"}, follow_redirects=False
    )
    assert done.status_code == 303 and other.get(f"/p/{owned}").status_code == 200
    assert web.get(f"/p/{owned}", follow_redirects=False).status_code == 303  # signed out there
    assert other.post(f"/account/reset/{value}", data={"password": "another long secret"}).status_code == 400
    assert _row(web, "SELECT confirmed FROM accounts")[0][0] is not None  # the link reached the address
    other.post("/account/forgot", data={"email": EMAIL})
    clock.now += accounts.RESET_LIFE + timedelta(minutes=1)
    assert other.get(f"/account/reset/{_link(mails[-1], 'reset')}").status_code == 410


def test_without_a_mail_service_nothing_is_promised_and_the_owner_makes_the_link(tmp_path, clock):
    with TestClient(_app(tmp_path, clock, [], mailer=None)) as client:
        assert _signup(client).headers["location"] == "/?made=2#start"
        assert "confirm your address later" in client.get("/?made=2").text
        token = _start(client)
        forgot = TestClient(client.app).post("/account/forgot", data={"email": EMAIL})
        assert forgot.status_code == 503 and "cannot send e-mail yet" in forgot.text
        inbox = client.post(f"/admin/{ADMIN}/reset", data={"email": EMAIL})
        value = re.search(r"/account/reset/([A-Za-z0-9_-]+)", inbox.text).group(1)
        fresh = TestClient(client.app)
        fresh.post(f"/account/reset/{value}", data={"password": "the owner sent this one"})
        assert fresh.get(f"/p/{token}").status_code == 200
        assert client.post("/admin/wrong-key/reset", data={"email": EMAIL}).status_code == 404
        assert "This server sends no e-mail yet." in client.get("/privacy").text


def test_the_demo_shows_the_mail_it_cannot_send(tmp_path, clock):
    with TestClient(_app(tmp_path, clock, [], mailer=None, show_mail=True)) as client:
        client.store = Store(tmp_path)
        _signup(client)
        token = _start(client)
        shown = client.post(f"/p/{token}/account/confirm")
        assert "This demo sends no e-mail" in shown.text
        link = re.search(r'href="(http://testserver/account/confirm/[^"]+)"', shown.text).group(1)
        client.post(link.removeprefix("http://testserver"))
        assert _row(client, "SELECT confirmed FROM accounts")[0][0] is not None
        forgot = TestClient(client.app).post("/account/forgot", data={"email": EMAIL})
        assert "/account/reset/" in forgot.text


# --------------------------------------------------------------------------- #
# Ways in from before, deletion, the copy, what the pages say
# --------------------------------------------------------------------------- #


def test_a_recovery_code_opens_the_account_too(web, owned):
    code = re.search(r'class="code"[^>]*>([^<]+)<', web.post(f"/p/{owned}/recovery").text).group(1)
    other = TestClient(web.app)
    answer = other.post("/recover", data={"code": code})
    assert answer.status_code == 200 and "cps_session=" in answer.headers["set-cookie"]
    assert other.get(f"/p/{owned}").status_code == 200


def test_deleting_the_plan_deletes_the_account_and_signs_out(web, owned):
    answer = web.post(f"/p/{owned}/delete")
    assert "Your account, your plan" in answer.text and 'cps_session=""' in answer.headers["set-cookie"]
    for table in ("accounts", "sessions", "mail_links"):
        assert _row(web, f"SELECT COUNT(*) FROM {table}") == [(0,)], table
    again = TestClient(web.app).post("/account/signin", data={"email": EMAIL, "password": PASSWORD})
    assert again.status_code == 400


def test_an_account_that_never_made_a_plan_goes_after_30_days(web, clock):
    _signup(web)
    service.sweep_subscriptions(web.store.path, clock.now + accounts.UNFINISHED_LIFE - timedelta(days=1))
    assert _row(web, "SELECT COUNT(*) FROM accounts") == [(1,)]
    service.sweep_subscriptions(web.store.path, clock.now + accounts.UNFINISHED_LIFE + timedelta(days=1))
    assert _row(web, "SELECT COUNT(*) FROM accounts") == [(0,)] == _row(web, "SELECT COUNT(*) FROM sessions")


def test_the_copy_has_the_address_and_the_devices_but_no_secret(web, owned):
    copy = web.get(f"/p/{owned}/export").json()
    assert copy["account"] == [
        {"email": EMAIL, "created": MORNING.isoformat(timespec="seconds"), "confirmed": None}
    ]
    assert len(copy["signed_in"]) == 1
    assert "scrypt$" not in web.get(f"/p/{owned}/export").text


def test_the_pages_say_what_an_account_keeps(web, tmp_path, clock):
    privacy = web.get("/privacy").text
    assert "cps_session" in privacy and "scrypt" in privacy and "no cookies" not in privacy
    assert "one cookie to keep you signed in" in web.get("/").text
    with TestClient(
        create_app(Config(store=tmp_path / "off", clock=clock, background=False, sweep_every=None))
    ) as off:
        assert "no e-mail, no password, no cookies" in off.get("/").text  # accounts off: as before (D35)
        assert 'action="/start"' in off.get("/").text


def test_a_plan_of_its_address_alone_still_opens_by_it(tmp_path, clock, mails):
    """A plan made before accounts, or a demo student (D31), belongs to no account and
    keeps opening by its address, as D35 had it."""
    store = Store(tmp_path)
    subscription = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    service.save_subscription(store.path, subscription)
    with TestClient(_app(tmp_path, clock, mails)) as client:
        assert client.get(f"/p/{subscription.token}").status_code == 200


def test_signing_in_ends_the_session_the_browser_had(web, owned):
    first = web.cookies.get("cps_session")
    web.post("/account/signin", data={"email": EMAIL, "password": PASSWORD})
    assert web.cookies.get("cps_session") != first
    assert _row(web, "SELECT COUNT(*) FROM sessions") == [(1,)]


def test_a_class_behind_one_address_can_sign_up_together(tmp_path, clock, mails):
    """A cohort on one university network reaches the server from one address (D94)."""
    with TestClient(_app(tmp_path, clock, mails)) as client:
        for n in range(30):
            student = TestClient(client.app)
            assert _signup(student, email=f"student{n}@example.org").status_code == 303, n
            _start(student)
