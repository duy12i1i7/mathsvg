"""Generate benchmark figures, skipping unavailable metrics transparently."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .common import atomic_write_json, canonical_json_sha256, sha256_file
from .verification import resolve_results_path


def _plot_provenance(document: Mapping[str, Any]) -> dict[str, str]:
    generator = Path(__file__).resolve()
    return {
        "result_document_sha256": canonical_json_sha256(document),
        "result_document_hash_method": "canonical-json-v1",
        "plot_generator": "python/mathzip_bench/plots.py",
        "plot_generator_sha256": sha256_file(generator),
    }


def _plot_artifact_hashes(output: Path, generated: Sequence[str]) -> dict[str, str]:
    names = sorted({"plot_data.json", *generated})
    return {name: sha256_file(output / name) for name in names}


def load_results(path: Path) -> dict[str, Any]:
    resolved = resolve_results_path(path)
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        raise ValueError("invalid benchmark result document")
    return value


def aggregate_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[
            (
                str(row.get("corpus")),
                str(row.get("codec")),
                int(row.get("threads") or 1),
            )
        ].append(row)
    aggregates: list[dict[str, Any]] = []
    for (corpus, codec, threads), values in sorted(groups.items()):
        successful = [row for row in values if row.get("status") == "ok"]
        original = sum(int(row["original_bytes"]) for row in successful)
        compressed = sum(int(row["compressed_bytes"]) for row in successful)
        compression_time = sum(
            float(row["compression_seconds"]) for row in successful
        )
        decompression_time = sum(
            float(row["decompression_seconds"]) for row in successful
        )
        aggregates.append(
            {
                "profile": str(values[0].get("profile") or "unknown"),
                "corpus": corpus,
                "codec": codec,
                "threads": threads,
                "successful_rows": len(successful),
                "failed_rows": len(values) - len(successful),
                "original_bytes": original,
                "compressed_bytes": compressed,
                "ratio": original / compressed
                if successful and compressed
                else None,
                "bits_per_byte": 8.0 * compressed / original
                if successful and original
                else None,
                "compression_mbps": original / 1_000_000 / compression_time
                if successful and compression_time
                else None,
                "decompression_mbps": original / 1_000_000 / decompression_time
                if successful and decompression_time
                else None,
                "peak_rss_bytes": max(
                    (
                        int(row["peak_rss_bytes"])
                        for row in successful
                        if row.get("peak_rss_bytes") is not None
                    ),
                    default=None,
                ),
            }
        )
    return aggregates


def _evidence_counts(rows: Sequence[Mapping[str, Any]]) -> tuple[str, int, int]:
    profiles = sorted(
        {
            str(row.get("profile"))
            for row in rows
            if row.get("profile") is not None
        }
    )
    if rows and all(
        "successful_rows" in row and "failed_rows" in row for row in rows
    ):
        successful = sum(int(row.get("successful_rows") or 0) for row in rows)
        failed = sum(int(row.get("failed_rows") or 0) for row in rows)
    else:
        successful = sum(row.get("status") == "ok" for row in rows)
        failed = len(rows) - successful
    return ",".join(profiles) or "unknown", successful, failed


def _stamp_figure(figure: Any, rows: Sequence[Mapping[str, Any]]) -> None:
    profile, successful, failed = _evidence_counts(rows)
    figure.text(
        0.01,
        0.005,
        f"profile={profile}; successful result rows={successful}; failed/unavailable={failed}",
        fontsize=7,
        color="#444444",
    )


def _grouped_bar(
    plt: Any,
    data: Sequence[Mapping[str, Any]],
    metric: str,
    ylabel: str,
    title: str,
    destination: Path,
) -> bool:
    valid = [row for row in data if row.get(metric) is not None]
    if not valid:
        return False
    corpora = sorted({str(row["corpus"]) for row in valid})
    series = sorted(
        {
            (str(row["codec"]), int(row.get("threads") or 1))
            for row in valid
        }
    )
    width = 0.8 / max(1, len(series))
    figure, axis = plt.subplots(figsize=(max(8, len(corpora) * 1.4), 5.5))
    for series_index, (codec, threads) in enumerate(series):
        lookup = {
            str(row["corpus"]): float(row[metric])
            for row in valid
            if row["codec"] == codec
            and int(row.get("threads") or 1) == threads
        }
        positions = [
            index - 0.4 + width / 2 + series_index * width
            for index in range(len(corpora))
        ]
        axis.bar(
            positions,
            [lookup.get(corpus, math.nan) for corpus in corpora],
            width,
            label=f"{codec} (t{threads})",
        )
    axis.set_xticks(range(len(corpora)), corpora, rotation=30, ha="right")
    axis.set_ylabel(ylabel)
    axis.set_title(title)
    axis.grid(axis="y", alpha=0.25)
    axis.legend(fontsize="small", ncols=max(1, min(3, len(series))))
    _stamp_figure(figure, data)
    figure.tight_layout()
    figure.savefig(destination, dpi=160)
    plt.close(figure)
    return True


def _scatter(
    plt: Any,
    data: Sequence[Mapping[str, Any]],
    x: str,
    y: str,
    xlabel: str,
    ylabel: str,
    title: str,
    destination: Path,
) -> bool:
    valid = [
        row
        for row in data
        if row.get(x) is not None
        and row.get(y) is not None
        and float(row[x]) > 0
        and float(row[y]) > 0
    ]
    if not valid:
        return False
    figure, axis = plt.subplots(figsize=(7.5, 5.5))
    for codec, threads in sorted(
        {
            (str(row["codec"]), int(row.get("threads") or 1))
            for row in valid
        }
    ):
        selected = [
            row
            for row in valid
            if row["codec"] == codec
            and int(row.get("threads") or 1) == threads
        ]
        axis.scatter(
            [row[x] for row in selected],
            [row[y] for row in selected],
            label=f"{codec} (t{threads})",
            alpha=0.8,
        )
    # A point is nondominated only against codecs measured on the same corpus
    # and thread count. Connecting those points makes this a real Pareto view,
    # while the scatter still exposes every dominated result.
    for corpus, threads in sorted(
        {(str(row["corpus"]), int(row.get("threads") or 1)) for row in valid}
    ):
        candidates = [
            row
            for row in valid
            if str(row["corpus"]) == corpus
            and int(row.get("threads") or 1) == threads
        ]
        frontier = [
            row
            for row in candidates
            if not any(
                float(other[x]) >= float(row[x])
                and float(other[y]) >= float(row[y])
                and (
                    float(other[x]) > float(row[x])
                    or float(other[y]) > float(row[y])
                )
                for other in candidates
            )
        ]
        frontier.sort(key=lambda row: float(row[x]))
        if frontier:
            axis.plot(
                [row[x] for row in frontier],
                [row[y] for row in frontier],
                linewidth=1.1,
                linestyle="--",
                alpha=0.65,
                label=f"{corpus}/t{threads} Pareto",
            )
    axis.set_xscale("log")
    axis.set_xlabel(xlabel)
    axis.set_ylabel(ylabel)
    axis.set_title(title)
    axis.grid(alpha=0.25)
    axis.legend(fontsize="small")
    _stamp_figure(figure, data)
    figure.tight_layout()
    figure.savefig(destination, dpi=160)
    plt.close(figure)
    return True


def _storage_breakdown(plt: Any, rows: Sequence[Mapping[str, Any]], path: Path) -> bool:
    fields = [
        ("model_parameter_bytes", "model parameters"),
        ("partition_metadata_bytes", "partition metadata"),
        ("residual_bytes", "residual"),
        ("container_overhead_bytes", "container overhead"),
    ]
    selected = [
        row
        for row in rows
        if row.get("status") == "ok"
        and row.get("codec_family") == "mathzip"
        and any(row.get(field) is not None for field, _ in fields)
    ]
    if not selected:
        return False
    grouped: dict[tuple[str, str, int], dict[str, float]] = {}
    for row in selected:
        key = (
            str(row["corpus"]),
            str(row["codec"]),
            int(row.get("threads") or 1),
        )
        totals = grouped.setdefault(key, {field: 0.0 for field, _ in fields})
        for field, _ in fields:
            value = row.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[field] += float(value)
    group_keys = sorted(grouped)
    labels = [
        f"{corpus}/{codec}/t{threads}"
        for corpus, codec, threads in group_keys
    ]
    figure, axis = plt.subplots(figsize=(max(9, len(labels) * 0.55), 5.5))
    bottom = [0.0] * len(grouped)
    for field, display in fields:
        values = [grouped[key][field] for key in group_keys]
        axis.bar(range(len(grouped)), values, bottom=bottom, label=display)
        bottom = [left + value for left, value in zip(bottom, values)]
    axis.set_xticks(range(len(labels)), labels, rotation=65, ha="right", fontsize=7)
    axis.set_ylabel("Bytes")
    axis.set_title("MathZip storage breakdown by corpus/configuration")
    axis.legend(fontsize="small")
    _stamp_figure(figure, rows)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)
    return True


def _model_distribution(plt: Any, rows: Sequence[Mapping[str, Any]], path: Path) -> bool:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        distribution = row.get("model_distribution")
        if row.get("status") == "ok" and isinstance(distribution, dict):
            for name, count in distribution.items():
                if isinstance(count, (int, float)):
                    totals[str(name)] += float(count)
    if not totals:
        return False
    figure, axis = plt.subplots(figsize=(8, 5))
    names = sorted(totals, key=totals.get, reverse=True)
    axis.bar(names, [totals[name] for name in names])
    axis.set_ylabel("Segments")
    axis.set_title("MathZip model type distribution")
    axis.tick_params(axis="x", rotation=45)
    _stamp_figure(figure, rows)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)
    return True


def _segment_histogram(plt: Any, rows: Sequence[Mapping[str, Any]], path: Path) -> bool:
    values = [
        float(row["median_segment_size"])
        for row in rows
        if row.get("status") == "ok"
        and row.get("median_segment_size") is not None
        and float(row["median_segment_size"]) > 0
    ]
    if not values:
        return False
    figure, axis = plt.subplots(figsize=(7, 5))
    axis.hist(values, bins=min(30, max(5, round(math.sqrt(len(values))))))
    axis.set_xscale("log")
    axis.set_xlabel("Median segment size (bytes, log scale)")
    axis.set_ylabel("Files")
    axis.set_title("MathZip segment size distribution")
    _stamp_figure(figure, rows)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)
    return True


def _residual_entropy(plt: Any, rows: Sequence[Mapping[str, Any]], path: Path) -> bool:
    selected = [
        row
        for row in rows
        if row.get("status") == "ok"
        and row.get("estimated_residual_entropy") is not None
        and row.get("actual_residual_coded_bytes") is not None
    ]
    if not selected:
        return False
    figure, axis = plt.subplots(figsize=(7, 5))
    axis.scatter(
        [row["estimated_residual_entropy"] for row in selected],
        [row["actual_residual_coded_bytes"] for row in selected],
    )
    axis.set_xlabel("Estimated residual entropy (bits/byte)")
    axis.set_ylabel("Actual residual coded bytes")
    axis.set_title("Residual entropy versus encoded size")
    axis.grid(alpha=0.25)
    _stamp_figure(figure, rows)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)
    return True


def _ablation(
    plt: Any, rows: Sequence[Mapping[str, Any]], path: Path
) -> tuple[bool, dict[str, Any]]:
    grouped: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        variant = row.get("variant")
        if variant:
            grouped[(str(variant), int(row.get("threads") or 1))].append(row)
    series = []
    for key in sorted(grouped):
        successful = [
            row
            for row in grouped[key]
            if row.get("status") == "ok" and row.get("ratio") is not None
        ]
        series.append(
            {
                "variant": key[0],
                "threads": key[1],
                "successful_rows": len(successful),
                "total_rows": len(grouped[key]),
                "median_successful_ratio": (
                    statistics.median(float(row["ratio"]) for row in successful)
                    if successful
                    else None
                ),
            }
        )
    metadata = {
        "aggregation": "median ratio among successful rows",
        "coverage": series,
    }
    if len(series) < 2:
        return False, metadata
    labels = [
        (
            f"{entry['variant']} "
            f"(t{entry['threads']}; {entry['successful_rows']}/{entry['total_rows']} ok)"
        )
        for entry in series
    ]
    values = [
        (
            float(entry["median_successful_ratio"])
            if entry["median_successful_ratio"] is not None
            else 0.0
        )
        for entry in series
    ]
    figure, axis = plt.subplots(figsize=(max(8, len(labels) * 0.6), 5))
    bars = axis.bar(labels, values)
    for bar, entry in zip(bars, series):
        failed = entry["total_rows"] - entry["successful_rows"]
        if failed:
            bar.set_color("#fdae61")
            bar.set_hatch("//")
            axis.annotate(
                (
                    f"{failed} failed"
                    if entry["successful_rows"]
                    else f"{failed} failed; no ratio"
                ),
                xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=7,
            )
    axis.set_ylabel("Median ratio (successful files)")
    axis.set_title("Ablation comparison (coverage shown per variant)")
    axis.tick_params(axis="x", rotation=65)
    _stamp_figure(figure, rows)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)
    return True, metadata


def _synthetic_noise(plt: Any, rows: Sequence[Mapping[str, Any]], path: Path) -> bool:
    selected = [
        row
        for row in rows
        if row.get("status") == "ok"
        and isinstance(row.get("tags"), dict)
        and row["tags"].get("noise_density") is not None
        and row.get("ratio") is not None
    ]
    if not selected:
        return False
    figure, axis = plt.subplots(figsize=(8, 5.5))
    keys = sorted(
        {
            (
                str(row["codec"]),
                int(row.get("threads") or 1),
                str(row["tags"].get("family")),
            )
            for row in selected
        }
    )
    for codec, threads, family in keys:
        points: dict[float, list[float]] = defaultdict(list)
        for row in selected:
            if (
                row["codec"] == codec
                and int(row.get("threads") or 1) == threads
                and row["tags"].get("family") == family
            ):
                points[float(row["tags"]["noise_density"])].append(float(row["ratio"]))
        xs = sorted(points)
        axis.plot(
            [value * 100 for value in xs],
            [statistics.median(points[value]) for value in xs],
            marker=".",
            label=f"{codec}/t{threads}/{family}",
            alpha=0.75,
        )
    axis.set_xlabel("Noise density (%)")
    axis.set_ylabel("Median compression ratio")
    axis.set_title("Synthetic noise density versus compression ratio")
    if len(keys) <= 16:
        axis.legend(fontsize=6, ncols=2)
    axis.grid(alpha=0.25)
    _stamp_figure(figure, rows)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)
    return True


def _comparison_rows(
    rows: Sequence[Mapping[str, Any]], baseline_family: str
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    successful = [row for row in rows if row.get("status") == "ok"]
    math_names = sorted(
        {str(row["codec"]) for row in successful if row.get("codec_family") == "mathzip"}
    )
    baseline_names = sorted(
        {
            str(row["codec"])
            for row in successful
            if row.get("codec_family") == baseline_family
        }
    )
    preferred_math = next(
        (
            candidate
            for candidate in (
                "mathzip-balanced",
                "ablation-19-balanced",
                "mathzip-fast",
                "ablation-18-fast",
                "mathzip-max",
                "ablation-20-max",
            )
            if candidate in math_names
        ),
        math_names[0] if math_names else None,
    )
    preferred_baseline = (
        f"{baseline_family}-default"
        if f"{baseline_family}-default" in baseline_names
        else baseline_names[0]
        if baseline_names
        else None
    )
    if not preferred_math or not preferred_baseline:
        return [], preferred_math, preferred_baseline
    lookup = {
        (row["input_path"], row.get("threads"), row["codec"]): row
        for row in successful
    }
    comparisons: list[dict[str, Any]] = []
    for row in successful:
        if row["codec"] != preferred_math:
            continue
        baseline = lookup.get(
            (row["input_path"], row.get("threads"), preferred_baseline)
        )
        if not baseline:
            continue
        baseline_bytes = float(baseline["compressed_bytes"])
        gain = (
            (baseline_bytes - float(row["compressed_bytes"])) / baseline_bytes * 100
            if baseline_bytes
            else None
        )
        comparisons.append(
            {
                "input_path": row["input_path"],
                "input_name": row["input_name"],
                "corpus": row["corpus"],
                "threads": int(row.get("threads") or 1),
                "entropy": row.get("input_entropy_bits_per_byte"),
                "gain_percent": gain,
                "mathzip_bytes": float(row["compressed_bytes"]),
                "baseline_bytes": baseline_bytes,
            }
        )
    return comparisons, preferred_math, preferred_baseline


def _gain_bar(
    plt: Any,
    rows: Sequence[Mapping[str, Any]],
    family: str,
    destination: Path,
) -> tuple[bool, dict[str, Any]]:
    comparisons, math_name, baseline_name = _comparison_rows(rows, family)
    valid = [row for row in comparisons if row["gain_percent"] is not None]
    metadata = {
        "mathzip_codec": math_name,
        "baseline_codec": baseline_name,
        "aggregation": "corpus/thread weighted total compressed bytes",
    }
    if not valid:
        return False, metadata
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        grouped[(str(row["corpus"]), int(row["threads"]))].append(row)
    group_keys = sorted(grouped)
    labels = [f"{corpus}/t{threads}" for corpus, threads in group_keys]
    values = []
    for key in group_keys:
        group = grouped[key]
        mathzip_bytes = sum(float(row["mathzip_bytes"]) for row in group)
        baseline_bytes = sum(float(row["baseline_bytes"]) for row in group)
        values.append(
            (baseline_bytes - mathzip_bytes) / baseline_bytes * 100.0
            if baseline_bytes
            else math.nan
        )
    figure, axis = plt.subplots(figsize=(max(8, len(grouped) * 0.7), 5.5))
    axis.bar(
        labels,
        values,
        color=["#2b8cbe" if value >= 0 else "#d7301f" for value in values],
    )
    axis.axhline(0, color="black", linewidth=0.8)
    axis.set_ylabel("Weighted MathZip size gain versus baseline (%)")
    axis.set_title(f"Corpus-weighted MathZip gain/loss versus {baseline_name}")
    axis.tick_params(axis="x", rotation=65, labelsize=7)
    _stamp_figure(figure, rows)
    figure.tight_layout()
    figure.savefig(destination, dpi=160)
    plt.close(figure)
    return True, metadata


def _entropy_gain(
    plt: Any, rows: Sequence[Mapping[str, Any]], destination: Path
) -> tuple[bool, dict[str, Any]]:
    comparisons, math_name, baseline_name = _comparison_rows(rows, "zstd")
    valid = [
        row
        for row in comparisons
        if row["gain_percent"] is not None and row["entropy"] is not None
    ]
    metadata = {"mathzip_codec": math_name, "baseline_codec": baseline_name}
    if not valid:
        return False, metadata
    figure, axis = plt.subplots(figsize=(7, 5))
    axis.scatter(
        [row["entropy"] for row in valid],
        [row["gain_percent"] for row in valid],
    )
    axis.axhline(0, color="black", linewidth=0.8)
    axis.set_xlabel("Input Shannon entropy (bits/byte)")
    axis.set_ylabel("MathZip size gain versus Zstd (%)")
    axis.set_title("File entropy versus MathZip gain")
    axis.grid(alpha=0.25)
    _stamp_figure(figure, rows)
    figure.tight_layout()
    figure.savefig(destination, dpi=160)
    plt.close(figure)
    return True, metadata


def generate_plots(
    document: Mapping[str, Any], output: Path, *, formats: Sequence[str] = ("png",)
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        atomic_write_json(
            output / "plot_data.json",
            {
                "run_id": document.get("run_id"),
                "aggregates": aggregate_rows(document.get("results", [])),
            },
        )
        manifest = {
            "schema_version": "mathzip-plot-manifest-v1",
            "status": "dependency_missing",
            "run_id": document.get("run_id"),
            "profile": document.get("profile"),
            "result_count": document.get("result_count"),
            "failure_count": document.get("failure_count"),
            "dependency": "matplotlib",
            "message": str(exc),
            "generated": [],
            "skipped": ["all plots"],
            "artifacts_sha256": _plot_artifact_hashes(output, []),
            **_plot_provenance(document),
        }
        atomic_write_json(output / "plot_manifest.json", manifest)
        return manifest

    rows = document.get("results", [])
    aggregates = aggregate_rows(rows)
    generated: list[str] = []
    skipped: list[str] = []
    metadata: dict[str, Any] = {}

    def render(
        stem: str,
        callback: Callable[[Path], bool | tuple[bool, dict[str, Any]]],
    ) -> None:
        made_any = False
        for extension in formats:
            destination = output / f"{stem}.{extension}"
            result = callback(destination)
            if isinstance(result, tuple):
                made, details = result
                metadata[stem] = details
            else:
                made = result
            if made:
                generated.append(destination.name)
                made_any = True
        if not made_any:
            skipped.append(stem)

    render(
        "compression_ratio_by_dataset",
        lambda path: _grouped_bar(
            plt,
            aggregates,
            "ratio",
            "Weighted compression ratio (original/compressed)",
            "Compression ratio by corpus",
            path,
        ),
    )
    render(
        "bits_per_byte_by_dataset",
        lambda path: _grouped_bar(
            plt,
            aggregates,
            "bits_per_byte",
            "Bits per original byte",
            "Bits per byte by corpus",
            path,
        ),
    )
    render(
        "compression_throughput",
        lambda path: _grouped_bar(
            plt,
            aggregates,
            "compression_mbps",
            "Compression MB/s",
            "Compression throughput",
            path,
        ),
    )
    render(
        "decompression_throughput",
        lambda path: _grouped_bar(
            plt,
            aggregates,
            "decompression_mbps",
            "Decompression MB/s",
            "Decompression throughput",
            path,
        ),
    )
    render(
        "ratio_vs_compression_speed",
        lambda path: _scatter(
            plt,
            aggregates,
            "compression_mbps",
            "ratio",
            "Compression MB/s (log scale)",
            "Weighted ratio",
            "Ratio versus compression speed",
            path,
        ),
    )
    render(
        "ratio_vs_decompression_speed",
        lambda path: _scatter(
            plt,
            aggregates,
            "decompression_mbps",
            "ratio",
            "Decompression MB/s (log scale)",
            "Weighted ratio",
            "Ratio versus decompression speed",
            path,
        ),
    )
    render(
        "peak_memory",
        lambda path: _grouped_bar(
            plt,
            [
                {
                    **row,
                    "peak_rss_mib": row["peak_rss_bytes"] / 1024**2
                    if row.get("peak_rss_bytes") is not None
                    else None,
                }
                for row in aggregates
            ],
            "peak_rss_mib",
            "Peak RSS (MiB)",
            "Peak memory",
            path,
        ),
    )
    render("storage_breakdown", lambda path: _storage_breakdown(plt, rows, path))
    render("model_distribution", lambda path: _model_distribution(plt, rows, path))
    render("segment_size_histogram", lambda path: _segment_histogram(plt, rows, path))
    render(
        "residual_entropy_vs_encoded_size",
        lambda path: _residual_entropy(plt, rows, path),
    )
    render("ablation_comparison", lambda path: _ablation(plt, rows, path))
    render(
        "synthetic_noise_vs_ratio", lambda path: _synthetic_noise(plt, rows, path)
    )
    render(
        "file_entropy_vs_mathzip_gain",
        lambda path: _entropy_gain(plt, rows, path),
    )
    render(
        "mathzip_gain_vs_zstd",
        lambda path: _gain_bar(plt, rows, "zstd", path),
    )
    render(
        "mathzip_gain_vs_xz",
        lambda path: _gain_bar(plt, rows, "xz", path),
    )
    atomic_write_json(
        output / "plot_data.json",
        {"run_id": document.get("run_id"), "aggregates": aggregates},
    )
    manifest = {
        "schema_version": "mathzip-plot-manifest-v1",
        "status": "generated",
        "run_id": document.get("run_id"),
        "profile": document.get("profile"),
        "result_count": document.get("result_count"),
        "failure_count": document.get("failure_count"),
        "generated": generated,
        "skipped_for_missing_metrics": sorted(set(skipped)),
        "comparison_choices": metadata,
        "artifacts_sha256": _plot_artifact_hashes(output, generated),
        **_plot_provenance(document),
    }
    atomic_write_json(output / "plot_manifest.json", manifest)
    return manifest
