#!/usr/bin/env python3
"""Measure the native MathSVG encoder/decoder RSS acceptance thresholds."""

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
from dataclasses import asdict
from typing import Mapping, Sequence

from mathsvg.python.benchmarks.config import (
    DECODER_RSS_LIMIT,
    ENCODER_RSS_LIMITS,
    ConfigError,
)
from mathsvg.python.benchmarks.freeze import (
    git_identity,
    sha256_file,
)
from mathsvg.python.benchmarks.runner import (
    Measurement,
    MeasurementFailure,
    RunnerError,
    build_native_specs,
    run_measured,
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
    probe_text,
    regular_file,
    within_repository,
)

REQUIRED_PROFILES = ("fast", "balanced", "max")
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


def _measurement(value: Measurement | None) -> dict[str, object] | None:
    return asdict(value) if value is not None else None


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


def _selected_entry(
    repository: pathlib.Path,
    manifest_path: pathlib.Path,
    split: str,
    dataset_id: str,
) -> tuple[object, pathlib.Path, list[object]]:
    entries = load_manifest(manifest_path)
    if any(entry.split == "holdout" for entry in entries):
        raise ReproError("memory runner refuses manifests containing holdout rows")
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
        raise ReproError("memory runner refuses sealed/holdout payloads")
    path = regular_file(
        within_repository(
            repository,
            repository / entry.path,
            label="dataset payload",
        ),
        label="dataset payload",
    )
    if path.stat().st_size != entry.bytes:
        raise ReproError("dataset payload length differs from manifest")
    if sha256_file(path) != entry.sha256:
        raise ReproError("dataset payload SHA-256 differs from manifest")
    return entry, path, list(entries)


def assess_gate(
    rows: Sequence[Mapping[str, object]],
    *,
    dataset_id: str,
    original_bytes: int,
    repetitions: int,
    warmups: int,
    profiles: Sequence[str],
    source_stable: bool,
    immutable_inputs_stable: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    failed = [row for row in rows if row.get("status") != "ok"]
    threshold_failures = [
        row
        for row in rows
        if row.get("status") == "ok"
        and (
            row.get("encoder_within_limit") is not True
            or row.get("decoder_within_limit") is not True
        )
    ]
    if failed:
        reasons.append(f"{len(failed)} execution/round-trip row(s) failed")
    if threshold_failures:
        reasons.append(
            f"{len(threshold_failures)} row(s) exceeded a strict RSS limit"
        )
    if (
        dataset_id != "dev-enwik9-enwik9"
        or original_bytes != 1_000_000_000
    ):
        reasons.append("Gate 3 requires the canonical 1,000,000,000-byte enwik9")
    if set(profiles) != set(REQUIRED_PROFILES):
        reasons.append("Gate 3 requires Fast, Balanced, and Max profiles")
    if repetitions < 5:
        reasons.append("benchmark methodology requires at least 5 repetitions")
    if warmups < 1:
        reasons.append("benchmark methodology requires a warm-up")
    if not source_stable:
        reasons.append("source identity changed during measurement")
    if not immutable_inputs_stable:
        reasons.append("binary/config/runner/input changed during measurement")
    if failed or threshold_failures:
        return "failed", reasons
    return ("pass" if not reasons else "incomplete"), reasons


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
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument(
        "--backend",
        choices=("scalar", "simd", "auto"),
        default="scalar",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--warmups", type=int, default=1)
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
    parser.add_argument(
        "--time-binary",
        type=pathlib.Path,
        default=pathlib.Path("/usr/bin/time"),
    )
    parser.add_argument("--compression-timeout-seconds", type=float, default=3600)
    parser.add_argument("--decompression-timeout-seconds", type=float, default=600)
    parser.add_argument("--scratch-root", type=pathlib.Path)
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/profiling/development-memory-smoke-v1.json"
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
        raise ReproError(f"memory report already exists: {output}")
    if args.repetitions < 1 or args.warmups < 0:
        raise ReproError("repetitions must be positive and warmups non-negative")
    if args.threads < 1 or args.threads > 64:
        raise ReproError("threads must be within 1..64")
    if min(
        args.compression_timeout_seconds,
        args.decompression_timeout_seconds,
    ) <= 0:
        raise ReproError("timeouts must be positive")
    if not args.experiment_id or any(
        character.isspace() for character in args.experiment_id
    ):
        raise ReproError("experiment-id must be non-empty without whitespace")
    if not args.machine_id:
        raise ReproError("machine-id must not be empty")
    profiles = tuple(sorted(set(args.profile or REQUIRED_PROFILES)))
    if not set(profiles) <= set(ENCODER_RSS_LIMITS):
        raise ReproError("unknown memory profile requested")

    manifest_path = (
        args.manifest.resolve()
        if args.manifest.is_absolute()
        else (repository / args.manifest).resolve()
    )
    manifest_path = regular_file(manifest_path, label="dataset manifest")
    entry, input_path, entries = _selected_entry(
        repository,
        manifest_path,
        args.split,
        args.dataset_id,
    )
    binary_path = executable_file(
        (
            args.binary.resolve()
            if args.binary.is_absolute()
            else (repository / args.binary).resolve()
        ),
        label="MathSVG binary",
    )
    config_root = (
        args.config_root.resolve()
        if args.config_root.is_absolute()
        else (repository / args.config_root).resolve()
    )
    time_binary = executable_file(
        args.time_binary.resolve(),
        label="GNU time",
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
                f"{profile} runtime/config preflight failed: "
                f"{spec.unavailable_reason}"
            )
        if spec.adapter is None or spec.runtime_profile is None:
            raise ReproError(f"{profile} runtime profile is incomplete")

    runner_path = pathlib.Path(__file__).resolve()
    source_before = git_identity(repository)
    immutable_before = {
        "runner_sha256": sha256_file(runner_path),
        "binary_sha256": sha256_file(binary_path),
        "time_sha256": sha256_file(time_binary),
        "manifest_sha256": sha256_file(manifest_path),
        "input_sha256": sha256_file(input_path),
        "configs": {
            profile: sha256_file(config_root / profile / "profile.toml")
            for profile in profiles
        },
        "runtime_profiles": {
            profile: by_profile[profile].runtime_profile_sha256
            for profile in profiles
        },
    }
    max_input, max_archive, max_output = _checked_limits(entry.bytes)
    scratch_parent = (
        args.scratch_root.resolve()
        if args.scratch_root is not None
        else pathlib.Path(tempfile.gettempdir()).resolve()
    )
    if not scratch_parent.is_dir():
        raise ReproError(f"scratch root is not a directory: {scratch_parent}")

    rows: list[dict[str, object]] = []
    phases = ((True, args.warmups), (False, args.repetitions))
    with tempfile.TemporaryDirectory(
        prefix="mathsvg-memory-gate-",
        dir=scratch_parent,
    ) as temporary:
        scratch = pathlib.Path(temporary)
        sequence = 0
        for warmup, count in phases:
            for repetition in range(count):
                order = list(profiles)
                random.Random(
                    args.seed
                    ^ repetition
                    ^ (0xA5A5 if warmup else 0x5A5A)
                ).shuffle(order)
                for schedule_order, profile in enumerate(order):
                    spec = by_profile[profile]
                    assert spec.adapter is not None
                    row_scratch = scratch / f"{sequence:04d}-{profile}"
                    compress_scratch = row_scratch / "compress"
                    decompress_scratch = row_scratch / "decompress"
                    compress_scratch.mkdir(parents=True)
                    decompress_scratch.mkdir(parents=True)
                    archive = row_scratch / "archive.msvg"
                    restored = row_scratch / "restored.bin"
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
                    decode_template = list(spec.adapter.decode_args)
                    decode_template[1:1] = ["--backend", args.backend]
                    decompress_argv = [
                        str(binary_path),
                        *_substitute(
                            decode_template,
                            input_path=input_path,
                            archive_path=archive,
                            restored_path=restored,
                            max_input_bytes=max_input,
                            max_archive_bytes=max_archive,
                            max_output_bytes=max_output,
                        ),
                    ]
                    compression: Measurement | None = None
                    decompression: Measurement | None = None
                    status = "ok"
                    reason = ""
                    archive_bytes: int | None = None
                    archive_sha256 = ""
                    restored_sha256 = ""
                    roundtrip_ok = False
                    try:
                        compression = run_measured(
                            time_binary=time_binary,
                            argv=compress_argv,
                            timeout_seconds=args.compression_timeout_seconds,
                            scratch=compress_scratch,
                            stdout_path=None,
                            threads=args.threads,
                        )
                        archive = regular_file(archive, label="native archive")
                        archive_bytes = archive.stat().st_size
                        archive_sha256 = sha256_file(archive)
                        if archive_bytes > max_archive:
                            raise ReproError(
                                "archive exceeds the checked native limit"
                            )
                        decompression = run_measured(
                            time_binary=time_binary,
                            argv=decompress_argv,
                            timeout_seconds=args.decompression_timeout_seconds,
                            scratch=decompress_scratch,
                            stdout_path=None,
                            threads=args.threads,
                        )
                        restored = regular_file(
                            restored, label="restored output"
                        )
                        restored_sha256 = sha256_file(restored)
                        roundtrip_ok = (
                            restored.stat().st_size == entry.bytes
                            and restored_sha256 == entry.sha256
                        )
                        if not roundtrip_ok:
                            status = "failed"
                            reason = "restored bytes differ from the manifest"
                    except MeasurementFailure as exc:
                        status = exc.status
                        reason = exc.message
                        if compression is None:
                            compression = exc.measurement
                        else:
                            decompression = exc.measurement
                    except (OSError, ReproError, RunnerError) as exc:
                        status = "failed"
                        reason = str(exc)

                    encoder_limit = ENCODER_RSS_LIMITS[profile]
                    encoder_within = (
                        compression is not None
                        and compression.peak_rss_bytes is not None
                        and compression.peak_rss_bytes < encoder_limit
                    )
                    decoder_within = (
                        decompression is not None
                        and decompression.peak_rss_bytes is not None
                        and decompression.peak_rss_bytes < DECODER_RSS_LIMIT
                    )
                    rows.append(
                        {
                            "sequence": sequence,
                            "warmup": warmup,
                            "repetition": repetition,
                            "schedule_order": schedule_order,
                            "profile": profile,
                            "threads": args.threads,
                            "backend": args.backend,
                            "status": status,
                            "reason": reason[:4000],
                            "compress_command": compress_argv,
                            "decompress_command": decompress_argv,
                            "compression": _measurement(compression),
                            "decompression": _measurement(decompression),
                            "archive_bytes": archive_bytes,
                            "archive_sha256": archive_sha256,
                            "restored_sha256": restored_sha256,
                            "roundtrip_ok": roundtrip_ok,
                            "encoder_rss_limit_bytes_exclusive": (
                                encoder_limit
                            ),
                            "decoder_rss_limit_bytes_exclusive": (
                                DECODER_RSS_LIMIT
                            ),
                            "encoder_within_limit": encoder_within,
                            "decoder_within_limit": decoder_within,
                        }
                    )
                    sequence += 1

    immutable_after = {
        "runner_sha256": sha256_file(runner_path),
        "binary_sha256": sha256_file(binary_path),
        "time_sha256": sha256_file(time_binary),
        "manifest_sha256": sha256_file(manifest_path),
        "input_sha256": sha256_file(input_path),
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
    gate_status, reasons = assess_gate(
        rows,
        dataset_id=entry.dataset_id,
        original_bytes=entry.bytes,
        repetitions=args.repetitions,
        warmups=args.warmups,
        profiles=profiles,
        source_stable=source_stable,
        immutable_inputs_stable=immutable_stable,
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": args.experiment_id,
        "evidence_scope": "development",
        "gate": "Gate 3 - Memory",
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
        "binary": {
            "path": str(binary_path),
            "sha256": sha256_file(binary_path),
            "version": probe_text(
                [str(binary_path), "--version"],
                cwd=repository,
            ),
        },
        "runtime_profiles": {
            profile: {
                "config_path": (
                    config_root / profile / "profile.toml"
                ).relative_to(repository).as_posix(),
                "config_file_sha256": sha256_file(
                    config_root / profile / "profile.toml"
                ),
                "runtime_profile_sha256": (
                    by_profile[profile].runtime_profile_sha256
                ),
                "measurement_config_sha256": (
                    by_profile[profile].config_sha256
                ),
            }
            for profile in profiles
        },
        "measurement": {
            "method": "gnu-time-v1-monotonic-wall",
            "time_path": str(time_binary),
            "time_sha256": sha256_file(time_binary),
            "time_version": probe_text(
                [str(time_binary), "--version"],
                cwd=repository,
            ).replace("\n", " | "),
            "warmups": args.warmups,
            "repetitions": args.repetitions,
            "seed": args.seed,
            "interleaved_profile_order": True,
            "strict_threshold_operator": "<",
            "native_limit_policy": {
                "max_input_bytes": max_input,
                "max_archive_bytes": max_archive,
                "max_output_bytes": max_output,
            },
            "limitations": [
                "GNU time %M is process peak RSS in KiB multiplied by 1024",
                "filesystem cache is not dropped between trials",
                "CPU frequency/governor is not controlled",
                "this report contains one machine/architecture only",
            ],
        },
        "rows": rows,
        "runner_sha256": sha256_file(runner_path),
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
        print(f"memory gate error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(
                    (
                        args.output.resolve()
                        if args.output.is_absolute()
                        else (args.repository.resolve() / args.output).resolve()
                    )
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
