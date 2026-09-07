#!/usr/bin/env python3
"""Build the canonical paired native-ablation evidence table.

The pipeline deliberately does not infer an ablation from a codec-config
name.  Every native configuration is resolved through the completed benchmark
run metadata, including its exact effective configuration hash, profile,
thread count, ablation ID, and disabled-provider set.

All deltas use the direction ``ablated - enabled baseline``.  A positive size
delta therefore means that the enabled algorithm saved archive bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from mathsvg.python.analysis.statistics import (
    paired_bootstrap_interval,
    summarize,
)
from mathsvg.python.benchmarks.schema import (
    COMMIT_RE,
    SHA256_RE,
    TrialError,
    TrialRecord,
    read_jsonl,
)
from mathsvg.python.benchmarks.summarize import (
    SummaryError,
    write_csv,
)

_ABLATION_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENVIRONMENT_RECORD_FIELDS = (
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
_METRICS = (
    ("archive_bytes", "sum"),
    ("compression_wall_ns", "sum"),
    ("decompression_wall_ns", "sum"),
    ("peak_rss_bytes", "max"),
)
_METRIC_FIELDS = tuple(
    field
    for metric, _ in _METRICS
    for field in (
        f"baseline_{metric}_mean",
        f"baseline_{metric}_median",
        f"ablated_{metric}_mean",
        f"ablated_{metric}_median",
        f"paired_{metric}_delta_mean",
        f"paired_{metric}_delta_median",
        f"paired_{metric}_delta_ci95_low",
        f"paired_{metric}_delta_ci95_high",
        f"paired_{metric}_delta_percent_mean",
        f"paired_{metric}_delta_percent_ci95_low",
        f"paired_{metric}_delta_percent_ci95_high",
        f"bootstrap_{metric}_delta_ci95_low",
        f"bootstrap_{metric}_delta_ci95_high",
        f"bootstrap_{metric}_delta_percent_ci95_low",
        f"bootstrap_{metric}_delta_percent_ci95_high",
    )
)

OUTPUT_FIELDS = (
    "schema_version",
    "row_scope",
    "experiment_id",
    "machine_id",
    "architecture",
    "source_commit",
    "source_dirty",
    "dataset_manifest_sha256",
    "randomization_seed",
    "measurement_method",
    "measurement_tool_sha256",
    "split",
    "raw_jsonl_sha256",
    "run_metadata_sha256",
    "ablation_catalog_sha256",
    "profile",
    "threads",
    "dataset_id",
    "input_path",
    "input_sha256",
    "original_bytes",
    "baseline_codec",
    "baseline_codec_config",
    "baseline_config_sha256",
    "baseline_runtime_profile_sha256",
    "baseline_executable_sha256",
    "ablated_codec",
    "ablated_codec_config",
    "ablated_config_sha256",
    "ablated_runtime_profile_sha256",
    "ablated_executable_sha256",
    "ablation_id",
    "disabled_algorithms",
    "delta_direction",
    "expected_repetitions",
    "expected_trial_pairs",
    "baseline_rows",
    "ablated_rows",
    "baseline_successes",
    "ablated_successes",
    "paired_repetitions",
    "paired_successes",
    "missing_baseline_repetitions",
    "missing_ablated_repetitions",
    "failed_pairs",
    "timeout_pairs",
    "unavailable_pairs",
    "schedule_mismatch_pairs",
    "baseline_status",
    "ablated_status",
    "pairing_status",
    "roundtrip_status",
    "determinism_status",
    "protocol_status",
    "confidence_status",
    "ci_method",
    "bootstrap_replicates",
    "bootstrap_seed",
    *_METRIC_FIELDS,
    "decision_status",
    "decision_evidence",
    "failure_reasons",
)


class AblationPipelineError(ValueError):
    """Benchmark evidence cannot support a trustworthy ablation table."""


@dataclass(frozen=True, order=True, slots=True)
class DatasetIdentity:
    dataset_id: str
    input_path: str
    input_sha256: str
    original_bytes: int


@dataclass(frozen=True, order=True, slots=True)
class ConfigKey:
    codec: str
    codec_config: str
    profile: str
    threads: int
    config_sha256: str


@dataclass(frozen=True, order=True, slots=True)
class NativeConfig:
    key: ConfigKey
    ablation_id: str
    disabled_algorithms: tuple[str, ...]
    executable_sha256: str
    runtime_profile_sha256: str
    available: bool
    unavailable_reason: str


@dataclass(frozen=True, slots=True)
class RunEnvironment:
    experiment_id: str
    machine_id: str
    architecture: str
    source_commit: str
    source_dirty: bool
    dataset_manifest_sha256: str
    randomization_seed: int
    measurement_method: str
    measurement_tool_sha256: str
    split: str


@dataclass(frozen=True, slots=True)
class RunMetadata:
    environment: RunEnvironment
    repetitions: int
    warmups: int
    schedule_trials: int
    selected_dataset_ids: tuple[str, ...]
    selected_ablation_ids: tuple[str, ...]
    ablation_catalog_sha256: str
    configs: tuple[NativeConfig, ...]
    result_trial_rows: int
    result_status_counts: tuple[tuple[str, int], ...]
    raw_jsonl_sha256: str
    raw_jsonl_bytes: int
    metadata_sha256: str = ""


@dataclass(frozen=True, slots=True)
class _FileUnit:
    dataset: DatasetIdentity
    baseline: Mapping[int, TrialRecord]
    ablated: Mapping[int, TrialRecord]


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise AblationPipelineError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise AblationPipelineError(f"{label} must be an array")
    return value


def _string(
    value: object,
    label: str,
    *,
    empty: bool = False,
) -> str:
    if not isinstance(value, str) or (not empty and not value):
        qualifier = "a string" if empty else "a non-empty string"
        raise AblationPipelineError(f"{label} must be {qualifier}")
    return value


def _integer(
    value: object,
    label: str,
    *,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AblationPipelineError(f"{label} must be an integer")
    if value < minimum:
        raise AblationPipelineError(
            f"{label} must be at least {minimum}"
        )
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise AblationPipelineError(f"{label} must be a boolean")
    return value


def _sha256(value: object, label: str, *, empty: bool = False) -> str:
    parsed = _string(value, label, empty=empty)
    if parsed or not empty:
        if not SHA256_RE.fullmatch(parsed):
            raise AblationPipelineError(
                f"{label} must be lowercase SHA-256"
            )
    return parsed


def _unique_strings(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    rows = _list(value, label)
    parsed = tuple(
        _string(item, f"{label}[{index}]")
        for index, item in enumerate(rows)
    )
    if not allow_empty and not parsed:
        raise AblationPipelineError(f"{label} must not be empty")
    if len(set(parsed)) != len(parsed):
        raise AblationPipelineError(f"{label} contains duplicates")
    return parsed


def parse_run_metadata(
    document: object,
    *,
    metadata_sha256: str = "",
) -> RunMetadata:
    """Parse the runner's completed metadata without opening its raw JSONL."""

    root = _mapping(document, "run metadata")
    if root.get("schema_version") != 1:
        raise AblationPipelineError(
            "run metadata schema_version must be 1"
        )
    experiment_id = _string(root.get("experiment_id"), "experiment_id")
    machine = _mapping(root.get("machine"), "machine")
    source = _mapping(root.get("source"), "source")
    manifest = _mapping(root.get("manifest"), "manifest")
    protocol = _mapping(root.get("protocol"), "protocol")
    measurement = _mapping(root.get("measurement"), "measurement")
    result = _mapping(root.get("result"), "result")
    ablation_catalog = _mapping(
        root.get("ablation_catalog"),
        "ablation_catalog",
    )

    if result.get("complete") is not True:
        raise AblationPipelineError(
            "run metadata result is incomplete; raw JSONL was not opened"
        )
    source_commit = _string(source.get("commit"), "source.commit")
    if not COMMIT_RE.fullmatch(source_commit):
        raise AblationPipelineError(
            "source.commit must be a full Git object ID"
        )
    split = _string(manifest.get("split"), "manifest.split")
    if split not in {"development", "validation", "holdout"}:
        raise AblationPipelineError("manifest.split is invalid")
    selected_dataset_ids = _unique_strings(
        manifest.get("selected_dataset_ids"),
        "manifest.selected_dataset_ids",
    )
    selected_ablation_ids = _unique_strings(
        protocol.get("native_ablation_ids"),
        "protocol.native_ablation_ids",
        allow_empty=True,
    )
    for identifier in selected_ablation_ids:
        if identifier != "none" and not _ABLATION_ID_RE.fullmatch(identifier):
            raise AblationPipelineError(
                f"invalid selected ablation ID {identifier!r}"
            )

    environment = RunEnvironment(
        experiment_id=experiment_id,
        machine_id=_string(machine.get("machine_id"), "machine.machine_id"),
        architecture=_string(
            machine.get("architecture"),
            "machine.architecture",
        ),
        source_commit=source_commit,
        source_dirty=_boolean(source.get("dirty"), "source.dirty"),
        dataset_manifest_sha256=_sha256(
            manifest.get("canonical_sha256"),
            "manifest.canonical_sha256",
        ),
        randomization_seed=_integer(protocol.get("seed"), "protocol.seed"),
        measurement_method=_string(
            measurement.get("method"),
            "measurement.method",
        ),
        measurement_tool_sha256=_sha256(
            measurement.get("gnu_time_sha256"),
            "measurement.gnu_time_sha256",
        ),
        split=split,
    )
    repetitions = _integer(
        protocol.get("repetitions"),
        "protocol.repetitions",
        minimum=1,
    )
    warmups = _integer(
        protocol.get("warmups"),
        "protocol.warmups",
        minimum=1,
    )
    schedule_trials = _integer(
        protocol.get("schedule_trials"),
        "protocol.schedule_trials",
        minimum=1,
    )

    native_configs: list[NativeConfig] = []
    identity_index: set[ConfigKey] = set()
    logical_index: set[tuple[str, int, str]] = set()
    semantics: dict[str, tuple[str, ...]] = {}
    for index, item in enumerate(_list(root.get("codecs"), "codecs")):
        row = _mapping(item, f"codecs[{index}]")
        if row.get("native_mathsvg") is not True:
            continue
        codec = _string(row.get("codec_id"), f"codecs[{index}].codec_id")
        if codec != "mathsvg":
            raise AblationPipelineError(
                f"codecs[{index}] native codec must be mathsvg"
            )
        profile = _string(
            row.get("profile"),
            f"codecs[{index}].profile",
        )
        threads = _integer(
            row.get("threads"),
            f"codecs[{index}].threads",
            minimum=1,
        )
        ablation_id = _string(
            row.get("ablation_id"),
            f"codecs[{index}].ablation_id",
        )
        if (
            ablation_id != "none"
            and not _ABLATION_ID_RE.fullmatch(ablation_id)
        ):
            raise AblationPipelineError(
                f"codecs[{index}].ablation_id is not canonical"
            )
        disabled = _unique_strings(
            row.get("disabled_algorithms"),
            f"codecs[{index}].disabled_algorithms",
            allow_empty=True,
        )
        if (ablation_id == "none") != (not disabled):
            raise AblationPipelineError(
                "none must disable nothing and every real ablation must "
                "disable at least one algorithm"
            )
        previous_semantics = semantics.setdefault(ablation_id, disabled)
        if previous_semantics != disabled:
            raise AblationPipelineError(
                f"ablation {ablation_id!r} has inconsistent disable sets"
            )
        key = ConfigKey(
            codec=codec,
            codec_config=_string(
                row.get("config_id"),
                f"codecs[{index}].config_id",
            ),
            profile=profile,
            threads=threads,
            config_sha256=_sha256(
                row.get("config_sha256"),
                f"codecs[{index}].config_sha256",
            ),
        )
        if key in identity_index:
            raise AblationPipelineError(
                f"duplicate native metadata configuration {key!r}"
            )
        identity_index.add(key)
        logical = (profile, threads, ablation_id)
        if logical in logical_index:
            raise AblationPipelineError(
                "multiple metadata configurations map to "
                f"profile/thread/ablation {logical!r}"
            )
        logical_index.add(logical)
        available = _boolean(
            row.get("available"),
            f"codecs[{index}].available",
        )
        unavailable_reason = _string(
            row.get("unavailable_reason"),
            f"codecs[{index}].unavailable_reason",
            empty=True,
        )
        if available == bool(unavailable_reason):
            raise AblationPipelineError(
                "available native config must have no unavailable reason; "
                "unavailable config must retain one"
            )
        executable_sha256 = _sha256(
            row.get("executable_sha256"),
            f"codecs[{index}].executable_sha256",
            empty=not available,
        )
        runtime_profile_sha256 = _sha256(
            row.get("runtime_profile_sha256"),
            f"codecs[{index}].runtime_profile_sha256",
            empty=not available,
        )
        native_configs.append(
            NativeConfig(
                key=key,
                ablation_id=ablation_id,
                disabled_algorithms=disabled,
                executable_sha256=executable_sha256,
                runtime_profile_sha256=runtime_profile_sha256,
                available=available,
                unavailable_reason=unavailable_reason,
            )
        )

    if not native_configs:
        raise AblationPipelineError(
            "run metadata contains no native MathSVG configuration"
        )
    actual_ablation_ids = {config.ablation_id for config in native_configs}
    if actual_ablation_ids != set(selected_ablation_ids):
        raise AblationPipelineError(
            "protocol native_ablation_ids differ from native codec metadata: "
            f"protocol={sorted(set(selected_ablation_ids))}, "
            f"codecs={sorted(actual_ablation_ids)}"
        )
    selected_set = set(selected_ablation_ids)
    by_profile_thread: dict[tuple[str, int], set[str]] = defaultdict(set)
    for config in native_configs:
        by_profile_thread[
            (config.key.profile, config.key.threads)
        ].add(config.ablation_id)
    incomplete_groups = {
        key: sorted(selected_set - present)
        for key, present in by_profile_thread.items()
        if present != selected_set
    }
    if incomplete_groups:
        raise AblationPipelineError(
            "native metadata does not contain the same ablation matrix for "
            f"every profile/thread: {incomplete_groups}"
        )

    status_counts_object = _mapping(
        result.get("status_counts"),
        "result.status_counts",
    )
    required_statuses = ("ok", "failed", "timeout", "unavailable")
    if set(status_counts_object) != set(required_statuses):
        raise AblationPipelineError(
            "result.status_counts must contain exactly "
            "ok/failed/timeout/unavailable"
        )
    result_status_counts = tuple(
        (
            status,
            _integer(
                status_counts_object[status],
                f"result.status_counts.{status}",
            ),
        )
        for status in required_statuses
    )
    catalog_sha256 = _sha256(
        ablation_catalog.get("sha256"),
        "ablation_catalog.sha256",
    )
    if ablation_catalog.get("selected") is not True:
        raise AblationPipelineError(
            "ablation catalogue was not selected for the native run"
        )
    if metadata_sha256 and not SHA256_RE.fullmatch(metadata_sha256):
        raise AblationPipelineError(
            "metadata_sha256 must be lowercase SHA-256"
        )

    return RunMetadata(
        environment=environment,
        repetitions=repetitions,
        warmups=warmups,
        schedule_trials=schedule_trials,
        selected_dataset_ids=selected_dataset_ids,
        selected_ablation_ids=selected_ablation_ids,
        ablation_catalog_sha256=catalog_sha256,
        configs=tuple(sorted(native_configs)),
        result_trial_rows=_integer(
            result.get("trial_rows"),
            "result.trial_rows",
            minimum=1,
        ),
        result_status_counts=result_status_counts,
        raw_jsonl_sha256=_sha256(
            result.get("raw_jsonl_sha256"),
            "result.raw_jsonl_sha256",
        ),
        raw_jsonl_bytes=_integer(
            result.get("raw_jsonl_bytes"),
            "result.raw_jsonl_bytes",
            minimum=1,
        ),
        metadata_sha256=metadata_sha256,
    )


def read_run_metadata(path: pathlib.Path) -> RunMetadata:
    """Read metadata atomically written by the runner and require completion."""

    encoded = path.read_bytes()
    metadata_sha256 = hashlib.sha256(encoded).hexdigest()
    try:
        document = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AblationPipelineError(
            f"{path}: invalid run metadata JSON: {exc}"
        ) from exc
    return parse_run_metadata(
        document,
        metadata_sha256=metadata_sha256,
    )


def _record_environment(record: TrialRecord) -> tuple[object, ...]:
    return tuple(
        getattr(record, field) for field in _ENVIRONMENT_RECORD_FIELDS
    )


def _metadata_environment(metadata: RunMetadata) -> tuple[object, ...]:
    environment = metadata.environment
    return tuple(
        getattr(environment, field)
        for field in _ENVIRONMENT_RECORD_FIELDS
    )


def _record_key(record: TrialRecord) -> ConfigKey:
    return ConfigKey(
        codec=record.codec,
        codec_config=record.codec_config,
        profile=record.profile,
        threads=record.threads,
        config_sha256=record.config_sha256,
    )


def validate_completed_evidence(
    records: Sequence[TrialRecord],
    metadata: RunMetadata,
) -> tuple[DatasetIdentity, ...]:
    """Cross-check all raw provenance and return the dataset universe."""

    if not records:
        raise AblationPipelineError("raw JSONL contains no trial rows")
    expected_environment = _metadata_environment(metadata)
    config_index = {config.key: config for config in metadata.configs}
    dataset_index: dict[str, DatasetIdentity] = {}
    seen_native_cells: set[
        tuple[ConfigKey, bool, str, int]
    ] = set()
    for record in records:
        record.validate()
        if _record_environment(record) != expected_environment:
            raise AblationPipelineError(
                "raw trial environment differs from run metadata at "
                f"{record.dataset_id}/{record.codec_config}/"
                f"repetition-{record.repetition}"
            )
        dataset = DatasetIdentity(
            dataset_id=record.dataset_id,
            input_path=record.input_path,
            input_sha256=record.input_sha256,
            original_bytes=record.original_bytes,
        )
        previous = dataset_index.setdefault(record.dataset_id, dataset)
        if previous != dataset:
            raise AblationPipelineError(
                f"dataset ID {record.dataset_id!r} has conflicting identity"
            )
        phase_limit = metadata.warmups if record.warmup else metadata.repetitions
        if record.repetition >= phase_limit:
            phase = "warm-up" if record.warmup else "measured"
            raise AblationPipelineError(
                f"{phase} repetition {record.repetition} exceeds metadata"
            )
        if not record.native_mathsvg:
            continue
        config = config_index.get(_record_key(record))
        if config is None:
            raise AblationPipelineError(
                "native raw configuration has no exact run-metadata mapping: "
                f"{_record_key(record)!r}"
            )
        if record.codec_executable_sha256 != config.executable_sha256:
            raise AblationPipelineError(
                "native executable identity differs from metadata for "
                f"{record.codec_config!r}"
            )
        cell = (
            config.key,
            record.warmup,
            record.dataset_id,
            record.repetition,
        )
        if cell in seen_native_cells:
            raise AblationPipelineError(
                f"duplicate native trial cell {cell!r}"
            )
        seen_native_cells.add(cell)

    if len(records) != metadata.result_trial_rows:
        raise AblationPipelineError(
            "raw row count differs from completed metadata: "
            f"raw={len(records)}, metadata={metadata.result_trial_rows}"
        )
    if len(records) != metadata.schedule_trials:
        raise AblationPipelineError(
            "raw row count differs from scheduled trial count: "
            f"raw={len(records)}, schedule={metadata.schedule_trials}"
        )
    observed_statuses = Counter(record.status for record in records)
    expected_statuses = dict(metadata.result_status_counts)
    if any(
        observed_statuses[status] != count
        for status, count in expected_statuses.items()
    ):
        raise AblationPipelineError(
            "raw status counts differ from completed metadata: "
            f"raw={dict(observed_statuses)}, metadata={expected_statuses}"
        )
    if set(dataset_index) != set(metadata.selected_dataset_ids):
        raise AblationPipelineError(
            "raw datasets differ from manifest selection: "
            f"raw={sorted(dataset_index)}, "
            f"metadata={sorted(metadata.selected_dataset_ids)}"
        )
    return tuple(
        dataset_index[identifier]
        for identifier in sorted(dataset_index)
    )


def load_completed_evidence(
    raw_jsonl: pathlib.Path,
    run_metadata: pathlib.Path,
) -> tuple[list[TrialRecord], RunMetadata]:
    """Load only a completed, hash-matched runner output.

    Metadata is opened and checked first.  Consequently, a benchmark that is
    still appending its JSONL is rejected before the pipeline opens that file.
    """

    metadata = read_run_metadata(run_metadata)
    actual_size = raw_jsonl.stat().st_size
    if actual_size != metadata.raw_jsonl_bytes:
        raise AblationPipelineError(
            "raw JSONL size differs from completed metadata"
        )
    actual_sha256 = _sha256_file(raw_jsonl)
    if actual_sha256 != metadata.raw_jsonl_sha256:
        raise AblationPipelineError(
            "raw JSONL SHA-256 differs from completed metadata"
        )
    records = read_jsonl(raw_jsonl)
    validate_completed_evidence(records, metadata)
    return records, metadata


def _index_measured_native(
    records: Sequence[TrialRecord],
    metadata: RunMetadata,
) -> dict[tuple[ConfigKey, str], dict[int, TrialRecord]]:
    config_index = {config.key: config for config in metadata.configs}
    result: dict[
        tuple[ConfigKey, str],
        dict[int, TrialRecord],
    ] = defaultdict(dict)
    for record in records:
        if record.warmup or not record.native_mathsvg:
            continue
        key = _record_key(record)
        if key not in config_index:
            # validate_completed_evidence reports the richer error first.
            continue
        repetitions = result[(key, record.dataset_id)]
        if record.repetition in repetitions:
            raise AblationPipelineError(
                "duplicate measured repetition after metadata resolution"
            )
        repetitions[record.repetition] = record
    return result


def _side_status(
    records: Sequence[TrialRecord | None],
) -> str:
    statuses = {
        "missing" if record is None else record.status
        for record in records
    }
    if len(statuses) == 1:
        return next(iter(statuses))
    return "mixed"


def _metric_value(record: TrialRecord, metric: str) -> float:
    value = getattr(record, metric)
    if value is None:
        raise AblationPipelineError(
            f"ok row unexpectedly lacks metric {metric}"
        )
    return float(value)


def _aggregate(values: Sequence[float], mode: str) -> float:
    if not values:
        raise AblationPipelineError("cannot aggregate an empty corpus")
    if mode == "sum":
        return float(sum(values))
    if mode == "max":
        return float(max(values))
    raise AblationPipelineError(f"unknown aggregation mode {mode!r}")


def _seed_for(
    seed: int,
    metadata: RunMetadata,
    profile: str,
    threads: int,
    ablation_id: str,
    metric: str,
    kind: str,
) -> int:
    material = "\0".join(
        (
            str(seed),
            metadata.environment.experiment_id,
            metadata.environment.machine_id,
            profile,
            str(threads),
            ablation_id,
            metric,
            kind,
        )
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _successful_pair(
    baseline: TrialRecord | None,
    ablated: TrialRecord | None,
) -> bool:
    return bool(
        baseline is not None
        and ablated is not None
        and baseline.schedule_block == ablated.schedule_block
        and baseline.status == "ok"
        and ablated.status == "ok"
    )


def _paired_samples(
    units: Sequence[_FileUnit],
    repetitions: range,
    metric: str,
    mode: str,
) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for repetition in repetitions:
        baseline_values: list[float] = []
        ablated_values: list[float] = []
        complete = True
        for unit in units:
            baseline = unit.baseline.get(repetition)
            ablated = unit.ablated.get(repetition)
            if not _successful_pair(baseline, ablated):
                complete = False
                break
            assert baseline is not None
            assert ablated is not None
            baseline_values.append(_metric_value(baseline, metric))
            ablated_values.append(_metric_value(ablated, metric))
        if complete:
            pairs.append(
                (
                    _aggregate(baseline_values, mode),
                    _aggregate(ablated_values, mode),
                )
            )
    return pairs


def _mean_delta(pairs: Sequence[tuple[float, float]]) -> float:
    return math.fsum(ablated - baseline for baseline, ablated in pairs) / len(
        pairs
    )


def _mean_percent_delta(
    pairs: Sequence[tuple[float, float]],
) -> float:
    if any(baseline == 0.0 for baseline, _ in pairs):
        raise ValueError("percentage denominator is zero")
    return math.fsum(
        (ablated - baseline) * 100.0 / baseline
        for baseline, ablated in pairs
    ) / len(pairs)


def _bootstrap_metric_interval(
    units: Sequence[_FileUnit],
    repetitions: range,
    metric: str,
    mode: str,
    *,
    seed: int,
    replicates: int,
    percentage: bool,
) -> tuple[float, float] | None:
    if not units:
        return None
    for unit in units:
        for repetition in repetitions:
            if not _successful_pair(
                unit.baseline.get(repetition),
                unit.ablated.get(repetition),
            ):
                return None

    def statistic(sampled_units: Sequence[_FileUnit]) -> float:
        pairs = _paired_samples(
            sampled_units,
            repetitions,
            metric,
            mode,
        )
        if percentage:
            return _mean_percent_delta(pairs)
        return _mean_delta(pairs)

    try:
        return paired_bootstrap_interval(
            units,
            statistic,
            seed=seed,
            replicates=replicates,
        )
    except ValueError:
        return None


def _empty_metric_values() -> dict[str, object]:
    return {field: "" for field in _METRIC_FIELDS}


def _metric_values(
    units: Sequence[_FileUnit],
    repetitions: range,
    *,
    row_scope: str,
    metadata: RunMetadata,
    profile: str,
    threads: int,
    ablation_id: str,
    bootstrap_replicates: int,
    bootstrap_seed: int,
) -> dict[str, object]:
    values = _empty_metric_values()
    for metric, mode in _METRICS:
        pairs = _paired_samples(units, repetitions, metric, mode)
        if not pairs:
            continue
        baseline_summary = summarize(pair[0] for pair in pairs)
        ablated_summary = summarize(pair[1] for pair in pairs)
        delta_summary = summarize(
            pair[1] - pair[0] for pair in pairs
        )
        values.update(
            {
                f"baseline_{metric}_mean": baseline_summary.mean,
                f"baseline_{metric}_median": baseline_summary.median,
                f"ablated_{metric}_mean": ablated_summary.mean,
                f"ablated_{metric}_median": ablated_summary.median,
                f"paired_{metric}_delta_mean": delta_summary.mean,
                f"paired_{metric}_delta_median": delta_summary.median,
                f"paired_{metric}_delta_ci95_low": delta_summary.ci95_low,
                f"paired_{metric}_delta_ci95_high": delta_summary.ci95_high,
            }
        )
        try:
            percent_summary = summarize(
                (ablated - baseline) * 100.0 / baseline
                for baseline, ablated in pairs
            )
        except (ValueError, ZeroDivisionError):
            percent_summary = None
        if percent_summary is not None:
            values.update(
                {
                    f"paired_{metric}_delta_percent_mean": (
                        percent_summary.mean
                    ),
                    f"paired_{metric}_delta_percent_ci95_low": (
                        percent_summary.ci95_low
                    ),
                    f"paired_{metric}_delta_percent_ci95_high": (
                        percent_summary.ci95_high
                    ),
                }
            )
        if row_scope != "corpus":
            continue
        absolute_interval = _bootstrap_metric_interval(
            units,
            repetitions,
            metric,
            mode,
            seed=_seed_for(
                bootstrap_seed,
                metadata,
                profile,
                threads,
                ablation_id,
                metric,
                "absolute",
            ),
            replicates=bootstrap_replicates,
            percentage=False,
        )
        percent_interval = _bootstrap_metric_interval(
            units,
            repetitions,
            metric,
            mode,
            seed=_seed_for(
                bootstrap_seed,
                metadata,
                profile,
                threads,
                ablation_id,
                metric,
                "percent",
            ),
            replicates=bootstrap_replicates,
            percentage=True,
        )
        if absolute_interval is not None:
            values[f"bootstrap_{metric}_delta_ci95_low"] = (
                absolute_interval[0]
            )
            values[f"bootstrap_{metric}_delta_ci95_high"] = (
                absolute_interval[1]
            )
        if percent_interval is not None:
            values[
                f"bootstrap_{metric}_delta_percent_ci95_low"
            ] = percent_interval[0]
            values[
                f"bootstrap_{metric}_delta_percent_ci95_high"
            ] = percent_interval[1]
    return values


def _correctness_status(
    records: Sequence[TrialRecord | None],
) -> str:
    present = [record for record in records if record is not None]
    if any(
        record.status == "failed" and not record.roundtrip_ok
        for record in present
    ):
        return "fail"
    if len(present) == len(records) and all(
        record.roundtrip_ok for record in present
    ):
        return "pass"
    return "incomplete"


def _determinism_status(
    units: Sequence[_FileUnit],
    repetitions: range,
) -> str:
    complete = True
    for unit in units:
        for side in (unit.baseline, unit.ablated):
            records = [side.get(repetition) for repetition in repetitions]
            if any(
                record is not None
                and record.status == "ok"
                and not record.deterministic_archive
                for record in records
            ):
                return "fail"
            successful_hashes = {
                record.archive_sha256
                for record in records
                if record is not None and record.status == "ok"
            }
            if len(successful_hashes) > 1:
                return "fail"
            if any(
                record is None
                or record.status != "ok"
                or not record.deterministic_archive
                for record in records
            ):
                complete = False
    return "pass" if complete else "incomplete"


def _close_delta_requires_ten(
    metric_values: Mapping[str, object],
) -> bool:
    for metric, _ in _METRICS:
        baseline_value = metric_values[f"baseline_{metric}_mean"]
        delta_value = metric_values[f"paired_{metric}_delta_mean"]
        if baseline_value == "" or delta_value == "":
            continue
        baseline = float(baseline_value)
        delta = float(delta_value)
        if baseline == 0.0:
            if delta == 0.0:
                return True
            continue
        if abs(delta) / abs(baseline) < 0.02:
            return True
    return False


def _conservative_size_interval(
    metric_values: Mapping[str, object],
) -> tuple[float, float] | None:
    low = metric_values["paired_archive_bytes_delta_ci95_low"]
    high = metric_values["paired_archive_bytes_delta_ci95_high"]
    if low == "" or high == "":
        return None
    lows = [float(low)]
    highs = [float(high)]
    bootstrap_low = metric_values[
        "bootstrap_archive_bytes_delta_ci95_low"
    ]
    bootstrap_high = metric_values[
        "bootstrap_archive_bytes_delta_ci95_high"
    ]
    if bootstrap_low != "" and bootstrap_high != "":
        lows.append(float(bootstrap_low))
        highs.append(float(bootstrap_high))
    return min(lows), max(highs)


def _decision(
    confidence_status: str,
    metric_values: Mapping[str, object],
) -> tuple[str, str]:
    interval = _conservative_size_interval(metric_values)
    size_delta = metric_values["paired_archive_bytes_delta_mean"]
    details: list[str] = [
        "delta=ablated-minus-enabled",
        f"size_delta_bytes={size_delta if size_delta != '' else 'missing'}",
    ]
    for metric in (
        "compression_wall_ns",
        "decompression_wall_ns",
        "peak_rss_bytes",
    ):
        value = metric_values[f"paired_{metric}_delta_percent_mean"]
        details.append(
            f"{metric}_delta_percent="
            f"{value if value != '' else 'missing'}"
        )
    if confidence_status != "confirmed" or interval is None:
        details.append(f"confidence={confidence_status}")
        return "evidence-incomplete", "; ".join(details)
    details.append(f"conservative_size_ci95=[{interval[0]},{interval[1]}]")
    if interval[0] > 0.0:
        return "enabled-algorithm-size-benefit", "; ".join(details)
    if interval[1] < 0.0:
        return "ablated-variant-size-benefit", "; ".join(details)
    return "size-effect-inconclusive", "; ".join(details)


def _config_values(
    prefix: str,
    config: NativeConfig | None,
) -> dict[str, object]:
    if config is None:
        return {
            f"{prefix}_codec": "",
            f"{prefix}_codec_config": "",
            f"{prefix}_config_sha256": "",
            f"{prefix}_runtime_profile_sha256": "",
            f"{prefix}_executable_sha256": "",
        }
    return {
        f"{prefix}_codec": config.key.codec,
        f"{prefix}_codec_config": config.key.codec_config,
        f"{prefix}_config_sha256": config.key.config_sha256,
        f"{prefix}_runtime_profile_sha256": (
            config.runtime_profile_sha256
        ),
        f"{prefix}_executable_sha256": config.executable_sha256,
    }


def _build_row(
    units: Sequence[_FileUnit],
    *,
    row_scope: str,
    metadata: RunMetadata,
    baseline_config: NativeConfig | None,
    ablated_config: NativeConfig,
    bootstrap_replicates: int,
    bootstrap_seed: int,
) -> dict[str, object]:
    repetitions = range(metadata.repetitions)
    baseline_records = [
        unit.baseline.get(repetition)
        for unit in units
        for repetition in repetitions
    ]
    ablated_records = [
        unit.ablated.get(repetition)
        for unit in units
        for repetition in repetitions
    ]
    expected_trial_pairs = metadata.repetitions * len(units)
    missing_baseline = sum(
        record is None for record in baseline_records
    )
    missing_ablated = sum(
        record is None for record in ablated_records
    )
    failed_pairs = 0
    timeout_pairs = 0
    unavailable_pairs = 0
    schedule_mismatches = 0
    failure_reasons: list[str] = []
    paired_repetitions = 0
    paired_successes = 0

    for unit in units:
        for repetition in repetitions:
            baseline = unit.baseline.get(repetition)
            ablated = unit.ablated.get(repetition)
            label = f"{unit.dataset.dataset_id}:r{repetition}"
            if baseline is None:
                failure_reasons.append(f"baseline:{label}:missing")
            elif baseline.status != "ok":
                failure_reasons.append(
                    f"baseline:{label}:{baseline.status}:{baseline.error}"
                )
            if ablated is None:
                failure_reasons.append(f"ablated:{label}:missing")
            elif ablated.status != "ok":
                failure_reasons.append(
                    f"ablated:{label}:{ablated.status}:{ablated.error}"
                )
            statuses = {
                record.status
                for record in (baseline, ablated)
                if record is not None
            }
            failed_pairs += "failed" in statuses
            timeout_pairs += "timeout" in statuses
            unavailable_pairs += "unavailable" in statuses
            if (
                baseline is not None
                and ablated is not None
                and baseline.schedule_block != ablated.schedule_block
            ):
                schedule_mismatches += 1
                failure_reasons.append(f"pair:{label}:schedule-mismatch")

    for repetition in repetitions:
        present_and_paired = all(
            unit.baseline.get(repetition) is not None
            and unit.ablated.get(repetition) is not None
            and unit.baseline[repetition].schedule_block
            == unit.ablated[repetition].schedule_block
            for unit in units
        )
        if present_and_paired:
            paired_repetitions += 1
        if all(
            _successful_pair(
                unit.baseline.get(repetition),
                unit.ablated.get(repetition),
            )
            for unit in units
        ):
            paired_successes += 1

    baseline_status = _side_status(baseline_records)
    ablated_status = _side_status(ablated_records)
    if missing_baseline or missing_ablated:
        pairing_status = (
            "incomplete"
            if schedule_mismatches
            else "missing"
        )
    elif schedule_mismatches:
        pairing_status = "schedule-mismatch"
    else:
        pairing_status = "complete"
    roundtrip_status = _correctness_status(
        [*baseline_records, *ablated_records]
    )
    determinism_status = _determinism_status(units, repetitions)
    metric_values = _metric_values(
        units,
        repetitions,
        row_scope=row_scope,
        metadata=metadata,
        profile=ablated_config.key.profile,
        threads=ablated_config.key.threads,
        ablation_id=ablated_config.ablation_id,
        bootstrap_replicates=bootstrap_replicates,
        bootstrap_seed=bootstrap_seed,
    )

    if roundtrip_status == "fail" or determinism_status == "fail":
        protocol_status = "failed"
    elif (
        pairing_status != "complete"
        or baseline_status != "ok"
        or ablated_status != "ok"
        or roundtrip_status != "pass"
        or determinism_status != "pass"
        or paired_successes != metadata.repetitions
    ):
        protocol_status = "incomplete"
    elif metadata.repetitions < 5 or (
        metadata.repetitions < 10
        and _close_delta_requires_ten(metric_values)
    ):
        protocol_status = "insufficient-repetitions"
    else:
        protocol_status = "complete"
    if paired_successes == 0:
        confidence_status = "not-measured"
    elif protocol_status == "complete":
        confidence_status = "confirmed"
    else:
        confidence_status = "inconclusive"
    decision_status, decision_evidence = _decision(
        confidence_status,
        metric_values,
    )

    if baseline_config is None:
        failure_reasons.append(
            "baseline:none configuration absent from run metadata"
        )
    else:
        if not baseline_config.available:
            failure_reasons.append(
                "baseline-config-unavailable:"
                + baseline_config.unavailable_reason
            )
    if not ablated_config.available:
        failure_reasons.append(
            "ablated-config-unavailable:"
            + ablated_config.unavailable_reason
        )
    if roundtrip_status != "pass":
        failure_reasons.append(f"roundtrip:{roundtrip_status}")
    if determinism_status != "pass":
        failure_reasons.append(f"determinism:{determinism_status}")
    if protocol_status != "complete":
        failure_reasons.append(f"protocol:{protocol_status}")

    environment = metadata.environment
    if row_scope == "file":
        if len(units) != 1:
            raise AblationPipelineError(
                "file row must contain exactly one dataset"
            )
        dataset_id = units[0].dataset.dataset_id
        input_path = units[0].dataset.input_path
        input_sha256 = units[0].dataset.input_sha256
        original_bytes = units[0].dataset.original_bytes
    else:
        dataset_id = "__aggregate__"
        input_path = ""
        input_sha256 = ""
        original_bytes = sum(
            unit.dataset.original_bytes for unit in units
        )

    return {
        "schema_version": 1,
        "row_scope": row_scope,
        "experiment_id": environment.experiment_id,
        "machine_id": environment.machine_id,
        "architecture": environment.architecture,
        "source_commit": environment.source_commit,
        "source_dirty": environment.source_dirty,
        "dataset_manifest_sha256": environment.dataset_manifest_sha256,
        "randomization_seed": environment.randomization_seed,
        "measurement_method": environment.measurement_method,
        "measurement_tool_sha256": environment.measurement_tool_sha256,
        "split": environment.split,
        "raw_jsonl_sha256": metadata.raw_jsonl_sha256,
        "run_metadata_sha256": metadata.metadata_sha256,
        "ablation_catalog_sha256": metadata.ablation_catalog_sha256,
        "profile": ablated_config.key.profile,
        "threads": ablated_config.key.threads,
        "dataset_id": dataset_id,
        "input_path": input_path,
        "input_sha256": input_sha256,
        "original_bytes": original_bytes,
        **_config_values("baseline", baseline_config),
        **_config_values("ablated", ablated_config),
        "ablation_id": ablated_config.ablation_id,
        "disabled_algorithms": ";".join(
            ablated_config.disabled_algorithms
        ),
        "delta_direction": "ablated_minus_enabled",
        "expected_repetitions": metadata.repetitions,
        "expected_trial_pairs": expected_trial_pairs,
        "baseline_rows": len(baseline_records) - missing_baseline,
        "ablated_rows": len(ablated_records) - missing_ablated,
        "baseline_successes": sum(
            record is not None and record.status == "ok"
            for record in baseline_records
        ),
        "ablated_successes": sum(
            record is not None and record.status == "ok"
            for record in ablated_records
        ),
        "paired_repetitions": paired_repetitions,
        "paired_successes": paired_successes,
        "missing_baseline_repetitions": missing_baseline,
        "missing_ablated_repetitions": missing_ablated,
        "failed_pairs": failed_pairs,
        "timeout_pairs": timeout_pairs,
        "unavailable_pairs": unavailable_pairs,
        "schedule_mismatch_pairs": schedule_mismatches,
        "baseline_status": baseline_status,
        "ablated_status": ablated_status,
        "pairing_status": pairing_status,
        "roundtrip_status": roundtrip_status,
        "determinism_status": determinism_status,
        "protocol_status": protocol_status,
        "confidence_status": confidence_status,
        "ci_method": (
            "paired-repetition-student-t"
            if row_scope == "file"
            else (
                "paired-repetition-student-t+"
                "deterministic-paired-file-bootstrap"
            )
        ),
        "bootstrap_replicates": (
            "" if row_scope == "file" else bootstrap_replicates
        ),
        "bootstrap_seed": (
            "" if row_scope == "file" else bootstrap_seed
        ),
        **metric_values,
        "decision_status": decision_status,
        "decision_evidence": decision_evidence,
        "failure_reasons": " | ".join(dict.fromkeys(failure_reasons)),
    }


def build_ablation_rows(
    records: Sequence[TrialRecord],
    metadata: RunMetadata,
    *,
    bootstrap_replicates: int = 10_000,
    bootstrap_seed: int = 1_297_748_005,
) -> tuple[dict[str, object], ...]:
    """Build deterministic per-file and corpus paired-ablation rows."""

    if bootstrap_replicates < 100:
        raise AblationPipelineError(
            "bootstrap_replicates must be at least 100"
        )
    datasets = validate_completed_evidence(records, metadata)
    measured_index = _index_measured_native(records, metadata)
    grouped_configs: dict[
        tuple[str, int],
        dict[str, NativeConfig],
    ] = defaultdict(dict)
    for config in metadata.configs:
        grouped_configs[
            (config.key.profile, config.key.threads)
        ][config.ablation_id] = config

    rows: list[dict[str, object]] = []
    for profile_threads in sorted(grouped_configs):
        configs = grouped_configs[profile_threads]
        baseline_config = configs.get("none")
        for ablation_id in sorted(set(configs) - {"none"}):
            ablated_config = configs[ablation_id]
            units = [
                _FileUnit(
                    dataset=dataset,
                    baseline=(
                        measured_index.get(
                            (baseline_config.key, dataset.dataset_id),
                            {},
                        )
                        if baseline_config is not None
                        else {}
                    ),
                    ablated=measured_index.get(
                        (ablated_config.key, dataset.dataset_id),
                        {},
                    ),
                )
                for dataset in datasets
            ]
            rows.extend(
                _build_row(
                    [unit],
                    row_scope="file",
                    metadata=metadata,
                    baseline_config=baseline_config,
                    ablated_config=ablated_config,
                    bootstrap_replicates=bootstrap_replicates,
                    bootstrap_seed=bootstrap_seed,
                )
                for unit in units
            )
            rows.append(
                _build_row(
                    units,
                    row_scope="corpus",
                    metadata=metadata,
                    baseline_config=baseline_config,
                    ablated_config=ablated_config,
                    bootstrap_replicates=bootstrap_replicates,
                    bootstrap_seed=bootstrap_seed,
                )
            )
    rows.sort(
        key=lambda row: (
            str(row["profile"]),
            int(row["threads"]),
            str(row["ablation_id"]),
            0 if row["row_scope"] == "file" else 1,
            str(row["dataset_id"]),
        )
    )
    return tuple(rows)


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the canonical per-file and corpus paired native-"
            "ablation CSV from completed raw benchmark evidence."
        )
    )
    parser.add_argument(
        "raw_jsonl",
        type=pathlib.Path,
        help="completed strict benchmark JSONL evidence",
    )
    parser.add_argument(
        "--run-metadata",
        type=pathlib.Path,
        required=True,
        help="completed runner metadata that identifies every ablation",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/ablation/ablation.csv"),
    )
    parser.add_argument(
        "--bootstrap-replicates",
        type=_positive_integer,
        default=10_000,
    )
    parser.add_argument(
        "--bootstrap-seed",
        type=int,
        default=1_297_748_005,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.bootstrap_replicates < 100:
            raise AblationPipelineError(
                "bootstrap_replicates must be at least 100"
            )
        records, metadata = load_completed_evidence(
            args.raw_jsonl,
            args.run_metadata,
        )
        rows = build_ablation_rows(
            records,
            metadata,
            bootstrap_replicates=args.bootstrap_replicates,
            bootstrap_seed=args.bootstrap_seed,
        )
        write_csv(args.output, OUTPUT_FIELDS, rows)
    except (
        OSError,
        TrialError,
        SummaryError,
        AblationPipelineError,
        ValueError,
    ) as exc:
        print(f"ablation pipeline failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
