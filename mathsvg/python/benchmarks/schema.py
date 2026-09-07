"""Strict raw-trial schema for MathSVG benchmark JSONL."""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Iterable

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
STATUSES = frozenset({"ok", "failed", "timeout", "unavailable"})


class TrialError(ValueError):
    """A raw trial cannot support reproducible analysis."""


@dataclass(frozen=True, slots=True)
class TrialRecord:
    schema_version: int
    experiment_id: str
    machine_id: str
    architecture: str
    source_commit: str
    config_sha256: str
    dataset_manifest_sha256: str
    randomization_seed: int
    schedule_block: int
    schedule_order: int
    repetition: int
    warmup: bool
    split: str
    dataset_id: str
    input_path: str
    input_sha256: str
    original_bytes: int
    codec: str
    codec_config: str
    profile: str
    threads: int
    native_mathsvg: bool
    codec_executable_sha256: str
    measurement_method: str
    measurement_tool_sha256: str
    status: str
    error: str
    archive_bytes: int | None
    archive_sha256: str
    restored_sha256: str
    roundtrip_ok: bool
    deterministic_archive: bool
    compression_wall_ns: int | None
    compression_cpu_ns: int | None
    decompression_wall_ns: int | None
    decompression_cpu_ns: int | None
    peak_rss_bytes: int | None
    compression_peak_rss_bytes: int | None
    decompression_peak_rss_bytes: int | None
    energy_uj: int | None
    literal_only_archive_bytes: int | None
    pre_entropy_archive_bytes: int | None
    entropy_saved_bytes: int | None
    entropy_penalty_bytes: int | None
    procedural_gain_bytes: int | None
    procedural_penalty_bytes: int | None
    container_overhead_bytes: int | None
    function_graph_bytes: int | None
    coordinate_bytes: int | None
    shared_definition_bytes: int | None
    reference_bytes: int | None
    parameter_bytes: int | None
    residual_layer_bytes: int | None
    literal_leaf_bytes: int | None
    entropy_metadata_bytes: int | None
    index_bytes: int | None
    node_count: int | None
    shared_node_count: int | None
    residual_depth_sum: int | None
    residual_root_count: int | None
    function_reconstructed_bytes: int | None
    literal_reconstructed_bytes: int | None
    coordinate_saved_bytes: int | None
    dag_saved_bytes: int | None
    recursive_residual_saved_bytes: int | None
    symbolic_saved_bytes: int | None
    search_ns: int | None

    def validate(self) -> None:
        if self.schema_version != 1:
            raise TrialError("schema_version must be 1")
        for name in (
            "experiment_id",
            "machine_id",
            "architecture",
            "source_commit",
            "dataset_id",
            "input_path",
            "codec",
            "codec_config",
        ):
            if not getattr(self, name):
                raise TrialError(f"{name} must not be empty")
        if not COMMIT_RE.fullmatch(self.source_commit):
            raise TrialError("source_commit must be a full Git object ID")
        for name in (
            "config_sha256",
            "dataset_manifest_sha256",
            "input_sha256",
        ):
            if not SHA256_RE.fullmatch(getattr(self, name)):
                raise TrialError(f"{name} must be lowercase SHA-256")
        for name in (
            "codec_executable_sha256",
            "measurement_tool_sha256",
        ):
            value = getattr(self, name)
            if value and not SHA256_RE.fullmatch(value):
                raise TrialError(f"{name} must be empty or lowercase SHA-256")
        if not self.measurement_method:
            raise TrialError("measurement_method must not be empty")
        if self.status not in STATUSES:
            raise TrialError(f"invalid status {self.status!r}")
        if self.split not in {"development", "validation", "holdout"}:
            raise TrialError("invalid dataset split")
        if self.original_bytes < 0:
            raise TrialError("original_bytes must be non-negative")
        for name in (
            "randomization_seed",
            "schedule_block",
            "schedule_order",
            "repetition",
        ):
            if getattr(self, name) < 0:
                raise TrialError(f"{name} must be non-negative")
        if self.threads < 1:
            raise TrialError("threads must be positive")
        numeric_optional = (
            "archive_bytes",
            "compression_wall_ns",
            "compression_cpu_ns",
            "decompression_wall_ns",
            "decompression_cpu_ns",
            "peak_rss_bytes",
            "compression_peak_rss_bytes",
            "decompression_peak_rss_bytes",
            "energy_uj",
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
        for name in numeric_optional:
            value = getattr(self, name)
            if value is not None and value < 0:
                raise TrialError(f"{name} must be non-negative")

        if self.status == "ok":
            for name in (
                "codec_executable_sha256",
                "measurement_tool_sha256",
            ):
                if not getattr(self, name):
                    raise TrialError(f"ok row requires {name}")
            required = (
                "archive_bytes",
                "compression_wall_ns",
                "compression_cpu_ns",
                "decompression_wall_ns",
                "decompression_cpu_ns",
                "peak_rss_bytes",
                "compression_peak_rss_bytes",
                "decompression_peak_rss_bytes",
            )
            missing = [name for name in required if getattr(self, name) is None]
            if missing:
                raise TrialError("ok row is missing: " + ", ".join(missing))
            for name in ("archive_sha256", "restored_sha256"):
                if not SHA256_RE.fullmatch(getattr(self, name)):
                    raise TrialError(f"ok row requires {name}")
            if not self.roundtrip_ok:
                raise TrialError("ok row must have roundtrip_ok=true")
            if self.restored_sha256 != self.input_sha256:
                raise TrialError("restored hash differs from input hash")
            if self.error:
                raise TrialError("ok row must not contain an error")
            if self.peak_rss_bytes != max(
                int(self.compression_peak_rss_bytes),
                int(self.decompression_peak_rss_bytes),
            ):
                raise TrialError(
                    "peak_rss_bytes must equal max(compression, decompression)"
                )
        else:
            if not self.error:
                raise TrialError("non-ok row must retain an error/status reason")
            if self.roundtrip_ok:
                if self.status != "failed":
                    raise TrialError(
                        "only a failed evidence row may retain a verified round trip"
                    )
                if not SHA256_RE.fullmatch(self.restored_sha256):
                    raise TrialError(
                        "verified failed row requires restored_sha256"
                    )
                if self.restored_sha256 != self.input_sha256:
                    raise TrialError(
                        "verified failed row restored hash differs from input"
                    )

        if self.native_mathsvg:
            if not self.profile:
                raise TrialError("Native MathSVG row requires a profile")
            breakdown = (
                self.container_overhead_bytes,
                self.function_graph_bytes,
                self.coordinate_bytes,
                self.shared_definition_bytes,
                self.reference_bytes,
                self.parameter_bytes,
                self.residual_layer_bytes,
                self.literal_leaf_bytes,
                self.entropy_metadata_bytes,
                self.node_count,
                self.shared_node_count,
                self.residual_depth_sum,
                self.residual_root_count,
                self.function_reconstructed_bytes,
                self.literal_reconstructed_bytes,
            )
            if self.status == "ok" and any(value is None for value in breakdown):
                raise TrialError(
                    "Native MathSVG ok row requires complete procedural breakdown"
                )
            if self.status == "ok":
                if (
                    self.entropy_saved_bytes is not None
                    and self.pre_entropy_archive_bytes is None
                ):
                    raise TrialError(
                        "entropy_saved_bytes requires pre_entropy_archive_bytes"
                    )
                if (
                    self.entropy_penalty_bytes is not None
                    and self.pre_entropy_archive_bytes is None
                ):
                    raise TrialError(
                        "entropy_penalty_bytes requires pre_entropy_archive_bytes"
                    )
                if (
                    self.entropy_saved_bytes is not None
                    and int(self.entropy_saved_bytes)
                    != max(
                        int(self.pre_entropy_archive_bytes)
                        - int(self.archive_bytes),
                        0,
                    )
                ):
                    raise TrialError(
                        "entropy_saved_bytes differs from exact positive delta"
                    )
                if (
                    self.entropy_penalty_bytes is not None
                    and int(self.entropy_penalty_bytes)
                    != max(
                        int(self.archive_bytes)
                        - int(self.pre_entropy_archive_bytes),
                        0,
                    )
                ):
                    raise TrialError(
                        "entropy_penalty_bytes differs from exact negative delta"
                    )
                if (
                    self.procedural_gain_bytes is not None
                    and self.literal_only_archive_bytes is None
                ):
                    raise TrialError(
                        "procedural_gain_bytes requires literal-only archive"
                    )
                if (
                    self.procedural_penalty_bytes is not None
                    and self.literal_only_archive_bytes is None
                ):
                    raise TrialError(
                        "procedural_penalty_bytes requires literal-only archive"
                    )
                if (
                    self.procedural_gain_bytes is not None
                    and int(self.procedural_gain_bytes)
                    != max(
                        int(self.literal_only_archive_bytes)
                        - int(self.archive_bytes),
                        0,
                    )
                ):
                    raise TrialError(
                        "procedural_gain_bytes differs from exact positive delta"
                    )
                if (
                    self.procedural_penalty_bytes is not None
                    and int(self.procedural_penalty_bytes)
                    != max(
                        int(self.archive_bytes)
                        - int(self.literal_only_archive_bytes),
                        0,
                    )
                ):
                    raise TrialError(
                        "procedural_penalty_bytes differs from exact negative delta"
                    )
                wire_partition = (
                    int(self.container_overhead_bytes)
                    + int(self.function_graph_bytes)
                    + int(self.coordinate_bytes)
                    + int(self.shared_definition_bytes)
                    + int(self.reference_bytes)
                    + int(self.parameter_bytes)
                    + int(self.residual_layer_bytes)
                    + int(self.literal_leaf_bytes)
                    + int(self.entropy_metadata_bytes)
                )
                if wire_partition != int(self.archive_bytes):
                    raise TrialError(
                        "native wire byte categories do not sum to archive_bytes"
                    )
            if (
                self.status == "ok"
                and self.function_reconstructed_bytes is not None
                and self.literal_reconstructed_bytes is not None
                and self.function_reconstructed_bytes
                + self.literal_reconstructed_bytes
                != self.original_bytes
            ):
                raise TrialError(
                    "procedural and literal reconstructed bytes must "
                    "cover input exactly"
                )
        else:
            if self.profile:
                raise TrialError(
                    "external baseline row must leave profile empty"
                )
            external_breakdown = (
                self.literal_only_archive_bytes,
                self.pre_entropy_archive_bytes,
                self.entropy_saved_bytes,
                self.entropy_penalty_bytes,
                self.procedural_gain_bytes,
                self.procedural_penalty_bytes,
                self.container_overhead_bytes,
                self.function_graph_bytes,
                self.coordinate_bytes,
                self.shared_definition_bytes,
                self.reference_bytes,
                self.parameter_bytes,
                self.residual_layer_bytes,
                self.literal_leaf_bytes,
                self.entropy_metadata_bytes,
                self.index_bytes,
                self.node_count,
                self.shared_node_count,
                self.residual_depth_sum,
                self.residual_root_count,
                self.function_reconstructed_bytes,
                self.literal_reconstructed_bytes,
                self.coordinate_saved_bytes,
                self.dag_saved_bytes,
                self.recursive_residual_saved_bytes,
                self.symbolic_saved_bytes,
                self.search_ns,
            )
            if any(value is not None for value in external_breakdown):
                raise TrialError(
                    "external baseline row must not claim native breakdown"
                )


def write_jsonl(path: pathlib.Path, records: Iterable[TrialRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            record.validate()
            handle.write(
                json.dumps(
                    dataclasses.asdict(record),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )


def append_jsonl(path: pathlib.Path, record: TrialRecord) -> None:
    """Append and fsync one trial so interrupted runs leave valid evidence."""

    record.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            dataclasses.asdict(record),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o644)
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("short JSONL append")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_jsonl(path: pathlib.Path) -> list[TrialRecord]:
    """Strictly parse complete JSONL records and reject schema drift."""

    records: list[TrialRecord] = []
    with path.open("r", encoding="ascii") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.endswith("\n"):
                raise TrialError(
                    f"line {line_number}: truncated JSONL record"
                )
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError("record must be an object")
                record = TrialRecord(**value)
                record.validate()
            except (json.JSONDecodeError, TypeError, TrialError) as exc:
                raise TrialError(f"line {line_number}: {exc}") from exc
            records.append(record)
    return records
