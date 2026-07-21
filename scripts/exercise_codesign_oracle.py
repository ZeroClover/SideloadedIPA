"""Compatibility alias for the packaged codesign oracle."""

from __future__ import annotations

import sys

from _bootstrap import load_legacy

_implementation = load_legacy("exercise_codesign_oracle")

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation
