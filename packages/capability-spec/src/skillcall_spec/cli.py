from __future__ import annotations

import argparse
from pathlib import Path

from .conformance import ConformanceError, default_spec_root, validate_repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skillcall-spec")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate a capability-spec snapshot")
    validate.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=default_spec_root(),
    )
    validate.add_argument("--implementations", type=Path)
    args = parser.parse_args(argv)
    try:
        counts = validate_repository(args.root, args.implementations)
    except ConformanceError as exc:
        for error in exc.errors:
            print(f"ERROR: {error}")
        return 1
    summary = ", ".join(f"{key}={value}" for key, value in counts.items())
    print(f"skillcall conformance: OK ({summary})")
    return 0
