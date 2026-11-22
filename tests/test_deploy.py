"""D75, the pilot on a server of its own: the files that run it stay in step with the
app (its settings, its port, where it keeps its data) and keep their safeguards.
CI builds the image and checks /health; these read the files only."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
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


# D181: every setting the app reads has a place on the server, or a reason to have none.
LEFT_OUT = {
    "CPS_DB": "the image's command names the database (--db /data/cps.sqlite)",
    "CPS_FEED_DIR": "`cps serve`'s, the Streamlit page's feeds; the hosted app serves its own",
    "CPS_FEED_URL": "`cps serve`'s, as above",
    "CPS_ALLOW_PRIVATE_LINKS": "never on a server: it lets a link reach the server's own network",
    "CPS_AI_MODEL": "the models in ai.py are the ones priced; another is the owner's choice (docs/DEPLOY.md)",
    "CPS_AI_COACH_MODEL": "as above",
    "CPS_AI_CARDS_MODEL": "as above",
    "CPS_VAPID_PRIVATE_KEY": "made once and kept in the store (D38); given only to move reminders elsewhere",
}


def test_every_setting_the_app_reads_has_a_place_on_the_server():
    # AUDIT 123: CPS_SEAL_KEY (D175) was read by the app and named nowhere on the server.
    read = set(re.findall(r'"((?:CPS_[A-Z0-9_]+)|ANTHROPIC_API_KEY)"', SOURCE))
    here = _settings(_read(HETZNER / "env.example")) | set(
        re.findall(r"\b(CPS_[A-Z_]+):", _read(HETZNER / "compose.yaml"))
    )
    assert "CPS_SEAL_KEY" in here
    assert read - here - set(LEFT_OUT) == set(), read - here - set(LEFT_OUT)
    assert set(LEFT_OUT) <= read, set(LEFT_OUT) - read  # no reason kept for a setting gone


@pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash") or not shutil.which("openssl"),
    reason="the server's script: bash and openssl, as on Ubuntu",
)
def test_the_settings_script_asks_three_things_and_makes_the_keys(tmp_path):
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    script = HETZNER / "new-env.sh"
    assert os.access(script, os.X_OK)
    here = tmp_path / "hetzner"
    here.mkdir()
    shutil.copy(script, here / "new-env.sh")
    shutil.copy(HETZNER / "env.example", here / "env.example")
    answers = '  https://Plan.Example.EU/  \n\nme+pilot@example.org\nAnna D\'Angelo "Nana" $HOME\n'
    done = subprocess.run(
        ["bash", str(here / "new-env.sh")], input=answers, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    written = here / ".env"
    assert written.stat().st_mode & 0o077 == 0  # the keys: readable by the owner alone
    values = dict(line.split("=", 1) for line in _read(written).splitlines() if re.match(r"[A-Z]", line))
    assert values["CPS_DOMAIN"] == "plan.example.eu"  # the address pasted whole, made a name
    assert values["CPS_CONTACT"] == '"me+pilot@example.org"'
    assert values["CPS_CONTROLLER"] == '"Anna D\'Angelo \\"Nana\\" $$HOME"'  # compose reads it as typed
    assert len(values["CPS_ADMIN_TOKEN"]) >= 24 and values["CPS_ADMIN_TOKEN"].isalnum()
    Fernet(values["CPS_SEAL_KEY"].encode("ascii"))  # a key the app can seal with
    assert values["CPS_ADMIN_TOKEN"] in done.stdout and values["CPS_SEAL_KEY"] in done.stdout
    # Every other line is the example's.
    example = _read(HETZNER / "env.example").splitlines()
    mine = _read(written).splitlines()
    assert len(mine) == len(example)
    changed = {"CPS_DOMAIN", "CPS_CONTACT", "CPS_CONTROLLER", "CPS_ADMIN_TOKEN", "CPS_SEAL_KEY"}
    assert [a for a, b in zip(example, mine, strict=True) if a != b and a.split("=")[0] not in changed] == []
    # Run again, it keeps the keys in use.
    again = subprocess.run(
        ["bash", str(here / "new-env.sh")], input="a.b\nx@y\nz\n", capture_output=True, text=True, check=False
    )
    assert again.returncode == 1 and "exists already" in again.stderr
    assert _read(written).splitlines() == mine
