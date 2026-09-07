from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mathzip_bench.system_info import (
    collect_source_metadata,
    collect_system_metadata,
)


class SystemMetadataTests(unittest.TestCase):
    def test_unversioned_source_gets_exact_manifest_and_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.py").write_text("print('source')\n", encoding="utf-8")
            (root / "target").mkdir()
            (root / "target" / "artifact.bin").write_bytes(b"excluded")
            metadata = collect_source_metadata(root)
            self.assertIsNone(metadata["source_revision"])
            self.assertIsInstance(metadata["source_tree_manifest"], list)
            self.assertEqual(metadata["source_file_count"], 1)
            self.assertEqual(
                metadata["source_tree_manifest"][0]["path"], "source.py"
            )
            self.assertEqual(len(metadata["source_tree_sha256"]), 64)

    def test_environment_has_explicit_optional_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metadata = collect_system_metadata((), Path(temporary))
            for field in (
                "logical_cores",
                "physical_cores",
                "cpu_affinity",
                "cpu_governors",
                "ram_bytes",
                "swap_bytes",
                "storage",
                "power_supplies",
                "container",
                "relevant_environment",
                "metric_unavailable",
            ):
                self.assertIn(field, metadata)
            self.assertIn("free_bytes", metadata["storage"])
            self.assertIn("image_digest", metadata["container"])
