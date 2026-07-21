"""Typed task and signing-policy configuration values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class SourceKind(StrEnum):
    DIRECT_URL = "direct-url"
    GITHUB_RELEASE = "github-release"


class BatchPublicationPolicy(StrEnum):
    ATOMIC = "atomic"
    INDEPENDENT = "independent"


class IdentifierStrategy(StrEnum):
    PRESERVE_SOURCE_SUFFIX = "preserve-source-suffix"


class UnknownProfileBundlePolicy(StrEnum):
    ERROR = "error"


class ProfileType(StrEnum):
    IOS_APP_DEVELOPMENT = "IOS_APP_DEVELOPMENT"


class EntitlementMode(StrEnum):
    PROFILE = "profile"
    PRESERVE_SOURCE = "preserve-source"
    TEMPLATE = "template"


@dataclass(frozen=True, slots=True)
class SourceConfig:
    kind: SourceKind
    location: str
    release_glob: str | None = None
    use_prerelease: bool = False


@dataclass(frozen=True, slots=True)
class EntitlementPolicy:
    mode: EntitlementMode
    template_path: PurePosixPath | None = None
    allowed_drops: tuple[str, ...] = ()
    drop_rationale: str | None = None


@dataclass(frozen=True, slots=True)
class BundleRule:
    source_bundle_id: str
    entitlement_policy: EntitlementPolicy
    target_bundle_id: str | None = None
    role: str | None = None
    required_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SigningPolicy:
    id_strategy: IdentifierStrategy
    unknown_profile_bundles: UnknownProfileBundlePolicy
    profile_type: ProfileType
    app_groups: tuple[tuple[str, str], ...] = ()
    manual_app_group_associations: tuple[str, ...] = ()
    bundles: tuple[BundleRule, ...] = ()


@dataclass(frozen=True, slots=True)
class Task:
    task_name: str
    app_name: str
    bundle_id: str
    source: SourceConfig
    slug: str
    icon_path: str | None = None
    signing: SigningPolicy | None = None
    publication_enabled: bool = True


@dataclass(frozen=True, slots=True)
class R2Config:
    ipa_prefix: str = "apps"
    registry_key: str = "site/apps.json"
    public_base_url: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationConfig:
    batch_policy: BatchPublicationPolicy = BatchPublicationPolicy.ATOMIC


@dataclass(frozen=True, slots=True)
class TaskConfiguration:
    tasks: tuple[Task, ...]
    r2: R2Config = R2Config()
    publication: PublicationConfig = PublicationConfig()
