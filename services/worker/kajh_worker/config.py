from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


def worker_url() -> str:
    value = os.environ.get("WORKER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("WORKER_DATABASE_URL is required")
    url = make_url(value)
    if url.username != "kajh_worker" or url.database != "kajh_dev":
        raise RuntimeError("Worker requires its restricted local database principal")
    if url.host not in {"db", "127.0.0.1", "localhost", "restore-db"}:
        raise RuntimeError("Worker database must be an owned local service")
    return value


def checkpoint_url() -> str:
    value = os.environ.get("CHECKPOINT_DATABASE_URL") or worker_url()
    url = make_url(value)
    if url.username != "kajh_worker" or url.database != "kajh_dev":
        raise RuntimeError("Checkpointing requires the restricted worker principal")
    if url.host not in {"db", "127.0.0.1", "localhost", "restore-db"}:
        raise RuntimeError("Checkpoint database must be local")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def session_factory():
    engine = create_engine(worker_url(), pool_pre_ping=True, pool_size=4, max_overflow=2, hide_parameters=True)
    return sessionmaker(engine, expire_on_commit=False)
