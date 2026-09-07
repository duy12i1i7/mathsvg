from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mathzip_bench.codecs import (
    BENCHMARK_MAX_ARCHIVE_BYTES,
    BENCHMARK_MAX_INPUT_BYTES,
    BENCHMARK_MAX_OUTPUT_BYTES,
    BENCHMARK_MAX_SEGMENTS,
    Codec,
    build_codec,
)
from mathzip_bench.plots import generate_plots
from mathzip_bench.report import _comparisons, generate_report
from mathzip_bench.runner import (
    InputCase,
    Trial,
    _atomic_checkpoint_json,
    _codec_definition,
    _expected_grid,
    _exclusive_benchmark_lock,
    _inspect_mathzip,
    _json_sha256,
    aggregate_trials,
    execute_trial,
    run_benchmarks,
)
from mathzip_bench.common import sha256_file
from mathzip_bench.process import CommandResult
from mathzip_bench.schema import (
    RESULT_SCHEMA_VERSION,
    mathzip_encode_metrics_error,
)
from mathzip_bench.verification import (
    resolve_results_path,
    validate_csv,
    validate_plot_manifest,
    validate_result_document,
)


class RunnerTests(unittest.TestCase):
    def test_encoder_search_provenance_is_complete_and_consistent(self) -> None:
        metrics = {
            "search_seconds": 0.1,
            "model_fitting_seconds": 0.2,
            "residual_coding_seconds": 0.3,
            "screened_large_input": True,
            "requested_mode": "max",
            "effective_segmentation": "adaptive",
            "primary_regular_anchor_bytes": 262_144,
            "primary_transform_finalist_limit": 5,
            "primary_model_mode_finalist_limit": 3,
            "includes_balanced_frontier": True,
            "balanced_frontier_regular_anchor_bytes": 65_536,
            "balanced_frontier_transform_finalist_limit": 3,
            "balanced_frontier_model_mode_finalist_limit": 2,
            "selected_balanced_frontier": True,
            "selected_regular_anchor_bytes": 65_536,
            "selected_transform_finalist_limit": 3,
            "selected_model_mode_finalist_limit": 2,
        }
        self.assertIsNone(mathzip_encode_metrics_error(metrics))

        malformed = copy.deepcopy(metrics)
        del malformed["effective_segmentation"]
        self.assertIn(
            "incomplete encoder search provenance",
            mathzip_encode_metrics_error(malformed) or "",
        )
        malformed = copy.deepcopy(metrics)
        malformed["requested_mode"] = "balanced"
        self.assertEqual(
            mathzip_encode_metrics_error(malformed),
            "includes_balanced_frontier requires screened Max metrics",
        )
        malformed = copy.deepcopy(metrics)
        malformed["screened_large_input"] = False
        malformed["includes_balanced_frontier"] = False
        self.assertEqual(
            mathzip_encode_metrics_error(malformed),
            "unscreened metrics require null "
            "primary_transform_finalist_limit",
        )

    def test_report_compares_named_mathzip_variants_with_their_baseline(self) -> None:
        rows = [
            {
                "status": "ok",
                "input_path": "fixture.bin",
                "threads": 1,
                "codec": "mathzip-fast-change-point-4kib",
                "codec_family": "mathzip",
                "codec_level": "fast",
                "compressed_bytes": 120,
            },
            {
                "status": "ok",
                "input_path": "fixture.bin",
                "threads": 1,
                "codec": "zstd-fast",
                "codec_family": "zstd",
                "codec_level": "fast",
                "compressed_bytes": 100,
            },
        ]
        comparisons = _comparisons(rows)
        self.assertIn(
            "`mathzip-fast-change-point-4kib` against `zstd-fast`",
            comparisons[0],
        )
        self.assertIn("1 larger", comparisons[0])
        self.assertNotIn("No successful MathZip rows", "\n".join(comparisons))

    def test_output_root_lock_rejects_a_concurrent_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "results"
            with _exclusive_benchmark_lock(output):
                with self.assertRaisesRegex(
                    ValueError, "already holds the output lock"
                ):
                    with _exclusive_benchmark_lock(output):
                        self.fail("a second benchmark lock must not be acquired")

    def test_ablation_plot_exposes_incomplete_variant_coverage(self) -> None:
        def row(
            variant: str,
            codec: str,
            input_path: str,
            *,
            status: str,
            ratio: float | None,
        ) -> dict[str, object]:
            return {
                "profile": "ablation",
                "corpus": "fixture",
                "codec": codec,
                "threads": 1,
                "variant": variant,
                "input_path": input_path,
                "status": status,
                "ratio": ratio,
                "original_bytes": 100,
                "compressed_bytes": 50 if ratio is not None else None,
                "compression_seconds": 1.0 if ratio is not None else None,
                "decompression_seconds": 1.0 if ratio is not None else None,
            }

        document = {
            "run_id": "run",
            "profile": "ablation",
            "result_count": 6,
            "failure_count": 3,
            "results": [
                row("fast", "fast", "a", status="ok", ratio=2.0),
                row("fast", "fast", "b", status="ok", ratio=2.5),
                row("max", "max", "a", status="ok", ratio=3.0),
                row("max", "max", "b", status="warmup_failed", ratio=None),
                row("failed", "failed", "a", status="warmup_failed", ratio=None),
                row("failed", "failed", "b", status="warmup_failed", ratio=None),
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            manifest = generate_plots(document, Path(temporary))
        if manifest["status"] == "dependency_missing":
            self.skipTest("matplotlib is unavailable")
        coverage = manifest["comparison_choices"]["ablation_comparison"]["coverage"]
        self.assertEqual(
            [
                (
                    entry["variant"],
                    entry["successful_rows"],
                    entry["total_rows"],
                )
                for entry in coverage
            ],
            [("failed", 0, 2), ("fast", 2, 2), ("max", 1, 2)],
        )

    def test_plot_manifest_is_verifiable_without_matplotlib(self) -> None:
        document = {
            "run_id": "plot-fixture",
            "profile": "smoke",
            "result_count": 0,
            "failure_count": 0,
            "results": [],
        }
        repository = Path(__file__).resolve().parents[2]
        generator = repository / "python" / "mathzip_bench" / "plots.py"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.dict(sys.modules, {"matplotlib": None}):
                manifest = generate_plots(document, output)
            self.assertEqual(manifest["status"], "dependency_missing")
            self.assertEqual(
                manifest["plot_generator"], "python/mathzip_bench/plots.py"
            )
            self.assertEqual(
                manifest["artifacts_sha256"],
                {"plot_data.json": sha256_file(output / "plot_data.json")},
            )
            errors, warnings = validate_plot_manifest(document, output, generator)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])

            with (output / "plot_data.json").open("a", encoding="utf-8") as handle:
                handle.write(" ")
            errors, _ = validate_plot_manifest(document, output, generator)
            self.assertIn(
                "plot artifact SHA-256 does not match: plot_data.json",
                errors,
            )

    def test_strict_validation_can_preserve_recorded_failures_as_warnings(
        self,
    ) -> None:
        failure = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "run_id": "run",
            "corpus": "fixture",
            "input_path": "fixture.bin",
            "codec": "mathzip-max",
            "threads": 1,
            "status": "warmup_failed",
            "error": "compression_timeout: compression timed out",
            "input_license": "CC0-1.0",
            "input_provenance": "unit-test fixture",
            "input_manifest": "fixture-manifest.json",
            "input_manifest_sha256": "a" * 64,
            "input_manifest_verified": True,
        }
        document = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "run_id": "run",
            "profile": "test",
            "source": {
                "source_revision": "f" * 40,
                "source_dirty": False,
                "source_tree_sha256": "c" * 64,
                "source_tree_manifest": None,
                "source_revision_after": "f" * 40,
                "source_dirty_after": False,
                "source_tree_sha256_after": "c" * 64,
                "source_file_count": 1,
                "source_file_count_after": 1,
                "source_stable_during_run": True,
            },
            "system": {},
            "methodology": {
                "warmups": 1,
                "repeats": 3,
                "collect_mathzip_inspection": True,
                "binaries_stable_during_run": True,
                "available_executable_hashes_complete": True,
                "mathzip_build_info_stable_during_run": True,
                "mathzip_build_matches_source": True,
                "source_stable_during_run": True,
                "input_rights_and_provenance_complete": True,
                "input_manifest_integrity_complete": True,
                "codec_filter": None,
            },
            "timing_protocol_compliant": True,
            "publication_evidence_complete": True,
            "publication_evidence_gaps": [],
            "scientifically_compliant_run": True,
            "codec_definitions": [],
            "result_count": 1,
            "failure_count": 1,
            "results": [failure],
        }
        message = (
            "results[0]: status=warmup_failed: "
            "compression_timeout: compression timed out"
        )

        strict_errors, strict_warnings = validate_result_document(
            document, strict=True
        )
        allowed_errors, allowed_warnings = validate_result_document(
            document, strict=True, allow_failures=True
        )

        self.assertIn(message, strict_errors)
        self.assertNotIn(message, strict_warnings)
        self.assertEqual(
            [error for error in strict_errors if error != message],
            allowed_errors,
        )
        self.assertIn(message, allowed_warnings)

    def test_inspection_uses_profile_timeout_and_rejects_non_object_json(self) -> None:
        codec = Codec(
            name="mathzip-fast",
            family="mathzip",
            level="fast",
            extension=".mz",
            executable="/bin/true",
            mode="fast",
        )
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "fixture.mz"
            archive.write_bytes(b"fixture")
            result = CommandResult(
                command=["mathzip", "inspect"],
                returncode=0,
                wall_seconds=0.1,
                cpu_seconds=0.1,
                peak_rss_bytes=1024,
                timed_out=False,
                stdout=b"[]",
                stderr=b"",
            )
            with patch(
                "mathzip_bench.runner.run_command", return_value=result
            ) as mocked:
                inspection = _inspect_mathzip(codec, archive, timeout=1234.0)
        self.assertEqual(
            inspection, {"_inspection_error": "inspect JSON root is not an object"}
        )
        self.assertEqual(mocked.call_args.kwargs["timeout"], 1234.0)

    def test_mathzip_metrics_mode_rejects_invalid_and_uncollectable_inline(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.json"
            for mode, collect, message in (
                ("unknown", True, "must be 'probe' or 'inline'"),
                ("inline", False, "requires benchmark.collect_mathzip_inspection=true"),
            ):
                with self.subTest(mode=mode, collect=collect):
                    config = {
                        "schema_version": 1,
                        "profile": "invalid-metrics-mode",
                        "output_dir": str(root / "results"),
                        "benchmark": {
                            "mathzip_metrics_mode": mode,
                            "collect_mathzip_inspection": collect,
                        },
                        "codecs": ["raw"],
                        "inputs": [],
                    }
                    config_path.write_text(
                        json.dumps(config),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        run_benchmarks(
                            config,
                            config_path=config_path,
                            repository_root=repository,
                            mathzip_binary="missing-mathzip",
                            allow_short_run=True,
                        )

    def test_inline_mathzip_metrics_use_the_timed_archive_and_one_compression(
        self,
    ) -> None:
        codec = Codec(
            name="mathzip-balanced",
            family="mathzip",
            level="balanced",
            extension=".mz",
            executable="/bin/true",
            mode="balanced",
        )
        calls: list[list[str]] = []
        emit_metrics = [True]
        phase_metrics = {
            "search_seconds": 0.1,
            "model_fitting_seconds": 0.2,
            "residual_coding_seconds": 0.3,
            "screened_large_input": False,
            "requested_mode": "balanced",
            "effective_segmentation": "adaptive",
            "primary_regular_anchor_bytes": 4096,
            "primary_transform_finalist_limit": None,
            "primary_model_mode_finalist_limit": None,
            "includes_balanced_frontier": False,
            "balanced_frontier_regular_anchor_bytes": None,
            "balanced_frontier_transform_finalist_limit": None,
            "balanced_frontier_model_mode_finalist_limit": None,
            "selected_balanced_frontier": False,
            "selected_regular_anchor_bytes": 4096,
            "selected_transform_finalist_limit": None,
            "selected_model_mode_finalist_limit": None,
        }

        def fake_run(command: list[str], **_kwargs: object) -> CommandResult:
            command = [str(argument) for argument in command]
            calls.append(command)
            stdout = b""
            if command[1] == "compress":
                source = Path(command[-2])
                archive = Path(command[-1])
                archive.write_bytes(source.read_bytes())
                if "--metrics-output" in command and emit_metrics[0]:
                    metrics_path = Path(
                        command[command.index("--metrics-output") + 1]
                    )
                    metrics_path.write_text(
                        json.dumps(phase_metrics),
                        encoding="utf-8",
                    )
            elif command[1] == "decompress":
                archive = Path(command[-2])
                restored = Path(command[-1])
                restored.write_bytes(archive.read_bytes())
            elif command[1] == "inspect":
                archive_size = Path(command[-1]).stat().st_size
                stdout = json.dumps(
                    {
                        "format_version": 1,
                        "original_size": archive_size,
                        "transformed_size": archive_size,
                        "compressed_size": archive_size,
                        "payload_size": 0,
                        "transform_count": 1,
                        "segment_count": 1,
                        "container_overhead_bytes": archive_size,
                        "metadata_bytes": 0,
                        "transform_metadata_bytes": 0,
                        "segment_descriptor_bytes": 0,
                        "partition_metadata_bytes": 0,
                        "model_parameter_bytes": 0,
                        "residual_bytes": 0,
                        "actual_residual_coded_bytes": 0,
                        "math_segments_winning_raw": 0,
                        "math_segments_winning_zstd": 0,
                        "model_distribution": {"raw": 1},
                        "residual_distribution": {"raw": 1},
                        "residual_mode_distribution": {"add": 1},
                        "checksums": {
                            "header": True,
                            "archive": True,
                            "segments": True,
                            "original": True,
                        },
                        "transforms": ["identity"],
                        "mean_segment_size": archive_size,
                        "median_segment_size": archive_size,
                        "raw_model_percentage": 100.0,
                        "raw_fallback_percentage": 100.0,
                        "estimated_residual_entropy": 0.0,
                    }
                ).encode("utf-8")
            return CommandResult(
                command=command,
                returncode=0,
                wall_seconds=0.5,
                cpu_seconds=0.25,
                peak_rss_bytes=1024,
                timed_out=False,
                stdout=stdout,
                stderr=b"",
            )

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "input.bin"
            source.write_bytes(b"inline metrics fixture")
            digest = sha256_file(source)
            with patch("mathzip_bench.runner.run_command", side_effect=fake_run):
                inline = execute_trial(
                    codec,
                    source,
                    digest,
                    index=0,
                    timeout=10,
                    collect_inspection=True,
                    mathzip_metrics_mode="inline",
                )
            inline_compressions = [
                command for command in calls if command[1] == "compress"
            ]
            self.assertEqual(inline.status, "ok", inline.error)
            self.assertEqual(len(inline_compressions), 1)
            self.assertEqual(
                inline.compression_metrics_command,
                inline.compression_command,
            )
            self.assertEqual(inline.compression_metrics, phase_metrics)
            self.assertIn("--metrics-output", inline.compression_command)
            metrics_index = inline.compression_command.index("--metrics-output")
            self.assertEqual(
                inline.compression_command[metrics_index + 1],
                inline.compression_command[-1] + ".metrics.json",
            )

            calls.clear()
            with patch("mathzip_bench.runner.run_command", side_effect=fake_run):
                warmup = execute_trial(
                    codec,
                    source,
                    digest,
                    index=-1,
                    timeout=10,
                    collect_inspection=False,
                    mathzip_metrics_mode="inline",
                )
            warmup_compressions = [
                command for command in calls if command[1] == "compress"
            ]
            self.assertEqual(warmup.status, "ok", warmup.error)
            self.assertEqual(len(warmup_compressions), 1)
            self.assertIn("--metrics-output", warmup.compression_command)
            self.assertEqual(
                warmup.compression_metrics_command,
                warmup.compression_command,
            )

            calls.clear()
            with patch("mathzip_bench.runner.run_command", side_effect=fake_run):
                probe = execute_trial(
                    codec,
                    source,
                    digest,
                    index=1,
                    timeout=10,
                    collect_inspection=True,
                )
            probe_compressions = [
                command for command in calls if command[1] == "compress"
            ]
            self.assertEqual(probe.status, "ok", probe.error)
            self.assertEqual(len(probe_compressions), 2)
            self.assertNotIn("--metrics-output", probe.compression_command)
            self.assertIn(
                "--metrics-output",
                probe.compression_metrics_command or [],
            )
            self.assertNotEqual(
                probe.compression_metrics_command,
                probe.compression_command,
            )

            calls.clear()
            emit_metrics[0] = False
            with patch("mathzip_bench.runner.run_command", side_effect=fake_run):
                missing_sidecar = execute_trial(
                    codec,
                    source,
                    digest,
                    index=2,
                    timeout=10,
                    collect_inspection=True,
                    mathzip_metrics_mode="inline",
                )
            missing_compressions = [
                command for command in calls if command[1] == "compress"
            ]
            self.assertEqual(
                missing_sidecar.status,
                "compression_metrics_failed",
            )
            self.assertIn(
                "produced no encoder metrics file",
                missing_sidecar.error or "",
            )
            self.assertEqual(len(missing_compressions), 1)
            self.assertEqual(
                missing_sidecar.compression_metrics_command,
                missing_sidecar.compression_command,
            )

        inline_definition = _codec_definition(
            codec,
            Path(__file__).resolve().parents[2],
            False,
            {"/bin/true": "a" * 64},
            {"/bin/true": None},
            "inline",
        )
        probe_definition = _codec_definition(
            codec,
            Path(__file__).resolve().parents[2],
            False,
            {"/bin/true": "a" * 64},
            {"/bin/true": None},
            "probe",
        )
        self.assertIn(
            "--metrics-output",
            inline_definition["compression_command_template"],
        )
        self.assertNotIn(
            "--metrics-output",
            probe_definition["compression_command_template"],
        )

    def test_strict_verification_accepts_complete_inline_metrics_provenance(
        self,
    ) -> None:
        source_revision = "f" * 40
        input_sha256 = "a" * 64
        archive_sha256 = "b" * 64
        compression_command = [
            "mathzip",
            "compress",
            "--metrics-output",
            "archive.mz.metrics.json",
            "input.bin",
            "archive.mz",
        ]
        inspection = {
            "format_version": 1,
            "original_size": 200,
            "transformed_size": 200,
            "compressed_size": 100,
            "payload_size": 40,
            "transform_count": 1,
            "segment_count": 1,
            "container_overhead_bytes": 10,
            "metadata_bytes": 50,
            "transform_metadata_bytes": 0,
            "segment_descriptor_bytes": 20,
            "partition_metadata_bytes": 20,
            "model_parameter_bytes": 30,
            "residual_bytes": 40,
            "actual_residual_coded_bytes": 40,
            "math_segments_winning_raw": 1,
            "math_segments_winning_zstd": 0,
            "model_distribution": {"raw": 1},
            "residual_distribution": {"raw": 1},
            "residual_mode_distribution": {"add": 1},
            "checksums": {
                "header": True,
                "archive": True,
                "segments": True,
                "original": True,
            },
            "transforms": ["identity"],
            "mean_segment_size": 200.0,
            "median_segment_size": 200.0,
            "raw_model_percentage": 100.0,
            "raw_fallback_percentage": 100.0,
            "estimated_residual_entropy": 0.0,
        }
        phase_metrics = {
            "search_seconds": 0.1,
            "model_fitting_seconds": 0.2,
            "residual_coding_seconds": 0.3,
            "screened_large_input": False,
            "requested_mode": "balanced",
            "effective_segmentation": "adaptive",
            "primary_regular_anchor_bytes": 4096,
            "primary_transform_finalist_limit": None,
            "primary_model_mode_finalist_limit": None,
            "includes_balanced_frontier": False,
            "balanced_frontier_regular_anchor_bytes": None,
            "balanced_frontier_transform_finalist_limit": None,
            "balanced_frontier_model_mode_finalist_limit": None,
            "selected_balanced_frontier": False,
            "selected_regular_anchor_bytes": 4096,
            "selected_transform_finalist_limit": None,
            "selected_model_mode_finalist_limit": None,
        }
        codec = Codec(
            name="mathzip-balanced",
            family="mathzip",
            level="balanced",
            extension=".mz",
            executable="/bin/mathzip",
            mode="balanced",
        )
        trials = [
            Trial(
                index=index,
                status="ok",
                error=None,
                compressed_bytes=100,
                compression_seconds=1.0,
                decompression_seconds=2.0,
                compression_cpu_seconds=0.5,
                decompression_cpu_seconds=0.75,
                compression_peak_rss_bytes=1024,
                decompression_peak_rss_bytes=2048,
                archive_sha256=archive_sha256,
                restored_sha256=input_sha256,
                roundtrip_verified=True,
                compression_command=list(compression_command),
                decompression_command=["mathzip", "decompress"],
                compression_stderr="",
                decompression_stderr="",
                inspection=copy.deepcopy(inspection),
                compression_metrics=dict(phase_metrics),
                compression_metrics_command=list(compression_command),
                compression_metrics_stderr="",
            )
            for index in range(3)
        ]
        row = aggregate_trials(
            run_id="run",
            profile="test",
            case=InputCase(
                corpus="fixture",
                path=Path("input.bin"),
                recorded_path="input.bin",
                tags={},
                input_license="CC0-1.0",
                input_provenance="unit-test generated fixture",
                expected_sha256=input_sha256,
                expected_size=200,
                input_manifest="manifest.json",
                input_manifest_sha256="c" * 64,
            ),
            input_sha256=input_sha256,
            input_entropy=1.0,
            original_bytes=200,
            codec=codec,
            requested_repeats=3,
            trials=trials,
        )
        self.assertEqual(
            row["mathzip_search_provenance"],
            {
                field: phase_metrics[field]
                for field in (
                    "screened_large_input",
                    "requested_mode",
                    "effective_segmentation",
                    "primary_regular_anchor_bytes",
                    "primary_transform_finalist_limit",
                    "primary_model_mode_finalist_limit",
                    "includes_balanced_frontier",
                    "balanced_frontier_regular_anchor_bytes",
                    "balanced_frontier_transform_finalist_limit",
                    "balanced_frontier_model_mode_finalist_limit",
                    "selected_balanced_frontier",
                    "selected_regular_anchor_bytes",
                    "selected_transform_finalist_limit",
                    "selected_model_mode_finalist_limit",
                )
            },
        )
        self.assertTrue(row["mathzip_search_provenance_consistent"])
        document = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "run_id": "run",
            "profile": "test",
            "source": {
                "source_revision": source_revision,
                "source_dirty": False,
                "source_tree_sha256": "d" * 64,
                "source_tree_manifest": None,
                "source_revision_after": source_revision,
                "source_dirty_after": False,
                "source_tree_sha256_after": "d" * 64,
                "source_file_count": 1,
                "source_file_count_after": 1,
                "source_stable_during_run": True,
            },
            "system": {},
            "methodology": {
                "warmups": 1,
                "repeats": 3,
                "collect_mathzip_inspection": True,
                "mathzip_metrics_mode": "inline",
                "mathzip_metrics_in_compression_timing": True,
                "binaries_stable_during_run": True,
                "available_executable_hashes_complete": True,
                "mathzip_build_info_stable_during_run": True,
                "mathzip_build_matches_source": True,
                "source_stable_during_run": True,
                "input_rights_and_provenance_complete": True,
                "input_manifest_integrity_complete": True,
                "codec_filter": None,
            },
            "timing_protocol_compliant": True,
            "publication_evidence_complete": True,
            "publication_evidence_gaps": [],
            "scientifically_compliant_run": True,
            "codec_definitions": [
                {
                    "name": codec.name,
                    "family": "mathzip",
                    "threads": 1,
                    "available": True,
                    "executable_sha256": "e" * 64,
                    "build_info": {
                        "source_revision": source_revision,
                        "source_dirty": False,
                    },
                    "compression_command_template": [
                        "mathzip",
                        "compress",
                        "--metrics-output",
                        "{archive}.metrics.json",
                        "{input}",
                        "{archive}",
                    ],
                }
            ],
            "result_count": 1,
            "failure_count": 0,
            "results": [row],
        }
        errors, warnings = validate_result_document(document, strict=True)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        inconsistent = copy.deepcopy(document)
        inconsistent["results"][0]["trials"][1]["compression_metrics"][
            "selected_regular_anchor_bytes"
        ] = 8192
        errors, _ = validate_result_document(inconsistent, strict=True)
        self.assertTrue(
            any(
                "search provenance differs across measured repetitions"
                in error
                for error in errors
            )
        )

    def test_raw_smoke_produces_verifiable_json_and_csv(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            (inputs / "fixture.bin").write_bytes(bytes(range(256)) * 4)
            config = {
                "schema_version": 1,
                "profile": "smoke",
                "output_dir": str(root / "results"),
                "benchmark": {
                    "warmups": 1,
                    "repeats": 3,
                    "timeout_seconds": 10,
                    "threads": [1],
                    "record_absolute_paths": False,
                },
                "codecs": ["raw"],
                "inputs": [
                    {
                        "corpus": "fixture",
                        "root": str(inputs),
                        "patterns": ["*.bin"],
                        "required": True,
                        "input_license": "CC0-1.0",
                        "input_provenance": "unit-test generated fixture",
                    }
                ],
            }
            config_path = root / "smoke.yaml"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            document, run_directory = run_benchmarks(
                config,
                config_path=config_path,
                repository_root=repository,
                mathzip_binary="missing-mathzip",
            )
            self.assertEqual(document["status"], "complete")
            self.assertEqual(document["failure_count"], 0)
            self.assertEqual(document["results"][0]["status"], "ok")
            self.assertTrue(
                document["results"][0]["input_path"].startswith("<external>/")
            )
            self.assertEqual(document["results"][0]["input_license"], "CC0-1.0")
            self.assertEqual(
                document["results"][0]["input_provenance"],
                "unit-test generated fixture",
            )
            self.assertIn("source_tree_sha256", document["source"])
            self.assertTrue(document["source"]["source_stable_during_run"])
            self.assertTrue(document["methodology"]["source_stable_during_run"])
            self.assertEqual(
                document["methodology"]["mathzip_metrics_mode"],
                "probe",
            )
            self.assertFalse(
                document["methodology"][
                    "mathzip_metrics_in_compression_timing"
                ]
            )
            self.assertIn("physical_cores", document["system"])
            self.assertTrue(document["timing_protocol_compliant"])
            self.assertNotIn(str(repository), json.dumps(document))
            errors, warnings = validate_result_document(document, strict=False)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])
            csv_errors, _ = validate_csv(
                run_directory / "results.csv", document["results"]
            )
            self.assertEqual(csv_errors, [])
            latest_target = resolve_results_path(root / "results" / "latest.json")
            self.assertEqual(latest_target, run_directory / "results.json")
            report = run_directory / "report.md"
            generate_report(document, report)
            self.assertIn("Weighted corpus results", report.read_text(encoding="utf-8"))
            plot_manifest = generate_plots(document, run_directory / "plots")
            self.assertIn(
                plot_manifest["status"], ("generated", "dependency_missing")
            )
            self.assertEqual(plot_manifest["profile"], "smoke")
            self.assertEqual(plot_manifest["failure_count"], 0)
            expected_document_hash = hashlib.sha256(
                json.dumps(
                    document,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual(
                plot_manifest["result_document_sha256"],
                expected_document_hash,
            )
            self.assertEqual(
                plot_manifest["result_document_hash_method"],
                "canonical-json-v1",
            )
            self.assertEqual(
                plot_manifest["plot_generator_sha256"],
                sha256_file(
                    repository / "python" / "mathzip_bench" / "plots.py"
                ),
            )
            self.assertTrue((run_directory / "plots" / "plot_manifest.json").is_file())
            self.assertTrue((run_directory / "plots" / "plot_data.json").is_file())
            identity = json.loads(
                (run_directory / "run-identity.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                identity["methodology_policy"]["mathzip_metrics_mode"],
                "probe",
            )
            self.assertFalse(
                identity["methodology_policy"][
                    "mathzip_metrics_in_compression_timing"
                ]
            )

            csv_path = run_directory / "results.csv"
            lines = csv_path.read_text(encoding="utf-8").splitlines()
            csv_path.write_text(
                "\n".join([lines[0], *(line + ",surplus" for line in lines[1:])])
                + "\n",
                encoding="utf-8",
            )
            csv_errors, _ = validate_csv(csv_path, document["results"])
            self.assertTrue(
                any("surplus undeclared cells" in error for error in csv_errors)
            )

    def test_checkpoint_resume_is_fail_closed_and_restarts_in_progress_row(
        self,
    ) -> None:
        clean_source = {
            "source_revision": "a" * 40,
            "source_dirty": False,
            "git_revision": "a" * 40,
            "git_dirty": False,
            "revision_available": True,
            "git_status_entry_count": 0,
            "source_tree_sha256": "b" * 64,
            "source_file_count": 1,
            "source_tree_manifest": None,
            "source_tree_manifest_reason": "test",
            "selection_method": "unit test",
        }
        system = {
            "hostname": "checkpoint-host",
            "os": "Unit Test OS",
            "kernel": "test",
            "architecture": "test",
            "cpu_model": "Test CPU",
            "logical_cores": 4,
            "physical_cores": 2,
            "ram_bytes": 1024 * 1024,
            "storage": {
                "filesystem_type": "testfs",
                "free_bytes": 1024,
            },
        }

        def successful_trial(
            _codec: Codec,
            input_path: Path,
            original_sha256: str,
            index: int,
            _timeout: float,
            *,
            collect_inspection: bool,
            mathzip_metrics_mode: str = "probe",
        ) -> Trial:
            del collect_inspection, mathzip_metrics_mode
            return Trial(
                index=index,
                status="ok",
                error=None,
                compressed_bytes=input_path.stat().st_size,
                compression_seconds=0.01,
                decompression_seconds=0.01,
                compression_cpu_seconds=0.005,
                decompression_cpu_seconds=0.005,
                compression_peak_rss_bytes=None,
                decompression_peak_rss_bytes=None,
                archive_sha256="c" * 64,
                restored_sha256=original_sha256,
                roundtrip_verified=True,
                compression_command=["fake-compress"],
                decompression_command=["fake-decompress"],
                compression_stderr="",
                decompression_stderr="",
                inspection=None,
            )

        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            fixtures = [inputs / "a.bin", inputs / "b.bin"]
            for index, fixture in enumerate(fixtures):
                fixture.write_bytes(bytes([index + 1]) * 64)
            (inputs / "manifest.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "path": fixture.name,
                                "size_bytes": fixture.stat().st_size,
                                "sha256": sha256_file(fixture),
                            }
                            for fixture in fixtures
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "schema_version": 1,
                "profile": "checkpoint-test",
                "output_dir": str(root / "results"),
                "benchmark": {
                    "warmups": 1,
                    "repeats": 3,
                    "timeout_seconds": 10,
                    "threads": [1],
                    "record_absolute_paths": False,
                },
                "codecs": ["raw"],
                "inputs": [
                    {
                        "corpus": "fixture",
                        "root": str(inputs),
                        "patterns": ["*.bin"],
                        "input_license": "CC0-1.0",
                        "input_provenance": "unit-test generated fixture",
                    }
                ],
            }
            config_path = root / "checkpoint.json"
            original_config_text = json.dumps(config)
            config_path.write_text(original_config_text, encoding="utf-8")
            call_count = 0

            def interrupt_second_row(*args: object, **kwargs: object) -> Trial:
                nonlocal call_count
                call_count += 1
                if call_count == 6:
                    raise KeyboardInterrupt
                return successful_trial(*args, **kwargs)  # type: ignore[arg-type]

            shared_patches = (
                patch(
                    "mathzip_bench.runner.collect_source_metadata",
                    return_value=clean_source,
                ),
                patch(
                    "mathzip_bench.runner.collect_system_metadata",
                    return_value=system,
                ),
            )
            with shared_patches[0], shared_patches[1], patch(
                "mathzip_bench.runner.execute_trial",
                side_effect=interrupt_second_row,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    run_benchmarks(
                        config,
                        config_path=config_path,
                        repository_root=repository,
                        mathzip_binary="missing-mathzip",
                    )

            output_root = root / "results"
            run_directory = next(output_root.iterdir())
            checkpoint = json.loads(
                (run_directory / "checkpoint.json").read_text(encoding="utf-8")
            )
            self.assertEqual(checkpoint["completed_result_count"], 1)
            self.assertEqual(
                len(list((run_directory / "checkpoint-rows").glob("*.json"))),
                1,
            )
            self.assertFalse((output_root / "latest.json").exists())
            original_run_id = checkpoint["run_id"]
            original_started_at = checkpoint["started_at_utc"]

            with patch(
                "mathzip_bench.runner.collect_source_metadata",
                return_value=clean_source,
            ), patch(
                "mathzip_bench.runner.collect_system_metadata",
                return_value=system,
            ):
                with self.assertRaisesRegex(
                    ValueError, "benchmark methodology/publication policy differs"
                ):
                    run_benchmarks(
                        config,
                        config_path=config_path,
                        repository_root=repository,
                        mathzip_binary="missing-mathzip",
                        resume_from=run_directory,
                        require_publication_evidence=True,
                    )

            mismatched = copy.deepcopy(config)
            mismatched["benchmark"]["timeout_seconds"] = 11
            config_path.write_text(json.dumps(mismatched), encoding="utf-8")
            with patch(
                "mathzip_bench.runner.collect_source_metadata",
                return_value=clean_source,
            ), patch(
                "mathzip_bench.runner.collect_system_metadata",
                return_value=system,
            ):
                with self.assertRaisesRegex(
                    ValueError, "config SHA-256 differs"
                ):
                    run_benchmarks(
                        mismatched,
                        config_path=config_path,
                        repository_root=repository,
                        mathzip_binary="missing-mathzip",
                        resume_from=run_directory,
                    )

            config_path.write_text(original_config_text, encoding="utf-8")
            changed_source = {**clean_source, "source_revision": "d" * 40}
            with patch(
                "mathzip_bench.runner.collect_source_metadata",
                return_value=changed_source,
            ), patch(
                "mathzip_bench.runner.collect_system_metadata",
                return_value=system,
            ):
                with self.assertRaisesRegex(
                    ValueError, "source revision/tree identity differs"
                ):
                    run_benchmarks(
                        config,
                        config_path=config_path,
                        repository_root=repository,
                        mathzip_binary="missing-mathzip",
                        resume_from=run_directory,
                    )

            changed_system = {**system, "hostname": "another-host"}
            with patch(
                "mathzip_bench.runner.collect_source_metadata",
                return_value=clean_source,
            ), patch(
                "mathzip_bench.runner.collect_system_metadata",
                return_value=changed_system,
            ):
                with self.assertRaisesRegex(
                    ValueError, "host/execution-environment identity differs"
                ):
                    run_benchmarks(
                        config,
                        config_path=config_path,
                        repository_root=repository,
                        mathzip_binary="missing-mathzip",
                        resume_from=run_directory,
                    )

            fixtures[1].write_bytes(b"changed input")
            with patch(
                "mathzip_bench.runner.collect_source_metadata",
                return_value=clean_source,
            ), patch(
                "mathzip_bench.runner.collect_system_metadata",
                return_value=system,
            ):
                with self.assertRaisesRegex(
                    ValueError, "input manifest/grid evidence differs"
                ):
                    run_benchmarks(
                        config,
                        config_path=config_path,
                        repository_root=repository,
                        mathzip_binary="missing-mathzip",
                        resume_from=run_directory,
                    )
            fixtures[1].write_bytes(bytes([2]) * 64)

            inconsistent_checkpoint = json.loads(
                (run_directory / "checkpoint.json").read_text(encoding="utf-8")
            )
            inconsistent_checkpoint["completed_result_count"] = 2
            (run_directory / "checkpoint.json").write_text(
                json.dumps(inconsistent_checkpoint), encoding="utf-8"
            )
            with patch(
                "mathzip_bench.runner.collect_source_metadata",
                return_value=clean_source,
            ), patch(
                "mathzip_bench.runner.collect_system_metadata",
                return_value=system,
            ):
                with self.assertRaisesRegex(
                    ValueError, "exceeds durable checkpoint rows"
                ):
                    run_benchmarks(
                        config,
                        config_path=config_path,
                        repository_root=repository,
                        mathzip_binary="missing-mathzip",
                        resume_from=run_directory,
                    )
            # A crash can durably rename a row envelope just before the small
            # progress document is replaced. Resume must recover that row.
            inconsistent_checkpoint["completed_result_count"] = 0
            (run_directory / "checkpoint.json").write_text(
                json.dumps(inconsistent_checkpoint), encoding="utf-8"
            )

            resumed_calls: list[tuple[str, int]] = []

            def record_resumed_row(
                codec: Codec,
                input_path: Path,
                original_sha256: str,
                index: int,
                timeout: float,
                *,
                collect_inspection: bool,
                mathzip_metrics_mode: str = "probe",
            ) -> Trial:
                resumed_calls.append((input_path.name, index))
                return successful_trial(
                    codec,
                    input_path,
                    original_sha256,
                    index,
                    timeout,
                    collect_inspection=collect_inspection,
                    mathzip_metrics_mode=mathzip_metrics_mode,
                )

            row_envelope_written = False

            def interrupt_after_resumed_row_envelope(
                path: Path, value: object
            ) -> None:
                nonlocal row_envelope_written
                if path.parent.name == "checkpoint-rows":
                    _atomic_checkpoint_json(path, value)
                    row_envelope_written = True
                    return
                if path.name == "checkpoint.json" and row_envelope_written:
                    raise KeyboardInterrupt
                _atomic_checkpoint_json(path, value)

            with patch(
                "mathzip_bench.runner.collect_source_metadata",
                return_value=clean_source,
            ), patch(
                "mathzip_bench.runner.collect_system_metadata",
                return_value=system,
            ), patch(
                "mathzip_bench.runner.execute_trial",
                side_effect=record_resumed_row,
            ), patch(
                "mathzip_bench.runner._atomic_checkpoint_json",
                side_effect=interrupt_after_resumed_row_envelope,
            ), self.assertRaises(KeyboardInterrupt):
                run_benchmarks(
                    config,
                    config_path=config_path,
                    repository_root=repository,
                    mathzip_binary="missing-mathzip",
                    resume_from=run_directory / "checkpoint.json",
                )

            self.assertEqual(
                resumed_calls,
                [("b.bin", -1), ("b.bin", 0), ("b.bin", 1), ("b.bin", 2)],
            )
            crash_checkpoint = json.loads(
                (run_directory / "checkpoint.json").read_text(encoding="utf-8")
            )
            self.assertEqual(crash_checkpoint["completed_result_count"], 1)
            self.assertEqual(len(crash_checkpoint["execution_intervals"]), 2)
            self.assertEqual(
                len(list((run_directory / "checkpoint-rows").glob("*.json"))),
                2,
            )
            self.assertFalse((output_root / "latest.json").exists())

            resumed_calls.clear()
            with patch(
                "mathzip_bench.runner.collect_source_metadata",
                return_value=clean_source,
            ), patch(
                "mathzip_bench.runner.collect_system_metadata",
                return_value=system,
            ), patch(
                "mathzip_bench.runner.execute_trial",
                side_effect=record_resumed_row,
            ):
                document, resumed_directory = run_benchmarks(
                    config,
                    config_path=config_path,
                    repository_root=repository,
                    mathzip_binary="missing-mathzip",
                    resume_from=run_directory / "checkpoint.json",
                )

            self.assertEqual(resumed_directory, run_directory)
            self.assertEqual(document["run_id"], original_run_id)
            self.assertEqual(document["started_at_utc"], original_started_at)
            self.assertEqual(resumed_calls, [])
            self.assertEqual(document["result_count"], 2)
            self.assertEqual(
                len(
                    {
                        (
                            row["corpus"],
                            row["input_path"],
                            row["codec"],
                            row["threads"],
                        )
                        for row in document["results"]
                    }
                ),
                2,
            )
            self.assertEqual(document["resume"]["resume_count"], 2)
            self.assertEqual(len(document["resume"]["execution_intervals"]), 3)
            self.assertTrue((output_root / "latest.json").is_file())
            completed_checkpoint = json.loads(
                (run_directory / "checkpoint.json").read_text(encoding="utf-8")
            )
            self.assertEqual(completed_checkpoint["state"], "complete")
            errors, warnings = validate_result_document(document, strict=True)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])

    def test_exact_expected_grid_rejects_missing_extra_and_duplicate_rows(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            for name in ("a.bin", "b.bin"):
                (inputs / name).write_bytes(name.encode("ascii"))
            config = {
                "schema_version": 1,
                "profile": "grid-test",
                "output_dir": str(root / "results"),
                "benchmark": {
                    "warmups": 0,
                    "repeats": 1,
                    "timeout_seconds": 10,
                    "threads": [1],
                },
                "codecs": ["raw"],
                "inputs": [
                    {
                        "corpus": "fixture",
                        "root": str(inputs),
                        "patterns": ["*.bin"],
                        "input_license": "CC0-1.0",
                        "input_provenance": "unit-test generated fixture",
                    }
                ],
            }
            config_path = root / "grid.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            document, _ = run_benchmarks(
                config,
                config_path=config_path,
                repository_root=repository,
                mathzip_binary="missing-mathzip",
                allow_short_run=True,
            )

        missing = copy.deepcopy(document)
        missing["results"].pop()
        missing["result_count"] = 1
        missing["failure_count"] = 0
        errors, _ = validate_result_document(missing, strict=False)
        self.assertTrue(any("grid is missing 1 expected" in error for error in errors))

        duplicate = copy.deepcopy(document)
        duplicate["results"].append(copy.deepcopy(duplicate["results"][0]))
        duplicate["result_count"] = 3
        errors, _ = validate_result_document(duplicate, strict=False)
        self.assertTrue(any("duplicate key" in error for error in errors))

        extra = copy.deepcopy(document)
        extra["results"][0]["codec"] = "unexpected"
        errors, _ = validate_result_document(extra, strict=False)
        self.assertTrue(any("unexpected key" in error for error in errors))
        self.assertTrue(any("missing 1 expected" in error for error in errors))

        legacy = copy.deepcopy(document)
        legacy.pop("expected_grid")
        legacy.pop("resume")
        for field in (
            "checkpoint_after_every_completed_row",
            "checkpoint_writes_outside_timed_regions",
            "resume_identity_fail_closed",
            "input_grid_checked_before_and_after",
            "input_grid_stable_during_run",
            "expected_grid_sha256_before",
            "expected_grid_sha256_after",
        ):
            legacy["methodology"].pop(field, None)
        errors, _ = validate_result_document(legacy, strict=False)
        self.assertEqual(errors, [])

        missing_resume = copy.deepcopy(document)
        missing_resume.pop("resume")
        errors, _ = validate_result_document(missing_resume, strict=False)
        self.assertTrue(any("missing resume metadata" in error for error in errors))

        malformed_intervals = copy.deepcopy(document)
        malformed_intervals["resume"]["execution_intervals"] = [1]
        errors, _ = validate_result_document(malformed_intervals, strict=False)
        self.assertTrue(
            any("resume.execution_intervals[0] is invalid" in error for error in errors)
        )

        unfinished_interval = copy.deepcopy(document)
        completed = unfinished_interval["resume"]["execution_intervals"][0]
        first = copy.deepcopy(completed)
        first.update(
            {
                "index": 0,
                "resumed": False,
                "completed_at_utc": None,
                "ended_at_utc": None,
                "interrupted": False,
            }
        )
        second = copy.deepcopy(completed)
        second.update({"index": 1, "resumed": True})
        unfinished_interval["resume"]["execution_intervals"] = [first, second]
        unfinished_interval["resume"]["resume_count"] = 1
        unfinished_interval["resume"]["active_elapsed_seconds"] = (
            first["active_seconds"] + second["active_seconds"]
        )
        errors, _ = validate_result_document(unfinished_interval, strict=False)
        self.assertTrue(
            any("unfinished non-final interval" in error for error in errors)
        )

        malformed = copy.deepcopy(document)
        malformed["results"][0]["corpus"] = []
        malformed["codec_definitions"][0]["name"] = {}
        errors, _ = validate_result_document(malformed, strict=False)
        self.assertTrue(any("invalid result grid key" in error for error in errors))
        self.assertTrue(any("invalid codec/thread key" in error for error in errors))

    def test_strict_run_does_not_publish_if_input_grid_changes_at_final_check(
        self,
    ) -> None:
        clean_source = {
            "source_revision": "a" * 40,
            "source_dirty": False,
            "git_revision": "a" * 40,
            "git_dirty": False,
            "revision_available": True,
            "git_status_entry_count": 0,
            "source_tree_sha256": "b" * 64,
            "source_file_count": 1,
            "source_tree_manifest": None,
            "source_tree_manifest_reason": "test",
            "selection_method": "unit test",
        }
        system = {
            "hostname": "stable-host",
            "os": "Unit Test OS",
            "kernel": "test",
            "architecture": "test",
            "cpu_model": "Test CPU",
            "logical_cores": 4,
            "physical_cores": 2,
            "ram_bytes": 1024 * 1024,
            "swap_bytes": 0,
            "cpu_affinity": {"logical_cpu_ids": [0, 1, 2, 3]},
            "cpu_governors": None,
            "container": {"detected": False},
            "relevant_environment": {},
            "python_version": "test",
            "storage": {
                "filesystem_type": "testfs",
                "free_bytes": 1024,
                "total_bytes": 2048,
                "mount_point": "/",
                "mount_source": "test",
            },
        }
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            fixture = inputs / "input.bin"
            fixture.write_bytes(b"stable input")
            (inputs / "manifest.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "path": fixture.name,
                                "size_bytes": fixture.stat().st_size,
                                "sha256": sha256_file(fixture),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "schema_version": 1,
                "profile": "input-stability-test",
                "output_dir": str(root / "results"),
                "benchmark": {
                    "warmups": 1,
                    "repeats": 3,
                    "timeout_seconds": 10,
                    "threads": [1],
                    "record_absolute_paths": False,
                },
                "codecs": ["raw"],
                "inputs": [
                    {
                        "corpus": "fixture",
                        "root": str(inputs),
                        "patterns": ["input.bin"],
                        "input_license": "CC0-1.0",
                        "input_provenance": "unit-test fixture",
                    }
                ],
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            dirty_source = {**clean_source, "source_dirty": True}
            with patch(
                "mathzip_bench.runner.collect_source_metadata",
                return_value=dirty_source,
            ), patch(
                "mathzip_bench.runner.collect_system_metadata",
                return_value=system,
            ):
                with self.assertRaisesRegex(
                    ValueError, "requires a clean source revision/tree"
                ):
                    run_benchmarks(
                        config,
                        config_path=config_path,
                        repository_root=repository,
                        mathzip_binary="missing-mathzip",
                        require_publication_evidence=True,
                    )
            self.assertFalse((root / "results").exists())

            grid_calls = 0

            def changed_final_grid(*args: object, **kwargs: object) -> object:
                nonlocal grid_calls
                grid_calls += 1
                grid, state = _expected_grid(*args, **kwargs)  # type: ignore[arg-type]
                if grid_calls == 2:
                    grid["inputs"][0]["actual_input_size"] += 1
                    body = {
                        key: value for key, value in grid.items() if key != "sha256"
                    }
                    grid["sha256"] = _json_sha256(body)
                return grid, state

            with patch(
                "mathzip_bench.runner.collect_source_metadata",
                return_value=clean_source,
            ), patch(
                "mathzip_bench.runner.collect_system_metadata",
                return_value=system,
            ), patch(
                "mathzip_bench.runner._expected_grid",
                side_effect=changed_final_grid,
            ):
                with self.assertRaisesRegex(
                    ValueError, "strict publication evidence became incomplete"
                ):
                    run_benchmarks(
                        config,
                        config_path=config_path,
                        repository_root=repository,
                        mathzip_binary="missing-mathzip",
                        require_publication_evidence=True,
                    )

            output_root = root / "results"
            run_directories = [path for path in output_root.iterdir() if path.is_dir()]
            self.assertEqual(len(run_directories), 1)
            self.assertFalse((output_root / "latest.json").exists())
            checkpoint = json.loads(
                (run_directories[0] / "checkpoint.json").read_text(encoding="utf-8")
            )
            self.assertEqual(checkpoint["state"], "finalizing")

    def test_mathzip_nondeterminism_is_a_failure(self) -> None:
        codec = Codec(
            name="mathzip-balanced",
            family="mathzip",
            level="balanced",
            extension=".mz",
            executable="/bin/true",
            mode="balanced",
        )
        case = InputCase(
            corpus="fixture",
            path=Path("fixture.bin"),
            recorded_path="fixture.bin",
            tags={},
        )
        trials = []
        for index, size in enumerate((10, 11, 10)):
            trials.append(
                Trial(
                    index=index,
                    status="ok",
                    error=None,
                    compressed_bytes=size,
                    compression_seconds=1.0,
                    decompression_seconds=1.0,
                    compression_cpu_seconds=0.5,
                    decompression_cpu_seconds=0.5,
                    compression_peak_rss_bytes=1024,
                    decompression_peak_rss_bytes=1024,
                    archive_sha256=f"{index:064x}",
                    restored_sha256="a" * 64,
                    roundtrip_verified=True,
                    compression_command=["mathzip"],
                    decompression_command=["mathzip"],
                    compression_stderr="",
                    decompression_stderr="",
                    inspection=None,
                )
            )
        row = aggregate_trials(
            run_id="run",
            profile="test",
            case=case,
            input_sha256="a" * 64,
            input_entropy=1.0,
            original_bytes=100,
            codec=codec,
            requested_repeats=3,
            trials=trials,
        )
        self.assertEqual(row["status"], "determinism_failure")
        self.assertFalse(row["deterministic_archive"])

    def test_mathzip_byte_breakdown_must_sum_to_archive_size(self) -> None:
        codec = Codec(
            name="mathzip-balanced",
            family="mathzip",
            level="balanced",
            extension=".mz",
            executable="/bin/true",
            mode="balanced",
        )
        input_sha256 = "a" * 64
        archive_sha256 = "b" * 64
        inspection = {
            "container_overhead_bytes": 10,
            "partition_metadata_bytes": 20,
            "model_parameter_bytes": 30,
            "residual_bytes": 40,
        }
        trials = [
            Trial(
                index=index,
                status="ok",
                error=None,
                compressed_bytes=100,
                compression_seconds=1.0,
                decompression_seconds=2.0,
                compression_cpu_seconds=0.5,
                decompression_cpu_seconds=0.75,
                compression_peak_rss_bytes=1024,
                decompression_peak_rss_bytes=2048,
                archive_sha256=archive_sha256,
                restored_sha256=input_sha256,
                roundtrip_verified=True,
                compression_command=["mathzip", "compress"],
                decompression_command=["mathzip", "decompress"],
                compression_stderr="",
                decompression_stderr="",
                inspection=inspection,
            )
            for index in range(3)
        ]
        row = aggregate_trials(
            run_id="run",
            profile="test",
            case=InputCase(
                corpus="fixture",
                path=Path("fixture.bin"),
                recorded_path="fixture.bin",
                tags={},
                input_license="CC0-1.0",
                input_provenance="unit-test generated fixture",
            ),
            input_sha256=input_sha256,
            input_entropy=1.0,
            original_bytes=200,
            codec=codec,
            requested_repeats=3,
            trials=trials,
        )
        document = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "run_id": "run",
            "profile": "test",
            "source": {
                "source_revision": "f" * 40,
                "source_dirty": False,
                "source_tree_sha256": "c" * 64,
                "source_tree_manifest": None,
                "source_revision_after": "f" * 40,
                "source_dirty_after": False,
                "source_tree_sha256_after": "c" * 64,
                "source_file_count": 1,
                "source_file_count_after": 1,
                "source_stable_during_run": True,
            },
            "system": {},
            "methodology": {
                "warmups": 1,
                "repeats": 3,
                "binaries_stable_during_run": True,
                "source_stable_during_run": True,
            },
            "timing_protocol_compliant": True,
            "publication_evidence_complete": False,
            "publication_evidence_gaps": [
                "unit-test fixture omits environment evidence"
            ],
            "scientifically_compliant_run": False,
            "result_count": 1,
            "failure_count": 0,
            "results": [row],
        }
        errors, _ = validate_result_document(document, strict=False)
        self.assertEqual(errors, [])
        self.assertEqual(row["actual_residual_coded_bytes"], 40)

        row["residual_bytes"] = 41
        errors, _ = validate_result_document(document, strict=False)
        self.assertIn(
            "results[0]: MathZip byte breakdown does not sum to compressed_bytes",
            errors,
        )

        document["methodology"]["collect_mathzip_inspection"] = True
        row["trials"][0]["inspection"] = {"_inspection_error": "timed out"}
        errors, _ = validate_result_document(document, strict=True)
        self.assertIn(
            "results[0].trials[0]: MathZip inspection failed: timed out",
            errors,
        )
        self.assertFalse(
            any("metrics command provenance" in error for error in errors)
        )

        document["methodology"]["mathzip_metrics_mode"] = "inline"
        document["methodology"][
            "mathzip_metrics_in_compression_timing"
        ] = False
        errors, _ = validate_result_document(document, strict=False)
        self.assertIn(
            "mathzip_metrics_in_compression_timing is inconsistent with "
            "mathzip_metrics_mode",
            errors,
        )

        document["methodology"][
            "mathzip_metrics_in_compression_timing"
        ] = True
        errors, _ = validate_result_document(document, strict=True)
        self.assertTrue(
            any("metrics command provenance is missing" in error for error in errors)
        )

        for trial in row["trials"]:
            trial["compression_command"] = [
                "mathzip",
                "compress",
                "--metrics-output",
                "archive.mz.metrics.json",
                "input.bin",
                "archive.mz",
            ]
            trial["compression_metrics_command"] = [
                "mathzip",
                "compress",
                "--metrics-output",
                "different.metrics.json",
                "input.bin",
                "archive.mz",
            ]
        errors, _ = validate_result_document(document, strict=True)
        self.assertTrue(
            any(
                "inline MathZip metrics command must equal" in error
                for error in errors
            )
        )

        duplicate_option = [
            "mathzip",
            "compress",
            "--metrics-output",
            "first.metrics.json",
            "--metrics-output",
            "second.metrics.json",
            "input.bin",
            "archive.mz",
        ]
        for trial in row["trials"]:
            trial["compression_command"] = list(duplicate_option)
            trial["compression_metrics_command"] = list(duplicate_option)
        errors, _ = validate_result_document(document, strict=True)
        self.assertTrue(
            any(
                "requires exactly one --metrics-output with a following path"
                in error
                for error in errors
            )
        )

        missing_option_argument = [
            "mathzip",
            "compress",
            "input.bin",
            "archive.mz",
            "--metrics-output",
        ]
        for trial in row["trials"]:
            trial["compression_command"] = list(missing_option_argument)
            trial["compression_metrics_command"] = list(
                missing_option_argument
            )
        errors, _ = validate_result_document(document, strict=True)
        self.assertTrue(
            any(
                "requires exactly one --metrics-output with a following path"
                in error
                for error in errors
            )
        )

        for trial in row["trials"]:
            timed_command = [
                "mathzip",
                "compress",
                "--metrics-output",
                "archive.mz.metrics.json",
                "input.bin",
                "archive.mz",
            ]
            trial["compression_command"] = timed_command
            trial["compression_metrics_command"] = list(timed_command)
        errors, _ = validate_result_document(document, strict=True)
        self.assertFalse(
            any("metrics command provenance" in error for error in errors)
        )
        self.assertFalse(
            any(
                "inline MathZip metrics command" in error
                or "inline timed compression command" in error
                for error in errors
            )
        )

    def test_ablation_codec_can_have_a_unique_display_name(self) -> None:
        codec = build_codec(
            {
                "name": "ablation-constant",
                "base": "mathzip-balanced",
                "variant": "constant",
                "supported": False,
                "skip_reason": "test",
            },
            mathzip_binary="mathzip",
            threads=1,
        )
        self.assertEqual(codec.name, "ablation-constant")
        self.assertEqual(codec.family, "mathzip")
        self.assertFalse(codec.available)

    def test_mathzip_benchmark_decode_plans_raise_limits_explicitly(self) -> None:
        codec = Codec(
            name="mathzip-balanced",
            family="mathzip",
            level="balanced",
            extension=".mz",
            executable="/bin/true",
            mode="balanced",
        )
        compression = codec.compression_plan(Path("input.bin"), Path("archive.mz"))
        self.assertIn("--max-input-bytes", compression.command)
        self.assertIn(str(BENCHMARK_MAX_INPUT_BYTES), compression.command)
        self.assertIn("--no-sync", compression.command)
        self.assertNotIn("--metrics-output", compression.command)
        metrics = codec.compression_metrics_plan(
            Path("input.bin"), Path("metrics-probe.mz")
        )
        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertIn("--metrics-output", metrics.command)
        decompression = codec.decompression_plan(
            Path("archive.mz"), Path("restored.bin")
        )
        inspection = codec.inspection_plan(Path("archive.mz"))
        self.assertIsNotNone(inspection)
        for plan in (decompression, inspection):
            assert plan is not None
            self.assertIn("--max-archive-bytes", plan.command)
            self.assertIn(str(BENCHMARK_MAX_ARCHIVE_BYTES), plan.command)
            self.assertIn("--max-output-bytes", plan.command)
            self.assertIn(str(BENCHMARK_MAX_OUTPUT_BYTES), plan.command)
            self.assertIn("--max-segments", plan.command)
            self.assertIn(str(BENCHMARK_MAX_SEGMENTS), plan.command)

    def test_every_installed_required_baseline_roundtrips(self) -> None:
        names = (
            "gzip-default",
            "bzip2-default",
            "xz-default",
            "zstd-default",
            "lz4-default",
            "brotli-default",
            "7z-default",
        )
        exercised = 0
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "input.bin"
            source.write_bytes(bytes(range(256)) * 32)
            digest = sha256_file(source)
            for name in names:
                codec = build_codec(
                    name, mathzip_binary="missing-mathzip", threads=1
                )
                if not codec.available:
                    continue
                with self.subTest(codec=name):
                    trial = execute_trial(
                        codec,
                        source,
                        digest,
                        index=0,
                        timeout=20,
                        collect_inspection=False,
                    )
                    self.assertEqual(trial.status, "ok", trial.error)
                    self.assertTrue(trial.roundtrip_verified)
                    exercised += 1
        self.assertGreaterEqual(exercised, 3)

    def test_bzip2_accepts_input_name_with_bz2_suffix(self) -> None:
        codec = build_codec(
            "bzip2-default", mathzip_binary="missing-mathzip", threads=1
        )
        if not codec.available:
            self.skipTest("bzip2 is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "already-compressed-name.bz2"
            source.write_bytes(bytes(range(256)) * 16)
            trial = execute_trial(
                codec,
                source,
                sha256_file(source),
                index=0,
                timeout=20,
                collect_inspection=False,
            )
            self.assertEqual(trial.status, "ok", trial.error)
            self.assertTrue(trial.roundtrip_verified)
            self.assertNotIn(str(source), trial.compression_command)

    def test_bzip2_accepts_an_input_that_already_has_bz2_suffix(self) -> None:
        codec = build_codec(
            "bzip2-fast", mathzip_binary="missing-mathzip", threads=1
        )
        if not codec.available:
            self.skipTest("bzip2 is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "already-compressed-fixture.bz2"
            source.write_bytes(bytes(range(256)) * 8)
            trial = execute_trial(
                codec,
                source,
                sha256_file(source),
                index=0,
                timeout=20,
                collect_inspection=False,
            )
        self.assertEqual(trial.status, "ok", trial.error)
        self.assertTrue(trial.roundtrip_verified)

    def test_cli_entrypoint_with_release_mathzip_when_built(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        binary = repository / "target" / "release" / "mathzip"
        if not binary.is_file():
            self.skipTest("release MathZip binary has not been built")
        source_status = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
        if source_status.returncode != 0 or source_status.stdout.strip():
            self.skipTest(
                "strict release-binary smoke requires a clean source checkout"
            )
        help_result = subprocess.run(
            [str(binary), "decompress", "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
        if "--max-archive-bytes" not in help_result.stdout:
            self.skipTest(
                "release MathZip binary predates benchmark decode-limit flags"
            )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            fixture = inputs / "periodic.bin"
            fixture.write_bytes(bytes((1, 2, 3, 4)) * 1024)
            (inputs / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "test-input-manifest-v1",
                        "entries": [
                            {
                                "path": fixture.name,
                                "size_bytes": fixture.stat().st_size,
                                "sha256": sha256_file(fixture),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "schema_version": 1,
                "profile": "mathzip-e2e-smoke",
                "output_dir": str(root / "results"),
                "benchmark": {
                    "warmups": 1,
                    "repeats": 3,
                    "timeout_seconds": 30,
                    "threads": [1],
                    "collect_mathzip_inspection": True,
                },
                "codecs": ["mathzip-fast"],
                "inputs": [
                    {
                        "corpus": "fixture",
                        "root": str(inputs),
                        "patterns": ["*.bin"],
                        "input_license": "CC0-1.0",
                        "input_provenance": "unit-test generated fixture",
                    }
                ],
            }
            config_path = root / "smoke.yaml"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(repository / "python" / "run_benchmarks.py"),
                    "--config",
                    str(config_path),
                    "--mathzip-binary",
                    str(binary),
                    "--strict",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            results_path = resolve_results_path(root / "results" / "latest.json")
            document = json.loads(results_path.read_text(encoding="utf-8"))
            row = document["results"][0]
            self.assertEqual(row["status"], "ok")
            self.assertTrue(row["deterministic_archive"])
            self.assertEqual(row["successful_repeats"], 3)
            self.assertIsInstance(row["model_distribution"], dict)
            self.assertIsNotNone(row["segment_count"])
            self.assertTrue(row["input_manifest_verified"])
            self.assertIsNotNone(row["search_seconds"])
            self.assertIsNotNone(row["model_fitting_seconds"])
            self.assertIsNotNone(row["residual_coding_seconds"])
            for trial in row["trials"]:
                self.assertNotIn("--metrics-output", trial["compression_command"])
                self.assertIn(
                    "--metrics-output", trial["compression_metrics_command"]
                )
            errors, warnings = validate_result_document(document, strict=False)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])
