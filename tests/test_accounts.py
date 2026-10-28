"""
Accounts that last (DECISIONS.md D35, D36): passkeys, the recovery code, the plan
kept 12 months after the last visit, and the next semester at the same address.

The passkey device is simulated (`conftest.PasskeyDevice`): real key pairs and real
CBOR, as a phone writes them, with each field spoilable. The browser run (AUDIT
item 53) checked the same flows with Chromium's own virtual authenticator.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from cps import accounts, passkeys, service
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)  # a Monday, 08:00 in Paris
ORIGIN, RP = "http://testserver", "testserver"
CODE = re.compile(r"[0-9A-HJKMNP-TV-Z]{5}(-[0-9A-HJKMNP-TV-Z]{5}){3}")


def _plan(where, now: datetime = NOW, name: str = "sample-timetable.ics") -> service.Subscription:
    sub = service.start_subscription(tz="Europe/Paris", ics=(EXAMPLES / name).read_bytes(), now=now)
    service.save_subscription(where, sub)
    return sub


def _options(token: str) -> dict:
    return {"challenge": passkeys.b64url(b"c" * 32), "rp": {"id": RP}, "user": {"id": "dQ"}, "rpId": RP}


def _added(where, sub, device, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)") -> str:
    options = service.passkey_options(where, sub, rp_id=RP, now=NOW)
    answer = device.create(options)
    service.add_passkey(where, sub.token, answer, origin=ORIGIN, rp_id=RP, user_agent=user_agent, now=NOW)
    return answer["id"]


# --------------------------------------------------------------------------- #
# The signature checks (passkeys.py)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("alg", [passkeys.ES256, passkeys.EDDSA, passkeys.RS256])
def test_each_algorithm_registers_then_signs_in(device, alg):
    device.alg = alg
    options = _options("t")
    challenge = passkeys.unb64url(options["challenge"])
    made = device.create(options)
    kept = passkeys.verify_registration(
        attestation_object=made["response"]["attestationObject"],
        client_data=made["response"]["clientDataJSON"],
        challenge=challenge,
        origin=ORIGIN,
        rp_id=RP,
    )
    assert kept.id == made["id"] and kept.alg == alg and kept.verified
    got = device.get(options)["response"]
    count = passkeys.verify_assertion(
        authenticator_data=got["authenticatorData"],
        client_data=got["clientDataJSON"],
        signature=got["signature"],
        challenge=challenge,
        origin=ORIGIN,
        rp_id=RP,
        public_key=kept.public_key,
        alg=kept.alg,
        sign_count=kept.sign_count,
    )
    assert count == 1


def _register(made: dict, challenge: bytes, origin: str = ORIGIN, rp_id: str = RP) -> passkeys.Credential:
    return passkeys.verify_registration(
        attestation_object=made["response"]["attestationObject"],
        client_data=made["response"]["clientDataJSON"],
        challenge=challenge,
        origin=origin,
        rp_id=rp_id,
    )


def test_a_passkey_made_elsewhere_or_without_the_person_is_refused(device):
    options = _options("t")
    challenge = passkeys.unb64url(options["challenge"])
    spoiled = {
        "on another site's page": device.create(options, origin="https://evil.example"),
        "for another site": device.create(options, rp_id="evil.example"),
        "as a sign-in": device.create(options, kind="webauthn.get"),
        "for another challenge": device.create(options, challenge=passkeys.b64url(b"d" * 32)),
        "nobody present": device.create(options, flags=0x40),
    }
    for why, made in spoiled.items():
        with pytest.raises(passkeys.PasskeyError):
            _register(made, challenge)
            pytest.fail(why)
    assert _register(device.create(options), challenge).id  # the same device, done right


def test_a_spoiled_signature_or_a_counter_going_back_is_refused(device):
    options = _options("t")
    challenge = passkeys.unb64url(options["challenge"])
    kept = _register(device.create(options), challenge)

    def check(got: dict, count: int = 0) -> int:
        r = got["response"]
        return passkeys.verify_assertion(
            authenticator_data=r["authenticatorData"],
            client_data=r["clientDataJSON"],
            signature=r["signature"],
            challenge=challenge,
            origin=ORIGIN,
            rp_id=RP,
            public_key=kept.public_key,
            alg=kept.alg,
            sign_count=count,
        )

    with pytest.raises(passkeys.PasskeyError, match="signature"):
        check(device.get(options, spoil=True))
    with pytest.raises(passkeys.PasskeyError, match="another site"):
        check(device.get(options, rp_id="evil.example"))
    with pytest.raises(passkeys.PasskeyError, match="present"):
        check(device.get(options, flags=0x00))
    assert check(device.get(options), count=3) == 4  # its fourth signature
    with pytest.raises(passkeys.PasskeyError, match="backwards"):
        check(device.get(options, step=0), count=7)
    device.count = 0
    assert check(device.get(options, step=0), count=0) == 0  # synced passkeys keep zero


def test_the_cbor_reader_refuses_what_webauthn_does_not_write():
    assert passkeys.cbor_decode(bytes.fromhex("a201020326")) == {1: 2, 3: -7}
    for broken in ("a2010203", "5f", "1a0000", "a10102ff", "f9"):
        with pytest.raises(passkeys.PasskeyError):
            passkeys.cbor_decode(bytes.fromhex(broken))


# --------------------------------------------------------------------------- #
# A plan's passkeys and its recovery code (accounts.py, service)
# --------------------------------------------------------------------------- #


def test_a_passkey_opens_its_plan(tmp_path, device):
    sub = _plan(tmp_path)
    _added(tmp_path, sub, device)
    view = service.account_view(tmp_path, sub, NOW)
    assert view["safe"] and not view["nudge"]
    assert [k["device"] for k in view["passkeys"]] == ["iPhone"]
    options = service.signin_options(tmp_path, rp_id=RP, now=NOW)
    assert options["allowCredentials"] == []  # the device offers its own; nobody learns which exist
    token = service.sign_in_with_passkey(tmp_path, device.get(options), origin=ORIGIN, rp_id=RP, now=NOW)
    assert token == sub.token
    assert service.account_view(tmp_path, sub, NOW)["passkeys"][0]["last"] == "2 March"


def test_the_options_name_no_secret_and_exclude_the_plans_passkeys(tmp_path, device):
    sub = _plan(tmp_path)
    first = _added(tmp_path, sub, device)
    options = service.passkey_options(tmp_path, sub, rp_id=RP, now=NOW)
    assert sub.token not in str(options)
    assert options["user"]["name"] == "Study plan · March 2026"
    assert options["excludeCredentials"] == [{"type": "public-key", "id": first}]
    assert options["attestation"] == "none" and options["authenticatorSelection"]["residentKey"] == "required"
    service.save_profile(tmp_path, sub.token, {"handle": "ada", "university": "Lyon 1", "old_enough": "1"})
    assert service.passkey_options(tmp_path, sub, rp_id=RP, now=NOW)["user"]["name"] == "@ada"


def test_a_challenge_answers_once_for_its_own_purpose_within_five_minutes(tmp_path, device):
    sub = _plan(tmp_path)
    _added(tmp_path, sub, device)
    options = service.signin_options(tmp_path, rp_id=RP, now=NOW)
    answer = device.get(options)
    assert service.sign_in_with_passkey(tmp_path, answer, origin=ORIGIN, rp_id=RP, now=NOW) == sub.token
    with pytest.raises(service.SignInFailed):  # replayed
        service.sign_in_with_passkey(tmp_path, answer, origin=ORIGIN, rp_id=RP, now=NOW)
    late = service.signin_options(tmp_path, rp_id=RP, now=NOW)
    with pytest.raises(service.SignInFailed):
        service.sign_in_with_passkey(
            tmp_path, device.get(late), origin=ORIGIN, rp_id=RP, now=NOW + timedelta(minutes=6)
        )
    signin = service.signin_options(tmp_path, rp_id=RP, now=NOW)
    made = device.create(
        {**service.passkey_options(tmp_path, sub, rp_id=RP, now=NOW), "challenge": signin["challenge"]}
    )
    with pytest.raises(service.SignInFailed):  # a sign-in's challenge does not register a passkey
        service.add_passkey(tmp_path, sub.token, made, origin=ORIGIN, rp_id=RP, now=NOW)
    with pytest.raises(service.SignInFailed):  # the wrong page
        service.sign_in_with_passkey(
            tmp_path,
            device.get(service.signin_options(tmp_path, rp_id=RP, now=NOW)),
            origin="https://evil.example",
            rp_id=RP,
            now=NOW,
        )


def test_a_passkey_cannot_open_another_plan(tmp_path, device):
    ada, bob = _plan(tmp_path), _plan(tmp_path)
    _added(tmp_path, ada, device)
    options = service.signin_options(tmp_path, rp_id=RP, now=NOW)
    bobs = passkeys.b64url(accounts.user_id(bob.token))
    with pytest.raises(service.SignInFailed):
        service.sign_in_with_passkey(
            tmp_path, device.get(options, handle=bobs), origin=ORIGIN, rp_id=RP, now=NOW
        )
    # The same credential offered to a second plan is refused, not moved.
    made = device.create(service.passkey_options(tmp_path, bob, rp_id=RP, now=NOW))
    service.add_passkey(tmp_path, bob.token, made, origin=ORIGIN, rp_id=RP, now=NOW)
    again = device.create(
        service.passkey_options(tmp_path, ada, rp_id=RP, now=NOW), credential=passkeys.unb64url(made["id"])
    )
    with pytest.raises(service.SignInFailed):
        service.add_passkey(tmp_path, ada.token, again, origin=ORIGIN, rp_id=RP, now=NOW)


def test_a_deleted_plan_takes_its_passkeys_and_its_code(tmp_path, device):
    sub = _plan(tmp_path)
    _added(tmp_path, sub, device)
    code = service.make_recovery_code(tmp_path, sub.token, NOW)
    service.delete_subscription(tmp_path, sub.token)
    with Store(tmp_path).connection() as db:
        assert db.execute("SELECT COUNT(*) FROM passkeys").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM recovery_codes").fetchone()[0] == 0
    options = service.signin_options(tmp_path, rp_id=RP, now=NOW)
    with pytest.raises(service.SignInFailed, match="no longer exists"):
        service.sign_in_with_passkey(tmp_path, device.get(options), origin=ORIGIN, rp_id=RP, now=NOW)
    with pytest.raises(service.SignInFailed):
        service.recover(tmp_path, code, NOW)


def test_a_recovery_code_works_once_and_is_replaced(tmp_path):
    sub = _plan(tmp_path)
    assert service.account_view(tmp_path, sub, NOW)["nudge"]
    code = service.make_recovery_code(tmp_path, sub.token, NOW)
    assert CODE.fullmatch(code)
    assert service.account_view(tmp_path, sub, NOW)["recovery"] == "2 March"
    with Store(tmp_path).connection() as db:
        (digest,) = db.execute("SELECT digest FROM recovery_codes").fetchone()
    assert code.replace("-", "") not in digest  # kept as a digest only
    typed = " ".join(code.lower().replace("-", "")[i : i + 4] for i in range(0, 20, 4)).replace("0", "o")
    token, fresh = service.recover(tmp_path, typed, NOW)  # as copied by hand
    assert token == sub.token and CODE.fullmatch(fresh) and fresh != code
    with pytest.raises(service.SignInFailed):
        service.recover(tmp_path, code, NOW)  # used
    newer = service.make_recovery_code(tmp_path, sub.token, NOW)
    with pytest.raises(service.SignInFailed):
        service.recover(tmp_path, fresh, NOW)  # replaced
    assert service.recover(tmp_path, newer, NOW)[0] == sub.token
    for wrong in ("", "ABCDE", "x" * 400):
        with pytest.raises(service.SignInFailed):
            service.recover(tmp_path, wrong, NOW)


def test_not_now_hides_the_suggestion_for_two_weeks(tmp_path):
    sub = service.account_later(_plan(tmp_path), NOW)
    assert not service.account_view(tmp_path, sub, NOW + timedelta(days=13))["nudge"]
    assert service.account_view(tmp_path, sub, NOW + timedelta(days=14))["nudge"]


# --------------------------------------------------------------------------- #
# Kept 12 months after the last visit (D36)
# --------------------------------------------------------------------------- #


def test_a_visit_moves_the_deletion_a_sign_in_too(tmp_path, device):
    sub = _plan(tmp_path)
    assert service.account_view(tmp_path, sub, NOW)["deleted"] == "2 March 2027"
    service.mark_seen(tmp_path, sub.token, NOW + timedelta(days=100))
    assert service.subscription_expiry(service.load_subscription(tmp_path, sub.token)) == NOW + timedelta(
        days=465
    )
    service.mark_seen(tmp_path, sub.token, NOW + timedelta(days=50))  # never earlier
    assert service.load_subscription(tmp_path, sub.token).seen == (NOW + timedelta(days=100)).isoformat()
    _added(tmp_path, sub, device)
    options = service.signin_options(tmp_path, rp_id=RP, now=NOW + timedelta(days=200))
    service.sign_in_with_passkey(
        tmp_path, device.get(options), origin=ORIGIN, rp_id=RP, now=NOW + timedelta(days=200)
    )
    assert service.load_subscription(tmp_path, sub.token).seen == (NOW + timedelta(days=200)).isoformat()


# --------------------------------------------------------------------------- #
# The next semester (D36)
# --------------------------------------------------------------------------- #

SPRING_END = datetime(2026, 9, 28, 7, 0, tzinfo=UTC)  # a Monday: the autumn semester starts


def _spring(tmp_path) -> service.Subscription:
    """A spring semester with two sessions reported, a profile, a card, a drawn
    week and three deadlines: one done, one past, one in the autumn."""
    sub = _plan(tmp_path)
    plan = service.PlanReport.from_dict(sub.plan)
    first, second = plan.sessions[0], plan.sessions[1]
    sub = service.report_session(
        sub, service.session_id(first), "done", now=datetime.fromisoformat(second.end)
    )
    sub = service.report_session(
        sub, service.session_id(second), "done", now=datetime.fromisoformat(second.end)
    )
    sub = service.revise_subscription(
        sub,
        busy_rows=[
            {"label": "Analysis", "kind": "class", "weekday": "Mon", "start": "09:00", "end": "11:00"},
            {"label": "Gym", "kind": "training", "weekday": "Wed", "start": "18:00", "end": "19:30"},
            {"label": "Dentist", "kind": "other", "date": "2026-03-10", "start": "14:00", "end": "15:00"},
            {"label": "Wedding", "kind": "timeoff", "date": "2026-10-17", "start": "10:00", "end": "23:00"},
        ],
        tasks=[
            service.TaskSpec("Lab report", "2026-03-12 18:00", 4, "Analysis", done=True),
            service.TaskSpec("Essay", "2026-04-15 12:00", 6, "Analysis"),
            service.TaskSpec("Thesis proposal", "2026-10-20 12:00", 8, ""),
        ],
        preferences={"weekly_hours": 9.0},
        now=datetime.fromisoformat(second.end),
        strict=False,
    )
    service.save_subscription(tmp_path, sub)
    service.save_profile(tmp_path, sub.token, {"handle": "ada", "university": "Lyon 1", "old_enough": "1"})
    service.add_cards(tmp_path, sub, {"course": "Analysis", "front": "e^0?", "back": "1"}, now=NOW)
    return service.load_subscription(tmp_path, sub.token)


def test_the_next_semester_keeps_the_address_and_what_was_done(tmp_path):
    spring = _spring(tmp_path)
    assert not service.semester_over(spring, NOW)
    assert not service.semester_over(spring, SPRING_END)  # the thesis proposal is still ahead
    assert service.semester_over(spring, datetime(2026, 10, 21, tzinfo=UTC))
    before = service.progress_view(spring, SPRING_END)
    autumn = service.start_next_semester(
        spring, ics=(EXAMPLES / "sample-semester.ics").read_bytes(), now=SPRING_END
    )
    assert autumn.token == spring.token and autumn.created == spring.created
    assert [s["name"] for s in autumn.subjects][:2] == ["Computer Programming 3", "Algebra 3"]
    assert autumn.outcomes == {} and "pins" not in autumn.options
    assert autumn.options["start"] == "2026-09-28"
    assert autumn.options["preferences"]["weekly_hours"] == 9.0  # the week's settings stay
    assert [t["name"] for t in autumn.options["tasks"]] == ["Thesis proposal"]
    assert [r["label"] for r in autumn.busy_rows] == ["Gym", "Wedding"]  # classes and past one-offs go
    past = autumn.options["past"]
    assert sum(p["report"] == "done" for p in past) == 2
    assert all(datetime.fromisoformat(p["start"]) <= SPRING_END for p in past)
    after = service.progress_view(autumn, SPRING_END)
    assert after["totals"]["sessions"] == before["totals"]["sessions"] == 2
    assert not service.semester_over(autumn, SPRING_END)
    # What is kept under the address does not move at all.
    service.save_subscription(tmp_path, autumn)
    assert service.profile_of(tmp_path, autumn.token)["handle"] == "ada"
    assert service.cards_due(tmp_path, autumn, SPRING_END) == 1
    # A third semester keeps the first one's sessions too.
    winter = service.start_next_semester(autumn, draw=True, now=datetime(2027, 2, 8, 7, tzinfo=UTC))
    assert winter.plan is None and winter.ics_text is None
    assert sum(p["report"] == "done" for p in winter.options["past"]) == 2
    assert len(winter.options["past"]) > len(past)
    assert len(service.progress_sessions(winter)) == len(winter.options["past"])


def test_the_next_semester_asks_for_a_timetable_or_the_week_drawn(tmp_path):
    spring = _spring(tmp_path)
    with pytest.raises(service.InvalidInput, match="link or its file"):
        service.start_next_semester(spring, now=SPRING_END)


def test_a_plan_never_planned_is_not_over():
    blank = service.start_subscription(tz="Europe/Paris", draw=True, now=NOW)
    assert not service.semester_over(blank, NOW) and service.progress_sessions(blank) == []


def test_old_documents_load_and_count_from_their_making():
    blank = service.start_subscription(tz="Europe/Paris", draw=True, now=NOW)
    data = blank.to_dict()
    del data["seen"]
    old = service.Subscription.from_dict(data)
    assert old.seen is None and service.subscription_expiry(old) == NOW + service.RETENTION
    assert service.subscription_expiry(
        replace(old, seen=(NOW + timedelta(days=3)).isoformat())
    ) == NOW + timedelta(days=368)


# --------------------------------------------------------------------------- #
# The pages (cps.web)
# --------------------------------------------------------------------------- #

MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/605.1.15 Safari/605.1.15"


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config), headers={"user-agent": MAC}) as client:
        client.store_path = tmp_path
        yield client


@pytest.fixture
def token(web) -> str:
    files = {"file": ("timetable.ics", (EXAMPLES / "sample-timetable.ics").read_bytes(), "text/calendar")}
    answer = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return answer.headers["location"].split("/")[2]


def test_a_passkey_added_in_settings_opens_the_plan_from_the_home_page(web, token, device):
    settings = web.get(f"/p/{token}/settings").text
    assert 'id="account"' in settings and "data-passkey-add" in settings and "/static/passkeys.js" in settings
    options = web.post(f"/p/{token}/passkeys/options").json()
    assert options["rp"]["id"] == "testserver"
    answer = web.post(f"/p/{token}/passkeys", json=device.create(options))
    assert answer.status_code == 200 and answer.json()["go"] == f"/p/{token}/settings?saved=passkey#account"
    settings = web.get(f"/p/{token}/settings?saved=passkey").text
    assert "<b>Mac</b>" in settings and "Passkey added." in settings and "Add another passkey" in settings
    home = web.get("/").text
    assert "data-passkey-signin" in home and 'action="/recover"' in home
    signin = web.post("/signin/options").json()
    assert web.post("/signin", json=device.get(signin)).json() == {"go": f"/p/{token}/open"}
    spoiled = web.post("/signin", json=device.get(web.post("/signin/options").json(), spoil=True))
    assert spoiled.status_code == 400 and spoiled.json()["message"] == "That did not work. Try again."
    assert web.post("/signin", content=b"not json").status_code == 400
    (kept,) = service.account_view(web.store_path, service.load_subscription(web.store_path, token), NOW)[
        "passkeys"
    ]
    removed = web.post(f"/p/{token}/passkeys/{kept['id']}/remove", follow_redirects=False)
    assert removed.status_code == 303 and removed.headers["location"].endswith(
        "saved=passkey_removed#account"
    )
    gone = web.post("/signin", json=device.get(web.post("/signin/options").json()))
    assert gone.json()["message"] == "This passkey belongs to a plan that no longer exists."


def test_a_refused_passkey_says_so_without_the_reason(web, token, device):
    options = web.post(f"/p/{token}/passkeys/options").json()
    refused = web.post(f"/p/{token}/passkeys", json=device.create(options, origin="https://evil.example"))
    assert refused.status_code == 400
    assert refused.json()["message"].startswith("That passkey could not be added.")
    assert "evil" not in refused.text
    assert web.post("/p/" + "x" * 32 + "/passkeys/options").status_code == 404


def test_a_recovery_code_is_shown_once_and_opens_the_plan(web, token):
    made = web.post(f"/p/{token}/recovery")
    assert made.status_code == 200 and made.headers["cache-control"] == "no-store"
    code = CODE.search(made.text).group(0)
    assert "Recovery code made on 2 March" in web.get(f"/p/{token}/settings").text
    assert code not in web.get(f"/p/{token}/settings").text
    opened = web.post("/recover", data={"code": code.lower()})
    assert opened.status_code == 200 and "Welcome back" in opened.text and f'href="/p/{token}"' in opened.text
    fresh = CODE.search(opened.text).group(0)
    assert fresh != code
    again = web.post("/recover", data={"code": code})
    assert again.status_code == 400 and "No plan has this recovery code" in again.text
    assert web.get("/recover").status_code == 405  # never a page to keep
    assert web.post("/recover", data={"code": fresh}).status_code == 200


def test_today_suggests_keeping_the_plan_until_it_is_safe(web, token):
    form = {"s0_exam": "2026-03-20T09:00", "s0_name": "Analysis", "s0_familiarity": "3"}
    web.post(f"/p/{token}/settings", data=form)
    assert "Keep your plan" in web.get(f"/p/{token}").text
    assert web.post(f"/p/{token}/account/later", follow_redirects=False).status_code == 303
    assert "Keep your plan" not in web.get(f"/p/{token}").text
    with Store(web.store_path).connection() as db:
        db.execute("UPDATE subscriptions SET data = json_remove(data, '$.options.account_later')")
    assert "Keep your plan" in web.get(f"/p/{token}").text
    web.post(f"/p/{token}/recovery")
    assert "Keep your plan" not in web.get(f"/p/{token}").text


def test_the_next_semester_page_starts_it_at_the_same_address(web, token):
    page = web.get(f"/p/{token}/semester").text
    assert f'action="/p/{token}/semester"' in page and 'name="draw"' in page
    assert "Start next semester" in web.get(f"/p/{token}/settings").text
    empty = web.post(f"/p/{token}/semester", data={})
    assert empty.status_code == 400 and "link, or choose its file" in empty.text
    files = {"file": ("autumn.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    moved = web.post(f"/p/{token}/semester", files=files, follow_redirects=False)
    assert moved.status_code == 303 and moved.headers["location"] == f"/p/{token}/settings?new=1"
    autumn = service.load_subscription(web.store_path, token)
    assert autumn.subjects[0]["name"] == "Computer Programming 3"
    drawn = web.post(f"/p/{token}/semester", data={"draw": "1"}, follow_redirects=False)
    assert drawn.headers["location"] == f"/p/{token}/timetable"
    assert service.load_subscription(web.store_path, token).ics_text is None
    assert [e["detail"] for e in Store(web.store_path).events(token) if e["kind"] == "semester"] == [
        "file",
        "draw",
    ]


def test_the_privacy_page_and_the_lost_page_say_how_to_come_back(web):
    privacy = web.get("/privacy").text
    assert (
        "SHA-256" in privacy and "passkey" in privacy and "12 months after you last open your plan" in privacy
    )
    lost = web.get("/p/" + "x" * 32).text
    assert "12 months" in lost and "recovery code" in lost


# --------------------------------------------------------------------------- #
# Pinned to a real implementation (CLAUDE.md invariant 9, in spirit)
# --------------------------------------------------------------------------- #


def test_the_checker_accepts_what_chromium_wrote_and_nothing_else():
    """The simulated device above is this repository's own reading of the
    specification; this recording is Chromium's (tests/data/chromium-passkey.json)."""
    import json

    data = json.loads((Path(__file__).parent / "data" / "chromium-passkey.json").read_text(encoding="utf-8"))
    made, got = data["registration"]["answer"], data["sign_in"]["answer"]
    kept = passkeys.verify_registration(
        attestation_object=made["response"]["attestationObject"],
        client_data=made["response"]["clientDataJSON"],
        challenge=passkeys.unb64url(data["registration"]["challenge"]),
        origin=data["origin"],
        rp_id=data["rp_id"],
    )
    assert kept.id == made["id"] and kept.alg == passkeys.ES256 and kept.verified
    assert got["id"] == made["id"] and got["response"]["userHandle"] == data["user_id"]

    def sign_in(**change: str) -> int:
        r = {**got["response"], **change}
        return passkeys.verify_assertion(
            authenticator_data=r["authenticatorData"],
            client_data=r["clientDataJSON"],
            signature=r["signature"],
            challenge=passkeys.unb64url(data["sign_in"]["challenge"]),
            origin=data["origin"],
            rp_id=data["rp_id"],
            public_key=kept.public_key,
            alg=kept.alg,
            sign_count=kept.sign_count,
        )

    assert sign_in() >= 1
    raw = passkeys.unb64url(got["response"]["signature"])
    with pytest.raises(passkeys.PasskeyError):
        sign_in(signature=passkeys.b64url(raw[:-1] + bytes([raw[-1] ^ 1])))
    with pytest.raises(passkeys.PasskeyError):
        sign_in(clientDataJSON=made["response"]["clientDataJSON"])  # the registration's, replayed
