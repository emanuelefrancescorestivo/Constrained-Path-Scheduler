"""D198: the browser extension that pauses distracting sites during a focus session. The plan's
pages say until when a session runs; the extension reads that and nothing else."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cps import service

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
SEMESTER = ROOT / "examples" / "sample-semester.ics"
NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)


def _running(**focus) -> service.Subscription:
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW)
    return service.start_focus(sub, "Algebra 3", now=NOW, **focus)


def test_a_session_says_until_when_it_runs():
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW)
    assert service.focus_until(sub, NOW) == "" and service.focus_until(None) == ""
    # 45 minutes with the 25 + 5 rhythm: one pause, and a quarter of an hour more.
    timed = _running(goal=45, rhythm=True)
    assert service.focus_until(timed, NOW) == (NOW + timedelta(minutes=45 + 5 + 15)).isoformat()
    plain = _running(goal=45, rhythm=False)
    assert service.focus_until(plain, NOW) == (NOW + timedelta(minutes=60)).isoformat()
    # With no length, three hours; once past, nothing to block.
    open_ = _running(goal=0)
    assert service.focus_until(open_, NOW) == (NOW + service.FOCUS_BLOCK_OPEN).isoformat()
    assert service.focus_until(open_, NOW + timedelta(hours=4)) == ""


def test_the_extension_asks_for_the_sites_it_pauses_and_nothing_more():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {"declarativeNetRequest", "storage", "alarms"}
    hosts = manifest["host_permissions"]
    assert all(any(site in h for h in hosts) for site in service.LOCK_SITES)
    assert len(hosts) == len(service.LOCK_SITES) and not any("<all_urls>" in h for h in hosts)
    # The content script runs on a plan's pages only, and reads one attribute.
    matches = manifest["content_scripts"][0]["matches"]
    assert all(m.endswith("/p/*") for m in matches)
    content = (EXT / "content.js").read_text(encoding="utf-8")
    assert "dataset.focusUntil" in content and "innerText" not in content and "fetch(" not in content
    background = (EXT / "background.js").read_text(encoding="utf-8")
    sites = json.loads(re.search(r"const SITES = (\[.*?\]);", background).group(1))
    assert tuple(sites) == service.LOCK_SITES
    # Its words in the app's three languages, each with the same messages.
    keys = {
        lang.name: set(json.loads((lang / "messages.json").read_text(encoding="utf-8")))
        for lang in (EXT / "_locales").iterdir()
    }
    assert set(keys) == {"en", "fr", "it"} and len({frozenset(k) for k in keys.values()}) == 1
    for path in ("blocked.html", "blocked.js", "blocked.css", "icons/icon-192.png"):
        assert (EXT / path).exists(), path
    # No inline script on the calm page (an extension page's CSP refuses one).
    assert not re.search(r"<script>(?!</script>)", (EXT / "blocked.html").read_text(encoding="utf-8"))


def test_every_page_of_a_plan_says_until_when_a_session_runs(tmp_path):
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    class Clock:
        now = NOW

        def __call__(self):
            return self.now

    web = TestClient(
        create_app(
            Config(store=tmp_path, background=False, sweep_every=None, remind_every=None, clock=Clock())
        )
    )
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    token = (
        web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        .headers["location"]
        .split("/")[2]
    )
    assert 'data-focus-until=""' in web.get(f"/p/{token}").text
    service.update_subscription(
        tmp_path, token, lambda s: service.start_focus(s, "Algebra 3", now=NOW, goal=25)
    )
    until = service.focus_until(service.load_subscription(tmp_path, token), NOW)
    for path in ("", "/focus", "/week", "/courses"):
        assert f'data-focus-until="{until}"' in web.get(f"/p/{token}{path}").text, path
    assert "data-focus-until" not in web.get("/").text  # not on the front page
