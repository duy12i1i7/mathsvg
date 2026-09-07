#!/usr/bin/env python3
"""Download and safely prepare checksummed benchmark corpora."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mathzip_bench.config import load_config, repository_root
from mathzip_bench.datasets import (
    DatasetError,
    prepare_dataset,
    selected_dataset_names,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        help="default: <repository>/datasets/manifests",
    )
    parser.add_argument("--download-cache", type=Path)
    parser.add_argument(
        "--dataset", action="append", dest="datasets", help="override config selection"
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    root = repository_root(__file__)
    try:
        config = load_config(arguments.config.resolve())
        names = arguments.datasets or selected_dataset_names(config)
        manifest_dir = (
            arguments.manifest_dir.resolve()
            if arguments.manifest_dir
            else root / "datasets" / "manifests"
        )
        if not names:
            raise DatasetError("no enabled datasets in config")
        failures = 0
        for name in names:
            manifest_path = manifest_dir / f"{name}.json"
            try:
                result = prepare_dataset(
                    manifest_path,
                    arguments.output.resolve(),
                    arguments.download_cache.resolve()
                    if arguments.download_cache
                    else None,
                    force=arguments.force,
                    timeout=arguments.timeout,
                )
            except (DatasetError, OSError) as exc:
                failures += 1
                print(f"{name}: FAILED: {exc}", file=sys.stderr)
            else:
                print(f"{name}: {result['status']} at {result['path']}")
        return 1 if failures else 0
    except (DatasetError, OSError, ValueError) as exc:
        print(f"dataset preparation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
