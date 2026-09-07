#!/usr/bin/env python3
"""Build and verify the local two-Rust-compiler development matrix.

This is deliberately a development-only, one-input check.  It creates both
release binaries in isolated temporary Cargo target directories, proves that
the stable build matches the declared canonical release binary, compares two
archives byte-for-byte, and exercises all four compiler/archive decode pairs.
It never reads a holdout manifest or payload.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from mathsvg.python.benchmarks.freeze import git_identity
from mathsvg.python.repro.common import (
    ReproError,
    atomic_json,
    canonical_sha256,
    executable_file,
    probe_text,
    regular_file,
    run_command,
    sha256_file,
    stable_environment,
    tree_identity,
    within_repository,
)


SCHEMA = "mathsvg-local-compiler-matrix-v1"
EXPERIMENT_ID = "compiler-matrix-local-v2"
STATUS = "development-pass-global-gate-incomplete"
INPUT_RELATIVE = pathlib.PurePosixPath(
    "datasets/data/canterbury/alice29.txt"
)
INPUT_BYTES = 152_089
INPUT_SHA256 = (
    "7467306ee0feed4971260f3c87421154a05be571d944e9cb021a5713700c38f0"
)
STABLE_RELEASE = "1.97.1"
COMPAT_RELEASE = "1.85.1"
STABLE_ID = f"stable-{STABLE_RELEASE}"
COMPAT_ID = f"rust-{COMPAT_RELEASE}"
EXPECTED_IGNORED_TESTS = 2
IGNORED_REASON = "Two explicitly development-only oracle scans are opt-in."

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_TEST_RESULT = re.compile(
    r"^\s*test result: (?P<status>ok|FAILED)\.\s+"
    r"(?P<passed>\d+) passed;\s+"
    r"(?P<failed>\d+) failed;\s+"
    r"(?P<ignored>\d+) ignored;",
    re.MULTILINE,
)

TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "experiment_id",
        "status",
        "evidence_scope",
        "holdout_payload_inspected",
        "source_identity",
        "machine",
        "toolchains",
        "workspace_test",
        "archive_comparison",
        "limitations",
    }
)
SOURCE_KEYS = frozenset(
    {
        "base_git_commit",
        "worktree_state",
        "rust_sources_changed_during_matrix",
        "note",
    }
)
MACHINE_KEYS = frozenset(
    {
        "machine_id",
        "architecture",
        "target",
        "independent_second_machine",
        "arm64",
    }
)
TOOLCHAIN_KEYS = frozenset(
    {
        "id",
        "rustc_version",
        "rustc_commit",
        "llvm_version",
        "rustc_path",
        "rustc_bytes",
        "rustc_sha256",
        "cargo_path",
        "cargo_bytes",
        "cargo_sha256",
        "release_binary_bytes",
        "release_binary_sha256",
    }
)
WORKSPACE_TEST_KEYS = frozenset(
    {
        "toolchain",
        "command",
        "working_directory",
        "exit_code",
        "passed",
        "failed",
        "ignored",
        "ignored_reason",
    }
)
ARCHIVE_KEYS = frozenset(
    {
        "input",
        "input_bytes",
        "input_sha256",
        "command_template",
        "archive_bytes",
        "stable_archive_sha256",
        "rust_1_85_archive_sha256",
        "archive_byte_identical",
        "stable_self_decode",
        "rust_1_85_self_decode",
        "stable_decodes_rust_1_85_archive",
        "rust_1_85_decodes_stable_archive",
        "all_restored_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class RustcDetails:
    version: str
    release: str
    commit: str
    host: str
    llvm_version: str


@dataclass(frozen=True, slots=True)
class Toolchain:
    selector: str
    identifier: str
    cargo: pathlib.Path
    rustc: pathlib.Path
    rustdoc: pathlib.Path
    rustc_details: RustcDetails
    cargo_bytes: int
    cargo_sha256: str
    rustc_bytes: int
    rustc_sha256: str
    rustdoc_bytes: int
    rustdoc_sha256: str

    def environment(self, target: pathlib.Path) -> dict[str, str]:
        return stable_environment(
            {
                "CARGO_TARGET_DIR": str(target),
                "RUSTC": str(self.rustc),
                "RUSTDOC": str(self.rustdoc),
                "CARGO_INCREMENTAL": "0",
                "CARGO_NET_OFFLINE": "true",
                "RUSTFLAGS": "",
                "RUSTDOCFLAGS": "",
                "CARGO_ENCODED_RUSTFLAGS": "",
            }
        )

    def report_entry(self, binary: pathlib.Path) -> dict[str, object]:
        return {
            "id": self.identifier,
            "rustc_version": self.rustc_details.version,
            "rustc_commit": self.rustc_details.commit,
            "llvm_version": self.rustc_details.llvm_version,
            "rustc_path": str(self.rustc),
            "rustc_bytes": self.rustc_bytes,
            "rustc_sha256": self.rustc_sha256,
            "cargo_path": str(self.cargo),
            "cargo_bytes": self.cargo_bytes,
            "cargo_sha256": self.cargo_sha256,
            "release_binary_bytes": binary.stat().st_size,
            "release_binary_sha256": sha256_file(binary),
        }


@dataclass(frozen=True, slots=True)
class TestCounts:
    summaries: int
    passed: int
    failed: int
    ignored: int
    all_ok: bool


def _repo_path(repository: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    return path.resolve() if path.is_absolute() else (repository / path).resolve()


def parse_rustc_verbose(rendered: str) -> RustcDetails:
    """Parse the stable fields emitted by ``rustc -vV``."""

    lines = [line.strip() for line in rendered.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("rustc "):
        raise ReproError("rustc -vV output has no rustc version line")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip().lower()] = value.strip()
    required = ("release", "commit-hash", "host", "llvm version")
    missing = [name for name in required if not fields.get(name)]
    if missing:
        raise ReproError(
            "rustc -vV output is missing: " + ", ".join(missing)
        )
    commit = fields["commit-hash"]
    if not _GIT_COMMIT.fullmatch(commit):
        raise ReproError("rustc -vV returned an invalid commit hash")
    return RustcDetails(
        version=lines[0],
        release=fields["release"],
        commit=commit,
        host=fields["host"],
        llvm_version=fields["llvm version"],
    )


def parse_test_counts(rendered: str) -> TestCounts:
    matches = list(_TEST_RESULT.finditer(rendered))
    if not matches:
        raise ReproError("cargo test produced no recognizable test summaries")
    return TestCounts(
        summaries=len(matches),
        passed=sum(int(match.group("passed")) for match in matches),
        failed=sum(int(match.group("failed")) for match in matches),
        ignored=sum(int(match.group("ignored")) for match in matches),
        all_ok=all(match.group("status") == "ok" for match in matches),
    )


def _rustup_which(
    rustup: pathlib.Path,
    repository: pathlib.Path,
    selector: str,
    executable: str,
) -> pathlib.Path:
    rendered = probe_text(
        [
            str(rustup),
            "which",
            "--toolchain",
            selector,
            executable,
        ],
        cwd=repository,
    )
    return executable_file(
        pathlib.Path(rendered),
        label=f"{selector} {executable}",
    )


def _resolve_toolchain(
    rustup: pathlib.Path,
    repository: pathlib.Path,
    selector: str,
    *,
    expected_release: str,
    identifier: str,
) -> Toolchain:
    cargo = _rustup_which(rustup, repository, selector, "cargo")
    rustc = _rustup_which(rustup, repository, selector, "rustc")
    rustdoc = _rustup_which(rustup, repository, selector, "rustdoc")
    details = parse_rustc_verbose(
        probe_text([str(rustc), "-vV"], cwd=repository)
    )
    if details.release != expected_release:
        raise ReproError(
            f"{selector} resolved rustc {details.release}; "
            f"compiler-matrix-local-v2 requires {expected_release}"
        )
    expected_directory = rustc.parent.resolve()
    if cargo.parent.resolve() != expected_directory:
        raise ReproError(f"{selector} cargo and rustc are from different bins")
    if rustdoc.parent.resolve() != expected_directory:
        raise ReproError(f"{selector} rustdoc and rustc are from different bins")
    return Toolchain(
        selector=selector,
        identifier=identifier,
        cargo=cargo,
        rustc=rustc,
        rustdoc=rustdoc,
        rustc_details=details,
        cargo_bytes=cargo.stat().st_size,
        cargo_sha256=sha256_file(cargo),
        rustc_bytes=rustc.stat().st_size,
        rustc_sha256=sha256_file(rustc),
        rustdoc_bytes=rustdoc.stat().st_size,
        rustdoc_sha256=sha256_file(rustdoc),
    )


def _toolchain_unchanged(toolchain: Toolchain) -> bool:
    return all(
        (
            toolchain.cargo.stat().st_size == toolchain.cargo_bytes,
            sha256_file(toolchain.cargo) == toolchain.cargo_sha256,
            toolchain.rustc.stat().st_size == toolchain.rustc_bytes,
            sha256_file(toolchain.rustc) == toolchain.rustc_sha256,
            toolchain.rustdoc.stat().st_size == toolchain.rustdoc_bytes,
            sha256_file(toolchain.rustdoc) == toolchain.rustdoc_sha256,
        )
    )


def _source_snapshot(repository: pathlib.Path) -> str:
    """Hash every local file that can affect this workspace build."""

    files: dict[str, dict[str, object]] = {}
    for relative in (
        pathlib.Path("Cargo.toml"),
        pathlib.Path("Cargo.lock"),
        pathlib.Path("mathsvg/python/repro/compiler_matrix.py"),
        pathlib.Path("mathsvg/python/repro/common.py"),
    ):
        path = regular_file(repository / relative, label="matrix source input")
        files[relative.as_posix()] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    trees: dict[str, dict[str, object]] = {}
    for relative in (pathlib.Path("crates"), pathlib.Path("mathsvg/crates")):
        identity = tree_identity(repository / relative)
        trees[relative.as_posix()] = {
            "files": identity.files,
            "bytes": identity.bytes,
            "sha256": identity.sha256,
        }
    cargo_config = repository / ".cargo"
    if cargo_config.exists():
        identity = tree_identity(cargo_config)
        trees[".cargo"] = {
            "files": identity.files,
            "bytes": identity.bytes,
            "sha256": identity.sha256,
        }
    return canonical_sha256({"files": files, "trees": trees})


def _run_checked(
    argv: Sequence[str],
    *,
    cwd: pathlib.Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    log_directory: pathlib.Path,
    name: str,
) -> str:
    result = run_command(
        argv,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        stdout_path=log_directory / f"{name}.stdout",
        stderr_path=log_directory / f"{name}.stderr",
        environment=environment,
    )
    stdout = result.stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = result.stderr_path.read_text(encoding="utf-8", errors="replace")
    if result.timed_out:
        raise ReproError(f"{name} timed out")
    if result.returncode != 0:
        detail = (stderr or stdout).strip()[-2000:]
        raise ReproError(f"{name} exited {result.returncode}: {detail}")
    return stdout + "\n" + stderr


def _same_file_bytes(first: pathlib.Path, second: pathlib.Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            left_chunk = left.read(1024 * 1024)
            right_chunk = right.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _safe_input(repository: pathlib.Path, requested: pathlib.Path) -> pathlib.Path:
    expected = (repository / INPUT_RELATIVE).resolve()
    candidate = _repo_path(repository, requested)
    if candidate != expected:
        raise ReproError(
            "compiler matrix is locked to the development Alice input"
        )
    path = regular_file(
        within_repository(repository, candidate, label="compiler matrix input"),
        label="compiler matrix input",
    )
    if path.stat().st_size != INPUT_BYTES:
        raise ReproError("Alice input length differs from the pinned identity")
    if sha256_file(path) != INPUT_SHA256:
        raise ReproError("Alice input SHA-256 differs from the pinned identity")
    return path


def _exact_keys(value: object, expected: frozenset[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ReproError(f"{label} must be an object")
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ReproError(
            f"{label} has invalid keys; missing={missing}, extra={extra}"
        )
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReproError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReproError(f"{label} must be an integer")
    if positive and value <= 0:
        raise ReproError(f"{label} must be positive")
    if not positive and value < 0:
        raise ReproError(f"{label} must be non-negative")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ReproError(f"{label} must be a boolean")
    return value


def _digest(value: object, label: str) -> str:
    rendered = _string(value, label)
    if not _SHA256.fullmatch(rendered):
        raise ReproError(f"{label} must be a lowercase SHA-256")
    return rendered


def validate_report(report: object) -> dict[str, object]:
    """Validate the exact v1 JSON shape and the v2 matrix invariants."""

    document = _exact_keys(report, TOP_LEVEL_KEYS, "report")
    if document["schema"] != SCHEMA:
        raise ReproError(f"schema must be {SCHEMA!r}")
    if document["experiment_id"] != EXPERIMENT_ID:
        raise ReproError(f"experiment_id must be {EXPERIMENT_ID!r}")
    if document["status"] != STATUS:
        raise ReproError(f"status must be {STATUS!r}")
    if document["evidence_scope"] != "development":
        raise ReproError("evidence_scope must be development")
    if _boolean(
        document["holdout_payload_inspected"],
        "holdout_payload_inspected",
    ):
        raise ReproError("holdout_payload_inspected must be false")

    source = _exact_keys(document["source_identity"], SOURCE_KEYS, "source_identity")
    commit = _string(source["base_git_commit"], "source_identity.base_git_commit")
    if not _GIT_COMMIT.fullmatch(commit):
        raise ReproError("source_identity.base_git_commit is invalid")
    if source["worktree_state"] not in ("clean", "dirty"):
        raise ReproError("source_identity.worktree_state must be clean or dirty")
    if _boolean(
        source["rust_sources_changed_during_matrix"],
        "source_identity.rust_sources_changed_during_matrix",
    ):
        raise ReproError("source changed during the compiler matrix")
    _string(source["note"], "source_identity.note")

    machine = _exact_keys(document["machine"], MACHINE_KEYS, "machine")
    _string(machine["machine_id"], "machine.machine_id")
    architecture = _string(machine["architecture"], "machine.architecture")
    target = _string(machine["target"], "machine.target")
    if not target.startswith(architecture + "-"):
        aliases = {("arm64", "aarch64"), ("aarch64", "arm64")}
        if not any(
            architecture == left and target.startswith(right + "-")
            for left, right in aliases
        ):
            raise ReproError("machine architecture and target disagree")
    if _boolean(
        machine["independent_second_machine"],
        "machine.independent_second_machine",
    ):
        raise ReproError("local v2 cannot claim an independent second machine")
    expected_arm64 = architecture in ("aarch64", "arm64")
    if _boolean(machine["arm64"], "machine.arm64") != expected_arm64:
        raise ReproError("machine.arm64 disagrees with architecture")

    toolchains = document["toolchains"]
    if not isinstance(toolchains, list) or len(toolchains) != 2:
        raise ReproError("toolchains must contain exactly two entries")
    expected_toolchains = ((STABLE_ID, STABLE_RELEASE), (COMPAT_ID, COMPAT_RELEASE))
    rustc_digests: set[str] = set()
    hosts: list[str] = []
    for index, (expected_id, expected_release) in enumerate(expected_toolchains):
        row = _exact_keys(toolchains[index], TOOLCHAIN_KEYS, f"toolchains[{index}]")
        if row["id"] != expected_id:
            raise ReproError(f"toolchains[{index}].id must be {expected_id!r}")
        version = _string(row["rustc_version"], f"toolchains[{index}].rustc_version")
        if not version.startswith(f"rustc {expected_release} "):
            raise ReproError(f"toolchains[{index}] has the wrong rustc release")
        rustc_commit = _string(row["rustc_commit"], f"toolchains[{index}].rustc_commit")
        if not _GIT_COMMIT.fullmatch(rustc_commit):
            raise ReproError(f"toolchains[{index}].rustc_commit is invalid")
        _string(row["llvm_version"], f"toolchains[{index}].llvm_version")
        for prefix in ("rustc", "cargo"):
            path = pathlib.Path(
                _string(row[f"{prefix}_path"], f"toolchains[{index}].{prefix}_path")
            )
            if not path.is_absolute():
                raise ReproError(f"toolchains[{index}].{prefix}_path must be absolute")
            _integer(row[f"{prefix}_bytes"], f"toolchains[{index}].{prefix}_bytes", positive=True)
            digest = _digest(row[f"{prefix}_sha256"], f"toolchains[{index}].{prefix}_sha256")
            if prefix == "rustc":
                rustc_digests.add(digest)
        _integer(
            row["release_binary_bytes"],
            f"toolchains[{index}].release_binary_bytes",
            positive=True,
        )
        _digest(
            row["release_binary_sha256"],
            f"toolchains[{index}].release_binary_sha256",
        )
        # The host is validated during generation; v1 has no field for it.
        hosts.append(target)
    if len(rustc_digests) != 2:
        raise ReproError("the two toolchains must have distinct rustc identities")

    workspace = _exact_keys(
        document["workspace_test"], WORKSPACE_TEST_KEYS, "workspace_test"
    )
    if workspace["toolchain"] != COMPAT_ID:
        raise ReproError(f"workspace_test.toolchain must be {COMPAT_ID!r}")
    command = _string(workspace["command"], "workspace_test.command")
    for required in ("test", "--locked", "--offline", "--workspace", "--release"):
        if required not in shlex.split(command):
            raise ReproError(f"workspace_test.command is missing {required}")
    if workspace["working_directory"] != ".":
        raise ReproError("workspace_test.working_directory must be '.'")
    if _integer(workspace["exit_code"], "workspace_test.exit_code") != 0:
        raise ReproError("workspace test did not exit successfully")
    _integer(workspace["passed"], "workspace_test.passed", positive=True)
    if _integer(workspace["failed"], "workspace_test.failed") != 0:
        raise ReproError("workspace tests contain failures")
    if _integer(workspace["ignored"], "workspace_test.ignored") != EXPECTED_IGNORED_TESTS:
        raise ReproError("workspace ignored-test count differs from the pinned contract")
    if workspace["ignored_reason"] != IGNORED_REASON:
        raise ReproError("workspace_test.ignored_reason is invalid")

    archive = _exact_keys(
        document["archive_comparison"], ARCHIVE_KEYS, "archive_comparison"
    )
    if archive["input"] != INPUT_RELATIVE.as_posix():
        raise ReproError("archive input is not the pinned Alice path")
    if _integer(archive["input_bytes"], "archive_comparison.input_bytes", positive=True) != INPUT_BYTES:
        raise ReproError("archive input byte count is not the pinned Alice size")
    if _digest(archive["input_sha256"], "archive_comparison.input_sha256") != INPUT_SHA256:
        raise ReproError("archive input digest is not the pinned Alice digest")
    if archive["command_template"] != (
        "{binary} compress --profile balanced --threads 1 {input} {archive}"
    ):
        raise ReproError("archive command_template is invalid")
    _integer(archive["archive_bytes"], "archive_comparison.archive_bytes", positive=True)
    stable_archive = _digest(
        archive["stable_archive_sha256"],
        "archive_comparison.stable_archive_sha256",
    )
    compat_archive = _digest(
        archive["rust_1_85_archive_sha256"],
        "archive_comparison.rust_1_85_archive_sha256",
    )
    if stable_archive != compat_archive:
        raise ReproError("compiler archives have different SHA-256 identities")
    if not _boolean(
        archive["archive_byte_identical"],
        "archive_comparison.archive_byte_identical",
    ):
        raise ReproError("compiler archives are not byte-identical")
    for key in (
        "stable_self_decode",
        "rust_1_85_self_decode",
        "stable_decodes_rust_1_85_archive",
        "rust_1_85_decodes_stable_archive",
    ):
        if archive[key] != "pass":
            raise ReproError(f"archive_comparison.{key} must be pass")
    if _digest(
        archive["all_restored_sha256"],
        "archive_comparison.all_restored_sha256",
    ) != INPUT_SHA256:
        raise ReproError("not all restored files match the input")

    limitations = document["limitations"]
    if not isinstance(limitations, list) or not all(
        isinstance(item, str) and item.strip() for item in limitations
    ):
        raise ReproError("limitations must be a non-empty string list")
    joined = " ".join(limitations).lower()
    for phrase in ("same physical", "one-input", "independent second machine"):
        if phrase not in joined:
            raise ReproError(f"limitations must disclose {phrase!r}")
    if source["worktree_state"] == "dirty" and "dirty" not in joined:
        raise ReproError("limitations must disclose the dirty source tree")
    return document


def _verify_report_files(
    repository: pathlib.Path,
    report: Mapping[str, object],
    canonical_release: pathlib.Path,
) -> None:
    archive = report["archive_comparison"]
    assert isinstance(archive, dict)
    input_path = _safe_input(repository, pathlib.Path(str(archive["input"])))
    if sha256_file(input_path) != archive["input_sha256"]:
        raise ReproError("report input no longer matches its identity")
    toolchains = report["toolchains"]
    assert isinstance(toolchains, list)
    for index, row in enumerate(toolchains):
        assert isinstance(row, dict)
        for prefix in ("rustc", "cargo"):
            path = executable_file(
                pathlib.Path(str(row[f"{prefix}_path"])),
                label=f"reported toolchains[{index}].{prefix}",
            )
            if path.stat().st_size != row[f"{prefix}_bytes"]:
                raise ReproError(f"reported {prefix} byte count changed")
            if sha256_file(path) != row[f"{prefix}_sha256"]:
                raise ReproError(f"reported {prefix} SHA-256 changed")
    canonical = executable_file(
        _repo_path(repository, canonical_release),
        label="canonical release MathSVG CLI",
    )
    stable = toolchains[0]
    assert isinstance(stable, dict)
    if canonical.stat().st_size != stable["release_binary_bytes"]:
        raise ReproError("canonical release byte count differs from stable build")
    if sha256_file(canonical) != stable["release_binary_sha256"]:
        raise ReproError("canonical release SHA-256 differs from stable build")


def _recorded_test_command(
    toolchain: Toolchain,
    target: pathlib.Path,
    argv: Sequence[str],
) -> str:
    replacements = {
        str(target): "{workdir}/target-rust-1.85",
    }
    tokens = [
        f"CARGO_TARGET_DIR={replacements[str(target)]}",
        f"RUSTC={toolchain.rustc}",
        f"RUSTDOC={toolchain.rustdoc}",
        *argv,
    ]
    return shlex.join(tokens)


def generate_report(args: argparse.Namespace) -> dict[str, object]:
    repository = args.repository.resolve()
    if not args.machine_id.strip():
        raise ReproError("machine-id must be non-empty")
    if args.experiment_id != EXPERIMENT_ID:
        raise ReproError(f"experiment-id must be {EXPERIMENT_ID!r}")
    if args.timeout_seconds <= 0:
        raise ReproError("timeout-seconds must be positive")
    if args.profile != "balanced":
        raise ReproError("compiler-matrix-local-v2 is pinned to balanced")

    output = within_repository(
        repository,
        _repo_path(repository, args.output),
        label="compiler matrix output",
    )
    if output.exists():
        raise ReproError("compiler matrix output already exists")
    input_path = _safe_input(repository, args.input)
    canonical = executable_file(
        within_repository(
            repository,
            _repo_path(repository, args.canonical_release),
            label="canonical release MathSVG CLI",
        ),
        label="canonical release MathSVG CLI",
    )
    canonical_before = {
        "bytes": canonical.stat().st_size,
        "sha256": sha256_file(canonical),
    }

    requested_rustup = args.rustup
    if requested_rustup is None:
        discovered = shutil.which("rustup")
        if discovered is None:
            raise ReproError("rustup was not found")
        requested_rustup = pathlib.Path(discovered)
    rustup = executable_file(requested_rustup, label="rustup")
    stable = _resolve_toolchain(
        rustup,
        repository,
        args.stable_toolchain,
        expected_release=STABLE_RELEASE,
        identifier=STABLE_ID,
    )
    compat = _resolve_toolchain(
        rustup,
        repository,
        args.compat_toolchain,
        expected_release=COMPAT_RELEASE,
        identifier=COMPAT_ID,
    )
    if stable.rustc_sha256 == compat.rustc_sha256:
        raise ReproError("stable and compatibility rustc identities are equal")
    if stable.rustc_details.host != compat.rustc_details.host:
        raise ReproError("the two rustc toolchains target different hosts")

    source_before = _source_snapshot(repository)
    git_before = git_identity(repository, excluded_artifacts=(output,))
    with tempfile.TemporaryDirectory(
        prefix="mathsvg-compiler-matrix-v2-"
    ) as temporary:
        work = pathlib.Path(temporary)
        logs = work / "logs"
        stable_target = work / "target-stable"
        compat_target = work / "target-rust-1.85"
        stable_environment_map = stable.environment(stable_target)
        compat_environment_map = compat.environment(compat_target)

        test_argv = [
            str(compat.cargo),
            "test",
            "--locked",
            "--offline",
            "--workspace",
            "--release",
        ]
        test_output = _run_checked(
            test_argv,
            cwd=repository,
            environment=compat_environment_map,
            timeout_seconds=args.timeout_seconds,
            log_directory=logs,
            name="compat-workspace-test",
        )
        counts = parse_test_counts(test_output)
        if not counts.all_ok or counts.failed:
            raise ReproError("compatibility workspace test summaries contain failures")
        if counts.passed <= 0:
            raise ReproError("compatibility workspace test ran no passing tests")
        if counts.ignored != EXPECTED_IGNORED_TESTS:
            raise ReproError(
                "compatibility workspace ignored-test count differs from the "
                f"pinned value {EXPECTED_IGNORED_TESTS}"
            )

        build_argv = [
            "build",
            "--locked",
            "--offline",
            "--release",
            "-p",
            "mathsvg-cli",
        ]
        for label, toolchain, environment in (
            ("stable", stable, stable_environment_map),
            ("compat", compat, compat_environment_map),
        ):
            _run_checked(
                [str(toolchain.cargo), *build_argv],
                cwd=repository,
                environment=environment,
                timeout_seconds=args.timeout_seconds,
                log_directory=logs,
                name=f"{label}-release-build",
            )

        stable_binary = executable_file(
            stable_target / "release" / "mathsvg",
            label="isolated stable MathSVG CLI",
        )
        compat_binary = executable_file(
            compat_target / "release" / "mathsvg",
            label="isolated Rust 1.85 MathSVG CLI",
        )
        if stable_binary.stat().st_size != canonical_before["bytes"]:
            raise ReproError("stable build byte count differs from canonical release")
        if sha256_file(stable_binary) != canonical_before["sha256"]:
            raise ReproError("stable build SHA-256 differs from canonical release")

        stable_archive = work / "stable.msvg"
        compat_archive = work / "rust-1.85.msvg"
        for label, binary, archive in (
            ("stable", stable_binary, stable_archive),
            ("compat", compat_binary, compat_archive),
        ):
            _run_checked(
                [
                    str(binary),
                    "compress",
                    "--profile",
                    "balanced",
                    "--threads",
                    "1",
                    str(input_path),
                    str(archive),
                ],
                cwd=repository,
                environment=stable_environment(),
                timeout_seconds=args.timeout_seconds,
                log_directory=logs,
                name=f"{label}-compress",
            )
            regular_file(archive, label=f"{label} archive")
        stable_archive_sha256 = sha256_file(stable_archive)
        compat_archive_sha256 = sha256_file(compat_archive)
        archive_identical = (
            stable_archive_sha256 == compat_archive_sha256
            and _same_file_bytes(stable_archive, compat_archive)
        )
        if not archive_identical:
            raise ReproError("stable and Rust 1.85 archives are not byte-identical")

        decode_cells = (
            ("stable-self", stable_binary, stable_archive),
            ("stable-cross", stable_binary, compat_archive),
            ("compat-self", compat_binary, compat_archive),
            ("compat-cross", compat_binary, stable_archive),
        )
        restored_hashes: list[str] = []
        for label, binary, archive in decode_cells:
            restored = work / f"{label}.txt"
            _run_checked(
                [str(binary), "decompress", str(archive), str(restored)],
                cwd=repository,
                environment=stable_environment(),
                timeout_seconds=args.timeout_seconds,
                log_directory=logs,
                name=f"{label}-decode",
            )
            regular_file(restored, label=f"{label} restored payload")
            restored_hashes.append(sha256_file(restored))
        if any(digest != INPUT_SHA256 for digest in restored_hashes):
            raise ReproError("one or more cross-decoded files differ from Alice")

        source_after = _source_snapshot(repository)
        git_after = git_identity(repository, excluded_artifacts=(output,))
        if source_before != source_after:
            raise ReproError("workspace source content changed during the matrix")
        if git_before != git_after:
            raise ReproError("Git worktree identity changed during the matrix")
        if sha256_file(input_path) != INPUT_SHA256:
            raise ReproError("Alice input changed during the matrix")
        if (
            canonical.stat().st_size != canonical_before["bytes"]
            or sha256_file(canonical) != canonical_before["sha256"]
        ):
            raise ReproError("canonical release binary changed during the matrix")
        if not _toolchain_unchanged(stable) or not _toolchain_unchanged(compat):
            raise ReproError("a compiler toolchain changed during the matrix")

        dirty = bool(git_before["dirty"])
        limitations = [
            "Both compilers ran on the same physical host.",
            (
                "The archive comparison is a one-input development spot check, "
                "not the full 100-repetition Gate 1 matrix."
            ),
            "ARM64 and an independent second machine remain unavailable.",
        ]
        if dirty:
            limitations.append(
                "The source tree is dirty, so the global publishable correctness "
                "gate remains incomplete."
            )
        report: dict[str, object] = {
            "schema": SCHEMA,
            "experiment_id": args.experiment_id,
            "status": STATUS,
            "evidence_scope": "development",
            "holdout_payload_inspected": False,
            "source_identity": {
                "base_git_commit": git_before["commit"],
                "worktree_state": "dirty" if dirty else "clean",
                "rust_sources_changed_during_matrix": False,
                "note": (
                    "Both compilers built the same workspace source/configuration "
                    f"content snapshot {source_before}."
                ),
            },
            "machine": {
                "machine_id": args.machine_id,
                "architecture": platform.machine(),
                "target": stable.rustc_details.host,
                "independent_second_machine": False,
                "arm64": platform.machine() in ("aarch64", "arm64"),
            },
            "toolchains": [
                stable.report_entry(stable_binary),
                compat.report_entry(compat_binary),
            ],
            "workspace_test": {
                "toolchain": COMPAT_ID,
                "command": _recorded_test_command(
                    compat, compat_target, test_argv
                ),
                "working_directory": ".",
                "exit_code": 0,
                "passed": counts.passed,
                "failed": counts.failed,
                "ignored": counts.ignored,
                "ignored_reason": IGNORED_REASON,
            },
            "archive_comparison": {
                "input": INPUT_RELATIVE.as_posix(),
                "input_bytes": INPUT_BYTES,
                "input_sha256": INPUT_SHA256,
                "command_template": (
                    "{binary} compress --profile balanced --threads 1 "
                    "{input} {archive}"
                ),
                "archive_bytes": stable_archive.stat().st_size,
                "stable_archive_sha256": stable_archive_sha256,
                "rust_1_85_archive_sha256": compat_archive_sha256,
                "archive_byte_identical": True,
                "stable_self_decode": "pass",
                "rust_1_85_self_decode": "pass",
                "stable_decodes_rust_1_85_archive": "pass",
                "rust_1_85_decodes_stable_archive": "pass",
                "all_restored_sha256": INPUT_SHA256,
            },
            "limitations": limitations,
        }
    validated = validate_report(report)
    atomic_json(output, validated)
    return validated


def validate_report_file(args: argparse.Namespace) -> dict[str, object]:
    repository = args.repository.resolve()
    report_path = regular_file(
        within_repository(
            repository,
            _repo_path(repository, args.report),
            label="compiler matrix report",
        ),
        label="compiler matrix report",
    )
    try:
        document = json.loads(report_path.read_text(encoding="ascii"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReproError(f"compiler matrix report is invalid JSON: {exc}") from exc
    validated = validate_report(document)
    if args.verify_files:
        _verify_report_files(repository, validated, args.canonical_release)
    return validated


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate")
    generate.add_argument(
        "--repository", type=pathlib.Path, default=pathlib.Path.cwd()
    )
    generate.add_argument("--experiment-id", default=EXPERIMENT_ID)
    generate.add_argument("--machine-id", required=True)
    generate.add_argument(
        "--stable-toolchain", default="stable-x86_64-unknown-linux-gnu"
    )
    generate.add_argument(
        "--compat-toolchain", default="1.85-x86_64-unknown-linux-gnu"
    )
    generate.add_argument("--rustup", type=pathlib.Path)
    generate.add_argument(
        "--canonical-release",
        type=pathlib.Path,
        default=pathlib.Path("target/release/mathsvg"),
    )
    generate.add_argument(
        "--input", type=pathlib.Path, default=pathlib.Path(INPUT_RELATIVE)
    )
    generate.add_argument("--profile", choices=("balanced",), default="balanced")
    generate.add_argument("--timeout-seconds", type=float, default=1800.0)
    generate.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/determinism/compiler-matrix-local-v2.json"
        ),
    )

    validate = subparsers.add_parser("validate")
    validate.add_argument(
        "--repository", type=pathlib.Path, default=pathlib.Path.cwd()
    )
    validate.add_argument("--report", type=pathlib.Path, required=True)
    validate.add_argument(
        "--canonical-release",
        type=pathlib.Path,
        default=pathlib.Path("target/release/mathsvg"),
    )
    validate.add_argument("--verify-files", action="store_true")
    return parser


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "generate":
            report = generate_report(args)
            output = _repo_path(args.repository.resolve(), args.output)
            summary = {
                "status": report["status"],
                "path": output.relative_to(args.repository.resolve()).as_posix(),
                "bytes": output.stat().st_size,
                "sha256": sha256_file(output),
                "gate_artifact": {
                    "gate": "compiler-matrix-local",
                    "status": report["status"],
                    "path": output.relative_to(args.repository.resolve()).as_posix(),
                    "bytes": output.stat().st_size,
                    "sha256": sha256_file(output),
                },
            }
        else:
            report = validate_report_file(args)
            summary = {
                "status": "valid",
                "experiment_id": report["experiment_id"],
                "verified_files": args.verify_files,
            }
    except (
        OSError,
        ReproError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"compiler matrix error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            summary,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
