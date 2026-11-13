"""Evidence-producing E-09 checks against this project's owned synthetic runtime."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import random
import secrets
import time

import psycopg
from psycopg import sql

from bootstrap import LOCAL, load_env
from init_roles import init_roles
from local_runtime import assert_owned, compose, docker, is_missing_resource


def connect(*, restore: bool = False, role: str = "postgres"):
    env = load_env()
    key = {"postgres": "KAJH_DB_PASSWORD", "kajh_api": "KAJH_API_PASSWORD", "kajh_worker": "KAJH_WORKER_PASSWORD"}[role]
    conn = psycopg.connect(host="127.0.0.1", port=55440 if restore else 55439,
                           dbname="kajh_dev", user=role, password=env[key], autocommit=True,
                           connect_timeout=5)
    expected = env["KAJH_PROJECT"] + ("-restore" if restore else "")
    cluster = conn.execute("SELECT current_setting('cluster_name')").fetchone()
    if not cluster or cluster[0] != expected:
        conn.close()
        raise RuntimeError("Unrecognized runtime cluster")
    return conn


def write_evidence(name: str, result: dict) -> dict:
    value = {"recorded_at": datetime.now(timezone.utc).isoformat(), "authorization": "KAJH-P1-AUTH-001", **result}
    (LOCAL / "evidence" / name).write_text(json.dumps(value, indent=2, default=str) + "\n")
    return value


def vector_probe(rows: int = 10000, dimensions: int = 1536, queries: int = 50) -> dict:
    schema = "kajh_e09_" + secrets.token_hex(5)
    quoted = '"' + schema + '"'
    started = time.perf_counter()
    with connect() as admin:
        owner = admin.execute("SELECT user_id FROM app.memberships WHERE role='Owner' AND workspace_id='00000000-0000-0000-0000-000000000001' AND active LIMIT 1").fetchone()[0]
        version = admin.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
        assert version == "0.8.7"
        admin.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION kajh_owner").format(sql.Identifier(schema)))
        try:
            admin.execute(f"CREATE TABLE {quoted}.toy(id int PRIMARY KEY,workspace_id uuid NOT NULL,embedding public.vector(3))")
            admin.execute(f"CREATE TABLE {quoted}.vectors(id int PRIMARY KEY,workspace_id uuid NOT NULL,grp int,embedding public.vector({dimensions}))")
            for table in ("toy", "vectors"):
                admin.execute(f"ALTER TABLE {quoted}.{table} OWNER TO kajh_owner")
                admin.execute(f"ALTER TABLE {quoted}.{table} ENABLE ROW LEVEL SECURITY")
                admin.execute(f"ALTER TABLE {quoted}.{table} FORCE ROW LEVEL SECURITY")
                admin.execute(f"CREATE POLICY scope ON {quoted}.{table} TO kajh_api USING(app.api_access(workspace_id))")
                admin.execute(f"GRANT SELECT ON {quoted}.{table} TO kajh_api")
            admin.execute(f"GRANT USAGE ON SCHEMA {quoted} TO kajh_api")
            a, b = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
            for identifier, workspace, vector in [(1,a,"[1,0,0]"),(2,a,"[0,1,0]"),(3,a,"[1,1,0]"),(4,b,"[1,0,0]")]:
                admin.execute(f"INSERT INTO {quoted}.toy VALUES(%s,%s,%s)",(identifier,workspace,vector))
            with connect(role="kajh_api") as api:
                with api.transaction():
                    api.execute("SELECT set_config('app.user_id',%s,true),set_config('app.workspace_id',%s,true)",(str(owner),a))
                    toy=api.execute(f"SELECT id,embedding <=> '[1,0,0]'::public.vector FROM {quoted}.toy ORDER BY embedding <=> '[1,0,0]'::public.vector").fetchall()
                    assert [r[0] for r in toy] == [1,3,2]
                    assert abs(toy[1][1]-(1-1/math.sqrt(2)))<0.000001
                with api.transaction():
                    api.execute("SELECT set_config('app.user_id',%s,true),set_config('app.workspace_id',%s,true)",(str(owner),b))
                    assert api.execute(f"SELECT count(*) FROM {quoted}.toy").fetchone()[0] == 0
                try:
                    api.execute("SELECT '[1,2]'::public.vector(3)")
                except psycopg.Error as error:
                    assert error.sqlstate == "22000"
                else:
                    raise AssertionError("Mismatched vector dimensions accepted")
            generator = random.Random(1701)
            with admin.cursor() as cursor:
                with cursor.copy(f"COPY {quoted}.vectors(id,workspace_id,grp,embedding) FROM STDIN") as copy:
                    for identifier in range(1,rows+1):
                        vector = "["+",".join(f"{generator.uniform(-1,1):.7f}" for _ in range(dimensions))+"]"
                        copy.write_row((identifier,a if identifier%2 else b,(identifier//2)%10,vector))
            build_start=time.perf_counter()
            admin.execute(f"CREATE INDEX e09_hnsw ON {quoted}.vectors USING hnsw(embedding public.vector_cosine_ops)")
            admin.execute(f"ANALYZE {quoted}.vectors")
            build_seconds=time.perf_counter()-build_start
            latencies, recalls=[],[]
            index_plan_verified=False
            with connect(role="kajh_api") as api:
                for identifier in range(1,queries*2,2):
                    with api.transaction():
                        api.execute("SELECT set_config('app.user_id',%s,true),set_config('app.workspace_id',%s,true)",(str(owner),a))
                        vector=api.execute(f"SELECT embedding::text FROM {quoted}.vectors WHERE id=%s",(identifier,)).fetchone()[0]
                        api.execute("SET LOCAL enable_indexscan=off")
                        api.execute("SET LOCAL enable_bitmapscan=off")
                        group=(identifier//2)%10
                        exact=api.execute(f"SELECT id FROM {quoted}.vectors WHERE grp=%s ORDER BY embedding <=> %s::public.vector LIMIT 10",(group,vector)).fetchall()
                        api.execute("SET LOCAL enable_indexscan=on")
                        api.execute("SET LOCAL enable_seqscan=off")
                        api.execute("SET LOCAL hnsw.ef_search=1000")
                        api.execute("SET LOCAL hnsw.iterative_scan='strict_order'")
                        if not index_plan_verified:
                            plan=api.execute(f"EXPLAIN (FORMAT JSON) SELECT id FROM {quoted}.vectors WHERE grp=%s ORDER BY embedding <=> %s::public.vector LIMIT 10",(group,vector)).fetchone()[0]
                            assert "e09_hnsw" in json.dumps(plan),"Approximate query did not use HNSW"
                            index_plan_verified=True
                        query_start=time.perf_counter()
                        approximate=api.execute(f"SELECT id,workspace_id::text,grp FROM {quoted}.vectors WHERE grp=%s ORDER BY embedding <=> %s::public.vector LIMIT 10",(group,vector)).fetchall()
                        latencies.append(time.perf_counter()-query_start)
                        assert len(exact)==len(approximate)==10
                        assert all(r[1]==a and r[2]==group for r in approximate)
                        recalls.append(len({r[0] for r in exact}&{r[0] for r in approximate})/10)
            recall=sum(recalls)/len(recalls)
            p95=sorted(latencies)[math.ceil(len(latencies)*.95)-1]
            result={"status":"PASS","pgvector":version,"rows":rows,"dimensions":dimensions,"queries":queries,
                    "seed":1701,"mean_recall_at_10":recall,"p95_seconds":p95,"index_build_seconds":build_seconds,
                    "total_seconds":time.perf_counter()-started,"actual_restricted_role":"kajh_api","workspace_isolation":True,
                    "ef_search":1000,"hnsw_plan_verified":index_plan_verified,"filter_shared_across_workspaces":True,"production_capacity_claim":False}
            if recall<.95 or p95>=2:
                result["status"]="FAIL"
                write_evidence("E09-vector.json",result)
                raise AssertionError(f"Vector threshold failed: recall={recall}, p95={p95}")
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    return write_evidence("E09-vector.json",result)


def snapshot(conn) -> dict:
    output={}
    tables=conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='app' ORDER BY tablename").fetchall()
    for (table,) in tables:
        data=conn.execute(sql.SQL("SELECT to_jsonb(t)::text FROM app.{} t ORDER BY to_jsonb(t)::text").format(sql.Identifier(table))).fetchall()
        output[table]={"count":len(data),"sha256":hashlib.sha256("\n".join(row[0] for row in data).encode()).hexdigest()}
    return output


def backup_restore() -> dict:
    import subprocess
    import sys
    from uuid import uuid4

    from bootstrap import ROOT
    from manage import app_env

    env=load_env()
    project=env["KAJH_PROJECT"]
    backup=LOCAL/"backups"/("kajh-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+".dump")
    started=time.perf_counter()
    for service in ("api","worker","auth","db"):
        assert_owned("container",f"{project}-{service}")
    # No application service ever points at the restore database.
    compose("stop","--timeout","30","api","worker","auth")
    restore_created=False
    probe=None
    primary_before=None

    def worker_probe(code: str, *, restored: bool = False, crash: bool = False) -> str:
        # Each invocation starts a new restricted worker, including checkpoint
        # connections. No administrator credential reaches the worker process.
        worker_env=app_env()
        for key in ("DATABASE_ADMIN_URL","AUTH_JWT_SECRET","OWNER_PASSWORD","REVIEWER_PASSWORD","AUDITOR_PASSWORD"):
            worker_env.pop(key,None)
        worker_env["DATABASE_URL"]=worker_env["WORKER_DATABASE_URL"]
        if restored:
            for key in ("DATABASE_URL","WORKER_DATABASE_URL","CHECKPOINT_DATABASE_URL"):
                worker_env[key]=worker_env[key].replace(":55439/",":55440/")
        worker_env["KAJH_WORKER_FAULT_INJECTION"]="true" if crash else "false"
        completed=subprocess.run([sys.executable,"-B","-c",code],cwd=ROOT,env=worker_env,
                                 capture_output=True,text=True,timeout=30,check=False)
        assert completed.returncode==(86 if crash else 0),"Restricted checkpoint probe failed"
        return completed.stdout.strip()

    def record_approval(identifier, *, restored: bool = False):
        # This fixture writes through the actual Owner-scoped API database role;
        # it is synthetic review evidence, not a claim of an interactive login.
        assert probe is not None
        with connect(restore=restored,role="kajh_api") as api:
            with api.transaction():
                api.execute("SELECT set_config('app.user_id',%s,true),set_config('app.workspace_id',%s,true)",
                            (str(probe["owner"]),str(probe["workspace"])))
                assert api.execute("SELECT app.can_review(%s,%s)",(probe["owner"],probe["workspace"])).fetchone()[0]
                row=api.execute("SELECT r.thread_id,o.version FROM app.workflow_runs r JOIN app.opportunities o ON o.id=r.opportunity_id WHERE r.id=%s",(probe["run"],)).fetchone()
                digest=hashlib.sha256(f"{row[0]}:{row[1]}:review".encode()).hexdigest()
                api.execute("INSERT INTO app.workflow_decisions(id,workspace_id,run_id,user_id,decision,payload_hash,content_version,expires_at,consumed,created_at) VALUES(%s,%s,%s,%s,'approved',%s,%s,clock_timestamp()+interval '10 minutes',false,clock_timestamp())",
                            (identifier,probe["workspace"],probe["run"],probe["owner"],digest,row[1]))

    try:
        with connect() as source:
            primary_before=snapshot(source)
            row=source.execute("SELECT o.id,o.workspace_id,m.user_id FROM app.opportunities o JOIN app.memberships m ON m.workspace_id=o.workspace_id AND m.role='Owner' AND m.active WHERE o.eligibility='Unknown' AND o.lifecycle='open' AND o.facts->>'synthetic'='true' AND EXISTS(SELECT 1 FROM app.opportunity_sources l JOIN app.sources s ON s.id=l.source_id JOIN app.raw_observations x ON x.id=l.observation_id WHERE l.opportunity_id=o.id AND s.adapter_type='synthetic' AND s.enabled AND NOT s.killed AND x.expires_at>clock_timestamp()) ORDER BY o.id LIMIT 1").fetchone()
            assert row,"A live synthetic Unknown opportunity and Owner are required"
            probe={"run":uuid4(),"old_decision":uuid4(),"fresh_decision":uuid4(),
                   "thread":"kajh-e09-restore-"+uuid4().hex,"opportunity":row[0],"workspace":row[1],"owner":row[2]}
            source.execute("INSERT INTO app.workflow_runs(id,workspace_id,opportunity_id,thread_id,graph_version,policy_version,status,budget,restore_hold,requested_by,version,created_at,updated_at) VALUES(%s,%s,%s,%s,'p1-v1','kajh-eligibility-1.0','test_pending','{\"paid_calls\":0,\"limit\":0,\"mock_mode\":\"crash_after_consume\"}'::jsonb,false,%s,1,clock_timestamp(),clock_timestamp())",
                           (probe["run"],probe["workspace"],probe["opportunity"],probe["thread"],probe["owner"]))
        run_code="import asyncio; from kajh_worker.workflow import run_workflow; "
        assert worker_probe(run_code+f"assert asyncio.run(run_workflow('{probe['run']}'))=='waiting_review'")==""
        record_approval(probe["old_decision"])
        worker_probe(run_code+f"asyncio.run(run_workflow('{probe['run']}','{probe['old_decision']}'))",crash=True)
        with connect() as source:
            assert source.execute("SELECT consumed FROM app.workflow_decisions WHERE id=%s",(probe["old_decision"],)).fetchone()[0]
            assert source.execute("SELECT status FROM app.workflow_runs WHERE id=%s",(probe["run"],)).fetchone()[0]=="approved"
            expected=snapshot(source)
            checkpoint_count=source.execute("SELECT count(*) FROM checkpoints.checkpoints").fetchone()[0]
            probe_checkpoints=source.execute("SELECT count(*) FROM checkpoints.checkpoints WHERE thread_id=%s",(probe["thread"],)).fetchone()[0]
            assert probe_checkpoints>0
        with backup.open("xb") as output:
            backup.chmod(0o600)
            docker("exec",project+"-db","pg_dump","-U","postgres","-d","kajh_dev","--format=custom",stdout=output)
        with backup.open("rb") as archive:
            assert archive.read(5)==b"PGDMP","Database archive is missing its custom-format header"
        digest=hashlib.sha256(backup.read_bytes()).hexdigest()
        # Standard schema roots are restored from the archive, not pre-created.
        # Mark the attempt before creation so a partial startup is cleaned too.
        restore_created=True
        compose("--profile","restore","up","-d","--wait","--wait-timeout","120","restore-db","restore-gateway")
        init_roles(restore=True,schemas=False)
        with backup.open("rb") as data:
            docker("exec","-i",project+"-restore-db","pg_restore","-U","postgres","-d","kajh_dev","--exit-on-error","--single-transaction",stdin=data)
        with connect(restore=True) as restored:
            actual=snapshot(restored)
            assert actual==expected,"Restored application row inventory differs"
            assert restored.execute("SELECT count(*) FROM checkpoints.checkpoints").fetchone()[0]==checkpoint_count
            with restored.transaction():
                restored.execute("UPDATE app.workflow_runs SET restore_hold=true,status='restore_hold'")
                restored.execute("DELETE FROM app.workflow_decisions")
                restored.execute("DELETE FROM auth.refresh_tokens")
                restored.execute("DELETE FROM auth.sessions")
            assert restored.execute("SELECT count(*) FROM app.workflow_runs WHERE NOT restore_hold").fetchone()[0]==0
            owner=restored.execute("SELECT user_id FROM app.memberships WHERE role='Owner' AND workspace_id='00000000-0000-0000-0000-000000000001' LIMIT 1").fetchone()[0]
            role_flags=restored.execute("SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname IN ('kajh_api','kajh_worker') ORDER BY rolname").fetchall()
            assert all(not r[1] and not r[2] for r in role_flags)
            missing=restored.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='app' AND c.relkind='r' AND c.relname NOT IN ('schema_migrations') AND NOT c.relrowsecurity").fetchone()[0]
            assert missing==0
        with connect(restore=True,role="kajh_api") as api:
            with api.transaction():
                api.execute("SELECT set_config('app.user_id',%s,true),set_config('app.workspace_id','00000000-0000-0000-0000-000000000002',true)",(str(owner),))
                assert api.execute("SELECT count(*) FROM app.opportunities").fetchone()[0]==0
        held_code=("import asyncio; from kajh_worker.workflow import run_workflow\n"
                   "try:\n"
                   f" asyncio.run(run_workflow('{probe['run']}','{probe['old_decision']}'))\n"
                   "except PermissionError:\n pass\n"
                   "else:\n raise AssertionError('Restored consumed approval escaped restore hold')\n")
        worker_probe(held_code,restored=True)
        with connect(restore=True) as restored:
            assert restored.execute("SELECT count(*) FROM checkpoints.checkpoints WHERE thread_id=%s",(probe["thread"],)).fetchone()[0]==probe_checkpoints
            assert restored.execute("SELECT count(*) FROM app.workflow_decisions WHERE run_id=%s",(probe["run"],)).fetchone()[0]==0
        # Release only this test thread using its current synthetic Owner. Every
        # other restored run remains held and no original approval is reusable.
        with connect(restore=True,role="kajh_api") as api:
            with api.transaction():
                api.execute("SELECT set_config('app.user_id',%s,true),set_config('app.workspace_id',%s,true)",
                            (str(probe["owner"]),str(probe["workspace"])))
                api.execute("UPDATE app.workflow_runs SET restore_hold=false,status='waiting_review',budget=jsonb_set(budget,'{mock_mode}','\"normal\"'::jsonb),version=version+1,updated_at=clock_timestamp() WHERE id=%s",(probe["run"],))
                api.execute("INSERT INTO app.audit_events(id,workspace_id,actor,action,object_type,object_id,details,created_at) VALUES(gen_random_uuid(),%s,%s,'workflow.synthetic_restore_release','workflow',%s,'{\"synthetic_test\":true,\"external_dispatch\":false}'::jsonb,clock_timestamp())",
                            (probe["workspace"],str(probe["owner"]),str(probe["run"])))
        worker_probe(run_code+f"assert asyncio.run(run_workflow('{probe['run']}'))=='waiting_review'",restored=True)
        record_approval(probe["fresh_decision"],restored=True)
        resume_code=run_code+f"assert asyncio.run(run_workflow('{probe['run']}','{probe['fresh_decision']}'))=='completed'"
        worker_probe(resume_code,restored=True)
        worker_probe(resume_code,restored=True)
        with connect(restore=True) as restored:
            assert restored.execute("SELECT status,restore_hold FROM app.workflow_runs WHERE id=%s",(probe["run"],)).fetchone()==("completed",False)
            assert restored.execute("SELECT consumed FROM app.workflow_decisions WHERE id=%s",(probe["fresh_decision"],)).fetchone()[0]
            fresh_consumptions=restored.execute("SELECT count(*) FROM app.audit_events WHERE object_id=%s AND action='workflow.local_review_consumed' AND details->>'decision_id'=%s",(str(probe["run"]),str(probe["fresh_decision"]))).fetchone()[0]
            assert fresh_consumptions==1
            assert restored.execute("SELECT count(*) FROM app.workflow_runs WHERE id<>%s AND NOT restore_hold",(probe["run"],)).fetchone()[0]==0
            expired=restored.execute("SELECT o.id FROM app.opportunities o JOIN app.opportunity_sources l ON l.opportunity_id=o.id JOIN app.raw_observations x ON x.id=l.observation_id WHERE o.id<>%s AND o.lifecycle='open' AND o.facts->>'synthetic'='true' GROUP BY o.id HAVING count(*)=1 ORDER BY o.id LIMIT 1",(probe["opportunity"],)).fetchone()
            assert expired,"A separate single-source synthetic retention fixture is required"
            expired_id=expired[0]
            expired_observations=[r[0] for r in restored.execute("SELECT observation_id FROM app.opportunity_sources WHERE opportunity_id=%s",(expired_id,)).fetchall()]
            restored.execute("UPDATE app.raw_observations SET expires_at=clock_timestamp()-interval '1 second' WHERE id=ANY(%s)",(expired_observations,))
        worker_probe("from kajh_worker.config import session_factory; from kajh_worker.ingestion import purge_expired\n"
                     "with session_factory()() as db, db.begin():\n assert purge_expired(db)['observations']>=1\n",restored=True)
        with connect(restore=True) as restored:
            assert restored.execute("SELECT count(*) FROM app.raw_observations WHERE id=ANY(%s)",(expired_observations,)).fetchone()[0]==0
            assert restored.execute("SELECT count(*) FROM app.opportunity_sources WHERE opportunity_id=%s",(expired_id,)).fetchone()[0]==0
            assert restored.execute("SELECT count(*) FROM app.eligibility_evidence WHERE opportunity_id=%s",(expired_id,)).fetchone()[0]==0
            erased=restored.execute("SELECT lifecycle,eligibility,title,description,organization_domain,skills FROM app.opportunities WHERE id=%s",(expired_id,)).fetchone()
            assert erased==("purged","Unknown","Expired synthetic opportunity","",None,[])
            assert restored.execute("SELECT count(*) FROM app.workflow_runs WHERE opportunity_id=%s AND NOT restore_hold",(expired_id,)).fetchone()[0]==0
        result={"status":"PASS","archive_sha256":digest,"archive_bytes":backup.stat().st_size,
                "tables_compared":len(expected),"checkpoint_count":checkpoint_count,"same_data_before_security_hold":True,
                "restored_workflows_held":True,"restored_approvals_cleared":True,"restored_sessions_revoked":True,
                "rls_and_roles_preserved":True,"actual_retention_purge":True,"external_dispatch":"ABSENT",
                "restored_consumed_approval_replay_blocked":True,"restored_checkpoint_requires_fresh_approval":True,
                "fresh_owner_scoped_fixture_approval":True,"approval_fixture_is_interactive_login":False,
                "new_process_resume_completed":True,"fresh_approval_consumed_once":fresh_consumptions==1,
                "object_storage":"NOT_USED_P1; database dump does not include object bytes",
                "seconds":time.perf_counter()-started}
    finally:
        cleanup_errors=[]
        try:
            if probe:
                try:
                    with connect() as source,source.transaction():
                        source.execute("DELETE FROM app.workflow_decisions WHERE run_id=%s",(probe["run"],))
                        source.execute("DELETE FROM app.workflow_runs WHERE id=%s",(probe["run"],))
                        source.execute("DELETE FROM app.audit_events WHERE object_id=%s",(str(probe["run"]),))
                        for table in ("checkpoint_writes","checkpoint_blobs","checkpoints"):
                            source.execute(sql.SQL("DELETE FROM checkpoints.{} WHERE thread_id=%s").format(sql.Identifier(table)),(probe["thread"],))
                        assert snapshot(source)==primary_before,"Primary fixture cleanup changed unrelated business rows"
                except Exception as error:
                    cleanup_errors.append("primary_probe:"+type(error).__name__)
            if restore_created:
                resources=[("container",project+"-restore-gateway"),("container",project+"-restore-db"),
                           ("volume",project+"-restoredata"),("network",project+"-restore"),("network",project+"-restore-ingress")]
                for kind,name in resources:
                    try:
                        found=docker(kind,"inspect",name,check=False,capture_output=True,text=True)
                        if found.returncode:
                            assert is_missing_resource(kind,name,found.stderr),"Unable to inspect restore resource"
                            continue
                        assert_owned(kind,name)
                        args=(kind,"rm","--force",name) if kind=="container" else (kind,"rm",name)
                        docker(*args,capture_output=True,text=True)
                    except Exception as error:
                        cleanup_errors.append(name+":"+type(error).__name__)
            try:
                if backup.exists():
                    backup.unlink()
            except OSError as error:
                cleanup_errors.append("temporary_dump:"+type(error).__name__)
        finally:
            # A cleanup failure must never strand the original application down.
            try:
                compose("up","-d","--no-build","--wait","--wait-timeout","120","auth","api","worker")
            except Exception as error:
                cleanup_errors.append("primary_restart:"+type(error).__name__)
        if cleanup_errors:
            write_evidence("E09-backup-restore-cleanup.json",{"status":"FAIL","errors":cleanup_errors})
            raise RuntimeError("Backup/restore cleanup failed; see E09-backup-restore-cleanup.json")
    result["disposable_restore_cleanup"]="PASS"
    result["primary_probe_cleanup"]="PASS"
    result["temporary_dump_deleted"]=not backup.exists()
    return write_evidence("E09-backup-restore.json",result)


def runtime_inventory() -> dict:
    env=load_env()
    project=env["KAJH_PROJECT"]
    services={}
    for name in ("db","auth","api","worker","mail","gateway"):
        assert_owned("container",project+"-"+name)
        data=json.loads(docker("container","inspect",project+"-"+name,capture_output=True,text=True).stdout)[0]
        services[name]={"running":data["State"]["Running"],"health":data["State"].get("Health",{}).get("Status"),
                        "memory_limit_bytes":data["HostConfig"]["Memory"],"nano_cpus":data["HostConfig"]["NanoCpus"],
                        "networks":list(data["NetworkSettings"]["Networks"]),"image_id":data["Image"]}
    net=json.loads(docker("network","inspect",project+"-private",capture_output=True,text=True).stdout)[0]
    assert net["Internal"]
    worker=services["worker"]
    assert worker["networks"]==[project+"-private"]
    for name in ("api","worker","db","auth","mail"):
        data=json.loads(docker("container","inspect",project+"-"+name,capture_output=True,text=True).stdout)[0]
        assert not any(data["NetworkSettings"].get("Ports",{}).values()),"Internal service unexpectedly published"
    gateway=json.loads(docker("container","inspect",project+"-gateway",capture_output=True,text=True).stdout)[0]
    assert all(binding["HostIp"]=="127.0.0.1" for bindings in gateway["NetworkSettings"]["Ports"].values() for binding in (bindings or []))
    stats=docker("stats","--no-stream","--format","{{json .}}",*(project+"-"+s for s in services),capture_output=True,text=True)
    with connect() as conn:
        server=conn.execute("SELECT version(),(SELECT extversion FROM pg_extension WHERE extname='vector')").fetchone()
        roles=conn.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname IN ('kajh_api','kajh_worker','kajh_auth_admin') ORDER BY rolname").fetchall()
        assert len(roles)==3 and all(not any(role[1:]) for role in roles)
    return write_evidence("E09-resources.json",{"status":"PASS","services":services,"internal_worker_network":True,
                          "postgres":server[0],"vector":server[1],"roles_nonsuperuser_nobypass_nocreate":True,
                          "loopback_only_ingress":True,"stats":[json.loads(line) for line in stats.stdout.splitlines()]})


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=["vectors","backup-restore","inventory"])
    mode=parser.parse_args().mode
    print(json.dumps({"vectors":vector_probe,"backup-restore":backup_restore,"inventory":runtime_inventory}[mode](),indent=2,default=str))
