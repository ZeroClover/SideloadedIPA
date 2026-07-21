"""Compatibility alias for :mod:`sideloadedipa.legacy.check_changes`."""

from __future__ import annotations

import sys

from _bootstrap import load_legacy

_implementation = load_legacy("check_changes")

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation
