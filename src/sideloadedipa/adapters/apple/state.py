"""Read-only normalization of App Store Connect signing state."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
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
    if data is None:
        return ()
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


def decode_bundle_identifier(
    resource: Mapping[str, object], field: str
) -> AppleBundleIdentifierState:
    """Decode one bundleIds JSON:API resource from the pinned ASC contract."""

    resource_id, attributes = _resource(resource, field, "bundleIds")
    return AppleBundleIdentifierState(
        resource_id=resource_id,
        identifier=_string(attributes.get("identifier"), f"{field}.attributes.identifier"),
        name=_string(attributes.get("name"), f"{field}.attributes.name"),
        platform=_string(attributes.get("platform"), f"{field}.attributes.platform"),
        seed_id=_optional_string(attributes.get("seedId"), f"{field}.attributes.seedId"),
    )


def decode_bundle_identifier_response(
    response: AscResponse, field: str = "bundle_id"
) -> AppleBundleIdentifierState:
    document = _response_mapping(response, field)
    resource = _mapping(document.get("data"), f"{field}.data")
    return decode_bundle_identifier(resource, f"{field}.data")


def collect_bundle_identifiers(
    client: AscStateReader,
) -> tuple[AppleBundleIdentifierState, ...]:
    resources = _data_list(
        client.run_json(("bundle-ids", "list"), paginate=True),
        "bundle_ids",
    )
    values = tuple(
        decode_bundle_identifier(resource, f"bundle_ids.data[{index}]")
        for index, resource in enumerate(resources)
    )
    return tuple(sorted(values, key=lambda value: (value.identifier.casefold(), value.resource_id)))


def decode_capability(
    resource: Mapping[str, object], field: str, bundle_resource_id: str
) -> AppleCapabilityState:
    resource_id, attributes = _resource(resource, field, "bundleIdCapabilities")
    raw_settings = attributes.get("settings", [])
    if not isinstance(raw_settings, list):
        raise _invalid("capability settings must be an array", f"{field}.attributes.settings")
    settings = []
    for index, raw_setting in enumerate(raw_settings):
        frozen = freeze_json(raw_setting)
        if not isinstance(frozen, FrozenJsonObject):
            raise _invalid(
                "capability setting must be an object",
                f"{field}.attributes.settings[{index}]",
            )
        settings.append(frozen)
    return AppleCapabilityState(
        resource_id=resource_id,
        bundle_resource_id=bundle_resource_id,
        capability_type=_string(
            attributes.get("capabilityType"), f"{field}.attributes.capabilityType"
        ),
        settings=tuple(settings),
    )


def decode_capability_response(
    response: AscResponse, bundle_resource_id: str, field: str = "capability"
) -> AppleCapabilityState:
    document = _response_mapping(response, field)
    resource = _mapping(document.get("data"), f"{field}.data")
    return decode_capability(resource, f"{field}.data", bundle_resource_id)


def collect_capabilities(
    client: AscStateReader, bundle_resource_id: str
) -> tuple[AppleCapabilityState, ...]:
    resources = _data_list(
        client.run_json(
            ("bundle-ids", "capabilities", "list", "--bundle", bundle_resource_id),
            paginate=True,
        ),
        "capabilities",
    )
    values = tuple(
        decode_capability(resource, f"capabilities.data[{index}]", bundle_resource_id)
        for index, resource in enumerate(resources)
    )
    return tuple(sorted(values, key=lambda value: (value.capability_type, value.resource_id)))


def _decoded_content(value: object, field: str) -> bytes | None:
    encoded = _optional_string(value, field)
    if encoded is None:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise _invalid(f"{field} is not valid base64", field) from error


def _content_sha256(value: object, field: str) -> str | None:
    decoded = _decoded_content(value, field)
    return None if decoded is None else hashlib.sha256(decoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _ProfileAttributes:
    resource_id: str
    name: str
    platform: str | None
    profile_type: str
    profile_state: str | None
    uuid: str | None
    created_date: str | None
    expiration_date: str | None
    profile_sha256: str
    profile_content: bytes


def _decode_profile_attributes(
    resource: Mapping[str, object],
    field: str,
) -> _ProfileAttributes:
    resource_id, attributes = _resource(resource, field, "profiles")
    content_field = f"{field}.attributes.profileContent"
    profile_content = _decoded_content(attributes.get("profileContent"), content_field)
    if profile_content is None:
        raise _invalid(f"{content_field} must be a non-empty string", content_field)
    return _ProfileAttributes(
        resource_id=resource_id,
        name=_string(attributes.get("name"), f"{field}.attributes.name"),
        platform=_optional_string(attributes.get("platform"), f"{field}.attributes.platform"),
        profile_type=_string(attributes.get("profileType"), f"{field}.attributes.profileType"),
        profile_state=_optional_string(
            attributes.get("profileState"), f"{field}.attributes.profileState"
        ),
        uuid=_optional_string(attributes.get("uuid"), f"{field}.attributes.uuid"),
        created_date=_optional_string(
            attributes.get("createdDate"), f"{field}.attributes.createdDate"
        ),
        expiration_date=_optional_string(
            attributes.get("expirationDate"),
            f"{field}.attributes.expirationDate",
        ),
        profile_sha256=hashlib.sha256(profile_content).hexdigest(),
        profile_content=profile_content,
    )


def _relationship_ids(
    resource: Mapping[str, object],
    field: str,
    relationship_name: str,
    expected_type: str,
    *,
    many: bool,
) -> tuple[str, ...]:
    relationships = _mapping(resource.get("relationships"), f"{field}.relationships")
    relationship = _mapping(
        relationships.get(relationship_name),
        f"{field}.relationships.{relationship_name}",
    )
    data = relationship.get("data")
    values: list[object]
    if many:
        if data is None:
            return ()
        if not isinstance(data, list):
            raise _invalid(
                f"{field}.relationships.{relationship_name}.data must be an array",
                f"{field}.relationships.{relationship_name}.data",
            )
        values = data
    else:
        values = [data]
    identifiers = []
    for index, value in enumerate(values):
        linkage_field = f"{field}.relationships.{relationship_name}.data[{index}]"
        linkage = _mapping(value, linkage_field)
        resource_type = _string(linkage.get("type"), f"{linkage_field}.type")
        if resource_type != expected_type:
            raise _invalid(
                f"{linkage_field}.type must be {expected_type}",
                f"{linkage_field}.type",
            )
        identifiers.append(
            _string(
                linkage.get("id"),
                f"{linkage_field}.id",
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
        "profiles": [
            {
                "resource_id": value.resource_id,
                "name": value.name,
                "platform": value.platform,
                "profile_type": value.profile_type,
                "profile_state": value.profile_state,
                "uuid": value.uuid,
                "created_date": value.created_date,
                "expiration_date": value.expiration_date,
                "profile_sha256": value.profile_sha256,
                "bundle_resource_id": value.bundle_resource_id,
                "certificate_resource_ids": value.certificate_resource_ids,
                "device_resource_ids": value.device_resource_ids,
            }
            for value in snapshot.profiles
        ],
    }


def decode_profile_response(
    response: AscResponse,
    *,
    expected_resource_id: str | None = None,
    field: str = "profile",
) -> AppleProfileState:
    """Decode one included profile response from the pinned ASC contract."""

    document = _response_mapping(response, field)
    resource = _mapping(document.get("data"), f"{field}.data")
    attributes = _decode_profile_attributes(resource, f"{field}.data")
    if expected_resource_id is not None and attributes.resource_id != expected_resource_id:
        raise _invalid("profile detail ID differs from requested ID", f"{field}.data.id")
    bundle_ids = _relationship_ids(
        resource,
        f"{field}.data",
        "bundleId",
        "bundleIds",
        many=False,
    )
    if len(bundle_ids) != 1:
        raise _invalid(
            "profile must link to exactly one bundle ID",
            f"{field}.data.relationships.bundleId.data",
        )
    certificate_ids = _relationship_ids(
        resource,
        f"{field}.data",
        "certificates",
        "certificates",
        many=True,
    )
    device_ids = _relationship_ids(
        resource,
        f"{field}.data",
        "devices",
        "devices",
        many=True,
    )
    return AppleProfileState(
        resource_id=attributes.resource_id,
        name=attributes.name,
        platform=attributes.platform,
        profile_type=attributes.profile_type,
        profile_state=attributes.profile_state,
        uuid=attributes.uuid,
        created_date=attributes.created_date,
        expiration_date=attributes.expiration_date,
        profile_sha256=attributes.profile_sha256,
        bundle_resource_id=bundle_ids[0],
        certificate_resource_ids=certificate_ids,
        device_resource_ids=device_ids,
        profile_content=attributes.profile_content,
    )


def collect_profile(client: AscStateReader, resource_id: str) -> AppleProfileState:
    """Read one profile with attributes and relationship linkages inline."""

    return decode_profile_response(
        client.run_json(
            (
                "profiles",
                "view",
                "--id",
                resource_id,
                "--include",
                "bundleId,certificates,devices",
            )
        ),
        expected_resource_id=resource_id,
    )


def collect_profiles(client: AscStateReader) -> tuple[AppleProfileState, ...]:
    """Collect normalized iOS development profiles and their exact relationships."""

    resources = _data_list(
        client.run_json(
            ("profiles", "list", "--profile-type", "IOS_APP_DEVELOPMENT"),
            paginate=True,
        ),
        "profiles",
    )
    values = []
    for index, summary in enumerate(resources):
        attributes = _decode_profile_attributes(summary, f"profiles.data[{index}]")
        relationship_state = collect_profile(client, attributes.resource_id)
        if relationship_state.profile_sha256 != attributes.profile_sha256:
            raise _invalid(
                "profile list content differs from the included profile view",
                f"profiles.data[{index}].attributes.profileContent",
            )
        values.append(
            AppleProfileState(
                resource_id=attributes.resource_id,
                name=attributes.name,
                platform=attributes.platform,
                profile_type=attributes.profile_type,
                profile_state=attributes.profile_state,
                uuid=attributes.uuid,
                created_date=attributes.created_date,
                expiration_date=attributes.expiration_date,
                profile_sha256=attributes.profile_sha256,
                bundle_resource_id=relationship_state.bundle_resource_id,
                certificate_resource_ids=relationship_state.certificate_resource_ids,
                device_resource_ids=relationship_state.device_resource_ids,
                profile_content=attributes.profile_content,
            )
        )
    return tuple(sorted(values, key=lambda value: value.resource_id))


def normalized_apple_state(
    *,
    bundle_ids: tuple[AppleBundleIdentifierState, ...],
    capabilities: tuple[AppleCapabilityState, ...],
    certificates: tuple[AppleCertificateState, ...],
    devices: tuple[AppleDeviceState, ...],
    profiles: tuple[AppleProfileState, ...],
) -> AppleStateSnapshot:
    """Sort held slices consistently and bind the resulting snapshot by digest."""

    snapshot = AppleStateSnapshot(
        snapshot_sha256="",
        bundle_ids=tuple(
            sorted(
                bundle_ids,
                key=lambda value: (value.identifier.casefold(), value.resource_id),
            )
        ),
        capabilities=tuple(
            sorted(
                capabilities,
                key=lambda value: (
                    value.bundle_resource_id,
                    value.capability_type,
                    value.resource_id,
                ),
            )
        ),
        certificates=tuple(sorted(certificates, key=lambda value: value.resource_id)),
        devices=tuple(sorted(devices, key=lambda value: value.resource_id)),
        profiles=tuple(sorted(profiles, key=lambda value: value.resource_id)),
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


class AppleStateCollector:
    def __init__(self, client: AscStateReader) -> None:
        self.client = client

    def _bundle_ids(self) -> tuple[AppleBundleIdentifierState, ...]:
        return collect_bundle_identifiers(self.client)

    def _capabilities(
        self,
        bundle_ids: tuple[AppleBundleIdentifierState, ...],
        managed_bundle_identifiers: tuple[str, ...] | None,
    ) -> tuple[AppleCapabilityState, ...]:
        managed = (
            None
            if managed_bundle_identifiers is None
            else {identifier.casefold() for identifier in managed_bundle_identifiers}
        )
        values: list[AppleCapabilityState] = []
        for bundle in bundle_ids:
            if managed is not None and bundle.identifier.casefold() not in managed:
                continue
            values.extend(collect_capabilities(self.client, bundle.resource_id))
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
        return collect_profiles(self.client)

    def collect(
        self,
        *,
        managed_bundle_identifiers: tuple[str, ...] | None = None,
        bundle_ids: tuple[AppleBundleIdentifierState, ...] | None = None,
        capabilities: tuple[AppleCapabilityState, ...] | None = None,
        certificates: tuple[AppleCertificateState, ...] | None = None,
        devices: tuple[AppleDeviceState, ...] | None = None,
        profiles: tuple[AppleProfileState, ...] | None = None,
    ) -> AppleStateSnapshot:
        """Read and normalize one complete signing-resource snapshot."""

        bundle_values = self._bundle_ids() if bundle_ids is None else bundle_ids
        return normalized_apple_state(
            bundle_ids=bundle_values,
            capabilities=(
                self._capabilities(bundle_values, managed_bundle_identifiers)
                if capabilities is None
                else capabilities
            ),
            certificates=self._certificates() if certificates is None else certificates,
            devices=self._devices() if devices is None else devices,
            profiles=self._profiles() if profiles is None else profiles,
        )
