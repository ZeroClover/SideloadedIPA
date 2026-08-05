"""Fixture doubles for the signing backend and package verifier ports.

Conformance gate: `tests/test_backend_contract.py` runs identical
protocol-conformance checks over these doubles and over the production
`ZsignBackend`/`PackageVerifier` adapters, so a port change fails the
contract test until both sides move together.

When a port in `sideloadedipa/ports.py` changes (a member is added, removed,
or re-shaped), update the production adapters *and* these doubles in the
same commit: the contract test names both sides and fails until the
implementations agree again.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import (
    CertificateMaterial,
    SigningNodeResult,
    SigningPlan,
    SigningResult,
    VerificationFinding,
    VerificationResult,
)
from sideloadedipa.verification import (
    build_verification_result,
    required_verification_checks,
)


@dataclass
class FixtureCopyBackend:
    """Stand-in signing backend: copies bytes instead of signing them."""

    called: bool = False

    def sign(
        self,
        plan: SigningPlan,
        source_ipa: Path,
        output_ipa: Path,
        certificate: CertificateMaterial,
    ) -> SigningResult:
        del certificate
        self.called = True
        shutil.copy2(source_ipa, output_ipa)
        output_sha256 = hashlib.sha256(output_ipa.read_bytes()).hexdigest()
        return SigningResult(
            plan.plan_sha256,
            PurePosixPath(output_ipa.name),
            output_sha256,
            plan.backend,
            tuple(
                SigningNodeResult(
                    node.source_path,
                    output_sha256,
                    node.profile_sha256,
                    node.expected_entitlements_sha256,
                    0.0,
                )
                for node in plan.nodes
            ),
            0.1,
        )


@dataclass
class FixturePassingVerifier:
    """Stand-in verifier: reports every required check as passed."""

    calls: int = 0

    def verify(self, plan: SigningPlan, signed_ipa: Path) -> VerificationResult:
        self.calls += 1
        findings = tuple(
            VerificationFinding(path, check.replace("*", "arm64"), True)
            for path, check in required_verification_checks(plan)
        )
        return build_verification_result(
            plan,
            hashlib.sha256(signed_ipa.read_bytes()).hexdigest(),
            findings,
        )
