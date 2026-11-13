"""Prevent regression of secrets into the frontend's process environment."""
import importlib.util
import asyncio
import json
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


@pytest.mark.parametrize("command", ["stop", "resume", "migrate", "web"])
def test_unknown_lifecycle_flags_fail_before_any_state_change(monkeypatch, command):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/"scripts"))
    import manage
    calls=[]
    for name in ("bootstrap", "compose", "assert_owned", "web_start", "web_stop", "run"):
        monkeypatch.setattr(manage,name,lambda *args, _name=name, **kwargs:calls.append(_name))
    monkeypatch.setattr(sys,"argv",["manage.py",command,"--dry-run"])
    with pytest.raises(SystemExit) as caught:
        manage.main()
    assert caught.value.code==2
    assert calls==[]


def test_test_command_still_forwards_pytest_options(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/"scripts"))
    import manage
    calls=[]
    monkeypatch.setattr(manage,"bootstrap",lambda:None)
    monkeypatch.setattr(manage,"run",lambda *args:calls.append(args))
    monkeypatch.setattr(sys,"argv",["manage.py","test","-q","tests/unit"])
    manage.main()
    assert len(calls)==1 and calls[0][-2:]==("-q","tests/unit")


def test_partial_backup_shutdown_still_restarts_primary_services(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/"scripts"))
    import validate_runtime
    monkeypatch.setattr(validate_runtime,"load_env",lambda:{"KAJH_PROJECT":"kajh-p1-test"})
    monkeypatch.setattr(validate_runtime,"assert_owned",lambda *a:None)
    monkeypatch.setattr(validate_runtime,"LOCAL",Path("/nonexistent/kajh-owned-validation"))
    calls=[]

    def compose(*args):
        calls.append(args)
        if args[0]=="stop":
            raise subprocess.CalledProcessError(1,["compose","stop"])

    monkeypatch.setattr(validate_runtime,"compose",compose)
    with pytest.raises(subprocess.CalledProcessError):
        validate_runtime.backup_restore()
    assert [call[0] for call in calls]==["stop","up"]
    assert calls[1][-3:]==("auth","api","worker")


def test_failed_backup_shutdown_cannot_delete_an_existing_archive(monkeypatch,tmp_path):
    from datetime import datetime,timezone
    root=Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/"scripts"))
    import validate_runtime
    fixed=datetime(2026,11,13,tzinfo=timezone.utc)

    class Clock:
        @staticmethod
        def now(tz):
            return fixed

    monkeypatch.setattr(validate_runtime,"datetime",Clock)
    monkeypatch.setattr(validate_runtime.secrets,"token_hex",lambda n:"01234567")
    monkeypatch.setattr(validate_runtime,"load_env",lambda:{"KAJH_PROJECT":"kajh-p1-test"})
    monkeypatch.setattr(validate_runtime,"assert_owned",lambda *a:None)

    def compose(*args):
        if args[0]=="stop":
            raise subprocess.CalledProcessError(1,["compose","stop"])

    monkeypatch.setattr(validate_runtime,"compose",compose)
    (tmp_path/"backups").mkdir()
    archive=tmp_path/"backups"/f"kajh-{fixed.strftime('%Y%m%dT%H%M%S%fZ')}-01234567.dump"
    archive.write_bytes(b"preexisting owned archive")
    monkeypatch.setattr(validate_runtime,"LOCAL",tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        validate_runtime.backup_restore()
    assert archive.read_bytes()==b"preexisting owned archive"


@pytest.mark.parametrize("module,function",[
    ("validate_runtime","vector_probe"),("validate_runtime","backup_restore"),
    ("validate_runtime","runtime_inventory"),("validate_offline","offline_probe"),
])
def test_optimized_python_cannot_bypass_runtime_validation(module,function):
    root=Path(__file__).resolve().parents[2]
    code=("import importlib,sys;sys.path.insert(0,'scripts');"
          f"m=importlib.import_module({module!r});"
          "m.load_env=lambda:(_ for _ in ()).throw(RuntimeError('State consulted before validation guard'));"
          f"getattr(m,{function!r})()")
    result=subprocess.run([sys.executable,"-O","-B","-c",code],cwd=root,capture_output=True,text=True)
    assert result.returncode!=0
    assert "Runtime validation requires Python assertions" in result.stderr
    assert "State consulted" not in result.stderr


def test_evidence_rerun_preserves_accepted_artifact(monkeypatch,tmp_path):
    root=Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/"scripts"))
    import validate_runtime
    (tmp_path/"evidence").mkdir()
    original=tmp_path/"evidence/E09-probe.json"
    accepted=b'{"status":"PASS","historical":true}\n'
    original.write_bytes(accepted)
    monkeypatch.setattr(validate_runtime,"LOCAL",tmp_path)
    result=validate_runtime.write_evidence("E09-probe.json",{"status":"FAIL","authorization":"untrusted"})
    assert original.read_bytes()==accepted
    assert result["evidence_file"]!=original.name
    rerun=tmp_path/"evidence"/result["evidence_file"]
    assert json.loads(rerun.read_text())["status"]=="FAIL"
    assert result["authorization"]=="KAJH-P1-AUTH-001"
    assert rerun.stat().st_mode&0o777==0o600


@pytest.mark.asyncio
async def test_gateway_preserves_response_after_client_half_close():
    path=Path(__file__).resolve().parents[2]/"infrastructure/local/gateway.py"
    spec=importlib.util.spec_from_file_location("kajh_test_gateway",path)
    assert spec and spec.loader
    gateway=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)

    async def upstream(reader,writer):
        body=await reader.read()
        writer.write(b"response:"+body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server=await asyncio.start_server(upstream,"127.0.0.1",0)
    async with server:
        target=server.sockets[0].getsockname()[:2]
        proxy=await asyncio.start_server(lambda r,w:gateway.accept(r,w,target),"127.0.0.1",0)
        async with proxy:
            reader,writer=await asyncio.open_connection(*proxy.sockets[0].getsockname()[:2])
            try:
                writer.write(b"synthetic request")
                await writer.drain()
                writer.write_eof()
                assert await asyncio.wait_for(reader.read(),2)==b"response:synthetic request"
            finally:
                writer.close()
                await writer.wait_closed()
