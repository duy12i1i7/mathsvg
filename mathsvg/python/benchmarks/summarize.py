#!/usr/bin/env python3
"""Create deterministic benchmark and procedural summary CSV artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
import pathlib
import statistics
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict
from typing import Callable, Iterable, Mapping, Sequence

from mathsvg.python.analysis.statistics import (
    paired_bootstrap_interval,
    summarize,
)
from mathsvg.python.benchmarks.schema import TrialError, TrialRecord, read_jsonl

GROUP_FIELDS = (
    "experiment_id",
    "machine_id",
    "architecture",
    "source_commit",
    "config_sha256",
    "dataset_manifest_sha256",
    "randomization_seed",
    "codec_executable_sha256",
    "measurement_method",
    "measurement_tool_sha256",
    "split",
    "codec",
    "codec_config",
    "profile",
    "threads",
)
SUMMARY_FIELDS = (
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
    "close_delta_below_2_percent",
    "protocol_repetitions_ok",
    "roundtrip_all",
    "deterministic_archive_all",
    "status",
    "confidence_status",
    "archive_bytes_count",
    "archive_bytes_mean",
    "archive_bytes_median",
    "archive_bytes_standard_deviation",
    "archive_bytes_ci95_low",
    "archive_bytes_ci95_high",
    "ratio_count",
    "ratio_mean",
    "ratio_median",
    "ratio_standard_deviation",
    "ratio_ci95_low",
    "ratio_ci95_high",
    "bits_per_byte_median",
    "compression_wall_ns_count",
    "compression_wall_ns_mean",
    "compression_wall_ns_median",
    "compression_wall_ns_standard_deviation",
    "compression_wall_ns_ci95_low",
    "compression_wall_ns_ci95_high",
    "compression_cpu_ns_count",
    "compression_cpu_ns_mean",
    "compression_cpu_ns_median",
    "compression_cpu_ns_standard_deviation",
    "compression_cpu_ns_ci95_low",
    "compression_cpu_ns_ci95_high",
    "decompression_wall_ns_count",
    "decompression_wall_ns_mean",
    "decompression_wall_ns_median",
    "decompression_wall_ns_standard_deviation",
    "decompression_wall_ns_ci95_low",
    "decompression_wall_ns_ci95_high",
    "decompression_cpu_ns_count",
    "decompression_cpu_ns_mean",
    "decompression_cpu_ns_median",
    "decompression_cpu_ns_standard_deviation",
    "decompression_cpu_ns_ci95_low",
    "decompression_cpu_ns_ci95_high",
    "peak_rss_bytes_count",
    "peak_rss_bytes_mean",
    "peak_rss_bytes_median",
    "peak_rss_bytes_standard_deviation",
    "peak_rss_bytes_ci95_low",
    "peak_rss_bytes_ci95_high",
    "compression_peak_rss_bytes_count",
    "compression_peak_rss_bytes_mean",
    "compression_peak_rss_bytes_median",
    "compression_peak_rss_bytes_standard_deviation",
    "compression_peak_rss_bytes_ci95_low",
    "compression_peak_rss_bytes_ci95_high",
    "decompression_peak_rss_bytes_count",
    "decompression_peak_rss_bytes_mean",
    "decompression_peak_rss_bytes_median",
    "decompression_peak_rss_bytes_standard_deviation",
    "decompression_peak_rss_bytes_ci95_low",
    "decompression_peak_rss_bytes_ci95_high",
    "energy_uj_count",
    "energy_uj_mean",
    "energy_uj_median",
    "energy_uj_standard_deviation",
    "energy_uj_ci95_low",
    "energy_uj_ci95_high",
    "compression_mbps_count",
    "compression_mbps_mean",
    "compression_mbps_median",
    "compression_mbps_standard_deviation",
    "compression_mbps_ci95_low",
    "compression_mbps_ci95_high",
    "decompression_mbps_count",
    "decompression_mbps_mean",
    "decompression_mbps_median",
    "decompression_mbps_standard_deviation",
    "decompression_mbps_ci95_low",
    "decompression_mbps_ci95_high",
    "bootstrap_ratio_ci95_low",
    "bootstrap_ratio_ci95_high",
    "bootstrap_compression_mbps_ci95_low",
    "bootstrap_compression_mbps_ci95_high",
    "bootstrap_decompression_mbps_ci95_low",
    "bootstrap_decompression_mbps_ci95_high",
    "bootstrap_replicates",
    "bootstrap_seed",
)
PROCEDURAL_METRICS = (
    "literal_only_archive_bytes",
    "pre_entropy_archive_bytes",
    "entropy_saved_bytes",
    "entropy_penalty_bytes",
    "procedural_gain_bytes",
    "procedural_penalty_bytes",
    "container_overhead_bytes",
    "function_graph_bytes",
    "coordinate_bytes",
    "shared_definition_bytes",
    "reference_bytes",
    "parameter_bytes",
    "residual_layer_bytes",
    "literal_leaf_bytes",
    "entropy_metadata_bytes",
    "index_bytes",
    "node_count",
    "shared_node_count",
    "residual_depth_sum",
    "residual_root_count",
    "function_reconstructed_bytes",
    "literal_reconstructed_bytes",
    "coordinate_saved_bytes",
    "dag_saved_bytes",
    "recursive_residual_saved_bytes",
    "symbolic_saved_bytes",
    "search_ns",
)
PROCEDURAL_FIELDS = (
    *GROUP_FIELDS,
    "dataset_id",
    "row_scope",
    "original_bytes",
    "scheduled_trials",
    "successful_trials",
    "status",
    *(f"{name}_median" for name in PROCEDURAL_METRICS),
    "archive_bytes_median",
    "procedural_coverage",
    "literal_fraction",
    "procedural_gain_before_entropy_bytes",
    "procedural_gain_bytes",
    "entropy_coder_gain_bytes",
    "entropy_coder_penalty_bytes",
    "procedural_penalty_bytes",
    "average_residual_depth",
    "search_time_per_saved_byte_ns",
    "time_per_saved_byte_basis",
)


class SummaryError(ValueError):
    """Raw rows cannot be summarized without hiding protocol defects."""


def _group_key(record: TrialRecord) -> tuple[object, ...]:
    return tuple(getattr(record, name) for name in GROUP_FIELDS)


def _group_values(key: tuple[object, ...]) -> dict[str, object]:
    return dict(zip(GROUP_FIELDS, key, strict=True))


def _status_counts(records: Sequence[TrialRecord]) -> dict[str, int]:
    return {
        status: sum(record.status == status for record in records)
        for status in ("ok", "failed", "timeout", "unavailable")
    }


def _overall_status(records: Sequence[TrialRecord]) -> str:
    counts = _status_counts(records)
    if counts["ok"] == len(records):
        return "complete"
    if counts["ok"]:
        return "partial"
    if counts["timeout"]:
        return "timeout"
    if counts["failed"]:
        return "failed"
    return "unavailable"


def _metric(
    row: dict[str, object],
    prefix: str,
    values: Iterable[float],
) -> None:
    samples = list(values)
    names = (
        "count",
        "mean",
        "median",
        "standard_deviation",
        "ci95_low",
        "ci95_high",
    )
    if not samples:
        for name in names:
            row[f"{prefix}_{name}"] = ""
        return
    summary = summarize(samples)
    for name, value in asdict(summary).items():
        row[f"{prefix}_{name}"] = value


def _throughput_mbps(original_bytes: int, elapsed_ns: int) -> float:
    if elapsed_ns <= 0:
        raise SummaryError("successful timing must be positive")
    return original_bytes * 1000.0 / elapsed_ns


def _bootstrap_seed(seed: int, key: tuple[object, ...], label: str) -> int:
    material = (
        f"{seed}\0{label}\0"
        + "\0".join(str(value) for value in key)
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _bootstrap(
    pairs: Sequence[tuple[float, float]],
    statistic: Callable[[Sequence[tuple[float, float]]], float],
    *,
    seed: int,
    replicates: int,
) -> tuple[float | str, float | str]:
    if not pairs:
        return "", ""
    return paired_bootstrap_interval(
        pairs,
        statistic,
        seed=seed,
        replicates=replicates,
    )


def _summary_row(
    key: tuple[object, ...],
    dataset_id: str,
    records: Sequence[TrialRecord],
    successful_samples: Sequence[
        tuple[
            int,
            int,
            int,
            int,
            int,
            int,
        ]
    ],
    *,
    original_bytes: int,
    row_scope: str,
    bootstrap_pairs: Mapping[str, Sequence[tuple[float, float]]],
    bootstrap_replicates: int,
    seed: int,
    aggregate_resources: Mapping[str, Sequence[float]] | None = None,
) -> dict[str, object]:
    counts = _status_counts(records)
    repetitions = {record.repetition for record in records}
    row: dict[str, object] = {
        **_group_values(key),
        "dataset_id": dataset_id,
        "row_scope": row_scope,
        "original_bytes": original_bytes,
        "scheduled_trials": len(records),
        "successful_trials": counts["ok"],
        "failed_trials": counts["failed"],
        "timeout_trials": counts["timeout"],
        "unavailable_trials": counts["unavailable"],
        "measured_repetitions": len(repetitions),
        "close_delta_below_2_percent": False,
        "protocol_repetitions_ok": len(repetitions) >= 5,
        "roundtrip_all": bool(records)
        and all(record.roundtrip_ok for record in records),
        "deterministic_archive_all": bool(records)
        and all(record.deterministic_archive for record in records),
        "status": _overall_status(records),
        "confidence_status": (
            "confirmed"
            if counts["ok"] == len(records) and len(repetitions) >= 5
            else ("inconclusive" if counts["ok"] else "not-measured")
        ),
        "bootstrap_replicates": (
            bootstrap_replicates if row_scope == "corpus" else ""
        ),
        "bootstrap_seed": seed if row_scope == "corpus" else "",
    }
    archives = [float(sample[0]) for sample in successful_samples]
    ratios = [
        float(sample[1]) / sample[0]
        for sample in successful_samples
        if sample[0] > 0
    ]
    compression_wall = [
        float(sample[2]) for sample in successful_samples
    ]
    compression_cpu = [
        float(sample[3]) for sample in successful_samples
    ]
    decompression_wall = [
        float(sample[4]) for sample in successful_samples
    ]
    decompression_cpu = [
        float(sample[5]) for sample in successful_samples
    ]
    if aggregate_resources is None:
        rss = [
            float(record.peak_rss_bytes)
            for record in records
            if record.status == "ok" and record.peak_rss_bytes is not None
        ]
        compression_rss = [
            float(record.compression_peak_rss_bytes)
            for record in records
            if record.status == "ok"
            and record.compression_peak_rss_bytes is not None
        ]
        decompression_rss = [
            float(record.decompression_peak_rss_bytes)
            for record in records
            if record.status == "ok"
            and record.decompression_peak_rss_bytes is not None
        ]
        energy = [
            float(record.energy_uj)
            for record in records
            if record.status == "ok" and record.energy_uj is not None
        ]
    else:
        rss = list(aggregate_resources["peak_rss_bytes"])
        compression_rss = list(
            aggregate_resources["compression_peak_rss_bytes"]
        )
        decompression_rss = list(
            aggregate_resources["decompression_peak_rss_bytes"]
        )
        energy = list(aggregate_resources["energy_uj"])
    compression_mbps = [
        _throughput_mbps(sample[1], sample[2])
        for sample in successful_samples
    ]
    decompression_mbps = [
        _throughput_mbps(sample[1], sample[4])
        for sample in successful_samples
    ]
    for prefix, values in (
        ("archive_bytes", archives),
        ("ratio", ratios),
        ("compression_wall_ns", compression_wall),
        ("compression_cpu_ns", compression_cpu),
        ("decompression_wall_ns", decompression_wall),
        ("decompression_cpu_ns", decompression_cpu),
        ("peak_rss_bytes", rss),
        ("compression_peak_rss_bytes", compression_rss),
        ("decompression_peak_rss_bytes", decompression_rss),
        ("energy_uj", energy),
        ("compression_mbps", compression_mbps),
        ("decompression_mbps", decompression_mbps),
    ):
        _metric(row, prefix, values)
    row["bits_per_byte_median"] = (
        float(row["archive_bytes_median"]) * 8.0 / original_bytes
        if archives and original_bytes > 0
        else ""
    )

    for label, statistic in (
        (
            "ratio",
            lambda pairs: sum(left for left, _ in pairs)
            / sum(right for _, right in pairs),
        ),
        (
            "compression_mbps",
            lambda pairs: _throughput_mbps(
                int(sum(left for left, _ in pairs)),
                int(sum(right for _, right in pairs)),
            ),
        ),
        (
            "decompression_mbps",
            lambda pairs: _throughput_mbps(
                int(sum(left for left, _ in pairs)),
                int(sum(right for _, right in pairs)),
            ),
        ),
    ):
        pairs = bootstrap_pairs.get(label, ())
        low, high = _bootstrap(
            list(pairs),
            statistic,
            seed=_bootstrap_seed(seed, key, label),
            replicates=bootstrap_replicates,
        )
        row[f"bootstrap_{label}_ci95_low"] = (
            low if row_scope == "corpus" else ""
        )
        row[f"bootstrap_{label}_ci95_high"] = (
            high if row_scope == "corpus" else ""
        )
    return row


def _enforce_close_delta_repetitions(
    rows: Sequence[dict[str, object]],
) -> None:
    comparison_groups: dict[
        tuple[object, ...], list[dict[str, object]]
    ] = defaultdict(list)
    for row in rows:
        key = (
            row["experiment_id"],
            row["machine_id"],
            row["architecture"],
            row["source_commit"],
            row["dataset_manifest_sha256"],
            row["randomization_seed"],
            row["measurement_method"],
            row["measurement_tool_sha256"],
            row["split"],
            row["dataset_id"],
            row["row_scope"],
            row["threads"],
        )
        comparison_groups[key].append(row)
    metrics = (
        "archive_bytes_median",
        "compression_wall_ns_median",
        "decompression_wall_ns_median",
        "peak_rss_bytes_median",
    )
    for group in comparison_groups.values():
        for left_index, left in enumerate(group):
            if left["status"] != "complete":
                continue
            for right in group[left_index + 1 :]:
                if right["status"] != "complete":
                    continue
                if (
                    left["codec"],
                    left["codec_config"],
                    left["profile"],
                ) == (
                    right["codec"],
                    right["codec_config"],
                    right["profile"],
                ):
                    continue
                close = False
                for metric in metrics:
                    left_value = left.get(metric, "")
                    right_value = right.get(metric, "")
                    if left_value == "" or right_value == "":
                        continue
                    denominator = max(
                        abs(float(left_value)),
                        abs(float(right_value)),
                    )
                    if denominator > 0 and (
                        abs(float(left_value) - float(right_value))
                        / denominator
                        < 0.02
                    ):
                        close = True
                        break
                if close:
                    left["close_delta_below_2_percent"] = True
                    right["close_delta_below_2_percent"] = True
    for row in rows:
        minimum = (
            10 if row["close_delta_below_2_percent"] else 5
        )
        protocol_ok = int(row["measured_repetitions"]) >= minimum
        row["protocol_repetitions_ok"] = protocol_ok
        native_determinism_ok = (
            not bool(row["profile"])
            or bool(row["deterministic_archive_all"])
        )
        row["confidence_status"] = (
            "confirmed"
            if (
                row["status"] == "complete"
                and protocol_ok
                and native_determinism_ok
            )
            else (
                "inconclusive"
                if int(row["successful_trials"]) > 0
                else "not-measured"
            )
        )


def build_summary_rows(
    records: Sequence[TrialRecord],
    *,
    bootstrap_replicates: int,
    seed: int,
) -> list[dict[str, object]]:
    measured = [record for record in records if not record.warmup]
    if not measured:
        raise SummaryError("raw JSONL has no measured trials")
    schedule_keys: set[tuple[object, ...]] = set()
    for record in measured:
        identity = (
            record.experiment_id,
            record.machine_id,
            record.split,
            record.randomization_seed,
            record.schedule_block,
            record.schedule_order,
            record.repetition,
        )
        if identity in schedule_keys:
            raise SummaryError(f"duplicate measured schedule cell: {identity}")
        schedule_keys.add(identity)

    grouped: dict[
        tuple[object, ...], dict[str, list[TrialRecord]]
    ] = defaultdict(lambda: defaultdict(list))
    for record in measured:
        grouped[_group_key(record)][record.dataset_id].append(record)

    rows: list[dict[str, object]] = []
    for key in sorted(grouped):
        datasets = grouped[key]
        for dataset_id in sorted(datasets):
            group = datasets[dataset_id]
            input_identities = {
                (
                    record.input_path,
                    record.input_sha256,
                    record.original_bytes,
                )
                for record in group
            }
            if len(input_identities) != 1:
                raise SummaryError(
                    f"{dataset_id}: inconsistent input identity across trials"
                )
            repetitions = [record.repetition for record in group]
            if len(repetitions) != len(set(repetitions)):
                raise SummaryError(
                    f"{dataset_id}: duplicate codec repetition"
                )
            if sorted(repetitions) != list(
                range(max(repetitions, default=-1) + 1)
            ):
                raise SummaryError(
                    f"{dataset_id}: missing measured repetition row"
                )
            successful = [
                (
                    int(record.archive_bytes),
                    record.original_bytes,
                    int(record.compression_wall_ns),
                    int(record.compression_cpu_ns),
                    int(record.decompression_wall_ns),
                    int(record.decompression_cpu_ns),
                )
                for record in group
                if record.status == "ok"
            ]
            rows.append(
                _summary_row(
                    key,
                    dataset_id,
                    group,
                    successful,
                    original_bytes=group[0].original_bytes,
                    row_scope="file",
                    bootstrap_pairs={},
                    bootstrap_replicates=bootstrap_replicates,
                    seed=seed,
                )
            )

        all_records = [
            record
            for dataset_id in sorted(datasets)
            for record in datasets[dataset_id]
        ]
        dataset_count = len(datasets)
        by_repetition: dict[int, list[TrialRecord]] = defaultdict(list)
        for record in all_records:
            by_repetition[record.repetition].append(record)
        aggregate_samples: list[tuple[int, int, int, int, int, int]] = []
        aggregate_resources: dict[str, list[float]] = {
            "peak_rss_bytes": [],
            "compression_peak_rss_bytes": [],
            "decompression_peak_rss_bytes": [],
            "energy_uj": [],
        }
        for repetition in sorted(by_repetition):
            repetition_rows = by_repetition[repetition]
            if (
                len(repetition_rows) == dataset_count
                and all(record.status == "ok" for record in repetition_rows)
            ):
                aggregate_samples.append(
                    (
                        sum(int(record.archive_bytes) for record in repetition_rows),
                        sum(record.original_bytes for record in repetition_rows),
                        sum(
                            int(record.compression_wall_ns)
                            for record in repetition_rows
                        ),
                        sum(
                            int(record.compression_cpu_ns)
                            for record in repetition_rows
                        ),
                        sum(
                            int(record.decompression_wall_ns)
                            for record in repetition_rows
                        ),
                        sum(
                            int(record.decompression_cpu_ns)
                            for record in repetition_rows
                        ),
                    )
                )
                aggregate_resources["peak_rss_bytes"].append(
                    float(
                        max(
                            int(record.peak_rss_bytes)
                            for record in repetition_rows
                        )
                    )
                )
                aggregate_resources["compression_peak_rss_bytes"].append(
                    float(
                        max(
                            int(record.compression_peak_rss_bytes)
                            for record in repetition_rows
                        )
                    )
                )
                aggregate_resources["decompression_peak_rss_bytes"].append(
                    float(
                        max(
                            int(record.decompression_peak_rss_bytes)
                            for record in repetition_rows
                        )
                    )
                )
                if all(
                    record.energy_uj is not None
                    for record in repetition_rows
                ):
                    aggregate_resources["energy_uj"].append(
                        float(
                            sum(
                                int(record.energy_uj)
                                for record in repetition_rows
                            )
                        )
                    )

        per_file_pairs: dict[str, list[tuple[float, float]]] = {
            "ratio": [],
            "compression_mbps": [],
            "decompression_mbps": [],
        }
        if all(record.status == "ok" for record in all_records):
            for dataset_id in sorted(datasets):
                successful_rows = datasets[dataset_id]
                original = float(successful_rows[0].original_bytes)
                archive = statistics.median(
                    int(record.archive_bytes)
                    for record in successful_rows
                )
                compression = statistics.median(
                    int(record.compression_wall_ns)
                    for record in successful_rows
                )
                decompression = statistics.median(
                    int(record.decompression_wall_ns)
                    for record in successful_rows
                )
                if archive > 0:
                    per_file_pairs["ratio"].append(
                        (original, float(archive))
                    )
                if compression > 0:
                    per_file_pairs["compression_mbps"].append(
                        (original, float(compression))
                    )
                if decompression > 0:
                    per_file_pairs["decompression_mbps"].append(
                        (original, float(decompression))
                    )
        rows.append(
            _summary_row(
                key,
                "__aggregate__",
                all_records,
                aggregate_samples,
                original_bytes=sum(
                    group[0].original_bytes for group in datasets.values()
                ),
                row_scope="corpus",
                bootstrap_pairs=per_file_pairs,
                bootstrap_replicates=bootstrap_replicates,
                seed=seed,
                aggregate_resources=aggregate_resources,
            )
        )
    _enforce_close_delta_repetitions(rows)
    return rows


def _median_value(
    records: Sequence[TrialRecord], field: str
) -> float | str:
    values = [
        getattr(record, field)
        for record in records
        if record.status == "ok" and getattr(record, field) is not None
    ]
    return statistics.median(values) if values else ""


def _procedural_row(
    key: tuple[object, ...],
    dataset_id: str,
    records: Sequence[TrialRecord],
    *,
    row_scope: str,
) -> dict[str, object]:
    successful = [record for record in records if record.status == "ok"]
    if row_scope == "file":
        original_bytes = records[0].original_bytes
        medians = {
            field: _median_value(records, field)
            for field in PROCEDURAL_METRICS
        }
        archive_median = _median_value(records, "archive_bytes")
        compression_wall_median = _median_value(
            records, "compression_wall_ns"
        )
    else:
        datasets: dict[str, list[TrialRecord]] = defaultdict(list)
        for record in records:
            datasets[record.dataset_id].append(record)
        original_bytes = sum(
            group[0].original_bytes for group in datasets.values()
        )
        medians = {}
        for field in PROCEDURAL_METRICS:
            values = [_median_value(group, field) for group in datasets.values()]
            medians[field] = (
                sum(float(value) for value in values)
                if values and all(value != "" for value in values)
                else ""
            )
        archives = [
            _median_value(group, "archive_bytes")
            for group in datasets.values()
        ]
        archive_median = (
            sum(float(value) for value in archives)
            if archives and all(value != "" for value in archives)
            else ""
        )
        compression_values = [
            _median_value(group, "compression_wall_ns")
            for group in datasets.values()
        ]
        compression_wall_median = (
            sum(float(value) for value in compression_values)
            if compression_values
            and all(value != "" for value in compression_values)
            else ""
        )
    row: dict[str, object] = {
        **_group_values(key),
        "dataset_id": dataset_id,
        "row_scope": row_scope,
        "original_bytes": original_bytes,
        "scheduled_trials": len(records),
        "successful_trials": len(successful),
        "status": _overall_status(records),
        "archive_bytes_median": archive_median,
    }
    for field, value in medians.items():
        row[f"{field}_median"] = value
    literal_reconstructed = medians["literal_reconstructed_bytes"]
    function_reconstructed = medians["function_reconstructed_bytes"]
    literal_only = medians["literal_only_archive_bytes"]
    pre_entropy = medians["pre_entropy_archive_bytes"]
    entropy_saved = medians["entropy_saved_bytes"]
    entropy_penalty = medians["entropy_penalty_bytes"]
    procedural_penalty = medians["procedural_penalty_bytes"]
    if (
        literal_reconstructed != ""
        and function_reconstructed != ""
        and original_bytes > 0
    ):
        reconstructed = (
            float(literal_reconstructed) + float(function_reconstructed)
        )
        if not math.isclose(reconstructed, original_bytes):
            raise SummaryError(
                f"{dataset_id}: procedural coverage does not cover input"
            )
        row["procedural_coverage"] = (
            float(function_reconstructed) / original_bytes
        )
        row["literal_fraction"] = (
            float(literal_reconstructed) / original_bytes
        )
    else:
        row["procedural_coverage"] = ""
        row["literal_fraction"] = ""
    gain = (
        float(literal_only) - float(archive_median)
        if literal_only != "" and archive_median != ""
        else None
    )
    row["procedural_gain_bytes"] = gain if gain is not None else ""
    row["procedural_gain_before_entropy_bytes"] = (
        float(literal_only) - float(pre_entropy)
        if literal_only != "" and pre_entropy != ""
        else ""
    )
    row["entropy_coder_gain_bytes"] = (
        entropy_saved if entropy_saved != "" else ""
    )
    row["entropy_coder_penalty_bytes"] = (
        entropy_penalty if entropy_penalty != "" else ""
    )
    row["procedural_penalty_bytes"] = (
        procedural_penalty if procedural_penalty != "" else ""
    )
    depth_sum = medians["residual_depth_sum"]
    roots = medians["residual_root_count"]
    row["average_residual_depth"] = (
        float(depth_sum) / float(roots)
        if depth_sum != "" and roots != "" and float(roots) > 0
        else ""
    )
    row["search_time_per_saved_byte_ns"] = (
        float(compression_wall_median) / gain
        if compression_wall_median != ""
        and gain is not None
        and gain > 0
        else ""
    )
    row["time_per_saved_byte_basis"] = "compression_wall_ns"
    return row


def build_procedural_rows(
    records: Sequence[TrialRecord],
) -> list[dict[str, object]]:
    measured = [
        record
        for record in records
        if not record.warmup and record.native_mathsvg
    ]
    grouped: dict[
        tuple[object, ...], dict[str, list[TrialRecord]]
    ] = defaultdict(lambda: defaultdict(list))
    for record in measured:
        grouped[_group_key(record)][record.dataset_id].append(record)
    rows: list[dict[str, object]] = []
    for key in sorted(grouped):
        datasets = grouped[key]
        for dataset_id in sorted(datasets):
            rows.append(
                _procedural_row(
                    key,
                    dataset_id,
                    datasets[dataset_id],
                    row_scope="file",
                )
            )
        rows.append(
            _procedural_row(
                key,
                "__aggregate__",
                [
                    record
                    for dataset_id in sorted(datasets)
                    for record in datasets[dataset_id]
                ],
                row_scope="corpus",
            )
        )
    return rows


def _format(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SummaryError("CSV value must be finite")
        return format(value, ".17g")
    return str(value)


def write_csv(
    path: pathlib.Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = pathlib.Path(handle.name)
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
                lineterminator="\n",
                extrasaction="raise",
            )
            writer.writeheader()
            for row in rows:
                missing = set(fieldnames) - set(row)
                extra = set(row) - set(fieldnames)
                if missing or extra:
                    raise SummaryError(
                        "CSV row/schema mismatch: "
                        f"missing={sorted(missing)}, extra={sorted(extra)}"
                    )
                writer.writerow(
                    {
                        field: _format(row.get(field, ""))
                        for field in fieldnames
                    }
                )
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "raw_jsonl",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/raw/benchmark.jsonl"),
    )
    parser.add_argument(
        "--benchmark-output",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/summary/benchmark.csv"),
    )
    parser.add_argument(
        "--procedural-output",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/summary/procedural-breakdown.csv"
        ),
    )
    parser.add_argument("--bootstrap-replicates", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=1_297_748_005)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.bootstrap_replicates < 100:
            raise SummaryError("bootstrap replicates must be at least 100")
        records = read_jsonl(args.raw_jsonl)
        summary_rows = build_summary_rows(
            records,
            bootstrap_replicates=args.bootstrap_replicates,
            seed=args.seed,
        )
        procedural_rows = build_procedural_rows(records)
        write_csv(args.benchmark_output, SUMMARY_FIELDS, summary_rows)
        write_csv(
            args.procedural_output,
            PROCEDURAL_FIELDS,
            procedural_rows,
        )
    except (OSError, SummaryError, TrialError, ValueError) as exc:
        print(f"benchmark summary error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
