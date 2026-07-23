"""Tests for read-only Apple plans and explicitly applied synchronization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sideloadedipa.adapters.apple import ProfileReconciliationResult
from sideloadedipa.apple.commands import (
    AppleCommandDependencies,
    plan_command,
    sync_command,
)
from sideloadedipa.application import CommandName, CommandRequest, OutputFormat
from sideloadedipa.domain import (
    AppleBundleIdentifierState,
    AppleCapabilityState,
    AppleCertificateState,
    AppleProfileState,
    AppleStateSnapshot,
    CertificateIdentity,
    EntitlementMode,
    EntitlementPolicy,
    ProfileType,
    ProvisioningProfile,
    SigningPolicy,
    SourceConfig,
    SourceKind,
    Task,
    TaskConfiguration,
    thaw_json,
)
from sideloadedipa.errors import ConfigurationError
from sideloadedipa.signing.profile_storage import profile_relative_path

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
TEAM_ID = "TEAMID1234"
CERTIFICATE_SHA256 = "c" * 64


def task(*, manual_capability: bool = False, manual_app_group: bool = False) -> Task:
    signing = None
    if manual_capability or manual_app_group:
        from sideloadedipa.domain import BundleRule

        required_capability = "APP_GROUPS" if manual_app_group else "INCREASED_MEMORY_LIMIT"
        signing = SigningPolicy(
            app_groups=(("shared", "group.io.example.shared"),) if manual_app_group else (),
            manual_app_group_associations=("group.io.example.shared",) if manual_app_group else (),
            bundles=(
                BundleRule(
                    source_bundle_id="com.upstream.example",
                    target_bundle_id="io.example.app",
                    role="root",
                    required_capabilities=(required_capability,),
                    entitlement_policy=EntitlementPolicy(EntitlementMode.PROFILE),
                ),
            ),
        )
    return Task(
        task_name="Example",
        app_name="Example",
        bundle_id="io.example.app",
        source=SourceConfig(SourceKind.DIRECT_URL, "https://example.com/App.ipa"),
        slug="Example",
        signing=signing,
    )


def request(command: CommandName, *, apply: bool = False) -> CommandRequest:
    return CommandRequest(
        command=command,
        config_path=Path("configs/tasks.toml"),
        task_names=(),
        output_format=OutputFormat.JSON,
        apply=apply,
    )


def payload(result: object) -> dict[str, object]:
    from sideloadedipa.application import CommandResult

    assert isinstance(result, CommandResult)
    return {key: thaw_json(value) for key, value in result.payload}


@dataclass
class RecordingBackend:
    bundle_exists: bool = True
    profile_created: bool = True
    created_profile_state: AppleProfileState | None = None
    calls: list[str] = field(default_factory=list)
    collect_profile_inputs: list[tuple[AppleProfileState, ...] | None] = field(default_factory=list)
    ensure_profile_inputs: list[tuple[AppleProfileState, ...]] = field(default_factory=list)
    mutations: dict[str, int] = field(
        default_factory=lambda: {
            "apple": 0,
            "cache_success": 0,
            "signing": 0,
            "r2": 0,
            "registry": 0,
        }
    )

    def _bundle(self) -> AppleBundleIdentifierState:
        return AppleBundleIdentifierState(
            resource_id="BUNDLE_ONE",
            identifier="io.example.app",
            name="Example",
            platform="IOS",
            seed_id="PREFIX1234",
        )

    def collect(
        self,
        *,
        profiles: tuple[AppleProfileState, ...] | None = None,
    ) -> AppleStateSnapshot:
        self.calls.append("collect")
        self.collect_profile_inputs.append(profiles)
        bundles = (self._bundle(),) if self.bundle_exists else ()
        certificates = (
            AppleCertificateState(
                resource_id="CERT_ONE",
                name="Development",
                certificate_type="DEVELOPMENT",
                display_name=None,
                serial_number="1234ABCD",
                platform="IOS",
                expiration_date=None,
                certificate_sha256=CERTIFICATE_SHA256,
            ),
        )
        return AppleStateSnapshot(
            "snapshot",
            bundles,
            (),
            certificates,
            (),
            profiles if profiles is not None else (),
        )

    def resolve_certificate(self, snapshot: AppleStateSnapshot) -> CertificateIdentity:
        self.calls.append("resolve_certificate")
        return CertificateIdentity(
            resource_id="CERT_ONE",
            team_id=TEAM_ID,
            serial_number="1234ABCD",
            public_key_sha256="b" * 64,
            certificate_sha256=CERTIFICATE_SHA256,
            expires_at=NOW + timedelta(days=90),
        )

    def ensure_bundle(self, intent: object) -> AppleBundleIdentifierState:
        self.calls.append("ensure_bundle")
        self.mutations["apple"] += 1
        self.bundle_exists = True
        return self._bundle()

    def ensure_capability(
        self, *, bundle: AppleBundleIdentifierState, capability_type: str
    ) -> None:
        self.calls.append(f"ensure_capability:{capability_type}")
        self.mutations["apple"] += 1

    def ensure_profile(self, **kwargs: object) -> ProfileReconciliationResult:
        self.calls.append("ensure_profile")
        self.mutations["apple"] += 1
        profile_states = kwargs["profile_states"]
        assert isinstance(profile_states, tuple)
        self.ensure_profile_inputs.append(profile_states)
        content = b"validated mobileprovision fixture"
        profile = ProvisioningProfile(
            resource_id="PROFILE_ONE",
            name="Example Dev",
            profile_type=ProfileType.IOS_APP_DEVELOPMENT,
            bundle_id="io.example.app",
            application_identifier="PREFIX1234.io.example.app",
            team_id=TEAM_ID,
            certificate_sha256=CERTIFICATE_SHA256,
            device_ids=(),
            created_at=NOW,
            expires_at=NOW + timedelta(days=90),
            profile_sha256=hashlib.sha256(content).hexdigest(),
            path=profile_relative_path("Example", "io.example.app"),
            entitlements=(),
        )
        return ProfileReconciliationResult(
            profile,
            content,
            self.profile_created,
            (),
            self.created_profile_state,
        )


def dependencies(tmp_path: Path, backend: RecordingBackend, configured_task: Task):
    return AppleCommandDependencies(
        load=lambda _: TaskConfiguration((configured_task,)),
        backend=backend,
        profile_root=tmp_path / "profiles",
    )


@pytest.mark.parametrize(
    ("command", "handler"),
    [(CommandName.PLAN, plan_command), (CommandName.SYNC, sync_command)],
)
def test_dry_commands_have_no_mutation_channel(
    tmp_path: Path, command: CommandName, handler: object
) -> None:
    backend = RecordingBackend()
    deps = dependencies(tmp_path, backend, task())

    result = handler(request(command), deps)  # type: ignore[operator]
    document = payload(result)

    assert result.exit_code == 0
    assert document["apply"] is False
    assert backend.calls == ["collect", "resolve_certificate"]
    assert backend.mutations == {
        "apple": 0,
        "cache_success": 0,
        "signing": 0,
        "r2": 0,
        "registry": 0,
    }
    assert not deps.profile_root.exists()
    rendered = str(document)
    assert "mobileprovision fixture" not in rendered
    assert "UDID" not in rendered
    assert "Task Example (1 bundles)" in (result.human_output or "")
    assert "safe-automatic: profile Example Dev" in (result.human_output or "")


def test_apply_stops_before_profiles_when_manual_prerequisite_remains(tmp_path: Path) -> None:
    backend = RecordingBackend()

    result = sync_command(
        request(CommandName.SYNC, apply=True),
        dependencies(tmp_path, backend, task(manual_capability=True)),
    )
    document = payload(result)

    assert result.exit_code == 1
    assert document["status"] == "blocked"
    assert "ensure_profile" not in backend.calls
    assert not (tmp_path / "profiles").exists()
    assert "remediation:" in (result.human_output or "")


def test_reviewed_app_group_confirmation_is_recorded_as_no_op(tmp_path: Path) -> None:
    backend = RecordingBackend()

    result = plan_command(
        request(CommandName.PLAN),
        dependencies(tmp_path, backend, task(manual_app_group=True)),
    )
    document = payload(result)
    tasks = document["tasks"]
    assert isinstance(tasks, list)
    operations = tasks[0]["operations"]
    group_operation = next(
        operation for operation in operations if operation["resource_kind"] == "app-group"
    )

    assert result.exit_code == 0
    assert group_operation["disposition"] == "no-op"
    assert group_operation["existing_resource_id"] is None


@pytest.mark.parametrize("names", [("Example", "Example"), ("Unknown",)])
def test_task_selection_errors_precede_all_apple_io(tmp_path: Path, names: tuple[str, ...]) -> None:
    backend = RecordingBackend()
    deps = dependencies(tmp_path, backend, task())
    selected = request(CommandName.PLAN)
    selected = CommandRequest(
        command=selected.command,
        config_path=selected.config_path,
        task_names=names,
        output_format=selected.output_format,
    )

    with pytest.raises(ConfigurationError) as caught:
        plan_command(selected, deps)

    assert caught.value.code is not None
    assert backend.calls == []


def test_apply_stores_only_validated_profiles_and_redacted_manifest(tmp_path: Path) -> None:
    backend = RecordingBackend(bundle_exists=False)
    deps = dependencies(tmp_path, backend, task())

    result = sync_command(request(CommandName.SYNC, apply=True), deps)
    document = payload(result)

    assert result.exit_code == 0
    assert document["status"] == "applied"
    assert backend.calls.count("ensure_bundle") == 1
    assert backend.calls.count("ensure_profile") == 1
    manifest_files = list(deps.profile_root.rglob("resource-manifest.json"))
    profile_files = list(deps.profile_root.rglob("*.mobileprovision"))
    assert len(manifest_files) == 1
    assert len(profile_files) == 1
    assert profile_files[0].read_bytes() == b"validated mobileprovision fixture"
    manifest = manifest_files[0].read_text()
    assert "validated mobileprovision fixture" not in manifest
    assert "PREFIX1234.io.example.app" not in manifest
    assert "manifest:" in (result.human_output or "")
    assert backend.collect_profile_inputs == [None, (), (), None]
    assert backend.ensure_profile_inputs == [()]


def test_apply_reuses_profile_snapshot_when_profiles_do_not_mutate(tmp_path: Path) -> None:
    backend = RecordingBackend(profile_created=False)

    result = sync_command(
        request(CommandName.SYNC, apply=True),
        dependencies(tmp_path, backend, task()),
    )

    assert result.exit_code == 0
    assert backend.collect_profile_inputs == [None, (), ()]
    assert backend.ensure_profile_inputs == [()]


def test_apply_merges_created_profile_state_then_refreshes_final_snapshot(
    tmp_path: Path,
) -> None:
    created = AppleProfileState(
        resource_id="PROFILE_ONE",
        name="Example Dev",
        platform="IOS",
        profile_type=ProfileType.IOS_APP_DEVELOPMENT.value,
        profile_state="ACTIVE",
        uuid="UUID-PROFILE_ONE",
        created_date=NOW.isoformat(),
        expiration_date=(NOW + timedelta(days=90)).isoformat(),
        profile_sha256="d" * 64,
        bundle_resource_id="BUNDLE_ONE",
        certificate_resource_ids=("CERT_ONE",),
        device_resource_ids=(),
    )
    backend = RecordingBackend(created_profile_state=created)
    recorded: list[tuple[str, str]] = []
    deps = AppleCommandDependencies(
        load=lambda _: TaskConfiguration((task(),)),
        backend=backend,
        profile_root=tmp_path / "profiles",
        record_created_resource=lambda kind, resource_id: recorded.append((kind, resource_id)),
    )

    result = sync_command(request(CommandName.SYNC, apply=True), deps)

    assert result.exit_code == 0
    assert recorded == [("profile", created.resource_id)]
    assert backend.collect_profile_inputs == [None, (), (), None]
