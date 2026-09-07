#!/usr/bin/env python3
"""Validate MathSVG dataset manifests without opening dataset payloads.

The validator deliberately works from metadata only.  In particular, validating
a sealed holdout manifest must not inspect, sample, or summarize the referenced
files.  Payload hashes are verified only by a separate, explicitly authorized
materialization step after an experiment has been frozen.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

SPLITS = frozenset({"development", "validation", "holdout"})
ORIGINS = frozenset({"real", "synthetic", "derived", "control"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

FIELDS = (
    "split",
    "dataset_id",
    "split_group",
    "origin",
    "primary",
    "domain",
    "source",
    "license",
    "sha256",
    "bytes",
    "sealed",
    "path",
    "legacy_observed",
)


class ManifestError(ValueError):
    """A deterministic, user-facing manifest validation failure."""


def _parse_bool(value: str, field: str, row_number: int) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ManifestError(
        f"row {row_number}: {field} must be exactly true or false"
    )


@dataclass(frozen=True, slots=True)
class DatasetEntry:
    split: str
    dataset_id: str
    split_group: str
    origin: str
    primary: bool
    domain: str
    source: str
    license: str
    sha256: str
    bytes: int
    sealed: bool
    path: str
    legacy_observed: bool

    @classmethod
    def from_row(cls, row: dict[str, str], row_number: int) -> "DatasetEntry":
        missing = [field for field in FIELDS if field not in row]
        if missing:
            raise ManifestError(
                f"row {row_number}: missing columns: {', '.join(missing)}"
            )
        try:
            byte_count = int(row["bytes"], 10)
        except ValueError as exc:
            raise ManifestError(
                f"row {row_number}: bytes must be a base-10 integer"
            ) from exc

        entry = cls(
            split=row["split"].strip(),
            dataset_id=row["dataset_id"].strip(),
            split_group=row["split_group"].strip(),
            origin=row["origin"].strip(),
            primary=_parse_bool(row["primary"], "primary", row_number),
            domain=row["domain"].strip(),
            source=row["source"].strip(),
            license=row["license"].strip(),
            sha256=row["sha256"].strip().lower(),
            bytes=byte_count,
            sealed=_parse_bool(row["sealed"], "sealed", row_number),
            path=row["path"].strip(),
            legacy_observed=_parse_bool(
                row["legacy_observed"], "legacy_observed", row_number
            ),
        )
        entry.validate(row_number)
        return entry

    def validate(self, row_number: int) -> None:
        prefix = f"row {row_number}"
        if self.split not in SPLITS:
            raise ManifestError(f"{prefix}: invalid split {self.split!r}")
        if self.origin not in ORIGINS:
            raise ManifestError(f"{prefix}: invalid origin {self.origin!r}")
        for field in (
            "dataset_id",
            "split_group",
            "domain",
            "source",
            "license",
            "path",
        ):
            if not getattr(self, field):
                raise ManifestError(f"{prefix}: {field} must not be empty")
        if not SHA256_RE.fullmatch(self.sha256):
            raise ManifestError(f"{prefix}: sha256 must be 64 lowercase hex digits")
        if self.bytes < 0:
            raise ManifestError(f"{prefix}: bytes must be non-negative")

        manifest_path = pathlib.PurePosixPath(self.path)
        if manifest_path.is_absolute() or ".." in manifest_path.parts:
            raise ManifestError(
                f"{prefix}: path must be a safe manifest-relative POSIX path"
            )
        if self.split == "holdout" and not self.sealed:
            raise ManifestError(f"{prefix}: every holdout row must be sealed")
        if self.split != "holdout" and self.sealed:
            raise ManifestError(
                f"{prefix}: only holdout rows may carry sealed=true"
            )
        if self.legacy_observed and self.split != "development":
            raise ManifestError(
                f"{prefix}: legacy-observed data is development-only"
            )
        if self.primary and self.origin != "real":
            raise ManifestError(
                f"{prefix}: primary=true is reserved for real data"
            )


def load_manifest(path: pathlib.Path) -> list[DatasetEntry]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(FIELDS):
            raise ManifestError(
                "header must exactly equal: " + ",".join(FIELDS)
            )
        entries = [
            DatasetEntry.from_row(dict(row), row_number)
            for row_number, row in enumerate(reader, start=2)
        ]
    validate_collection(entries)
    return entries


def validate_collection(entries: Sequence[DatasetEntry]) -> None:
    if not entries:
        raise ManifestError("manifest must contain at least one row")

    seen_ids: set[str] = set()
    group_splits: dict[str, str] = {}
    for entry in entries:
        if entry.dataset_id in seen_ids:
            raise ManifestError(f"duplicate dataset_id: {entry.dataset_id}")
        seen_ids.add(entry.dataset_id)

        previous = group_splits.setdefault(entry.split_group, entry.split)
        if previous != entry.split:
            raise ManifestError(
                "split leakage: split_group "
                f"{entry.split_group!r} occurs in {previous!r} and {entry.split!r}"
            )


def composition(entries: Iterable[DatasetEntry]) -> dict[str, float | int | bool]:
    rows = list(entries)
    file_count = len(rows)
    byte_count = sum(entry.bytes for entry in rows)
    # The wording in the project requirement is "non-synthetic", but controls
    # and generated replicas must not be usable to game that threshold.  Only
    # primary, real-origin samples count toward the publishable 70% balance.
    nonsynthetic = [
        entry for entry in rows if entry.origin == "real" and entry.primary
    ]
    nonsynthetic_files = len(nonsynthetic)
    nonsynthetic_bytes = sum(entry.bytes for entry in nonsynthetic)
    file_fraction = nonsynthetic_files / file_count if file_count else 0.0
    byte_fraction = nonsynthetic_bytes / byte_count if byte_count else 0.0
    return {
        "files": file_count,
        "bytes": byte_count,
        "non_synthetic_files": nonsynthetic_files,
        "non_synthetic_bytes": nonsynthetic_bytes,
        "balance_eligibility": "origin=real AND primary=true",
        "non_synthetic_file_fraction": file_fraction,
        "non_synthetic_byte_fraction": byte_fraction,
        "passes_70_percent_rule": file_fraction >= 0.70
        and byte_fraction >= 0.70,
    }


def canonical_manifest_sha256(entries: Iterable[DatasetEntry]) -> str:
    canonical_rows = [
        asdict(entry)
        for entry in sorted(entries, key=lambda item: item.dataset_id)
    ]
    payload = json.dumps(
        canonical_rows,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def report(entries: Sequence[DatasetEntry]) -> dict[str, object]:
    by_split = {
        split: composition(entry for entry in entries if entry.split == split)
        for split in sorted(SPLITS)
    }
    return {
        "schema_version": 1,
        "manifest_sha256": canonical_manifest_sha256(entries),
        "all": composition(entries),
        "by_split": by_split,
        "holdout_payload_inspected": False,
    }


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("--require-main-balance", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        entries = load_manifest(args.manifest)
        result = report(entries)
        if (
            args.require_main_balance
            and not result["all"]["passes_70_percent_rule"]  # type: ignore[index]
        ):
            raise ManifestError(
                "main benchmark violates the 70% non-synthetic files/bytes rule"
            )
    except (ManifestError, OSError) as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
