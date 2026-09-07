"""Shared, deterministic provenance helpers for verification campaigns."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import signal
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence


class ReproError(RuntimeError):
    """A campaign cannot produce trustworthy evidence."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int | None
    elapsed_ns: int
    timed_out: bool
    stdout_path: pathlib.Path
    stderr_path: pathlib.Path


@dataclass(frozen=True, slots=True)
class TreeIdentity:
    files: int
    bytes: int
    sha256: str
    entries: tuple[dict[str, object], ...]


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def regular_file(path: pathlib.Path, *, label: str) -> pathlib.Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReproError(f"{label} is unavailable: {path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ReproError(f"{label} must not be a symbolic link: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise ReproError(f"{label} must be a regular file: {path}")
    return path.resolve()


def executable_file(path: pathlib.Path, *, label: str) -> pathlib.Path:
    resolved = regular_file(path, label=label)
    if not os.access(resolved, os.X_OK):
        raise ReproError(f"{label} is not executable: {resolved}")
    return resolved


def within_repository(
    repository: pathlib.Path,
    path: pathlib.Path,
    *,
    label: str,
) -> pathlib.Path:
    resolved_repository = repository.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(resolved_repository)
    except ValueError as exc:
        raise ReproError(f"{label} is outside the repository: {path}") from exc
    return resolved


def tree_identity(root: pathlib.Path) -> TreeIdentity:
    """Hash regular files by relative name, length, and content.

    Symlinks and non-regular entries are rejected so a seed or artifact
    identity cannot silently depend on an external path.
    """

    resolved = root.resolve()
    if not resolved.is_dir():
        raise ReproError(f"tree root is not a directory: {root}")
    entries: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(resolved.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ReproError(f"tree contains a non-regular entry: {path}")
        relative = path.relative_to(resolved).as_posix()
        size = metadata.st_size
        entries.append(
            {
                "path": relative,
                "bytes": size,
                "sha256": sha256_file(path),
            }
        )
        total_bytes += size
    canonical_entries = tuple(entries)
    return TreeIdentity(
        files=len(canonical_entries),
        bytes=total_bytes,
        sha256=canonical_sha256(canonical_entries),
        entries=canonical_entries,
    )


def stable_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
            "SOURCE_DATE_EPOCH": "0",
        }
    )
    if overrides:
        environment.update(overrides)
    return environment


def run_command(
    argv: Sequence[str],
    *,
    cwd: pathlib.Path,
    timeout_seconds: float,
    stdout_path: pathlib.Path,
    stderr_path: pathlib.Path,
    environment: Mapping[str, str] | None = None,
) -> CommandResult:
    if not argv:
        raise ReproError("command argv must not be empty")
    if not pathlib.Path(argv[0]).is_absolute():
        raise ReproError("command argv must start with an absolute executable")
    if timeout_seconds <= 0:
        raise ReproError("command timeout must be positive")
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout_seconds)
            timed_out = False
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
            returncode = None
            timed_out = True
    return CommandResult(
        returncode=returncode,
        elapsed_ns=time.monotonic_ns() - started,
        timed_out=timed_out,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


def probe_text(
    argv: Sequence[str],
    *,
    cwd: pathlib.Path,
    timeout_seconds: float = 30.0,
) -> str:
    with tempfile.TemporaryDirectory(prefix="mathsvg-repro-probe-") as temporary:
        scratch = pathlib.Path(temporary)
        result = run_command(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            stdout_path=scratch / "stdout",
            stderr_path=scratch / "stderr",
            environment=stable_environment(),
        )
        stdout = result.stdout_path.read_text(
            encoding="utf-8", errors="replace"
        )
        stderr = result.stderr_path.read_text(
            encoding="utf-8", errors="replace"
        )
    if result.timed_out:
        raise ReproError(f"probe timed out: {json.dumps(list(argv))}")
    if result.returncode != 0:
        detail = (stderr or stdout).strip()[:2000]
        raise ReproError(
            f"probe exited {result.returncode}: {json.dumps(list(argv))}: "
            f"{detail}"
        )
    rendered = (stdout or stderr).strip()
    if not rendered:
        raise ReproError(f"probe produced no identity: {json.dumps(list(argv))}")
    return rendered


def log_identity(path: pathlib.Path) -> dict[str, object]:
    return {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def atomic_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def dataclass_dict(value: object) -> dict[str, object]:
    rendered = asdict(value)  # type: ignore[arg-type]
    assert isinstance(rendered, dict)
    return rendered
