from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from datasets.manifest import FIELDS, load_manifest  # noqa: E402
from oracle.analyze import DATASET_FIELDS, stop_policy_rows, write_csv  # noqa: E402
from oracle.model import (  # noqa: E402
    best_coordinate,
    choose_representation,
    coordinate_candidates,
    dag_sharing_oracle,
    decode_exact,
    decode_uleb,
    encode_uleb,
    entropy_lower_bound,
    family_candidate,
    literal_node,
    segmentation_oracle,
)


class UlebTests(unittest.TestCase):
    def test_u64_boundaries_round_trip_canonically(self) -> None:
        values = (
            0,
            1,
            0x7F,
            0x80,
            0x3FFF,
            0x4000,
            (1 << 32) - 1,
            (1 << 64) - 1,
        )
        for value in values:
            encoded = encode_uleb(value)
            self.assertEqual(decode_uleb(encoded), (value, len(encoded)))

    def test_noncanonical_and_overflow_are_rejected(self) -> None:
        for invalid in (
            b"\x80\x00",
            b"\x81\x00",
            b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\x02",
        ):
            with self.assertRaises(ValueError):
                decode_uleb(invalid)


class CandidateTests(unittest.TestCase):
    def test_exact_generator_families_round_trip(self) -> None:
        constant = b"\xaa" * 512
        linear = bytes((17 * index + 29) & 0xFF for index in range(512))
        periodic = (b"\x01\x02\x03\x04\x09\x10\x19" * 74)[:512]
        fibonacci = bytearray((1, 1))
        while len(fibonacci) < 512:
            fibonacci.append((fibonacci[-1] + fibonacci[-2]) & 0xFF)
        cases = (
            ("const", constant),
            ("linear", linear),
            ("periodic", periodic),
            ("recurrence", bytes(fibonacci)),
        )
        for family, data in cases:
            candidate = family_candidate(data, family)
            self.assertNotEqual(candidate.name, "literal")
            self.assertEqual(decode_exact(candidate.blob), data)
            self.assertLess(candidate.size, len(data))

    def test_literal_is_a_universal_upper_bound(self) -> None:
        generator = random.Random(0x4D535647)
        for length in (0, 1, 31, 256, 1024):
            data = bytes(generator.randrange(256) for _ in range(length))
            literal_size = len(literal_node(data))
            deep = choose_representation(data, 2)
            self.assertEqual(decode_exact(deep.blob), data)
            self.assertLessEqual(deep.size, literal_size)
            self.assertLessEqual(deep.size, choose_representation(data, 0).size)

    def test_recursive_residual_is_exact(self) -> None:
        data = bytearray((17 * index + 29) & 0xFF for index in range(1024))
        for position, value in ((7, 1), (128, 2), (999, 3)):
            data[position] ^= value
        depth0 = choose_representation(bytes(data), 0)
        depth2 = choose_representation(bytes(data), 2)
        self.assertEqual(decode_exact(depth2.blob), bytes(data))
        self.assertLessEqual(depth2.size, depth0.size)


class CoordinateAndTopologyTests(unittest.TestCase):
    def test_every_coordinate_candidate_has_an_exact_inverse(self) -> None:
        data = bytes((index * 37 + index // 8) & 0xFF for index in range(257))
        candidates = coordinate_candidates(data, 0)
        self.assertGreaterEqual(len(candidates), 7)
        for candidate in candidates:
            self.assertEqual(decode_exact(candidate.blob), data, candidate.name)
        winner = best_coordinate(data, 0)
        self.assertEqual(decode_exact(winner.blob), data)

    def test_segmentation_dp_serializes_complete_concat_cost(self) -> None:
        left = b"\x00" * 256
        right = bytes((17 * index + 29) & 0xFF for index in range(256))
        data = left + right
        result = segmentation_oracle(data, quantum=256, residual_depth=0)
        self.assertEqual(decode_exact(result.blob), data)
        self.assertEqual(result.boundaries, (0, 256, 512))
        unsplit = choose_representation(data, 0)
        self.assertLess(result.size, unsplit.size)

    def test_dag_activation_enumeration_includes_all_metadata(self) -> None:
        generator = random.Random(20260728)
        block = bytes(generator.randrange(256) for _ in range(512))
        result = dag_sharing_oracle([block, block, block, block])
        self.assertEqual(decode_exact(result.oracle_blob), block * 4)
        self.assertEqual(result.active_definitions, 1)
        self.assertLess(result.oracle_size, result.inline_size)


class EvidenceTests(unittest.TestCase):
    def test_entropy_bound_known_cases(self) -> None:
        self.assertEqual(entropy_lower_bound(b"\x00" * 100), (0.0, 0))
        bits, byte_bound = entropy_lower_bound(bytes((0, 1)) * 8)
        self.assertEqual(bits, 16.0)
        self.assertEqual(byte_bound, 2)

    def test_csv_writer_is_byte_deterministic(self) -> None:
        fields = ("a", "b")
        rows = ({"a": 2, "b": "z"}, {"a": 1, "b": "x"})
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.csv"
            second = Path(directory) / "second.csv"
            write_csv(first, fields, rows)
            write_csv(second, fields, reversed(rows))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).digest(),
                hashlib.sha256(second.read_bytes()).digest(),
            )

    def test_oracle_dataset_header_matches_governance_schema(self) -> None:
        self.assertEqual(DATASET_FIELDS, FIELDS)
        row = {
            "split": "development",
            "dataset_id": "legacy-real",
            "split_group": "legacy-group",
            "origin": "real",
            "primary": True,
            "domain": "test",
            "source": "https://example.invalid/source",
            "license": "UNKNOWN",
            "sha256": "0" * 64,
            "bytes": 0,
            "sealed": False,
            "path": "datasets/example.bin",
            "legacy_observed": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.csv"
            write_csv(manifest, DATASET_FIELDS, [row])
            entries = load_manifest(manifest)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].split, "development")
            self.assertTrue(entries[0].legacy_observed)

    def test_stop_policy_does_not_turn_incomplete_zero_into_global_proof(
        self,
    ) -> None:
        coordinate = [
            {
                "origin": "real",
                "baseline_bytes": 1000,
                "oracle_bytes": 1000,
                "headroom_bytes": 0,
                "complete_within_declared_catalogue": False,
            }
        ]
        ordinary = [
            {
                "origin": "real",
                "baseline_bytes": 1000,
                "oracle_bytes": 1000,
                "headroom_bytes": 0,
                "complete_within_declared_catalogue": False,
            }
        ]
        residual = [
            {
                "row_kind": "native_depth_winner",
                "depth_limit": 2,
                "origin": "real",
                "baseline_depth0_bytes": 1000,
                "complete_candidate_bytes": 1000,
                "headroom_bytes": 0,
                "complete_within_declared_catalogue": False,
            }
        ]
        dag = [
            {
                "origin": "real",
                "inline_bytes": 1000,
                "oracle_bytes": 1000,
                "headroom_bytes": 0,
                "complete_within_declared_catalogue": False,
            }
        ]
        rows = stop_policy_rows(
            coordinate, ordinary, residual, ordinary, dag
        )
        by_algorithm = {row["algorithm"]: row for row in rows}
        self.assertEqual(
            by_algorithm["coordinate-basis"]["decision"],
            "stop_emission_zero_incremental_real_wins",
        )
        self.assertEqual(
            by_algorithm["segmentation-256b"]["decision"],
            "retain_core_no_deeper_search_bounded_inconclusive",
        )
        for algorithm in (
            "recursive-residual-depth2",
            "symbolic-depth2",
            "dag-sharing",
        ):
            self.assertEqual(
                by_algorithm[algorithm]["decision"],
                "profile_disabled_bounded_inconclusive",
            )
            self.assertIn(
                "not a global no-headroom proof",
                by_algorithm[algorithm]["reason"],
            )


class GeneratedArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[3]
        cls.oracle_root = cls.repo_root / "mathsvg/results/oracle"

    def test_required_aliases_are_byte_identical(self) -> None:
        aliases = {
            "search.csv": "search-oracle.csv",
            "coordinate.csv": "coordinate-oracle.csv",
            "segmentation.csv": "segmentation-oracle.csv",
            "residual.csv": "residual-oracle.csv",
            "symbolic.csv": "symbolic-headroom.csv",
            "dag-sharing.csv": "dag-sharing-headroom.csv",
        }
        for alias, canonical in aliases.items():
            self.assertEqual(
                (self.oracle_root / alias).read_bytes(),
                (self.oracle_root / canonical).read_bytes(),
            )

    def test_bounded_dp_never_loses_to_its_unsplit_state(self) -> None:
        path = self.oracle_root / "segmentation-oracle.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(rows)
        self.assertLessEqual(
            {row["status"] for row in rows},
            {"ok", "bounded_incomplete"},
        )
        for row in rows:
            self.assertLessEqual(
                int(row["oracle_bytes"]),
                int(row["baseline_bytes"]),
                row["input_id"],
            )

    def test_native_symbolic_oracle_uses_complete_archive_rows(self) -> None:
        path = self.oracle_root / "symbolic-headroom.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(rows)
        self.assertLessEqual(
            {row["status"] for row in rows},
            {"ok", "bounded_incomplete"},
        )
        for row in rows:
            self.assertTrue(row["oracle_bytes"])
            self.assertLessEqual(
                int(row["oracle_bytes"]),
                int(row["baseline_bytes"]),
                row["input_id"],
            )
            self.assertNotIn("holdout", row["source"].lower())

    def test_coordinate_basis_stop_scan_is_real_and_has_no_incremental_win(
        self,
    ) -> None:
        path = self.oracle_root / "coordinate-oracle.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 73)
        self.assertEqual({row["origin"] for row in rows}, {"real"})
        self.assertTrue(
            all(row["basis_incremental_winner"] == "false" for row in rows)
        )

    def test_native_probe_declares_current_entropy_and_no_holdout(self) -> None:
        payload = json.loads(
            (self.oracle_root / "native-development-oracle.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(payload["holdout_payload_inspected"])
        self.assertIn("canonical-huffman", payload["entropy_catalog"])
        self.assertIn("lz-huffman", payload["entropy_catalog"])
        self.assertEqual(len(payload["samples"]), 13)
        self.assertEqual(len(payload["coordinate_basis_blocks"]), 73)

    def test_stop_policy_is_explicit_and_never_claims_holdout(self) -> None:
        path = self.oracle_root / "stop-policy.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(
            {row["algorithm"] for row in rows},
            {
                "coordinate-basis",
                "segmentation-256b",
                "recursive-residual-depth2",
                "symbolic-depth2",
                "dag-sharing",
            },
        )
        self.assertTrue(
            all(row["status"] == "development_only" for row in rows)
        )
        self.assertTrue(
            all(
                row["holdout_status"] == "not_opened_not_evaluated"
                for row in rows
            )
        )
        self.assertTrue(
            all(row["minimum_gain_fraction"] == "0.005000000" for row in rows)
        )

    def test_manifest_hashes_every_declared_output(self) -> None:
        manifest_path = (
            self.repo_root / "mathsvg/results/manifests/oracle-manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "mathsvg-development-oracle-v2")
        self.assertFalse(manifest["holdout_payload_inspected"])
        self.assertEqual(manifest["split"], "development")
        self.assertIn(
            "mathsvg/crates/mathsvg-entropy/src/lib.rs",
            manifest["native_source_sha256"],
        )
        probe = json.loads(
            (self.oracle_root / "native-development-oracle.json").read_text(
                encoding="utf-8"
            )
        )
        probe_digests = {
            row["input_id"]: row["sample_sha256"] for row in probe["samples"]
        }
        manifest_digests = {
            row["input_id"]: row["sample_sha256"] for row in manifest["inputs"]
        }
        self.assertEqual(probe_digests, manifest_digests)
        for output in manifest["outputs"]:
            data = (self.repo_root / output["path"]).read_bytes()
            self.assertEqual(len(data), output["bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), output["sha256"])

    def test_current_external_evidence_has_captured_provenance(self) -> None:
        manifest = json.loads(
            (
                self.repo_root
                / "mathsvg/results/manifests/oracle-manifest.json"
            ).read_text(encoding="utf-8")
        )
        evidence = manifest["external_evidence"]
        self.assertEqual(
            evidence["kind"], "current-native-development-pilot"
        )
        raw_path = self.repo_root / evidence["source"]
        self.assertTrue(raw_path.is_file())
        self.assertNotIn("holdout", evidence["source"].lower())
        self.assertEqual(
            hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            evidence["raw_sha256"],
        )
        run_path = raw_path.with_name(f"{raw_path.stem}-run.json")
        run = json.loads(run_path.read_text(encoding="utf-8"))
        self.assertEqual(run["manifest"]["split"], "development")
        self.assertTrue(run["result"]["complete"])
        self.assertFalse(run["holdout_payload_opened"])
        self.assertEqual(run["result"]["raw_jsonl"], evidence["source"])
        self.assertEqual(
            run["runner_sha256"], evidence["captured_runner_sha256"]
        )
        self.assertTrue(evidence["current_workspace_runner_not_inferred"])

        with (self.oracle_root / "external-gap.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(
            any(
                row["scope"] == "all"
                and row["baseline_codec"] == "zstd-default"
                and row["status"] == "current_native_development"
                for row in rows
            )
        )
        self.assertTrue(
            any(
                row["scope"] == "all"
                and row["baseline_codec"] == "brotli"
                and row["status"] == "not_run_current_pilot"
                for row in rows
            )
        )


if __name__ == "__main__":
    unittest.main()
