"""No HTTP clients, scraping, or arbitrary fetch capability exists in these adapters."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol


@dataclass(frozen=True)
class AdapterPage:
    items: list[dict[str, Any]]
    next_cursor: str | None
    exhausted: bool


class SourceAdapter(Protocol):
    def collect(self, cursor: str | None = None, *, page_size: int = 4) -> AdapterPage: ...


class DisabledAdapter:
    """Explicit future integration boundary; deliberately has no network implementation."""

    def __init__(self, name: str):
        if name not in {"remote_ok", "lever", "greenhouse", "ashby"}:
            raise ValueError("Unsupported future adapter")
        self.name = name

    def collect(self, cursor: str | None = None, *, page_size: int = 4) -> AdapterPage:
        raise PermissionError(f"{self.name} is disabled: source entitlement E-02 remains open")


def synthetic_vendor_profile() -> dict[str, Any]:
    return {
        "synthetic": True, "verified": False,
        "legal_entity": {"name": "Synthetic Koras development vendor", "country": "US", "incorporated": True, "verified": False},
        "delivery_countries": ["US", "CA", "GB"],
        "delivery_regions": ["US", "CA", "GB"],
        "residency_countries": ["US"], "work_authorizations": ["US"],
        "available_overlap_hours": 6, "timezone": "America/New_York",
        "onsite_countries": ["US"], "max_onsite_days": 1,
        "onboarding": ["synthetic-insurance"],
        "registrations": ["synthetic-insurance", "synthetic-procurement-registration"],
        "certifications": [], "certification_evidence": [],
        "technical_capabilities": ["Power Platform", "Power Pages", "Dynamics 365", "Dataverse", "Copilot Studio", "Azure", "AI Architecture", "Enterprise Integration"],
        "rate_preferences": {"currency": "USD", "unit": "hour", "minimum": 100},
        "restrictions": ["Synthetic development profile; not verified legal or commercial qualifications"],
        "min_duration_months": 1, "max_duration_months": 24,
    }


def synthetic_permission(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    return {
        "approved": True, "status": "approved", "revoked": False,
        "commercial_use": True, "automated_collection": True,
        "private_display": True, "persistent_storage": True, "evidence_retention": True,
        "ai_processing": False, "embeddings": False,
        "retention_days": 30, "quota_per_day": 1000,
        "attribution": "Koras synthetic development fixtures — no real opportunity data",
        "expires_at": (now + timedelta(days=365)).isoformat(),
        "license_evidence": "Owner-created synthetic records under KAJH-P1-AUTH-001",
    }


def synthetic_opportunities(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    facts = {
        "b2b_accepted": True, "c2c_accepted": True, "contract_type": "sow",
        "employee_only": False, "w2_only": False,
        "incorporated_vendor_required": True,
        "eligible_vendor_countries": ["US", "CA"],
        "delivery_countries": ["US", "CA"],
        "residency_required": [], "work_authorization_required": [],
        "required_overlap_hours": 4, "workplace_type": "remote",
        "onboarding_required": [], "duration_months": 6,
        "confirmed_fraud": False, "suspicious_indicators": [],
        "closing_at": (now + timedelta(days=30)).isoformat(),
    }
    base: dict[str, Any] = {
        "synthetic": True, "external_id": "syn-power-platform-001", "requisition_id": "SYN-PP-001",
        "title": "Power Platform Solution Architect — synthetic SOW",
        "company": "Synthetic Northstar Consulting", "employer_domain": "northstar.example.invalid",
        "category": "consulting_project",
        "description": "Synthetic six-month B2B architecture engagement. No real client or recruiter. Deliver a Dataverse architecture and Power Platform roadmap.",
        "skills": ["power platform", "dataverse", "powerapps", "azure integration"],
        "terms": {"currency": "USD", "rate_min": "140", "rate_max": "180", "rate_unit": "hour"},
        "facts": facts, "published_at": (now - timedelta(days=1)).isoformat(),
        "closing_at": facts["closing_at"],
        "original_url": "https://example.invalid/fixtures/syn-power-platform-001",
        "evidence": [{"field": "b2b_accepted", "excerpt": "Synthetic fixture explicitly accepts incorporated B2B vendors", "source_url": "https://example.invalid/fixtures/syn-power-platform-001"}],
    }
    result = []

    def variant(identifier: str, title: str, changes: dict[str, Any], **overrides: Any):
        value = copy.deepcopy(base)
        value.update(external_id=identifier, requisition_id=identifier.upper(), title=title,
                     original_url=f"https://example.invalid/fixtures/{identifier}")
        value["facts"].update(changes)
        value.update(overrides)
        value["evidence"] = [{
            "field": "b2b_accepted",
            "excerpt": f"Synthetic fixture B2B acceptance: {value['facts'].get('b2b_accepted')}",
            "source_url": value["original_url"],
        }]
        result.append(value)

    result.append(copy.deepcopy(base))
    variant("syn-power-pages-002", "Power Pages Consultant — synthetic project", {}, skills=["power pages", "dataverse"])
    variant("syn-d365-w2-003", "Dynamics 365 Architect — synthetic W2-only", {"w2_only": True, "b2b_accepted": False}, skills=["d365"])
    variant("syn-copilot-unknown-004", "Copilot Studio Advisor — B2B terms unknown", {"b2b_accepted": None}, skills=["copilot studio", "ai agents"])
    variant("syn-azure-review-005", "Azure Integration Lead — conflicting synthetic terms", {"contradictions": ["Synthetic agency and client disagree about vendor acceptance"]}, skills=["azure", "enterprise integration"])
    variant("syn-au-only-006", "Dataverse Architect — Australia-only synthetic delivery", {"delivery_countries": ["AU"]}, skills=["dataverse"])
    variant("syn-hybrid-007", "Power Apps Lead — synthetic hybrid restriction", {"workplace_type": "hybrid", "onsite_country": "GB", "onsite_days": 3}, skills=["power apps"])
    variant("syn-expired-008", "AI Architecture SOW — closed synthetic engagement", {"closing_at": (now - timedelta(days=1)).isoformat()}, closing_at=(now - timedelta(days=1)).isoformat(), skills=["ai architecture"])
    variant("syn-subcontract-009", "Microsoft Partner Delivery — synthetic subcontract", {"subcontracting_allowed": True}, category="subcontract", skills=["power automate", "power platform"])
    variant("syn-procurement-010", "Enterprise Integration — synthetic RFQ", {"procurement_requirements": ["synthetic-procurement-registration"]}, category="procurement", skills=["enterprise integration", "azure"])
    variant("syn-risk-011", "Power Platform Project — suspicious synthetic payment", {"suspicious_indicators": ["Synthetic request for an upfront payment"]}, skills=["power platform"])
    variant("syn-rate-unknown-012", "Senior Dataverse Consultant — undisclosed synthetic currency", {}, terms={"currency": "$", "rate_min": "160", "rate_max": "180", "rate_unit": "hour"}, skills=["dataverse"])
    variant("syn-same-title-different-013", base["title"], {}, employer_domain="northstar.example.invalid")
    # Same explicit requisition represents another observation, not a title-only merge.
    duplicate = copy.deepcopy(base)
    duplicate["external_id"] = "syn-power-platform-001-syndicated"
    duplicate["original_url"] = "https://example.invalid/fixtures/syndicated-001"
    result.append(duplicate)
    return result


class SyntheticAdapter:
    def __init__(self, *, now: datetime | None = None):
        self.now = now or datetime.now(timezone.utc)

    def collect(self, cursor: str | None = None, *, page_size: int = 4) -> AdapterPage:
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("Page size must be between 1 and 100")
        try:
            offset = int(cursor or "0")
        except ValueError:
            raise ValueError("Invalid synthetic source cursor") from None
        if offset < 0:
            raise ValueError("Invalid synthetic source cursor")
        rows = synthetic_opportunities(self.now)
        if offset > len(rows):
            raise ValueError("Synthetic source cursor exceeds available records")
        end = min(offset + page_size, len(rows))
        return AdapterPage(rows[offset:end], str(end) if end < len(rows) else None, end >= len(rows))
