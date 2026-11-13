"""Generate only this project's synthetic local configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"


def load_env() -> dict[str, str]:
    values = {}
    for line in (LOCAL / "env").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def main() -> None:
    if ROOT != Path("/Users/kan/Projects/koras-ai-job-hunter"):
        raise SystemExit("Refusing configuration outside the authorized project")
    if LOCAL.is_symlink():
        raise SystemExit("Refusing symlinked local configuration")
    os.umask(0o077)
    LOCAL.mkdir(exist_ok=True)
    for name in ("evidence", "backups", "docker-client", "logs"):
        (LOCAL / name).mkdir(exist_ok=True)
    marker = LOCAL / "instance.json"
    if marker.exists():
        existing = json.loads(marker.read_text())
        if existing["root"] != str(ROOT) or not existing["project"].startswith("kajh-p1-"):
            raise SystemExit("Unrecognized existing runtime ownership marker")
        if not (LOCAL / "env").is_file():
            raise SystemExit("Existing ownership marker has no configuration; refusing overwrite")
        print(f"Existing owned runtime: {existing['project']}")
        return
    if (LOCAL / "env").exists():
        raise SystemExit("Unrecognized existing local environment; refusing overwrite")
    project = "kajh-p1-" + secrets.token_hex(6)
    values = {
        "KAJH_PROJECT": project,
        "KAJH_ROOT": str(ROOT),
        "KAJH_DOCKER_SOCKET": "unix:///Users/kan/.docker/run/docker.sock",
        "KAJH_DB_NAME": "kajh_dev",
        "KAJH_DB_PORT": "55439",
        "KAJH_API_PORT": "8210",
        "KAJH_AUTH_PORT": "59999",
        "KAJH_MAIL_PORT": "58029",
        "KAJH_WEB_PORT": "3210",
    }
    for key in ("DB_PASSWORD", "API_PASSWORD", "WORKER_PASSWORD", "AUTH_PASSWORD", "JWT_SECRET",
                "OWNER_PASSWORD", "REVIEWER_PASSWORD", "AUDITOR_PASSWORD"):
        values["KAJH_" + key] = secrets.token_hex(24)
    (LOCAL / "env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    (LOCAL / "docker-client/config.json").write_text('{"auths":{}}\n')
    plugin_dir = LOCAL / "docker-client/cli-plugins"
    plugin_dir.mkdir()
    for name in ("docker-compose", "docker-buildx"):
        target = Path("/Applications/Docker.app/Contents/Resources/cli-plugins") / name
        if not target.is_file():
            raise SystemExit(f"Required installed Docker plugin missing: {name}")
        (plugin_dir / name).symlink_to(target)
    marker.write_text(json.dumps({"project": project, "root": str(ROOT), "authorization": "KAJH-P1-AUTH-001"}, indent=2))
    print(f"Created synthetic configuration for {project}; credentials remain in ignored .local/env")


if __name__ == "__main__":
    main()
