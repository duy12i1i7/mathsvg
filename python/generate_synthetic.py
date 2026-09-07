#!/usr/bin/env python3
"""Generate MathZip's deterministic synthetic benchmark corpus."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mathzip_bench.synthetic import (
    DEFAULT_FAMILIES,
    DEFAULT_NOISE,
    DEFAULT_SIZES,
    corpus_manifest_is_current,
    generate_corpus,
)


def _csv_ints(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("sizes must be comma-separated integers") from exc


def _csv_floats(value: str) -> tuple[float, ...]:
    try:
        return tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "noise values must be comma-separated numbers"
        ) from exc


def _csv_strings(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="corpus directory")
    parser.add_argument("--seed", type=int, default=0x4D5A2025)
    parser.add_argument(
        "--sizes",
        type=_csv_ints,
        default=DEFAULT_SIZES,
        help="comma-separated byte sizes (default: 0,1,31,256,4096,65536)",
    )
    parser.add_argument(
        "--noise",
        type=_csv_floats,
        default=DEFAULT_NOISE,
        help="comma-separated noise densities",
    )
    parser.add_argument(
        "--families",
        type=_csv_strings,
        default=DEFAULT_FAMILIES,
        help="comma-separated family names",
    )
    parser.add_argument("--force", action="store_true", help="regenerate existing corpus")
    parser.add_argument(
        "--check-current",
        action="store_true",
        help=(
            "exit successfully only when the existing manifest matches this "
            "generator and configuration"
        ),
    )
    parser.add_argument(
        "--list-families", action="store_true", help="print family names and exit"
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    if arguments.list_families:
        print("\n".join(DEFAULT_FAMILIES))
        return 0
    if arguments.check_current:
        if arguments.force:
            print("--check-current cannot be combined with --force", file=sys.stderr)
            return 2
        current = corpus_manifest_is_current(
            output=arguments.output.resolve(),
            seed=arguments.seed,
            sizes=arguments.sizes,
            noise_densities=arguments.noise,
            families=arguments.families,
        )
        if current:
            print(f"synthetic corpus is current: {arguments.output.resolve()}")
            return 0
        print(
            f"synthetic corpus is missing or stale: {arguments.output.resolve()}",
            file=sys.stderr,
        )
        return 1
    try:
        manifest = generate_corpus(
            output=arguments.output.resolve(),
            seed=arguments.seed,
            sizes=arguments.sizes,
            noise_densities=arguments.noise,
            families=arguments.families,
            force=arguments.force,
        )
    except (OSError, ValueError) as exc:
        print(f"synthetic generation failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"generated {len(manifest['entries'])} files in "
        f"{arguments.output.resolve()} (seed={arguments.seed})"
    )
    for reason in manifest["skipped_optional_samples"]:
        print(f"optional sample skipped: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
