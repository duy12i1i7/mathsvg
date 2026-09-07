#!/usr/bin/env python3
"""Run real, lossless MathSVG development/validation benchmark trials.

Commands are constructed as argv arrays from a closed adapter catalogue.
Neither baseline catalogue strings nor dataset paths are evaluated by a shell.
Every successful row represents a real archive, a real decompression, and an
SHA-256 verified round trip.

This runner deliberately refuses holdout rows.  Holdout execution belongs to a
separate post-freeze phase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

from mathsvg.python.benchmarks.config import (
    PROFILE_NAMES,
    ConfigError,
    load_ablation_catalog,
    load_profile,
)
from mathsvg.python.benchmarks.freeze import git_identity, sha256_file
from mathsvg.python.benchmarks.protocol import (
    Codec,
    Trial,
    Workload,
    required_repetitions,
    schedule,
    validate_interleaving,
)
from mathsvg.python.benchmarks.schema import TrialRecord, append_jsonl
from mathsvg.python.datasets.manifest import (
    DatasetEntry,
    ManifestError,
    canonical_manifest_sha256,
    load_manifest,
)

GNU_TIME_FORMAT = (
    "user_seconds=%U\n"
    "system_seconds=%S\n"
    "max_rss_kib=%M\n"
    "exit_status=%x\n"
)
MAX_DIAGNOSTIC_BYTES = 16 * 1024
MAX_INSPECT_BYTES = 4 * 1024 * 1024
MAX_U64 = (1 << 64) - 1
MAX_NATIVE_THREADS = 64
NATIVE_COORDINATE_PADDING_BYTES = 7
NATIVE_ARCHIVE_HEADROOM_BYTES = 1024 * 1024
NATIVE_ALGORITHM_ORDER = (
    "whole-block-entropy",
    "whole-block-functions",
    "interval-functions",
    "coordinates",
)
MEASUREMENT_METHOD = "gnu-time-v1-monotonic-wall"
NATIVE_REQUIRED_BREAKDOWN_FIELDS = (
    "container_overhead_bytes",
    "function_graph_bytes",
    "coordinate_bytes",
    "shared_definition_bytes",
    "reference_bytes",
    "parameter_bytes",
    "residual_layer_bytes",
    "literal_leaf_bytes",
    "entropy_metadata_bytes",
    "node_count",
    "shared_node_count",
    "residual_depth_sum",
    "residual_root_count",
    "function_reconstructed_bytes",
    "literal_reconstructed_bytes",
)
NATIVE_OPTIONAL_BREAKDOWN_FIELDS = (
    "literal_only_archive_bytes",
    "pre_entropy_archive_bytes",
    "entropy_saved_bytes",
    "entropy_penalty_bytes",
    "procedural_gain_bytes",
    "procedural_penalty_bytes",
    "index_bytes",
    "coordinate_saved_bytes",
    "dag_saved_bytes",
    "recursive_residual_saved_bytes",
    "symbolic_saved_bytes",
    "search_ns",
)
NATIVE_BREAKDOWN_FIELDS = (
    *NATIVE_REQUIRED_BREAKDOWN_FIELDS,
    *NATIVE_OPTIONAL_BREAKDOWN_FIELDS,
)


class RunnerError(RuntimeError):
    """The requested experiment cannot produce honest benchmark evidence."""


def _source_identity_stable(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> bool:
    """Return whether the complete recorded Git identity stayed unchanged."""

    return dict(before) == dict(after)


@dataclass(frozen=True, slots=True)
class Adapter:
    encode_args: tuple[str, ...]
    decode_args: tuple[str, ...]
    encode_stdout: bool
    decode_stdout: bool


SAFE_BASELINE_ADAPTERS: Mapping[str, Adapter] = {
    "raw": Adapter(
        encode_args=("--", "{input}", "{archive}"),
        decode_args=("--", "{archive}", "{restored}"),
        encode_stdout=False,
        decode_stdout=False,
    ),
    "lz4": Adapter(
        encode_args=("-q", "-1", "{input}", "{archive}"),
        decode_args=("-q", "-d", "{archive}", "{restored}"),
        encode_stdout=False,
        decode_stdout=False,
    ),
    "snappy": Adapter(
        encode_args=("-c", "{input}"),
        decode_args=("-d", "-c", "{archive}"),
        encode_stdout=True,
        decode_stdout=True,
    ),
    "gzip-6": Adapter(
        encode_args=("-n", "-6", "-c", "{input}"),
        decode_args=("-d", "-c", "{archive}"),
        encode_stdout=True,
        decode_stdout=True,
    ),
    "bzip2-9": Adapter(
        encode_args=("-9", "-c", "{input}"),
        decode_args=("-d", "-c", "{archive}"),
        encode_stdout=True,
        decode_stdout=True,
    ),
    "zstd-default": Adapter(
        encode_args=("-q", "-3", "-T1", "-c", "{input}"),
        decode_args=("-q", "-d", "-c", "{archive}"),
        encode_stdout=True,
        decode_stdout=True,
    ),
    "zstd-19": Adapter(
        encode_args=("-q", "-19", "-T1", "-c", "{input}"),
        decode_args=("-q", "-d", "-c", "{archive}"),
        encode_stdout=True,
        decode_stdout=True,
    ),
    "brotli-6": Adapter(
        encode_args=("-q", "6", "-c", "{input}"),
        decode_args=("-d", "-c", "{archive}"),
        encode_stdout=True,
        decode_stdout=True,
    ),
    "brotli-11": Adapter(
        encode_args=("-q", "11", "-c", "{input}"),
        decode_args=("-d", "-c", "{archive}"),
        encode_stdout=True,
        decode_stdout=True,
    ),
    "xz-9e": Adapter(
        encode_args=("-9e", "-T1", "-c", "{input}"),
        decode_args=("-d", "-c", "{archive}"),
        encode_stdout=True,
        decode_stdout=True,
    ),
    "7zip-lzma2-ultra": Adapter(
        encode_args=(
            "a",
            "-bd",
            "-y",
            "-mx=9",
            "-m0=lzma2",
            "-mmt=1",
            "{archive}",
            "{input}",
        ),
        decode_args=("e", "-bd", "-y", "-so", "{archive}"),
        encode_stdout=False,
        decode_stdout=True,
    ),
    "zpaq": Adapter(
        encode_args=(
            "a",
            "{archive}",
            "{input}",
            "-to",
            "payload.bin",
            "-method",
            "5",
            "-threads",
            "1",
            "-noattributes",
        ),
        decode_args=(
            "x",
            "{archive}",
            "payload.bin",
            "-to",
            "{restored}",
            "-threads",
            "1",
            "-force",
        ),
        encode_stdout=False,
        decode_stdout=False,
    ),
    "flac": Adapter(
        encode_args=(
            "--silent",
            "--best",
            "--no-padding",
            "-f",
            "-o",
            "{archive}",
            "{input}",
        ),
        decode_args=(
            "--silent",
            "--decode",
            "-f",
            "-o",
            "{restored}",
            "{archive}",
        ),
        encode_stdout=False,
        decode_stdout=False,
    ),
}


@dataclass(frozen=True, slots=True)
class CodecSpec:
    codec: Codec
    native_mathsvg: bool
    profile: str
    executable: pathlib.Path | None
    executable_sha256: str
    version: str
    adapter: Adapter | None
    config_sha256: str
    unavailable_reason: str
    runtime_profile_sha256: str = ""
    runtime_profile: Mapping[str, object] | None = None
    ablation_id: str = "none"
    disabled_algorithms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedWorkload:
    workload: Workload
    entry: DatasetEntry
    path: pathlib.Path
    error: str


@dataclass(frozen=True, slots=True)
class Measurement:
    wall_ns: int
    cpu_ns: int | None
    peak_rss_bytes: int | None
    returncode: int
    stdout_excerpt: str
    stderr_excerpt: str


@dataclass(frozen=True, slots=True)
class MeasurementFailure(Exception):
    status: str
    message: str
    measurement: Measurement | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True)
class RunContext:
    repository: pathlib.Path
    experiment_id: str
    machine_id: str
    architecture: str
    source_commit: str
    dataset_manifest_sha256: str
    seed: int
    time_binary: pathlib.Path
    time_sha256: str
    compression_timeout_seconds: float
    decompression_timeout_seconds: float
    inspect_timeout_seconds: float
    deterministic_hashes: dict[tuple[str, str, str, int], str]


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def benchmark_repetitions(
    expected_delta_fraction: float | None,
    requested: int | None,
) -> int:
    """Choose a count that cannot silently under-run the <2% rule."""

    minimum = (
        10
        if expected_delta_fraction is None
        else required_repetitions(expected_delta_fraction)
    )
    repetitions = minimum if requested is None else requested
    if repetitions < minimum:
        raise RunnerError(
            f"protocol requires at least {minimum} repetitions"
        )
    return repetitions


def _available_parallelism() -> int:
    """Mirror the native CLI's affinity-aware `available_parallelism`."""

    try:
        affinity = os.sched_getaffinity(0)
    except (AttributeError, OSError):
        affinity = ()
    detected = len(affinity) if affinity else (os.cpu_count() or 1)
    return max(1, min(detected, MAX_NATIVE_THREADS))


def _native_thread_selections(
    requested: Sequence[str],
) -> list[tuple[str, int]]:
    """Return canonical CLI selectors and their recorded effective counts."""

    values = list(requested) if requested else ["1"]
    selections: list[tuple[str, int]] = []
    seen: set[str] = set()
    for value in values:
        if value == "all":
            selector = value
            effective = _available_parallelism()
        elif (
            value.isascii()
            and value.isdigit()
            and len(value) <= 2
            and value == str(int(value, 10))
        ):
            selector = value
            effective = int(value, 10)
            if not 1 <= effective <= MAX_NATIVE_THREADS:
                raise RunnerError(
                    f"native thread count must be 1..{MAX_NATIVE_THREADS} or 'all'"
                )
        else:
            raise RunnerError(
                f"native thread count must be 1..{MAX_NATIVE_THREADS} or 'all'"
            )
        if selector not in seen:
            selections.append((selector, effective))
            seen.add(selector)
    return selections


def _native_ablation_selections(
    requested: Sequence[str],
    catalog: Mapping[str, tuple[str, ...]],
) -> list[tuple[str, tuple[str, ...]]]:
    """Return a canonical baseline/one-or-more real ablation matrix."""

    values = sorted(set(requested)) if requested else ["none"]
    selections: list[tuple[str, tuple[str, ...]]] = []
    order = {name: index for index, name in enumerate(NATIVE_ALGORITHM_ORDER)}
    for identifier in values:
        if identifier == "none":
            selections.append((identifier, ()))
            continue
        disabled = catalog.get(identifier)
        if disabled is None:
            raise RunnerError(f"unknown native ablation {identifier!r}")
        if any(algorithm not in order for algorithm in disabled):
            raise RunnerError(
                f"native ablation {identifier!r} contains an unsupported algorithm"
            )
        canonical = tuple(sorted(disabled, key=order.__getitem__))
        selections.append((identifier, canonical))
    return selections


def _runtime_ablation_identity(disabled_algorithms: Sequence[str]) -> str:
    if not disabled_algorithms:
        return "none"
    return "without-" + "-and-".join(disabled_algorithms)


def _native_limits(original_bytes: int) -> tuple[int, int, int]:
    """Freeze finite CLI limits for one workload without saturating u64."""

    if original_bytes < 0:
        raise RunnerError("native input byte bound must be non-negative")
    if original_bytes > MAX_U64 - NATIVE_COORDINATE_PADDING_BYTES:
        raise RunnerError(
            "native input is too large for coordinate padding headroom"
        )
    input_output_limit = (
        original_bytes + NATIVE_COORDINATE_PADDING_BYTES
    )
    if (
        input_output_limit
        > (MAX_U64 - NATIVE_ARCHIVE_HEADROOM_BYTES) // 2
    ):
        raise RunnerError(
            "native input is too large for the checked archive headroom policy"
        )
    archive_limit = (
        input_output_limit * 2 + NATIVE_ARCHIVE_HEADROOM_BYTES
    )
    return input_output_limit, archive_limit, input_output_limit


def _safe_excerpt(path: pathlib.Path, limit: int = MAX_DIAGNOSTIC_BYTES) -> str:
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    truncated = len(data) > limit
    rendered = data[:limit].decode("utf-8", errors="replace")
    return rendered + ("\n[diagnostic truncated]" if truncated else "")


def _seconds_to_ns(value: str) -> int:
    try:
        result = Decimal(value) * Decimal(1_000_000_000)
    except InvalidOperation as exc:
        raise RunnerError(f"GNU time emitted invalid seconds value {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise RunnerError(f"GNU time emitted invalid seconds value {value!r}")
    return int(result)


def _parse_time_metrics(path: pathlib.Path, wall_ns: int) -> Measurement:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            if not line:
                continue
            name, separator, value = line.partition("=")
            if not separator or name in values:
                raise RunnerError("GNU time metrics are malformed")
            values[name] = value
        required = {
            "user_seconds",
            "system_seconds",
            "max_rss_kib",
            "exit_status",
        }
        if set(values) != required:
            raise RunnerError("GNU time metrics are incomplete")
        user_ns = _seconds_to_ns(values["user_seconds"])
        system_ns = _seconds_to_ns(values["system_seconds"])
        peak_kib = int(values["max_rss_kib"], 10)
        returncode = int(values["exit_status"], 10)
    except (OSError, UnicodeError, ValueError) as exc:
        raise RunnerError(f"could not parse GNU time metrics: {exc}") from exc
    if peak_kib < 0:
        raise RunnerError("GNU time emitted negative max RSS")
    return Measurement(
        wall_ns=wall_ns,
        cpu_ns=user_ns + system_ns,
        peak_rss_bytes=peak_kib * 1024,
        returncode=returncode,
        stdout_excerpt="",
        stderr_excerpt="",
    )


def _benchmark_environment(threads: int) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
            "SOURCE_DATE_EPOCH": "0",
            "OMP_NUM_THREADS": str(threads),
            "RAYON_NUM_THREADS": str(threads),
        }
    )
    return environment


def _host_load_snapshot() -> dict[str, object]:
    """Record ambient host load without claiming process isolation."""

    try:
        one, five, fifteen = os.getloadavg()
        load_average: dict[str, float] | None = {
            "one_minute": one,
            "five_minutes": five,
            "fifteen_minutes": fifteen,
        }
    except OSError:
        load_average = None
    return {
        "logical_cpus": os.cpu_count(),
        "load_average": load_average,
        "resource_isolation": False,
        "cpu_affinity_pinned": False,
        "background_processes_quiesced": False,
    }


def run_measured(
    *,
    time_binary: pathlib.Path,
    argv: Sequence[str],
    timeout_seconds: float,
    scratch: pathlib.Path,
    stdout_path: pathlib.Path | None,
    threads: int,
) -> Measurement:
    """Measure one argv command with GNU time and kill its process group."""

    if not argv or not pathlib.Path(argv[0]).is_absolute():
        raise RunnerError("measured argv must start with an absolute executable")
    if timeout_seconds <= 0:
        raise RunnerError("timeout must be positive")
    metrics_path = scratch / "time.metrics"
    stdout_log = scratch / "stdout.log"
    stderr_log = scratch / "stderr.log"
    command = [
        str(time_binary),
        "-q",
        "-f",
        GNU_TIME_FORMAT,
        "-o",
        str(metrics_path),
        "--",
        *argv,
    ]
    stdout_target = stdout_path if stdout_path is not None else stdout_log
    start_ns = time.monotonic_ns()
    with stdout_target.open("wb") as stdout_handle, stderr_log.open(
        "wb"
    ) as stderr_handle:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            env=_benchmark_environment(threads),
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except KeyboardInterrupt:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            wall_ns = time.monotonic_ns() - start_ns
            raise MeasurementFailure(
                "timeout",
                f"wall timeout after {timeout_seconds:g} seconds",
                Measurement(
                    wall_ns=wall_ns,
                    cpu_ns=None,
                    peak_rss_bytes=None,
                    returncode=-signal.SIGKILL,
                    stdout_excerpt=(
                        ""
                        if stdout_path is not None
                        else _safe_excerpt(stdout_target)
                    ),
                    stderr_excerpt=_safe_excerpt(stderr_log),
                ),
            )
    wall_ns = time.monotonic_ns() - start_ns
    measured = _parse_time_metrics(metrics_path, wall_ns)
    result = Measurement(
        wall_ns=measured.wall_ns,
        cpu_ns=measured.cpu_ns,
        peak_rss_bytes=measured.peak_rss_bytes,
        returncode=returncode,
        stdout_excerpt=_safe_excerpt(stdout_target)
        if stdout_path is None
        else "",
        stderr_excerpt=_safe_excerpt(stderr_log),
    )
    if returncode != measured.returncode:
        raise RunnerError(
            "GNU time exit status differs from subprocess exit status"
        )
    if returncode != 0:
        diagnostic = result.stderr_excerpt or result.stdout_excerpt
        raise MeasurementFailure(
            "failed",
            f"command exited {returncode}: {diagnostic[:2000]}",
            result,
        )
    return result


def _run_control(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    scratch: pathlib.Path,
    threads: int,
    operation: str = "native inspect",
) -> bytes:
    """Run a bounded, unmeasured native control command."""

    stdout_path = scratch / "control.stdout"
    stderr_path = scratch / "control.stderr"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=_benchmark_environment(threads),
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except KeyboardInterrupt:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise RunnerError(f"{operation} timed out") from exc
    if returncode != 0:
        detail = _safe_excerpt(stderr_path) or _safe_excerpt(stdout_path)
        raise RunnerError(f"{operation} exited {returncode}: {detail[:2000]}")
    if stdout_path.stat().st_size > MAX_INSPECT_BYTES:
        raise RunnerError(f"{operation} output exceeds safety limit")
    return stdout_path.read_bytes()


def _substitute(
    template: Sequence[str],
    *,
    input_path: pathlib.Path,
    archive_path: pathlib.Path,
    restored_path: pathlib.Path,
    max_input_bytes: int,
    max_archive_bytes: int,
    max_output_bytes: int,
) -> list[str]:
    replacements = {
        "{input}": str(input_path),
        "{archive}": str(archive_path),
        "{restored}": str(restored_path),
        "{max_input_bytes}": str(max_input_bytes),
        "{max_archive_bytes}": str(max_archive_bytes),
        "{max_output_bytes}": str(max_output_bytes),
    }
    result: list[str] = []
    for argument in template:
        if argument in replacements:
            result.append(replacements[argument])
        elif "{" in argument or "}" in argument:
            raise RunnerError(f"unknown argv placeholder {argument!r}")
        else:
            result.append(argument)
    return result


def _regular_file_hash(path: pathlib.Path) -> tuple[int, str]:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RunnerError(f"expected a regular, unsymlinked file: {path}")
    return metadata.st_size, sha256_file(path)


def _prepare_workloads(
    repository: pathlib.Path,
    entries: Sequence[DatasetEntry],
    split: str,
    selected_ids: set[str],
) -> list[PreparedWorkload]:
    if split not in {"development", "validation"}:
        raise RunnerError("runner permits development or validation only")
    if any(entry.split == "holdout" for entry in entries):
        raise RunnerError("holdout rows are forbidden in this runner")
    selected = [
        entry
        for entry in entries
        if entry.split == split
        and (not selected_ids or entry.dataset_id in selected_ids)
    ]
    missing = selected_ids - {entry.dataset_id for entry in selected}
    if missing:
        raise RunnerError(
            "selected dataset IDs not found in split: " + ", ".join(sorted(missing))
        )
    if not selected:
        raise RunnerError(f"manifest has no selected {split} rows")

    prepared: list[PreparedWorkload] = []
    resolved_repository = repository.resolve()
    for entry in sorted(selected, key=lambda item: item.dataset_id):
        path = repository / entry.path
        error = ""
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(resolved_repository)
            size, digest = _regular_file_hash(path)
            if size != entry.bytes:
                raise RunnerError(
                    f"manifest bytes={entry.bytes}, payload bytes={size}"
                )
            if digest != entry.sha256:
                raise RunnerError(
                    f"manifest SHA-256={entry.sha256}, payload SHA-256={digest}"
                )
        except (OSError, ValueError, RunnerError) as exc:
            error = f"dataset integrity failure: {exc}"
        prepared.append(
            PreparedWorkload(
                workload=Workload(entry.dataset_id, entry.path),
                entry=entry,
                path=path.absolute(),
                error=error,
            )
        )
    return prepared


def _inventory_rows(
    path: pathlib.Path,
    *,
    expected_catalog_sha256: str,
) -> dict[str, dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RunnerError("unsupported baseline inventory schema")
    if value.get("catalog_sha256") != expected_catalog_sha256:
        raise RunnerError(
            "baseline inventory was not generated from the selected catalogue"
        )
    rows = value.get("baselines")
    if not isinstance(rows, list):
        raise RunnerError("baseline inventory has no baselines array")
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise RunnerError("invalid baseline inventory row")
        identifier = str(row["id"])
        if identifier in result:
            raise RunnerError(f"duplicate inventory baseline {identifier}")
        result[identifier] = row
    return result


def _executable_identity(
    path: pathlib.Path,
    expected_sha256: str | None,
) -> tuple[str, str]:
    try:
        size, digest = _regular_file_hash(path)
    except (OSError, RunnerError) as exc:
        return "", f"executable unavailable: {exc}"
    if size == 0:
        return "", "executable is empty"
    if expected_sha256 and digest != expected_sha256:
        return "", (
            "executable SHA-256 differs from frozen inventory: "
            f"expected {expected_sha256}, got {digest}"
        )
    if not os.access(path, os.X_OK):
        return "", f"executable is not executable: {path}"
    return digest, ""


def _gnu_time_version(path: pathlib.Path) -> str:
    try:
        process = subprocess.run(
            [str(path), "--version"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"could not identify GNU time: {exc}") from exc
    rendered = process.stdout.decode("utf-8", errors="replace")
    first_line = next(
        (line.strip() for line in rendered.splitlines() if line.strip()),
        "",
    )
    if process.returncode != 0 or "GNU Time" not in rendered:
        raise RunnerError(
            f"measurement executable is not GNU time: {first_line!r}"
        )
    return first_line[:500]


def _binary_version(path: pathlib.Path) -> tuple[str, str]:
    try:
        process = subprocess.run(
            [str(path), "--version"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"could not query executable version: {exc}"
    first_line = next(
        (
            line.strip()
            for line in process.stdout.decode(
                "utf-8", errors="replace"
            ).splitlines()
            if line.strip()
        ),
        "",
    )
    if process.returncode != 0 or not first_line:
        return "", (
            f"executable version query failed with status {process.returncode}"
        )
    return first_line[:500], ""


def _unique_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise RunnerError(
                f"runtime profile JSON contains duplicate key {name!r}"
            )
        result[name] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise RunnerError(f"runtime profile JSON contains non-finite {value}")


def _runtime_value(
    document: Mapping[str, object],
    path: tuple[str, ...],
) -> object:
    current: object = document
    for component in path:
        if not isinstance(current, dict) or component not in current:
            raise RunnerError(
                "runtime profile is missing " + ".".join(path)
            )
        current = current[component]
    return current


def _runtime_profile_contract(
    binary: pathlib.Path,
    profile: str,
    config: Mapping[str, object],
    disabled_algorithms: tuple[str, ...],
) -> tuple[Mapping[str, object] | None, str, str]:
    """Query, validate and hash the executable's actual built-in profile."""

    try:
        with tempfile.TemporaryDirectory(
            prefix="mathsvg-runtime-profile-"
        ) as temporary:
            argv = [
                str(binary),
                "profile",
                "--profile",
                profile,
            ]
            for algorithm in disabled_algorithms:
                argv.extend(["--disable", algorithm])
            raw = _run_control(
                argv,
                timeout_seconds=10.0,
                scratch=pathlib.Path(temporary),
                threads=1,
                operation="native profile introspection",
            )
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(decoded, dict):
            raise RunnerError("runtime profile JSON must be an object")

        blocks = config.get("blocks")
        search = config.get("search")
        parallel = config.get("parallel")
        entropy = config.get("entropy")
        if not all(
            isinstance(table, dict)
            for table in (blocks, search, parallel, entropy)
        ):
            raise RunnerError(
                "validated profile TOML lacks operational tables"
            )
        assert isinstance(blocks, dict)
        assert isinstance(search, dict)
        assert isinstance(parallel, dict)
        assert isinstance(entropy, dict)
        operational_mirror = {
            ("schema_version",): config.get("schema_version"),
            ("profile",): profile,
            ("container_version",): config.get("container_version"),
            ("dsl_version",): config.get("dsl_version"),
            ("optimizer", "block_bytes"): blocks.get("block_bytes"),
            ("optimizer", "microblock_bytes"): blocks.get(
                "microblock_bytes"
            ),
            ("optimizer", "max_candidates"): search.get(
                "candidate_budget"
            ),
            ("optimizer", "work_budget"): search.get("work_budget"),
            ("parallel", "default_threads"): parallel.get(
                "default_threads"
            ),
            (
                "parallel",
                "deterministic_source_order_merge",
            ): parallel.get("deterministic_merge"),
            ("entropy_search", "baseline_policy"): entropy.get(
                "baseline_policy"
            ),
            ("entropy_search", "add_only_policy"): entropy.get(
                "add_only_policy"
            ),
            ("entropy_search", "chain_depth"): entropy.get(
                "chain_depth"
            ),
            ("entropy_search", "one_byte_lazy"): entropy.get(
                "one_byte_lazy"
            ),
            ("entropy_search", "parser_scratch_bytes"): entropy.get(
                "parser_scratch_bytes"
            ),
            (
                "entropy_search",
                "maximum_additional_work_per_input_byte_per_walk",
            ): entropy.get(
                "maximum_additional_work_per_input_byte_per_walk"
            ),
            ("entropy_search", "maximum_policy_walks"): entropy.get(
                "maximum_policy_walks"
            ),
            ("entropy_search", "wire_opcode"): 7,
            ("entropy_search", "decoder_semantics_changed"): False,
            ("ablation", "id"): _runtime_ablation_identity(
                disabled_algorithms
            ),
            (
                "ablation",
                "disabled_algorithms",
            ): list(disabled_algorithms),
        }
        for path, expected in operational_mirror.items():
            actual = _runtime_value(decoded, path)
            if type(actual) is not type(expected) or actual != expected:
                name = ".".join(path)
                raise RunnerError(
                    f"runtime profile/TOML mismatch for {name}: "
                    f"runtime={actual!r}, TOML={expected!r}"
                )
        catalogue_mirror = {
            ("implemented_catalogue", "functions"): search.get(
                "function_catalog"
            ),
            ("implemented_catalogue", "coordinates"): search.get(
                "coordinate_catalog"
            ),
        }
        for path, expected in catalogue_mirror.items():
            actual = _runtime_value(decoded, path)
            if actual != expected:
                name = ".".join(path)
                raise RunnerError(
                    f"runtime profile/TOML catalogue mismatch for {name}: "
                    f"runtime={actual!r}, TOML={expected!r}"
                )
        gate_mirror = {
            ("emission_gates", "whole_block_entropy"): (
                search.get("whole_block_entropy") is True
                and "whole-block-entropy" not in disabled_algorithms
            ),
            ("emission_gates", "whole_block_functions"): (
                search.get("whole_block_functions") is True
                and "whole-block-functions" not in disabled_algorithms
            ),
            ("emission_gates", "interval_functions"): (
                search.get("interval_functions") is True
                and "interval-functions" not in disabled_algorithms
            ),
            ("emission_gates", "coordinates"): (
                bool(search.get("coordinate_depth"))
                and "coordinates" not in disabled_algorithms
            ),
            ("emission_gates", "residual"): (
                bool(search.get("residual_depth"))
                and "residual" not in disabled_algorithms
            ),
            ("emission_gates", "symbolic"): bool(
                search.get("symbolic_depth")
            ),
            ("emission_gates", "graph"): search.get("dag_sharing"),
        }
        for path, expected in gate_mirror.items():
            actual = _runtime_value(decoded, path)
            if (
                not isinstance(actual, bool)
                or not isinstance(expected, bool)
                or actual != expected
            ):
                name = ".".join(path)
                raise RunnerError(
                    f"runtime profile/TOML emission gate mismatch for {name}: "
                    f"runtime={actual!r}, TOML={expected!r}"
                )
        return decoded, _canonical_sha256(decoded), ""
    except (
        json.JSONDecodeError,
        OSError,
        RunnerError,
        UnicodeError,
    ) as exc:
        return None, "", f"runtime profile unavailable: {exc}"


def build_baseline_specs(
    requested: Sequence[str],
    *,
    inventory_path: pathlib.Path,
    catalog_path: pathlib.Path,
) -> list[CodecSpec]:
    catalog_sha256 = sha256_file(catalog_path)
    inventory = _inventory_rows(
        inventory_path,
        expected_catalog_sha256=catalog_sha256,
    )
    specs: list[CodecSpec] = []
    for identifier in sorted(set(requested)):
        row = inventory.get(identifier)
        adapter = SAFE_BASELINE_ADAPTERS.get(identifier)
        executable: pathlib.Path | None = None
        executable_sha256 = ""
        version = ""
        reason = ""
        if row is None:
            reason = "baseline is absent from frozen inventory"
        elif row.get("status") != "available":
            reason = f"inventory status is {row.get('status', 'invalid')}"
        elif adapter is None:
            reason = "no safe encode/decode argv adapter is frozen"
        else:
            resolved = row.get("resolved_executable")
            expected = row.get("executable_sha256")
            version_value = row.get("version")
            if not isinstance(resolved, str) or not resolved:
                reason = "inventory has no resolved executable"
            elif not isinstance(expected, str) or not expected:
                reason = "inventory has no executable SHA-256"
            else:
                executable = pathlib.Path(resolved)
                executable_sha256, reason = _executable_identity(
                    executable, expected
                )
                version = version_value if isinstance(version_value, str) else ""

        config_material = {
            "adapter_schema": 1,
            "baseline_catalog_sha256": catalog_sha256,
            "codec": identifier,
            "threads": 1,
            "encode_args": list(adapter.encode_args) if adapter else None,
            "decode_args": list(adapter.decode_args) if adapter else None,
            "encode_stdout": adapter.encode_stdout if adapter else None,
            "decode_stdout": adapter.decode_stdout if adapter else None,
        }
        specs.append(
            CodecSpec(
                codec=Codec(identifier, identifier, 1),
                native_mathsvg=False,
                profile="",
                executable=executable,
                executable_sha256=executable_sha256,
                version=version,
                adapter=adapter,
                config_sha256=_canonical_sha256(config_material),
                unavailable_reason=reason,
            )
        )
    return specs


def build_native_specs(
    profiles: Sequence[str],
    *,
    binary: pathlib.Path,
    config_root: pathlib.Path,
    thread_selectors: Sequence[str] = ("1",),
    ablation_ids: Sequence[str] = (),
    ablation_catalog: Mapping[str, tuple[str, ...]] | None = None,
    ablation_catalog_sha256: str = "",
) -> list[CodecSpec]:
    executable_sha256, binary_error = _executable_identity(binary, None)
    version, version_error = (
        _binary_version(binary) if not binary_error else ("", "")
    )
    specs: list[CodecSpec] = []
    thread_selections = _native_thread_selections(thread_selectors)
    ablation_selections = _native_ablation_selections(
        ablation_ids,
        ablation_catalog or {},
    )
    for profile in sorted(set(profiles)):
        if profile not in PROFILE_NAMES:
            raise RunnerError(f"invalid native profile {profile!r}")
        config_path = config_root / profile / "profile.toml"
        reason = binary_error or version_error
        config_file_sha256 = ""
        config: Mapping[str, object] | None = None
        try:
            config_file_sha256 = sha256_file(config_path)
            config = load_profile(config_path)
            if config["profile"] != profile:
                raise ConfigError("profile name differs from requested profile")
        except (ConfigError, OSError) as exc:
            reason = reason or f"profile config unavailable: {exc}"
        for ablation_id, disabled_algorithms in ablation_selections:
            variant_reason = reason
            runtime_profile: Mapping[str, object] | None = None
            runtime_profile_sha256 = ""
            if config is not None and not binary_error:
                (
                    runtime_profile,
                    runtime_profile_sha256,
                    runtime_error,
                ) = _runtime_profile_contract(
                    binary,
                    profile,
                    config,
                    disabled_algorithms,
                )
                variant_reason = variant_reason or runtime_error

            encode_prefix = [
                "compress",
                "--profile",
                profile,
                "--threads",
            ]
            for thread_selector, effective_threads in thread_selections:
                encode_args = [
                    *encode_prefix,
                    thread_selector,
                ]
                for algorithm in disabled_algorithms:
                    encode_args.extend(["--disable", algorithm])
                encode_args.extend(
                    [
                        "--max-input-bytes",
                        "{max_input_bytes}",
                        "{input}",
                        "{archive}",
                    ]
                )
                adapter = Adapter(
                    encode_args=tuple(encode_args),
                    decode_args=(
                        "decompress",
                        "--max-archive-bytes",
                        "{max_archive_bytes}",
                        "--max-output-bytes",
                        "{max_output_bytes}",
                        "{archive}",
                        "{restored}",
                    ),
                    encode_stdout=False,
                    decode_stdout=False,
                )
                inspect_args = [
                    "inspect",
                    "--verify",
                    "--max-archive-bytes",
                    "{max_archive_bytes}",
                    "--max-output-bytes",
                    "{max_output_bytes}",
                    "{archive}",
                ]
                config_material = {
                    "adapter_schema": 3,
                    "codec": "mathsvg",
                    "profile": profile,
                    "thread_selector": thread_selector,
                    "effective_threads": effective_threads,
                    "ablation_id": ablation_id,
                    "disabled_algorithms": list(disabled_algorithms),
                    "ablation_catalog_sha256": ablation_catalog_sha256,
                    "profile_config_sha256": config_file_sha256,
                    "runtime_profile_sha256": runtime_profile_sha256,
                    "native_limit_policy": {
                        "max_input_bytes": (
                            "manifest bytes + 7 bit-plane padding bytes"
                        ),
                        "max_output_bytes": (
                            "manifest bytes + 7 bit-plane padding bytes"
                        ),
                        "max_archive_bytes": (
                            "2 * (manifest bytes + 7) + 1048576, checked u64"
                        ),
                    },
                    "encode_args": list(adapter.encode_args),
                    "decode_args": list(adapter.decode_args),
                    "inspect_args": inspect_args,
                }
                thread_identity = (
                    ""
                    if thread_selector == "1"
                    else (
                        f"-threads-all-{effective_threads}"
                        if thread_selector == "all"
                        else f"-threads-{thread_selector}"
                    )
                )
                ablation_identity = (
                    "" if ablation_id == "none" else f"-ablation-{ablation_id}"
                )
                config_id = (
                    f"{profile}{ablation_identity}{thread_identity}-v1"
                )
                specs.append(
                    CodecSpec(
                        codec=Codec(
                            "mathsvg",
                            config_id,
                            effective_threads,
                        ),
                        native_mathsvg=True,
                        profile=profile,
                        executable=binary,
                        executable_sha256=executable_sha256,
                        version=version,
                        adapter=adapter,
                        config_sha256=_canonical_sha256(config_material),
                        unavailable_reason=variant_reason,
                        runtime_profile_sha256=runtime_profile_sha256,
                        runtime_profile=runtime_profile,
                        ablation_id=ablation_id,
                        disabled_algorithms=disabled_algorithms,
                    )
                )
    return specs


def _empty_record_values() -> dict[str, int | None]:
    optional = {
        "archive_bytes",
        "compression_wall_ns",
        "compression_cpu_ns",
        "decompression_wall_ns",
        "decompression_cpu_ns",
        "peak_rss_bytes",
        "compression_peak_rss_bytes",
        "decompression_peak_rss_bytes",
        "energy_uj",
        *NATIVE_BREAKDOWN_FIELDS,
    }
    return {name: None for name in optional}


def _base_record(
    context: RunContext,
    trial: Trial,
    workload: PreparedWorkload,
    spec: CodecSpec,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": context.experiment_id,
        "machine_id": context.machine_id,
        "architecture": context.architecture,
        "source_commit": context.source_commit,
        "config_sha256": spec.config_sha256,
        "dataset_manifest_sha256": context.dataset_manifest_sha256,
        "randomization_seed": context.seed,
        "schedule_block": trial.block,
        "schedule_order": trial.order,
        "repetition": trial.repetition,
        "warmup": trial.warmup,
        "split": workload.entry.split,
        "dataset_id": workload.entry.dataset_id,
        "input_path": workload.entry.path,
        "input_sha256": workload.entry.sha256,
        "original_bytes": workload.entry.bytes,
        "codec": spec.codec.codec_id,
        "codec_config": spec.codec.config_id,
        "profile": spec.profile,
        "threads": spec.codec.threads,
        "native_mathsvg": spec.native_mathsvg,
        "codec_executable_sha256": spec.executable_sha256,
        "measurement_method": MEASUREMENT_METHOD,
        "measurement_tool_sha256": context.time_sha256,
        "status": "failed",
        "error": "",
        "archive_sha256": "",
        "restored_sha256": "",
        "roundtrip_ok": False,
        "deterministic_archive": False,
        **_empty_record_values(),
    }


def _failure_record(
    base: dict[str, object],
    *,
    status: str,
    error: str,
    compression: Measurement | None = None,
    decompression: Measurement | None = None,
    archive_bytes: int | None = None,
    archive_sha256: str = "",
    restored_sha256: str = "",
    deterministic_archive: bool = False,
    roundtrip_ok: bool = False,
) -> TrialRecord:
    base.update(
        {
            "status": status,
            "error": error[:4000],
            "archive_bytes": archive_bytes,
            "archive_sha256": archive_sha256,
            "restored_sha256": restored_sha256,
            "roundtrip_ok": roundtrip_ok,
            "deterministic_archive": deterministic_archive,
            "compression_wall_ns": compression.wall_ns if compression else None,
            "compression_cpu_ns": compression.cpu_ns if compression else None,
            "decompression_wall_ns": (
                decompression.wall_ns if decompression else None
            ),
            "decompression_cpu_ns": (
                decompression.cpu_ns if decompression else None
            ),
            "compression_peak_rss_bytes": (
                compression.peak_rss_bytes if compression else None
            ),
            "decompression_peak_rss_bytes": (
                decompression.peak_rss_bytes if decompression else None
            ),
            "peak_rss_bytes": max(
                (
                    measurement.peak_rss_bytes
                    for measurement in (compression, decompression)
                    if measurement is not None
                    and measurement.peak_rss_bytes is not None
                ),
                default=None,
            ),
        }
    )
    return TrialRecord(**base)  # type: ignore[arg-type]


def _native_breakdown(
    spec: CodecSpec,
    archive: pathlib.Path,
    context: RunContext,
    scratch: pathlib.Path,
    expected_archive_bytes: int,
    expected_original_bytes: int,
    expected_original_sha256: str,
) -> dict[str, int]:
    assert spec.executable is not None
    _, max_archive_bytes, max_output_bytes = _native_limits(
        expected_original_bytes
    )
    if expected_archive_bytes > max_archive_bytes:
        raise RunnerError(
            "native archive exceeds the deterministic checked archive bound"
        )
    raw = _run_control(
        [
            str(spec.executable),
            "inspect",
            "--verify",
            "--max-archive-bytes",
            str(max_archive_bytes),
            "--max-output-bytes",
            str(max_output_bytes),
            str(archive),
        ],
        timeout_seconds=context.inspect_timeout_seconds,
        scratch=scratch,
        threads=spec.codec.threads,
    )
    try:
        inspection = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise RunnerError(f"native inspect returned invalid JSON: {exc}") from exc
    if not isinstance(inspection, dict):
        raise RunnerError("native inspect JSON must be an object")
    invariants = {
        "archive_bytes": expected_archive_bytes,
        "original_bytes": expected_original_bytes,
        "original_sha256": expected_original_sha256,
        "restored_verified": True,
    }
    for name, expected in invariants.items():
        if inspection.get(name) != expected:
            raise RunnerError(
                f"native inspect invariant {name} mismatch: "
                f"expected {expected!r}, got {inspection.get(name)!r}"
            )
    breakdown = inspection.get("procedural_breakdown")
    if not isinstance(breakdown, dict):
        raise RunnerError(
            "native inspect lacks exact procedural_breakdown; "
            "trial cannot be reported as successful without fabricating §24 metrics"
        )
    result: dict[str, int] = {}
    missing = [
        name
        for name in NATIVE_REQUIRED_BREAKDOWN_FIELDS
        if name not in breakdown
    ]
    if missing:
        raise RunnerError(
            "native inspect procedural_breakdown is missing: "
            + ", ".join(missing)
        )
    for name in NATIVE_REQUIRED_BREAKDOWN_FIELDS:
        value = breakdown[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RunnerError(
                f"native inspect field {name} must be a non-negative integer"
            )
        result[name] = value
    for name in NATIVE_OPTIONAL_BREAKDOWN_FIELDS:
        if name not in breakdown:
            continue
        value = breakdown[name]
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RunnerError(
                f"native inspect field {name} must be a non-negative integer"
            )
        result[name] = value
    wire_partition = sum(
        result[name]
        for name in (
            "container_overhead_bytes",
            "function_graph_bytes",
            "coordinate_bytes",
            "shared_definition_bytes",
            "reference_bytes",
            "parameter_bytes",
            "residual_layer_bytes",
            "literal_leaf_bytes",
            "entropy_metadata_bytes",
        )
    )
    if wire_partition != expected_archive_bytes:
        raise RunnerError(
            "native inspect wire partition does not sum to archive_bytes"
        )
    reconstructed = (
        result["function_reconstructed_bytes"]
        + result["literal_reconstructed_bytes"]
    )
    if reconstructed != expected_original_bytes:
        raise RunnerError(
            "native inspect reconstructed attribution does not cover input"
        )
    literal_only = result.get("literal_only_archive_bytes")
    pre_entropy = result.get("pre_entropy_archive_bytes")
    entropy_saved = result.get("entropy_saved_bytes")
    entropy_penalty = result.get("entropy_penalty_bytes")
    if (
        entropy_saved is not None or entropy_penalty is not None
    ) and pre_entropy is None:
        raise RunnerError(
            "native inspect entropy delta lacks pre-entropy counterfactual"
        )
    if pre_entropy is not None:
        exact_entropy_saved = max(
            pre_entropy - expected_archive_bytes, 0
        )
        exact_entropy_penalty = max(
            expected_archive_bytes - pre_entropy, 0
        )
        if (
            entropy_saved is not None
            and entropy_saved != exact_entropy_saved
        ):
            raise RunnerError(
                "native inspect entropy saved counterfactual is inconsistent"
            )
        if (
            entropy_penalty is not None
            and entropy_penalty != exact_entropy_penalty
        ):
            raise RunnerError(
                "native inspect entropy penalty counterfactual is inconsistent"
            )
        result["entropy_saved_bytes"] = exact_entropy_saved
        result["entropy_penalty_bytes"] = exact_entropy_penalty
    procedural_gain = result.get("procedural_gain_bytes")
    procedural_penalty = result.get("procedural_penalty_bytes")
    if (
        procedural_gain is not None or procedural_penalty is not None
    ) and literal_only is None:
        raise RunnerError(
            "native inspect procedural delta lacks literal-only counterfactual"
        )
    if literal_only is not None:
        exact_procedural_gain = max(
            literal_only - expected_archive_bytes, 0
        )
        exact_procedural_penalty = max(
            expected_archive_bytes - literal_only, 0
        )
        if (
            procedural_gain is not None
            and procedural_gain != exact_procedural_gain
        ):
            raise RunnerError(
                "native inspect procedural gain counterfactual is inconsistent"
            )
        if (
            procedural_penalty is not None
            and procedural_penalty != exact_procedural_penalty
        ):
            raise RunnerError(
                "native inspect procedural penalty counterfactual is inconsistent"
            )
        result["procedural_gain_bytes"] = exact_procedural_gain
        result["procedural_penalty_bytes"] = exact_procedural_penalty
    return result


def execute_trial(
    context: RunContext,
    trial: Trial,
    workload: PreparedWorkload,
    spec: CodecSpec,
    scratch_parent: pathlib.Path,
) -> TrialRecord:
    base = _base_record(context, trial, workload, spec)
    if workload.error:
        return _failure_record(base, status="failed", error=workload.error)
    if spec.unavailable_reason or spec.executable is None or spec.adapter is None:
        return _failure_record(
            base,
            status="unavailable",
            error=spec.unavailable_reason or "codec adapter is unavailable",
        )
    try:
        if spec.native_mathsvg:
            max_input_bytes, max_archive_bytes, max_output_bytes = (
                _native_limits(workload.entry.bytes)
            )
        else:
            # Baseline adapters do not contain limit placeholders.
            max_input_bytes = workload.entry.bytes
            max_archive_bytes = workload.entry.bytes
            max_output_bytes = workload.entry.bytes
    except RunnerError as exc:
        return _failure_record(
            base,
            status="failed",
            error=f"native limit policy: {exc}",
        )

    with tempfile.TemporaryDirectory(
        prefix="mathsvg-trial-", dir=scratch_parent
    ) as temporary:
        scratch = pathlib.Path(temporary)
        archive_suffix = (
            ".7z"
            if spec.codec.codec_id == "7zip-lzma2-ultra"
            else ".bin"
        )
        archive = scratch / f"archive{archive_suffix}"
        restored = scratch / "restored.bin"
        encode_argv = [
            str(spec.executable),
            *_substitute(
                spec.adapter.encode_args,
                input_path=workload.path,
                archive_path=archive,
                restored_path=restored,
                max_input_bytes=max_input_bytes,
                max_archive_bytes=max_archive_bytes,
                max_output_bytes=max_output_bytes,
            ),
        ]
        try:
            compression = run_measured(
                time_binary=context.time_binary,
                argv=encode_argv,
                timeout_seconds=context.compression_timeout_seconds,
                scratch=scratch,
                stdout_path=archive if spec.adapter.encode_stdout else None,
                threads=spec.codec.threads,
            )
        except MeasurementFailure as exc:
            return _failure_record(
                base,
                status=exc.status,
                error=f"compression: {exc.message}",
                compression=exc.measurement,
            )
        except (OSError, RunnerError) as exc:
            return _failure_record(
                base, status="failed", error=f"compression measurement: {exc}"
            )

        try:
            archive_bytes, archive_sha256 = _regular_file_hash(archive)
        except (OSError, RunnerError) as exc:
            return _failure_record(
                base,
                status="failed",
                error=f"compressor produced no valid archive: {exc}",
                compression=compression,
            )
        if spec.native_mathsvg and archive_bytes > max_archive_bytes:
            return _failure_record(
                base,
                status="failed",
                error=(
                    "native archive exceeds deterministic checked bound: "
                    f"{archive_bytes} > {max_archive_bytes}"
                ),
                compression=compression,
                archive_bytes=archive_bytes,
                archive_sha256=archive_sha256,
            )
        deterministic_key = (
            workload.entry.dataset_id,
            spec.codec.codec_id,
            spec.codec.config_id,
            spec.codec.threads,
        )
        first_hash = context.deterministic_hashes.setdefault(
            deterministic_key, archive_sha256
        )
        deterministic = first_hash == archive_sha256

        decode_argv = [
            str(spec.executable),
            *_substitute(
                spec.adapter.decode_args,
                input_path=workload.path,
                archive_path=archive,
                restored_path=restored,
                max_input_bytes=max_input_bytes,
                max_archive_bytes=max_archive_bytes,
                max_output_bytes=max_output_bytes,
            ),
        ]
        decode_scratch = scratch / "decode"
        decode_scratch.mkdir()
        try:
            decompression = run_measured(
                time_binary=context.time_binary,
                argv=decode_argv,
                timeout_seconds=context.decompression_timeout_seconds,
                scratch=decode_scratch,
                stdout_path=restored if spec.adapter.decode_stdout else None,
                threads=spec.codec.threads,
            )
        except MeasurementFailure as exc:
            return _failure_record(
                base,
                status=exc.status,
                error=f"decompression: {exc.message}",
                compression=compression,
                decompression=exc.measurement,
                archive_bytes=archive_bytes,
                archive_sha256=archive_sha256,
                deterministic_archive=deterministic,
            )
        except (OSError, RunnerError) as exc:
            return _failure_record(
                base,
                status="failed",
                error=f"decompression measurement: {exc}",
                compression=compression,
                archive_bytes=archive_bytes,
                archive_sha256=archive_sha256,
                deterministic_archive=deterministic,
            )

        try:
            restored_bytes, restored_sha256 = _regular_file_hash(restored)
        except (OSError, RunnerError) as exc:
            return _failure_record(
                base,
                status="failed",
                error=f"decompressor produced no valid output: {exc}",
                compression=compression,
                decompression=decompression,
                archive_bytes=archive_bytes,
                archive_sha256=archive_sha256,
                deterministic_archive=deterministic,
            )
        if (
            restored_bytes != workload.entry.bytes
            or restored_sha256 != workload.entry.sha256
        ):
            return _failure_record(
                base,
                status="failed",
                error=(
                    "round-trip mismatch: "
                    f"bytes={restored_bytes}, sha256={restored_sha256}"
                ),
                compression=compression,
                decompression=decompression,
                archive_bytes=archive_bytes,
                archive_sha256=archive_sha256,
                restored_sha256=restored_sha256,
                deterministic_archive=deterministic,
            )

        breakdown: dict[str, int] = {}
        if spec.native_mathsvg:
            inspect_scratch = scratch / "inspect"
            inspect_scratch.mkdir()
            try:
                breakdown = _native_breakdown(
                    spec,
                    archive,
                    context,
                    inspect_scratch,
                    archive_bytes,
                    workload.entry.bytes,
                    workload.entry.sha256,
                )
            except (OSError, RunnerError) as exc:
                return _failure_record(
                    base,
                    status="failed",
                    error=(
                        "round-trip verified but native evidence is incomplete: "
                        f"{exc}"
                    ),
                    compression=compression,
                    decompression=decompression,
                    archive_bytes=archive_bytes,
                    archive_sha256=archive_sha256,
                    restored_sha256=restored_sha256,
                    deterministic_archive=deterministic,
                    roundtrip_ok=True,
                )

        base.update(
            {
                "status": "ok",
                "error": "",
                "archive_bytes": archive_bytes,
                "archive_sha256": archive_sha256,
                "restored_sha256": restored_sha256,
                "roundtrip_ok": True,
                "deterministic_archive": deterministic,
                "compression_wall_ns": compression.wall_ns,
                "compression_cpu_ns": compression.cpu_ns,
                "decompression_wall_ns": decompression.wall_ns,
                "decompression_cpu_ns": decompression.cpu_ns,
                "compression_peak_rss_bytes": compression.peak_rss_bytes,
                "decompression_peak_rss_bytes": decompression.peak_rss_bytes,
                "peak_rss_bytes": max(
                    int(compression.peak_rss_bytes),
                    int(decompression.peak_rss_bytes),
                ),
                **breakdown,
            }
        )
        return TrialRecord(**base)  # type: ignore[arg-type]


def _atomic_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
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
            handle.write(encoded)
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
    parser.add_argument("--repository", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument(
        "--split",
        required=True,
        choices=("development", "validation"),
    )
    parser.add_argument("--dataset-id", action="append", default=[])
    parser.add_argument("--native-profile", action="append", default=[])
    parser.add_argument(
        "--native-ablation",
        action="append",
        default=[],
        metavar="ID",
        help=(
            "native ablation variant from the checked-in catalogue; repeat "
            "for a matrix (default: none)"
        ),
    )
    parser.add_argument(
        "--native-threads",
        action="append",
        default=[],
        metavar="N|all",
        help=(
            "native compression thread selector; repeat for a matrix "
            "(default: 1)"
        ),
    )
    parser.add_argument("--baseline", action="append", default=[])
    parser.add_argument("--mathsvg-binary", type=pathlib.Path)
    parser.add_argument(
        "--profile-root",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/configs"),
    )
    parser.add_argument(
        "--ablation-catalog",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/configs/ablation/catalog.toml"),
    )
    parser.add_argument(
        "--baseline-inventory",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/manifests/baseline-inventory.json"
        ),
    )
    parser.add_argument(
        "--baseline-catalog",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/configs/baselines.toml"),
    )
    parser.add_argument(
        "--time-binary",
        type=pathlib.Path,
        default=pathlib.Path("/usr/bin/time"),
    )
    parser.add_argument("--expected-delta-fraction", type=float)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1_297_748_005)
    parser.add_argument("--compression-timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--decompression-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--inspect-timeout-seconds", type=float, default=600.0)
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/raw/benchmark.jsonl"),
    )
    parser.add_argument(
        "--run-metadata",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/raw/benchmark-run.json"),
    )
    parser.add_argument("--scratch-root", type=pathlib.Path)
    return parser.parse_args(argv)


def _repository_path(repository: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else repository / path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    repository = args.repository.resolve()
    manifest_path = _repository_path(repository, args.manifest)
    output_path = _repository_path(repository, args.output)
    metadata_path = _repository_path(repository, args.run_metadata)
    inventory_path = _repository_path(repository, args.baseline_inventory)
    catalog_path = _repository_path(repository, args.baseline_catalog)
    profile_root = _repository_path(repository, args.profile_root)
    ablation_catalog_path = _repository_path(
        repository, args.ablation_catalog
    )
    ablation_catalog_sha256 = ""
    time_binary = args.time_binary.resolve()
    if output_path.exists() or metadata_path.exists():
        print(
            "benchmark runner error: output/run metadata already exists; "
            "use a new experiment path",
            file=sys.stderr,
        )
        return 2
    try:
        if not sys.platform.startswith("linux"):
            raise RunnerError(
                "this measurement backend currently supports Linux only"
            )
        if not args.native_profile and not args.baseline:
            raise RunnerError(
                "select at least one --native-profile or --baseline"
            )
        if args.native_threads and not args.native_profile:
            raise RunnerError(
                "--native-threads requires at least one --native-profile"
            )
        if args.native_ablation and not args.native_profile:
            raise RunnerError(
                "--native-ablation requires at least one --native-profile"
            )
        if not args.experiment_id or not args.machine_id:
            raise RunnerError(
                "experiment-id and machine-id must not be empty"
            )
        if any(character.isspace() for character in args.experiment_id):
            raise RunnerError(
                "experiment-id must not contain whitespace"
            )
        if args.seed < 0:
            raise RunnerError("randomization seed must be non-negative")
        if min(
            args.compression_timeout_seconds,
            args.decompression_timeout_seconds,
            args.inspect_timeout_seconds,
        ) <= 0:
            raise RunnerError("all timeout values must be positive")
        repetitions = benchmark_repetitions(
            args.expected_delta_fraction,
            args.repetitions,
        )
        time_sha256, time_error = _executable_identity(time_binary, None)
        if time_error:
            raise RunnerError(
                f"GNU time measurement facility unavailable: {time_error}"
            )
        time_version = _gnu_time_version(time_binary)
        entries = load_manifest(manifest_path)
        prepared = _prepare_workloads(
            repository,
            entries,
            args.split,
            set(args.dataset_id),
        )
        native_specs: list[CodecSpec] = []
        if args.native_profile:
            if args.mathsvg_binary is None:
                raise RunnerError(
                    "--mathsvg-binary is required for native profiles"
                )
            ablation_catalog = load_ablation_catalog(
                ablation_catalog_path
            )
            ablation_catalog_sha256 = sha256_file(
                ablation_catalog_path
            )
            native_specs = build_native_specs(
                args.native_profile,
                binary=_repository_path(
                    repository, args.mathsvg_binary
                ).resolve(),
                config_root=profile_root,
                thread_selectors=args.native_threads or ("1",),
                ablation_ids=args.native_ablation or ("none",),
                ablation_catalog=ablation_catalog,
                ablation_catalog_sha256=ablation_catalog_sha256,
            )
        baseline_specs = build_baseline_specs(
            args.baseline,
            inventory_path=inventory_path,
            catalog_path=catalog_path,
        )
        specs = native_specs + baseline_specs
        by_codec = {spec.codec: spec for spec in specs}
        if len(by_codec) != len(specs):
            raise RunnerError("duplicate codec/config/thread selection")
        trials = schedule(
            [item.workload for item in prepared],
            by_codec,
            repetitions=repetitions,
            warmups=args.warmups,
            seed=args.seed,
        )
        validate_interleaving(trials)
        by_workload = {item.workload: item for item in prepared}
        generated_artifacts = (output_path, metadata_path)
        source = git_identity(
            repository, excluded_artifacts=generated_artifacts
        )
        context = RunContext(
            repository=repository,
            experiment_id=args.experiment_id,
            machine_id=args.machine_id,
            architecture=platform.machine(),
            source_commit=str(source["commit"]),
            dataset_manifest_sha256=canonical_manifest_sha256(entries),
            seed=args.seed,
            time_binary=time_binary,
            time_sha256=time_sha256,
            compression_timeout_seconds=args.compression_timeout_seconds,
            decompression_timeout_seconds=args.decompression_timeout_seconds,
            inspect_timeout_seconds=args.inspect_timeout_seconds,
            deterministic_hashes={},
        )
        scratch_parent = (
            args.scratch_root.resolve()
            if args.scratch_root
            else pathlib.Path(tempfile.gettempdir()).resolve()
        )
        if not scratch_parent.is_dir():
            raise RunnerError(
                f"scratch root is not a directory: {scratch_parent}"
            )
        metadata = {
            "schema_version": 1,
            "experiment_id": args.experiment_id,
            "machine": {
                "machine_id": args.machine_id,
                "architecture": platform.machine(),
                "platform": platform.platform(),
                "python": platform.python_version(),
            },
            "source": source,
            "runner_sha256": sha256_file(pathlib.Path(__file__)),
            "manifest": {
                "path": manifest_path.relative_to(repository).as_posix(),
                "file_sha256": sha256_file(manifest_path),
                "canonical_sha256": context.dataset_manifest_sha256,
                "split": args.split,
                "selected_dataset_ids": [
                    item.entry.dataset_id for item in prepared
                ],
            },
            "baseline_inventory": {
                "path": inventory_path.relative_to(repository).as_posix(),
                "sha256": sha256_file(inventory_path),
            },
            "baseline_catalog": {
                "path": catalog_path.relative_to(repository).as_posix(),
                "sha256": sha256_file(catalog_path),
            },
            "ablation_catalog": {
                "path": ablation_catalog_path.relative_to(
                    repository
                ).as_posix(),
                "sha256": ablation_catalog_sha256,
                "selected": bool(args.native_profile),
            },
            "protocol": {
                "seed": args.seed,
                "warmups": args.warmups,
                "repetitions": repetitions,
                "expected_delta_fraction": args.expected_delta_fraction,
                "interleaved": True,
                "schedule_trials": len(trials),
                "native_thread_selectors": (
                    list(args.native_threads)
                    if args.native_threads
                    else (["1"] if args.native_profile else [])
                ),
                "native_ablation_ids": (
                    list(args.native_ablation)
                    if args.native_ablation
                    else (["none"] if args.native_profile else [])
                ),
                "native_limit_policy": {
                    "manifest_integrity_checked_before_execution": True,
                    "coordinate_padding_bytes": (
                        NATIVE_COORDINATE_PADDING_BYTES
                    ),
                    "archive_headroom_bytes": (
                        NATIVE_ARCHIVE_HEADROOM_BYTES
                    ),
                    "max_input_and_output": (
                        "manifest_bytes + coordinate_padding_bytes"
                    ),
                    "max_archive": (
                        "2 * max_input_and_output + archive_headroom_bytes"
                    ),
                    "u64_arithmetic_checked": True,
                },
            },
            "measurement": {
                "method": MEASUREMENT_METHOD,
                "host_preflight": _host_load_snapshot(),
                "gnu_time_path": str(time_binary),
                "gnu_time_sha256": time_sha256,
                "gnu_time_version": time_version,
                "wall_clock": "time.monotonic_ns around GNU time wrapper",
                "cpu": "GNU time %U + %S, decimal seconds",
                "peak_rss": "GNU time %M multiplied by 1024",
                "limitations": [
                    (
                        "GNU time max RSS is not the sum of concurrently "
                        "resident descendants"
                    ),
                    "filesystem cache is not dropped between randomized trials",
                    "CPU frequency/governor is not controlled by this runner",
                    (
                        "host background processes are not quiesced and CPU "
                        "affinity is not pinned"
                    ),
                    "energy and hardware counters are not measured",
                    "wrapper spawn/collection overhead is included in wall time",
                ],
            },
            "codecs": [
                {
                    **asdict(spec.codec),
                    "native_mathsvg": spec.native_mathsvg,
                    "profile": spec.profile,
                    "executable": str(spec.executable) if spec.executable else "",
                    "executable_sha256": spec.executable_sha256,
                    "version": spec.version,
                    "config_sha256": spec.config_sha256,
                    "runtime_profile_sha256": (
                        spec.runtime_profile_sha256
                    ),
                    "runtime_profile": spec.runtime_profile,
                    "ablation_id": spec.ablation_id,
                    "disabled_algorithms": list(
                        spec.disabled_algorithms
                    ),
                    "available": not bool(spec.unavailable_reason),
                    "unavailable_reason": spec.unavailable_reason,
                    "encode_argv": (
                        list(spec.adapter.encode_args)
                        if spec.adapter
                        else None
                    ),
                    "decode_argv": (
                        list(spec.adapter.decode_args)
                        if spec.adapter
                        else None
                    ),
                    "encode_stdout": (
                        spec.adapter.encode_stdout
                        if spec.adapter
                        else None
                    ),
                    "decode_stdout": (
                        spec.adapter.decode_stdout
                        if spec.adapter
                        else None
                    ),
                }
                for spec in specs
            ],
            "holdout_payload_opened": False,
            "runner_performed_tuning": False,
            "result": {
                "complete": False,
                "raw_jsonl": output_path.relative_to(repository).as_posix(),
            },
        }
        _atomic_json(metadata_path, metadata)
        status_counts = {
            "ok": 0,
            "failed": 0,
            "timeout": 0,
            "unavailable": 0,
        }
        for trial in trials:
            record = execute_trial(
                context,
                trial,
                by_workload[trial.workload],
                by_codec[trial.codec],
                scratch_parent,
            )
            append_jsonl(output_path, record)
            status_counts[record.status] += 1
            print(
                f"{trial.block}:{trial.order} "
                f"{record.dataset_id} {record.codec}/{record.codec_config} "
                f"{record.status}",
                flush=True,
            )
        source_after = git_identity(
            repository, excluded_artifacts=generated_artifacts
        )
        source_stable_during_run = _source_identity_stable(
            source,
            source_after,
        )
        metadata["source_after"] = source_after
        metadata["source_stable_during_run"] = source_stable_during_run
        if not source_stable_during_run:
            metadata["measurement"]["limitations"].append(
                "Git source identity changed while trials were running"
            )
        metadata["result"] = {
            "complete": True,
            "raw_jsonl": output_path.relative_to(repository).as_posix(),
            "raw_jsonl_sha256": sha256_file(output_path),
            "raw_jsonl_bytes": output_path.stat().st_size,
            "trial_rows": len(trials),
            "status_counts": status_counts,
        }
        _atomic_json(metadata_path, metadata)
    except (
        ManifestError,
        OSError,
        RunnerError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"benchmark runner error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
