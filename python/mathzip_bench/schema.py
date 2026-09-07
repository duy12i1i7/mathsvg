"""Stable JSON/CSV result schema and derived storage metrics."""

from __future__ import annotations

from typing import Any, Mapping

RESULT_SCHEMA_VERSION = "mathzip-benchmark-results-v1"

MATHZIP_SEARCH_PROVENANCE_FIELDS = (
    "screened_large_input",
    "requested_mode",
    "effective_segmentation",
    "primary_regular_anchor_bytes",
    "primary_transform_finalist_limit",
    "primary_model_mode_finalist_limit",
    "includes_balanced_frontier",
    "balanced_frontier_regular_anchor_bytes",
    "balanced_frontier_transform_finalist_limit",
    "balanced_frontier_model_mode_finalist_limit",
    "selected_balanced_frontier",
    "selected_regular_anchor_bytes",
    "selected_transform_finalist_limit",
    "selected_model_mode_finalist_limit",
)

CSV_FIELDS = [
    "schema_version",
    "run_id",
    "profile",
    "corpus",
    "input_path",
    "input_name",
    "input_license",
    "input_provenance",
    "input_manifest",
    "input_manifest_sha256",
    "input_manifest_verified",
    "input_sha256",
    "input_entropy_bits_per_byte",
    "original_bytes",
    "codec",
    "codec_family",
    "codec_level",
    "variant",
    "threads",
    "status",
    "error",
    "successful_repeats",
    "requested_repeats",
    "compressed_bytes",
    "ratio",
    "savings",
    "savings_percent",
    "bits_per_byte",
    "expansion_percent",
    "compression_seconds",
    "decompression_seconds",
    "compression_mbps",
    "decompression_mbps",
    "compression_cpu_seconds",
    "decompression_cpu_seconds",
    "peak_rss_bytes",
    "roundtrip_verified",
    "deterministic_archive",
    "archive_sha256",
    "restored_sha256",
    "transform",
    "segment_count",
    "mean_segment_size",
    "median_segment_size",
    "model_distribution_json",
    "residual_distribution_json",
    "model_parameter_bytes",
    "partition_metadata_bytes",
    "residual_bytes",
    "container_overhead_bytes",
    "raw_model_percent",
    "raw_fallback_percent",
    "estimated_residual_entropy",
    "actual_residual_coded_bytes",
    "math_segments_winning_raw",
    "math_segments_winning_zstd",
    "search_seconds",
    "model_fitting_seconds",
    "residual_coding_seconds",
    "command_lines_json",
    "tags_json",
]

_MATHZIP_INSPECTION_INTEGER_FIELDS = (
    "format_version",
    "original_size",
    "transformed_size",
    "compressed_size",
    "payload_size",
    "transform_count",
    "segment_count",
    "container_overhead_bytes",
    "metadata_bytes",
    "transform_metadata_bytes",
    "segment_descriptor_bytes",
    "partition_metadata_bytes",
    "model_parameter_bytes",
    "residual_bytes",
    "actual_residual_coded_bytes",
    "math_segments_winning_raw",
    "math_segments_winning_zstd",
)


def mathzip_inspection_error(value: Any) -> str | None:
    """Return why MathZip's machine-readable inspection is incomplete."""

    if not isinstance(value, Mapping):
        return "inspect JSON root is not an object"
    if value.get("_inspection_error"):
        return str(value["_inspection_error"])
    for field in _MATHZIP_INSPECTION_INTEGER_FIELDS:
        item = value.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            return f"missing or invalid field {field}"
    for field in (
        "model_distribution",
        "residual_distribution",
        "residual_mode_distribution",
        "checksums",
    ):
        if not isinstance(value.get(field), Mapping):
            return f"missing or invalid field {field}"
    if not isinstance(value.get("transforms"), list):
        return "missing or invalid field transforms"
    checksums = value["checksums"]
    if any(checksums.get(field) is not True for field in ("header", "archive", "segments", "original")):
        return "one or more checksum validations did not pass"
    segment_count = value["segment_count"]
    for field in (
        "model_distribution",
        "residual_distribution",
        "residual_mode_distribution",
    ):
        distribution = value[field]
        if any(
            not isinstance(count, int) or isinstance(count, bool) or count < 0
            for count in distribution.values()
        ):
            return f"invalid count in {field}"
        if sum(distribution.values()) != segment_count:
            return f"{field} does not sum to segment_count"
    if value["transform_count"] != len(value["transforms"]):
        return "transform_count differs from transforms length"
    if value["partition_metadata_bytes"] != value["segment_descriptor_bytes"]:
        return "partition metadata differs from segment descriptor bytes"
    if value["actual_residual_coded_bytes"] != value["residual_bytes"]:
        return "actual residual bytes differ from residual bytes"
    storage_total = (
        value["container_overhead_bytes"]
        + value["partition_metadata_bytes"]
        + value["model_parameter_bytes"]
        + value["actual_residual_coded_bytes"]
    )
    if storage_total != value["compressed_size"]:
        return "storage breakdown does not sum to compressed_size"
    original_size = value["original_size"]
    optional_metrics = (
        "mean_segment_size",
        "median_segment_size",
        "raw_model_percentage",
        "raw_fallback_percentage",
        "estimated_residual_entropy",
    )
    for field in optional_metrics:
        item = value.get(field)
        if original_size == 0 and item is None:
            continue
        if (
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not float("-inf") < float(item) < float("inf")
        ):
            return f"missing or invalid field {field}"
    return None


def mathzip_encode_metrics_error(value: Any) -> str | None:
    """Return why MathZip's out-of-band encoder timing record is invalid."""

    if not isinstance(value, Mapping):
        return "encoder metrics JSON root is not an object"
    for field in (
        "search_seconds",
        "model_fitting_seconds",
        "residual_coding_seconds",
    ):
        item = value.get(field)
        if (
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not 0.0 <= float(item) < float("inf")
        ):
            return f"missing or invalid field {field}"

    if any(field in value for field in MATHZIP_SEARCH_PROVENANCE_FIELDS):
        missing = [
            field
            for field in MATHZIP_SEARCH_PROVENANCE_FIELDS
            if field not in value
        ]
        if missing:
            return (
                "incomplete encoder search provenance; missing "
                + ", ".join(missing)
            )
        screened = value["screened_large_input"]
        includes_balanced = value["includes_balanced_frontier"]
        selected_balanced = value["selected_balanced_frontier"]
        if not isinstance(screened, bool):
            return "missing or invalid field screened_large_input"
        if value["requested_mode"] not in ("fast", "balanced", "max"):
            return "missing or invalid field requested_mode"
        if value["effective_segmentation"] not in (
            "fixed",
            "change_point",
            "adaptive",
            "recursive",
        ):
            return "missing or invalid field effective_segmentation"
        for field in (
            "primary_regular_anchor_bytes",
            "selected_regular_anchor_bytes",
        ):
            anchor = value[field]
            if (
                not isinstance(anchor, int)
                or isinstance(anchor, bool)
                or anchor <= 0
            ):
                return f"missing or invalid field {field}"
        if not isinstance(includes_balanced, bool):
            return "missing or invalid field includes_balanced_frontier"
        if not isinstance(selected_balanced, bool):
            return "missing or invalid field selected_balanced_frontier"
        for field in (
            "primary_transform_finalist_limit",
            "primary_model_mode_finalist_limit",
            "selected_transform_finalist_limit",
            "selected_model_mode_finalist_limit",
        ):
            item = value[field]
            if screened:
                if (
                    not isinstance(item, int)
                    or isinstance(item, bool)
                    or item <= 0
                ):
                    return f"missing or invalid field {field}"
            elif item is not None:
                return f"unscreened metrics require null {field}"
        if includes_balanced and (
            not screened or value["requested_mode"] != "max"
        ):
            return (
                "includes_balanced_frontier requires screened Max metrics"
            )
        balanced_anchor = value["balanced_frontier_regular_anchor_bytes"]
        balanced_transform_limit = value[
            "balanced_frontier_transform_finalist_limit"
        ]
        balanced_model_limit = value[
            "balanced_frontier_model_mode_finalist_limit"
        ]
        if includes_balanced:
            for field, item in (
                ("balanced_frontier_regular_anchor_bytes", balanced_anchor),
                (
                    "balanced_frontier_transform_finalist_limit",
                    balanced_transform_limit,
                ),
                (
                    "balanced_frontier_model_mode_finalist_limit",
                    balanced_model_limit,
                ),
            ):
                if (
                    not isinstance(item, int)
                    or isinstance(item, bool)
                    or item <= 0
                ):
                    return f"missing or invalid field {field}"
        elif any(
            item is not None
            for item in (
                balanced_anchor,
                balanced_transform_limit,
                balanced_model_limit,
            )
        ):
            return (
                "metrics without a Balanced frontier require null "
                "Balanced frontier settings"
            )
        if selected_balanced and not includes_balanced:
            return (
                "selected_balanced_frontier requires an evaluated "
                "Balanced frontier"
            )
        selected_settings = (
            value["selected_regular_anchor_bytes"],
            value["selected_transform_finalist_limit"],
            value["selected_model_mode_finalist_limit"],
        )
        expected_selected = (
            (
                balanced_anchor,
                balanced_transform_limit,
                balanced_model_limit,
            )
            if selected_balanced
            else (
                value["primary_regular_anchor_bytes"],
                value["primary_transform_finalist_limit"],
                value["primary_model_mode_finalist_limit"],
            )
        )
        if selected_settings != expected_selected:
            return "selected frontier settings are inconsistent"
    return None


def storage_metrics(original_bytes: int, compressed_bytes: int) -> dict[str, Any]:
    if compressed_bytes < 0 or original_bytes < 0:
        raise ValueError("byte counts cannot be negative")
    ratio = original_bytes / compressed_bytes if compressed_bytes else None
    if original_bytes:
        savings = 1.0 - compressed_bytes / original_bytes
        bits_per_byte = 8.0 * compressed_bytes / original_bytes
        expansion = max(0.0, (compressed_bytes / original_bytes - 1.0) * 100.0)
    else:
        savings = None
        bits_per_byte = None
        expansion = None
    return {
        "ratio": ratio,
        "savings": savings,
        "savings_percent": savings * 100.0 if savings is not None else None,
        "bits_per_byte": bits_per_byte,
        "expansion_percent": expansion,
    }


def throughput_mbps(byte_count: int, seconds: float | None) -> float | None:
    if seconds is None or seconds <= 0.0:
        return None
    return byte_count / 1_000_000.0 / seconds
