"""Apple resource adapters."""

from sideloadedipa.adapters.apple.app_groups import (
    app_group_association_verified,
    app_group_requirement,
)
from sideloadedipa.adapters.apple.asc import AscClient, AscResponse, AscToolIdentity
from sideloadedipa.adapters.apple.bundle_ids import (
    AscBundleIdGateway,
    BundleIdReconciler,
    bundle_id_requirement,
    exact_bundle_id_matches,
)
from sideloadedipa.adapters.apple.capabilities import (
    AscCapabilityGateway,
    CapabilityReconciler,
    capability_requirement,
    exact_capability_matches,
)
from sideloadedipa.adapters.apple.profiles import (
    AscProfileGateway,
    ProfileReconciler,
    ProfileReconciliationResult,
    ProfileSyncRequest,
    next_profile_name,
)
from sideloadedipa.adapters.apple.state import (
    AppleStateCollector,
    collect_bundle_identifiers,
    collect_profile,
    collect_profiles,
    decode_bundle_identifier_response,
    normalized_apple_state,
)

__all__ = [
    "AppleStateCollector",
    "AscBundleIdGateway",
    "AscCapabilityGateway",
    "AscClient",
    "AscProfileGateway",
    "AscResponse",
    "AscToolIdentity",
    "BundleIdReconciler",
    "CapabilityReconciler",
    "ProfileReconciler",
    "ProfileReconciliationResult",
    "ProfileSyncRequest",
    "app_group_association_verified",
    "app_group_requirement",
    "bundle_id_requirement",
    "capability_requirement",
    "collect_bundle_identifiers",
    "collect_profile",
    "collect_profiles",
    "decode_bundle_identifier_response",
    "exact_bundle_id_matches",
    "exact_capability_matches",
    "next_profile_name",
    "normalized_apple_state",
]
