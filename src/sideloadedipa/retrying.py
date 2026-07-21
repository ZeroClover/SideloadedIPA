"""Bounded retries for operations with explicit idempotency contracts."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")


class RetryOperation(StrEnum):
    READ = "read"
    CONTENT_ADDRESSED_UPLOAD = "content-addressed-upload"
    REGISTRY_REPLACE = "registry-replace"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 2.0
    jitter_ratio: float = 0.25

    def __post_init__(self) -> None:
        if (
            self.max_attempts < 1
            or self.base_delay_seconds < 0
            or self.max_delay_seconds < self.base_delay_seconds
            or not 0 <= self.jitter_ratio <= 1
        ):
            raise ValueError("invalid retry policy")


def retry_call(
    *,
    operation_id: str,
    operation: RetryOperation,
    action: Callable[[], T],
    is_transient: Callable[[Exception], bool],
    policy: RetryPolicy = RetryPolicy(),
    sleep: Callable[[float], None] = time.sleep,
    random_unit: Callable[[], float] = random.random,
) -> T:
    """Retry one safe operation without changing its identity or arguments."""

    if not operation_id:
        raise ValueError("retry operation identity must be non-empty")
    del operation
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return action()
        except Exception as error:
            if attempt == policy.max_attempts or not is_transient(error):
                raise
            base = min(
                policy.max_delay_seconds,
                policy.base_delay_seconds * (2 ** (attempt - 1)),
            )
            jitter = 1 + policy.jitter_ratio * ((2 * random_unit()) - 1)
            sleep(base * jitter)
    raise AssertionError("retry loop exhausted without returning or raising")


def reconcile_additive_once(
    *,
    operation_id: str,
    read_current: Callable[[], Sequence[T]],
    matches: Callable[[T], bool],
    create: Callable[[], T],
    is_transient: Callable[[Exception], bool],
    on_created: Callable[[T], None] | None = None,
    policy: RetryPolicy = RetryPolicy(),
    sleep: Callable[[float], None] = time.sleep,
    random_unit: Callable[[], float] = random.random,
) -> tuple[T, bool]:
    """Create at most once, then resolve an ambiguous response by re-reading state."""

    def read() -> Sequence[T]:
        return retry_call(
            operation_id=f"{operation_id}:read",
            operation=RetryOperation.READ,
            action=read_current,
            is_transient=is_transient,
            policy=policy,
            sleep=sleep,
            random_unit=random_unit,
        )

    if existing := next((value for value in read() if matches(value)), None):
        return existing, False
    try:
        created = create()
    except Exception as error:
        if not is_transient(error):
            raise
        if recovered := next((value for value in read() if matches(value)), None):
            if on_created is not None:
                on_created(recovered)
            return recovered, True
        raise error
    if on_created is not None:
        on_created(created)
    return created, True
