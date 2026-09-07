#!/usr/bin/env python3
"""Run the independent MathSVG Gate 1 archive-determinism matrix.

The runner never evaluates command strings with a shell. Missing build,
thread, backend, architecture, or compiler capabilities remain visible as
`unavailable`; command failures and timeouts remain visible as their own rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from mathsvg.python.benchmarks.freeze import git_identity, sha256_file
from mathsvg.python.determinism.schema import (
    BACKENDS,
    THREAD_MODES,
    DeterminismError,
    DeterminismRecord,
    read_csv,
    write_csv,
)

DEFAULT_REPETITIONS = 100
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_THREAD_MODES = ("1", "2", "4", "8", "all")
DEFAULT_BACKENDS = ("scalar", "simd", "auto")
MAX_DIAGNOSTIC_BYTES = 16 * 1024
PROFILE_NAMES = ("fast", "balanced", "max", "structured", "repository")


class RunnerError(RuntimeError):
    """The requested run cannot produce trustworthy evidence."""


@dataclass(frozen=True, slots=True)
class CompilerIdentity:
    executable: pathlib.Path
    binary_sha256: str
    version: str
    unavailable_reason: str = ""


@dataclass(frozen=True, slots=True)
class BuildSpec:
    label: str
    executable: pathlib.Path
    compiler: CompilerIdentity


@dataclass(frozen=True, slots=True)
class Capabilities:
    available: bool
    reason: str
    binary_sha256: str
    probe_sha256: str
    profile_supported: bool
    threads_supported: bool
    all_threads_supported: bool
    backend_values: frozenset[str]
    decompress_threads_supported: bool
    decompress_backend_values: frozenset[str]
    threads_description: str
    backend_description: str


@dataclass(frozen=True, slots=True)
class AxisCell:
    thread_mode: str
    backend: str
    required: bool
    available: bool
    reason: str


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    returncode: int | None
    elapsed_ns: int
    timed_out: bool
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class GateSummary:
    status: str
    reasons: tuple[str, ...]
    rows: int
    ok_rows: int
    failed_rows: int
    timeout_rows: int
    unavailable_rows: int
    archive_sha256: str


@dataclass(frozen=True, slots=True)
class RunRequest:
    repository: pathlib.Path
    experiment_id: str
    machine_id: str
    split: str
    input_path: pathlib.Path
    config_path: pathlib.Path
    profile: str
    builds: tuple[BuildSpec, ...]
    repetitions: int = DEFAULT_REPETITIONS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    thread_modes: tuple[str, ...] = DEFAULT_THREAD_MODES
    backends: tuple[str, ...] = DEFAULT_BACKENDS
    publishable: bool = True
    source_identity: Mapping[str, object] | None = None
    reference_archive_sha256: str = ""


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _runner_sha256() -> str:
    directory = pathlib.Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted((directory / "runner.py", directory / "schema.py")):
        digest.update(path.name.encode("ascii"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_text(path: pathlib.Path, scratch: pathlib.Path) -> str:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_DIAGNOSTIC_BYTES + 1)
    except OSError as exc:
        return f"could not read diagnostic: {exc}"
    truncated = len(data) > MAX_DIAGNOSTIC_BYTES
    rendered = data[:MAX_DIAGNOSTIC_BYTES].decode(
        "utf-8", errors="replace"
    )
    rendered = rendered.replace(str(scratch), "{scratch}")
    if truncated:
        rendered += "\n[diagnostic truncated]"
    return rendered.strip()


def _stable_environment(
    *,
    scratch: pathlib.Path,
    resolved_threads: int | None,
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
            "SOURCE_DATE_EPOCH": "0",
            "TMPDIR": str(scratch),
        }
    )
    if resolved_threads is not None:
        value = str(resolved_threads)
        environment["OMP_NUM_THREADS"] = value
        environment["RAYON_NUM_THREADS"] = value
    return environment


def _run_argv(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    scratch: pathlib.Path,
    name: str,
    resolved_threads: int | None = None,
) -> CommandOutcome:
    if not argv:
        raise RunnerError("process argv must not be empty")
    executable = pathlib.Path(argv[0])
    if not executable.is_absolute():
        raise RunnerError("process argv must use an absolute executable")
    if timeout_seconds <= 0:
        raise RunnerError("timeout must be positive")
    stdout_path = scratch / f"{name}.stdout"
    stderr_path = scratch / f"{name}.stderr"
    started = time.monotonic_ns()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            cwd=scratch,
            env=_stable_environment(
                scratch=scratch,
                resolved_threads=resolved_threads,
            ),
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            returncode = None
            timed_out = True
    elapsed = time.monotonic_ns() - started
    return CommandOutcome(
        returncode=returncode,
        elapsed_ns=elapsed,
        timed_out=timed_out,
        stdout=_safe_text(stdout_path, scratch),
        stderr=_safe_text(stderr_path, scratch),
    )


def compiler_identity(
    executable: pathlib.Path | None,
    *,
    timeout_seconds: float = 10.0,
) -> CompilerIdentity:
    if executable is None:
        return CompilerIdentity(
            executable=pathlib.Path("<unavailable>"),
            binary_sha256="",
            version="",
            unavailable_reason="compiler executable was not provided",
        )
    requested_name = executable.name
    path = executable.expanduser().resolve()
    if not path.is_file():
        return CompilerIdentity(
            executable=path,
            binary_sha256="",
            version="",
            unavailable_reason=f"compiler executable is not a file: {path}",
        )
    if not os.access(path, os.X_OK):
        return CompilerIdentity(
            executable=path,
            binary_sha256="",
            version="",
            unavailable_reason=f"compiler executable is not executable: {path}",
        )
    binary_sha256 = sha256_file(path)
    with tempfile.TemporaryDirectory(prefix="mathsvg-compiler-") as temporary:
        scratch = pathlib.Path(temporary)
        arguments = [str(path), "--version"]
        if path.name.startswith("rustc"):
            arguments.append("--verbose")
        outcome = _run_argv(
            arguments,
            timeout_seconds=timeout_seconds,
            scratch=scratch,
            name="compiler-version",
        )
    if outcome.timed_out:
        return CompilerIdentity(
            executable=path,
            binary_sha256=binary_sha256,
            version="",
            unavailable_reason="compiler version probe timed out",
        )
    if outcome.returncode != 0:
        detail = outcome.stderr or outcome.stdout or "no diagnostic"
        return CompilerIdentity(
            executable=path,
            binary_sha256=binary_sha256,
            version="",
            unavailable_reason=(
                f"compiler version probe exited {outcome.returncode}: {detail}"
            ),
        )
    version = (outcome.stdout or outcome.stderr).replace("\n", " | ").strip()
    if not version:
        return CompilerIdentity(
            executable=path,
            binary_sha256=binary_sha256,
            version="",
            unavailable_reason="compiler version probe produced no version",
        )
    if requested_name.startswith("rustc"):
        with tempfile.TemporaryDirectory(
            prefix="mathsvg-rustc-sysroot-"
        ) as temporary:
            scratch = pathlib.Path(temporary)
            sysroot_outcome = _run_argv(
                [str(path), "--print", "sysroot"],
                timeout_seconds=timeout_seconds,
                scratch=scratch,
                name="rustc-sysroot",
            )
        if (
            not sysroot_outcome.timed_out
            and sysroot_outcome.returncode == 0
            and sysroot_outcome.stdout
        ):
            actual = (
                pathlib.Path(sysroot_outcome.stdout.strip())
                / "bin"
                / "rustc"
            ).resolve()
            if actual.is_file() and os.access(actual, os.X_OK):
                path = actual
                binary_sha256 = sha256_file(path)
    return CompilerIdentity(
        executable=path,
        binary_sha256=binary_sha256,
        version=version,
    )


def _advertises_flag(help_text: str, flag: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_-]){re.escape(flag)}(?:[ =<]|$)", help_text) is not None


def _backend_values(help_text: str) -> frozenset[str]:
    if not _advertises_flag(help_text, "--backend"):
        return frozenset()
    match = re.search(
        r"--backend[\s\S]{0,400}?\[possible values:\s*([^\]]+)\]",
        help_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return frozenset()
    return frozenset(
        value.strip().lower()
        for value in match.group(1).split(",")
        if value.strip().lower() in {"scalar", "simd", "auto"}
    )


def _all_threads_advertised(help_text: str) -> bool:
    match = re.search(r"--threads[\s\S]{0,300}", help_text)
    if match is None:
        return False
    return re.search(
        r"\b(all|logical(?:[- ]cpu)?s?)\b",
        match.group(0),
        flags=re.IGNORECASE,
    ) is not None


def probe_capabilities(
    build: BuildSpec,
    *,
    timeout_seconds: float = 10.0,
) -> Capabilities:
    executable = build.executable.expanduser().resolve()
    if not executable.is_file():
        return Capabilities(
            available=False,
            reason=f"CLI executable is not a file: {executable}",
            binary_sha256="",
            probe_sha256="",
            profile_supported=False,
            threads_supported=False,
            all_threads_supported=False,
            backend_values=frozenset(),
            decompress_threads_supported=False,
            decompress_backend_values=frozenset(),
            threads_description="unavailable: CLI executable missing",
            backend_description="unavailable: CLI executable missing",
        )
    if not os.access(executable, os.X_OK):
        return Capabilities(
            available=False,
            reason=f"CLI executable is not executable: {executable}",
            binary_sha256="",
            probe_sha256="",
            profile_supported=False,
            threads_supported=False,
            all_threads_supported=False,
            backend_values=frozenset(),
            decompress_threads_supported=False,
            decompress_backend_values=frozenset(),
            threads_description="unavailable: CLI executable is not executable",
            backend_description="unavailable: CLI executable is not executable",
        )
    binary_sha256 = sha256_file(executable)
    outputs: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="mathsvg-capabilities-") as temporary:
        scratch = pathlib.Path(temporary)
        for name, arguments in (
            ("version", ("--version",)),
            ("compress_help", ("compress", "--help")),
            ("decompress_help", ("decompress", "--help")),
        ):
            outcome = _run_argv(
                [str(executable), *arguments],
                timeout_seconds=timeout_seconds,
                scratch=scratch,
                name=name,
            )
            if outcome.timed_out:
                return Capabilities(
                    available=False,
                    reason=f"{name} capability probe timed out",
                    binary_sha256=binary_sha256,
                    probe_sha256="",
                    profile_supported=False,
                    threads_supported=False,
                    all_threads_supported=False,
                    backend_values=frozenset(),
                    decompress_threads_supported=False,
                    decompress_backend_values=frozenset(),
                    threads_description="unavailable: capability probe failed",
                    backend_description="unavailable: capability probe failed",
                )
            if outcome.returncode != 0:
                detail = outcome.stderr or outcome.stdout or "no diagnostic"
                return Capabilities(
                    available=False,
                    reason=(
                        f"{name} capability probe exited "
                        f"{outcome.returncode}: {detail}"
                    ),
                    binary_sha256=binary_sha256,
                    probe_sha256="",
                    profile_supported=False,
                    threads_supported=False,
                    all_threads_supported=False,
                    backend_values=frozenset(),
                    decompress_threads_supported=False,
                    decompress_backend_values=frozenset(),
                    threads_description="unavailable: capability probe failed",
                    backend_description="unavailable: capability probe failed",
                )
            outputs[name] = outcome.stdout + "\n" + outcome.stderr
    compress_help = outputs["compress_help"]
    decompress_help = outputs["decompress_help"]
    threads_supported = _advertises_flag(compress_help, "--threads")
    all_threads = threads_supported and _all_threads_advertised(compress_help)
    backend_values = _backend_values(compress_help)
    decompression_backends = _backend_values(decompress_help)
    all_backends = backend_values | decompression_backends
    probe_sha256 = _canonical_sha256(outputs)
    return Capabilities(
        available=True,
        reason="",
        binary_sha256=binary_sha256,
        probe_sha256=probe_sha256,
        profile_supported=_advertises_flag(compress_help, "--profile"),
        threads_supported=threads_supported,
        all_threads_supported=all_threads,
        backend_values=backend_values,
        decompress_threads_supported=_advertises_flag(
            decompress_help, "--threads"
        ),
        decompress_backend_values=decompression_backends,
        threads_description=(
            "available: --threads; all advertised"
            if all_threads
            else (
                "partial: --threads; all not advertised"
                if threads_supported
                else "unavailable: compress help has no --threads"
            )
        ),
        backend_description=(
            "compress="
            + (
                ",".join(sorted(backend_values))
                if backend_values
                else "default-only"
            )
            + ";decompress="
            + (
                ",".join(sorted(decompression_backends))
                if decompression_backends
                else "default-only"
            )
            if all_backends
            else (
                "unavailable: --backend values are not enumerated"
                if _advertises_flag(compress_help, "--backend")
                or _advertises_flag(decompress_help, "--backend")
                else "unavailable: compress help has no --backend"
            )
        ),
    )


def _axis_cells(
    capabilities: Capabilities,
    thread_modes: Sequence[str],
    backends: Sequence[str],
) -> list[AxisCell]:
    available_backends = (
        capabilities.backend_values
        | capabilities.decompress_backend_values
    )
    cells = [
        AxisCell(
            thread_mode="default",
            backend="default",
            required=False,
            available=capabilities.available
            and capabilities.profile_supported,
            reason=(
                capabilities.reason
                if not capabilities.available
                else (
                    ""
                    if capabilities.profile_supported
                    else "compress help has no --profile"
                )
            ),
        )
    ]
    for thread_mode in thread_modes:
        for backend in backends:
            reasons: list[str] = []
            if not capabilities.available:
                reasons.append(capabilities.reason)
            elif not capabilities.profile_supported:
                reasons.append("compress help has no --profile")
            if not capabilities.threads_supported:
                reasons.append("compress help has no --threads")
            elif thread_mode == "all" and not capabilities.all_threads_supported:
                reasons.append(
                    "--threads help does not advertise the value all"
                )
            if backend not in available_backends:
                reasons.append(
                    f"neither compress nor decompress help advertises backend {backend}"
                )
            cells.append(
                AxisCell(
                    thread_mode=thread_mode,
                    backend=backend,
                    required=True,
                    available=not reasons,
                    reason="; ".join(dict.fromkeys(reasons)),
                )
            )
    if not any(cell.required and cell.available for cell in cells):
        if capabilities.threads_supported:
            for thread_mode in thread_modes:
                reasons: list[str] = []
                if not capabilities.available:
                    reasons.append(capabilities.reason)
                elif not capabilities.profile_supported:
                    reasons.append("compress help has no --profile")
                if (
                    thread_mode == "all"
                    and not capabilities.all_threads_supported
                ):
                    reasons.append(
                        "--threads help does not advertise the value all"
                    )
                cells.append(
                    AxisCell(
                        thread_mode=thread_mode,
                        backend="default",
                        required=True,
                        available=not reasons,
                        reason="; ".join(dict.fromkeys(reasons)),
                    )
                )
        for backend in backends:
            if (
                capabilities.available
                and capabilities.profile_supported
                and backend in available_backends
            ):
                cells.append(
                    AxisCell(
                        thread_mode="default",
                        backend=backend,
                        required=False,
                        available=True,
                        reason="",
                    )
                )
    return cells


def _resolved_threads(
    thread_mode: str, logical_cpus: int
) -> int | None:
    if thread_mode == "default":
        return None
    if thread_mode == "all":
        return logical_cpus
    return int(thread_mode, 10)


def _command_template(
    *,
    executable: pathlib.Path,
    operation: str,
    profile: str,
    thread_mode: str,
    backend: str,
    include_threads: bool = True,
    include_backend: bool = True,
) -> str:
    argv = [str(executable), operation]
    if operation == "compress":
        argv.extend(["--profile", profile])
    if include_threads and thread_mode != "default":
        argv.extend(["--threads", thread_mode])
    if include_backend and backend != "default":
        argv.extend(["--backend", backend])
    if operation == "compress":
        argv.extend(["{input}", "{archive}"])
    else:
        argv.extend(["{archive}", "{restored}"])
    return json.dumps(argv, ensure_ascii=True, separators=(",", ":"))


def _reason(outcome: CommandOutcome, operation: str) -> str:
    if outcome.timed_out:
        return f"{operation} wall timeout"
    detail = outcome.stderr or outcome.stdout or "no diagnostic"
    return f"{operation} exited {outcome.returncode}: {detail}"


def _outcome_status(outcome: CommandOutcome) -> str:
    if outcome.timed_out:
        return "timeout"
    diagnostic = (outcome.stderr + "\n" + outcome.stdout).lower()
    capability_pattern = re.compile(
        r"(backend|simd|thread)[^\n]{0,120}"
        r"(unavailable|not available|unsupported|not supported|requires)"
    )
    return "unavailable" if capability_pattern.search(diagnostic) else "failed"


def _base_values(
    *,
    request: RunRequest,
    source: Mapping[str, object],
    build: BuildSpec,
    capabilities: Capabilities,
    runner_sha256: str,
    config_sha256: str,
    input_sha256: str,
    input_bytes: int,
    architecture: str,
    operating_system: str,
    logical_cpus: int,
    cell: AxisCell,
    repetition: int,
) -> dict[str, object]:
    executable = build.executable.expanduser().resolve()
    resolved_threads = (
        _resolved_threads(cell.thread_mode, logical_cpus)
        if cell.available
        else None
    )
    return {
        "schema_version": 1,
        "experiment_id": request.experiment_id,
        "source_commit": source["commit"],
        "source_dirty": source["dirty"],
        "source_status_sha256": source["status_sha256"],
        "machine_id": request.machine_id,
        "architecture": architecture,
        "operating_system": operating_system,
        "logical_cpus": logical_cpus,
        "compiler_version": build.compiler.version,
        "compiler_binary_sha256": build.compiler.binary_sha256,
        "build": build.label,
        "binary_path": str(executable),
        "binary_sha256": capabilities.binary_sha256,
        "runner_sha256": runner_sha256,
        "config_path": str(request.config_path.resolve()),
        "config_sha256": config_sha256,
        "profile": request.profile,
        "split": request.split,
        "input_path": str(request.input_path.resolve()),
        "input_bytes": input_bytes,
        "input_sha256": input_sha256,
        "planned_repetitions": request.repetitions,
        "repetition": repetition,
        "thread_mode": cell.thread_mode,
        "resolved_threads": resolved_threads,
        "backend": cell.backend,
        "required_axis": cell.required,
        "threads_capability": capabilities.threads_description,
        "backend_capability": capabilities.backend_description,
        "capability_probe_sha256": capabilities.probe_sha256,
        "compress_command": _command_template(
            executable=executable,
            operation="compress",
            profile=request.profile,
            thread_mode=cell.thread_mode,
            backend=cell.backend,
            include_threads=capabilities.threads_supported,
            include_backend=cell.backend in capabilities.backend_values,
        ),
        "decompress_command": _command_template(
            executable=executable,
            operation="decompress",
            profile=request.profile,
            thread_mode=(
                cell.thread_mode
                if capabilities.decompress_threads_supported
                else "default"
            ),
            backend=(
                cell.backend
                if cell.backend in capabilities.decompress_backend_values
                else "default"
            ),
            include_threads=capabilities.decompress_threads_supported,
            include_backend=(
                cell.backend in capabilities.decompress_backend_values
            ),
        ),
    }


def _unavailable_record(
    base: Mapping[str, object],
    reason: str,
    reference_archive_sha256: str,
) -> DeterminismRecord:
    return DeterminismRecord(
        **base,
        status="unavailable",
        reason=reason,
        compress_exit_code=None,
        decompress_exit_code=None,
        compression_ns=None,
        decompression_ns=None,
        archive_bytes=None,
        archive_sha256="",
        reference_archive_sha256=reference_archive_sha256,
        archive_matches_reference=False,
        restored_sha256="",
        roundtrip_ok=False,
    )  # type: ignore[arg-type]


def _execute_cell(
    *,
    request: RunRequest,
    build: BuildSpec,
    capabilities: Capabilities,
    cell: AxisCell,
    repetition: int,
    base: Mapping[str, object],
    reference_archive_sha256: str,
    logical_cpus: int,
) -> tuple[DeterminismRecord, str]:
    executable = build.executable.expanduser().resolve()
    resolved_threads = _resolved_threads(cell.thread_mode, logical_cpus)
    with tempfile.TemporaryDirectory(
        prefix=(
            f"mathsvg-determinism-{build.label}-"
            f"{cell.thread_mode}-{cell.backend}-{repetition}-"
        )
    ) as temporary:
        scratch = pathlib.Path(temporary)
        archive = scratch / f"archive-{repetition}.msvg"
        restored = scratch / f"restored-{repetition}.bin"
        compress_argv = [
            str(executable),
            "compress",
            "--profile",
            request.profile,
        ]
        if cell.thread_mode != "default":
            compress_argv.extend(["--threads", cell.thread_mode])
        if cell.backend in capabilities.backend_values:
            compress_argv.extend(["--backend", cell.backend])
        compress_argv.extend(
            [str(request.input_path.resolve()), str(archive)]
        )
        compression = _run_argv(
            compress_argv,
            timeout_seconds=request.timeout_seconds,
            scratch=scratch,
            name="compress",
            resolved_threads=resolved_threads,
        )
        if compression.timed_out or compression.returncode != 0:
            status = _outcome_status(compression)
            if status == "unavailable":
                return (
                    _unavailable_record(
                        base,
                        _reason(compression, "compression"),
                        reference_archive_sha256,
                    ),
                    reference_archive_sha256,
                )
            record = DeterminismRecord(
                **base,
                status=status,
                reason=_reason(compression, "compression"),
                compress_exit_code=compression.returncode,
                decompress_exit_code=None,
                compression_ns=compression.elapsed_ns,
                decompression_ns=None,
                archive_bytes=None,
                archive_sha256="",
                reference_archive_sha256=reference_archive_sha256,
                archive_matches_reference=False,
                restored_sha256="",
                roundtrip_ok=False,
            )  # type: ignore[arg-type]
            return record, reference_archive_sha256
        if not archive.is_file():
            record = DeterminismRecord(
                **base,
                status="failed",
                reason="compression exited zero without an archive",
                compress_exit_code=0,
                decompress_exit_code=None,
                compression_ns=compression.elapsed_ns,
                decompression_ns=None,
                archive_bytes=None,
                archive_sha256="",
                reference_archive_sha256=reference_archive_sha256,
                archive_matches_reference=False,
                restored_sha256="",
                roundtrip_ok=False,
            )  # type: ignore[arg-type]
            return record, reference_archive_sha256

        archive_sha256 = sha256_file(archive)
        archive_bytes = archive.stat().st_size
        decompression_thread = (
            cell.thread_mode
            if capabilities.decompress_threads_supported
            else "default"
        )
        decompression_backend = (
            cell.backend
            if cell.backend in capabilities.decompress_backend_values
            else "default"
        )
        decompress_argv = [str(executable), "decompress"]
        if decompression_thread != "default":
            decompress_argv.extend(["--threads", decompression_thread])
        if decompression_backend != "default":
            decompress_argv.extend(["--backend", decompression_backend])
        decompress_argv.extend([str(archive), str(restored)])
        decompression = _run_argv(
            decompress_argv,
            timeout_seconds=request.timeout_seconds,
            scratch=scratch,
            name="decompress",
            resolved_threads=resolved_threads,
        )
        if decompression.timed_out or decompression.returncode != 0:
            status = _outcome_status(decompression)
            if status == "unavailable":
                return (
                    _unavailable_record(
                        base,
                        _reason(decompression, "decompression"),
                        reference_archive_sha256,
                    ),
                    reference_archive_sha256,
                )
            record = DeterminismRecord(
                **base,
                status=status,
                reason=_reason(decompression, "decompression"),
                compress_exit_code=0,
                decompress_exit_code=decompression.returncode,
                compression_ns=compression.elapsed_ns,
                decompression_ns=decompression.elapsed_ns,
                archive_bytes=archive_bytes,
                archive_sha256=archive_sha256,
                reference_archive_sha256=reference_archive_sha256,
                archive_matches_reference=(
                    bool(reference_archive_sha256)
                    and archive_sha256 == reference_archive_sha256
                ),
                restored_sha256="",
                roundtrip_ok=False,
            )  # type: ignore[arg-type]
            return record, reference_archive_sha256
        if not restored.is_file():
            record = DeterminismRecord(
                **base,
                status="failed",
                reason="decompression exited zero without restored output",
                compress_exit_code=0,
                decompress_exit_code=0,
                compression_ns=compression.elapsed_ns,
                decompression_ns=decompression.elapsed_ns,
                archive_bytes=archive_bytes,
                archive_sha256=archive_sha256,
                reference_archive_sha256=reference_archive_sha256,
                archive_matches_reference=(
                    bool(reference_archive_sha256)
                    and archive_sha256 == reference_archive_sha256
                ),
                restored_sha256="",
                roundtrip_ok=False,
            )  # type: ignore[arg-type]
            return record, reference_archive_sha256

        restored_sha256 = sha256_file(restored)
        roundtrip_ok = restored_sha256 == base["input_sha256"]
        if not roundtrip_ok:
            record = DeterminismRecord(
                **base,
                status="failed",
                reason="restored SHA-256 differs from input SHA-256",
                compress_exit_code=0,
                decompress_exit_code=0,
                compression_ns=compression.elapsed_ns,
                decompression_ns=decompression.elapsed_ns,
                archive_bytes=archive_bytes,
                archive_sha256=archive_sha256,
                reference_archive_sha256=reference_archive_sha256,
                archive_matches_reference=(
                    bool(reference_archive_sha256)
                    and archive_sha256 == reference_archive_sha256
                ),
                restored_sha256=restored_sha256,
                roundtrip_ok=False,
            )  # type: ignore[arg-type]
            return record, reference_archive_sha256

        if not reference_archive_sha256:
            reference_archive_sha256 = archive_sha256
        archive_matches = archive_sha256 == reference_archive_sha256
        status = "ok" if archive_matches else "failed"
        reason = (
            ""
            if archive_matches
            else "archive SHA-256 differs from the matrix reference"
        )
        record = DeterminismRecord(
            **base,
            status=status,
            reason=reason,
            compress_exit_code=0,
            decompress_exit_code=0,
            compression_ns=compression.elapsed_ns,
            decompression_ns=decompression.elapsed_ns,
            archive_bytes=archive_bytes,
            archive_sha256=archive_sha256,
            reference_archive_sha256=reference_archive_sha256,
            archive_matches_reference=archive_matches,
            restored_sha256=restored_sha256,
            roundtrip_ok=True,
        )  # type: ignore[arg-type]
        return record, reference_archive_sha256


def _validate_request(request: RunRequest) -> None:
    if not request.experiment_id or any(
        character.isspace() for character in request.experiment_id
    ):
        raise RunnerError(
            "experiment_id must be non-empty and contain no whitespace"
        )
    if not request.machine_id or any(
        character.isspace() for character in request.machine_id
    ):
        raise RunnerError(
            "machine_id must be non-empty and contain no whitespace"
        )
    if request.split not in {"development", "validation"}:
        raise RunnerError("determinism runner refuses holdout execution")
    if request.profile not in PROFILE_NAMES:
        raise RunnerError(f"invalid profile {request.profile!r}")
    if request.repetitions < 1:
        raise RunnerError("repetitions must be positive")
    if request.publishable and request.repetitions < DEFAULT_REPETITIONS:
        raise RunnerError("publishable mode requires at least 100 repetitions")
    if request.timeout_seconds <= 0:
        raise RunnerError("timeout must be positive")
    if not request.input_path.resolve().is_file():
        raise RunnerError(f"input is not a file: {request.input_path}")
    if not request.config_path.resolve().is_file():
        raise RunnerError(f"config is not a file: {request.config_path}")
    if not request.builds:
        raise RunnerError("at least one build is required")
    labels = {build.label for build in request.builds}
    if not labels <= {"debug", "release"}:
        raise RunnerError("build labels must be debug or release")
    if request.publishable and labels != {"debug", "release"}:
        raise RunnerError("publishable mode requires debug and release builds")
    if not request.thread_modes:
        raise RunnerError("thread matrix must not be empty")
    if not request.backends:
        raise RunnerError("backend matrix must not be empty")
    if any(mode not in THREAD_MODES - {"default"} for mode in request.thread_modes):
        raise RunnerError("invalid explicit thread mode")
    if any(backend not in BACKENDS - {"default"} for backend in request.backends):
        raise RunnerError("invalid explicit backend")
    if len(set(request.thread_modes)) != len(request.thread_modes):
        raise RunnerError("thread matrix contains duplicates")
    if len(set(request.backends)) != len(request.backends):
        raise RunnerError("backend matrix contains duplicates")
    if request.reference_archive_sha256 and not re.fullmatch(
        r"[0-9a-f]{64}", request.reference_archive_sha256
    ):
        raise RunnerError("reference archive SHA-256 is invalid")


def run_matrix(request: RunRequest) -> list[DeterminismRecord]:
    _validate_request(request)
    repository = request.repository.resolve()
    source = (
        dict(request.source_identity)
        if request.source_identity is not None
        else git_identity(repository)
    )
    required_source = {"commit", "dirty", "status_sha256"}
    if set(source) < required_source:
        raise RunnerError("source identity is incomplete")
    if request.publishable and source["dirty"]:
        raise RunnerError("publishable determinism run requires a clean source tree")
    config_path = request.config_path.resolve()
    input_path = request.input_path.resolve()
    config_sha256 = sha256_file(config_path)
    input_sha256 = sha256_file(input_path)
    input_bytes = input_path.stat().st_size
    runner_sha256 = _runner_sha256()
    architecture = platform.machine() or "unknown"
    operating_system = platform.platform()
    logical_cpus = os.cpu_count() or 1
    reference_archive_sha256 = request.reference_archive_sha256
    records: list[DeterminismRecord] = []
    initial_binary_hashes: dict[tuple[str, str, str], str] = {}

    build_rank = {"debug": 0, "release": 1}
    builds = sorted(
        request.builds,
        key=lambda build: (
            build_rank[build.label],
            str(build.executable.resolve()),
            build.compiler.binary_sha256,
        ),
    )
    for build in builds:
        capabilities = probe_capabilities(
            build,
            timeout_seconds=10.0,
        )
        initial_binary_hashes[
            (
                build.label,
                str(build.executable.resolve()),
                build.compiler.binary_sha256,
            )
        ] = capabilities.binary_sha256
        compiler_reason = build.compiler.unavailable_reason
        compiler_path = build.compiler.executable.resolve()
        if not compiler_reason:
            if not compiler_path.is_file():
                compiler_reason = (
                    f"compiler executable disappeared: {compiler_path}"
                )
            elif sha256_file(compiler_path) != build.compiler.binary_sha256:
                compiler_reason = (
                    "compiler executable hash differs from BuildSpec identity"
                )
        cells = _axis_cells(
            capabilities,
            request.thread_modes,
            request.backends,
        )
        for cell in cells:
            unavailable_reason = cell.reason
            if compiler_reason:
                unavailable_reason = "; ".join(
                    value
                    for value in (unavailable_reason, compiler_reason)
                    if value
                )
            cell_available = cell.available and not compiler_reason
            repetitions = range(request.repetitions) if cell_available else range(1)
            stop_reason = ""
            for repetition in repetitions:
                base = _base_values(
                    request=request,
                    source=source,
                    build=build,
                    capabilities=capabilities,
                    runner_sha256=runner_sha256,
                    config_sha256=config_sha256,
                    input_sha256=input_sha256,
                    input_bytes=input_bytes,
                    architecture=architecture,
                    operating_system=operating_system,
                    logical_cpus=logical_cpus,
                    cell=AxisCell(
                        thread_mode=cell.thread_mode,
                        backend=cell.backend,
                        required=cell.required,
                        available=cell_available,
                        reason=unavailable_reason,
                    ),
                    repetition=repetition,
                )
                if not cell_available:
                    records.append(
                        _unavailable_record(
                            base,
                            unavailable_reason
                            or "matrix cell is unavailable",
                            reference_archive_sha256,
                        )
                    )
                    continue
                if stop_reason:
                    records.append(
                        _unavailable_record(
                            base,
                            stop_reason,
                            reference_archive_sha256,
                        )
                    )
                    continue
                record, reference_archive_sha256 = _execute_cell(
                    request=request,
                    build=build,
                    capabilities=capabilities,
                    cell=cell,
                    repetition=repetition,
                    base=base,
                    reference_archive_sha256=reference_archive_sha256,
                    logical_cpus=logical_cpus,
                )
                record.validate()
                records.append(record)
                if record.status != "ok":
                    stop_reason = (
                        "not executed after earlier matrix-cell "
                        f"{record.status}: {record.reason}"
                    )

    # Inputs, configs, tools and executables are evidence identities, not
    # mutable conveniences. A mid-run mutation invalidates the entire matrix.
    if sha256_file(config_path) != config_sha256:
        raise RunnerError("configuration changed during determinism run")
    if sha256_file(input_path) != input_sha256:
        raise RunnerError("input changed during determinism run")
    if _runner_sha256() != runner_sha256:
        raise RunnerError("determinism runner changed during its own run")
    for build in builds:
        key = (
            build.label,
            str(build.executable.resolve()),
            build.compiler.binary_sha256,
        )
        before = initial_binary_hashes[key]
        path = build.executable.resolve()
        after = sha256_file(path) if path.is_file() else ""
        if after != before:
            raise RunnerError(
                f"{build.label} CLI binary changed during determinism run"
            )
        if not build.compiler.unavailable_reason:
            compiler_path = build.compiler.executable.resolve()
            if (
                not compiler_path.is_file()
                or sha256_file(compiler_path)
                != build.compiler.binary_sha256
            ):
                raise RunnerError(
                    f"{build.label} compiler changed during determinism run"
                )
    return records


def evaluate_gate(records: Sequence[DeterminismRecord]) -> GateSummary:
    if not records:
        return GateSummary(
            status="incomplete",
            reasons=("no determinism rows",),
            rows=0,
            ok_rows=0,
            failed_rows=0,
            timeout_rows=0,
            unavailable_rows=0,
            archive_sha256="",
        )
    for record in records:
        record.validate()
    reasons: list[str] = []
    failed = [row for row in records if row.status == "failed"]
    timed_out = [row for row in records if row.status == "timeout"]
    unavailable = [row for row in records if row.status == "unavailable"]
    ok = [row for row in records if row.status == "ok"]
    hashes = {row.archive_sha256 for row in records if row.archive_sha256}
    reference_hashes = {
        row.reference_archive_sha256
        for row in records
        if row.reference_archive_sha256
    }
    if any(row.source_dirty for row in records):
        reasons.append("source tree is dirty")
    if len({row.source_commit for row in records}) != 1:
        reasons.append("multiple source commits are mixed")
    if len({row.source_status_sha256 for row in records}) != 1:
        reasons.append("multiple source status identities are mixed")
    if len({row.runner_sha256 for row in records}) != 1:
        reasons.append("multiple runner hashes are mixed")
    experiment_inputs = {
        (
            row.experiment_id,
            row.profile,
            row.split,
            row.config_sha256,
            row.input_sha256,
        )
        for row in records
    }
    if len(experiment_inputs) != 1:
        reasons.append("multiple experiment/input/config identities are mixed")
    if failed:
        reasons.append(f"{len(failed)} failed row(s)")
    if timed_out:
        reasons.append(f"{len(timed_out)} timeout row(s)")
    if len(hashes) > 1:
        reasons.append("archive SHA-256 differs across executed rows")
    if len(reference_hashes) > 1:
        reasons.append("multiple reference archive SHA-256 values are mixed")
    required_unavailable = [
        row for row in unavailable if row.required_axis
    ]
    if required_unavailable:
        reasons.append(
            f"{len(required_unavailable)} required axis row(s) unavailable"
        )
    compilers = {
        row.compiler_binary_sha256
        for row in ok
        if row.compiler_binary_sha256
    }
    if len(compilers) < 2:
        reasons.append("fewer than two compiler binaries are represented")
    compiler_versions = {
        row.compiler_version for row in ok if row.compiler_version
    }
    if len(compiler_versions) < 2:
        reasons.append("fewer than two compiler versions are represented")
    architectures = {row.architecture for row in ok}
    if len(architectures) < 2:
        reasons.append("fewer than two architectures are represented")
    machines = {row.machine_id for row in ok}
    if len(machines) < 2:
        reasons.append("fewer than two machines are represented")

    expected_axes = {
        (thread_mode, backend)
        for thread_mode in DEFAULT_THREAD_MODES
        for backend in DEFAULT_BACKENDS
    }
    matrix_groups: dict[
        tuple[object, ...], list[DeterminismRecord]
    ] = {}
    build_groups: dict[
        tuple[object, ...], dict[str, set[str]]
    ] = {}
    for row in records:
        matrix_key = (
            row.experiment_id,
            row.machine_id,
            row.architecture,
            row.compiler_binary_sha256,
            row.build,
            row.binary_sha256,
            row.config_sha256,
            row.input_sha256,
        )
        matrix_groups.setdefault(matrix_key, []).append(row)
        if row.status == "ok":
            build_key = (
                row.experiment_id,
                row.machine_id,
                row.architecture,
                row.compiler_binary_sha256,
                row.config_sha256,
                row.input_sha256,
            )
            builds = build_groups.setdefault(build_key, {})
            builds.setdefault(row.build, set()).add(row.binary_sha256)
    if any(
        set(builds) != {"debug", "release"}
        for builds in build_groups.values()
    ):
        reasons.append(
            "debug and release evidence are not both present for every environment"
        )
    if any(
        builds.get("debug", set()) & builds.get("release", set())
        for builds in build_groups.values()
    ):
        reasons.append(
            "debug and release labels reuse the same CLI binary"
        )
    for rows in matrix_groups.values():
        present = {
            (row.thread_mode, row.backend)
            for row in rows
            if row.required_axis
            and row.thread_mode != "default"
            and row.backend != "default"
        }
        if present != expected_axes:
            reasons.append("a build is missing required thread/backend axes")
            break

    required = [row for row in records if row.required_axis]
    if any(
        row.planned_repetitions < DEFAULT_REPETITIONS
        for row in required
    ):
        reasons.append("a required matrix cell plans fewer than 100 repetitions")

    executed_required: dict[tuple[object, ...], list[DeterminismRecord]] = {}
    for row in records:
        if not row.required_axis or row.status == "unavailable":
            continue
        key = (
            row.experiment_id,
            row.machine_id,
            row.architecture,
            row.compiler_binary_sha256,
            row.build,
            row.binary_sha256,
            row.config_sha256,
            row.input_sha256,
            row.thread_mode,
            row.backend,
        )
        executed_required.setdefault(key, []).append(row)
    for rows in executed_required.values():
        planned_values = {row.planned_repetitions for row in rows}
        if len(planned_values) != 1:
            reasons.append(
                "a required matrix cell mixes planned repetition counts"
            )
            break
        planned = next(iter(planned_values))
        repetitions = {row.repetition for row in rows}
        if repetitions != set(range(planned)):
            reasons.append("a required matrix cell has missing repetitions")
            break
    status = (
        "failed"
        if failed or timed_out or len(hashes) > 1
        else ("incomplete" if reasons else "pass")
    )
    return GateSummary(
        status=status,
        reasons=tuple(dict.fromkeys(reasons)),
        rows=len(records),
        ok_rows=len(ok),
        failed_rows=len(failed),
        timeout_rows=len(timed_out),
        unavailable_rows=len(unavailable),
        archive_sha256=next(iter(hashes)) if len(hashes) == 1 else "",
    )


def merge_records(
    existing: Iterable[DeterminismRecord],
    additions: Iterable[DeterminismRecord],
) -> list[DeterminismRecord]:
    records = [*existing, *additions]
    # Canonical writer performs the strict duplicate-coordinate check.
    with tempfile.TemporaryDirectory(prefix="mathsvg-determinism-merge-") as temporary:
        path = pathlib.Path(temporary) / "merged.csv"
        write_csv(path, records)
        return read_csv(path)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument(
        "--split",
        choices=("development", "validation"),
        default="development",
    )
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--profile", choices=PROFILE_NAMES, default="balanced")
    parser.add_argument("--config", type=pathlib.Path)
    parser.add_argument(
        "--debug-cli",
        type=pathlib.Path,
        default=pathlib.Path("target/debug/mathsvg"),
    )
    parser.add_argument(
        "--release-cli",
        type=pathlib.Path,
        default=pathlib.Path("target/release/mathsvg"),
    )
    parser.add_argument("--compiler", type=pathlib.Path)
    parser.add_argument(
        "--repetitions", type=int, default=DEFAULT_REPETITIONS
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--mode", choices=("publishable", "smoke"), default="publishable"
    )
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--output", type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    root = args.root.resolve()
    input_path = (
        args.input.resolve()
        if args.input.is_absolute()
        else (root / args.input).resolve()
    )
    config = (
        (
            args.config.resolve()
            if args.config.is_absolute()
            else (root / args.config).resolve()
        )
        if args.config is not None
        else root / "mathsvg" / "configs" / args.profile / "profile.toml"
    )
    output = (
        (
            args.output.resolve()
            if args.output.is_absolute()
            else (root / args.output).resolve()
        )
        if args.output is not None
        else root / "mathsvg" / "results" / "determinism" / "results.csv"
    )
    compiler_path = args.compiler
    if compiler_path is None:
        discovered = shutil.which("rustc")
        compiler_path = pathlib.Path(discovered) if discovered else None
    try:
        compiler = compiler_identity(compiler_path)
        build_specs = (
            BuildSpec(
                "debug",
                (root / args.debug_cli).resolve()
                if not args.debug_cli.is_absolute()
                else args.debug_cli.resolve(),
                compiler,
            ),
            BuildSpec(
                "release",
                (root / args.release_cli).resolve()
                if not args.release_cli.is_absolute()
                else args.release_cli.resolve(),
                compiler,
            ),
        )
        existing = read_csv(output) if args.append and output.exists() else []
        if existing:
            if not input_path.is_file() or not config.resolve().is_file():
                raise RunnerError(
                    "append input and configuration must be readable files"
                )
            expected_identity = (
                args.experiment_id,
                args.profile,
                sha256_file(input_path),
                sha256_file(config.resolve()),
            )
            observed_identities = {
                (
                    row.experiment_id,
                    row.profile,
                    row.input_sha256,
                    row.config_sha256,
                )
                for row in existing
            }
            if observed_identities != {expected_identity}:
                raise RunnerError(
                    "append requires the same experiment/input/config/profile"
                )
        reference_hashes = {
            row.reference_archive_sha256
            for row in existing
            if row.reference_archive_sha256
        }
        if len(reference_hashes) > 1:
            raise RunnerError(
                "existing results contain multiple reference archive hashes"
            )
        request = RunRequest(
            repository=root,
            experiment_id=args.experiment_id,
            machine_id=args.machine_id,
            split=args.split,
            input_path=input_path,
            config_path=config,
            profile=args.profile,
            builds=build_specs,
            repetitions=args.repetitions,
            timeout_seconds=args.timeout_seconds,
            publishable=args.mode == "publishable",
            reference_archive_sha256=(
                next(iter(reference_hashes)) if reference_hashes else ""
            ),
        )
        additions = run_matrix(request)
        records = merge_records(existing, additions)
        write_csv(output, records)
        summary = evaluate_gate(records)
    except (
        DeterminismError,
        RunnerError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"determinism error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(output.resolve()),
                "status": summary.status,
                "reasons": summary.reasons,
                "rows": summary.rows,
                "ok_rows": summary.ok_rows,
                "failed_rows": summary.failed_rows,
                "timeout_rows": summary.timeout_rows,
                "unavailable_rows": summary.unavailable_rows,
                "archive_sha256": summary.archive_sha256,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if summary.status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
