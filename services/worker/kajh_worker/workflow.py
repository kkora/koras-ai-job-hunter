"""Persistent local assessment graph. It has no model SDK or dispatch capability."""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Literal, TypedDict
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import NodeError, NodeTimeoutError
from langgraph.graph import START, StateGraph
from langgraph.types import Command, RetryPolicy, interrupt
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from kajh_api.models import Opportunity, WorkflowDecision, WorkflowRun, utcnow
from kajh_domain import POLICY_VERSION

from .config import checkpoint_url, session_factory
from .ingestion import GRAPH_VERSION, audit, validate_provenance


class AssessmentState(TypedDict, total=False):
    run_id: str
    workspace_id: str
    graph_version: str
    policy_version: str
    opportunity_version: int
    status: str
    paid_calls: int
    mock_steps: int


def decision_hash(run: WorkflowRun, opportunity: Opportunity) -> str:
    return hashlib.sha256(f"{run.thread_id}:{opportunity.version}:review".encode()).hexdigest()


def no_external_authority():
    if os.environ.get("DISPATCH_ENABLED", "false").lower() != "false":
        raise PermissionError("External dispatch must remain disabled")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SMTP_PASSWORD", "GMAIL_ACCESS_TOKEN", "SUPABASE_SERVICE_ROLE_KEY"):
        if os.environ.get(name):
            raise PermissionError("Worker cannot receive model or messaging credentials")


def _current_rows(db: Session, run_id: UUID, *, lock: bool = False) -> tuple[WorkflowRun, Opportunity]:
    query = select(WorkflowRun).where(WorkflowRun.id == run_id)
    run = db.scalar(query)
    if not run or run.restore_hold:
        raise PermissionError("Workflow missing or held for restore review")
    opportunity_query = select(Opportunity).where(Opportunity.id == run.opportunity_id)
    opportunity = db.scalar(opportunity_query)
    if lock and opportunity is not None:
        validate_provenance(db, opportunity, lock=True)
        opportunity = db.scalar(opportunity_query.with_for_update().execution_options(populate_existing=True))
        run = db.scalar(query.with_for_update().execution_options(populate_existing=True))
    if not run or run.restore_hold:
        raise PermissionError("Workflow missing or held for restore review")
    if run.graph_version != GRAPH_VERSION or run.policy_version != POLICY_VERSION:
        raise ValueError("Incompatible graph or eligibility policy version")
    if not opportunity or opportunity.lifecycle in {"purged", "expired"} or (opportunity.expires_at and opportunity.expires_at <= utcnow()):
        raise PermissionError("Workflow evidence is unavailable or expired")
    bound = run.budget.get("opportunity_version")
    if type(bound) is not int or bound != opportunity.version:
        raise PermissionError("Workflow content binding is missing or stale")
    if any(type(run.budget.get(key)) is not int or run.budget[key] != 0 for key in ("paid_calls", "limit")):
        raise PermissionError("P1 cost budget forbids paid calls")
    validate_provenance(db, opportunity)
    return run, opportunity


def current(run_id: UUID) -> tuple[WorkflowRun, Opportunity]:
    with session_factory()() as db:
        run, opportunity = _current_rows(db, run_id)
        db.expunge(run)
        db.expunge(opportunity)
        return run, opportunity


async def validate(state: AssessmentState) -> Command[Literal["mock", "__end__"]]:
    if state.get("graph_version") != GRAPH_VERSION or state.get("policy_version") != POLICY_VERSION:
        raise ValueError("Checkpoint version does not match current graph")
    run, opportunity = current(UUID(state["run_id"]))
    if type(state.get("opportunity_version")) is not int or state["opportunity_version"] != opportunity.version:
        raise PermissionError("Checkpoint content version is stale")
    if state.get("paid_calls", 0) != 0 or run.budget.get("paid_calls", 0) != 0 or run.budget.get("limit", 0) != 0:
        raise PermissionError("P1 cost budget forbids paid calls")
    if run.budget.get("mock_step_limit", 3) <= state.get("mock_steps", 0):
        with session_factory()() as db, db.begin():
            row, _ = _current_rows(db, run.id, lock=True)
            row.status, row.error = "budget_exhausted", "Synthetic step budget exhausted"
            row.updated_at = utcnow()
        return Command(update={"status": "budget_exhausted"}, goto="__end__")
    return Command(update={"opportunity_version": opportunity.version}, goto="mock")


async def mock(state: AssessmentState) -> Command[Literal["wait", "finish"]]:
    run, opportunity = current(UUID(state["run_id"]))
    mode = run.budget.get("mock_mode", "normal")
    if mode not in {"normal", "transient", "timeout", "error", "crash_after_consume"}:
        raise ValueError("Unsupported deterministic mock mode")
    with session_factory()() as db, db.begin():
        row, _ = _current_rows(db, run.id, lock=True)
        budget = dict(row.budget)
        budget["mock_attempts"] = int(budget.get("mock_attempts", 0)) + 1
        row.budget = budget
        attempt = budget["mock_attempts"]
    if mode == "transient" and attempt == 1:
        raise ConnectionError("Synthetic first-attempt mock failure")
    if mode == "timeout":
        await asyncio.sleep(5)
    if mode == "error":
        raise ValueError("Synthetic mock failure")
    # The mock returns deterministic metadata only. Listing text cannot become instructions.
    needs_review = opportunity.eligibility in {"Unknown", "Needs Review"}
    return Command(update={"mock_steps": state.get("mock_steps", 0) + 1, "paid_calls": 0,
                           "status": "review_required" if needs_review else "classified"},
                   goto="wait" if needs_review else "finish")


async def wait(state: AssessmentState) -> Command[Literal["approval"]]:
    run, _ = current(UUID(state["run_id"]))
    with session_factory()() as db, db.begin():
        row, _ = _current_rows(db, run.id, lock=True)
        if row.status not in {"waiting_review", "resume_requested", "approved", "completed"}:
            row.status, row.updated_at = "waiting_review", utcnow()
            row.version += 1
            audit(db, row.workspace_id, "workflow.interrupted", "workflow", row.id, {"external_dispatch": False})
    return Command(goto="approval")


async def approval(state: AssessmentState) -> Command[Literal["finish"]]:
    run, opportunity = current(UUID(state["run_id"]))
    expected_hash = decision_hash(run, opportunity)
    response = interrupt({"action": "continue-local-assessment", "run_id": str(run.id),
        "opportunity_version": opportunity.version, "payload_hash": expected_hash, "external_dispatch": False})
    if not isinstance(response, dict) or set(response) != {"decision_id"}:
        raise PermissionError("Invalid local review decision response")
    decision_id = UUID(str(response["decision_id"]))
    crash_mode = run.budget.get("mock_mode") == "crash_after_consume"
    if crash_mode and run.status not in {"approved", "completed"} and os.environ.get("KAJH_WORKER_FAULT_INJECTION") != "true":
        raise PermissionError("Process crash injection is not enabled")
    with session_factory()() as db, db.begin():
        applied = db.scalar(text("SELECT app.consume_workflow_decision(:run,:decision,:hash,:version)"),
            {"run": run.id, "decision": decision_id, "hash": expected_hash, "version": opportunity.version})
    if applied and crash_mode:
        os._exit(86)
    return Command(update={"status": "approved", "opportunity_version": opportunity.version}, goto="finish")


async def finish(state: AssessmentState) -> Command[Literal["__end__"]]:
    run, opportunity = current(UUID(state["run_id"]))
    with session_factory()() as db, db.begin():
        row, opportunity = _current_rows(db, run.id, lock=True)
        if type(state.get("opportunity_version")) is not int or state["opportunity_version"] != opportunity.version:
            raise PermissionError("Checkpoint content version is stale")
        if opportunity.eligibility in {"Unknown", "Needs Review"}:
            expected_hash = decision_hash(row, opportunity)
            decision = db.scalar(select(WorkflowDecision).where(WorkflowDecision.run_id == row.id,
                WorkflowDecision.consumed.is_(True), WorkflowDecision.decision == "approved",
                WorkflowDecision.content_version == opportunity.version,
                WorkflowDecision.payload_hash == expected_hash).order_by(WorkflowDecision.created_at.desc()).limit(1))
            if decision is None:
                raise PermissionError("A trusted consumed review decision is required before completion")
            db.scalar(text("SELECT app.consume_workflow_decision(:run,:decision,:hash,:version)"),
                {"run": row.id, "decision": decision.id, "hash": expected_hash, "version": opportunity.version})
        if row.status != "completed":
            row.status = "completed"
            row.updated_at = utcnow()
            row.version += 1
            audit(db, row.workspace_id, "workflow.completed", "workflow", row.id,
                  {"eligibility": opportunity.eligibility, "paid_calls": 0, "external_dispatch": False})
    return Command(update={"status": "completed", "paid_calls": 0}, goto="__end__")


async def recover(state: AssessmentState, error: NodeError) -> Command[Literal["__end__"]]:
    with session_factory()() as db, db.begin():
        row = db.get(WorkflowRun, UUID(state["run_id"]))
        if row and not row.restore_hold:
            row.status, row.error, row.updated_at = "error", type(error.error).__name__, utcnow()
            audit(db, row.workspace_id, "workflow.failed", "workflow", row.id,
                  {"error_type": row.error, "external_dispatch": False})
    return Command(update={"status": "error"}, goto="__end__")


def build_graph(saver):
    graph = StateGraph(AssessmentState)
    graph.add_node("validate", validate, error_handler=recover)
    graph.add_node("mock", mock, timeout=2,
        retry_policy=RetryPolicy(max_attempts=2, initial_interval=0.1, max_interval=0.2,
                                retry_on=(ConnectionError, NodeTimeoutError)), error_handler=recover)
    graph.add_node("wait", wait, error_handler=recover)
    graph.add_node("approval", approval, error_handler=recover)
    graph.add_node("finish", finish, error_handler=recover)
    graph.add_edge(START, "validate")
    return graph.compile(checkpointer=saver)


async def setup_checkpoints():
    no_external_authority()
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    async with await AsyncConnection.connect(checkpoint_url(), autocommit=True, row_factory=dict_row,
                                              prepare_threshold=None) as conn:
        await conn.execute("SET search_path TO checkpoints, public")
        await AsyncPostgresSaver(conn).setup()


async def run_workflow(run_id: UUID | str, decision_id: UUID | str | None = None) -> str:
    no_external_authority()
    run_id = UUID(str(run_id))
    run, opportunity = current(run_id)
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    async with await AsyncConnection.connect(checkpoint_url(), autocommit=True, row_factory=dict_row,
                                              prepare_threshold=None) as conn:
        lock = await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,42)) AS locked", (run.thread_id,))
        lock_row = await lock.fetchone()
        if not lock_row or not lock_row["locked"]:
            return "busy"
        try:
            run, opportunity = current(run_id)
            await conn.execute("SET search_path TO checkpoints, public")
            saver = AsyncPostgresSaver(conn)
            graph = build_graph(saver)
            config = {"configurable": {"thread_id": run.thread_id}}
            snapshot = await graph.aget_state(config)
            value: AssessmentState | Command | None
            if snapshot.values:
                expected = {"run_id": str(run.id), "workspace_id": str(run.workspace_id),
                    "graph_version": GRAPH_VERSION, "policy_version": POLICY_VERSION,
                    "opportunity_version": opportunity.version, "paid_calls": 0}
                if (type(snapshot.values.get("paid_calls")) is not int or
                        type(snapshot.values.get("opportunity_version")) is not int or
                        any(snapshot.values.get(key) != value for key, value in expected.items())):
                    raise PermissionError("Checkpoint metadata or content version is incompatible")
                if not snapshot.next:
                    status = snapshot.values.get("status")
                    if not isinstance(status, str) or status not in {"completed", "error", "budget_exhausted"} or status != run.status:
                        raise PermissionError("Terminal checkpoint does not match the durable workflow status")
                    return status
                if "approval" in snapshot.next:
                    if decision_id is None and run.status == "approved":
                        with session_factory()() as db:
                            decision = db.scalar(select(WorkflowDecision).where(WorkflowDecision.run_id == run.id,
                                WorkflowDecision.consumed.is_(True)).order_by(WorkflowDecision.created_at.desc()).limit(1))
                            decision_id = decision.id if decision else None
                    if decision_id is None:
                        return "waiting_review"
                    value = Command(resume={"decision_id": str(decision_id)})
                else:
                    value = None
            else:
                if decision_id is not None:
                    raise PermissionError("Cannot resume a workflow without a persisted interrupt")
                value = {"run_id": str(run.id), "workspace_id": str(run.workspace_id),
                    "graph_version": GRAPH_VERSION, "policy_version": POLICY_VERSION,
                    "opportunity_version": opportunity.version, "status": "queued", "paid_calls": 0, "mock_steps": 0}
            await graph.ainvoke(value, config, durability="sync")
            snapshot = await graph.aget_state(config)
            if "approval" in snapshot.next:
                return "waiting_review"
            return str(snapshot.values.get("status", "unknown"))
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,42))", (run.thread_id,))
