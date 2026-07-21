"""Compatibility alias for the packaged backend comparison."""

from __future__ import annotations

import sys

from _bootstrap import load_legacy

_implementation = load_legacy("compare_backend_qualification")

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation
