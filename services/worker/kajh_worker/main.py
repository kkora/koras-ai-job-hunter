from __future__ import annotations

import argparse
import asyncio
import json
import signal
import time
from uuid import UUID

from sqlalchemy import select

from kajh_api.models import WorkflowRun, utcnow

from .config import session_factory
from .ingestion import claim_job, fenced_job, finish_failure, ingest_job, purge_expired, reassess_workspace, reconcile_workflow_bindings
from .workflow import run_workflow, setup_checkpoints


def run_once() -> bool:
    factory = session_factory()
    with factory() as db, db.begin():
        reconcile_workflow_bindings(db)
        claimed = claim_job(db)
    if claimed:
        job_id, token = claimed
        try:
            with factory() as db:
                from kajh_api.models import Job
                job = db.get(Job, job_id)
                kind, payload = job.kind, dict(job.payload)
            if kind == "ingest":
                with factory() as db, db.begin():
                    ingest_job(db, job_id, token)
            elif kind == "resume":
                status = asyncio.run(run_workflow(UUID(payload["run_id"]), UUID(payload["decision_id"])))
                if status == "busy":
                    raise ConnectionError("Workflow thread is leased by another worker")
                if status == "error":
                    raise ValueError("Workflow rejected local review authorization")
                with factory() as db, db.begin():
                    job = fenced_job(db, job_id, token)
                    job.status, job.result, job.lease_until = "completed", {"workflow_status": status}, None
                    job.updated_at = utcnow()
            elif kind == "retention":
                with factory() as db, db.begin():
                    job = fenced_job(db, job_id, token)
                    job.result = purge_expired(db)
                    job.status, job.lease_until, job.updated_at = "completed", None, utcnow()
            elif kind == "reassess":
                with factory() as db, db.begin():
                    job = fenced_job(db, job_id, token)
                    job.result = reassess_workspace(db, job.workspace_id)
                    job.status, job.lease_until, job.updated_at = "completed", None, utcnow()
            else:
                raise ValueError("Unsupported local job kind")
        except Exception as error:
            with factory() as db, db.begin():
                finish_failure(db, job_id, token, error)
            print(json.dumps({"event": "job.failed", "job_id": str(job_id), "error_type": type(error).__name__}), flush=True)
        return True
    with factory() as db:
        run_id = db.scalar(select(WorkflowRun.id).where(
            WorkflowRun.status.in_(["queued", "running", "approved"]), WorkflowRun.restore_hold.is_(False),
        ).order_by(WorkflowRun.created_at).limit(1))
    if run_id:
        try:
            asyncio.run(run_workflow(run_id))
        except Exception as error:
            with factory() as db, db.begin():
                run = db.get(WorkflowRun, run_id)
                if run and not run.restore_hold:
                    run.status, run.error = "error", type(error).__name__
            print(json.dumps({"event": "workflow.failed", "run_id": str(run_id), "error_type": type(error).__name__}), flush=True)
        return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--setup", action="store_true")
    args = parser.parse_args()
    asyncio.run(setup_checkpoints())
    if args.setup:
        return
    if args.once:
        run_once()
        return
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_retention = 0.0
    while not stopping:
        try:
            worked = run_once()
            if time.monotonic() - last_retention > 60:
                with session_factory()() as db, db.begin():
                    purge_expired(db)
                last_retention = time.monotonic()
            if not worked:
                time.sleep(0.5)
        except Exception as error:
            print(json.dumps({"event": "worker.loop_error", "error_type": type(error).__name__}), flush=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
