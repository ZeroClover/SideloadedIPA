"""Tests for bounded retries and additive Apple operation recovery."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from sideloadedipa.retrying import (
    RetryOperation,
    RetryPolicy,
    reconcile_additive_once,
    retry_call,
)


@dataclass(frozen=True)
class Resource:
    resource_id: str
    target: str


def test_safe_retry_is_bounded_with_exponential_jitter() -> None:
    calls = 0
    delays: list[float] = []

    def action() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError("transient")
        return "ok"

    result = retry_call(
        operation_id="read:profiles",
        operation=RetryOperation.READ,
        action=action,
        is_transient=lambda error: isinstance(error, OSError),
        policy=RetryPolicy(base_delay_seconds=1, max_delay_seconds=4),
        sleep=delays.append,
        random_unit=lambda: 0.5,
    )

    assert result == "ok"
    assert calls == 3
    assert delays == [1, 2]


def test_non_transient_failure_is_not_retried() -> None:
    calls = 0

    def action() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("invalid")

    with pytest.raises(ValueError):
        retry_call(
            operation_id="read:profiles",
            operation=RetryOperation.READ,
            action=action,
            is_transient=lambda error: isinstance(error, OSError),
            sleep=lambda delay: None,
        )

    assert calls == 1


def test_ambiguous_additive_create_is_confirmed_without_second_create() -> None:
    resources: list[Resource] = []
    recorded: list[str] = []
    creates = 0

    def create() -> Resource:
        nonlocal creates
        creates += 1
        resources.append(Resource("ID-1", "io.example.app"))
        raise OSError("response lost after create")

    resource, created = reconcile_additive_once(
        operation_id="bundle-id:io.example.app",
        read_current=lambda: resources,
        matches=lambda value: value.target == "io.example.app",
        create=create,
        is_transient=lambda error: isinstance(error, OSError),
        on_created=lambda value: recorded.append(value.resource_id),
        sleep=lambda delay: None,
    )

    assert (resource.resource_id, created, creates) == ("ID-1", True, 1)
    assert recorded == ["ID-1"]


def test_unconfirmed_additive_create_is_never_repeated() -> None:
    creates = 0

    def create() -> Resource:
        nonlocal creates
        creates += 1
        raise OSError("ambiguous")

    with pytest.raises(OSError):
        reconcile_additive_once(
            operation_id="bundle-id:io.example.app",
            read_current=lambda: (),
            matches=lambda value: value.target == "io.example.app",
            create=create,
            is_transient=lambda error: isinstance(error, OSError),
            sleep=lambda delay: None,
        )

    assert creates == 1
