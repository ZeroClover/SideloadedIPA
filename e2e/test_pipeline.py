"""End-to-end scenarios for the signing pipeline, driven through its command line."""

from __future__ import annotations

import json
import plistlib
import zipfile
from collections.abc import Callable
from pathlib import Path

from e2e.harness import (
    EXTENSION_BUNDLE,
    HELPER_EXECUTABLE,
    ROOT_BUNDLE,
    Apple,
    Outcome,
    Source,
    build_fixture_ipa,
    sha256_file,
    write_configuration,
)
from tests.signing_fixtures import unsigned_macho

Cli = Callable[..., Outcome]

ROOT_TARGET = "io.example.e2e.fixture"
EXTENSION_TARGET = "io.example.e2e.fixture.Share"
SIGNED_IPA = Path("work/signed/Fixture.ipa")
RESOURCE_CREATION = [
    ["bundle-ids", "create", "--identifier", ROOT_TARGET, "--name", "Fixture App"],
    ["bundle-ids", "create", "--identifier", EXTENSION_TARGET, "--name", "Fixture App Share"],
    ["bundle-ids", "capabilities", "add", "--bundle", "BUNDLE0001", "--capability", "HEALTHKIT"],
    ["profiles", "create", "--name", "Fixture App Dev"],
    ["profiles", "create", "--name", "Fixture App Share Dev"],
]


def succeed(outcome: Outcome) -> dict[str, object]:
    assert outcome.exit_code == 0, outcome.stderr
    return outcome.payload


def sign_in_stages(cli: Cli, zsign: dict[str, str], run_id: str) -> None:
    """Run the production workflow's commands, one process per stage, up to signing."""

    succeed(cli("inspect", "--run-id", run_id))
    succeed(cli("sync", "--apply", "--run-id", run_id))
    succeed(cli("sign", "--run-id", run_id, environment_overrides=zsign))


def created_resources(apple: Apple) -> list[list[str]]:
    """Each Apple mutation, cut to the arguments that identify what it created."""

    return [
        call[: len(expected)]
        for call, expected in zip(apple.mutations(), RESOURCE_CREATION, strict=True)
    ]


def run_report(project: Path, outcome: Outcome) -> dict[str, object]:
    report = json.loads((project / str(succeed(outcome)["report_path"])).read_text())
    assert isinstance(report, dict)
    return report


def test_staged_commands_sign_and_independently_verify_every_bundle(
    cli: Cli, project: Path, apple: Apple, source: Source, zsign: dict[str, str]
) -> None:
    sign_in_stages(cli, zsign, "staged")
    verified = cli("verify", "--run-id", "staged", environment_overrides=zsign)

    assert created_resources(apple) == RESOURCE_CREATION

    signed = project / SIGNED_IPA
    report = run_report(project, verified)
    (task,) = report["tasks"]
    assert report["passed"] is True
    assert task["verification"]["passed"] is True
    assert task["verification"]["artifact_sha256"] == sha256_file(signed)
    assert all(finding["passed"] for finding in task["verification"]["findings"])
    assert {finding["node_path"] for finding in task["verification"]["findings"]} >= {
        ROOT_BUNDLE,
        EXTENSION_BUNDLE,
        HELPER_EXECUTABLE,
    }

    with zipfile.ZipFile(signed) as archive:
        members = set(archive.namelist())
        for bundle, executable, target in (
            (ROOT_BUNDLE, "Fixture", ROOT_TARGET),
            (EXTENSION_BUNDLE, "Share", EXTENSION_TARGET),
        ):
            information = plistlib.loads(archive.read(f"{bundle}/Info.plist"))
            assert information["CFBundleIdentifier"] == target
            assert archive.read(f"{bundle}/embedded.mobileprovision") == (
                apple.issued_profile(target)
            )
            assert f"{bundle}/_CodeSignature/CodeResources" in members
            assert f"E2ETEAM001.{target}".encode() in archive.read(f"{bundle}/{executable}")
        root_executable = archive.read(f"{ROOT_BUNDLE}/Fixture")
        extension_executable = archive.read(f"{EXTENSION_BUNDLE}/Share")
        helper = archive.read(HELPER_EXECUTABLE)
    assert b"com.apple.developer.healthkit" in root_executable
    assert b"com.apple.developer.healthkit" not in extension_executable
    assert helper != unsigned_macho()
    assert source.requests == ["/Fixture.ipa"]


def test_verify_rejects_a_signed_ipa_modified_after_signing(
    cli: Cli, project: Path, zsign: dict[str, str]
) -> None:
    sign_in_stages(cli, zsign, "tampered")
    signed = project / SIGNED_IPA
    executable_name = f"{ROOT_BUNDLE}/Fixture"
    rewritten = signed.with_suffix(".rewritten")
    with zipfile.ZipFile(signed) as original, zipfile.ZipFile(rewritten, "w") as replacement:
        for member in original.infolist():
            content = original.read(member.filename)
            if member.filename == executable_name:
                # A padding byte of the first segment name: the image still parses,
                # but no longer matches the code hashes that were signed.
                assert content[40:50] == b"__LINKEDIT"
                content = content[:55] + b"X" + content[56:]
            replacement.writestr(member, content)
    rewritten.replace(signed)

    outcome = cli("verify", "--run-id", "tampered", environment_overrides=zsign)

    assert outcome.exit_code == 2
    assert outcome.error["code"] == "signing.verification_failed"
    assert outcome.error["message"] == "existing signed IPA did not pass independent verification"
    assert outcome.stdout == ""
    assert not (project / "work/reports/tampered.json").exists()
    assert not list((project / "work/pipeline").glob("tampered-*/*/verification-report.json"))


def test_rerun_reuses_the_verified_artifact_and_existing_apple_resources(
    cli: Cli, project: Path, apple: Apple, zsign: dict[str, str]
) -> None:
    first = run_report(
        project, cli("run", "--apply", "--run-id", "first", environment_overrides=zsign)
    )
    artifact_sha256 = sha256_file(project / SIGNED_IPA)
    created = apple.mutations()
    assert created_resources(apple) == RESOURCE_CREATION

    second = run_report(
        project, cli("run", "--apply", "--run-id", "second", environment_overrides=zsign)
    )

    (first_task,) = first["tasks"]
    (second_task,) = second["tasks"]
    assert first_task["cache"]["rebuild"] is True
    assert second_task["cache"]["rebuild"] is False
    assert second_task["cache"]["reason"] == "cache-hit"
    assert second_task["cache"]["fingerprint_sha256"] == first_task["cache"]["fingerprint_sha256"]
    assert second_task["verification"]["passed"] is True
    assert second_task["verification"]["artifact_sha256"] == artifact_sha256
    assert sha256_file(project / SIGNED_IPA) == artifact_sha256
    assert apple.mutations() == created


def test_run_without_apply_plans_apple_changes_and_makes_none(
    cli: Cli, project: Path, apple: Apple
) -> None:
    plan = succeed(cli("run", "--run-id", "plan-only"))

    assert plan["command"] == "plan"
    assert plan["apply"] is False
    assert plan["counts"] == {"no-op": 1, "safe-automatic": len(RESOURCE_CREATION)}
    assert apple.calls()
    assert apple.mutations() == []
    assert apple.state()["bundle_ids"] == []
    assert not (project / "work/signed").exists()


def test_publishing_a_task_not_approved_for_publication_is_refused_before_any_side_effect(
    cli: Cli, project: Path, apple: Apple, source: Source
) -> None:
    outcome = cli("run", "--apply", "--publish", "--task", "Unapproved", "--run-id", "unapproved")

    assert outcome.exit_code == 2
    assert outcome.error["code"] == "config.invalid"
    assert outcome.error["message"] == "selected tasks are not approved for publication"
    assert outcome.error["details"] == {"task_names": ["Unapproved"]}
    assert source.requests == []
    assert apple.calls() == []
    assert not list((project / "work").rglob("source.ipa"))


def test_default_selection_processes_only_tasks_approved_for_publication(
    cli: Cli, project: Path
) -> None:
    inspected = succeed(cli("inspect", "--run-id", "default"))

    assert [task["task_name"] for task in inspected["tasks"]] == ["Fixture"]


def test_default_selection_without_an_approved_task_is_refused(
    cli: Cli, project: Path, source: Source
) -> None:
    configuration = project / "configs/tasks.toml"
    configuration.write_text(
        configuration.read_text().replace(
            "publication_enabled = true", "publication_enabled = false"
        )
    )

    outcome = cli("run", "--apply", "--publish", "--run-id", "none-approved")

    assert outcome.exit_code == 2
    assert outcome.error["code"] == "config.invalid"
    assert outcome.error["message"] == "production selection has no publication-enabled tasks"
    assert source.requests == []


def test_source_that_differs_from_its_reviewed_digest_is_rejected(
    cli: Cli, project: Path, apple: Apple, source: Source
) -> None:
    build_fixture_ipa(source.directory / "Fixture.ipa", extra_extension=True)

    outcome = cli("run", "--apply", "--run-id", "drifted")

    assert outcome.exit_code == 2
    assert outcome.error["code"] == "signing.plan_invalid"
    assert outcome.error["details"] == {"diagnostics": ["Fixture:source.digest_mismatch"]}
    assert apple.calls() == []
    assert not (project / "work/signed").exists()


def test_bundle_without_a_signing_rule_stops_the_run_before_apple_changes(
    cli: Cli, workspace: Path, apple: Apple, source: Source
) -> None:
    build_fixture_ipa(source.directory / "Fixture.ipa", extra_extension=True)
    write_configuration(workspace, source)

    outcome = cli("run", "--apply", "--run-id", "unknown-bundle")

    assert outcome.exit_code == 2
    assert outcome.error["code"] == "signing.plan_invalid"
    assert outcome.error["details"] == {"diagnostics": ["Fixture:config.unconfigured_bundle"]}
    assert apple.calls() == []
    assert not (workspace / "work/signed").exists()
