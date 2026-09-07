from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stderr

from mathsvg.python.analysis.ablation_pipeline import (
    OUTPUT_FIELDS,
    AblationPipelineError,
    build_ablation_rows,
    main,
    parse_run_metadata,
)
from mathsvg.python.benchmarks.schema import TrialRecord, write_jsonl
from mathsvg.python.tests.test_trial_schema import trial


def _dataset_values(dataset_id: str) -> dict[str, object]:
    symbol = "3" if dataset_id == "one" else "8"
    return {
        "dataset_id": dataset_id,
        "input_path": f"development/{dataset_id}.bin",
        "input_sha256": symbol * 64,
        "restored_sha256": symbol * 64,
        "original_bytes": 100 if dataset_id == "one" else 200,
    }


def _record(
    *,
    ablation_id: str,
    repetition: int,
    dataset_id: str = "one",
    archive_bytes: int | None = None,
    compression_ns: int | None = None,
    decompression_ns: int | None = None,
    peak_rss: int | None = None,
    threads: int = 1,
    status: str = "ok",
    schedule_offset: int = 0,
    deterministic: bool = True,
) -> TrialRecord:
    baseline = ablation_id == "none"
    archive = (
        archive_bytes
        if archive_bytes is not None
        else (80 if baseline else 90)
    )
    compression = (
        compression_ns
        if compression_ns is not None
        else (100 if baseline else 120)
    )
    decompression = (
        decompression_ns
        if decompression_ns is not None
        else (50 if baseline else 60)
    )
    rss = (
        peak_rss
        if peak_rss is not None
        else (1000 if baseline else 1200)
    )
    dataset_offset = 0 if dataset_id == "one" else 1
    original_bytes = 100 if dataset_id == "one" else 200
    record = trial(
        profile="fast",
        codec_config=(
            "opaque-enabled-v7" if baseline else "opaque-counterfactual-v9"
        ),
        config_sha256=("1" if baseline else "a") * 64,
        repetition=repetition,
        schedule_block=repetition * 2 + dataset_offset + schedule_offset,
        schedule_order=0 if baseline else 1,
        archive_bytes=archive,
        archive_sha256=("4" if baseline else "5") * 64,
        compression_wall_ns=compression,
        compression_cpu_ns=compression,
        decompression_wall_ns=decompression,
        decompression_cpu_ns=decompression,
        peak_rss_bytes=rss,
        compression_peak_rss_bytes=rss,
        decompression_peak_rss_bytes=rss - 1,
        literal_only_archive_bytes=356,
        pre_entropy_archive_bytes=archive + 10,
        entropy_saved_bytes=10,
        entropy_penalty_bytes=0,
        procedural_gain_bytes=356 - archive,
        procedural_penalty_bytes=0,
        literal_leaf_bytes=archive - 30,
        function_reconstructed_bytes=original_bytes // 2,
        literal_reconstructed_bytes=original_bytes // 2,
        threads=threads,
        deterministic_archive=deterministic,
        **_dataset_values(dataset_id),
    )
    if status != "ok":
        record = dataclasses.replace(
            record,
            status=status,
            error=f"native {status}",
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
    record.validate()
    return record


def _codec(
    ablation_id: str,
    *,
    threads: int = 1,
) -> dict[str, object]:
    baseline = ablation_id == "none"
    return {
        "codec_id": "mathsvg",
        "config_id": (
            "opaque-enabled-v7" if baseline else "opaque-counterfactual-v9"
        ),
        "threads": threads,
        "native_mathsvg": True,
        "profile": "fast",
        "executable_sha256": "6" * 64,
        "config_sha256": ("1" if baseline else "a") * 64,
        "runtime_profile_sha256": ("8" if baseline else "9") * 64,
        "ablation_id": ablation_id,
        "disabled_algorithms": (
            [] if baseline else ["coordinates"]
        ),
        "available": True,
        "unavailable_reason": "",
    }


def _metadata_document(
    records: list[TrialRecord],
    *,
    datasets: tuple[str, ...] = ("one",),
    ablation_ids: tuple[str, ...] = ("none", "no-coordinate"),
    codecs: list[dict[str, object]] | None = None,
    complete: bool = True,
    raw_sha256: str = "e" * 64,
    raw_bytes: int = 1,
) -> dict[str, object]:
    selected_codecs = (
        codecs
        if codecs is not None
        else [_codec(identifier) for identifier in ablation_ids]
    )
    status_counts = {
        status: sum(record.status == status for record in records)
        for status in ("ok", "failed", "timeout", "unavailable")
    }
    return {
        "schema_version": 1,
        "experiment_id": "dev-1",
        "machine": {
            "machine_id": "local-x86",
            "architecture": "x86_64",
        },
        "source": {
            "commit": "0" * 40,
            "dirty": True,
        },
        "manifest": {
            "canonical_sha256": "2" * 64,
            "split": "development",
            "selected_dataset_ids": list(datasets),
        },
        "ablation_catalog": {
            "sha256": "d" * 64,
            "selected": True,
        },
        "protocol": {
            "seed": 42,
            "warmups": 1,
            "repetitions": 5,
            "schedule_trials": max(len(records), 1),
            "native_ablation_ids": list(ablation_ids),
        },
        "measurement": {
            "method": "gnu-time-v1",
            "gnu_time_sha256": "7" * 64,
        },
        "codecs": selected_codecs,
        "result": {
            "complete": complete,
            "raw_jsonl_sha256": raw_sha256,
            "raw_jsonl_bytes": raw_bytes,
            "trial_rows": max(len(records), 1),
            "status_counts": status_counts,
        },
    }


def _metadata(
    records: list[TrialRecord],
    **overrides: object,
) -> object:
    return parse_run_metadata(
        _metadata_document(records, **overrides),
    )


class AblationPipelineTests(unittest.TestCase):
    def test_metadata_mapping_not_config_name_drives_paired_deltas(
        self,
    ) -> None:
        records = [
            record
            for repetition in range(5)
            for record in (
                _record(ablation_id="none", repetition=repetition),
                _record(
                    ablation_id="no-coordinate",
                    repetition=repetition,
                ),
            )
        ]

        rows = build_ablation_rows(
            records,
            _metadata(records),
            bootstrap_replicates=100,
        )

        self.assertEqual(len(rows), 2)
        file_row = next(row for row in rows if row["row_scope"] == "file")
        self.assertEqual(file_row["ablation_id"], "no-coordinate")
        self.assertEqual(file_row["disabled_algorithms"], "coordinates")
        self.assertEqual(
            file_row["baseline_codec_config"],
            "opaque-enabled-v7",
        )
        self.assertEqual(
            file_row["ablated_codec_config"],
            "opaque-counterfactual-v9",
        )
        self.assertEqual(
            file_row["paired_archive_bytes_delta_mean"],
            10.0,
        )
        self.assertEqual(file_row["paired_successes"], 5)
        self.assertEqual(file_row["protocol_status"], "complete")
        self.assertEqual(file_row["confidence_status"], "confirmed")
        self.assertEqual(
            file_row["decision_status"],
            "enabled-algorithm-size-benefit",
        )

    def test_timeout_and_missing_repetition_remain_explicit(self) -> None:
        records = [
            _record(ablation_id="none", repetition=repetition)
            for repetition in range(5)
        ]
        records.extend(
            _record(
                ablation_id="no-coordinate",
                repetition=repetition,
                status="timeout" if repetition == 3 else "ok",
            )
            for repetition in range(4)
        )

        rows = build_ablation_rows(
            records,
            _metadata(records),
            bootstrap_replicates=100,
        )
        file_row = next(row for row in rows if row["row_scope"] == "file")

        self.assertEqual(file_row["missing_ablated_repetitions"], 1)
        self.assertEqual(file_row["timeout_pairs"], 1)
        self.assertEqual(file_row["ablated_status"], "mixed")
        self.assertEqual(file_row["pairing_status"], "missing")
        self.assertEqual(file_row["protocol_status"], "incomplete")
        self.assertEqual(file_row["confidence_status"], "inconclusive")
        self.assertIn("native timeout", file_row["failure_reasons"])
        self.assertIn("ablated:one:r4:missing", file_row["failure_reasons"])

    def test_corpus_adds_bytes_and_time_but_takes_maximum_rss(
        self,
    ) -> None:
        records: list[TrialRecord] = []
        for repetition in range(5):
            records.extend(
                (
                    _record(
                        ablation_id="none",
                        repetition=repetition,
                        dataset_id="one",
                        archive_bytes=80,
                        peak_rss=1000,
                    ),
                    _record(
                        ablation_id="no-coordinate",
                        repetition=repetition,
                        dataset_id="one",
                        archive_bytes=90,
                        peak_rss=1200,
                    ),
                    _record(
                        ablation_id="none",
                        repetition=repetition,
                        dataset_id="two",
                        archive_bytes=160,
                        compression_ns=200,
                        decompression_ns=100,
                        peak_rss=2000,
                    ),
                    _record(
                        ablation_id="no-coordinate",
                        repetition=repetition,
                        dataset_id="two",
                        archive_bytes=180,
                        compression_ns=240,
                        decompression_ns=120,
                        peak_rss=2400,
                    ),
                )
            )

        first = build_ablation_rows(
            records,
            _metadata(records, datasets=("one", "two")),
            bootstrap_replicates=100,
            bootstrap_seed=123,
        )
        second = build_ablation_rows(
            list(reversed(records)),
            _metadata(records, datasets=("one", "two")),
            bootstrap_replicates=100,
            bootstrap_seed=123,
        )

        self.assertEqual(first, second)
        corpus = next(row for row in first if row["row_scope"] == "corpus")
        self.assertEqual(corpus["original_bytes"], 300)
        self.assertEqual(corpus["baseline_archive_bytes_mean"], 240.0)
        self.assertEqual(corpus["ablated_archive_bytes_mean"], 270.0)
        self.assertEqual(
            corpus["paired_archive_bytes_delta_mean"],
            30.0,
        )
        self.assertEqual(corpus["baseline_peak_rss_bytes_mean"], 2000.0)
        self.assertEqual(corpus["ablated_peak_rss_bytes_mean"], 2400.0)
        self.assertNotEqual(
            corpus["bootstrap_archive_bytes_delta_ci95_low"],
            "",
        )

    def test_archive_nondeterminism_blocks_a_decision(self) -> None:
        records = [
            record
            for repetition in range(5)
            for record in (
                _record(ablation_id="none", repetition=repetition),
                _record(
                    ablation_id="no-coordinate",
                    repetition=repetition,
                    deterministic=repetition != 4,
                ),
            )
        ]

        rows = build_ablation_rows(
            records,
            _metadata(records),
            bootstrap_replicates=100,
        )
        file_row = next(row for row in rows if row["row_scope"] == "file")
        self.assertEqual(file_row["determinism_status"], "fail")
        self.assertEqual(file_row["protocol_status"], "failed")
        self.assertEqual(file_row["decision_status"], "evidence-incomplete")

    def test_schedule_mismatch_is_not_treated_as_a_pair(self) -> None:
        records: list[TrialRecord] = []
        for repetition in range(5):
            records.extend(
                (
                    _record(
                        ablation_id="none",
                        repetition=repetition,
                    ),
                    _record(
                        ablation_id="no-coordinate",
                        repetition=repetition,
                        schedule_offset=1 if repetition == 4 else 0,
                    ),
                )
            )

        rows = build_ablation_rows(
            records,
            _metadata(records),
            bootstrap_replicates=100,
        )
        file_row = next(row for row in rows if row["row_scope"] == "file")
        self.assertEqual(file_row["schedule_mismatch_pairs"], 1)
        self.assertEqual(file_row["paired_successes"], 4)
        self.assertEqual(file_row["pairing_status"], "schedule-mismatch")
        self.assertEqual(file_row["protocol_status"], "incomplete")

    def test_five_repetitions_are_inconclusive_below_two_percent(
        self,
    ) -> None:
        records = [
            record
            for repetition in range(5)
            for record in (
                _record(ablation_id="none", repetition=repetition),
                _record(
                    ablation_id="no-coordinate",
                    repetition=repetition,
                    archive_bytes=81,
                    compression_ns=101,
                    decompression_ns=50,
                    peak_rss=1010,
                ),
            )
        ]

        rows = build_ablation_rows(
            records,
            _metadata(records),
            bootstrap_replicates=100,
        )
        file_row = next(row for row in rows if row["row_scope"] == "file")
        self.assertEqual(
            file_row["protocol_status"],
            "insufficient-repetitions",
        )
        self.assertEqual(file_row["confidence_status"], "inconclusive")
        self.assertEqual(file_row["decision_status"], "evidence-incomplete")

    def test_profile_thread_groups_are_never_cross_paired(self) -> None:
        records = [
            record
            for repetition in range(5)
            for threads in (1, 2)
            for record in (
                _record(
                    ablation_id="none",
                    repetition=repetition,
                    threads=threads,
                ),
                _record(
                    ablation_id="no-coordinate",
                    repetition=repetition,
                    threads=threads,
                ),
            )
        ]
        codecs = [
            _codec(ablation_id, threads=threads)
            for threads in (1, 2)
            for ablation_id in ("none", "no-coordinate")
        ]

        rows = build_ablation_rows(
            records,
            _metadata(records, codecs=codecs),
            bootstrap_replicates=100,
        )

        self.assertEqual(len(rows), 4)
        self.assertEqual({row["threads"] for row in rows}, {1, 2})
        self.assertTrue(all(row["paired_successes"] == 5 for row in rows))

    def test_absent_none_configuration_is_reported_not_inferred(self) -> None:
        records = [
            _record(
                ablation_id="no-coordinate",
                repetition=repetition,
            )
            for repetition in range(5)
        ]
        metadata = _metadata(
            records,
            ablation_ids=("no-coordinate",),
            codecs=[_codec("no-coordinate")],
        )

        rows = build_ablation_rows(
            records,
            metadata,
            bootstrap_replicates=100,
        )
        file_row = next(row for row in rows if row["row_scope"] == "file")
        self.assertEqual(file_row["baseline_codec_config"], "")
        self.assertEqual(file_row["baseline_status"], "missing")
        self.assertEqual(file_row["paired_successes"], 0)
        self.assertEqual(file_row["confidence_status"], "not-measured")
        self.assertIn(
            "configuration absent from run metadata",
            file_row["failure_reasons"],
        )

    def test_raw_config_that_only_looks_like_an_ablation_is_rejected(
        self,
    ) -> None:
        records = [
            record
            for repetition in range(5)
            for record in (
                _record(ablation_id="none", repetition=repetition),
                dataclasses.replace(
                    _record(
                        ablation_id="no-coordinate",
                        repetition=repetition,
                    ),
                    codec_config="fast-ablation-no-coordinate-v1",
                ),
            )
        ]

        with self.assertRaisesRegex(
            AblationPipelineError,
            "no exact run-metadata mapping",
        ):
            build_ablation_rows(
                records,
                _metadata(records),
                bootstrap_replicates=100,
            )

    def test_cli_validates_completed_hashes_and_writes_exact_schema(
        self,
    ) -> None:
        records = [
            record
            for repetition in range(5)
            for record in (
                _record(ablation_id="none", repetition=repetition),
                _record(
                    ablation_id="no-coordinate",
                    repetition=repetition,
                ),
            )
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            raw = root / "benchmark.jsonl"
            metadata_path = root / "benchmark-run.json"
            output = root / "ablation.csv"
            write_jsonl(raw, records)
            document = _metadata_document(
                records,
                raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
                raw_bytes=raw.stat().st_size,
            )
            metadata_path.write_text(
                json.dumps(document, sort_keys=True),
                encoding="utf-8",
            )

            status = main(
                [
                    str(raw),
                    "--run-metadata",
                    str(metadata_path),
                    "--output",
                    str(output),
                    "--bootstrap-replicates",
                    "100",
                ]
            )

            self.assertEqual(status, 0)
            with output.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(
                    tuple(reader.fieldnames or ()),
                    OUTPUT_FIELDS,
                )
                rows = list(reader)
            self.assertEqual(len(rows), 2)
            self.assertRegex(rows[0]["run_metadata_sha256"], r"^[0-9a-f]{64}$")

    def test_incomplete_metadata_rejects_before_opening_raw_jsonl(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            raw = root / "still-running.jsonl"
            metadata_path = root / "still-running-run.json"
            output = root / "ablation.csv"
            raw.write_bytes(b'{"truncated":')
            document = _metadata_document(
                [],
                complete=False,
            )
            metadata_path.write_text(
                json.dumps(document, sort_keys=True),
                encoding="utf-8",
            )
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                status = main(
                    [
                        str(raw),
                        "--run-metadata",
                        str(metadata_path),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(status, 2)
            self.assertIn("result is incomplete", stderr.getvalue())
            self.assertNotIn("truncated JSONL", stderr.getvalue())
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
