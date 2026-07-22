"""Tests for shared environment and publication-runtime helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

import sideloadedipa.pipeline.environment as commands
from sideloadedipa.config import load_configuration
from sideloadedipa.errors import ConfigurationError, ErrorCode


def test_revalidation_sends_secret_in_header_and_preserves_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class Response:
        status = 204

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    def open_url(request, *, timeout):  # type: ignore[no-untyped-def]
        seen["url"] = request.full_url
        seen["secret"] = request.get_header("X-revalidate-secret")
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(commands.urllib.request, "urlopen", open_url)

    assert commands.trigger_revalidation(
        {
            "VERCEL_REVALIDATE_SECRET": "a secret&value",
            "VERCEL_REVALIDATE_URL": "https://example.test/revalidate?scope=apps",
        }
    )
    assert seen == {
        "url": "https://example.test/revalidate?scope=apps",
        "secret": "a secret&value",
        "timeout": 30,
    }


def test_revalidation_reports_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        commands.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    assert not commands.trigger_revalidation({"VERCEL_REVALIDATE_SECRET": "secret"})


def test_publication_runtime_reports_missing_r2_credentials() -> None:
    configuration = load_configuration(Path("configs/tasks.toml"))

    with pytest.raises(ConfigurationError, match="R2 credentials") as caught:
        commands.publication_runtime(
            configuration,
            {"VERCEL_REVALIDATE_SECRET": "secret"},
        )

    assert caught.value.code is ErrorCode.CONFIG_MISSING
