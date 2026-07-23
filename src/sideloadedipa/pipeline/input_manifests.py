"""Canonical source and unsigned-inventory evidence for one run and task."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    BundleNodeKind,
    EntitlementSliceDigest,
    FrozenJsonObject,
    PipelineStage,
    SourceAsset,
    StageManifest,
    StageStatus,
    Task,
    freeze_json,
    thaw_json,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.ipa.graph import canonical_graph_json
from sideloadedipa.pipeline.inspection import ResolvedSource
from sideloadedipa.pipeline.manifest_store import FileStageManifestStore
from sideloadedipa.pipeline.sign_stage import json_digest
from sideloadedipa.sources import DownloadedSource
from sideloadedipa.util.atomics import atomic_write_bytes, canonical_json, file_sha256

INPUT_MANIFEST_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SourceInputManifest:
    schema_version: int
    run_id: str
    task_name: str
    source_url: str
    source_filename: str
    expected_size: int | None
    actual_size: int
    expected_sha256: str | None
    bound_sha256: str
    actual_sha256: str
    download_attempts: int
    evidence: FrozenJsonObject
    source: SourceAsset
    source_stage_sha256: str
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class InventoryInputManifest:
    schema_version: int
    run_id: str
    task_name: str
    source_manifest_sha256: str
    source_stage_sha256: str
    inventory_stage_sha256: str
    graph: BundleGraph
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class CanonicalInputs:
    resolved: ResolvedSource
    downloaded: DownloadedSource
    source: SourceAsset
    graph: BundleGraph
    source_manifest: SourceInputManifest
    inventory_manifest: InventoryInputManifest


def _error(message: str, *, task_name: str | None = None) -> ConfigurationError:
    return ConfigurationError(
        ErrorCode.CONFIG_INVALID,
        message,
        task_name=task_name,
        remediation="discard this run and restart inspection with a new run ID",
    )


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _error(f"canonical input manifest {field} is invalid")
    return value


def _optional_sha256(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _sha256(value, field)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(f"canonical input manifest {field} is invalid")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _integer(value: object, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        raise _error(f"canonical input manifest {field} is invalid")
    return value


def _optional_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field)


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _error(f"canonical input manifest {field} is invalid")
    return value


def _array(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise _error(f"canonical input manifest {field} is invalid")
    return value


def _path(value: object, field: str) -> PurePosixPath:
    path = PurePosixPath(_string(value, field))
    if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise _error(f"canonical input manifest {field} is invalid")
    return path


def _source_document(source: SourceAsset) -> dict[str, object]:
    return {
        "asset_id": source.asset_id,
        "name": source.name,
        "source_url": source.source_url,
        "version": source.version,
        "published_at": source.published_at.isoformat() if source.published_at else None,
        "path": source.path.as_posix(),
        "sha256": source.sha256,
    }


def _parse_source(value: object) -> SourceAsset:
    document = _object(value, "source")
    if set(document) != {
        "asset_id",
        "name",
        "source_url",
        "version",
        "published_at",
        "path",
        "sha256",
    }:
        raise _error("canonical input manifest source fields are invalid")
    published = _optional_string(document["published_at"], "source.published_at")
    try:
        published_at = datetime.fromisoformat(published) if published is not None else None
    except ValueError as error:
        raise _error("canonical input manifest source timestamp is invalid") from error
    if published_at is not None and published_at.tzinfo is None:
        raise _error("canonical input manifest source timestamp is invalid")
    return SourceAsset(
        asset_id=_string(document["asset_id"], "source.asset_id"),
        name=_string(document["name"], "source.name"),
        source_url=_string(document["source_url"], "source.source_url"),
        version=_string(document["version"], "source.version"),
        published_at=published_at,
        path=_path(document["path"], "source.path"),
        sha256=_sha256(document["sha256"], "source.sha256"),
    )


def _graph_from_document(value: object) -> BundleGraph:
    document = _object(value, "graph")
    nodes_document = _array(document.get("nodes"), "graph.nodes")
    nodes: list[BundleNode] = []
    for index, raw_node in enumerate(nodes_document):
        field = f"graph.nodes[{index}]"
        node = _object(raw_node, field)
        slices = tuple(
            EntitlementSliceDigest(
                architecture=_string(
                    _object(raw_slice, f"{field}.entitlement_slices[{slice_index}]").get(
                        "architecture"
                    ),
                    f"{field}.entitlement_slices[{slice_index}].architecture",
                ),
                xml_sha256=_optional_sha256(
                    _object(raw_slice, f"{field}.entitlement_slices[{slice_index}]").get(
                        "xml_sha256"
                    ),
                    f"{field}.entitlement_slices[{slice_index}].xml_sha256",
                ),
                der_sha256=_optional_sha256(
                    _object(raw_slice, f"{field}.entitlement_slices[{slice_index}]").get(
                        "der_sha256"
                    ),
                    f"{field}.entitlement_slices[{slice_index}].der_sha256",
                ),
            )
            for slice_index, raw_slice in enumerate(
                _array(node.get("entitlement_slices"), f"{field}.entitlement_slices")
            )
        )
        entitlement_value = freeze_json(_object(node.get("entitlements"), f"{field}.entitlements"))
        if not isinstance(entitlement_value, FrozenJsonObject):  # pragma: no cover
            raise _error(f"canonical input manifest {field}.entitlements is invalid")
        try:
            kind = BundleNodeKind(_string(node.get("kind"), f"{field}.kind"))
        except ValueError as error:
            raise _error(f"canonical input manifest {field}.kind is invalid") from error
        nodes.append(
            BundleNode(
                path=_path(node.get("path"), f"{field}.path"),
                kind=kind,
                depth=_integer(node.get("depth"), f"{field}.depth"),
                executable_path=_path(node.get("executable_path"), f"{field}.executable_path"),
                executable_sha256=_sha256(
                    node.get("executable_sha256"), f"{field}.executable_sha256"
                ),
                parent_path=(
                    _path(node["parent"], f"{field}.parent")
                    if node.get("parent") is not None
                    else None
                ),
                source_bundle_id=_optional_string(
                    node.get("source_bundle_id"), f"{field}.source_bundle_id"
                ),
                info_plist_sha256=_optional_sha256(
                    node.get("info_plist_sha256"), f"{field}.info_plist_sha256"
                ),
                version=_optional_string(node.get("version"), f"{field}.version"),
                short_version=_optional_string(node.get("short_version"), f"{field}.short_version"),
                embedded_profile_sha256=_optional_sha256(
                    node.get("embedded_profile_sha256"),
                    f"{field}.embedded_profile_sha256",
                ),
                xml_entitlements_sha256=_optional_sha256(
                    node.get("xml_entitlements_sha256"),
                    f"{field}.xml_entitlements_sha256",
                ),
                der_entitlements_sha256=_optional_sha256(
                    node.get("der_entitlements_sha256"),
                    f"{field}.der_entitlements_sha256",
                ),
                entitlement_slices=slices,
                entitlements=entitlement_value.items,
            )
        )

    graph = BundleGraph(
        root_path=_path(document.get("root_path"), "graph.root_path"),
        nodes=tuple(nodes),
        source_sha256=_sha256(document.get("source_sha256"), "graph.source_sha256"),
        graph_sha256=_sha256(document.get("graph_sha256"), "graph.graph_sha256"),
    )
    canonical = json.loads(canonical_graph_json(graph))
    if canonical != document:
        raise _error("canonical input manifest graph is not canonical")
    digest_document = dict(canonical)
    digest_document.pop("graph_sha256")
    if hashlib.sha256(canonical_json(digest_document)).hexdigest() != graph.graph_sha256:
        raise _error("canonical input manifest graph digest is invalid")
    return graph


def _source_manifest_document(manifest: SourceInputManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "run_id": manifest.run_id,
        "task_name": manifest.task_name,
        "source_url": manifest.source_url,
        "source_filename": manifest.source_filename,
        "expected_size": manifest.expected_size,
        "actual_size": manifest.actual_size,
        "expected_sha256": manifest.expected_sha256,
        "bound_sha256": manifest.bound_sha256,
        "actual_sha256": manifest.actual_sha256,
        "download_attempts": manifest.download_attempts,
        "evidence": thaw_json(manifest.evidence),
        "source": _source_document(manifest.source),
        "source_stage_sha256": manifest.source_stage_sha256,
    }


def _inventory_manifest_document(manifest: InventoryInputManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "run_id": manifest.run_id,
        "task_name": manifest.task_name,
        "source_manifest_sha256": manifest.source_manifest_sha256,
        "source_stage_sha256": manifest.source_stage_sha256,
        "inventory_stage_sha256": manifest.inventory_stage_sha256,
        "graph": json.loads(canonical_graph_json(manifest.graph)),
    }


def _with_digest(document: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(document)).hexdigest()


def _decode_document(payload: bytes, kind: str) -> dict[str, object]:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _error(f"canonical {kind} manifest is not valid JSON") from error
    if not isinstance(document, dict):
        raise _error(f"canonical {kind} manifest root is invalid")
    try:
        digest = _sha256(document.pop("manifest_sha256"), "manifest_sha256")
    except KeyError as error:
        raise _error(f"canonical {kind} manifest digest is missing") from error
    if _with_digest(document) != digest:
        raise _error(f"canonical {kind} manifest digest is invalid")
    document["manifest_sha256"] = digest
    return document


def _require_success(
    stage: StageManifest | None,
    expected_stage: PipelineStage,
    task_name: str,
) -> StageManifest:
    if (
        stage is None
        or stage.task_name != task_name
        or stage.stage is not expected_stage
        or stage.status is not StageStatus.SUCCEEDED
    ):
        raise _error(
            f"canonical input requires a successful {expected_stage.value} stage",
            task_name=task_name,
        )
    return stage


@dataclass(frozen=True, slots=True)
class CanonicalInputManifestStore:
    stages: FileStageManifestStore

    def source_manifest_path(self, task_name: str) -> Path:
        return self.stages.task_root(task_name) / "source-input.json"

    def inventory_manifest_path(self, task_name: str) -> Path:
        return self.stages.task_root(task_name) / "inventory-input.json"

    def source_path(self, task_name: str) -> Path:
        return self.stages.task_root(task_name) / "source.ipa"

    def save_source(
        self,
        *,
        task: Task,
        resolved: ResolvedSource,
        downloaded: DownloadedSource,
        source: SourceAsset,
        source_stage: StageManifest,
    ) -> SourceInputManifest:
        stage = _require_success(source_stage, PipelineStage.SOURCE, task.task_name)
        path = self.source_path(task.task_name)
        expected_sha256 = resolved.evidence.get("expected_sha256")
        expected_digest = (
            _optional_sha256(expected_sha256, "evidence.expected_sha256")
            if expected_sha256 is not None
            else None
        )
        bound_digest = _sha256(
            (
                resolved.expected_sha256.removeprefix("sha256:")
                if resolved.expected_sha256 is not None
                else None
            ),
            "bound_sha256",
        )
        if (
            downloaded.path != path
            or not path.is_file()
            or path.stat().st_size != downloaded.size
            or file_sha256(path) != downloaded.sha256
            or downloaded.sha256 != bound_digest
            or (expected_digest is not None and expected_digest != downloaded.sha256)
            or source.sha256 != downloaded.sha256
            or source.source_url != resolved.url
            or source.path != PurePosixPath(path.name)
            or stage.result_sha256 != json_digest(asdict(source))
        ):
            raise _error(
                "source input does not match successful source evidence", task_name=task.task_name
            )
        evidence = freeze_json(dict(resolved.evidence))
        if not isinstance(evidence, FrozenJsonObject):  # pragma: no cover
            raise _error("source evidence must be an object", task_name=task.task_name)
        partial = SourceInputManifest(
            INPUT_MANIFEST_SCHEMA_VERSION,
            self.stages.run_id,
            task.task_name,
            resolved.url,
            path.name,
            resolved.advertised_size,
            downloaded.size,
            expected_digest,
            bound_digest,
            downloaded.sha256,
            downloaded.attempts,
            evidence,
            source,
            stage.manifest_sha256,
            "",
        )
        manifest = SourceInputManifest(
            partial.schema_version,
            partial.run_id,
            partial.task_name,
            partial.source_url,
            partial.source_filename,
            partial.expected_size,
            partial.actual_size,
            partial.expected_sha256,
            partial.bound_sha256,
            partial.actual_sha256,
            partial.download_attempts,
            partial.evidence,
            partial.source,
            partial.source_stage_sha256,
            _with_digest(_source_manifest_document(partial)),
        )
        document = _source_manifest_document(manifest)
        document["manifest_sha256"] = manifest.manifest_sha256
        atomic_write_bytes(self.source_manifest_path(task.task_name), canonical_json(document))
        return manifest

    def load_source(
        self, task: Task
    ) -> tuple[SourceInputManifest, ResolvedSource, DownloadedSource]:
        path = self.source_manifest_path(task.task_name)
        try:
            document = _decode_document(path.read_bytes(), "source input")
            manifest = self._parse_source_manifest(document)
        except OSError as error:
            raise _error(
                "canonical source input manifest is missing", task_name=task.task_name
            ) from error
        if manifest.run_id != self.stages.run_id or manifest.task_name != task.task_name:
            raise _error("canonical source input identity is invalid", task_name=task.task_name)
        source_stage = _require_success(
            self.stages.load(task.task_name, PipelineStage.SOURCE),
            PipelineStage.SOURCE,
            task.task_name,
        )
        source_path = self.source_path(task.task_name)
        if (
            manifest.source_filename != source_path.name
            or manifest.source.path != PurePosixPath(source_path.name)
            or not source_path.is_file()
            or source_path.stat().st_size != manifest.actual_size
            or file_sha256(source_path) != manifest.actual_sha256
            or manifest.bound_sha256 != manifest.actual_sha256
            or (
                manifest.expected_size is not None
                and manifest.expected_size != manifest.actual_size
            )
            or (
                manifest.expected_sha256 is not None
                and manifest.expected_sha256 != manifest.actual_sha256
            )
            or manifest.source.sha256 != manifest.actual_sha256
            or manifest.source.source_url != manifest.source_url
            or manifest.source_stage_sha256 != source_stage.manifest_sha256
            or source_stage.result_sha256 != json_digest(asdict(manifest.source))
            or dict(manifest.evidence.items).get("kind") != task.source.kind.value
        ):
            raise _error(
                "canonical source input evidence no longer matches", task_name=task.task_name
            )
        if task.source.kind.value == "direct-url" and (
            task.source.location != manifest.source_url
            or task.source.ipa_sha256 != manifest.expected_sha256
        ):
            raise _error(
                "direct source identity changed after inspection", task_name=task.task_name
            )
        evidence_document = thaw_json(manifest.evidence)
        if not isinstance(evidence_document, dict):  # pragma: no cover
            raise _error("canonical source evidence is invalid", task_name=task.task_name)
        resolved = ResolvedSource(
            manifest.source_url,
            f"sha256:{manifest.bound_sha256}",
            evidence_document,
            manifest.expected_size,
        )
        downloaded = DownloadedSource(
            source_path,
            manifest.actual_size,
            manifest.actual_sha256,
            manifest.download_attempts,
        )
        return manifest, resolved, downloaded

    def _parse_source_manifest(self, document: dict[str, object]) -> SourceInputManifest:
        expected_fields = {
            "schema_version",
            "run_id",
            "task_name",
            "source_url",
            "source_filename",
            "expected_size",
            "actual_size",
            "expected_sha256",
            "bound_sha256",
            "actual_sha256",
            "download_attempts",
            "evidence",
            "source",
            "source_stage_sha256",
            "manifest_sha256",
        }
        if (
            set(document) != expected_fields
            or document.get("schema_version") != INPUT_MANIFEST_SCHEMA_VERSION
        ):
            raise _error("canonical source input manifest schema is unsupported")
        evidence = freeze_json(_object(document["evidence"], "evidence"))
        if not isinstance(evidence, FrozenJsonObject):  # pragma: no cover
            raise _error("canonical source evidence is invalid")
        return SourceInputManifest(
            INPUT_MANIFEST_SCHEMA_VERSION,
            _string(document["run_id"], "run_id"),
            _string(document["task_name"], "task_name"),
            _string(document["source_url"], "source_url"),
            _string(document["source_filename"], "source_filename"),
            _optional_integer(document["expected_size"], "expected_size"),
            _integer(document["actual_size"], "actual_size"),
            _optional_sha256(document["expected_sha256"], "expected_sha256"),
            _sha256(document["bound_sha256"], "bound_sha256"),
            _sha256(document["actual_sha256"], "actual_sha256"),
            _integer(document["download_attempts"], "download_attempts", positive=True),
            evidence,
            _parse_source(document["source"]),
            _sha256(document["source_stage_sha256"], "source_stage_sha256"),
            _sha256(document["manifest_sha256"], "manifest_sha256"),
        )

    def save_inventory(
        self,
        *,
        task: Task,
        source_manifest: SourceInputManifest,
        graph: BundleGraph,
        inventory_stage: StageManifest,
    ) -> InventoryInputManifest:
        stage = _require_success(inventory_stage, PipelineStage.INVENTORY, task.task_name)
        if (
            source_manifest.run_id != self.stages.run_id
            or source_manifest.task_name != task.task_name
            or graph.source_sha256 != source_manifest.actual_sha256
            or stage.predecessor_sha256 != source_manifest.source_stage_sha256
            or stage.result_sha256 != graph.graph_sha256
        ):
            raise _error(
                "inventory input does not match successful predecessor evidence",
                task_name=task.task_name,
            )
        _graph_from_document(json.loads(canonical_graph_json(graph)))
        partial = InventoryInputManifest(
            INPUT_MANIFEST_SCHEMA_VERSION,
            self.stages.run_id,
            task.task_name,
            source_manifest.manifest_sha256,
            source_manifest.source_stage_sha256,
            stage.manifest_sha256,
            graph,
            "",
        )
        manifest = InventoryInputManifest(
            partial.schema_version,
            partial.run_id,
            partial.task_name,
            partial.source_manifest_sha256,
            partial.source_stage_sha256,
            partial.inventory_stage_sha256,
            partial.graph,
            _with_digest(_inventory_manifest_document(partial)),
        )
        document = _inventory_manifest_document(manifest)
        document["manifest_sha256"] = manifest.manifest_sha256
        atomic_write_bytes(self.inventory_manifest_path(task.task_name), canonical_json(document))
        return manifest

    def load(self, task: Task) -> CanonicalInputs:
        source_manifest, resolved, downloaded = self.load_source(task)
        try:
            document = _decode_document(
                self.inventory_manifest_path(task.task_name).read_bytes(),
                "inventory input",
            )
            inventory = self._parse_inventory_manifest(document)
        except OSError as error:
            raise _error(
                "canonical inventory input manifest is missing", task_name=task.task_name
            ) from error
        source_stage = _require_success(
            self.stages.load(task.task_name, PipelineStage.SOURCE),
            PipelineStage.SOURCE,
            task.task_name,
        )
        inventory_stage = _require_success(
            self.stages.load(task.task_name, PipelineStage.INVENTORY),
            PipelineStage.INVENTORY,
            task.task_name,
        )
        if (
            inventory.run_id != self.stages.run_id
            or inventory.task_name != task.task_name
            or inventory.source_manifest_sha256 != source_manifest.manifest_sha256
            or inventory.source_stage_sha256 != source_stage.manifest_sha256
            or inventory.inventory_stage_sha256 != inventory_stage.manifest_sha256
            or inventory_stage.predecessor_sha256 != source_stage.manifest_sha256
            or inventory_stage.result_sha256 != inventory.graph.graph_sha256
            or inventory.graph.source_sha256 != downloaded.sha256
        ):
            raise _error(
                "canonical inventory input evidence no longer matches", task_name=task.task_name
            )
        return CanonicalInputs(
            resolved,
            downloaded,
            source_manifest.source,
            inventory.graph,
            source_manifest,
            inventory,
        )

    def _parse_inventory_manifest(self, document: dict[str, object]) -> InventoryInputManifest:
        expected_fields = {
            "schema_version",
            "run_id",
            "task_name",
            "source_manifest_sha256",
            "source_stage_sha256",
            "inventory_stage_sha256",
            "graph",
            "manifest_sha256",
        }
        if (
            set(document) != expected_fields
            or document.get("schema_version") != INPUT_MANIFEST_SCHEMA_VERSION
        ):
            raise _error("canonical inventory input manifest schema is unsupported")
        return InventoryInputManifest(
            INPUT_MANIFEST_SCHEMA_VERSION,
            _string(document["run_id"], "run_id"),
            _string(document["task_name"], "task_name"),
            _sha256(document["source_manifest_sha256"], "source_manifest_sha256"),
            _sha256(document["source_stage_sha256"], "source_stage_sha256"),
            _sha256(document["inventory_stage_sha256"], "inventory_stage_sha256"),
            _graph_from_document(document["graph"]),
            _sha256(document["manifest_sha256"], "manifest_sha256"),
        )
