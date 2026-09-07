#!/usr/bin/env python3
"""Create a canonical MathSVG experiment-freeze record.

Development freezes are intentionally lightweight.  A publishable freeze is a
closed protocol: it validates the exact checked-in manifests and configuration
catalogues, the clean source commit, the release executable, compiler, machine,
and all five effective runtime-profile contracts.

No function in this module opens a dataset payload.  In particular, holdout
rows are read only from their CSV manifest; the paths named by those rows are
never resolved, stat'ed, hashed, or otherwise inspected here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import stat
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from mathsvg.python.benchmarks.config import (
    ConfigError,
    load_ablation_catalog,
    load_profile,
)
from mathsvg.python.datasets.manifest import (
    ManifestError,
    canonical_manifest_sha256,
    load_manifest,
)


class FreezeError(RuntimeError):
    """An experiment is not sufficiently pinned for the requested state."""


PUBLISHABLE_BRANCH = "mathsvg-absolute"
PROFILE_NAMES = ("balanced", "fast", "max", "repository", "structured")
BASELINE_INVENTORY_PATH = (
    "mathsvg/results/manifests/baseline-inventory.json"
)
REQUIRED_MANIFESTS: Mapping[str, str] = {
    "mathsvg/results/manifests/development.csv": "development",
    "mathsvg/results/manifests/holdout.csv": "holdout",
    "mathsvg/results/manifests/validation.csv": "validation",
}
REQUIRED_CONFIGS = frozenset(
    {
        *(f"mathsvg/configs/{profile}/profile.toml" for profile in PROFILE_NAMES),
        "mathsvg/configs/baselines.toml",
        "mathsvg/configs/ablation/catalog.toml",
    }
)
SOURCE_POLICY_PATHS: Mapping[str, str] = {
    "container_versions_and_limits": (
        "mathsvg/crates/mathsvg-container/src/lib.rs"
    ),
    "dsl_primitive_catalog": "mathsvg/crates/mathsvg-dsl/src/opcode.rs",
    "runtime_catalog_and_profile_contract": (
        "mathsvg/crates/mathsvg-cli/src/main.rs"
    ),
    "tie_break_policy": "mathsvg/crates/mathsvg-core/src/cost.rs",
}
REQUIRED_DOCUMENTATION_PATHS = frozenset(
    {
        "YeuCau.md",
        "mathsvg/README.md",
        "mathsvg/docs/benchmark-methodology.md",
        "mathsvg/docs/determinism.md",
        "mathsvg/docs/format-spec.md",
        "mathsvg/docs/impossibility-boundary.md",
        "mathsvg/docs/mathematical-spec.md",
        "mathsvg/docs/procedural-dsl.md",
    }
)
CARGO_LOCK_PATH = "Cargo.lock"
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_MAX_CONTROL_OUTPUT_BYTES = 1024 * 1024


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    """Hash a strict, deterministic JSON representation."""

    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FreezeError(f"value is not canonical JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _git(root: pathlib.Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise FreezeError(f"git {' '.join(arguments)} failed: {detail}")
    return process.stdout.strip()


def git_identity(
    root: pathlib.Path,
    *,
    excluded_artifacts: Sequence[pathlib.Path] = (),
) -> dict[str, object]:
    """Return the complete Git identity outside explicitly generated artifacts.

    Freeze callers use the default and therefore hash the entire worktree.
    Long-running evidence producers may exclude only their own declared output
    files so creating those files cannot masquerade as a source-code change.
    """
    repository_root = pathlib.Path(
        _git(root, "rev-parse", "--show-toplevel")
    ).resolve()
    requested_root = root.resolve()
    if repository_root != requested_root:
        raise FreezeError(
            "freeze root must be the repository root: "
            f"expected {repository_root}, got {requested_root}"
        )
    commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    pathspecs = ["."]
    for artifact in excluded_artifacts:
        candidate = (
            artifact
            if artifact.is_absolute()
            else repository_root / artifact
        ).resolve()
        try:
            relative = candidate.relative_to(repository_root)
        except ValueError as exc:
            raise FreezeError(
                "excluded Git artifact is outside the repository: "
                f"{artifact}"
            ) from exc
        if not relative.parts:
            raise FreezeError("repository root cannot be a Git artifact")
        pathspecs.append(f":(exclude){relative.as_posix()}")
    status_lines = [
        line
        for line in _git(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *pathspecs,
        ).splitlines()
        if line
    ]
    status_digest = hashlib.sha256(
        "\n".join(status_lines).encode("utf-8")
    ).hexdigest()
    return {
        "commit": commit,
        "branch": branch,
        "dirty": bool(status_lines),
        "status_sha256": status_digest,
    }


@dataclass(frozen=True, slots=True)
class FrozenFile:
    path: str
    sha256: str
    bytes: int


def _resolved_regular_file(
    path: pathlib.Path,
    *,
    label: str,
) -> pathlib.Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FreezeError(f"{label} is unavailable: {path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise FreezeError(f"{label} must not be a symbolic link: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise FreezeError(f"{label} is not a regular file: {path}")
    return path.resolve()


def frozen_file(root: pathlib.Path, path: pathlib.Path) -> FrozenFile:
    resolved_root = root.resolve()
    resolved = _resolved_regular_file(path, label="freeze input")
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise FreezeError(f"freeze input is outside repository: {path}") from exc
    return FrozenFile(
        path=relative.as_posix(),
        sha256=sha256_file(resolved),
        bytes=resolved.stat().st_size,
    )


def _freeze_files(
    root: pathlib.Path,
    paths: Sequence[pathlib.Path],
    *,
    kind: str,
) -> list[tuple[pathlib.Path, FrozenFile]]:
    records: list[tuple[pathlib.Path, FrozenFile]] = []
    seen: set[str] = set()
    for path in paths:
        record = frozen_file(root, path)
        if record.path in seen:
            raise FreezeError(f"duplicate {kind} input: {record.path}")
        seen.add(record.path)
        records.append((path, record))
    return sorted(records, key=lambda item: item[1].path)


def _require_exact_paths(
    actual: Sequence[FrozenFile],
    expected: set[str] | frozenset[str],
    *,
    kind: str,
) -> None:
    actual_paths = {record.path for record in actual}
    missing = sorted(expected - actual_paths)
    extra = sorted(actual_paths - expected)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise FreezeError(
            f"publishable freeze requires the exact {kind} set: "
            + "; ".join(details)
        )


def _validate_source_identity(source: Mapping[str, object]) -> None:
    commit = source.get("commit")
    branch = source.get("branch")
    dirty = source.get("dirty")
    status_digest = source.get("status_sha256")
    if not isinstance(commit, str) or not _GIT_COMMIT.fullmatch(commit):
        raise FreezeError("publishable freeze requires a canonical Git commit")
    if branch != PUBLISHABLE_BRANCH:
        raise FreezeError(
            "publishable freeze requires branch "
            f"{PUBLISHABLE_BRANCH!r}, got {branch!r}"
        )
    if dirty is not False:
        raise FreezeError("publishable freeze requires a clean source tree")
    if not isinstance(status_digest, str) or not _HEX_DIGEST.fullmatch(
        status_digest
    ):
        raise FreezeError("source status_sha256 is not a SHA-256 digest")
    if status_digest != hashlib.sha256(b"").hexdigest():
        raise FreezeError(
            "clean source status_sha256 does not identify an empty status"
        )


def _validate_baseline_catalog(
    path: pathlib.Path,
) -> dict[str, dict[str, object]]:
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise FreezeError(f"invalid baseline catalogue: {exc}") from exc
    if document.get("schema_version") != 1:
        raise FreezeError("unsupported baseline catalogue schema")
    if document.get("status") not in {"development", "frozen"}:
        raise FreezeError("baseline catalogue status must be development or frozen")
    if document.get("native_payload_forbidden") is not True:
        raise FreezeError(
            "baseline catalogue must set native_payload_forbidden=true"
        )
    rows = document.get("baseline")
    if not isinstance(rows, list) or not rows:
        raise FreezeError("baseline catalogue must contain baseline rows")
    catalog: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FreezeError("baseline catalogue row must be a table")
        identifier = row.get("id")
        family = row.get("family")
        required = row.get("required")
        command = row.get("command_template")
        availability = row.get("availability", "auto")
        reason = row.get("unavailable_reason", "")
        if (
            not isinstance(identifier, str)
            or not identifier
            or any(
                not (
                    character.islower()
                    or character.isdigit()
                    or character == "-"
                )
                for character in identifier
            )
        ):
            raise FreezeError("baseline catalogue row has no canonical id")
        if identifier in catalog:
            raise FreezeError(f"duplicate baseline id: {identifier}")
        if not isinstance(family, str) or not family:
            raise FreezeError(f"baseline {identifier} has no family")
        if not isinstance(required, bool):
            raise FreezeError(
                f"baseline {identifier} required must be a boolean"
            )
        if not isinstance(command, str):
            raise FreezeError(
                f"baseline {identifier} has no command_template"
            )
        if availability not in {"auto", "unavailable"}:
            raise FreezeError(
                f"baseline {identifier} availability must be auto or unavailable"
            )
        if availability == "unavailable":
            if command:
                raise FreezeError(
                    f"baseline {identifier} is explicitly unavailable but "
                    "has a command_template"
                )
            if not isinstance(reason, str) or not reason.strip():
                raise FreezeError(
                    f"baseline {identifier} explicit unavailability "
                    "requires a reason"
                )
        else:
            if not command:
                raise FreezeError(
                    f"baseline {identifier} has no command_template"
                )
            if reason:
                raise FreezeError(
                    f"baseline {identifier} is auto but has an "
                    "unavailable_reason"
                )
        catalog[identifier] = dict(row)
    return catalog


def _validate_publishable_configs(
    root: pathlib.Path,
    config_records: Sequence[tuple[pathlib.Path, FrozenFile]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, object]],
    dict[str, tuple[str, ...]],
]:
    by_relative = {record.path: path for path, record in config_records}
    for path, record in config_records:
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise FreezeError(
                f"invalid publishable configuration {record.path}: {exc}"
            ) from exc
        if document.get("status") != "frozen":
            raise FreezeError(
                "publishable configuration must set top-level "
                f"status = \"frozen\": {record.path}"
            )

    profiles: dict[str, dict[str, Any]] = {}
    for profile in PROFILE_NAMES:
        relative = f"mathsvg/configs/{profile}/profile.toml"
        document = load_profile(by_relative[relative])
        if document.get("profile") != profile:
            raise FreezeError(f"{relative}: directory/profile mismatch")
        profiles[profile] = document
    ablations = load_ablation_catalog(
        by_relative["mathsvg/configs/ablation/catalog.toml"]
    )
    baselines = _validate_baseline_catalog(
        by_relative["mathsvg/configs/baselines.toml"]
    )

    for path, record in config_records:
        if b"UNFROZEN" in path.read_bytes():
            raise FreezeError(
                f"publishable freeze contains UNFROZEN config: {record.path}"
            )
    return profiles, baselines, ablations


def _runtime_value(
    document: Mapping[str, object],
    path: tuple[str, ...],
) -> object:
    current: object = document
    for component in path:
        if not isinstance(current, Mapping) or component not in current:
            raise FreezeError(
                "runtime profile is missing " + ".".join(path)
            )
        current = current[component]
    return current


def _validate_string_catalog(value: object, *, name: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise FreezeError(f"runtime profile {name} must be a non-empty string array")
    if len(value) != len(set(value)):
        raise FreezeError(f"runtime profile {name} contains duplicates")
    return value


def validate_runtime_profile(
    profile: str,
    document: Mapping[str, object],
    config: Mapping[str, object],
) -> dict[str, object]:
    """Validate one no-ablation runtime profile against its checked-in TOML."""

    if not isinstance(document, Mapping):
        raise FreezeError(f"runtime profile {profile} JSON must be an object")
    blocks = config.get("blocks")
    search = config.get("search")
    parallel = config.get("parallel")
    entropy = config.get("entropy")
    if not all(
        isinstance(table, Mapping)
        for table in (blocks, search, parallel, entropy)
    ):
        raise FreezeError(f"profile {profile} lacks validated operational tables")
    assert isinstance(blocks, Mapping)
    assert isinstance(search, Mapping)
    assert isinstance(parallel, Mapping)
    assert isinstance(entropy, Mapping)

    mirror = {
        ("schema_version",): config.get("schema_version"),
        ("profile",): profile,
        ("container_version",): config.get("container_version"),
        ("dsl_version",): config.get("dsl_version"),
        ("optimizer", "block_bytes"): blocks.get("block_bytes"),
        ("optimizer", "microblock_bytes"): blocks.get("microblock_bytes"),
        ("optimizer", "max_candidates"): search.get("candidate_budget"),
        ("optimizer", "work_budget"): search.get("work_budget"),
        ("parallel", "default_threads"): parallel.get("default_threads"),
        ("parallel", "deterministic_source_order_merge"): parallel.get(
            "deterministic_merge"
        ),
        ("entropy_search", "baseline_policy"): entropy.get(
            "baseline_policy"
        ),
        ("entropy_search", "add_only_policy"): entropy.get(
            "add_only_policy"
        ),
        ("entropy_search", "chain_depth"): entropy.get("chain_depth"),
        ("entropy_search", "one_byte_lazy"): entropy.get(
            "one_byte_lazy"
        ),
        ("entropy_search", "parser_scratch_bytes"): entropy.get(
            "parser_scratch_bytes"
        ),
        (
            "entropy_search",
            "maximum_additional_work_per_input_byte_per_walk",
        ): entropy.get(
            "maximum_additional_work_per_input_byte_per_walk"
        ),
        ("entropy_search", "maximum_policy_walks"): entropy.get(
            "maximum_policy_walks"
        ),
        ("entropy_search", "wire_opcode"): 7,
        ("entropy_search", "decoder_semantics_changed"): False,
        ("ablation", "id"): "none",
        ("ablation", "disabled_algorithms"): [],
    }
    for path, expected in mirror.items():
        actual = _runtime_value(document, path)
        if type(actual) is not type(expected) or actual != expected:
            raise FreezeError(
                "runtime profile/TOML mismatch for "
                f"{profile}.{'.'.join(path)}: runtime={actual!r}, "
                f"TOML={expected!r}"
            )

    catalog_mirror = {
        ("implemented_catalogue", "functions"): search.get("function_catalog"),
        ("implemented_catalogue", "coordinates"): search.get(
            "coordinate_catalog"
        ),
    }
    for path, expected in catalog_mirror.items():
        actual = _runtime_value(document, path)
        if actual != expected:
            raise FreezeError(
                "runtime profile/TOML catalogue mismatch for "
                f"{profile}.{'.'.join(path)}"
            )

    gate_mirror = {
        ("emission_gates", "whole_block_entropy"): (
            search.get("whole_block_entropy") is True
        ),
        ("emission_gates", "whole_block_functions"): (
            search.get("whole_block_functions") is True
        ),
        ("emission_gates", "interval_functions"): (
            search.get("interval_functions") is True
        ),
        ("emission_gates", "coordinates"): bool(
            search.get("coordinate_depth")
        ),
        ("emission_gates", "residual"): bool(search.get("residual_depth")),
        ("emission_gates", "symbolic"): bool(search.get("symbolic_depth")),
        ("emission_gates", "graph"): bool(search.get("dag_sharing")),
    }
    for path, expected in gate_mirror.items():
        actual = _runtime_value(document, path)
        if not isinstance(actual, bool) or actual != expected:
            raise FreezeError(
                "runtime profile/TOML emission gate mismatch for "
                f"{profile}.{'.'.join(path)}"
            )

    for name in ("entropy", "functions", "coordinates", "experimental"):
        _validate_string_catalog(
            _runtime_value(document, ("implemented_catalogue", name)),
            name=f"{profile}.implemented_catalogue.{name}",
        )
    for required_object in ("format_limits", "backend_policy"):
        value = _runtime_value(document, (required_object,))
        if not isinstance(value, Mapping) or not value:
            raise FreezeError(
                f"runtime profile {profile}.{required_object} must be an object"
            )

    # Materialise ordinary dictionaries/lists and reject non-finite or
    # non-serialisable values before they enter the canonical record.
    try:
        canonical = json.loads(
            json.dumps(
                document,
                allow_nan=False,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise FreezeError(
            f"runtime profile {profile} is not canonical JSON: {exc}"
        ) from exc
    assert isinstance(canonical, dict)
    return canonical


def validate_runtime_profiles(
    documents: Mapping[str, Mapping[str, object]],
    configs: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    actual = set(documents)
    expected = set(PROFILE_NAMES)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise FreezeError(
            "publishable freeze requires one runtime profile per built-in "
            "profile: " + "; ".join(details)
        )

    records: list[dict[str, object]] = []
    common_catalog: object | None = None
    common_format_limits: object | None = None
    for profile in PROFILE_NAMES:
        canonical = validate_runtime_profile(
            profile, documents[profile], configs[profile]
        )
        catalog = canonical["implemented_catalogue"]
        limits = canonical["format_limits"]
        if common_catalog is None:
            common_catalog = catalog
            common_format_limits = limits
        elif catalog != common_catalog or limits != common_format_limits:
            raise FreezeError(
                "runtime primitive catalogue/format limits differ by profile"
            )
        records.append(
            {
                "profile": profile,
                "canonical_sha256": canonical_sha256(canonical),
                "contract": canonical,
            }
        )
    return records


def _source_policy(root: pathlib.Path) -> dict[str, object]:
    files: list[dict[str, object]] = []
    by_identifier: dict[str, FrozenFile] = {}
    for identifier, relative in sorted(SOURCE_POLICY_PATHS.items()):
        record = frozen_file(root, root / relative)
        by_identifier[identifier] = record
        files.append({"id": identifier, **asdict(record)})
    aggregate = canonical_sha256(files)
    return {
        "files": files,
        "source_requirements_sha256": aggregate,
        "dsl_primitive_catalog_sha256": by_identifier[
            "dsl_primitive_catalog"
        ].sha256,
        "runtime_catalog_and_profile_contract_sha256": by_identifier[
            "runtime_catalog_and_profile_contract"
        ].sha256,
        "tie_break_policy_sha256": by_identifier["tie_break_policy"].sha256,
        "container_versions_and_limits_sha256": by_identifier[
            "container_versions_and_limits"
        ].sha256,
    }


def _documentation_policy(root: pathlib.Path) -> dict[str, object]:
    documentation_root = root / "mathsvg/docs"
    try:
        metadata = documentation_root.lstat()
    except OSError as exc:
        raise FreezeError(
            f"documentation root is unavailable: {documentation_root}: {exc}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FreezeError(
            "documentation root must be an unsymlinked directory: "
            f"{documentation_root}"
        )

    paths = {
        root / relative for relative in REQUIRED_DOCUMENTATION_PATHS
    }
    paths.update(documentation_root.rglob("*.md"))
    records = [
        frozen_file(root, path)
        for path in sorted(paths, key=lambda candidate: candidate.as_posix())
    ]
    actual = {record.path for record in records}
    missing = sorted(REQUIRED_DOCUMENTATION_PATHS - actual)
    if missing:
        raise FreezeError(
            "publishable freeze lacks required documentation: "
            + ", ".join(missing)
        )
    rendered = [asdict(record) for record in records]
    return {
        "files": rendered,
        "documentation_sha256": canonical_sha256(rendered),
    }


def _configuration_policy(
    config_records: Sequence[tuple[pathlib.Path, FrozenFile]],
    profiles: Mapping[str, Mapping[str, object]],
    baselines: Mapping[str, Mapping[str, object]],
    ablations: Mapping[str, tuple[str, ...]],
) -> dict[str, object]:
    files = {record.path: record for _, record in config_records}
    block_and_search: list[dict[str, object]] = []
    for profile in PROFILE_NAMES:
        document = profiles[profile]
        blocks = document["blocks"]
        search = document["search"]
        parallel = document["parallel"]
        assert isinstance(blocks, Mapping)
        assert isinstance(search, Mapping)
        assert isinstance(parallel, Mapping)
        block_and_search.append(
            {
                "profile": profile,
                "dsl_version": document["dsl_version"],
                "container_version": document["container_version"],
                "blocks": dict(blocks),
                "search": dict(search),
                "parallel": dict(parallel),
            }
        )

    profile_documents = {
        profile: profiles[profile] for profile in PROFILE_NAMES
    }
    baseline_path = "mathsvg/configs/baselines.toml"
    ablation_path = "mathsvg/configs/ablation/catalog.toml"
    return {
        "profile_configs_sha256": canonical_sha256(profile_documents),
        "baseline_catalog_sha256": files[baseline_path].sha256,
        "baseline_catalog_semantics_sha256": canonical_sha256(baselines),
        "ablation_catalog_sha256": files[ablation_path].sha256,
        "ablation_catalog_semantics_sha256": canonical_sha256(ablations),
        "block_and_search_semantics": block_and_search,
        "block_and_search_semantics_sha256": canonical_sha256(
            block_and_search
        ),
    }


def _executable_identity(
    root: pathlib.Path,
    path: pathlib.Path,
    *,
    version: str,
    label: str,
    require_inside_repository: bool,
) -> dict[str, object]:
    if not isinstance(version, str) or not version.strip():
        raise FreezeError(f"{label} version identity is required")
    resolved = _resolved_regular_file(path, label=label)
    if resolved.stat().st_size <= 0:
        raise FreezeError(f"{label} is empty: {path}")
    if not os.access(resolved, os.X_OK):
        raise FreezeError(f"{label} is not executable: {path}")
    if require_inside_repository:
        record = frozen_file(root, path)
        rendered_path = record.path
        digest = record.sha256
        size = record.bytes
    else:
        rendered_path = resolved.as_posix()
        digest = sha256_file(resolved)
        size = resolved.stat().st_size
    return {
        "path": rendered_path,
        "sha256": digest,
        "bytes": size,
        "version": version.strip(),
    }


def collect_machine_identity(machine_id: str) -> dict[str, str]:
    if not machine_id or any(character.isspace() for character in machine_id):
        raise FreezeError(
            "machine_id must be non-empty and contain no whitespace"
        )
    return {
        "id": machine_id,
        "hostname": platform.node() or "unknown",
        "os": platform.system() or "unknown",
        "os_release": platform.release() or "unknown",
        "architecture": platform.machine() or "unknown",
    }


def _validate_machine_identity(machine: Mapping[str, object]) -> dict[str, str]:
    required = ("id", "hostname", "os", "os_release", "architecture")
    canonical: dict[str, str] = {}
    for field in required:
        value = machine.get(field)
        if not isinstance(value, str) or not value.strip():
            raise FreezeError(f"machine identity requires non-empty {field}")
        canonical[field] = value.strip()
    return canonical


def _validate_baseline_inventory(
    root: pathlib.Path,
    *,
    catalog_path: pathlib.Path,
    catalog: Mapping[str, Mapping[str, object]],
    machine: Mapping[str, str],
) -> dict[str, object]:
    path = root / BASELINE_INVENTORY_PATH
    record = frozen_file(root, path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = json.load(
                handle,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise FreezeError(f"invalid baseline inventory JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise FreezeError("unsupported baseline inventory schema")

    expected_catalog_sha256 = sha256_file(catalog_path)
    if document.get("catalog_sha256") != expected_catalog_sha256:
        raise FreezeError(
            "baseline inventory was not generated from the frozen catalogue"
        )
    stored_inventory_sha256 = document.get("inventory_sha256")
    if (
        not isinstance(stored_inventory_sha256, str)
        or not _HEX_DIGEST.fullmatch(stored_inventory_sha256)
    ):
        raise FreezeError("baseline inventory lacks a canonical identity")
    inventory_body = dict(document)
    inventory_body.pop("inventory_sha256")
    if canonical_sha256(inventory_body) != stored_inventory_sha256:
        raise FreezeError("baseline inventory canonical identity mismatch")

    inventory_machine = document.get("machine")
    if not isinstance(inventory_machine, dict):
        raise FreezeError("baseline inventory lacks machine identity")
    machine_mirror = {
        "system": machine["os"],
        "release": machine["os_release"],
        "machine": machine["architecture"],
    }
    for field, expected in machine_mirror.items():
        if inventory_machine.get(field) != expected:
            raise FreezeError(
                "baseline inventory/machine mismatch for "
                f"{field}: inventory={inventory_machine.get(field)!r}, "
                f"freeze={expected!r}"
            )
    python_version = inventory_machine.get("python")
    if not isinstance(python_version, str) or not python_version:
        raise FreezeError("baseline inventory lacks Python version identity")

    rows = document.get("baselines")
    if not isinstance(rows, list):
        raise FreezeError("baseline inventory has no baselines array")
    seen: set[str] = set()
    available = 0
    unavailable = 0
    for row in rows:
        if not isinstance(row, dict):
            raise FreezeError("baseline inventory row must be an object")
        identifier = row.get("id")
        if not isinstance(identifier, str) or identifier not in catalog:
            raise FreezeError(
                f"baseline inventory has unknown id: {identifier!r}"
            )
        if identifier in seen:
            raise FreezeError(
                f"duplicate baseline inventory id: {identifier}"
            )
        seen.add(identifier)
        if row.get("family") != catalog[identifier].get("family"):
            raise FreezeError(
                f"baseline inventory family mismatch: {identifier}"
            )

        status_value = row.get("status")
        reason = row.get("unavailable_reason")
        resolved_value = row.get("resolved_executable")
        digest_value = row.get("executable_sha256")
        version_value = row.get("version")
        requested_value = row.get("requested_executable")
        if status_value == "available":
            if (
                not isinstance(requested_value, str)
                or not requested_value
                or not isinstance(resolved_value, str)
                or not resolved_value
                or not pathlib.PurePath(resolved_value).is_absolute()
                or not isinstance(digest_value, str)
                or not _HEX_DIGEST.fullmatch(digest_value)
                or not isinstance(version_value, str)
                or not version_value
                or reason not in {"", None}
            ):
                raise FreezeError(
                    f"available baseline inventory row is incomplete: "
                    f"{identifier}"
                )
            executable = _resolved_regular_file(
                pathlib.Path(resolved_value),
                label=f"baseline {identifier} executable",
            )
            if not os.access(executable, os.X_OK):
                raise FreezeError(
                    f"baseline {identifier} executable is not executable"
                )
            if sha256_file(executable) != digest_value:
                raise FreezeError(
                    f"baseline {identifier} executable SHA-256 mismatch"
                )
            available += 1
        elif status_value == "unavailable":
            if (
                not isinstance(reason, str)
                or not reason
                or resolved_value not in {"", None}
                or digest_value not in {"", None}
                or version_value not in {"", None}
            ):
                raise FreezeError(
                    f"unavailable baseline inventory row is incomplete: "
                    f"{identifier}"
                )
            unavailable += 1
        else:
            raise FreezeError(
                f"baseline inventory has invalid status for {identifier}"
            )

    expected_ids = set(catalog)
    if seen != expected_ids:
        missing = sorted(expected_ids - seen)
        extra = sorted(seen - expected_ids)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise FreezeError(
            "baseline inventory/catalogue id mismatch: " + "; ".join(details)
        )
    return {
        "file": asdict(record),
        "canonical_sha256": stored_inventory_sha256,
        "catalog_sha256": expected_catalog_sha256,
        "machine_sha256": canonical_sha256(inventory_machine),
        "baseline_ids_sha256": canonical_sha256(sorted(seen)),
        "available": available,
        "unavailable": unavailable,
    }


def _validate_publishable_manifests(
    manifest_records: Sequence[dict[str, object]],
    entries_by_path: Mapping[str, Sequence[object]],
) -> None:
    dataset_splits: dict[str, str] = {}
    group_splits: dict[str, str] = {}
    for record in manifest_records:
        relative = str(record["path"])
        expected_split = REQUIRED_MANIFESTS[relative]
        entries = entries_by_path[relative]
        if not entries:
            raise FreezeError(f"publishable manifest is empty: {relative}")
        wrong = [
            entry
            for entry in entries
            if getattr(entry, "split", None) != expected_split
        ]
        if wrong:
            raise FreezeError(
                f"{relative} must contain only {expected_split} rows"
            )
        for entry in entries:
            dataset_id = getattr(entry, "dataset_id", None)
            split_group = getattr(entry, "split_group", None)
            if not isinstance(dataset_id, str) or not dataset_id:
                raise FreezeError(
                    f"{relative} contains a row without dataset_id"
                )
            if not isinstance(split_group, str) or not split_group:
                raise FreezeError(
                    f"{relative} contains a row without split_group"
                )
            previous_dataset = dataset_splits.setdefault(
                dataset_id, expected_split
            )
            if previous_dataset != expected_split:
                raise FreezeError(
                    "publishable manifests leak dataset_id "
                    f"{dataset_id!r} across {previous_dataset} and "
                    f"{expected_split}"
                )
            previous_group = group_splits.setdefault(
                split_group, expected_split
            )
            if previous_group != expected_split:
                raise FreezeError(
                    "publishable manifests leak split_group "
                    f"{split_group!r} across {previous_group} and "
                    f"{expected_split}"
                )


def build_record(
    *,
    root: pathlib.Path,
    experiment_id: str,
    manifest_paths: Sequence[pathlib.Path],
    config_paths: Sequence[pathlib.Path],
    state: str,
    release_binary_path: pathlib.Path | None = None,
    release_binary_version: str | None = None,
    compiler_path: pathlib.Path | None = None,
    compiler_version: str | None = None,
    runtime_profiles: Mapping[str, Mapping[str, object]] | None = None,
    machine: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if not experiment_id or any(character.isspace() for character in experiment_id):
        raise FreezeError("experiment_id must be non-empty and contain no whitespace")
    if state not in {"development", "publishable"}:
        raise FreezeError("state must be development or publishable")
    if not manifest_paths:
        raise FreezeError("at least one dataset manifest is required")
    if not config_paths:
        raise FreezeError("at least one configuration file is required")

    root = root.resolve()
    source = git_identity(root)
    if state == "publishable":
        _validate_source_identity(source)

    manifest_files = _freeze_files(
        root, manifest_paths, kind="manifest"
    )
    config_files = _freeze_files(root, config_paths, kind="configuration")
    if state == "publishable":
        _require_exact_paths(
            [record for _, record in manifest_files],
            set(REQUIRED_MANIFESTS),
            kind="manifest",
        )
        _require_exact_paths(
            [record for _, record in config_files],
            REQUIRED_CONFIGS,
            kind="configuration",
        )

    manifest_records: list[dict[str, object]] = []
    entries_by_path: dict[str, Sequence[object]] = {}
    holdout_rows = 0
    for path, frozen in manifest_files:
        entries = load_manifest(path)
        entries_by_path[frozen.path] = entries
        row_holdouts = sum(
            getattr(entry, "split", None) == "holdout" for entry in entries
        )
        holdout_rows += row_holdouts
        manifest_records.append(
            {
                **asdict(frozen),
                "canonical_metadata_sha256": canonical_manifest_sha256(entries),
                "rows": len(entries),
                "holdout_rows": row_holdouts,
                "payload_inspected_by_freeze": False,
            }
        )

    config_records = [asdict(record) for _, record in config_files]
    publishable: dict[str, object] | None = None
    if state == "publishable":
        _validate_publishable_manifests(manifest_records, entries_by_path)
        if holdout_rows == 0:
            raise FreezeError("publishable freeze requires sealed holdout rows")
        profiles, baselines, ablations = _validate_publishable_configs(
            root, config_files
        )
        if release_binary_path is None or release_binary_version is None:
            raise FreezeError("publishable freeze requires a release binary identity")
        if compiler_path is None or compiler_version is None:
            raise FreezeError("publishable freeze requires a compiler identity")
        if runtime_profiles is None:
            raise FreezeError(
                "publishable freeze requires all runtime profile contracts"
            )
        if machine is None:
            raise FreezeError("publishable freeze requires machine identity")

        release_binary = _executable_identity(
            root,
            release_binary_path,
            version=release_binary_version,
            label="release binary",
            require_inside_repository=True,
        )
        compiler = _executable_identity(
            root,
            compiler_path,
            version=compiler_version,
            label="compiler",
            # A rustup/rustc installation normally lives outside the source
            # repository.  Its absolute path, bytes and version are all pinned.
            require_inside_repository=False,
        )
        runtime_records = validate_runtime_profiles(runtime_profiles, profiles)
        source_policy = _source_policy(root)
        documentation_policy = _documentation_policy(root)
        configuration_policy = _configuration_policy(
            config_files, profiles, baselines, ablations
        )
        cargo_lock = asdict(frozen_file(root, root / CARGO_LOCK_PATH))
        validated_machine = _validate_machine_identity(machine)
        baseline_inventory = _validate_baseline_inventory(
            root,
            catalog_path=root / "mathsvg/configs/baselines.toml",
            catalog=baselines,
            machine=validated_machine,
        )
        primitive_catalog = runtime_records[0]["contract"]
        assert isinstance(primitive_catalog, dict)
        primitive_catalog = primitive_catalog["implemented_catalogue"]
        publishable = {
            "release_binary": release_binary,
            "compiler": compiler,
            "machine": validated_machine,
            "machine_sha256": canonical_sha256(validated_machine),
            "cargo_lock": cargo_lock,
            "source_identity_sha256": canonical_sha256(source),
            "source_policy": source_policy,
            "documentation_policy": documentation_policy,
            "configuration_policy": configuration_policy,
            "baseline_inventory": baseline_inventory,
            "runtime_profiles": runtime_records,
            "runtime_profiles_sha256": canonical_sha256(runtime_records),
            "runtime_primitive_catalog_sha256": canonical_sha256(
                primitive_catalog
            ),
        }

    body: dict[str, object] = {
        "schema_version": 2,
        "experiment_id": experiment_id,
        "state": state,
        "source": source,
        "dsl_version": 1,
        "container_version": 1,
        "manifests": manifest_records,
        "manifest_set_sha256": canonical_sha256(manifest_records),
        "configs": config_records,
        "configuration_set_sha256": canonical_sha256(config_records),
        "holdout_rows": holdout_rows,
        "holdout_payload_inspected": False,
    }
    if publishable is not None:
        body["publishable_identity"] = publishable
    body["freeze_sha256"] = canonical_sha256(body)
    return body


def _unique_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise FreezeError(
                f"runtime profile JSON contains duplicate key {name!r}"
            )
        result[name] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise FreezeError(f"runtime profile JSON contains non-finite {value}")


def _run_identity_command(
    executable: pathlib.Path,
    arguments: Sequence[str],
    *,
    label: str,
) -> bytes:
    try:
        process = subprocess.run(
            [str(executable), *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FreezeError(f"could not query {label}: {exc}") from exc
    if process.returncode != 0:
        detail = (
            process.stderr.decode("utf-8", errors="replace").strip()
            or process.stdout.decode("utf-8", errors="replace").strip()
        )
        raise FreezeError(
            f"{label} query exited {process.returncode}: {detail[:1000]}"
        )
    if len(process.stdout) > _MAX_CONTROL_OUTPUT_BYTES:
        raise FreezeError(f"{label} output exceeds safety limit")
    return process.stdout


def query_version(
    executable: pathlib.Path,
    arguments: Sequence[str],
    *,
    label: str,
) -> str:
    raw = _run_identity_command(executable, arguments, label=label)
    try:
        version = raw.decode("utf-8").strip()
    except UnicodeError as exc:
        raise FreezeError(f"{label} output is not UTF-8") from exc
    if not version:
        raise FreezeError(f"{label} returned an empty version")
    return version


def query_runtime_profiles(
    binary: pathlib.Path,
) -> dict[str, Mapping[str, object]]:
    documents: dict[str, Mapping[str, object]] = {}
    for profile in PROFILE_NAMES:
        raw = _run_identity_command(
            binary,
            ("profile", "--profile", profile),
            label=f"runtime profile {profile}",
        )
        try:
            document = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise FreezeError(
                f"runtime profile {profile} is invalid JSON: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise FreezeError(f"runtime profile {profile} JSON must be an object")
        documents[profile] = document
    return documents


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--manifest", action="append", type=pathlib.Path, required=True)
    parser.add_argument("--config", action="append", type=pathlib.Path, required=True)
    parser.add_argument(
        "--state", choices=("development", "publishable"), default="development"
    )
    parser.add_argument("--release-binary", type=pathlib.Path)
    parser.add_argument("--compiler", type=pathlib.Path)
    parser.add_argument("--machine-id")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        binary_version: str | None = None
        compiler_version: str | None = None
        runtime_profiles: Mapping[str, Mapping[str, object]] | None = None
        machine: Mapping[str, object] | None = None
        if args.release_binary is not None:
            binary_version = query_version(
                args.release_binary, ("--version",), label="release binary"
            )
            runtime_profiles = query_runtime_profiles(args.release_binary)
        if args.compiler is not None:
            compiler_version = query_version(
                args.compiler, ("-Vv",), label="compiler"
            )
        if args.machine_id is not None:
            machine = collect_machine_identity(args.machine_id)
        record = build_record(
            root=args.root,
            experiment_id=args.experiment_id,
            manifest_paths=args.manifest,
            config_paths=args.config,
            state=args.state,
            release_binary_path=args.release_binary,
            release_binary_version=binary_version,
            compiler_path=args.compiler,
            compiler_version=compiler_version,
            runtime_profiles=runtime_profiles,
            machine=machine,
        )
        encoded = json.dumps(record, indent=2, sort_keys=True) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    except (ConfigError, FreezeError, ManifestError, OSError) as exc:
        print(f"freeze error: {exc}", file=sys.stderr)
        return 2
    print(record["freeze_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
