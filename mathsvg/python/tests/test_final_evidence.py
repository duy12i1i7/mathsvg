from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


class FinalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[3]
        cls.manifest_path = (
            cls.repo_root
            / "mathsvg/results/manifests/final-local-evidence.json"
        )
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))

    def test_declared_artifacts_match_size_and_sha256(self) -> None:
        declared = [
            *self.manifest["canonical_aliases"],
            *self.manifest["gate_artifacts"],
        ]
        for row in declared:
            with self.subTest(path=row["path"]):
                data = (self.repo_root / row["path"]).read_bytes()
                self.assertEqual(len(data), row["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])

    def test_canonical_aliases_are_byte_identical(self) -> None:
        for row in self.manifest["canonical_aliases"]:
            with self.subTest(path=row["path"]):
                self.assertEqual(
                    (self.repo_root / row["path"]).read_bytes(),
                    (self.repo_root / row["source"]).read_bytes(),
                )

    def test_section_29_outputs_exist(self) -> None:
        required = [
            "mathsvg/results/raw/benchmark.jsonl",
            "mathsvg/results/summary/benchmark.csv",
            "mathsvg/results/summary/procedural-breakdown.csv",
            "mathsvg/results/oracle/search.csv",
            "mathsvg/results/oracle/coordinate.csv",
            "mathsvg/results/oracle/segmentation.csv",
            "mathsvg/results/oracle/residual.csv",
            "mathsvg/results/oracle/symbolic.csv",
            "mathsvg/results/oracle/dag-sharing.csv",
            "mathsvg/results/oracle/external-gap.csv",
            "mathsvg/results/ablation/ablation.csv",
            "mathsvg/results/dominance/per-file.csv",
            "mathsvg/results/dominance/per-corpus.csv",
            "mathsvg/results/dominance/pareto-envelope.csv",
            "mathsvg/results/dominance/failures.csv",
            "mathsvg/results/determinism/results.csv",
        ]
        for relative in required:
            with self.subTest(path=relative):
                self.assertTrue((self.repo_root / relative).is_file())
        self.assertTrue(any((self.repo_root / "mathsvg/results/profiling").iterdir()))
        self.assertTrue(any((self.repo_root / "mathsvg/results/plots").iterdir()))

    def test_manifest_never_promotes_local_evidence_to_acceptance(self) -> None:
        self.assertEqual(self.manifest["status"], "acceptance_incomplete")
        self.assertFalse(self.manifest["holdout_payload_inspected"])
        self.assertFalse(self.manifest["acceptance"]["dominance_certificate_pass"])
        self.assertFalse(
            self.manifest["acceptance"][
                "finite_oracle_proves_no_remaining_headroom"
            ]
        )
        self.assertFalse(self.manifest["acceptance"]["holdout_authorized"])


if __name__ == "__main__":
    unittest.main()
