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
from sideloadedipa.ipa.graph import LiefMachOProbe, MachOProbe, discover_bundle_graph

__all__ = [
    "ArchiveEntry",
    "ArchiveLimits",
    "EntitlementSliceEvidence",
    "LiefEntitlementInspector",
    "LiefMachOProbe",
    "MachOEntitlementEvidence",
    "MachOProbe",
    "decode_der_entitlements",
    "discover_bundle_graph",
    "extract_ipa_safely",
    "discover_root_app",
    "validate_archive_entries",
]
