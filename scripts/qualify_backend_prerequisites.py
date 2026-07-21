"""Compatibility alias for the packaged Apple qualification helper."""

from __future__ import annotations

import sys

from _bootstrap import load_legacy

_implementation = load_legacy("qualify_backend_prerequisites")

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation
