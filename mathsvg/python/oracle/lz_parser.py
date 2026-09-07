"""Validate and summarize the bounded LZH-ChainLazy development oracle."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
from typing import Any, Iterable

SCHEMA = "mathsvg-lzh-chain-lazy-oracle-v1"
PROVENANCE_SCHEMA = "mathsvg-lzh-chain-lazy-provenance-v3"
SELECTION_POLICY = "sequential-incremental-exact-gain-v1"
SIZE_ONLY_SELECTION_POLICY = "minimum-exact-archive-bytes-v1"
POLICIES = ("C4", "C8", "C16", "C4L", "C8L", "C16L")
TIMED_POLICIES = ("C4", "C4L", "C8L", "C16L")
RETENTION_GAIN = 0.005
MAXIMUM_SLOWDOWN_BELOW_THRESHOLD = 2.0

CSV_FIELDS = (
    "schema",
    "scope",
    "dataset_id",
    "origin",
    "policy",
    "input_bytes",
    "block_count",
    "complete",
    "budget_stop_blocks",
    "winning_blocks",
    "baseline_full_archive_bytes",
    "portfolio_full_archive_bytes",
    "saved_full_archive_bytes",
    "gain_fraction",
    "baseline_search_ns_median",
    "portfolio_search_ns_median",
    "search_slowdown",
    "timing_evidence_status",
    "retention_pass",
    "retention_reason",
)


class LzOracleError(ValueError):
    """The raw oracle evidence or its development manifest is invalid."""


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_fact(path: pathlib.Path) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise LzOracleError(f"invalid manifest boolean: {value!r}")


def load_development_manifest(path: pathlib.Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise LzOracleError("development manifest is empty")
    by_id: dict[str, dict[str, str]] = {}
    for row in rows:
        dataset_id = row.get("dataset_id", "")
        if not dataset_id or dataset_id in by_id:
            raise LzOracleError(f"invalid or duplicate dataset_id: {dataset_id!r}")
        by_id[dataset_id] = row
    return by_id


def _policy_rows(raw: dict[str, Any]) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for sample in raw["samples"]:
        for row in sample["policies"]:
            yield sample, row


def validate_raw(
    raw: dict[str, Any],
    manifest: dict[str, dict[str, str]],
    *,
    expected_policies: tuple[str, ...] = POLICIES,
) -> None:
    if raw.get("schema") != SCHEMA:
        raise LzOracleError(f"unexpected raw schema: {raw.get('schema')!r}")
    if raw.get("holdout_payload_inspected") is not False:
        raise LzOracleError("oracle must explicitly report holdout_payload_inspected=false")
    if tuple(raw.get("policies", ())) != expected_policies:
        raise LzOracleError("raw policy catalogue/order differs from the frozen oracle")
    if raw.get("block_bytes") != 1 << 20:
        raise LzOracleError("oracle block size must be exactly 1 MiB")
    if not isinstance(raw.get("repetitions"), int) or raw["repetitions"] <= 0:
        raise LzOracleError("oracle repetitions must be positive")
    samples = raw.get("samples")
    if not isinstance(samples, list) or not samples:
        raise LzOracleError("raw oracle has no samples")

    seen: set[str] = set()
    for sample in samples:
        dataset_id = sample.get("dataset_id")
        if not isinstance(dataset_id, str) or dataset_id in seen:
            raise LzOracleError(f"invalid or duplicate raw dataset_id: {dataset_id!r}")
        seen.add(dataset_id)
        manifest_row = manifest.get(dataset_id)
        if manifest_row is None:
            raise LzOracleError(f"raw dataset is absent from development manifest: {dataset_id}")
        if (
            manifest_row.get("split") != "development"
            or manifest_row.get("origin") != "real"
            or _boolean(manifest_row.get("sealed", ""))
        ):
            raise LzOracleError(f"oracle dataset is not open development-real: {dataset_id}")
        source = sample.get("source")
        if source != manifest_row.get("path") or "holdout" in str(source).lower():
            raise LzOracleError(f"oracle source is not the manifest development path: {dataset_id}")
        if sample.get("origin") != "real":
            raise LzOracleError(f"non-real sample entered the retention oracle: {dataset_id}")
        if sample.get("input_sha256") != manifest_row.get("sha256"):
            raise LzOracleError(f"sample SHA-256 differs from manifest: {dataset_id}")
        if sample.get("input_bytes") != int(manifest_row.get("bytes", "-1")):
            raise LzOracleError(f"sample byte count differs from manifest: {dataset_id}")
        policies = sample.get("policies")
        if not isinstance(policies, list) or tuple(
            row.get("policy") for row in policies
        ) != expected_policies:
            raise LzOracleError(f"sample policy rows are incomplete: {dataset_id}")

    for sample, row in _policy_rows(raw):
        baseline = row.get("baseline_full_archive_bytes")
        portfolio = row.get("portfolio_full_archive_bytes")
        saved = row.get("saved_full_archive_bytes")
        if not all(isinstance(value, int) for value in (baseline, portfolio, saved)):
            raise LzOracleError("archive byte fields must be integers")
        if baseline <= 0 or portfolio <= 0 or portfolio > baseline:
            raise LzOracleError("add-only portfolio archive accounting is invalid")
        if saved != baseline - portfolio:
            raise LzOracleError("saved bytes differ from exact archive subtraction")
        expected_gain = saved / baseline
        if abs(float(row.get("gain_fraction")) - expected_gain) > 1e-12:
            raise LzOracleError("gain fraction differs from exact archive bytes")
        complete = row.get("complete")
        stops = row.get("budget_stop_blocks")
        if not isinstance(complete, bool) or not isinstance(stops, int) or stops < 0:
            raise LzOracleError("invalid completeness/budget fields")
        if complete != (stops == 0):
            raise LzOracleError("complete flag conflicts with budget-stop count")
        slowdown = row.get("search_slowdown")
        baseline_ns = row.get("baseline_search_ns_median")
        portfolio_ns = row.get("portfolio_search_ns_median")
        if (
            not isinstance(baseline_ns, int)
            or baseline_ns <= 0
            or not isinstance(portfolio_ns, int)
            or portfolio_ns <= 0
            or not isinstance(slowdown, (int, float))
            or slowdown <= 0
        ):
            raise LzOracleError("invalid search timing fields")
        if abs(float(slowdown) - portfolio_ns / baseline_ns) > 1e-12:
            raise LzOracleError("search slowdown differs from timing fields")
        blocks = row.get("blocks")
        if not isinstance(blocks, list) or len(blocks) != sample.get("block_count"):
            raise LzOracleError("block evidence is absent or incomplete")
        if sum(bool(block.get("portfolio_selected_policy")) for block in blocks) != row.get(
            "winning_blocks"
        ):
            raise LzOracleError("winning block count differs from block evidence")


def validate_timing_raw(
    timing_raw: dict[str, Any],
    manifest: dict[str, dict[str, str]],
    size_raw: dict[str, Any],
) -> None:
    """Validate the isolated directional timing run against exact size evidence."""

    validate_raw(
        timing_raw,
        manifest,
        expected_policies=TIMED_POLICIES,
    )
    if timing_raw.get("repetitions", 0) < 10:
        raise LzOracleError("directional timing requires at least ten repetitions")
    provenance = timing_raw.get("provenance")
    if not isinstance(provenance, dict):
        raise LzOracleError("timing raw lacks explicit provenance")
    if provenance.get("timing_evidence_status") != (
        "development-directional-gui-host"
    ):
        raise LzOracleError("timing evidence status is absent or unexpected")
    if provenance.get("project_workloads_quiesced_by_coordinator") is not True:
        raise LzOracleError("timing run did not record project workload quiescence")
    if provenance.get("resource_isolation") is not False:
        raise LzOracleError("timing run must honestly record absent resource isolation")

    size_samples = {
        sample["dataset_id"]: sample for sample in size_raw["samples"]
    }
    if set(size_samples) != {
        sample.get("dataset_id") for sample in timing_raw["samples"]
    }:
        raise LzOracleError("timing and size sample catalogues differ")
    for timing_sample in timing_raw["samples"]:
        size_sample = size_samples[timing_sample["dataset_id"]]
        for field in (
            "source",
            "origin",
            "input_bytes",
            "input_sha256",
            "block_count",
        ):
            if timing_sample.get(field) != size_sample.get(field):
                raise LzOracleError(
                    f"timing/size identity differs for "
                    f"{timing_sample['dataset_id']}.{field}"
                )
        size_policies = {
            row["policy"]: row for row in size_sample["policies"]
        }
        for timing_row in timing_sample["policies"]:
            size_row = size_policies[timing_row["policy"]]
            for field in (
                "complete",
                "budget_stop_blocks",
                "winning_blocks",
                "baseline_leaf_bytes",
                "policy_leaf_bytes",
                "portfolio_leaf_bytes",
                "baseline_one_block_archive_bytes",
                "policy_one_block_archive_bytes",
                "portfolio_one_block_archive_bytes",
                "baseline_full_archive_bytes",
                "policy_full_archive_bytes",
                "portfolio_full_archive_bytes",
                "saved_full_archive_bytes",
                "gain_fraction",
                "parser_stats",
                "blocks",
            ):
                if timing_row.get(field) != size_row.get(field):
                    raise LzOracleError(
                        f"timing changed exact size evidence for "
                        f"{timing_sample['dataset_id']}."
                        f"{timing_row['policy']}.{field}"
                    )


def _format_float(value: float) -> str:
    return f"{value:.12f}"


def summarize(
    raw: dict[str, Any],
    timing_raw: dict[str, Any] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    timing_by_key = (
        {
            (sample["dataset_id"], row["policy"]): row
            for sample, row in _policy_rows(timing_raw)
        }
        if timing_raw is not None
        else {}
    )
    has_directional_timing = timing_raw is not None
    rows: list[dict[str, str]] = []
    aggregate_by_policy: dict[str, dict[str, Any]] = {
        policy: {
            "input_bytes": 0,
            "block_count": 0,
            "complete": True,
            "budget_stop_blocks": 0,
            "winning_blocks": 0,
            "winning_files": 0,
            "baseline_full_archive_bytes": 0,
            "portfolio_full_archive_bytes": 0,
            "baseline_search_ns_median": (
                0 if not has_directional_timing or policy in TIMED_POLICIES else None
            ),
            "portfolio_search_ns_median": (
                0 if not has_directional_timing or policy in TIMED_POLICIES else None
            ),
        }
        for policy in POLICIES
    }
    for sample, policy_row in _policy_rows(raw):
        policy = policy_row["policy"]
        gain = float(policy_row["gain_fraction"])
        timing_row = timing_by_key.get((sample["dataset_id"], policy))
        effective_timing = timing_row or (
            policy_row if not has_directional_timing else None
        )
        timing_status = (
            "development-directional-gui-host"
            if timing_row is not None
            else (
                "size-run-timing"
                if not has_directional_timing
                else "unavailable-size-run-concurrent"
            )
        )
        rows.append(
            {
                "schema": SCHEMA,
                "scope": "file",
                "dataset_id": sample["dataset_id"],
                "origin": "real",
                "policy": policy,
                "input_bytes": str(sample["input_bytes"]),
                "block_count": str(sample["block_count"]),
                "complete": str(policy_row["complete"]).lower(),
                "budget_stop_blocks": str(policy_row["budget_stop_blocks"]),
                "winning_blocks": str(policy_row["winning_blocks"]),
                "baseline_full_archive_bytes": str(
                    policy_row["baseline_full_archive_bytes"]
                ),
                "portfolio_full_archive_bytes": str(
                    policy_row["portfolio_full_archive_bytes"]
                ),
                "saved_full_archive_bytes": str(
                    policy_row["saved_full_archive_bytes"]
                ),
                "gain_fraction": _format_float(gain),
                "baseline_search_ns_median": (
                    str(effective_timing["baseline_search_ns_median"])
                    if effective_timing is not None
                    else ""
                ),
                "portfolio_search_ns_median": (
                    str(effective_timing["portfolio_search_ns_median"])
                    if effective_timing is not None
                    else ""
                ),
                "search_slowdown": (
                    _format_float(effective_timing["search_slowdown"])
                    if effective_timing is not None
                    else ""
                ),
                "timing_evidence_status": timing_status,
                "retention_pass": "",
                "retention_reason": "",
            }
        )
        aggregate = aggregate_by_policy[policy]
        aggregate["input_bytes"] += sample["input_bytes"]
        aggregate["block_count"] += sample["block_count"]
        aggregate["complete"] &= policy_row["complete"]
        aggregate["budget_stop_blocks"] += policy_row["budget_stop_blocks"]
        aggregate["winning_blocks"] += policy_row["winning_blocks"]
        aggregate["winning_files"] += policy_row["saved_full_archive_bytes"] > 0
        aggregate["baseline_full_archive_bytes"] += policy_row[
            "baseline_full_archive_bytes"
        ]
        aggregate["portfolio_full_archive_bytes"] += policy_row[
            "portfolio_full_archive_bytes"
        ]
        if effective_timing is not None:
            aggregate["baseline_search_ns_median"] += effective_timing[
                "baseline_search_ns_median"
            ]
            aggregate["portfolio_search_ns_median"] += effective_timing[
                "portfolio_search_ns_median"
            ]

    decisions: dict[str, Any] = {}
    for policy in POLICIES:
        aggregate = aggregate_by_policy[policy]
        baseline = aggregate["baseline_full_archive_bytes"]
        portfolio = aggregate["portfolio_full_archive_bytes"]
        saved = baseline - portfolio
        gain = saved / baseline
        slowdown = (
            aggregate["portfolio_search_ns_median"]
            / aggregate["baseline_search_ns_median"]
            if aggregate["baseline_search_ns_median"] is not None
            else None
        )
        passed = (
            aggregate["complete"]
            and aggregate["budget_stop_blocks"] == 0
            and aggregate["winning_files"] >= 1
            and gain >= RETENTION_GAIN
        )
        if not aggregate["complete"] or aggregate["budget_stop_blocks"]:
            reason = "fail: bounded parser stopped before completing every real block"
        elif aggregate["winning_files"] == 0:
            reason = "fail: no non-synthetic full-file win"
        elif gain < RETENTION_GAIN:
            reason = "fail: aggregate exact archive gain is below 0.5%"
        else:
            reason = "pass: exact archive gain and real-win gates passed"
        if (
            slowdown is not None
            and slowdown > MAXIMUM_SLOWDOWN_BELOW_THRESHOLD
            and gain < RETENTION_GAIN
        ):
            reason += "; search exceeds 2x below the gain threshold"
        decisions[policy] = {
            **aggregate,
            "saved_full_archive_bytes": saved,
            "gain_fraction": gain,
            "search_slowdown": slowdown,
            "retention_pass": passed,
            "retention_reason": reason,
        }
        rows.append(
            {
                "schema": SCHEMA,
                "scope": "aggregate-real-development",
                "dataset_id": "__aggregate__",
                "origin": "real",
                "policy": policy,
                "input_bytes": str(aggregate["input_bytes"]),
                "block_count": str(aggregate["block_count"]),
                "complete": str(aggregate["complete"]).lower(),
                "budget_stop_blocks": str(aggregate["budget_stop_blocks"]),
                "winning_blocks": str(aggregate["winning_blocks"]),
                "baseline_full_archive_bytes": str(baseline),
                "portfolio_full_archive_bytes": str(portfolio),
                "saved_full_archive_bytes": str(saved),
                "gain_fraction": _format_float(gain),
                "baseline_search_ns_median": (
                    str(aggregate["baseline_search_ns_median"])
                    if aggregate["baseline_search_ns_median"] is not None
                    else ""
                ),
                "portfolio_search_ns_median": (
                    str(aggregate["portfolio_search_ns_median"])
                    if aggregate["portfolio_search_ns_median"] is not None
                    else ""
                ),
                "search_slowdown": (
                    _format_float(slowdown) if slowdown is not None else ""
                ),
                "timing_evidence_status": (
                    "development-directional-gui-host"
                    if slowdown is not None and has_directional_timing
                    else (
                        "size-run-timing"
                        if not has_directional_timing
                        else "unavailable-size-run-concurrent"
                    )
                ),
                "retention_pass": str(passed).lower(),
                "retention_reason": reason,
            }
        )
    passing = [policy for policy in POLICIES if decisions[policy]["retention_pass"]]
    if has_directional_timing:
        timed_passing = [policy for policy in TIMED_POLICIES if policy in passing]
        selected = timed_passing[0] if timed_passing else None
        accepted_steps: list[str] = []
        rejected_step: str | None = None
        if selected is not None:
            start = TIMED_POLICIES.index(selected)
            for deeper in TIMED_POLICIES[start + 1 :]:
                if deeper not in passing:
                    rejected_step = f"{selected}->{deeper}: policy failed admission"
                    break
                incremental_gain = (
                    decisions[deeper]["gain_fraction"]
                    - decisions[selected]["gain_fraction"]
                )
                if incremental_gain < RETENTION_GAIN:
                    rejected_step = (
                        f"{selected}->{deeper}: incremental exact archive gain "
                        f"{incremental_gain:.12f} is below {RETENTION_GAIN:.12f}"
                    )
                    break
                accepted_steps.append(
                    f"{selected}->{deeper}: incremental exact archive gain "
                    f"{incremental_gain:.12f}"
                )
                selected = deeper
        if selected is None:
            selection_reason = "no timed policy passed the admission gates"
        else:
            details = "; ".join(accepted_steps)
            if rejected_step is not None:
                details = f"{details}; {rejected_step}" if details else rejected_step
            selection_reason = (
                f"{selected} is the deepest timed policy reached by sequential "
                f"0.5 percentage-point exact-gain admission"
                + (f": {details}" if details else "")
            )
    else:
        selected = (
            min(
                passing,
                key=lambda policy: (
                    decisions[policy]["portfolio_full_archive_bytes"],
                    decisions[policy]["search_slowdown"],
                    POLICIES.index(policy),
                ),
            )
            if passing
            else None
        )
        selection_reason = (
            "minimum exact archive bytes among passing policies"
            if selected is not None
            else "no policy passed the admission gates"
        )
    return rows, {
        "selection_policy": (
            SELECTION_POLICY if has_directional_timing else SIZE_ONLY_SELECTION_POLICY
        ),
        "threshold_gain_fraction": RETENTION_GAIN,
        "maximum_search_multiplier_below_threshold": (
            MAXIMUM_SLOWDOWN_BELOW_THRESHOLD
        ),
        "decisions": decisions,
        "selected_policy": selected,
        "selection_reason": selection_reason,
        "timing_evidence_status": (
            "development-directional-gui-host"
            if has_directional_timing
            else "size-run-timing"
        ),
        "retention_pass": selected is not None,
    }


def write_csv(path: pathlib.Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: pathlib.Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def build_provenance(
    *,
    raw_path: pathlib.Path,
    csv_path: pathlib.Path,
    manifest_path: pathlib.Path,
    binary_path: pathlib.Path,
    timing_raw_path: pathlib.Path | None,
    prior_size_provenance: dict[str, Any] | None,
    source_paths: list[pathlib.Path],
    raw: dict[str, Any],
    retention: dict[str, Any],
) -> dict[str, Any]:
    prior_probe_binary = None
    if prior_size_provenance is not None:
        prior_probe_binary = prior_size_provenance.get("probe_binary")
        prior_size_evidence = prior_size_provenance.get("size_evidence")
        if prior_probe_binary is None and isinstance(
            prior_size_evidence, dict
        ):
            prior_probe_binary = prior_size_evidence.get("probe_binary")
    return {
        "schema": PROVENANCE_SCHEMA,
        "selection_policy": retention["selection_policy"],
        "evidence_scope": "development-real-only",
        "holdout_payload_inspected": False,
        "size_evidence": {
            "raw": file_fact(raw_path),
            "probe_binary": (
                prior_probe_binary
                if prior_probe_binary is not None
                else file_fact(binary_path)
            ),
            "timing_usable": False,
            "timing_reason": (
                "the size run overlapped unrelated project workloads; its "
                "exact bytes/work/roundtrip evidence remains usable"
            ),
        },
        "timing_evidence": (
            {
                "raw": file_fact(timing_raw_path),
                "probe": raw.get("_validated_timing_provenance"),
                "use": "directional policy selection only",
            }
            if timing_raw_path is not None
            else None
        ),
        "summary_csv": file_fact(csv_path),
        "development_manifest": file_fact(manifest_path),
        "analysis_sources": [file_fact(path) for path in source_paths],
        "sample_dataset_ids": [sample["dataset_id"] for sample in raw["samples"]],
        "probe_contract": {
            "schema": raw["schema"],
            "engine": raw["engine"],
            "block_bytes": raw["block_bytes"],
            "warmups": raw["warmups"],
            "repetitions": raw["repetitions"],
            "policies": raw["policies"],
        },
        "retention": retention,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--binary", type=pathlib.Path, required=True)
    parser.add_argument("--timing-raw", type=pathlib.Path)
    parser.add_argument("--prior-size-provenance", type=pathlib.Path)
    parser.add_argument("--csv-output", type=pathlib.Path, required=True)
    parser.add_argument("--provenance-output", type=pathlib.Path, required=True)
    parser.add_argument(
        "--source",
        type=pathlib.Path,
        action="append",
        default=[],
        help="source file included in provenance; may be repeated",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    manifest = load_development_manifest(args.manifest)
    validate_raw(raw, manifest)
    timing_raw = (
        json.loads(args.timing_raw.read_text(encoding="utf-8"))
        if args.timing_raw is not None
        else None
    )
    if timing_raw is not None:
        validate_timing_raw(timing_raw, manifest, raw)
        raw["_validated_timing_provenance"] = timing_raw["provenance"]
    prior_size_provenance = (
        json.loads(args.prior_size_provenance.read_text(encoding="utf-8"))
        if args.prior_size_provenance is not None
        else None
    )
    rows, retention = summarize(raw, timing_raw)
    write_csv(args.csv_output, rows)
    provenance = build_provenance(
        raw_path=args.raw,
        csv_path=args.csv_output,
        manifest_path=args.manifest,
        binary_path=args.binary,
        timing_raw_path=args.timing_raw,
        prior_size_provenance=prior_size_provenance,
        source_paths=args.source,
        raw=raw,
        retention=retention,
    )
    write_json(args.provenance_output, provenance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
