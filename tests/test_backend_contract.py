"""Protocol-conformance contract for signing backend and verifier ports.

Every test double that stands in for the signing backend or package verifier
must pass the *same* conformance checks as the production adapters, in the
ordinary PR test suite, without credentials or a zsign binary. When a port
in `sideloadedipa/ports.py` changes, this contract fails until the adapters
and the doubles in `tests/fakes.py` are updated together.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import get_type_hints

import pytest

from sideloadedipa.adapters.signing import ZsignBackend
from sideloadedipa.ports import SigningBackend, Verifier
from sideloadedipa.verification.service import PackageVerifier
from tests.fakes import FixtureCopyBackend, FixturePassingVerifier


def _production_backend() -> ZsignBackend:
    # Construction only: no executable probe, signing, or credential access.
    return ZsignBackend(
        executable=Path("zsign"),
        expected_executable_sha256="0" * 64,
        profile_root=Path("profiles"),
    )


def _production_verifier() -> PackageVerifier:
    # Construction only: verification itself is never executed here.
    return PackageVerifier(
        Path("source.ipa"),
        (),
        datetime(2026, 7, 22, tzinfo=timezone.utc),
    )


BACKEND_IMPLEMENTATIONS = pytest.mark.parametrize(
    "backend",
    [
        pytest.param(FixtureCopyBackend(), id="fixture-double"),
        pytest.param(_production_backend(), id="zsign-backend"),
    ],
)

VERIFIER_IMPLEMENTATIONS = pytest.mark.parametrize(
    "verifier",
    [
        pytest.param(FixturePassingVerifier(), id="fixture-double"),
        pytest.param(_production_verifier(), id="package-verifier"),
    ],
)


def _method_shape(owner: type, name: str) -> list[inspect.Parameter]:
    method = inspect.getattr_static(owner, name)
    assert callable(method), f"{owner.__name__}.{name} is missing"
    signature = inspect.signature(method)
    return [parameter for parameter in signature.parameters.values() if parameter.name != "self"]


def _method_type_hints(owner: type, name: str) -> dict[str, object]:
    """Resolve the full parameter and return annotations of a port method."""

    method = inspect.getattr_static(owner, name)
    assert callable(method), f"{owner.__name__}.{name} is missing"
    return dict(get_type_hints(method))


def _assert_matches_port(implementation: object, port: type, method_name: str) -> None:
    protocol_parameters = _method_shape(port, method_name)
    actual_parameters = _method_shape(type(implementation), method_name)

    assert [parameter.name for parameter in actual_parameters] == [
        parameter.name for parameter in protocol_parameters
    ]
    assert [parameter.kind for parameter in actual_parameters] == [
        parameter.kind for parameter in protocol_parameters
    ]
    # Parameter names alone cannot stop a wrongly typed double: a
    # `verify(plan: str, signed_ipa: str) -> str` implementation must fail.
    # Compare fully resolved annotations (parameters plus return type).
    assert _method_type_hints(type(implementation), method_name) == _method_type_hints(
        port, method_name
    )


@BACKEND_IMPLEMENTATIONS
def test_backend_satisfies_the_signing_port(backend: object) -> None:
    assert isinstance(backend, SigningBackend)


@BACKEND_IMPLEMENTATIONS
def test_backend_sign_matches_the_port_call_shape(backend: object) -> None:
    _assert_matches_port(backend, SigningBackend, "sign")


@VERIFIER_IMPLEMENTATIONS
def test_verifier_satisfies_the_verifier_port(verifier: object) -> None:
    assert isinstance(verifier, Verifier)


@VERIFIER_IMPLEMENTATIONS
def test_verifier_verify_matches_the_port_call_shape(verifier: object) -> None:
    # The exact protocol the pipeline stages rely on: verify(plan, signed_ipa)
    # is the sole verifier interaction in the signing/verification stages.
    _assert_matches_port(verifier, Verifier, "verify")
