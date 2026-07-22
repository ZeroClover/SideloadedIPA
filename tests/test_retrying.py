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
