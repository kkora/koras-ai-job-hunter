from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class ReviewInput(Input):
    version: int = Field(ge=1)
    facts: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=5, max_length=2000)


class SavedInput(Input):
    saved: bool


class VendorInput(Input):
    version: int = Field(ge=1)
    profile: dict[str, Any]


class SearchInput(Input):
    name: str = Field(min_length=2, max_length=200)
    filters: dict[str, Any] = Field(default_factory=dict)


class SearchUpdate(SearchInput):
    version: int = Field(ge=1)


class SourceUpdate(Input):
    version: int = Field(ge=1)
    enabled: bool | None = None
    killed: bool | None = None
    permission: dict[str, Any] | None = None


class ResumeInput(Input):
    version: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=2000)
    decision: Literal["approved"]


class MergeInput(Input):
    target_id: str
    reason: str = Field(min_length=5, max_length=2000)


class SplitSourceInput(Input):
    version: int = Field(ge=1)
    source_link_id: str
    reason: str = Field(min_length=5, max_length=2000)


def bounded_object(value: dict[str, Any]) -> dict[str, Any]:
    import json
    if len(json.dumps(value)) > 64_000:
        raise ValueError("Configuration exceeds the 64 KB limit")
    forbidden = {"password", "api_key", "access_token", "refresh_token", "service_role_key", "secret"}
    def visit(item: Any, depth: int = 0) -> None:
        if depth > 8:
            raise ValueError("Configuration is nested too deeply")
        if isinstance(item, dict):
            for key, child in item.items():
                if key.lower() in forbidden:
                    raise ValueError("Credentials cannot be stored in business configuration")
                visit(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 500:
                raise ValueError("Configuration array is too large")
            for child in item:
                visit(child, depth + 1)
    visit(value)
    return value
