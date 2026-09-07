from __future__ import annotations

import csv
import pathlib
import tempfile
import unittest

from mathsvg.python.datasets.manifest import (
    FIELDS,
    DatasetEntry,
    ManifestError,
    canonical_manifest_sha256,
    composition,
    load_manifest,
    validate_collection,
)


def entry(**overrides: object) -> DatasetEntry:
    values: dict[str, object] = {
        "split": "development",
        "dataset_id": "one",
        "split_group": "family-one",
        "origin": "real",
        "primary": True,
        "domain": "text",
        "source": "https://example.invalid/one",
        "license": "test-only",
        "sha256": "0" * 64,
        "bytes": 100,
        "sealed": False,
        "path": "development/one.bin",
        "legacy_observed": False,
    }
    values.update(overrides)
    return DatasetEntry(**values)  # type: ignore[arg-type]


class ManifestTests(unittest.TestCase):
    def test_composition_rule_counts_files_and_bytes(self) -> None:
        rows = [
            entry(dataset_id=f"real-{index}", split_group=f"real-{index}")
            for index in range(7)
        ]
        rows.extend(
            entry(
                dataset_id=f"synthetic-{index}",
                split_group=f"synthetic-{index}",
                origin="synthetic",
                primary=False,
                bytes=10,
            )
            for index in range(3)
        )
        result = composition(rows)
        self.assertEqual(result["non_synthetic_file_fraction"], 0.7)
        self.assertTrue(result["passes_70_percent_rule"])

    def test_controls_cannot_game_main_balance(self) -> None:
        rows = [
            entry(dataset_id="real", split_group="real", bytes=100),
            entry(
                dataset_id="control",
                split_group="control",
                origin="control",
                primary=False,
                bytes=1000,
            ),
        ]
        result = composition(rows)
        self.assertEqual(result["non_synthetic_files"], 1)
        self.assertEqual(result["non_synthetic_bytes"], 100)
        self.assertFalse(result["passes_70_percent_rule"])

    def test_derived_data_is_valid_but_never_primary(self) -> None:
        derived = entry(
            origin="derived",
            primary=False,
            dataset_id="derived",
            split_group="derived",
        )
        derived.validate(2)
        result = composition([entry(), derived])
        self.assertEqual(result["non_synthetic_files"], 1)
        self.assertFalse(result["passes_70_percent_rule"])

        disguised = entry(origin="derived", primary=True)
        with self.assertRaisesRegex(ManifestError, "reserved for real data"):
            disguised.validate(2)

    def test_split_group_cannot_leak(self) -> None:
        rows = [
            entry(),
            entry(
                dataset_id="two",
                split="validation",
                split_group="family-one",
                path="validation/two.bin",
            ),
        ]
        with self.assertRaisesRegex(ManifestError, "split leakage"):
            validate_collection(rows)

    def test_legacy_observed_is_development_only(self) -> None:
        row = entry(
            split="holdout",
            sealed=True,
            legacy_observed=True,
            path="holdout/one.bin",
        )
        with self.assertRaisesRegex(ManifestError, "development-only"):
            row.validate(2)

    def test_holdout_must_be_sealed(self) -> None:
        row = entry(split="holdout", path="holdout/one.bin")
        with self.assertRaisesRegex(ManifestError, "must be sealed"):
            row.validate(2)

    def test_path_traversal_rejected(self) -> None:
        row = entry(path="../secret.bin")
        with self.assertRaisesRegex(ManifestError, "safe manifest-relative"):
            row.validate(2)

    def test_canonical_hash_is_order_independent(self) -> None:
        first = entry(dataset_id="a", split_group="a")
        second = entry(dataset_id="b", split_group="b")
        self.assertEqual(
            canonical_manifest_sha256([first, second]),
            canonical_manifest_sha256([second, first]),
        )

    def test_load_requires_exact_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "manifest.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(FIELDS))
                writer.writeheader()
                writer.writerow(
                    {
                        "split": "development",
                        "dataset_id": "one",
                        "split_group": "one",
                        "origin": "real",
                        "primary": "true",
                        "domain": "text",
                        "source": "local:test",
                        "license": "test-only",
                        "sha256": "0" * 64,
                        "bytes": "1",
                        "sealed": "false",
                        "path": "development/one",
                        "legacy_observed": "false",
                    }
                )
            self.assertEqual(load_manifest(path)[0].dataset_id, "one")


if __name__ == "__main__":
    unittest.main()
