"""Apple resource adapters."""

from sideloadedipa.adapters.apple.asc import AscClient, AscResponse, AscToolIdentity
from sideloadedipa.adapters.apple.bundle_ids import (
    AscBundleIdGateway,
    BundleIdReconciler,
    bundle_id_requirement,
    exact_bundle_id_matches,
)
from sideloadedipa.adapters.apple.capabilities import (
    CAPABILITY_REGISTRY,
    AscCapabilityGateway,
    CapabilityAutomation,
    CapabilityReconciler,
    CapabilityRule,
    capability_requirement,
    capability_rule,
    exact_capability_matches,
)
from sideloadedipa.adapters.apple.state import (
    AppleStateCollector,
    canonical_apple_snapshot_json,
    collect_bundle_identifiers,
    decode_bundle_identifier_response,
)

__all__ = [
    "AppleStateCollector",
    "AscBundleIdGateway",
    "AscCapabilityGateway",
    "AscClient",
    "AscResponse",
    "AscToolIdentity",
    "BundleIdReconciler",
    "CAPABILITY_REGISTRY",
    "CapabilityAutomation",
    "CapabilityReconciler",
    "CapabilityRule",
    "bundle_id_requirement",
    "capability_requirement",
    "capability_rule",
    "canonical_apple_snapshot_json",
    "collect_bundle_identifiers",
    "decode_bundle_identifier_response",
    "exact_bundle_id_matches",
    "exact_capability_matches",
]
