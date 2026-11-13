"""Evidence-preserving deterministic qualification; no AI or network calls."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

POLICY_VERSION = "kajh-eligibility-1.0"
ELIGIBLE = "Eligible"
INELIGIBLE = "Ineligible"
UNKNOWN = "Unknown"
REVIEW = "Needs Review"


def _countries(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {item.upper() for item in value if isinstance(item, str) and len(item) == 2}


def evaluate_eligibility(
    facts_or_record: Mapping[str, Any], vendor: Mapping[str, Any] | None = None,
    *, now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate explicit facts. Missing mandatory facts generate Unknown, never a pass.

    An empty requirement list means the source explicitly states no such requirement;
    omitted/null lists remain unknown. Countries need explicit coverage or a source
    statement that coverage is unrestricted. Vendor facts may be synthetic, but that
    label is always preserved and must not be represented as verified legal status.
    """
    record = dict(facts_or_record)
    facts = dict(record.get("facts") or record)
    terms = record.get("terms") or {}
    for key, value in terms.items():
        facts.setdefault(key, value)
    profile = dict(vendor or {})
    profile = dict(profile.get("data") or profile)
    legal = profile.get("legal_entity") or profile.get("legal_entity_details") or {}
    if not isinstance(legal, Mapping):
        legal = {}
    country = profile.get("entity_country") or profile.get("country") or profile.get("legal_country") or legal.get("country")
    incorporated = profile.get("incorporated", legal.get("incorporated"))
    delivery = _countries(profile.get("delivery_countries", profile.get("delivery_regions", [])))
    if country and isinstance(country, str):
        country = country.upper()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Eligibility evaluation time must have a time zone")
    rules: list[dict[str, Any]] = []
    evidence_map = facts.get("evidence") or record.get("evidence") or []

    def add(rule: str, outcome: str, reason: str, keys: list[str], mandatory: bool = True):
        evidence: list[dict[str, Any]] = [
            {"field": key, "value": facts.get(key), "source": "source_fact"}
            for key in keys if key in facts
        ]
        if isinstance(evidence_map, list):
            evidence += [dict(item) for item in evidence_map if isinstance(item, Mapping) and item.get("field") in keys]
        rules.append({
            "rule_id": rule, "rule": rule, "status": outcome, "outcome": outcome,
            "reason": reason, "evidence": evidence, "mandatory": mandatory,
        })

    def boolean_rule(rule: str, key: str, *, prohibited: bool = False):
        value = facts.get(key)
        if type(value) is not bool:
            add(rule, UNKNOWN, "Required fact is not established", [key])
        elif value == prohibited:
            add(rule, INELIGIBLE, "Explicit source restriction conflicts with B2B delivery", [key])
        else:
            add(rule, ELIGIBLE, "Explicit source fact satisfies the rule", [key])

    profile_countries = {str(value).upper() for value in (
        profile.get("entity_country"), profile.get("country"), profile.get("legal_country"), legal.get("country")
    ) if value}
    if len(profile_countries) > 1:
        add("vendor_profile_consistency", REVIEW, "Vendor profile contains conflicting legal countries", [])
    if profile.get("delivery_countries") is not None and profile.get("delivery_regions") is not None:
        if _countries(profile["delivery_countries"]) != _countries(profile["delivery_regions"]):
            add("vendor_delivery_consistency", REVIEW, "Vendor delivery-country fields conflict", [])

    boolean_rule("b2b_acceptance", "b2b_accepted")
    boolean_rule("employee_only", "employee_only", prohibited=True)
    boolean_rule("w2_only", "w2_only", prohibited=True)

    if facts.get("contract_type") == "c2c" or facts.get("requires_c2c") is True:
        boolean_rule("c2c", "c2c_accepted")
    else:
        add("c2c", ELIGIBLE, "A C2C-specific arrangement is not mandatory", ["contract_type"], False)

    required = facts.get("incorporated_vendor_required")
    if required is False:
        add("incorporated_vendor", ELIGIBLE, "No incorporated-entity requirement stated", ["incorporated_vendor_required"])
    elif required is True:
        add("incorporated_vendor", ELIGIBLE if incorporated is True else INELIGIBLE if incorporated is False else UNKNOWN,
            "Compare the explicit entity requirement with the vendor profile", ["incorporated_vendor_required"])
    else:
        add("incorporated_vendor", UNKNOWN, "Vendor entity requirement is missing", ["incorporated_vendor_required"])

    allowed = facts.get("eligible_vendor_countries")
    if facts.get("vendor_country_unrestricted") is True:
        add("vendor_jurisdiction", ELIGIBLE, "Source explicitly allows unrestricted vendor jurisdictions", ["vendor_country_unrestricted"])
    elif not _countries(allowed) or not country:
        add("vendor_jurisdiction", UNKNOWN, "Vendor jurisdiction or permitted countries are missing", ["eligible_vendor_countries"])
    else:
        add("vendor_jurisdiction", ELIGIBLE if country in _countries(allowed) else INELIGIBLE,
            "Vendor legal country compared with permitted countries", ["eligible_vendor_countries"])

    countries = facts.get("delivery_countries")
    if facts.get("delivery_worldwide") is True:
        add("delivery_geography", ELIGIBLE, "Source explicitly permits worldwide delivery", ["delivery_worldwide"])
    elif not _countries(countries) or not delivery:
        add("delivery_geography", UNKNOWN, "Remote does not establish delivery-country permission", ["delivery_countries"])
    else:
        add("delivery_geography", ELIGIBLE if _countries(countries) & delivery else INELIGIBLE,
            "Supported delivery countries compared with source restrictions", ["delivery_countries"])

    for rule, key, vendor_key in (
        ("residency", "residency_required", "residency_countries"),
        ("work_authorization", "work_authorization_required", "work_authorizations"),
    ):
        requirement = facts.get(key)
        if requirement == []:
            add(rule, ELIGIBLE, "Source explicitly states no requirement", [key])
        elif not isinstance(requirement, list):
            add(rule, UNKNOWN, "Requirement is not established", [key])
        elif len(_countries(requirement)) != len(set(str(value).upper() for value in requirement)):
            add(rule, UNKNOWN, "Required country codes are malformed", [key])
        else:
            available = _countries(profile.get(vendor_key))
            outcome = UNKNOWN if not available else ELIGIBLE if _countries(requirement) <= available else INELIGIBLE
            add(rule, outcome, "Required country evidence compared with vendor profile", [key])

    overlap = facts.get("required_overlap_hours")
    available_overlap = profile.get("available_overlap_hours")
    if overlap == 0:
        add("timezone_overlap", ELIGIBLE, "No mandatory overlap", ["required_overlap_hours"])
    elif isinstance(overlap, (int, float)) and not isinstance(overlap, bool) and 0 < overlap <= 24:
        if isinstance(available_overlap, (int, float)) and not isinstance(available_overlap, bool):
            add("timezone_overlap", ELIGIBLE if available_overlap >= overlap else INELIGIBLE,
                "Required overlap compared with explicitly available overlap", ["required_overlap_hours"])
        else:
            add("timezone_overlap", UNKNOWN, "Vendor overlap availability is missing", ["required_overlap_hours"])
    else:
        add("timezone_overlap", UNKNOWN, "Required time-zone overlap is missing or invalid", ["required_overlap_hours"])

    workplace = facts.get("workplace_type")
    if workplace == "remote":
        add("workplace", ELIGIBLE, "Remote delivery; geography is checked separately", ["workplace_type"])
    elif workplace in {"hybrid", "onsite"}:
        onsite_country = facts.get("onsite_country")
        allowed_onsite = _countries(profile.get("onsite_countries"))
        onsite_days = facts.get("onsite_days")
        max_days = profile.get("max_onsite_days")
        if (not onsite_country or not isinstance(onsite_days, (int, float))
                or not isinstance(max_days, (int, float)) or isinstance(onsite_days, bool)
                or isinstance(max_days, bool) or onsite_days < 0 or max_days < 0):
            add("workplace", UNKNOWN, "Mandatory onsite terms or availability missing", ["workplace_type", "onsite_country", "onsite_days"])
        else:
            passes = str(onsite_country).upper() in allowed_onsite and onsite_days <= max_days
            add("workplace", ELIGIBLE if passes else INELIGIBLE,
                "Mandatory attendance compared with vendor availability", ["workplace_type", "onsite_country", "onsite_days"])
    else:
        add("workplace", UNKNOWN, "Workplace type is not established", ["workplace_type"])

    requirements = facts.get("onboarding_required")
    if requirements == []:
        add("vendor_onboarding", ELIGIBLE, "No additional onboarding criteria stated", ["onboarding_required"])
    elif isinstance(requirements, list):
        onboard_available = profile.get("onboarding", profile.get("registrations"))
        if not isinstance(onboard_available, list):
            add("vendor_onboarding", UNKNOWN, "Vendor onboarding evidence missing", ["onboarding_required"])
        else:
            required_set = {str(value).casefold() for value in requirements}
            available_set = {str(value).casefold() for value in onboard_available}
            add("vendor_onboarding", ELIGIBLE if required_set <= available_set else INELIGIBLE,
                "Mandatory onboarding compared with supplied evidence", ["onboarding_required"])
    else:
        add("vendor_onboarding", UNKNOWN, "Vendor onboarding requirements missing", ["onboarding_required"])

    duration = facts.get("duration_months")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0:
        minimum = profile.get("min_duration_months", 0)
        maximum = profile.get("max_duration_months")
        if type(minimum) not in (int, float) or (maximum is not None and type(maximum) not in (int, float)):
            add("contract_duration", REVIEW, "Vendor duration preferences are malformed", ["duration_months"])
        else:
            passes = duration >= minimum and (maximum is None or duration <= maximum)
            add("contract_duration", ELIGIBLE if passes else INELIGIBLE,
                "Explicit duration compared with vendor preferences", ["duration_months"])
    else:
        add("contract_duration", UNKNOWN, "Contract duration missing", ["duration_months"])

    currency, unit = facts.get("currency"), facts.get("rate_unit")
    minimum, maximum = facts.get("rate_min"), facts.get("rate_max")
    if not currency or unit not in {"hour", "day", "week", "month", "project"} or minimum is None or maximum is None:
        add("compensation", UNKNOWN, "Comparable currency, rate bounds, and consulting unit required", ["currency", "rate_unit", "rate_min", "rate_max"])
    else:
        try:
            low, high = Decimal(str(minimum)), Decimal(str(maximum))
            if not low.is_finite() or not high.is_finite() or low < 0 or high < low:
                raise InvalidOperation
            rate_pref = profile.get("rate_preferences") or profile.get("rates") or {}
            if not isinstance(rate_pref, Mapping):
                rate_pref = {}
            rate_pref = dict(rate_pref)
            if profile.get("preferred_currency") is not None:
                rate_pref["currency"] = profile["preferred_currency"]
            if profile.get("minimum_rate") is not None:
                rate_pref["minimum"] = profile["minimum_rate"]
            if profile.get("rate_unit") is not None:
                rate_pref["unit"] = profile["rate_unit"]
            if rate_pref.get("currency") and (rate_pref.get("currency") != currency or rate_pref.get("unit", rate_pref.get("rate_unit")) != unit):
                add("compensation", UNKNOWN, "Rate currencies/units cannot be compared without an approved conversion", ["currency", "rate_unit"])
            elif rate_pref.get("minimum") is not None and high < Decimal(str(rate_pref["minimum"])):
                add("compensation", INELIGIBLE, "Entire disclosed rate is below configured vendor minimum", ["rate_min", "rate_max", "currency", "rate_unit"])
            else:
                add("compensation", ELIGIBLE, "Explicit rates and units are valid and comparable", ["rate_min", "rate_max", "currency", "rate_unit"])
        except (InvalidOperation, ValueError, TypeError):
            add("compensation", REVIEW, "Contradictory or malformed compensation", ["rate_min", "rate_max"])

    category = record.get("category", facts.get("category", "job"))
    if category == "subcontract":
        boolean_rule("subcontracting", "subcontracting_allowed")
    else:
        add("subcontracting", ELIGIBLE, "Not a subcontract opportunity", [], False)

    if category == "procurement":
        requirements = facts.get("procurement_requirements")
        if not isinstance(requirements, list):
            add("procurement", UNKNOWN, "Mandatory procurement criteria missing", ["procurement_requirements"])
        else:
            registrations = profile.get("registrations")
            if requirements and not isinstance(registrations, list):
                add("procurement", UNKNOWN, "Vendor procurement evidence missing", ["procurement_requirements"])
            else:
                passes = {str(item).casefold() for item in requirements} <= {str(item).casefold() for item in registrations or []}
                add("procurement", ELIGIBLE if passes else INELIGIBLE, "Notice-specific criteria compared with vendor registrations", ["procurement_requirements"])
    else:
        add("procurement", ELIGIBLE, "Not a procurement notice", [], False)

    closing = facts.get("closing_at", record.get("closing_at"))
    if facts.get("deadline_timezone_unknown"):
        add("closing_date", UNKNOWN, "Deadline lacks time-zone evidence", ["closing_at", "deadline_timezone_unknown"])
    elif closing:
        try:
            deadline = closing if isinstance(closing, datetime) else datetime.fromisoformat(str(closing).replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                raise ValueError
            add("closing_date", ELIGIBLE if deadline > now else INELIGIBLE, "Closing instant compared with evaluation time", ["closing_at"])
        except (ValueError, TypeError):
            add("closing_date", UNKNOWN, "Closing date is invalid or unzoned", ["closing_at"])
    elif facts.get("no_closing_date") is True:
        add("closing_date", ELIGIBLE, "Source explicitly states rolling/no closing date", ["no_closing_date"])
    else:
        add("closing_date", UNKNOWN, "Opportunity availability/closing date not established", ["closing_at"])

    if facts.get("confirmed_fraud") is True:
        add("authenticity", INELIGIBLE, "Confirmed fraud evidence", ["confirmed_fraud"])
    elif facts.get("suspicious_indicators"):
        add("authenticity", REVIEW, "Suspicious indicators require review, not a fraud conclusion", ["suspicious_indicators"])
    elif facts.get("confirmed_fraud") is False:
        add("authenticity", ELIGIBLE, "No confirmed fraud; this is not proof of legitimacy", ["confirmed_fraud"])
    else:
        add("authenticity", UNKNOWN, "Authenticity evidence missing", ["confirmed_fraud"])

    if facts.get("contradictions"):
        add("contradictions", REVIEW, "Material source evidence conflicts", ["contradictions"])

    mandatory = [rule["status"] for rule in rules if rule["mandatory"]]
    # Confirmed exclusions are never promoted by ambiguous favorable evidence.
    status = INELIGIBLE if INELIGIBLE in mandatory else REVIEW if REVIEW in mandatory else UNKNOWN if UNKNOWN in mandatory else ELIGIBLE
    return {
        "status": status, "eligibility": status, "rules": rules,
        "policy_version": POLICY_VERSION, "evaluated_at": now.astimezone(timezone.utc).isoformat(),
        "review_required": status in {UNKNOWN, REVIEW},
        "synthetic_profile": profile.get("synthetic") is True,
    }
