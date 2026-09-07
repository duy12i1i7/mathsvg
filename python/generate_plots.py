#!/usr/bin/env python3
"""Generate benchmark figures from verified JSON results."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mathzip_bench.plots import generate_plots, load_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--format",
        action="append",
        dest="formats",
        choices=("png", "svg", "pdf"),
        help="repeat for multiple formats; default: png",
    )
    parser.add_argument(
        "--strict-dependencies",
        action="store_true",
        help="fail if matplotlib is unavailable",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        document = load_results(arguments.results.resolve())
        manifest = generate_plots(
            document,
            arguments.output.resolve(),
            formats=arguments.formats or ("png",),
        )
    except (OSError, ValueError) as exc:
        print(f"plot generation failed: {exc}", file=sys.stderr)
        return 2
    if manifest["status"] == "dependency_missing":
        print(
            "matplotlib is unavailable; wrote plot_data.json and a skip manifest",
            file=sys.stderr,
        )
        return 1 if arguments.strict_dependencies else 0
    print(
        f"generated {len(manifest['generated'])} plot file(s); "
        f"skipped {len(manifest['skipped_for_missing_metrics'])} plot type(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
