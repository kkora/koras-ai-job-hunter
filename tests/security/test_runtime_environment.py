"""Prevent regression of secrets into the frontend's process environment."""
import importlib.util
from pathlib import Path
import sys
import subprocess

import pytest


def test_frontend_environment_excludes_administrative_and_provider_credentials(monkeypatch):
    scripts=Path(__file__).resolve().parents[2]/"scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec=importlib.util.spec_from_file_location("kajh_runtime_manage",scripts/"manage.py")
    assert spec and spec.loader
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    forbidden={
        "DATABASE_URL":"restricted-api-secret", "DATABASE_ADMIN_URL":"postgres-superuser-secret",
        "WORKER_DATABASE_URL":"worker-secret", "AUTH_JWT_SECRET":"signing-secret",
        "OWNER_PASSWORD":"owner-secret", "REVIEWER_PASSWORD":"reviewer-secret",
        "OPENAI_API_KEY":"provider-secret", "UNRELATED_CUSTOM_SECRET":"unrelated-secret",
    }
    for key,value in forbidden.items():
        monkeypatch.setenv(key,value)
    env=module.web_env()
    assert not set(forbidden)&set(env)
    assert not set(forbidden.values())&set(env.values())
    assert env["KAJH_API_URL"]=="http://127.0.0.1:8210"
    assert env["NEXT_TELEMETRY_DISABLED"]=="1"


def test_compose_refuses_existing_volume_with_another_owner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/"scripts"))
    import local_runtime
    monkeypatch.setattr(local_runtime,"load_env",lambda:{"KAJH_PROJECT":"kajh-p1-probe"})
    mutations=[]

    def fake_docker(*args,**kwargs):
        if args[0]=="compose":
            mutations.append(args)
        if args[:2]==("volume","inspect") and args[2].endswith("-pgdata"):
            return subprocess.CompletedProcess(args,0,'[{"Labels":{"io.koras.kajh.project":"another-owner"}}]',"")
        return subprocess.CompletedProcess(args,1,"[]","Error: no such object")

    monkeypatch.setattr(local_runtime,"docker",fake_docker)
    with pytest.raises(RuntimeError,match="unrecognized volume"):
        local_runtime.compose("up","-d")
    assert not mutations


def test_preflight_does_not_treat_unavailable_daemon_as_absent_resources(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/"scripts"))
    import local_runtime
    monkeypatch.setattr(local_runtime,"load_env",lambda:{"KAJH_PROJECT":"kajh-p1-probe"})
    monkeypatch.setattr(local_runtime,"docker",lambda *a,**k:subprocess.CompletedProcess(a,1,"","Cannot connect to Docker daemon: socket no such file or directory"))
    with pytest.raises(RuntimeError,match="no mutation attempted"):
        local_runtime.preflight_ownership()
