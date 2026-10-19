"""D75, the pilot on a server of its own: the files that run it stay in step with the
app (its settings, its port, where it keeps its data) and keep their safeguards.
CI builds the image and checks /health; these read the files only."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HETZNER = ROOT / "deploy" / "hetzner"
SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "cps").rglob("*.py"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _settings(text: str) -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]+)=", text, re.M))


def _service(compose: str, name: str) -> str:
    """One service's block of compose.yaml, by its indentation."""
    match = re.search(rf"^  {name}:\n((?:    .*\n|\n)+)", compose, re.M)
    assert match, name
    return match[1]


def test_every_setting_of_the_old_host_has_a_place_on_the_new_one():
    # render.yaml is the pilot's other way to run (D72): whatever it sets, the server
    # sets too, in env.example or in compose.yaml.
    render = set(re.findall(r"- key: ([A-Z0-9_]+)", _read(ROOT / "render.yaml"))) - {"PYTHON_VERSION"}
    here = _settings(_read(HETZNER / "env.example")) | set(
        re.findall(r"\b(CPS_[A-Z_]+):", _read(HETZNER / "compose.yaml"))
    )
    assert render <= here, render - here


def test_every_setting_in_the_example_is_one_the_app_reads():
    # CPS_DOMAIN is Caddy's and compose's, which make CPS_BASE_URL of it.
    settings = _settings(_read(HETZNER / "env.example")) - {"CPS_DOMAIN"}
    assert settings
    for name in settings:
        assert f'"{name}"' in SOURCE, f"{name} is in env.example but the app never reads it"


def test_the_image_runs_the_app_behind_the_proxy_as_its_own_user():
    dockerfile = _read(ROOT / "Dockerfile")
    assert re.search(r"^USER (?!root)\w+", dockerfile, re.M)
    command = re.search(r"^CMD (\[.*\])", dockerfile, re.M)
    assert command and "--behind-proxy" in command[1]
    assert '"--db", "/data/cps.sqlite"' in command[1] and '"--port", "8000"' in command[1]
    assert re.search(r"^EXPOSE 8000$", dockerfile, re.M)
    assert '.[web,ai]"' in dockerfile


def test_only_what_the_image_needs_goes_into_it():
    ignored = _read(ROOT / ".dockerignore").splitlines()
    assert ignored[ignored.index("*")] == "*"  # everything out, then back in by name
    allowed = {line[1:] for line in ignored if line.startswith("!")}
    assert allowed == {"pyproject.toml", "src/"}


def test_only_caddy_faces_the_internet_and_the_data_has_a_volume():
    compose = _read(HETZNER / "compose.yaml")
    app, caddy = _service(compose, "app"), _service(compose, "caddy")
    assert "ports:" not in app and '- "8000"' in app
    assert '- "80:80"' in caddy and '- "443:443"' in caddy
    assert "- data:/data" in app
    assert "CPS_BACKUP_DIR: /data/backups" in app  # the backups live in the volume too
    assert "CPS_BASE_URL: https://${CPS_DOMAIN" in app


def test_caddy_passes_requests_to_the_app_and_keeps_no_access_log():
    caddyfile = _read(HETZNER / "Caddyfile")
    assert "reverse_proxy app:8000" in caddyfile
    assert "{$CPS_DOMAIN}" in caddyfile
    directives = [
        line.strip().split()[0]
        for line in caddyfile.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "log" not in directives  # a plan's address would be in every line


def test_the_server_signs_in_with_keys_only_and_opens_three_ports():
    config = _read(HETZNER / "cloud-init.yaml")
    assert config.startswith("#cloud-config\n")
    for line in ("PasswordAuthentication no", "KbdInteractiveAuthentication no", "PermitRootLogin no"):
        assert line in config
    opened = re.findall(r"^  - ufw allow (\S+)$", config, re.M)
    assert opened == ["OpenSSH", "80/tcp", "443/tcp", "443/udp"]
    assert "ufw default deny incoming" in config


def test_a_forgotten_key_does_not_lock_the_owner_out():
    config = _read(HETZNER / "cloud-init.yaml")
    placeholder = re.search(r"ssh-ed25519 (\S+)", config)
    assert placeholder and placeholder[1] == "PASTE-YOUR-PUBLIC-KEY-HERE"
    guard = config.index("if grep -q PASTE-YOUR-PUBLIC-KEY")
    assert guard < config.index("systemctl restart ssh")  # checked before sshd reads the rules
    assert "rm /etc/ssh/sshd_config.d/10-study-plan.conf" in config[guard:]


def test_the_servers_settings_are_never_committed():
    assert "deploy/hetzner/.env" in _read(ROOT / ".gitignore").splitlines()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no execute bit")
def test_the_deploy_script_runs_whole_and_waits_for_health():
    script = HETZNER / "deploy.sh"
    assert os.access(script, os.X_OK)
    text = _read(script)
    assert text.rstrip().endswith('main "$@"')  # read whole before git reset replaces it
    assert "--wait" in text and "set -euo pipefail" in text


def test_the_deploy_workflow_does_nothing_until_it_is_configured():
    workflow = _read(ROOT / ".github" / "workflows" / "deploy.yml")
    assert "vars.DEPLOY_HOST != ''" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "permissions: {}" in workflow
