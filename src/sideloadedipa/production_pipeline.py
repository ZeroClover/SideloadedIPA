"""Manifest-driven production orchestration for signing and publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from botocore.exceptions import BotoCoreError, ClientError

from sideloadedipa.adapters.apple import capability_rule
from sideloadedipa.apple_commands import (
    AppleCommandDependencies,
)
from sideloadedipa.apple_commands import plan_command as apple_plan_command
from sideloadedipa.apple_commands import sync_command as apple_sync_command
from sideloadedipa.apple_intents import derive_bundle_resource_intents
from sideloadedipa.application import CommandName, CommandRequest, CommandResult
from sideloadedipa.cache_decisions import (
    RebuildDecision,
    RebuildReason,
    TaskCacheRecord,
    build_cache_index,
    select_rebuilds,
)
from sideloadedipa.cache_fingerprint import (
    SigningCacheFingerprint,
    ToolFingerprint,
    build_signing_cache_fingerprint,
)
from sideloadedipa.cache_reuse import CachePrerequisiteState, revalidate_cached_artifact
from sideloadedipa.cache_store import SigningCacheStore
from sideloadedipa.cancellation import (
    SideEffectJournal,
    load_side_effect_journal,
    record_cancellation,
    write_side_effect_journal,
)
from sideloadedipa.config import load_configuration
from sideloadedipa.domain import (
    BundleGraph,
    FrozenJsonObject,
    PipelineStage,
    PublicationCandidate,
    PublicationResult,
    SigningPlan,
    SourceAsset,
    StageManifest,
    StageStatus,
    Task,
    VerificationResult,
    freeze_json,
    thaw_json,
)
from sideloadedipa.errors import (
    ConfigurationError,
    DomainError,
    ErrorCode,
    SideloadedIPAError,
)
from sideloadedipa.inspection import ResolvedSource, resolve_source
from sideloadedipa.ipa import read_ipa_metadata
from sideloadedipa.legacy.app_icon import IconError, build_icon_png
from sideloadedipa.manifest_store import FileStageManifestStore
from sideloadedipa.package_commands import (
    PackageCommandDependencies,
    _decode_p12,
    _publication_runtime,
    _required,
    _safe_filename,
)
from sideloadedipa.package_runner import inspect_source_graph, prepare_package_signing
from sideloadedipa.preflight import validate_signing_preflight
from sideloadedipa.run_reports import RunReport, TaskRunEvidence, write_run_report
from sideloadedipa.signing_reports import canonical_signing_report_json
from sideloadedipa.signing_service import (
    PackageSigningRequest,
    execute_package_signing,
    plan_package_signing,
    verify_package_artifact,
)
from sideloadedipa.sources import DownloadedSource
from sideloadedipa.stage_manifests import finish_stage, start_stage

_PROFILE_REFRESH_THRESHOLD = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class ProductionPipelineDependencies:
    package: PackageCommandDependencies = PackageCommandDependencies()
    apple: AppleCommandDependencies = AppleCommandDependencies()
    manifest_root: Path = Path("work/pipeline")
    report_root: Path = Path("work/reports")


@dataclass(frozen=True, slots=True)
class SourceContext:
    task: Task
    resolved: ResolvedSource
    downloaded: DownloadedSource
    source: SourceAsset
    graph: BundleGraph


@dataclass(frozen=True, slots=True)
class PreparedContext:
    source: SourceContext
    request: PackageSigningRequest
    fingerprint: SigningCacheFingerprint

    @property
    def plan(self):  # type: ignore[no-untyped-def]
        return plan_package_signing(self.request)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _json_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _payload_document(result: CommandResult) -> dict[str, object]:
    return {key: thaw_json(value) for key, value in result.payload}


def _result(command: str, document: dict[str, object], human_output: str) -> CommandResult:
    payload = {"schema_version": 1, "command": command, **document}
    frozen = freeze_json(payload)
    if not isinstance(frozen, FrozenJsonObject):
        raise TypeError("production pipeline command report root must be an object")
    return CommandResult(human_output=human_output, payload=frozen.items)


def _published_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _source_asset(resolved: ResolvedSource, downloaded: DownloadedSource) -> SourceAsset:
    evidence = resolved.evidence
    asset_id = evidence.get("asset_id")
    name = evidence.get("asset_name")
    version = evidence.get("release_tag")
    return SourceAsset(
        asset_id=str(asset_id) if asset_id is not None else downloaded.sha256[:16],
        name=name if isinstance(name, str) and name else downloaded.path.name,
        source_url=resolved.url,
        version=version if isinstance(version, str) and version else downloaded.sha256[:12],
        published_at=_published_at(evidence.get("published_at")),
        path=PurePosixPath(downloaded.path.name),
        sha256=downloaded.sha256,
    )


def _selected_tasks(request: CommandRequest) -> tuple[Task, ...]:
    configuration = load_configuration(request.config_path)
    available = {task.task_name: task for task in configuration.tasks}
    names = request.task_names or tuple(available)
    if len(set(names)) != len(names) or any(name not in available for name in names):
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "production pipeline task selection is invalid",
            remediation="select each configured task name at most once",
            safe_details=(("task_names", names),),
        )
    return tuple(available[name] for name in names)


def _policy_sha256(task: Task) -> str:
    return _json_digest(asdict(task))


def _template_digests(task: Task, repository_root: Path) -> tuple[tuple[str, str], ...]:
    if task.signing is None:
        return ()
    values: list[tuple[str, str]] = []
    for rule in task.signing.bundles:
        relative = rule.entitlement_policy.template_path
        if relative is None:
            continue
        path = repository_root.joinpath(*relative.parts)
        values.append((relative.as_posix(), _sha256_file(path)))
    return tuple(sorted(values))


def _device_set_sha256(request: PackageSigningRequest) -> str:
    return _json_digest(
        sorted(entry.device_set_sha256 for entry in request.profile_manifest.entries)
    )


def _fingerprint(
    source: SourceContext,
    request: PackageSigningRequest,
    repository_root: Path,
) -> SigningCacheFingerprint:
    plan = plan_package_signing(request)
    return build_signing_cache_fingerprint(
        source=source.source,
        policy_sha256=_policy_sha256(source.task),
        graph=source.graph,
        entitlement_template_sha256=_template_digests(source.task, repository_root),
        resource_manifest=request.profile_manifest,
        profiles=request.profiles,
        plan=plan,
        device_set_sha256=_device_set_sha256(request),
        tools=(
            ToolFingerprint(
                plan.backend.name,
                plan.backend.version,
                plan.backend.executable_sha256,
            ),
        ),
    )


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_cached_signing_report(
    *,
    plan: SigningPlan,
    record: TaskCacheRecord,
    cached_path: Path,
    retained_path: Path,
) -> str:
    try:
        payload = cached_path.read_bytes()
        report_sha256 = hashlib.sha256(payload).hexdigest()
        document = json.loads(payload)
        nodes = document["nodes"]
        if (
            report_sha256 != record.signing_report_sha256
            or document["task_name"] != plan.task_name
            or document["plan_sha256"] != plan.plan_sha256
            or document["output_sha256"] != record.artifact_sha256
            or not isinstance(nodes, list)
            or {value.get("source_path") for value in nodes if isinstance(value, dict)}
            != {value.source_path.as_posix() for value in plan.nodes}
            or any(
                not isinstance(value, dict) or not isinstance(value.get("backend_evidence"), dict)
                for value in nodes
            )
        ):
            raise TypeError
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise DomainError(
            ErrorCode.CACHE_REUSE_INVALID,
            "cached signing report is missing or inconsistent",
            task_name=plan.task_name,
            remediation="discard the cache hit and rebuild the task",
        ) from error
    _write_bytes_atomic(retained_path, payload)
    return report_sha256


def _write_source_selection(path: Path, resolved: ResolvedSource) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "url": resolved.url,
        "expected_sha256": resolved.expected_sha256,
        "evidence": dict(resolved.evidence),
        "advertised_size": resolved.advertised_size,
    }
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_source_selection(path: Path) -> ResolvedSource:
    try:
        document = json.loads(path.read_text())
        url = document["url"]
        expected_sha256 = document["expected_sha256"]
        evidence = document["evidence"]
        advertised_size = document["advertised_size"]
        if (
            not isinstance(url, str)
            or not url
            or (expected_sha256 is not None and not isinstance(expected_sha256, str))
            or not isinstance(evidence, dict)
            or (advertised_size is not None and not isinstance(advertised_size, int))
        ):
            raise TypeError
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "persisted source selection is missing or invalid",
            remediation="restart the pipeline with a new run ID",
        ) from error
    return ResolvedSource(url, expected_sha256, evidence, advertised_size)


class ProductionPipeline:
    def __init__(
        self,
        dependencies: ProductionPipelineDependencies = ProductionPipelineDependencies(),
        journal: SideEffectJournal | None = None,
    ) -> None:
        self.dependencies = dependencies
        self.journal = journal

    def _store(self, request: CommandRequest) -> FileStageManifestStore:
        return FileStageManifestStore(self.dependencies.manifest_root, request.run_id)

    def _cache(self) -> SigningCacheStore:
        return SigningCacheStore(self.dependencies.package.cache_root)

    def _require_signing_environment(self) -> None:
        environment = self.dependencies.package.environment
        for key in (
            "ZSIGN_BIN",
            "ZSIGN_SHA256",
            "APPLE_DEV_CERT_P12_ENCODED",
            "APPLE_DEV_CERT_PASSWORD",
        ):
            _required(environment, key)

    def _pending_cache(self, request: CommandRequest) -> SigningCacheStore:
        return SigningCacheStore(self._store(request).run_root / "pending-cache")

    def _source_path(self, request: CommandRequest, task: Task) -> Path:
        return self._store(request).task_root(task.task_name) / "source.ipa"

    def _source_selection_path(self, request: CommandRequest, task: Task) -> Path:
        return self._store(request).task_root(task.task_name) / "source-selection.json"

    def _signing_report_path(self, request: CommandRequest, task_name: str) -> Path:
        return self._store(request).task_root(task_name) / "signing-report.json"

    def _record_success(
        self,
        store: FileStageManifestStore,
        task_name: str,
        stage: PipelineStage,
        result_sha256: str,
        predecessor: StageManifest | None,
    ) -> StageManifest:
        existing = store.load(task_name, stage)
        if existing is not None:
            if (
                existing.status is StageStatus.SUCCEEDED
                and existing.result_sha256 == result_sha256
                and existing.predecessor_sha256
                == (predecessor.manifest_sha256 if predecessor is not None else None)
            ):
                return existing
            raise DomainError(
                ErrorCode.PIPELINE_TRANSITION_INVALID,
                "existing stage evidence differs from current inputs",
                task_name=task_name,
                remediation="use a new run ID after changing pipeline inputs",
                safe_details=(("stage", stage.value),),
            )
        running = start_stage(
            task_name=task_name,
            stage=stage,
            started_at=datetime.now(timezone.utc),
            input_sha256=predecessor.result_sha256 if predecessor is not None else None,
            predecessor=predecessor,
        )
        store.save(running)
        completed = finish_stage(
            running,
            status=StageStatus.SUCCEEDED,
            completed_at=datetime.now(timezone.utc),
            result_sha256=result_sha256,
        )
        store.save(completed)
        return completed

    def _record_failure(
        self,
        store: FileStageManifestStore,
        task_name: str,
        stage: PipelineStage,
        error: SideloadedIPAError,
        predecessor: StageManifest | None,
    ) -> None:
        if store.load(task_name, stage) is not None:
            return
        running = start_stage(
            task_name=task_name,
            stage=stage,
            started_at=datetime.now(timezone.utc),
            input_sha256=predecessor.result_sha256 if predecessor is not None else None,
            predecessor=predecessor,
        )
        store.save(running)
        store.save(
            finish_stage(
                running,
                status=StageStatus.FAILED,
                completed_at=datetime.now(timezone.utc),
                diagnostics=(error.to_diagnostic(),),
            )
        )

    def _require(
        self,
        store: FileStageManifestStore,
        task_name: str,
        stage: PipelineStage,
    ) -> StageManifest:
        manifest = store.load(task_name, stage)
        if manifest is None or manifest.status is not StageStatus.SUCCEEDED:
            raise DomainError(
                ErrorCode.PIPELINE_TRANSITION_INVALID,
                "required production predecessor manifest is missing or unsuccessful",
                task_name=task_name,
                remediation=f"complete the {stage.value} stage for this run first",
                safe_details=(("stage", stage.value),),
            )
        store.completed(task_name)
        return manifest

    def _resolve_source(self, request: CommandRequest, task: Task) -> SourceContext:
        dependencies = self.dependencies.package.inspect
        environment = self.dependencies.package.environment
        path = self._source_path(request, task)
        selection_path = self._source_selection_path(request, task)
        if path.exists():
            resolved = _read_source_selection(selection_path)
            digest = _sha256_file(path)
            if (
                resolved.expected_sha256 is not None
                and digest != resolved.expected_sha256.removeprefix("sha256:").lower()
            ):
                raise DomainError(
                    ErrorCode.SOURCE_DIGEST_MISMATCH,
                    "persisted run source differs from current reviewed digest",
                    task_name=task.task_name,
                )
            downloaded = DownloadedSource(path, path.stat().st_size, digest)
        else:
            resolved = resolve_source(task, dependencies, environment.get("GITHUB_TOKEN"))
            downloaded = dependencies.download(
                resolved.url,
                path,
                expected_sha256=resolved.expected_sha256,
            )
            _write_source_selection(selection_path, resolved)
        graph = inspect_source_graph(downloaded.path, task=task)
        return SourceContext(task, resolved, downloaded, _source_asset(resolved, downloaded), graph)

    def _inspect_contexts(self, request: CommandRequest) -> tuple[SourceContext, ...]:
        store = self._store(request)
        contexts: list[SourceContext] = []
        diagnostics: list[str] = []
        repository_root = request.config_path.resolve().parent.parent
        for task in _selected_tasks(request):
            source_manifest: StageManifest | None = None
            inventory_manifest: StageManifest | None = None
            try:
                context = self._resolve_source(request, task)
                source_manifest = self._record_success(
                    store,
                    task.task_name,
                    PipelineStage.SOURCE,
                    _json_digest(asdict(context.source)),
                    None,
                )
                inventory_manifest = self._record_success(
                    store,
                    task.task_name,
                    PipelineStage.INVENTORY,
                    context.graph.graph_sha256,
                    source_manifest,
                )
                preflight = validate_signing_preflight(
                    task,
                    context.graph,
                    repository_root=repository_root,
                    team_id="PREFLIGHTTEAM",
                    app_identifier_prefix="PREFLIGHTPREFIX.",
                )
                if not preflight.valid:
                    error = DomainError(
                        ErrorCode.SIGNING_PLAN_INVALID,
                        "current source inventory does not satisfy its signing policy",
                        task_name=task.task_name,
                        safe_details=(
                            (
                                "diagnostic_codes",
                                tuple(value.code for value in preflight.diagnostics),
                            ),
                        ),
                    )
                    self._record_failure(
                        store,
                        task.task_name,
                        PipelineStage.POLICY,
                        error,
                        inventory_manifest,
                    )
                    diagnostics.extend(
                        f"{task.task_name}:{value.code}" for value in preflight.diagnostics
                    )
                    continue
                self._record_success(
                    store,
                    task.task_name,
                    PipelineStage.POLICY,
                    _json_digest(
                        {"policy": _policy_sha256(task), "graph": context.graph.graph_sha256}
                    ),
                    inventory_manifest,
                )
                contexts.append(context)
            except SideloadedIPAError as error:
                stage = (
                    PipelineStage.SOURCE
                    if source_manifest is None
                    else (
                        PipelineStage.INVENTORY
                        if inventory_manifest is None
                        else PipelineStage.POLICY
                    )
                )
                predecessor = (
                    source_manifest if stage is PipelineStage.INVENTORY else inventory_manifest
                )
                self._record_failure(store, task.task_name, stage, error, predecessor)
                diagnostics.append(f"{task.task_name}:{error.code.value}")
        if diagnostics:
            raise DomainError(
                ErrorCode.SIGNING_PLAN_INVALID,
                "production preflight found blocking diagnostics",
                remediation="resolve every reported task diagnostic before Apple apply",
                safe_details=(("diagnostics", tuple(diagnostics)),),
            )
        return tuple(contexts)

    def inspect(self, request: CommandRequest) -> CommandResult:
        contexts = self._inspect_contexts(request)
        return _result(
            "inspect",
            {
                "status": "passed",
                "tasks": [
                    {
                        "task_name": value.task.task_name,
                        "source_sha256": value.source.sha256,
                        "graph_sha256": value.graph.graph_sha256,
                    }
                    for value in contexts
                ],
            },
            f"Production preflight: {len(contexts)} passed",
        )

    def plan(self, request: CommandRequest) -> CommandResult:
        contexts = self._inspect_contexts(request)
        store = self._store(request)
        apple_request = replace(
            request,
            command=CommandName.PLAN,
            apply=False,
            publish=False,
        )
        result = apple_plan_command(apple_request, self.dependencies.apple)
        if result.exit_code:
            raise DomainError(
                ErrorCode.APPLE_RESOURCE_CONFLICT,
                "Apple resource plan contains blocking prerequisites",
                remediation="complete the manual or blocked operations before apply",
            )
        digest = _json_digest(_payload_document(result))
        for context in contexts:
            policy = self._require(store, context.task.task_name, PipelineStage.POLICY)
            self._record_success(
                store,
                context.task.task_name,
                PipelineStage.RESOURCE_PLAN,
                _json_digest({"task": context.task.task_name, "plan": digest}),
                policy,
            )
        return result

    def sync(self, request: CommandRequest) -> CommandResult:
        contexts = self._inspect_contexts(request)
        store = self._store(request)
        for context in contexts:
            self._require(store, context.task.task_name, PipelineStage.RESOURCE_PLAN)
        apple_request = replace(
            request,
            command=CommandName.SYNC,
            publish=False,
        )
        apple_dependencies = self.dependencies.apple
        if self.journal is not None:
            apple_dependencies = replace(
                apple_dependencies,
                record_created_resource=self.journal.record_apple_resource,
            )
        result = apple_sync_command(apple_request, apple_dependencies)
        if result.exit_code:
            raise DomainError(
                ErrorCode.APPLE_RESOURCE_CONFLICT,
                "Apple resource synchronization did not reach an applied state",
            )
        digest = _json_digest(_payload_document(result))
        for context in contexts:
            predecessor = self._require(store, context.task.task_name, PipelineStage.RESOURCE_PLAN)
            self._record_success(
                store,
                context.task.task_name,
                PipelineStage.RESOURCE_APPLY,
                _json_digest({"task": context.task.task_name, "apply": digest}),
                predecessor,
            )
        return result

    @contextmanager
    def _prepared(
        self,
        request: CommandRequest,
        contexts: tuple[SourceContext, ...],
    ) -> Iterator[tuple[PreparedContext, ...]]:
        environment = self.dependencies.package.environment
        zsign = Path(_required(environment, "ZSIGN_BIN"))
        zsign_sha256 = _required(environment, "ZSIGN_SHA256")
        repository_root = request.config_path.resolve().parent.parent
        with tempfile.TemporaryDirectory(prefix="sideloadedipa-production-") as directory:
            private_root = Path(directory)
            p12_path = private_root / "certificate.p12"
            p12_password = _decode_p12(environment, p12_path)
            prepared: list[PreparedContext] = []
            for context in contexts:
                destination = self.dependencies.package.output_root / f"{context.task.slug}.ipa"
                signing_request = prepare_package_signing(
                    task=context.task,
                    source_ipa=context.downloaded.path,
                    destination_ipa=destination,
                    profile_root=self.dependencies.package.profile_root,
                    p12_path=p12_path,
                    p12_password=p12_password,
                    private_directory=private_root / context.task.slug,
                    zsign_executable=zsign,
                    zsign_sha256=zsign_sha256,
                    repository_root=repository_root,
                )
                prepared.append(
                    PreparedContext(
                        context,
                        signing_request,
                        _fingerprint(context, signing_request, repository_root),
                    )
                )
            yield tuple(prepared)

    def _write_decisions(
        self,
        request: CommandRequest,
        decisions: tuple[RebuildDecision, ...],
    ) -> None:
        path = self._store(request).run_root / "cache-decisions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {
                        "task_name": value.task_name,
                        "rebuild": value.rebuild,
                        "reason": value.reason.value,
                        "fingerprint_sha256": value.fingerprint_sha256,
                        "cached_artifact_sha256": value.cached_artifact_sha256,
                    }
                    for value in decisions
                ],
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )

    def _read_decisions(self, request: CommandRequest) -> tuple[RebuildDecision, ...]:
        path = self._store(request).run_root / "cache-decisions.json"
        try:
            values = json.loads(path.read_text())
            return tuple(
                RebuildDecision(
                    value["task_name"],
                    value["rebuild"],
                    RebuildReason(value["reason"]),
                    value["fingerprint_sha256"],
                    value["cached_artifact_sha256"],
                )
                for value in values
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                "production cache decisions are missing or invalid",
                remediation="rerun the sign stage for this run ID",
            ) from error

    def sign(self, request: CommandRequest) -> CommandResult:
        self._require_signing_environment()
        contexts = self._inspect_contexts(request)
        store = self._store(request)
        for context in contexts:
            self._require(store, context.task.task_name, PipelineStage.RESOURCE_APPLY)
        try:
            cached = self._cache().load()
        except ValueError:
            cached = None
        with self._prepared(request, contexts) as prepared:
            decisions = list(
                select_rebuilds(
                    tuple(value.fingerprint for value in prepared),
                    cached,
                    force=request.force_rebuild,
                )
            )
            cached_records = {value.task_name: value for value in cached.records} if cached else {}
            pending_records: list[TaskCacheRecord] = []
            for index, value in enumerate(prepared):
                task_name = value.source.task.task_name
                resource_apply = self._require(store, task_name, PipelineStage.RESOURCE_APPLY)
                plan = value.plan
                signing_plan = self._record_success(
                    store,
                    task_name,
                    PipelineStage.SIGNING_PLAN,
                    plan.plan_sha256,
                    resource_apply,
                )
                decision = decisions[index]
                verification: VerificationResult
                signing_report_sha256: str
                if not decision.rebuild:
                    record = cached_records[task_name]
                    artifact = self._cache().artifact_path(task_name, value.fingerprint.sha256)
                    try:
                        verification = revalidate_cached_artifact(
                            plan=plan,
                            cache_record=record,
                            artifact=artifact,
                            prerequisites=CachePrerequisiteState(
                                True, value.request.profile_manifest.snapshot_sha256
                            ),
                            profiles=value.request.profiles,
                            now=datetime.now(timezone.utc),
                            refresh_threshold=_PROFILE_REFRESH_THRESHOLD,
                            verifier=value.request.verifier,
                        )
                        signing_report_sha256 = _restore_cached_signing_report(
                            plan=plan,
                            record=record,
                            cached_path=self._cache().signing_report_path(
                                task_name, value.fingerprint.sha256
                            ),
                            retained_path=self._signing_report_path(request, task_name),
                        )
                        _atomic_copy(artifact, value.request.destination_ipa)
                    except (OSError, SideloadedIPAError):
                        decision = RebuildDecision(
                            task_name,
                            True,
                            RebuildReason.CACHE_REJECTED,
                            value.fingerprint.sha256,
                            record.artifact_sha256,
                        )
                        decisions[index] = decision
                        execution = execute_package_signing(value.request)
                        verification = execution.execution.verification
                        artifact = self._cache().artifact_path(task_name, value.fingerprint.sha256)
                        _atomic_copy(value.request.destination_ipa, artifact)
                        signing_report = canonical_signing_report_json(
                            plan, execution.execution.signing
                        )
                        signing_report_sha256 = hashlib.sha256(signing_report).hexdigest()
                        _write_bytes_atomic(
                            self._cache().signing_report_path(task_name, value.fingerprint.sha256),
                            signing_report,
                        )
                        _write_bytes_atomic(
                            self._signing_report_path(request, task_name), signing_report
                        )
                else:
                    execution = execute_package_signing(value.request)
                    verification = execution.execution.verification
                    artifact = self._cache().artifact_path(task_name, value.fingerprint.sha256)
                    _atomic_copy(value.request.destination_ipa, artifact)
                    signing_report = canonical_signing_report_json(
                        plan, execution.execution.signing
                    )
                    signing_report_sha256 = hashlib.sha256(signing_report).hexdigest()
                    _write_bytes_atomic(
                        self._cache().signing_report_path(task_name, value.fingerprint.sha256),
                        signing_report,
                    )
                    _write_bytes_atomic(
                        self._signing_report_path(request, task_name), signing_report
                    )
                artifact_sha256 = _sha256_file(value.request.destination_ipa)
                self._record_success(
                    store,
                    task_name,
                    PipelineStage.SIGN,
                    artifact_sha256,
                    signing_plan,
                )
                pending_records.append(
                    TaskCacheRecord(
                        task_name,
                        value.fingerprint.schema_version,
                        value.fingerprint.sha256,
                        artifact_sha256,
                        verification.report_sha256,
                        signing_report_sha256,
                    )
                )
            decisions_tuple = tuple(decisions)
            self._write_decisions(request, decisions_tuple)
            existing = (
                {record.task_name: record for record in cached.records}
                if cached is not None
                else {}
            )
            existing.update({record.task_name: record for record in pending_records})
            self._pending_cache(request).save(build_cache_index(tuple(existing.values())))
        return _result(
            "sign",
            {
                "status": "passed",
                "tasks": [
                    {
                        "task_name": value.task_name,
                        "rebuild": value.rebuild,
                        "reason": value.reason.value,
                        "signing_report_sha256": next(
                            record.signing_report_sha256
                            for record in pending_records
                            if record.task_name == value.task_name
                        ),
                    }
                    for value in decisions_tuple
                ],
            },
            f"Production signing: {len(decisions_tuple)} passed",
        )

    def _promote_cache(self, request: CommandRequest) -> None:
        pending = self._pending_cache(request).load()
        if pending is None:
            raise ConfigurationError(
                ErrorCode.CONFIG_MISSING,
                "verified run has no pending cache index",
                remediation="rerun signing and verification for this run ID",
            )
        self._cache().save(pending)

    def _report(
        self,
        request: CommandRequest,
        prepared: tuple[PreparedContext, ...],
        verifications: Mapping[str, VerificationResult],
        publications: Mapping[str, PublicationResult] | None = None,
    ) -> Path:
        decisions = {value.task_name: value for value in self._read_decisions(request)}
        tasks = tuple(
            TaskRunEvidence(
                task_name=value.source.task.task_name,
                stages=self._store(request).completed(value.source.task.task_name),
                source=value.source.source,
                graph_sha256=value.source.graph.graph_sha256,
                plan=value.plan,
                capability_classifications=tuple(
                    (
                        intent.target_bundle_id,
                        capability,
                        capability_rule(capability).automation.value,
                    )
                    for intent in derive_bundle_resource_intents(value.source.task)
                    for capability in intent.required_capabilities
                ),
                manual_actions=(
                    value.source.task.signing.manual_app_group_associations
                    if value.source.task.signing is not None
                    else ()
                ),
                apple_resource_ids=tuple(
                    resource
                    for entry in value.request.profile_manifest.entries
                    for resource in (
                        ("bundle-id", entry.bundle_resource_id),
                        ("profile", entry.profile_resource_id),
                    )
                ),
                cache_decision=decisions[value.source.task.task_name],
                verification=verifications[value.source.task.task_name],
                publication=(
                    publications.get(value.source.task.task_name)
                    if publications is not None
                    else None
                ),
            )
            for value in prepared
        )
        started_at = min(stage.started_at for task in tasks for stage in task.stages)
        completed_at = max(
            stage.completed_at or stage.started_at for task in tasks for stage in task.stages
        )
        path = self.dependencies.report_root / f"{request.run_id}.json"
        environment = self.dependencies.package.environment
        redactions = tuple(
            value
            for key, value in environment.items()
            if value
            and any(token in key for token in ("SECRET", "PASSWORD", "PRIVATE", "P12", "TOKEN"))
        )
        write_run_report(
            path,
            RunReport(request.run_id, started_at, completed_at, tasks),
            secret_redactions=redactions,
            path_redactions=(self._store(request).run_root,),
        )
        return path

    def verify(self, request: CommandRequest) -> CommandResult:
        self._require_signing_environment()
        contexts = self._inspect_contexts(request)
        store = self._store(request)
        for context in contexts:
            self._require(store, context.task.task_name, PipelineStage.SIGN)
        verifications: dict[str, VerificationResult] = {}
        with self._prepared(request, contexts) as prepared:
            for value in prepared:
                task_name = value.source.task.task_name
                signing = self._require(store, task_name, PipelineStage.SIGN)
                plan = value.plan
                planned = self._require(store, task_name, PipelineStage.SIGNING_PLAN)
                if planned.result_sha256 != plan.plan_sha256:
                    raise DomainError(
                        ErrorCode.PIPELINE_TRANSITION_INVALID,
                        "reconstructed signing plan differs from the sign stage",
                        task_name=task_name,
                    )
                if signing.result_sha256 != _sha256_file(value.request.destination_ipa):
                    raise DomainError(
                        ErrorCode.SIGNING_VERIFICATION_FAILED,
                        "signed artifact changed before standalone verification",
                        task_name=task_name,
                    )
                verification = verify_package_artifact(
                    value.request, plan, value.request.destination_ipa
                )
                verifications[task_name] = verification
                self._record_success(
                    store,
                    task_name,
                    PipelineStage.VERIFY,
                    verification.report_sha256,
                    signing,
                )
            if not request.publish:
                self._promote_cache(request)
            report = self._report(request, prepared, verifications)
        return _result(
            "verify",
            {
                "status": "passed",
                "report_path": str(report),
                "tasks": [
                    {
                        "task_name": task_name,
                        "verification_report_sha256": verification.report_sha256,
                    }
                    for task_name, verification in verifications.items()
                ],
            },
            f"Production verification: {len(verifications)} passed",
        )

    def publish(self, request: CommandRequest) -> CommandResult:
        self._require_signing_environment()
        contexts = self._inspect_contexts(request)
        store = self._store(request)
        configuration = load_configuration(request.config_path)
        verifications: dict[str, VerificationResult] = {}
        with self._prepared(request, contexts) as prepared:
            publication_store, publisher = _publication_runtime(
                configuration, self.dependencies.package.environment
            )
            candidates: list[PublicationCandidate] = []
            for value in prepared:
                task = value.source.task
                verification_manifest = self._require(store, task.task_name, PipelineStage.VERIFY)
                if not task.publication_enabled:
                    raise ConfigurationError(
                        ErrorCode.CONFIG_INVALID,
                        "selected task is not approved for publication",
                        task_name=task.task_name,
                    )
                plan = value.plan
                verification = verify_package_artifact(
                    value.request, plan, value.request.destination_ipa
                )
                if verification_manifest.result_sha256 != verification.report_sha256:
                    raise DomainError(
                        ErrorCode.PIPELINE_TRANSITION_INVALID,
                        "current verification differs from the retained verify stage",
                        task_name=task.task_name,
                    )
                verifications[task.task_name] = verification
                metadata = read_ipa_metadata(value.request.destination_ipa)
                icon_url: str | None = None
                if task.icon_path is not None:
                    try:
                        png = build_icon_png(
                            task.icon_path,
                            task.source.location,
                            ref=(
                                value.source.source.version
                                if value.source.resolved.evidence.get("release_tag") is not None
                                else None
                            ),
                            ipa_path=value.request.destination_ipa,
                        )
                        icon_url = publication_store.upload_icon(task.slug, png)
                    except (BotoCoreError, ClientError, IconError, OSError):
                        icon_url = None
                candidates.append(
                    PublicationCandidate(
                        task.task_name,
                        task.slug,
                        task.app_name,
                        metadata.bundle_id,
                        metadata.version,
                        f"{_safe_filename(task.app_name)}.ipa",
                        str(value.request.destination_ipa),
                        _sha256_file(value.request.destination_ipa),
                        icon_url,
                        task.publication_enabled,
                        plan,
                        verification,
                    )
                )
            results = publisher.publish(candidates, now=datetime.now(timezone.utc))
            if self.journal is not None:
                self.journal.mark_publication_committed()
            publications = {value.task_name: value for value in results}
            for value in prepared:
                task_name = value.source.task.task_name
                verify_manifest = self._require(store, task_name, PipelineStage.VERIFY)
                self._record_success(
                    store,
                    task_name,
                    PipelineStage.PUBLISH,
                    publications[task_name].registry_sha256,
                    verify_manifest,
                )
            self._promote_cache(request)
            report = self._report(request, prepared, verifications, publications)
        return _result(
            "publish",
            {
                "status": "passed",
                "report_path": str(report),
                "tasks": [
                    {
                        "task_name": value.task_name,
                        "artifact_key": value.artifact_key,
                        "registry_sha256": value.registry_sha256,
                    }
                    for value in results
                ],
            },
            f"Production publication: {len(results)} passed",
        )

    def run(self, request: CommandRequest) -> CommandResult:
        self.inspect(replace(request, command=CommandName.INSPECT))
        planned = self.plan(replace(request, command=CommandName.PLAN))
        if not request.apply:
            return planned
        self.sync(replace(request, command=CommandName.SYNC, apply=True))
        self.sign(replace(request, command=CommandName.SIGN))
        self.verify(
            replace(
                request,
                command=CommandName.VERIFY,
                publish=request.publish,
            )
        )
        if request.publish:
            return self.publish(replace(request, command=CommandName.PUBLISH))
        report = self.dependencies.report_root / f"{request.run_id}.json"
        return _result(
            "run",
            {"status": "passed", "report_path": str(report)},
            "Production run: passed",
        )


def _execute_default(
    request: CommandRequest,
    operation: str,
) -> CommandResult:
    journal_path = FileStageManifestStore(Path("work/pipeline"), request.run_id).run_root / (
        "side-effects.json"
    )
    journal = load_side_effect_journal(journal_path)
    pipeline = ProductionPipeline(journal=journal)
    report = pipeline.dependencies.report_root / f"{request.run_id}-cancellation.json"
    try:
        with record_cancellation(journal, report):
            handler = getattr(pipeline, operation)
            return handler(request)  # type: ignore[no-any-return]
    finally:
        write_side_effect_journal(journal_path, journal)


def inspect_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "inspect")


def plan_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "plan")


def sync_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "sync")


def sign_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "sign")


def verify_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "verify")


def publish_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "publish")


def run_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "run")
