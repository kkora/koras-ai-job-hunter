from copy import deepcopy
from datetime import datetime, timezone

import pytest

from kajh_domain import DisabledAdapter, SyntheticAdapter, dedupe_key, normalize_opportunity, validate_source_policy
from kajh_domain.adapters import synthetic_opportunities, synthetic_permission
from kajh_domain.policy import PolicyDenied

NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)


def test_normalization_preserves_contract_text_and_removes_active_html():
    raw = synthetic_opportunities(NOW)[0]
    raw["description"] = "<p>B2B permitted &amp; incorporated only.</p><script>send()</script>"
    normalized = normalize_opportunity(raw, source_id="source-a", observed_at=NOW)
    assert "B2B permitted & incorporated only." in normalized["description"]
    assert "send()" not in normalized["description"]
    assert normalized["skills"] == ["Power Platform", "Dataverse", "Power Apps", "Azure Integration"]


def test_dollar_symbol_is_not_a_currency_and_annual_is_not_hourly():
    raw = synthetic_opportunities(NOW)[0]
    raw["terms"] = {"currency": "$", "rate_unit": "annual", "rate_min": "160,000", "rate_max": "190000"}
    terms = normalize_opportunity(raw, observed_at=NOW)["terms"]
    assert terms == {"currency": None, "rate_unit": "year", "rate_min": "160000", "rate_max": "190000"}


def test_no_timezone_is_not_silently_utc():
    raw = synthetic_opportunities(NOW)[0]
    raw["closing_at"] = "2026-12-01T17:00:00"
    normalized = normalize_opportunity(raw, observed_at=NOW)
    assert normalized["closing_at"] is None
    assert normalized["facts"]["deadline_timezone_unknown"] is True


def test_title_similarity_never_merges_distinct_requisitions():
    raw = synthetic_opportunities(NOW)[0]
    first = normalize_opportunity(raw, observed_at=NOW)
    second_raw = deepcopy(raw)
    second_raw["requisition_id"] = "OTHER-REQ"
    second = normalize_opportunity(second_raw, observed_at=NOW)
    assert first["title"] == second["title"]
    assert dedupe_key(first) != dedupe_key(second)
    second.pop("requisition_id")
    assert dedupe_key(second) is None


def test_same_requisition_with_distinct_observation_can_deduplicate():
    rows = synthetic_opportunities(NOW)
    a, b = normalize_opportunity(rows[0]), normalize_opportunity(rows[-1])
    assert a["external_id"] != b["external_id"]
    assert dedupe_key(a) == dedupe_key(b)


def test_invalid_rate_bounds_rejected():
    raw = synthetic_opportunities(NOW)[0]
    raw["terms"]["rate_min"] = 1000
    with pytest.raises(ValueError, match="Minimum rate"):
        normalize_opportunity(raw)


def test_source_reference_credentials_are_not_retained():
    raw = synthetic_opportunities(NOW)[0]
    raw["original_url"] = "https://user:secret@example.invalid/test"
    assert normalize_opportunity(raw)["source_ref"] is None


def test_cursor_pagination_has_no_missing_records():
    adapter = SyntheticAdapter(now=NOW)
    cursor, rows = None, []
    while True:
        page = adapter.collect(cursor, page_size=3)
        rows += page.items
        if page.exhausted:
            break
        cursor = page.next_cursor
    assert len(rows) == 14
    assert len({r["external_id"] for r in rows}) == 14
    assert adapter.collect("0", page_size=3).items == rows[:3]


@pytest.mark.parametrize("adapter", ["remote_ok", "greenhouse", "lever", "ashby"])
def test_real_adapters_have_no_acquisition_implementation(adapter):
    with pytest.raises(PermissionError):
        DisabledAdapter(adapter).collect()
    with pytest.raises(PolicyDenied):
        validate_source_policy({"adapter_type": adapter, "enabled": True}, synthetic_permission(NOW), now=NOW)


@pytest.mark.parametrize("change", [
    {"approved": False, "status": "pending"}, {"revoked": True},
    {"expires_at": "2026-01-01T00:00:00+00:00"}, {"expires_at": "2027-01-01"},
    {"commercial_use": False}, {"automated_collection": False},
    {"persistent_storage": False}, {"evidence_retention": False},
    {"retention_days": 0}, {"quota_per_day": 0}, {"attribution": ""},
])
def test_source_policy_fails_closed(change):
    permission = synthetic_permission(NOW)
    permission.update(change)
    with pytest.raises(PolicyDenied):
        validate_source_policy({"adapter_type": "synthetic", "enabled": True}, permission, now=NOW)


def test_ai_and_embedding_permissions_are_distinct_from_collection():
    source = {"adapter_type": "synthetic", "enabled": True}
    permission = synthetic_permission(NOW)
    assert validate_source_policy(source, permission, now=NOW)
    with pytest.raises(PolicyDenied):
        validate_source_policy(source, permission, purpose="ai", now=NOW)
    with pytest.raises(PolicyDenied):
        validate_source_policy(source, permission, purpose="embeddings", now=NOW)


@pytest.mark.parametrize("source", [
    {"adapter_type": "synthetic", "enabled": False},
    {"adapter_type": "synthetic", "enabled": True, "killed": True},
])
def test_source_kill_switch_blocks_collection(source):
    with pytest.raises(PolicyDenied):
        validate_source_policy(source, synthetic_permission(NOW), now=NOW)
