"""Small, dependency-free helpers shared by the benchmark tools."""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    """Hash MathZip's normalized JSON representation.

    ``canonical-json-v1`` is deliberately project-local: sorted keys, compact
    separators, UTF-8 without ASCII escaping, no trailing newline, and no
    non-finite numbers.
    """

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def checksum_file(path: Path, algorithm: str, chunk_size: int = 1024 * 1024) -> str:
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:
        raise ValueError(f"unsupported checksum algorithm: {algorithm}") from exc
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def median_or_none(values: Iterable[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.median(clean) if clean else None


def geometric_mean(values: Iterable[float | int]) -> float | None:
    clean = [float(value) for value in values if float(value) > 0.0]
    if not clean:
        return None
    return math.exp(sum(math.log(value) for value in clean) / len(clean))


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def flatten_mapping(
    mapping: Mapping[str, Any], prefix: str = "", separator: str = "."
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in mapping.items():
        name = f"{prefix}{separator}{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            result.update(flatten_mapping(value, name, separator))
        else:
            result[name] = value
    return result


def iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            yield path


def shannon_entropy(path: Path, chunk_size: int = 1024 * 1024) -> float:
    counts = [0] * 256
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            total += len(chunk)
            for value in chunk:
                counts[value] += 1
    if total == 0:
        return 0.0
    return -sum(
        (count / total) * math.log2(count / total) for count in counts if count
    )


def ensure_relative_to(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"path escapes destination root: {path}") from exc


def quote_command(command: Sequence[str]) -> str:
    import shlex

    return shlex.join(str(item) for item in command)
