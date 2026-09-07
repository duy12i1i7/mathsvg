from __future__ import annotations

import hashlib
import gzip
import json
import os
import pathlib
import stat
import tempfile
import unittest
import zipfile
from dataclasses import replace

from mathsvg.python.datasets.manifest import DatasetEntry
from mathsvg.python.datasets.materialize_research_corpus import (
    DIRECT_SOURCES,
    DOWNLOAD_CAP_BYTES,
    GENERATED_SEED,
    LOCAL_CONTROLS,
    MIXED_SCHEMA,
    STANFORD_BUNNY,
    UAV_TELEMETRY,
    WIKIMEDIA_SQL_DUMP,
    DirectSource,
    GzipSource,
    ZipMember,
    ZipSource,
    local_control_entries,
    materialize_direct_source,
    materialize_gzip_source,
    materialize_zip_source,
    remove_retired_research_rows,
    upsert_split_entries,
)
from mathsvg.python.datasets.materialize_validation import MaterializationError


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _direct_source(
    payload: bytes,
    *,
    output_relative: str = "fixture/payload.bin",
) -> DirectSource:
    return DirectSource(
        cache_name="payload.bin",
        dataset_id="validation-research-fixture",
        split="validation",
        split_group="validation-research-fixture",
        origin="real",
        primary=True,
        domain="fixture-real",
        source_url="https://example.invalid/payload.bin",
        version="fixture-v1",
        license="test-only",
        sha256=_sha256(payload),
        bytes=len(payload),
        output_relative=output_relative,
    )


def _zip_source(
    archive_path: pathlib.Path,
    payloads: dict[str, bytes],
    *,
    selected_name: str,
    selected_output: str = "fixture/selected.json",
    catalogue_entries: int | None = None,
) -> ZipSource:
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        selected = archive.getinfo(selected_name)
    files = [info for info in infos if not info.is_dir()]
    return ZipSource(
        cache_name="fixture.zip",
        split="development",
        split_group="fixture-uav",
        origin="real",
        primary=True,
        source_url="https://example.invalid/fixture.zip",
        version="fixture-v1",
        license="test-only",
        archive_sha256=_sha256(archive_path.read_bytes()),
        archive_bytes=archive_path.stat().st_size,
        catalogue_entries=(
            len(infos) if catalogue_entries is None else catalogue_entries
        ),
        catalogue_files=len(files),
        catalogue_uncompressed_bytes=sum(info.file_size for info in files),
        catalogue_compressed_bytes=sum(info.compress_size for info in files),
        members=(
            ZipMember(
                name=selected_name,
                dataset_id="development-uav-fixture",
                domain="real-uav-flight-telemetry-json",
                sha256=_sha256(payloads[selected_name]),
                bytes=len(payloads[selected_name]),
                compressed_bytes=selected.compress_size,
                output_relative=selected_output,
            ),
        ),
    )


def _gzip_source(archive: bytes, payload: bytes) -> GzipSource:
    return GzipSource(
        cache_name="fixture.sql.gz",
        dataset_id="development-database-dump-fixture",
        split="development",
        split_group="fixture-database-dump",
        origin="real",
        primary=True,
        domain="real-world-database-dump",
        source_url="https://example.invalid/fixture.sql.gz",
        version="fixture-v1",
        license="test-only",
        archive_sha256=_sha256(archive),
        archive_bytes=len(archive),
        output_sha256=_sha256(payload),
        output_bytes=len(payload),
        output_relative="fixture/database.sql",
    )


def _row(dataset_id: str, *, split: str = "validation") -> DatasetEntry:
    return DatasetEntry(
        split=split,
        dataset_id=dataset_id,
        split_group=f"group-{dataset_id}",
        origin="real",
        primary=True,
        domain="fixture",
        source="https://example.invalid/source",
        license="test-only",
        sha256="0" * 64,
        bytes=1,
        sealed=False,
        path=f"fixtures/{dataset_id}.bin",
        legacy_observed=False,
    )


def _dicom_fixture(transfer_syntax: str) -> bytes:
    def ui(group: int, element: int, value: str) -> bytes:
        encoded = value.encode("ascii") + b"\x00"
        if len(encoded) % 2:
            encoded += b"\x00"
        return (
            group.to_bytes(2, "little")
            + element.to_bytes(2, "little")
            + b"UI"
            + len(encoded).to_bytes(2, "little")
            + encoded
        )

    return (
        b"\x00" * 128
        + b"DICM"
        + ui(0x0002, 0x0002, "1.2.840.10008.5.1.4.1.1.2")
        + ui(0x0002, 0x0010, transfer_syntax)
        + b"\x08\x00\x08\x00"
    )


class ResearchCorpusMaterializerTests(unittest.TestCase):
    def test_direct_copy_is_exact_idempotent_and_real_primary(self) -> None:
        payload = b"upstream payload\n"
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            source_path = repository / "sources/payload.bin"
            source_path.parent.mkdir()
            source_path.write_bytes(payload)
            output_root = repository / "datasets/data/research"
            source = _direct_source(payload)

            first_path, first_row = materialize_direct_source(
                source_path, output_root, repository, source
            )
            first_mtime = first_path.stat().st_mtime_ns
            second_path, second_row = materialize_direct_source(
                source_path, output_root, repository, source
            )

            self.assertEqual(first_path.read_bytes(), payload)
            self.assertEqual(first_path, second_path)
            self.assertEqual(first_mtime, second_path.stat().st_mtime_ns)
            self.assertEqual(first_row, second_row)
            self.assertEqual(first_row.origin, "real")
            self.assertTrue(first_row.primary)
            self.assertEqual(first_row.split, "validation")

    def test_direct_checksum_mismatch_creates_no_output(self) -> None:
        payload = b"upstream payload\n"
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            source_path = repository / "payload.bin"
            source_path.write_bytes(payload)
            source = replace(_direct_source(payload), sha256="0" * 64)
            output_root = repository / "output"

            with self.assertRaisesRegex(
                MaterializationError, "source payload SHA-256 mismatch"
            ):
                materialize_direct_source(
                    source_path, output_root, repository, source
                )
            self.assertFalse(output_root.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_input_symlink_is_rejected(self) -> None:
        payload = b"upstream payload\n"
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            target = repository / "target.bin"
            target.write_bytes(payload)
            source_path = repository / "payload.bin"
            source_path.symlink_to(target)

            with self.assertRaisesRegex(
                MaterializationError, "must not be a symlink"
            ):
                materialize_direct_source(
                    source_path,
                    repository / "output",
                    repository,
                    _direct_source(payload),
                )

    def test_output_traversal_is_rejected(self) -> None:
        payload = b"upstream payload\n"
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            source_path = repository / "payload.bin"
            source_path.write_bytes(payload)
            source = _direct_source(
                payload, output_relative="../escaped.bin"
            )

            with self.assertRaisesRegex(
                MaterializationError, "safe POSIX relative path"
            ):
                materialize_direct_source(
                    source_path, repository / "output", repository, source
                )
            self.assertFalse((repository / "escaped.bin").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_destination_parent_symlink_is_rejected(self) -> None:
        payload = b"upstream payload\n"
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            source_path = repository / "payload.bin"
            source_path.write_bytes(payload)
            output_root = repository / "output"
            validation = output_root / "validation"
            outside = repository / "outside"
            validation.mkdir(parents=True)
            outside.mkdir()
            (validation / "fixture").symlink_to(
                outside, target_is_directory=True
            )

            with self.assertRaisesRegex(
                MaterializationError, "must not be a symlink"
            ):
                materialize_direct_source(
                    source_path,
                    output_root,
                    repository,
                    _direct_source(payload),
                )
            self.assertFalse((outside / "payload.bin").exists())

    def test_existing_changed_payload_is_never_overwritten(self) -> None:
        payload = b"canonical"
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            source_path = repository / "payload.bin"
            source_path.write_bytes(payload)
            output_root = repository / "output"
            existing = output_root / "validation/fixture/payload.bin"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"user-data")

            with self.assertRaisesRegex(
                MaterializationError,
                "existing payload (byte count|SHA-256) mismatch",
            ):
                materialize_direct_source(
                    source_path,
                    output_root,
                    repository,
                    _direct_source(payload),
                )
            self.assertEqual(existing.read_bytes(), b"user-data")

    def test_dicom_profile_requires_magic_ct_sop_and_uncompressed_uid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            source_path = repository / "ct.dcm"
            valid = _dicom_fixture("1.2.840.10008.1.2")
            source_path.write_bytes(valid)
            source = replace(
                _direct_source(valid, output_relative="dicom/ct.dcm"),
                dataset_id="validation-idc-cptac-sar-ct-dicom",
                content_profile="dicom-ct-implicit-vr-little-endian",
            )

            path, row = materialize_direct_source(
                source_path, repository / "output", repository, source
            )
            self.assertEqual(path.read_bytes(), valid)
            self.assertEqual(row.sha256, _sha256(valid))

            compressed = _dicom_fixture("1.2.840.10008.1.2.4.90")
            source_path.write_bytes(compressed)
            compressed_source = replace(
                source,
                sha256=_sha256(compressed),
                bytes=len(compressed),
                output_relative="dicom/compressed.dcm",
            )
            with self.assertRaisesRegex(
                MaterializationError, "expected uncompressed"
            ):
                materialize_direct_source(
                    source_path,
                    repository / "other-output",
                    repository,
                    compressed_source,
                )

    def test_zip_catalogue_and_selected_member_are_fully_pinned(self) -> None:
        payloads = {
            "dataset/drone1/message.json": b'{"altitude":30}\n',
            "dataset/drone1/other.json": b'{"altitude":31}\n',
        }
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            archive_path = repository / "fixture.zip"
            with zipfile.ZipFile(
                archive_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr("dataset/", b"")
                archive.writestr("dataset/drone1/", b"")
                for name, payload in payloads.items():
                    archive.writestr(name, payload)
            source = _zip_source(
                archive_path,
                payloads,
                selected_name="dataset/drone1/message.json",
            )

            paths, rows = materialize_zip_source(
                archive_path,
                repository / "output",
                repository,
                source,
            )
            first_mtime = paths[0].stat().st_mtime_ns
            second_paths, second_rows = materialize_zip_source(
                archive_path,
                repository / "output",
                repository,
                source,
            )

            self.assertEqual(paths[0].read_bytes(), payloads[source.members[0].name])
            self.assertEqual(first_mtime, second_paths[0].stat().st_mtime_ns)
            self.assertEqual(rows, second_rows)
            self.assertTrue(rows[0].primary)
            self.assertEqual(rows[0].split, "development")

            drifted = replace(
                source, catalogue_entries=source.catalogue_entries + 1
            )
            with self.assertRaisesRegex(
                MaterializationError, "catalogue entry count mismatch"
            ):
                materialize_zip_source(
                    archive_path,
                    repository / "other-output",
                    repository,
                    drifted,
                )

    def test_gzip_output_is_bounded_hashed_and_idempotent(self) -> None:
        payload = b"CREATE TABLE fixture (value INTEGER);\n" * 32
        archive = gzip.compress(payload, mtime=0)
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            archive_path = repository / "fixture.sql.gz"
            archive_path.write_bytes(archive)
            source = _gzip_source(archive, payload)

            path, row = materialize_gzip_source(
                archive_path,
                repository / "output",
                repository,
                source,
            )
            first_mtime = path.stat().st_mtime_ns
            second_path, second_row = materialize_gzip_source(
                archive_path,
                repository / "output",
                repository,
                source,
            )

            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(first_mtime, second_path.stat().st_mtime_ns)
            self.assertEqual(row, second_row)
            self.assertEqual(row.sha256, _sha256(payload))
            self.assertEqual(row.bytes, len(payload))

            changed = replace(
                source,
                output_sha256="f" * 64,
                output_relative="fixture/changed.sql",
            )
            with self.assertRaisesRegex(
                MaterializationError, "copied payload SHA-256 mismatch"
            ):
                materialize_gzip_source(
                    archive_path,
                    repository / "other-output",
                    repository,
                    changed,
                )
            self.assertFalse(
                (repository / "other-output/development/fixture/changed.sql").exists()
            )

    def test_zip_traversal_and_symlink_members_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            traversal = repository / "traversal.zip"
            traversal_payloads = {
                "dataset/message.json": b"safe",
                "../escaped": b"escape",
            }
            with zipfile.ZipFile(traversal, "w") as archive:
                for name, payload in traversal_payloads.items():
                    archive.writestr(name, payload)
            traversal_source = _zip_source(
                traversal,
                traversal_payloads,
                selected_name="dataset/message.json",
            )
            with self.assertRaisesRegex(
                MaterializationError, "safe POSIX relative path"
            ):
                materialize_zip_source(
                    traversal,
                    repository / "output",
                    repository,
                    traversal_source,
                )
            self.assertFalse((repository / "escaped").exists())

            symlink = repository / "symlink.zip"
            payloads = {"dataset/message.json": b"safe"}
            link = zipfile.ZipInfo("dataset/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(symlink, "w") as archive:
                archive.writestr("dataset/message.json", b"safe")
                archive.writestr(link, b"../../secret")
            symlink_source = _zip_source(
                symlink,
                payloads,
                selected_name="dataset/message.json",
            )
            with self.assertRaisesRegex(
                MaterializationError, "must not be a symlink"
            ):
                materialize_zip_source(
                    symlink,
                    repository / "other-output",
                    repository,
                    symlink_source,
                )

    def test_generated_media_remain_non_primary_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            root = repository / "datasets/generated/mixed"
            entries = []
            for index, selection in enumerate(LOCAL_CONTROLS):
                payload = f"control-{index}".encode("ascii")
                path = root / selection.manifest_relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                entries.append(
                    {
                        "path": selection.manifest_relative,
                        "size_bytes": len(payload),
                        "sha256": _sha256(payload),
                    }
                )
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": MIXED_SCHEMA,
                        "seed": GENERATED_SEED,
                        "generator": "fixture-generator",
                        "license": "CC0-1.0",
                        "entries": entries,
                    }
                ),
                encoding="utf-8",
            )

            rows = local_control_entries(repository)

            self.assertEqual(len(rows), len(LOCAL_CONTROLS))
            self.assertTrue(all(row.origin == "control" for row in rows))
            self.assertTrue(all(not row.primary for row in rows))
            self.assertTrue(all(row.split == "development" for row in rows))
            self.assertEqual(len({row.split_group for row in rows}), 1)

    def test_split_merge_is_idempotent_and_refuses_changed_row(self) -> None:
        existing = [_row("existing")]
        addition = _row("addition")

        first = upsert_split_entries(existing, [addition], "validation")
        second = upsert_split_entries(first, [addition], "validation")

        self.assertEqual(first, second)
        with self.assertRaisesRegex(
            MaterializationError, "refusing to replace changed"
        ):
            upsert_split_entries(
                second,
                [replace(addition, sha256="f" * 64)],
                "validation",
            )

    def test_frozen_production_labels_do_not_promote_codec_fixtures(self) -> None:
        controls = [source for source in DIRECT_SOURCES if not source.primary]
        real = [source for source in DIRECT_SOURCES if source.primary]

        self.assertEqual({source.origin for source in controls}, {"control"})
        self.assertTrue(all(source.origin == "real" for source in real))
        self.assertEqual(UAV_TELEMETRY.origin, "real")
        self.assertTrue(UAV_TELEMETRY.primary)
        self.assertEqual(
            STANFORD_BUNNY.members[0].name, "bunny/data/bun000.ply"
        )
        self.assertEqual(STANFORD_BUNNY.catalogue_entries, 21)
        dicom = next(
            source
            for source in real
            if source.dataset_id
            == "validation-idc-cptac-sar-ct-dicom"
        )
        self.assertEqual(
            dicom.content_profile,
            "dicom-ct-implicit-vr-little-endian",
        )
        self.assertIn("1.2.840.10008.1.2", dicom.version)
        self.assertIn("CT Image Storage", dicom.version)
        container = next(
            source
            for source in real
            if source.dataset_id
            == "validation-docker-official-hello-world-amd64-layer"
        )
        self.assertIn(container.sha256, container.source_url)
        self.assertIn("linux/amd64 manifest", container.version)
        gpkg = next(
            source
            for source in real
            if source.dataset_id
            == "validation-geoserver-2-28-2-natural-earth-gpkg"
        )
        self.assertEqual(
            gpkg.content_profile, "geopackage-natural-earth-2.28.2"
        )
        git_bundle = next(
            source
            for source in real
            if source.dataset_id
            == "validation-gnu-gnulib-20250729-git-bundle"
        )
        self.assertIn("336 refs", git_bundle.version)
        self.assertEqual(WIKIMEDIA_SQL_DUMP.output_bytes, 4_455_489)
        source_bytes = (
            sum(source.bytes for source in DIRECT_SOURCES)
            + WIKIMEDIA_SQL_DUMP.archive_bytes
            + UAV_TELEMETRY.archive_bytes
            + STANFORD_BUNNY.archive_bytes
        )
        self.assertLessEqual(source_bytes, DOWNLOAD_CAP_BYTES)
        self.assertNotIn("pydicom", " ".join(source.dataset_id for source in real))
        self.assertNotIn("px4", " ".join(source.dataset_id for source in real))

    def test_only_exact_superseded_row_can_be_retired(self) -> None:
        old = replace(
            _row("validation-idc-lidc-idri-0001-qiicr-sr-dicom"),
            sha256=(
                "974860e871549d6d57635f45a99d02dc"
                "2e3e162d0193e87b9bce2477910a7f63"
            ),
        )
        keep = _row("keep")

        rows, count = remove_retired_research_rows([old, keep])

        self.assertEqual(rows, [keep])
        self.assertEqual(count, 1)
        with self.assertRaisesRegex(
            MaterializationError, "refusing to retire changed"
        ):
            remove_retired_research_rows(
                [replace(old, sha256="f" * 64), keep]
            )


if __name__ == "__main__":
    unittest.main()
