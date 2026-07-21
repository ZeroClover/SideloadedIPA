"""Non-publishing package signing command composition."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sideloadedipa.application import CommandRequest, CommandResult
from sideloadedipa.config import load_configuration
from sideloadedipa.domain import FrozenJsonObject, SigningEngine, Task, freeze_json
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.inspection import InspectDependencies, resolve_source
from sideloadedipa.package_runner import run_package_signing
from sideloadedipa.sources import download_source_asset


@dataclass(frozen=True, slots=True)
class PackageCommandDependencies:
    inspect: InspectDependencies = InspectDependencies()
    profile_root: Path = Path("work/profiles")
    output_root: Path = Path("work/signed")
    environment: Mapping[str, str] = field(default_factory=lambda: os.environ)


def _selected_tasks(request: CommandRequest) -> tuple[Task, ...]:
    configuration = load_configuration(request.config_path)
    available = {task.task_name: task for task in configuration.tasks}
    names = request.task_names or tuple(available)
    if len(set(names)) != len(names) or any(name not in available for name in names):
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "package signing task selection is invalid",
            remediation="select each configured task name at most once",
            safe_details=(("task_names", names),),
        )
    tasks = tuple(available[name] for name in names)
    disabled = tuple(
        task.task_name for task in tasks if task.signing_engine is not SigningEngine.PACKAGE
    )
    if disabled:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "selected tasks have not enabled the package signing engine",
            remediation="complete parity review before changing the per-task engine",
            safe_details=(("task_names", disabled),),
        )
    return tasks


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key)
    if not value:
        raise ConfigurationError(
            ErrorCode.CONFIG_MISSING,
            f"package signing requires {key}",
            remediation=f"provide {key} through the local or CI secret environment",
        )
    return value


def _decode_p12(environment: Mapping[str, str], destination: Path) -> str:
    encoded = _required(environment, "APPLE_DEV_CERT_P12_ENCODED")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "APPLE_DEV_CERT_P12_ENCODED is not valid base64",
            remediation="replace the CI secret with the complete base64-encoded P12",
        ) from error
    destination.write_bytes(content)
    destination.chmod(0o600)
    return _required(environment, "APPLE_DEV_CERT_PASSWORD")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sign_command(
    request: CommandRequest,
    dependencies: PackageCommandDependencies = PackageCommandDependencies(),
) -> CommandResult:
    """Sign and verify selected package-engine tasks without publication."""

    tasks = _selected_tasks(request)
    environment = dependencies.environment
    zsign = Path(_required(environment, "ZSIGN_BIN"))
    zsign_sha256 = _required(environment, "ZSIGN_SHA256")
    repository_root = request.config_path.resolve().parent.parent
    dependencies.output_root.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="sideloadedipa-package-command-") as directory:
        private_root = Path(directory)
        p12_path = private_root / "certificate.p12"
        p12_password = _decode_p12(environment, p12_path)
        for task in tasks:
            task_root = private_root / task.slug
            task_root.mkdir()
            resolved = resolve_source(task, dependencies.inspect, environment.get("GITHUB_TOKEN"))
            source = download_source_asset(
                resolved.url,
                task_root / "source.ipa",
                expected_sha256=resolved.expected_sha256,
            )
            destination = dependencies.output_root / f"{task.slug}.ipa"
            destination.unlink(missing_ok=True)
            result = run_package_signing(
                task=task,
                source_ipa=source.path,
                destination_ipa=destination,
                profile_root=dependencies.profile_root,
                p12_path=p12_path,
                p12_password=p12_password,
                private_directory=task_root / "private",
                zsign_executable=zsign,
                zsign_sha256=zsign_sha256,
                repository_root=repository_root,
            )
            reports.append(
                {
                    "task_name": task.task_name,
                    "source_sha256": source.sha256,
                    "graph_sha256": result.plan.graph_sha256,
                    "plan_sha256": result.plan.plan_sha256,
                    "artifact_sha256": _sha256(destination),
                    "verification_report_sha256": (result.execution.verification.report_sha256),
                    "output_path": str(destination),
                    "publication": "disabled",
                }
            )
    document = {
        "schema_version": 1,
        "command": "sign",
        "status": "passed",
        "task_count": len(reports),
        "tasks": reports,
    }
    frozen = freeze_json(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise TypeError("package signing report root must be an object")
    return CommandResult(
        human_output=f"Package signing: {len(reports)} passed",
        payload=frozen.items,
    )
