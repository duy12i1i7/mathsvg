from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mathzip_bench.auxiliary import generate_auxiliary_corpora
from mathzip_bench.verification import verify_generated_corpora


class AuxiliaryCorpusTests(unittest.TestCase):
    def test_mixed_and_snapshot_corpora_are_self_verifying(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "generated"
            result = generate_auxiliary_corpora(output, seed=123)
            paths = {
                entry["path"] for entry in result["mixed"]["entries"]
            }
            for required in (
                "files/sample.txt",
                "files/sample.sqlite",
                "files/sample.pdf",
                "files/sample.bmp",
                "files/sample.wav",
                "files/sample.png",
                "files/sample.jpg",
                "files/sample.zip",
                "files/sample.gz",
                "files/sample.xz",
                "files/sample.mp4",
                "files/random.bin",
            ):
                self.assertIn(required, paths)
            self.assertEqual(len(result["git_snapshots"]["versions"]), 3)
            errors, warnings = verify_generated_corpora(output)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])
