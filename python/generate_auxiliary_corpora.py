#!/usr/bin/env python3
"""Generate the local mixed-file and Git-snapshot benchmark corpora."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mathzip_bench.auxiliary import generate_auxiliary_corpora


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0x4D5A2025)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        result = generate_auxiliary_corpora(
            arguments.output.resolve(), arguments.seed, arguments.force
        )
    except (OSError, ValueError) as exc:
        print(f"auxiliary corpus generation failed: {exc}", file=sys.stderr)
        return 2
    mixed = result["mixed"]
    snapshots = result["git_snapshots"]
    print(
        f"generated {len(mixed['entries'])} mixed files and "
        f"{len(snapshots['versions'])} source snapshots in {arguments.output.resolve()}"
    )
    for skipped in mixed["skipped_optional_files"]:
        print(
            f"optional mixed file skipped: {skipped['path']}: {skipped['reason']}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
