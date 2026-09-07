from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mathzip_bench.config import ConfigError, load_config, repository_root


class ConfigTests(unittest.TestCase):
    def test_repository_root_is_found_from_entrypoint(self) -> None:
        expected = Path(__file__).resolve().parents[2]
        self.assertEqual(repository_root(expected / "python" / "run_benchmarks.py"), expected)

    def test_json_compatible_yaml_needs_no_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            path.write_text(
                json.dumps({"schema_version": 1, "profile": "$MISSING_VALUE"}),
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config["schema_version"], 1)

    def test_wrong_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text('{"schema_version": 99}', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_repository_profiles_declare_input_rights_and_provenance(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for path in sorted((root / "configs").glob("*.yaml")):
            with self.subTest(config=path.name):
                config = load_config(path)
                for index, source in enumerate(config.get("inputs", [])):
                    self.assertTrue(
                        source.get("input_license"),
                        f"{path.name} inputs[{index}] has no input_license",
                    )
                    self.assertTrue(
                        source.get("input_provenance"),
                        f"{path.name} inputs[{index}] has no input_provenance",
                    )

    def test_full_pipeline_requires_baselines_and_allows_recorded_failures(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "full.yaml")
        self.assertEqual(
            config["benchmark"]["mathzip_metrics_mode"],
            "inline",
        )
        self.assertEqual(config["benchmark"]["timeout_seconds"], 5400)

        script_path = root / "scripts" / "benchmark_full.sh"
        script = script_path.read_text(encoding="utf-8")
        self.assertTrue(script_path.stat().st_mode & 0o111)
        self.assertIn("for tool in gzip bzip2 xz zstd lz4 brotli", script)
        self.assertIn('command -v "$tool"', script)
        self.assertIn("command -v 7z", script)
        self.assertIn("command -v 7zz", script)
        self.assertIn("python3 python/run_benchmarks.py", script)
        self.assertIn("python3 python/verify_results.py", script)
        self.assertGreaterEqual(script.count("--allow-failures"), 3)

    def test_silesia_profile_covers_every_manifest_member(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "silesia.yaml")
        manifest = json.loads(
            (root / "datasets" / "manifests" / "silesia.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(config["profile"], "silesia")
        self.assertEqual(len(config["inputs"]), 1)
        self.assertEqual(
            sorted(config["inputs"][0]["patterns"]),
            sorted(manifest["archive"]["members"]),
        )
        self.assertEqual(config["benchmark"]["warmups"], 1)
        self.assertGreaterEqual(config["benchmark"]["repeats"], 3)

    def test_ablation_zstd_variant_is_single_factor(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "ablation.yaml")
        self.assertEqual(config["benchmark"]["mathzip_metrics_mode"], "inline")
        entries = {
            entry["name"]: entry
            for entry in config["codecs"]
            if isinstance(entry, dict)
        }
        custom = entries["ablation-13-custom-residual"]
        zstd = entries["ablation-14-zstd-residual"]
        self.assertEqual(custom["base"], zstd["base"])
        self.assertEqual(
            custom["compress_args"],
            ["--residual-coders", "raw,rle,zero-run,sparse,bit-pack"],
        )
        self.assertEqual(zstd["compress_args"], ["--residual-coders", "zstd"])

    def test_ablation_no_raw_fallback_keeps_the_base_model_catalog(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "ablation.yaml")
        entries = {
            entry["name"]: entry
            for entry in config["codecs"]
            if isinstance(entry, dict)
        }
        no_fallback = entries["ablation-17-no-raw-fallback"]
        self.assertEqual(no_fallback["base"], "mathzip-balanced")
        self.assertEqual(no_fallback["compress_args"], ["--no-raw-fallback"])

    def test_residual_ablation_changes_only_the_residual_coder(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "residual-ablation.yaml")
        historical = load_config(root / "configs" / "ablation.yaml")
        self.assertEqual(config["benchmark"]["mathzip_metrics_mode"], "inline")
        self.assertEqual(config["benchmark"], historical["benchmark"])
        self.assertEqual(config["inputs"], historical["inputs"])
        entries = {
            entry["name"]: entry
            for entry in config["codecs"]
            if isinstance(entry, dict)
        }
        custom = entries["mathzip-balanced-custom-residual"]
        zstd = entries["mathzip-balanced-zstd-residual"]
        self.assertEqual(custom["base"], zstd["base"])
        self.assertEqual(
            custom["compress_args"],
            ["--residual-coders", "raw,rle,zero-run,sparse,bit-pack"],
        )
        self.assertEqual(zstd["compress_args"], ["--residual-coders", "zstd"])
        self.assertEqual(
            {
                key: value
                for key, value in custom.items()
                if key not in {"name", "variant", "compress_args"}
            },
            {
                key: value
                for key, value in zstd.items()
                if key not in {"name", "variant", "compress_args"}
            },
        )

    def test_git_copy_ablation_changes_only_the_copy_model(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "git-copy-ablation.yaml")
        entries = {
            entry["name"]: entry
            for entry in config["codecs"]
            if isinstance(entry, dict)
        }
        enabled = entries["mathzip-balanced-copy-on"]
        disabled = entries["mathzip-balanced-copy-off"]
        self.assertEqual(enabled["base"], disabled["base"])

        def parsed(arguments: list[str]) -> tuple[dict[str, str], bool]:
            options: dict[str, str] = {}
            no_copy = False
            index = 0
            while index < len(arguments):
                if arguments[index] == "--no-copy-model":
                    no_copy = True
                    index += 1
                    continue
                options[arguments[index]] = arguments[index + 1]
                index += 2
            return options, no_copy

        enabled_options, enabled_no_copy = parsed(enabled["compress_args"])
        disabled_options, disabled_no_copy = parsed(disabled["compress_args"])
        self.assertEqual(enabled_options, disabled_options)
        self.assertIn("copy", enabled_options["--models"].split(","))
        self.assertFalse(enabled_no_copy)
        self.assertTrue(disabled_no_copy)

    def test_recursive_ablation_changes_only_the_segmentation_search(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "recursive-ablation.yaml")
        entries = {
            entry["name"]: entry
            for entry in config["codecs"]
            if isinstance(entry, dict)
        }
        adaptive = entries["mathzip-fast-adaptive"]
        recursive = entries["mathzip-fast-recursive"]
        self.assertEqual(adaptive["base"], recursive["base"])
        self.assertEqual(
            adaptive["compress_args"],
            ["--segmentation", "adaptive"],
        )
        self.assertEqual(
            recursive["compress_args"],
            [
                "--segmentation",
                "recursive",
                "--max-tree-depth",
                "4",
            ],
        )
        self.assertEqual(
            {
                key: value
                for key, value in adaptive.items()
                if key not in {"name", "variant", "compress_args"}
            },
            {
                key: value
                for key, value in recursive.items()
                if key not in {"name", "variant", "compress_args"}
            },
        )

    def test_segmentation_ablation_covers_required_modes_and_fixed_sizes(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "segmentation-ablation.yaml")
        self.assertEqual(config["profile"], "segmentation-ablation")
        self.assertEqual(config["benchmark"]["warmups"], 1)
        self.assertGreaterEqual(config["benchmark"]["repeats"], 3)
        self.assertEqual(config["benchmark"]["threads"], [1])

        entries = {
            entry["name"]: entry
            for entry in config["codecs"]
            if isinstance(entry, dict)
        }
        mathzip_entries = list(entries.values())
        self.assertEqual(
            {entry["base"] for entry in mathzip_entries},
            {"mathzip-fast"},
        )

        fixed_sizes = {
            entry["compress_args"][3]
            for entry in mathzip_entries
            if entry["compress_args"][:2] == ["--segmentation", "fixed"]
        }
        self.assertEqual(
            fixed_sizes,
            {"256", "1024", "4096", "16384", "65536", "262144"},
        )
        self.assertEqual(
            entries["mathzip-fast-change-point-4kib"]["compress_args"],
            ["--segmentation", "change-point", "--segment-size", "4096"],
        )
        self.assertEqual(
            entries["mathzip-fast-adaptive-4kib"]["compress_args"],
            ["--segmentation", "adaptive", "--segment-size", "4096"],
        )
        self.assertIn("zstd-fast", config["codecs"])

    def test_segmentation_ablation_script_runs_strict_verified_pipeline(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        script_path = root / "scripts" / "benchmark_segmentation_ablation.sh"
        script = script_path.read_text(encoding="utf-8")
        self.assertTrue(script_path.stat().st_mode & 0o111)
        self.assertIn("command -v zstd", script)
        self.assertIn("configs/segmentation-ablation.yaml", script)
        self.assertIn(
            "--synthetic-manifest datasets/synthetic/manifest.json",
            script,
        )
        self.assertIn(
            "benchmarks/results/segmentation-ablation/latest.json",
            script,
        )
        self.assertGreaterEqual(script.count("--strict"), 2)

    def test_max_timeout_regression_replays_both_historical_failures(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "max-timeout-regression.yaml")
        self.assertEqual(config["profile"], "max-timeout-regression")
        self.assertEqual(config["codecs"], ["mathzip-max", "zstd-max"])
        self.assertEqual(config["benchmark"]["warmups"], 1)
        self.assertEqual(config["benchmark"]["repeats"], 3)
        self.assertEqual(config["benchmark"]["timeout_seconds"], 600)
        self.assertTrue(config["benchmark"]["collect_mathzip_inspection"])
        self.assertEqual(
            config["inputs"][0]["patterns"],
            ["kennedy.xls", "ptt5"],
        )

    def test_max_timeout_regression_script_runs_strict_verified_pipeline(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        script_path = root / "scripts" / "benchmark_max_timeout_regression.sh"
        script = script_path.read_text(encoding="utf-8")
        self.assertTrue(script_path.stat().st_mode & 0o111)
        self.assertIn("command -v zstd", script)
        self.assertIn("configs/max-timeout-regression.yaml", script)
        self.assertIn(
            "benchmarks/results/max-timeout-regression/latest.json",
            script,
        )
        self.assertGreaterEqual(script.count("--strict"), 2)

    def test_bit_plane_ablation_changes_only_the_bit_plane_representation(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "bit-plane-ablation.yaml")
        self.assertEqual(config["profile"], "bit-plane-ablation")
        self.assertEqual(config["benchmark"]["warmups"], 1)
        self.assertEqual(config["benchmark"]["repeats"], 3)
        self.assertEqual(config["benchmark"]["threads"], [1])
        self.assertTrue(config["benchmark"]["collect_mathzip_inspection"])

        entries = {
            entry["name"]: entry
            for entry in config["codecs"]
            if isinstance(entry, dict)
        }
        packed = entries["mathzip-balanced-bit-plane-packed-v1"]
        independent = entries["mathzip-balanced-bit-plane-independent-v2"]
        self.assertEqual(packed["base"], independent["base"])

        def parsed(arguments: list[str]) -> tuple[dict[str, str], set[str]]:
            values: dict[str, str] = {}
            flags: set[str] = set()
            index = 0
            while index < len(arguments):
                argument = arguments[index]
                if argument == "--no-raw-fallback":
                    flags.add(argument)
                    index += 1
                    continue
                values[argument] = arguments[index + 1]
                index += 2
            return values, flags

        packed_values, packed_flags = parsed(packed["compress_args"])
        independent_values, independent_flags = parsed(
            independent["compress_args"]
        )
        self.assertEqual(packed_flags, {"--no-raw-fallback"})
        self.assertEqual(independent_flags, packed_flags)
        self.assertEqual(
            {
                key: value
                for key, value in packed_values.items()
                if key != "--transforms"
            },
            {
                key: value
                for key, value in independent_values.items()
                if key != "--transforms"
            },
        )
        self.assertEqual(packed_values["--transforms"], "bit-plane-packed")
        self.assertEqual(
            independent_values["--transforms"], "bit-plane-independent"
        )
        self.assertIn("zstd-default", config["codecs"])

        patterns = config["inputs"][0]["patterns"]
        self.assertTrue(any("s0000000031_" in pattern for pattern in patterns))
        self.assertTrue(any("s0000065536_" in pattern for pattern in patterns))
        self.assertTrue(
            all(
                "n000p000_" in pattern or "n001p000_" in pattern
                for pattern in patterns
            )
        )

    def test_bit_plane_ablation_script_runs_strict_verified_pipeline(self) -> None:
        root = Path(__file__).resolve().parents[2]
        script_path = root / "scripts" / "benchmark_bit_plane_ablation.sh"
        script = script_path.read_text(encoding="utf-8")
        self.assertTrue(script_path.stat().st_mode & 0o111)
        self.assertIn("command -v zstd", script)
        self.assertIn("configs/bit-plane-ablation.yaml", script)
        self.assertIn(
            "--synthetic-manifest datasets/synthetic/manifest.json",
            script,
        )
        self.assertIn(
            "benchmarks/results/bit-plane-ablation/latest.json",
            script,
        )
        self.assertGreaterEqual(script.count("--strict"), 2)
