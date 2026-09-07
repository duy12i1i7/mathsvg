#!/usr/bin/env python3
"""Generate strict development profiling CSV and deterministic SVG plots.

Only non-warm-up rows contribute.  Performance metrics are calculated from
successful trials; failed, timed-out and unavailable trials remain visible as
counts and never become zero-valued measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import math
import os
import pathlib
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from mathsvg.python.benchmarks.schema import TrialRecord, read_jsonl
from mathsvg.python.benchmarks.summarize import write_csv

EVIDENCE_SCOPE = "development"
SVG_SUFFIXES = (
    "size-ratio",
    "compression-throughput",
    "decompression-throughput",
    "peak-rss",
    "procedural-entropy",
)
PROFILE_FIELDS = (
    "evidence_scope",
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
    "native_mathsvg",
    "dataset_id",
    "row_scope",
    "original_bytes",
    "scheduled_measured_trials",
    "successful_trials",
    "failed_trials",
    "timeout_trials",
    "unavailable_trials",
    "complete_repetitions",
    "metric_status",
    "roundtrip_all_successes",
    "deterministic_archive_all_successes",
    "archive_bytes_median",
    "archive_fraction_median",
    "compression_ratio_median",
    "compression_wall_ns_median",
    "compression_cpu_ns_median",
    "compression_cpu_wall_fraction_median",
    "decompression_wall_ns_median",
    "decompression_cpu_ns_median",
    "decompression_cpu_wall_fraction_median",
    "compression_throughput_mb_s_median",
    "decompression_throughput_mb_s_median",
    "peak_rss_bytes_median",
    "compression_peak_rss_bytes_median",
    "decompression_peak_rss_bytes_median",
    "procedural_evidence_status",
    "literal_only_archive_bytes_median",
    "pre_entropy_archive_bytes_median",
    "procedural_gain_before_entropy_bytes_median",
    "procedural_penalty_before_entropy_bytes_median",
    "entropy_gain_bytes_median",
    "entropy_penalty_bytes_median",
    "function_reconstructed_bytes_median",
    "literal_reconstructed_bytes_median",
    "procedural_coverage_median",
    "literal_fraction_median",
)
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
    "native_mathsvg",
)
RUN_IDENTITY_FIELDS = (
    "experiment_id",
    "machine_id",
    "architecture",
    "source_commit",
    "dataset_manifest_sha256",
    "randomization_seed",
    "measurement_method",
    "measurement_tool_sha256",
    "split",
)
PALETTE = (
    "#355070",
    "#2a9d8f",
    "#457b9d",
    "#e9c46a",
    "#8d5a97",
    "#e76f51",
    "#6d597a",
    "#588157",
)
CODEC_COLORS = {
    "balanced-v1": "#6d597a",
    "fast-v1": "#e76f51",
    "gzip-6": "#e9c46a",
    "lz4": "#2a9d8f",
    "raw": "#6b7280",
    "xz-9e": "#8d5a97",
    "zstd-default": "#457b9d",
}


class ProfilingError(RuntimeError):
    """Raw evidence cannot support an honest profiling artifact."""


@dataclass(frozen=True, slots=True)
class MetricSample:
    original_bytes: int
    archive_bytes: int
    compression_wall_ns: int
    compression_cpu_ns: int
    decompression_wall_ns: int
    decompression_cpu_ns: int
    peak_rss_bytes: int
    compression_peak_rss_bytes: int
    decompression_peak_rss_bytes: int
    literal_only_archive_bytes: int | None
    pre_entropy_archive_bytes: int | None
    function_reconstructed_bytes: int | None
    literal_reconstructed_bytes: int | None

    @property
    def has_counterfactual_evidence(self) -> bool:
        return (
            self.literal_only_archive_bytes is not None
            and self.pre_entropy_archive_bytes is not None
        )

    @property
    def has_structural_evidence(self) -> bool:
        return (
            self.function_reconstructed_bytes is not None
            and self.literal_reconstructed_bytes is not None
        )

    @property
    def has_exact_procedural_evidence(self) -> bool:
        return (
            self.has_counterfactual_evidence
            and self.has_structural_evidence
        )


@dataclass(frozen=True, slots=True)
class BarItem:
    label: str
    value: float | None
    display_value: str
    color: str


def _group_key(record: TrialRecord) -> tuple[object, ...]:
    return tuple(getattr(record, field) for field in GROUP_FIELDS)


def _group_values(key: tuple[object, ...]) -> dict[str, object]:
    return dict(zip(GROUP_FIELDS, key, strict=True))


def _sample(record: TrialRecord) -> MetricSample:
    if record.status != "ok":
        raise ProfilingError("only successful trials can become metric samples")
    return MetricSample(
        original_bytes=record.original_bytes,
        archive_bytes=int(record.archive_bytes),
        compression_wall_ns=int(record.compression_wall_ns),
        compression_cpu_ns=int(record.compression_cpu_ns),
        decompression_wall_ns=int(record.decompression_wall_ns),
        decompression_cpu_ns=int(record.decompression_cpu_ns),
        peak_rss_bytes=int(record.peak_rss_bytes),
        compression_peak_rss_bytes=int(
            record.compression_peak_rss_bytes
        ),
        decompression_peak_rss_bytes=int(
            record.decompression_peak_rss_bytes
        ),
        literal_only_archive_bytes=record.literal_only_archive_bytes,
        pre_entropy_archive_bytes=record.pre_entropy_archive_bytes,
        function_reconstructed_bytes=record.function_reconstructed_bytes,
        literal_reconstructed_bytes=record.literal_reconstructed_bytes,
    )


def _aggregate_sample(records: Sequence[TrialRecord]) -> MetricSample:
    if not records or any(record.status != "ok" for record in records):
        raise ProfilingError("corpus samples require only successful trials")
    native = records[0].native_mathsvg
    if any(record.native_mathsvg != native for record in records):
        raise ProfilingError("corpus sample mixes native and external rows")

    def optional_sum(field: str) -> int | None:
        values = [getattr(record, field) for record in records]
        if any(value is None for value in values):
            return None
        return sum(int(value) for value in values)

    return MetricSample(
        original_bytes=sum(record.original_bytes for record in records),
        archive_bytes=sum(int(record.archive_bytes) for record in records),
        compression_wall_ns=sum(
            int(record.compression_wall_ns) for record in records
        ),
        compression_cpu_ns=sum(
            int(record.compression_cpu_ns) for record in records
        ),
        decompression_wall_ns=sum(
            int(record.decompression_wall_ns) for record in records
        ),
        decompression_cpu_ns=sum(
            int(record.decompression_cpu_ns) for record in records
        ),
        peak_rss_bytes=max(int(record.peak_rss_bytes) for record in records),
        compression_peak_rss_bytes=max(
            int(record.compression_peak_rss_bytes) for record in records
        ),
        decompression_peak_rss_bytes=max(
            int(record.decompression_peak_rss_bytes) for record in records
        ),
        literal_only_archive_bytes=optional_sum(
            "literal_only_archive_bytes"
        ),
        pre_entropy_archive_bytes=optional_sum(
            "pre_entropy_archive_bytes"
        ),
        function_reconstructed_bytes=optional_sum(
            "function_reconstructed_bytes"
        ),
        literal_reconstructed_bytes=optional_sum(
            "literal_reconstructed_bytes"
        ),
    )


def _median(
    samples: Sequence[MetricSample],
    value: Callable[[MetricSample], float | int | None],
) -> float | int | None:
    values = [result for sample in samples if (result := value(sample)) is not None]
    return statistics.median(values) if values else None


def _positive_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _throughput_mb_s(original_bytes: int, wall_ns: int) -> float | None:
    return (
        original_bytes * 1000.0 / wall_ns
        if wall_ns > 0
        else None
    )


def _procedural_status(
    *,
    native: bool,
    samples: Sequence[MetricSample],
) -> str:
    if not native:
        return "not-applicable"
    counterfactual = sum(
        sample.has_counterfactual_evidence for sample in samples
    )
    structural = sum(sample.has_structural_evidence for sample in samples)
    if (
        counterfactual == len(samples)
        and structural == len(samples)
        and samples
    ):
        return "exact"
    if structural == len(samples) and samples and counterfactual == 0:
        return "structural-only"
    if counterfactual or structural:
        return "partial"
    return "not-measured"


def _status_counts(records: Sequence[TrialRecord]) -> Counter[str]:
    return Counter(record.status for record in records)


def _profile_row(
    key: tuple[object, ...],
    dataset_id: str,
    row_scope: str,
    original_bytes: int,
    records: Sequence[TrialRecord],
    samples: Sequence[MetricSample],
) -> dict[str, object]:
    group = _group_values(key)
    counts = _status_counts(records)
    successful = counts["ok"]
    metric_status = (
        "complete"
        if records and successful == len(records)
        else ("partial" if successful else "not-measured")
    )
    procedural_status = _procedural_status(
        native=bool(group["native_mathsvg"]),
        samples=samples,
    )

    def counterfactual_median(
        value: Callable[[MetricSample], float | int | None],
    ) -> float | int | None:
        return _median(
            [
                sample
                for sample in samples
                if sample.has_counterfactual_evidence
            ],
            value,
        )

    def structural_median(
        value: Callable[[MetricSample], float | int | None],
    ) -> float | int | None:
        return _median(
            [
                sample
                for sample in samples
                if sample.has_structural_evidence
            ],
            value,
        )

    return {
        "evidence_scope": EVIDENCE_SCOPE,
        **group,
        "dataset_id": dataset_id,
        "row_scope": row_scope,
        "original_bytes": original_bytes,
        "scheduled_measured_trials": len(records),
        "successful_trials": successful,
        "failed_trials": counts["failed"],
        "timeout_trials": counts["timeout"],
        "unavailable_trials": counts["unavailable"],
        "complete_repetitions": len(samples),
        "metric_status": metric_status,
        "roundtrip_all_successes": bool(samples)
        and all(record.roundtrip_ok for record in records if record.status == "ok"),
        "deterministic_archive_all_successes": bool(samples)
        and all(
            record.deterministic_archive
            for record in records
            if record.status == "ok"
        ),
        "archive_bytes_median": _median(
            samples, lambda sample: sample.archive_bytes
        ),
        "archive_fraction_median": _median(
            samples,
            lambda sample: _positive_ratio(
                sample.archive_bytes, sample.original_bytes
            ),
        ),
        "compression_ratio_median": _median(
            samples,
            lambda sample: _positive_ratio(
                sample.original_bytes, sample.archive_bytes
            ),
        ),
        "compression_wall_ns_median": _median(
            samples, lambda sample: sample.compression_wall_ns
        ),
        "compression_cpu_ns_median": _median(
            samples, lambda sample: sample.compression_cpu_ns
        ),
        "compression_cpu_wall_fraction_median": _median(
            samples,
            lambda sample: _positive_ratio(
                sample.compression_cpu_ns,
                sample.compression_wall_ns,
            ),
        ),
        "decompression_wall_ns_median": _median(
            samples, lambda sample: sample.decompression_wall_ns
        ),
        "decompression_cpu_ns_median": _median(
            samples, lambda sample: sample.decompression_cpu_ns
        ),
        "decompression_cpu_wall_fraction_median": _median(
            samples,
            lambda sample: _positive_ratio(
                sample.decompression_cpu_ns,
                sample.decompression_wall_ns,
            ),
        ),
        "compression_throughput_mb_s_median": _median(
            samples,
            lambda sample: _throughput_mb_s(
                sample.original_bytes,
                sample.compression_wall_ns,
            ),
        ),
        "decompression_throughput_mb_s_median": _median(
            samples,
            lambda sample: _throughput_mb_s(
                sample.original_bytes,
                sample.decompression_wall_ns,
            ),
        ),
        "peak_rss_bytes_median": _median(
            samples, lambda sample: sample.peak_rss_bytes
        ),
        "compression_peak_rss_bytes_median": _median(
            samples, lambda sample: sample.compression_peak_rss_bytes
        ),
        "decompression_peak_rss_bytes_median": _median(
            samples, lambda sample: sample.decompression_peak_rss_bytes
        ),
        "procedural_evidence_status": procedural_status,
        "literal_only_archive_bytes_median": counterfactual_median(
            lambda sample: sample.literal_only_archive_bytes
        ),
        "pre_entropy_archive_bytes_median": counterfactual_median(
            lambda sample: sample.pre_entropy_archive_bytes
        ),
        "procedural_gain_before_entropy_bytes_median": counterfactual_median(
            lambda sample: max(
                int(sample.literal_only_archive_bytes)
                - int(sample.pre_entropy_archive_bytes),
                0,
            )
        ),
        "procedural_penalty_before_entropy_bytes_median": counterfactual_median(
            lambda sample: max(
                int(sample.pre_entropy_archive_bytes)
                - int(sample.literal_only_archive_bytes),
                0,
            )
        ),
        "entropy_gain_bytes_median": counterfactual_median(
            lambda sample: max(
                int(sample.pre_entropy_archive_bytes)
                - sample.archive_bytes,
                0,
            )
        ),
        "entropy_penalty_bytes_median": counterfactual_median(
            lambda sample: max(
                sample.archive_bytes
                - int(sample.pre_entropy_archive_bytes),
                0,
            )
        ),
        "function_reconstructed_bytes_median": structural_median(
            lambda sample: sample.function_reconstructed_bytes
        ),
        "literal_reconstructed_bytes_median": structural_median(
            lambda sample: sample.literal_reconstructed_bytes
        ),
        "procedural_coverage_median": structural_median(
            lambda sample: _positive_ratio(
                int(sample.function_reconstructed_bytes),
                sample.original_bytes,
            ),
        ),
        "literal_fraction_median": structural_median(
            lambda sample: _positive_ratio(
                int(sample.literal_reconstructed_bytes),
                sample.original_bytes,
            ),
        ),
    }


def _validate_input(records: Sequence[TrialRecord]) -> list[TrialRecord]:
    if not records:
        raise ProfilingError("raw JSONL is empty")
    if any(record.split != EVIDENCE_SCOPE for record in records):
        splits = sorted({record.split for record in records})
        raise ProfilingError(
            "this pipeline accepts development evidence only; "
            f"observed splits={splits}"
        )
    measured = [record for record in records if not record.warmup]
    if not measured:
        raise ProfilingError("raw JSONL has no measured trials")
    identities = {
        tuple(getattr(record, field) for field in RUN_IDENTITY_FIELDS)
        for record in measured
    }
    if len(identities) != 1:
        raise ProfilingError(
            "one artifact set must contain exactly one run identity"
        )

    input_identities: dict[
        tuple[str, str], tuple[str, str, int]
    ] = {}
    coordinates: set[tuple[object, ...]] = set()
    for record in measured:
        input_key = (record.split, record.dataset_id)
        input_identity = (
            record.input_path,
            record.input_sha256,
            record.original_bytes,
        )
        previous = input_identities.setdefault(input_key, input_identity)
        if previous != input_identity:
            raise ProfilingError(
                f"{record.dataset_id}: inconsistent input identity"
            )
        coordinate = (
            *_group_key(record),
            record.dataset_id,
            record.repetition,
        )
        if coordinate in coordinates:
            raise ProfilingError(
                "duplicate measured codec/dataset repetition coordinate"
            )
        coordinates.add(coordinate)
    return measured


def build_profile_rows(
    records: Sequence[TrialRecord],
) -> list[dict[str, object]]:
    measured = _validate_input(records)
    grouped: dict[
        tuple[object, ...], dict[str, list[TrialRecord]]
    ] = defaultdict(lambda: defaultdict(list))
    for record in measured:
        grouped[_group_key(record)][record.dataset_id].append(record)

    rows: list[dict[str, object]] = []
    for key in sorted(grouped):
        datasets = grouped[key]
        for dataset_id in sorted(datasets):
            group = sorted(datasets[dataset_id], key=lambda row: row.repetition)
            successful_samples = [
                _sample(record) for record in group if record.status == "ok"
            ]
            rows.append(
                _profile_row(
                    key,
                    dataset_id,
                    "file",
                    group[0].original_bytes,
                    group,
                    successful_samples,
                )
            )

        all_records = [
            record
            for dataset_id in sorted(datasets)
            for record in datasets[dataset_id]
        ]
        expected_datasets = set(datasets)
        by_repetition: dict[int, dict[str, TrialRecord]] = defaultdict(dict)
        for record in all_records:
            by_repetition[record.repetition][record.dataset_id] = record
        aggregate_samples = [
            _aggregate_sample(
                [repetition_rows[name] for name in sorted(expected_datasets)]
            )
            for repetition_rows in (
                by_repetition[repetition]
                for repetition in sorted(by_repetition)
            )
            if set(repetition_rows) == expected_datasets
            and all(
                record.status == "ok"
                for record in repetition_rows.values()
            )
        ]
        rows.append(
            _profile_row(
                key,
                "__aggregate__",
                "corpus",
                sum(group[0].original_bytes for group in datasets.values()),
                all_records,
                aggregate_samples,
            )
        )
    return rows


def _format_number(value: float, digits: int = 3) -> str:
    if not math.isfinite(value):
        raise ProfilingError("plot value must be finite")
    rendered = f"{value:.{digits}f}"
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered if rendered and rendered != "-0" else "0"


def _codec_label(row: Mapping[str, object]) -> str:
    if row["native_mathsvg"]:
        profile = str(row["profile"]).capitalize()
        return f"MathSVG {profile} · {row['threads']}t"
    config = str(row["codec_config"])
    return f"{config} · {row['threads']}t"


def _codec_color(row: Mapping[str, object]) -> str:
    config = str(row["codec_config"])
    if config in CODEC_COLORS:
        return CODEC_COLORS[config]
    label = _codec_label(row).encode("utf-8")
    index = hashlib.sha256(label).digest()[0] % len(PALETTE)
    return PALETTE[index]


def _svg_bar_chart(
    title: str,
    axis_label: str,
    items: Sequence[BarItem],
    axis_format: Callable[[float], str],
    *,
    note: str,
) -> str:
    ordered = list(items)
    width = 960
    left = min(
        360,
        max(225, 24 + max((len(item.label) for item in ordered), default=0) * 6),
    )
    right = 120
    top = 105
    row_height = 42
    bottom = 58
    height = top + max(len(ordered), 1) * row_height + bottom
    plot_width = width - left - right
    measured = [
        item.value
        for item in ordered
        if item.value is not None and math.isfinite(item.value)
    ]
    maximum = max(measured, default=0.0)
    scale_max = maximum if maximum > 0 else 1.0
    lines = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="title desc">'
        ),
        f"  <title id=\"title\">{html.escape(title)}</title>",
        (
            '  <desc id="desc">Development evidence only; warm-ups excluded; '
            "missing measurements are not replaced by zero.</desc>"
        ),
        (
            "  <metadata>MathSVG development evidence; deterministic "
            "code-native SVG; measured successful TrialRecord rows only.</metadata>"
        ),
        '  <rect width="100%" height="100%" fill="#ffffff"/>',
        (
            f'  <text x="24" y="34" font-family="sans-serif" '
            f'font-size="20" font-weight="700" fill="#17202a">'
            f"{html.escape(title)}</text>"
        ),
        (
            f'  <text x="24" y="58" font-family="sans-serif" '
            f'font-size="12" fill="#52606d">'
            "DEVELOPMENT EVIDENCE · measured rows only · warm-ups excluded"
            "</text>"
        ),
        (
            f'  <text x="24" y="78" font-family="sans-serif" '
            f'font-size="11" fill="#6b7280">{html.escape(note)}</text>'
        ),
    ]
    for tick in range(5):
        fraction = tick / 4
        x = left + plot_width * fraction
        value = scale_max * fraction
        lines.extend(
            [
                (
                    f'  <line x1="{_format_number(x)}" y1="{top - 12}" '
                    f'x2="{_format_number(x)}" '
                    f'y2="{height - bottom + 4}" stroke="#e5e7eb" '
                    'stroke-width="1"/>'
                ),
                (
                    f'  <text x="{_format_number(x)}" y="{height - 22}" '
                    'text-anchor="middle" font-family="sans-serif" '
                    f'font-size="10" fill="#6b7280">'
                    f"{html.escape(axis_format(value))}</text>"
                ),
            ]
        )
    lines.append(
        (
            f'  <text x="{left + plot_width / 2:.3f}" y="{height - 4}" '
            'text-anchor="middle" font-family="sans-serif" '
            f'font-size="11" fill="#374151">{html.escape(axis_label)}</text>'
        )
    )
    if not ordered:
        lines.append(
            (
                f'  <text x="{width / 2:.1f}" y="{top + 24}" '
                'text-anchor="middle" font-family="sans-serif" '
                'font-size="14" fill="#6b7280">not measured</text>'
            )
        )
    for index, item in enumerate(ordered):
        y = top + index * row_height
        lines.append(
            (
                f'  <text x="{left - 12}" y="{y + 18}" text-anchor="end" '
                'font-family="sans-serif" font-size="11" fill="#1f2937">'
                f"{html.escape(item.label)}</text>"
            )
        )
        if item.value is None:
            lines.extend(
                [
                    (
                        f'  <rect x="{left}" y="{y + 5}" width="{plot_width}" '
                        'height="18" rx="3" fill="#f3f4f6"/>'
                    ),
                    (
                        f'  <text x="{left + 8}" y="{y + 18}" '
                        'font-family="sans-serif" font-size="10" '
                        'fill="#6b7280">not measured</text>'
                    ),
                ]
            )
            continue
        bar_width = plot_width * item.value / scale_max
        lines.extend(
            [
                (
                    f'  <rect x="{left}" y="{y + 5}" '
                    f'width="{_format_number(bar_width)}" height="18" '
                    f'rx="3" fill="{item.color}"/>'
                ),
                (
                    f'  <text x="{left + bar_width + 7:.3f}" y="{y + 18}" '
                    'font-family="sans-serif" font-size="10" fill="#111827">'
                    f"{html.escape(item.display_value)}</text>"
                ),
            ]
        )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def build_svgs(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    corpus = sorted(
        (row for row in rows if row["row_scope"] == "corpus"),
        key=_codec_label,
    )

    def items_for(
        field: str,
        display: Callable[[float], str],
        convert: Callable[[float], float] = lambda value: value,
    ) -> list[BarItem]:
        result: list[BarItem] = []
        for row in corpus:
            raw = row[field]
            value = None if raw is None or raw == "" else convert(float(raw))
            result.append(
                BarItem(
                    label=_codec_label(row),
                    value=value,
                    display_value="not measured" if value is None else display(value),
                    color=_codec_color(row),
                )
            )
        return result

    charts = {
        "size-ratio": _svg_bar_chart(
            "Development pilot — archive fraction",
            "archive bytes / original bytes · lower is better",
            items_for(
                "archive_fraction_median",
                lambda value: f"{_format_number(value, 4)}×",
            ),
            lambda value: f"{_format_number(value, 2)}×",
            note="Corpus medians use only complete, successful repetitions.",
        ),
        "compression-throughput": _svg_bar_chart(
            "Development pilot — compression throughput",
            "MB/s (decimal) · higher is better",
            items_for(
                "compression_throughput_mb_s_median",
                lambda value: f"{_format_number(value, 2)} MB/s",
            ),
            lambda value: _format_number(value, 1),
            note="Wall-clock throughput includes process-wrapper overhead.",
        ),
        "decompression-throughput": _svg_bar_chart(
            "Development pilot — decompression throughput",
            "MB/s (decimal) · higher is better",
            items_for(
                "decompression_throughput_mb_s_median",
                lambda value: f"{_format_number(value, 2)} MB/s",
            ),
            lambda value: _format_number(value, 1),
            note="Every successful decode was SHA-256 verified.",
        ),
        "peak-rss": _svg_bar_chart(
            "Development pilot — peak RSS",
            "MiB · lower is better",
            items_for(
                "peak_rss_bytes_median",
                lambda value: f"{_format_number(value, 2)} MiB",
                lambda value: value / (1024 * 1024),
            ),
            lambda value: _format_number(value, 1),
            note="Corpus RSS is max(file peak) within each repetition.",
        ),
    }
    procedural_items: list[BarItem] = []
    procedural_fields = (
        (
            "procedural_gain_before_entropy_bytes_median",
            "procedural gain",
            "#2a9d8f",
        ),
        (
            "entropy_gain_bytes_median",
            "entropy gain",
            "#457b9d",
        ),
        (
            "procedural_penalty_before_entropy_bytes_median",
            "procedural penalty",
            "#e76f51",
        ),
        (
            "entropy_penalty_bytes_median",
            "entropy penalty",
            "#b56576",
        ),
    )
    for row in corpus:
        if not row["native_mathsvg"]:
            continue
        for field, label, color in procedural_fields:
            raw = row[field]
            value = None if raw is None or raw == "" else float(raw)
            procedural_items.append(
                BarItem(
                    label=f"{_codec_label(row)} · {label}",
                    value=value,
                    display_value=(
                        "not measured"
                        if value is None
                        else f"{_format_number(value, 0)} B"
                    ),
                    color=color,
                )
            )
    charts["procedural-entropy"] = _svg_bar_chart(
        "Development pilot — procedural vs entropy attribution",
        "exact counterfactual bytes",
        procedural_items,
        lambda value: f"{_format_number(value / 1000, 0)}k",
        note=(
            "Gains and penalties are separate; exact measured zero remains "
            "zero, missing evidence remains not measured."
        ),
    )
    return charts


def _atomic_text(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = pathlib.Path(handle.name)
            handle.write(content)
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


def write_artifacts(
    rows: Sequence[Mapping[str, object]],
    *,
    profiling_output: pathlib.Path,
    plots_directory: pathlib.Path,
    artifact_prefix: str,
) -> list[pathlib.Path]:
    if (
        not artifact_prefix
        or artifact_prefix in {".", ".."}
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in artifact_prefix
        )
    ):
        raise ProfilingError("artifact prefix contains unsafe characters")
    write_csv(profiling_output, PROFILE_FIELDS, rows)
    outputs = [profiling_output]
    for suffix, content in build_svgs(rows).items():
        path = plots_directory / f"{artifact_prefix}-{suffix}.svg"
        _atomic_text(path, content)
        outputs.append(path)
    return outputs


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_jsonl", type=pathlib.Path)
    parser.add_argument(
        "--profiling-output",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/profiling/benchmark.csv"),
    )
    parser.add_argument(
        "--plots-directory",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/plots"),
    )
    parser.add_argument("--artifact-prefix", default="benchmark")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        records = read_jsonl(args.raw_jsonl)
        rows = build_profile_rows(records)
        outputs = write_artifacts(
            rows,
            profiling_output=args.profiling_output,
            plots_directory=args.plots_directory,
            artifact_prefix=args.artifact_prefix,
        )
    except (OSError, ProfilingError, ValueError) as exc:
        print(f"profiling error: {exc}", file=sys.stderr)
        return 2
    print(
        f"wrote {len(rows)} profiling rows and {len(outputs) - 1} SVG plots"
    )
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
