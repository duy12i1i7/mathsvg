#!/usr/bin/env python3
"""Run a holdout-safe, runtime-preflighted determinism development matrix."""

from __future__ import annotations

import argparse
import json
import pathlib
import platform
import subprocess
import sys
from typing import Sequence

from mathsvg.python.benchmarks.freeze import git_identity, sha256_file
from mathsvg.python.benchmarks.runner import (
    RunnerError as BenchmarkRunnerError,
    build_native_specs,
)
from mathsvg.python.determinism.runner import (
    BuildSpec,
    RunRequest,
    RunnerError,
    compiler_identity,
    evaluate_gate,
    run_matrix,
)
from mathsvg.python.determinism.schema import (
    DeterminismError,
    write_csv,
)
from mathsvg.python.datasets.manifest import (
    ManifestError,
    canonical_manifest_sha256,
    load_manifest,
)
from mathsvg.python.repro.common import (
    ReproError,
    atomic_json,
    canonical_sha256,
    executable_file,
    regular_file,
    within_repository,
)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository", type=pathlib.Path, default=pathlib.Path.cwd()
    )
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument(
        "--split",
        choices=("development", "validation"),
        default="development",
    )
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument(
        "--profile",
        choices=("fast", "balanced", "max", "structured", "repository"),
        default="balanced",
    )
    parser.add_argument(
        "--config-root",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/configs"),
    )
    parser.add_argument(
        "--debug-cli",
        type=pathlib.Path,
        default=pathlib.Path("target/debug/mathsvg"),
    )
    parser.add_argument(
        "--release-cli",
        type=pathlib.Path,
        default=pathlib.Path("target/release/mathsvg"),
    )
    parser.add_argument("--compiler", type=pathlib.Path)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/determinism/development-smoke-v1.csv"
        ),
    )
    parser.add_argument("--report", type=pathlib.Path)
    return parser.parse_args(argv)


def _repo_path(repository: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    return path.resolve() if path.is_absolute() else (repository / path).resolve()


def _safe_input(
    repository: pathlib.Path,
    manifest_path: pathlib.Path,
    *,
    split: str,
    dataset_id: str,
) -> tuple[object, pathlib.Path, list[object]]:
    entries = load_manifest(manifest_path)
    if any(entry.split == "holdout" for entry in entries):
        raise ReproError(
            "determinism campaign refuses manifests containing holdout rows"
        )
    matches = [
        entry
        for entry in entries
        if entry.split == split and entry.dataset_id == dataset_id
    ]
    if len(matches) != 1:
        raise ReproError(
            "dataset-id must identify exactly one development/validation row"
        )
    entry = matches[0]
    if entry.sealed or "holdout" in pathlib.PurePosixPath(entry.path).parts:
        raise ReproError("determinism campaign refuses sealed/holdout payloads")
    input_path = regular_file(
        within_repository(
            repository,
            repository / entry.path,
            label="determinism input",
        ),
        label="determinism input",
    )
    if input_path.stat().st_size != entry.bytes:
        raise ReproError("determinism input length differs from manifest")
    if sha256_file(input_path) != entry.sha256:
        raise ReproError("determinism input SHA-256 differs from manifest")
    return entry, input_path, list(entries)


def _preflight(
    binary: pathlib.Path,
    *,
    profile: str,
    config_root: pathlib.Path,
) -> dict[str, object]:
    specs = build_native_specs(
        (profile,),
        binary=binary,
        config_root=config_root,
        thread_selectors=("1",),
        ablation_ids=("none",),
    )
    if len(specs) != 1:
        raise ReproError("runtime profile preflight returned an invalid matrix")
    spec = specs[0]
    if spec.unavailable_reason:
        raise ReproError(
            f"runtime profile preflight failed for {binary}: "
            f"{spec.unavailable_reason}"
        )
    if spec.runtime_profile is None or not spec.runtime_profile_sha256:
        raise ReproError("runtime profile preflight returned no contract")
    return {
        "path": str(binary),
        "sha256": spec.executable_sha256,
        "version": spec.version,
        "runtime_profile_sha256": spec.runtime_profile_sha256,
        "measurement_config_sha256": spec.config_sha256,
    }


def _limitations(repetitions: int) -> list[str]:
    limitations = [
        (
            "the selected compiler executable is identified, but this "
            "development runner has no cryptographic build attestation "
            "linking it to the pre-existing debug/release binaries"
        ),
        "the campaign covers one machine and one architecture",
    ]
    if repetitions < 100:
        limitations.append("fewer than 100 repetitions remains incomplete")
    limitations.append(
        "dirty-source evidence cannot satisfy the publishable gate"
    )
    return limitations


def run_campaign(args: argparse.Namespace) -> dict[str, object]:
    repository = args.repository.resolve()
    output = _repo_path(repository, args.output)
    report_path = (
        _repo_path(repository, args.report)
        if args.report is not None
        else output.with_suffix(".json")
    )
    if output.exists() or report_path.exists():
        raise ReproError("determinism output/report already exists")
    if args.repetitions < 1:
        raise ReproError("repetitions must be positive")
    if args.timeout_seconds <= 0:
        raise ReproError("timeout must be positive")

    manifest_path = regular_file(
        _repo_path(repository, args.manifest),
        label="dataset manifest",
    )
    entry, input_path, entries = _safe_input(
        repository,
        manifest_path,
        split=args.split,
        dataset_id=args.dataset_id,
    )
    config_root = _repo_path(repository, args.config_root)
    config_path = regular_file(
        config_root / args.profile / "profile.toml",
        label="profile configuration",
    )
    debug = executable_file(
        _repo_path(repository, args.debug_cli),
        label="debug MathSVG CLI",
    )
    release = executable_file(
        _repo_path(repository, args.release_cli),
        label="release MathSVG CLI",
    )
    debug_preflight = _preflight(
        debug,
        profile=args.profile,
        config_root=config_root,
    )
    release_preflight = _preflight(
        release,
        profile=args.profile,
        config_root=config_root,
    )
    if (
        debug_preflight["runtime_profile_sha256"]
        != release_preflight["runtime_profile_sha256"]
    ):
        raise ReproError("debug/release runtime profile contracts differ")
    compiler_path = args.compiler
    if compiler_path is None:
        import shutil

        discovered = shutil.which("rustc")
        compiler_path = pathlib.Path(discovered) if discovered else None
    compiler = compiler_identity(compiler_path)
    if compiler.unavailable_reason:
        raise ReproError(
            f"compiler identity unavailable: {compiler.unavailable_reason}"
        )

    source_before = git_identity(repository)
    immutable_before = {
        "campaign_runner_sha256": sha256_file(pathlib.Path(__file__)),
        "matrix_runner_sha256": canonical_sha256(
            {
                name: sha256_file(
                    pathlib.Path(__file__).resolve().parents[1]
                    / "determinism"
                    / name
                )
                for name in ("runner.py", "schema.py")
            }
        ),
        "manifest_sha256": sha256_file(manifest_path),
        "input_sha256": sha256_file(input_path),
        "config_sha256": sha256_file(config_path),
        "debug_sha256": sha256_file(debug),
        "release_sha256": sha256_file(release),
        "compiler_sha256": compiler.binary_sha256,
    }
    request = RunRequest(
        repository=repository,
        experiment_id=args.experiment_id,
        machine_id=args.machine_id,
        split=args.split,
        input_path=input_path,
        config_path=config_path,
        profile=args.profile,
        builds=(
            BuildSpec("debug", debug, compiler),
            BuildSpec("release", release, compiler),
        ),
        repetitions=args.repetitions,
        timeout_seconds=args.timeout_seconds,
        publishable=False,
        source_identity=source_before,
    )
    rows = run_matrix(request)
    summary = evaluate_gate(rows)
    immutable_after = {
        "campaign_runner_sha256": sha256_file(pathlib.Path(__file__)),
        "matrix_runner_sha256": canonical_sha256(
            {
                name: sha256_file(
                    pathlib.Path(__file__).resolve().parents[1]
                    / "determinism"
                    / name
                )
                for name in ("runner.py", "schema.py")
            }
        ),
        "manifest_sha256": sha256_file(manifest_path),
        "input_sha256": sha256_file(input_path),
        "config_sha256": sha256_file(config_path),
        "debug_sha256": sha256_file(debug),
        "release_sha256": sha256_file(release),
        "compiler_sha256": sha256_file(compiler.executable),
    }
    source_after = git_identity(repository)
    source_stable = source_before == source_after
    immutable_stable = immutable_before == immutable_after
    write_csv(output, rows)
    evidence_status = (
        "failed"
        if summary.status == "failed"
        else "incomplete"
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": args.experiment_id,
        "evidence_scope": "development",
        "evidence_status": evidence_status,
        "matrix_status": summary.status,
        "matrix_reasons": list(summary.reasons),
        "rows": summary.rows,
        "ok_rows": summary.ok_rows,
        "failed_rows": summary.failed_rows,
        "timeout_rows": summary.timeout_rows,
        "unavailable_rows": summary.unavailable_rows,
        "archive_sha256": summary.archive_sha256,
        "source_before": source_before,
        "source_after": source_after,
        "source_stable_during_campaign": source_stable,
        "immutable_inputs_stable": immutable_stable,
        "immutable_inputs_sha256": canonical_sha256(immutable_before),
        "machine": {
            "machine_id": args.machine_id,
            "architecture": platform.machine(),
            "platform": platform.platform(),
        },
        "input": {
            "split": entry.split,
            "dataset_id": entry.dataset_id,
            "path": entry.path,
            "bytes": entry.bytes,
            "sha256": entry.sha256,
            "manifest_path": manifest_path.relative_to(repository).as_posix(),
            "manifest_file_sha256": sha256_file(manifest_path),
            "manifest_canonical_sha256": canonical_manifest_sha256(entries),
        },
        "profile": {
            "name": args.profile,
            "config_path": config_path.relative_to(repository).as_posix(),
            "config_sha256": sha256_file(config_path),
            "runtime_profile_sha256": (
                debug_preflight["runtime_profile_sha256"]
            ),
        },
        "builds": {
            "debug": debug_preflight,
            "release": release_preflight,
        },
        "compiler": {
            "path": str(compiler.executable),
            "sha256": compiler.binary_sha256,
            "version": compiler.version,
            "provenance_status": "unverified",
            "blocker": (
                "no build-time manifest cryptographically links this "
                "compiler identity to both pre-existing binaries"
            ),
        },
        "csv_path": output.relative_to(repository).as_posix(),
        "csv_sha256": sha256_file(output),
        "holdout_payload_inspected": False,
        "limitations": _limitations(args.repetitions),
    }
    atomic_json(report_path, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = run_campaign(args)
    except (
        BenchmarkRunnerError,
        DeterminismError,
        ManifestError,
        OSError,
        ReproError,
        RunnerError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"determinism campaign error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": report["evidence_status"],
                "matrix_status": report["matrix_status"],
                "reasons": report["matrix_reasons"],
                "rows": report["rows"],
                "csv": report["csv_path"],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if report["evidence_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
