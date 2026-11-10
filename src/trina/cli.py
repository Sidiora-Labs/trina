"""Command-line entry point of the trina package."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from trina import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trina",
        description="Training system for memory retrieval and context assembly.",
    )
    parser.add_argument("--version", action="version", version=f"trina {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
