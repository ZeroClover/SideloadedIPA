"""Compatibility alias for :mod:`sideloadedipa.legacy.r2_store`."""

from __future__ import annotations

import sys

from _bootstrap import load_legacy

_implementation = load_legacy("r2_store")

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation
