"""Independent consistency checks for benchmark and corpus manifests."""

from __future__ import annotations

import csv
from collections import Counter
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping

from .common import canonical_json_sha256, sha256_file
from .schema import (
    CSV_FIELDS,
    MATHZIP_SEARCH_PROVENANCE_FIELDS,
    RESULT_SCHEMA_VERSION,
    mathzip_encode_metrics_error,
    mathzip_inspection_error,
    storage_metrics,
)

EXPECTED_GRID_SCHEMA_VERSION = "mathzip-benchmark-expected-grid-v1"
PLOT_MANIFEST_SCHEMA_VERSION = "mathzip-plot-manifest-v1"


def _result_grid_key(
    value: Mapping[str, Any],
) -> tuple[str, str, str, int] | None:
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
        or threads < 1
    ):
        return None
    return corpus, input_path, codec, threads


def resolve_results_path(path: Path) -> Path:
    if path.is_dir():
        candidate = path / "results.json"
        if candidate.is_file():
            return candidate
        latest = path / "latest.json"
        if latest.is_file():
            path = latest
        else:
            raise ValueError(f"{path} contains neither results.json nor latest.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") == "mathzip-benchmark-latest-v1":
        target = Path(str(value.get("results_json", "")))
        if not target.is_absolute():
            target = (path.parent / target).resolve()
        if not target.is_file():
            raise ValueError(f"latest pointer target is missing: {target}")
        return target
    return path


def validate_plot_manifest(
    document: Mapping[str, Any],
    plot_directory: Path,
    generator_path: Path,
) -> tuple[list[str], list[str]]:
    """Validate the provenance and bytes named by a generated plot manifest."""

    errors: list[str] = []
    manifest_path = plot_directory / "plot_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        return ["plot_manifest.json must contain a JSON object"], []
    if manifest.get("schema_version") != PLOT_MANIFEST_SCHEMA_VERSION:
        errors.append("plot manifest has an unsupported schema_version")
    if manifest.get("status") not in {"generated", "dependency_missing"}:
        errors.append("plot manifest has an invalid status")
    for field in ("run_id", "profile", "result_count", "failure_count"):
        if manifest.get(field) != document.get(field):
            errors.append(f"plot manifest {field} differs from result document")
    if manifest.get("result_document_hash_method") != "canonical-json-v1":
        errors.append("plot manifest declares an unsupported result hash method")
    if manifest.get("result_document_sha256") != canonical_json_sha256(document):
        errors.append("plot manifest result-document SHA-256 does not match")
    if manifest.get("plot_generator") != "python/mathzip_bench/plots.py":
        errors.append("plot manifest identifies an unexpected generator path")
    if not generator_path.is_file():
        errors.append(f"plot generator is missing: {generator_path}")
    elif manifest.get("plot_generator_sha256") != sha256_file(generator_path):
        errors.append("plot manifest generator SHA-256 does not match")

    generated = manifest.get("generated")
    if not isinstance(generated, list) or any(
        not isinstance(name, str)
        or not name
        or Path(name).name != name
        or name == "plot_manifest.json"
        for name in generated
    ):
        errors.append("plot manifest generated list is invalid")
        generated_names: list[str] = []
    else:
        generated_names = generated
        if len(set(generated_names)) != len(generated_names):
            errors.append("plot manifest generated list contains duplicates")

    artifacts = manifest.get("artifacts_sha256")
    expected_names = {"plot_data.json", *generated_names}
    if not isinstance(artifacts, Mapping):
        errors.append("plot manifest artifacts_sha256 must be a mapping")
    else:
        actual_names = set(artifacts)
        if actual_names != expected_names:
            errors.append(
                "plot manifest artifact keys differ from plot_data/generated files"
            )
        for name, expected_digest in artifacts.items():
            if (
                not isinstance(name, str)
                or not name
                or Path(name).name != name
                or not isinstance(expected_digest, str)
                or len(expected_digest) != 64
            ):
                errors.append(f"plot manifest artifact entry is invalid: {name!r}")
                continue
            artifact_path = plot_directory / name
            if not artifact_path.is_file():
                errors.append(f"plot artifact is missing: {name}")
            elif sha256_file(artifact_path) != expected_digest:
                errors.append(f"plot artifact SHA-256 does not match: {name}")
    return errors, []


def _close(first: Any, second: Any, tolerance: float = 1e-9) -> bool:
    if first is None or second is None:
        return first is second
    try:
        return math.isclose(
            float(first), float(second), rel_tol=tolerance, abs_tol=tolerance
        )
    except (TypeError, ValueError):
        return first == second


def _median(trials: Iterable[Mapping[str, Any]], field: str) -> float | None:
    values = [
        float(trial[field])
        for trial in trials
        if trial.get("status") == "ok" and trial.get(field) is not None
    ]
    return statistics.median(values) if values else None


def _has_one_metrics_output_path(command: list[str]) -> bool:
    positions = [
        index
        for index, argument in enumerate(command)
        if argument == "--metrics-output"
    ]
    if len(positions) != 1:
        return False
    index = positions[0]
    return (
        index + 1 < len(command)
        and bool(command[index + 1])
        and not command[index + 1].startswith("-")
    )


def _tree_hash_from_manifest(manifest: list[Mapping[str, Any]]) -> str | None:
    digest = hashlib.sha256()
    seen: set[str] = set()
    for entry in manifest:
        path = entry.get("path")
        size = entry.get("size_bytes")
        checksum = entry.get("sha256")
        if (
            not isinstance(path, str)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(checksum, str)
            or len(checksum) != 64
            or path in seen
            or path.startswith("/")
            or ".." in Path(path).parts
        ):
            return None
        try:
            checksum_bytes = bytes.fromhex(checksum)
        except ValueError:
            return None
        seen.add(path)
        encoded_path = path.encode("utf-8", errors="surrogateescape")
        digest.update(len(encoded_path).to_bytes(8, "little"))
        digest.update(encoded_path)
        digest.update(size.to_bytes(8, "little"))
        digest.update(checksum_bytes)
    return digest.hexdigest()


def _expected_grid_errors(
    document: Mapping[str, Any],
    results: list[Any],
) -> list[str]:
    grid = document.get("expected_grid")
    if grid is None:
        # Results created before row checkpoints did not carry an exact grid.
        return []
    if not isinstance(grid, Mapping):
        return ["expected_grid must be a mapping"]
    errors: list[str] = []
    if grid.get("schema_version") != EXPECTED_GRID_SCHEMA_VERSION:
        errors.append("expected_grid has an unsupported schema_version")
    body = {key: value for key, value in grid.items() if key != "sha256"}
    encoded = json.dumps(
        body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if grid.get("sha256") != hashlib.sha256(encoded).hexdigest():
        errors.append("expected_grid SHA-256 does not match its contents")
    if grid.get("key_fields") != [
        "corpus",
        "input_path",
        "codec",
        "threads",
    ]:
        errors.append("expected_grid declares unexpected key_fields")

    inputs = grid.get("inputs")
    codecs = grid.get("codecs")
    if not isinstance(inputs, list):
        errors.append("expected_grid.inputs must be a list")
        inputs = []
    if not isinstance(codecs, list):
        errors.append("expected_grid.codecs must be a list")
        codecs = []
    input_keys: list[tuple[str, str]] = []
    input_evidence: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, entry in enumerate(inputs):
        if (
            not isinstance(entry, Mapping)
            or not isinstance(entry.get("corpus"), str)
            or not isinstance(entry.get("input_path"), str)
        ):
            errors.append(f"expected_grid.inputs[{index}] has an invalid key")
            continue
        key = (entry["corpus"], entry["input_path"])
        input_keys.append(key)
        input_evidence[key] = entry
        if entry.get("missing_reason") is None:
            expected_verified = (
                isinstance(entry.get("expected_input_sha256"), str)
                and len(entry["expected_input_sha256"]) == 64
                and isinstance(entry.get("expected_input_size"), int)
                and not isinstance(entry.get("expected_input_size"), bool)
                and entry.get("actual_input_sha256")
                == entry.get("expected_input_sha256")
                and entry.get("actual_input_size")
                == entry.get("expected_input_size")
            )
            if entry.get("input_manifest_verified") is not expected_verified:
                errors.append(
                    f"expected_grid.inputs[{index}] has inconsistent input evidence"
                )
        elif entry.get("input_manifest_verified") is not None:
            errors.append(
                f"expected_grid.inputs[{index}] missing input evidence must be null"
            )
    if len(set(input_keys)) != len(input_keys):
        errors.append("expected_grid.inputs contains duplicate keys")

    codec_keys: list[tuple[str, int]] = []
    for index, entry in enumerate(codecs):
        if (
            not isinstance(entry, Mapping)
            or not isinstance(entry.get("codec"), str)
            or not isinstance(entry.get("threads"), int)
            or isinstance(entry.get("threads"), bool)
            or entry["threads"] < 1
        ):
            errors.append(f"expected_grid.codecs[{index}] has an invalid key")
            continue
        codec_keys.append((entry["codec"], entry["threads"]))
    if len(set(codec_keys)) != len(codec_keys):
        errors.append("expected_grid.codecs contains duplicate keys")
    definitions = document.get("codec_definitions")
    if isinstance(definitions, list):
        definition_keys: list[tuple[str, int]] = []
        for index, entry in enumerate(definitions):
            if (
                not isinstance(entry, Mapping)
                or not isinstance(entry.get("name"), str)
                or not isinstance(entry.get("threads"), int)
                or isinstance(entry.get("threads"), bool)
                or entry["threads"] < 1
            ):
                errors.append(
                    f"codec_definitions[{index}] has an invalid codec/thread key"
                )
                continue
            definition_keys.append((entry["name"], entry["threads"]))
        if Counter(definition_keys) != Counter(codec_keys):
            errors.append(
                "expected_grid codec/thread keys differ from codec_definitions"
            )

    expected = {
        (corpus, input_path, codec, threads)
        for corpus, input_path in input_keys
        for codec, threads in codec_keys
    }
    declared_count = grid.get("expected_result_count")
    if declared_count != len(input_keys) * len(codec_keys):
        errors.append(
            "expected_grid.expected_result_count differs from its input/codec product"
        )
    actual_keys: list[tuple[str, str, str, int]] = []
    for index, row in enumerate(results):
        if not isinstance(row, Mapping):
            continue
        key = _result_grid_key(row)
        if key is None:
            errors.append(f"results[{index}] has an invalid result grid key")
            continue
        actual_keys.append(key)
    counts = Counter(actual_keys)
    duplicates = sorted(
        (key for key, count in counts.items() if count > 1),
        key=repr,
    )
    if duplicates:
        errors.append(
            f"result grid contains {len(duplicates)} duplicate key(s): "
            f"{duplicates[:3]}"
        )
    actual = set(actual_keys)
    missing = sorted(expected - actual, key=repr)
    extra = sorted(actual - expected, key=repr)
    if missing:
        errors.append(
            f"result grid is missing {len(missing)} expected key(s): {missing[:3]}"
        )
    if extra:
        errors.append(
            f"result grid contains {len(extra)} unexpected key(s): {extra[:3]}"
        )
    if len(results) != declared_count:
        errors.append("result row count differs from expected_grid")

    methodology = document.get("methodology")
    if isinstance(methodology, Mapping):
        before = methodology.get("expected_grid_sha256_before")
        after = methodology.get("expected_grid_sha256_after")
        stable = methodology.get("input_grid_stable_during_run")
        if before is not None or after is not None or stable is not None:
            if before != grid.get("sha256"):
                errors.append(
                    "methodology expected-grid before hash differs from expected_grid"
                )
            hashes_match = (
                isinstance(before, str)
                and isinstance(after, str)
                and before == after
            )
            if stable is not hashes_match:
                errors.append(
                    "input_grid_stable_during_run is inconsistent with grid hashes"
                )

    evidence_fields = {
        "input_manifest": "input_manifest",
        "input_manifest_sha256": "input_manifest_sha256",
        "input_manifest_verified": "input_manifest_verified",
        "actual_input_sha256": "input_sha256",
        "actual_input_size": "original_bytes",
    }
    for index, row in enumerate(results):
        if not isinstance(row, Mapping):
            continue
        result_key = _result_grid_key(row)
        if result_key is None:
            continue
        evidence = input_evidence.get((result_key[0], result_key[1]))
        if evidence is None:
            continue
        for grid_field, row_field in evidence_fields.items():
            if evidence.get(grid_field) != row.get(row_field):
                errors.append(
                    f"results[{index}]: {row_field} differs from expected_grid evidence"
                )
    return errors


def _resume_metadata_errors(document: Mapping[str, Any]) -> list[str]:
    resume = document.get("resume")
    if resume is None:
        return []
    if not isinstance(resume, Mapping):
        return ["resume metadata must be a mapping"]
    errors: list[str] = []
    if (
        resume.get("checkpoint_schema_version")
        != "mathzip-benchmark-checkpoint-v1"
    ):
        errors.append("resume checkpoint_schema_version is unsupported")
    intervals = resume.get("execution_intervals")
    if not isinstance(intervals, list) or not intervals:
        return ["resume.execution_intervals must be a non-empty list"]
    active_values: list[float] = []
    for index, interval in enumerate(intervals):
        if not isinstance(interval, Mapping):
            errors.append(f"resume.execution_intervals[{index}] is invalid")
            continue
        if interval.get("index") != index:
            errors.append(
                f"resume.execution_intervals[{index}].index is inconsistent"
            )
        for field in ("started_at_utc", "checkpointed_through_utc"):
            if not isinstance(interval.get(field), str):
                errors.append(
                    f"resume.execution_intervals[{index}].{field} is invalid"
                )
        active = interval.get("active_seconds")
        if (
            not isinstance(active, (int, float))
            or isinstance(active, bool)
            or not math.isfinite(float(active))
            or active < 0
        ):
            errors.append(
                f"resume.execution_intervals[{index}].active_seconds is invalid"
            )
        else:
            active_values.append(float(active))
        if interval.get("resumed") is not (index > 0):
            errors.append(
                f"resume.execution_intervals[{index}].resumed is inconsistent"
            )
        interrupted = interval.get("interrupted")
        completed_at = interval.get("completed_at_utc")
        ended_at = interval.get("ended_at_utc")
        if not isinstance(interrupted, bool):
            errors.append(
                f"resume.execution_intervals[{index}].interrupted is invalid"
            )
        elif interrupted:
            if completed_at is not None or not isinstance(ended_at, str):
                errors.append(
                    f"resume.execution_intervals[{index}] has inconsistent "
                    "interruption metadata"
                )
        elif completed_at is None and index < len(intervals) - 1:
            errors.append(
                f"resume.execution_intervals[{index}] is an unfinished "
                "non-final interval"
            )
        elif completed_at is not None and (
            not isinstance(completed_at, str) or ended_at != completed_at
        ):
            errors.append(
                f"resume.execution_intervals[{index}] has inconsistent "
                "completion metadata"
            )
    if resume.get("resume_count") != len(intervals) - 1:
        errors.append("resume_count differs from execution interval count")
    if not _close(resume.get("active_elapsed_seconds"), sum(active_values)):
        errors.append("resume active_elapsed_seconds is inconsistent")
    if resume.get("expected_result_count") != document.get("result_count"):
        errors.append("resume expected_result_count differs from result_count")
    if resume.get("checkpoint_rows_durable") != document.get("result_count"):
        errors.append("resume checkpoint row count differs from result_count")
    first_interval = intervals[0]
    if isinstance(first_interval, Mapping):
        if first_interval.get("started_at_utc") != document.get("started_at_utc"):
            errors.append(
                "resume metadata does not preserve the original start time"
            )
    last_interval = intervals[-1]
    if isinstance(last_interval, Mapping):
        if (
            last_interval.get("interrupted") is not False
            or not isinstance(last_interval.get("completed_at_utc"), str)
        ):
            errors.append("final resume execution interval is not completed")
    elapsed = document.get("elapsed_seconds")
    active = resume.get("active_elapsed_seconds")
    if (
        isinstance(elapsed, (int, float))
        and isinstance(active, (int, float))
        and active > elapsed + 1e-6
    ):
        errors.append("resume active elapsed time exceeds wall elapsed time")
    return errors


def validate_result_document(
    document: Mapping[str, Any],
    *,
    strict: bool = False,
    allow_failures: bool = False,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if document.get("schema_version") != RESULT_SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {RESULT_SCHEMA_VERSION!r}, got "
            f"{document.get('schema_version')!r}"
        )
    for field in ("run_id", "profile", "source", "system", "methodology", "results"):
        if field not in document:
            errors.append(f"top-level field is missing: {field}")
    methodology = document.get("methodology")
    checkpoint_claimed = (
        isinstance(methodology, Mapping)
        and (
            methodology.get("checkpoint_after_every_completed_row") is True
            or methodology.get("resume_identity_fail_closed") is True
        )
    )
    if (
        checkpoint_claimed
        or document.get("expected_grid") is not None
        or document.get("resume") is not None
    ):
        if document.get("expected_grid") is None:
            errors.append("checkpoint-aware result is missing expected_grid")
        if document.get("resume") is None:
            errors.append("checkpoint-aware result is missing resume metadata")
    source = document.get("source")
    if isinstance(source, Mapping):
        tree_hash = source.get("source_tree_sha256")
        if not isinstance(tree_hash, str) or len(tree_hash) != 64:
            errors.append("source_tree_sha256 must be a 64-character digest")
        tree_hash_after = source.get("source_tree_sha256_after")
        if not isinstance(tree_hash_after, str) or len(tree_hash_after) != 64:
            errors.append("source_tree_sha256_after must be a 64-character digest")
        if source.get("source_stable_during_run") is not (
            tree_hash == tree_hash_after
            and source.get("source_revision") == source.get("source_revision_after")
            and source.get("source_dirty") == source.get("source_dirty_after")
            and source.get("source_file_count") == source.get("source_file_count_after")
        ):
            errors.append("source_stable_during_run is inconsistent with source metadata")
        manifest = source.get("source_tree_manifest")
        if manifest is not None:
            if not isinstance(manifest, list):
                errors.append("source_tree_manifest must be a list or null")
            else:
                calculated = _tree_hash_from_manifest(manifest)
                if calculated is None:
                    errors.append("source_tree_manifest contains invalid entries")
                elif calculated != tree_hash:
                    errors.append("source_tree_manifest does not match source_tree_sha256")
    elif source is not None:
        errors.append("source metadata must be a mapping")
    results = document.get("results")
    if not isinstance(results, list):
        errors.append("results must be a list")
        return errors, warnings
    if document.get("result_count") != len(results):
        errors.append("result_count does not equal len(results)")
    errors.extend(_expected_grid_errors(document, results))
    errors.extend(_resume_metadata_errors(document))
    actual_failures = sum(
        isinstance(row, dict) and row.get("status") != "ok" for row in results
    )
    if document.get("failure_count") != actual_failures:
        errors.append("failure_count does not match result statuses")
    methodology = document.get("methodology", {})
    mathzip_metrics_protocol_declared = (
        isinstance(methodology, Mapping)
        and (
            "mathzip_metrics_mode" in methodology
            or "mathzip_metrics_in_compression_timing" in methodology
        )
    )
    mathzip_metrics_mode = (
        methodology.get("mathzip_metrics_mode")
        if isinstance(methodology, Mapping)
        else None
    )
    mathzip_metrics_in_timing = (
        methodology.get("mathzip_metrics_in_compression_timing")
        if isinstance(methodology, Mapping)
        else None
    )
    if mathzip_metrics_protocol_declared:
        if mathzip_metrics_mode not in ("probe", "inline"):
            errors.append(
                "methodology.mathzip_metrics_mode must be 'probe' or 'inline'"
            )
        if not isinstance(mathzip_metrics_in_timing, bool):
            errors.append(
                "methodology.mathzip_metrics_in_compression_timing must be boolean"
            )
        elif mathzip_metrics_mode in ("probe", "inline") and (
            mathzip_metrics_in_timing
            is not (mathzip_metrics_mode == "inline")
        ):
            errors.append(
                "mathzip_metrics_in_compression_timing is inconsistent with "
                "mathzip_metrics_mode"
            )
    expected_timing_compliance = (
        isinstance(methodology, Mapping)
        and methodology.get("warmups", 0) >= 1
        and methodology.get("repeats", 0) >= 3
    )
    if document.get("timing_protocol_compliant") is not expected_timing_compliance:
        errors.append("timing_protocol_compliant is inconsistent with methodology")
    if document.get("scientifically_compliant_run") is not document.get(
        "publication_evidence_complete"
    ):
        errors.append(
            "scientifically_compliant_run must match publication_evidence_complete"
        )
    evidence_gaps = document.get("publication_evidence_gaps")
    if not isinstance(evidence_gaps, list) or any(
        not isinstance(value, str) for value in evidence_gaps
    ):
        errors.append("publication_evidence_gaps must be a list of strings")
    elif document.get("publication_evidence_complete") is not (
        len(evidence_gaps) == 0
    ):
        errors.append(
            "publication_evidence_complete is inconsistent with evidence gaps"
        )
    config = document.get("config")
    if isinstance(config, Mapping) and (
        "sha256_after" in config or "stable_during_run" in config
    ):
        before = config.get("sha256")
        after = config.get("sha256_after")
        stable = config.get("stable_during_run")
        hashes_match = (
            isinstance(before, str)
            and isinstance(after, str)
            and before == after
        )
        if stable is not hashes_match:
            errors.append(
                "config.stable_during_run is inconsistent with config SHA-256 values"
            )
        if (
            isinstance(methodology, Mapping)
            and methodology.get("config_stable_during_run") is not stable
        ):
            errors.append(
                "methodology config stability differs from config metadata"
            )
    if isinstance(methodology, Mapping) and (
        "host_identity_sha256_before" in methodology
        or "host_identity_sha256_after" in methodology
        or "host_identity_stable_during_run" in methodology
    ):
        before = methodology.get("host_identity_sha256_before")
        after = methodology.get("host_identity_sha256_after")
        stable = methodology.get("host_identity_stable_during_run")
        hashes_match = (
            isinstance(before, str)
            and isinstance(after, str)
            and before == after
        )
        if stable is not hashes_match:
            errors.append(
                "host_identity_stable_during_run is inconsistent with host hashes"
            )

    run_id = document.get("run_id")
    seen: set[tuple[Any, ...]] = set()
    for index, row in enumerate(results):
        label = f"results[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label} is not a mapping")
            continue
        key = _result_grid_key(row)
        if key is None:
            errors.append(f"{label}: invalid corpus/input/codec/threads key")
        elif key in seen:
            errors.append(f"{label} duplicates corpus/input/codec/threads key {key}")
        else:
            seen.add(key)
        if row.get("schema_version") != RESULT_SCHEMA_VERSION:
            errors.append(f"{label}: wrong schema_version")
        if row.get("run_id") != run_id:
            errors.append(f"{label}: run_id differs from document")
        for field in (
            "input_license",
            "input_provenance",
            "input_manifest",
            "input_manifest_sha256",
            "input_manifest_verified",
        ):
            if field not in row:
                errors.append(f"{label}: missing {field}")
        if strict and row.get("input_manifest_verified") is not True:
            errors.append(f"{label}: input manifest verification is not true")
        status = row.get("status")
        if status != "ok":
            message = f"{label}: status={status}: {row.get('error') or 'no error detail'}"
            (errors if strict and not allow_failures else warnings).append(message)
            continue
        requested = row.get("requested_repeats")
        successful = row.get("successful_repeats")
        if not isinstance(requested, int) or requested < 1:
            errors.append(f"{label}: invalid requested_repeats")
        elif strict and requested < 3:
            errors.append(f"{label}: fewer than three measured repetitions")
        if successful != requested:
            errors.append(f"{label}: ok row does not have all requested repetitions")
        trials = row.get("trials")
        if not isinstance(trials, list):
            errors.append(f"{label}: trials is not a list")
            continue
        if len(trials) != requested:
            errors.append(f"{label}: trial count differs from requested_repeats")
        if any(trial.get("status") != "ok" for trial in trials):
            errors.append(f"{label}: ok aggregate contains a failed trial")
        if not row.get("roundtrip_verified"):
            errors.append(f"{label}: roundtrip_verified is false")
        input_hash = row.get("input_sha256")
        for trial_index, trial in enumerate(trials):
            if not trial.get("roundtrip_verified"):
                errors.append(
                    f"{label}.trials[{trial_index}]: roundtrip was not verified"
                )
            if trial.get("restored_sha256") != input_hash:
                errors.append(
                    f"{label}.trials[{trial_index}]: restored SHA-256 mismatch"
                )
            if trial.get("compressed_bytes") is None:
                errors.append(
                    f"{label}.trials[{trial_index}]: compressed size is missing"
                )
        median_bytes = _median(trials, "compressed_bytes")
        if median_bytes is not None and int(median_bytes) != row.get("compressed_bytes"):
            errors.append(f"{label}: compressed_bytes is not the trial median")
        for field in (
            "compression_seconds",
            "decompression_seconds",
            "compression_cpu_seconds",
            "decompression_cpu_seconds",
        ):
            expected = _median(trials, field)
            if not _close(row.get(field), expected):
                errors.append(f"{label}: {field} is not the trial median")
        original = row.get("original_bytes")
        compressed = row.get("compressed_bytes")
        if isinstance(original, int) and isinstance(compressed, int):
            expected_metrics = storage_metrics(original, compressed)
            for field, expected in expected_metrics.items():
                if not _close(row.get(field), expected):
                    errors.append(f"{label}: inconsistent derived metric {field}")
        else:
            errors.append(f"{label}: byte counts must be integers")
        archive_hashes = {trial.get("archive_sha256") for trial in trials}
        archive_sizes = {trial.get("compressed_bytes") for trial in trials}
        deterministic = len(archive_hashes) == 1
        if row.get("deterministic_archive") is not deterministic:
            errors.append(f"{label}: deterministic_archive is inconsistent")
        if deterministic and row.get("archive_sha256") != next(iter(archive_hashes)):
            errors.append(f"{label}: aggregate archive_sha256 is inconsistent")
        if row.get("codec_family") == "mathzip" and (
            not deterministic or len(archive_sizes) != 1
        ):
            errors.append(
                f"{label}: nondeterministic MathZip output must not have status=ok"
            )
        if strict and row.get("codec_family") == "mathzip":
            inspection = row.get("mathzip_inspection")
            if methodology.get("collect_mathzip_inspection") is not True:
                errors.append(
                    f"{label}: strict MathZip results require inspection collection"
                )
            inspection_error = mathzip_inspection_error(inspection)
            if inspection_error is not None:
                errors.append(
                    f"{label}: MathZip inspection failed: {inspection_error}"
                )
            for trial_index, trial in enumerate(trials):
                trial_inspection = trial.get("inspection")
                trial_inspection_error = mathzip_inspection_error(trial_inspection)
                if trial_inspection_error is not None:
                    errors.append(
                        f"{label}.trials[{trial_index}]: MathZip inspection failed: "
                        f"{trial_inspection_error}"
                    )
                timing_error = mathzip_encode_metrics_error(
                    trial.get("compression_metrics")
                )
                if timing_error is not None:
                    errors.append(
                        f"{label}.trials[{trial_index}]: MathZip encoder metrics "
                        f"failed: {timing_error}"
                    )
                if (
                    mathzip_metrics_protocol_declared
                    and mathzip_metrics_mode in ("probe", "inline")
                ):
                    compression_command = trial.get("compression_command")
                    metrics_command = trial.get("compression_metrics_command")
                    if (
                        not isinstance(compression_command, list)
                        or not compression_command
                        or any(
                            not isinstance(argument, str)
                            for argument in compression_command
                        )
                        or not isinstance(metrics_command, list)
                        or not metrics_command
                        or any(
                            not isinstance(argument, str)
                            for argument in metrics_command
                        )
                    ):
                        errors.append(
                            f"{label}.trials[{trial_index}]: MathZip metrics "
                            "command provenance is missing"
                        )
                    elif mathzip_metrics_mode == "inline":
                        if metrics_command != compression_command:
                            errors.append(
                                f"{label}.trials[{trial_index}]: inline MathZip "
                                "metrics command must equal the timed compression "
                                "command"
                            )
                        if not _has_one_metrics_output_path(
                            compression_command
                        ):
                            errors.append(
                                f"{label}.trials[{trial_index}]: inline timed "
                                "compression command requires exactly one "
                                "--metrics-output with a following path"
                            )
                    else:
                        if metrics_command == compression_command:
                            errors.append(
                                f"{label}.trials[{trial_index}]: probe MathZip "
                                "metrics command must differ from the timed "
                                "compression command"
                            )
                        if "--metrics-output" in compression_command:
                            errors.append(
                                f"{label}.trials[{trial_index}]: probe timed "
                                "compression command unexpectedly contains "
                                "--metrics-output"
                            )
                        if not _has_one_metrics_output_path(metrics_command):
                            errors.append(
                                f"{label}.trials[{trial_index}]: probe MathZip "
                                "metrics command requires exactly one "
                                "--metrics-output with a following path"
                            )
            trial_metrics = [
                trial.get("compression_metrics") for trial in trials
            ]
            provenance_declared = any(
                isinstance(metrics, Mapping)
                and any(
                    field in metrics
                    for field in MATHZIP_SEARCH_PROVENANCE_FIELDS
                )
                for metrics in trial_metrics
            )
            if provenance_declared:
                provenance_records = [
                    {
                        field: metrics[field]
                        for field in MATHZIP_SEARCH_PROVENANCE_FIELDS
                    }
                    for metrics in trial_metrics
                    if isinstance(metrics, Mapping)
                    and mathzip_encode_metrics_error(metrics) is None
                    and all(
                        field in metrics
                        for field in MATHZIP_SEARCH_PROVENANCE_FIELDS
                    )
                ]
                signatures = {
                    json.dumps(
                        record, sort_keys=True, separators=(",", ":")
                    )
                    for record in provenance_records
                }
                if (
                    len(provenance_records) != len(trials)
                    or len(signatures) != 1
                ):
                    errors.append(
                        f"{label}: MathZip search provenance differs across "
                        "measured repetitions"
                    )
                else:
                    expected_provenance = provenance_records[0]
                    if (
                        row.get("mathzip_search_provenance")
                        != expected_provenance
                    ):
                        errors.append(
                            f"{label}: aggregate MathZip search provenance "
                            "is inconsistent"
                        )
                    if (
                        row.get("mathzip_search_provenance_consistent")
                        is not True
                    ):
                        errors.append(
                            f"{label}: MathZip search provenance consistency "
                            "flag is not true"
                        )
            required_integer_metrics = (
                "segment_count",
                "model_parameter_bytes",
                "partition_metadata_bytes",
                "residual_bytes",
                "container_overhead_bytes",
                "actual_residual_coded_bytes",
                "math_segments_winning_raw",
                "math_segments_winning_zstd",
            )
            for field in required_integer_metrics:
                value = row.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    errors.append(
                        f"{label}: missing or invalid MathZip inspection metric {field}"
                    )
            for field in ("model_distribution", "residual_distribution"):
                if not isinstance(row.get(field), Mapping):
                    errors.append(
                        f"{label}: missing or invalid MathZip inspection metric {field}"
                    )
            if row.get("transform") is None:
                errors.append(f"{label}: missing MathZip inspection metric transform")
            for field in (
                "search_seconds",
                "model_fitting_seconds",
                "residual_coding_seconds",
            ):
                value = row.get(field)
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not 0.0 <= float(value) < float("inf")
                ):
                    errors.append(
                        f"{label}: missing or invalid MathZip encoder metric {field}"
                    )
                phase_values = [
                    float(trial["compression_metrics"][field])
                    for trial in trials
                    if mathzip_encode_metrics_error(
                        trial.get("compression_metrics")
                    )
                    is None
                ]
                expected_phase = (
                    statistics.median(phase_values) if phase_values else None
                )
                if not _close(value, expected_phase):
                    errors.append(
                        f"{label}: {field} is not the encoder-metric median"
                    )
            if original != 0:
                for field in (
                    "mean_segment_size",
                    "median_segment_size",
                    "raw_model_percent",
                    "raw_fallback_percent",
                    "estimated_residual_entropy",
                ):
                    value = row.get(field)
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        errors.append(
                            f"{label}: missing or invalid MathZip inspection metric "
                            f"{field}"
                        )
        breakdown = (
            row.get("container_overhead_bytes"),
            row.get("partition_metadata_bytes"),
            row.get("model_parameter_bytes"),
            row.get("residual_bytes"),
        )
        if (
            row.get("codec_family") == "mathzip"
            and isinstance(row.get("compressed_bytes"), int)
            and all(isinstance(value, int) for value in breakdown)
            and sum(breakdown) != row["compressed_bytes"]
        ):
            errors.append(
                f"{label}: MathZip byte breakdown does not sum to compressed_bytes"
            )
    if strict:
        if document.get("expected_grid") is not None:
            if methodology.get("input_grid_stable_during_run") is not True:
                errors.append("strict result requires a stable input grid")
            config_metadata = document.get("config")
            if (
                not isinstance(config_metadata, Mapping)
                or config_metadata.get("stable_during_run") is not True
            ):
                errors.append("strict result requires a stable config")
            if methodology.get("host_identity_stable_during_run") is not True:
                errors.append("strict result requires a stable host execution identity")
        if methodology.get("warmups", 0) < 1:
            errors.append("strict verification requires at least one warm-up")
        if methodology.get("repeats", 0) < 3:
            errors.append("strict verification requires at least three repeats")
        if not document.get("timing_protocol_compliant"):
            errors.append("run is not timing-protocol compliant")
        if not document.get("scientifically_compliant_run"):
            errors.append("run is marked scientifically non-compliant")
        source = document.get("source", {})
        if not isinstance(source, Mapping):
            errors.append("strict verification requires source metadata")
        else:
            revision_evidence = bool(source.get("source_revision")) and (
                source.get("source_dirty") is False
            )
            manifest_evidence = (
                source.get("source_revision") is None
                and isinstance(source.get("source_tree_manifest"), list)
                and bool(source.get("source_tree_manifest"))
            )
            if not (revision_evidence or manifest_evidence):
                errors.append(
                    "strict verification requires a clean revision or an exact "
                    "source-tree manifest when no HEAD exists"
                )
            tree_hash = source.get("source_tree_sha256")
            if not isinstance(tree_hash, str) or len(tree_hash) != 64:
                errors.append("strict verification requires a source-tree SHA-256")
        if methodology.get("binaries_stable_during_run") is not True:
            errors.append("strict verification requires stable executable hashes")
        if methodology.get("available_executable_hashes_complete") is not True:
            errors.append(
                "strict verification requires SHA-256 evidence for available executables"
            )
        if methodology.get("mathzip_build_info_stable_during_run") is not True:
            errors.append("strict verification requires stable MathZip build metadata")
        if methodology.get("mathzip_build_matches_source") is not True:
            errors.append("strict verification requires MathZip binary/source identity")
        if methodology.get("source_stable_during_run") is not True:
            errors.append("strict verification requires a stable source tree")
        if methodology.get("input_rights_and_provenance_complete") is not True:
            errors.append(
                "strict verification requires input license/terms and provenance"
            )
        if methodology.get("input_manifest_integrity_complete") is not True:
            errors.append("strict verification requires input manifest integrity")
        if methodology.get("codec_filter") is not None:
            errors.append("strict verification rejects codec-filtered diagnostic runs")
        definitions = document.get("codec_definitions", [])
        if not isinstance(definitions, list) or any(
            definition.get("available")
            and definition.get("family") != "raw"
            and not definition.get("executable_sha256")
            for definition in definitions
            if isinstance(definition, Mapping)
        ):
            errors.append(
                "strict verification requires SHA-256 for each available executable"
            )
        if isinstance(definitions, list) and isinstance(source, Mapping):
            for index, definition in enumerate(definitions):
                if (
                    not isinstance(definition, Mapping)
                    or definition.get("family") != "mathzip"
                    or not definition.get("available")
                ):
                    continue
                build_info = definition.get("build_info")
                if (
                    not isinstance(build_info, Mapping)
                    or not isinstance(source.get("source_revision"), str)
                    or not source.get("source_revision")
                    or build_info.get("source_revision")
                    != source.get("source_revision")
                    or build_info.get("source_dirty") is not False
                ):
                    errors.append(
                        f"codec_definitions[{index}]: MathZip build does not "
                        "match the clean source revision"
                    )
                if (
                    mathzip_metrics_protocol_declared
                    and mathzip_metrics_mode in ("probe", "inline")
                ):
                    template = definition.get("compression_command_template")
                    if (
                        not isinstance(template, list)
                        or not template
                        or any(
                            not isinstance(argument, str)
                            for argument in template
                        )
                    ):
                        errors.append(
                            f"codec_definitions[{index}]: timed MathZip "
                            "compression command template is missing"
                        )
                    elif (
                        mathzip_metrics_mode == "inline"
                        and not _has_one_metrics_output_path(template)
                    ) or (
                        mathzip_metrics_mode == "probe"
                        and "--metrics-output" in template
                    ):
                        errors.append(
                            f"codec_definitions[{index}]: timed MathZip "
                            "compression command template disagrees with "
                            "mathzip_metrics_mode"
                        )
    return errors, warnings


def validate_csv(
    csv_path: Path, results: list[Mapping[str, Any]]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not csv_path.is_file():
        return [f"CSV file is missing: {csv_path}"], warnings
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != CSV_FIELDS:
            errors.append("CSV header differs from the declared schema")
        rows = list(reader)
    if len(rows) != len(results):
        errors.append(f"CSV has {len(rows)} rows; JSON has {len(results)} rows")
        return errors, warnings

    def expected_csv_value(json_row: Mapping[str, Any], field: str) -> str:
        aliases = {
            "model_distribution_json": "model_distribution",
            "residual_distribution_json": "residual_distribution",
            "command_lines_json": "command_lines",
            "tags_json": "tags",
        }
        value = json_row.get(aliases.get(field, field))
        if isinstance(value, (dict, list)):
            return json.dumps(
                value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
        if isinstance(value, bool):
            return "true" if value else "false"
        return "" if value is None else str(value)

    for index, (csv_row, json_row) in enumerate(zip(rows, results)):
        if None in csv_row:
            errors.append(f"CSV row {index}: contains surplus undeclared cells")
        for field in CSV_FIELDS:
            if csv_row.get(field) != expected_csv_value(json_row, field):
                errors.append(f"CSV row {index}: {field} differs from JSON")
    return errors, warnings


def verify_synthetic_manifest(path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot load synthetic manifest: {exc}"], warnings
    if manifest.get("schema_version") != "mathzip-synthetic-corpus-v1":
        errors.append("unexpected synthetic manifest schema")
    root = path.parent
    generation_marker = root / ".generation-in-progress.json"
    if generation_marker.exists():
        errors.append(
            "synthetic corpus has an unfinished generation marker; regenerate "
            "with --force"
        )
    seen: set[str] = set()
    entries_by_path: dict[str, Mapping[str, Any]] = {}
    for index, entry in enumerate(manifest.get("entries", [])):
        if not isinstance(entry, Mapping):
            errors.append(f"synthetic entry {index}: entry is not a mapping")
            continue
        relative = entry.get("path")
        if not isinstance(relative, str) or relative in seen:
            errors.append(f"synthetic entry {index}: invalid or duplicate path")
            continue
        seen.add(relative)
        entries_by_path[relative] = entry
        file_path = (root / relative).resolve()
        try:
            file_path.relative_to(root.resolve())
        except ValueError:
            errors.append(f"synthetic entry {index}: path escapes corpus root")
            continue
        if not file_path.is_file():
            errors.append(f"synthetic entry {index}: missing file {relative}")
            continue
        if file_path.stat().st_size != entry.get("size_bytes"):
            errors.append(f"synthetic entry {index}: size mismatch for {relative}")
        if sha256_file(file_path) != entry.get("sha256"):
            errors.append(f"synthetic entry {index}: SHA-256 mismatch for {relative}")
    actual_files = {
        candidate.relative_to(root).as_posix()
        for candidate in root.rglob("*")
        if candidate.is_file()
        and candidate.relative_to(root).as_posix()
        not in {"manifest.json", "manifest.csv"}
    }
    orphaned = sorted(actual_files - seen)
    if orphaned:
        errors.append(
            f"synthetic corpus contains {len(orphaned)} undeclared file(s): "
            f"{orphaned[:3]}"
        )

    csv_path = root / "manifest.csv"
    if not csv_path.is_file():
        errors.append("synthetic manifest.csv is missing")
    else:
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
        except OSError as exc:
            errors.append(f"cannot load synthetic manifest.csv: {exc}")
            csv_rows = []
        csv_paths = [row.get("path") for row in csv_rows]
        if len(set(csv_paths)) != len(csv_paths):
            errors.append("synthetic manifest.csv contains duplicate paths")
        if set(csv_paths) != seen:
            errors.append("synthetic manifest.csv path set differs from manifest.json")
        for row in csv_rows:
            relative = row.get("path")
            entry = entries_by_path.get(str(relative))
            if entry is None:
                continue
            if (
                row.get("sha256") != entry.get("sha256")
                or row.get("size_bytes") != str(entry.get("size_bytes"))
                or row.get("family") != entry.get("family")
            ):
                errors.append(
                    f"synthetic manifest.csv metadata differs for {relative}"
                )
    required_noise = {0.0, 0.001, 0.01, 0.05, 0.1, 0.25}
    declared_noise = {float(value) for value in manifest.get("noise_densities", [])}
    if not required_noise.issubset(declared_noise):
        warnings.append("synthetic manifest does not contain every required noise density")
    return errors, warnings


def verify_prepared_datasets(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    metadata_paths = sorted(root.glob("*/dataset.json"))
    if not metadata_paths:
        warnings.append(f"no prepared dataset metadata found below {root}")
    for metadata_path in metadata_paths:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{metadata_path}: cannot load: {exc}")
            continue
        dataset_root = metadata_path.parent.resolve()
        for entry in metadata.get("files", []):
            path = (dataset_root / str(entry.get("path", ""))).resolve()
            try:
                path.relative_to(dataset_root)
            except ValueError:
                errors.append(f"{metadata_path}: file path escapes dataset root")
                continue
            if not path.is_file():
                errors.append(f"{metadata_path}: missing {entry.get('path')}")
                continue
            if path.stat().st_size != entry.get("size_bytes"):
                errors.append(f"{metadata_path}: size mismatch for {entry.get('path')}")
            if sha256_file(path) != entry.get("sha256"):
                errors.append(f"{metadata_path}: SHA-256 mismatch for {entry.get('path')}")
    return errors, warnings


def verify_generated_corpora(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    def checked_path(base: Path, relative_value: Any, label: str) -> Path | None:
        if not isinstance(relative_value, str):
            errors.append(f"{label}: invalid path")
            return None
        candidate = (base / relative_value).resolve()
        try:
            candidate.relative_to(base.resolve())
        except ValueError:
            errors.append(f"{label}: path escapes corpus root")
            return None
        return candidate

    mixed_manifest = root / "mixed" / "manifest.json"
    if mixed_manifest.is_file():
        try:
            mixed = json.loads(mixed_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{mixed_manifest}: cannot load: {exc}")
        else:
            for index, entry in enumerate(mixed.get("entries", [])):
                label = f"mixed entry {index}"
                path = checked_path(mixed_manifest.parent, entry.get("path"), label)
                if path is None:
                    continue
                if not path.is_file():
                    errors.append(f"{label}: missing file")
                    continue
                if path.stat().st_size != entry.get("size_bytes"):
                    errors.append(f"{label}: size mismatch")
                if sha256_file(path) != entry.get("sha256"):
                    errors.append(f"{label}: SHA-256 mismatch")
    else:
        warnings.append(f"mixed corpus manifest is missing below {root}")

    snapshots_manifest = root / "git_snapshots" / "manifest.json"
    if snapshots_manifest.is_file():
        try:
            snapshots = json.loads(snapshots_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{snapshots_manifest}: cannot load: {exc}")
        else:
            base = snapshots_manifest.parent
            for version_index, version in enumerate(snapshots.get("versions", [])):
                label = f"snapshot version {version_index}"
                combined = checked_path(base, version.get("path"), label)
                if combined is not None:
                    if not combined.is_file():
                        errors.append(f"{label}: combined stream is missing")
                    else:
                        if combined.stat().st_size != version.get("size_bytes"):
                            errors.append(f"{label}: combined size mismatch")
                        if sha256_file(combined) != version.get("sha256"):
                            errors.append(f"{label}: combined SHA-256 mismatch")
                version_name = version.get("version")
                for member_index, member in enumerate(version.get("members", [])):
                    member_label = f"{label} member {member_index}"
                    member_path = checked_path(
                        base / "versions" / str(version_name),
                        member.get("path"),
                        member_label,
                    )
                    if member_path is None:
                        continue
                    if not member_path.is_file():
                        errors.append(f"{member_label}: file is missing")
                        continue
                    if member_path.stat().st_size != member.get("length"):
                        errors.append(f"{member_label}: size mismatch")
                    if sha256_file(member_path) != member.get("sha256"):
                        errors.append(f"{member_label}: SHA-256 mismatch")
            all_versions = snapshots.get("all_versions", {})
            all_path = checked_path(base, all_versions.get("path"), "all_versions")
            if all_path is not None:
                if not all_path.is_file():
                    errors.append("all_versions: stream is missing")
                else:
                    if all_path.stat().st_size != all_versions.get("size_bytes"):
                        errors.append("all_versions: size mismatch")
                    if sha256_file(all_path) != all_versions.get("sha256"):
                        errors.append("all_versions: SHA-256 mismatch")
    else:
        warnings.append(f"Git snapshot manifest is missing below {root}")
    return errors, warnings
