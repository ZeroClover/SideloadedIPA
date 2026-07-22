"""Tests for immutable domain model boundaries."""

from __future__ import annotations

import inspect
from dataclasses import is_dataclass

import pytest

from sideloadedipa.domain import (
    apple,
    bundle,
    common,
    config,
    pipeline,
    signing,
)


@pytest.mark.parametrize("module", [apple, bundle, common, config, pipeline, signing])
def test_every_domain_dataclass_is_frozen(module: object) -> None:
    classes = [
        value
        for _, value in inspect.getmembers(module, inspect.isclass)
        if value.__module__ == module.__name__ and is_dataclass(value)
    ]

    assert classes
    assert all(value.__dataclass_params__.frozen for value in classes)
