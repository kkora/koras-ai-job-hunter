from __future__ import annotations

from functools import lru_cache
import os

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    database_url: str
    auth_url: str = "http://auth:9999"
    auth_issuer: str = "http://127.0.0.1:59999"
    auth_jwt_secret: str = Field(min_length=32)
    web_origins: str = "http://127.0.0.1:3210,http://localhost:3210"
    session_max_age: int = Field(default=3600, ge=60, le=86400)
    environment: str = Field(default="local", validation_alias="APP_ENV")

    @field_validator("database_url")
    @classmethod
    def private_database(cls, value: str) -> str:
        url = make_url(value)
        if url.username != "kajh_api" or url.database != "kajh_dev":
            raise ValueError("API requires its restricted local database principal")
        if url.host not in {"db", "127.0.0.1", "localhost"}:
            raise ValueError("Only the isolated local database is permitted")
        return value

    @field_validator("auth_url")
    @classmethod
    def private_auth(cls, value: str) -> str:
        if value not in {"http://auth:9999", "http://127.0.0.1:59999", "http://localhost:59999"}:
            raise ValueError("Only the isolated local Auth component is permitted")
        return value

    @field_validator("environment")
    @classmethod
    def local_only(cls, value: str) -> str:
        if value != "local":
            raise ValueError("P1 is authorized for local development only")
        return value

    @property
    def origins(self) -> set[str]:
        return {origin.strip() for origin in self.web_origins.split(",") if origin.strip()}


@lru_cache
def settings() -> Settings:
    return Settings(database_url=os.environ["DATABASE_URL"], auth_jwt_secret=os.environ["AUTH_JWT_SECRET"])
