from __future__ import annotations

import pathlib
import tempfile
import unittest

from mathsvg.python.benchmarks.schema import (
    TrialError,
    TrialRecord,
    append_jsonl,
    read_jsonl,
    write_jsonl,
)


def trial(**overrides: object) -> TrialRecord:
    values: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": "dev-1",
        "machine_id": "local-x86",
        "architecture": "x86_64",
        "source_commit": "0" * 40,
        "config_sha256": "1" * 64,
        "dataset_manifest_sha256": "2" * 64,
        "randomization_seed": 42,
        "schedule_block": 0,
        "schedule_order": 0,
        "repetition": 0,
        "warmup": False,
        "split": "development",
        "dataset_id": "one",
        "input_path": "development/one.bin",
        "input_sha256": "3" * 64,
        "original_bytes": 100,
        "codec": "mathsvg",
        "codec_config": "balanced-v1",
        "profile": "balanced",
        "threads": 1,
        "native_mathsvg": True,
        "codec_executable_sha256": "6" * 64,
        "measurement_method": "gnu-time-v1",
        "measurement_tool_sha256": "7" * 64,
        "status": "ok",
        "error": "",
        "archive_bytes": 80,
        "archive_sha256": "4" * 64,
        "restored_sha256": "3" * 64,
        "roundtrip_ok": True,
        "deterministic_archive": True,
        "compression_wall_ns": 100,
        "compression_cpu_ns": 90,
        "decompression_wall_ns": 50,
        "decompression_cpu_ns": 45,
        "peak_rss_bytes": 1000,
        "compression_peak_rss_bytes": 1000,
        "decompression_peak_rss_bytes": 800,
        "energy_uj": None,
        "literal_only_archive_bytes": 356,
        "pre_entropy_archive_bytes": 90,
        "entropy_saved_bytes": 10,
        "entropy_penalty_bytes": 0,
        "procedural_gain_bytes": 276,
        "procedural_penalty_bytes": 0,
        "container_overhead_bytes": 5,
        "function_graph_bytes": 5,
        "coordinate_bytes": 5,
        "shared_definition_bytes": 0,
        "reference_bytes": 0,
        "parameter_bytes": 5,
        "residual_layer_bytes": 5,
        "literal_leaf_bytes": 50,
        "entropy_metadata_bytes": 5,
        "index_bytes": None,
        "node_count": 4,
        "shared_node_count": 0,
        "residual_depth_sum": 0,
        "residual_root_count": 1,
        "function_reconstructed_bytes": 50,
        "literal_reconstructed_bytes": 50,
        "coordinate_saved_bytes": 0,
        "dag_saved_bytes": 0,
        "recursive_residual_saved_bytes": 0,
        "symbolic_saved_bytes": 0,
        "search_ns": 30,
    }
    values.update(overrides)
    return TrialRecord(**values)  # type: ignore[arg-type]


class TrialSchemaTests(unittest.TestCase):
    def test_valid_native_row_and_deterministic_json(self) -> None:
        record = trial()
        record.validate()
        with tempfile.TemporaryDirectory() as temp:
            first = pathlib.Path(temp) / "first.jsonl"
            second = pathlib.Path(temp) / "second.jsonl"
            write_jsonl(first, [record])
            write_jsonl(second, [record])
            self.assertEqual(first.read_bytes(), second.read_bytes())
            third = pathlib.Path(temp) / "third.jsonl"
            append_jsonl(third, record)
            self.assertEqual(read_jsonl(third), [record])

    def test_failure_is_retained(self) -> None:
        record = trial(
            status="timeout",
            error="wall timeout",
            archive_bytes=None,
            archive_sha256="",
            restored_sha256="",
            roundtrip_ok=False,
            compression_wall_ns=None,
            compression_cpu_ns=None,
            decompression_wall_ns=None,
            decompression_cpu_ns=None,
            peak_rss_bytes=None,
            compression_peak_rss_bytes=None,
            decompression_peak_rss_bytes=None,
            function_graph_bytes=None,
            coordinate_bytes=None,
            shared_definition_bytes=None,
            reference_bytes=None,
            parameter_bytes=None,
            residual_layer_bytes=None,
            literal_leaf_bytes=None,
            entropy_metadata_bytes=None,
            index_bytes=None,
        )
        record.validate()

    def test_hash_mismatch_rejected(self) -> None:
        with self.assertRaisesRegex(TrialError, "restored hash"):
            trial(restored_sha256="5" * 64).validate()

    def test_failed_evidence_can_retain_verified_roundtrip(self) -> None:
        record = trial(
            status="failed",
            error="inspect breakdown unavailable",
            roundtrip_ok=True,
            parameter_bytes=None,
        )
        record.validate()

    def test_native_counterfactuals_may_remain_unmeasured(self) -> None:
        record = trial(
            literal_only_archive_bytes=None,
            pre_entropy_archive_bytes=None,
            entropy_saved_bytes=None,
            entropy_penalty_bytes=None,
            procedural_gain_bytes=None,
            procedural_penalty_bytes=None,
            index_bytes=None,
            coordinate_saved_bytes=None,
            dag_saved_bytes=None,
            recursive_residual_saved_bytes=None,
            symbolic_saved_bytes=None,
            search_ns=None,
        )
        record.validate()

    def test_missing_breakdown_rejected(self) -> None:
        with self.assertRaisesRegex(
            TrialError, "complete procedural breakdown"
        ):
            trial(parameter_bytes=None).validate()

    def test_external_profile_rejected(self) -> None:
        with self.assertRaisesRegex(TrialError, "leave profile empty"):
            trial(
                native_mathsvg=False,
                codec="zstd",
                codec_config="default",
            ).validate()


if __name__ == "__main__":
    unittest.main()
