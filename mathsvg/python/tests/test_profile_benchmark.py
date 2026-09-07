from __future__ import annotations

import csv
import dataclasses
import pathlib
import tempfile
import unittest

from mathsvg.python.analysis.profile_benchmark import (
    PROFILE_FIELDS,
    ProfilingError,
    build_profile_rows,
    build_svgs,
    write_artifacts,
)
from mathsvg.python.benchmarks.schema import TrialRecord
from mathsvg.python.tests.test_benchmark_summary import external_record
from mathsvg.python.tests.test_trial_schema import trial


def native_record(
    dataset_id: str,
    repetition: int,
    *,
    warmup: bool = False,
) -> TrialRecord:
    digest = ("3" if dataset_id == "a" else "8") * 64
    archive_digest = ("4" if dataset_id == "a" else "9") * 64
    return trial(
        dataset_id=dataset_id,
        input_path=f"development/{dataset_id}.bin",
        input_sha256=digest,
        restored_sha256=digest,
        archive_sha256=archive_digest,
        repetition=repetition,
        schedule_block=repetition * 2 + (dataset_id == "b"),
        warmup=warmup,
    )


def unavailable_record(repetition: int) -> TrialRecord:
    base = external_record(
        "a",
        repetition,
        original_bytes=100,
        archive_bytes=50,
    )
    assert isinstance(base, TrialRecord)
    return dataclasses.replace(
        base,
        status="unavailable",
        error="codec is not installed",
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


class ProfileBenchmarkTests(unittest.TestCase):
    def test_csv_and_svgs_are_byte_deterministic(self) -> None:
        native: list[TrialRecord] = []
        external: list[TrialRecord] = []
        for repetition in range(5):
            for dataset_id in ("a", "b"):
                record = native_record(dataset_id, repetition)
                if dataset_id == "b":
                    record = dataclasses.replace(
                        record,
                        peak_rss_bytes=10_000 + repetition,
                        compression_peak_rss_bytes=10_000 + repetition,
                        decompression_peak_rss_bytes=9_000 + repetition,
                    )
                native.append(record)
                baseline = external_record(
                    dataset_id,
                    repetition,
                    original_bytes=100,
                    archive_bytes=50,
                )
                assert isinstance(baseline, TrialRecord)
                external.append(baseline)
        warmup = dataclasses.replace(
            native[0],
            warmup=True,
            schedule_block=999,
        )
        records = [warmup, *native, *external]
        for record in records:
            record.validate()

        first_rows = build_profile_rows(records)
        second_rows = build_profile_rows(list(reversed(records)))
        self.assertEqual(first_rows, second_rows)
        self.assertEqual(len(first_rows), 6)
        native_corpus = next(
            row
            for row in first_rows
            if row["row_scope"] == "corpus"
            and row["native_mathsvg"]
        )
        self.assertEqual(native_corpus["scheduled_measured_trials"], 10)
        self.assertEqual(native_corpus["complete_repetitions"], 5)
        self.assertEqual(native_corpus["peak_rss_bytes_median"], 10_002)
        self.assertEqual(native_corpus["archive_fraction_median"], 0.8)
        self.assertEqual(native_corpus["procedural_evidence_status"], "exact")

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            first_root = root / "first"
            second_root = root / "second"
            first_outputs = write_artifacts(
                first_rows,
                profiling_output=first_root / "profile.csv",
                plots_directory=first_root / "plots",
                artifact_prefix="fixture",
            )
            second_outputs = write_artifacts(
                second_rows,
                profiling_output=second_root / "profile.csv",
                plots_directory=second_root / "plots",
                artifact_prefix="fixture",
            )
            self.assertEqual(len(first_outputs), 6)
            self.assertEqual(len(second_outputs), 6)
            for first, second in zip(
                first_outputs, second_outputs, strict=True
            ):
                self.assertEqual(first.name, second.name)
                self.assertEqual(first.read_bytes(), second.read_bytes())
            rendered = (
                first_root / "plots" / "fixture-size-ratio.svg"
            ).read_text(encoding="utf-8")
            self.assertIn("DEVELOPMENT EVIDENCE", rendered)
            self.assertNotIn("<script", rendered)

    def test_unavailable_rows_remain_counts_without_fake_metrics(self) -> None:
        records = [unavailable_record(repetition) for repetition in range(5)]
        for record in records:
            record.validate()
        rows = build_profile_rows(records)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["metric_status"], "not-measured")
            self.assertEqual(row["unavailable_trials"], 5)
            self.assertEqual(row["successful_trials"], 0)
            self.assertEqual(row["archive_bytes_median"], None)
            self.assertEqual(
                row["compression_throughput_mb_s_median"], None
            )
        svgs = build_svgs(rows)
        self.assertIn("not measured", svgs["size-ratio"])

        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "profile.csv"
            write_artifacts(
                rows,
                profiling_output=output,
                plots_directory=pathlib.Path(temporary) / "plots",
                artifact_prefix="unavailable",
            )
            with output.open(newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            self.assertEqual(csv_rows[0]["archive_bytes_median"], "")
            self.assertEqual(
                csv_rows[0]["compression_throughput_mb_s_median"], ""
            )

    def test_missing_counterfactual_is_not_replaced_by_zero(self) -> None:
        records = [
            dataclasses.replace(
                native_record("a", repetition),
                literal_only_archive_bytes=None,
                pre_entropy_archive_bytes=None,
                entropy_saved_bytes=None,
                entropy_penalty_bytes=None,
                procedural_gain_bytes=None,
                procedural_penalty_bytes=None,
            )
            for repetition in range(5)
        ]
        for record in records:
            record.validate()
        rows = build_profile_rows(records)
        file_row = next(row for row in rows if row["row_scope"] == "file")
        self.assertEqual(
            file_row["procedural_evidence_status"],
            "structural-only",
        )
        self.assertIsNone(
            file_row["procedural_gain_before_entropy_bytes_median"]
        )
        self.assertEqual(file_row["procedural_coverage_median"], 0.5)
        procedural = build_svgs(rows)["procedural-entropy"]
        self.assertIn("not measured", procedural)

    def test_exact_zero_counterfactual_remains_measured_zero(self) -> None:
        records = [
            dataclasses.replace(
                native_record("a", repetition),
                literal_only_archive_bytes=90,
                pre_entropy_archive_bytes=90,
                entropy_saved_bytes=10,
                entropy_penalty_bytes=0,
                procedural_gain_bytes=10,
                procedural_penalty_bytes=0,
            )
            for repetition in range(5)
        ]
        for record in records:
            record.validate()
        rows = build_profile_rows(records)
        file_row = next(row for row in rows if row["row_scope"] == "file")
        self.assertEqual(
            file_row["procedural_gain_before_entropy_bytes_median"], 0
        )
        self.assertEqual(file_row["entropy_gain_bytes_median"], 10)
        rendered = build_svgs(rows)["procedural-entropy"]
        self.assertIn("0 B", rendered)
        self.assertIn("10 B", rendered)

    def test_non_development_split_is_rejected(self) -> None:
        record = dataclasses.replace(
            native_record("a", 0),
            split="validation",
        )
        record.validate()
        with self.assertRaisesRegex(
            ProfilingError, "development evidence only"
        ):
            build_profile_rows([record])

    def test_profile_schema_is_unique(self) -> None:
        self.assertEqual(len(PROFILE_FIELDS), len(set(PROFILE_FIELDS)))


if __name__ == "__main__":
    unittest.main()
