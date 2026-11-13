from __future__ import annotations

from functools import lru_cache
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import settings


@lru_cache
def engine() -> Engine:
    return create_engine(settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5, hide_parameters=True)


def bind_identity(session: Session, user_id: str, workspace_id: str) -> None:
    session.execute(text("SELECT set_config('app.user_id', :uid, true), set_config('app.workspace_id', :wid, true)"), {"uid": user_id, "wid": workspace_id})
