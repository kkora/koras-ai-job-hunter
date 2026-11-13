"""Pure mappings: unknown values stay unknown, commercial units stay explicit."""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

SKILL_ALIASES = {
    "powerapps": "Power Apps", "power apps": "Power Apps",
    "power automate": "Power Automate", "microsoft flow": "Power Automate",
    "power pages": "Power Pages", "power portals": "Power Pages",
    "power platform": "Power Platform", "dataverse": "Dataverse",
    "dynamics 365": "Dynamics 365", "d365": "Dynamics 365",
    "copilot studio": "Copilot Studio", "power virtual agents": "Copilot Studio",
    "azure": "Azure", "azure integration": "Azure Integration",
    "ai architecture": "AI Architecture", "ai agents": "AI Agents",
    "enterprise integration": "Enterprise Integration",
}
RATE_UNITS = {
    "hour": "hour", "hourly": "hour", "hr": "hour", "h": "hour",
    "day": "day", "daily": "day", "week": "week", "weekly": "week",
    "month": "month", "monthly": "month", "project": "project",
    "fixed": "project", "fixed-price": "project", "year": "year", "annual": "year",
}
CURRENCIES = {"USD", "CAD", "EUR", "GBP", "AUD", "NZD", "INR", "SGD", "CHF", "JPY", "AED", "SEK", "NOK", "DKK", "PLN", "BRL", "ZAR", "MXN"}
BOOLEAN_FACTS = {"b2b_accepted", "c2c_accepted", "w2_only", "employee_only", "incorporated_vendor_required",
    "requires_c2c", "vendor_country_unrestricted", "delivery_worldwide", "subcontracting_allowed", "confirmed_fraud"}


def boolean_claim_conflicts(record: Mapping[str, Any], facts: Mapping[str, Any], terms: Mapping[str, Any]) -> list[dict[str, Any]]:
    conflicts = []
    for field in sorted(BOOLEAN_FACTS):
        claims = [{"source": name, "value": mapping[field]} for name, mapping in
            (("facts", facts), ("terms", terms), ("record", record)) if type(mapping.get(field)) is bool]
        if {claim["value"] for claim in claims} == {True, False}:
            conflicts.append({"field": field, "claims": claims})
    return conflicts


def normalize_skills(values: Any) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,;]", values)
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = " ".join(value.strip().split())[:120]
        normalized = SKILL_ALIASES.get(text.casefold(), text)
        if normalized and normalized not in result:
            result.append(normalized)
    return result[:100]


def _number(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    # Avoid materializing an attacker-supplied exponent into an enormous string.
    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int) or abs(exponent) > 100 or abs(number.adjusted()) > 100:
        return None
    fixed = format(number, "f")
    # Decimal.normalize() applies the process precision context and can round
    # contractual amounts. Formatting preserves every supplied bounded digit.
    if "." in fixed:
        fixed = fixed.rstrip("0").rstrip(".")
    return "0" if number == 0 else fixed


def _timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    # A naive deadline must not be silently turned into UTC.
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _safe_reference(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http", "synthetic"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    # References are displayed as provenance, never fetched by this module.
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, parsed.query, ""))


def normalize_opportunity(
    raw: Mapping[str, Any], source_id: str | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("Opportunity payload must be an object")
    now = observed_at or datetime.now(timezone.utc)
    title_value = raw.get("title")
    title = " ".join(title_value.split())[:300] if isinstance(title_value, str) else ""
    if not title:
        raise ValueError("Opportunity title is required")
    description = html.unescape(str(raw.get("description", "")))[:100_000]
    # Remove active markup, preserving visible contractual text. React also renders text only.
    description = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", "", description)
    description = re.sub(r"<[^>]+>", " ", description)
    description = re.sub(r"[ \t]+", " ", description).strip()
    employer_value = raw.get("employer", raw.get("company", "Unknown organization"))
    if isinstance(employer_value, Mapping):
        employer = dict(employer_value)
    else:
        employer = {"name": str(employer_value)[:200], "domain": raw.get("employer_domain")}
    employer["name"] = str(employer.get("name", "Unknown organization"))[:200]
    if employer.get("domain"):
        employer["domain"] = str(employer["domain"]).lower().strip().removeprefix("www.")
    terms = dict(raw.get("terms") or {})
    facts = dict(raw.get("facts") or {})
    for field in (
        "b2b_accepted", "c2c_accepted", "w2_only", "employee_only",
        "incorporated_vendor_required", "eligible_vendor_countries", "delivery_countries",
        "residency_required", "work_authorization_required", "required_overlap_hours",
        "workplace_type", "onsite_country", "onsite_days", "onboarding_required",
        "duration_months", "subcontracting_allowed", "procurement_requirements",
        "confirmed_fraud", "suspicious_indicators", "contradictions",
        "contract_type", "requires_c2c", "vendor_country_unrestricted", "delivery_worldwide", "no_closing_date",
    ):
        if field in raw and field not in facts:
            facts[field] = raw[field]
        if field in terms and field not in facts:
            facts[field] = terms[field]
    conflicts = boolean_claim_conflicts(raw, facts, terms)
    if conflicts:
        existing = facts.get("contradictions")
        facts["contradictions"] = [*(existing if isinstance(existing, list) else []), *conflicts]
    for field in ("rate_min", "rate_max"):
        terms[field] = _number(terms.get(field, raw.get(field)))
    currency = terms.get("currency", raw.get("currency"))
    terms["currency"] = currency.upper() if isinstance(currency, str) and currency.upper() in CURRENCIES else None
    unit = terms.get("rate_unit", raw.get("rate_unit"))
    terms["rate_unit"] = RATE_UNITS.get(str(unit).lower()) if unit else None
    if terms["rate_min"] and terms["rate_max"] and Decimal(terms["rate_min"]) > Decimal(terms["rate_max"]):
        raise ValueError("Minimum rate exceeds maximum rate")
    # Annual employee compensation is retained as annual, never divided into a consulting rate.
    facts.update({key: terms[key] for key in ("rate_min", "rate_max", "currency", "rate_unit")})
    published = _timestamp(raw.get("published_at"))
    closing_value = raw.get("closing_at", facts.get("closing_at"))
    closing = _timestamp(closing_value)
    if closing_value is not None and closing is None:
        facts["deadline_timezone_unknown"] = True
    facts["closing_at"] = closing
    category = raw.get("category", "job")
    if category not in {"job", "consulting_project", "subcontract", "procurement"}:
        raise ValueError("Unsupported opportunity category")
    reference = _safe_reference(raw.get("source_ref", raw.get("original_url")))
    external_value = raw.get("external_id")
    if external_value is not None and (isinstance(external_value, bool) or not isinstance(external_value, (str, int))):
        raise ValueError("Source identity must be a string or integer")
    external_id = str(external_value).strip() if external_value is not None else ""
    # Bound the storage key without truncating distinct provider identities.
    if len(external_id) > 200:
        external_id = "id:" + hashlib.sha256(external_id.encode()).hexdigest()
    if not external_id:
        if not reference:
            raise ValueError("A source identity or original reference is required")
        external_id = "url:" + hashlib.sha256(reference.encode()).hexdigest()
    content = {
        "external_id": external_id, "title": title, "description": description,
        "employer": employer, "category": category, "skills": normalize_skills(raw.get("skills", [])),
        "terms": terms, "facts": facts, "source_ref": reference,
        "requisition_id": raw.get("requisition_id"), "published_at": published, "closing_at": closing,
        "evidence": list(raw.get("evidence") or []), "synthetic": raw.get("synthetic") is True,
    }
    content["content_hash"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    content["source_id"] = str(source_id) if source_id is not None else None
    content["observed_at"] = _timestamp(now)
    return content


def dedupe_key(record: Mapping[str, Any]) -> str | None:
    """Only explicit employer/requisition identity permits cross-source merging."""
    requisition = record.get("requisition_id")
    employer = record.get("employer") or {}
    domain = employer.get("domain") if isinstance(employer, Mapping) else None
    if not isinstance(requisition, str) or not requisition.strip() or not domain:
        return None
    identity = f"{str(domain).lower().strip()}|{requisition.strip()}|{record.get('category', 'job')}"
    return hashlib.sha256(identity.encode()).hexdigest()


def canonical_hash(record: Mapping[str, Any]) -> str:
    """Hash canonical content independently of source observation identity."""
    body = {key: record.get(key) for key in ("title", "description", "employer", "category", "skills", "facts", "terms", "requisition_id")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def vendor_hash(profile: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True, default=str).encode()).hexdigest()
