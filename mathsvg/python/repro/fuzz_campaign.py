#!/usr/bin/env python3
"""Run finite sanitizer-backed MathSVG fuzz campaigns with provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Mapping, Sequence

from mathsvg.python.benchmarks.freeze import git_identity
from mathsvg.python.repro.common import (
    ReproError,
    atomic_json,
    canonical_sha256,
    executable_file,
    log_identity,
    probe_text,
    regular_file,
    run_command,
    sha256_file,
    stable_environment,
    tree_identity,
    within_repository,
)

TARGETS: Mapping[str, tuple[str, str, int]] = {
    "mathsvg_decode": (
        "fuzz/fuzz_targets/mathsvg_decode.rs",
        "fuzz/seeds/mathsvg_decode",
        65_536,
    ),
    "mathsvg_entropy": (
        "fuzz/fuzz_targets/mathsvg_entropy.rs",
        "fuzz/seeds/mathsvg_entropy",
        16_384,
    ),
    "mathsvg_roundtrip": (
        "fuzz/fuzz_targets/mathsvg_roundtrip.rs",
        "fuzz/seeds/mathsvg_roundtrip",
        4_096,
    ),
}
EXECUTED_UNITS_RE = re.compile(
    r"stat::number_of_executed_units:\s*(\d+)"
)


@dataclass(frozen=True, slots=True)
class Tool:
    path: pathlib.Path
    sha256: str
    version: str


def _tool(
    path: pathlib.Path,
    *,
    name: str,
    version_args: Sequence[str],
    repository: pathlib.Path,
) -> Tool:
    executable = executable_file(path, label=name)
    return Tool(
        path=executable,
        sha256=sha256_file(executable),
        version=probe_text(
            [str(executable), *version_args],
            cwd=repository,
        ).replace("\n", " | "),
    )


def _requested_targets(values: Sequence[str]) -> tuple[str, ...]:
    selected = tuple(sorted(set(values or TARGETS)))
    unknown = set(selected) - set(TARGETS)
    if unknown:
        raise ReproError(f"unknown fuzz targets: {sorted(unknown)}")
    if not selected:
        raise ReproError("at least one fuzz target is required")
    return selected


def _parse_executed_units(stderr: str) -> int | None:
    matches = EXECUTED_UNITS_RE.findall(stderr)
    return int(matches[-1], 10) if matches else None


def _copy_regular_tree(source: pathlib.Path, destination: pathlib.Path) -> None:
    identity = tree_identity(source)
    destination.mkdir(parents=True, exist_ok=False)
    for entry in identity.entries:
        relative = pathlib.PurePosixPath(str(entry["path"]))
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source.joinpath(*relative.parts), target)


def _generated_decode_sources() -> tuple[tuple[str, bytes], ...]:
    """Return deterministic exact generators that reach valid DSL decoding."""

    return (
        ("constant", bytes([37]) * 4096),
        ("linear", bytes((17 + 29 * index) & 0xFF for index in range(4096))),
        ("periodic", (b"MathSVG-absolute:" * 256)[:4096]),
    )


def _fuzz_command(
    cargo_fuzz: pathlib.Path,
    fuzz_dir: pathlib.Path,
    target: str,
    corpus: pathlib.Path,
    artifacts: pathlib.Path,
    *,
    runs: int,
    max_len: int,
    input_timeout_seconds: int,
    rss_limit_mb: int,
) -> list[str]:
    """Invoke the exact frozen cargo-fuzz binary under an explicit toolchain."""

    return [
        str(cargo_fuzz),
        "run",
        "--sanitizer",
        "address",
        "--fuzz-dir",
        str(fuzz_dir),
        target,
        str(corpus),
        "--",
        f"-runs={runs}",
        f"-max_len={max_len}",
        f"-timeout={input_timeout_seconds}",
        f"-rss_limit_mb={rss_limit_mb}",
        "-print_final_stats=1",
        "-verbosity=0",
        f"-artifact_prefix={artifacts}{os.sep}",
    ]


def _add_valid_decode_seeds(
    *,
    binary: pathlib.Path,
    corpus: pathlib.Path,
    scratch: pathlib.Path,
    repository: pathlib.Path,
) -> list[dict[str, object]]:
    """Create valid function-heavy archives before mutational decode fuzzing."""

    scratch.mkdir(parents=True, exist_ok=False)
    seeds: list[dict[str, object]] = []
    for name, payload in _generated_decode_sources():
        source = scratch / f"{name}.bin"
        archive = corpus / f"valid-{name}.msvg"
        source.write_bytes(payload)
        command = [
            str(binary),
            "compress",
            "--profile",
            "fast",
            "--threads",
            "1",
            "--max-input-bytes",
            str(len(payload)),
            str(source),
            str(archive),
        ]
        outcome = run_command(
            command,
            cwd=repository,
            timeout_seconds=120.0,
            stdout_path=scratch / f"{name}.compress.stdout",
            stderr_path=scratch / f"{name}.compress.stderr",
            environment=stable_environment(),
        )
        if outcome.timed_out or outcome.returncode != 0:
            raise ReproError(
                f"could not generate valid {name} decode seed: "
                f"exit={outcome.returncode}, timeout={outcome.timed_out}"
            )
        archive = regular_file(
            archive,
            label=f"generated {name} decode seed",
        )
        if archive.stat().st_size > TARGETS["mathsvg_decode"][2]:
            raise ReproError(
                f"generated {name} decode seed exceeds fuzz max input"
            )
        inspect_command = [
            str(binary),
            "inspect",
            "--verify",
            "--max-archive-bytes",
            str(TARGETS["mathsvg_decode"][2]),
            "--max-output-bytes",
            str(len(payload)),
            str(archive),
        ]
        inspection = run_command(
            inspect_command,
            cwd=repository,
            timeout_seconds=120.0,
            stdout_path=scratch / f"{name}.inspect.stdout",
            stderr_path=scratch / f"{name}.inspect.stderr",
            environment=stable_environment(),
        )
        if inspection.timed_out or inspection.returncode != 0:
            raise ReproError(
                f"generated {name} decode seed did not verify: "
                f"exit={inspection.returncode}, timeout={inspection.timed_out}"
            )
        seeds.append(
            {
                "generator": name,
                "source_bytes": len(payload),
                "source_sha256": hashlib.sha256(payload).hexdigest(),
                "archive_bytes": archive.stat().st_size,
                "archive_sha256": sha256_file(archive),
                "compress_command": command,
                "inspect_command": inspect_command,
            }
        )
    return seeds


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository", type=pathlib.Path, default=pathlib.Path.cwd()
    )
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--runs", type=int, default=10_000)
    parser.add_argument("--input-timeout-seconds", type=int, default=10)
    parser.add_argument("--wall-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--rss-limit-mb", type=int, default=2048)
    parser.add_argument("--cargo", type=pathlib.Path)
    parser.add_argument("--cargo-fuzz", type=pathlib.Path)
    parser.add_argument("--nightly-rustc", type=pathlib.Path)
    parser.add_argument(
        "--mathsvg-binary",
        type=pathlib.Path,
        default=pathlib.Path("target/release/mathsvg"),
    )
    parser.add_argument("--scratch-root", type=pathlib.Path)
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/fuzz/development-sanitizer-v1/report.json"
        ),
    )
    return parser.parse_args(argv)


def _resolve_tool(
    requested: pathlib.Path | None,
    fallback_name: str,
) -> pathlib.Path:
    if requested is not None:
        return requested
    discovered = shutil.which(fallback_name)
    if discovered is None:
        raise ReproError(f"could not find {fallback_name} on PATH")
    return pathlib.Path(discovered)


def _nightly_tool(
    requested: pathlib.Path | None,
    repository: pathlib.Path,
    name: str,
) -> pathlib.Path:
    if requested is not None:
        return requested
    rustup_name = shutil.which("rustup")
    if rustup_name is None:
        raise ReproError(f"could not find rustup to resolve nightly {name}")
    rendered = probe_text(
        [
            str(pathlib.Path(rustup_name).resolve()),
            "which",
            name,
            "--toolchain",
            "nightly",
        ],
        cwd=repository,
    )
    return pathlib.Path(rendered.strip())


def run_campaign(args: argparse.Namespace) -> dict[str, object]:
    repository = args.repository.resolve()
    output = (
        args.output.resolve()
        if args.output.is_absolute()
        else (repository / args.output).resolve()
    )
    if output.exists():
        raise ReproError(f"fuzz report already exists: {output}")
    if args.runs < 1:
        raise ReproError("--runs must be positive")
    if args.input_timeout_seconds < 1:
        raise ReproError("--input-timeout-seconds must be positive")
    if args.wall_timeout_seconds <= 0:
        raise ReproError("--wall-timeout-seconds must be positive")
    if args.rss_limit_mb < 64:
        raise ReproError("--rss-limit-mb must be at least 64")
    if not args.experiment_id or any(
        character.isspace() for character in args.experiment_id
    ):
        raise ReproError("experiment-id must be non-empty without whitespace")
    if not args.machine_id:
        raise ReproError("machine-id must not be empty")

    selected = _requested_targets(args.target)
    runner_path = pathlib.Path(__file__).resolve()
    runner_sha256 = sha256_file(runner_path)
    manifest = regular_file(
        repository / "fuzz" / "Cargo.toml",
        label="fuzz manifest",
    )
    lockfile = regular_file(
        repository / "fuzz" / "Cargo.lock",
        label="fuzz lockfile",
    )
    cargo = _tool(
        _nightly_tool(args.cargo, repository, "cargo"),
        name="cargo",
        version_args=("--version", "--verbose"),
        repository=repository,
    )
    cargo_fuzz = _tool(
        _resolve_tool(args.cargo_fuzz, "cargo-fuzz"),
        name="cargo-fuzz",
        version_args=("--version",),
        repository=repository,
    )
    rustc = _tool(
        _nightly_tool(args.nightly_rustc, repository, "rustc"),
        name="nightly rustc",
        version_args=("--version", "--verbose"),
        repository=repository,
    )
    bootstrap_binary = executable_file(
        (
            args.mathsvg_binary.resolve()
            if args.mathsvg_binary.is_absolute()
            else (repository / args.mathsvg_binary).resolve()
        ),
        label="MathSVG bootstrap binary",
    )
    source_before = git_identity(repository)
    immutable_before = {
        "runner_sha256": runner_sha256,
        "manifest_sha256": sha256_file(manifest),
        "lockfile_sha256": sha256_file(lockfile),
        "bootstrap_binary_sha256": sha256_file(bootstrap_binary),
        "targets": {
            target: {
                "source_sha256": sha256_file(
                    regular_file(repository / TARGETS[target][0], label=target)
                ),
                "seeds": tree_identity(
                    within_repository(
                        repository,
                        repository / TARGETS[target][1],
                        label=f"{target} seeds",
                    )
                ).sha256,
            }
            for target in selected
        },
    }
    scratch_parent = (
        args.scratch_root.resolve()
        if args.scratch_root is not None
        else pathlib.Path(tempfile.gettempdir()).resolve()
    )
    if not scratch_parent.is_dir():
        raise ReproError(f"scratch root is not a directory: {scratch_parent}")

    results: list[dict[str, object]] = []
    retained: list[tuple[pathlib.Path, pathlib.Path, pathlib.Path]] = []
    toolchain_environment = {
        "CARGO": str(cargo.path),
        "RUSTC": str(rustc.path),
        "RUSTUP_TOOLCHAIN": "nightly",
    }
    with tempfile.TemporaryDirectory(
        prefix="mathsvg-fuzz-campaign-",
        dir=scratch_parent,
    ) as temporary:
        scratch = pathlib.Path(temporary)
        for target in selected:
            source_relative, seeds_relative, max_len = TARGETS[target]
            source = regular_file(
                repository / source_relative,
                label=f"{target} source",
            )
            seeds = within_repository(
                repository,
                repository / seeds_relative,
                label=f"{target} seeds",
            )
            initial = tree_identity(seeds)
            target_scratch = scratch / target
            corpus = target_scratch / "corpus"
            artifacts = target_scratch / "artifacts"
            logs = target_scratch / "logs"
            target_scratch.mkdir(parents=True)
            _copy_regular_tree(seeds, corpus)
            artifacts.mkdir()
            logs.mkdir()
            bootstrap_seeds = (
                _add_valid_decode_seeds(
                    binary=bootstrap_binary,
                    corpus=corpus,
                    scratch=target_scratch / "valid-seed-generation",
                    repository=repository,
                )
                if target == "mathsvg_decode"
                else []
            )
            stdout_path = logs / "stdout.log"
            stderr_path = logs / "stderr.log"
            argv = _fuzz_command(
                cargo_fuzz.path,
                repository / "fuzz",
                target,
                corpus,
                artifacts,
                runs=args.runs,
                max_len=max_len,
                input_timeout_seconds=args.input_timeout_seconds,
                rss_limit_mb=args.rss_limit_mb,
            )
            outcome = run_command(
                argv,
                cwd=repository,
                timeout_seconds=args.wall_timeout_seconds,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                environment=stable_environment(
                    {
                        **toolchain_environment,
                        "RUST_BACKTRACE": "1",
                        "CARGO_TERM_COLOR": "never",
                    }
                ),
            )
            final_corpus = tree_identity(corpus)
            artifact_identity = tree_identity(artifacts)
            stderr = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            executed_units = _parse_executed_units(stderr)
            if outcome.timed_out:
                status = "timeout"
                reason = (
                    f"campaign wall timeout after "
                    f"{args.wall_timeout_seconds:g} seconds"
                )
            elif outcome.returncode != 0:
                status = "failed"
                reason = f"cargo fuzz exited {outcome.returncode}"
            elif artifact_identity.files:
                status = "failed"
                reason = "sanitizer/libFuzzer retained crash artifacts"
            elif executed_units is None:
                status = "failed"
                reason = "libFuzzer final executed-unit counter is missing"
            elif (
                executed_units < args.runs
            ):
                status = "failed"
                reason = (
                    f"libFuzzer reported {executed_units} units, fewer "
                    f"than the requested {args.runs}"
                )
            else:
                status = "ok"
                reason = ""
            retained.append((logs, artifacts, pathlib.Path(target)))
            results.append(
                {
                    "target": target,
                    "source_path": source_relative,
                    "source_sha256": sha256_file(source),
                    "seed_path": seeds_relative,
                    "initial_seed_corpus": {
                        "files": initial.files,
                        "bytes": initial.bytes,
                        "sha256": initial.sha256,
                    },
                    "generated_valid_seeds": bootstrap_seeds,
                    "final_ephemeral_corpus": {
                        "files": final_corpus.files,
                        "bytes": final_corpus.bytes,
                        "sha256": final_corpus.sha256,
                        "preserved": False,
                    },
                    "artifacts": {
                        "files": artifact_identity.files,
                        "bytes": artifact_identity.bytes,
                        "sha256": artifact_identity.sha256,
                    },
                    "sanitizer": "address",
                    "planned_runs": args.runs,
                    "reported_executed_units": executed_units,
                    "max_input_bytes": max_len,
                    "per_input_timeout_seconds": args.input_timeout_seconds,
                    "rss_limit_mb": args.rss_limit_mb,
                    "campaign_wall_timeout_seconds": (
                        args.wall_timeout_seconds
                    ),
                    "command": argv,
                    "toolchain_environment": toolchain_environment,
                    "elapsed_ns": outcome.elapsed_ns,
                    "exit_code": outcome.returncode,
                    "timed_out": outcome.timed_out,
                    "status": status,
                    "reason": reason,
                    "stdout_log": log_identity(stdout_path),
                    "stderr_log": log_identity(stderr_path),
                }
            )

        immutable_after = {
            "runner_sha256": sha256_file(runner_path),
            "manifest_sha256": sha256_file(manifest),
            "lockfile_sha256": sha256_file(lockfile),
            "bootstrap_binary_sha256": sha256_file(bootstrap_binary),
            "targets": {
                target: {
                    "source_sha256": sha256_file(
                        repository / TARGETS[target][0]
                    ),
                    "seeds": tree_identity(
                        repository / TARGETS[target][1]
                    ).sha256,
                }
                for target in selected
            },
        }
        source_after = git_identity(repository)
        source_stable = source_before == source_after
        immutable_inputs_stable = immutable_before == immutable_after

        report_root = output.parent
        logs_root = report_root / "logs"
        artifacts_root = report_root / "artifacts"
        if logs_root.exists() or artifacts_root.exists():
            raise ReproError(
                "fuzz log/artifact output directory already exists"
            )
        logs_root.mkdir(parents=True)
        artifacts_root.mkdir(parents=True)
        for logs, artifacts, target_path in retained:
            shutil.copytree(logs, logs_root / target_path)
            shutil.copytree(artifacts, artifacts_root / target_path)

    failed = [row for row in results if row["status"] != "ok"]
    status = (
        "failed"
        if failed
        else (
            "incomplete"
            if not source_stable or not immutable_inputs_stable
            else "development-pass"
        )
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": args.experiment_id,
        "evidence_scope": "development",
        "status": status,
        "failure_count": len(failed),
        "source_before": source_before,
        "source_after": source_after,
        "source_stable_during_campaign": source_stable,
        "immutable_inputs_stable": immutable_inputs_stable,
        "immutable_inputs_sha256": canonical_sha256(immutable_before),
        "machine": {
            "machine_id": args.machine_id,
            "architecture": platform.machine(),
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count() or 1,
            "python": platform.python_version(),
        },
        "tools": {
            "cargo": {
                "path": str(cargo.path),
                "sha256": cargo.sha256,
                "version": cargo.version,
            },
            "cargo_fuzz": {
                "path": str(cargo_fuzz.path),
                "sha256": cargo_fuzz.sha256,
                "version": cargo_fuzz.version,
            },
            "nightly_rustc": {
                "path": str(rustc.path),
                "sha256": rustc.sha256,
                "version": rustc.version,
            },
            "mathsvg_bootstrap_binary": {
                "path": str(bootstrap_binary),
                "sha256": sha256_file(bootstrap_binary),
                "purpose": (
                    "generate valid constant/linear/periodic archives so "
                    "mutational decode fuzzing reaches strict DSL/evaluator paths"
                ),
            },
        },
        "toolchain_environment": toolchain_environment,
        "runner_sha256": runner_sha256,
        "fuzz_manifest_sha256": sha256_file(manifest),
        "fuzz_lockfile_sha256": sha256_file(lockfile),
        "targets": results,
        "holdout_payload_inspected": False,
        "limitations": [
            "finite run counts do not prove absence of all defects",
            "coverage maps and the ephemeral generated corpus are not retained",
            "this single-machine dirty-tree campaign is development evidence",
        ],
    }
    atomic_json(output, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = run_campaign(args)
    except (
        OSError,
        ReproError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"fuzz campaign error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(
                    (
                        args.output.resolve()
                        if args.output.is_absolute()
                        else (args.repository.resolve() / args.output).resolve()
                    )
                ),
                "status": report["status"],
                "failure_count": report["failure_count"],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if report["status"] == "development-pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
