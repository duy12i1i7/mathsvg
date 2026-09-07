from __future__ import annotations

import hashlib
import os
import pathlib
import stat
import tempfile
import unittest
import zipfile
from dataclasses import replace

from mathsvg.python.datasets.materialize_validation import (
    MaterializationError,
    ValidationLimits,
    ValidationSource,
    build_validation_entry,
    materialize_archive,
    merge_development_validation,
    upsert_validation_entries,
    write_manifest,
)


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_for(
    archive: pathlib.Path,
    payload: bytes,
    *,
    member_name: str = "payload.csv",
    output_relative: str = "fixture/payload.csv",
) -> ValidationSource:
    with zipfile.ZipFile(archive) as handle:
        info = handle.getinfo(member_name)
    return ValidationSource(
        dataset_id="validation-fixture",
        split_group="validation-fixture-family",
        domain="structured-sensor-csv",
        source_url="https://example.invalid/fixture.zip",
        license="test-only",
        archive_sha256=sha256(archive),
        archive_bytes=archive.stat().st_size,
        member_name=member_name,
        member_compressed_bytes=info.compress_size,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_bytes=len(payload),
        output_relative=output_relative,
    )


class ValidationMaterializerTests(unittest.TestCase):
    def test_materialization_is_exact_and_idempotent(self) -> None:
        payload = b"sensor,value\n1,42\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / "source.zip"
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED
            ) as handle:
                handle.writestr("payload.csv", payload)
            source = source_for(archive, payload)
            output_root = root / "validation"

            first = materialize_archive(archive, output_root, source)
            first_mtime = first.stat().st_mtime_ns
            second = materialize_archive(archive, output_root, source)

            self.assertEqual(first, second)
            self.assertEqual(first.read_bytes(), payload)
            self.assertEqual(second.stat().st_mtime_ns, first_mtime)

    def test_archive_hash_mismatch_is_rejected_before_extraction(self) -> None:
        payload = b"safe"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("payload.csv", payload)
            source = source_for(archive, payload)
            source = replace(source, archive_sha256="0" * 64)
            output_root = root / "validation"

            with self.assertRaisesRegex(
                MaterializationError, "archive SHA-256 mismatch"
            ):
                materialize_archive(archive, output_root, source)
            self.assertFalse(output_root.exists())

    def test_path_traversal_member_is_rejected(self) -> None:
        payload = b"escape"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape.csv", payload)
            source = source_for(
                archive, payload, member_name="../escape.csv"
            )

            with self.assertRaisesRegex(
                MaterializationError, "safe POSIX relative path"
            ):
                materialize_archive(archive, root / "validation", source)
            self.assertFalse((root / "escape.csv").exists())

    def test_symlink_member_is_rejected(self) -> None:
        payload = b"target.csv"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / "source.zip"
            info = zipfile.ZipInfo("payload.csv")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr(info, payload)
            source = source_for(archive, payload)

            with self.assertRaisesRegex(
                MaterializationError, "must not be a symlink"
            ):
                materialize_archive(archive, root / "validation", source)

    def test_uncompressed_limit_is_enforced_before_extraction(self) -> None:
        payload = b"x" * 1024
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / "source.zip"
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED
            ) as handle:
                handle.writestr("payload.csv", payload)
            source = source_for(archive, payload)
            limits = ValidationLimits(
                max_entries=1,
                max_archive_bytes=4096,
                max_compressed_bytes=4096,
                max_uncompressed_bytes=100,
            )

            with self.assertRaisesRegex(
                MaterializationError, "max_uncompressed_bytes"
            ):
                materialize_archive(
                    archive, root / "validation", source, limits
                )

    def test_existing_wrong_payload_is_never_overwritten(self) -> None:
        payload = b"canonical"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("payload.csv", payload)
            source = source_for(archive, payload)
            destination = root / "validation" / "fixture" / "payload.csv"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"user-data")

            with self.assertRaisesRegex(
                MaterializationError, "payload (byte count|SHA-256) mismatch"
            ):
                materialize_archive(archive, root / "validation", source)
            self.assertEqual(destination.read_bytes(), b"user-data")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_destination_parent_symlink_is_rejected(self) -> None:
        payload = b"canonical"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("payload.csv", payload)
            source = source_for(archive, payload)
            output_root = root / "validation"
            outside = root / "outside"
            output_root.mkdir()
            outside.mkdir()
            (output_root / "fixture").symlink_to(
                outside, target_is_directory=True
            )

            with self.assertRaisesRegex(
                MaterializationError, "must not be a symlink"
            ):
                materialize_archive(archive, output_root, source)
            self.assertFalse((outside / "payload.csv").exists())

    def test_manifest_entry_is_validation_real_primary_only(self) -> None:
        payload = b"sensor"
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            archive = repository / "source.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("payload.csv", payload)
            source = source_for(archive, payload)
            materialized = materialize_archive(
                archive, repository / "datasets" / "validation", source
            )

            entry = build_validation_entry(
                repository, materialized, source
            )

            self.assertEqual(entry.split, "validation")
            self.assertEqual(entry.origin, "real")
            self.assertTrue(entry.primary)
            self.assertFalse(entry.legacy_observed)
            self.assertFalse(entry.sealed)
            entry.validate(2)

            first = repository / "first.csv"
            second = repository / "second.csv"
            write_manifest(first, [entry])
            write_manifest(second, [entry])
            self.assertEqual(first.read_bytes(), second.read_bytes())

            development = replace(
                entry,
                split="development",
                dataset_id="development-fixture",
                split_group="development-fixture-family",
                path="datasets/development/fixture.csv",
            )
            merged = merge_development_validation(
                [development], [entry]
            )
            self.assertEqual(merged["development_rows"], 1)
            self.assertEqual(merged["validation_rows"], 1)

    def test_merge_rejects_cross_split_leakage(self) -> None:
        payload = b"sensor"
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            archive = repository / "source.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("payload.csv", payload)
            source = source_for(archive, payload)
            materialized = materialize_archive(
                archive, repository / "datasets" / "validation", source
            )
            validation = build_validation_entry(
                repository, materialized, source
            )
            development = replace(
                validation,
                split="development",
                dataset_id="development-fixture",
                path="datasets/development/fixture.csv",
            )

            with self.assertRaisesRegex(
                ValueError, "split leakage"
            ):
                merge_development_validation(
                    [development], [validation]
                )

    def test_validation_upsert_preserves_other_sources(self) -> None:
        payload = b"sensor"
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            archive = repository / "source.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("payload.csv", payload)
            source = source_for(archive, payload)
            materialized = materialize_archive(
                archive, repository / "datasets" / "validation", source
            )
            uci = build_validation_entry(repository, materialized, source)
            crate = replace(
                uci,
                dataset_id="validation-crate-source",
                split_group="validation-crate-family",
                domain="real-world-rust-crate-source",
                source="https://example.invalid/source.crate",
                path="datasets/validation/crate/src/lib.rs",
            )

            first = upsert_validation_entries([crate], [uci])
            second = upsert_validation_entries(first, [uci])
            self.assertEqual(first, second)
            self.assertEqual(
                {entry.dataset_id for entry in second},
                {"validation-crate-source", "validation-fixture"},
            )

            with self.assertRaisesRegex(
                MaterializationError, "refusing to replace"
            ):
                upsert_validation_entries(
                    second, [replace(uci, sha256="0" * 64)]
                )


if __name__ == "__main__":
    unittest.main()
