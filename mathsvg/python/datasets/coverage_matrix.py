#!/usr/bin/env python3
"""Report exact MathSVG §20 corpus coverage without consulting holdout data.

The report distinguishes selected upstream data from partial proxies, pinned
recipes, and locally generated diagnostics. A generated BMP or SQLite file is
useful for development, but it never satisfies a real-world requirement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
    from mathsvg.python.datasets.manifest import (  # type: ignore[import-not-found]
        DatasetEntry,
        canonical_manifest_sha256,
        composition,
        load_manifest,
        validate_collection,
    )
    from mathsvg.python.datasets.materialize_crate_validation import (
        SERDE_1_0_229,
    )
    from mathsvg.python.datasets.materialize_research_corpus import (
        DIRECT_SOURCES,
        LOCAL_CONTROLS,
        STANFORD_BUNNY,
        UAV_TELEMETRY,
        WIKIMEDIA_SQL_DUMP,
    )
else:
    from .manifest import (
        DatasetEntry,
        canonical_manifest_sha256,
        composition,
        load_manifest,
        validate_collection,
    )
    from .materialize_crate_validation import SERDE_1_0_229
    from .materialize_research_corpus import (
        DIRECT_SOURCES,
        LOCAL_CONTROLS,
        STANFORD_BUNNY,
        UAV_TELEMETRY,
        WIKIMEDIA_SQL_DUMP,
    )


FROZEN_RESEARCH_SOURCE_IDS = frozenset(
    source.dataset_id for source in DIRECT_SOURCES
) | frozenset(member.dataset_id for member in UAV_TELEMETRY.members) | {
    f"{STANFORD_BUNNY.dataset_prefix}-bun000-ply",
    WIKIMEDIA_SQL_DUMP.dataset_id,
}
FROZEN_LOCAL_CONTROL_IDS = frozenset(
    control.dataset_id for control in LOCAL_CONTROLS
)


@dataclass(frozen=True, slots=True)
class Requirement:
    section: str
    key: str
    label: str
    kind: str


@dataclass(frozen=True, slots=True)
class CoverageRow:
    section: str
    requirement: str
    status: str
    meets_requirement: bool
    evidence: str
    note: str


def _requirements(
    section: str, kind: str, values: Sequence[tuple[str, str]]
) -> tuple[Requirement, ...]:
    return tuple(
        Requirement(section, key, label, kind) for key, label in values
    )


REQUIREMENTS = (
    *_requirements(
        "standard-general-purpose",
        "real",
        (
            ("canterbury", "Canterbury"),
            ("calgary", "Calgary"),
            ("silesia", "Silesia"),
            ("enwik8", "enwik8"),
            ("enwik9", "enwik9"),
            ("pizza-chili", "Pizza & Chili"),
            (
                "large-text-compression-benchmark-subset",
                "Large Text Compression Benchmark subset",
            ),
        ),
    ),
    *_requirements(
        "real-world-mixed",
        "real",
        (
            ("linux-source", "Linux source"),
            ("llvm-source", "LLVM source"),
            ("rust-crates", "Rust crates"),
            ("json", "JSON"),
            ("xml", "XML"),
            ("csv", "CSV"),
            ("executables", "Executables"),
            ("shared-libraries", "Shared libraries"),
            ("sqlite", "SQLite"),
            ("database-dumps", "Database dumps"),
            ("pdf", "PDF"),
            ("container-layers", "Container layers"),
            ("git-snapshots", "Git snapshots"),
        ),
    ),
    *_requirements(
        "structured-real-world",
        "real",
        (
            ("uav-flight-logs", "UAV flight logs"),
            ("telemetry", "Telemetry"),
            ("public-sensor-datasets", "Public sensor datasets"),
            ("pcm-wav", "PCM/WAV"),
            ("bmp", "BMP"),
            ("tiff-raw", "TIFF raw"),
            ("dicom-uncompressed", "DICOM uncompressed"),
            ("fits", "FITS"),
            ("raster-gis", "Raster GIS"),
            ("ply-las", "PLY/LAS"),
            ("climate-arrays", "Climate arrays"),
            ("seismic-arrays", "Seismic arrays"),
            ("integer-columns", "Integer columns"),
            ("float-columns", "Float columns"),
            ("fixed-record-binaries", "Fixed-record binaries"),
        ),
    ),
    *_requirements(
        "incompressible-control",
        "control",
        (
            ("cryptographic-random", "Cryptographic random"),
            ("encrypted-data", "Encrypted data"),
            ("gzip", "gzip"),
            ("zstd", "Zstd"),
            ("xz", "XZ"),
            ("zip", "ZIP"),
            ("png", "PNG"),
            ("jpeg", "JPEG"),
            ("avif", "AVIF"),
            ("mp3", "MP3"),
            ("flac", "FLAC"),
            ("mp4-av1", "MP4/AV1"),
        ),
    ),
    *_requirements(
        "synthetic-diagnostic",
        "synthetic",
        (
            ("constant", "Constant"),
            ("linear", "Linear"),
            ("polynomial", "Polynomial"),
            ("periodic", "Periodic"),
            ("recurrence", "Recurrence"),
            ("lfsr", "LFSR"),
            ("piecewise", "Piecewise"),
            ("sparse-exceptions", "Sparse exceptions"),
            ("multiple-noise-levels", "Multiple noise levels"),
            ("mixed-structured-random", "Mixed structured/random"),
        ),
    ),
)


SELECTED_REAL: dict[str, tuple[str, ...]] = {
    "canterbury": ("prefix:dev-canterbury-",),
    "calgary": ("prefix:dev-calgary-",),
    "silesia": ("prefix:dev-silesia-",),
    "enwik8": ("id:dev-enwik8-enwik8",),
    "enwik9": ("id:dev-enwik9-enwik9",),
    "pizza-chili": ("prefix:dev-pizza-chili-",),
    "large-text-compression-benchmark-subset": (
        "id:dev-enwik8-enwik8",
        "id:dev-enwik9-enwik9",
    ),
    "rust-crates": (
        f"prefix:{SERDE_1_0_229.dataset_prefix}-",
    ),
    "xml": ("id:dev-silesia-xml",),
    "csv": ("id:validation-uci-1081-gsalc-csv",),
    "executables": ("id:dev-silesia-mozilla",),
    "shared-libraries": ("id:dev-silesia-ooffice",),
    "public-sensor-datasets": (
        "id:validation-uci-1081-gsalc-csv",
    ),
    "linux-source": ("id:validation-linux-v6-10-kernel-sched-core-c",),
    "llvm-source": ("id:validation-llvm-18-1-8-function-cpp",),
    "json": ("prefix:development-zenodo-uav-",),
    "sqlite": ("id:validation-geoserver-2-28-2-natural-earth-gpkg",),
    "database-dumps": (
        "id:development-wikimedia-enwiki-20260701-protected-titles-sql",
    ),
    "pdf": ("id:development-usgs-fs-2010-3086-pdf",),
    "container-layers": (
        "id:validation-docker-official-hello-world-amd64-layer",
    ),
    "git-snapshots": (
        "id:validation-gnu-gnulib-20250729-git-bundle",
    ),
    "uav-flight-logs": ("prefix:development-zenodo-uav-",),
    "telemetry": ("prefix:development-zenodo-uav-",),
    "pcm-wav": ("id:validation-fsdd-v1-0-9-0-george-0-wav",),
    "tiff-raw": ("id:development-usgs-ofr-2006-1216-geotiff",),
    "bmp": ("id:validation-zenodo-15978325-sem-tem-bmp",),
    "fits": ("id:validation-nasa-hst-fos-y19g0309t-fits",),
    "dicom-uncompressed": (
        "id:validation-idc-cptac-sar-ct-dicom",
    ),
    "raster-gis": ("id:development-usgs-ofr-2006-1216-geotiff",),
    "climate-arrays": (
        "id:development-noaa-globaltemp-v5-1-0-netcdf",
    ),
    "seismic-arrays": (
        "id:validation-earthscope-iu-anmo-bhz-20100227-mseed",
    ),
    "integer-columns": (
        "id:validation-nyc-tlc-yellow-trip-2020-04-parquet",
    ),
    "float-columns": (
        "id:validation-nyc-tlc-yellow-trip-2020-04-parquet",
    ),
    "fixed-record-binaries": (
        "id:validation-earthscope-iu-anmo-bhz-20100227-mseed",
    ),
    "ply-las": ("id:development-stanford-bunny-1994-bun000-ply",),
}


PARTIAL_REAL: dict[str, tuple[tuple[str, ...], str]] = {
    "telemetry": (
        ("id:validation-uci-1081-gsalc-csv",),
        "The UCI sensor table is real sampled measurements, not an explicitly "
        "declared telemetry log.",
    ),
    "dicom-uncompressed": (
        ("id:dev-silesia-mr", "id:dev-silesia-x-ray"),
        "Silesia supplies real medical binary data, but not a pinned "
        "uncompressed DICOM container. pydicom test fixtures are deliberately "
        "excluded because fixture provenance cannot satisfy real-world DICOM.",
    ),
    "seismic-arrays": (
        ("id:dev-calgary-geo",),
        "Calgary geo is real geophysical binary data; its metadata does not "
        "establish the required seismic-array layout.",
    ),
}


SELECTED_CONTROL: dict[str, tuple[str, ...]] = {
    "cryptographic-random": (
        "id:dev-control-cryptographic-random",
        "id:dev-synthetic-random",
    ),
    "encrypted-data": ("id:dev-control-encrypted",),
    "gzip": ("id:dev-control-gzip",),
    "xz": ("id:dev-control-xz",),
    "zip": ("id:dev-control-zip",),
    "zstd": ("id:development-control-generated-zstd",),
    "png": ("id:development-control-generated-png",),
    "jpeg": ("id:development-control-generated-jpeg",),
    "avif": ("id:validation-control-libavif-v1-2-1-avif",),
    "mp3": ("id:validation-control-minimp3-ill2-layer3",),
    "flac": ("id:validation-control-ietf-flac-mono-audio",),
    "mp4-av1": ("id:development-control-generated-mp4",),
}


SELECTED_SYNTHETIC: dict[str, tuple[str, ...]] = {
    "constant": ("id:dev-synthetic-constant-00",),
    "linear": ("id:dev-synthetic-linear",),
    "polynomial": ("id:dev-synthetic-polynomial-d2",),
    "periodic": ("id:dev-synthetic-periodic",),
    "recurrence": ("id:dev-synthetic-recurrence",),
    "lfsr": ("id:dev-synthetic-lfsr8",),
    "piecewise": ("id:dev-synthetic-piecewise-mixed",),
    "sparse-exceptions": (
        "id:dev-synthetic-linear-noise-0p001",
    ),
    "multiple-noise-levels": (
        "id:dev-synthetic-linear-noise-0p001",
        "id:dev-synthetic-linear-noise-0p25",
    ),
    "mixed-structured-random": (
        "id:dev-synthetic-piecewise-mixed",
    ),
}

REQUIRE_EVERY_SELECTOR = frozenset({"multiple-noise-levels"})


PINNED_RECIPES: dict[str, tuple[str, str]] = {
    "rust-crates": (
        SERDE_1_0_229.source_url,
        "Pinned serde 1.0.229 archive and 25 per-file Rust source checksums; "
        "recipe is not selected until materialized into validation.csv.",
    ),
}


INELIGIBLE_NOTES: dict[str, str] = {}


MIXED_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "json": ("files/sample.json",),
    "xml": ("files/sample.xml",),
    "csv": ("files/sample.csv",),
    "executables": ("files/program.elf",),
    "shared-libraries": ("files/libsample.so",),
    "sqlite": ("files/sample.sqlite",),
    "pdf": ("files/sample.pdf",),
    "pcm-wav": ("files/sample.wav",),
    "bmp": ("files/sample.bmp",),
    "cryptographic-random": ("files/random.bin",),
    "gzip": ("files/sample.gz",),
    "zstd": ("files/sample.zst",),
    "xz": ("files/sample.xz",),
    "zip": ("files/sample.zip",),
    "png": ("files/sample.png",),
    "jpeg": ("files/sample.jpg",),
    "mp4-av1": ("files/sample.mp4",),
}


SYNTHETIC_AVAILABLE: dict[str, tuple[tuple[str, float | None, str | None], ...]] = {
    "constant": (("constant_00", 0.0, None),),
    "linear": (("linear", 0.0, None),),
    "polynomial": (("polynomial_d2", 0.0, None),),
    "periodic": (("periodic", 0.0, None),),
    "recurrence": (("recurrence", 0.0, None),),
    "lfsr": (("lfsr8", 0.0, None),),
    "piecewise": (("piecewise_mixed", 0.0, None),),
    "sparse-exceptions": (("linear", 0.001, None),),
    "multiple-noise-levels": (
        ("linear", 0.001, None),
        ("linear", 0.25, None),
    ),
    "mixed-structured-random": (("piecewise_mixed", 0.0, None),),
    "cryptographic-random": (("random", 0.0, None),),
    "encrypted-data": (("encrypted", 0.0, None),),
    "gzip": (("already_compressed", None, "gz"),),
    "zstd": (("already_compressed", None, "zst"),),
    "xz": (("already_compressed", None, "xz"),),
    "zip": (("already_compressed", None, "zip"),),
    "png": (("already_compressed", None, "png"),),
    "jpeg": (("already_compressed", None, "jpg"),),
    "mp4-av1": (("already_compressed", None, "mp4"),),
}


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
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(f"{label} must be a safe relative POSIX path")
    result = pathlib.PurePosixPath(value)
    if result.is_absolute() or ".." in result.parts:
        raise ValueError(f"{label} must be a safe relative POSIX path")
    return result


def _load_json(path: pathlib.Path, schema: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        raise ValueError(f"{path}: expected schema_version={schema!r}")
    return value


def _verify_artifact(
    root: pathlib.Path, item: dict[str, Any], label: str
) -> str:
    relative = _safe_relative(item.get("path"), f"{label}.path")
    expected_bytes = item.get("size_bytes")
    expected_hash = item.get("sha256")
    if (
        not isinstance(expected_bytes, int)
        or expected_bytes < 0
        or not isinstance(expected_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
    ):
        raise ValueError(f"{label}: invalid size_bytes/sha256 declaration")
    payload = root.joinpath(*relative.parts)
    if not payload.is_file() or payload.is_symlink():
        raise ValueError(f"{label}: payload is absent or not a regular file")
    if payload.stat().st_size != expected_bytes:
        raise ValueError(f"{label}: payload byte count mismatch")
    if _sha256(payload) != expected_hash:
        raise ValueError(f"{label}: payload SHA-256 mismatch")
    return relative.as_posix()


def discover_local_diagnostics(
    repository: pathlib.Path,
) -> dict[str, tuple[str, ...]]:
    """Verify relevant generated metadata and payloads, never holdout paths."""

    available: dict[str, list[str]] = {}

    mixed_path = repository / "datasets/generated/mixed/manifest.json"
    mixed = _load_json(mixed_path, "mathzip-generated-mixed-v1")
    mixed_entries = mixed.get("entries")
    if not isinstance(mixed_entries, list):
        raise ValueError(f"{mixed_path}: entries must be an array")
    by_path: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(mixed_entries):
        if not isinstance(item, dict):
            raise ValueError(f"{mixed_path}: entries[{index}] is not an object")
        relative = _verify_artifact(
            mixed_path.parent, item, f"{mixed_path}.entries[{index}]"
        )
        if relative in by_path:
            raise ValueError(f"{mixed_path}: duplicate path {relative!r}")
        by_path[relative] = item
    for key, paths in MIXED_ARTIFACTS.items():
        present = [path for path in paths if path in by_path]
        if present:
            available.setdefault(key, []).extend(
                f"datasets/generated/mixed/{path}" for path in present
            )

    snapshots_path = (
        repository / "datasets/generated/git_snapshots/manifest.json"
    )
    snapshots = _load_json(
        snapshots_path, "mathzip-generated-git-snapshots-v1"
    )
    all_versions = snapshots.get("all_versions")
    if not isinstance(all_versions, dict):
        raise ValueError(f"{snapshots_path}: all_versions must be an object")
    relative = _verify_artifact(
        snapshots_path.parent,
        all_versions,
        f"{snapshots_path}.all_versions",
    )
    available.setdefault("git-snapshots", []).append(
        f"datasets/generated/git_snapshots/{relative}"
    )

    synthetic_path = repository / "datasets/synthetic/manifest.json"
    synthetic = _load_json(
        synthetic_path, "mathzip-synthetic-corpus-v1"
    )
    synthetic_entries = synthetic.get("entries")
    if not isinstance(synthetic_entries, list):
        raise ValueError(f"{synthetic_path}: entries must be an array")
    for key, queries in SYNTHETIC_AVAILABLE.items():
        found: list[str] = []
        for family, noise, container in queries:
            matches: list[tuple[int, dict[str, Any]]] = []
            for index, item in enumerate(synthetic_entries):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"{synthetic_path}: entries[{index}] is not an object"
                    )
                parameters = item.get("parameters")
                actual_container = (
                    parameters.get("container_type")
                    if isinstance(parameters, dict)
                    else None
                )
                if (
                    item.get("family") == family
                    and item.get("size_requested") == 65_536
                    and item.get("noise_density") == noise
                    and actual_container == container
                ):
                    matches.append((index, item))
            if len(matches) != 1:
                raise ValueError(
                    f"{synthetic_path}: query {(family, noise, container)!r} "
                    f"matched {len(matches)} rows"
                )
            index, item = matches[0]
            relative = _verify_artifact(
                synthetic_path.parent,
                item,
                f"{synthetic_path}.entries[{index}]",
            )
            found.append(f"datasets/synthetic/{relative}")
        available.setdefault(key, []).extend(found)

    return {
        key: tuple(sorted(set(paths))) for key, paths in available.items()
    }


def _matches(
    entries: Sequence[DatasetEntry], selectors: Sequence[str]
) -> list[DatasetEntry]:
    result: dict[str, DatasetEntry] = {}
    for selector in selectors:
        kind, value = selector.split(":", 1)
        for entry in entries:
            matched = (
                (kind == "id" and entry.dataset_id == value)
                or (kind == "prefix" and entry.dataset_id.startswith(value))
                or (kind == "domain" and entry.domain == value)
                or (kind == "group" and entry.split_group == value)
            )
            if matched:
                result[entry.dataset_id] = entry
    return sorted(result.values(), key=lambda entry: entry.dataset_id)


def _evidence_ids(entries: Sequence[DatasetEntry]) -> str:
    identifiers = [entry.dataset_id for entry in entries]
    if len(identifiers) <= 3:
        return ", ".join(identifiers)
    return ", ".join(identifiers[:3]) + f" (+{len(identifiers) - 3} more)"


def build_coverage_rows(
    entries: Sequence[DatasetEntry],
    available: dict[str, tuple[str, ...]],
) -> list[CoverageRow]:
    """Build strict rows; proxies and recipes never count as completion."""

    validate_collection(entries)
    rows: list[CoverageRow] = []
    for requirement in REQUIREMENTS:
        exact_selectors = (
            SELECTED_REAL
            if requirement.kind == "real"
            else SELECTED_CONTROL
            if requirement.kind == "control"
            else SELECTED_SYNTHETIC
        ).get(requirement.key, ())
        exact = _matches(entries, exact_selectors)
        if requirement.kind == "real":
            exact = [
                entry
                for entry in exact
                if entry.origin == "real" and entry.primary
            ]
        elif requirement.kind == "synthetic":
            exact = [
                entry
                for entry in exact
                if entry.origin == "synthetic" and not entry.primary
            ]
        else:
            exact = [
                entry
                for entry in exact
                if entry.origin == "control" and not entry.primary
            ]
        if requirement.key in REQUIRE_EVERY_SELECTOR:
            every_selector_present = all(
                _matches(exact, (selector,)) for selector in exact_selectors
            )
            if not every_selector_present:
                exact = []
        if exact:
            status = {
                "real": "selected-real",
                "control": "selected-control",
                "synthetic": "selected-synthetic",
            }[requirement.kind]
            rows.append(
                CoverageRow(
                    requirement.section,
                    requirement.label,
                    status,
                    True,
                    _evidence_ids(exact),
                    "Selected in development/validation manifest.",
                )
            )
            continue

        partial = PARTIAL_REAL.get(requirement.key)
        if requirement.kind == "real" and partial is not None:
            partial_entries = [
                entry
                for entry in _matches(entries, partial[0])
                if entry.origin == "real" and entry.primary
            ]
            if partial_entries:
                rows.append(
                    CoverageRow(
                        requirement.section,
                        requirement.label,
                        "selected-partial-real",
                        False,
                        _evidence_ids(partial_entries),
                        partial[1],
                    )
                )
                continue

        recipe = PINNED_RECIPES.get(requirement.key)
        if recipe is not None:
            rows.append(
                CoverageRow(
                    requirement.section,
                    requirement.label,
                    "pinned-recipe",
                    False,
                    recipe[0],
                    recipe[1],
                )
            )
            continue

        local = available.get(requirement.key, ())
        if local:
            status = (
                "available-control"
                if requirement.kind == "control"
                else "available-synthetic"
                if requirement.kind == "synthetic"
                else "available-derived"
            )
            note = {
                "real": (
                    "Integrity-verified local artifact, but not selected "
                    "upstream real data for this requirement."
                ),
                "control": (
                    "Integrity-verified control exists locally but is not "
                    "selected in a development/validation manifest."
                ),
                "synthetic": (
                    "Integrity-verified synthetic diagnostic exists locally "
                    "but is not selected in a development/validation manifest."
                ),
            }[requirement.kind]
            note = INELIGIBLE_NOTES.get(requirement.key, note)
            rows.append(
                CoverageRow(
                    requirement.section,
                    requirement.label,
                    status,
                    False,
                    ", ".join(local[:2])
                    + (f" (+{len(local) - 2} more)" if len(local) > 2 else ""),
                    note,
                )
            )
            continue

        rows.append(
            CoverageRow(
                requirement.section,
                requirement.label,
                "missing",
                False,
                "",
                INELIGIBLE_NOTES.get(
                    requirement.key,
                    "No eligible selected payload or pinned local recipe.",
                ),
            )
        )
    return rows


def duplicate_payload_groups(
    entries: Iterable[DatasetEntry],
) -> list[dict[str, object]]:
    by_hash: dict[str, list[DatasetEntry]] = {}
    for entry in entries:
        by_hash.setdefault(entry.sha256, []).append(entry)
    return [
        {
            "sha256": digest,
            "bytes": group[0].bytes,
            "dataset_ids": sorted(entry.dataset_id for entry in group),
        }
        for digest, group in sorted(by_hash.items())
        if len(group) > 1
    ]


def coverage_report(
    entries: Sequence[DatasetEntry],
    available: dict[str, tuple[str, ...]],
) -> dict[str, object]:
    rows = build_coverage_rows(entries, available)
    sections: dict[str, dict[str, int]] = {}
    for section in sorted({row.section for row in rows}):
        selected = [row for row in rows if row.section == section]
        sections[section] = {
            "requirements": len(selected),
            "met": sum(row.meets_requirement for row in selected),
            "missing_or_partial": sum(
                not row.meets_requirement for row in selected
            ),
        }
    return {
        "schema_version": 1,
        "requirement_source": "YeuCau.md section 20",
        "holdout_payload_inspected": False,
        "manifest_sha256": canonical_manifest_sha256(entries),
        "frozen_research_source_ids": sorted(FROZEN_RESEARCH_SOURCE_IDS),
        "frozen_local_control_ids": sorted(FROZEN_LOCAL_CONTROL_IDS),
        "composition": composition(entries),
        "sections": sections,
        "duplicate_payload_groups": duplicate_payload_groups(entries),
        "rows": [asdict(row) for row in rows],
    }


def render_markdown(report_value: dict[str, object]) -> str:
    rows = report_value["rows"]
    assert isinstance(rows, list)
    sections = report_value["sections"]
    assert isinstance(sections, dict)
    lines = [
        "# MathSVG §20 dataset coverage",
        "",
        "Generated from development/validation manifest metadata plus "
        "integrity-verified local generated catalogues. The generator does "
        "**not** inspect sealed holdout payloads.",
        "",
        "A row is complete only when `meets_requirement=yes`. Generated "
        "or derived artifacts remain useful diagnostics but do not masquerade "
        "as real-world data.",
        "",
        "## Summary",
        "",
        "| Section | Met | Required | Gap |",
        "|---|---:|---:|---:|",
    ]
    for section, value in sorted(sections.items()):
        assert isinstance(value, dict)
        lines.append(
            f"| {section} | {value['met']} | {value['requirements']} | "
            f"{value['missing_or_partial']} |"
        )

    current_section = ""
    for row_value in rows:
        assert isinstance(row_value, dict)
        section = str(row_value["section"])
        if section != current_section:
            current_section = section
            lines.extend(
                (
                    "",
                    f"## {section}",
                    "",
                    "| Requirement | Status | Meets | Evidence | Note |",
                    "|---|---|:---:|---|---|",
                )
            )
        escaped = {
            key: str(row_value[key]).replace("|", "\\|").replace("\n", " ")
            for key in ("requirement", "status", "evidence", "note")
        }
        meets = "yes" if row_value["meets_requirement"] else "no"
        lines.append(
            f"| {escaped['requirement']} | {escaped['status']} | {meets} | "
            f"{escaped['evidence']} | {escaped['note']} |"
        )

    duplicates = report_value["duplicate_payload_groups"]
    assert isinstance(duplicates, list)
    lines.extend(
        (
            "",
            "## Integrity notes",
            "",
            f"- Exact duplicate payload groups in the selected manifests: "
            f"{len(duplicates)}.",
            "- The development builder rejects unpinned generated paths and "
            "deduplicates exact upstream payload bytes before applying the "
            "70% rule.",
            "- `origin=derived`, `origin=synthetic`, and `origin=control` "
            "are always non-primary.",
            "",
        )
    )
    return "\n".join(lines)


def _atomic_write(path: pathlib.Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing to replace symlink: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[3],
    )
    parser.add_argument(
        "--development-manifest",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/manifests/development.csv"
        ),
    )
    parser.add_argument(
        "--validation-manifest",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/manifests/validation.csv"),
    )
    parser.add_argument(
        "--output-json",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/manifests/dataset-coverage.json"
        ),
    )
    parser.add_argument(
        "--output-markdown",
        type=pathlib.Path,
        default=pathlib.Path("datasets/COVERAGE.md"),
    )
    return parser.parse_args(argv)


def _under(repository: pathlib.Path, configured: pathlib.Path) -> pathlib.Path:
    result = configured if configured.is_absolute() else repository / configured
    normalized = pathlib.Path(os.path.abspath(result))
    try:
        normalized.relative_to(repository)
    except ValueError as exc:
        raise ValueError(f"path must remain inside repository: {configured}") from exc
    return normalized


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    repository = args.repository.resolve()
    try:
        development = load_manifest(
            _under(repository, args.development_manifest)
        )
        validation = load_manifest(
            _under(repository, args.validation_manifest)
        )
        entries = [*development, *validation]
        validate_collection(entries)
        available = discover_local_diagnostics(repository)
        result = coverage_report(entries, available)
        _atomic_write(
            _under(repository, args.output_json),
            json.dumps(result, indent=2, sort_keys=True) + "\n",
        )
        _atomic_write(
            _under(repository, args.output_markdown),
            render_markdown(result),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"dataset coverage error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result["sections"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
