import copy
import csv
import pathlib
import tempfile
import unittest

from mathsvg.python.oracle.lz_parser import (
    LzOracleError,
    POLICIES,
    PROVENANCE_SCHEMA,
    SELECTION_POLICY,
    TIMED_POLICIES,
    build_provenance,
    load_development_manifest,
    summarize,
    validate_raw,
    validate_timing_raw,
)


def policy_row(policy: str, baseline: int = 1000, portfolio: int = 990) -> dict:
    return {
        "policy": policy,
        "complete": True,
        "budget_stop_blocks": 0,
        "winning_blocks": 1,
        "baseline_full_archive_bytes": baseline,
        "portfolio_full_archive_bytes": portfolio,
        "saved_full_archive_bytes": baseline - portfolio,
        "gain_fraction": (baseline - portfolio) / baseline,
        "baseline_search_ns_median": 100,
        "portfolio_search_ns_median": 150,
        "search_slowdown": 1.5,
        "blocks": [{"portfolio_selected_policy": True}],
    }


def raw_document() -> dict:
    return {
        "schema": "mathsvg-lzh-chain-lazy-oracle-v1",
        "engine": "test",
        "block_bytes": 1 << 20,
        "warmups": 1,
        "repetitions": 3,
        "holdout_payload_inspected": False,
        "policies": list(POLICIES),
        "samples": [
            {
                "dataset_id": "dev-real",
                "origin": "real",
                "domain": "text",
                "source": "datasets/data/real.bin",
                "input_bytes": 10,
                "input_sha256": "a" * 64,
                "block_count": 1,
                "policies": [policy_row(policy) for policy in POLICIES],
            }
        ],
    }


class LzParserOracleTests(unittest.TestCase):
    def manifest(self, root: pathlib.Path) -> pathlib.Path:
        path = root / "development.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "split",
                    "dataset_id",
                    "origin",
                    "sha256",
                    "bytes",
                    "sealed",
                    "path",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "split": "development",
                    "dataset_id": "dev-real",
                    "origin": "real",
                    "sha256": "a" * 64,
                    "bytes": 10,
                    "sealed": "false",
                    "path": "datasets/data/real.bin",
                }
            )
        return path

    def test_validates_open_real_development_rows_and_retains_gain(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manifest = load_development_manifest(self.manifest(pathlib.Path(temp)))
            raw = raw_document()
            validate_raw(raw, manifest)
            rows, retention = summarize(raw)
        self.assertTrue(retention["retention_pass"])
        self.assertEqual(retention["selected_policy"], "C4")
        self.assertEqual(len(rows), len(POLICIES) * 2)

    def test_rejects_holdout_or_sealed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manifest_path = self.manifest(pathlib.Path(temp))
            with manifest_path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["sealed"] = "true"
            with manifest_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            manifest = load_development_manifest(manifest_path)
            with self.assertRaises(LzOracleError):
                validate_raw(raw_document(), manifest)

    def test_rejects_inconsistent_exact_archive_arithmetic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manifest = load_development_manifest(self.manifest(pathlib.Path(temp)))
            raw = raw_document()
            raw["samples"][0]["policies"][0]["saved_full_archive_bytes"] = 99
            with self.assertRaises(LzOracleError):
                validate_raw(raw, manifest)

    def test_below_threshold_and_slow_search_fail_honestly(self) -> None:
        raw = raw_document()
        for row in raw["samples"][0]["policies"]:
            row.update(
                {
                    "portfolio_full_archive_bytes": 999,
                    "saved_full_archive_bytes": 1,
                    "gain_fraction": 0.001,
                    "portfolio_search_ns_median": 250,
                    "search_slowdown": 2.5,
                }
            )
        _, retention = summarize(raw)
        self.assertFalse(retention["retention_pass"])
        self.assertIsNone(retention["selected_policy"])
        self.assertIn(
            "search exceeds 2x",
            retention["decisions"]["C4"]["retention_reason"],
        )

    def test_directional_timing_selects_deepest_incrementally_admitted_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manifest = load_development_manifest(self.manifest(pathlib.Path(temp)))
            size_raw = raw_document()
            by_policy = {
                row["policy"]: row
                for row in size_raw["samples"][0]["policies"]
            }
            by_policy["C4"].update(
                {
                    "portfolio_full_archive_bytes": 994,
                    "saved_full_archive_bytes": 6,
                    "gain_fraction": 0.006,
                }
            )
            by_policy["C4L"].update(
                {
                    "portfolio_full_archive_bytes": 988,
                    "saved_full_archive_bytes": 12,
                    "gain_fraction": 0.012,
                }
            )
            by_policy["C8L"].update(
                {
                    "portfolio_full_archive_bytes": 981,
                    "saved_full_archive_bytes": 19,
                    "gain_fraction": 0.019,
                }
            )
            by_policy["C16L"].update(
                {
                    "portfolio_full_archive_bytes": 978,
                    "saved_full_archive_bytes": 22,
                    "gain_fraction": 0.022,
                }
            )
            timing_raw = copy.deepcopy(size_raw)
            timing_raw["repetitions"] = 10
            timing_raw["policies"] = list(TIMED_POLICIES)
            timing_raw["samples"][0]["policies"] = [
                row
                for row in timing_raw["samples"][0]["policies"]
                if row["policy"] in TIMED_POLICIES
            ]
            timing_raw["provenance"] = {
                "timing_evidence_status": (
                    "development-directional-gui-host"
                ),
                "project_workloads_quiesced_by_coordinator": True,
                "resource_isolation": False,
            }
            validate_raw(size_raw, manifest)
            validate_timing_raw(timing_raw, manifest, size_raw)
            rows, retention = summarize(size_raw, timing_raw)

        self.assertEqual(retention["selected_policy"], "C8L")
        self.assertEqual(retention["selection_policy"], SELECTION_POLICY)
        self.assertIn("deepest timed policy", retention["selection_reason"])
        self.assertIn("C8L->C16L", retention["selection_reason"])
        c8_aggregate = next(
            row
            for row in rows
            if row["scope"] == "aggregate-real-development"
            and row["policy"] == "C8"
        )
        self.assertEqual(c8_aggregate["baseline_search_ns_median"], "")
        self.assertEqual(
            c8_aggregate["timing_evidence_status"],
            "unavailable-size-run-concurrent",
        )

    def test_provenance_names_the_sequential_selection_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            files = {
                name: root / name
                for name in (
                    "raw.json",
                    "timing.json",
                    "summary.csv",
                    "manifest.csv",
                    "probe",
                    "source.rs",
                )
            }
            for path in files.values():
                path.write_bytes(b"fixture")

            provenance = build_provenance(
                raw_path=files["raw.json"],
                csv_path=files["summary.csv"],
                manifest_path=files["manifest.csv"],
                binary_path=files["probe"],
                timing_raw_path=files["timing.json"],
                prior_size_provenance=None,
                source_paths=[files["source.rs"]],
                raw=raw_document(),
                retention={
                    "selected_policy": "C8L",
                    "selection_policy": SELECTION_POLICY,
                },
            )

        self.assertEqual(
            PROVENANCE_SCHEMA,
            "mathsvg-lzh-chain-lazy-provenance-v3",
        )
        self.assertEqual(
            SELECTION_POLICY,
            "sequential-incremental-exact-gain-v1",
        )
        self.assertEqual(provenance["schema"], PROVENANCE_SCHEMA)
        self.assertEqual(provenance["selection_policy"], SELECTION_POLICY)


if __name__ == "__main__":
    unittest.main()
