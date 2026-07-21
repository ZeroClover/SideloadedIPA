"""Production package signing and verified publication commands."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from sideloadedipa.adapters.publication import R2PublicationGateway
from sideloadedipa.application import CommandRequest, CommandResult, OutputFormat
from sideloadedipa.config import load_configuration
from sideloadedipa.domain import (
    FrozenJsonObject,
    PublicationCandidate,
    Task,
    TaskConfiguration,
    freeze_json,
)
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.inspection import InspectDependencies, resolve_source
from sideloadedipa.ipa import read_ipa_metadata
from sideloadedipa.legacy.app_icon import IconError, build_icon_png
from sideloadedipa.legacy.r2_store import R2Store
from sideloadedipa.package_runner import run_package_signing
from sideloadedipa.publication import VerifiedPublicationService
from sideloadedipa.signing_service import PlannedSigningExecution
from sideloadedipa.sources import download_source_asset

_DEFAULT_REVALIDATE_URL = "https://itms.zeroclover.io/api/revalidate"


@dataclass(frozen=True, slots=True)
class PackageCommandDependencies:
    inspect: InspectDependencies = InspectDependencies()
    profile_root: Path = Path("work/profiles")
    output_root: Path = Path("work/signed")
    cache_root: Path = Path("work/cache")
    environment: Mapping[str, str] = field(default_factory=lambda: os.environ)


@dataclass(frozen=True, slots=True)
class _SignedTask:
    task: Task
    release_tag: str | None
    release_cache_entry: Mapping[str, object] | None
    source_sha256: str
    artifact_path: Path
    artifact_sha256: str
    result: PlannedSigningExecution


@contextmanager
def _redirect_progress(request: CommandRequest) -> Iterator[None]:
    if request.output_format is OutputFormat.JSON:
        with redirect_stdout(sys.stderr):
            yield
    else:
        yield


def _selected_tasks(
    request: CommandRequest, configuration: TaskConfiguration | None = None
) -> tuple[Task, ...]:
    configuration = configuration or load_configuration(request.config_path)
    available = {task.task_name: task for task in configuration.tasks}
    names = request.task_names or tuple(available)
    if len(set(names)) != len(names) or any(name not in available for name in names):
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "package signing task selection is invalid",
            remediation="select each configured task name at most once",
            safe_details=(("task_names", names),),
        )
    return tuple(available[name] for name in names)


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


def _safe_filename(value: str) -> str:
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return re.sub(r"_+", "_", filename).strip("._-") or "app"


def _trigger_revalidation(environment: Mapping[str, str]) -> bool:
    secret = _required(environment, "VERCEL_REVALIDATE_SECRET")
    endpoint = environment.get("VERCEL_REVALIDATE_URL", _DEFAULT_REVALIDATE_URL)
    parsed = urllib.parse.urlsplit(endpoint)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("secret", secret))
    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as response:
            status = int(response.status)
            return 200 <= status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def _publication_runtime(
    configuration: TaskConfiguration,
    environment: Mapping[str, str],
) -> tuple[R2Store, VerifiedPublicationService]:
    _required(environment, "VERCEL_REVALIDATE_SECRET")
    try:
        store = R2Store.from_env(
            configuration.r2.ipa_prefix,
            configuration.r2.registry_key,
            environment,
        )
    except RuntimeError as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_MISSING,
            "verified publication requires complete R2 credentials",
            remediation="provide the required R2_* values through CI secrets",
        ) from error
    gateway = R2PublicationGateway(store, lambda: _trigger_revalidation(environment))
    return store, VerifiedPublicationService(gateway, configuration.publication.batch_policy)


def _release_cache_entry(
    resolved_url: str,
    evidence: Mapping[str, object],
) -> Mapping[str, object] | None:
    version = evidence.get("release_tag")
    if not isinstance(version, str):
        return None
    return {
        "version": version,
        "published_at": evidence.get("published_at"),
        "download_url": resolved_url,
        "asset_id": evidence.get("asset_id"),
    }


def _update_release_cache(
    path: Path,
    values: tuple[_SignedTask, ...],
    *,
    now: datetime,
) -> bool:
    try:
        current = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(current, dict):
            current = {}
        tasks = current.get("tasks")
        if not isinstance(tasks, dict):
            tasks = {}
        for value in values:
            if value.release_cache_entry is not None:
                tasks[value.task.task_name] = dict(value.release_cache_entry)
        current["tasks"] = tasks
        current["last_updated"] = now.astimezone(timezone.utc).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(json.dumps(current, indent=2) + "\n")
        temporary.replace(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return True


def _sign_tasks(
    request: CommandRequest,
    tasks: tuple[Task, ...],
    dependencies: PackageCommandDependencies,
) -> tuple[_SignedTask, ...]:
    environment = dependencies.environment
    zsign = Path(_required(environment, "ZSIGN_BIN"))
    zsign_sha256 = _required(environment, "ZSIGN_SHA256")
    repository_root = request.config_path.resolve().parent.parent
    dependencies.output_root.mkdir(parents=True, exist_ok=True)
    signed: list[_SignedTask] = []
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
            release_tag = resolved.evidence.get("release_tag")
            signed.append(
                _SignedTask(
                    task,
                    release_tag if isinstance(release_tag, str) else None,
                    _release_cache_entry(resolved.url, resolved.evidence),
                    source.sha256,
                    destination,
                    _sha256(destination),
                    result,
                )
            )
    return tuple(signed)


def _task_report(value: _SignedTask, publication: object) -> dict[str, object]:
    result = value.result
    return {
        "task_name": value.task.task_name,
        "source_sha256": value.source_sha256,
        "graph_sha256": result.plan.graph_sha256,
        "plan_sha256": result.plan.plan_sha256,
        "artifact_sha256": value.artifact_sha256,
        "verification_report_sha256": result.execution.verification.report_sha256,
        "output_path": str(value.artifact_path),
        "publication": publication,
    }


def sign_command(
    request: CommandRequest,
    dependencies: PackageCommandDependencies = PackageCommandDependencies(),
) -> CommandResult:
    """Sign and verify selected package-engine tasks without publication."""

    with _redirect_progress(request):
        signed = _sign_tasks(request, _selected_tasks(request), dependencies)
    reports = [_task_report(value, "disabled") for value in signed]
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


def run_command(
    request: CommandRequest,
    dependencies: PackageCommandDependencies = PackageCommandDependencies(),
) -> CommandResult:
    """Sign, independently verify, and optionally atomically publish a task batch."""

    configuration = load_configuration(request.config_path)
    tasks = _selected_tasks(request, configuration)
    if request.publish:
        disabled = tuple(task.task_name for task in tasks if not task.publication_enabled)
        if disabled:
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                "selected tasks are not approved for publication",
                remediation="complete physical-device acceptance before enabling publication",
                safe_details=(("task_names", disabled),),
            )
    with _redirect_progress(request):
        signed = _sign_tasks(request, tasks, dependencies)
    publication_by_task: dict[str, object] = {value.task.task_name: "disabled" for value in signed}
    if request.publish:
        with _redirect_progress(request):
            store, publisher = _publication_runtime(configuration, dependencies.environment)
            candidates: list[PublicationCandidate] = []
            for value in signed:
                metadata = read_ipa_metadata(value.artifact_path)
                icon_url: str | None = None
                if value.task.icon_path is not None:
                    try:
                        png = build_icon_png(
                            value.task.icon_path,
                            value.task.source.location,
                            ref=value.release_tag,
                            ipa_path=value.artifact_path,
                        )
                        icon_url = store.upload_icon(value.task.slug, png)
                    except (BotoCoreError, ClientError, IconError, OSError):
                        icon_url = None
                result = value.result
                candidates.append(
                    PublicationCandidate(
                        value.task.task_name,
                        value.task.slug,
                        value.task.app_name,
                        metadata.bundle_id,
                        metadata.version,
                        f"{_safe_filename(value.task.app_name)}.ipa",
                        str(value.artifact_path),
                        value.artifact_sha256,
                        icon_url,
                        value.task.publication_enabled,
                        result.plan,
                        result.execution.verification,
                    )
                )
            published = publisher.publish(candidates, now=datetime.now(timezone.utc))
        publication_by_task = {
            result.task_name: {
                "status": "published",
                "artifact_key": result.artifact_key,
                "artifact_url": result.artifact_url,
                "registry_key": result.registry_key,
                "registry_sha256": result.registry_sha256,
                "stale_keys_removed": result.stale_keys_removed,
            }
            for result in published
        }
        _update_release_cache(
            dependencies.cache_root / "release-versions.json",
            signed,
            now=datetime.now(timezone.utc),
        )
    reports = [_task_report(value, publication_by_task[value.task.task_name]) for value in signed]
    document = {
        "schema_version": 1,
        "command": "run",
        "status": "passed",
        "task_count": len(reports),
        "tasks": reports,
    }
    frozen = freeze_json(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise TypeError("package run report root must be an object")
    return CommandResult(
        human_output=f"Package run: {len(reports)} passed",
        payload=frozen.items,
    )
