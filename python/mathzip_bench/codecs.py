"""Command wrappers for MathZip and required comparison codecs."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BENCHMARK_MAX_ARCHIVE_BYTES = 16 * 1024 * 1024 * 1024
BENCHMARK_MAX_INPUT_BYTES = 8 * 1024 * 1024 * 1024
BENCHMARK_MAX_OUTPUT_BYTES = 8 * 1024 * 1024 * 1024
BENCHMARK_MAX_SEGMENTS = 8 * 1024 * 1024


@dataclass(frozen=True)
class CommandPlan:
    command: list[str]
    stdout_to_output: bool = False
    stdin_path: Path | None = None
    metrics_path: Path | None = None


@dataclass
class Codec:
    name: str
    family: str
    level: str
    extension: str
    executable: str | None
    mode: str | None = None
    threads: int = 1
    variant: str = ""
    extra_compress_args: list[str] = field(default_factory=list)
    available_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.family == "raw" or (
            self.executable is not None and self.available_reason is None
        )

    def compression_plan(self, source: Path, destination: Path) -> CommandPlan:
        executable = self._require_executable()
        if self.family == "mathzip":
            return CommandPlan(
                [
                    executable,
                    "compress",
                    "--mode",
                    self.mode or "balanced",
                    "--max-input-bytes",
                    str(BENCHMARK_MAX_INPUT_BYTES),
                    "--no-sync",
                    "--force",
                    *self.extra_compress_args,
                    str(source),
                    str(destination),
                ]
            )
        if self.family == "gzip":
            level = {"fast": "-1", "default": "-6", "max": "-9"}[self.level]
            return CommandPlan(
                [executable, "-n", "-c", level, str(source)], stdout_to_output=True
            )
        if self.family == "bzip2":
            level = {"fast": "-1", "default": "-6", "max": "-9"}[self.level]
            return CommandPlan(
                [executable, "-c", level],
                stdout_to_output=True,
                stdin_path=source,
            )
        if self.family == "xz":
            level_args = {
                "fast": ["-1"],
                "default": ["-6"],
                "max": ["-9e"],
            }[self.level]
            return CommandPlan(
                [
                    executable,
                    "-c",
                    "--threads=1" if self.threads == 1 else f"--threads={self.threads}",
                    *level_args,
                    str(source),
                ],
                stdout_to_output=True,
            )
        if self.family == "zstd":
            level = {"fast": "-1", "default": "-3", "max": "-19"}[self.level]
            return CommandPlan(
                [
                    executable,
                    "-q",
                    "-f",
                    f"-T{self.threads}",
                    level,
                    "-c",
                    str(source),
                ],
                stdout_to_output=True,
            )
        if self.family == "lz4":
            level = {"fast": "-1", "default": "-1", "max": "-12"}[self.level]
            return CommandPlan(
                [executable, "-q", "-f", level, str(source), str(destination)]
            )
        if self.family == "brotli":
            level = {"fast": "4", "default": "6", "max": "11"}[self.level]
            return CommandPlan(
                [
                    executable,
                    "-f",
                    "-q",
                    level,
                    "-o",
                    str(destination),
                    str(source),
                ]
            )
        if self.family == "7z":
            level = {"fast": "1", "default": "5", "max": "9"}[self.level]
            return CommandPlan(
                [
                    executable,
                    "a",
                    "-y",
                    "-bd",
                    "-bso0",
                    "-bsp0",
                    "-t7z",
                    "-m0=lzma2",
                    f"-mx={level}",
                    f"-mmt={self.threads}",
                    str(destination),
                    str(source),
                ]
            )
        raise ValueError(f"unsupported codec family: {self.family}")

    def compression_metrics_plan(
        self, source: Path, destination: Path
    ) -> CommandPlan | None:
        if self.family != "mathzip":
            return None
        plan = self.compression_plan(source, destination)
        metrics_path = destination.with_name(destination.name + ".metrics.json")
        command = [
            *plan.command[:-2],
            "--metrics-output",
            str(metrics_path),
            *plan.command[-2:],
        ]
        return CommandPlan(command, metrics_path=metrics_path)

    def decompression_plan(self, source: Path, destination: Path) -> CommandPlan:
        executable = self._require_executable()
        if self.family == "mathzip":
            return CommandPlan(
                [
                    executable,
                    "decompress",
                    "--max-archive-bytes",
                    str(BENCHMARK_MAX_ARCHIVE_BYTES),
                    "--max-output-bytes",
                    str(BENCHMARK_MAX_OUTPUT_BYTES),
                    "--max-segments",
                    str(BENCHMARK_MAX_SEGMENTS),
                    "--no-sync",
                    "--force",
                    str(source),
                    str(destination),
                ]
            )
        if self.family == "gzip":
            return CommandPlan(
                [executable, "-d", "-c", str(source)], stdout_to_output=True
            )
        if self.family == "bzip2":
            return CommandPlan(
                [executable, "-d", "-c", str(source)], stdout_to_output=True
            )
        if self.family == "xz":
            return CommandPlan(
                [
                    executable,
                    "-d",
                    "-c",
                    f"--threads={self.threads}",
                    str(source),
                ],
                stdout_to_output=True,
            )
        if self.family == "zstd":
            return CommandPlan(
                [executable, "-q", "-d", "-c", str(source)], stdout_to_output=True
            )
        if self.family == "lz4":
            return CommandPlan(
                [executable, "-q", "-d", "-f", str(source), str(destination)]
            )
        if self.family == "brotli":
            return CommandPlan(
                [executable, "-f", "-d", "-o", str(destination), str(source)]
            )
        if self.family == "7z":
            return CommandPlan(
                [
                    executable,
                    "e",
                    "-so",
                    "-bd",
                    f"-mmt={self.threads}",
                    str(source),
                ],
                stdout_to_output=True,
            )
        raise ValueError(f"unsupported codec family: {self.family}")

    def inspection_plan(self, archive: Path) -> CommandPlan | None:
        if self.family != "mathzip" or not self.executable:
            return None
        return CommandPlan(
            [
                self.executable,
                "inspect",
                "--json",
                "--max-archive-bytes",
                str(BENCHMARK_MAX_ARCHIVE_BYTES),
                "--max-output-bytes",
                str(BENCHMARK_MAX_OUTPUT_BYTES),
                "--max-segments",
                str(BENCHMARK_MAX_SEGMENTS),
                str(archive),
            ]
        )

    def _require_executable(self) -> str:
        if not self.executable:
            raise FileNotFoundError(self.available_reason or self.family)
        return self.executable


def _resolve_executable(value: str | None) -> str | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    if candidate.parent != Path(".") or os.sep in value:
        return str(candidate.resolve()) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    return shutil.which(value)


def _parse_name(name: str) -> tuple[str, str]:
    if name == "raw":
        return "raw", "default"
    for family in ("mathzip", "gzip", "bzip2", "xz", "zstd", "lz4", "brotli", "7z"):
        prefix = family + "-"
        if name.startswith(prefix):
            level = name[len(prefix) :]
            if family == "mathzip" and level in {"fast", "balanced", "max"}:
                return family, level
            if level in {"fast", "default", "max"}:
                return family, level
    raise ValueError(f"unknown codec name: {name}")


def build_codec(
    entry: str | dict[str, Any],
    *,
    mathzip_binary: str,
    threads: int,
) -> Codec:
    options: dict[str, Any]
    if isinstance(entry, str):
        name = entry
        options = {}
    elif isinstance(entry, dict):
        options = entry
        name = str(options.get("name", ""))
    else:
        raise ValueError("codec entries must be strings or mappings")
    family, level = _parse_name(str(options.get("base", name)))
    if family == "raw":
        return Codec(
            name=name,
            family=family,
            level=level,
            extension=".raw",
            executable=None,
            threads=threads,
            variant=str(options.get("variant", "")),
        )
    if options.get("supported", True) is False:
        return Codec(
            name=name,
            family=family,
            level=level,
            extension=".unavailable",
            executable=None,
            mode=level if family == "mathzip" else None,
            threads=threads,
            variant=str(options.get("variant", name)),
            available_reason=str(options.get("skip_reason", "disabled by config")),
        )
    executable_names = {
        "mathzip": mathzip_binary,
        "gzip": "gzip",
        "bzip2": "bzip2",
        "xz": "xz",
        "zstd": "zstd",
        "lz4": "lz4",
        "brotli": "brotli",
        "7z": shutil.which("7zz") or "7z",
    }
    requested_executable = str(options.get("executable", executable_names[family]))
    executable = _resolve_executable(requested_executable)
    extensions = {
        "mathzip": ".mz",
        "gzip": ".gz",
        "bzip2": ".bz2",
        "xz": ".xz",
        "zstd": ".zst",
        "lz4": ".lz4",
        "brotli": ".br",
        "7z": ".7z",
    }
    return Codec(
        name=name,
        family=family,
        level=level,
        extension=extensions[family],
        executable=executable,
        mode=level if family == "mathzip" else None,
        threads=int(options.get("threads", threads)),
        variant=str(options.get("variant", "")),
        extra_compress_args=[str(value) for value in options.get("compress_args", [])],
        available_reason=(
            None if executable else f"executable not found: {requested_executable}"
        ),
    )


def compressor_tool_names() -> list[str]:
    return ["gzip", "bzip2", "xz", "zstd", "lz4", "brotli", "7z", "7zz"]
