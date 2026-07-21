"""Immutable domain types shared across the signing pipeline."""

from sideloadedipa.domain.apple import (
    AppleOperation,
    AppleResource,
    AppleResourceKind,
    AppleResourcePlan,
    CertificateIdentity,
    OperationDisposition,
    ProvisioningProfile,
)
from sideloadedipa.domain.bundle import BundleGraph, BundleNode, BundleNodeKind
from sideloadedipa.domain.common import Diagnostic, DiagnosticSeverity, FrozenJsonValue
from sideloadedipa.domain.config import (
    BundleRule,
    EntitlementMode,
    EntitlementPolicy,
    IdentifierStrategy,
    ProfileType,
    R2Config,
    SigningPolicy,
    SourceConfig,
    SourceKind,
    Task,
    UnknownProfileBundlePolicy,
)
from sideloadedipa.domain.pipeline import PipelineStage, PublicationResult, StageState, StageStatus
from sideloadedipa.domain.signing import (
    SigningBackendIdentity,
    SigningNodePlan,
    SigningNodeResult,
    SigningPlan,
    SigningResult,
)

__all__ = [
    "AppleOperation",
    "AppleResource",
    "AppleResourceKind",
    "AppleResourcePlan",
    "BundleGraph",
    "BundleNode",
    "BundleNodeKind",
    "BundleRule",
    "CertificateIdentity",
    "Diagnostic",
    "DiagnosticSeverity",
    "EntitlementMode",
    "EntitlementPolicy",
    "FrozenJsonValue",
    "IdentifierStrategy",
    "OperationDisposition",
    "PipelineStage",
    "ProfileType",
    "ProvisioningProfile",
    "PublicationResult",
    "R2Config",
    "SigningBackendIdentity",
    "SigningNodePlan",
    "SigningNodeResult",
    "SigningPlan",
    "SigningPolicy",
    "SigningResult",
    "SourceConfig",
    "SourceKind",
    "StageState",
    "StageStatus",
    "Task",
    "UnknownProfileBundlePolicy",
]
