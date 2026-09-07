"""Create an evidence-backed Markdown benchmark report from raw results."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .common import atomic_write_text, geometric_mean
from .plots import aggregate_rows


def _format_number(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return f"{value:,}"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _aggregate_table(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    aggregates = aggregate_rows(rows)
    failures: dict[tuple[str, str, int], int] = defaultdict(int)
    all_groups = {
        (
            str(row.get("corpus")),
            str(row.get("codec")),
            int(row.get("threads") or 1),
        )
        for row in rows
    }
    for row in rows:
        if row.get("status") != "ok":
            failures[
                (
                    str(row.get("corpus")),
                    str(row.get("codec")),
                    int(row.get("threads") or 1),
                )
            ] += 1
    lookup = {
        (row["corpus"], row["codec"], row["threads"]): row for row in aggregates
    }
    lines = [
        "| Corpus | Codec | Threads | Original bytes | Compressed bytes | Weighted ratio | Bits/byte | Compression MB/s | Decompression MB/s | Peak RSS MiB | Failures |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in sorted(all_groups):
        aggregate = lookup.get(key, {})
        peak = aggregate.get("peak_rss_bytes")
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape(key[0]),
                    _escape(key[1]),
                    str(key[2]),
                    _format_number(aggregate.get("original_bytes"), 0),
                    _format_number(aggregate.get("compressed_bytes"), 0),
                    _format_number(aggregate.get("ratio")),
                    _format_number(aggregate.get("bits_per_byte")),
                    _format_number(aggregate.get("compression_mbps")),
                    _format_number(aggregate.get("decompression_mbps")),
                    _format_number(peak / 1024**2 if peak is not None else None),
                    str(failures.get(key, 0)),
                ]
            )
            + " |"
        )
    return lines


def _per_file_statistics(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    groups: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for row in rows:
        if row.get("status") == "ok" and row.get("ratio") is not None:
            groups[
                (
                    str(row["corpus"]),
                    str(row["codec"]),
                    int(row.get("threads") or 1),
                )
            ].append(float(row["ratio"]))
    lines = [
        "| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for (corpus, codec, threads), values in sorted(groups.items()):
        lines.append(
            f"| {_escape(corpus)} | {_escape(codec)} | {threads} | {len(values)} | "
            f"{statistics.fmean(values):.3f} | "
            f"{_format_number(geometric_mean(values))} | "
            f"{statistics.median(values):.3f} |"
        )
    return lines


def _comparisons(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    successful = [row for row in rows if row.get("status") == "ok"]
    lookup = {
        (row["input_path"], row.get("threads"), row["codec"]): row
        for row in successful
    }
    preferred_math_name = next(
        (
            name
            for name in (
                "mathzip-balanced",
                "ablation-19-balanced",
                "mathzip-fast",
                "ablation-18-fast",
                "mathzip-max",
                "ablation-20-max",
            )
            if any(row["codec"] == name for row in successful)
        ),
        None,
    )
    math_names = (
        [preferred_math_name]
        if preferred_math_name
        else sorted(
            {
                str(row["codec"])
                for row in successful
                if row.get("codec_family") == "mathzip"
            }
        )
    )
    lines: list[str] = []
    if not math_names:
        return ["No successful MathZip rows were available for baseline comparisons."]
    canonical_levels = {
        "mathzip-fast": "fast",
        "mathzip-balanced": "default",
        "mathzip-max": "max",
        "ablation-18-fast": "fast",
        "ablation-19-balanced": "default",
        "ablation-20-max": "max",
    }
    for math_name in math_names:
        level = canonical_levels.get(math_name)
        if level is None:
            level = next(
                (
                    str(row.get("codec_level"))
                    for row in successful
                    if row["codec"] == math_name and row.get("codec_level")
                ),
                "default",
            )
            if level == "balanced":
                level = "default"
        for baseline in (f"zstd-{level}", f"xz-{level}"):
            gains: list[float] = []
            wins = ties = losses = 0
            for row in successful:
                if row["codec"] != math_name:
                    continue
                other = lookup.get((row["input_path"], row.get("threads"), baseline))
                if not other or not other.get("compressed_bytes"):
                    continue
                difference = int(other["compressed_bytes"]) - int(
                    row["compressed_bytes"]
                )
                gains.append(difference / int(other["compressed_bytes"]) * 100.0)
                if difference > 0:
                    wins += 1
                elif difference < 0:
                    losses += 1
                else:
                    ties += 1
            if gains:
                lines.append(
                    f"- `{math_name}` against `{baseline}` on {len(gains)} matched "
                    f"file/thread pairs: {wins} smaller, {ties} equal, "
                    f"{losses} larger; median MathZip size gain "
                    f"{statistics.median(gains):.3f}% "
                    "(positive means MathZip produced fewer bytes)."
                )
            else:
                lines.append(
                    f"- No matched successful rows for `{math_name}` against "
                    f"`{baseline}`."
                )
    return lines


def _provenance_table(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    entries = sorted(
        {
            (
                str(row.get("corpus")),
                row.get("input_license"),
                row.get("input_provenance"),
            )
            for row in rows
        },
        key=lambda value: value[0],
    )
    lines = [
        "| Corpus | License/terms | Provenance |",
        "|---|---|---|",
    ]
    for corpus, license_value, provenance in entries:
        lines.append(
            f"| {_escape(corpus)} | {_escape(license_value or 'unavailable')} | "
            f"{_escape(provenance or 'unavailable')} |"
        )
    return lines


def generate_report(document: Mapping[str, Any], output: Path) -> None:
    rows = document.get("results", [])
    system = document.get("system", {})
    methodology = document.get("methodology", {})
    source = document.get("source", {})
    failures = [row for row in rows if row.get("status") != "ok"]
    lines = [
        "# MathZip benchmark report",
        "",
        f"Run ID: `{_escape(document.get('run_id'))}`",
        f"Profile: `{_escape(document.get('profile'))}`",
        f"Status: `{_escape(document.get('status'))}`",
        f"Started (UTC): `{_escape(document.get('started_at_utc'))}`",
        f"Completed (UTC): `{_escape(document.get('completed_at_utc'))}`",
        "",
        "This report is generated only from recorded file outputs and trial evidence. "
        "It makes no claim for configurations or datasets that failed or were absent.",
        "",
        "## Experimental setup",
        "",
        f"- CPU: {_escape(system.get('cpu_model') or 'unknown')}",
        f"- Logical cores: {_escape(system.get('logical_cores') or 'unknown')}",
        f"- Physical cores: {_escape(system.get('physical_cores') or 'unknown')}",
        f"- CPU affinity count: "
        f"{_escape((system.get('cpu_affinity') or {}).get('logical_cpu_count') or 'unknown')}",
        f"- CPU governor(s): {_escape(system.get('cpu_governors') or 'unavailable')}",
        f"- RAM bytes: {_format_number(system.get('ram_bytes'), 0)}",
        f"- Swap bytes: {_format_number(system.get('swap_bytes'), 0)}",
        f"- Filesystem: "
        f"{_escape((system.get('storage') or {}).get('filesystem_type') or 'unknown')}; "
        f"free bytes {_format_number((system.get('storage') or {}).get('free_bytes'), 0)}",
        f"- Container image digest: "
        f"{_escape((system.get('container') or {}).get('image_digest') or 'unavailable')}",
        f"- OS: {_escape(system.get('os') or 'unknown')}",
        f"- Rust: {_escape(system.get('rustc_version') or 'unknown')}",
        f"- Python: {_escape(system.get('python_version') or 'unknown')}",
        f"- Warm-ups: {_escape(methodology.get('warmups'))}",
        f"- Measured repeats: {_escape(methodology.get('repeats'))}",
        f"- Per-operation timeout: {_escape(methodology.get('timeout_seconds_per_operation'))} s",
        "- Aggregate: median of successful measured repetitions.",
        "- Every measured round trip is checked against the original SHA-256.",
        "",
        "## Weighted corpus results",
        "",
        *_aggregate_table(rows),
        "",
        "Weighted ratio is total original bytes divided by total compressed bytes. "
        "Throughput is total original bytes divided by the sum of per-file median times.",
        "",
        "## Per-file ratio statistics",
        "",
        *_per_file_statistics(rows),
        "",
        "Arithmetic and geometric means are shown separately from the weighted aggregate; "
        "they are not substituted for corpus-wide byte totals.",
        "",
        "## Dataset provenance",
        "",
        *_provenance_table(rows),
        "",
        "## Baseline comparisons",
        "",
        *_comparisons(rows),
        "",
        "## Failures and unavailable measurements",
        "",
    ]
    if failures:
        lines.extend(
            [
                "| Corpus | Input | Codec | Threads | Status | Detail |",
                "|---|---|---|---:|---|---|",
            ]
        )
        for row in failures:
            lines.append(
                f"| {_escape(row.get('corpus'))} | {_escape(row.get('input_path'))} | "
                f"{_escape(row.get('codec'))} | {_escape(row.get('threads'))} | "
                f"{_escape(row.get('status'))} | {_escape(row.get('error') or '')} |"
            )
    else:
        lines.append("No failed or unavailable rows were recorded.")
    lines.extend(
        [
            "",
            "## Reproducibility evidence",
            "",
            f"- Result schema: `{_escape(document.get('schema_version'))}`",
            f"- Config SHA-256: `{_escape(document.get('config', {}).get('sha256'))}`",
            f"- Source revision: "
            f"`{_escape(source.get('source_revision') or 'unavailable')}`",
            f"- Source tree dirty: `{_escape(source.get('source_dirty'))}`",
            f"- Source-tree SHA-256: "
            f"`{_escape(source.get('source_tree_sha256') or 'unavailable')}`",
            f"- Source-tree manifest embedded: "
            f"`{str(isinstance(source.get('source_tree_manifest'), list)).lower()}`",
            f"- Source stable during run: "
            f"`{str(bool(source.get('source_stable_during_run'))).lower()}`",
            f"- Executable hashes stable during run: "
            f"`{str(bool(methodology.get('binaries_stable_during_run'))).lower()}`",
            f"- Available executable hashes complete: "
            f"`{str(bool(methodology.get('available_executable_hashes_complete'))).lower()}`",
            f"- Input rights/provenance complete: "
            f"`{str(bool(methodology.get('input_rights_and_provenance_complete'))).lower()}`",
            f"- Timing protocol compliant: "
            f"`{str(bool(document.get('timing_protocol_compliant'))).lower()}`",
            f"- Successful rows: {sum(row.get('status') == 'ok' for row in rows)}",
            f"- Failed/unavailable rows: {len(failures)}",
            f"- Publication-compliant evidence: "
            f"`{str(bool(document.get('scientifically_compliant_run'))).lower()}`",
            f"- Publication evidence gaps: "
            f"{_escape(document.get('publication_evidence_gaps') or 'none')}",
            "",
            "Exact command lines, compressor versions, per-trial wall/CPU/RSS values, "
            "archive SHA-256 values and restored SHA-256 values remain in `results.json`.",
            "",
            "## Interpretation limits",
            "",
            "- A failed row is not omitted from totals silently; it appears above and is "
            "excluded from successful aggregates.",
            "- OS page-cache effects are not eliminated. Fresh output paths prevent reuse "
            "of prior compressed results, but do not flush the system page cache.",
            "- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable "
            "or underestimate short-lived peaks on other systems.",
            "- Missing MathZip inspection fields remain `n/a`; the report does not infer "
            "model, residual or segmentation breakdowns.",
            "",
        ]
    )
    atomic_write_text(output, "\n".join(lines))
