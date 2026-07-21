"""Read-only normalization of App Store Connect signing state."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import Protocol

from sideloadedipa.adapters.apple.asc import AscResponse
from sideloadedipa.domain import (
    AppleBundleIdentifierState,
    AppleCapabilityState,
    AppleCertificateState,
    AppleDeviceState,
    AppleProfileState,
    AppleStateSnapshot,
    FrozenJsonObject,
    freeze_json,
    thaw_json,
)
from sideloadedipa.errors import AdapterError, ErrorCode


class AscStateReader(Protocol):
    def run_json(
        self,
        args: tuple[str, ...],
        *,
        paginate: bool = False,
        allow_empty: bool = False,
    ) -> AscResponse: ...


def _invalid(message: str, field: str) -> AdapterError:
    return AdapterError(
        ErrorCode.ADAPTER_RESPONSE_INVALID,
        message,
        adapter="asc",
        operation="normalize-state",
        remediation="retry with the supported asc version and an unmodified JSON response",
        safe_details=(("field", field),),
    )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _invalid(f"{field} must be an object", field)
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(f"{field} must be a non-empty string", field)
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    return _string(value, field)


def _response_mapping(response: AscResponse, field: str) -> Mapping[str, object]:
    if response.document is None:
        raise _invalid(f"{field} response is empty", field)
    return _mapping(thaw_json(response.document), field)


def _data_list(response: AscResponse, field: str) -> tuple[Mapping[str, object], ...]:
    document = _response_mapping(response, field)
    data = document.get("data")
    if not isinstance(data, list):
        raise _invalid(f"{field}.data must be an array", f"{field}.data")
    return tuple(_mapping(item, f"{field}.data[{index}]") for index, item in enumerate(data))


def _resource(
    value: Mapping[str, object], field: str, expected_type: str
) -> tuple[str, Mapping[str, object]]:
    resource_type = _string(value.get("type"), f"{field}.type")
    if resource_type != expected_type:
        raise _invalid(f"{field}.type must be {expected_type}", f"{field}.type")
    resource_id = _string(value.get("id"), f"{field}.id")
    attributes = _mapping(value.get("attributes"), f"{field}.attributes")
    return resource_id, attributes


def _content_sha256(value: object, field: str) -> str | None:
    encoded = _optional_string(value, field)
    if encoded is None:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise _invalid(f"{field} is not valid base64", field) from error
    return hashlib.sha256(decoded).hexdigest()


def _relationship_ids(
    resource: Mapping[str, object], relationship: str, *, many: bool
) -> tuple[str, ...]:
    relationships = _mapping(resource.get("relationships"), "profile.data.relationships")
    relation = _mapping(
        relationships.get(relationship), f"profile.data.relationships.{relationship}"
    )
    data = relation.get("data")
    values: list[object]
    if many:
        if not isinstance(data, list):
            raise _invalid(
                f"profile relationship {relationship} must be an array",
                f"profile.data.relationships.{relationship}.data",
            )
        values = data
    else:
        values = [data]
    identifiers = []
    for index, value in enumerate(values):
        linkage = _mapping(
            value,
            f"profile.data.relationships.{relationship}.data[{index}]",
        )
        identifiers.append(
            _string(
                linkage.get("id"),
                f"profile.data.relationships.{relationship}.data[{index}].id",
            )
        )
    return tuple(sorted(identifiers))


def _snapshot_document(snapshot: AppleStateSnapshot) -> dict[str, object]:
    return {
        "schema_version": 1,
        "bundle_ids": [asdict(value) for value in snapshot.bundle_ids],
        "capabilities": [
            {
                "resource_id": value.resource_id,
                "bundle_resource_id": value.bundle_resource_id,
                "capability_type": value.capability_type,
                "settings": [thaw_json(setting) for setting in value.settings],
            }
            for value in snapshot.capabilities
        ],
        "certificates": [asdict(value) for value in snapshot.certificates],
        "devices": [asdict(value) for value in snapshot.devices],
        "profiles": [asdict(value) for value in snapshot.profiles],
    }


def canonical_apple_snapshot_json(snapshot: AppleStateSnapshot) -> bytes:
    """Serialize a redacted Apple state snapshot with its stable digest."""

    document = _snapshot_document(snapshot)
    document["snapshot_sha256"] = snapshot.snapshot_sha256
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


class AppleStateCollector:
    def __init__(self, client: AscStateReader) -> None:
        self.client = client

    def _bundle_ids(self) -> tuple[AppleBundleIdentifierState, ...]:
        resources = _data_list(
            self.client.run_json(("bundle-ids", "list"), paginate=True),
            "bundle_ids",
        )
        values = []
        for index, resource in enumerate(resources):
            resource_id, attributes = _resource(resource, f"bundle_ids.data[{index}]", "bundleIds")
            values.append(
                AppleBundleIdentifierState(
                    resource_id=resource_id,
                    identifier=_string(
                        attributes.get("identifier"),
                        f"bundle_ids.data[{index}].attributes.identifier",
                    ),
                    name=_string(
                        attributes.get("name"), f"bundle_ids.data[{index}].attributes.name"
                    ),
                    platform=_string(
                        attributes.get("platform"),
                        f"bundle_ids.data[{index}].attributes.platform",
                    ),
                    seed_id=_optional_string(
                        attributes.get("seedId"),
                        f"bundle_ids.data[{index}].attributes.seedId",
                    ),
                )
            )
        return tuple(sorted(values, key=lambda value: (value.identifier, value.resource_id)))

    def _capabilities(
        self, bundle_ids: tuple[AppleBundleIdentifierState, ...]
    ) -> tuple[AppleCapabilityState, ...]:
        values = []
        for bundle in bundle_ids:
            resources = _data_list(
                self.client.run_json(
                    ("bundle-ids", "capabilities", "list", "--bundle", bundle.resource_id),
                    paginate=True,
                ),
                "capabilities",
            )
            for index, resource in enumerate(resources):
                resource_id, attributes = _resource(
                    resource, f"capabilities.data[{index}]", "bundleIdCapabilities"
                )
                raw_settings = attributes.get("settings", [])
                if not isinstance(raw_settings, list):
                    raise _invalid(
                        "capability settings must be an array",
                        f"capabilities.data[{index}].attributes.settings",
                    )
                settings = []
                for setting_index, raw_setting in enumerate(raw_settings):
                    frozen = freeze_json(raw_setting)
                    if not isinstance(frozen, FrozenJsonObject):
                        raise _invalid(
                            "capability setting must be an object",
                            f"capabilities.data[{index}].attributes.settings[{setting_index}]",
                        )
                    settings.append(frozen)
                values.append(
                    AppleCapabilityState(
                        resource_id=resource_id,
                        bundle_resource_id=bundle.resource_id,
                        capability_type=_string(
                            attributes.get("capabilityType"),
                            f"capabilities.data[{index}].attributes.capabilityType",
                        ),
                        settings=tuple(settings),
                    )
                )
        return tuple(
            sorted(
                values,
                key=lambda value: (
                    value.bundle_resource_id,
                    value.capability_type,
                    value.resource_id,
                ),
            )
        )

    def _certificates(self) -> tuple[AppleCertificateState, ...]:
        resources = _data_list(
            self.client.run_json(
                (
                    "certificates",
                    "list",
                    "--certificate-type",
                    "IOS_DEVELOPMENT,DEVELOPMENT",
                ),
                paginate=True,
            ),
            "certificates",
        )
        values = []
        for index, resource in enumerate(resources):
            resource_id, attributes = _resource(
                resource, f"certificates.data[{index}]", "certificates"
            )
            values.append(
                AppleCertificateState(
                    resource_id=resource_id,
                    name=_string(
                        attributes.get("name"), f"certificates.data[{index}].attributes.name"
                    ),
                    certificate_type=_string(
                        attributes.get("certificateType"),
                        f"certificates.data[{index}].attributes.certificateType",
                    ),
                    display_name=_optional_string(
                        attributes.get("displayName"),
                        f"certificates.data[{index}].attributes.displayName",
                    ),
                    serial_number=_optional_string(
                        attributes.get("serialNumber"),
                        f"certificates.data[{index}].attributes.serialNumber",
                    ),
                    platform=_optional_string(
                        attributes.get("platform"),
                        f"certificates.data[{index}].attributes.platform",
                    ),
                    expiration_date=_optional_string(
                        attributes.get("expirationDate"),
                        f"certificates.data[{index}].attributes.expirationDate",
                    ),
                    certificate_sha256=_content_sha256(
                        attributes.get("certificateContent"),
                        f"certificates.data[{index}].attributes.certificateContent",
                    ),
                )
            )
        return tuple(sorted(values, key=lambda value: value.resource_id))

    def _devices(self) -> tuple[AppleDeviceState, ...]:
        resources = _data_list(
            self.client.run_json(
                ("devices", "list", "--platform", "IOS", "--status", "ENABLED"),
                paginate=True,
            ),
            "devices",
        )
        values = []
        for index, resource in enumerate(resources):
            resource_id, attributes = _resource(resource, f"devices.data[{index}]", "devices")
            udid = _string(attributes.get("udid"), f"devices.data[{index}].attributes.udid")
            values.append(
                AppleDeviceState(
                    resource_id=resource_id,
                    name=_string(attributes.get("name"), f"devices.data[{index}].attributes.name"),
                    platform=_string(
                        attributes.get("platform"),
                        f"devices.data[{index}].attributes.platform",
                    ),
                    status=_string(
                        attributes.get("status"), f"devices.data[{index}].attributes.status"
                    ),
                    device_class=_string(
                        attributes.get("deviceClass"),
                        f"devices.data[{index}].attributes.deviceClass",
                    ),
                    udid_sha256=hashlib.sha256(udid.encode()).hexdigest(),
                )
            )
        return tuple(sorted(values, key=lambda value: value.resource_id))

    def _profiles(self) -> tuple[AppleProfileState, ...]:
        resources = _data_list(
            self.client.run_json(
                ("profiles", "list", "--profile-type", "IOS_APP_DEVELOPMENT"),
                paginate=True,
            ),
            "profiles",
        )
        values = []
        for index, summary in enumerate(resources):
            profile_id, _ = _resource(summary, f"profiles.data[{index}]", "profiles")
            detail_document = _response_mapping(
                self.client.run_json(
                    (
                        "profiles",
                        "view",
                        "--id",
                        profile_id,
                        "--include",
                        "bundleId,certificates,devices",
                    )
                ),
                "profile",
            )
            detail = _mapping(detail_document.get("data"), "profile.data")
            detail_id, attributes = _resource(detail, "profile.data", "profiles")
            if detail_id != profile_id:
                raise _invalid("profile detail ID differs from list ID", "profile.data.id")
            bundle_ids = _relationship_ids(detail, "bundleId", many=False)
            certificate_ids = _relationship_ids(detail, "certificates", many=True)
            device_ids = _relationship_ids(detail, "devices", many=True)
            values.append(
                AppleProfileState(
                    resource_id=profile_id,
                    name=_string(attributes.get("name"), "profile.data.attributes.name"),
                    platform=_optional_string(
                        attributes.get("platform"), "profile.data.attributes.platform"
                    ),
                    profile_type=_string(
                        attributes.get("profileType"), "profile.data.attributes.profileType"
                    ),
                    profile_state=_optional_string(
                        attributes.get("profileState"), "profile.data.attributes.profileState"
                    ),
                    uuid=_optional_string(attributes.get("uuid"), "profile.data.attributes.uuid"),
                    created_date=_optional_string(
                        attributes.get("createdDate"), "profile.data.attributes.createdDate"
                    ),
                    expiration_date=_optional_string(
                        attributes.get("expirationDate"),
                        "profile.data.attributes.expirationDate",
                    ),
                    profile_sha256=_content_sha256(
                        attributes.get("profileContent"),
                        "profile.data.attributes.profileContent",
                    ),
                    bundle_resource_id=bundle_ids[0],
                    certificate_resource_ids=certificate_ids,
                    device_resource_ids=device_ids,
                )
            )
        return tuple(sorted(values, key=lambda value: value.resource_id))

    def collect(self) -> AppleStateSnapshot:
        """Read and normalize one complete signing-resource snapshot."""

        bundle_ids = self._bundle_ids()
        snapshot = AppleStateSnapshot(
            snapshot_sha256="",
            bundle_ids=bundle_ids,
            capabilities=self._capabilities(bundle_ids),
            certificates=self._certificates(),
            devices=self._devices(),
            profiles=self._profiles(),
        )
        digest = hashlib.sha256(
            json.dumps(_snapshot_document(snapshot), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return AppleStateSnapshot(
            snapshot_sha256=digest,
            bundle_ids=snapshot.bundle_ids,
            capabilities=snapshot.capabilities,
            certificates=snapshot.certificates,
            devices=snapshot.devices,
            profiles=snapshot.profiles,
        )
