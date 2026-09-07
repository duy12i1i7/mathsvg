#!/usr/bin/env python3
"""Run MathZip and required baselines with real round-trip verification."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mathzip_bench.config import load_config, repository_root
from mathzip_bench.runner import run_benchmarks
from mathzip_bench.verification import validate_csv, validate_result_document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--mathzip-binary",
        default="target/release/mathzip",
        help="path or command name for the MathZip CLI",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--resume",
        type=Path,
        help=(
            "resume an interrupted run from its run directory or checkpoint.json; "
            "all run, source, binary, input, host, and timing identities must match"
        ),
    )
    parser.add_argument(
        "--codec", action="append", dest="codecs", help="run only this configured codec"
    )
    parser.add_argument(
        "--allow-short-run",
        action="store_true",
        help="allow <3 repeats or no warm-up; result is marked non-compliant",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when any input/codec result fails",
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
    if arguments.resume and arguments.output_dir:
        print("--output-dir cannot be combined with --resume", file=sys.stderr)
        return 2
    root = repository_root(__file__)
    try:
        config_path = arguments.config.resolve()
        config = load_config(config_path)
        binary = Path(arguments.mathzip_binary)
        mathzip_binary = (
            str(
                (
                    binary
                    if binary.is_absolute()
                    else root / binary
                ).resolve()
            )
            if binary.parent != Path(".") or "/" in arguments.mathzip_binary
            else arguments.mathzip_binary
        )
        result, run_directory = run_benchmarks(
            config,
            config_path=config_path,
            repository_root=root,
            mathzip_binary=mathzip_binary,
            output_override=arguments.output_dir.resolve()
            if arguments.output_dir
            else None,
            allow_short_run=arguments.allow_short_run,
            selected_codecs=arguments.codecs,
            resume_from=arguments.resume,
            require_publication_evidence=arguments.strict,
        )
    except (OSError, ValueError) as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"benchmark {result['run_id']}: {result['status']}; "
        f"{result['result_count']} rows, {result['failure_count']} failures"
    )
    print(f"results: {run_directory / 'results.json'}")
    if arguments.strict:
        errors, warnings = validate_result_document(
            result, strict=True, allow_failures=arguments.allow_failures
        )
        csv_errors, csv_warnings = validate_csv(
            run_directory / "results.csv", result["results"]
        )
        for warning in [*warnings, *csv_warnings]:
            print(f"warning: {warning}", file=sys.stderr)
        for error in [*errors, *csv_errors]:
            print(f"strict validation error: {error}", file=sys.stderr)
        if errors or csv_errors:
            return 1
    if not any(row["status"] == "ok" for row in result["results"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
