"""Qualified zsign adapter with paired per-profile entitlement inputs."""

from __future__ import annotations

import hashlib
import plistlib
import tempfile
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import (
    CertificateMaterial,
    SigningBackendFeature,
    SigningBackendIdentity,
    SigningPlan,
    SigningResult,
    normalize_entitlements,
    thaw_json,
)
from sideloadedipa.errors import AdapterError, ErrorCode
from sideloadedipa.subprocesses import SubprocessRunner

EXPECTED_ZSIGN_VERSION = "1.1.1+sideloadedipa.1"
ZSIGN_CONTRACT_VERSION = "1"
_FEATURES = (
    SigningBackendFeature.PER_PROFILE_ENTITLEMENTS,
    SigningBackendFeature.RECURSIVE_SIGNING,
)


def _file_sha256(path: Path, *, operation: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise AdapterError(
            ErrorCode.ADAPTER_UNAVAILABLE,
            "required signing input could not be read",
            adapter="zsign",
            operation=operation,
            safe_details=(("input_name", path.name), ("os_error", type(error).__name__)),
        ) from error


def _resolved_below(root: Path, relative: PurePosixPath) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise AdapterError(
            ErrorCode.SIGNING_PLAN_INVALID,
            "planned profile path is not workspace-relative",
            adapter="zsign",
            operation="resolve-profile",
        )
    resolved_root = root.resolve()
    path = resolved_root.joinpath(*relative.parts).resolve()
    if not path.is_relative_to(resolved_root):
        raise AdapterError(
            ErrorCode.SIGNING_PLAN_INVALID,
            "planned profile path escapes profile storage",
            adapter="zsign",
            operation="resolve-profile",
        )
    return path


class ZsignBackend:
    def __init__(
        self,
        *,
        executable: Path,
        expected_executable_sha256: str,
        profile_root: Path,
        runner: SubprocessRunner | None = None,
        timeout_seconds: float = 180,
    ) -> None:
        self.executable = executable
        self.expected_executable_sha256 = expected_executable_sha256
        self.profile_root = profile_root
        self.runner = runner or SubprocessRunner(default_timeout_seconds=timeout_seconds)
        self.timeout_seconds = timeout_seconds

    def identity(self) -> SigningBackendIdentity:
        executable_sha256 = _file_sha256(self.executable, operation="verify-executable")
        if executable_sha256 != self.expected_executable_sha256:
            raise AdapterError(
                ErrorCode.ADAPTER_VERSION_MISMATCH,
                "zsign executable checksum does not match the qualified build",
                adapter="zsign",
                operation="verify-executable",
                safe_details=(("actual_sha256", executable_sha256),),
            )
        result = self.runner.run(
            [self.executable, "-v"],
            timeout_seconds=30,
            path_redactions=(self.executable,),
        )
        version = result.stdout.strip()
        if version != EXPECTED_ZSIGN_VERSION:
            raise AdapterError(
                ErrorCode.ADAPTER_VERSION_MISMATCH,
                "zsign version does not provide the qualified per-profile entitlement contract",
                adapter="zsign",
                operation="verify-version",
                safe_details=(
                    ("expected_version", EXPECTED_ZSIGN_VERSION),
                    ("actual_version", version),
                ),
            )
        return SigningBackendIdentity(
            "zsign",
            version,
            executable_sha256,
            ZSIGN_CONTRACT_VERSION,
            _FEATURES,
        )

    def sign(
        self,
        plan: SigningPlan,
        source_ipa: Path,
        output_ipa: Path,
        certificate: CertificateMaterial,
    ) -> SigningResult:
        identity = self.identity()
        if plan.backend != identity:
            raise AdapterError(
                ErrorCode.ADAPTER_VERSION_MISMATCH,
                "signing plan backend identity differs from the installed backend",
                adapter="zsign",
                operation="verify-plan",
                task_name=plan.task_name,
                safe_details=(("plan_sha256", plan.plan_sha256),),
            )
        if plan.certificate_sha256 != certificate.identity.certificate_sha256:
            raise AdapterError(
                ErrorCode.SIGNING_PLAN_INVALID,
                "signing plan certificate differs from the provided certificate material",
                adapter="zsign",
                operation="verify-certificate",
                task_name=plan.task_name,
            )

        profile_nodes = tuple(
            sorted(
                (node for node in plan.nodes if node.profile_resource_id is not None),
                key=lambda node: node.order,
            )
        )
        if not profile_nodes:
            raise AdapterError(
                ErrorCode.SIGNING_PLAN_INVALID,
                "signing plan contains no provisioning profile",
                adapter="zsign",
                operation="build-command",
                task_name=plan.task_name,
            )

        with tempfile.TemporaryDirectory(prefix="sideloadedipa-zsign-") as directory:
            entitlements_root = Path(directory)
            pairs: list[tuple[Path, Path]] = []
            for node in profile_nodes:
                if node.profile_path is None or node.profile_sha256 is None:
                    raise AdapterError(
                        ErrorCode.SIGNING_PLAN_INVALID,
                        "profile-bearing node has incomplete profile evidence",
                        adapter="zsign",
                        operation="build-command",
                        task_name=plan.task_name,
                        safe_details=(("bundle_path", node.source_path.as_posix()),),
                    )
                profile_path = _resolved_below(self.profile_root, node.profile_path)
                if _file_sha256(profile_path, operation="verify-profile") != node.profile_sha256:
                    raise AdapterError(
                        ErrorCode.SIGNING_PLAN_INVALID,
                        "provisioning profile content changed after planning",
                        adapter="zsign",
                        operation="verify-profile",
                        task_name=plan.task_name,
                        bundle_id=node.target_bundle_id,
                        safe_details=(("profile_resource_id", node.profile_resource_id),),
                    )
                document = {key: thaw_json(value) for key, value in node.expected_entitlements}
                normalized = normalize_entitlements(document)
                if normalized.sha256 != node.expected_entitlements_sha256:
                    raise AdapterError(
                        ErrorCode.SIGNING_PLAN_INVALID,
                        "planned entitlement content changed after planning",
                        adapter="zsign",
                        operation="verify-entitlements",
                        task_name=plan.task_name,
                        bundle_id=node.target_bundle_id,
                    )
                entitlement_path = entitlements_root / f"{node.order:04d}.plist"
                entitlement_path.write_bytes(
                    plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True)
                )
                entitlement_path.chmod(0o600)
                pairs.append((profile_path, entitlement_path))

            command: list[str | Path] = [
                self.executable,
                "-f",
                "-k",
                certificate.private_key_path,
                "-c",
                certificate.certificate_path,
            ]
            for profile_path, entitlement_path in pairs:
                command.extend(["-m", profile_path, "-e", entitlement_path])
            command.extend(["-o", output_ipa, source_ipa])
            private_paths = (
                self.executable,
                certificate.private_key_path,
                certificate.certificate_path,
                source_ipa,
                output_ipa,
                *(path for pair in pairs for path in pair),
            )
            try:
                result = self.runner.run(
                    command,
                    timeout_seconds=self.timeout_seconds,
                    path_redactions=private_paths,
                )
            except AdapterError as error:
                raise AdapterError(
                    error.code,
                    "zsign failed to execute the validated signing plan",
                    adapter="zsign",
                    operation="sign",
                    task_name=plan.task_name,
                    remediation="inspect the redacted backend evidence and keep the prior artifact",
                    safe_details=(
                        ("plan_sha256", plan.plan_sha256),
                        *error.safe_details,
                    ),
                ) from error

        output_sha256 = _file_sha256(output_ipa, operation="verify-output")
        return SigningResult(
            plan_sha256=plan.plan_sha256,
            output_path=PurePosixPath(output_ipa.name),
            output_sha256=output_sha256,
            backend=identity,
            nodes=(),
            duration_seconds=result.duration_seconds,
            backend_argv=result.argv,
        )
