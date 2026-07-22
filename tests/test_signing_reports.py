"""Tests for canonical redacted signing evidence."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import PurePosixPath

import pytest

from sideloadedipa.domain import (
    BundleNodeKind,
    Diagnostic,
    DiagnosticSeverity,
    SigningBackendFeature,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningNodeResult,
    SigningPlan,
    SigningResult,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.signing_reports import (
    canonical_signing_report_json,
    canonical_signing_result_json,
    signing_result_sha256,
)

ROOT = PurePosixPath("Payload/Example.app")
EXTENSION = ROOT / "PlugIns/Share.appex"


def fixture_plan() -> SigningPlan:
    empty = normalize_entitlements({})
    backend = SigningBackendIdentity(
        "zsign",
        "1.1.1+sideloadedipa.2",
        "a" * 64,
        "1",
        (
            SigningBackendFeature.PER_PROFILE_ENTITLEMENTS,
            SigningBackendFeature.RECURSIVE_SIGNING,
        ),
    )
    nodes = tuple(
        SigningNodePlan(
            source_path=path,
            executable_path=path / "Executable",
            kind=kind,
            order=order,
            target_bundle_id=target,
            profile_resource_id=f"PROFILE_{order}",
            profile_path=PurePosixPath(f"Example/{order}.mobileprovision"),
            profile_sha256=str(order) * 64,
            expected_entitlements=empty.values,
            expected_entitlements_sha256=empty.sha256,
        )
        for order, (path, kind, target) in enumerate(
            (
                (EXTENSION, BundleNodeKind.APP_EXTENSION, "io.example.app.Share"),
                (ROOT, BundleNodeKind.APP, "io.example.app"),
            )
        )
    )
    return SigningPlan("Example", "b" * 64, "c" * 64, "d" * 64, backend, nodes, "e" * 64)


def fixture_result(plan: SigningPlan) -> SigningResult:
    diagnostics = (
        Diagnostic(
            "backend.node.signed",
            DiagnosticSeverity.INFO,
            "node signed",
            details=(("attempt", 1),),
        ),
    )
    nodes = tuple(
        SigningNodeResult(
            node.source_path,
            str(node.order + 2) * 64,
            node.profile_sha256,
            node.expected_entitlements_sha256,
            node.order + 0.25,
            diagnostics,
        )
        for node in plan.nodes
    )
    return SigningResult(
        plan.plan_sha256,
        PurePosixPath("private/work/signed.ipa"),
        "f" * 64,
        plan.backend,
        nodes,
        1.5,
        diagnostics,
        (
            "/private/tools/zsign",
            "-k",
            "/private/key.pem",
            "-c",
            "/private/certificate.pem",
            "-m",
            "/private/raw.mobileprovision",
            "-e",
            "/private/entitlements.plist",
            "-o",
            "/private/output.ipa",
            "/private/source.ipa",
        ),
    )


def test_result_json_is_canonical_digest_bound_and_redacted() -> None:
    signing_plan = fixture_plan()
    result = fixture_result(signing_plan)

    encoded = canonical_signing_result_json(result)
    repeated = canonical_signing_result_json(result)
    document = json.loads(encoded)

    assert encoded == repeated
    assert document["result_sha256"] == signing_result_sha256(result)
    assert document["output_name"] == "signed.ipa"
    assert document["backend_argv_shape"] == [
        "<redacted>",
        "-k",
        "<redacted>",
        "-c",
        "<redacted>",
        "-m",
        "<redacted>",
        "-e",
        "<redacted>",
        "-o",
        "<redacted>",
        "<redacted>",
    ]
    for private_value in (
        "/private",
        "key.pem",
        "certificate.pem",
        "raw.mobileprovision",
        "entitlements.plist",
    ):
        assert private_value.encode() not in encoded


def test_report_joins_every_planned_node_to_backend_evidence() -> None:
    signing_plan = fixture_plan()
    result = fixture_result(signing_plan)

    document = json.loads(canonical_signing_report_json(signing_plan, result))

    assert document["plan_sha256"] == signing_plan.plan_sha256
    assert document["result_sha256"] == signing_result_sha256(result)
    assert [node["source_path"] for node in document["nodes"]] == [
        EXTENSION.as_posix(),
        ROOT.as_posix(),
    ]
    assert all(node["backend_evidence"] is not None for node in document["nodes"])
    assert document["nodes"][0]["backend_evidence"]["signed_executable_sha256"] == "2" * 64


def test_report_marks_backend_evidence_absent_without_inventing_values() -> None:
    signing_plan = fixture_plan()
    result = replace(fixture_result(signing_plan), nodes=())

    document = json.loads(canonical_signing_report_json(signing_plan, result))

    assert [node["backend_evidence"] for node in document["nodes"]] == [None, None]


@pytest.mark.parametrize("mismatch", ["plan", "backend", "duplicate", "unknown"])
def test_report_rejects_inconsistent_backend_evidence(mismatch: str) -> None:
    signing_plan = fixture_plan()
    result = fixture_result(signing_plan)
    if mismatch == "plan":
        result = replace(result, plan_sha256="0" * 64)
    elif mismatch == "backend":
        result = replace(result, backend=replace(result.backend, name="other"))
    elif mismatch == "duplicate":
        result = replace(result, nodes=(result.nodes[0], result.nodes[0]))
    else:
        result = replace(
            result,
            nodes=(replace(result.nodes[0], source_path=PurePosixPath("Payload/Unknown.app")),),
        )

    with pytest.raises(DomainError) as caught:
        canonical_signing_report_json(signing_plan, result)

    assert caught.value.code is ErrorCode.SIGNING_VERIFICATION_FAILED
