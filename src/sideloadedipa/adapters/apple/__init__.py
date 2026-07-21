"""Apple resource adapters."""

from sideloadedipa.adapters.apple.asc import AscClient, AscResponse, AscToolIdentity
from sideloadedipa.adapters.apple.bundle_ids import (
    AscBundleIdGateway,
    BundleIdReconciler,
    bundle_id_requirement,
    exact_bundle_id_matches,
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
    "AscClient",
    "AscResponse",
    "AscToolIdentity",
    "BundleIdReconciler",
    "bundle_id_requirement",
    "canonical_apple_snapshot_json",
    "collect_bundle_identifiers",
    "decode_bundle_identifier_response",
    "exact_bundle_id_matches",
]
