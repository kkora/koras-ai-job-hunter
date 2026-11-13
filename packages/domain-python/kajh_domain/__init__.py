"""Deterministic, network-free opportunity qualification shared by API and worker."""

from .adapters import DisabledAdapter, SyntheticAdapter, synthetic_permission, synthetic_vendor_profile
from .eligibility import POLICY_VERSION, evaluate_eligibility
from .normalization import dedupe_key, normalize_opportunity, normalize_skills
from .policy import PolicyDenied, validate_source_policy

__all__ = [
    "POLICY_VERSION", "DisabledAdapter", "PolicyDenied", "SyntheticAdapter",
    "dedupe_key", "evaluate_eligibility", "normalize_opportunity", "normalize_skills",
    "validate_source_policy",
    "synthetic_permission", "synthetic_vendor_profile",
]
