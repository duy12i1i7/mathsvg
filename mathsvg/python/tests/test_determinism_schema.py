from __future__ import annotations

import pathlib
import tempfile
import unittest

from mathsvg.python.determinism.schema import (
    DeterminismError,
    DeterminismRecord,
    read_csv,
    write_csv,
)


def record(**overrides: object) -> DeterminismRecord:
    values: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": "determinism-smoke",
        "source_commit": "0" * 40,
        "source_dirty": True,
        "source_status_sha256": "1" * 64,
        "machine_id": "test-machine",
        "architecture": "x86_64",
        "operating_system": "test-os",
        "logical_cpus": 8,
        "compiler_version": "rustc test",
        "compiler_binary_sha256": "2" * 64,
        "build": "debug",
        "binary_path": "/tmp/mathsvg",
        "binary_sha256": "3" * 64,
        "runner_sha256": "4" * 64,
        "config_path": "/tmp/profile.toml",
        "config_sha256": "5" * 64,
        "profile": "balanced",
        "split": "development",
        "input_path": "/tmp/input.bin",
        "input_bytes": 3,
        "input_sha256": "6" * 64,
        "planned_repetitions": 100,
        "repetition": 0,
        "thread_mode": "1",
        "resolved_threads": 1,
        "backend": "scalar",
        "required_axis": True,
        "threads_capability": "available: --threads; all advertised",
        "backend_capability": "available: auto,scalar,simd",
        "capability_probe_sha256": "7" * 64,
        "status": "ok",
        "reason": "",
        "compress_exit_code": 0,
        "decompress_exit_code": 0,
        "compression_ns": 10,
        "decompression_ns": 5,
        "archive_bytes": 20,
        "archive_sha256": "8" * 64,
        "reference_archive_sha256": "8" * 64,
        "archive_matches_reference": True,
        "restored_sha256": "6" * 64,
        "roundtrip_ok": True,
        "compress_command": '["/tmp/mathsvg","compress"]',
        "decompress_command": '["/tmp/mathsvg","decompress"]',
    }
    values.update(overrides)
    return DeterminismRecord(**values)  # type: ignore[arg-type]


class DeterminismSchemaTests(unittest.TestCase):
    def test_canonical_csv_round_trip_and_sorting(self) -> None:
        first_record = record(repetition=0)
        second_record = record(repetition=1)
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            first = root / "first.csv"
            second = root / "second.csv"
            write_csv(first, [second_record, first_record])
            write_csv(second, [first_record, second_record])
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(
                read_csv(first),
                [first_record, second_record],
            )

    def test_unavailable_row_cannot_claim_a_pass(self) -> None:
        unavailable = record(
            status="unavailable",
            reason="compress help has no --threads",
            resolved_threads=None,
            compress_exit_code=None,
            decompress_exit_code=None,
            compression_ns=None,
            decompression_ns=None,
            archive_bytes=None,
            archive_sha256="",
            reference_archive_sha256="",
            archive_matches_reference=False,
            restored_sha256="",
            roundtrip_ok=False,
            binary_sha256="",
            capability_probe_sha256="",
            compiler_binary_sha256="",
            compiler_version="",
        )
        unavailable.validate()
        with self.assertRaisesRegex(
            DeterminismError, "cannot claim verification"
        ):
            record(
                status="unavailable",
                reason="unsupported",
                compress_exit_code=None,
                decompress_exit_code=None,
                compression_ns=None,
                decompression_ns=None,
                archive_bytes=None,
                archive_sha256="",
                reference_archive_sha256="",
                restored_sha256="",
                roundtrip_ok=True,
                archive_matches_reference=True,
            ).validate()

    def test_archive_mismatch_is_a_retained_failure(self) -> None:
        mismatch = record(
            status="failed",
            reason="archive SHA-256 differs from the matrix reference",
            archive_sha256="9" * 64,
            reference_archive_sha256="8" * 64,
            archive_matches_reference=False,
            roundtrip_ok=True,
        )
        mismatch.validate()

    def test_rejects_holdout_and_duplicate_coordinates(self) -> None:
        with self.assertRaisesRegex(
            DeterminismError, "refuses holdout"
        ):
            record(split="holdout").validate()
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "duplicate.csv"
            with self.assertRaisesRegex(
                DeterminismError, "duplicate"
            ):
                write_csv(path, [record(), record()])


if __name__ == "__main__":
    unittest.main()
