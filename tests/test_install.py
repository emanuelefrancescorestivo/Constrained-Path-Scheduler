"""
The app on a home screen (DECISIONS.md D33) and in Italian (D34): a manifest per
plan, so that the installed app opens that plan; the service worker served from
the site's root, with the files it keeps all present; the offline page; the
security policy that lets them load; a deleted plan's copies forgotten; and a plan
in Italian, from the start page to its settings.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps.web import Config, create_app

STATIC = Path(__file__).resolve().parent.parent / "src" / "cps" / "web" / "static"
NOW = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def _plan(web, **data) -> str:
    started = web.post("/start", data={"draw": "1", **data}, follow_redirects=False)
    assert started.status_code == 303, started.text
    return started.headers["location"].split("/")[2]


def test_the_manifest_opens_the_plan_it_was_installed_from(web):
    site = web.get("/manifest.webmanifest")
    assert site.status_code == 200 and site.headers["content-type"].startswith("application/manifest+json")
    body = site.json()
    assert body["start_url"] == "/" and body["display"] == "standalone" and body["name"] == "Study plan"
    for icon in body["icons"]:
        assert (STATIC / icon["src"].removeprefix("/static/")).exists(), icon["src"]
    assert {"192x192", "512x512"} <= {i["sizes"] for i in body["icons"]}
    assert any(i.get("purpose") == "maskable" for i in body["icons"])

    token = _plan(web, lang="it")
    own = web.get(f"/p/{token}/manifest.webmanifest").json()
    assert own["start_url"] == own["id"] == f"/p/{token}" and own["lang"] == "it"
    assert own["name"] == "Piano di studio"  # the plan's language
    assert web.get("/p/" + "x" * 32 + "/manifest.webmanifest").status_code == 404
    page = web.get(f"/p/{token}/timetable").text
    assert f'<link rel="manifest" href="/p/{token}/manifest.webmanifest">' in page
    assert 'rel="apple-touch-icon"' in page and 'src="/static/pwa.js"' in page


def test_the_service_worker_keeps_only_files_that_exist(web):
    worker = web.get("/sw.js")
    assert worker.status_code == 200 and "javascript" in worker.headers["content-type"]
    assert worker.headers["cache-control"] == "no-cache"  # a new version is picked up at once
    listed = re.search(r"SHELL_FILES = \[(.*?)\];", worker.text, re.S).group(1)
    assert "OFFLINE" in listed and "const OFFLINE = `/offline?lang=" in worker.text
    kept = json.loads("[" + listed.replace("OFFLINE,", "").strip().rstrip(",") + "]")
    for path in [*kept, "/offline?lang=it"]:
        assert web.get(path).status_code == 200, path
    policy = worker.headers["content-security-policy"]
    assert "worker-src 'self'" in policy and "manifest-src 'self'" in policy
    offline = unescape(web.get("/offline").text)
    assert "No network" in offline and "javascript:" not in offline
    assert "Nessuna rete" in unescape(web.get("/offline?lang=it").text)


def test_a_deleted_plan_tells_this_device_to_forget_its_copies(web):
    token = _plan(web)
    gone = web.post(f"/p/{token}/delete")
    assert gone.status_code == 200 and f'data-forget="{token}"' in gone.text


def test_a_plan_in_italian(web):
    home = unescape(web.get("/?lang=it").text)
    assert '<html lang="it"' in home and "Come vuoi cominciare?" in home
    assert "Disegna la mia settimana" in home
    assert "Français" in home and "English" in home  # the other languages, offered
    token = _plan(web, lang="it")
    drawing = unescape(web.get(f"/p/{token}/timetable").text)
    assert "Disegna la tua settimana" in drawing and "Avanti: esami e scadenze" in drawing
    settings = unescape(web.get(f"/p/{token}/settings").text)
    assert "Impostazioni" in settings and "Sul tuo telefono" in settings and "Italiano" in settings


def test_every_file_the_pages_use_ships_with_the_package():
    """A normal install (`pip install .`, as the host does) carries only the files
    package-data names; an editable one, as in these tests, has them all (AUDIT 51)."""
    import fnmatch
    import tomllib

    root = STATIC.parent
    project = tomllib.loads((root.parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = project["tool"]["setuptools"]["package-data"]["cps.web"]
    for folder in ("static", "templates"):
        for path in (root / folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                relative = path.relative_to(root).as_posix()
                # setuptools' globs, like fnmatch's here, let "*" cross no "/"
                assert any(
                    fnmatch.fnmatch(relative, p) and relative.count("/") == p.count("/") for p in patterns
                ), f"{relative} is not shipped: add a pattern to pyproject.toml"
