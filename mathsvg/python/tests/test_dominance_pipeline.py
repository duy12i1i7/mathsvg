from __future__ import annotations

import csv
import pathlib
import tempfile
import unittest
from dataclasses import replace

from mathsvg.python.analysis.dominance_pipeline import (
    OUTPUT_FIELDS,
    build_dominance_artifacts,
    main,
)
from mathsvg.python.benchmarks.schema import TrialRecord, write_jsonl
from mathsvg.python.tests.test_trial_schema import trial

_EXTERNAL_NONE_FIELDS = (
    "literal_only_archive_bytes",
    "pre_entropy_archive_bytes",
    "entropy_saved_bytes",
    "entropy_penalty_bytes",
    "procedural_gain_bytes",
    "procedural_penalty_bytes",
    "container_overhead_bytes",
    "function_graph_bytes",
    "coordinate_bytes",
    "shared_definition_bytes",
    "reference_bytes",
    "parameter_bytes",
    "residual_layer_bytes",
    "literal_leaf_bytes",
    "entropy_metadata_bytes",
    "index_bytes",
    "node_count",
    "shared_node_count",
    "residual_depth_sum",
    "residual_root_count",
    "function_reconstructed_bytes",
    "literal_reconstructed_bytes",
    "coordinate_saved_bytes",
    "dag_saved_bytes",
    "recursive_residual_saved_bytes",
    "symbolic_saved_bytes",
    "search_ns",
)


def _dataset_values(dataset_id: str) -> dict[str, object]:
    suffix = "3" if dataset_id == "one" else "a"
    return {
        "dataset_id": dataset_id,
        "input_path": f"development/{dataset_id}.bin",
        "input_sha256": suffix * 64,
        "restored_sha256": suffix * 64,
    }


def _native(
    *,
    repetition: int,
    dataset_id: str = "one",
    schedule_order: int = 0,
    archive_bytes: int = 50,
    compression_ns: int = 100,
    decompression_ns: int = 50,
    peak_rss: int = 1000,
    threads: int = 1,
) -> TrialRecord:
    record = trial(
        repetition=repetition,
        schedule_block=repetition,
        schedule_order=schedule_order,
        archive_bytes=archive_bytes,
        archive_sha256="4" * 64,
        compression_wall_ns=compression_ns,
        compression_cpu_ns=compression_ns,
        decompression_wall_ns=decompression_ns,
        decompression_cpu_ns=decompression_ns,
        peak_rss_bytes=peak_rss,
        compression_peak_rss_bytes=peak_rss,
        decompression_peak_rss_bytes=peak_rss - 1,
        pre_entropy_archive_bytes=archive_bytes + 10,
        entropy_saved_bytes=10,
        entropy_penalty_bytes=0,
        procedural_gain_bytes=356 - archive_bytes,
        procedural_penalty_bytes=0,
        literal_leaf_bytes=archive_bytes - 30,
        threads=threads,
        **_dataset_values(dataset_id),
    )
    record.validate()
    return record


def _baseline(
    *,
    repetition: int,
    dataset_id: str = "one",
    schedule_order: int = 1,
    archive_bytes: int = 100,
    compression_ns: int = 200,
    decompression_ns: int = 100,
    peak_rss: int = 2000,
    threads: int = 1,
    status: str = "ok",
) -> TrialRecord:
    values: dict[str, object] = {
        "repetition": repetition,
        "schedule_block": repetition,
        "schedule_order": schedule_order,
        "codec": "zstd",
        "codec_config": "level-3",
        "profile": "",
        "native_mathsvg": False,
        "codec_executable_sha256": "8" * 64,
        "config_sha256": "9" * 64,
        "archive_bytes": archive_bytes,
        "archive_sha256": "b" * 64,
        "compression_wall_ns": compression_ns,
        "compression_cpu_ns": compression_ns,
        "decompression_wall_ns": decompression_ns,
        "decompression_cpu_ns": decompression_ns,
        "peak_rss_bytes": peak_rss,
        "compression_peak_rss_bytes": peak_rss,
        "decompression_peak_rss_bytes": peak_rss - 1,
        "threads": threads,
        **_dataset_values(dataset_id),
        **{field: None for field in _EXTERNAL_NONE_FIELDS},
    }
    if status != "ok":
        values.update(
            {
                "status": status,
                "error": f"zstd {status}",
                "archive_bytes": None,
                "archive_sha256": "",
                "restored_sha256": "",
                "roundtrip_ok": False,
                "deterministic_archive": False,
                "compression_wall_ns": None,
                "compression_cpu_ns": None,
                "decompression_wall_ns": None,
                "decompression_cpu_ns": None,
                "peak_rss_bytes": None,
                "compression_peak_rss_bytes": None,
                "decompression_peak_rss_bytes": None,
            }
        )
    record = replace(trial(), **values)
    record.validate()
    return record


def _strong_pass_records() -> list[TrialRecord]:
    return [
        record
        for repetition in range(5)
        for record in (
            _native(repetition=repetition),
            _baseline(repetition=repetition),
        )
    ]


class DominancePipelineTests(unittest.TestCase):
    def test_strong_separation_passes_file_and_corpus(self) -> None:
        artifacts = build_dominance_artifacts(
            _strong_pass_records(),
            bootstrap_replicates=100,
        )

        self.assertEqual(len(artifacts.per_file), 1)
        self.assertEqual(len(artifacts.per_corpus), 1)
        self.assertEqual(len(artifacts.pareto_envelope), 2)
        self.assertEqual(artifacts.failures, ())
        for row in (*artifacts.per_file, *artifacts.per_corpus):
            self.assertTrue(row["certificate_pass"])
            self.assertEqual(row["comparison_status"], "pass")
            self.assertEqual(row["confidence_status"], "confirmed")
            self.assertTrue(row["native_mathsvg"])

    def test_overlapping_confidence_interval_never_passes(self) -> None:
        records: list[TrialRecord] = []
        for repetition in range(10):
            native_compression = 50 if repetition < 5 else 150
            records.extend(
                (
                    _native(
                        repetition=repetition,
                        compression_ns=native_compression,
                    ),
                    _baseline(
                        repetition=repetition,
                        compression_ns=native_compression + 1,
                    ),
                )
            )

        artifacts = build_dominance_artifacts(
            records,
            bootstrap_replicates=100,
        )

        self.assertEqual(len(artifacts.failures), 2)
        for row in artifacts.failures:
            self.assertFalse(row["certificate_pass"])
            self.assertEqual(
                row["compression_confidence_status"],
                "inconclusive",
            )
            self.assertEqual(row["comparison_status"], "inconclusive")

    def test_unavailable_baseline_is_retained_in_failures(self) -> None:
        records = [
            record
            for repetition in range(5)
            for record in (
                _native(repetition=repetition),
                _baseline(
                    repetition=repetition,
                    status="unavailable",
                ),
            )
        ]

        artifacts = build_dominance_artifacts(
            records,
            bootstrap_replicates=100,
        )

        self.assertEqual(len(artifacts.failures), 2)
        for row in artifacts.failures:
            self.assertEqual(row["baseline_status"], "unavailable")
            self.assertEqual(row["comparison_status"], "unavailable")
            self.assertIn("zstd unavailable", row["failure_reason"])

    def test_missing_file_and_corpus_coverage_are_explicit(self) -> None:
        records: list[TrialRecord] = []
        for repetition in range(5):
            records.extend(
                (
                    _native(
                        repetition=repetition,
                        dataset_id="one",
                        schedule_order=0,
                    ),
                    _baseline(
                        repetition=repetition,
                        dataset_id="one",
                        schedule_order=1,
                    ),
                    _native(
                        repetition=repetition,
                        dataset_id="two",
                        schedule_order=2,
                    ),
                )
            )

        artifacts = build_dominance_artifacts(
            records,
            bootstrap_replicates=100,
        )

        missing_file = next(
            row
            for row in artifacts.per_file
            if row["dataset_id"] == "two"
        )
        self.assertEqual(missing_file["comparison_status"], "missing")
        self.assertIn(
            "missing baseline evidence",
            missing_file["failure_reason"],
        )
        corpus = artifacts.per_corpus[0]
        self.assertEqual(corpus["comparison_status"], "incomparable")
        self.assertIn("corpus coverage mismatch", corpus["failure_reason"])

    def test_thread_mismatch_is_not_cross_paired(self) -> None:
        records = [
            record
            for repetition in range(5)
            for record in (
                _native(repetition=repetition, threads=2),
                _baseline(repetition=repetition, threads=1),
            )
        ]

        artifacts = build_dominance_artifacts(
            records,
            bootstrap_replicates=100,
        )

        self.assertEqual(len(artifacts.failures), 4)
        self.assertFalse(
            any(row["certificate_pass"] for row in artifacts.failures)
        )
        self.assertEqual(
            {row["comparison_status"] for row in artifacts.failures},
            {"missing"},
        )

    def test_cli_writes_exact_four_artifacts_with_full_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            raw = root / "benchmark.jsonl"
            output = root / "dominance"
            write_jsonl(raw, _strong_pass_records())

            status = main(
                [
                    str(raw),
                    "--output-dir",
                    str(output),
                    "--bootstrap-replicates",
                    "100",
                ]
            )

            self.assertEqual(status, 0)
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {
                    "per-file.csv",
                    "per-corpus.csv",
                    "pareto-envelope.csv",
                    "failures.csv",
                },
            )
            for path in output.iterdir():
                with path.open(
                    "r",
                    encoding="utf-8",
                    newline="",
                ) as handle:
                    reader = csv.DictReader(handle)
                    self.assertEqual(
                        tuple(reader.fieldnames or ()),
                        OUTPUT_FIELDS,
                    )


if __name__ == "__main__":
    unittest.main()
