from __future__ import annotations

import unittest

from mathsvg.python.datasets.coverage_matrix import (
    REQUIREMENTS,
    build_coverage_rows,
    coverage_report,
    duplicate_payload_groups,
    render_markdown,
)
from mathsvg.python.datasets.manifest import DatasetEntry


def entry(
    dataset_id: str,
    *,
    origin: str = "real",
    primary: bool = True,
    sha256: str | None = None,
) -> DatasetEntry:
    return DatasetEntry(
        split="development",
        dataset_id=dataset_id,
        split_group=f"group-{dataset_id}",
        origin=origin,
        primary=primary,
        domain="fixture",
        source="https://example.invalid/source",
        license="test-only",
        sha256=sha256 or ("0" * 64),
        bytes=100,
        sealed=False,
        path=f"datasets/fixtures/{dataset_id}.bin",
        legacy_observed=True,
    )


def row_by_requirement(rows: list[object], requirement: str) -> object:
    return next(
        row for row in rows if getattr(row, "requirement") == requirement
    )


class DatasetCoverageMatrixTests(unittest.TestCase):
    def test_requirement_catalogue_exactly_tracks_section_20(self) -> None:
        self.assertEqual(len(REQUIREMENTS), 57)
        self.assertEqual(
            {
                requirement.section
                for requirement in REQUIREMENTS
            },
            {
                "standard-general-purpose",
                "real-world-mixed",
                "structured-real-world",
                "incompressible-control",
                "synthetic-diagnostic",
            },
        )

    def test_derived_json_never_masquerades_as_real(self) -> None:
        entries = [entry("dev-canterbury-alice29-txt")]
        rows = build_coverage_rows(
            entries,
            {"json": ("datasets/generated/mixed/files/sample.json",)},
        )
        json_row = row_by_requirement(rows, "JSON")
        self.assertEqual(getattr(json_row, "status"), "available-derived")
        self.assertFalse(getattr(json_row, "meets_requirement"))

    def test_real_primary_xml_is_selected_but_spoofed_derived_is_not(self) -> None:
        real_rows = build_coverage_rows(
            [entry("dev-silesia-xml")],
            {},
        )
        real_xml = row_by_requirement(real_rows, "XML")
        self.assertEqual(getattr(real_xml, "status"), "selected-real")
        self.assertTrue(getattr(real_xml, "meets_requirement"))

        derived_rows = build_coverage_rows(
            [
                entry(
                    "dev-silesia-xml",
                    origin="derived",
                    primary=False,
                )
            ],
            {},
        )
        derived_xml = row_by_requirement(derived_rows, "XML")
        self.assertEqual(getattr(derived_xml, "status"), "missing")
        self.assertFalse(getattr(derived_xml, "meets_requirement"))

    def test_noise_sweep_requires_both_selected_levels(self) -> None:
        sparse = entry(
            "dev-synthetic-linear-noise-0p001",
            origin="synthetic",
            primary=False,
        )
        high = entry(
            "dev-synthetic-linear-noise-0p25",
            origin="synthetic",
            primary=False,
        )
        one_level = build_coverage_rows([sparse], {})
        one_row = row_by_requirement(one_level, "Multiple noise levels")
        self.assertFalse(getattr(one_row, "meets_requirement"))

        both_levels = build_coverage_rows([sparse, high], {})
        both_row = row_by_requirement(both_levels, "Multiple noise levels")
        self.assertEqual(
            getattr(both_row, "status"), "selected-synthetic"
        )
        self.assertTrue(getattr(both_row, "meets_requirement"))

    def test_control_requirement_requires_control_origin(self) -> None:
        spoofed = entry(
            "development-control-generated-zstd",
            origin="synthetic",
            primary=False,
        )
        spoofed_rows = build_coverage_rows([spoofed], {})
        spoofed_zstd = row_by_requirement(spoofed_rows, "Zstd")
        self.assertFalse(getattr(spoofed_zstd, "meets_requirement"))

        control = entry(
            "development-control-generated-zstd",
            origin="control",
            primary=False,
        )
        control_rows = build_coverage_rows([control], {})
        control_zstd = row_by_requirement(control_rows, "Zstd")
        self.assertEqual(
            getattr(control_zstd, "status"), "selected-control"
        )
        self.assertTrue(getattr(control_zstd, "meets_requirement"))

    def test_pinned_real_sources_close_the_four_remaining_gaps(self) -> None:
        entries = [
            entry("validation-geoserver-2-28-2-natural-earth-gpkg"),
            entry(
                "development-wikimedia-enwiki-20260701-"
                "protected-titles-sql"
            ),
            entry("validation-gnu-gnulib-20250729-git-bundle"),
            entry("validation-zenodo-15978325-sem-tem-bmp"),
        ]
        rows = build_coverage_rows(entries, {})
        for requirement in ("SQLite", "Database dumps", "Git snapshots", "BMP"):
            row = row_by_requirement(rows, requirement)
            self.assertEqual(getattr(row, "status"), "selected-real")
            self.assertTrue(getattr(row, "meets_requirement"))

    def test_unmaterialized_serde_is_reported_only_as_pinned_recipe(self) -> None:
        rows = build_coverage_rows(
            [entry("dev-canterbury-alice29-txt")], {}
        )
        rust = row_by_requirement(rows, "Rust crates")
        self.assertEqual(getattr(rust, "status"), "pinned-recipe")
        self.assertFalse(getattr(rust, "meets_requirement"))
        self.assertIn("serde-1.0.229.crate", getattr(rust, "evidence"))

    def test_duplicate_payload_audit_and_markdown_are_explicit(self) -> None:
        rows = [
            entry("first", sha256="a" * 64),
            entry("second", sha256="a" * 64),
        ]
        duplicates = duplicate_payload_groups(rows)
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["dataset_ids"], ["first", "second"])

        report = coverage_report(rows, {})
        markdown = render_markdown(report)
        self.assertIn("Exact duplicate payload groups", markdown)
        self.assertIn("does **not** inspect sealed holdout payloads", markdown)


if __name__ == "__main__":
    unittest.main()
