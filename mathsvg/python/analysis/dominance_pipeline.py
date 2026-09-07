#!/usr/bin/env python3
"""Build conservative dominance artifacts from raw benchmark evidence.

The raw JSONL is always required because the summary CSV deliberately omits
input paths, hashes, failure messages, and the native/external marker.  An
existing summary CSV may be supplied, but it is checked against a summary
recomputed from the raw evidence before it is trusted.
"""

from __future__ import annotations

import argparse
import csv
import math
import pathlib
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Sequence

from mathsvg.python.analysis.dominance import (
    CERTIFICATE_FIELDS,
    Measurement,
    compare,
)
from mathsvg.python.analysis.statistics import summarize
from mathsvg.python.benchmarks.schema import (
    TrialError,
    TrialRecord,
    read_jsonl,
)
from mathsvg.python.benchmarks.summarize import (
    GROUP_FIELDS,
    SUMMARY_FIELDS,
    SummaryError,
    build_summary_rows,
    write_csv,
)

ENVIRONMENT_FIELDS = (
    "experiment_id",
    "machine_id",
    "architecture",
    "source_commit",
    "dataset_manifest_sha256",
    "randomization_seed",
    "measurement_method",
    "measurement_tool_sha256",
    "split",
    "threads",
)
CORE_METRICS = (
    ("size", "archive_bytes"),
    ("compression", "compression_wall_ns"),
    ("decompression", "decompression_wall_ns"),
    ("memory", "peak_rss_bytes"),
)
EXTRA_FIELDS = (
    "schema_version",
    "row_scope",
    "experiment_id",
    "machine_id",
    "architecture",
    "source_commit",
    "dataset_manifest_sha256",
    "randomization_seed",
    "measurement_method",
    "measurement_tool_sha256",
    "split",
    "dataset_id",
    "input_sha256",
    "threads",
    "mathsvg_codec",
    "mathsvg_codec_config",
    "mathsvg_config_sha256",
    "mathsvg_executable_sha256",
    "mathsvg_status",
    "baseline_config_sha256",
    "baseline_executable_sha256",
    "baseline_status",
    "size_confidence_status",
    "compression_confidence_status",
    "decompression_confidence_status",
    "memory_confidence_status",
    "comparison_status",
    "certificate_pass",
    "failure_reason",
)
OUTPUT_FIELDS = (*CERTIFICATE_FIELDS, *EXTRA_FIELDS)
OUTPUT_NAMES = (
    "per-file.csv",
    "per-corpus.csv",
    "pareto-envelope.csv",
    "failures.csv",
)
_INTEGER_ENVIRONMENT_FIELDS = frozenset({"randomization_seed", "threads"})


class DominancePipelineError(ValueError):
    """Evidence cannot safely be converted into a dominance certificate."""


@dataclass(frozen=True, order=True, slots=True)
class LogicalConfig:
    native: bool
    codec: str
    config: str
    profile: str


@dataclass(frozen=True, slots=True)
class DominanceArtifacts:
    per_file: tuple[dict[str, object], ...]
    per_corpus: tuple[dict[str, object], ...]
    pareto_envelope: tuple[dict[str, object], ...]
    failures: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _BuiltRow:
    values: dict[str, object]
    environment: tuple[object, ...]
    mathsvg: LogicalConfig
    baseline: LogicalConfig
    dataset_id: str
    row_scope: str


def _normalise_environment_value(field: str, value: object) -> object:
    if field in _INTEGER_ENVIRONMENT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise DominancePipelineError(
                f"{field} must be an integer, got {value!r}"
            ) from exc
    return str(value)


def _environment_from_record(record: TrialRecord) -> tuple[object, ...]:
    return tuple(getattr(record, field) for field in ENVIRONMENT_FIELDS)


def _environment_from_row(row: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(
        _normalise_environment_value(field, row[field])
        for field in ENVIRONMENT_FIELDS
    )


def _environment_values(
    environment: tuple[object, ...],
) -> dict[str, object]:
    return dict(zip(ENVIRONMENT_FIELDS, environment, strict=True))


def _logical_from_record(record: TrialRecord) -> LogicalConfig:
    return LogicalConfig(
        native=record.native_mathsvg,
        codec=record.codec,
        config=record.codec_config,
        profile=record.profile,
    )


def _logical_from_row(row: Mapping[str, object]) -> LogicalConfig:
    profile = str(row["profile"])
    return LogicalConfig(
        native=bool(profile),
        codec=str(row["codec"]),
        config=str(row["codec_config"]),
        profile=profile,
    )


def _summary_key(
    row: Mapping[str, object],
) -> tuple[object, ...]:
    return (
        *_environment_from_row(row),
        _logical_from_row(row),
        str(row["dataset_id"]),
        str(row["row_scope"]),
    )


def _as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _as_int(value: object) -> int | None:
    parsed = _as_float(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    normalised = str(value).strip().lower()
    if normalised in {"true", "1"}:
        return True
    if normalised in {"false", "0", ""}:
        return False
    raise DominancePipelineError(f"invalid boolean value {value!r}")


def _read_summary_csv(path: pathlib.Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != SUMMARY_FIELDS:
            raise DominancePipelineError(
                f"{path}: summary schema does not match SUMMARY_FIELDS"
            )
        return [dict(row) for row in reader]


def _same_summary_value(left: object, right: object) -> bool:
    if left in ("", None) or right in ("", None):
        return left in ("", None) and right in ("", None)
    left_number = _as_float(left)
    right_number = _as_float(right)
    if left_number is not None and right_number is not None:
        return math.isclose(
            left_number,
            right_number,
            rel_tol=1e-12,
            abs_tol=1e-9,
        )
    return str(left).lower() == str(right).lower()


def _validate_supplied_summary(
    supplied: Sequence[Mapping[str, object]],
    recomputed: Sequence[Mapping[str, object]],
) -> None:
    supplied_index: dict[tuple[object, ...], Mapping[str, object]] = {}
    recomputed_index: dict[tuple[object, ...], Mapping[str, object]] = {}
    for label, rows, index in (
        ("supplied", supplied, supplied_index),
        ("recomputed", recomputed, recomputed_index),
    ):
        for row in rows:
            key = _summary_key(row)
            if key in index:
                raise DominancePipelineError(
                    f"{label} summary contains duplicate row {key!r}"
                )
            index[key] = row
    if supplied_index.keys() != recomputed_index.keys():
        missing = sorted(
            repr(key) for key in recomputed_index.keys() - supplied_index.keys()
        )
        extra = sorted(
            repr(key) for key in supplied_index.keys() - recomputed_index.keys()
        )
        raise DominancePipelineError(
            "supplied summary does not cover the raw evidence: "
            f"missing={missing}, extra={extra}"
        )
    checked_fields = (
        *GROUP_FIELDS,
        "dataset_id",
        "row_scope",
        "original_bytes",
        "scheduled_trials",
        "successful_trials",
        "failed_trials",
        "timeout_trials",
        "unavailable_trials",
        "measured_repetitions",
        "protocol_repetitions_ok",
        "roundtrip_all",
        "deterministic_archive_all",
        "status",
        "confidence_status",
        *(
            field
            for _, prefix in CORE_METRICS
            for field in (
                f"{prefix}_median",
                f"{prefix}_ci95_low",
                f"{prefix}_ci95_high",
            )
        ),
    )
    for key, expected in recomputed_index.items():
        actual = supplied_index[key]
        for field in checked_fields:
            if not _same_summary_value(actual[field], expected[field]):
                raise DominancePipelineError(
                    "supplied summary is stale or inconsistent with raw "
                    f"evidence at {key!r}, field {field!r}"
                )


def _override_corpus_peak_rss(
    rows: Sequence[dict[str, object]],
    records: Sequence[TrialRecord],
) -> None:
    """Use max-per-repetition RSS for a sequential corpus workload."""

    groups: dict[tuple[object, ...], list[TrialRecord]] = defaultdict(list)
    for record in records:
        if record.warmup:
            continue
        key = tuple(getattr(record, field) for field in GROUP_FIELDS)
        groups[key].append(record)
    for row in rows:
        if row["row_scope"] != "corpus":
            continue
        key = tuple(
            _normalise_environment_value(field, row[field])
            if field in _INTEGER_ENVIRONMENT_FIELDS
            else str(row[field])
            for field in GROUP_FIELDS
        )
        group = groups.get(key, ())
        dataset_ids = {record.dataset_id for record in group}
        by_repetition: dict[int, list[TrialRecord]] = defaultdict(list)
        for record in group:
            by_repetition[record.repetition].append(record)
        samples = [
            float(
                max(
                    int(record.peak_rss_bytes)
                    for record in repetition_records
                    if record.peak_rss_bytes is not None
                )
            )
            for repetition_records in by_repetition.values()
            if (
                len(repetition_records) == len(dataset_ids)
                and all(
                    record.status == "ok"
                    and record.peak_rss_bytes is not None
                    for record in repetition_records
                )
            )
        ]
        fields = (
            "count",
            "mean",
            "median",
            "standard_deviation",
            "ci95_low",
            "ci95_high",
        )
        if not samples:
            for field in fields:
                row[f"peak_rss_bytes_{field}"] = ""
            continue
        summary = asdict(summarize(samples))
        for field in fields:
            row[f"peak_rss_bytes_{field}"] = summary[field]


def _index_summary(
    rows: Sequence[Mapping[str, object]],
) -> dict[tuple[object, ...], list[Mapping[str, object]]]:
    index: dict[
        tuple[object, ...], list[Mapping[str, object]]
    ] = defaultdict(list)
    for row in rows:
        index[_summary_key(row)].append(row)
    return index


def _index_records(
    records: Sequence[TrialRecord],
) -> dict[tuple[object, ...], list[TrialRecord]]:
    index: dict[tuple[object, ...], list[TrialRecord]] = defaultdict(list)
    for record in records:
        if record.warmup:
            continue
        index[
            (
                *_environment_from_record(record),
                _logical_from_record(record),
                record.dataset_id,
            )
        ].append(record)
    return index


def _records_for(
    index: Mapping[tuple[object, ...], list[TrialRecord]],
    environment: tuple[object, ...],
    logical: LogicalConfig,
    dataset_id: str,
    row_scope: str,
) -> list[TrialRecord]:
    if row_scope == "file":
        return list(index.get((*environment, logical, dataset_id), ()))
    records: list[TrialRecord] = []
    prefix = (*environment, logical)
    for key, group in index.items():
        if key[: len(prefix)] == prefix:
            records.extend(group)
    return records


def _summary_row_for(
    index: Mapping[
        tuple[object, ...], list[Mapping[str, object]]
    ],
    environment: tuple[object, ...],
    logical: LogicalConfig,
    dataset_id: str,
    row_scope: str,
) -> tuple[Mapping[str, object] | None, bool]:
    rows = index.get(
        (*environment, logical, dataset_id, row_scope),
        (),
    )
    return (rows[0] if len(rows) == 1 else None, len(rows) > 1)


def _identity_map(
    records: Sequence[TrialRecord],
) -> dict[str, set[tuple[str, str, int]]]:
    identities: dict[str, set[tuple[str, str, int]]] = defaultdict(set)
    for record in records:
        identities[record.dataset_id].add(
            (
                record.input_path,
                record.input_sha256,
                record.original_bytes,
            )
        )
    return identities


def _identity(
    mathsvg_records: Sequence[TrialRecord],
    baseline_records: Sequence[TrialRecord],
    dataset_id: str,
    row_scope: str,
) -> tuple[str, str, int | None, bool, str]:
    mathsvg = _identity_map(mathsvg_records)
    baseline = _identity_map(baseline_records)
    present = mathsvg or baseline
    if row_scope == "file":
        candidates = mathsvg.get(dataset_id) or baseline.get(dataset_id) or set()
        path = next(iter(candidates))[0] if len(candidates) == 1 else dataset_id
        digest = next(iter(candidates))[1] if len(candidates) == 1 else ""
        original = next(iter(candidates))[2] if len(candidates) == 1 else None
    else:
        path = "__aggregate__"
        singleton_identities = [
            next(iter(values))
            for values in present.values()
            if len(values) == 1
        ]
        original = (
            sum(identity[2] for identity in singleton_identities)
            if len(singleton_identities) == len(present)
            else None
        )
        digest = ""
    if not mathsvg or not baseline:
        return path, digest, original, False, ""
    if mathsvg.keys() != baseline.keys():
        left_only = sorted(mathsvg.keys() - baseline.keys())
        right_only = sorted(baseline.keys() - mathsvg.keys())
        return (
            path,
            digest,
            original,
            False,
            "corpus coverage mismatch: "
            f"mathsvg_only={left_only}, baseline_only={right_only}",
        )
    for current_dataset in sorted(mathsvg):
        if len(mathsvg[current_dataset]) != 1:
            return (
                path,
                digest,
                original,
                False,
                f"MathSVG input identity is ambiguous for {current_dataset}",
            )
        if len(baseline[current_dataset]) != 1:
            return (
                path,
                digest,
                original,
                False,
                f"baseline input identity is ambiguous for {current_dataset}",
            )
        if mathsvg[current_dataset] != baseline[current_dataset]:
            return (
                path,
                digest,
                original,
                False,
                f"input identity mismatch for {current_dataset}",
            )
    identities = [next(iter(mathsvg[key])) for key in sorted(mathsvg)]
    if row_scope == "file":
        path, digest, original = identities[0]
    else:
        original = sum(identity[2] for identity in identities)
    return path, digest, original, True, ""


def _side_status(
    row: Mapping[str, object] | None,
    records: Sequence[TrialRecord],
) -> str:
    if not records and row is None:
        return "missing"
    if any(record.status == "unavailable" for record in records):
        return "unavailable"
    if row is not None:
        return str(row["status"])
    if any(record.status == "timeout" for record in records):
        return "timeout"
    if any(record.status == "failed" for record in records):
        return "failed"
    return "incomparable"


def _summary_ready(
    row: Mapping[str, object] | None,
    *,
    native: bool,
) -> bool:
    if row is None:
        return False
    return (
        str(row["status"]) == "complete"
        and str(row["confidence_status"]) == "confirmed"
        and _as_bool(row["protocol_repetitions_ok"])
        and _as_bool(row["roundtrip_all"])
        and (
            not native
            or _as_bool(row["deterministic_archive_all"])
        )
    )


def _native_point_available(
    row: Mapping[str, object] | None,
) -> bool:
    """A point may be plotted even when its CI protocol is incomplete."""

    return (
        row is not None
        and str(row["status"]) == "complete"
        and _as_bool(row["roundtrip_all"])
        and _as_bool(row["deterministic_archive_all"])
    )


def _pair_repetition_protocol_ok(
    mathsvg_row: Mapping[str, object],
    baseline_row: Mapping[str, object],
) -> bool:
    close = False
    for _, prefix in CORE_METRICS:
        mathsvg_value = _metric_value(mathsvg_row, prefix)
        baseline_value = _metric_value(baseline_row, prefix)
        if mathsvg_value is None or baseline_value is None:
            continue
        denominator = max(abs(mathsvg_value), abs(baseline_value))
        if (
            denominator > 0
            and abs(mathsvg_value - baseline_value) / denominator < 0.02
        ):
            close = True
            break
    minimum = 10 if close else 5
    mathsvg_repetitions = _as_int(mathsvg_row["measured_repetitions"])
    baseline_repetitions = _as_int(
        baseline_row["measured_repetitions"]
    )
    return (
        mathsvg_repetitions is not None
        and baseline_repetitions is not None
        and mathsvg_repetitions >= minimum
        and baseline_repetitions >= minimum
    )


def _direction(
    mathsvg_row: Mapping[str, object] | None,
    baseline_row: Mapping[str, object] | None,
    prefix: str,
) -> tuple[bool, str]:
    mathsvg_value = (
        _as_float(mathsvg_row.get(f"{prefix}_median", ""))
        if mathsvg_row is not None
        else None
    )
    baseline_value = (
        _as_float(baseline_row.get(f"{prefix}_median", ""))
        if baseline_row is not None
        else None
    )
    if mathsvg_value is None or baseline_value is None:
        return False, "not-measured"
    point_not_worse = mathsvg_value <= baseline_value
    if not (
        _summary_ready(mathsvg_row, native=True)
        and _summary_ready(baseline_row, native=False)
        and _pair_repetition_protocol_ok(
            mathsvg_row,
            baseline_row,
        )
    ):
        return point_not_worse, "inconclusive"
    mathsvg_low = _as_float(mathsvg_row[f"{prefix}_ci95_low"])
    mathsvg_high = _as_float(mathsvg_row[f"{prefix}_ci95_high"])
    baseline_low = _as_float(baseline_row[f"{prefix}_ci95_low"])
    baseline_high = _as_float(baseline_row[f"{prefix}_ci95_high"])
    if None in (
        mathsvg_low,
        mathsvg_high,
        baseline_low,
        baseline_high,
    ):
        return point_not_worse, "not-measured"
    if point_not_worse:
        status = (
            "confirmed"
            if float(mathsvg_high) <= float(baseline_low)
            else "inconclusive"
        )
    else:
        status = (
            "confirmed"
            if float(mathsvg_low) > float(baseline_high)
            else "inconclusive"
        )
    return point_not_worse, status


def _metric_value(
    row: Mapping[str, object] | None,
    prefix: str,
) -> float | None:
    return (
        _as_float(row.get(f"{prefix}_median", ""))
        if row is not None
        else None
    )


def _hash_value(
    row: Mapping[str, object] | None,
    records: Sequence[TrialRecord],
    field: str,
) -> str:
    if row is not None:
        return str(row[field])
    values = {str(getattr(record, field)) for record in records}
    return next(iter(values)) if len(values) == 1 else ""


def _failure_messages(records: Iterable[TrialRecord], side: str) -> list[str]:
    return sorted(
        {
            f"{side} {record.status}: {record.error}"
            for record in records
            if record.status != "ok"
        }
    )


def _gap_message(
    label: str,
    mathsvg: float | None,
    baseline: float | None,
) -> str:
    if mathsvg is None or baseline is None:
        return f"{label} is not measured"
    delta = mathsvg - baseline
    percent = (
        delta / baseline * 100.0
        if baseline != 0
        else math.inf
    )
    percent_text = (
        format(percent, ".6g") if math.isfinite(percent) else "inf"
    )
    return (
        f"{label} worse by {format(delta, '.17g')} "
        f"({percent_text}%)"
    )


def _comparison_row(
    *,
    environment: tuple[object, ...],
    mathsvg: LogicalConfig,
    baseline: LogicalConfig,
    dataset_id: str,
    row_scope: str,
    summary_index: Mapping[
        tuple[object, ...], list[Mapping[str, object]]
    ],
    record_index: Mapping[tuple[object, ...], list[TrialRecord]],
) -> _BuiltRow:
    summary_dataset = (
        dataset_id if row_scope == "file" else "__aggregate__"
    )
    mathsvg_row, mathsvg_ambiguous = _summary_row_for(
        summary_index,
        environment,
        mathsvg,
        summary_dataset,
        row_scope,
    )
    baseline_row, baseline_ambiguous = _summary_row_for(
        summary_index,
        environment,
        baseline,
        summary_dataset,
        row_scope,
    )
    mathsvg_records = _records_for(
        record_index,
        environment,
        mathsvg,
        dataset_id,
        row_scope,
    )
    baseline_records = _records_for(
        record_index,
        environment,
        baseline,
        dataset_id,
        row_scope,
    )
    file_name, input_sha256, original_bytes, identity_ok, identity_error = (
        _identity(
            mathsvg_records,
            baseline_records,
            dataset_id,
            row_scope,
        )
    )
    metric_values: dict[str, tuple[float | None, float | None]] = {
        label: (
            _metric_value(mathsvg_row, prefix),
            _metric_value(baseline_row, prefix),
        )
        for label, prefix in CORE_METRICS
    }
    directions = {
        label: _direction(mathsvg_row, baseline_row, prefix)
        for label, prefix in CORE_METRICS
    }
    confidence_values = [status for _, status in directions.values()]
    if "not-measured" in confidence_values:
        confidence_status = "not-measured"
    elif all(status == "confirmed" for status in confidence_values):
        confidence_status = "confirmed"
    else:
        confidence_status = "inconclusive"
    if not identity_ok and mathsvg_records and baseline_records:
        confidence_status = "inconclusive"

    roundtrip_ok = (
        mathsvg_row is not None
        and baseline_row is not None
        and _as_bool(mathsvg_row["roundtrip_all"])
        and _as_bool(baseline_row["roundtrip_all"])
    )
    native_mathsvg = bool(mathsvg_records) and all(
        record.native_mathsvg for record in mathsvg_records
    )
    mathsvg_bytes, baseline_bytes = metric_values["size"]
    mathsvg_compression, baseline_compression = metric_values["compression"]
    mathsvg_decompression, baseline_decompression = metric_values[
        "decompression"
    ]
    mathsvg_rss, baseline_rss = metric_values["memory"]
    complete_points = (
        original_bytes is not None
        and mathsvg_bytes is not None
        and baseline_bytes is not None
        and mathsvg_compression is not None
        and baseline_compression is not None
        and mathsvg_decompression is not None
        and baseline_decompression is not None
        and mathsvg_rss is not None
        and baseline_rss is not None
    )
    certificate_values: dict[str, object]
    if complete_points and identity_ok and native_mathsvg:
        certificate = compare(
            Measurement(
                codec=mathsvg.codec,
                config=mathsvg.profile,
                dataset=str(
                    _environment_values(environment)["split"]
                ),
                file=file_name,
                original_bytes=int(original_bytes),
                compressed_bytes=float(mathsvg_bytes),  # type: ignore[arg-type]
                compression_ns=float(mathsvg_compression),  # type: ignore[arg-type]
                decompression_ns=float(mathsvg_decompression),  # type: ignore[arg-type]
                peak_rss_bytes=float(mathsvg_rss),  # type: ignore[arg-type]
                confidence_status=confidence_status,
                roundtrip_ok=roundtrip_ok,
                native_mathsvg=True,
            ),
            Measurement(
                codec=baseline.codec,
                config=baseline.config,
                dataset=str(
                    _environment_values(environment)["split"]
                ),
                file=file_name,
                original_bytes=int(original_bytes),
                compressed_bytes=float(baseline_bytes),  # type: ignore[arg-type]
                compression_ns=float(baseline_compression),  # type: ignore[arg-type]
                decompression_ns=float(baseline_decompression),  # type: ignore[arg-type]
                peak_rss_bytes=float(baseline_rss),  # type: ignore[arg-type]
                confidence_status=confidence_status,
                roundtrip_ok=roundtrip_ok,
                native_mathsvg=False,
            ),
        )
        certificate_values = asdict(certificate)
    else:
        point_not_worse = {
            label: (
                left is not None
                and right is not None
                and left <= right
            )
            for label, (left, right) in metric_values.items()
        }
        strict_count = sum(
            left is not None and right is not None and left < right
            for left, right in metric_values.values()
        )
        certificate_values = {
            "baseline_codec": baseline.codec,
            "baseline_config": baseline.config,
            "mathsvg_profile": mathsvg.profile,
            "dataset": str(
                _environment_values(environment)["split"]
            ),
            "file": file_name,
            "original_bytes": (
                original_bytes if original_bytes is not None else ""
            ),
            "baseline_bytes": (
                baseline_bytes if baseline_bytes is not None else ""
            ),
            "mathsvg_bytes": (
                mathsvg_bytes if mathsvg_bytes is not None else ""
            ),
            "baseline_compression_ns": (
                baseline_compression
                if baseline_compression is not None
                else ""
            ),
            "mathsvg_compression_ns": (
                mathsvg_compression
                if mathsvg_compression is not None
                else ""
            ),
            "baseline_decompression_ns": (
                baseline_decompression
                if baseline_decompression is not None
                else ""
            ),
            "mathsvg_decompression_ns": (
                mathsvg_decompression
                if mathsvg_decompression is not None
                else ""
            ),
            "baseline_peak_rss": (
                baseline_rss if baseline_rss is not None else ""
            ),
            "mathsvg_peak_rss": (
                mathsvg_rss if mathsvg_rss is not None else ""
            ),
            "size_not_worse": point_not_worse["size"],
            "compression_not_worse": point_not_worse["compression"],
            "decompression_not_worse": point_not_worse[
                "decompression"
            ],
            "memory_not_worse": point_not_worse["memory"],
            "strict_metric_count": strict_count,
            "confidence_status": confidence_status,
            "roundtrip_ok": roundtrip_ok,
            "native_mathsvg": native_mathsvg,
        }

    reasons: list[str] = []
    if not mathsvg_records:
        reasons.append("missing MathSVG evidence")
    if not baseline_records:
        reasons.append("missing baseline evidence")
    if mathsvg_ambiguous:
        reasons.append(
            "ambiguous MathSVG config/executable hashes in one environment"
        )
    if baseline_ambiguous:
        reasons.append(
            "ambiguous baseline config/executable hashes in one environment"
        )
    if identity_error:
        reasons.append(identity_error)
    reasons.extend(_failure_messages(mathsvg_records, "MathSVG"))
    reasons.extend(_failure_messages(baseline_records, "baseline"))
    if mathsvg_records and not native_mathsvg:
        reasons.append("MathSVG evidence is not native")
    if not roundtrip_ok:
        reasons.append("round-trip is not verified for both sides")
    if (
        mathsvg_row is not None
        and not _as_bool(mathsvg_row["deterministic_archive_all"])
    ):
        reasons.append("native MathSVG archive is not deterministic")
    for label, _ in CORE_METRICS:
        not_worse, status = directions[label]
        if status != "confirmed":
            reasons.append(f"{label} confidence is {status}")
            if (
                not not_worse
                and metric_values[label][0] is not None
                and metric_values[label][1] is not None
            ):
                reasons.append(
                    _gap_message(
                        f"{label} point estimate",
                        metric_values[label][0],
                        metric_values[label][1],
                    )
                )
        elif not not_worse:
            reasons.append(
                _gap_message(
                    label,
                    metric_values[label][0],
                    metric_values[label][1],
                )
            )
    if (
        confidence_status == "confirmed"
        and all(value[0] for value in directions.values())
        and int(certificate_values["strict_metric_count"]) < 1
    ):
        reasons.append("no metric is strictly better")

    provisional_pass = (
        not reasons
        and bool(certificate_values["roundtrip_ok"])
        and bool(certificate_values["native_mathsvg"])
        and certificate_values["confidence_status"] == "confirmed"
        and all(
            bool(certificate_values[field])
            for field in (
                "size_not_worse",
                "compression_not_worse",
                "decompression_not_worse",
                "memory_not_worse",
            )
        )
        and int(certificate_values["strict_metric_count"]) >= 1
    )
    mathsvg_status = _side_status(mathsvg_row, mathsvg_records)
    baseline_status = _side_status(baseline_row, baseline_records)
    if provisional_pass:
        comparison_status = "pass"
    elif mathsvg_ambiguous or baseline_ambiguous or identity_error:
        comparison_status = "incomparable"
    elif not mathsvg_records or not baseline_records:
        comparison_status = "missing"
    elif "unavailable" in (mathsvg_status, baseline_status):
        comparison_status = "unavailable"
    elif confidence_status != "confirmed":
        comparison_status = "inconclusive"
    else:
        comparison_status = "fail"

    environment_values = _environment_values(environment)
    extras: dict[str, object] = {
        "schema_version": 1,
        "row_scope": row_scope,
        **environment_values,
        "dataset_id": (
            dataset_id if row_scope == "file" else "__aggregate__"
        ),
        "input_sha256": input_sha256,
        "mathsvg_codec": mathsvg.codec,
        "mathsvg_codec_config": mathsvg.config,
        "mathsvg_config_sha256": _hash_value(
            mathsvg_row,
            mathsvg_records,
            "config_sha256",
        ),
        "mathsvg_executable_sha256": _hash_value(
            mathsvg_row,
            mathsvg_records,
            "codec_executable_sha256",
        ),
        "mathsvg_status": mathsvg_status,
        "baseline_config_sha256": _hash_value(
            baseline_row,
            baseline_records,
            "config_sha256",
        ),
        "baseline_executable_sha256": _hash_value(
            baseline_row,
            baseline_records,
            "codec_executable_sha256",
        ),
        "baseline_status": baseline_status,
        **{
            f"{label}_confidence_status": directions[label][1]
            for label, _ in CORE_METRICS
        },
        "comparison_status": comparison_status,
        "certificate_pass": provisional_pass,
        "failure_reason": "; ".join(dict.fromkeys(reasons)),
    }
    values = {**certificate_values, **extras}
    if set(values) != set(OUTPUT_FIELDS):
        raise AssertionError("internal dominance row/schema mismatch")
    return _BuiltRow(
        values=values,
        environment=environment,
        mathsvg=mathsvg,
        baseline=baseline,
        dataset_id=dataset_id,
        row_scope=row_scope,
    )


def _required_baseline(value: str) -> LogicalConfig:
    codec, separator, config = value.partition(":")
    if not codec or (separator and not config):
        raise argparse.ArgumentTypeError(
            "baseline must be CODEC or CODEC:CONFIG"
        )
    return LogicalConfig(
        native=False,
        codec=codec,
        config=config if separator else codec,
        profile="",
    )


def _row_sort_key(row: _BuiltRow) -> tuple[str, ...]:
    return tuple(
        str(value)
        for value in (
            *row.environment,
            row.dataset_id,
            row.mathsvg.codec,
            row.mathsvg.config,
            row.mathsvg.profile,
            row.baseline.codec,
            row.baseline.config,
        )
    )


def _pareto_envelope(
    built: Sequence[_BuiltRow],
    summary_index: Mapping[
        tuple[object, ...], list[Mapping[str, object]]
    ],
) -> list[_BuiltRow]:
    candidate_groups: dict[
        tuple[object, ...],
        dict[LogicalConfig, tuple[float, ...]],
    ] = defaultdict(dict)
    seen_points = {
        (
            row.environment,
            row.row_scope,
            row.dataset_id,
            row.mathsvg,
        )
        for row in built
    }
    for environment, row_scope, dataset_id, logical in seen_points:
        summary_dataset = (
            dataset_id if row_scope == "file" else "__aggregate__"
        )
        summary_row, ambiguous = _summary_row_for(
            summary_index,
            environment,
            logical,
            summary_dataset,
            row_scope,
        )
        vector = tuple(
            value
            for _, prefix in CORE_METRICS
            if (
                value := _metric_value(summary_row, prefix)
            )
            is not None
        )
        if (
            not ambiguous
            and summary_row is not None
            and _native_point_available(summary_row)
            and len(vector) == len(CORE_METRICS)
        ):
            candidate_groups[
                (environment, row_scope, dataset_id)
            ][logical] = vector

    envelope: set[
        tuple[tuple[object, ...], str, str, LogicalConfig]
    ] = set()
    for (environment, row_scope, dataset_id), candidates in (
        candidate_groups.items()
    ):
        for logical, vector in candidates.items():
            dominated = any(
                other != logical
                and all(
                    right <= left
                    for right, left in zip(
                        other_vector,
                        vector,
                        strict=True,
                    )
                )
                and any(
                    right < left
                    for right, left in zip(
                        other_vector,
                        vector,
                        strict=True,
                    )
                )
                for other, other_vector in candidates.items()
            )
            if not dominated:
                envelope.add(
                    (environment, row_scope, dataset_id, logical)
                )
    return [
        row
        for row in built
        if (
            row.environment,
            row.row_scope,
            row.dataset_id,
            row.mathsvg,
        )
        in envelope
    ]


def build_dominance_artifacts(
    records: Sequence[TrialRecord],
    *,
    summary_rows: Sequence[Mapping[str, object]] | None = None,
    bootstrap_replicates: int = 10_000,
    seed: int = 1_297_748_005,
    required_baselines: Sequence[LogicalConfig] = (),
    required_profiles: Sequence[str] = (),
) -> DominanceArtifacts:
    """Build all four required artifacts without making optimistic joins."""

    if bootstrap_replicates < 100:
        raise DominancePipelineError(
            "bootstrap_replicates must be at least 100"
        )
    measured = [record for record in records if not record.warmup]
    if not measured:
        raise DominancePipelineError("raw JSONL has no measured trials")
    for record in measured:
        record.validate()
    if any(logical.native for logical in required_baselines):
        raise DominancePipelineError(
            "required_baselines must contain external configurations"
        )
    recomputed = build_summary_rows(
        measured,
        bootstrap_replicates=bootstrap_replicates,
        seed=seed,
    )
    if summary_rows is not None:
        _validate_supplied_summary(summary_rows, recomputed)
        selected_summary = [dict(row) for row in summary_rows]
    else:
        selected_summary = [dict(row) for row in recomputed]
    _override_corpus_peak_rss(selected_summary, measured)
    summary_index = _index_summary(selected_summary)
    record_index = _index_records(measured)

    environments = sorted(
        {_environment_from_record(record) for record in measured},
        key=lambda values: tuple(str(value) for value in values),
    )
    built: list[_BuiltRow] = []
    for environment in environments:
        environment_records = [
            record
            for record in measured
            if _environment_from_record(record) == environment
        ]
        native_configs = {
            _logical_from_record(record)
            for record in environment_records
            if record.native_mathsvg
        }
        baseline_configs = {
            _logical_from_record(record)
            for record in environment_records
            if not record.native_mathsvg
        }
        for profile in required_profiles:
            if not profile:
                raise DominancePipelineError(
                    "required profile must not be empty"
                )
            if not any(
                logical.profile == profile
                for logical in native_configs
            ):
                native_configs.add(
                    LogicalConfig(
                        native=True,
                        codec="mathsvg",
                        config=profile,
                        profile=profile,
                    )
                )
        baseline_configs.update(required_baselines)
        if not native_configs:
            native_configs.add(
                LogicalConfig(
                    native=True,
                    codec="mathsvg",
                    config="__missing__",
                    profile="__missing__",
                )
            )
        if not baseline_configs:
            baseline_configs.add(
                LogicalConfig(
                    native=False,
                    codec="__missing__",
                    config="__missing__",
                    profile="",
                )
            )
        dataset_ids = sorted(
            {record.dataset_id for record in environment_records}
        )
        for mathsvg in sorted(native_configs):
            for baseline in sorted(baseline_configs):
                for dataset_id in dataset_ids:
                    built.append(
                        _comparison_row(
                            environment=environment,
                            mathsvg=mathsvg,
                            baseline=baseline,
                            dataset_id=dataset_id,
                            row_scope="file",
                            summary_index=summary_index,
                            record_index=record_index,
                        )
                    )
                built.append(
                    _comparison_row(
                        environment=environment,
                        mathsvg=mathsvg,
                        baseline=baseline,
                        dataset_id="__aggregate__",
                        row_scope="corpus",
                        summary_index=summary_index,
                        record_index=record_index,
                    )
                )
    built.sort(key=_row_sort_key)
    per_file = [row for row in built if row.row_scope == "file"]
    per_corpus = [row for row in built if row.row_scope == "corpus"]
    pareto = _pareto_envelope(built, summary_index)
    failures = [
        row
        for row in built
        if not bool(row.values["certificate_pass"])
    ]
    return DominanceArtifacts(
        per_file=tuple(row.values for row in per_file),
        per_corpus=tuple(row.values for row in per_corpus),
        pareto_envelope=tuple(row.values for row in pareto),
        failures=tuple(row.values for row in failures),
    )


def write_dominance_artifacts(
    output_dir: pathlib.Path,
    artifacts: DominanceArtifacts,
) -> None:
    rows_by_name = {
        "per-file.csv": artifacts.per_file,
        "per-corpus.csv": artifacts.per_corpus,
        "pareto-envelope.csv": artifacts.pareto_envelope,
        "failures.csv": artifacts.failures,
    }
    for name in OUTPUT_NAMES:
        write_csv(
            output_dir / name,
            OUTPUT_FIELDS,
            rows_by_name[name],
        )


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate conservative per-file, per-corpus, Pareto-envelope, "
            "and failure dominance certificates."
        )
    )
    parser.add_argument(
        "raw_jsonl",
        type=pathlib.Path,
        help="strict benchmark JSONL evidence",
    )
    parser.add_argument(
        "--summary-csv",
        type=pathlib.Path,
        help=(
            "optional summary CSV; it must exactly correspond to the raw "
            "evidence"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/dominance"),
    )
    parser.add_argument(
        "--bootstrap-replicates",
        type=_positive_integer,
        default=10_000,
    )
    parser.add_argument("--seed", type=int, default=1_297_748_005)
    parser.add_argument(
        "--required-baseline",
        action="append",
        type=_required_baseline,
        default=[],
        metavar="CODEC[:CONFIG]",
        help=(
            "frozen baseline expected in every environment; repeat for "
            "multiple configurations"
        ),
    )
    parser.add_argument(
        "--required-profile",
        action="append",
        default=[],
        metavar="PROFILE",
        help=(
            "frozen MathSVG profile expected in every environment; repeat "
            "for multiple profiles"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        records = read_jsonl(args.raw_jsonl)
        summary_rows = (
            _read_summary_csv(args.summary_csv)
            if args.summary_csv is not None
            else None
        )
        artifacts = build_dominance_artifacts(
            records,
            summary_rows=summary_rows,
            bootstrap_replicates=args.bootstrap_replicates,
            seed=args.seed,
            required_baselines=args.required_baseline,
            required_profiles=args.required_profile,
        )
        write_dominance_artifacts(args.output_dir, artifacts)
    except (
        OSError,
        TrialError,
        SummaryError,
        DominancePipelineError,
    ) as exc:
        print(f"dominance pipeline failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
