"""Owned local runtime lifecycle. No cloud, sources, paid inference, or outbound actions."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

from bootstrap import LOCAL, ROOT, load_env, main as bootstrap
from local_runtime import compose, assert_owned


def app_env() -> dict[str, str]:
    values = load_env()
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in ("services/api", "services/worker", "packages/domain-python")),
        "PYTHONDONTWRITEBYTECODE": "1", "APP_ENV": "local", "DISPATCH_ENABLED": "false",
        "KAJH_PROJECT": values["KAJH_PROJECT"],
        "DATABASE_URL": f"postgresql+psycopg://kajh_api:{values['KAJH_API_PASSWORD']}@127.0.0.1:55439/kajh_dev",
        "DATABASE_ADMIN_URL": f"postgresql+psycopg://postgres:{values['KAJH_DB_PASSWORD']}@127.0.0.1:55439/kajh_dev",
        "WORKER_DATABASE_URL": f"postgresql+psycopg://kajh_worker:{values['KAJH_WORKER_PASSWORD']}@127.0.0.1:55439/kajh_dev",
        "CHECKPOINT_DATABASE_URL": f"postgresql://kajh_worker:{values['KAJH_WORKER_PASSWORD']}@127.0.0.1:55439/kajh_dev",
        "AUTH_URL": "http://127.0.0.1:59999", "AUTH_ISSUER": "http://127.0.0.1:59999",
        "AUTH_JWT_SECRET": values["KAJH_JWT_SECRET"],
        "WEB_ORIGINS": "http://127.0.0.1:3210,http://localhost:3210",
        "OWNER_PASSWORD": values["KAJH_OWNER_PASSWORD"], "REVIEWER_PASSWORD": values["KAJH_REVIEWER_PASSWORD"],
        "AUDITOR_PASSWORD": values["KAJH_AUDITOR_PASSWORD"],
        "LANGGRAPH_STRICT_MSGPACK": "true", "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
        "NEXT_TELEMETRY_DISABLED": "1", "npm_config_update_notifier": "false",
        "npm_config_cache": str(ROOT / "apps/web/.cache/npm"),
        "PLAYWRIGHT_BROWSERS_PATH": str(LOCAL / "playwright"),
        "API_BASE_URL": "http://127.0.0.1:8210", "KAJH_API_URL": "http://127.0.0.1:8210",
        "KAJH_TEST_RUNTIME": "1",
    })
    # Never pass an unrelated provider credential to application/test processes.
    for key in list(env):
        if key in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LANGSMITH_API_KEY", "GMAIL_ACCESS_TOKEN", "SUPABASE_SERVICE_ROLE_KEY", "SMTP_PASSWORD"}:
            env.pop(key, None)
    return env


def run(*args: str, **kwargs):
    return subprocess.run(args, cwd=ROOT, env=app_env(), check=True, **kwargs)


def web_env() -> dict[str, str]:
    """The frontend proxy needs no database, JWT, account or provider secrets."""
    allowed = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TZ", "SHELL")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({"API_BASE_URL": "http://127.0.0.1:8210", "KAJH_API_URL": "http://127.0.0.1:8210",
                "NEXT_TELEMETRY_DISABLED": "1", "npm_config_update_notifier": "false",
                "npm_config_cache": str(ROOT / "apps/web/.cache/npm")})
    return env


def process_signature(pid: int) -> str:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="], capture_output=True, text=True)
    return result.stdout.strip()


def web_start() -> None:
    pid_file = LOCAL / "web-process.json"
    if pid_file.exists():
        record = json.loads(pid_file.read_text())
        if process_signature(record["pid"]) == record["signature"]:
            print("Web process already running")
            return
        pid_file.unlink()
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 3210))
    log = (LOCAL / "logs/web.log").open("a")
    proc = subprocess.Popen(["npm", "run", "dev"], cwd=ROOT / "apps/web", env=web_env(),
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    time.sleep(0.3)
    signature = process_signature(proc.pid)
    if not signature:
        raise RuntimeError("Web process exited; inspect .local/logs/web.log")
    pid_file.write_text(json.dumps({"pid": proc.pid, "signature": signature,
                                    "root": str(ROOT), "project": load_env()["KAJH_PROJECT"]}, indent=2))
    for _ in range(45):
        try:
            with urllib.request.urlopen("http://127.0.0.1:3210", timeout=2) as response:
                if response.status == 200:
                    print("Web: http://127.0.0.1:3210")
                    return
        except (OSError, TimeoutError):
            time.sleep(1)
    raise RuntimeError("Web did not become ready; inspect .local/logs/web.log")


def web_stop() -> None:
    pid_file = LOCAL / "web-process.json"
    if not pid_file.exists():
        return
    record = json.loads(pid_file.read_text())
    if record["root"] != str(ROOT) or record["project"] != load_env()["KAJH_PROJECT"]:
        raise RuntimeError("Web ownership mismatch")
    signature = process_signature(record["pid"])
    if signature:
        if signature != record["signature"] or os.getpgid(record["pid"]) != record["pid"]:
            raise RuntimeError("Refusing to stop an unrecognized/reused process")
        os.killpg(record["pid"], signal.SIGTERM)
    pid_file.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "up", "resume", "migrate", "seed", "web", "stop", "status", "test", "credentials", "backup-restore"))
    args, extra = parser.parse_known_args()
    if args.command == "prepare":
        bootstrap()
        return
    if not (LOCAL / "env").exists():
        bootstrap()
    if args.command == "up":
        from init_roles import init_roles
        from resolve_images import main as resolve
        resolve()
        compose("up", "-d", "--wait", "--wait-timeout", "180", "db", "mail", "gateway")
        init_roles()
        compose("up", "-d", "--wait", "--wait-timeout", "180", "auth")
        run(sys.executable, "-B", "-m", "kajh_api.bootstrap", "migrate")
        run(sys.executable, "-B", "-m", "kajh_api.bootstrap", "seed")
        compose("build", "api")
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "api", "worker")
        web_start()
    elif args.command == "resume":
        # Reuse only the already-owned runtime, without registry, build, seed,
        # migration, or dependency-install steps. Suitable for cached offline use.
        project = load_env()["KAJH_PROJECT"]
        for service in ("db", "api", "worker", "auth", "mail", "gateway"):
            assert_owned("container", f"{project}-{service}")
        compose("up", "-d", "--pull", "never", "--no-build", "--wait", "--wait-timeout", "180", "db", "mail", "gateway", "auth", "api", "worker")
        web_start()
    elif args.command in {"migrate", "seed"}:
        run(sys.executable, "-B", "-m", "kajh_api.bootstrap", args.command)
    elif args.command == "web":
        web_start()
    elif args.command == "stop":
        web_stop()
        project = load_env()["KAJH_PROJECT"]
        for service in ("db", "api", "worker", "auth", "mail", "gateway"):
            assert_owned("container", f"{project}-{service}")
        compose("stop", "--timeout", "30", "api", "worker", "auth", "mail", "gateway", "db")
    elif args.command == "status":
        compose("ps")
        print("Web process:", "recorded" if (LOCAL / "web-process.json").exists() else "not started")
    elif args.command == "test":
        run(sys.executable, "-B", "-m", "pytest", *(extra or ["-q"]))
    elif args.command == "credentials":
        values = load_env()
        for role in ("OWNER", "REVIEWER", "AUDITOR"):
            print(role.lower() + "@kajh.invalid", values[f"KAJH_{role}_PASSWORD"])
        print("Synthetic local accounts only; do not reuse these credentials.")
    elif args.command == "backup-restore":
        from validate_runtime import backup_restore
        backup_restore()


if __name__ == "__main__":
    main()
