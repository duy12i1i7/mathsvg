from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest

from mathsvg.python.datasets.build_development_manifest import (
    DEVELOPMENT_SEED,
    DEVELOPMENT_SIZE,
    REAL_CORPORA,
    SYNTHETIC_SELECTIONS,
    _generated_entries,
    _real_entries,
    _synthetic_entries,
)
from mathsvg.python.datasets.manifest import DatasetEntry, composition


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _entry(index: int, *, origin: str, primary: bool) -> DatasetEntry:
    return DatasetEntry(
        split="development",
        dataset_id=f"entry-{index}",
        split_group=f"group-{index}",
        origin=origin,
        primary=primary,
        domain="fixture",
        source="local:fixture",
        license="test-only",
        sha256=f"{index:064x}",
        bytes=100,
        sealed=False,
        path=f"fixtures/{index}.bin",
        legacy_observed=True,
    )


class DevelopmentManifestBuilderTests(unittest.TestCase):
    def test_synthetic_catalogue_selection_preserves_honest_origins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            synthetic_root = repository / "datasets/synthetic"
            catalogue: list[dict[str, object]] = []
            for index, selection in enumerate(SYNTHETIC_SELECTIONS):
                relative = f"selected/{index}.bin"
                payload = f"payload-{index}".encode("ascii")
                path = synthetic_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                parameters: dict[str, str] = {}
                if selection.container_type is not None:
                    parameters["container_type"] = selection.container_type
                catalogue.append(
                    {
                        "family": selection.family,
                        "noise_density": selection.noise_density,
                        "parameters": parameters,
                        "path": relative,
                        "seed": DEVELOPMENT_SEED,
                        "sha256": _sha256(payload),
                        "size_bytes": len(payload),
                        "size_requested": DEVELOPMENT_SIZE,
                    }
                )
            _write_json(
                synthetic_root / "manifest.json",
                {
                    "schema_version": "mathzip-synthetic-corpus-v1",
                    "seed": DEVELOPMENT_SEED,
                    "generator": "fixture-generator",
                    "license": "CC0-1.0",
                    "entries": catalogue,
                },
            )

            entries = _synthetic_entries(repository)
            self.assertEqual(len(entries), len(SYNTHETIC_SELECTIONS))
            self.assertTrue(all(not entry.primary for entry in entries))
            self.assertEqual(
                {entry.origin for entry in entries},
                {"synthetic", "control"},
            )
            self.assertEqual(
                len({entry.split_group for entry in entries}), 1
            )

    def test_generated_catalogues_are_derived_and_integrity_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            generated = repository / "datasets/generated"
            sqlite_payload = b"SQLite format 3\x00fixture"
            sqlite_path = generated / "mixed/files/sample.sqlite"
            sqlite_path.parent.mkdir(parents=True)
            sqlite_path.write_bytes(sqlite_payload)
            _write_json(
                generated / "mixed/manifest.json",
                {
                    "schema_version": "mathzip-generated-mixed-v1",
                    "seed": DEVELOPMENT_SEED,
                    "generator": "fixture-generator",
                    "license": "CC0-1.0",
                    "entries": [
                        {
                            "path": "files/sample.sqlite",
                            "size_bytes": len(sqlite_payload),
                            "sha256": _sha256(sqlite_payload),
                        }
                    ],
                },
            )

            snapshot_payload = b"version-one\nversion-two\n"
            snapshot_path = (
                generated / "git_snapshots/combined/all_versions.bin"
            )
            snapshot_path.parent.mkdir(parents=True)
            snapshot_path.write_bytes(snapshot_payload)
            _write_json(
                generated / "git_snapshots/manifest.json",
                {
                    "schema_version": (
                        "mathzip-generated-git-snapshots-v1"
                    ),
                    "seed": DEVELOPMENT_SEED,
                    "generator": "fixture-generator",
                    "license": "CC0-1.0",
                    "all_versions": {
                        "path": "combined/all_versions.bin",
                        "size_bytes": len(snapshot_payload),
                        "sha256": _sha256(snapshot_payload),
                    },
                },
            )

            entries = _generated_entries(repository)
            self.assertEqual(len(entries), 2)
            self.assertTrue(all(entry.origin == "derived" for entry in entries))
            self.assertTrue(all(not entry.primary for entry in entries))
            self.assertEqual(
                len({entry.split_group for entry in entries}), 1
            )

            sqlite_path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                _generated_entries(repository)

    def test_real_payloads_are_deduplicated_before_primary_counting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            data_root = repository / "datasets/data"
            for index, corpus in enumerate(REAL_CORPORA):
                payload = (
                    b"same"
                    if corpus in {"calgary", "canterbury"}
                    else f"unique-{index}".encode("ascii")
                )
                relative = f"{corpus}.bin"
                path = data_root / corpus / relative
                path.parent.mkdir(parents=True)
                path.write_bytes(payload)
                _write_json(
                    data_root / corpus / "dataset.json",
                    {
                        "homepage": f"https://example.invalid/{corpus}",
                        "license": "test-only",
                        "files": [
                            {
                                "path": relative,
                                "size_bytes": len(payload),
                                "sha256": _sha256(payload),
                            }
                        ],
                    },
                )

            entries = _real_entries(repository)
            self.assertEqual(len(entries), len(REAL_CORPORA) - 1)
            self.assertEqual(len({entry.sha256 for entry in entries}), len(entries))

    def test_expanded_selection_keeps_real_file_balance_above_70_percent(self) -> None:
        rows = [
            _entry(index, origin="real", primary=True)
            for index in range(39)
        ]
        rows.extend(
            _entry(index + 39, origin="synthetic", primary=False)
            for index in range(16)
        )
        result = composition(rows)
        self.assertAlmostEqual(
            result["non_synthetic_file_fraction"], 39 / 55
        )
        self.assertTrue(result["passes_70_percent_rule"])

        rows.append(_entry(55, origin="derived", primary=False))
        self.assertFalse(composition(rows)["passes_70_percent_rule"])


if __name__ == "__main__":
    unittest.main()
