#!/usr/bin/env python3
"""Measure the exact Gate 2 expansion bound on incompressible controls."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import random
import subprocess
import sys
import tempfile
from collections import defaultdict
from typing import Mapping, Sequence

from mathsvg.python.benchmarks.config import ConfigError
from mathsvg.python.benchmarks.freeze import git_identity, sha256_file
from mathsvg.python.benchmarks.runner import RunnerError, build_native_specs
from mathsvg.python.datasets.manifest import (
    DatasetEntry,
    ManifestError,
    canonical_manifest_sha256,
    load_manifest,
)
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

REQUIRED_PROFILES = ("fast", "balanced", "max")
REQUIRED_DOMAINS = frozenset(
    {
        "incompressible-control-cryptographic-random",
        "incompressible-control-encrypted",
        "incompressible-control-gzip",
        "incompressible-control-zstd",
        "incompressible-control-xz",
        "incompressible-control-zip",
        "incompressible-control-png",
        "incompressible-control-jpeg",
        "incompressible-control-avif",
        "incompressible-control-mp3",
        "incompressible-control-flac",
        "incompressible-control-mp4-av1",
    }
)

# Canonical v1 literal fallback consists of the fixed file header/footer and,
# for every block, one directory record, one block header, and the 16-byte
# canonical Concat + Literal wire envelope. Literal payload bytes are excluded.
FILE_ENVELOPE_BYTES = 128 + 128
PER_BLOCK_LITERAL_ENVELOPE_BYTES = 144 + 72 + 16
EXPANSION_RATE_DENOMINATOR = 1000
MAX_U64 = (1 << 64) - 1
PADDING_BYTES = 7
ARCHIVE_HEADROOM_BYTES = 1024 * 1024


def fixed_literal_overhead(block_count: int) -> int:
    if block_count < 0:
        raise ReproError("block count must be non-negative")
    return FILE_ENVELOPE_BYTES + block_count * PER_BLOCK_LITERAL_ENVELOPE_BYTES


def maximum_archive_bytes(original_bytes: int, block_count: int) -> int:
    if original_bytes < 0:
        raise ReproError("original byte count must be non-negative")
    return (
        original_bytes
        + original_bytes // EXPANSION_RATE_DENOMINATOR
        + fixed_literal_overhead(block_count)
    )


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


def _substitute(
    template: Sequence[str],
    *,
    input_path: pathlib.Path,
    archive_path: pathlib.Path,
    restored_path: pathlib.Path,
    max_input_bytes: int,
    max_archive_bytes: int,
    max_output_bytes: int,
) -> list[str]:
    replacements = {
        "{input}": str(input_path),
        "{archive}": str(archive_path),
        "{restored}": str(restored_path),
        "{max_input_bytes}": str(max_input_bytes),
        "{max_archive_bytes}": str(max_archive_bytes),
        "{max_output_bytes}": str(max_output_bytes),
    }
    result: list[str] = []
    for argument in template:
        if argument in replacements:
            result.append(replacements[argument])
        elif "{" in argument or "}" in argument:
            raise ReproError(f"unknown command placeholder: {argument}")
        else:
            result.append(argument)
    return result


def _command_failure(result: object, stderr_path: pathlib.Path) -> str:
    timed_out = bool(getattr(result, "timed_out"))
    returncode = getattr(result, "returncode")
    if timed_out:
        return "command timed out"
    detail = stderr_path.read_text(encoding="utf-8", errors="replace")
    return f"command exited {returncode}: {detail.strip()[:2000]}"


def _load_controls(
    repository: pathlib.Path,
    manifest_paths: Sequence[pathlib.Path],
) -> tuple[list[tuple[DatasetEntry, pathlib.Path]], list[dict[str, object]]]:
    selected: list[tuple[DatasetEntry, pathlib.Path]] = []
    manifests: list[dict[str, object]] = []
    dataset_ids: set[str] = set()
    for manifest_path in manifest_paths:
        entries = load_manifest(manifest_path)
        if any(entry.split == "holdout" for entry in entries):
            raise ReproError(
                "literal-safety runner refuses manifests containing holdout rows"
            )
        manifests.append(
            {
                "path": manifest_path.relative_to(repository).as_posix(),
                "file_sha256": sha256_file(manifest_path),
                "canonical_sha256": canonical_manifest_sha256(entries),
            }
        )
        for entry in entries:
            if entry.domain not in REQUIRED_DOMAINS:
                continue
            if entry.origin != "control" or entry.sealed:
                raise ReproError(
                    f"required control has invalid origin/seal: {entry.dataset_id}"
                )
            if entry.dataset_id in dataset_ids:
                raise ReproError(f"duplicate selected dataset: {entry.dataset_id}")
            dataset_ids.add(entry.dataset_id)
            path = regular_file(
                within_repository(
                    repository,
                    repository / entry.path,
                    label="control payload",
                ),
                label="control payload",
            )
            if path.stat().st_size != entry.bytes:
                raise ReproError(
                    f"control payload length differs from manifest: {entry.dataset_id}"
                )
            if sha256_file(path) != entry.sha256:
                raise ReproError(
                    f"control payload SHA-256 differs from manifest: {entry.dataset_id}"
                )
            selected.append((entry, path))
    selected.sort(key=lambda item: item[0].dataset_id)
    return selected, manifests


def assess_gate(
    rows: Sequence[Mapping[str, object]],
    *,
    profiles: Sequence[str],
    repetitions: int,
    covered_domains: Sequence[str],
    source_stable: bool,
    immutable_inputs_stable: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    execution_failures = [row for row in rows if row.get("status") != "ok"]
    expansion_failures = [
        row
        for row in rows
        if row.get("status") == "ok"
        and row.get("within_expansion_bound") is not True
    ]
    hashes: dict[tuple[str, str], set[str]] = defaultdict(set)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        if row.get("status") != "ok":
            continue
        key = (str(row.get("dataset_id")), str(row.get("profile")))
        hashes[key].add(str(row.get("archive_sha256")))
        counts[key] += 1
    nondeterministic = [key for key, values in hashes.items() if len(values) != 1]
    short_groups = [key for key, count in counts.items() if count != repetitions]

    if execution_failures:
        reasons.append(f"{len(execution_failures)} execution/round-trip row(s) failed")
    if expansion_failures:
        reasons.append(
            f"{len(expansion_failures)} row(s) exceeded 0.1% plus declared overhead"
        )
    if nondeterministic:
        reasons.append(f"{len(nondeterministic)} dataset/profile archive(s) varied")
    if short_groups and not execution_failures:
        reasons.append(f"{len(short_groups)} dataset/profile group(s) are incomplete")

    missing_domains = sorted(REQUIRED_DOMAINS - set(covered_domains))
    if missing_domains:
        reasons.append("missing required controls: " + ", ".join(missing_domains))
    if set(profiles) != set(REQUIRED_PROFILES):
        reasons.append("Gate 2 requires Fast, Balanced, and Max profiles")
    if repetitions < 2:
        reasons.append("at least two repetitions are required for archive identity")
    if not source_stable:
        reasons.append("source identity changed during measurement")
    if not immutable_inputs_stable:
        reasons.append("binary/config/runner/manifest/input changed during measurement")

    if execution_failures or expansion_failures or nondeterministic:
        return "failed", reasons
    return ("pass" if not reasons else "incomplete"), reasons


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository", type=pathlib.Path, default=pathlib.Path.cwd()
    )
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument(
        "--manifest", action="append", required=True, type=pathlib.Path
    )
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--seed", type=int, default=1_297_748_005)
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
    parser.add_argument("--timeout-seconds", type=float, default=600)
    parser.add_argument("--scratch-root", type=pathlib.Path)
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/profiling/development-validation-literal-safety-v1.json"
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
        raise ReproError(f"literal-safety report already exists: {output}")
    if args.repetitions < 1:
        raise ReproError("repetitions must be positive")
    if args.threads < 1 or args.threads > 64:
        raise ReproError("threads must be within 1..64")
    if args.timeout_seconds <= 0:
        raise ReproError("timeout must be positive")
    if not args.experiment_id or any(
        character.isspace() for character in args.experiment_id
    ):
        raise ReproError("experiment-id must be non-empty without whitespace")
    if not args.machine_id:
        raise ReproError("machine-id must not be empty")
    profiles = tuple(sorted(set(args.profile or REQUIRED_PROFILES)))
    if not set(profiles) <= set(REQUIRED_PROFILES):
        raise ReproError("unknown literal-safety profile requested")

    manifest_paths = [
        regular_file(
            path.resolve()
            if path.is_absolute()
            else (repository / path).resolve(),
            label="dataset manifest",
        )
        for path in args.manifest
    ]
    controls, manifests = _load_controls(repository, manifest_paths)
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
        profiles,
        binary=binary_path,
        config_root=config_root,
        thread_selectors=(str(args.threads),),
        ablation_ids=("none",),
    )
    by_profile = {spec.profile: spec for spec in specs}
    if set(by_profile) != set(profiles):
        raise ReproError("native runtime preflight omitted a requested profile")
    for profile, spec in by_profile.items():
        if spec.unavailable_reason:
            raise ReproError(
                f"{profile} runtime/config preflight failed: {spec.unavailable_reason}"
            )
        if spec.adapter is None or spec.runtime_profile is None:
            raise ReproError(f"{profile} runtime profile is incomplete")

    runner_path = pathlib.Path(__file__).resolve()
    source_before = git_identity(repository)
    immutable_before = {
        "runner_sha256": sha256_file(runner_path),
        "binary_sha256": sha256_file(binary_path),
        "manifests": {
            item["path"]: item["file_sha256"] for item in manifests
        },
        "inputs": {entry.dataset_id: sha256_file(path) for entry, path in controls},
        "configs": {
            profile: sha256_file(config_root / profile / "profile.toml")
            for profile in profiles
        },
        "runtime_profiles": {
            profile: by_profile[profile].runtime_profile_sha256
            for profile in profiles
        },
    }
    scratch_parent = (
        args.scratch_root.resolve()
        if args.scratch_root is not None
        else pathlib.Path(tempfile.gettempdir()).resolve()
    )
    if not scratch_parent.is_dir():
        raise ReproError(f"scratch root is not a directory: {scratch_parent}")

    schedule = [
        (entry, input_path, profile, repetition)
        for repetition in range(args.repetitions)
        for entry, input_path in controls
        for profile in profiles
    ]
    random.Random(args.seed).shuffle(schedule)
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(
        prefix="mathsvg-literal-safety-", dir=scratch_parent
    ) as temporary:
        scratch = pathlib.Path(temporary)
        for sequence, (entry, input_path, profile, repetition) in enumerate(schedule):
            spec = by_profile[profile]
            assert spec.adapter is not None
            row_scratch = scratch / f"{sequence:04d}-{entry.dataset_id}-{profile}"
            row_scratch.mkdir(parents=True)
            archive = row_scratch / "archive.msvg"
            restored = row_scratch / "restored.bin"
            max_input, max_archive, max_output = _checked_limits(entry.bytes)
            compress_argv = [
                str(binary_path),
                *_substitute(
                    spec.adapter.encode_args,
                    input_path=input_path,
                    archive_path=archive,
                    restored_path=restored,
                    max_input_bytes=max_input,
                    max_archive_bytes=max_archive,
                    max_output_bytes=max_output,
                ),
            ]
            decompress_argv = [
                str(binary_path),
                *_substitute(
                    spec.adapter.decode_args,
                    input_path=input_path,
                    archive_path=archive,
                    restored_path=restored,
                    max_input_bytes=max_input,
                    max_archive_bytes=max_archive,
                    max_output_bytes=max_output,
                ),
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
                "repetition": repetition,
                "dataset_id": entry.dataset_id,
                "split": entry.split,
                "domain": entry.domain,
                "profile": profile,
                "threads": args.threads,
                "original_bytes": entry.bytes,
                "input_sha256": entry.sha256,
                "status": "failed",
                "reason": "campaign row did not complete",
                "compress_command": compress_argv,
                "inspect_command": inspect_argv,
                "decompress_command": decompress_argv,
            }
            try:
                compress_result = run_command(
                    compress_argv,
                    cwd=repository,
                    timeout_seconds=args.timeout_seconds,
                    stdout_path=row_scratch / "compress.stdout",
                    stderr_path=row_scratch / "compress.stderr",
                    environment=stable_environment(),
                )
                if compress_result.timed_out or compress_result.returncode != 0:
                    raise ReproError(
                        _command_failure(compress_result, compress_result.stderr_path)
                    )
                archive = regular_file(archive, label="native archive")
                archive_bytes = archive.stat().st_size
                archive_sha256 = sha256_file(archive)

                inspect_result = run_command(
                    inspect_argv,
                    cwd=repository,
                    timeout_seconds=args.timeout_seconds,
                    stdout_path=row_scratch / "inspect.stdout",
                    stderr_path=row_scratch / "inspect.stderr",
                    environment=stable_environment(),
                )
                if inspect_result.timed_out or inspect_result.returncode != 0:
                    raise ReproError(
                        _command_failure(inspect_result, inspect_result.stderr_path)
                    )
                inspection = json.loads(
                    inspect_result.stdout_path.read_text(encoding="utf-8")
                )
                block_count = int(inspection["block_count"])
                if (
                    inspection.get("restored_verified") is not True
                    or int(inspection["original_bytes"]) != entry.bytes
                    or inspection["original_sha256"] != entry.sha256
                ):
                    raise ReproError("strict archive inspection disagrees with manifest")

                decompress_result = run_command(
                    decompress_argv,
                    cwd=repository,
                    timeout_seconds=args.timeout_seconds,
                    stdout_path=row_scratch / "decompress.stdout",
                    stderr_path=row_scratch / "decompress.stderr",
                    environment=stable_environment(),
                )
                if decompress_result.timed_out or decompress_result.returncode != 0:
                    raise ReproError(
                        _command_failure(
                            decompress_result, decompress_result.stderr_path
                        )
                    )
                restored = regular_file(restored, label="restored output")
                restored_sha256 = sha256_file(restored)
                roundtrip_ok = (
                    restored.stat().st_size == entry.bytes
                    and restored_sha256 == entry.sha256
                )
                if not roundtrip_ok:
                    raise ReproError("restored bytes differ from manifest")

                fixed_overhead = fixed_literal_overhead(block_count)
                rate_allowance = entry.bytes // EXPANSION_RATE_DENOMINATOR
                archive_limit = maximum_archive_bytes(entry.bytes, block_count)
                row.update(
                    {
                        "status": "ok",
                        "reason": "",
                        "archive_bytes": archive_bytes,
                        "archive_sha256": archive_sha256,
                        "restored_sha256": restored_sha256,
                        "roundtrip_ok": True,
                        "block_count": block_count,
                        "fixed_literal_overhead_bytes": fixed_overhead,
                        "rate_allowance_bytes": rate_allowance,
                        "maximum_archive_bytes_inclusive": archive_limit,
                        "expansion_bytes": archive_bytes - entry.bytes,
                        "within_expansion_bound": archive_bytes <= archive_limit,
                    }
                )
            except (KeyError, OSError, ReproError, ValueError, json.JSONDecodeError) as exc:
                row["reason"] = str(exc)[:4000]
            rows.append(row)

    immutable_after = {
        "runner_sha256": sha256_file(runner_path),
        "binary_sha256": sha256_file(binary_path),
        "manifests": {
            item["path"]: item["file_sha256"] for item in manifests
        },
        "inputs": {entry.dataset_id: sha256_file(path) for entry, path in controls},
        "configs": {
            profile: sha256_file(config_root / profile / "profile.toml")
            for profile in profiles
        },
        "runtime_profiles": {
            profile: by_profile[profile].runtime_profile_sha256
            for profile in profiles
        },
    }
    source_after = git_identity(repository)
    source_stable = source_before == source_after
    immutable_stable = immutable_before == immutable_after
    covered_domains = sorted({entry.domain for entry, _ in controls})
    gate_status, reasons = assess_gate(
        rows,
        profiles=profiles,
        repetitions=args.repetitions,
        covered_domains=covered_domains,
        source_stable=source_stable,
        immutable_inputs_stable=immutable_stable,
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": args.experiment_id,
        "evidence_scope": "development-and-validation-controls",
        "gate": "Gate 2 - Literal safety",
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
        "manifests": manifests,
        "inputs": [
            {
                "split": entry.split,
                "dataset_id": entry.dataset_id,
                "domain": entry.domain,
                "path": entry.path,
                "bytes": entry.bytes,
                "sha256": entry.sha256,
            }
            for entry, _ in controls
        ],
        "coverage": {
            "required_domains": sorted(REQUIRED_DOMAINS),
            "covered_domains": covered_domains,
            "complete": set(covered_domains) == set(REQUIRED_DOMAINS),
        },
        "bound": {
            "formula": (
                "archive_bytes <= original_bytes + floor(original_bytes/1000) "
                "+ 256 + 232*block_count"
            ),
            "rate_numerator": 1,
            "rate_denominator": EXPANSION_RATE_DENOMINATOR,
            "rate_rounding": "floor",
            "file_header_footer_bytes": FILE_ENVELOPE_BYTES,
            "per_block_literal_envelope_bytes": PER_BLOCK_LITERAL_ENVELOPE_BYTES,
            "per_block_components": {
                "directory_record_bytes": 144,
                "block_header_bytes": 72,
                "canonical_concat_literal_wire_bytes": 16,
            },
        },
        "measurement": {
            "profiles": list(profiles),
            "threads": args.threads,
            "repetitions": args.repetitions,
            "seed": args.seed,
            "randomized_schedule": True,
            "strict_archive_inspection": True,
            "restored_sha256_verified": True,
            "archive_identity_checked_across_repetitions": True,
            "limitations": [
                "this is exact size/correctness evidence, not a timing benchmark",
                "this report contains one machine/architecture only",
                "validation controls were measured but no holdout payload was opened",
            ],
        },
        "runtime_profiles": {
            profile: {
                "config_path": (
                    config_root / profile / "profile.toml"
                ).relative_to(repository).as_posix(),
                "config_file_sha256": sha256_file(
                    config_root / profile / "profile.toml"
                ),
                "runtime_profile_sha256": by_profile[
                    profile
                ].runtime_profile_sha256,
                "measurement_config_sha256": by_profile[profile].config_sha256,
            }
            for profile in profiles
        },
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
        print(f"literal-safety gate error: {exc}", file=sys.stderr)
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
