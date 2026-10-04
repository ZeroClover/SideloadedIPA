"""Tests for bounded retries and additive Apple operation recovery."""

from __future__ import annotations

import pytest

from sideloadedipa.util.retrying import (
    RetryOperation,
    RetryPolicy,
    retry_call,
)


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


@pytest.mark.parametrize(
    "override",
    [
        {"max_attempts": True},
        {"max_attempts": 1.5},
        {"base_delay_seconds": float("nan")},
        {"base_delay_seconds": float("inf")},
        {"max_delay_seconds": float("nan")},
        {"max_delay_seconds": float("inf")},
        {"jitter_ratio": float("nan")},
        {"base_delay_seconds": True},
        {"max_delay_seconds": True},
        {"jitter_ratio": True},
    ],
)
def test_retry_policy_rejects_unbounded_values(override: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**override)  # type: ignore[arg-type]


def test_jitter_never_exceeds_maximum_delay() -> None:
    delays: list[float] = []

    def fail() -> None:
        raise OSError("transient")

    with pytest.raises(OSError):
        retry_call(
            operation_id="read:profiles",
            operation=RetryOperation.READ,
            action=fail,
            is_transient=lambda error: True,
            policy=RetryPolicy(base_delay_seconds=2, max_delay_seconds=2, jitter_ratio=1),
            sleep=delays.append,
            random_unit=lambda: 1,
        )
    assert delays == [2, 2]


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
