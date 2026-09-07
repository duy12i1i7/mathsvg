from __future__ import annotations

import dataclasses
import pathlib
import tempfile
import unittest

from mathsvg.python.benchmarks.schema import write_jsonl
from mathsvg.python.benchmarks.summarize import (
    PROCEDURAL_FIELDS,
    SUMMARY_FIELDS,
    build_procedural_rows,
    build_summary_rows,
    write_csv,
)
from mathsvg.python.tests.test_trial_schema import trial


def external_record(
    dataset_id: str,
    repetition: int,
    *,
    original_bytes: int,
    archive_bytes: int,
) -> object:
    return trial(
        dataset_id=dataset_id,
        input_path=f"development/{dataset_id}.bin",
        input_sha256=("3" if dataset_id == "a" else "8") * 64,
        original_bytes=original_bytes,
        codec="fake",
        codec_config="fixture",
        profile="",
        native_mathsvg=False,
        repetition=repetition,
        schedule_block=repetition * 2 + (dataset_id == "b"),
        archive_bytes=archive_bytes,
        archive_sha256=("4" if dataset_id == "a" else "9") * 64,
        restored_sha256=("3" if dataset_id == "a" else "8") * 64,
        compression_wall_ns=original_bytes * 100,
        compression_cpu_ns=original_bytes * 90,
        decompression_wall_ns=original_bytes * 50,
        decompression_cpu_ns=original_bytes * 45,
        peak_rss_bytes=1000 + repetition,
        compression_peak_rss_bytes=1000 + repetition,
        decompression_peak_rss_bytes=800 + repetition,
        literal_only_archive_bytes=None,
        pre_entropy_archive_bytes=None,
        entropy_saved_bytes=None,
        entropy_penalty_bytes=None,
        procedural_gain_bytes=None,
        procedural_penalty_bytes=None,
        container_overhead_bytes=None,
        function_graph_bytes=None,
        coordinate_bytes=None,
        shared_definition_bytes=None,
        reference_bytes=None,
        parameter_bytes=None,
        residual_layer_bytes=None,
        literal_leaf_bytes=None,
        entropy_metadata_bytes=None,
        index_bytes=None,
        node_count=None,
        shared_node_count=None,
        residual_depth_sum=None,
        residual_root_count=None,
        function_reconstructed_bytes=None,
        literal_reconstructed_bytes=None,
        coordinate_saved_bytes=None,
        dag_saved_bytes=None,
        recursive_residual_saved_bytes=None,
        symbolic_saved_bytes=None,
        search_ns=None,
    )


class BenchmarkSummaryTests(unittest.TestCase):
    def test_file_and_bootstrap_corpus_rows_are_deterministic(self) -> None:
        records = [
            external_record(
                dataset_id,
                repetition,
                original_bytes=100 if dataset_id == "a" else 200,
                archive_bytes=50 if dataset_id == "a" else 100,
            )
            for repetition in range(5)
            for dataset_id in ("a", "b")
        ]
        records = [
            (
                dataclasses.replace(
                    record,
                    peak_rss_bytes=10_000 + record.repetition,
                    compression_peak_rss_bytes=10_000
                    + record.repetition,
                    decompression_peak_rss_bytes=9_000
                    + record.repetition,
                )
                if record.dataset_id == "b"
                else record
            )
            for record in records
        ]
        first = build_summary_rows(
            records, bootstrap_replicates=200, seed=42
        )
        second = build_summary_rows(
            list(reversed(records)),
            bootstrap_replicates=200,
            seed=42,
        )
        self.assertEqual(first, second)
        aggregate = next(
            row for row in first if row["dataset_id"] == "__aggregate__"
        )
        self.assertEqual(aggregate["ratio_median"], 2.0)
        self.assertEqual(aggregate["successful_trials"], 10)
        self.assertEqual(aggregate["peak_rss_bytes_count"], 5)
        self.assertEqual(aggregate["peak_rss_bytes_median"], 10_002)
        self.assertEqual(
            aggregate["compression_peak_rss_bytes_median"],
            10_002,
        )
        self.assertEqual(
            aggregate["decompression_peak_rss_bytes_median"],
            9_002,
        )
        self.assertTrue(aggregate["protocol_repetitions_ok"])
        self.assertNotEqual(aggregate["bootstrap_ratio_ci95_low"], "")

    def test_procedural_metrics_are_derived_without_estimates(self) -> None:
        records = [
            dataclasses.replace(
                trial(),
                repetition=repetition,
                schedule_block=repetition,
            )
            for repetition in range(5)
        ]
        rows = build_procedural_rows(records)
        file_row = next(row for row in rows if row["row_scope"] == "file")
        self.assertEqual(file_row["procedural_coverage"], 0.5)
        self.assertEqual(
            file_row["procedural_gain_before_entropy_bytes"],
            266.0,
        )
        self.assertEqual(file_row["procedural_gain_bytes"], 276.0)
        self.assertEqual(
            file_row["search_time_per_saved_byte_ns"],
            100 / 276,
        )
        self.assertEqual(
            file_row["time_per_saved_byte_basis"],
            "compression_wall_ns",
        )

    def test_observed_delta_below_two_percent_requires_ten_repetitions(
        self,
    ) -> None:
        first = [
            external_record(
                "a",
                repetition,
                original_bytes=1000,
                archive_bytes=500,
            )
            for repetition in range(5)
        ]
        second = [
            dataclasses.replace(
                record,
                codec="fake-close",
                codec_config="fixture-close",
                config_sha256="a" * 64,
                archive_bytes=505,
                archive_sha256="b" * 64,
                schedule_order=1,
            )
            for record in first
        ]
        rows = build_summary_rows(
            [*first, *second],
            bootstrap_replicates=200,
            seed=42,
        )
        file_rows = [
            row for row in rows if row["row_scope"] == "file"
        ]
        self.assertEqual(len(file_rows), 2)
        self.assertTrue(
            all(
                row["close_delta_below_2_percent"]
                for row in file_rows
            )
        )
        self.assertTrue(
            all(not row["protocol_repetitions_ok"] for row in file_rows)
        )
        self.assertTrue(
            all(
                row["confidence_status"] == "inconclusive"
                for row in file_rows
            )
        )

    def test_csv_and_jsonl_outputs_are_byte_deterministic(self) -> None:
        records = [
            external_record(
                "a",
                repetition,
                original_bytes=100,
                archive_bytes=50,
            )
            for repetition in range(5)
        ]
        summary = build_summary_rows(
            records, bootstrap_replicates=200, seed=42
        )
        procedural = build_procedural_rows(records)
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            raw = root / "raw.jsonl"
            write_jsonl(raw, records)
            self.assertEqual(len(raw.read_text().splitlines()), 5)
            first = root / "first.csv"
            second = root / "second.csv"
            write_csv(first, SUMMARY_FIELDS, summary)
            write_csv(second, SUMMARY_FIELDS, summary)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            procedural_path = root / "procedural.csv"
            write_csv(
                procedural_path,
                PROCEDURAL_FIELDS,
                procedural,
            )
            self.assertEqual(
                procedural_path.read_text().splitlines(),
                [",".join(PROCEDURAL_FIELDS)],
            )

    def test_unavailable_trials_remain_visible_in_summary(self) -> None:
        base = external_record(
            "a",
            0,
            original_bytes=100,
            archive_bytes=50,
        )
        unavailable = dataclasses.replace(
            base,
            status="unavailable",
            error="codec not installed",
            archive_bytes=None,
            archive_sha256="",
            restored_sha256="",
            roundtrip_ok=False,
            deterministic_archive=False,
            compression_wall_ns=None,
            compression_cpu_ns=None,
            decompression_wall_ns=None,
            decompression_cpu_ns=None,
            peak_rss_bytes=None,
            compression_peak_rss_bytes=None,
            decompression_peak_rss_bytes=None,
        )
        rows = build_summary_rows(
            [unavailable],
            bootstrap_replicates=200,
            seed=42,
        )
        file_row = next(row for row in rows if row["row_scope"] == "file")
        self.assertEqual(file_row["status"], "unavailable")
        self.assertEqual(file_row["unavailable_trials"], 1)
        self.assertEqual(file_row["successful_trials"], 0)
        self.assertEqual(
            file_row["confidence_status"], "not-measured"
        )


if __name__ == "__main__":
    unittest.main()
