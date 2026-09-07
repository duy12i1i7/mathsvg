"""Fair, file-producing benchmark orchestration for MathZip and baselines."""

from __future__ import annotations

import csv
import datetime as dt
import fcntl
import hashlib
import json
import os
import resource
import shutil
import stat
import statistics
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .codecs import Codec, CommandPlan, build_codec, compressor_tool_names
from .common import (
    atomic_write_json,
    atomic_write_text,
    flatten_mapping,
    quote_command,
    sha256_file,
    shannon_entropy,
)
from .process import CommandResult, run_command
from .schema import (
    CSV_FIELDS,
    MATHZIP_SEARCH_PROVENANCE_FIELDS,
    RESULT_SCHEMA_VERSION,
    mathzip_encode_metrics_error,
    mathzip_inspection_error,
    storage_metrics,
    throughput_mbps,
)
from .system_info import collect_source_metadata, collect_system_metadata


CHECKPOINT_SCHEMA_VERSION = "mathzip-benchmark-checkpoint-v1"
CHECKPOINT_ROW_SCHEMA_VERSION = "mathzip-benchmark-checkpoint-row-v1"
EXPECTED_GRID_SCHEMA_VERSION = "mathzip-benchmark-expected-grid-v1"
RUN_IDENTITY_SCHEMA_VERSION = "mathzip-benchmark-run-identity-v1"


@dataclass(frozen=True)
class InputCase:
    corpus: str
    path: Path
    recorded_path: str
    tags: dict[str, Any]
    input_license: str | None = None
    input_provenance: str | None = None
    expected_sha256: str | None = None
    expected_size: int | None = None
    input_manifest: str | None = None
    input_manifest_sha256: str | None = None


@dataclass
class Trial:
    index: int
    status: str
    error: str | None
    compressed_bytes: int | None
    compression_seconds: float | None
    decompression_seconds: float | None
    compression_cpu_seconds: float | None
    decompression_cpu_seconds: float | None
    compression_peak_rss_bytes: int | None
    decompression_peak_rss_bytes: int | None
    archive_sha256: str | None
    restored_sha256: str | None
    roundtrip_verified: bool
    compression_command: list[str]
    decompression_command: list[str]
    compression_stderr: str
    decompression_stderr: str
    inspection: dict[str, Any] | None
    compression_metrics: dict[str, Any] | None = None
    compression_metrics_command: list[str] | None = None
    compression_metrics_stderr: str | None = None


def _decode_limited(value: bytes, limit: int = 8192) -> str:
    text = value[:limit].decode("utf-8", errors="replace").strip()
    if len(value) > limit:
        text += f"\n...[truncated {len(value) - limit} bytes]"
    return text


def _copy_timed(source: Path, destination: Path) -> tuple[float, float, int | None]:
    usage_before = resource.getrusage(resource.RUSAGE_SELF)
    cpu_before = time.process_time()
    started = time.perf_counter()
    shutil.copyfile(source, destination)
    elapsed = time.perf_counter() - started
    cpu = max(0.0, time.process_time() - cpu_before)
    usage_after = resource.getrusage(resource.RUSAGE_SELF)
    # ru_maxrss is a process-lifetime high-water mark, so it is not an isolated
    # measurement. Do not report it as though it were per-operation.
    peak = None
    if usage_after.ru_maxrss > usage_before.ru_maxrss:
        scale = 1024 if os.name == "posix" and os.uname().sysname != "Darwin" else 1
        peak = int(usage_after.ru_maxrss * scale)
    return elapsed, cpu, peak


def _failed_trial(
    index: int,
    status: str,
    error: str,
    compression: CommandResult | None = None,
    decompression: CommandResult | None = None,
) -> Trial:
    return Trial(
        index=index,
        status=status,
        error=error,
        compressed_bytes=None,
        compression_seconds=compression.wall_seconds if compression else None,
        decompression_seconds=decompression.wall_seconds if decompression else None,
        compression_cpu_seconds=compression.cpu_seconds if compression else None,
        decompression_cpu_seconds=decompression.cpu_seconds if decompression else None,
        compression_peak_rss_bytes=compression.peak_rss_bytes if compression else None,
        decompression_peak_rss_bytes=decompression.peak_rss_bytes if decompression else None,
        archive_sha256=None,
        restored_sha256=None,
        roundtrip_verified=False,
        compression_command=compression.command if compression else [],
        decompression_command=decompression.command if decompression else [],
        compression_stderr=_decode_limited(compression.stderr) if compression else "",
        decompression_stderr=_decode_limited(decompression.stderr)
        if decompression
        else "",
        inspection=None,
    )


def _inspect_mathzip(codec: Codec, archive: Path, timeout: float) -> dict[str, Any] | None:
    plan = codec.inspection_plan(archive)
    if plan is None:
        return None
    try:
        result = run_command(plan.command, timeout=timeout)
    except OSError as exc:
        return {"_inspection_error": f"cannot execute inspect: {exc}"}
    if result.timed_out or result.returncode != 0:
        return {
            "_inspection_error": _decode_limited(result.stderr)
            or (
                f"inspect timed out after {timeout:g} seconds"
                if result.timed_out
                else f"inspect exited with {result.returncode}"
            )
        }
    try:
        parsed = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"_inspection_error": f"invalid inspect JSON: {exc}"}
    validation_error = mathzip_inspection_error(parsed)
    if validation_error is not None:
        return {"_inspection_error": validation_error}
    if parsed["compressed_size"] != archive.stat().st_size:
        return {
            "_inspection_error": (
                "inspect compressed_size differs from the measured archive size"
            )
        }
    return dict(parsed)


def _read_mathzip_encode_metrics(path: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    if path is None or not path.is_file():
        return None, "compressor produced no encoder metrics file"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid encoder metrics JSON: {exc}"
    validation_error = mathzip_encode_metrics_error(parsed)
    if validation_error is not None:
        return None, validation_error
    return dict(parsed), None


def _timed_compression_plan(
    codec: Codec,
    source: Path,
    destination: Path,
    mathzip_metrics_mode: str,
) -> CommandPlan:
    if codec.family == "mathzip" and mathzip_metrics_mode == "inline":
        plan = codec.compression_metrics_plan(source, destination)
        if plan is None:
            raise ValueError("codec produced no inline encoder metrics command")
        return plan
    return codec.compression_plan(source, destination)


def execute_trial(
    codec: Codec,
    input_path: Path,
    original_sha256: str,
    index: int,
    timeout: float,
    *,
    collect_inspection: bool,
    mathzip_metrics_mode: str = "probe",
) -> Trial:
    with tempfile.TemporaryDirectory(prefix="mathzip-trial-") as temporary:
        work = Path(temporary)
        archive = work / f"archive{codec.extension}"
        restored = work / "restored.bin"
        if codec.family == "raw":
            try:
                compression_seconds, compression_cpu, compression_peak = _copy_timed(
                    input_path, archive
                )
                decompression_seconds, decompression_cpu, decompression_peak = _copy_timed(
                    archive, restored
                )
            except OSError as exc:
                return _failed_trial(index, "error", f"raw copy failed: {exc}")
            restored_sha256 = sha256_file(restored)
            verified = restored_sha256 == original_sha256
            return Trial(
                index=index,
                status="ok" if verified else "checksum_mismatch",
                error=None if verified else "raw round-trip SHA-256 mismatch",
                compressed_bytes=archive.stat().st_size,
                compression_seconds=compression_seconds,
                decompression_seconds=decompression_seconds,
                compression_cpu_seconds=compression_cpu,
                decompression_cpu_seconds=decompression_cpu,
                compression_peak_rss_bytes=compression_peak,
                decompression_peak_rss_bytes=decompression_peak,
                archive_sha256=sha256_file(archive),
                restored_sha256=restored_sha256,
                roundtrip_verified=verified,
                compression_command=["stdlib:shutil.copyfile"],
                decompression_command=["stdlib:shutil.copyfile"],
                compression_stderr="",
                decompression_stderr="",
                inspection=None,
            )

        try:
            compression_plan = _timed_compression_plan(
                codec,
                input_path,
                archive,
                mathzip_metrics_mode,
            )
            compression = run_command(
                compression_plan.command,
                timeout=timeout,
                stdout_path=archive if compression_plan.stdout_to_output else None,
                stdin_path=compression_plan.stdin_path,
            )
        except OSError as exc:
            return _failed_trial(index, "error", f"cannot start compressor: {exc}")
        if compression.timed_out:
            return _failed_trial(
                index, "compression_timeout", "compression timed out", compression
            )
        if compression.returncode != 0:
            return _failed_trial(
                index,
                "compression_failed",
                f"compressor exited with {compression.returncode}: "
                f"{_decode_limited(compression.stderr)}",
                compression,
            )
        if not archive.is_file():
            return _failed_trial(
                index, "compression_failed", "compressor produced no archive", compression
            )
        try:
            decompression_plan = codec.decompression_plan(archive, restored)
            decompression = run_command(
                decompression_plan.command,
                timeout=timeout,
                stdout_path=restored if decompression_plan.stdout_to_output else None,
            )
        except OSError as exc:
            return _failed_trial(
                index, "error", f"cannot start decompressor: {exc}", compression
            )
        if decompression.timed_out:
            return _failed_trial(
                index,
                "decompression_timeout",
                "decompression timed out",
                compression,
                decompression,
            )
        if decompression.returncode != 0:
            return _failed_trial(
                index,
                "decompression_failed",
                f"decompressor exited with {decompression.returncode}: "
                f"{_decode_limited(decompression.stderr)}",
                compression,
                decompression,
            )
        if not restored.is_file():
            return _failed_trial(
                index,
                "decompression_failed",
                "decompressor produced no restored file",
                compression,
                decompression,
            )
        restored_sha256 = sha256_file(restored)
        verified = restored_sha256 == original_sha256
        inspection = (
            _inspect_mathzip(codec, archive, timeout) if collect_inspection else None
        )
        inspection_error = None
        if collect_inspection and codec.family == "mathzip":
            inspection_error = mathzip_inspection_error(inspection)
        compression_metrics = None
        metrics_error = None
        metrics_command = None
        metrics_stderr = None
        if (
            verified
            and codec.family == "mathzip"
            and mathzip_metrics_mode == "inline"
        ):
            metrics_command = compression.command
            metrics_stderr = _decode_limited(compression.stderr)
            compression_metrics, metrics_error = _read_mathzip_encode_metrics(
                compression_plan.metrics_path
            )
        elif verified and collect_inspection and codec.family == "mathzip":
            metrics_archive = work / "metrics-probe.mz"
            metrics_plan = codec.compression_metrics_plan(
                input_path, metrics_archive
            )
            if metrics_plan is None:
                metrics_error = "codec produced no encoder metrics command"
            else:
                try:
                    metrics_result = run_command(
                        metrics_plan.command,
                        timeout=timeout,
                    )
                    metrics_command = metrics_result.command
                    metrics_stderr = _decode_limited(metrics_result.stderr)
                    if metrics_result.timed_out:
                        metrics_error = "encoder metrics probe timed out"
                    elif metrics_result.returncode != 0:
                        metrics_error = (
                            f"encoder metrics probe exited with "
                            f"{metrics_result.returncode}: {metrics_stderr}"
                        )
                    elif not metrics_archive.is_file():
                        metrics_error = "encoder metrics probe produced no archive"
                    elif sha256_file(metrics_archive) != sha256_file(archive):
                        metrics_error = (
                            "encoder metrics probe archive differs from measured archive"
                        )
                    else:
                        compression_metrics, metrics_error = (
                            _read_mathzip_encode_metrics(metrics_plan.metrics_path)
                        )
                except OSError as exc:
                    metrics_error = f"cannot start encoder metrics probe: {exc}"
        status = "ok" if verified else "checksum_mismatch"
        error = None if verified else "restored SHA-256 differs from the input"
        if verified and inspection_error is not None:
            status = "inspection_failed"
            error = inspection_error
        elif verified and metrics_error is not None:
            status = "compression_metrics_failed"
            error = metrics_error
        return Trial(
            index=index,
            status=status,
            error=error,
            compressed_bytes=archive.stat().st_size,
            compression_seconds=compression.wall_seconds,
            decompression_seconds=decompression.wall_seconds,
            compression_cpu_seconds=compression.cpu_seconds,
            decompression_cpu_seconds=decompression.cpu_seconds,
            compression_peak_rss_bytes=compression.peak_rss_bytes,
            decompression_peak_rss_bytes=decompression.peak_rss_bytes,
            archive_sha256=sha256_file(archive),
            restored_sha256=restored_sha256,
            roundtrip_verified=verified,
            compression_command=compression.command,
            decompression_command=decompression.command,
            compression_stderr=_decode_limited(compression.stderr),
            decompression_stderr=_decode_limited(decompression.stderr),
            inspection=inspection,
            compression_metrics=compression_metrics,
            compression_metrics_command=metrics_command,
            compression_metrics_stderr=metrics_stderr,
        )


def _number_from_inspection(
    inspection: Mapping[str, Any] | None, aliases: Sequence[str]
) -> float | int | None:
    if not inspection:
        return None
    flattened = {
        key.lower().replace("-", "_"): value
        for key, value in flatten_mapping(inspection).items()
    }
    for alias in aliases:
        normalized = alias.lower().replace("-", "_")
        for key, value in flattened.items():
            if key == normalized or key.endswith("." + normalized):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return value
    return None


def _value_from_inspection(
    inspection: Mapping[str, Any] | None, aliases: Sequence[str]
) -> Any:
    if not inspection:
        return None
    for alias in aliases:
        if alias in inspection:
            return inspection[alias]
    flattened = flatten_mapping(inspection)
    for alias in aliases:
        for key, value in flattened.items():
            if key.lower().endswith("." + alias.lower()) or key.lower() == alias.lower():
                return value
    return None


def _mathzip_metrics(successful: Sequence[Trial]) -> dict[str, Any]:
    inspections = [
        trial.inspection
        for trial in successful
        if trial.inspection and "_inspection_error" not in trial.inspection
    ]
    inspection = inspections[-1] if inspections else None
    segments = _value_from_inspection(inspection, ("segments", "segment_descriptors"))
    sizes: list[int] = []
    if isinstance(segments, list):
        for segment in segments:
            if isinstance(segment, dict):
                value = segment.get("length", segment.get("segment_length"))
                if isinstance(value, int):
                    sizes.append(value)
    segment_count = _number_from_inspection(
        inspection, ("segment_count", "segments_count")
    )
    if segment_count is None and sizes:
        segment_count = len(sizes)
    phase_metrics: dict[str, float | None] = {}
    valid_encoder_metrics = [
        trial.compression_metrics
        for trial in successful
        if trial.compression_metrics is not None
        and mathzip_encode_metrics_error(trial.compression_metrics) is None
    ]
    for field in (
        "search_seconds",
        "model_fitting_seconds",
        "residual_coding_seconds",
    ):
        values = [
            float(metrics[field]) for metrics in valid_encoder_metrics
        ]
        phase_metrics[field] = statistics.median(values) if values else None
    provenance_declared = any(
        any(field in metrics for field in MATHZIP_SEARCH_PROVENANCE_FIELDS)
        for metrics in valid_encoder_metrics
    )
    provenance_records = [
        {
            field: metrics[field]
            for field in MATHZIP_SEARCH_PROVENANCE_FIELDS
        }
        for metrics in valid_encoder_metrics
        if all(field in metrics for field in MATHZIP_SEARCH_PROVENANCE_FIELDS)
    ]
    provenance_signatures = {
        json.dumps(record, sort_keys=True, separators=(",", ":"))
        for record in provenance_records
    }
    provenance_consistent = (
        provenance_declared
        and len(provenance_records) == len(valid_encoder_metrics)
        and len(provenance_signatures) == 1
    )
    search_provenance = (
        provenance_records[0]
        if provenance_records and provenance_consistent
        else None
    )
    return {
        "transform": _value_from_inspection(
            inspection,
            ("transform", "transforms", "selected_transform", "transform_type"),
        ),
        "segment_count": int(segment_count) if segment_count is not None else None,
        "mean_segment_size": statistics.fmean(sizes)
        if sizes
        else _number_from_inspection(inspection, ("mean_segment_size",)),
        "median_segment_size": statistics.median(sizes)
        if sizes
        else _number_from_inspection(inspection, ("median_segment_size",)),
        "model_distribution": _value_from_inspection(
            inspection, ("model_distribution", "models")
        ),
        "residual_distribution": _value_from_inspection(
            inspection, ("residual_distribution", "residual_coder_distribution")
        ),
        "model_parameter_bytes": _number_from_inspection(
            inspection, ("model_parameter_bytes", "parameter_bytes")
        ),
        "partition_metadata_bytes": _number_from_inspection(
            inspection, ("partition_metadata_bytes", "segment_metadata_bytes")
        ),
        "residual_bytes": _number_from_inspection(
            inspection,
            (
                "residual_bytes",
                "actual_residual_coded_bytes",
                "residual_coded_bytes",
                "residual_size",
            ),
        ),
        "container_overhead_bytes": _number_from_inspection(
            inspection,
            ("container_overhead_bytes", "header_footer_transform_bytes"),
        ),
        "raw_model_percent": _number_from_inspection(
            inspection, ("raw_model_percent", "raw_model_percentage")
        ),
        "raw_fallback_percent": _number_from_inspection(
            inspection, ("raw_fallback_percent", "raw_fallback_percentage")
        ),
        "estimated_residual_entropy": _number_from_inspection(
            inspection, ("estimated_residual_entropy", "residual_entropy")
        ),
        "actual_residual_coded_bytes": _number_from_inspection(
            inspection,
            (
                "actual_residual_coded_bytes",
                "residual_coded_bytes",
                "residual_bytes",
            ),
        ),
        "math_segments_winning_raw": _number_from_inspection(
            inspection, ("math_segments_winning_raw", "segments_winning_raw")
        ),
        "math_segments_winning_zstd": _number_from_inspection(
            inspection, ("math_segments_winning_zstd", "segments_winning_zstd")
        ),
        "search_seconds": phase_metrics["search_seconds"],
        "model_fitting_seconds": phase_metrics["model_fitting_seconds"],
        "residual_coding_seconds": phase_metrics["residual_coding_seconds"],
        "mathzip_search_provenance": search_provenance,
        "mathzip_search_provenance_consistent": (
            provenance_consistent if provenance_declared else None
        ),
        "inspection": inspection,
    }


def _median(values: Iterable[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.median(clean) if clean else None


def _case_input_evidence(
    case: InputCase, input_sha256: str, original_bytes: int
) -> dict[str, Any]:
    verified = (
        case.expected_sha256 is not None
        and case.expected_size is not None
        and input_sha256 == case.expected_sha256
        and original_bytes == case.expected_size
    )
    return {
        "input_manifest": case.input_manifest,
        "input_manifest_sha256": case.input_manifest_sha256,
        "input_manifest_verified": verified,
    }


def _empty_row(
    *,
    run_id: str,
    profile: str,
    corpus: str,
    input_path: str,
    codec: Codec,
    requested_repeats: int,
    status: str,
    error: str,
    tags: Mapping[str, Any] | None = None,
    input_license: str | None = None,
    input_provenance: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {field: None for field in CSV_FIELDS}
    row.update(
        {
            "schema_version": RESULT_SCHEMA_VERSION,
            "run_id": run_id,
            "profile": profile,
            "corpus": corpus,
            "input_path": input_path,
            "input_name": Path(input_path).name,
            "input_license": input_license,
            "input_provenance": input_provenance,
            "codec": codec.name,
            "codec_family": codec.family,
            "codec_level": codec.level,
            "variant": codec.variant,
            "threads": codec.threads,
            "status": status,
            "error": error,
            "successful_repeats": 0,
            "requested_repeats": requested_repeats,
            "roundtrip_verified": False,
            "deterministic_archive": None,
            "tags": dict(tags or {}),
            "trials": [],
        }
    )
    return row


def aggregate_trials(
    *,
    run_id: str,
    profile: str,
    case: InputCase,
    input_sha256: str,
    input_entropy: float,
    original_bytes: int,
    codec: Codec,
    requested_repeats: int,
    trials: Sequence[Trial],
) -> dict[str, Any]:
    input_evidence = _case_input_evidence(case, input_sha256, original_bytes)
    successful = [trial for trial in trials if trial.status == "ok"]
    if not successful:
        errors = sorted(
            {
                f"{trial.status}: {trial.error or 'unknown failure'}"
                for trial in trials
            }
        )
        row = _empty_row(
            run_id=run_id,
            profile=profile,
            corpus=case.corpus,
            input_path=case.recorded_path,
            codec=codec,
            requested_repeats=requested_repeats,
            status=trials[-1].status if trials else "not_run",
            error="; ".join(errors) if errors else "no successful trials",
            tags=case.tags,
            input_license=case.input_license,
            input_provenance=case.input_provenance,
        )
        row.update(
            {
                "input_sha256": input_sha256,
                "input_entropy_bits_per_byte": input_entropy,
                "original_bytes": original_bytes,
                "trials": [asdict(trial) for trial in trials],
                **input_evidence,
            }
        )
        return row

    compressed_bytes_value = _median(
        trial.compressed_bytes for trial in successful
    )
    assert compressed_bytes_value is not None
    compressed_bytes = int(compressed_bytes_value)
    compression_seconds = _median(
        trial.compression_seconds for trial in successful
    )
    decompression_seconds = _median(
        trial.decompression_seconds for trial in successful
    )
    compression_peak = _median(
        trial.compression_peak_rss_bytes for trial in successful
    )
    decompression_peak = _median(
        trial.decompression_peak_rss_bytes for trial in successful
    )
    archives = {trial.archive_sha256 for trial in successful}
    restored = {trial.restored_sha256 for trial in successful}
    status = "ok" if len(successful) == requested_repeats else "partial_failure"
    errors = sorted(
        {
            f"{trial.status}: {trial.error or 'unknown failure'}"
            for trial in trials
            if trial.status != "ok"
        }
    )
    sizes = {trial.compressed_bytes for trial in successful}
    if codec.family == "mathzip" and (len(archives) != 1 or len(sizes) != 1):
        status = "determinism_failure"
        errors.append(
            "MathZip produced different archive hashes or sizes across successful repeats"
        )
    metrics = _mathzip_metrics(successful) if codec.family == "mathzip" else {}
    row = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "profile": profile,
        "corpus": case.corpus,
        "input_path": case.recorded_path,
        "input_name": case.path.name,
        "input_license": case.input_license,
        "input_provenance": case.input_provenance,
        **input_evidence,
        "input_sha256": input_sha256,
        "input_entropy_bits_per_byte": input_entropy,
        "original_bytes": original_bytes,
        "codec": codec.name,
        "codec_family": codec.family,
        "codec_level": codec.level,
        "variant": codec.variant,
        "threads": codec.threads,
        "status": status,
        "error": "; ".join(errors) if errors else None,
        "successful_repeats": len(successful),
        "requested_repeats": requested_repeats,
        "compressed_bytes": compressed_bytes,
        **storage_metrics(original_bytes, compressed_bytes),
        "compression_seconds": compression_seconds,
        "decompression_seconds": decompression_seconds,
        "compression_mbps": throughput_mbps(original_bytes, compression_seconds),
        "decompression_mbps": throughput_mbps(
            original_bytes, decompression_seconds
        ),
        "compression_cpu_seconds": _median(
            trial.compression_cpu_seconds for trial in successful
        ),
        "decompression_cpu_seconds": _median(
            trial.decompression_cpu_seconds for trial in successful
        ),
        "peak_rss_bytes": max(
            value for value in (compression_peak, decompression_peak) if value is not None
        )
        if compression_peak is not None or decompression_peak is not None
        else None,
        "roundtrip_verified": all(
            trial.roundtrip_verified for trial in successful
        ),
        "deterministic_archive": len(archives) == 1,
        "archive_sha256": next(iter(archives)) if len(archives) == 1 else None,
        "restored_sha256": next(iter(restored)) if len(restored) == 1 else None,
        "transform": metrics.get("transform"),
        "segment_count": metrics.get("segment_count"),
        "mean_segment_size": metrics.get("mean_segment_size"),
        "median_segment_size": metrics.get("median_segment_size"),
        "model_distribution": metrics.get("model_distribution"),
        "residual_distribution": metrics.get("residual_distribution"),
        "model_parameter_bytes": metrics.get("model_parameter_bytes"),
        "partition_metadata_bytes": metrics.get("partition_metadata_bytes"),
        "residual_bytes": metrics.get("residual_bytes"),
        "container_overhead_bytes": metrics.get("container_overhead_bytes"),
        "raw_model_percent": metrics.get("raw_model_percent"),
        "raw_fallback_percent": metrics.get("raw_fallback_percent"),
        "estimated_residual_entropy": metrics.get("estimated_residual_entropy"),
        "actual_residual_coded_bytes": metrics.get(
            "actual_residual_coded_bytes"
        ),
        "math_segments_winning_raw": metrics.get("math_segments_winning_raw"),
        "math_segments_winning_zstd": metrics.get(
            "math_segments_winning_zstd"
        ),
        "search_seconds": metrics.get("search_seconds"),
        "model_fitting_seconds": metrics.get("model_fitting_seconds"),
        "residual_coding_seconds": metrics.get("residual_coding_seconds"),
        "mathzip_search_provenance": metrics.get(
            "mathzip_search_provenance"
        ),
        "mathzip_search_provenance_consistent": metrics.get(
            "mathzip_search_provenance_consistent"
        ),
        "command_lines": sorted(
            {
                quote_command(trial.compression_command)
                for trial in successful
                if trial.compression_command
            }
            | {
                quote_command(trial.decompression_command)
                for trial in successful
                if trial.decompression_command
            }
            | {
                quote_command(trial.compression_metrics_command)
                for trial in successful
                if trial.compression_metrics_command
            }
        ),
        "tags": case.tags,
        "trials": [asdict(trial) for trial in trials],
    }
    if metrics.get("inspection") is not None:
        row["mathzip_inspection"] = metrics["inspection"]
    return row


def _load_synthetic_tags(root: Path) -> dict[str, dict[str, Any]]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    tags = {}
    for entry in manifest.get("entries", []):
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            tags[entry["path"]] = {
                key: entry.get(key)
                for key in ("family", "noise_density", "seed", "size_requested")
            }
    return tags


def _input_rights_and_provenance(
    root: Path, source: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    license_value = source.get("input_license", source.get("license"))
    provenance_value = source.get("input_provenance", source.get("provenance"))
    metadata: dict[str, Any] = {}
    for name in ("dataset.json", "manifest.json"):
        path = root / name
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            metadata = dict(value)
            metadata["_metadata_filename"] = name
            break
    if license_value is None:
        license_value = metadata.get("license")
    if provenance_value is None and metadata:
        homepage = metadata.get("homepage")
        generator = metadata.get("generator")
        if homepage:
            provenance_value = f"{homepage} ({metadata['_metadata_filename']})"
        elif generator:
            provenance_value = (
                f"{generator} ({metadata['_metadata_filename']})"
            )
        else:
            provenance_value = metadata["_metadata_filename"]
    return (
        str(license_value) if license_value is not None else None,
        str(provenance_value) if provenance_value is not None else None,
    )


def _load_input_manifest(
    root: Path,
) -> tuple[dict[str, tuple[str, int]], Path | None, str | None]:
    manifest_path = next(
        (
            candidate
            for candidate in (root / "dataset.json", root / "manifest.json")
            if candidate.is_file()
        ),
        None,
    )
    if manifest_path is None:
        return {}, None, None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read input manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, Mapping):
        raise ValueError(f"input manifest {manifest_path} is not an object")

    entries: dict[str, tuple[str, int]] = {}

    def add(relative: Any, checksum: Any, size: Any) -> None:
        if (
            not isinstance(relative, str)
            or not isinstance(checksum, str)
            or len(checksum) != 64
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise ValueError(f"invalid file evidence in {manifest_path}")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"unsafe file path in {manifest_path}: {relative}")
        normalized = relative_path.as_posix()
        if normalized in entries:
            raise ValueError(f"duplicate file evidence in {manifest_path}: {relative}")
        entries[normalized] = (checksum.lower(), size)

    listed = manifest.get("files", manifest.get("entries", []))
    if isinstance(listed, list):
        for entry in listed:
            if isinstance(entry, Mapping):
                add(entry.get("path"), entry.get("sha256"), entry.get("size_bytes"))
    versions = manifest.get("versions")
    if isinstance(versions, list):
        for version in versions:
            if not isinstance(version, Mapping):
                continue
            add(version.get("path"), version.get("sha256"), version.get("size_bytes"))
            version_name = version.get("version")
            members = version.get("members")
            if isinstance(version_name, str) and isinstance(members, list):
                for member in members:
                    if isinstance(member, Mapping):
                        add(
                            f"versions/{version_name}/{member.get('path')}",
                            member.get("sha256"),
                            member.get("length"),
                        )
    all_versions = manifest.get("all_versions")
    if isinstance(all_versions, Mapping):
        add(
            all_versions.get("path"),
            all_versions.get("sha256"),
            all_versions.get("size_bytes"),
        )
    if not entries:
        raise ValueError(f"input manifest {manifest_path} contains no file evidence")
    return entries, manifest_path, sha256_file(manifest_path)


def discover_inputs(
    config: Mapping[str, Any], repository_root: Path
) -> tuple[list[InputCase], list[dict[str, Any]]]:
    cases: list[InputCase] = []
    missing: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for source in config.get("inputs", []):
        if not isinstance(source, dict):
            raise ValueError("inputs entries must be mappings")
        if not source.get("enabled", True):
            continue
        corpus = str(source.get("corpus", "unknown"))
        configured_root = Path(str(source.get("root", ".")))
        root = (
            configured_root
            if configured_root.is_absolute()
            else (repository_root / configured_root).resolve()
        )
        required = bool(source.get("required", True))
        input_license, input_provenance = _input_rights_and_provenance(
            root, source
        )
        if not root.is_dir():
            if required:
                missing.append(
                    {
                        "corpus": corpus,
                        "path": _record_path(root, repository_root),
                        "reason": "input root missing",
                        "input_license": input_license,
                        "input_provenance": input_provenance,
                    }
                )
            continue
        manifest_entries, manifest_path, manifest_sha256 = _load_input_manifest(root)
        patterns = source.get("patterns", ["**/*"])
        excludes = source.get("exclude", [])
        selected: list[Path] = []
        for pattern in patterns:
            selected.extend(path for path in root.glob(str(pattern)) if path.is_file())
        synthetic_tags = _load_synthetic_tags(root)
        for path in sorted(set(selected)):
            relative = path.relative_to(root)
            if any(relative.match(str(pattern)) for pattern in excludes):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            base_tags = dict(source.get("tags", {}))
            base_tags.update(synthetic_tags.get(relative.as_posix(), {}))
            try:
                recorded_path = resolved.relative_to(
                    repository_root.resolve()
                ).as_posix()
            except ValueError:
                recorded_path = f"<external>/{corpus}/{relative.as_posix()}"
            cases.append(
                InputCase(
                    corpus=corpus,
                    path=resolved,
                    recorded_path=recorded_path,
                    tags=base_tags,
                    input_license=input_license,
                    input_provenance=input_provenance,
                    expected_sha256=manifest_entries.get(relative.as_posix(), (None, None))[0],
                    expected_size=manifest_entries.get(relative.as_posix(), (None, None))[1],
                    input_manifest=_record_path(manifest_path, repository_root)
                    if manifest_path is not None
                    else None,
                    input_manifest_sha256=manifest_sha256,
                )
            )
        max_files = source.get("max_files")
        if max_files is not None:
            corpus_cases = [case for case in cases if case.corpus == corpus]
            if len(corpus_cases) > int(max_files):
                keep = {
                    case.path for case in corpus_cases[: int(max_files)]
                }
                cases = [
                    case
                    for case in cases
                    if case.corpus != corpus or case.path in keep
                ]
        if required and not any(case.corpus == corpus for case in cases):
            missing.append(
                {
                    "corpus": corpus,
                    "path": _record_path(root, repository_root),
                    "reason": "no files matched configured patterns",
                    "input_license": input_license,
                    "input_provenance": input_provenance,
                }
            )
    return cases, missing


def _record_path(path: Path, repository_root: Path) -> str:
    try:
        return path.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return f"<external>/{path.name}"


def _sanitize_argument(argument: str, repository_root: Path) -> str:
    if not os.path.isabs(argument):
        return argument
    path = Path(argument)
    try:
        relative = path.resolve().relative_to(repository_root.resolve())
        return "<repo>/" + relative.as_posix()
    except ValueError:
        pass
    temporary_root = Path(tempfile.gettempdir()).resolve()
    try:
        relative = path.resolve().relative_to(temporary_root)
        if relative.parts and relative.parts[0].startswith("mathzip-trial-"):
            return "<work>/" + Path(*relative.parts[1:]).as_posix()
        return "<tmp>/" + relative.as_posix()
    except ValueError:
        return f"<external>/{path.name}"


def _sanitize_trial_paths(trial: Trial, repository_root: Path) -> None:
    trial.compression_command = [
        _sanitize_argument(value, repository_root)
        for value in trial.compression_command
    ]
    trial.decompression_command = [
        _sanitize_argument(value, repository_root)
        for value in trial.decompression_command
    ]
    if trial.compression_metrics_command is not None:
        trial.compression_metrics_command = [
            _sanitize_argument(value, repository_root)
            for value in trial.compression_metrics_command
        ]


def _executable_hashes(codecs: Sequence[Codec]) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for codec in codecs:
        executable = codec.executable
        if not executable or executable in hashes:
            continue
        path = Path(executable)
        try:
            hashes[executable] = sha256_file(path) if path.is_file() else None
        except OSError:
            hashes[executable] = None
    return hashes


def _mathzip_build_infos(
    codecs: Sequence[Codec],
) -> dict[str, dict[str, Any] | None]:
    infos: dict[str, dict[str, Any] | None] = {}
    for codec in codecs:
        executable = codec.executable
        if codec.family != "mathzip" or not executable or executable in infos:
            continue
        try:
            result = run_command([executable, "build-info"], timeout=10.0)
            parsed = json.loads(result.stdout.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            infos[executable] = None
            continue
        if (
            result.timed_out
            or result.returncode != 0
            or not isinstance(parsed, dict)
            or not isinstance(parsed.get("package_version"), str)
            or parsed.get("source_revision") is not None
            and not isinstance(parsed.get("source_revision"), str)
            or not isinstance(parsed.get("source_dirty"), bool)
        ):
            infos[executable] = None
        else:
            infos[executable] = dict(parsed)
    return infos


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _result_key(value: Mapping[str, Any]) -> tuple[str, str, str, int]:
    corpus = value.get("corpus")
    input_path = value.get("input_path")
    codec = value.get("codec")
    threads = value.get("threads")
    if (
        not isinstance(corpus, str)
        or not isinstance(input_path, str)
        or not isinstance(codec, str)
        or not isinstance(threads, int)
        or isinstance(threads, bool)
    ):
        raise ValueError(
            "benchmark row key requires string corpus/input_path/codec "
            "and integer threads"
        )
    return corpus, input_path, codec, threads


def _expected_grid(
    cases: Sequence[InputCase],
    missing_inputs: Sequence[Mapping[str, Any]],
    codecs: Sequence[Codec],
) -> tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]]]:
    inputs: list[dict[str, Any]] = []
    input_state: dict[tuple[str, str], dict[str, Any]] = {}
    for missing in missing_inputs:
        entry = {
            "corpus": str(missing["corpus"]),
            "input_path": str(missing["path"]),
            "missing_reason": str(missing["reason"]),
            "input_license": missing.get("input_license"),
            "input_provenance": missing.get("input_provenance"),
            "input_manifest": None,
            "input_manifest_sha256": None,
            "expected_input_sha256": None,
            "expected_input_size": None,
            "actual_input_sha256": None,
            "actual_input_size": None,
            "input_manifest_verified": None,
        }
        inputs.append(entry)
        input_state[(entry["corpus"], entry["input_path"])] = entry
    for case in cases:
        checksum = sha256_file(case.path)
        size = case.path.stat().st_size
        evidence = _case_input_evidence(case, checksum, size)
        entry = {
            "corpus": case.corpus,
            "input_path": case.recorded_path,
            "missing_reason": None,
            "input_license": case.input_license,
            "input_provenance": case.input_provenance,
            "input_manifest": case.input_manifest,
            "input_manifest_sha256": case.input_manifest_sha256,
            "expected_input_sha256": case.expected_sha256,
            "expected_input_size": case.expected_size,
            "actual_input_sha256": checksum,
            "actual_input_size": size,
            "input_manifest_verified": evidence["input_manifest_verified"],
        }
        inputs.append(entry)
        input_state[(case.corpus, case.recorded_path)] = entry
    input_keys = [(entry["corpus"], entry["input_path"]) for entry in inputs]
    if len(set(input_keys)) != len(input_keys):
        raise ValueError("expected benchmark grid contains duplicate corpus/input keys")

    codec_entries = [
        {
            "codec": codec.name,
            "threads": codec.threads,
        }
        for codec in codecs
    ]
    codec_keys = [(entry["codec"], entry["threads"]) for entry in codec_entries]
    if len(set(codec_keys)) != len(codec_keys):
        raise ValueError("expected benchmark grid contains duplicate codec/thread keys")
    body = {
        "schema_version": EXPECTED_GRID_SCHEMA_VERSION,
        "key_fields": ["corpus", "input_path", "codec", "threads"],
        "inputs": inputs,
        "codecs": codec_entries,
        "expected_result_count": len(inputs) * len(codec_entries),
    }
    return {**body, "sha256": _json_sha256(body)}, input_state


def _grid_keys(grid: Mapping[str, Any]) -> list[tuple[str, str, str, int]]:
    keys: list[tuple[str, str, str, int]] = []
    for input_entry in grid.get("inputs", []):
        for codec_entry in grid.get("codecs", []):
            keys.append(
                (
                    str(input_entry["corpus"]),
                    str(input_entry["input_path"]),
                    str(codec_entry["codec"]),
                    int(codec_entry["threads"]),
                )
            )
    return keys


def _source_resume_identity(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: source.get(field)
        for field in (
            "source_revision",
            "source_dirty",
            "source_tree_sha256",
            "source_file_count",
        )
    }


def _host_resume_identity(system: Mapping[str, Any]) -> dict[str, Any]:
    hostname = str(system.get("hostname") or "")
    execution_state = {
        "kernel": system.get("kernel"),
        "architecture": system.get("architecture"),
        "swap_bytes": system.get("swap_bytes"),
        "cpu_affinity": system.get("cpu_affinity"),
        "cpu_governors": system.get("cpu_governors"),
        "container": system.get("container"),
        "relevant_environment": system.get("relevant_environment"),
        "python_version": system.get("python_version"),
        "storage": {
            field: (
                system.get("storage", {}).get(field)
                if isinstance(system.get("storage"), Mapping)
                else None
            )
            for field in (
                "filesystem_type",
                "mount_point",
                "mount_source",
                "total_bytes",
            )
        },
    }
    return {
        "hostname_sha256": hashlib.sha256(hostname.encode("utf-8")).hexdigest(),
        "cpu_model": system.get("cpu_model"),
        "logical_cores": system.get("logical_cores"),
        "physical_cores": system.get("physical_cores"),
        "ram_bytes": system.get("ram_bytes"),
        "os": system.get("os"),
        "execution_state_sha256": _json_sha256(execution_state),
    }


def _codec_resume_identity(
    codecs: Sequence[Codec],
    executable_hashes: Mapping[str, str | None],
    mathzip_build_infos: Mapping[str, Mapping[str, Any] | None],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for codec in codecs:
        executable = codec.executable
        entries.append(
            {
                "codec": codec.name,
                "family": codec.family,
                "level": codec.level,
                "variant": codec.variant,
                "threads": codec.threads,
                "available": codec.available,
                "unavailable_reason": codec.available_reason,
                "executable_path_sha256": (
                    hashlib.sha256(executable.encode("utf-8")).hexdigest()
                    if executable
                    else None
                ),
                "executable_sha256": (
                    executable_hashes.get(executable) if executable else None
                ),
                "build_info": (
                    mathzip_build_infos.get(executable)
                    if codec.family == "mathzip" and executable
                    else None
                ),
            }
        )
    return entries


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise ValueError(f"cannot stat {label} {path}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISREG(mode):
        raise ValueError(f"{label} must be a regular non-symlink file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} {path} is not a JSON object")
    return value


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_checkpoint_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)
    _fsync_directory(path.parent)


def _checkpoint_path(resume_from: Path) -> Path:
    supplied = resume_from.expanduser()
    if supplied.is_symlink():
        raise ValueError(f"resume target must not be a symlink: {supplied}")
    candidate = supplied / "checkpoint.json" if supplied.is_dir() else supplied
    if candidate.name != "checkpoint.json" or candidate.is_symlink():
        raise ValueError(
            "resume target must be a run directory or its regular checkpoint.json"
        )
    try:
        mode = candidate.stat(follow_symlinks=False).st_mode
    except OSError:
        mode = 0
    if not stat.S_ISREG(mode):
        raise ValueError(f"resume checkpoint is missing: {candidate}")
    return candidate.resolve()


def _load_checkpoint_rows(
    run_directory: Path,
    checkpoint: Mapping[str, Any],
    expected_keys: Sequence[tuple[str, str, str, int]],
) -> tuple[list[dict[str, Any]], dict[int, tuple[float, str]]]:
    row_directory = run_directory / "checkpoint-rows"
    try:
        row_mode = row_directory.stat(follow_symlinks=False).st_mode
    except OSError:
        row_mode = 0
    if row_directory.is_symlink() or not stat.S_ISDIR(row_mode):
        raise ValueError(f"resume checkpoint row directory is missing: {row_directory}")
    rows_by_index: dict[int, dict[str, Any]] = {}
    interval_progress: dict[int, tuple[float, str]] = {}
    for path in sorted(row_directory.glob("*.json")):
        try:
            path_mode = path.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            raise ValueError(f"cannot stat checkpoint row {path}: {exc}") from exc
        if path.is_symlink() or not stat.S_ISREG(path_mode):
            raise ValueError(
                f"checkpoint row must be a regular non-symlink file: {path}"
            )
        envelope = _read_json_object(path, "checkpoint row")
        if envelope.get("schema_version") != CHECKPOINT_ROW_SCHEMA_VERSION:
            raise ValueError(f"checkpoint row has unsupported schema: {path}")
        index = envelope.get("expected_grid_index")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or index >= len(expected_keys)
            or path.name != f"{index:08d}.json"
        ):
            raise ValueError(f"checkpoint row has invalid grid index: {path}")
        row = envelope.get("row")
        if not isinstance(row, dict):
            raise ValueError(f"checkpoint row payload is invalid: {path}")
        key = _result_key(row)
        if list(key) != envelope.get("key") or key != expected_keys[index]:
            raise ValueError(f"checkpoint row key does not match expected grid: {path}")
        if index in rows_by_index:
            raise ValueError(f"checkpoint contains duplicate grid index {index}")
        rows_by_index[index] = row
        interval_index = envelope.get("execution_interval_index")
        active_seconds = envelope.get("interval_active_seconds")
        completed_at = envelope.get("completed_at_utc")
        if (
            not isinstance(interval_index, int)
            or isinstance(interval_index, bool)
            or interval_index < 0
            or not isinstance(active_seconds, (int, float))
            or isinstance(active_seconds, bool)
            or not 0.0 <= float(active_seconds) < float("inf")
            or not isinstance(completed_at, str)
        ):
            raise ValueError(
                f"checkpoint row has invalid completion metadata: {path}"
            )
        previous = interval_progress.get(interval_index)
        if previous is None or float(active_seconds) > previous[0]:
            interval_progress[interval_index] = (
                float(active_seconds),
                completed_at,
            )
    recorded_count = checkpoint.get("completed_result_count")
    if (
        not isinstance(recorded_count, int)
        or isinstance(recorded_count, bool)
        or recorded_count < 0
        or recorded_count > len(rows_by_index)
    ):
        raise ValueError(
            "checkpoint completed_result_count exceeds durable checkpoint rows"
        )
    return (
        [rows_by_index[index] for index in sorted(rows_by_index)],
        interval_progress,
    )


def _resume_mismatch(
    stored: Mapping[str, Any],
    current: Mapping[str, Any],
) -> str | None:
    comparisons = (
        ("config SHA-256", "config_sha256"),
        ("config snapshot", "config_snapshot"),
        ("source revision/tree identity", "source"),
        ("available executable hashes/build-info", "codecs"),
        ("input manifest/grid evidence", "expected_grid_sha256"),
        ("host/execution-environment identity", "host"),
        ("benchmark methodology/publication policy", "methodology_policy"),
    )
    for label, field in comparisons:
        if stored.get(field) != current.get(field):
            return label
    return None


def _resume_source_is_clean(source: Mapping[str, Any]) -> bool:
    revision = source.get("source_revision")
    if revision is not None:
        return bool(revision) and source.get("source_dirty") is False
    return (
        source.get("source_dirty") in (None, False)
        and isinstance(source.get("source_tree_sha256"), str)
        and len(source["source_tree_sha256"]) == 64
    )


@contextmanager
def _exclusive_benchmark_lock(output_root: Path) -> Any:
    lock_identity = hashlib.sha256(
        str(output_root.resolve()).encode("utf-8")
    ).hexdigest()
    user_suffix = str(os.getuid()) if hasattr(os, "getuid") else "default"
    lock_root = (
        Path(tempfile.gettempdir()) / f"mathzip-benchmark-locks-{user_suffix}"
    )
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if lock_root.is_symlink() or not lock_root.is_dir():
        raise ValueError(f"unsafe benchmark lock directory: {lock_root}")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_root / f"{lock_identity}.lock", flags, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                f"another benchmark invocation already holds the output lock: "
                f"{output_root}"
            ) from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _codec_definition(
    codec: Codec,
    repository_root: Path,
    redact_paths: bool,
    executable_hashes: Mapping[str, str | None],
    mathzip_build_infos: Mapping[str, Mapping[str, Any] | None],
    mathzip_metrics_mode: str,
) -> dict[str, Any]:
    definition = {
        "name": codec.name,
        "family": codec.family,
        "level": codec.level,
        "variant": codec.variant,
        "threads": codec.threads,
        "executable": codec.executable,
        "executable_sha256": executable_hashes.get(codec.executable)
        if codec.executable
        else None,
        "available": codec.available,
        "unavailable_reason": codec.available_reason,
    }
    if codec.family == "mathzip" and codec.executable:
        definition["build_info"] = mathzip_build_infos.get(codec.executable)
    if codec.available and codec.family != "raw":
        source = Path("{input}")
        archive = Path("{archive}")
        restored = Path("{restored}")
        compression_plan = _timed_compression_plan(
            codec,
            source,
            archive,
            mathzip_metrics_mode,
        )
        definition["compression_command_template"] = compression_plan.command
        if compression_plan.stdin_path is not None:
            definition["compression_stdin_template"] = str(
                compression_plan.stdin_path
            )
        definition["decompression_command_template"] = codec.decompression_plan(
            archive, restored
        ).command
    elif codec.family == "raw":
        definition["compression_command_template"] = ["stdlib:shutil.copyfile"]
        definition["decompression_command_template"] = ["stdlib:shutil.copyfile"]
    if redact_paths:
        if isinstance(definition.get("executable"), str):
            definition["executable"] = _sanitize_argument(
                definition["executable"], repository_root
            )
        for key in (
            "compression_command_template",
            "compression_stdin_template",
            "decompression_command_template",
        ):
            if isinstance(definition.get(key), str):
                definition[key] = _sanitize_argument(
                    definition[key], repository_root
                )
            elif isinstance(definition.get(key), list):
                definition[key] = [
                    _sanitize_argument(str(value), repository_root)
                    for value in definition[key]
                ]
    return definition


def _sanitize_structure(value: Any, repository_root: Path) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_structure(item, repository_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_structure(item, repository_root) for item in value]
    if isinstance(value, str) and os.path.isabs(value):
        return _sanitize_argument(value, repository_root)
    return value


def _row_for_csv(row: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for field in CSV_FIELDS:
        if field == "model_distribution_json":
            value = row.get("model_distribution")
        elif field == "residual_distribution_json":
            value = row.get("residual_distribution")
        elif field == "command_lines_json":
            value = row.get("command_lines")
        elif field == "tags_json":
            value = row.get("tags")
        else:
            value = row.get(field)
        if isinstance(value, (dict, list)):
            output[field] = json.dumps(
                value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
        elif isinstance(value, bool):
            output[field] = "true" if value else "false"
        elif value is None:
            output[field] = ""
        else:
            output[field] = value
    return output


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=CSV_FIELDS, lineterminator="\n"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(_row_for_csv(row))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _run_benchmarks_unlocked(
    config: dict[str, Any],
    *,
    config_path: Path,
    repository_root: Path,
    mathzip_binary: str,
    output_override: Path | None = None,
    allow_short_run: bool = False,
    selected_codecs: Sequence[str] | None = None,
    resume_from: Path | None = None,
    require_publication_evidence: bool = False,
) -> tuple[dict[str, Any], Path]:
    invocation_started = dt.datetime.now(dt.timezone.utc)
    invocation_clock = time.perf_counter()
    benchmark = config.get("benchmark", {})
    if not isinstance(benchmark, dict):
        raise ValueError("benchmark must be a mapping")
    collect_inspection = bool(
        benchmark.get("collect_mathzip_inspection", True)
    )
    mathzip_metrics_mode = benchmark.get("mathzip_metrics_mode", "probe")
    if mathzip_metrics_mode not in ("probe", "inline"):
        raise ValueError(
            "benchmark.mathzip_metrics_mode must be 'probe' or 'inline'"
        )
    if mathzip_metrics_mode == "inline" and not collect_inspection:
        raise ValueError(
            "benchmark.mathzip_metrics_mode='inline' requires "
            "benchmark.collect_mathzip_inspection=true"
        )
    warmups = int(benchmark.get("warmups", 1))
    repeats = int(benchmark.get("repeats", 3))
    timeout = float(benchmark.get("timeout_seconds", 300.0))
    if warmups < 1 and not allow_short_run:
        raise ValueError("fair benchmark requires at least one warm-up")
    if repeats < 3 and not allow_short_run:
        raise ValueError("fair benchmark requires at least three repetitions")
    if repeats < 1 or warmups < 0:
        raise ValueError("repeats must be positive and warmups non-negative")
    if timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    threads_values = [int(value) for value in benchmark.get("threads", [1])]
    if not threads_values or any(value < 1 for value in threads_values):
        raise ValueError("benchmark.threads must contain positive integers")

    codec_entries = config.get("codecs", ["mathzip-balanced", "raw"])
    if selected_codecs:
        requested = set(selected_codecs)
        codec_entries = [
            entry
            for entry in codec_entries
            if (entry if isinstance(entry, str) else entry.get("name")) in requested
        ]
        missing_codec_names = requested - {
            entry if isinstance(entry, str) else str(entry.get("name"))
            for entry in codec_entries
        }
        if missing_codec_names:
            raise ValueError(
                "requested codecs are absent from config: "
                + ", ".join(sorted(missing_codec_names))
            )
    codecs: list[Codec] = []
    for threads in threads_values:
        for entry in codec_entries:
            multi_thread = (
                bool(entry.get("multi_thread", False))
                if isinstance(entry, dict)
                else False
            )
            if threads > 1 and not multi_thread:
                continue
            codecs.append(
                build_codec(
                    entry,
                    mathzip_binary=mathzip_binary,
                    threads=threads,
                )
            )
    if not codecs:
        raise ValueError("no codec/thread combinations are enabled")

    profile = str(config.get("profile", "custom"))
    redact_paths = not bool(benchmark.get("record_absolute_paths", False))
    cases, missing_inputs = discover_inputs(config, repository_root)
    if not redact_paths:
        cases = [
            InputCase(
                corpus=case.corpus,
                path=case.path,
                recorded_path=str(case.path),
                tags=case.tags,
                input_license=case.input_license,
                input_provenance=case.input_provenance,
                expected_sha256=case.expected_sha256,
                expected_size=case.expected_size,
                input_manifest=case.input_manifest,
                input_manifest_sha256=case.input_manifest_sha256,
            )
            for case in cases
        ]

    source_before = collect_source_metadata(repository_root)
    executable_hashes_before = _executable_hashes(codecs)
    mathzip_build_infos_before = _mathzip_build_infos(codecs)
    system_before = collect_system_metadata(
        compressor_tool_names(), repository_root
    )
    expected_grid, input_state = _expected_grid(cases, missing_inputs, codecs)
    expected_keys = _grid_keys(expected_grid)
    expected_key_indexes = {
        key: index for index, key in enumerate(expected_keys)
    }
    if len(expected_key_indexes) != len(expected_keys):
        raise ValueError("expected benchmark grid contains duplicate result keys")

    methodology_policy = {
        "warmups": warmups,
        "repeats": repeats,
        "timeout_seconds_per_operation": timeout,
        "configured_threads": threads_values,
        "codec_thread_keys": [
            [codec.name, codec.threads] for codec in codecs
        ],
        "collect_mathzip_inspection": collect_inspection,
        "mathzip_metrics_mode": mathzip_metrics_mode,
        "mathzip_metrics_in_compression_timing": (
            mathzip_metrics_mode == "inline"
        ),
        "record_absolute_paths": not redact_paths,
        "allow_short_run": allow_short_run,
        "selected_codecs": list(selected_codecs) if selected_codecs else None,
        "require_publication_evidence": require_publication_evidence,
    }
    stored_system = (
        _sanitize_structure(system_before, repository_root)
        if redact_paths
        else system_before
    )
    if redact_paths:
        stored_system["hostname"] = "redacted"
    stored_config_snapshot = (
        _sanitize_structure(config, repository_root) if redact_paths else config
    )
    current_identity = {
        "schema_version": RUN_IDENTITY_SCHEMA_VERSION,
        "config_sha256": sha256_file(config_path),
        "config_snapshot": stored_config_snapshot,
        "source": _source_resume_identity(source_before),
        "codecs": _codec_resume_identity(
            codecs, executable_hashes_before, mathzip_build_infos_before
        ),
        "expected_grid_sha256": expected_grid["sha256"],
        "host": _host_resume_identity(system_before),
        "methodology_policy": methodology_policy,
        "initial_source_metadata": source_before,
        "initial_system_metadata": stored_system,
        "codec_definitions": [
            _codec_definition(
                codec,
                repository_root,
                redact_paths,
                executable_hashes_before,
                mathzip_build_infos_before,
                mathzip_metrics_mode,
            )
            for codec in codecs
        ],
    }
    if require_publication_evidence:
        if not _resume_source_is_clean(current_identity["source"]):
            raise ValueError(
                "strict publication run requires a clean source revision/tree "
                "before the first benchmark row"
            )
        source_revision = source_before.get("source_revision")
        for codec_identity in current_identity["codecs"]:
            if (
                codec_identity.get("family") != "mathzip"
                or not codec_identity.get("available")
            ):
                continue
            build_info = codec_identity.get("build_info")
            if (
                not source_revision
                or not isinstance(build_info, Mapping)
                or build_info.get("source_revision") != source_revision
                or build_info.get("source_dirty") is not False
            ):
                raise ValueError(
                    "strict publication run requires the MathZip binary to "
                    "match the clean source revision before the first row"
                )

    configured_output = Path(
        str(config.get("output_dir", f"benchmarks/results/{profile}"))
    )
    configured_output_root = output_override or (
        configured_output
        if configured_output.is_absolute()
        else repository_root / configured_output
    )
    if resume_from is not None:
        if output_override is not None:
            raise ValueError("--output-dir cannot be combined with --resume")
        checkpoint_path = _checkpoint_path(resume_from)
        run_directory = checkpoint_path.parent
        output_root = run_directory.parent
        checkpoint = _read_json_object(checkpoint_path, "resume checkpoint")
        if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("resume checkpoint has an unsupported schema version")
        if checkpoint.get("state") not in ("running", "finalizing", "complete"):
            raise ValueError("resume checkpoint has an invalid state")
        if checkpoint.get("state") == "complete":
            raise ValueError("resume checkpoint is already complete")
        if (
            checkpoint.get("run_identity_file") != "run-identity.json"
            or checkpoint.get("expected_grid_file") != "expected-grid.json"
            or checkpoint.get("checkpoint_rows_directory") != "checkpoint-rows"
            or checkpoint.get("expected_result_count") != len(expected_keys)
        ):
            raise ValueError("resume checkpoint layout/grid count is inconsistent")
        run_id = checkpoint.get("run_id")
        if not isinstance(run_id, str) or run_id != run_directory.name:
            raise ValueError("resume checkpoint run_id does not match its directory")
        if checkpoint.get("profile") != profile:
            raise ValueError("resume rejected: profile differs")

        stored_grid = _read_json_object(
            run_directory / "expected-grid.json", "expected grid"
        )
        stored_grid_body = {
            key: value for key, value in stored_grid.items() if key != "sha256"
        }
        if (
            stored_grid.get("schema_version") != EXPECTED_GRID_SCHEMA_VERSION
            or stored_grid.get("sha256") != _json_sha256(stored_grid_body)
            or checkpoint.get("expected_grid_sha256")
            != stored_grid.get("sha256")
        ):
            raise ValueError("resume checkpoint expected-grid integrity check failed")
        stored_identity_path = run_directory / "run-identity.json"
        stored_identity = _read_json_object(
            stored_identity_path, "run identity"
        )
        if (
            stored_identity.get("schema_version")
            != RUN_IDENTITY_SCHEMA_VERSION
            or checkpoint.get("run_identity_sha256")
            != sha256_file(stored_identity_path)
        ):
            raise ValueError("resume checkpoint run-identity integrity check failed")
        if not _resume_source_is_clean(stored_identity.get("source", {})):
            raise ValueError(
                "resume rejected: checkpoint source is not a clean revision/tree"
            )
        if not _resume_source_is_clean(current_identity["source"]):
            raise ValueError(
                "resume rejected: current source is not a clean revision/tree"
            )
        mismatch = _resume_mismatch(stored_identity, current_identity)
        if mismatch is not None:
            raise ValueError(f"resume rejected: {mismatch} differs")
        if stored_grid != expected_grid:
            raise ValueError("resume rejected: input manifest/grid evidence differs")
        rows, interval_progress = _load_checkpoint_rows(
            run_directory, checkpoint, expected_keys
        )
        for row in rows:
            if row.get("run_id") != run_id or row.get("profile") != profile:
                raise ValueError(
                    "resume checkpoint row run_id/profile does not match the run"
                )
        identity = stored_identity
        started_at_text = checkpoint.get("started_at_utc")
        if not isinstance(started_at_text, str):
            raise ValueError("resume checkpoint has no valid original start time")
        try:
            timestamp = dt.datetime.fromisoformat(started_at_text)
        except ValueError as exc:
            raise ValueError(
                "resume checkpoint has an invalid original start time"
            ) from exc
        if timestamp.tzinfo is None:
            raise ValueError("resume checkpoint original start time lacks timezone")
        intervals = checkpoint.get("execution_intervals")
        if not isinstance(intervals, list) or not intervals:
            raise ValueError("resume checkpoint has no execution intervals")
        for index, interval in enumerate(intervals):
            active = (
                interval.get("active_seconds")
                if isinstance(interval, Mapping)
                else None
            )
            if (
                not isinstance(interval, dict)
                or interval.get("index") != index
                or interval.get("resumed") is not (index > 0)
                or not isinstance(active, (int, float))
                or isinstance(active, bool)
                or not 0.0 <= float(active) < float("inf")
            ):
                raise ValueError(
                    "resume checkpoint contains an invalid execution interval"
                )
        if checkpoint.get("resume_count") != len(intervals) - 1:
            raise ValueError("resume checkpoint resume_count is inconsistent")
        for index, progress in interval_progress.items():
            if index >= len(intervals) or not isinstance(intervals[index], dict):
                raise ValueError(
                    "checkpoint row references an invalid execution interval"
                )
            active_seconds, completed_at_text = progress
            if active_seconds > float(intervals[index]["active_seconds"]):
                intervals[index]["active_seconds"] = active_seconds
                intervals[index]["checkpointed_through_utc"] = completed_at_text
        previous = intervals[-1]
        if not isinstance(previous, dict):
            raise ValueError("resume checkpoint execution interval is invalid")
        if previous.get("completed_at_utc") is None:
            previous["interrupted"] = True
            previous["ended_at_utc"] = previous.get(
                "checkpointed_through_utc"
            )
        intervals.append(
            {
                "index": len(intervals),
                "resumed": True,
                "started_at_utc": invocation_started.isoformat(),
                "checkpointed_through_utc": invocation_started.isoformat(),
                "active_seconds": 0.0,
                "completed_at_utc": None,
                "interrupted": False,
            }
        )
        checkpoint["completed_result_count"] = len(rows)
        checkpoint["state"] = "running"
        checkpoint["updated_at_utc"] = invocation_started.isoformat()
        checkpoint["resume_count"] = len(intervals) - 1
        checkpoint["active_elapsed_seconds"] = sum(
            float(interval.get("active_seconds", 0.0))
            for interval in intervals
            if isinstance(interval, Mapping)
        )
        checkpoint["execution_intervals"] = intervals
        # Persist the new interval before any row can reference its index. If a
        # crash lands after a row envelope but before the following progress
        # update, the next resume can still validate and recover that row.
        _atomic_checkpoint_json(checkpoint_path, checkpoint)
    else:
        timestamp = invocation_started
        config_sha256 = current_identity["config_sha256"]
        base_run_id = (
            timestamp.strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + str(config_sha256)[:8]
        )
        run_id = base_run_id
        output_root = configured_output_root.resolve()
        run_directory = output_root / run_id
        suffix = 1
        while run_directory.exists():
            run_id = f"{base_run_id}-{suffix}"
            run_directory = output_root / run_id
            suffix += 1
        run_directory.mkdir(parents=True)
        (run_directory / "checkpoint-rows").mkdir()
        identity = current_identity
        _atomic_checkpoint_json(
            run_directory / "expected-grid.json", expected_grid
        )
        identity_path = run_directory / "run-identity.json"
        _atomic_checkpoint_json(identity_path, identity)
        intervals = [
            {
                "index": 0,
                "resumed": False,
                "started_at_utc": timestamp.isoformat(),
                "checkpointed_through_utc": timestamp.isoformat(),
                "active_seconds": 0.0,
                "completed_at_utc": None,
                "interrupted": False,
            }
        ]
        checkpoint = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "state": "running",
            "run_id": run_id,
            "profile": profile,
            "started_at_utc": timestamp.isoformat(),
            "updated_at_utc": timestamp.isoformat(),
            "completed_at_utc": None,
            "run_identity_file": "run-identity.json",
            "run_identity_sha256": sha256_file(identity_path),
            "expected_grid_file": "expected-grid.json",
            "expected_grid_sha256": expected_grid["sha256"],
            "checkpoint_rows_directory": "checkpoint-rows",
            "expected_result_count": len(expected_keys),
            "completed_result_count": 0,
            "resume_count": 0,
            "active_elapsed_seconds": 0.0,
            "execution_intervals": intervals,
        }
        rows = []
        _atomic_checkpoint_json(run_directory / "checkpoint.json", checkpoint)

    checkpoint_path = run_directory / "checkpoint.json"
    row_directory = run_directory / "checkpoint-rows"
    rows_by_index: dict[int, dict[str, Any]] = {}
    for row in rows:
        index = expected_key_indexes[_result_key(row)]
        if index in rows_by_index:
            raise ValueError(f"resume checkpoint duplicates expected grid index {index}")
        rows_by_index[index] = row
    current_interval = intervals[-1]

    def update_checkpoint(*, completed: bool = False) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        current_interval["checkpointed_through_utc"] = now.isoformat()
        current_interval["active_seconds"] = max(
            float(current_interval.get("active_seconds", 0.0)),
            time.perf_counter() - invocation_clock,
        )
        if completed:
            current_interval["completed_at_utc"] = now.isoformat()
            current_interval["ended_at_utc"] = now.isoformat()
        checkpoint["updated_at_utc"] = now.isoformat()
        checkpoint["completed_result_count"] = len(rows_by_index)
        checkpoint["resume_count"] = len(intervals) - 1
        checkpoint["active_elapsed_seconds"] = sum(
            float(interval.get("active_seconds", 0.0))
            for interval in intervals
            if isinstance(interval, Mapping)
        )
        checkpoint["execution_intervals"] = intervals
        _atomic_checkpoint_json(checkpoint_path, checkpoint)

    def commit_row(row: dict[str, Any]) -> None:
        key = _result_key(row)
        index = expected_key_indexes.get(key)
        if index is None:
            raise ValueError(f"completed row is outside expected grid: {key}")
        if index in rows_by_index:
            raise ValueError(f"completed row duplicates expected grid key: {key}")
        now = dt.datetime.now(dt.timezone.utc)
        interval_active = time.perf_counter() - invocation_clock
        row_path = row_directory / f"{index:08d}.json"
        if row_path.exists():
            raise ValueError(f"checkpoint row already exists unexpectedly: {row_path}")
        _atomic_checkpoint_json(
            row_path,
            {
                "schema_version": CHECKPOINT_ROW_SCHEMA_VERSION,
                "expected_grid_index": index,
                "key": list(key),
                "execution_interval_index": current_interval["index"],
                "interval_active_seconds": interval_active,
                "completed_at_utc": now.isoformat(),
                "row": row,
            },
        )
        rows_by_index[index] = row
        update_checkpoint()

    for missing in missing_inputs:
        for codec in codecs:
            key = (
                str(missing["corpus"]),
                str(missing["path"]),
                codec.name,
                codec.threads,
            )
            if expected_key_indexes[key] in rows_by_index:
                continue
            commit_row(
                _empty_row(
                    run_id=run_id,
                    profile=profile,
                    corpus=missing["corpus"],
                    input_path=missing["path"],
                    codec=codec,
                    requested_repeats=repeats,
                    status="missing_input",
                    error=missing["reason"],
                    input_license=missing.get("input_license"),
                    input_provenance=missing.get("input_provenance"),
                )
            )

    for case in cases:
        pending_codecs = [
            codec
            for codec in codecs
            if expected_key_indexes[
                (case.corpus, case.recorded_path, codec.name, codec.threads)
            ]
            not in rows_by_index
        ]
        if not pending_codecs:
            continue
        state = input_state[(case.corpus, case.recorded_path)]
        original_sha256 = str(state["actual_input_sha256"])
        original_bytes = int(state["actual_input_size"])
        entropy = shannon_entropy(case.path)
        input_evidence = _case_input_evidence(case, original_sha256, original_bytes)
        if case.input_manifest is not None and not input_evidence["input_manifest_verified"]:
            for codec in pending_codecs:
                row = _empty_row(
                    run_id=run_id,
                    profile=profile,
                    corpus=case.corpus,
                    input_path=case.recorded_path,
                    codec=codec,
                    requested_repeats=repeats,
                    status="input_integrity_failed",
                    error=(
                        "input size or SHA-256 differs from its recorded manifest"
                    ),
                    tags=case.tags,
                    input_license=case.input_license,
                    input_provenance=case.input_provenance,
                )
                row.update(
                    {
                        "input_sha256": original_sha256,
                        "input_entropy_bits_per_byte": entropy,
                        "original_bytes": original_bytes,
                        **input_evidence,
                    }
                )
                commit_row(row)
            continue
        for codec in pending_codecs:
            if not codec.available:
                row = _empty_row(
                    run_id=run_id,
                    profile=profile,
                    corpus=case.corpus,
                    input_path=case.recorded_path,
                    codec=codec,
                    requested_repeats=repeats,
                    status="unavailable_codec",
                    error=codec.available_reason or "codec unavailable",
                    tags=case.tags,
                    input_license=case.input_license,
                    input_provenance=case.input_provenance,
                )
                row.update(
                    {
                        "input_sha256": original_sha256,
                        "input_entropy_bits_per_byte": entropy,
                        "original_bytes": original_bytes,
                        **input_evidence,
                    }
                )
                commit_row(row)
                continue

            warmup_failure: Trial | None = None
            for warmup_index in range(warmups):
                warmup = execute_trial(
                    codec,
                    case.path,
                    original_sha256,
                    -(warmup_index + 1),
                    timeout,
                    collect_inspection=False,
                    mathzip_metrics_mode=mathzip_metrics_mode,
                )
                if redact_paths:
                    _sanitize_trial_paths(warmup, repository_root)
                if warmup.status != "ok":
                    warmup_failure = warmup
                    break
            if warmup_failure:
                row = _empty_row(
                    run_id=run_id,
                    profile=profile,
                    corpus=case.corpus,
                    input_path=case.recorded_path,
                    codec=codec,
                    requested_repeats=repeats,
                    status="warmup_failed",
                    error=f"{warmup_failure.status}: {warmup_failure.error}",
                    tags=case.tags,
                    input_license=case.input_license,
                    input_provenance=case.input_provenance,
                )
                row.update(
                    {
                        "input_sha256": original_sha256,
                        "input_entropy_bits_per_byte": entropy,
                        "original_bytes": original_bytes,
                        "warmup": asdict(warmup_failure),
                        **input_evidence,
                    }
                )
                commit_row(row)
                continue

            trials = [
                execute_trial(
                    codec,
                    case.path,
                    original_sha256,
                    index,
                    timeout,
                    collect_inspection=collect_inspection,
                    mathzip_metrics_mode=mathzip_metrics_mode,
                )
                for index in range(repeats)
            ]
            if redact_paths:
                for trial in trials:
                    _sanitize_trial_paths(trial, repository_root)
            commit_row(
                aggregate_trials(
                    run_id=run_id,
                    profile=profile,
                    case=case,
                    input_sha256=original_sha256,
                    input_entropy=entropy,
                    original_bytes=original_bytes,
                    codec=codec,
                    requested_repeats=repeats,
                    trials=trials,
                )
            )

    if set(rows_by_index) != set(range(len(expected_keys))):
        missing_indexes = sorted(set(range(len(expected_keys))) - set(rows_by_index))
        extra_indexes = sorted(set(rows_by_index) - set(range(len(expected_keys))))
        raise ValueError(
            "benchmark grid is incomplete before publication: "
            f"{len(missing_indexes)} missing, {len(extra_indexes)} extra"
        )
    rows = [rows_by_index[index] for index in range(len(expected_keys))]
    checkpoint["state"] = "finalizing"
    update_checkpoint(completed=True)

    executable_hashes_after = _executable_hashes(codecs)
    mathzip_build_infos_after = _mathzip_build_infos(codecs)
    codec_identity_before = identity["codecs"]
    codec_identity_after = _codec_resume_identity(
        codecs, executable_hashes_after, mathzip_build_infos_after
    )
    binary_fields = (
        "codec",
        "threads",
        "available",
        "executable_path_sha256",
        "executable_sha256",
    )
    binaries_stable = [
        {field: entry.get(field) for field in binary_fields}
        for entry in codec_identity_before
    ] == [
        {field: entry.get(field) for field in binary_fields}
        for entry in codec_identity_after
    ]
    build_infos_stable = [
        entry.get("build_info") for entry in codec_identity_before
    ] == [entry.get("build_info") for entry in codec_identity_after]
    source_metadata_after = collect_source_metadata(repository_root)
    source_metadata = dict(identity["initial_source_metadata"])
    source_stable = all(
        source_metadata.get(field) == source_metadata_after.get(field)
        for field in (
            "source_revision",
            "source_dirty",
            "source_tree_sha256",
            "source_file_count",
        )
    )
    source_metadata.update(
        {
            "source_stable_during_run": source_stable,
            "source_revision_after": source_metadata_after.get("source_revision"),
            "source_dirty_after": source_metadata_after.get("source_dirty"),
            "source_tree_sha256_after": source_metadata_after.get(
                "source_tree_sha256"
            ),
            "source_file_count_after": source_metadata_after.get(
                "source_file_count"
            ),
        }
    )
    config_sha256_after = sha256_file(config_path)
    config_stable = identity["config_sha256"] == config_sha256_after
    cases_after, missing_inputs_after = discover_inputs(config, repository_root)
    if not redact_paths:
        cases_after = [
            InputCase(
                corpus=case.corpus,
                path=case.path,
                recorded_path=str(case.path),
                tags=case.tags,
                input_license=case.input_license,
                input_provenance=case.input_provenance,
                expected_sha256=case.expected_sha256,
                expected_size=case.expected_size,
                input_manifest=case.input_manifest,
                input_manifest_sha256=case.input_manifest_sha256,
            )
            for case in cases_after
        ]
    expected_grid_after, _ = _expected_grid(
        cases_after, missing_inputs_after, codecs
    )
    input_grid_stable = expected_grid_after == expected_grid
    system_after = collect_system_metadata(
        compressor_tool_names(), repository_root
    )
    host_identity_after = _host_resume_identity(system_after)
    host_identity_stable = host_identity_after == identity["host"]
    timing_protocol_compliant = warmups >= 1 and repeats >= 3
    source_revision = source_metadata.get("source_revision")
    source_evidence_complete = (
        bool(source_revision) and source_metadata.get("source_dirty") is False
    ) or (
        source_revision is None
        and bool(source_metadata.get("source_tree_sha256"))
        and isinstance(source_metadata.get("source_tree_manifest"), list)
        and bool(source_metadata["source_tree_manifest"])
    )
    source_evidence_complete = source_evidence_complete and source_stable
    system_metadata = dict(identity["initial_system_metadata"])
    required_environment_fields = {
        "cpu_model": system_metadata.get("cpu_model"),
        "logical_cores": system_metadata.get("logical_cores"),
        "physical_cores": system_metadata.get("physical_cores"),
        "ram_bytes": system_metadata.get("ram_bytes"),
        "os": system_metadata.get("os"),
        "storage.filesystem_type": system_metadata.get("storage", {}).get(
            "filesystem_type"
        ),
        "storage.free_bytes": system_metadata.get("storage", {}).get(
            "free_bytes"
        ),
    }
    environment_evidence_complete = all(
        value is not None for value in required_environment_fields.values()
    )
    executable_evidence_complete = all(
        not entry.get("available")
        or entry.get("family") == "raw"
        or bool(entry.get("executable_sha256"))
        for entry in codec_identity_before
    )
    has_available_mathzip = any(
        entry.get("family") == "mathzip" and entry.get("available")
        for entry in codec_identity_before
    )
    mathzip_build_evidence_complete = (
        not has_available_mathzip or bool(source_revision)
    ) and build_infos_stable and all(
        entry.get("family") != "mathzip"
        or not entry.get("available")
        or (
            isinstance(entry.get("build_info"), Mapping)
            and entry["build_info"].get("source_revision") == source_revision
            and entry["build_info"].get("source_dirty") is False
        )
        for entry in codec_identity_before
    )
    input_evidence_complete = bool(rows) and all(
        bool(row.get("input_license")) and bool(row.get("input_provenance"))
        for row in rows
    )
    input_integrity_complete = bool(rows) and all(
        row.get("input_manifest_verified") is True for row in rows
    )
    publication_evidence_gaps: list[str] = []
    if not timing_protocol_compliant:
        publication_evidence_gaps.append(
            "timing protocol requires at least one warm-up and three repeats"
        )
    if not binaries_stable:
        publication_evidence_gaps.append(
            "one or more codec executable hashes changed during the run"
        )
    if not executable_evidence_complete:
        publication_evidence_gaps.append(
            "one or more available codec executables lacks a before/after SHA-256"
        )
    if not mathzip_build_evidence_complete:
        publication_evidence_gaps.append(
            "MathZip binary build revision/dirty state does not match the benchmark source"
        )
    if not source_evidence_complete:
        publication_evidence_gaps.append(
            "source requires a stable clean Git revision or a stable embedded "
            "deterministic tree manifest"
        )
    if not config_stable:
        publication_evidence_gaps.append(
            "benchmark config bytes changed during the run"
        )
    if not input_evidence_complete:
        publication_evidence_gaps.append(
            "every result row requires non-empty input license/terms and provenance"
        )
    if not input_integrity_complete:
        publication_evidence_gaps.append(
            "every benchmark input must match size and SHA-256 evidence in a manifest"
        )
    if not input_grid_stable:
        publication_evidence_gaps.append(
            "input discovery, manifest evidence, or live input bytes changed during the run"
        )
    if not host_identity_stable:
        publication_evidence_gaps.append(
            "host affinity/governor/environment/container/filesystem identity "
            "changed during the run"
        )
    if selected_codecs:
        publication_evidence_gaps.append(
            "codec-filtered runs are diagnostic and cannot be publication evidence"
        )
    for field, value in required_environment_fields.items():
        if value is None:
            publication_evidence_gaps.append(
                f"required environment metadata unavailable: {field}"
            )
    publication_evidence_complete = not publication_evidence_gaps
    scientifically_compliant = publication_evidence_complete
    active_elapsed_seconds = float(checkpoint["active_elapsed_seconds"])
    completed_at = dt.datetime.now(dt.timezone.utc)
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "profile": profile,
        "status": (
            "complete"
            if rows and all(row["status"] == "ok" for row in rows)
            else "complete_with_failures"
        ),
        "started_at_utc": timestamp.isoformat(),
        "completed_at_utc": completed_at.isoformat(),
        "elapsed_seconds": (completed_at - timestamp).total_seconds(),
        "resume": {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "resume_count": len(intervals) - 1,
            "active_elapsed_seconds": active_elapsed_seconds,
            "execution_intervals": intervals,
            "expected_result_count": len(expected_keys),
            "checkpoint_rows_durable": len(rows),
        },
        "timing_protocol_compliant": timing_protocol_compliant,
        "publication_evidence_complete": publication_evidence_complete,
        "publication_evidence_gaps": publication_evidence_gaps,
        "scientifically_compliant_run": scientifically_compliant,
        "source": source_metadata,
        "methodology": {
            "warmups": warmups,
            "repeats": repeats,
            "collect_mathzip_inspection": collect_inspection,
            "mathzip_metrics_mode": mathzip_metrics_mode,
            "mathzip_metrics_in_compression_timing": (
                mathzip_metrics_mode == "inline"
            ),
            "aggregate": "median of successful measured repetitions",
            "timeout_seconds_per_operation": timeout,
            "roundtrip_verification": "SHA-256 after every warm-up and repetition",
            "archive_size_source": "actual file output",
            "download_and_build_time_included": False,
            "throughput_unit": "decimal MB/s (1,000,000 bytes/s)",
            "rss_method": "10 ms /proc process-tree sampling when available",
            "binary_hashes_checked_before_and_after": True,
            "binaries_stable_during_run": binaries_stable,
            "mathzip_build_info_stable_during_run": build_infos_stable,
            "mathzip_build_matches_source": mathzip_build_evidence_complete,
            "available_executable_hashes_complete": executable_evidence_complete,
            "source_metadata_checked_before_and_after": True,
            "source_stable_during_run": source_stable,
            "config_checked_before_and_after": True,
            "config_stable_during_run": config_stable,
            "input_grid_checked_before_and_after": True,
            "input_grid_stable_during_run": input_grid_stable,
            "expected_grid_sha256_before": expected_grid["sha256"],
            "expected_grid_sha256_after": expected_grid_after["sha256"],
            "host_identity_checked_before_and_after": True,
            "host_identity_stable_during_run": host_identity_stable,
            "host_identity_sha256_before": _json_sha256(identity["host"]),
            "host_identity_sha256_after": _json_sha256(host_identity_after),
            "checkpoint_after_every_completed_row": True,
            "checkpoint_writes_outside_timed_regions": True,
            "resume_identity_fail_closed": True,
            "input_rights_and_provenance_complete": input_evidence_complete,
            "input_manifest_integrity_complete": input_integrity_complete,
            "codec_filter": list(selected_codecs) if selected_codecs else None,
            "publication_source_requirement": (
                "stable clean Git revision, or stable exact tree manifest/hash "
                "when no HEAD exists"
            ),
            "compliance_scope": (
                "timing_protocol_compliant covers only warm-up/repeat counts; "
                "scientifically_compliant_run additionally requires stable binaries, "
                "source evidence, and core environment metadata"
            ),
        },
        "config": {
            "path": _record_path(config_path, repository_root)
            if redact_paths
            else str(config_path),
            "sha256": identity["config_sha256"],
            "sha256_after": config_sha256_after,
            "stable_during_run": config_stable,
            "snapshot": identity["config_snapshot"],
        },
        "system": system_metadata,
        "codec_definitions": identity["codec_definitions"],
        "expected_grid": expected_grid,
        "input_count": len(cases),
        "result_count": len(rows),
        "failure_count": sum(row["status"] != "ok" for row in rows),
        "results": rows,
    }
    if redact_paths:
        result["system"] = _sanitize_structure(result["system"], repository_root)
        result["methodology"]["recorded_paths"] = (
            "repository-relative; executable paths use <repo>/<external>/<tmp> placeholders"
        )
    else:
        result["methodology"]["recorded_paths"] = "absolute paths explicitly enabled"
    from .verification import validate_csv, validate_result_document

    structural_errors, _ = validate_result_document(result, strict=False)
    if structural_errors:
        raise ValueError(
            "refusing to publish structurally invalid benchmark results: "
            + "; ".join(structural_errors[:5])
        )
    if require_publication_evidence and not publication_evidence_complete:
        raise ValueError(
            "strict publication evidence became incomplete during the run: "
            + "; ".join(publication_evidence_gaps[:5])
        )
    results_json = run_directory / "results.json"
    results_csv = run_directory / "results.csv"
    atomic_write_json(results_json, result)
    write_csv(results_csv, rows)
    _fsync_directory(run_directory)
    csv_errors, _ = validate_csv(results_csv, rows)
    if csv_errors:
        raise ValueError(
            "refusing to publish benchmark CSV that differs from JSON: "
            + "; ".join(csv_errors[:5])
        )
    if (
        result["result_count"] != expected_grid["expected_result_count"]
        or {_result_key(row) for row in rows} != set(expected_keys)
    ):
        raise ValueError("refusing to publish latest pointer for an incomplete grid")
    latest = {
        "schema_version": "mathzip-benchmark-latest-v1",
        "run_id": run_id,
        "results_json": str(results_json.relative_to(output_root.resolve())),
        "results_csv": str(results_csv.relative_to(output_root.resolve())),
    }
    atomic_write_text(
        output_root.resolve() / "latest.txt",
        str(run_directory.relative_to(output_root.resolve())) + "\n",
    )
    atomic_write_json(output_root.resolve() / "latest.json", latest)
    _fsync_directory(output_root.resolve())
    checkpoint["state"] = "complete"
    checkpoint["completed_at_utc"] = completed_at.isoformat()
    checkpoint["results_json_sha256"] = sha256_file(results_json)
    checkpoint["results_csv_sha256"] = sha256_file(results_csv)
    _atomic_checkpoint_json(checkpoint_path, checkpoint)
    return result, run_directory


def run_benchmarks(
    config: dict[str, Any],
    *,
    config_path: Path,
    repository_root: Path,
    mathzip_binary: str,
    output_override: Path | None = None,
    allow_short_run: bool = False,
    selected_codecs: Sequence[str] | None = None,
    resume_from: Path | None = None,
    require_publication_evidence: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Run one invocation while exclusively owning its publication root."""

    if resume_from is not None:
        if output_override is not None:
            raise ValueError("--output-dir cannot be combined with --resume")
        lock_root = _checkpoint_path(resume_from).parent.parent
    else:
        profile = str(config.get("profile", "custom"))
        configured_output = Path(
            str(config.get("output_dir", f"benchmarks/results/{profile}"))
        )
        lock_root = output_override or (
            configured_output
            if configured_output.is_absolute()
            else repository_root / configured_output
        )
        lock_root = lock_root.resolve()
    with _exclusive_benchmark_lock(lock_root):
        return _run_benchmarks_unlocked(
            config,
            config_path=config_path,
            repository_root=repository_root,
            mathzip_binary=mathzip_binary,
            output_override=output_override,
            allow_short_run=allow_short_run,
            selected_codecs=selected_codecs,
            resume_from=resume_from,
            require_publication_evidence=require_publication_evidence,
        )
