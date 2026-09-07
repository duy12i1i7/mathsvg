"""Timed subprocess execution with timeouts and Linux RSS sampling."""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass
class CommandResult:
    command: list[str]
    returncode: int | None
    wall_seconds: float
    cpu_seconds: float | None
    peak_rss_bytes: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes


def _rss_bytes(pid: int) -> int:
    status = Path(f"/proc/{pid}/status")
    try:
        for line in status.read_text(encoding="ascii", errors="ignore").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _children(pid: int) -> list[int]:
    children_path = Path(f"/proc/{pid}/task/{pid}/children")
    try:
        return [
            int(value)
            for value in children_path.read_text(encoding="ascii").split()
            if value.isdigit()
        ]
    except OSError:
        return []


def _process_tree_rss(pid: int) -> int:
    pending = [pid]
    seen: set[int] = set()
    total = 0
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        total += _rss_bytes(current)
        pending.extend(_children(current))
    return total


def _usage_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def run_command(
    command: Sequence[str],
    *,
    timeout: float,
    stdout_path: Path | None = None,
    stdin_path: Path | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    sample_interval: float = 0.01,
) -> CommandResult:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    output_handle = stdout_path.open("wb") if stdout_path is not None else None
    input_handle = stdin_path.open("rb") if stdin_path is not None else None
    process_environment = os.environ.copy()
    process_environment.update({"LC_ALL": "C", "LANG": "C"})
    if env:
        process_environment.update(env)
    cpu_before = _usage_seconds()
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            [str(item) for item in command],
            cwd=cwd,
            env=process_environment,
            stdin=input_handle if input_handle is not None else subprocess.DEVNULL,
            stdout=output_handle if output_handle is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
    except BaseException:
        if output_handle is not None:
            output_handle.close()
        if input_handle is not None:
            input_handle.close()
        raise

    stop = threading.Event()
    peak = [0]

    def sample() -> None:
        while not stop.is_set():
            peak[0] = max(peak[0], _process_tree_rss(process.pid))
            stop.wait(sample_interval)
        peak[0] = max(peak[0], _process_tree_rss(process.pid))

    sampler = threading.Thread(target=sample, name="rss-sampler", daemon=True)
    sampler.start()
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        stdout, stderr = process.communicate()
    finally:
        stop.set()
        sampler.join(timeout=max(0.1, sample_interval * 4))
        if output_handle is not None:
            output_handle.close()
        if input_handle is not None:
            input_handle.close()
    elapsed = time.perf_counter() - started
    cpu_after = _usage_seconds()
    return CommandResult(
        command=[str(item) for item in command],
        returncode=process.returncode,
        wall_seconds=elapsed,
        cpu_seconds=max(0.0, cpu_after - cpu_before),
        peak_rss_bytes=peak[0] or None,
        timed_out=timed_out,
        stdout=stdout or b"",
        stderr=stderr or b"",
    )
