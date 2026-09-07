from __future__ import annotations

import hashlib
import pathlib
import tempfile
import unittest

from mathsvg.python.benchmarks.freeze import sha256_file
from mathsvg.python.benchmarks.config import load_ablation_catalog
from mathsvg.python.benchmarks.protocol import Codec, Trial, Workload
from mathsvg.python.benchmarks.runner import (
    Adapter,
    CodecSpec,
    PreparedWorkload,
    RunContext,
    RunnerError,
    SAFE_BASELINE_ADAPTERS,
    _native_ablation_selections,
    _native_limits,
    _native_thread_selections,
    _source_identity_stable,
    benchmark_repetitions,
    build_native_specs,
    execute_trial,
)
from mathsvg.python.datasets.manifest import DatasetEntry


FAKE_CODEC = """#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sys
import time

command = sys.argv[1]
if command == "--version":
    print("fake-codec 1.0")
elif command == "profile":
    profile = sys.argv[sys.argv.index("--profile") + 1]
    block_bytes = 2097152 if profile == "max" else 1048576
    candidate_budget = {
        "fast": 256,
        "balanced": 4096,
        "max": 65536,
        "structured": 16384,
        "repository": 65536,
    }[profile]
    work_budget = {
        "fast": 24000000,
        "balanced": 80000000,
        "max": 1000000000,
        "structured": 300000000,
        "repository": 1000000000,
    }[profile]
    disabled = []
    for index, value in enumerate(sys.argv):
        if value == "--disable":
            disabled.append(sys.argv[index + 1])
    order = [
        "whole-block-entropy",
        "whole-block-functions",
        "interval-functions",
        "coordinates",
    ]
    disabled = [value for value in order if value in set(disabled)]
    ablation_id = (
        "none"
        if not disabled
        else "without-" + "-and-".join(disabled)
    )
    report = {
        "schema_version": 1,
        "profile": profile,
        "ablation": {
            "id": ablation_id,
            "disabled_algorithms": disabled,
        },
        "container_version": 1,
        "dsl_version": 1,
        "optimizer": {
            "block_bytes": block_bytes,
            "microblock_bytes": 4096,
            "max_candidates": candidate_budget,
            "work_budget": work_budget,
        },
        "parallel": {
            "default_threads": 1,
            "deterministic_source_order_merge": True,
        },
        "entropy_search": {
            "baseline_policy": "G1",
            "add_only_policy": "none" if profile == "fast" else "C8L",
            "chain_depth": 1 if profile == "fast" else 8,
            "one_byte_lazy": profile != "fast",
            "parser_scratch_bytes": (
                262144 if profile == "fast" else 524288
            ),
            "maximum_additional_work_per_input_byte_per_walk": (
                0 if profile == "fast" else 25
            ),
            "maximum_policy_walks": 0 if profile == "fast" else 2,
            "wire_opcode": 7,
            "decoder_semantics_changed": False,
        },
        "implemented_catalogue": {
            "functions": [
                "literal",
                "entropy_literal",
                "const",
                "linear",
                "periodic",
                "recurrence",
                "exceptions",
            ],
            "coordinates": [
                "identity",
                "stride",
                "byte_plane",
                "bit_plane",
            ],
        },
        "emission_gates": {
            "whole_block_entropy": "whole-block-entropy" not in disabled,
            "whole_block_functions": "whole-block-functions" not in disabled,
            "interval_functions": (
                profile != "balanced" and "interval-functions" not in disabled
            ),
            "coordinates": (
                profile != "balanced" and "coordinates" not in disabled
            ),
            "residual": False,
            "symbolic": False,
            "graph": False,
        },
    }
    print(json.dumps(report))
elif command == "encode":
    source = pathlib.Path(sys.argv[2]).read_bytes()
    pathlib.Path(sys.argv[3]).write_bytes(b"FAKE" + source[::-1])
elif command == "decode":
    archive = pathlib.Path(sys.argv[2]).read_bytes()
    if not archive.startswith(b"FAKE"):
        raise SystemExit(3)
    pathlib.Path(sys.argv[3]).write_bytes(archive[4:][::-1])
elif command == "sleep":
    time.sleep(5)
elif command == "inspect":
    archive = pathlib.Path(sys.argv[-1]).read_bytes()
    original = archive[4:][::-1]
    report = {
        "archive_bytes": len(archive),
        "original_bytes": len(original),
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "restored_verified": True,
    }
    if pathlib.Path(sys.argv[0]).name.endswith("-full"):
        report["procedural_breakdown"] = {
            "container_overhead_bytes": len(archive),
            "function_graph_bytes": 0,
            "coordinate_bytes": 0,
            "shared_definition_bytes": 0,
            "reference_bytes": 0,
            "parameter_bytes": 0,
            "residual_layer_bytes": 0,
            "literal_leaf_bytes": 0,
            "entropy_metadata_bytes": 0,
            "node_count": 0,
            "shared_node_count": 0,
            "residual_depth_sum": 0,
            "residual_root_count": 1,
            "function_reconstructed_bytes": 0,
            "literal_reconstructed_bytes": len(original),
            "literal_only_archive_bytes": None,
            "pre_entropy_archive_bytes": None,
            "coordinate_saved_bytes": None,
        }
    print(json.dumps(report))
else:
    raise SystemExit(4)
"""


@unittest.skipUnless(
    pathlib.Path("/usr/bin/time").is_file(),
    "GNU time is unavailable",
)
class BenchmarkRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.codec = self.root / "fake-codec"
        self.codec.write_text(FAKE_CODEC, encoding="utf-8")
        self.codec.chmod(0o755)
        payload = b"real archive round trip" * 32
        self.input = self.root / "input.bin"
        self.input.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        entry = DatasetEntry(
            split="development",
            dataset_id="fixture",
            split_group="fixture-family",
            origin="real",
            primary=True,
            domain="test",
            source="local:test",
            license="test-only",
            sha256=digest,
            bytes=len(payload),
            sealed=False,
            path="input.bin",
            legacy_observed=False,
        )
        workload = Workload(entry.dataset_id, entry.path)
        self.prepared = PreparedWorkload(
            workload=workload,
            entry=entry,
            path=self.input,
            error="",
        )
        self.context = RunContext(
            repository=self.root,
            experiment_id="runner-test",
            machine_id="test-machine",
            architecture="test-arch",
            source_commit="0" * 40,
            dataset_manifest_sha256="1" * 64,
            seed=42,
            time_binary=pathlib.Path("/usr/bin/time"),
            time_sha256=sha256_file(pathlib.Path("/usr/bin/time")),
            compression_timeout_seconds=5.0,
            decompression_timeout_seconds=5.0,
            inspect_timeout_seconds=5.0,
            deterministic_hashes={},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_repetition_policy_defaults_unknown_delta_to_ten(self) -> None:
        self.assertEqual(benchmark_repetitions(None, None), 10)
        self.assertEqual(benchmark_repetitions(0.02, None), 5)
        self.assertEqual(benchmark_repetitions(0.019, None), 10)
        with self.assertRaisesRegex(RunnerError, "at least 10"):
            benchmark_repetitions(None, 5)

    def test_source_stability_compares_the_complete_git_identity(self) -> None:
        identity = {
            "commit": "0" * 40,
            "branch": "mathsvg-absolute",
            "dirty": False,
            "status_sha256": hashlib.sha256(b"").hexdigest(),
        }
        self.assertTrue(_source_identity_stable(identity, dict(identity)))
        for key, value in (
            ("commit", "1" * 40),
            ("branch", "other"),
            ("dirty", True),
            ("status_sha256", "2" * 64),
        ):
            changed = dict(identity)
            changed[key] = value
            with self.subTest(key=key):
                self.assertFalse(
                    _source_identity_stable(identity, changed)
                )
        extended = dict(identity)
        extended["future_identity_field"] = "must-not-be-ignored"
        self.assertFalse(_source_identity_stable(identity, extended))

    def test_native_limits_are_finite_checked_and_cover_enwik9(self) -> None:
        self.assertEqual(
            _native_limits(1_000_000_000),
            (1_000_000_007, 2_001_048_590, 1_000_000_007),
        )
        with self.assertRaisesRegex(RunnerError, "checked archive headroom"):
            _native_limits(1 << 63)

    def test_native_thread_matrix_is_canonical_and_recordable(self) -> None:
        self.assertEqual(_native_thread_selections([]), [("1", 1)])
        selections = _native_thread_selections(["1", "2", "all", "2"])
        self.assertEqual(selections[:2], [("1", 1), ("2", 2)])
        self.assertEqual(selections[2][0], "all")
        self.assertGreaterEqual(selections[2][1], 1)
        for invalid in ("0", "01", "65", "-1", "ALL"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(RunnerError, "thread count"):
                    _native_thread_selections([invalid])

    def test_native_ablation_matrix_is_real_canonical_and_identified(self) -> None:
        catalog_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "configs"
            / "ablation"
            / "catalog.toml"
        )
        catalog = load_ablation_catalog(catalog_path)
        self.assertEqual(
            _native_ablation_selections(
                ["none", "no-coordinate", "none"],
                catalog,
            ),
            [
                ("no-coordinate", ("coordinates",)),
                ("none", ()),
            ],
        )
        with self.assertRaisesRegex(RunnerError, "unknown native ablation"):
            _native_ablation_selections(["no-fake-engine"], catalog)

        config_root = pathlib.Path(__file__).resolve().parents[2] / "configs"
        specs = build_native_specs(
            ["fast"],
            binary=self.codec,
            config_root=config_root,
            ablation_ids=["none", "no-coordinate"],
            ablation_catalog=catalog,
            ablation_catalog_sha256=sha256_file(catalog_path),
        )
        self.assertEqual(len(specs), 2)
        by_ablation = {spec.ablation_id: spec for spec in specs}
        baseline = by_ablation["none"]
        ablated = by_ablation["no-coordinate"]
        self.assertEqual(baseline.disabled_algorithms, ())
        self.assertEqual(ablated.disabled_algorithms, ("coordinates",))
        self.assertEqual(
            ablated.codec.config_id,
            "fast-ablation-no-coordinate-v1",
        )
        assert ablated.adapter is not None
        self.assertIn("--disable", ablated.adapter.encode_args)
        disable_index = ablated.adapter.encode_args.index("--disable")
        self.assertEqual(
            ablated.adapter.encode_args[disable_index + 1],
            "coordinates",
        )
        self.assertNotEqual(
            baseline.runtime_profile_sha256,
            ablated.runtime_profile_sha256,
        )
        self.assertNotEqual(baseline.config_sha256, ablated.config_sha256)
        self.assertEqual(ablated.unavailable_reason, "")


    def test_native_adapter_freezes_threads_and_bounded_cli_limits(self) -> None:
        config_root = pathlib.Path(__file__).resolve().parents[2] / "configs"
        specs = build_native_specs(
            ["fast"],
            binary=self.codec,
            config_root=config_root,
            thread_selectors=["2"],
        )
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec.codec.threads, 2)
        self.assertEqual(spec.codec.config_id, "fast-threads-2-v1")
        self.assertEqual(spec.unavailable_reason, "")
        self.assertEqual(len(spec.runtime_profile_sha256), 64)
        self.assertIsNotNone(spec.runtime_profile)
        self.assertIsNotNone(spec.adapter)
        assert spec.adapter is not None
        self.assertEqual(
            spec.adapter.encode_args,
            (
                "compress",
                "--profile",
                "fast",
                "--threads",
                "2",
                "--max-input-bytes",
                "{max_input_bytes}",
                "{input}",
                "{archive}",
            ),
        )
        self.assertIn("{max_archive_bytes}", spec.adapter.decode_args)
        self.assertIn("{max_output_bytes}", spec.adapter.decode_args)

    def test_zpaq_adapter_renames_the_single_payload_and_freezes_threads(self) -> None:
        adapter = SAFE_BASELINE_ADAPTERS["zpaq"]
        self.assertEqual(
            adapter.encode_args,
            (
                "a",
                "{archive}",
                "{input}",
                "-to",
                "payload.bin",
                "-method",
                "5",
                "-threads",
                "1",
                "-noattributes",
            ),
        )
        self.assertEqual(
            adapter.decode_args,
            (
                "x",
                "{archive}",
                "payload.bin",
                "-to",
                "{restored}",
                "-threads",
                "1",
                "-force",
            ),
        )
        self.assertFalse(adapter.encode_stdout)
        self.assertFalse(adapter.decode_stdout)

    def test_runtime_profile_mismatch_makes_native_cell_unavailable(self) -> None:
        bad_codec = self.root / "fake-codec-bad-profile"
        bad_codec.write_text(
            self.codec.read_text(encoding="utf-8").replace(
                '"fast": 256',
                '"fast": 257',
                1,
            ),
            encoding="utf-8",
        )
        bad_codec.chmod(0o755)
        config_root = pathlib.Path(__file__).resolve().parents[2] / "configs"
        [spec] = build_native_specs(
            ["fast"],
            binary=bad_codec,
            config_root=config_root,
        )
        self.assertIn("runtime profile/TOML mismatch", spec.unavailable_reason)
        self.assertEqual(spec.runtime_profile_sha256, "")
        self.assertIsNone(spec.runtime_profile)

    def spec(
        self,
        *,
        native: bool = False,
        executable: pathlib.Path | None = None,
    ) -> CodecSpec:
        executable = executable or self.codec
        return CodecSpec(
            codec=Codec(
                "mathsvg" if native else "fake",
                "fast-v1" if native else "fixture",
                1,
            ),
            native_mathsvg=native,
            profile="fast" if native else "",
            executable=executable,
            executable_sha256=sha256_file(executable),
            version="test",
            adapter=Adapter(
                encode_args=("encode", "{input}", "{archive}"),
                decode_args=("decode", "{archive}", "{restored}"),
                encode_stdout=False,
                decode_stdout=False,
            ),
            config_sha256="2" * 64,
            unavailable_reason="",
        )

    def trial(self, codec: Codec, repetition: int = 0) -> Trial:
        return Trial(
            block=repetition,
            order=0,
            repetition=repetition,
            workload=self.prepared.workload,
            codec=codec,
            warmup=False,
        )

    def test_fake_codec_creates_real_deterministic_roundtrip(self) -> None:
        spec = self.spec()
        first = execute_trial(
            self.context,
            self.trial(spec.codec, 0),
            self.prepared,
            spec,
            self.root,
        )
        second = execute_trial(
            self.context,
            self.trial(spec.codec, 1),
            self.prepared,
            spec,
            self.root,
        )
        self.assertEqual(first.status, "ok")
        self.assertTrue(first.roundtrip_ok)
        self.assertTrue(first.deterministic_archive)
        self.assertTrue(second.deterministic_archive)
        self.assertEqual(first.archive_sha256, second.archive_sha256)
        self.assertGreater(first.compression_wall_ns or 0, 0)
        self.assertGreater(first.peak_rss_bytes or 0, 0)

    def test_native_missing_exact_breakdown_fails_explicitly(self) -> None:
        spec = self.spec(native=True)
        record = execute_trial(
            self.context,
            self.trial(spec.codec),
            self.prepared,
            spec,
            self.root,
        )
        self.assertEqual(record.status, "failed")
        self.assertIn("lacks exact procedural_breakdown", record.error)
        self.assertIn("round-trip verified", record.error)
        self.assertTrue(record.roundtrip_ok)

    def test_native_exact_required_breakdown_succeeds(self) -> None:
        full_codec = self.root / "fake-codec-full"
        full_codec.write_text(FAKE_CODEC, encoding="utf-8")
        full_codec.chmod(0o755)
        spec = self.spec(native=True, executable=full_codec)
        record = execute_trial(
            self.context,
            self.trial(spec.codec),
            self.prepared,
            spec,
            self.root,
        )
        record.validate()
        self.assertEqual(record.status, "ok")
        self.assertTrue(record.roundtrip_ok)
        self.assertEqual(
            record.container_overhead_bytes,
            record.archive_bytes,
        )
        self.assertIsNone(record.literal_only_archive_bytes)
        self.assertIsNone(record.coordinate_saved_bytes)
        self.assertIsNone(record.search_ns)

    def test_unavailable_codec_is_retained_without_execution(self) -> None:
        spec = CodecSpec(
            codec=Codec("missing", "missing", 1),
            native_mathsvg=False,
            profile="",
            executable=None,
            executable_sha256="",
            version="",
            adapter=None,
            config_sha256="2" * 64,
            unavailable_reason="not installed",
        )
        record = execute_trial(
            self.context,
            self.trial(spec.codec),
            self.prepared,
            spec,
            self.root,
        )
        self.assertEqual(record.status, "unavailable")
        self.assertEqual(record.error, "not installed")

    def test_timeout_is_recorded_and_process_group_is_killed(self) -> None:
        spec = self.spec()
        spec = CodecSpec(
            codec=spec.codec,
            native_mathsvg=False,
            profile="",
            executable=spec.executable,
            executable_sha256=spec.executable_sha256,
            version=spec.version,
            adapter=Adapter(
                encode_args=("sleep",),
                decode_args=spec.adapter.decode_args if spec.adapter else (),
                encode_stdout=False,
                decode_stdout=False,
            ),
            config_sha256=spec.config_sha256,
            unavailable_reason="",
        )
        self.context.compression_timeout_seconds = 0.05
        record = execute_trial(
            self.context,
            self.trial(spec.codec),
            self.prepared,
            spec,
            self.root,
        )
        self.assertEqual(record.status, "timeout")
        self.assertIn("wall timeout", record.error)
        self.assertIsNotNone(record.compression_wall_ns)
        self.assertIsNone(record.compression_cpu_ns)
        self.assertIsNone(record.compression_peak_rss_bytes)

    def test_nonzero_exit_retains_measurement_and_diagnostic(self) -> None:
        spec = self.spec()
        spec = CodecSpec(
            codec=spec.codec,
            native_mathsvg=False,
            profile="",
            executable=spec.executable,
            executable_sha256=spec.executable_sha256,
            version=spec.version,
            adapter=Adapter(
                encode_args=("unknown-command",),
                decode_args=spec.adapter.decode_args if spec.adapter else (),
                encode_stdout=False,
                decode_stdout=False,
            ),
            config_sha256=spec.config_sha256,
            unavailable_reason="",
        )
        record = execute_trial(
            self.context,
            self.trial(spec.codec),
            self.prepared,
            spec,
            self.root,
        )
        self.assertEqual(record.status, "failed")
        self.assertIn("exited 4", record.error)
        self.assertIsNotNone(record.compression_wall_ns)
        self.assertIsNotNone(record.compression_peak_rss_bytes)


if __name__ == "__main__":
    unittest.main()
