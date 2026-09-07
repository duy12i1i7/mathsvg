#!/usr/bin/env python3
"""Generate an honest Markdown report from benchmark result JSON."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mathzip_bench.plots import load_results
from mathzip_bench.report import generate_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        document = load_results(arguments.results.resolve())
        generate_report(document, arguments.output.resolve())
    except (OSError, ValueError) as exc:
        print(f"report generation failed: {exc}", file=sys.stderr)
        return 2
    print(f"report: {arguments.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
