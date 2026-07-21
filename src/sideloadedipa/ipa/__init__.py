"""Safe IPA archive inspection utilities."""

from sideloadedipa.ipa.archive import (
    ArchiveEntry,
    ArchiveLimits,
    extract_ipa_safely,
    validate_archive_entries,
)
from sideloadedipa.ipa.discovery import discover_root_app
from sideloadedipa.ipa.entitlements import (
    EntitlementSliceEvidence,
    LiefEntitlementInspector,
    MachOEntitlementEvidence,
    decode_der_entitlements,
)
from sideloadedipa.ipa.graph import (
    EntitlementInspector,
    LiefMachOProbe,
    MachOProbe,
    canonical_graph_json,
    discover_bundle_graph,
    discover_bundle_structure,
)

__all__ = [
    "ArchiveEntry",
    "ArchiveLimits",
    "EntitlementSliceEvidence",
    "EntitlementInspector",
    "LiefEntitlementInspector",
    "LiefMachOProbe",
    "MachOEntitlementEvidence",
    "MachOProbe",
    "canonical_graph_json",
    "decode_der_entitlements",
    "discover_bundle_graph",
    "discover_bundle_structure",
    "extract_ipa_safely",
    "discover_root_app",
    "validate_archive_entries",
]
