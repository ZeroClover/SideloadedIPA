"""Complete, redacted cache fingerprints for signing inputs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sideloadedipa.domain import (
    BundleGraph,
    FrozenJsonObject,
    FrozenJsonValue,
    ProfileResourceManifest,
    ProvisioningProfile,
    SigningPlan,
    SourceAsset,
    freeze_json,
    thaw_json,
)

CACHE_FINGERPRINT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ToolFingerprint:
    name: str
    version: str
    executable_sha256: str


@dataclass(frozen=True, slots=True)
class SigningCacheFingerprint:
    schema_version: int
    task_name: str
    components: tuple[tuple[str, FrozenJsonValue], ...]
    sha256: str


def _canonical_json(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def _component_document(
    *,
    source: SourceAsset,
    policy_sha256: str,
    graph: BundleGraph,
    entitlement_template_sha256: tuple[tuple[str, str], ...],
    resource_manifest: ProfileResourceManifest,
    profiles: tuple[ProvisioningProfile, ...],
    plan: SigningPlan,
    device_set_sha256: str,
    tools: tuple[ToolFingerprint, ...],
) -> dict[str, object]:
    return {
        "source": {
            "asset_id": source.asset_id,
            "name": source.name,
            "version": source.version,
            "published_at": source.published_at.isoformat() if source.published_at else None,
            "sha256": source.sha256,
            "source_url_sha256": hashlib.sha256(source.source_url.encode()).hexdigest(),
        },
        "policy_sha256": policy_sha256,
        "graph_sha256": graph.graph_sha256,
        "entitlement_templates": [
            {"path": path, "sha256": sha256} for path, sha256 in sorted(entitlement_template_sha256)
        ],
        "target_bundle_ids": sorted(
            node.target_bundle_id for node in plan.nodes if node.target_bundle_id is not None
        ),
        "apple_resources": {
            "snapshot_sha256": resource_manifest.snapshot_sha256,
            "manifest_sha256": resource_manifest.manifest_sha256,
            "profiles": [
                {
                    "target_bundle_id": entry.target_bundle_id,
                    "bundle_resource_id": entry.bundle_resource_id,
                    "profile_resource_id": entry.profile_resource_id,
                    "profile_sha256": entry.profile_sha256,
                    "certificate_resource_id": entry.certificate_resource_id,
                    "device_set_sha256": entry.device_set_sha256,
                    "expires_at": entry.expires_at.isoformat(),
                }
                for entry in sorted(
                    resource_manifest.entries, key=lambda value: value.target_bundle_id
                )
            ],
        },
        "decoded_profiles": [
            {
                "resource_id": profile.resource_id,
                "application_identifier": profile.application_identifier,
                "team_id": profile.team_id,
                "certificate_sha256": profile.certificate_sha256,
                "profile_sha256": profile.profile_sha256,
                "device_ids": sorted(profile.device_ids),
                "expires_at": profile.expires_at.isoformat(),
                "entitlements_sha256": hashlib.sha256(
                    _canonical_json({key: thaw_json(value) for key, value in profile.entitlements})
                ).hexdigest(),
            }
            for profile in sorted(profiles, key=lambda value: value.resource_id)
        ],
        "certificate_sha256": plan.certificate_sha256,
        "device_set_sha256": device_set_sha256,
        "signing_plan_sha256": plan.plan_sha256,
        "backend": {
            "name": plan.backend.name,
            "version": plan.backend.version,
            "executable_sha256": plan.backend.executable_sha256,
            "contract_version": plan.backend.contract_version,
            "features": sorted(value.value for value in plan.backend.features),
        },
        "tools": [
            {
                "name": tool.name,
                "version": tool.version,
                "executable_sha256": tool.executable_sha256,
            }
            for tool in sorted(tools, key=lambda value: value.name)
        ],
    }


def build_signing_cache_fingerprint(
    *,
    source: SourceAsset,
    policy_sha256: str,
    graph: BundleGraph,
    entitlement_template_sha256: tuple[tuple[str, str], ...],
    resource_manifest: ProfileResourceManifest,
    profiles: tuple[ProvisioningProfile, ...],
    plan: SigningPlan,
    device_set_sha256: str,
    tools: tuple[ToolFingerprint, ...],
) -> SigningCacheFingerprint:
    document = _component_document(
        source=source,
        policy_sha256=policy_sha256,
        graph=graph,
        entitlement_template_sha256=entitlement_template_sha256,
        resource_manifest=resource_manifest,
        profiles=profiles,
        plan=plan,
        device_set_sha256=device_set_sha256,
        tools=tools,
    )
    frozen = freeze_json(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise TypeError("cache fingerprint components must be an object")
    components = frozen.items
    digest_document = {
        "schema_version": CACHE_FINGERPRINT_SCHEMA_VERSION,
        "task_name": plan.task_name,
        "components": document,
    }
    return SigningCacheFingerprint(
        CACHE_FINGERPRINT_SCHEMA_VERSION,
        plan.task_name,
        components,
        hashlib.sha256(_canonical_json(digest_document)).hexdigest(),
    )


def canonical_cache_fingerprint_json(fingerprint: SigningCacheFingerprint) -> bytes:
    document = {
        "schema_version": fingerprint.schema_version,
        "task_name": fingerprint.task_name,
        "components": {key: thaw_json(value) for key, value in fingerprint.components},
    }
    expected = hashlib.sha256(_canonical_json(document)).hexdigest()
    if expected != fingerprint.sha256:
        raise ValueError("cache fingerprint digest is inconsistent with its components")
    document["sha256"] = fingerprint.sha256
    return _canonical_json(document)
