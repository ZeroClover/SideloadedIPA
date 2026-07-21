"""Compatibility alias for the packaged qualification fixture builder."""

from __future__ import annotations

import sys

from _bootstrap import load_legacy

_implementation = load_legacy("build_backend_qualification_fixture")

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation
