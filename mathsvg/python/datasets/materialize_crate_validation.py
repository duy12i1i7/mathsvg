#!/usr/bin/env python3
"""Safely materialize a pinned crates.io source corpus for validation.

The command never downloads data. It accepts an already downloaded ``.crate``
archive, verifies its crates.io SHA-256 and complete tar catalogue, rejects
unsafe member types and paths, and then copies only the independently pinned
Rust source members. Existing payloads are accepted only when their exact
length and SHA-256 still match.

This module never reads, writes, or names the sealed holdout directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from typing import BinaryIO, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
    from mathsvg.python.datasets.manifest import (  # type: ignore[import-not-found]
        DatasetEntry,
        ManifestError,
        load_manifest,
        report,
        validate_collection,
    )
    from mathsvg.python.datasets.materialize_validation import (  # type: ignore[import-not-found]
        MaterializationError,
        merge_development_validation,
        upsert_validation_entries,
        write_manifest,
    )
else:
    from .manifest import (
        DatasetEntry,
        ManifestError,
        load_manifest,
        report,
        validate_collection,
    )
    from .materialize_validation import (
        MaterializationError,
        merge_development_validation,
        upsert_validation_entries,
        write_manifest,
    )


MEBIBYTE = 1024 * 1024
COPY_CHUNK_BYTES = MEBIBYTE
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class CrateLimits:
    """Hard resource limits enforced before and during decompression."""

    max_entries: int = 64
    max_archive_bytes: int = MEBIBYTE
    max_uncompressed_bytes: int = 2 * MEBIBYTE
    max_selected_bytes: int = MEBIBYTE


@dataclass(frozen=True, slots=True)
class CrateMember:
    """One pinned source member selected from a crate archive."""

    name: str
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CrateSource:
    """Complete provenance and integrity declaration for one source crate."""

    dataset_prefix: str
    split_group: str
    domain: str
    source_url: str
    license: str
    archive_sha256: str
    archive_bytes: int
    archive_root: str
    catalogue_entries: int
    catalogue_file_bytes: int
    members: tuple[CrateMember, ...]


SERDE_1_0_229 = CrateSource(
    dataset_prefix="validation-crates-io-serde-1-0-229",
    split_group="crates-io-serde-1-0-229",
    domain="real-world-rust-crate-source",
    source_url=(
        "https://static.crates.io/crates/serde/serde-1.0.229.crate"
    ),
    license="MIT OR Apache-2.0",
    archive_sha256=(
        "4148590afebada386688f18773da617792bf2ef03ffc1e4cbd2b1d45b023e0ba"
    ),
    archive_bytes=83_669,
    archive_root="serde-1.0.229",
    catalogue_entries=33,
    catalogue_file_bytes=561_555,
    members=(
        CrateMember(
            "serde-1.0.229/build.rs",
            2_471,
            "e2ea6a7a82f5404c3e28cb055883c737ee7903367a10206fbb47b75c009f0eb3",
        ),
        CrateMember(
            "serde-1.0.229/src/core/crate_root.rs",
            7_081,
            "157ca402e23c32f11a4f1797c81afb5e9f08df96768012cf3e3199153aafb2dd",
        ),
        CrateMember(
            "serde-1.0.229/src/core/de/ignored_any.rs",
            6_154,
            "6480f2b2a83dc4764d01b2eec7309729eef2492eede2e5ee98d23a60b05198eb",
        ),
        CrateMember(
            "serde-1.0.229/src/core/de/impls.rs",
            97_134,
            "2f8e2ef9d343875bb996319e69feae0dee2287e2548cedd8d3c752b64812331c",
        ),
        CrateMember(
            "serde-1.0.229/src/core/de/mod.rs",
            83_879,
            "5ec8602d593915e0cf480b0ce67b02f6ab066dac34725237d2c7b4a6ef12a845",
        ),
        CrateMember(
            "serde-1.0.229/src/core/de/value.rs",
            48_848,
            "fb6fef6d23d95d516c6e1d6b5cefd8b98ba3881214a82a8a7e0a8ffbb0a12083",
        ),
        CrateMember(
            "serde-1.0.229/src/core/format.rs",
            726,
            "c85071b016df643b161859682d21ce34fa0ebf2a3bdbeeea69859da48f5d934f",
        ),
        CrateMember(
            "serde-1.0.229/src/core/lib.rs",
            4_351,
            "4e5c396d38ff5d9abf45b826a67ec3444654ed0802d057f46400ebe26f5879f8",
        ),
        CrateMember(
            "serde-1.0.229/src/core/macros.rs",
            8_276,
            "a61c9d19b210697304328e6bb9380a1de713e21042256df90a2b4553f178b0be",
        ),
        CrateMember(
            "serde-1.0.229/src/core/private/content.rs",
            750,
            "5fdfb2bb95ecc80375507acb813a4c640496385e56fc99ab448f6b19e01fcc01",
        ),
        CrateMember(
            "serde-1.0.229/src/core/private/doc.rs",
            5_146,
            "abe656c015267555ca26ebbcf2f4dcc52c719a0b9ade3a5ed4635b2784699b8c",
        ),
        CrateMember(
            "serde-1.0.229/src/core/private/mod.rs",
            504,
            "3bb3427ec80077b9df1853aa17681de796de0179d74871a96b88b72469de6cfc",
        ),
        CrateMember(
            "serde-1.0.229/src/core/private/seed.rs",
            635,
            "3f6e098c5bd314788370dcaf3ab0152fcd7feb6bcf36a9c51808938cd58071eb",
        ),
        CrateMember(
            "serde-1.0.229/src/core/private/size_hint.rs",
            692,
            "350694a2abaad94ca5d33958710a5bb8973a2ea1a3dcc50a41405c943761b81f",
        ),
        CrateMember(
            "serde-1.0.229/src/core/private/string.rs",
            794,
            "c1500fd4b64c24a5e45fa5f48c85c802816d6954a2999a72fc5a8861687212d4",
        ),
        CrateMember(
            "serde-1.0.229/src/core/ser/fmt.rs",
            4_166,
            "bd129d9f085933b76dafef6eb43ffac893c1f6484a3064dcd82faeeebc3b203c",
        ),
        CrateMember(
            "serde-1.0.229/src/core/ser/impls.rs",
            29_535,
            "5ee7efc439345e8665da0bd79bc06c02a0506e5fd0f3a4cf11af0c7197eaa643",
        ),
        CrateMember(
            "serde-1.0.229/src/core/ser/impossible.rs",
            5_308,
            "283f628d5107aa030d2e96eeb1dee187f0ac18c24d517edeb51738ab15dfb871",
        ),
        CrateMember(
            "serde-1.0.229/src/core/ser/mod.rs",
            65_231,
            "ec097d92c8545356961e977a4c9650361cadd1d3a243d805ae7b0e0e589ae803",
        ),
        CrateMember(
            "serde-1.0.229/src/core/std_error.rs",
            1_351,
            "b36fd6a2b6898770b9f1c51517eb362af115767d0f7cb4a713e1b949530ffa8a",
        ),
        CrateMember(
            "serde-1.0.229/src/integer128.rs",
            480,
            "a5ca321ace6b11b71f637397cf4ea41992545a9c297a23230c8a9e8b92db71fa",
        ),
        CrateMember(
            "serde-1.0.229/src/lib.rs",
            11_673,
            "9fcd921cee5dc64077f4027a3b42347253fdc3f3d9b88660e8c872c316db3620",
        ),
        CrateMember(
            "serde-1.0.229/src/private/de.rs",
            110_746,
            "68ee17774d3bbc693d9f19803747fa2c3afdb7046482e6d94ac6cf7db5a4fe20",
        ),
        CrateMember(
            "serde-1.0.229/src/private/mod.rs",
            574,
            "52cc5b33364b13724ea4c2989f3ccd8ba02a2ef903761499e15264835ff76388",
        ),
        CrateMember(
            "serde-1.0.229/src/private/ser.rs",
            41_496,
            "bea364a6199b57c2aa5a96a6bb0530176850dbeaaee908a6307fe610c873c8e3",
        ),
    ),
)


def _safe_relative(value: str, label: str) -> pathlib.PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    if value.startswith("/") or value.endswith("/"):
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    result = pathlib.PurePosixPath(value)
    if result.is_absolute() or ".." in result.parts:
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    return result


def _sha256_stream(
    handle: BinaryIO, byte_limit: int
) -> tuple[str, int]:
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


def _regular_file(path: pathlib.Path, label: str) -> os.stat_result:
    try:
        result = path.lstat()
    except FileNotFoundError as exc:
        raise MaterializationError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(result.st_mode):
        raise MaterializationError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(result.st_mode):
        raise MaterializationError(f"{label} must be a regular file: {path}")
    return result


def _verify_archive(
    path: pathlib.Path, source: CrateSource, limits: CrateLimits
) -> None:
    archive_stat = _regular_file(path, "crate archive")
    if archive_stat.st_size > limits.max_archive_bytes:
        raise MaterializationError(
            f"archive exceeds max_archive_bytes={limits.max_archive_bytes}"
        )
    if archive_stat.st_size != source.archive_bytes:
        raise MaterializationError(
            "archive byte count mismatch: "
            f"expected {source.archive_bytes}, got {archive_stat.st_size}"
        )
    with path.open("rb") as handle:
        digest, size = _sha256_stream(handle, limits.max_archive_bytes)
    if size != source.archive_bytes or digest != source.archive_sha256:
        raise MaterializationError(
            "archive SHA-256 mismatch: "
            f"expected {source.archive_sha256}, got {digest}"
        )


def _verify_catalogue(
    archive: tarfile.TarFile,
    source: CrateSource,
    limits: CrateLimits,
) -> dict[str, tarfile.TarInfo]:
    catalogue = archive.getmembers()
    if len(catalogue) > limits.max_entries:
        raise MaterializationError(
            f"archive has {len(catalogue)} entries; limit is {limits.max_entries}"
        )
    if len(catalogue) != source.catalogue_entries:
        raise MaterializationError(
            "archive catalogue entry count mismatch: "
            f"expected {source.catalogue_entries}, got {len(catalogue)}"
        )

    by_name: dict[str, tarfile.TarInfo] = {}
    file_bytes = 0
    for item in catalogue:
        _safe_relative(item.name, "archive member")
        if item.name in by_name:
            raise MaterializationError(
                f"archive contains duplicate member {item.name!r}"
            )
        by_name[item.name] = item
        if item.isdir():
            continue
        if not item.isreg():
            raise MaterializationError(
                f"archive member has forbidden type: {item.name!r}"
            )
        if item.size < 0:
            raise MaterializationError(
                f"archive member has negative size: {item.name!r}"
            )
        file_bytes += item.size
        if file_bytes > limits.max_uncompressed_bytes:
            raise MaterializationError(
                "archive exceeds max_uncompressed_bytes="
                f"{limits.max_uncompressed_bytes}"
            )

    if file_bytes != source.catalogue_file_bytes:
        raise MaterializationError(
            "archive catalogue byte count mismatch: "
            f"expected {source.catalogue_file_bytes}, got {file_bytes}"
        )

    selected_bytes = 0
    for expected in source.members:
        if not SHA256_RE.fullmatch(expected.sha256) or expected.bytes < 0:
            raise MaterializationError(
                f"invalid pinned member declaration: {expected.name!r}"
            )
        actual = by_name.get(expected.name)
        if actual is None or not actual.isreg():
            raise MaterializationError(
                f"required source member is absent: {expected.name!r}"
            )
        if actual.size != expected.bytes:
            raise MaterializationError(
                f"member size mismatch for {expected.name!r}: "
                f"expected {expected.bytes}, got {actual.size}"
            )
        selected_bytes += actual.size
    if selected_bytes > limits.max_selected_bytes:
        raise MaterializationError(
            f"selected members exceed max_selected_bytes={limits.max_selected_bytes}"
        )
    return by_name


def _member_hash(
    archive: tarfile.TarFile,
    item: tarfile.TarInfo,
    limit: int,
) -> tuple[str, int]:
    handle = archive.extractfile(item)
    if handle is None:
        raise MaterializationError(
            f"unable to read regular archive member {item.name!r}"
        )
    with handle:
        return _sha256_stream(handle, limit)


def _ensure_directory(path: pathlib.Path) -> None:
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
    if path.parent != path:
        _ensure_directory(path.parent)
    path.mkdir()


def _member_relative(source: CrateSource, member: CrateMember) -> pathlib.Path:
    full = _safe_relative(member.name, "pinned member")
    root = _safe_relative(source.archive_root, "archive_root")
    try:
        suffix = full.relative_to(root)
    except ValueError as exc:
        raise MaterializationError(
            f"member is outside archive_root: {member.name!r}"
        ) from exc
    if not suffix.parts:
        raise MaterializationError(
            f"member cannot equal archive_root: {member.name!r}"
        )
    return pathlib.Path("crates", source.archive_root, *suffix.parts)


def _verify_payload(path: pathlib.Path, member: CrateMember) -> None:
    payload_stat = _regular_file(path, "validation payload")
    if payload_stat.st_size != member.bytes:
        raise MaterializationError(
            f"payload byte count mismatch for {path}: "
            f"expected {member.bytes}, got {payload_stat.st_size}"
        )
    with path.open("rb") as handle:
        digest, size = _sha256_stream(handle, member.bytes)
    if size != member.bytes or digest != member.sha256:
        raise MaterializationError(
            f"payload SHA-256 mismatch for {path}: "
            f"expected {member.sha256}, got {digest}"
        )


def _copy_member(
    archive: tarfile.TarFile,
    item: tarfile.TarInfo,
    destination: pathlib.Path,
    member: CrateMember,
) -> None:
    _ensure_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        _verify_payload(destination, member)
        return

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = pathlib.Path(temporary_name)
    try:
        source_handle = archive.extractfile(item)
        if source_handle is None:
            raise MaterializationError(
                f"unable to read regular archive member {item.name!r}"
            )
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "wb") as output, source_handle:
            while chunk := source_handle.read(COPY_CHUNK_BYTES):
                size += len(chunk)
                if size > member.bytes:
                    raise MaterializationError(
                        f"member exceeds pinned size: {member.name!r}"
                    )
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != member.bytes or digest.hexdigest() != member.sha256:
            raise MaterializationError(
                f"member changed during extraction: {member.name!r}"
            )
        temporary.chmod(0o644)
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            _verify_payload(destination, member)
        temporary.unlink()
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def materialize_crate(
    archive_path: pathlib.Path,
    output_root: pathlib.Path,
    source: CrateSource = SERDE_1_0_229,
    limits: CrateLimits = CrateLimits(),
) -> tuple[pathlib.Path, ...]:
    """Verify and materialize all pinned source members without overwriting."""

    if min(
        limits.max_entries,
        limits.max_archive_bytes,
        limits.max_uncompressed_bytes,
        limits.max_selected_bytes,
    ) <= 0:
        raise MaterializationError("all crate safety limits must be positive")
    if len({member.name for member in source.members}) != len(source.members):
        raise MaterializationError("pinned source contains duplicate member names")
    _safe_relative(source.archive_root, "archive_root")
    _verify_archive(archive_path, source, limits)
    _ensure_directory(output_root)

    destinations = tuple(
        output_root / _member_relative(source, member)
        for member in source.members
    )
    with tarfile.open(archive_path, mode="r:gz") as archive:
        catalogue = _verify_catalogue(archive, source, limits)
        # Verify every selected member before creating any payload.
        for member in source.members:
            digest, size = _member_hash(
                archive, catalogue[member.name], member.bytes
            )
            if size != member.bytes or digest != member.sha256:
                raise MaterializationError(
                    f"member SHA-256 mismatch for {member.name!r}: "
                    f"expected {member.sha256}, got {digest}"
                )
        for member, destination in zip(
            source.members, destinations, strict=True
        ):
            _copy_member(
                archive, catalogue[member.name], destination, member
            )

    for member, destination in zip(source.members, destinations, strict=True):
        _verify_payload(destination, member)
    return destinations


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def build_validation_entries(
    repository: pathlib.Path,
    payloads: Sequence[pathlib.Path],
    source: CrateSource = SERDE_1_0_229,
) -> list[DatasetEntry]:
    """Build one real validation row per independently pinned Rust file."""

    if len(payloads) != len(source.members):
        raise MaterializationError(
            f"expected {len(source.members)} payloads, got {len(payloads)}"
        )
    repository = repository.resolve()
    entries: list[DatasetEntry] = []
    for member, payload in zip(source.members, payloads, strict=True):
        try:
            relative = payload.resolve(strict=True).relative_to(repository)
        except ValueError as exc:
            raise MaterializationError(
                "validation payload must be inside the repository"
            ) from exc
        suffix = _member_relative(source, member).relative_to(
            pathlib.Path("crates", source.archive_root)
        )
        entries.append(
            DatasetEntry(
                split="validation",
                dataset_id=f"{source.dataset_prefix}-{_slug(suffix.as_posix())}",
                split_group=source.split_group,
                origin="real",
                primary=True,
                domain=source.domain,
                source=source.source_url,
                license=source.license,
                sha256=member.sha256,
                bytes=member.bytes,
                sealed=False,
                path=relative.as_posix(),
                legacy_observed=False,
            )
        )
    validate_collection(entries)
    return entries


def merge_validation_entries(
    existing: Sequence[DatasetEntry],
    additions: Sequence[DatasetEntry],
) -> list[DatasetEntry]:
    """Merge idempotently, refusing to replace a changed validation row."""

    return upsert_validation_entries(existing, additions)


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


def _write_json(path: pathlib.Path, value: object) -> None:
    _ensure_directory(path.parent)
    if path.is_symlink():
        raise MaterializationError(
            f"generated metadata path must not be a symlink: {path}"
        )
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
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


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "archive",
        type=pathlib.Path,
        help="already downloaded serde-1.0.229.crate archive",
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
    )
    parser.add_argument(
        "--report",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/manifests/validation-serde-report.json"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    repository = args.repository.resolve()
    data_root = _under_repository(repository, args.data_root)
    output = _under_repository(repository, args.output)
    development_path = _under_repository(
        repository, args.development_manifest
    )
    report_path = _under_repository(repository, args.report)
    try:
        payloads = materialize_crate(
            args.archive.absolute(), data_root, SERDE_1_0_229
        )
        additions = build_validation_entries(
            repository, payloads, SERDE_1_0_229
        )
        existing = load_manifest(output) if output.exists() else []
        merged = merge_validation_entries(existing, additions)
        development = load_manifest(development_path)
        validate_collection([*development, *merged])
        write_manifest(output, merged)
        validated = load_manifest(output)
        result = report(validated)
        result["development_validation_merge"] = merge_development_validation(
            development, validated
        )
        result["validation_payload_inspected"] = True
        result["source_archive"] = {
            "bytes": SERDE_1_0_229.archive_bytes,
            "catalogue_entries": SERDE_1_0_229.catalogue_entries,
            "catalogue_file_bytes": SERDE_1_0_229.catalogue_file_bytes,
            "license": SERDE_1_0_229.license,
            "sha256": SERDE_1_0_229.archive_sha256,
            "url": SERDE_1_0_229.source_url,
        }
        result["verified_payloads"] = {
            "bytes": sum(entry.bytes for entry in additions),
            "files": len(additions),
            "sha256_pinned_individually": True,
        }
        _write_json(report_path, result)
    except (
        ManifestError,
        MaterializationError,
        OSError,
        ValueError,
        tarfile.TarError,
    ) as exc:
        print(f"crate validation materialization error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
