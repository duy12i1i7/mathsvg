from __future__ import annotations

import hashlib
import io
import pathlib
import tarfile
import tempfile
import unittest
from dataclasses import replace

from mathsvg.python.datasets.manifest import DatasetEntry
from mathsvg.python.datasets.materialize_crate_validation import (
    CrateLimits,
    CrateMember,
    CrateSource,
    build_validation_entries,
    materialize_crate,
    merge_validation_entries,
)
from mathsvg.python.datasets.materialize_validation import MaterializationError


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _archive_sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _add_file(
    archive: tarfile.TarFile,
    name: str,
    payload: bytes,
    *,
    kind: bytes = tarfile.REGTYPE,
    linkname: str = "",
) -> None:
    item = tarfile.TarInfo(name)
    item.type = kind
    item.linkname = linkname
    item.mode = 0o644
    item.size = len(payload) if kind == tarfile.REGTYPE else 0
    archive.addfile(item, io.BytesIO(payload) if item.size else None)


def _fixture_source(
    archive_path: pathlib.Path,
    payloads: dict[str, bytes],
    *,
    catalogue_entries: int | None = None,
    catalogue_file_bytes: int | None = None,
) -> CrateSource:
    members = tuple(
        CrateMember(name, len(payload), _sha256(payload))
        for name, payload in payloads.items()
    )
    with tarfile.open(archive_path, "r:gz") as archive:
        catalogue = archive.getmembers()
        file_bytes = sum(item.size for item in catalogue if item.isreg())
    return CrateSource(
        dataset_prefix="validation-fixture-crate",
        split_group="fixture-crate-1-0-0",
        domain="real-world-rust-crate-source",
        source_url="https://example.invalid/fixture-1.0.0.crate",
        license="MIT",
        archive_sha256=_archive_sha256(archive_path),
        archive_bytes=archive_path.stat().st_size,
        archive_root="fixture-1.0.0",
        catalogue_entries=(
            len(catalogue)
            if catalogue_entries is None
            else catalogue_entries
        ),
        catalogue_file_bytes=(
            file_bytes
            if catalogue_file_bytes is None
            else catalogue_file_bytes
        ),
        members=members,
    )


def _manifest_entry(dataset_id: str = "existing") -> DatasetEntry:
    return DatasetEntry(
        split="validation",
        dataset_id=dataset_id,
        split_group=dataset_id,
        origin="real",
        primary=True,
        domain="structured-sensor-csv",
        source="https://example.invalid/source",
        license="test-only",
        sha256="0" * 64,
        bytes=1,
        sealed=False,
        path=f"datasets/data/validation/{dataset_id}.bin",
        legacy_observed=False,
    )


class CrateValidationMaterializerTests(unittest.TestCase):
    def test_exact_materialization_is_idempotent_and_builds_real_rows(self) -> None:
        payloads = {
            "fixture-1.0.0/src/lib.rs": b"pub fn answer() -> u8 { 42 }\n",
            "fixture-1.0.0/src/value.rs": b"pub const VALUE: u8 = 42;\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            archive_path = repository / "fixture.crate"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name, payload in payloads.items():
                    _add_file(archive, name, payload)
            source = _fixture_source(archive_path, payloads)
            output_root = repository / "datasets/data/validation"

            first = materialize_crate(
                archive_path,
                output_root,
                source,
                CrateLimits(max_archive_bytes=4096),
            )
            first_mtimes = [path.stat().st_mtime_ns for path in first]
            second = materialize_crate(
                archive_path,
                output_root,
                source,
                CrateLimits(max_archive_bytes=4096),
            )
            entries = build_validation_entries(repository, second, source)

            self.assertEqual(first, second)
            self.assertEqual(
                [path.stat().st_mtime_ns for path in second], first_mtimes
            )
            self.assertEqual(len(entries), 2)
            self.assertTrue(all(entry.origin == "real" for entry in entries))
            self.assertTrue(all(entry.primary for entry in entries))
            self.assertEqual({entry.split_group for entry in entries}, {
                source.split_group
            })

    def test_archive_hash_mismatch_is_rejected_before_output(self) -> None:
        payloads = {"fixture-1.0.0/src/lib.rs": b"safe\n"}
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive_path = root / "fixture.crate"
            with tarfile.open(archive_path, "w:gz") as archive:
                _add_file(
                    archive,
                    "fixture-1.0.0/src/lib.rs",
                    payloads["fixture-1.0.0/src/lib.rs"],
                )
            source = replace(
                _fixture_source(archive_path, payloads),
                archive_sha256="0" * 64,
            )
            output_root = root / "validation"

            with self.assertRaisesRegex(
                MaterializationError, "archive SHA-256 mismatch"
            ):
                materialize_crate(
                    archive_path,
                    output_root,
                    source,
                    CrateLimits(max_archive_bytes=4096),
                )
            self.assertFalse(output_root.exists())

    def test_path_traversal_member_is_rejected(self) -> None:
        payloads = {"fixture-1.0.0/src/lib.rs": b"safe\n"}
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive_path = root / "fixture.crate"
            with tarfile.open(archive_path, "w:gz") as archive:
                _add_file(
                    archive,
                    "fixture-1.0.0/src/lib.rs",
                    payloads["fixture-1.0.0/src/lib.rs"],
                )
                _add_file(archive, "../escape.rs", b"escape\n")
            source = _fixture_source(archive_path, payloads)

            with self.assertRaisesRegex(
                MaterializationError, "safe POSIX relative path"
            ):
                materialize_crate(
                    archive_path,
                    root / "validation",
                    source,
                    CrateLimits(max_archive_bytes=4096),
                )
            self.assertFalse((root / "escape.rs").exists())

    def test_symlink_member_is_rejected_even_when_not_selected(self) -> None:
        payloads = {"fixture-1.0.0/src/lib.rs": b"safe\n"}
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive_path = root / "fixture.crate"
            with tarfile.open(archive_path, "w:gz") as archive:
                _add_file(
                    archive,
                    "fixture-1.0.0/src/lib.rs",
                    payloads["fixture-1.0.0/src/lib.rs"],
                )
                _add_file(
                    archive,
                    "fixture-1.0.0/src/link.rs",
                    b"",
                    kind=tarfile.SYMTYPE,
                    linkname="../../secret",
                )
            source = _fixture_source(archive_path, payloads)

            with self.assertRaisesRegex(
                MaterializationError, "forbidden type"
            ):
                materialize_crate(
                    archive_path,
                    root / "validation",
                    source,
                    CrateLimits(max_archive_bytes=4096),
                )

    def test_existing_changed_payload_is_never_overwritten(self) -> None:
        payloads = {"fixture-1.0.0/src/lib.rs": b"safe\n"}
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive_path = root / "fixture.crate"
            with tarfile.open(archive_path, "w:gz") as archive:
                _add_file(
                    archive,
                    "fixture-1.0.0/src/lib.rs",
                    payloads["fixture-1.0.0/src/lib.rs"],
                )
            source = _fixture_source(archive_path, payloads)
            destination = (
                root
                / "validation/crates/fixture-1.0.0/src/lib.rs"
            )
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"changed\n")

            with self.assertRaisesRegex(
                MaterializationError, "payload byte count mismatch"
            ):
                materialize_crate(
                    archive_path,
                    root / "validation",
                    source,
                    CrateLimits(max_archive_bytes=4096),
                )
            self.assertEqual(destination.read_bytes(), b"changed\n")

    def test_validation_merge_is_idempotent_and_refuses_replacement(self) -> None:
        existing = _manifest_entry()
        addition = _manifest_entry("crate-source")
        first = merge_validation_entries([existing], [addition])
        second = merge_validation_entries(first, [addition])
        self.assertEqual(first, second)

        changed = replace(addition, sha256="1" * 64)
        with self.assertRaisesRegex(
            MaterializationError, "refusing to replace"
        ):
            merge_validation_entries(first, [changed])


if __name__ == "__main__":
    unittest.main()
