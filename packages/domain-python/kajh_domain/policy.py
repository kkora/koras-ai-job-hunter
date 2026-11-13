"""Fail-closed source permissions. P1 never enables a real acquisition adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


class PolicyDenied(ValueError):
    pass


def validate_source_policy(
    source: Mapping[str, Any], permission: Mapping[str, Any] | None = None,
    *, purpose: str = "collect", now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    adapter = source.get("adapter_type", source.get("type"))
    if adapter != "synthetic":
        raise PolicyDenied("Real source activation is not authorized in P1")
    if source.get("enabled") is not True or source.get("killed", False) is not False:
        raise PolicyDenied("Source is disabled or its kill switch is active")
    policy = dict(permission if permission is not None else source.get("permission") or source)
    if policy.get("revoked") is not False:
        raise PolicyDenied("Source permission revocation state is not explicitly clear")
    if (("approved" in policy and policy["approved"] is not True) or
            ("status" in policy and policy["status"] != "approved")):
        raise PolicyDenied("Source approval evidence is contradictory or incomplete")
    if policy.get("approved") is not True and policy.get("status") != "approved":
        raise PolicyDenied("Source permission is not approved")
    expiry = policy.get("expires_at")
    try:
        expires = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
        if expires.tzinfo is None or expires <= now:
            raise ValueError
    except (ValueError, TypeError):
        raise PolicyDenied("Source permission is expired or lacks an explicit expiry") from None
    required = {
        "collect": ("commercial_use", "automated_collection", "private_display", "persistent_storage", "evidence_retention"),
        "ai": ("ai_processing",),
        "embeddings": ("ai_processing", "embeddings"),
    }
    if purpose not in required:
        raise PolicyDenied("Unsupported processing purpose")
    for key in required[purpose]:
        if policy.get(key) is not True:
            raise PolicyDenied(f"Permission not established for {key}")
    days = policy.get("retention_days")
    if type(days) is not int or not 1 <= days <= 3650:
        raise PolicyDenied("A bounded retention policy is required")
    quota = policy.get("quota_per_day")
    if type(quota) is not int or quota < 1:
        raise PolicyDenied("A positive quota is required")
    if not isinstance(policy.get("attribution"), str) or not policy["attribution"].strip():
        raise PolicyDenied("Attribution requirements must be recorded")
    return policy
