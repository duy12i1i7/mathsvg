"""Manifest-driven, checksummed and traversal-safe dataset preparation."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable

from .common import (
    atomic_write_json,
    checksum_file,
    ensure_relative_to,
    iter_files,
    sha256_file,
)


class DatasetError(RuntimeError):
    pass


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"cannot load manifest {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise DatasetError(f"{path}: unsupported or missing schema_version")
    if not isinstance(value.get("name"), str) or not value["name"]:
        raise DatasetError(f"{path}: dataset name is required")
    archive = value.get("archive")
    if not isinstance(archive, dict):
        raise DatasetError(f"{path}: archive mapping is required")
    urls = archive.get("urls")
    if not isinstance(urls, list) or not urls:
        raise DatasetError(f"{path}: archive.urls must be a non-empty list")
    checksums = archive.get("checksums", {})
    expected = value.get("expected_files", [])
    has_payload_checksum = any(
        isinstance(item, dict) and bool(item.get("checksums")) for item in expected
    )
    if not checksums and not has_payload_checksum:
        raise DatasetError(
            f"{path}: archive or at least one extracted payload needs a checksum"
        )
    return value


def _safe_member_path(name: str) -> Path:
    if not name or "\x00" in name or "\\" in name:
        raise DatasetError(f"unsafe archive member name: {name!r}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise DatasetError(f"unsafe archive member path: {name!r}")
    if pure.parts and ":" in pure.parts[0]:
        raise DatasetError(f"unsafe drive-like archive member path: {name!r}")
    return Path(*pure.parts)


def _selected(relative: Path, allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    normalized = relative.as_posix()
    return normalized in allowed


def _copy_limited(source: BinaryIO, destination: BinaryIO, limit: int) -> int:
    total = 0
    while chunk := source.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise DatasetError(f"extracted data exceeds safety limit of {limit} bytes")
        destination.write(chunk)
    return total


def _extract_tar(
    archive_path: Path,
    destination: Path,
    allowed: set[str] | None,
    max_uncompressed: int,
    max_entries: int,
) -> None:
    total = 0
    destinations: set[Path] = set()
    with tarfile.open(archive_path, mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > max_entries:
            raise DatasetError(f"tar has {len(members)} entries; limit is {max_entries}")
        for member in members:
            relative = _safe_member_path(member.name.rstrip("/"))
            if member.isdir():
                continue
            if not member.isfile():
                raise DatasetError(
                    f"tar member is not a regular file: {member.name!r}"
                )
            if not _selected(relative, allowed):
                continue
            total += member.size
            if total > max_uncompressed:
                raise DatasetError("tar exceeds configured uncompressed-size limit")
            target = destination / relative
            ensure_relative_to(target, destination)
            if target in destinations:
                raise DatasetError(f"duplicate archive destination: {relative}")
            destinations.add(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise DatasetError(f"cannot read tar member: {member.name}")
            with source, target.open("wb") as output:
                copied = _copy_limited(source, output, member.size)
            if copied != member.size:
                raise DatasetError(
                    f"truncated tar member {member.name}: {copied} != {member.size}"
                )


def _extract_zip(
    archive_path: Path,
    destination: Path,
    allowed: set[str] | None,
    max_uncompressed: int,
    max_entries: int,
    max_compression_ratio: float,
) -> None:
    total = 0
    destinations: set[Path] = set()
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > max_entries:
            raise DatasetError(f"zip has {len(members)} entries; limit is {max_entries}")
        for member in members:
            relative = _safe_member_path(member.filename.rstrip("/"))
            unix_mode = (member.external_attr >> 16) & 0xFFFF
            if unix_mode and stat.S_ISLNK(unix_mode):
                raise DatasetError(f"zip symlink is forbidden: {member.filename!r}")
            if member.is_dir():
                continue
            if not _selected(relative, allowed):
                continue
            total += member.file_size
            if total > max_uncompressed:
                raise DatasetError("zip exceeds configured uncompressed-size limit")
            if (
                member.compress_size > 0
                and member.file_size / member.compress_size > max_compression_ratio
            ):
                raise DatasetError(
                    f"zip member {member.filename!r} exceeds compression-ratio limit"
                )
            target = destination / relative
            ensure_relative_to(target, destination)
            if target in destinations:
                raise DatasetError(f"duplicate archive destination: {relative}")
            destinations.add(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, target.open("wb") as output:
                copied = _copy_limited(source, output, member.file_size)
            if copied != member.file_size:
                raise DatasetError(
                    f"truncated zip member {member.filename}: "
                    f"{copied} != {member.file_size}"
                )


def _extract_gzip(
    archive_path: Path, destination: Path, output_name: str, max_uncompressed: int
) -> None:
    relative = _safe_member_path(output_name)
    target = destination / relative
    ensure_relative_to(target, destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive_path, "rb") as source, target.open("wb") as output:
        _copy_limited(source, output, max_uncompressed)


def _download(
    urls: Iterable[str],
    destination: Path,
    expected_size: int | None,
    max_download_bytes: int,
    timeout: float,
) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for url in urls:
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.unlink(missing_ok=True)
        request = urllib.request.Request(
            str(url),
            headers={"User-Agent": "MathZip-dataset-downloader/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response, partial.open(
                "wb"
            ) as output:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_download_bytes:
                        raise DatasetError(
                            f"download exceeds limit of {max_download_bytes} bytes"
                        )
                    output.write(chunk)
            if expected_size is not None and total != expected_size:
                raise DatasetError(
                    f"downloaded size {total} does not match expected {expected_size}"
                )
            partial.replace(destination)
            return str(url)
        except (OSError, urllib.error.URLError, DatasetError) as exc:
            partial.unlink(missing_ok=True)
            errors.append(f"{url}: {exc}")
    raise DatasetError("all download URLs failed:\n  " + "\n  ".join(errors))


def _verify_checksums(path: Path, expected: dict[str, str], label: str) -> None:
    for algorithm, wanted in expected.items():
        actual = checksum_file(path, algorithm)
        if actual.lower() != str(wanted).lower():
            raise DatasetError(
                f"{label} {algorithm} mismatch: expected {wanted}, got {actual}"
            )


def _verify_expected_files(root: Path, expected: list[dict[str, Any]]) -> None:
    for entry in expected:
        relative = _safe_member_path(str(entry["path"]))
        path = root / relative
        ensure_relative_to(path, root)
        if not path.is_file():
            raise DatasetError(f"expected extracted file is missing: {relative}")
        size = entry.get("size_bytes")
        if size is not None and path.stat().st_size != int(size):
            raise DatasetError(
                f"{relative} size mismatch: {path.stat().st_size} != {size}"
            )
        _verify_checksums(path, entry.get("checksums", {}), str(relative))


def _create_combined(root: Path, spec: dict[str, Any]) -> None:
    output_name = str(spec.get("output", "combined.bin"))
    manifest_name = str(spec.get("manifest", "combined.manifest.json"))
    output_path = root / _safe_member_path(output_name)
    manifest_path = root / _safe_member_path(manifest_name)
    members = [str(item) for item in spec.get("members", [])]
    if not members:
        raise DatasetError("combined.members must not be empty")
    records: list[dict[str, Any]] = []
    offset = 0
    with output_path.open("wb") as output:
        for member_name in members:
            relative = _safe_member_path(member_name)
            source = root / relative
            if not source.is_file():
                raise DatasetError(f"combined member is missing: {relative}")
            digest = hashlib.sha256()
            length = 0
            with source.open("rb") as input_handle:
                while chunk := input_handle.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    length += len(chunk)
            records.append(
                {
                    "path": relative.as_posix(),
                    "offset": offset,
                    "length": length,
                    "sha256": digest.hexdigest(),
                }
            )
            offset += length
    atomic_write_json(
        manifest_path,
        {
            "schema_version": "mathzip-combined-stream-v1",
            "output": output_name,
            "size_bytes": offset,
            "sha256": sha256_file(output_path),
            "members": records,
        },
    )


def _existing_is_valid(destination: Path, manifest_sha256: str) -> bool:
    metadata_path = destination / "dataset.json"
    if not metadata_path.is_file():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if metadata.get("manifest_sha256") != manifest_sha256:
        return False
    for entry in metadata.get("files", []):
        path = destination / str(entry.get("path", ""))
        if (
            not path.is_file()
            or path.stat().st_size != entry.get("size_bytes")
            or sha256_file(path) != entry.get("sha256")
        ):
            return False
    return True


def prepare_dataset(
    manifest_path: Path,
    output_root: Path,
    download_root: Path | None = None,
    *,
    force: bool = False,
    timeout: float = 60.0,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    name = manifest["name"]
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in name):
        raise DatasetError(f"unsafe dataset name: {name!r}")
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / name
    ensure_relative_to(destination, output_root)
    manifest_sha256 = sha256_file(manifest_path)
    if destination.exists() and not force:
        if _existing_is_valid(destination, manifest_sha256):
            return {
                "name": name,
                "status": "already_prepared",
                "path": str(destination),
            }
        raise DatasetError(
            f"{destination} exists but does not match the manifest; use --force"
        )

    archive_spec = manifest["archive"]
    archive_name = str(archive_spec.get("filename") or Path(archive_spec["urls"][0]).name)
    _safe_member_path(archive_name)
    downloads = download_root or output_root / "_downloads"
    archive_path = downloads / archive_name
    ensure_relative_to(archive_path, downloads)
    checksums = archive_spec.get("checksums", {})
    used_url: str | None = None
    if archive_path.is_file():
        try:
            if archive_spec.get("size_bytes") is not None and archive_path.stat().st_size != int(
                archive_spec["size_bytes"]
            ):
                raise DatasetError("cached archive size mismatch")
            _verify_checksums(archive_path, checksums, "cached archive")
        except DatasetError:
            archive_path.unlink(missing_ok=True)
    if not archive_path.is_file():
        size = archive_spec.get("size_bytes")
        default_limit = int(size) + max(1024 * 1024, int(size) // 100) if size else 2_000_000_000
        used_url = _download(
            archive_spec["urls"],
            archive_path,
            int(size) if size is not None else None,
            int(archive_spec.get("max_download_bytes", default_limit)),
            timeout,
        )
    _verify_checksums(archive_path, checksums, "archive")

    max_uncompressed = int(archive_spec.get("max_uncompressed_bytes", 4_000_000_000))
    max_entries = int(archive_spec.get("max_entries", 100_000))
    allowed = archive_spec.get("members")
    allowed_set = set(str(item) for item in allowed) if allowed is not None else None
    if allowed_set is not None:
        for item in allowed_set:
            _safe_member_path(item)

    with tempfile.TemporaryDirectory(prefix=f".{name}.", dir=output_root) as temporary:
        stage = Path(temporary) / "prepared"
        stage.mkdir()
        archive_type = str(archive_spec.get("type", "")).lower()
        if archive_type in {"tar", "tar.gz", "tgz", "tar.bz2", "tar.xz"}:
            _extract_tar(
                archive_path, stage, allowed_set, max_uncompressed, max_entries
            )
        elif archive_type == "zip":
            _extract_zip(
                archive_path,
                stage,
                allowed_set,
                max_uncompressed,
                max_entries,
                float(archive_spec.get("max_compression_ratio", 100_000.0)),
            )
        elif archive_type == "gzip":
            _extract_gzip(
                archive_path,
                stage,
                str(archive_spec["output_name"]),
                max_uncompressed,
            )
        elif archive_type == "file":
            relative = _safe_member_path(str(archive_spec["output_name"]))
            shutil.copyfile(archive_path, stage / relative)
        else:
            raise DatasetError(f"unsupported archive type: {archive_type!r}")

        _verify_expected_files(stage, manifest.get("expected_files", []))
        if manifest.get("combined"):
            _create_combined(stage, manifest["combined"])
        files = [
            {
                "path": path.relative_to(stage).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in iter_files(stage)
        ]
        metadata = {
            "schema_version": "mathzip-prepared-dataset-v1",
            "name": name,
            "description": manifest.get("description"),
            "homepage": manifest.get("homepage"),
            "license": manifest.get("license"),
            "manifest": str(manifest_path),
            "manifest_sha256": manifest_sha256,
            "archive": {
                "path": str(archive_path),
                "source_url": used_url,
                "size_bytes": archive_path.stat().st_size,
                "sha256": sha256_file(archive_path),
                "declared_checksums": checksums,
            },
            "files": files,
        }
        atomic_write_json(stage / "dataset.json", metadata)
        if destination.exists():
            ensure_relative_to(destination, output_root)
            shutil.rmtree(destination)
        stage.replace(destination)
    return {
        "name": name,
        "status": "prepared",
        "path": str(destination),
        "files": len(files),
    }


def selected_dataset_names(config: dict[str, Any]) -> list[str]:
    selected: list[str] = []
    for item in config.get("datasets", []):
        if isinstance(item, str):
            selected.append(item)
        elif isinstance(item, dict) and item.get("enabled", True):
            name = item.get("name")
            if not isinstance(name, str):
                raise DatasetError("dataset config entries require a name")
            selected.append(name)
        else:
            if not isinstance(item, dict):
                raise DatasetError("datasets must contain strings or mappings")
    return selected
