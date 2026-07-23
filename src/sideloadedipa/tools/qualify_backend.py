"""One supported backend-qualification entry point."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, cast

from sideloadedipa.adapters.signing import ZsignBackend
from sideloadedipa.application import CommandName, CommandRequest, OutputFormat
from sideloadedipa.config import load_configuration
from sideloadedipa.domain import CertificateMaterial, SigningBackendIdentity, Task
from sideloadedipa.errors import ConfigurationError, ErrorCode, SideloadedIPAError
from sideloadedipa.pipeline.environment import decode_p12
from sideloadedipa.pipeline.production import ProductionPipeline
from sideloadedipa.pipeline.sign_stage import policy_sha256
from sideloadedipa.signing.certificate_identity import load_p12_certificate_material
from sideloadedipa.signing.profile_storage import load_profile_manifest
from sideloadedipa.tools.build_backend_qualification_fixture import build_fixture
from sideloadedipa.tools.compare_backend_qualification import (
    ComparisonError,
    compare_summaries,
    load_summary,
)
from sideloadedipa.tools.exercise_codesign_oracle import (
    CodesignOracleError,
    CodesignOracleRequest,
)
from sideloadedipa.tools.exercise_codesign_oracle import exercise as exercise_codesign
from sideloadedipa.tools.exercise_zsign_backend import (
    TARGETS,
    BackendExerciseError,
    ZsignExerciseRequest,
)
from sideloadedipa.tools.exercise_zsign_backend import exercise as exercise_zsign
from sideloadedipa.util.atomics import atomic_write_bytes, canonical_json, file_sha256

QUALIFICATION_EVIDENCE_SCHEMA_VERSION = 1
DEFAULT_CONTRACT_PATH = Path("patches/zsign/qualification-contract.json")
DEFAULT_EVIDENCE_PATH = Path("work/qualification/backend-qualification.json")
_SHA256_LENGTH = 64

JsonObject = dict[str, Any]


class QualificationError(RuntimeError):
    """The retained backend qualification contract could not be proven."""


@dataclass(frozen=True, slots=True)
class QualificationOptions:
    config_path: Path
    task_name: str
    run_id: str
    apply: bool
    zsign: Path | None
    zsign_sha256: str | None
    profile_root: Path
    contract_path: Path
    evidence_path: Path
    oracle_summary_path: Path | None
    codesign_identity: str | None
    codesign_keychain: Path | None


@dataclass(frozen=True, slots=True)
class PreparedQualification:
    task: Task
    fixture_ipa: Path
    profiles_dir: Path
    certificate: CertificateMaterial
    output_root: Path


class QualificationPreparer(Protocol):
    def __call__(
        self,
        options: QualificationOptions,
        dependencies: QualificationDependencies,
    ) -> AbstractContextManager[PreparedQualification]: ...


@dataclass(frozen=True, slots=True)
class QualificationDependencies:
    pipeline_factory: Callable[[], ProductionPipeline] = ProductionPipeline
    environment: Mapping[str, str] = field(default_factory=lambda: os.environ)
    system: Callable[[], str] = platform.system
    machine: Callable[[], str] = platform.machine
    which: Callable[[str], str | None] = shutil.which
    backend_exercise: Callable[[ZsignExerciseRequest], JsonObject] = exercise_zsign
    oracle_exercise: Callable[[CodesignOracleRequest], JsonObject] = exercise_codesign
    comparison: Callable[[Mapping[str, Any], Mapping[str, Any]], JsonObject] = compare_summaries
    prepare: QualificationPreparer | None = None


def _digest(value: object) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value, default=str)).hexdigest()


def _object(value: object, field_name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise QualificationError(f"qualification contract {field_name} must be an object")
    return cast(JsonObject, value)


def load_qualification_contract(path: Path) -> JsonObject:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError("qualification contract is missing or invalid") from error
    document = _object(value, "root")
    if (
        set(document)
        != {
            "schema_version",
            "backend",
            "command_shape",
            "fixture",
            "supported_platforms",
        }
        or document.get("schema_version") != 1
    ):
        raise QualificationError("qualification contract schema is unsupported")
    backend = _object(document["backend"], "backend")
    fixture = _object(document["fixture"], "fixture")
    command_shape = _object(document["command_shape"], "command_shape")
    if set(backend) != {
        "contract_version",
        "name",
        "patch_path",
        "patch_sha256",
        "source_commit",
        "source_sha256",
        "version",
    }:
        raise QualificationError("qualification backend contract fields are invalid")
    if set(fixture) != {
        "policy_sha256",
        "roles",
        "target_bundle_ids",
        "task_name",
    }:
        raise QualificationError("qualification fixture contract fields are invalid")
    if set(command_shape) != {
        "adjacent_profile_entitlement_pairs",
        "entitlement_count",
        "profile_count",
        "recursive",
        "root_last",
    }:
        raise QualificationError("qualification command-shape contract fields are invalid")
    return document


def _platform_id(dependencies: QualificationDependencies) -> str:
    system = dependencies.system().casefold()
    machine = dependencies.machine().casefold().replace("x86_64", "amd64")
    if system == "darwin":
        return "macos"
    return f"{system}-{machine}"


def _validate_contract_inputs(
    options: QualificationOptions,
    dependencies: QualificationDependencies,
    contract: JsonObject,
) -> None:
    backend = _object(contract["backend"], "backend")
    fixture = _object(contract["fixture"], "fixture")
    supported_platforms = contract["supported_platforms"]
    if fixture.get("task_name") != options.task_name:
        raise QualificationError("qualification task does not match the reviewed fixture contract")
    if (
        not isinstance(supported_platforms, list)
        or _platform_id(dependencies) not in supported_platforms
    ):
        raise QualificationError("current platform is outside the reviewed backend contract")
    patch_path = Path(str(backend["patch_path"]))
    if file_sha256(patch_path) != backend.get("patch_sha256"):
        raise QualificationError("reviewed zsign patch digest changed; requalification is required")
    if options.zsign is None or options.zsign_sha256 is None:
        raise ConfigurationError(
            ErrorCode.CONFIG_MISSING,
            "backend qualification requires ZSIGN_BIN and ZSIGN_SHA256",
            remediation="build the reviewed patched zsign action and export both outputs",
        )
    if len(options.zsign_sha256) != _SHA256_LENGTH:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "ZSIGN_SHA256 must be a canonical SHA-256 digest",
        )


def run_production_preparation(
    options: QualificationOptions,
    pipeline: ProductionPipeline,
) -> CommandRequest:
    """Reuse the production source, Apple-plan, and profile-sync transactions."""

    request = CommandRequest(
        command=CommandName.INSPECT,
        config_path=options.config_path,
        task_names=(options.task_name,),
        output_format=OutputFormat.JSON,
        run_id=options.run_id,
    )
    pipeline.inspect(request)
    pipeline.plan(replace(request, command=CommandName.PLAN))
    pipeline.sync(replace(request, command=CommandName.SYNC, apply=options.apply))
    return request


def _selected_task(options: QualificationOptions) -> Task:
    matches = tuple(
        task
        for task in load_configuration(options.config_path).tasks
        if task.task_name == options.task_name
    )
    if len(matches) != 1 or matches[0].signing is None:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "backend qualification requires one explicit multi-bundle signing task",
            task_name=options.task_name,
        )
    return matches[0]


@contextmanager
def prepare_qualification(
    options: QualificationOptions,
    dependencies: QualificationDependencies,
) -> Iterator[PreparedQualification]:
    pipeline = dependencies.pipeline_factory()
    request = run_production_preparation(options, pipeline)
    task = _selected_task(options)
    inputs = pipeline._inputs(request).load(task)
    manifest = load_profile_manifest(options.profile_root, task.task_name)
    entries = {entry.target_bundle_id: entry for entry in manifest.entries}
    expected_targets = {target[2] for target in TARGETS.values()}
    if set(entries) != expected_targets:
        raise QualificationError(
            "profile manifest does not exactly match the qualification fixture"
        )
    certificate_ids = {entry.certificate_resource_id for entry in manifest.entries}
    if len(certificate_ids) != 1:
        raise QualificationError("qualification profiles do not share one certificate resource")

    with tempfile.TemporaryDirectory(prefix="sideloadedipa-backend-qualification-") as directory:
        root = Path(directory)
        fixture_ipa = root / "fixture.ipa"
        build_fixture(inputs.downloaded.path, fixture_ipa, inputs.downloaded.sha256)
        profiles_dir = root / "profiles"
        profiles_dir.mkdir(mode=0o700)
        for role, (_, _, target_bundle_id) in TARGETS.items():
            entry = entries[target_bundle_id]
            source = options.profile_root.joinpath(*entry.profile_path.parts)
            destination = profiles_dir / f"{role}.mobileprovision"
            shutil.copyfile(source, destination)

        private = root / "private"
        private.mkdir(mode=0o700)
        p12_path = private / "certificate.p12"
        password = decode_p12(dependencies.environment, p12_path)
        certificate = load_p12_certificate_material(
            p12_path,
            password,
            resource_id=next(iter(certificate_ids)),
            output_directory=private,
        )
        yield PreparedQualification(task, fixture_ipa, profiles_dir, certificate, root)


def _backend_identity(
    options: QualificationOptions,
    prepared: PreparedQualification,
) -> SigningBackendIdentity:
    assert options.zsign is not None
    assert options.zsign_sha256 is not None
    return ZsignBackend(
        executable=options.zsign,
        expected_executable_sha256=options.zsign_sha256,
        profile_root=prepared.profiles_dir,
    ).identity()


def _new_evidence(
    options: QualificationOptions,
    dependencies: QualificationDependencies,
    contract: JsonObject,
) -> JsonObject:
    backend = _object(contract["backend"], "backend")
    return {
        "schema_version": QUALIFICATION_EVIDENCE_SCHEMA_VERSION,
        "status": "running",
        "run_id": options.run_id,
        "task_name": options.task_name,
        "contract": {"sha256": _digest(contract)},
        "fixture": {"sha256": None},
        "backend": {
            "name": backend["name"],
            "version": backend["version"],
            "contract_version": backend["contract_version"],
            "executable_sha256": None,
            "patch_sha256": backend["patch_sha256"],
            "platform": _platform_id(dependencies),
            "summary": None,
            "summary_sha256": None,
        },
        "plan": {"document": None, "sha256": None},
        "output": {"sha256": None},
        "oracle": {
            "status": "not-run",
            "summary": None,
            "summary_sha256": None,
        },
        "comparison": {
            "status": "not-run",
            "summary": None,
            "summary_sha256": None,
        },
        "failure": None,
    }


def _plan_document(
    prepared: PreparedQualification,
    identity: SigningBackendIdentity,
    backend_summary: Mapping[str, Any],
) -> JsonObject:
    return {
        "task_name": prepared.task.task_name,
        "policy_sha256": policy_sha256(prepared.task),
        "fixture_sha256": file_sha256(prepared.fixture_ipa),
        "backend": {
            "name": identity.name,
            "version": identity.version,
            "executable_sha256": identity.executable_sha256,
            "contract_version": identity.contract_version,
        },
        "command_shape": backend_summary.get("command_shape"),
        "profile_sha256": {
            role: file_sha256(prepared.profiles_dir / f"{role}.mobileprovision")
            for role in sorted(TARGETS)
        },
        "targets": {role: TARGETS[role][2] for role in sorted(TARGETS)},
    }


def _validate_backend_result(
    contract: JsonObject,
    prepared: PreparedQualification,
    identity: SigningBackendIdentity,
    summary: Mapping[str, Any],
) -> None:
    backend = _object(contract["backend"], "backend")
    fixture = _object(contract["fixture"], "fixture")
    command = _object(contract["command_shape"], "command_shape")
    if (
        identity.name != backend.get("name")
        or identity.version != backend.get("version")
        or identity.contract_version != backend.get("contract_version")
    ):
        raise QualificationError("production backend identity differs from the reviewed contract")
    if policy_sha256(prepared.task) != fixture.get("policy_sha256"):
        raise QualificationError(
            "qualification signing policy changed and requires requalification"
        )
    if summary.get("contract_pass") is not True or summary.get("violations") != []:
        raise QualificationError("patched zsign did not satisfy the entitlement contract")
    shape = summary.get("command_shape")
    if shape != {
        "entitlement_count": command["entitlement_count"],
        "global_entitlements": False,
        "profile_count": command["profile_count"],
    }:
        raise QualificationError("patched zsign command shape differs from the reviewed contract")
    order = summary.get("signing_order")
    if not isinstance(order, list) or set(order) != set(TARGETS) or order[-1:] != ["root"]:
        raise QualificationError("patched zsign signing order is not complete and root-last")
    if summary.get("mismatched_entitlement_count_rejected") is not True:
        raise QualificationError("patched zsign accepted a profile/entitlement count mismatch")


def _oracle_summary(
    options: QualificationOptions,
    dependencies: QualificationDependencies,
    prepared: PreparedQualification,
) -> tuple[JsonObject | None, str | None]:
    if options.oracle_summary_path is not None:
        return load_summary(options.oracle_summary_path), None
    if dependencies.system() != "Darwin":
        return None, "macOS codesign oracle evidence is required"
    if (
        options.codesign_identity is None
        or options.codesign_keychain is None
        or not options.codesign_keychain.is_file()
        or dependencies.which("codesign") is None
        or dependencies.which("security") is None
    ):
        return None, "macOS codesign identity, keychain, codesign, and security are required"
    return (
        dependencies.oracle_exercise(
            CodesignOracleRequest(
                fixture_ipa=prepared.fixture_ipa,
                identity=options.codesign_identity,
                keychain=options.codesign_keychain,
                profiles_dir=prepared.profiles_dir,
                output_dir=prepared.output_root / "oracle",
            )
        ),
        None,
    )


def run_qualification(
    options: QualificationOptions,
    dependencies: QualificationDependencies = QualificationDependencies(),
) -> tuple[JsonObject, int]:
    contract = load_qualification_contract(options.contract_path)
    _validate_contract_inputs(options, dependencies, contract)
    evidence = _new_evidence(options, dependencies, contract)
    preparer = dependencies.prepare or prepare_qualification
    with preparer(options, dependencies) as prepared:
        identity = _backend_identity(options, prepared)
        assert options.zsign is not None
        backend_summary = dependencies.backend_exercise(
            ZsignExerciseRequest(
                zsign=options.zsign,
                fixture_ipa=prepared.fixture_ipa,
                private_key=prepared.certificate.private_key_path,
                certificate=prepared.certificate.certificate_path,
                profiles_dir=prepared.profiles_dir,
                output_dir=prepared.output_root / "zsign",
                config=options.config_path,
            )
        )
        _validate_backend_result(contract, prepared, identity, backend_summary)
        fixture_sha256 = file_sha256(prepared.fixture_ipa)
        plan = _plan_document(prepared, identity, backend_summary)
        evidence["fixture"] = {"sha256": fixture_sha256}
        evidence["backend"] = {
            **cast(JsonObject, evidence["backend"]),
            "executable_sha256": identity.executable_sha256,
            "summary": backend_summary,
            "summary_sha256": _digest(backend_summary),
        }
        evidence["plan"] = {"document": plan, "sha256": _digest(plan)}
        evidence["output"] = {"sha256": backend_summary.get("signed_ipa_sha256")}

        oracle, unmet_reason = _oracle_summary(options, dependencies, prepared)
        if oracle is None:
            evidence["status"] = "manual-gate-unmet"
            evidence["oracle"] = {
                "status": "required",
                "summary": None,
                "summary_sha256": None,
            }
            evidence["failure"] = {
                "code": "qualification.manual_gate_unmet",
                "message": unmet_reason,
            }
            validate_qualification_evidence(evidence)
            return evidence, 3
        if oracle.get("source_fixture_sha256") != fixture_sha256:
            raise QualificationError("macOS oracle evidence belongs to another fixture")
        comparison = dependencies.comparison(backend_summary, oracle)
        evidence["oracle"] = {
            "status": "passed",
            "summary": oracle,
            "summary_sha256": _digest(oracle),
        }
        evidence["comparison"] = {
            "status": "passed",
            "summary": comparison,
            "summary_sha256": _digest(comparison),
        }
        evidence["status"] = "passed"
        validate_qualification_evidence(evidence)
        return evidence, 0


def _optional_sha256(value: object, field_name: str) -> None:
    if value is not None and (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise QualificationError(f"qualification evidence {field_name} is not a SHA-256")


def _validate_bound_document(section: Mapping[str, Any], field_name: str) -> None:
    document = section.get("summary", section.get("document"))
    recorded = section.get("summary_sha256", section.get("sha256"))
    if (document is None) != (recorded is None):
        raise QualificationError(f"qualification evidence {field_name} binding is incomplete")
    if document is not None and _digest(document) != recorded:
        raise QualificationError(f"qualification evidence {field_name} digest does not match")


def validate_qualification_evidence(document: Mapping[str, Any]) -> None:
    if (
        set(document)
        != {
            "schema_version",
            "status",
            "run_id",
            "task_name",
            "contract",
            "fixture",
            "backend",
            "plan",
            "output",
            "oracle",
            "comparison",
            "failure",
        }
        or document.get("schema_version") != QUALIFICATION_EVIDENCE_SCHEMA_VERSION
    ):
        raise QualificationError("qualification evidence schema is unsupported")
    contract = _object(document["contract"], "evidence.contract")
    fixture = _object(document["fixture"], "evidence.fixture")
    backend = _object(document["backend"], "evidence.backend")
    plan = _object(document["plan"], "evidence.plan")
    output = _object(document["output"], "evidence.output")
    oracle = _object(document["oracle"], "evidence.oracle")
    comparison = _object(document["comparison"], "evidence.comparison")
    _optional_sha256(contract.get("sha256"), "contract.sha256")
    _optional_sha256(fixture.get("sha256"), "fixture.sha256")
    _optional_sha256(backend.get("executable_sha256"), "backend.executable_sha256")
    _optional_sha256(backend.get("patch_sha256"), "backend.patch_sha256")
    _optional_sha256(backend.get("summary_sha256"), "backend.summary_sha256")
    _optional_sha256(plan.get("sha256"), "plan.sha256")
    _optional_sha256(output.get("sha256"), "output.sha256")
    _optional_sha256(oracle.get("summary_sha256"), "oracle.summary_sha256")
    _optional_sha256(comparison.get("summary_sha256"), "comparison.summary_sha256")
    _validate_bound_document(backend, "backend.summary")
    _validate_bound_document(plan, "plan.document")
    _validate_bound_document(oracle, "oracle.summary")
    _validate_bound_document(comparison, "comparison.summary")
    status = document.get("status")
    if status == "passed" and any(
        value is None
        for value in (
            fixture.get("sha256"),
            backend.get("executable_sha256"),
            backend.get("summary_sha256"),
            plan.get("sha256"),
            output.get("sha256"),
            oracle.get("summary_sha256"),
            comparison.get("summary_sha256"),
        )
    ):
        raise QualificationError("passing qualification evidence is incomplete")
    if status == "manual-gate-unmet" and oracle.get("status") != "required":
        raise QualificationError("manual qualification gate is not explicit")


def write_qualification_evidence(path: Path, document: Mapping[str, Any]) -> None:
    validate_qualification_evidence(document)
    atomic_write_bytes(path, canonical_json(document))


def _failure_evidence(
    options: QualificationOptions,
    dependencies: QualificationDependencies,
    error: Exception,
) -> JsonObject:
    try:
        contract = load_qualification_contract(options.contract_path)
        document = _new_evidence(options, dependencies, contract)
    except QualificationError:
        document = {
            "schema_version": QUALIFICATION_EVIDENCE_SCHEMA_VERSION,
            "status": "failed",
            "run_id": options.run_id,
            "task_name": options.task_name,
            "contract": {"sha256": None},
            "fixture": {"sha256": None},
            "backend": {
                "name": "zsign",
                "version": None,
                "contract_version": None,
                "executable_sha256": None,
                "patch_sha256": None,
                "platform": _platform_id(dependencies),
                "summary": None,
                "summary_sha256": None,
            },
            "plan": {"document": None, "sha256": None},
            "output": {"sha256": None},
            "oracle": {"status": "not-run", "summary": None, "summary_sha256": None},
            "comparison": {
                "status": "not-run",
                "summary": None,
                "summary_sha256": None,
            },
            "failure": None,
        }
    document["status"] = "failed"
    code = error.code.value if isinstance(error, SideloadedIPAError) else "qualification.failed"
    document["failure"] = {"code": code, "message": str(error)}
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sideloadedipa-qualify-backend")
    parser.add_argument("--config", type=Path, default=Path("configs/tasks.toml"))
    parser.add_argument("--task", default="LiveContainer")
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "qualification"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--zsign", type=Path, default=os.environ.get("ZSIGN_BIN"))
    parser.add_argument("--zsign-sha256", default=os.environ.get("ZSIGN_SHA256"))
    parser.add_argument("--profile-root", type=Path, default=Path("work/profiles"))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE_PATH)
    parser.add_argument("--oracle-summary", type=Path)
    parser.add_argument("--codesign-identity", default=os.environ.get("CODESIGN_IDENTITY"))
    parser.add_argument(
        "--codesign-keychain",
        type=Path,
        default=os.environ.get("CODESIGN_KEYCHAIN"),
    )
    return parser


def _options(namespace: argparse.Namespace) -> QualificationOptions:
    return QualificationOptions(
        config_path=namespace.config,
        task_name=namespace.task,
        run_id=namespace.run_id,
        apply=namespace.apply,
        zsign=namespace.zsign,
        zsign_sha256=namespace.zsign_sha256,
        profile_root=namespace.profile_root,
        contract_path=namespace.contract,
        evidence_path=namespace.evidence,
        oracle_summary_path=namespace.oracle_summary,
        codesign_identity=namespace.codesign_identity,
        codesign_keychain=namespace.codesign_keychain,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    dependencies: QualificationDependencies = QualificationDependencies(),
) -> int:
    options = _options(build_parser().parse_args(argv))
    try:
        evidence, exit_code = run_qualification(options, dependencies)
    except (
        BackendExerciseError,
        CodesignOracleError,
        ComparisonError,
        ConfigurationError,
        QualificationError,
        SideloadedIPAError,
        OSError,
        ValueError,
        zipfile.BadZipFile,
    ) as error:
        evidence = _failure_evidence(options, dependencies, error)
        exit_code = 2
    write_qualification_evidence(options.evidence_path, evidence)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
