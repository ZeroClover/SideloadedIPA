"""Contract tests for the single backend-qualification command."""

from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, cast

import pytest

from sideloadedipa.adapters.signing.zsign import (
    EXPECTED_ZSIGN_VERSION,
)
from sideloadedipa.apple.intents import derive_bundle_resource_intents
from sideloadedipa.application import CommandName
from sideloadedipa.domain import CertificateIdentity, CertificateMaterial
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.pipeline.production import ProductionPipeline
from sideloadedipa.pipeline.stages.signing_cache import policy_sha256
from sideloadedipa.tools.exercise_zsign_backend import TARGETS
from sideloadedipa.tools.qualify_backend import (
    DEFAULT_CONTRACT_PATH,
    PreparedQualification,
    QualificationDependencies,
    QualificationError,
    QualificationOptions,
    main,
    run_production_preparation,
    run_qualification,
    validate_qualification_evidence,
    write_qualification_evidence,
)
from sideloadedipa.util.atomics import file_sha256
from tests.sample_project import CONFIG_PATH, sample_configuration


def fake_zsign(tmp_path: Path) -> tuple[Path, str]:
    executable = tmp_path / "zsign"
    executable.write_text(
        "#!/bin/sh\n" f'test "$1" = "-v" && printf \'version: {EXPECTED_ZSIGN_VERSION}\\n\'\n'
    )
    executable.chmod(0o755)
    return executable, file_sha256(executable)


def sample_contract(tmp_path: Path) -> Path:
    """The repository contract, rebound to the test-owned LiveContainer policy."""

    task = next(task for task in sample_configuration().tasks if task.task_name == "LiveContainer")
    contract = json.loads(DEFAULT_CONTRACT_PATH.read_text())
    contract["fixture"]["policy_sha256"] = policy_sha256(task)
    contract["fixture"]["target_bundle_ids"] = sorted(
        intent.target_bundle_id for intent in derive_bundle_resource_intents(task)
    )
    path = tmp_path / "qualification-contract.json"
    path.write_text(json.dumps(contract))
    return path


def options(
    tmp_path: Path,
    executable: Path,
    executable_sha256: str,
    *,
    oracle_summary: Path | None = None,
) -> QualificationOptions:
    return QualificationOptions(
        config_path=CONFIG_PATH,
        task_name="LiveContainer",
        run_id="qualification-test",
        apply=False,
        zsign=executable,
        zsign_sha256=executable_sha256,
        profile_root=tmp_path / "profiles",
        contract_path=sample_contract(tmp_path),
        evidence_path=tmp_path / "evidence.json",
        oracle_summary_path=oracle_summary,
        codesign_identity=None,
        codesign_keychain=None,
    )


def prepared_factory(tmp_path: Path):
    task = next(task for task in sample_configuration().tasks if task.task_name == "LiveContainer")
    fixture = tmp_path / "fixture.ipa"
    fixture.write_bytes(b"deterministic fixture")
    profiles = tmp_path / "prepared-profiles"
    profiles.mkdir()
    for role in TARGETS:
        (profiles / f"{role}.mobileprovision").write_bytes(f"profile:{role}".encode())
    private = tmp_path / "private"
    private.mkdir()
    certificate_path = private / "certificate.pem"
    private_key_path = private / "private-key.pem"
    certificate_path.write_text("certificate")
    private_key_path.write_text("private key")
    certificate = CertificateMaterial(
        CertificateIdentity(
            "CERTIFICATE",
            "TEAMID1234",
            "1234",
            "a" * 64,
            "b" * 64,
            datetime.now(timezone.utc) + timedelta(days=90),
        ),
        certificate_path,
        private_key_path,
    )
    output_root = tmp_path / "output"
    output_root.mkdir()

    @contextmanager
    def prepare(
        qualification_options: QualificationOptions,
        dependencies: QualificationDependencies,
    ) -> Iterator[PreparedQualification]:
        del qualification_options, dependencies
        yield PreparedQualification(task, fixture, profiles, certificate, output_root)

    return prepare, fixture


def backend_summary(executable_sha256: str) -> dict[str, object]:
    return {
        "backend": "zsign",
        "backend_variant": "per-profile-entitlements-extension",
        "command_shape": {
            "entitlement_count": 4,
            "global_entitlements": False,
            "profile_count": 4,
        },
        "contract_pass": True,
        "executable_sha256": executable_sha256,
        "mismatched_entitlement_count_rejected": True,
        "profiles": {},
        "signed_entitlements": {},
        "signed_ipa_sha256": "f" * 64,
        "signing_order": ["launch", "process", "share", "root"],
        "violations": [],
    }


@pytest.mark.parametrize(
    ("system", "which"),
    [
        ("Linux", lambda command: f"/usr/bin/{command}"),
        ("Darwin", lambda command: None),
    ],
)
def test_missing_macos_oracle_is_an_explicit_failing_manual_gate(
    tmp_path: Path,
    system: str,
    which,
) -> None:
    executable, digest = fake_zsign(tmp_path)
    prepare, _ = prepared_factory(tmp_path)
    dependencies = QualificationDependencies(
        system=lambda: system,
        machine=lambda: "x86_64",
        which=which,
        backend_exercise=lambda request: backend_summary(digest),
        prepare=prepare,
    )

    evidence, exit_code = run_qualification(options(tmp_path, executable, digest), dependencies)

    assert exit_code == 3
    assert evidence["status"] == "manual-gate-unmet"
    assert evidence["oracle"]["status"] == "required"
    assert evidence["failure"]["code"] == "qualification.manual_gate_unmet"
    assert evidence["comparison"]["status"] == "not-run"


def test_provided_oracle_produces_self_contained_digest_bound_evidence(
    tmp_path: Path,
) -> None:
    executable, digest = fake_zsign(tmp_path)
    prepare, fixture = prepared_factory(tmp_path)
    oracle_path = tmp_path / "oracle.json"
    oracle = {
        "backend": "codesign",
        "contract_pass": True,
        "source_fixture_sha256": file_sha256(fixture),
        "violations": [],
    }
    oracle_path.write_text(json.dumps(oracle))
    comparison = {"contract_pass": True, "profile_mapping_matches": True}
    dependencies = QualificationDependencies(
        system=lambda: "Linux",
        machine=lambda: "x86_64",
        backend_exercise=lambda request: backend_summary(digest),
        comparison=lambda linux, macos: comparison,
        prepare=prepare,
    )

    evidence, exit_code = run_qualification(
        options(tmp_path, executable, digest, oracle_summary=oracle_path), dependencies
    )
    write_qualification_evidence(tmp_path / "written.json", evidence)

    assert exit_code == 0
    assert evidence["status"] == "passed"
    assert evidence["fixture"]["sha256"] == file_sha256(fixture)
    assert evidence["backend"]["executable_sha256"] == digest
    assert (
        evidence["plan"]["document"]["policy_sha256"]
        == "ab8417518dd41fe9bc8c026331827dc96287b9a3f9f5df2cda930fb8c1328237"
    )
    assert evidence["output"]["sha256"] == "f" * 64
    assert evidence["oracle"]["summary"] == oracle
    assert evidence["comparison"]["summary"] == comparison
    validate_qualification_evidence(json.loads((tmp_path / "written.json").read_bytes()))
    tampered = deepcopy(evidence)
    tampered["backend"]["summary"]["contract_pass"] = False
    with pytest.raises(QualificationError, match="backend.summary digest does not match"):
        validate_qualification_evidence(tampered)


def test_no_credentials_failure_writes_non_passing_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    executable, digest = fake_zsign(tmp_path)

    @contextmanager
    def missing_credentials(
        qualification_options: QualificationOptions,
        dependencies: QualificationDependencies,
    ) -> Iterator[PreparedQualification]:
        del qualification_options, dependencies
        raise ConfigurationError(
            ErrorCode.CONFIG_MISSING,
            "package signing requires APPLE_DEV_CERT_P12_ENCODED",
        )
        yield cast(PreparedQualification, None)

    evidence_path = tmp_path / "failed.json"
    exit_code = main(
        [
            "--zsign",
            str(executable),
            "--zsign-sha256",
            digest,
            "--evidence",
            str(evidence_path),
        ],
        dependencies=QualificationDependencies(
            system=lambda: "Linux",
            machine=lambda: "x86_64",
            prepare=missing_credentials,
        ),
    )

    evidence = json.loads(evidence_path.read_bytes())
    assert exit_code == 2
    assert evidence["status"] == "failed"
    assert evidence["failure"]["code"] == "config.missing"
    assert evidence["oracle"]["status"] == "not-run"
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_production_preparation_reuses_inspect_plan_and_sync_in_order(tmp_path: Path) -> None:
    executable, digest = fake_zsign(tmp_path)
    qualification_options = options(tmp_path, executable, digest)
    qualification_options = replace(qualification_options, apply=True)
    calls = []

    class RecordingPipeline:
        def inspect(self, request):
            calls.append(request)

        def plan(self, request):
            calls.append(request)

        def sync(self, request):
            calls.append(request)

    request = run_production_preparation(
        qualification_options,
        cast(ProductionPipeline, RecordingPipeline()),
    )

    assert [call.command for call in calls] == [
        CommandName.INSPECT,
        CommandName.PLAN,
        CommandName.SYNC,
    ]
    assert calls[-1].apply is True
    assert request.task_names == ("LiveContainer",)
