#!/usr/bin/env python3
"""Safely materialize the pinned, non-holdout MathSVG validation dataset.

This module intentionally handles only UCI dataset 1081.  It verifies both the
downloaded archive and the extracted payload against pinned byte counts and
SHA-256 digests.  It never calls ``extractall`` and never follows archive or
destination symlinks.

The resulting sample is validation data: it may be inspected during validation
work, but it must not be used as a sealed holdout or marked legacy-observed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pathlib
import stat
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from typing import BinaryIO, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
    from mathsvg.python.datasets.manifest import (  # type: ignore[import-not-found]
        FIELDS,
        DatasetEntry,
        ManifestError,
        canonical_manifest_sha256,
        composition,
        load_manifest,
        report,
        validate_collection,
    )
else:
    from .manifest import (
        FIELDS,
        DatasetEntry,
        ManifestError,
        canonical_manifest_sha256,
        composition,
        load_manifest,
        report,
        validate_collection,
    )


MEBIBYTE = 1024 * 1024
COPY_CHUNK_BYTES = MEBIBYTE


class MaterializationError(ValueError):
    """A deterministic validation materialization failure."""


@dataclass(frozen=True, slots=True)
class ValidationLimits:
    """Hard limits applied before and during decompression."""

    max_entries: int = 8
    max_archive_bytes: int = 8 * MEBIBYTE
    max_compressed_bytes: int = 8 * MEBIBYTE
    max_uncompressed_bytes: int = 16 * MEBIBYTE


@dataclass(frozen=True, slots=True)
class ValidationSource:
    """Pinned provenance and expected bytes for one validation payload."""

    dataset_id: str
    split_group: str
    domain: str
    source_url: str
    license: str
    archive_sha256: str
    archive_bytes: int
    member_name: str
    member_compressed_bytes: int
    payload_sha256: str
    payload_bytes: int
    output_relative: str


UCI_GAS_SENSOR_LOW_CONCENTRATION = ValidationSource(
    dataset_id="validation-uci-1081-gsalc-csv",
    split_group="uci-1081-gas-sensor-low-concentration",
    domain="structured-sensor-csv",
    source_url=(
        "https://archive.ics.uci.edu/static/public/1081/"
        "gas%2Bsensor%2Barray%2Blow-concentration.zip"
    ),
    license="CC BY 4.0",
    archive_sha256=(
        "813ebb7d00ed9172114b68d27ab3213170d6c220b8b23907dce91e9838cd48ab"
    ),
    archive_bytes=610_209,
    member_name="gsalc.csv",
    member_compressed_bytes=610_077,
    payload_sha256=(
        "d79afe6494903b988de68d4a8ab133167151a1bb620ba413e5cefd15003d07b3"
    ),
    payload_bytes=5_222_034,
    output_relative="uci-1081/gsalc.csv",
)


def _validate_safe_relative(value: str, label: str) -> pathlib.PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    if value.startswith("/") or value.endswith("/"):
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    if raw_parts[0].endswith(":"):
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    result = pathlib.PurePosixPath(value)
    if result.is_absolute() or ".." in result.parts:
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    return result


def _sha256_stream(handle: BinaryIO, byte_limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while chunk := handle.read(COPY_CHUNK_BYTES):
        total += len(chunk)
        if total > byte_limit:
            raise MaterializationError(
                f"stream exceeds the {byte_limit}-byte safety limit"
            )
        digest.update(chunk)
    return digest.hexdigest(), total


def _sha256_file(path: pathlib.Path, byte_limit: int) -> tuple[str, int]:
    with path.open("rb") as handle:
        return _sha256_stream(handle, byte_limit)


def _verify_regular_unsymlinked_file(path: pathlib.Path, label: str) -> os.stat_result:
    try:
        result = path.lstat()
    except FileNotFoundError as exc:
        raise MaterializationError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(result.st_mode):
        raise MaterializationError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(result.st_mode):
        raise MaterializationError(f"{label} must be a regular file: {path}")
    return result


def _verify_archive_identity(
    archive: pathlib.Path,
    source: ValidationSource,
    limits: ValidationLimits,
) -> None:
    archive_stat = _verify_regular_unsymlinked_file(archive, "archive")
    if archive_stat.st_size > limits.max_archive_bytes:
        raise MaterializationError(
            f"archive exceeds max_archive_bytes={limits.max_archive_bytes}"
        )
    if archive_stat.st_size != source.archive_bytes:
        raise MaterializationError(
            "archive byte count mismatch: "
            f"expected {source.archive_bytes}, got {archive_stat.st_size}"
        )
    archive_hash, archive_bytes = _sha256_file(
        archive, limits.max_archive_bytes
    )
    if archive_bytes != source.archive_bytes or archive_hash != source.archive_sha256:
        raise MaterializationError(
            "archive SHA-256 mismatch: "
            f"expected {source.archive_sha256}, got {archive_hash}"
        )


def _verify_zip_catalogue(
    archive: zipfile.ZipFile,
    source: ValidationSource,
    limits: ValidationLimits,
) -> zipfile.ZipInfo:
    entries = archive.infolist()
    if len(entries) > limits.max_entries:
        raise MaterializationError(
            f"archive has {len(entries)} entries; limit is {limits.max_entries}"
        )
    if len(entries) != 1:
        raise MaterializationError(
            f"archive must contain exactly one entry, found {len(entries)}"
        )

    total_compressed = 0
    total_uncompressed = 0
    seen_names: set[str] = set()
    for info in entries:
        _validate_safe_relative(info.filename, "archive member")
        if info.filename in seen_names:
            raise MaterializationError(
                f"archive contains duplicate member {info.filename!r}"
            )
        seen_names.add(info.filename)
        if info.is_dir():
            raise MaterializationError(
                f"archive member must be a regular file: {info.filename!r}"
            )
        unix_mode = info.external_attr >> 16
        file_type = stat.S_IFMT(unix_mode)
        if file_type == stat.S_IFLNK:
            raise MaterializationError(
                f"archive member must not be a symlink: {info.filename!r}"
            )
        if file_type not in {0, stat.S_IFREG}:
            raise MaterializationError(
                f"archive member has unsupported file type: {info.filename!r}"
            )
        if info.flag_bits & 0x1:
            raise MaterializationError(
                f"encrypted archive member is forbidden: {info.filename!r}"
            )
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise MaterializationError(
                f"unsupported ZIP compression method for {info.filename!r}"
            )
        total_compressed += info.compress_size
        total_uncompressed += info.file_size

    if total_compressed > limits.max_compressed_bytes:
        raise MaterializationError(
            "archive members exceed max_compressed_bytes="
            f"{limits.max_compressed_bytes}"
        )
    if total_uncompressed > limits.max_uncompressed_bytes:
        raise MaterializationError(
            "archive members exceed max_uncompressed_bytes="
            f"{limits.max_uncompressed_bytes}"
        )

    member = entries[0]
    if member.filename != source.member_name:
        raise MaterializationError(
            f"expected archive member {source.member_name!r}, "
            f"found {member.filename!r}"
        )
    if member.compress_size != source.member_compressed_bytes:
        raise MaterializationError(
            "compressed member byte count mismatch: "
            f"expected {source.member_compressed_bytes}, got {member.compress_size}"
        )
    if member.file_size != source.payload_bytes:
        raise MaterializationError(
            "uncompressed member byte count mismatch: "
            f"expected {source.payload_bytes}, got {member.file_size}"
        )
    return member


def _ensure_unsymlinked_directory(path: pathlib.Path) -> None:
    if path.exists() or path.is_symlink():
        result = path.lstat()
        if stat.S_ISLNK(result.st_mode):
            raise MaterializationError(
                f"destination directory must not be a symlink: {path}"
            )
        if not stat.S_ISDIR(result.st_mode):
            raise MaterializationError(
                f"destination parent is not a directory: {path}"
            )
        return
    parent = path.parent
    if parent != path:
        _ensure_unsymlinked_directory(parent)
    path.mkdir()


def _safe_destination(
    output_root: pathlib.Path,
    source: ValidationSource,
) -> pathlib.Path:
    relative = _validate_safe_relative(
        source.output_relative, "output_relative"
    )
    _ensure_unsymlinked_directory(output_root)
    current = output_root
    for component in relative.parts[:-1]:
        current = current / component
        _ensure_unsymlinked_directory(current)
    destination = output_root.joinpath(*relative.parts)
    if destination.is_symlink():
        raise MaterializationError(
            f"destination payload must not be a symlink: {destination}"
        )
    return destination


def _verify_payload_file(
    path: pathlib.Path,
    source: ValidationSource,
    limits: ValidationLimits,
) -> None:
    payload_stat = _verify_regular_unsymlinked_file(path, "payload")
    if payload_stat.st_size != source.payload_bytes:
        raise MaterializationError(
            "payload byte count mismatch: "
            f"expected {source.payload_bytes}, got {payload_stat.st_size}"
        )
    payload_hash, payload_bytes = _sha256_file(
        path, limits.max_uncompressed_bytes
    )
    if payload_bytes != source.payload_bytes or payload_hash != source.payload_sha256:
        raise MaterializationError(
            "payload SHA-256 mismatch: "
            f"expected {source.payload_sha256}, got {payload_hash}"
        )


def materialize_archive(
    archive_path: pathlib.Path,
    output_root: pathlib.Path,
    source: ValidationSource = UCI_GAS_SENSOR_LOW_CONCENTRATION,
    limits: ValidationLimits = ValidationLimits(),
) -> pathlib.Path:
    """Verify and extract a single pinned payload without overwriting data."""

    if limits.max_entries <= 0:
        raise MaterializationError("max_entries must be positive")
    if min(
        limits.max_archive_bytes,
        limits.max_compressed_bytes,
        limits.max_uncompressed_bytes,
    ) < 0:
        raise MaterializationError("byte limits must be non-negative")

    archive_path = archive_path.absolute()
    output_root = output_root.absolute()
    _verify_archive_identity(archive_path, source, limits)
    destination = _safe_destination(output_root, source)

    if destination.exists():
        _verify_payload_file(destination, source, limits)
        return destination

    temporary_path: pathlib.Path | None = None
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            member = _verify_zip_catalogue(archive, source, limits)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            temporary_path = pathlib.Path(temporary_name)
            with os.fdopen(descriptor, "wb") as output, archive.open(
                member, "r"
            ) as payload:
                digest = hashlib.sha256()
                payload_bytes = 0
                while chunk := payload.read(COPY_CHUNK_BYTES):
                    payload_bytes += len(chunk)
                    if payload_bytes > limits.max_uncompressed_bytes:
                        raise MaterializationError(
                            "decompressed payload exceeds "
                            f"max_uncompressed_bytes={limits.max_uncompressed_bytes}"
                        )
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())

            if payload_bytes != source.payload_bytes:
                raise MaterializationError(
                    "extracted payload byte count mismatch: "
                    f"expected {source.payload_bytes}, got {payload_bytes}"
                )
            payload_hash = digest.hexdigest()
            if payload_hash != source.payload_sha256:
                raise MaterializationError(
                    "extracted payload SHA-256 mismatch: "
                    f"expected {source.payload_sha256}, got {payload_hash}"
                )
            temporary_path.chmod(0o644)
            try:
                os.link(temporary_path, destination, follow_symlinks=False)
            except FileExistsError:
                _verify_payload_file(destination, source, limits)
            temporary_path.unlink()
            temporary_path = None
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise MaterializationError(f"invalid ZIP archive: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    _verify_payload_file(destination, source, limits)
    return destination


def build_validation_entry(
    repository: pathlib.Path,
    payload_path: pathlib.Path,
    source: ValidationSource = UCI_GAS_SENSOR_LOW_CONCENTRATION,
) -> DatasetEntry:
    """Create the canonical UCI validation row after payload verification."""

    repository = repository.resolve()
    try:
        relative = payload_path.resolve(strict=True).relative_to(repository)
    except ValueError as exc:
        raise MaterializationError(
            "validation payload must be inside the repository"
        ) from exc
    return DatasetEntry(
        split="validation",
        dataset_id=source.dataset_id,
        split_group=source.split_group,
        origin="real",
        primary=True,
        domain=source.domain,
        source=source.source_url,
        license=source.license,
        sha256=source.payload_sha256,
        bytes=source.payload_bytes,
        sealed=False,
        path=relative.as_posix(),
        legacy_observed=False,
    )


def _atomic_write_text(path: pathlib.Path, payload: str) -> None:
    _ensure_unsymlinked_directory(path.parent)
    if path.is_symlink():
        raise MaterializationError(
            f"generated metadata path must not be a symlink: {path}"
        )
    if path.exists() and not path.is_file():
        raise MaterializationError(
            f"generated metadata path must be a regular file: {path}"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_manifest(path: pathlib.Path, entries: Sequence[DatasetEntry]) -> None:
    """Write canonical CSV bytes: exact header, row order, and LF endings."""

    with tempfile.SpooledTemporaryFile(
        mode="w+", encoding="utf-8", newline=""
    ) as buffer:
        writer = csv.DictWriter(
            buffer, fieldnames=FIELDS, lineterminator="\n"
        )
        writer.writeheader()
        for entry in sorted(entries, key=lambda item: item.dataset_id):
            row = asdict(entry)
            for field in ("primary", "sealed", "legacy_observed"):
                row[field] = "true" if row[field] else "false"
            writer.writerow(row)
        buffer.seek(0)
        _atomic_write_text(path, buffer.read())


def upsert_validation_entries(
    existing: Sequence[DatasetEntry],
    additions: Sequence[DatasetEntry],
) -> list[DatasetEntry]:
    """Merge idempotently, refusing to replace a changed validation row."""

    if any(entry.split != "validation" for entry in [*existing, *additions]):
        raise MaterializationError(
            "validation merge contains a non-validation row"
        )
    by_id = {entry.dataset_id: entry for entry in existing}
    if len(by_id) != len(existing):
        raise MaterializationError("existing validation rows contain duplicates")
    for entry in additions:
        previous = by_id.get(entry.dataset_id)
        if previous is not None and previous != entry:
            raise MaterializationError(
                f"refusing to replace changed validation row {entry.dataset_id!r}"
            )
        by_id[entry.dataset_id] = entry
    result = sorted(by_id.values(), key=lambda entry: entry.dataset_id)
    validate_collection(result)
    return result


def merge_development_validation(
    development: Sequence[DatasetEntry],
    validation: Sequence[DatasetEntry],
) -> dict[str, object]:
    """Validate the disjoint split union without emitting or touching holdout."""

    if not development:
        raise MaterializationError("development manifest must not be empty")
    if not validation:
        raise MaterializationError("validation manifest must not be empty")
    if any(entry.split != "development" for entry in development):
        raise MaterializationError(
            "development manifest contains a non-development row"
        )
    if any(entry.split != "validation" for entry in validation):
        raise MaterializationError(
            "validation manifest contains a non-validation row"
        )
    combined = [*development, *validation]
    validate_collection(combined)
    return {
        "development_rows": len(development),
        "validation_rows": len(validation),
        "canonical_manifest_sha256": canonical_manifest_sha256(combined),
        "composition": composition(combined),
    }


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "archive",
        type=pathlib.Path,
        help="downloaded, pinned UCI dataset 1081 ZIP archive",
    )
    parser.add_argument(
        "--repository",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[3],
    )
    parser.add_argument(
        "--data-root",
        type=pathlib.Path,
        default=pathlib.Path("datasets/data/validation"),
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/manifests/validation.csv"),
    )
    parser.add_argument(
        "--development-manifest",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/manifests/development.csv"
        ),
        help="read-only manifest merged for split-leakage validation",
    )
    parser.add_argument(
        "--report",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/manifests/validation-report.json"
        ),
    )
    return parser.parse_args(argv)


def _under_repository(
    repository: pathlib.Path, configured: pathlib.Path
) -> pathlib.Path:
    result = configured if configured.is_absolute() else repository / configured
    normalized = pathlib.Path(os.path.abspath(result))
    try:
        normalized.relative_to(repository)
    except ValueError as exc:
        raise MaterializationError(
            f"output must remain inside repository: {configured}"
        ) from exc
    return normalized


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    repository = args.repository.resolve()
    data_root = _under_repository(repository, args.data_root)
    output = _under_repository(repository, args.output)
    development_path = _under_repository(
        repository, args.development_manifest
    )
    report_path = _under_repository(repository, args.report)
    source = UCI_GAS_SENSOR_LOW_CONCENTRATION
    limits = ValidationLimits()
    try:
        payload = materialize_archive(
            args.archive, data_root, source=source, limits=limits
        )
        entry = build_validation_entry(repository, payload, source)
        entry.validate(2)
        existing = load_manifest(output) if output.exists() else []
        merged = upsert_validation_entries(existing, [entry])
        write_manifest(output, merged)
        validated = load_manifest(output)
        development = load_manifest(development_path)
        result = report(validated)
        result["development_validation_merge"] = merge_development_validation(
            development, validated
        )
        result["validation_payload_inspected"] = True
        result["source_archive"] = {
            "bytes": source.archive_bytes,
            "member": source.member_name,
            "member_compressed_bytes": source.member_compressed_bytes,
            "sha256": source.archive_sha256,
            "url": source.source_url,
        }
        result["verified_payload"] = {
            "bytes": source.payload_bytes,
            "path": entry.path,
            "sha256": source.payload_sha256,
        }
        _atomic_write_text(
            report_path,
            json.dumps(result, indent=2, sort_keys=True) + "\n",
        )
    except (
        ManifestError,
        MaterializationError,
        OSError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"validation materialization error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
