"""Canonical CSV schema for the MathSVG Gate 1 determinism matrix."""

from __future__ import annotations

import csv
import dataclasses
import os
import pathlib
import re
import tempfile
from dataclasses import dataclass
from typing import Iterable

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
STATUSES = frozenset({"ok", "failed", "timeout", "unavailable"})
BUILDS = frozenset({"debug", "release"})
THREAD_MODES = frozenset({"default", "1", "2", "4", "8", "all"})
BACKENDS = frozenset({"default", "scalar", "simd", "auto"})
SPLITS = frozenset({"development", "validation"})


class DeterminismError(ValueError):
    """A determinism row cannot support an honest Gate 1 conclusion."""


@dataclass(frozen=True, slots=True)
class DeterminismRecord:
    schema_version: int
    experiment_id: str
    source_commit: str
    source_dirty: bool
    source_status_sha256: str
    machine_id: str
    architecture: str
    operating_system: str
    logical_cpus: int
    compiler_version: str
    compiler_binary_sha256: str
    build: str
    binary_path: str
    binary_sha256: str
    runner_sha256: str
    config_path: str
    config_sha256: str
    profile: str
    split: str
    input_path: str
    input_bytes: int
    input_sha256: str
    planned_repetitions: int
    repetition: int
    thread_mode: str
    resolved_threads: int | None
    backend: str
    required_axis: bool
    threads_capability: str
    backend_capability: str
    capability_probe_sha256: str
    status: str
    reason: str
    compress_exit_code: int | None
    decompress_exit_code: int | None
    compression_ns: int | None
    decompression_ns: int | None
    archive_bytes: int | None
    archive_sha256: str
    reference_archive_sha256: str
    archive_matches_reference: bool
    restored_sha256: str
    roundtrip_ok: bool
    compress_command: str
    decompress_command: str

    def validate(self) -> None:
        if self.schema_version != 1:
            raise DeterminismError("schema_version must be 1")
        for name in (
            "experiment_id",
            "machine_id",
            "architecture",
            "operating_system",
            "build",
            "binary_path",
            "config_path",
            "profile",
            "split",
            "input_path",
            "thread_mode",
            "backend",
            "threads_capability",
            "backend_capability",
            "status",
            "compress_command",
            "decompress_command",
        ):
            if not getattr(self, name):
                raise DeterminismError(f"{name} must not be empty")
        if not COMMIT_RE.fullmatch(self.source_commit):
            raise DeterminismError("source_commit must be lowercase Git hex")
        for name in (
            "source_status_sha256",
            "runner_sha256",
            "config_sha256",
            "input_sha256",
        ):
            if not SHA256_RE.fullmatch(getattr(self, name)):
                raise DeterminismError(f"{name} must be lowercase SHA-256")
        for name in (
            "compiler_binary_sha256",
            "binary_sha256",
            "capability_probe_sha256",
            "archive_sha256",
            "reference_archive_sha256",
            "restored_sha256",
        ):
            value = getattr(self, name)
            if value and not SHA256_RE.fullmatch(value):
                raise DeterminismError(
                    f"{name} must be empty or lowercase SHA-256"
                )
        if self.build not in BUILDS:
            raise DeterminismError(f"invalid build {self.build!r}")
        if self.thread_mode not in THREAD_MODES:
            raise DeterminismError(
                f"invalid thread_mode {self.thread_mode!r}"
            )
        if self.backend not in BACKENDS:
            raise DeterminismError(f"invalid backend {self.backend!r}")
        if self.split not in SPLITS:
            raise DeterminismError("determinism runner refuses holdout rows")
        if self.status not in STATUSES:
            raise DeterminismError(f"invalid status {self.status!r}")
        if self.logical_cpus < 1:
            raise DeterminismError("logical_cpus must be positive")
        if self.input_bytes < 0:
            raise DeterminismError("input_bytes must be non-negative")
        if self.planned_repetitions < 1:
            raise DeterminismError("planned_repetitions must be positive")
        if not 0 <= self.repetition < self.planned_repetitions:
            raise DeterminismError("repetition is outside the planned range")
        if self.resolved_threads is not None and self.resolved_threads < 1:
            raise DeterminismError("resolved_threads must be positive")
        for name in (
            "compression_ns",
            "decompression_ns",
            "archive_bytes",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise DeterminismError(f"{name} must be non-negative")

        if self.status == "ok":
            if self.reason:
                raise DeterminismError("ok row must not contain a reason")
            for name in (
                "compiler_binary_sha256",
                "binary_sha256",
                "capability_probe_sha256",
                "archive_sha256",
                "reference_archive_sha256",
                "restored_sha256",
            ):
                if not SHA256_RE.fullmatch(getattr(self, name)):
                    raise DeterminismError(f"ok row requires {name}")
            for name in (
                "compress_exit_code",
                "decompress_exit_code",
                "compression_ns",
                "decompression_ns",
                "archive_bytes",
            ):
                if getattr(self, name) is None:
                    raise DeterminismError(f"ok row requires {name}")
            if (
                self.thread_mode != "default"
                and self.resolved_threads is None
            ):
                raise DeterminismError(
                    "explicit thread row requires resolved_threads"
                )
            if self.compress_exit_code != 0 or self.decompress_exit_code != 0:
                raise DeterminismError("ok row requires zero exit codes")
            if not self.archive_matches_reference:
                raise DeterminismError(
                    "ok row must match the reference archive"
                )
            if not self.roundtrip_ok:
                raise DeterminismError("ok row must pass round trip")
            if self.restored_sha256 != self.input_sha256:
                raise DeterminismError("restored hash differs from input hash")
        else:
            if not self.reason:
                raise DeterminismError(
                    "non-ok row must retain a reason"
                )
            if self.status == "unavailable":
                if any(
                    value is not None
                    for value in (
                        self.compress_exit_code,
                        self.decompress_exit_code,
                        self.compression_ns,
                        self.decompression_ns,
                        self.archive_bytes,
                    )
                ):
                    raise DeterminismError(
                        "unavailable row cannot contain execution metrics"
                    )
                if self.archive_sha256 or self.restored_sha256:
                    raise DeterminismError(
                        "unavailable row cannot contain output hashes"
                    )
                if self.roundtrip_ok or self.archive_matches_reference:
                    raise DeterminismError(
                        "unavailable row cannot claim verification"
                    )

        if self.thread_mode == "default":
            if self.required_axis:
                raise DeterminismError(
                    "default thread mode cannot satisfy a required axis"
                )
        elif not self.required_axis:
            raise DeterminismError(
                "explicit thread mode must be a required axis"
            )
        if self.backend == "default" and self.thread_mode != "default":
            # Capability-only supplemental cells are allowed when a future CLI
            # exposes threads before it exposes backend selection.
            pass
        elif self.backend == "default" and self.required_axis:
            raise DeterminismError(
                "default backend cannot satisfy a required axis"
            )


CSV_FIELDS = tuple(field.name for field in dataclasses.fields(DeterminismRecord))
_BOOL_FIELDS = frozenset(
    {
        "source_dirty",
        "required_axis",
        "archive_matches_reference",
        "roundtrip_ok",
    }
)
_OPTIONAL_INT_FIELDS = frozenset(
    {
        "resolved_threads",
        "compress_exit_code",
        "decompress_exit_code",
        "compression_ns",
        "decompression_ns",
        "archive_bytes",
    }
)
_INT_FIELDS = frozenset(
    {
        "schema_version",
        "logical_cpus",
        "input_bytes",
        "planned_repetitions",
        "repetition",
    }
)


def record_sort_key(record: DeterminismRecord) -> tuple[object, ...]:
    build_rank = {"debug": 0, "release": 1}[record.build]
    thread_rank = {
        "default": 0,
        "1": 1,
        "2": 2,
        "4": 3,
        "8": 4,
        "all": 5,
    }[record.thread_mode]
    backend_rank = {
        "default": 0,
        "scalar": 1,
        "simd": 2,
        "auto": 3,
    }[record.backend]
    return (
        record.experiment_id,
        record.machine_id,
        record.architecture,
        record.source_commit,
        record.source_status_sha256,
        record.compiler_binary_sha256,
        build_rank,
        record.binary_sha256,
        record.config_sha256,
        record.input_sha256,
        thread_rank,
        backend_rank,
        record.repetition,
        record.status,
    )


def _encode(record: DeterminismRecord) -> dict[str, str]:
    record.validate()
    row: dict[str, str] = {}
    for name in CSV_FIELDS:
        value = getattr(record, name)
        if name in _BOOL_FIELDS:
            row[name] = "true" if value else "false"
        elif value is None:
            row[name] = ""
        else:
            row[name] = str(value)
    return row


def _decode(row: dict[str, str]) -> DeterminismRecord:
    values: dict[str, object] = {}
    for name in CSV_FIELDS:
        value = row[name]
        if name in _BOOL_FIELDS:
            if value not in {"true", "false"}:
                raise DeterminismError(f"{name} must be true or false")
            values[name] = value == "true"
        elif name in _OPTIONAL_INT_FIELDS:
            values[name] = None if value == "" else int(value, 10)
        elif name in _INT_FIELDS:
            values[name] = int(value, 10)
        else:
            values[name] = value
    record = DeterminismRecord(**values)  # type: ignore[arg-type]
    record.validate()
    return record


def _deduplicate(records: Iterable[DeterminismRecord]) -> list[DeterminismRecord]:
    ordered = sorted(records, key=record_sort_key)
    result: list[DeterminismRecord] = []
    previous: tuple[object, ...] | None = None
    for record in ordered:
        key = record_sort_key(record)[:-1]
        if key == previous:
            raise DeterminismError(
                "duplicate determinism matrix coordinate"
            )
        previous = key
        result.append(record)
    return result


def write_csv(path: pathlib.Path, records: Iterable[DeterminismRecord]) -> None:
    ordered = _deduplicate(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=CSV_FIELDS,
                lineterminator="\n",
                extrasaction="raise",
            )
            writer.writeheader()
            for record in ordered:
                writer.writerow(_encode(record))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def read_csv(path: pathlib.Path) -> list[DeterminismRecord]:
    records: list[DeterminismRecord] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(CSV_FIELDS):
            raise DeterminismError("determinism CSV header is not canonical")
        for line_number, row in enumerate(reader, start=2):
            try:
                if None in row or set(row) != set(CSV_FIELDS):
                    raise DeterminismError("row width does not match header")
                records.append(_decode(row))
            except (DeterminismError, ValueError) as exc:
                raise DeterminismError(
                    f"line {line_number}: {exc}"
                ) from exc
    ordered = _deduplicate(records)
    if records != ordered:
        raise DeterminismError("determinism CSV rows are not canonical")
    return records
