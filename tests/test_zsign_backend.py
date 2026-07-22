"""Tests for the qualified paired-profile zsign adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

import sideloadedipa.adapters.signing.zsign as zsign_module
from sideloadedipa.adapters.signing.zsign import (
    EXPECTED_ZSIGN_VERSION,
    ZSIGN_CONTRACT_VERSION,
    ZsignBackend,
    collect_signed_node_evidence,
)
from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    BundleNodeKind,
    CertificateIdentity,
    CertificateMaterial,
    SigningBackendFeature,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningNodeResult,
    SigningPlan,
    normalize_entitlements,
)
from sideloadedipa.errors import AdapterError, ErrorCode

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)


def executable(
    tmp_path: Path,
    *,
    version: str = EXPECTED_ZSIGN_VERSION,
    fail: bool = False,
    delay: bool = False,
) -> Path:
    path = tmp_path / "tools with spaces" / "zsign"
    path.parent.mkdir()
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"VERSION = {version!r}\n"
        "if sys.argv[1:] == ['-v']:\n"
        "    print(f'version: {VERSION}')\n"
        "    raise SystemExit(0)\n"
        + ("print('fixture failure', file=sys.stderr)\nraise SystemExit(7)\n" if fail else "")
        + ("import time\ntime.sleep(1)\n" if delay else "")
        + "args = sys.argv[1:]\n"
        "output = pathlib.Path(args[args.index('-o') + 1])\n"
        "output.write_text(json.dumps(args))\n"
    )
    path.chmod(0o755)
    return path


def certificate(tmp_path: Path) -> CertificateMaterial:
    certificate_path = tmp_path / "private" / "certificate.pem"
    private_key_path = tmp_path / "private" / "private-key.pem"
    certificate_path.parent.mkdir(exist_ok=True)
    certificate_path.write_text("certificate")
    private_key_path.write_text("private key")
    identity = CertificateIdentity(
        "CERT_ONE",
        "TEAMID1234",
        "1234ABCD",
        "a" * 64,
        "b" * 64,
        NOW + timedelta(days=90),
    )
    return CertificateMaterial(identity, certificate_path, private_key_path)


def profile_node(
    profile_root: Path,
    *,
    order: int,
    target: str,
    resource_id: str,
) -> SigningNodePlan:
    relative = PurePosixPath("Example") / f"{resource_id}.mobileprovision"
    content = f"profile:{resource_id}".encode()
    path = profile_root.joinpath(*relative.parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    expected = normalize_entitlements({"application-identifier": f"PREFIX.{target}"})
    return SigningNodePlan(
        source_path=PurePosixPath(f"Payload/App.app/{resource_id}.appex"),
        executable_path=PurePosixPath(f"Payload/App.app/{resource_id}.appex/Executable"),
        kind=BundleNodeKind.APP_EXTENSION,
        order=order,
        target_bundle_id=target,
        profile_resource_id=resource_id,
        profile_path=relative,
        profile_sha256=hashlib.sha256(content).hexdigest(),
        expected_entitlements=expected.values,
        expected_entitlements_sha256=expected.sha256,
    )


def profile_free_node() -> SigningNodePlan:
    empty = normalize_entitlements({})
    return SigningNodePlan(
        source_path=PurePosixPath("Payload/App.app/Frameworks/Kit.framework"),
        executable_path=PurePosixPath("Payload/App.app/Frameworks/Kit.framework/Kit"),
        kind=BundleNodeKind.FRAMEWORK,
        order=0,
        target_bundle_id=None,
        profile_resource_id=None,
        profile_path=None,
        profile_sha256=None,
        expected_entitlements=empty.values,
        expected_entitlements_sha256=empty.sha256,
    )


def plan(profile_root: Path, backend: SigningBackendIdentity) -> SigningPlan:
    return SigningPlan(
        task_name="Example",
        source_ipa_sha256="c" * 64,
        graph_sha256="d" * 64,
        certificate_sha256="b" * 64,
        backend=backend,
        nodes=(
            profile_free_node(),
            profile_node(
                profile_root,
                order=1,
                target="io.example.app.Share",
                resource_id="PROFILE_SHARE",
            ),
            profile_node(
                profile_root,
                order=2,
                target="io.example.app",
                resource_id="PROFILE_ROOT",
            ),
        ),
        plan_sha256="e" * 64,
    )


def first_profile(signing_plan: SigningPlan) -> SigningNodePlan:
    return next(node for node in signing_plan.nodes if node.profile_resource_id is not None)


def backend(tmp_path: Path, executable_path: Path) -> ZsignBackend:
    return ZsignBackend(
        executable=executable_path,
        expected_executable_sha256=hashlib.sha256(executable_path.read_bytes()).hexdigest(),
        profile_root=tmp_path / "profiles",
        evidence_collector=lambda signing_plan, output: tuple(
            SigningNodeResult(
                node.source_path,
                hashlib.sha256(output.read_bytes()).hexdigest(),
                node.profile_sha256,
                node.expected_entitlements_sha256,
                0.0,
            )
            for node in signing_plan.nodes
        ),
    )


def test_signs_with_adjacent_profile_entitlement_pairs_and_redacted_argv(
    tmp_path: Path,
) -> None:
    executable_path = executable(tmp_path)
    adapter = backend(tmp_path, executable_path)
    identity = adapter.identity()
    signing_plan = plan(tmp_path / "profiles", identity)
    signing_plan = replace(signing_plan, nodes=tuple(reversed(signing_plan.nodes)))
    source = tmp_path / "source with spaces.ipa"
    output = tmp_path / "signed output.ipa"
    source.write_bytes(b"source")

    result = adapter.sign(signing_plan, source, output, certificate(tmp_path))
    argv = json.loads(output.read_text())

    assert identity == SigningBackendIdentity(
        "zsign",
        EXPECTED_ZSIGN_VERSION,
        hashlib.sha256(executable_path.read_bytes()).hexdigest(),
        ZSIGN_CONTRACT_VERSION,
        (
            SigningBackendFeature.PER_PROFILE_ENTITLEMENTS,
            SigningBackendFeature.RECURSIVE_SIGNING,
        ),
    )
    profile_indexes = [index for index, value in enumerate(argv) if value == "-m"]
    assert len(profile_indexes) == 2
    assert all(argv[index + 2] == "-e" for index in profile_indexes)
    assert "-p" not in argv
    assert str(profile_free_node().source_path) not in argv
    assert "PROFILE_SHARE.mobileprovision" in argv[profile_indexes[0] + 1]
    assert "PROFILE_ROOT.mobileprovision" in argv[profile_indexes[1] + 1]
    assert argv[-2:] == [str(output), str(source)]
    assert result.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result.output_path == PurePosixPath(output.name)
    assert {node.source_path for node in result.nodes} == {
        node.source_path for node in signing_plan.nodes
    }
    rendered = repr(result.backend_argv)
    assert str(source) not in rendered
    assert str(output) not in rendered
    assert str(certificate(tmp_path).private_key_path) not in rendered
    assert "***" in rendered


def test_collects_actual_evidence_for_every_planned_node(tmp_path: Path, monkeypatch) -> None:
    executable_path = executable(tmp_path)
    signing_plan = plan(tmp_path / "profiles", backend(tmp_path, executable_path).identity())
    output = tmp_path / "signed.ipa"
    output.write_bytes(b"signed")
    graph_nodes = tuple(
        BundleNode(
            path=node.source_path,
            kind=node.kind,
            depth=0,
            executable_path=node.executable_path,
            executable_sha256=str(node.order + 1) * 64,
            embedded_profile_sha256=node.profile_sha256,
            entitlements=node.expected_entitlements,
        )
        for node in signing_plan.nodes
    )
    graph = BundleGraph(
        signing_plan.nodes[-1].source_path,
        graph_nodes,
        hashlib.sha256(b"signed").hexdigest(),
        "f" * 64,
    )

    monkeypatch.setattr(
        zsign_module,
        "extract_ipa_safely",
        lambda source, destination: destination.mkdir(parents=True),
    )
    monkeypatch.setattr(
        zsign_module,
        "discover_bundle_graph",
        lambda extracted, sha256: graph,
    )

    evidence = collect_signed_node_evidence(signing_plan, output)

    assert len(evidence) == len(signing_plan.nodes)
    assert evidence[-1].signed_executable_sha256 == str(signing_plan.nodes[-1].order + 1) * 64
    assert (
        evidence[-1].signed_entitlements_sha256
        == signing_plan.nodes[-1].expected_entitlements_sha256
    )


def test_rejects_incomplete_actual_node_evidence(tmp_path: Path, monkeypatch) -> None:
    executable_path = executable(tmp_path)
    signing_plan = plan(tmp_path / "profiles", backend(tmp_path, executable_path).identity())
    output = tmp_path / "signed.ipa"
    output.write_bytes(b"signed")
    graph = BundleGraph(
        signing_plan.nodes[-1].source_path,
        (),
        hashlib.sha256(b"signed").hexdigest(),
        "f" * 64,
    )
    monkeypatch.setattr(
        zsign_module,
        "extract_ipa_safely",
        lambda source, destination: destination.mkdir(parents=True),
    )
    monkeypatch.setattr(zsign_module, "discover_bundle_graph", lambda extracted, sha256: graph)

    with pytest.raises(AdapterError, match="differs from the signing plan"):
        collect_signed_node_evidence(signing_plan, output)


@pytest.mark.parametrize("mismatch", ["checksum", "version", "plan"])
def test_rejects_backend_identity_mismatch_before_signing_inputs(
    tmp_path: Path, mismatch: str
) -> None:
    executable_path = executable(
        tmp_path,
        version="wrong-version" if mismatch == "version" else EXPECTED_ZSIGN_VERSION,
    )
    adapter = backend(tmp_path, executable_path)
    if mismatch == "checksum":
        adapter.expected_executable_sha256 = "0" * 64
        identity = SigningBackendIdentity("zsign", EXPECTED_ZSIGN_VERSION, "0" * 64, "1")
    elif mismatch == "version":
        identity = SigningBackendIdentity("zsign", "wrong-version", "0" * 64, "1")
    else:
        identity = replace(adapter.identity(), contract_version="wrong")
    signing_plan = plan(tmp_path / "profiles", identity)
    material = CertificateMaterial(
        certificate(tmp_path).identity,
        tmp_path / "missing-certificate.pem",
        tmp_path / "missing-key.pem",
    )

    with pytest.raises(AdapterError) as caught:
        adapter.sign(signing_plan, tmp_path / "missing.ipa", tmp_path / "output.ipa", material)

    assert caught.value.code is ErrorCode.ADAPTER_VERSION_MISMATCH


def test_rejects_profile_content_changed_after_planning(tmp_path: Path) -> None:
    executable_path = executable(tmp_path)
    adapter = backend(tmp_path, executable_path)
    signing_plan = plan(tmp_path / "profiles", adapter.identity())
    node = first_profile(signing_plan)
    assert node.profile_path is not None
    tmp_path.joinpath("profiles", *node.profile_path.parts).write_bytes(b"tampered")

    with pytest.raises(AdapterError) as caught:
        adapter.sign(
            signing_plan,
            tmp_path / "source.ipa",
            tmp_path / "output.ipa",
            certificate(tmp_path),
        )

    assert caught.value.code is ErrorCode.SIGNING_PLAN_INVALID
    assert caught.value.bundle_id == node.target_bundle_id


@pytest.mark.parametrize(
    "mutation",
    ["certificate", "no-profile", "incomplete-profile", "entitlement-digest", "profile-path"],
)
def test_rejects_incomplete_or_changed_plan_evidence(tmp_path: Path, mutation: str) -> None:
    executable_path = executable(tmp_path)
    adapter = backend(tmp_path, executable_path)
    signing_plan = plan(tmp_path / "profiles", adapter.identity())
    first = first_profile(signing_plan)
    if mutation == "certificate":
        signing_plan = replace(signing_plan, certificate_sha256="0" * 64)
    elif mutation == "no-profile":
        signing_plan = replace(
            signing_plan,
            nodes=(replace(first, profile_resource_id=None),),
        )
    elif mutation == "incomplete-profile":
        signing_plan = replace(signing_plan, nodes=(replace(first, profile_path=None),))
    elif mutation == "entitlement-digest":
        signing_plan = replace(
            signing_plan,
            nodes=(replace(first, expected_entitlements_sha256="0" * 64),),
        )
    else:
        signing_plan = replace(
            signing_plan,
            nodes=(replace(first, profile_path=PurePosixPath("../outside.mobileprovision")),),
        )

    with pytest.raises(AdapterError) as caught:
        adapter.sign(
            signing_plan,
            tmp_path / "source.ipa",
            tmp_path / "output.ipa",
            certificate(tmp_path),
        )

    assert caught.value.code is ErrorCode.SIGNING_PLAN_INVALID


def test_missing_backend_executable_has_typed_error(tmp_path: Path) -> None:
    adapter = ZsignBackend(
        executable=tmp_path / "missing-zsign",
        expected_executable_sha256="0" * 64,
        profile_root=tmp_path / "profiles",
    )

    with pytest.raises(AdapterError) as caught:
        adapter.identity()

    assert caught.value.code is ErrorCode.ADAPTER_UNAVAILABLE


def test_wraps_nonzero_signing_failure_with_task_context_and_redacted_paths(
    tmp_path: Path,
) -> None:
    executable_path = executable(tmp_path, fail=True)
    adapter = backend(tmp_path, executable_path)
    signing_plan = plan(tmp_path / "profiles", adapter.identity())
    source = tmp_path / "private source.ipa"
    source.write_bytes(b"source")

    with pytest.raises(AdapterError) as caught:
        adapter.sign(signing_plan, source, tmp_path / "output.ipa", certificate(tmp_path))

    assert caught.value.code is ErrorCode.ADAPTER_COMMAND_FAILED
    assert caught.value.task_name == "Example"
    assert str(source) not in repr(caught.value.safe_details)
    assert dict(caught.value.safe_details)["plan_sha256"] == signing_plan.plan_sha256


def test_wraps_signing_timeout_with_task_context(tmp_path: Path) -> None:
    executable_path = executable(tmp_path, delay=True)
    adapter = ZsignBackend(
        executable=executable_path,
        expected_executable_sha256=hashlib.sha256(executable_path.read_bytes()).hexdigest(),
        profile_root=tmp_path / "profiles",
        timeout_seconds=0.01,
    )
    signing_plan = plan(tmp_path / "profiles", adapter.identity())
    source = tmp_path / "source.ipa"
    source.write_bytes(b"source")

    with pytest.raises(AdapterError) as caught:
        adapter.sign(signing_plan, source, tmp_path / "output.ipa", certificate(tmp_path))

    assert caught.value.code is ErrorCode.ADAPTER_TIMEOUT
    assert caught.value.task_name == "Example"
