from __future__ import annotations

import pathlib
import tempfile
import unittest

from mathsvg.python.benchmarks.config import (
    ConfigError,
    load_ablation_catalog,
    load_all,
    load_profile,
)


class ConfigTests(unittest.TestCase):
    def test_checked_in_profiles_validate(self) -> None:
        profiles = load_all(pathlib.Path("mathsvg/configs"))
        self.assertEqual(
            set(profiles), {"fast", "balanced", "max", "structured", "repository"}
        )

    def test_equal_rss_gate_is_rejected(self) -> None:
        source = pathlib.Path("mathsvg/configs/fast/profile.toml").read_text(
            encoding="utf-8"
        )
        source = source.replace(
            "encoder_rss_bytes = 268435455",
            "encoder_rss_bytes = 268435456",
        )
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "profile.toml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "not strict"):
                load_profile(path)

    def test_non_divisible_microblock_is_rejected(self) -> None:
        source = pathlib.Path("mathsvg/configs/fast/profile.toml").read_text(
            encoding="utf-8"
        )
        source = source.replace("microblock_bytes = 4096", "microblock_bytes = 4095")
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "profile.toml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "multiple"):
                load_profile(path)

    def test_ablation_catalog_contains_only_real_runtime_engines(self) -> None:
        catalog = load_ablation_catalog(
            pathlib.Path("mathsvg/configs/ablation/catalog.toml")
        )
        self.assertEqual(
            catalog,
            {
                "no-whole-block-entropy": ("whole-block-entropy",),
                "no-whole-block-functions": ("whole-block-functions",),
                "no-interval-functions": ("interval-functions",),
                "no-coordinate": ("coordinates",),
                "no-interval-functions-or-coordinate": (
                    "coordinates",
                    "interval-functions",
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()
