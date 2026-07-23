"""Copy-on-write signing execution with verification-gated promotion."""

from __future__ import annotations

import shutil
import stat
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import (
    CertificateMaterial,
    SigningPlan,
    SigningResult,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa.archive import extract_ipa_safely
from sideloadedipa.ports import SigningBackend
from sideloadedipa.signing.bundle_transform import (
    BundleIdentifierRewrite,
    rewrite_bundle_identifiers,
)
from sideloadedipa.util.atomics import file_sha256
from sideloadedipa.util.workspace import task_workspace

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_COPY_BUFFER_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class SigningExecutionResult:
    signing: SigningResult
    rewrites: tuple[BundleIdentifierRewrite, ...]


def _execution_error(
    plan: SigningPlan,
    message: str,
    *,
    details: tuple[tuple[str, str], ...] = (),
) -> DomainError:
    return DomainError(
        ErrorCode.SIGNING_VERIFICATION_FAILED,
        message,
        task_name=plan.task_name,
        remediation="inspect the signing and verification evidence before retrying",
        safe_details=details,
    )


def _zip_info(path: str, mode: int, *, is_directory: bool) -> zipfile.ZipInfo:
    name = f"{path.rstrip('/')}" + ("/" if is_directory else "")
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    file_type = stat.S_IFDIR if is_directory else stat.S_IFREG
    info.external_attr = (file_type | mode) << 16
    return info


def package_workspace_ipa(workspace: Path, destination: Path) -> None:
    """Create a deterministic IPA from a safely extracted workspace."""

    entries = sorted(workspace.rglob("*"), key=lambda item: item.relative_to(workspace).as_posix())
    with zipfile.ZipFile(
        destination,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for entry in entries:
            relative = entry.relative_to(workspace).as_posix()
            if entry.is_symlink():
                raise DomainError(
                    ErrorCode.WORKSPACE_INVALID,
                    "signing workspace contains a non-regular entry",
                    remediation="recreate the signing workspace from the validated source IPA",
                    safe_details=(("path", relative),),
                )
            mode = stat.S_IMODE(entry.stat().st_mode)
            if entry.is_dir():
                archive.writestr(_zip_info(relative, mode, is_directory=True), b"")
                continue
            if not entry.is_file():
                raise DomainError(
                    ErrorCode.WORKSPACE_INVALID,
                    "signing workspace contains a non-regular entry",
                    remediation="recreate the signing workspace from the validated source IPA",
                    safe_details=(("path", relative),),
                )
            info = _zip_info(relative, mode, is_directory=False)
            with entry.open("rb") as source, archive.open(info, "w", force_zip64=True) as output:
                shutil.copyfileobj(source, output, length=_COPY_BUFFER_BYTES)


def _validate_backend_result(
    plan: SigningPlan,
    result: SigningResult,
    output_ipa: Path,
) -> str:
    if not output_ipa.is_file():
        raise _execution_error(plan, "signing backend did not create its requested output")
    actual_sha256 = file_sha256(output_ipa)
    mismatches: list[tuple[str, str]] = []
    if result.plan_sha256 != plan.plan_sha256:
        mismatches.append(("backend_plan_sha256", result.plan_sha256))
    if result.backend != plan.backend:
        mismatches.append(("backend_identity", result.backend.name))
    if result.output_path != PurePosixPath(output_ipa.name):
        mismatches.append(("backend_output_path", result.output_path.as_posix()))
    if result.output_sha256 != actual_sha256:
        mismatches.append(("backend_output_sha256", result.output_sha256))
    planned_nodes = {node.source_path: node for node in plan.nodes}
    result_nodes = {node.source_path: node for node in result.nodes}
    if len(result_nodes) != len(result.nodes) or set(result_nodes) != set(planned_nodes):
        mismatches.append(
            (
                "backend_node_paths",
                ",".join(sorted(path.as_posix() for path in result_nodes)),
            )
        )
    else:
        for path, node in result_nodes.items():
            planned = planned_nodes[path]
            if len(node.signed_executable_sha256) != 64 or any(
                character not in "0123456789abcdef" for character in node.signed_executable_sha256
            ):
                mismatches.append(
                    (
                        f"backend_node_executable_sha256:{path.as_posix()}",
                        node.signed_executable_sha256,
                    )
                )
            if node.embedded_profile_sha256 != planned.profile_sha256:
                mismatches.append(
                    (
                        f"backend_node_profile_sha256:{path.as_posix()}",
                        node.embedded_profile_sha256 or "",
                    )
                )
            if node.signed_entitlements_sha256 != planned.expected_entitlements_sha256:
                mismatches.append(
                    (
                        f"backend_node_entitlements_sha256:{path.as_posix()}",
                        node.signed_entitlements_sha256,
                    )
                )
    if mismatches:
        raise _execution_error(
            plan,
            "signing backend result does not match the requested plan or output",
            details=tuple(mismatches),
        )
    return actual_sha256


def execute_signing_plan(
    *,
    plan: SigningPlan,
    source_ipa: Path,
    destination_ipa: Path,
    certificate: CertificateMaterial,
    backend: SigningBackend,
) -> SigningExecutionResult:
    """Sign an isolated copy and expose it after backend-evidence validation."""

    if source_ipa.resolve() == destination_ipa.resolve():
        raise _execution_error(plan, "source and destination IPA paths must be different")
    if file_sha256(source_ipa) != plan.source_ipa_sha256:
        raise _execution_error(plan, "source IPA digest does not match the signing plan")

    destination_ipa.parent.mkdir(parents=True, exist_ok=True)
    workspace_base = destination_ipa.parent / ".sideloadedipa-signing"
    remove_workspace_base = not workspace_base.exists()
    try:
        with task_workspace(workspace_base, plan.task_name) as workspace:
            shutil.copy2(source_ipa, workspace.source_ipa)
            if file_sha256(workspace.source_ipa) != plan.source_ipa_sha256:
                raise _execution_error(plan, "workspace source copy digest changed")

            extract_ipa_safely(workspace.source_ipa, workspace.extracted)
            rewrites = rewrite_bundle_identifiers(workspace.extracted, plan)
            prepared_ipa = workspace.root / "prepared.ipa"
            package_workspace_ipa(workspace.extracted, prepared_ipa)

            signing = backend.sign(plan, prepared_ipa, workspace.output_ipa, certificate)
            _validate_backend_result(plan, signing, workspace.output_ipa)

            workspace.output_ipa.replace(destination_ipa)
            promoted = replace(signing, output_path=PurePosixPath(destination_ipa.name))
            return SigningExecutionResult(promoted, rewrites)
    finally:
        if remove_workspace_base:
            try:
                workspace_base.rmdir()
            except OSError:
                pass
