"""Compatibility locks for the legacy single-bundle signing pipeline."""

from __future__ import annotations

import base64
import hashlib
import json
import plistlib
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import run_signing
import sync_profiles_asc

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "baseline"


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


def _verify_asset(path: Path, expected: dict[str, Any]) -> None:
    """Fail fixture setup before use when size or digest drifts."""
    assert path.stat().st_size == expected["size"], f"unexpected size for {path.name}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == expected["sha256"], f"unexpected SHA-256 for {path.name}"


def _write_ipa(path: Path, bundle_id: str = "com.example.legacy", version: str = "1.2.3") -> None:
    info = {
        "CFBundleIdentifier": bundle_id,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": "123",
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Payload/Legacy.app/Info.plist", plistlib.dumps(info))


def test_livecontainer_release_fixture_records_reviewed_assets() -> None:
    baseline = _load_json("livecontainer-3.8.0.json")

    assert baseline["tag"] == "3.8.0"
    assert baseline["commit"] == "e370a92dfc03ce109ebce00ed4a7cfc64ad1c801"
    assert [(asset["name"], asset["size"], asset["sha256"]) for asset in baseline["assets"]] == [
        (
            "LiveContainer.ipa",
            4_707_271,
            "b6fea95e30083382e29ffef88fa1aaa40b5069e1112e5307d490dab04648bba6",
        ),
        (
            "LiveContainer+SideStore.ipa",
            35_403_538,
            "97dc0fd2202fd4460efcab389943b8d5fdbb4988efff76b116b92b84a4662425",
        ),
    ]


def test_livecontainer_fixture_setup_rejects_drift(tmp_path: Path) -> None:
    expected = _load_json("livecontainer-3.8.0.json")["assets"][0]
    candidate = tmp_path / expected["name"]
    candidate.write_bytes(b"not the reviewed release asset")

    with pytest.raises(AssertionError, match="unexpected size"):
        _verify_asset(candidate, expected)


def test_legacy_toml_and_root_bundle_id_are_preserved(tmp_path: Path) -> None:
    config = tmp_path / "tasks.toml"
    config.write_text(
        """
[[tasks]]
task_name = "Legacy"
app_name = "Legacy App"
bundle_id = "com.example.legacy"
ipa_url = "https://example.com/Legacy.ipa"
""".strip()
    )

    task = run_signing.read_toml(config)["tasks"][0]

    assert task["bundle_id"] == "com.example.legacy"
    assert run_signing.validate_task(task) == (True, None)
    assert run_signing.should_rebuild_task(task, "Legacy", {"tasks": {}}, None) == (
        True,
        "https://example.com/Legacy.ipa",
        None,
    )


def test_legacy_release_selection_warns_and_uses_first_match(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    client = run_signing.GitHubAPIClient()
    release = {
        "assets": [
            {"name": "first.ipa", "id": 1},
            {"name": "second.ipa", "id": 2},
            {"name": "notes.txt", "id": 3},
        ]
    }

    selected = client.find_matching_asset(release, "*.ipa")

    assert selected == {"name": "first.ipa", "id": 1}
    assert "Multiple assets match" in capsys.readouterr().out


def test_legacy_release_selection_reports_zero_matches(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    client = run_signing.GitHubAPIClient()

    assert client.find_matching_asset({"assets": [{"name": "readme.txt"}]}, "*.ipa") is None
    output = capsys.readouterr().out
    assert "No assets match pattern '*.ipa'" in output
    assert "readme.txt" in output


def test_certificate_normalization_uses_environment_and_removes_plaintext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password = "fixture password ; not shell syntax"
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs["env"]))
        output = Path(argv[argv.index("-out") + 1])
        output.write_bytes(b"normalized")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(run_signing.subprocess, "run", fake_run)

    result = run_signing.prepare_signing_p12(
        base64.b64encode(b"legacy p12").decode(), tmp_path, password
    )

    assert result == tmp_path / "cert.p12"
    assert result.read_bytes() == b"normalized"
    assert not (tmp_path / "cert_apple.p12").exists()
    assert not (tmp_path / "cert.pem").exists()
    assert len(calls) == 2
    assert all(password not in argv for argv, _ in calls)
    assert all(env["ZSIGN_P12_PW"] == password for _, env in calls)


def test_profile_download_decodes_current_asc_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "Legacy.mobileprovision"
    payload = base64.b64encode(b"sanitized profile bytes").decode()
    run_asc = MagicMock(return_value={"data": {"attributes": {"profileContent": payload}}})
    monkeypatch.setattr(sync_profiles_asc, "run_asc", run_asc)

    sync_profiles_asc.download_profile("profile-1", output)

    assert output.read_bytes() == b"sanitized profile bytes"
    run_asc.assert_called_once_with(["profiles", "view", "--id", "profile-1"])


def test_profile_regeneration_deletes_then_creates_then_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync_profiles_asc, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(sync_profiles_asc, "find_bundle_id", lambda _: "bundle-resource")
    monkeypatch.setattr(
        sync_profiles_asc, "find_profile", lambda _name, _bundle: {"id": "old-profile"}
    )
    delete_profile = MagicMock()
    create_profile = MagicMock(return_value="new-profile")
    download_profile = MagicMock()
    monkeypatch.setattr(sync_profiles_asc, "delete_profile", delete_profile)
    monkeypatch.setattr(sync_profiles_asc, "create_profile", create_profile)
    monkeypatch.setattr(sync_profiles_asc, "download_profile", download_profile)

    sync_profiles_asc.sync_profile(
        {
            "task_name": "Legacy",
            "app_name": "Legacy App",
            "bundle_id": "com.example.legacy",
        },
        ["certificate-1"],
        ["device-1"],
    )

    delete_profile.assert_called_once_with("old-profile")
    create_profile.assert_called_once_with(
        "Legacy App Dev", "bundle-resource", ["certificate-1"], ["device-1"]
    )
    download_profile.assert_called_once_with("new-profile", tmp_path / "Legacy.mobileprovision")


def test_missing_profile_is_created_with_enabled_ios_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync_profiles_asc, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(sync_profiles_asc, "find_bundle_id", lambda _: "bundle-resource")
    monkeypatch.setattr(sync_profiles_asc, "find_profile", lambda *_: None)
    monkeypatch.setattr(
        sync_profiles_asc,
        "fetch_devices",
        lambda: [
            {"id": "iphone", "attributes": {"deviceClass": "IPHONE"}},
            {"id": "watch", "attributes": {"deviceClass": "APPLE_WATCH"}},
        ],
    )
    monkeypatch.setattr(sync_profiles_asc, "fetch_certificates", lambda: [{"id": "cert"}])
    create_profile = MagicMock(return_value="profile")
    download_profile = MagicMock()
    monkeypatch.setattr(sync_profiles_asc, "create_profile", create_profile)
    monkeypatch.setattr(sync_profiles_asc, "download_profile", download_profile)

    sync_profiles_asc.download_existing_profile(
        {
            "task_name": "Legacy",
            "app_name": "Legacy App",
            "bundle_id": "com.example.legacy",
        }
    )

    create_profile.assert_called_once_with(
        "Legacy App Dev", "bundle-resource", ["cert"], ["iphone"]
    )
    download_profile.assert_called_once_with("profile", tmp_path / "Legacy.mobileprovision")


def test_missing_bundle_id_fails_before_profile_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sync_profiles_asc, "find_bundle_id", lambda _: None)
    create_profile = MagicMock()
    monkeypatch.setattr(sync_profiles_asc, "create_profile", create_profile)

    with pytest.raises(SystemExit) as error:
        sync_profiles_asc.sync_profile(
            {
                "task_name": "Legacy",
                "app_name": "Legacy App",
                "bundle_id": "com.example.missing",
            },
            ["cert"],
            ["device"],
        )

    assert error.value.code == 1
    create_profile.assert_not_called()


def test_legacy_main_preserves_output_names_r2_key_and_registry_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "tasks.toml"
    config.write_text(
        """
[[tasks]]
task_name = "Legacy"
app_name = "Legacy App"
bundle_id = "com.example.target"
ipa_url = "https://example.com/Legacy.ipa"
slug = "legacy"
""".strip()
    )
    profile = tmp_path / "work" / "profiles" / "Legacy.mobileprovision"
    profile.parent.mkdir(parents=True)
    profile.write_bytes(b"profile")
    monkeypatch.setenv("CONFIG_TOML", str(config))
    monkeypatch.setenv("APPLE_DEV_CERT_P12_ENCODED", "fixture")
    monkeypatch.setenv("APPLE_DEV_CERT_PASSWORD", "secret-password")
    monkeypatch.setenv("REBUILD_TASKS", '["Legacy"]')

    store = MagicMock()
    store.ipa_key.return_value = "apps/legacy/1.2.3/Legacy_App.ipa"
    store.upload_ipa.return_value = "https://ipa.example/apps/legacy/1.2.3/Legacy_App.ipa"
    monkeypatch.setattr(run_signing.r2_store.R2Store, "from_env", MagicMock(return_value=store))
    monkeypatch.setattr(run_signing, "find_zsign", lambda: "/tools/zsign")
    monkeypatch.setattr(run_signing, "prepare_signing_p12", lambda *_: tmp_path / "cert.p12")

    def fake_download(_command: str, cwd: Path | None = None) -> None:
        del cwd
        _write_ipa(tmp_path / "work" / "Legacy_App" / "Legacy_App_ori.ipa")

    def fake_zsign(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        output = Path(argv[argv.index("-o") + 1])
        _write_ipa(output, bundle_id="com.example.target")
        return subprocess.CompletedProcess(argv, 0, "", "")

    publish_registry = MagicMock(return_value=True)
    monkeypatch.setattr(run_signing, "run", fake_download)
    monkeypatch.setattr(run_signing.subprocess, "run", fake_zsign)
    monkeypatch.setattr(run_signing, "publish_registry", publish_registry)

    assert run_signing.main() == 0
    assert (tmp_path / "work" / "Legacy_App" / "Legacy_App_ori.ipa").exists()
    assert (tmp_path / "work" / "Legacy_App" / "Legacy_App.ipa").exists()
    store.ipa_key.assert_called_once_with("legacy", "1.2.3", "Legacy_App.ipa")
    publish_registry.assert_called_once()
    updates = publish_registry.call_args.args[1]
    assert updates == [
        {
            "slug": "legacy",
            "name": "Legacy App",
            "bundleId": "com.example.target",
            "version": "1.2.3",
            "ipaUrl": "https://ipa.example/apps/legacy/1.2.3/Legacy_App.ipa",
            "iconUrl": "",
        }
    ]


def test_github_output_contract_is_append_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    sync_profiles_asc.write_github_output("devices_changed", "false")
    sync_profiles_asc.write_github_output("missing_profiles", '["Legacy"]')

    assert output.read_text().splitlines() == [
        "devices_changed=false",
        'missing_profiles=["Legacy"]',
    ]


def test_compatibility_contract_records_exit_codes_and_redaction() -> None:
    contract = _load_json("compatibility-contract.json")

    assert contract["run_signing"]["exit_codes"] == {
        "0": "success or no tasks",
        "2": "configuration error",
        "3": "missing signing or R2 environment",
        "4": "certificate normalization failure",
        "5": "one or more task or publication failures",
        "6": "GitHub authentication missing",
    }
    assert "APPLE_DEV_CERT_PASSWORD" in contract["redaction"]["never_log"]
    assert "R2_SECRET_ACCESS_KEY" in contract["redaction"]["never_log"]


def test_legacy_main_exit_codes_for_config_and_missing_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONFIG_TOML", str(tmp_path / "missing.toml"))
    assert run_signing.main() == 2

    config = tmp_path / "tasks.toml"
    config.write_text(
        """
[[tasks]]
task_name = "Legacy"
app_name = "Legacy"
bundle_id = "com.example.legacy"
ipa_url = "https://example.com/Legacy.ipa"
""".strip()
    )
    monkeypatch.setenv("CONFIG_TOML", str(config))
    monkeypatch.delenv("APPLE_DEV_CERT_P12_ENCODED", raising=False)
    monkeypatch.delenv("APPLE_DEV_CERT_PASSWORD", raising=False)

    assert run_signing.main() == 3


def test_current_release_audit_requires_one_match_per_production_task() -> None:
    audit = _load_json("production-release-audit.json")

    assert audit["effective_glob"] == "*.ipa"
    assert [task["task_name"] for task in audit["tasks"]] == [
        "JHenTai",
        "Eros FE",
        "Asspp",
        "PiliPlus",
        "StikDebug",
    ]
    assert all(len(task["matches"]) == 1 for task in audit["tasks"])
