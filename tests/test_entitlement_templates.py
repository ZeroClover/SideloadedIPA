"""Tests for restricted entitlement template loading and expansion."""

from __future__ import annotations

import plistlib
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.config import EntitlementTemplateContext, load_entitlement_template
from sideloadedipa.errors import ConfigurationError, ErrorCode


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / "configs" / "signing").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def context() -> EntitlementTemplateContext:
    return EntitlementTemplateContext(
        team_id="TEAM123456",
        app_identifier_prefix="PREFIX12345.",
        target_bundle_id="io.zeroclover.app.livecontainer",
        app_groups=(("shared", "group.io.zeroclover.livecontainer"),),
    )


def write_plist(
    repository: Path, name: str, value: object, *, binary: bool = False
) -> PurePosixPath:
    relative = PurePosixPath("configs/signing") / name
    path = repository / relative
    path.write_bytes(plistlib.dumps(value, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML))
    return relative


def test_expands_only_typed_placeholders_and_preserves_value_types(
    repository: Path, context: EntitlementTemplateContext
) -> None:
    relative = write_plist(
        repository,
        "root.plist",
        {
            "application-identifier": "${APP_IDENTIFIER_PREFIX}${TARGET_BUNDLE_ID}",
            "com.apple.developer.team-identifier": "${TEAM_ID}",
            "com.apple.security.application-groups": ["${APP_GROUP:shared}"],
            "get-task-allow": True,
            "nested": {"count": 2},
        },
        binary=True,
    )

    document = load_entitlement_template(repository, relative, context)

    assert document == {
        "application-identifier": "PREFIX12345.io.zeroclover.app.livecontainer",
        "com.apple.developer.team-identifier": "TEAM123456",
        "com.apple.security.application-groups": ["group.io.zeroclover.livecontainer"],
        "get-task-allow": True,
        "nested": {"count": 2},
    }


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"key": "${HOME}"}, "unknown"),
        ({"key": "${APP_GROUP:missing}"}, "unknown"),
        ({"key": "${TEAM_ID"}, "malformed"),
        ({"${TEAM_ID}": "value"}, "static strings"),
        ({"key": b"binary"}, "unsupported"),
    ],
)
def test_rejects_unknown_environment_and_type_changing_values(
    repository: Path,
    context: EntitlementTemplateContext,
    value: object,
    message: str,
) -> None:
    relative = write_plist(repository, "invalid.plist", value)

    with pytest.raises(ConfigurationError, match=message) as caught:
        load_entitlement_template(repository, relative, context)

    assert caught.value.code is ErrorCode.ENTITLEMENTS_TEMPLATE_INVALID


def test_rejects_parent_absolute_and_symlink_path_escape(
    repository: Path, context: EntitlementTemplateContext
) -> None:
    outside = repository / "outside.plist"
    outside.write_bytes(plistlib.dumps({"key": "value"}))
    link = repository / "configs" / "signing" / "link.plist"
    link.symlink_to(outside)

    for path in (
        PurePosixPath("configs/signing/../outside.plist"),
        PurePosixPath(outside.as_posix()),
        PurePosixPath("configs/signing/link.plist"),
    ):
        with pytest.raises(ConfigurationError) as caught:
            load_entitlement_template(repository, path, context)
        assert caught.value.code is ErrorCode.ENTITLEMENTS_TEMPLATE_PATH


def test_reports_missing_malformed_and_non_dictionary_templates(
    repository: Path, context: EntitlementTemplateContext
) -> None:
    with pytest.raises(ConfigurationError) as missing:
        load_entitlement_template(
            repository, PurePosixPath("configs/signing/missing.plist"), context
        )
    assert missing.value.code is ErrorCode.ENTITLEMENTS_TEMPLATE_MISSING

    malformed_path = repository / "configs" / "signing" / "malformed.plist"
    malformed_path.write_bytes(b"not a plist")
    with pytest.raises(ConfigurationError) as malformed:
        load_entitlement_template(
            repository, PurePosixPath("configs/signing/malformed.plist"), context
        )
    assert malformed.value.code is ErrorCode.ENTITLEMENTS_TEMPLATE_INVALID

    array = write_plist(repository, "array.plist", ["not", "a", "dictionary"])
    with pytest.raises(ConfigurationError, match="root must be a dictionary"):
        load_entitlement_template(repository, array, context)
