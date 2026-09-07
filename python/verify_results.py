#!/usr/bin/env python3
"""Validate benchmark schema, medians, derived metrics and SHA-256 evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mathzip_bench.verification import (
    resolve_results_path,
    validate_csv,
    validate_plot_manifest,
    validate_result_document,
    verify_prepared_datasets,
    verify_generated_corpora,
    verify_synthetic_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, nargs="?")
    parser.add_argument("--csv", type=Path, help="default: results.csv beside JSON")
    parser.add_argument(
        "--plots",
        type=Path,
        help="validate plot_manifest.json and every named plot artifact",
    )
    parser.add_argument("--synthetic-manifest", type=Path)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--generated-root", type=Path)
    parser.add_argument(
        "--strict", action="store_true", help="treat failed rows as validation errors"
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help=(
            "with --strict, retain all strict evidence checks but report recorded "
            "failed rows as warnings"
        ),
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    if arguments.allow_failures and not arguments.strict:
        print("--allow-failures requires --strict", file=sys.stderr)
        return 2
    if arguments.plots and not arguments.results:
        print("--plots requires a benchmark results target", file=sys.stderr)
        return 2
    errors: list[str] = []
    warnings: list[str] = []
    if not arguments.results and not (
        arguments.synthetic_manifest or arguments.dataset_root or arguments.generated_root
    ):
        print("at least one results/corpus target is required", file=sys.stderr)
        return 2
    try:
        if arguments.results:
            results_path = resolve_results_path(arguments.results.resolve())
            document = json.loads(results_path.read_text(encoding="utf-8"))
            new_errors, new_warnings = validate_result_document(
                document,
                strict=arguments.strict,
                allow_failures=arguments.allow_failures,
            )
            errors.extend(new_errors)
            warnings.extend(new_warnings)
            csv_path = arguments.csv.resolve() if arguments.csv else results_path.with_suffix(
                ".csv"
            )
            new_errors, new_warnings = validate_csv(
                csv_path, document.get("results", [])
            )
            errors.extend(new_errors)
            warnings.extend(new_warnings)
            if arguments.plots:
                new_errors, new_warnings = validate_plot_manifest(
                    document,
                    arguments.plots.resolve(),
                    Path(__file__).resolve().parent
                    / "mathzip_bench"
                    / "plots.py",
                )
                errors.extend(new_errors)
                warnings.extend(new_warnings)
        if arguments.synthetic_manifest:
            new_errors, new_warnings = verify_synthetic_manifest(
                arguments.synthetic_manifest.resolve()
            )
            errors.extend(new_errors)
            warnings.extend(new_warnings)
        if arguments.dataset_root:
            new_errors, new_warnings = verify_prepared_datasets(
                arguments.dataset_root.resolve()
            )
            errors.extend(new_errors)
            warnings.extend(new_warnings)
        if arguments.generated_root:
            new_errors, new_warnings = verify_generated_corpora(
                arguments.generated_root.resolve()
            )
            errors.extend(new_errors)
            warnings.extend(new_warnings)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"verification could not run: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        print(f"verification failed: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"verification passed with {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
