#!/usr/bin/env python3
"""Measure the exact synthetic and real-structured Gate 4 requirements."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import subprocess
import sys
import tempfile
from collections import defaultdict
from typing import Mapping, Sequence

from mathsvg.python.benchmarks.config import ConfigError
from mathsvg.python.benchmarks.freeze import git_identity, sha256_file
from mathsvg.python.benchmarks.runner import RunnerError, build_native_specs
from mathsvg.python.datasets.manifest import DatasetEntry, ManifestError, load_manifest
from mathsvg.python.repro.common import (
    ReproError,
    atomic_json,
    canonical_sha256,
    executable_file,
    probe_text,
    regular_file,
    run_command,
    stable_environment,
    within_repository,
)

EXACT_GENERATOR_IDS = frozenset(
    {
        "dev-synthetic-constant-00",
        "dev-synthetic-linear",
        "dev-synthetic-periodic",
        "dev-synthetic-polynomial-d2",
        "dev-synthetic-recurrence",
        "dev-synthetic-lfsr8",
    }
)

REAL_STRUCTURED_DOMAINS = frozenset(
    {
        "structured-bitmap-bilevel",
        "structured-medical-binary",
        "real-laser-range-scan-ply",
        "real-raster-gis-uncompressed-geotiff",
        "real-uav-flight-telemetry-json",
        "real-climate-gridded-float-array-netcdf",
    }
)

MAX_U64 = (1 << 64) - 1
PADDING_BYTES = 7
ARCHIVE_HEADROOM_BYTES = 1024 * 1024


def _checked_limits(original_bytes: int) -> tuple[int, int, int]:
    if original_bytes < 0 or original_bytes > MAX_U64 - PADDING_BYTES:
        raise ReproError("input byte count exceeds checked u64 policy")
    input_output = original_bytes + PADDING_BYTES
    if input_output > (MAX_U64 - ARCHIVE_HEADROOM_BYTES) // 2:
        raise ReproError("archive byte bound exceeds checked u64 policy")
    return (
        input_output,
        input_output * 2 + ARCHIVE_HEADROOM_BYTES,
        input_output,
    )


def _selected_inputs(
    repository: pathlib.Path, manifest_path: pathlib.Path
) -> list[tuple[str, DatasetEntry, pathlib.Path]]:
    entries = load_manifest(manifest_path)
    if any(entry.split == "holdout" for entry in entries):
        raise ReproError(
            "procedural runner refuses manifests containing holdout rows"
        )
    selected: list[tuple[str, DatasetEntry, pathlib.Path]] = []
    for entry in entries:
        category = ""
        if entry.dataset_id in EXACT_GENERATOR_IDS:
            if entry.origin != "synthetic":
                raise ReproError(f"exact generator is not synthetic: {entry.dataset_id}")
            category = "synthetic-exact"
        elif entry.domain in REAL_STRUCTURED_DOMAINS:
            if entry.origin != "real" or not entry.primary:
                raise ReproError(
                    f"real structured row is not primary-real: {entry.dataset_id}"
                )
            category = "real-structured"
        if not category:
            continue
        if entry.sealed or entry.split != "development":
            raise ReproError(f"procedural input is sealed/non-development: {entry.dataset_id}")
        path = regular_file(
            within_repository(
                repository,
                repository / entry.path,
                label="procedural input",
            ),
            label="procedural input",
        )
        if path.stat().st_size != entry.bytes:
            raise ReproError(f"input length differs from manifest: {entry.dataset_id}")
        if sha256_file(path) != entry.sha256:
            raise ReproError(f"input SHA-256 differs from manifest: {entry.dataset_id}")
        selected.append((category, entry, path))
    selected.sort(key=lambda item: item[1].dataset_id)
    present_exact = {entry.dataset_id for category, entry, _ in selected if category == "synthetic-exact"}
    if present_exact != set(EXACT_GENERATOR_IDS):
        missing = sorted(EXACT_GENERATOR_IDS - present_exact)
        raise ReproError("missing exact generator rows: " + ", ".join(missing))
    present_real = {
        entry.domain for category, entry, _ in selected if category == "real-structured"
    }
    if present_real != set(REAL_STRUCTURED_DOMAINS):
        missing = sorted(REAL_STRUCTURED_DOMAINS - present_real)
        raise ReproError("missing real structured domains: " + ", ".join(missing))
    return selected


def domain_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        if row.get("category") == "real-structured":
            grouped[str(row.get("domain"))].append(row)
    result: list[dict[str, object]] = []
    for domain in sorted(grouped):
        values = grouped[domain]
        complete = all(row.get("status") == "ok" for row in values)
        original = sum(int(row.get("original_bytes") or 0) for row in values)
        functions = sum(
            int(row.get("function_reconstructed_bytes") or 0) for row in values
        )
        literal_only = sum(
            int(row.get("literal_only_archive_bytes") or 0) for row in values
        )
        pre_entropy = sum(
            int(row.get("pre_entropy_archive_bytes") or 0) for row in values
        )
        coverage = functions / original if original else 0.0
        gain = literal_only - pre_entropy
        result.append(
            {
                "domain": domain,
                "files": len(values),
                "complete": complete,
                "original_bytes": original,
                "function_reconstructed_bytes": functions,
                "procedural_coverage": coverage,
                "procedural_gain_before_entropy_bytes": gain,
                "gate_pass": complete and gain > 0 and coverage >= 0.5,
            }
        )
    return result


def assess_gate(
    rows: Sequence[Mapping[str, object]],
    *,
    source_stable: bool,
    immutable_inputs_stable: bool,
) -> tuple[str, list[str], list[dict[str, object]]]:
    reasons: list[str] = []
    execution_failures = [row for row in rows if row.get("status") != "ok"]
    synthetic_failures = [
        row
        for row in rows
        if row.get("category") == "synthetic-exact"
        and row.get("status") == "ok"
        and (
            float(row.get("procedural_coverage") or 0.0) < 0.99
            or int(row.get("literal_leaf_bytes") or 0) != 0
            or int(row.get("procedural_gain_before_entropy_bytes") or 0) <= 0
        )
    ]
    domains = domain_rows(rows)
    real_winners = [row for row in domains if row["gate_pass"] is True]
    if execution_failures:
        reasons.append(f"{len(execution_failures)} execution/round-trip row(s) failed")
    if synthetic_failures:
        reasons.append(
            f"{len(synthetic_failures)} exact generator(s) failed coverage/literal/gain"
        )
    if not real_winners:
        reasons.append(
            "no real structured domain has positive pre-entropy gain and >=50% function coverage"
        )
    if not source_stable:
        reasons.append("source identity changed during measurement")
    if not immutable_inputs_stable:
        reasons.append("binary/config/runner/manifest/input changed during measurement")
    if execution_failures or synthetic_failures or not real_winners:
        return "failed", reasons, domains
    return ("pass" if not reasons else "incomplete"), reasons, domains


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository", type=pathlib.Path, default=pathlib.Path.cwd()
    )
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument(
        "--binary",
        type=pathlib.Path,
        default=pathlib.Path("target/release/mathsvg"),
    )
    parser.add_argument(
        "--config-root",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/configs"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--scratch-root", type=pathlib.Path)
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/profiling/development-procedural-gate-v1.json"
        ),
    )
    return parser.parse_args(argv)


def run_campaign(args: argparse.Namespace) -> dict[str, object]:
    repository = args.repository.resolve()
    output = (
        args.output.resolve()
        if args.output.is_absolute()
        else (repository / args.output).resolve()
    )
    if output.exists():
        raise ReproError(f"procedural report already exists: {output}")
    if args.timeout_seconds <= 0:
        raise ReproError("timeout must be positive")
    if not args.experiment_id or any(
        character.isspace() for character in args.experiment_id
    ):
        raise ReproError("experiment-id must be non-empty without whitespace")
    if not args.machine_id:
        raise ReproError("machine-id must not be empty")

    manifest_path = regular_file(
        args.manifest.resolve()
        if args.manifest.is_absolute()
        else (repository / args.manifest).resolve(),
        label="dataset manifest",
    )
    selected = _selected_inputs(repository, manifest_path)
    binary_path = executable_file(
        args.binary.resolve()
        if args.binary.is_absolute()
        else (repository / args.binary).resolve(),
        label="MathSVG binary",
    )
    config_root = (
        args.config_root.resolve()
        if args.config_root.is_absolute()
        else (repository / args.config_root).resolve()
    )
    specs = build_native_specs(
        ("structured",),
        binary=binary_path,
        config_root=config_root,
        thread_selectors=("1",),
        ablation_ids=("none",),
    )
    if len(specs) != 1 or specs[0].unavailable_reason:
        reason = specs[0].unavailable_reason if specs else "missing runtime profile"
        raise ReproError(f"structured runtime/config preflight failed: {reason}")
    spec = specs[0]
    if spec.runtime_profile is None:
        raise ReproError("structured runtime profile is incomplete")

    runner_path = pathlib.Path(__file__).resolve()
    source_before = git_identity(repository)
    immutable_before = {
        "runner_sha256": sha256_file(runner_path),
        "binary_sha256": sha256_file(binary_path),
        "manifest_sha256": sha256_file(manifest_path),
        "inputs": {entry.dataset_id: sha256_file(path) for _, entry, path in selected},
        "config_sha256": sha256_file(config_root / "structured" / "profile.toml"),
        "runtime_profile_sha256": spec.runtime_profile_sha256,
    }
    scratch_parent = (
        args.scratch_root.resolve()
        if args.scratch_root is not None
        else pathlib.Path(tempfile.gettempdir()).resolve()
    )
    if not scratch_parent.is_dir():
        raise ReproError(f"scratch root is not a directory: {scratch_parent}")

    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(
        prefix="mathsvg-procedural-gate-", dir=scratch_parent
    ) as temporary:
        scratch = pathlib.Path(temporary)
        for sequence, (category, entry, input_path) in enumerate(selected):
            row_scratch = scratch / f"{sequence:03d}-{entry.dataset_id}"
            row_scratch.mkdir(parents=True)
            archive = row_scratch / "archive.msvg"
            max_input, max_archive, max_output = _checked_limits(entry.bytes)
            compress_argv = [
                str(binary_path),
                "compress",
                "--profile",
                "structured",
                "--threads",
                "1",
                "--max-input-bytes",
                str(max_input),
                str(input_path),
                str(archive),
            ]
            inspect_argv = [
                str(binary_path),
                "inspect",
                "--verify",
                "--backend",
                "scalar",
                "--max-archive-bytes",
                str(max_archive),
                "--max-output-bytes",
                str(max_output),
                str(archive),
            ]
            row: dict[str, object] = {
                "sequence": sequence,
                "category": category,
                "dataset_id": entry.dataset_id,
                "domain": entry.domain,
                "original_bytes": entry.bytes,
                "input_sha256": entry.sha256,
                "status": "failed",
                "reason": "campaign row did not complete",
                "compress_command": compress_argv,
                "inspect_command": inspect_argv,
            }
            try:
                compressed = run_command(
                    compress_argv,
                    cwd=repository,
                    timeout_seconds=args.timeout_seconds,
                    stdout_path=row_scratch / "compress.stdout",
                    stderr_path=row_scratch / "compress.stderr",
                    environment=stable_environment(),
                )
                if compressed.timed_out or compressed.returncode != 0:
                    detail = compressed.stderr_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()[:2000]
                    raise ReproError(
                        "compression timed out"
                        if compressed.timed_out
                        else f"compression exited {compressed.returncode}: {detail}"
                    )
                archive = regular_file(archive, label="native archive")
                inspected = run_command(
                    inspect_argv,
                    cwd=repository,
                    timeout_seconds=args.timeout_seconds,
                    stdout_path=row_scratch / "inspect.stdout",
                    stderr_path=row_scratch / "inspect.stderr",
                    environment=stable_environment(),
                )
                if inspected.timed_out or inspected.returncode != 0:
                    detail = inspected.stderr_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()[:2000]
                    raise ReproError(
                        "inspection timed out"
                        if inspected.timed_out
                        else f"inspection exited {inspected.returncode}: {detail}"
                    )
                value = json.loads(inspected.stdout_path.read_text(encoding="utf-8"))
                breakdown = value["procedural_breakdown"]
                if (
                    value.get("restored_verified") is not True
                    or int(value["original_bytes"]) != entry.bytes
                    or value["original_sha256"] != entry.sha256
                ):
                    raise ReproError("strict archive inspection disagrees with manifest")
                function_bytes = int(breakdown["function_reconstructed_bytes"])
                literal_bytes = int(breakdown["literal_reconstructed_bytes"])
                if function_bytes + literal_bytes != entry.bytes:
                    raise ReproError("reconstruction attribution does not partition input")
                literal_only = int(breakdown["literal_only_archive_bytes"])
                pre_entropy = int(breakdown["pre_entropy_archive_bytes"])
                row.update(
                    {
                        "status": "ok",
                        "reason": "",
                        "archive_bytes": archive.stat().st_size,
                        "archive_sha256": sha256_file(archive),
                        "block_count": int(value["block_count"]),
                        "function_graph_bytes": int(breakdown["function_graph_bytes"]),
                        "function_reconstructed_bytes": function_bytes,
                        "literal_reconstructed_bytes": literal_bytes,
                        "literal_leaf_bytes": int(breakdown["literal_leaf_bytes"]),
                        "literal_only_archive_bytes": literal_only,
                        "pre_entropy_archive_bytes": pre_entropy,
                        "procedural_gain_before_entropy_bytes": literal_only
                        - pre_entropy,
                        "entropy_coder_gain_bytes": pre_entropy
                        - archive.stat().st_size,
                        "procedural_coverage": function_bytes / entry.bytes
                        if entry.bytes
                        else 0.0,
                    }
                )
            except (KeyError, OSError, ReproError, TypeError, ValueError, json.JSONDecodeError) as exc:
                row["reason"] = str(exc)[:4000]
            rows.append(row)

    immutable_after = {
        "runner_sha256": sha256_file(runner_path),
        "binary_sha256": sha256_file(binary_path),
        "manifest_sha256": sha256_file(manifest_path),
        "inputs": {entry.dataset_id: sha256_file(path) for _, entry, path in selected},
        "config_sha256": sha256_file(config_root / "structured" / "profile.toml"),
        "runtime_profile_sha256": spec.runtime_profile_sha256,
    }
    source_after = git_identity(repository)
    source_stable = source_before == source_after
    immutable_stable = immutable_before == immutable_after
    gate_status, reasons, domains = assess_gate(
        rows,
        source_stable=source_stable,
        immutable_inputs_stable=immutable_stable,
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": args.experiment_id,
        "evidence_scope": "development",
        "gate": "Gate 4 - Procedural proof",
        "gate_status": gate_status,
        "gate_reasons": reasons,
        "source_before": source_before,
        "source_after": source_after,
        "source_stable_during_campaign": source_stable,
        "immutable_inputs_stable": immutable_stable,
        "immutable_inputs_sha256": canonical_sha256(immutable_before),
        "machine": {
            "machine_id": args.machine_id,
            "architecture": platform.machine(),
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count() or 1,
            "python": platform.python_version(),
        },
        "binary": {
            "path": str(binary_path),
            "sha256": sha256_file(binary_path),
            "version": probe_text([str(binary_path), "--version"], cwd=repository),
        },
        "runtime_profile": {
            "profile": "structured",
            "runtime_profile_sha256": spec.runtime_profile_sha256,
            "measurement_config_sha256": spec.config_sha256,
            "value": spec.runtime_profile,
        },
        "requirements": {
            "synthetic_exact": {
                "dataset_ids": sorted(EXACT_GENERATOR_IDS),
                "minimum_function_coverage": 0.99,
                "maximum_literal_leaf_bytes": 0,
                "minimum_procedural_gain_before_entropy_bytes_exclusive": 0,
            },
            "real_structured": {
                "domains": sorted(REAL_STRUCTURED_DOMAINS),
                "minimum_winning_domains": 1,
                "minimum_function_coverage": 0.5,
                "minimum_procedural_gain_before_entropy_bytes_exclusive": 0,
            },
        },
        "domain_rows": domains,
        "rows": rows,
        "runner_sha256": sha256_file(runner_path),
        "runner_performed_tuning": False,
        "holdout_payload_inspected": False,
    }
    atomic_json(output, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = run_campaign(args)
    except (
        ConfigError,
        ManifestError,
        OSError,
        ReproError,
        RunnerError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"procedural gate error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(
                    args.output.resolve()
                    if args.output.is_absolute()
                    else (args.repository.resolve() / args.output).resolve()
                ),
                "gate_status": report["gate_status"],
                "gate_reasons": report["gate_reasons"],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if report["gate_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
