#!/usr/bin/env python3
"""Build the honest MathSVG development manifest from pinned local corpora.

All selected payloads were visible to the predecessor project and are marked
``legacy_observed=true``. Upstream corpus bytes are ``real`` and primary;
locally generated diagnostics, derived toolchain output, and incompressibility
controls are explicitly non-primary. This tool intentionally creates no
validation or holdout rows and cannot be used to unseal a holdout.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
import sys
from dataclasses import asdict
from dataclasses import dataclass
from typing import Any, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
    from mathsvg.python.datasets.manifest import (  # type: ignore[import-not-found]
        FIELDS,
        DatasetEntry,
        composition,
        load_manifest,
        report,
    )
else:
    from .manifest import FIELDS, DatasetEntry, composition, load_manifest, report


REAL_CORPORA = (
    "calgary",
    "canterbury",
    "silesia",
    "enwik8",
    "enwik9",
    "pizza_chili",
)

SYNTHETIC_SCHEMA = "mathzip-synthetic-corpus-v1"
MIXED_SCHEMA = "mathzip-generated-mixed-v1"
SNAPSHOT_SCHEMA = "mathzip-generated-git-snapshots-v1"
DEVELOPMENT_SEED = 1_297_748_005
DEVELOPMENT_SIZE = 65_536


@dataclass(frozen=True, slots=True)
class SyntheticSelection:
    """One deliberately bounded row selected from the synthetic catalogue."""

    dataset_id: str
    family: str
    noise_density: float | None
    container_type: str | None
    origin: str
    domain: str


SYNTHETIC_SELECTIONS = (
    SyntheticSelection(
        "dev-synthetic-constant-00",
        "constant_00",
        0.0,
        None,
        "synthetic",
        "synthetic-diagnostic",
    ),
    SyntheticSelection(
        "dev-synthetic-linear",
        "linear",
        0.0,
        None,
        "synthetic",
        "synthetic-diagnostic",
    ),
    SyntheticSelection(
        "dev-synthetic-polynomial-d2",
        "polynomial_d2",
        0.0,
        None,
        "synthetic",
        "synthetic-diagnostic",
    ),
    SyntheticSelection(
        "dev-synthetic-periodic",
        "periodic",
        0.0,
        None,
        "synthetic",
        "synthetic-diagnostic",
    ),
    SyntheticSelection(
        "dev-synthetic-recurrence",
        "recurrence",
        0.0,
        None,
        "synthetic",
        "synthetic-diagnostic",
    ),
    SyntheticSelection(
        "dev-synthetic-lfsr8",
        "lfsr8",
        0.0,
        None,
        "synthetic",
        "synthetic-diagnostic",
    ),
    SyntheticSelection(
        "dev-synthetic-piecewise-mixed",
        "piecewise_mixed",
        0.0,
        None,
        "synthetic",
        "synthetic-diagnostic",
    ),
    SyntheticSelection(
        "dev-control-cryptographic-random",
        "random",
        0.0,
        None,
        "control",
        "incompressible-control-cryptographic-random",
    ),
    SyntheticSelection(
        "dev-synthetic-linear-noise-0p001",
        "linear",
        0.001,
        None,
        "synthetic",
        "synthetic-diagnostic-sparse-exceptions",
    ),
    SyntheticSelection(
        "dev-synthetic-linear-noise-0p25",
        "linear",
        0.25,
        None,
        "synthetic",
        "synthetic-diagnostic-noise-sweep",
    ),
    SyntheticSelection(
        "dev-control-encrypted",
        "encrypted",
        0.0,
        None,
        "control",
        "incompressible-control-encrypted",
    ),
    SyntheticSelection(
        "dev-control-gzip",
        "already_compressed",
        None,
        "gz",
        "control",
        "incompressible-control-gzip",
    ),
    SyntheticSelection(
        "dev-control-xz",
        "already_compressed",
        None,
        "xz",
        "control",
        "incompressible-control-xz",
    ),
    SyntheticSelection(
        "dev-control-zip",
        "already_compressed",
        None,
        "zip",
        "control",
        "incompressible-control-zip",
    ),
)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: object, label: str) -> pathlib.PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty POSIX path")
    if "\x00" in value or "\\" in value:
        raise ValueError(f"{label} must be a safe relative POSIX path")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"{label} must be a safe relative POSIX path")
    result = pathlib.PurePosixPath(value)
    if result.is_absolute() or ".." in result.parts:
        raise ValueError(f"{label} must be a safe relative POSIX path")
    return result


def _load_object(path: pathlib.Path, expected_schema: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON value must be an object")
    if value.get("schema_version") != expected_schema:
        raise ValueError(
            f"{path}: expected schema_version={expected_schema!r}"
        )
    return value


def _verified_declared_payload(
    root: pathlib.Path,
    relative_value: object,
    declared_bytes: object,
    declared_hash: object,
    label: str,
) -> pathlib.Path:
    relative = _safe_relative(relative_value, f"{label}.path")
    if not isinstance(declared_bytes, int) or declared_bytes < 0:
        raise ValueError(f"{label}.size_bytes must be a non-negative integer")
    if (
        not isinstance(declared_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", declared_hash)
    ):
        raise ValueError(f"{label}.sha256 must be 64 lowercase hex digits")
    payload = root.joinpath(*relative.parts)
    if not payload.is_file() or payload.is_symlink():
        raise FileNotFoundError(payload)
    if payload.stat().st_size != declared_bytes:
        raise ValueError(f"size mismatch for {payload}")
    if _sha256(payload) != declared_hash:
        raise ValueError(f"SHA-256 mismatch for {payload}")
    return payload


def _domain(corpus: str, relative: str) -> str:
    if corpus in {"enwik8", "enwik9", "pizza_chili"}:
        return "standard-text"
    if corpus == "silesia":
        return {
            "dickens": "standard-text",
            "mozilla": "real-mixed-executable",
            "mr": "structured-medical-binary",
            "nci": "real-mixed-database",
            "ooffice": "real-mixed-shared-library",
            "osdb": "real-mixed-database",
            "reymont": "standard-text",
            "samba": "real-mixed-source",
            "sao": "structured-scientific-binary",
            "webster": "standard-text",
            "x-ray": "structured-medical-binary",
            "xml": "real-mixed-xml",
        }.get(relative, "standard-mixed")
    if corpus == "calgary":
        return {
            "geo": "structured-geophysical-binary",
            "obj1": "real-mixed-object-code",
            "obj2": "real-mixed-object-code",
            "pic": "structured-bitmap-bilevel",
        }.get(relative, "standard-text")
    suffix = pathlib.PurePosixPath(relative).suffix.lower()
    if suffix in {".txt", ".html", ".c", ".lsp"}:
        return "standard-text"
    if suffix == ".xls":
        return "standard-tabular"
    return "standard-mixed"


def _real_entries(repository: pathlib.Path) -> list[DatasetEntry]:
    entries: list[DatasetEntry] = []
    seen_payloads: dict[str, pathlib.Path] = {}
    data_root = repository / "datasets" / "data"
    for corpus in REAL_CORPORA:
        metadata_path = data_root / corpus / "dataset.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        source = str(metadata["homepage"])
        license_text = str(metadata["license"])
        for item in metadata["files"]:
            relative = str(item["path"])
            # Combined streams and their boundary manifests duplicate the
            # individually reported source files.
            if relative in {"combined.bin", "combined.manifest.json"}:
                continue
            payload = data_root / corpus / relative
            if not payload.is_file():
                raise FileNotFoundError(payload)
            declared_bytes = int(item["size_bytes"])
            declared_hash = str(item["sha256"])
            if payload.stat().st_size != declared_bytes:
                raise ValueError(f"size mismatch for {payload}")
            if _sha256(payload) != declared_hash:
                raise ValueError(f"SHA-256 mismatch for {payload}")
            # Calgary ``pic`` and Canterbury ``ptt5`` are the same bytes.
            # Exact duplicate payloads must not receive two primary votes in
            # the file-balance rule or aggregate benchmark.
            if declared_hash in seen_payloads:
                continue
            seen_payloads[declared_hash] = payload
            entries.append(
                DatasetEntry(
                    split="development",
                    dataset_id=f"dev-{_slug(corpus)}-{_slug(relative)}",
                    split_group=f"legacy-{_slug(corpus)}",
                    origin="real",
                    primary=True,
                    domain=_domain(corpus, relative),
                    source=source,
                    license=license_text,
                    sha256=declared_hash,
                    bytes=declared_bytes,
                    sealed=False,
                    path=payload.relative_to(repository).as_posix(),
                    legacy_observed=True,
                )
            )
    return entries


def _synthetic_entries(repository: pathlib.Path) -> list[DatasetEntry]:
    manifest_path = repository / "datasets" / "synthetic" / "manifest.json"
    metadata = _load_object(manifest_path, SYNTHETIC_SCHEMA)
    if metadata.get("seed") != DEVELOPMENT_SEED:
        raise ValueError(
            f"{manifest_path}: expected seed={DEVELOPMENT_SEED}"
        )
    entries_value = metadata.get("entries")
    if not isinstance(entries_value, list):
        raise ValueError(f"{manifest_path}: entries must be an array")
    generator = str(metadata.get("generator", "")).strip()
    license_text = str(metadata.get("license", "")).strip()
    if not generator or not license_text:
        raise ValueError(f"{manifest_path}: generator/license must be declared")

    entries: list[DatasetEntry] = []
    used_catalogue_rows: set[int] = set()
    for selection in SYNTHETIC_SELECTIONS:
        matches: list[tuple[int, dict[str, Any]]] = []
        for index, value in enumerate(entries_value):
            if not isinstance(value, dict):
                raise ValueError(
                    f"{manifest_path}: entries[{index}] must be an object"
                )
            parameters = value.get("parameters")
            container_type = (
                parameters.get("container_type")
                if isinstance(parameters, dict)
                else None
            )
            if (
                value.get("family") == selection.family
                and value.get("size_requested") == DEVELOPMENT_SIZE
                and value.get("noise_density") == selection.noise_density
                and container_type == selection.container_type
            ):
                matches.append((index, value))
        if len(matches) != 1:
            raise ValueError(
                f"{manifest_path}: selection {selection.dataset_id!r} "
                f"matched {len(matches)} catalogue rows"
            )
        index, item = matches[0]
        if index in used_catalogue_rows:
            raise ValueError(
                f"{manifest_path}: synthetic selection reuses entries[{index}]"
            )
        used_catalogue_rows.add(index)
        payload = _verified_declared_payload(
            repository / "datasets" / "synthetic",
            item.get("path"),
            item.get("size_bytes"),
            item.get("sha256"),
            f"{manifest_path}.entries[{index}]",
        )
        entries.append(
            DatasetEntry(
                split="development",
                dataset_id=selection.dataset_id,
                split_group=f"legacy-synthetic-seed-{DEVELOPMENT_SEED}",
                origin=selection.origin,
                primary=False,
                domain=selection.domain,
                source=(
                    f"{generator}; manifest=datasets/synthetic/manifest.json; "
                    f"seed={DEVELOPMENT_SEED}"
                ),
                license=license_text,
                sha256=str(item["sha256"]),
                bytes=payload.stat().st_size,
                sealed=False,
                path=payload.relative_to(repository).as_posix(),
                legacy_observed=True,
            )
        )
    return entries


def _generated_entries(repository: pathlib.Path) -> list[DatasetEntry]:
    root = repository / "datasets" / "generated"
    result: list[DatasetEntry] = []

    mixed_manifest_path = root / "mixed" / "manifest.json"
    mixed = _load_object(mixed_manifest_path, MIXED_SCHEMA)
    mixed_entries = mixed.get("entries")
    if not isinstance(mixed_entries, list):
        raise ValueError(f"{mixed_manifest_path}: entries must be an array")
    sqlite_matches = [
        (index, item)
        for index, item in enumerate(mixed_entries)
        if isinstance(item, dict) and item.get("path") == "files/sample.sqlite"
    ]
    if len(sqlite_matches) != 1:
        raise ValueError(
            f"{mixed_manifest_path}: expected exactly one sample.sqlite row"
        )
    mixed_index, sqlite_item = sqlite_matches[0]
    sqlite_payload = _verified_declared_payload(
        root / "mixed",
        sqlite_item.get("path"),
        sqlite_item.get("size_bytes"),
        sqlite_item.get("sha256"),
        f"{mixed_manifest_path}.entries[{mixed_index}]",
    )
    mixed_generator = str(mixed.get("generator", "")).strip()
    mixed_license = str(mixed.get("license", "")).strip()
    mixed_seed = mixed.get("seed")
    if not mixed_generator or not mixed_license or mixed_seed != DEVELOPMENT_SEED:
        raise ValueError(
            f"{mixed_manifest_path}: generator/license/seed mismatch"
        )
    result.append(
        DatasetEntry(
            split="development",
            dataset_id="dev-derived-mixed-sqlite",
            split_group=f"legacy-generated-seed-{DEVELOPMENT_SEED}",
            origin="derived",
            primary=False,
            domain="derived-mixed-sqlite",
            source=(
                f"{mixed_generator}; "
                "manifest=datasets/generated/mixed/manifest.json; "
                f"seed={DEVELOPMENT_SEED}"
            ),
            license=mixed_license,
            sha256=str(sqlite_item["sha256"]),
            bytes=sqlite_payload.stat().st_size,
            sealed=False,
            path=sqlite_payload.relative_to(repository).as_posix(),
            legacy_observed=True,
        )
    )

    snapshots_manifest_path = root / "git_snapshots" / "manifest.json"
    snapshots = _load_object(snapshots_manifest_path, SNAPSHOT_SCHEMA)
    all_versions = snapshots.get("all_versions")
    if not isinstance(all_versions, dict):
        raise ValueError(
            f"{snapshots_manifest_path}: all_versions must be an object"
        )
    snapshot_payload = _verified_declared_payload(
        root / "git_snapshots",
        all_versions.get("path"),
        all_versions.get("size_bytes"),
        all_versions.get("sha256"),
        f"{snapshots_manifest_path}.all_versions",
    )
    snapshot_generator = str(snapshots.get("generator", "")).strip()
    snapshot_license = str(snapshots.get("license", "")).strip()
    snapshot_seed = snapshots.get("seed")
    if (
        not snapshot_generator
        or not snapshot_license
        or snapshot_seed != DEVELOPMENT_SEED
    ):
        raise ValueError(
            f"{snapshots_manifest_path}: generator/license/seed mismatch"
        )
    result.append(
        DatasetEntry(
            split="development",
            dataset_id="dev-derived-git-snapshot-stream",
            split_group=f"legacy-generated-seed-{DEVELOPMENT_SEED}",
            origin="derived",
            primary=False,
            domain="derived-version-snapshot",
            source=(
                f"{snapshot_generator}; "
                "manifest=datasets/generated/git_snapshots/manifest.json; "
                f"seed={DEVELOPMENT_SEED}"
            ),
            license=snapshot_license,
            sha256=str(all_versions["sha256"]),
            bytes=snapshot_payload.stat().st_size,
            sealed=False,
            path=snapshot_payload.relative_to(repository).as_posix(),
            legacy_observed=True,
        )
    )
    return result


def build_entries(repository: pathlib.Path) -> list[DatasetEntry]:
    entries = (
        _real_entries(repository)
        + _synthetic_entries(repository)
        + _generated_entries(repository)
    )
    entries.sort(key=lambda entry: entry.dataset_id)
    result = composition(entries)
    if not result["passes_70_percent_rule"]:
        raise ValueError("development selection violates the 70% balance rule")
    return entries


def write_manifest(path: pathlib.Path, entries: Sequence[DatasetEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for entry in entries:
            row = asdict(entry)
            for field in ("primary", "sealed", "legacy_observed"):
                row[field] = "true" if row[field] else "false"
            writer.writerow(row)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[3],
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/manifests/development.csv"),
    )
    parser.add_argument(
        "--report",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/manifests/development-report.json"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    repository = args.repository.resolve()
    output = args.output if args.output.is_absolute() else repository / args.output
    report_path = (
        args.report if args.report.is_absolute() else repository / args.report
    )
    try:
        entries = build_entries(repository)
        write_manifest(output, entries)
        # Parse the emitted bytes through the independent validator before
        # publishing its report.
        validated = load_manifest(output)
        result = report(validated)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"development manifest error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
