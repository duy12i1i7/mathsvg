#!/usr/bin/env python3
"""Generate deterministic current-engine MathSVG development oracle evidence.

Only frozen development/legacy-observed inputs are opened. A standalone Rust
probe calls the production public APIs, measures complete native v1 archives,
and includes the Canonical Huffman and LZ-Huffman literal leaves. The older
Python reference model remains unit-test infrastructure; it no longer decides
emission or stop policy.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Iterable, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oracle.model import (  # noqa: E402
    ADD,
    XOR,
    Candidate,
    algebra_node,
    best_coordinate,
    choose_direct_representation,
    choose_representation,
    coordinate_candidates,
    dag_sharing_oracle,
    decode_exact,
    entropy_lower_bound,
    family_candidate,
    literal_node,
    predictor_candidates,
    segmentation_oracle,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_BYTES = 4096
ORACLE_SCHEMA = "mathsvg-development-oracle-v2"
NATIVE_PROBE_SCHEMA = "mathsvg-native-development-oracle-v2"
CATALOG = (
    "native-v1:literal,entropy(raw,byte-rle,zero-run,sparse-zero,bit-pack,"
    "lz-tokens,canonical-huffman,lz-huffman),const,linear,periodic<=64,"
    "recurrence<=4,exceptions,residual-add/xor-depth<=2,symbolic-depth<=2,"
    "dag-exact-chunk-activation,segmentation-256B,coordinate-basis"
)
NATIVE_PROBE_RELATIVE = Path("mathsvg/python/oracle/native_probe/Cargo.toml")
NATIVE_OUTPUT_NAME = "native-development-oracle.json"

DATASET_FIELDS = (
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

SEARCH_FIELDS = (
    "oracle_kind",
    "scope",
    "input_id",
    "origin",
    "status",
    "baseline",
    "candidate_catalog",
    "baseline_bytes",
    "oracle_bytes",
    "headroom_bytes",
    "headroom_percent",
    "winner",
    "winner_entropy_opcode",
    "evaluated_candidates",
    "work_used",
    "budget_exhausted",
    "complete_within_declared_catalogue",
    "source",
    "notes",
)
COORDINATE_FIELDS = (
    "oracle_kind",
    "input_id",
    "origin",
    "sample_bytes",
    "status",
    "baseline",
    "baseline_bytes",
    "oracle_bytes",
    "headroom_bytes",
    "headroom_percent",
    "winner",
    "evaluated_candidates",
    "no_coordinate_bytes",
    "whole_coordinate_bytes",
    "basis_bytes",
    "basis_incremental_winner",
    "budget_exhausted",
    "complete_within_declared_catalogue",
    "source",
    "notes",
)
SEGMENTATION_FIELDS = (
    "input_id",
    "origin",
    "sample_bytes",
    "status",
    "baseline",
    "baseline_bytes",
    "oracle_bytes",
    "headroom_bytes",
    "headroom_percent",
    "segment_count",
    "boundaries",
    "leaf_winners",
    "quantum_bytes",
    "work_used",
    "ledger_entries",
    "budget_exhausted",
    "complete_within_declared_catalogue",
    "source",
    "notes",
)
RESIDUAL_FIELDS = (
    "row_kind",
    "input_id",
    "origin",
    "sample_bytes",
    "status",
    "depth_limit",
    "predictor",
    "domain",
    "residual_layers",
    "residual_entropy_bits",
    "entropy_lower_bound_bytes",
    "literal_correction_bytes",
    "baseline_depth0_bytes",
    "complete_candidate_bytes",
    "headroom_bytes",
    "headroom_percent",
    "winner",
    "states_visited",
    "work_used",
    "ledger_entries",
    "budget_exhausted",
    "heuristic_omission",
    "complete_within_declared_catalogue",
    "source",
    "notes",
)
FUNCTION_FIELDS = (
    "evidence_kind",
    "input_id",
    "origin",
    "sample_bytes",
    "status",
    "family",
    "baseline",
    "baseline_bytes",
    "oracle_bytes",
    "headroom_bytes",
    "headroom_percent",
    "winner",
    "complete_within_declared_catalogue",
    "source",
    "notes",
)
SYMBOLIC_FIELDS = (
    "input_id",
    "origin",
    "sample_bytes",
    "scope",
    "status",
    "depth_budget",
    "node_budget",
    "baseline_bytes",
    "oracle_bytes",
    "headroom_bytes",
    "headroom_percent",
    "winner",
    "states_retained",
    "pairs_considered",
    "work_used",
    "ledger_entries",
    "budget_exhausted",
    "complete_within_declared_catalogue",
    "source",
    "reason",
)
DAG_FIELDS = (
    "input_id",
    "origin",
    "sample_bytes",
    "status",
    "block_count",
    "inline_bytes",
    "graph_candidate_bytes",
    "oracle_bytes",
    "headroom_bytes",
    "headroom_percent",
    "active_definitions",
    "referenced_source_bytes",
    "evaluated_subsets",
    "work_used",
    "ledger_entries",
    "budget_exhausted",
    "complete_within_declared_catalogue",
    "source",
    "notes",
)
EXTERNAL_FIELDS = (
    "scope",
    "status",
    "current_codec",
    "baseline_codec",
    "input_count",
    "original_bytes",
    "current_bytes",
    "baseline_bytes",
    "size_gap_bytes",
    "size_gap_percent",
    "compression_slowdown",
    "decompression_slowdown",
    "peak_rss_ratio",
    "source",
    "notes",
)
STOP_POLICY_FIELDS = (
    "algorithm",
    "status",
    "evaluation_rows",
    "real_rows",
    "complete_rows",
    "winning_rows",
    "real_winning_rows",
    "qualifying_real_rows",
    "minimum_gain_fraction",
    "baseline_bytes",
    "oracle_bytes",
    "headroom_bytes",
    "headroom_percent",
    "decision",
    "holdout_status",
    "reason",
)


@dataclass(frozen=True)
class SampleSpec:
    input_id: str
    path: str
    origin: str
    domain: str
    split_group: str
    source: str
    license: str


@dataclass(frozen=True)
class Sample:
    spec: SampleSpec
    data: bytes
    source_bytes: int
    source_sha256: str
    sample_sha256: str


SPECS = (
    SampleSpec(
        "syn-constant",
        "datasets/synthetic/constant_00/s0000004096_n000p000_seed1297748005.bin",
        "synthetic",
        "exact-generator",
        "legacy-synthetic",
        "python/mathzip_bench/synthetic.py",
        "CC0-1.0",
    ),
    SampleSpec(
        "syn-linear",
        "datasets/synthetic/linear/s0000004096_n000p000_seed1297748005.bin",
        "synthetic",
        "exact-generator",
        "legacy-synthetic",
        "python/mathzip_bench/synthetic.py",
        "CC0-1.0",
    ),
    SampleSpec(
        "syn-periodic",
        "datasets/synthetic/periodic/s0000004096_n000p000_seed1297748005.bin",
        "synthetic",
        "exact-generator",
        "legacy-synthetic",
        "python/mathzip_bench/synthetic.py",
        "CC0-1.0",
    ),
    SampleSpec(
        "syn-recurrence",
        "datasets/synthetic/recurrence/s0000004096_n000p000_seed1297748005.bin",
        "synthetic",
        "exact-generator",
        "legacy-synthetic",
        "python/mathzip_bench/synthetic.py",
        "CC0-1.0",
    ),
    SampleSpec(
        "syn-piecewise",
        "datasets/synthetic/piecewise_mixed/s0000004096_n000p000_seed1297748005.bin",
        "synthetic",
        "mixed-generator",
        "legacy-synthetic",
        "python/mathzip_bench/synthetic.py",
        "CC0-1.0",
    ),
    SampleSpec(
        "syn-random",
        "datasets/synthetic/random/s0000004096_n000p000_seed1297748005.bin",
        "control",
        "incompressible-control",
        "legacy-synthetic",
        "python/mathzip_bench/synthetic.py",
        "CC0-1.0",
    ),
    SampleSpec(
        "syn-linear-noise1",
        "datasets/synthetic/linear/s0000004096_n001p000_seed1297748005.bin",
        "synthetic",
        "noisy-generator",
        "legacy-synthetic",
        "python/mathzip_bench/synthetic.py",
        "CC0-1.0",
    ),
    SampleSpec(
        "syn-periodic-noise1",
        "datasets/synthetic/periodic/s0000004096_n001p000_seed1297748005.bin",
        "synthetic",
        "noisy-generator",
        "legacy-synthetic",
        "python/mathzip_bench/synthetic.py",
        "CC0-1.0",
    ),
    SampleSpec(
        "syn-recurrence-noise1",
        "datasets/synthetic/recurrence/s0000004096_n001p000_seed1297748005.bin",
        "synthetic",
        "noisy-generator",
        "legacy-synthetic",
        "python/mathzip_bench/synthetic.py",
        "CC0-1.0",
    ),
    SampleSpec(
        "real-canterbury-alice",
        "datasets/data/canterbury/alice29.txt",
        "real",
        "text",
        "legacy-canterbury",
        "https://corpus.canterbury.ac.nz/resources/cantrbry.tar.gz",
        "upstream-individual-terms",
    ),
    SampleSpec(
        "real-canterbury-kennedy",
        "datasets/data/canterbury/kennedy.xls",
        "real",
        "spreadsheet",
        "legacy-canterbury",
        "https://corpus.canterbury.ac.nz/resources/cantrbry.tar.gz",
        "upstream-individual-terms",
    ),
    SampleSpec(
        "real-calgary-pic",
        "datasets/data/calgary/pic",
        "real",
        "bitmap",
        "legacy-calgary",
        "https://corpus.canterbury.ac.nz/resources/calgary.tar.gz",
        "upstream-individual-terms",
    ),
    SampleSpec(
        "real-calgary-progc",
        "datasets/data/calgary/progc",
        "real",
        "source-code",
        "legacy-calgary",
        "https://corpus.canterbury.ac.nz/resources/calgary.tar.gz",
        "upstream-individual-terms",
    ),
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentage(gain: int | float, baseline: int | float) -> str:
    if not baseline:
        return "0.000000000"
    return f"{100.0 * gain / baseline:.9f}"


def decimal(value: float) -> str:
    return f"{value:.9f}"


def load_samples(repo_root: Path) -> list[Sample]:
    samples = []
    for spec in SPECS:
        path = repo_root / spec.path
        source = path.read_bytes()
        data = source[:SAMPLE_BYTES]
        if not data:
            raise ValueError(f"oracle sample is empty: {spec.path}")
        samples.append(
            Sample(
                spec,
                data,
                len(source),
                sha256_bytes(source),
                sha256_bytes(data),
            )
        )
    return samples


def load_native_probe(
    repo_root: Path, native_probe_json: Path | None = None
) -> dict[str, object]:
    """Run or load the production Rust oracle probe and validate its boundary."""
    if native_probe_json is None:
        command = [
            "cargo",
            "run",
            "--release",
            "--manifest-path",
            str(repo_root / NATIVE_PROBE_RELATIVE),
            "--locked",
            "--offline",
            "--",
            str(repo_root),
        ]
        completed = subprocess.run(
            command,
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        payload = json.loads(completed.stdout)
    else:
        payload = json.loads(native_probe_json.resolve().read_text(encoding="utf-8"))

    if payload.get("schema") != NATIVE_PROBE_SCHEMA:
        raise ValueError(
            f"native probe schema mismatch: {payload.get('schema')!r}"
        )
    if payload.get("holdout_payload_inspected") is not False:
        raise ValueError("native development probe crossed the holdout boundary")
    expected = {spec.input_id: spec for spec in SPECS}
    expected_ids = set(expected)
    samples = payload.get("samples")
    if not isinstance(samples, list):
        raise ValueError("native probe samples must be a list")
    actual_ids = {
        str(row.get("input_id"))
        for row in samples
        if isinstance(row, Mapping)
    }
    if actual_ids != expected_ids or len(samples) != len(expected_ids):
        raise ValueError(
            "native probe input set differs from the frozen development sample set"
        )
    for row in samples:
        assert isinstance(row, Mapping)
        spec = expected[str(row["input_id"])]
        if row.get("source") != spec.path or row.get("origin") != spec.origin:
            raise ValueError("native probe input provenance differs from frozen specs")
        if int(row.get("sample_bytes", 0)) != SAMPLE_BYTES:
            raise ValueError("native probe sample is not the frozen 4KiB microblock")
        expected_sample = (repo_root / spec.path).read_bytes()[:SAMPLE_BYTES]
        if row.get("sample_sha256") != sha256_bytes(expected_sample):
            raise ValueError("native probe sample digest differs from frozen input")
        source = str(row.get("source", "")).lower()
        if "holdout" in source or "sealed" in source:
            raise ValueError("native probe source crossed the holdout boundary")
    coordinate = payload.get("coordinate_basis_blocks")
    if not isinstance(coordinate, list) or len(coordinate) != 73:
        raise ValueError("native coordinate-basis probe must contain 73 real blocks")
    if any(
        not isinstance(row, Mapping)
        or row.get("origin") != "real"
        or "holdout" in str(row.get("source", "")).lower()
        or "sealed" in str(row.get("source", "")).lower()
        for row in coordinate
    ):
        raise ValueError("coordinate-basis stop scan must contain only real rows")
    coordinate_counts = Counter(str(row["source"]) for row in coordinate)
    if coordinate_counts != {
        "datasets/data/canterbury/kennedy.xls": 32,
        "datasets/data/calgary/progc": 9,
        "datasets/data/canterbury/alice29.txt": 32,
    }:
        raise ValueError("coordinate-basis scan composition differs from frozen set")
    if any(int(row.get("sample_bytes", 0)) != SAMPLE_BYTES for row in coordinate):
        raise ValueError("coordinate-basis scan contains a non-4KiB block")
    entropy_catalog = str(payload.get("entropy_catalog", ""))
    for required in ("canonical-huffman", "lz-huffman"):
        if required not in entropy_catalog:
            raise ValueError(f"native entropy catalogue omits {required}")
    return payload


def write_native_probe(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def write_csv(
    path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, object]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [
        {field: _csv_value(row.get(field, "")) for field in fields}
        for row in rows
    ]
    normalized.sort(
        key=lambda row: json.dumps(
            row, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(fields), lineterminator="\n", extrasaction="raise"
        )
        writer.writeheader()
        writer.writerows(normalized)


def copy_alias(source: Path, target: Path) -> None:
    # Writing exact bytes, rather than a symlink, keeps result bundles portable.
    target.write_bytes(source.read_bytes())


def dataset_manifest_rows(samples: Sequence[Sample]) -> list[dict[str, object]]:
    rows = []
    for sample in samples:
        rows.append(
            {
                "split": "development",
                "dataset_id": sample.spec.input_id,
                "split_group": sample.spec.split_group,
                "origin": sample.spec.origin,
                "primary": sample.spec.origin == "real",
                "domain": sample.spec.domain,
                "source": sample.spec.source,
                "license": sample.spec.license,
                "sha256": sample.source_sha256,
                "bytes": sample.source_bytes,
                "sealed": False,
                "path": sample.spec.path,
                "legacy_observed": True,
            }
        )
    return rows


def finite_search_rows(samples: Sequence[Sample]) -> list[dict[str, object]]:
    rows = []
    for sample in samples:
        baseline = len(literal_node(sample.data))
        candidates = []
        for depth in range(3):
            candidate = choose_representation(sample.data, depth)
            candidates.append(candidate)
        winner = min(candidates, key=lambda item: (item.size, item.work, item.nodes, item.blob))
        gain = baseline - winner.size
        rows.append(
            {
                "oracle_kind": "finite_reference_exact",
                "scope": "development-microblock",
                "input_id": sample.spec.input_id,
                "origin": sample.spec.origin,
                "status": "ok",
                "baseline": "complete-literal-node",
                "candidate_catalog": CATALOG,
                "baseline_bytes": baseline,
                "oracle_bytes": winner.size,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, baseline),
                "winner": winner.name,
                "evaluated_candidates": "depth0,depth1,depth2",
                "source": sample.spec.path,
                "notes": (
                    "Exact complete root-node bytes; common container/block bytes omitted "
                    "from both sides."
                ),
            }
        )
    return rows


def coordinate_rows(samples: Sequence[Sample]) -> list[dict[str, object]]:
    rows = []
    for sample in samples:
        candidates = coordinate_candidates(sample.data, 0)
        identity = min(
            (candidate for candidate in candidates if candidate.name.startswith("identity:")),
            key=lambda candidate: (candidate.size, candidate.blob),
        )
        winner = best_coordinate(sample.data, 0)
        gain = identity.size - winner.size
        rows.append(
            {
                "input_id": sample.spec.input_id,
                "origin": sample.spec.origin,
                "sample_bytes": len(sample.data),
                "status": "ok",
                "baseline": identity.name,
                "baseline_bytes": identity.size,
                "oracle_bytes": winner.size,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, identity.size),
                "winner": winner.name,
                "evaluated_candidates": len(candidates),
                "source": sample.spec.path,
                "notes": (
                    "Identity/stride{2,3,4,8,16}/byte-plane widths "
                    "{2,3,4,6,8} both endian/bit-plane; complete TLV bytes."
                ),
            }
        )
    return rows


def finite_segmentation_rows(samples: Sequence[Sample]) -> list[dict[str, object]]:
    selected = {
        "syn-piecewise",
        "syn-linear-noise1",
        "real-canterbury-alice",
        "real-canterbury-kennedy",
        "real-calgary-pic",
    }
    rows = []
    for sample in samples:
        if sample.spec.input_id not in selected:
            continue
        # The DP leaf catalogue is literal plus exact generators.  Its unsplit
        # state must use the identical catalogue or a negative "oracle gain"
        # would merely be a baseline mismatch.
        baseline_candidate = choose_direct_representation(sample.data)
        oracle = segmentation_oracle(sample.data, quantum=256, residual_depth=0)
        gain = baseline_candidate.size - oracle.size
        rows.append(
            {
                "input_id": sample.spec.input_id,
                "origin": sample.spec.origin,
                "sample_bytes": len(sample.data),
                "status": "ok",
                "baseline": f"unsplit:{baseline_candidate.name}",
                "baseline_bytes": baseline_candidate.size,
                "oracle_bytes": oracle.size,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, baseline_candidate.size),
                "segment_count": len(oracle.boundaries) - 1,
                "boundaries": ";".join(str(value) for value in oracle.boundaries),
                "leaf_winners": ";".join(oracle.leaf_names),
                "quantum_bytes": 256,
                "source": sample.spec.path,
                "notes": (
                    "Exact DP over all intervals on the 256-byte grid and all leaf "
                    "counts; complete CONCAT TLV serialized for every final state."
                ),
            }
        )
    return rows


def residual_rows(samples: Sequence[Sample]) -> list[dict[str, object]]:
    selected = {
        "syn-linear-noise1",
        "syn-periodic-noise1",
        "syn-recurrence-noise1",
        "real-canterbury-alice",
        "real-canterbury-kennedy",
    }
    rows = []
    for sample in samples:
        if sample.spec.input_id not in selected:
            continue
        depth0 = choose_representation(sample.data, 0)
        for depth in (0, 1, 2):
            candidate = choose_representation(sample.data, depth)
            if candidate.residual_layers:
                first = candidate.residual_layers[0]
                entropy_bits, entropy_bytes = entropy_lower_bound(first.residual)
                predictor = first.predictor
                domain = first.domain
            else:
                entropy_bits, entropy_bytes = 0.0, 0
                predictor = ""
                domain = ""
            gain = depth0.size - candidate.size
            rows.append(
                {
                    "row_kind": "depth_winner",
                    "input_id": sample.spec.input_id,
                    "origin": sample.spec.origin,
                    "sample_bytes": len(sample.data),
                    "status": "ok",
                    "depth_limit": depth,
                    "predictor": predictor,
                    "domain": domain,
                    "residual_layers": len(candidate.residual_layers),
                    "residual_entropy_bits": decimal(entropy_bits),
                    "entropy_lower_bound_bytes": entropy_bytes,
                    "literal_correction_bytes": "",
                    "baseline_depth0_bytes": depth0.size,
                    "complete_candidate_bytes": candidate.size,
                    "headroom_bytes": gain,
                    "headroom_percent": percentage(gain, depth0.size),
                    "winner": candidate.name,
                    "source": sample.spec.path,
                    "notes": (
                        "Winner among complete serialized candidates. Entropy is the "
                        "zero-order lower bound for the first selected correction only."
                    ),
                }
            )
        for predictor in predictor_candidates(sample.data):
            for opcode, domain in ((XOR, "xor"), (ADD, "add_mod_256")):
                if opcode == XOR:
                    residual = bytes(
                        actual ^ predicted
                        for actual, predicted in zip(sample.data, predictor.output)
                    )
                else:
                    residual = bytes(
                        (actual - predicted) & 0xFF
                        for actual, predicted in zip(sample.data, predictor.output)
                    )
                child = choose_representation(residual, 1)
                blob = algebra_node(opcode, predictor.blob, child.blob)
                if decode_exact(blob) != sample.data:
                    raise AssertionError("residual projection did not round trip")
                entropy_bits, entropy_bytes = entropy_lower_bound(residual)
                literal_bytes = len(literal_node(residual))
                gain = depth0.size - len(blob)
                rows.append(
                    {
                        "row_kind": "projection",
                        "input_id": sample.spec.input_id,
                        "origin": sample.spec.origin,
                        "sample_bytes": len(sample.data),
                        "status": "ok",
                        "depth_limit": 2,
                        "predictor": predictor.name,
                        "domain": domain,
                        "residual_layers": 1 + len(child.residual_layers),
                        "residual_entropy_bits": decimal(entropy_bits),
                        "entropy_lower_bound_bytes": entropy_bytes,
                        "literal_correction_bytes": literal_bytes,
                        "baseline_depth0_bytes": depth0.size,
                        "complete_candidate_bytes": len(blob),
                        "headroom_bytes": gain,
                        "headroom_percent": percentage(gain, depth0.size),
                        "winner": f"{domain}({predictor.name},{child.name})",
                        "source": sample.spec.path,
                        "notes": (
                            "Entropy bound excludes model and entropy metadata and is "
                            "not presented as an achievable archive size."
                        ),
                    }
                )
    return rows


def function_rows(samples: Sequence[Sample]) -> list[dict[str, object]]:
    rows = []
    families = ("const", "linear", "periodic", "recurrence")
    for sample in samples:
        baseline = len(literal_node(sample.data))
        for family in families:
            winner = family_candidate(sample.data, family)
            gain = baseline - winner.size
            rows.append(
                {
                    "evidence_kind": "finite_reference_exact",
                    "input_id": sample.spec.input_id,
                    "origin": sample.spec.origin,
                    "sample_bytes": len(sample.data),
                    "status": "ok",
                    "family": family,
                    "baseline": "complete-literal-node",
                    "baseline_bytes": baseline,
                    "oracle_bytes": winner.size,
                    "headroom_bytes": gain,
                    "headroom_percent": percentage(gain, baseline),
                    "winner": winner.name,
                    "source": sample.spec.path,
                    "notes": (
                        "Family competes with literal and may use one exact EXCEPTIONS "
                        "wrapper; complete root-node serialization."
                    ),
                }
            )
    return rows


def dag_rows(samples: Sequence[Sample]) -> list[dict[str, object]]:
    by_id = {sample.spec.input_id: sample for sample in samples}
    workloads = (
        (
            "dag-real-alice-repeat4",
            "control",
            [by_id["real-canterbury-alice"].data[:1024]] * 4,
            by_id["real-canterbury-alice"].spec.path,
            (
                "Artificial development-derived stress test: one real-origin block "
                "was repeated in memory four times; this is not a natural real input."
            ),
        ),
        (
            "dag-random-repeat4",
            "control",
            [by_id["syn-random"].data[:1024]] * 4,
            by_id["syn-random"].spec.path,
            "Four exact calls to one deterministic control block.",
        ),
        (
            "dag-mixed-distinct",
            "synthetic",
            [
                by_id["syn-constant"].data[:1024],
                by_id["syn-linear"].data[:1024],
                by_id["syn-periodic"].data[:1024],
                by_id["syn-recurrence"].data[:1024],
            ],
            "development-derived in-memory composition",
            "Negative control with four distinct procedural block nodes.",
        ),
    )
    rows = []
    for input_id, origin, blocks, source, notes in workloads:
        result = dag_sharing_oracle(blocks)
        gain = result.inline_size - result.oracle_size
        rows.append(
            {
                "input_id": input_id,
                "origin": origin,
                "sample_bytes": sum(map(len, blocks)),
                "status": (
                    "development_derived_exact"
                    if input_id != "dag-mixed-distinct"
                    else "ok_negative_control"
                ),
                "block_count": len(blocks),
                "inline_bytes": result.inline_size,
                "oracle_bytes": result.oracle_size,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, result.inline_size),
                "active_definitions": result.active_definitions,
                "source": source,
                "notes": (
                    notes
                    + " All definition activation subsets are serialized; reference "
                    "and SHARE bytes are included."
                ),
            }
        )
    return rows


def _native_samples(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    rows = payload["samples"]
    if not isinstance(rows, list):
        raise ValueError("native samples are not a list")
    return [row for row in rows if isinstance(row, Mapping)]


def _native_status(row: Mapping[str, object]) -> str:
    complete = bool(row.get("complete_within_declared_catalogue"))
    exhausted = bool(row.get("budget_exhausted"))
    return "ok" if complete and not exhausted else "bounded_incomplete"


def native_search_rows(payload: Mapping[str, object]) -> list[dict[str, object]]:
    rows = []
    for sample in _native_samples(payload):
        measured = sample["search"]
        if not isinstance(measured, Mapping):
            raise ValueError("native search row is not a mapping")
        baseline = int(measured["literal_archive_bytes"])
        oracle = int(measured["winner_archive_bytes"])
        gain = baseline - oracle
        rows.append(
            {
                "oracle_kind": "native_complete_archive_bounded_search",
                "scope": "development-microblock",
                "input_id": sample["input_id"],
                "origin": sample["origin"],
                "status": _native_status(measured),
                "baseline": "native-complete-raw-literal-archive",
                "candidate_catalog": CATALOG,
                "baseline_bytes": baseline,
                "oracle_bytes": oracle,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, baseline),
                "winner": measured["winner_family"],
                "winner_entropy_opcode": measured.get("winner_entropy_opcode", ""),
                "evaluated_candidates": measured["ledger_entries"],
                "work_used": measured["work_used"],
                "budget_exhausted": measured["budget_exhausted"],
                "complete_within_declared_catalogue": measured[
                    "complete_within_declared_catalogue"
                ],
                "source": sample["source"],
                "notes": (
                    "Native Rust v1 complete archive bytes. Entropy opcode 0x06 is "
                    "canonical Huffman and 0x07 is LZ-Huffman. Bounded catalogue only."
                ),
            }
        )
    return rows


def native_coordinate_rows(
    payload: Mapping[str, object],
) -> list[dict[str, object]]:
    raw_rows = payload["coordinate_basis_blocks"]
    if not isinstance(raw_rows, list):
        raise ValueError("native coordinate rows are not a list")
    rows = []
    for measured in raw_rows:
        if not isinstance(measured, Mapping):
            raise ValueError("native coordinate row is not a mapping")
        no_coordinate = int(measured["no_coordinate_archive_bytes"])
        whole_value = measured.get("whole_coordinate_archive_bytes")
        basis_value = measured.get("basis_archive_bytes")
        whole = int(whole_value) if whole_value is not None else no_coordinate
        basis = int(basis_value) if basis_value is not None else no_coordinate
        baseline = min(no_coordinate, whole)
        oracle = min(baseline, basis)
        gain = baseline - oracle
        complete = bool(
            measured["whole_complete_within_declared_catalogue"]
            and measured["basis_complete_within_declared_catalogue"]
        )
        exhausted = bool(
            measured["whole_budget_exhausted"]
            or measured["basis_budget_exhausted"]
        )
        winner = (
            "coordinate-basis"
            if basis < baseline
            else ("whole-coordinate" if whole < no_coordinate else "no-coordinate")
        )
        rows.append(
            {
                "oracle_kind": "native_coordinate_basis_incremental_ablation",
                "input_id": measured["input_id"],
                "origin": measured["origin"],
                "sample_bytes": measured["sample_bytes"],
                "status": "ok" if complete and not exhausted else "bounded_incomplete",
                "baseline": "min(no-coordinate,whole-coordinate)",
                "baseline_bytes": baseline,
                "oracle_bytes": oracle,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, baseline),
                "winner": winner,
                "evaluated_candidates": 3,
                "no_coordinate_bytes": no_coordinate,
                "whole_coordinate_bytes": (
                    whole_value if whole_value is not None else ""
                ),
                "basis_bytes": basis_value if basis_value is not None else "",
                "basis_incremental_winner": measured[
                    "basis_is_incremental_winner"
                ],
                "budget_exhausted": exhausted,
                "complete_within_declared_catalogue": complete,
                "source": measured["source"],
                "notes": (
                    "Frozen 73-block real development scan. Each value is a complete "
                    "native archive under the current Canonical/LZ-Huffman function leaf."
                ),
            }
        )
    return rows


def native_segmentation_rows(
    payload: Mapping[str, object],
) -> list[dict[str, object]]:
    rows = []
    for sample in _native_samples(payload):
        measured = sample["segmentation"]
        if not isinstance(measured, Mapping):
            raise ValueError("native segmentation row is not a mapping")
        baseline = int(measured["baseline_archive_bytes"])
        oracle = int(measured["oracle_archive_bytes"])
        gain = baseline - oracle
        rows.append(
            {
                "input_id": sample["input_id"],
                "origin": sample["origin"],
                "sample_bytes": sample["sample_bytes"],
                "status": _native_status(measured),
                "baseline": "native-unsplit-current-entropy",
                "baseline_bytes": baseline,
                "oracle_bytes": oracle,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, baseline),
                "segment_count": measured["segment_count"],
                "boundaries": ";".join(
                    str(value) for value in measured["boundaries"]
                ),
                "leaf_winners": ";".join(measured["selected_providers"]),
                "quantum_bytes": measured["quantum_bytes"],
                "work_used": measured["work_used"],
                "ledger_entries": measured["ledger_entries"],
                "budget_exhausted": measured["budget_exhausted"],
                "complete_within_declared_catalogue": measured[
                    "complete_within_declared_catalogue"
                ],
                "source": sample["source"],
                "notes": (
                    "Production optimizer DP at 256-byte quantum and Balanced hard caps; "
                    "a bounded-incomplete row is not an exact segmentation optimum."
                ),
            }
        )
    return rows


def native_residual_rows(payload: Mapping[str, object]) -> list[dict[str, object]]:
    rows = []
    for sample in _native_samples(payload):
        measured_rows = sample["residual"]
        if not isinstance(measured_rows, list):
            raise ValueError("native residual rows are not a list")
        for measured in measured_rows:
            if not isinstance(measured, Mapping):
                raise ValueError("native residual row is not a mapping")
            baseline = int(measured["baseline_function_archive_bytes"])
            oracle = int(measured["winner_archive_bytes"])
            gain = baseline - oracle
            rows.append(
                {
                    "row_kind": "native_depth_winner",
                    "input_id": sample["input_id"],
                    "origin": sample["origin"],
                    "sample_bytes": sample["sample_bytes"],
                    "status": _native_status(measured),
                    "depth_limit": measured["depth_limit"],
                    "predictor": ";".join(measured["winner_projections"]),
                    "domain": ";".join(measured["winner_domains"]),
                    "residual_layers": measured["winner_residual_depth"],
                    "residual_entropy_bits": "",
                    "entropy_lower_bound_bytes": "",
                    "literal_correction_bytes": "",
                    "baseline_depth0_bytes": baseline,
                    "complete_candidate_bytes": oracle,
                    "headroom_bytes": gain,
                    "headroom_percent": percentage(gain, baseline),
                    "winner": f"native-depth-{measured['winner_residual_depth']}",
                    "states_visited": measured["states_visited"],
                    "work_used": measured["work_used"],
                    "ledger_entries": measured["ledger_entries"],
                    "budget_exhausted": measured["budget_exhausted"],
                    "heuristic_omission": measured["heuristic_omission"],
                    "complete_within_declared_catalogue": measured[
                        "complete_within_declared_catalogue"
                    ],
                    "source": sample["source"],
                    "notes": (
                        "Complete native archive competition against the current "
                        "function/entropy winner; no entropy lower bound is substituted."
                    ),
                }
            )
    return rows


def native_function_rows(payload: Mapping[str, object]) -> list[dict[str, object]]:
    rows = []
    for sample in _native_samples(payload):
        measured = sample["search"]
        if not isinstance(measured, Mapping):
            raise ValueError("native function row is not a mapping")
        baseline = int(measured["literal_archive_bytes"])
        minima = measured["evaluated_family_minima"]
        if not isinstance(minima, Mapping):
            raise ValueError("native family minima are not a mapping")
        for family, raw_size in minima.items():
            candidate = int(raw_size)
            oracle = min(baseline, candidate)
            gain = baseline - oracle
            rows.append(
                {
                    "evidence_kind": "native_complete_archive_family_ledger",
                    "input_id": sample["input_id"],
                    "origin": sample["origin"],
                    "sample_bytes": sample["sample_bytes"],
                    "status": _native_status(measured),
                    "family": family,
                    "baseline": "native-complete-raw-literal-archive",
                    "baseline_bytes": baseline,
                    "oracle_bytes": oracle,
                    "headroom_bytes": gain,
                    "headroom_percent": percentage(gain, baseline),
                    "winner": family if candidate < baseline else "literal",
                    "complete_within_declared_catalogue": measured[
                        "complete_within_declared_catalogue"
                    ],
                    "source": sample["source"],
                    "notes": (
                        "Minimum exact complete archive row retained for this native "
                        "family; includes current entropy metadata and container bytes."
                    ),
                }
            )
    return rows


def native_symbolic_rows(payload: Mapping[str, object]) -> list[dict[str, object]]:
    rows = []
    for sample in _native_samples(payload):
        measured = sample["symbolic"]
        if not isinstance(measured, Mapping):
            raise ValueError("native symbolic row is not a mapping")
        baseline = int(measured["baseline_function_archive_bytes"])
        oracle = int(measured["winner_archive_bytes"])
        gain = baseline - oracle
        family = measured.get("winner_expression_family")
        depth = measured.get("winner_expression_depth")
        winner = "base-function"
        if family is not None:
            winner = f"{family}@depth-{depth}"
        rows.append(
            {
                "input_id": sample["input_id"],
                "origin": sample["origin"],
                "sample_bytes": sample["sample_bytes"],
                "scope": "development-microblock",
                "status": _native_status(measured),
                "depth_budget": 2,
                "node_budget": "states=128;pairs=4096",
                "baseline_bytes": baseline,
                "oracle_bytes": oracle,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, baseline),
                "winner": winner,
                "states_retained": measured["states_retained"],
                "pairs_considered": measured["pairs_considered"],
                "work_used": measured["work_used"],
                "ledger_entries": measured["ledger_entries"],
                "budget_exhausted": measured["budget_exhausted"],
                "complete_within_declared_catalogue": measured[
                    "complete_within_declared_catalogue"
                ],
                "source": sample["source"],
                "reason": (
                    "Native residual-sensitive symbolic enumeration. A zero row is "
                    "only a bounded-catalogue result, never a global impossibility claim."
                ),
            }
        )
    return rows


def native_dag_rows(payload: Mapping[str, object]) -> list[dict[str, object]]:
    rows = []
    for sample in _native_samples(payload):
        measured = sample["dag"]
        if not isinstance(measured, Mapping):
            raise ValueError("native DAG row is not a mapping")
        baseline = int(measured["baseline_function_archive_bytes"])
        oracle = int(measured["portfolio_best_archive_bytes"])
        gain = baseline - oracle
        rows.append(
            {
                "input_id": sample["input_id"],
                "origin": sample["origin"],
                "sample_bytes": sample["sample_bytes"],
                "status": _native_status(measured),
                "block_count": 1,
                "inline_bytes": baseline,
                "graph_candidate_bytes": measured["graph_archive_bytes"],
                "oracle_bytes": oracle,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, baseline),
                "active_definitions": measured["active_definitions"],
                "referenced_source_bytes": measured["referenced_source_bytes"],
                "evaluated_subsets": measured["evaluated_subsets"],
                "work_used": measured["work_used"],
                "ledger_entries": measured["ledger_entries"],
                "budget_exhausted": measured["budget_exhausted"],
                "complete_within_declared_catalogue": measured[
                    "complete_within_declared_catalogue"
                ],
                "source": sample["source"],
                "notes": (
                    "Natural development microblock. Graph archive competes against "
                    "the current Canonical/LZ-Huffman-aware native function winner."
                ),
            }
        )
    return rows


def stop_policy_rows(
    coordinate: Sequence[Mapping[str, object]],
    segmentation: Sequence[Mapping[str, object]],
    residual: Sequence[Mapping[str, object]],
    symbolic: Sequence[Mapping[str, object]],
    dag: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    depth2 = [
        row
        for row in residual
        if row.get("row_kind") == "native_depth_winner"
        and int(row.get("depth_limit", -1)) == 2
    ]
    catalogues = (
        (
            "coordinate-basis",
            coordinate,
            "baseline_bytes",
            "oracle_bytes",
            "incremental basis must beat active coordinate/no-coordinate",
        ),
        (
            "segmentation-256b",
            segmentation,
            "baseline_bytes",
            "oracle_bytes",
            "bounded production DP versus identical unsplit catalogue",
        ),
        (
            "recursive-residual-depth2",
            depth2,
            "baseline_depth0_bytes",
            "complete_candidate_bytes",
            "depth two versus current function/entropy winner",
        ),
        (
            "symbolic-depth2",
            symbolic,
            "baseline_bytes",
            "oracle_bytes",
            "RSEE versus current function/entropy winner",
        ),
        (
            "dag-sharing",
            dag,
            "inline_bytes",
            "oracle_bytes",
            "native graph versus current function/entropy winner",
        ),
    )
    rows = []
    for algorithm, evidence, baseline_field, oracle_field, comparison in catalogues:
        baseline = sum(int(row[baseline_field]) for row in evidence)
        oracle = sum(int(row[oracle_field]) for row in evidence)
        gain = baseline - oracle
        real = [row for row in evidence if row.get("origin") == "real"]
        winning = [row for row in evidence if int(row.get("headroom_bytes", 0)) > 0]
        real_winning = [
            row for row in real if int(row.get("headroom_bytes", 0)) > 0
        ]
        qualifying = [
            row
            for row in real
            if int(row[baseline_field]) > 0
            and int(row.get("headroom_bytes", 0)) * 1000
            >= int(row[baseline_field]) * 5
        ]
        complete = [
            row
            for row in evidence
            if row.get("complete_within_declared_catalogue") is True
            or str(row.get("complete_within_declared_catalogue", "")).lower()
            == "true"
        ]
        incomplete = len(evidence) - len(complete)
        if algorithm == "coordinate-basis" and not real_winning:
            decision = "stop_emission_zero_incremental_real_wins"
        elif qualifying:
            decision = "retain_experimental_for_validation"
        elif algorithm == "segmentation-256b" and incomplete:
            decision = "retain_core_no_deeper_search_bounded_inconclusive"
        elif algorithm == "segmentation-256b":
            decision = "stop_deeper_search_retention_gate_failed"
        elif incomplete:
            decision = "profile_disabled_bounded_inconclusive"
        else:
            decision = "stop_emission_retention_gate_failed"
        rows.append(
            {
                "algorithm": algorithm,
                "status": "development_only",
                "evaluation_rows": len(evidence),
                "real_rows": len(real),
                "complete_rows": len(complete),
                "winning_rows": len(winning),
                "real_winning_rows": len(real_winning),
                "qualifying_real_rows": len(qualifying),
                "minimum_gain_fraction": "0.005000000",
                "baseline_bytes": baseline,
                "oracle_bytes": oracle,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, baseline),
                "decision": decision,
                "holdout_status": "not_opened_not_evaluated",
                "reason": (
                    f"{comparison}; {incomplete} bounded-incomplete row(s). "
                    "A zero incomplete row is not a global no-headroom proof."
                ),
            }
        )
    return rows


def scan_legacy_full(
    repo_root: Path,
) -> tuple[dict[tuple[str, str], dict[str, object]], str, str]:
    run_dir = (
        repo_root
        / "benchmarks/results/full/20260726T044843Z-213f07c3"
    )
    checkpoint_dir = run_dir / "checkpoint-rows"
    selected_codecs = {
        "mathzip-fast",
        "mathzip-balanced",
        "mathzip-max",
        "zstd-default",
        "xz-default",
        "brotli-default",
    }
    selected: dict[tuple[str, str], tuple[int, dict[str, object]]] = {}
    digest = hashlib.sha256()
    for path in sorted(checkpoint_dir.glob("*.json")):
        raw = path.read_bytes()
        digest.update(path.name.encode("ascii"))
        digest.update(hashlib.sha256(raw).digest())
        wrapper = json.loads(raw)
        row = wrapper["row"]
        codec = row.get("codec")
        if codec not in selected_codecs or row.get("status") != "ok":
            continue
        key = (str(row["input_path"]), str(codec))
        grid_index = int(wrapper["expected_grid_index"])
        current = selected.get(key)
        if current is None or grid_index < current[0]:
            selected[key] = (grid_index, row)
    rows = {key: value[1] for key, value in selected.items()}
    return rows, digest.hexdigest(), str(run_dir.relative_to(repo_root))


def _legacy_scope(row: Mapping[str, object], scope: str) -> bool:
    corpus = str(row["corpus"])
    if scope == "all":
        return True
    if scope == "synthetic":
        return corpus == "synthetic"
    if scope == "legacy-labeled-non-synthetic":
        return corpus != "synthetic"
    raise ValueError(scope)


def legacy_search_rows(
    legacy: Mapping[tuple[str, str], Mapping[str, object]], source: str
) -> list[dict[str, object]]:
    paths = sorted({path for path, _ in legacy})
    rows = []
    profiles = ("mathzip-fast", "mathzip-balanced", "mathzip-max")
    for scope in ("all", "synthetic", "legacy-labeled-non-synthetic"):
        eligible = []
        for path in paths:
            profile_rows = [legacy.get((path, codec)) for codec in profiles]
            if any(row is None for row in profile_rows):
                continue
            assert profile_rows[1] is not None
            if not _legacy_scope(profile_rows[1], scope):
                continue
            eligible.append((path, profile_rows))
        baseline_bytes = sum(
            int(profile_rows[1]["compressed_bytes"])
            for _, profile_rows in eligible
            if profile_rows[1] is not None
        )
        oracle_bytes = sum(
            min(int(row["compressed_bytes"]) for row in profile_rows if row is not None)
            for _, profile_rows in eligible
        )
        gain = baseline_bytes - oracle_bytes
        rows.append(
            {
                "oracle_kind": "observed_profile_oracle",
                "scope": scope,
                "input_id": "aggregate",
                "origin": "legacy-mixed",
                "status": "observed_legacy_not_candidate_oracle",
                "baseline": "mathzip-balanced",
                "candidate_catalog": "|".join(profiles),
                "baseline_bytes": baseline_bytes,
                "oracle_bytes": oracle_bytes,
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, baseline_bytes),
                "winner": "per-input-minimum-observed-profile",
                "evaluated_candidates": len(eligible) * len(profiles),
                "source": source,
                "notes": (
                    "Predecessor Full result, development-only. This is an observed "
                    "profile/configuration oracle, not an internal candidate ledger. "
                    "The non-synthetic label is historical and not the new strict "
                    "origin=real AND primary=true balance predicate."
                ),
            }
        )
    return rows


def external_rows(
    legacy: Mapping[tuple[str, str], Mapping[str, object]], source: str
) -> list[dict[str, object]]:
    rows = []
    current_codec = "mathzip-balanced"
    for scope in ("all", "synthetic", "legacy-labeled-non-synthetic"):
        for baseline_codec in ("zstd-default", "xz-default", "brotli-default"):
            pairs = []
            for path in sorted({path for path, _ in legacy}):
                current = legacy.get((path, current_codec))
                baseline = legacy.get((path, baseline_codec))
                if current is None or baseline is None or not _legacy_scope(current, scope):
                    continue
                pairs.append((current, baseline))
            current_bytes = sum(int(current["compressed_bytes"]) for current, _ in pairs)
            baseline_bytes = sum(int(baseline["compressed_bytes"]) for _, baseline in pairs)
            original_bytes = sum(int(current["original_bytes"]) for current, _ in pairs)
            current_compression = sum(float(current["compression_seconds"]) for current, _ in pairs)
            baseline_compression = sum(float(baseline["compression_seconds"]) for _, baseline in pairs)
            current_decompression = sum(float(current["decompression_seconds"]) for current, _ in pairs)
            baseline_decompression = sum(float(baseline["decompression_seconds"]) for _, baseline in pairs)
            current_rss = max(float(current["peak_rss_bytes"]) for current, _ in pairs)
            baseline_rss = max(float(baseline["peak_rss_bytes"]) for _, baseline in pairs)
            gap = current_bytes - baseline_bytes
            rows.append(
                {
                    "scope": scope,
                    "status": "observed_legacy_external_only",
                    "current_codec": current_codec,
                    "baseline_codec": baseline_codec,
                    "input_count": len(pairs),
                    "original_bytes": original_bytes,
                    "current_bytes": current_bytes,
                    "baseline_bytes": baseline_bytes,
                    "size_gap_bytes": gap,
                    "size_gap_percent": percentage(gap, baseline_bytes),
                    "compression_slowdown": decimal(current_compression / baseline_compression),
                    "decompression_slowdown": decimal(current_decompression / baseline_decompression),
                    "peak_rss_ratio": decimal(current_rss / baseline_rss),
                    "source": source,
                    "notes": (
                        "External baseline is comparison-only. These are predecessor "
                        "MathZip results, not Native MathSVG, and no baseline payload "
                        "is embedded."
                    ),
                }
            )
    return rows


def scan_current_benchmark(
    repo_root: Path, run_metadata_path: Path
) -> tuple[list[dict[str, object]], str, str, str]:
    """Load one completed development benchmark without trusting its filename."""
    metadata_path = run_metadata_path.resolve()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    result = metadata.get("result")
    if not isinstance(result, Mapping) or result.get("complete") is not True:
        raise ValueError("current external benchmark is not complete")
    if metadata.get("holdout_payload_opened") is not False:
        raise ValueError("external benchmark crossed the holdout boundary")
    manifest = metadata.get("manifest")
    if not isinstance(manifest, Mapping) or manifest.get("split") != "development":
        raise ValueError("external oracle accepts development benchmark only")
    raw_relative = str(result["raw_jsonl"])
    raw_path = repo_root / raw_relative
    raw = raw_path.read_bytes()
    if len(raw) != int(result["raw_jsonl_bytes"]):
        raise ValueError("external benchmark byte count differs from metadata")
    digest = sha256_bytes(raw)
    if digest != result["raw_jsonl_sha256"]:
        raise ValueError("external benchmark digest differs from metadata")
    rows = [
        json.loads(line)
        for line in raw.decode("utf-8").splitlines()
        if line.strip()
    ]
    expected_rows = int(result["trial_rows"])
    if len(rows) != expected_rows:
        raise ValueError("external benchmark row count differs from metadata")
    actual_counts = Counter(str(row.get("status")) for row in rows)
    if actual_counts != Counter(
        {
            str(status): int(count)
            for status, count in result["status_counts"].items()
            if int(count)
        }
    ):
        raise ValueError("external benchmark status counts differ from metadata")
    runner_sha256 = str(metadata.get("runner_sha256", ""))
    if len(runner_sha256) != 64:
        raise ValueError("external benchmark lacks captured runner identity")
    return rows, digest, raw_relative, runner_sha256


def current_external_rows(
    repo_root: Path,
    benchmark_rows: Sequence[Mapping[str, object]],
    source: str,
    run_metadata_path: Path,
) -> list[dict[str, object]]:
    """Aggregate paired medians from the completed current native pilot."""
    metadata = json.loads(run_metadata_path.resolve().read_text(encoding="utf-8"))
    manifest_path = repo_root / str(metadata["manifest"]["path"])
    if sha256_file(manifest_path) != metadata["manifest"]["file_sha256"]:
        raise ValueError("current benchmark manifest hash differs from metadata")
    with manifest_path.open(encoding="utf-8", newline="") as handle:
        manifest_rows = {
            row["dataset_id"]: row for row in csv.DictReader(handle)
        }
    selected_ids = set(metadata["manifest"]["selected_dataset_ids"])

    measured = [
        row
        for row in benchmark_rows
        if row.get("warmup") is False
        and row.get("status") == "ok"
        and row.get("dataset_id") in selected_ids
    ]
    if not measured:
        raise ValueError("current benchmark has no measured successful rows")
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for row in measured:
        grouped.setdefault(
            (str(row["dataset_id"]), str(row["codec_config"])), []
        ).append(row)

    current_config = "balanced-v1"
    baselines = ("lz4", "gzip-6", "zstd-default", "xz-9e")
    scopes = (
        ("all", lambda row: True),
        ("synthetic", lambda row: row["origin"] == "synthetic"),
        (
            "strict-real-primary",
            lambda row: row["origin"] == "real"
            and str(row["primary"]).lower() == "true",
        ),
    )

    def one_input(rows: Sequence[Mapping[str, object]]) -> dict[str, float | int]:
        if not rows:
            raise ValueError("missing measured external-oracle repetitions")
        sizes = {int(row["archive_bytes"]) for row in rows}
        originals = {int(row["original_bytes"]) for row in rows}
        if len(sizes) != 1 or len(originals) != 1:
            raise ValueError("size/original bytes changed across repetitions")
        if any(
            row.get("roundtrip_ok") is not True
            or row.get("restored_sha256") != row.get("input_sha256")
            for row in rows
        ):
            raise ValueError("external benchmark contains an invalid round trip")
        return {
            "archive_bytes": sizes.pop(),
            "original_bytes": originals.pop(),
            "compression_ns": statistics.median(
                int(row["compression_wall_ns"]) for row in rows
            ),
            "decompression_ns": statistics.median(
                int(row["decompression_wall_ns"]) for row in rows
            ),
            "peak_rss_bytes": statistics.median(
                int(row["peak_rss_bytes"]) for row in rows
            ),
        }

    output = []
    for scope, predicate in scopes:
        dataset_ids = sorted(
            dataset_id
            for dataset_id in selected_ids
            if dataset_id in manifest_rows and predicate(manifest_rows[dataset_id])
        )
        for baseline in baselines:
            pairs = []
            for dataset_id in dataset_ids:
                current = grouped.get((dataset_id, current_config))
                opponent = grouped.get((dataset_id, baseline))
                if current is None or opponent is None:
                    continue
                pairs.append((one_input(current), one_input(opponent)))
            current_bytes = sum(
                int(current["archive_bytes"]) for current, _ in pairs
            )
            baseline_bytes = sum(
                int(opponent["archive_bytes"]) for _, opponent in pairs
            )
            original_bytes = sum(
                int(current["original_bytes"]) for current, _ in pairs
            )
            current_compression = sum(
                float(current["compression_ns"]) for current, _ in pairs
            )
            baseline_compression = sum(
                float(opponent["compression_ns"]) for _, opponent in pairs
            )
            current_decompression = sum(
                float(current["decompression_ns"]) for current, _ in pairs
            )
            baseline_decompression = sum(
                float(opponent["decompression_ns"]) for _, opponent in pairs
            )
            current_rss = max(
                (float(current["peak_rss_bytes"]) for current, _ in pairs),
                default=0.0,
            )
            baseline_rss = max(
                (float(opponent["peak_rss_bytes"]) for _, opponent in pairs),
                default=0.0,
            )
            gap = current_bytes - baseline_bytes
            output.append(
                {
                    "scope": scope,
                    "status": "current_native_development",
                    "current_codec": "mathsvg-balanced-v1",
                    "baseline_codec": baseline,
                    "input_count": len(pairs),
                    "original_bytes": original_bytes,
                    "current_bytes": current_bytes,
                    "baseline_bytes": baseline_bytes,
                    "size_gap_bytes": gap,
                    "size_gap_percent": percentage(gap, baseline_bytes),
                    "compression_slowdown": decimal(
                        current_compression / baseline_compression
                    ),
                    "decompression_slowdown": decimal(
                        current_decompression / baseline_decompression
                    ),
                    "peak_rss_ratio": decimal(current_rss / baseline_rss),
                    "source": source,
                    "notes": (
                        "Current completed development pilot; paired per-input medians "
                        "from 10 measured repetitions. Captured runner identity is in "
                        "oracle-manifest.json; no baseline payload is embedded."
                    ),
                }
            )
    for scope, _ in scopes:
        output.append(
            {
                "scope": scope,
                "status": "not_run_current_pilot",
                "current_codec": "mathsvg-balanced-v1",
                "baseline_codec": "brotli",
                "input_count": 0,
                "original_bytes": "",
                "current_bytes": "",
                "baseline_bytes": "",
                "size_gap_bytes": "",
                "size_gap_percent": "",
                "compression_slowdown": "",
                "decompression_slowdown": "",
                "peak_rss_ratio": "",
                "source": source,
                "notes": "Brotli was not scheduled in this current development pilot.",
            }
        )
    return output


def legacy_segmentation_row(repo_root: Path) -> dict[str, object] | None:
    path = (
        repo_root
        / "benchmarks/results/segmentation-ablation/"
        "20260725T051723Z-a5f0fdfc/results.json"
    )
    if not path.exists():
        return None
    artifact = json.loads(path.read_text(encoding="utf-8"))
    native = [
        row
        for row in artifact["results"]
        if str(row["codec"]).startswith("mathzip-") and row.get("status") == "ok"
    ]
    by_path: dict[str, dict[str, Mapping[str, object]]] = {}
    for row in native:
        by_path.setdefault(str(row["input_path"]), {})[str(row["codec"])] = row
    baseline_name = "mathzip-fast-adaptive-4kib"
    eligible = [rows for rows in by_path.values() if baseline_name in rows]
    baseline = sum(int(rows[baseline_name]["compressed_bytes"]) for rows in eligible)
    oracle = sum(
        min(int(row["compressed_bytes"]) for row in rows.values())
        for rows in eligible
    )
    gain = baseline - oracle
    return {
        "input_id": "legacy-segmentation-ablation-aggregate",
        "origin": "legacy-mixed",
        "sample_bytes": sum(
            int(rows[baseline_name]["original_bytes"]) for rows in eligible
        ),
        "status": "observed_legacy_not_exact_dp",
        "baseline": baseline_name,
        "baseline_bytes": baseline,
        "oracle_bytes": oracle,
        "headroom_bytes": gain,
        "headroom_percent": percentage(gain, baseline),
        "segment_count": "",
        "boundaries": "",
        "leaf_winners": "per-input-minimum-observed-configuration",
        "quantum_bytes": "",
        "source": str(path.relative_to(repo_root)),
        "notes": (
            "Eight observed predecessor segmentation configurations on 10 inputs; "
            "this is not the exact microblock DP above."
        ),
    }


def legacy_function_rows(repo_root: Path) -> list[dict[str, object]]:
    path = (
        repo_root
        / "benchmarks/results/ablation/"
        "20260725T170925Z-dde3921f/results.json"
    )
    if not path.exists():
        return []
    artifact = json.loads(path.read_text(encoding="utf-8"))
    totals: dict[str, int] = {}
    original = 0
    seen_inputs = set()
    for row in artifact["results"]:
        if row.get("status") != "ok":
            continue
        codec = str(row["codec"])
        if not codec.startswith("ablation-"):
            continue
        totals[codec] = totals.get(codec, 0) + int(row["compressed_bytes"])
        input_path = str(row["input_path"])
        if input_path not in seen_inputs:
            seen_inputs.add(input_path)
            original += int(row["original_bytes"])
    increments = (
        ("linear", "ablation-05-adaptive-constant", "ablation-06-adaptive-linear"),
        ("polynomial", "ablation-06-adaptive-linear", "ablation-07-add-polynomial"),
        ("periodic", "ablation-07-add-polynomial", "ablation-08-add-periodic"),
        ("recurrence", "ablation-08-add-periodic", "ablation-09-add-recurrence"),
        ("bit-plane", "ablation-09-add-recurrence", "ablation-10-add-bit-plane"),
        ("stride", "ablation-10-add-bit-plane", "ablation-11-add-stride"),
        ("copy", "ablation-11-add-stride", "ablation-12-add-copy"),
        ("custom-residual", "ablation-12-add-copy", "ablation-13-custom-residual"),
    )
    rows = []
    for family, before, after in increments:
        if before not in totals or after not in totals:
            continue
        gain = totals[before] - totals[after]
        rows.append(
            {
                "evidence_kind": "observed_legacy_incremental_ablation",
                "input_id": "legacy-ablation-aggregate",
                "origin": "legacy-mixed",
                "sample_bytes": original,
                "status": "observed_legacy_not_family_oracle",
                "family": family,
                "baseline": before,
                "baseline_bytes": totals[before],
                "oracle_bytes": totals[after],
                "headroom_bytes": gain,
                "headroom_percent": percentage(gain, totals[before]),
                "winner": after,
                "source": str(path.relative_to(repo_root)),
                "notes": (
                    "Sequential predecessor ablation; interactions and configuration "
                    "changes mean this is supporting evidence, not an isolated "
                    "MathSVG primitive oracle."
                ),
            }
        )
    return rows


def _sum_headroom(rows: Sequence[Mapping[str, object]], status: str = "ok") -> tuple[int, int]:
    selected = [row for row in rows if row.get("status") == status]
    baseline = sum(int(row.get("baseline_bytes", row.get("inline_bytes", 0))) for row in selected)
    oracle = sum(int(row.get("oracle_bytes", 0)) for row in selected)
    return baseline, baseline - oracle


def write_report(
    path: Path,
    search: Sequence[Mapping[str, object]],
    coordinate: Sequence[Mapping[str, object]],
    segmentation: Sequence[Mapping[str, object]],
    residual: Sequence[Mapping[str, object]],
    symbolic: Sequence[Mapping[str, object]],
    dag: Sequence[Mapping[str, object]],
    external: Sequence[Mapping[str, object]],
) -> None:
    def aggregate(
        rows: Sequence[Mapping[str, object]], baseline: str, oracle: str
    ) -> tuple[int, int]:
        baseline_bytes = sum(int(row[baseline]) for row in rows)
        oracle_bytes = sum(int(row[oracle]) for row in rows)
        return baseline_bytes, baseline_bytes - oracle_bytes

    search_base, search_gain = aggregate(
        search, "baseline_bytes", "oracle_bytes"
    )
    coordinate_base, coordinate_gain = aggregate(
        coordinate, "baseline_bytes", "oracle_bytes"
    )
    segmentation_base, segmentation_gain = aggregate(
        segmentation, "baseline_bytes", "oracle_bytes"
    )
    depth2 = [
        row
        for row in residual
        if row["row_kind"] == "native_depth_winner"
        and int(row["depth_limit"]) == 2
    ]
    residual_base, residual_gain = aggregate(
        depth2, "baseline_depth0_bytes", "complete_candidate_bytes"
    )
    residual_oracle = residual_base - residual_gain
    symbolic_base, symbolic_gain = aggregate(
        symbolic, "baseline_bytes", "oracle_bytes"
    )
    dag_base, dag_gain = aggregate(dag, "inline_bytes", "oracle_bytes")
    external_all = [row for row in external if row["scope"] == "all"]
    current_external = any(
        row.get("status") == "current_native_development"
        for row in external_all
    )

    def real_wins(
        rows: Sequence[Mapping[str, object]], headroom: str = "headroom_bytes"
    ) -> int:
        return sum(
            1
            for row in rows
            if row.get("origin") == "real" and int(row.get(headroom, 0)) > 0
        )

    def complete_count(rows: Sequence[Mapping[str, object]]) -> str:
        count = sum(
            1
            for row in rows
            if str(row.get("complete_within_declared_catalogue", "")).lower()
            == "true"
            or row.get("complete_within_declared_catalogue") is True
        )
        return f"{count}/{len(rows)}"

    def qualifying_real_wins(
        rows: Sequence[Mapping[str, object]], baseline: str
    ) -> int:
        return sum(
            1
            for row in rows
            if row.get("origin") == "real"
            and int(row.get(baseline, 0)) > 0
            and int(row.get("headroom_bytes", 0)) * 1000
            >= int(row[baseline]) * 5
        )

    coordinate_incremental_wins = sum(
        1
        for row in coordinate
        if str(row.get("basis_incremental_winner", "")).lower() == "true"
        or row.get("basis_incremental_winner") is True
    )
    coordinate_qualifying = qualifying_real_wins(coordinate, "baseline_bytes")
    segmentation_qualifying = qualifying_real_wins(
        segmentation, "baseline_bytes"
    )
    residual_qualifying = qualifying_real_wins(
        depth2, "baseline_depth0_bytes"
    )
    residual_real_winners = [
        row
        for row in depth2
        if row.get("origin") == "real"
        and int(row.get("headroom_bytes", 0)) > 0
    ]
    residual_winner_detail = ", ".join(
        (
            f"`{row['input_id']}` +{row['headroom_bytes']} bytes "
            f"({row['headroom_percent']}% on its 4 KiB sample)"
        )
        for row in residual_real_winners
    )
    symbolic_qualifying = qualifying_real_wins(symbolic, "baseline_bytes")
    dag_qualifying = qualifying_real_wins(dag, "inline_bytes")
    search_real_winners = [
        row
        for row in search
        if row.get("origin") == "real"
        and int(row.get("headroom_bytes", 0)) > 0
    ]
    search_entropy_real_wins = sum(
        1
        for row in search_real_winners
        if row.get("winner") == "entropy_literal"
    )
    search_function_real_wins = (
        len(search_real_winners) - search_entropy_real_wins
    )
    coordinate_decision = (
        "- **Coordinate basis: stop emission.** The frozen scan contains "
        "0/73 incremental wins over the already active whole-coordinate/"
        "no-coordinate competition. Keep the experiment and rows, but do not "
        "add it to built-in profiles."
        if coordinate_incremental_wins == 0
        else (
            f"- **Coordinate basis: retention review required.** The scan contains "
            f"{coordinate_incremental_wins}/73 incremental wins, of which "
            f"{coordinate_qualifying} meet the per-row 0.5% real-data threshold. "
            "No holdout conclusion is inferred."
        )
    )
    lines = [
        "# Current native development oracle analysis",
        "",
        "## Scope and validity",
        "",
        (
            "This is development-only evidence from the native Rust engine. Every input "
            "was already exposed to the predecessor project and is marked "
            "`legacy_observed=true`. The probe refuses holdout/sealed paths; no validation "
            "or holdout payload was opened."
        ),
        "",
        (
            "Every compared value is a complete canonical v1 archive byte count from the "
            "production serializer. Native literal leaves compete through the current "
            "eight-codec catalogue, including Canonical Huffman (0x06) and LZ-Huffman "
            "(0x07). A bounded result is not an unrestricted global optimum."
        ),
        "",
        "## Headroom summary",
        "",
        "| Oracle | Baseline bytes | Best bytes | Headroom | Headroom % |",
        "|---|---:|---:|---:|---:|",
        (
            f"| Native finite search vs raw literal | {search_base} | "
            f"{search_base-search_gain} | "
            f"{search_gain} | {percentage(search_gain, search_base)} |"
        ),
        (
            f"| Coordinate-basis incremental, 73 real blocks | {coordinate_base} | "
            f"{coordinate_base-coordinate_gain} | "
            f"{coordinate_gain} | {percentage(coordinate_gain, coordinate_base)} |"
        ),
        (
            f"| Production bounded segmentation, 256 B | {segmentation_base} | "
            f"{segmentation_base-segmentation_gain} | {segmentation_gain} | "
            f"{percentage(segmentation_gain, segmentation_base)} |"
        ),
        (
            f"| Residual depth 0→2 | {residual_base} | {residual_oracle} | "
            f"{residual_gain} | {percentage(residual_gain, residual_base)} |"
        ),
        (
            f"| Symbolic depth ≤2 | {symbolic_base} | "
            f"{symbolic_base-symbolic_gain} | {symbolic_gain} | "
            f"{percentage(symbolic_gain, symbolic_base)} |"
        ),
        (
            f"| Native DAG vs function/entropy winner | {dag_base} | "
            f"{dag_base-dag_gain} | {dag_gain} | "
            f"{percentage(dag_gain, dag_base)} |"
        ),
        "",
        "## Completeness and observed real wins",
        "",
        "| Oracle | Complete rows | Real winning rows |",
        "|---|---:|---:|",
        f"| Search | {complete_count(search)} | {real_wins(search)} |",
        f"| Coordinate basis | {complete_count(coordinate)} | {coordinate_incremental_wins} |",
        f"| Segmentation | {complete_count(segmentation)} | {real_wins(segmentation)} |",
        f"| Residual depth 2 | {complete_count(depth2)} | {real_wins(depth2)} |",
        f"| Symbolic | {complete_count(symbolic)} | {real_wins(symbolic)} |",
        f"| DAG | {complete_count(dag)} | {real_wins(dag)} |",
        "",
        (
            "The search row counts a win against raw literal. Real wins: "
            f"{len(search_real_winners)} total; {search_entropy_real_wins} "
            f"entropy-literal; {search_function_real_wins} non-literal function. "
            "The total must not be reported as new function-family headroom."
        ),
        "",
        "## Stop-policy decisions",
        "",
        coordinate_decision,
        (
            f"- **Segmentation: retain only bounded infrastructure.** It has "
            f"{real_wins(segmentation)} real winning sample rows and "
            f"{segmentation_qualifying} at or above 0.5%. Any zero result on a "
            "bounded-incomplete row cannot close search headroom."
        ),
        (
            f"- **Residual: retain as an experiment.** Depth-two real winning rows: "
            f"{real_wins(depth2)}; rows meeting the 0.5% threshold: "
            f"{residual_qualifying}. The development retention gate passes, so the "
            "provider remains available for validation; this does not enable built-in "
            "emission."
            + (
                f" The observed real winner is {residual_winner_detail}."
                if residual_winner_detail
                else ""
            )
        ),
        (
            f"- **Symbolic: bounded result, not global proof.** Aggregate observed headroom "
            f"is {symbolic_gain} bytes, with {symbolic_qualifying} qualifying real "
            "rows. Budget stops or zero qualifying real wins keep RSEE out of "
            "built-in profiles."
        ),
        (
            f"- **DAG sharing: stop emission.** Natural development samples "
            f"contain {real_wins(dag)} real incremental wins over the current entropy-aware "
            f"function winner, {dag_qualifying} at or above 0.5%. Definition-count "
            "reduction without byte gain is rejected; all declared DAG rows are complete."
        ),
        "",
        "## Current external gap" if current_external else "## Legacy observed gap",
        "",
        (
            (
                "The current native development pilot uses ten measured repetitions and "
                "captured artifact provenance. It remains one-host development evidence, "
                "not validation, holdout or a two-machine dominance certificate."
            )
            if current_external
            else (
                "The predecessor Full run is retained only as development evidence. It "
                "has three repetitions on one x86-64 host and does not meet the new "
                "randomised, confidence-interval, two-machine or strict-origin protocol."
            )
        ),
        "",
        "| External baseline | Size gap % | Compress slowdown | Decode slowdown | RSS ratio |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in sorted(external_all, key=lambda value: str(value["baseline_codec"])):
        size_gap = row["size_gap_percent"] or "not run"
        compression = row["compression_slowdown"] or "not run"
        decompression = row["decompression_slowdown"] or "not run"
        peak_rss = row["peak_rss_ratio"] or "not run"
        lines.append(
            f"| {row['baseline_codec']} | {size_gap} | "
            f"{compression} | {decompression} | {peak_rss} |"
        )
    lines.extend(
        [
            "",
            "## Limits and publication boundary",
            "",
            (
                "- Search/profile rows with `bounded_incomplete` expose a real exact winner "
                "inside consumed budget, but cannot prove zero remaining headroom."
            ),
            (
                "- Residual and symbolic comparisons use complete archives; no entropy "
                "lower bound is substituted for an achievable candidate."
            ),
            (
                "- The 73-block coordinate-basis decision is an incremental feature decision "
                "inside the frozen catalogue, not a claim that coordinate discovery has no "
                "headroom."
            ),
            (
                "- No row in this phase is publishable holdout evidence or a dominance "
                "certificate."
            ),
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_artifact_manifest(
    path: Path,
    output_files: Sequence[Path],
    samples: Sequence[Sample],
    external_kind: str,
    external_digest: str,
    external_source: str,
    external_runner_sha256: str,
    dataset_manifest: Path,
) -> None:
    repo_root = REPO_ROOT
    analyzer = Path(__file__).resolve()
    model = analyzer.with_name("model.py")
    native_sources = (
        Path("Cargo.lock"),
        Path("mathsvg/python/oracle/native_probe/Cargo.toml"),
        Path("mathsvg/python/oracle/native_probe/Cargo.lock"),
        Path("mathsvg/python/oracle/native_probe/src/main.rs"),
        Path("mathsvg/crates/mathsvg-container/src/lib.rs"),
        Path("mathsvg/crates/mathsvg-coordinates/src/lib.rs"),
        Path("mathsvg/crates/mathsvg-dsl/src/lib.rs"),
        Path("mathsvg/crates/mathsvg-entropy/src/lib.rs"),
        Path("mathsvg/crates/mathsvg-evaluator/src/lib.rs"),
        Path("mathsvg/crates/mathsvg-functions/src/lib.rs"),
        Path("mathsvg/crates/mathsvg-graph/src/lib.rs"),
        Path("mathsvg/crates/mathsvg-optimizer/src/lib.rs"),
        Path("mathsvg/crates/mathsvg-residual/src/lib.rs"),
        Path("mathsvg/crates/mathsvg-symbolic/src/lib.rs"),
    )
    payload = {
        "schema": ORACLE_SCHEMA,
        "catalog": CATALOG,
        "deterministic": True,
        "split": "development",
        "holdout_payload_inspected": False,
        "balance_eligibility": "origin=real AND primary=true",
        "analyzer_sha256": sha256_file(analyzer),
        "model_sha256": sha256_file(model),
        "native_source_sha256": {
            str(source): sha256_file(repo_root / source)
            for source in native_sources
        },
        "dataset_manifest": str(dataset_manifest.relative_to(repo_root)),
        "dataset_manifest_sha256": sha256_file(dataset_manifest),
        "external_evidence": {
            "kind": external_kind,
            "source": external_source,
            "raw_sha256": external_digest,
            "captured_runner_sha256": external_runner_sha256,
            "current_workspace_runner_not_inferred": True,
        },
        "inputs": [
            {
                "input_id": sample.spec.input_id,
                "path": sample.spec.path,
                "source_bytes": sample.source_bytes,
                "source_sha256": sample.source_sha256,
                "sample_bytes": len(sample.data),
                "sample_sha256": sample.sample_sha256,
                "split": "development",
                "legacy_observed": True,
            }
            for sample in sorted(samples, key=lambda value: value.spec.input_id)
        ],
        "outputs": [
            {
                "path": str(output.relative_to(repo_root)),
                "bytes": output.stat().st_size,
                "sha256": sha256_file(output),
            }
            for output in sorted(output_files)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run(
    repo_root: Path = REPO_ROOT,
    include_legacy: bool = True,
    native_probe_json: Path | None = None,
    current_benchmark_run: Path | None = None,
) -> dict[str, object]:
    repo_root = repo_root.resolve()
    output_root = repo_root / "mathsvg/results/oracle"
    manifest_root = repo_root / "mathsvg/results/manifests"
    docs_root = repo_root / "mathsvg/docs"
    samples = load_samples(repo_root)
    native = load_native_probe(repo_root, native_probe_json)
    native_output = output_root / NATIVE_OUTPUT_NAME
    write_native_probe(native_output, native)

    dataset_manifest = manifest_root / "oracle-development.csv"
    write_csv(dataset_manifest, DATASET_FIELDS, dataset_manifest_rows(samples))

    search = native_search_rows(native)
    coordinate = native_coordinate_rows(native)
    segmentation = native_segmentation_rows(native)
    residual = native_residual_rows(native)
    functions = native_function_rows(native)
    symbolic = native_symbolic_rows(native)
    dag = native_dag_rows(native)
    stop_policy = stop_policy_rows(
        coordinate, segmentation, residual, symbolic, dag
    )
    external: list[dict[str, object]] = []
    external_kind = "not-scanned"
    external_digest = "not-scanned"
    external_source = "not-scanned"
    external_runner_sha256 = ""
    if current_benchmark_run is not None:
        (
            benchmark_rows,
            external_digest,
            external_source,
            external_runner_sha256,
        ) = scan_current_benchmark(repo_root, current_benchmark_run)
        external = current_external_rows(
            repo_root,
            benchmark_rows,
            external_source,
            current_benchmark_run,
        )
        external_kind = "current-native-development-pilot"
    elif include_legacy:
        legacy, external_digest, external_source = scan_legacy_full(repo_root)
        external.extend(external_rows(legacy, external_source))
        external_kind = "legacy-predecessor-full"
    if not external:
        external.append(
            {
                "scope": "all",
                "status": "not_run",
                "current_codec": "mathzip-balanced",
                "baseline_codec": "",
                "input_count": 0,
                "original_bytes": "",
                "current_bytes": "",
                "baseline_bytes": "",
                "size_gap_bytes": "",
                "size_gap_percent": "",
                "compression_slowdown": "",
                "decompression_slowdown": "",
                "peak_rss_ratio": "",
                "source": "",
                "notes": "Legacy artifact scan disabled.",
            }
        )

    files = {
        "search-oracle.csv": (SEARCH_FIELDS, search),
        "coordinate-oracle.csv": (COORDINATE_FIELDS, coordinate),
        "segmentation-oracle.csv": (SEGMENTATION_FIELDS, segmentation),
        "residual-oracle.csv": (RESIDUAL_FIELDS, residual),
        "function-family-headroom.csv": (FUNCTION_FIELDS, functions),
        "symbolic-headroom.csv": (SYMBOLIC_FIELDS, symbolic),
        "dag-sharing-headroom.csv": (DAG_FIELDS, dag),
        "stop-policy.csv": (STOP_POLICY_FIELDS, stop_policy),
        "external-gap.csv": (EXTERNAL_FIELDS, external),
    }
    output_files = [native_output]
    for filename, (fields, rows) in files.items():
        path = output_root / filename
        write_csv(path, fields, rows)
        output_files.append(path)
    aliases = {
        "search.csv": "search-oracle.csv",
        "coordinate.csv": "coordinate-oracle.csv",
        "segmentation.csv": "segmentation-oracle.csv",
        "residual.csv": "residual-oracle.csv",
        "symbolic.csv": "symbolic-headroom.csv",
        "dag-sharing.csv": "dag-sharing-headroom.csv",
    }
    for alias, source in aliases.items():
        target = output_root / alias
        copy_alias(output_root / source, target)
        output_files.append(target)

    report_path = docs_root / "oracle-analysis.md"
    write_report(
        report_path,
        search,
        coordinate,
        segmentation,
        residual,
        symbolic,
        dag,
        external,
    )
    output_files.append(report_path)
    output_files.append(dataset_manifest)

    artifact_manifest = manifest_root / "oracle-manifest.json"
    write_artifact_manifest(
        artifact_manifest,
        output_files,
        samples,
        external_kind,
        external_digest,
        external_source,
        external_runner_sha256,
        dataset_manifest,
    )
    return {
        "samples": len(samples),
        "search_rows": len(search),
        "coordinate_rows": len(coordinate),
        "segmentation_rows": len(segmentation),
        "residual_rows": len(residual),
        "function_rows": len(functions),
        "symbolic_rows": len(symbolic),
        "dag_rows": len(dag),
        "stop_policy_rows": len(stop_policy),
        "external_rows": len(external),
        "manifest": str(artifact_manifest.relative_to(repo_root)),
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--skip-legacy", action="store_true")
    parser.add_argument(
        "--native-probe-json",
        type=Path,
        help="reuse one completed native probe JSON instead of executing Cargo",
    )
    parser.add_argument(
        "--current-benchmark-run",
        type=Path,
        help="completed development run metadata used for current external gap",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = run(
        args.repo_root,
        include_legacy=not args.skip_legacy,
        native_probe_json=args.native_probe_json,
        current_benchmark_run=args.current_benchmark_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
