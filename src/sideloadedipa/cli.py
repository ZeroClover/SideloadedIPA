"""Command-line entry point for the typed pipeline."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from sideloadedipa import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sideloadedipa")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
