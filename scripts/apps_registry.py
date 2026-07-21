"""Compatibility alias for :mod:`sideloadedipa.legacy.apps_registry`."""

from __future__ import annotations

import sys

from _bootstrap import load_legacy

_implementation = load_legacy("apps_registry")

sys.modules[__name__] = _implementation
