"""Common ownership-checked Docker operations; never uses the global context."""
from __future__ import annotations

import json
import os
import subprocess

from bootstrap import LOCAL, ROOT, load_env


def docker_args() -> list[str]:
    marker = json.loads((LOCAL / "instance.json").read_text())
    env = load_env()
    if marker["root"] != str(ROOT) or marker["project"] != env["KAJH_PROJECT"]:
        raise RuntimeError("Runtime ownership mismatch")
    if not marker["project"].startswith("kajh-p1-"):
        raise RuntimeError("Invalid project prefix")
    return ["/usr/local/bin/docker", "--config", str(LOCAL / "docker-client"),
            "--host", "unix:///Users/kan/.docker/run/docker.sock"]


def command_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        env.pop(key, None)
    env.update(load_env())
    image_file = ROOT / "infrastructure/local/images.lock.json"
    if image_file.exists():
        images = json.loads(image_file.read_text())
        for name, data in images.items():
            env[f"KAJH_{name.upper()}_IMAGE"] = data["reference"]
    return env


def docker(*args: str, check: bool = True, **kwargs):
    return subprocess.run(docker_args() + list(args), env=command_env(), check=check, **kwargs)


def compose(*args: str, check: bool = True, **kwargs):
    env = load_env()
    return docker("compose", "--project-name", env["KAJH_PROJECT"], "--project-directory", str(ROOT),
                  "--env-file", str(LOCAL / "env"), "--file", str(ROOT / "infrastructure/local/compose.yml"),
                  *args, check=check, **kwargs)


def assert_owned(kind: str, name: str) -> None:
    project = load_env()["KAJH_PROJECT"]
    if not name.startswith(project + "-"):
        raise RuntimeError("Refusing unrelated resource")
    result = docker(kind, "inspect", name, capture_output=True, text=True)
    item = json.loads(result.stdout)[0]
    labels = item.get("Config", {}).get("Labels", {}) if kind == "container" else item.get("Labels", {})
    if labels.get("io.koras.kajh.project") != project:
        raise RuntimeError("Owned resource label mismatch")
