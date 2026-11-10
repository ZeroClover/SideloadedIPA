"""Values and builders the end-to-end fixtures and scenarios share."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sideloadedipa.tools.build_backend_qualification_fixture import info_plist, zip_info
from tests.signing_fixtures import unsigned_macho

TEAM_ID = "E2ETEAM001"
P12_PASSWORD = "e2e-p12-password"
DEVICE_UDID = "00008110-000E2E0000000001"
CONSOLE_SCRIPT = Path(sys.executable).parent / "sideloadedipa"
FAKE_ASC_SOURCE = Path(__file__).parent / "fake_asc.py"

ROOT_BUNDLE = "Payload/Fixture.app"
EXTENSION_BUNDLE = "Payload/Fixture.app/PlugIns/Share.appex"
HELPER_EXECUTABLE = "Payload/Fixture.app/Frameworks/helper"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_fixture_ipa(path: Path, *, extra_extension: bool = False) -> None:
    """Write an unsigned app with one extension and one loose helper executable."""

    bundles = [
        (ROOT_BUNDLE, "Fixture", "com.upstream.fixture", "APPL"),
        (EXTENSION_BUNDLE, "Share", "com.upstream.fixture.Share", "XPC!"),
    ]
    if extra_extension:
        bundles.append(
            (
                "Payload/Fixture.app/PlugIns/Widget.appex",
                "Widget",
                "com.upstream.fixture.Widget",
                "XPC!",
            )
        )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for bundle, executable, identifier, package_type in bundles:
            archive.writestr(
                zip_info(f"{bundle}/Info.plist", executable=False),
                info_plist(executable, identifier, package_type),
            )
            archive.writestr(zip_info(f"{bundle}/{executable}", executable=True), unsigned_macho())
        archive.writestr(zip_info(HELPER_EXECUTABLE, executable=True), unsigned_macho())


@dataclass(frozen=True, slots=True)
class Outcome:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def payload(self) -> dict[str, Any]:
        """The command's JSON result document."""

        document = json.loads(self.stdout)
        assert isinstance(document, dict)
        return document

    @property
    def error(self) -> dict[str, Any]:
        """The typed JSON diagnostic a failed command writes last on stderr."""

        document = json.loads(self.stderr.strip().splitlines()[-1])
        assert isinstance(document, dict)
        return document


@dataclass(frozen=True, slots=True)
class Apple:
    """Handle on the stand-in App Store Connect account."""

    root: Path

    def calls(self) -> list[list[str]]:
        log = self.root / "state" / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def mutations(self) -> list[list[str]]:
        """Invocations that changed the account, in order, without output flags."""

        return [call[: call.index("--output")] for call in self.calls() if _is_mutation(call)]

    def state(self) -> dict[str, Any]:
        document = json.loads((self.root / "state" / "state.json").read_text(encoding="utf-8"))
        assert isinstance(document, dict)
        return document

    def issued_profile(self, bundle_identifier: str) -> bytes:
        """The profile content Apple holds for one App ID."""

        state = self.state()
        (bundle,) = [
            value for value in state["bundle_ids"] if value["identifier"] == bundle_identifier
        ]
        (profile,) = [value for value in state["profiles"] if value["bundle"] == bundle["id"]]
        return base64.b64decode(profile["profileContent"])


def _is_mutation(call: list[str]) -> bool:
    return any(verb in call for verb in ("create", "add"))


@dataclass(frozen=True, slots=True)
class Source:
    """Handle on the HTTPS host that serves unsigned IPAs."""

    directory: Path
    base_url: str
    requests: list[str]

    def url(self, name: str) -> str:
        return f"{self.base_url}/{name}"


@dataclass(frozen=True, slots=True)
class Tls:
    authority_path: Path
    certificate_path: Path
    key_path: Path


def write_configuration(workspace: Path, source: Source, *, ipa_name: str = "Fixture.ipa") -> None:
    """Configure one approved multi-bundle task and one task not approved to publish."""

    digest = sha256_file(source.directory / ipa_name)
    (workspace / "configs" / "tasks.toml").write_text(
        f"""
[[tasks]]
task_name = "Fixture"
app_name = "Fixture App"
bundle_id = "io.example.e2e.fixture"
ipa_url = "{source.url(ipa_name)}"
ipa_sha256 = "{digest}"
slug = "Fixture"
publication_enabled = true

[[tasks.signing.bundles]]
source_bundle_id = "com.upstream.fixture"
target_bundle_id = "io.example.e2e.fixture"
role = "root"
required_capabilities = ["HEALTHKIT"]
entitlement_mode = "profile"

[[tasks.signing.bundles]]
source_bundle_id = "com.upstream.fixture.Share"
entitlement_mode = "profile"

[[tasks]]
task_name = "Unapproved"
app_name = "Unapproved App"
bundle_id = "io.example.e2e.unapproved"
ipa_url = "{source.url(ipa_name)}"
ipa_sha256 = "{digest}"
slug = "Unapproved"
publication_enabled = false
""",
        encoding="utf-8",
    )
