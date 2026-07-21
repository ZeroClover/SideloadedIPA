"""Compatibility alias for :mod:`sideloadedipa.legacy.app_icon`."""

from __future__ import annotations

import sys

from _bootstrap import load_legacy

_implementation = load_legacy("app_icon")

if __name__ == "__main__":
    raise SystemExit(_implementation.main(sys.argv))

sys.modules[__name__] = _implementation
