from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from mathsvg.python.benchmarks.freeze import (
    PROFILE_NAMES,
    FreezeError,
    _validate_baseline_catalog,
    build_record,
    canonical_sha256,
    git_identity,
    query_runtime_profiles,
    sha256_file,
    validate_runtime_profiles,
)


def _profile_toml(profile: str, *, status: str = "frozen") -> str:
    enhanced = profile != "fast"
    add_only_policy = "C8L" if enhanced else "none"
    chain_depth = 8 if enhanced else 1
    one_byte_lazy = "true" if enhanced else "false"
    parser_scratch_bytes = 524288 if enhanced else 262144
    maximum_additional_work = 25 if enhanced else 0
    maximum_policy_walks = 2 if enhanced else 0
    interval_functions = "false" if profile == "balanced" else "true"
    coordinate_depth = 0 if profile == "balanced" else 1
    return f"""\
schema_version = 1
profile = "{profile}"
status = "{status}"
dsl_version = 1
container_version = 1

[blocks]
superblock_bytes = 1048576
block_bytes = 1048576
microblock_bytes = 4096

[limits]
encoder_rss_bytes = 1
decoder_rss_bytes = 1
max_nodes_per_block = 1
max_graph_depth = 1

[search]
coordinate_catalog = ["identity", "stride", "byte_plane", "bit_plane"]
function_catalog = ["literal", "entropy_literal", "const", "linear", "periodic", "recurrence", "exceptions"]
whole_block_entropy = true
whole_block_functions = true
interval_functions = {interval_functions}
coordinate_depth = {coordinate_depth}
residual_depth = 0
symbolic_depth = 0
dag_sharing = false
candidate_budget = 17
work_budget = 23

[parallel]
default_threads = 1
deterministic_merge = true

[entropy]
baseline_policy = "G1"
add_only_policy = "{add_only_policy}"
chain_depth = {chain_depth}
one_byte_lazy = {one_byte_lazy}
parser_scratch_bytes = {parser_scratch_bytes}
maximum_additional_work_per_input_byte_per_walk = {maximum_additional_work}
maximum_policy_walks = {maximum_policy_walks}

[stop]
minimum_oracle_gain_fraction = 0.005
maximum_search_multiplier_below_threshold = 2.0
"""


def _profile_config(profile: str) -> dict[str, object]:
    enhanced = profile != "fast"
    return {
        "schema_version": 1,
        "profile": profile,
        "status": "frozen",
        "dsl_version": 1,
        "container_version": 1,
        "blocks": {
            "superblock_bytes": 1_048_576,
            "block_bytes": 1_048_576,
            "microblock_bytes": 4096,
        },
        "limits": {},
        "search": {
            "coordinate_catalog": [
                "identity",
                "stride",
                "byte_plane",
                "bit_plane",
            ],
            "function_catalog": [
                "literal",
                "entropy_literal",
                "const",
                "linear",
                "periodic",
                "recurrence",
                "exceptions",
            ],
            "whole_block_entropy": True,
            "whole_block_functions": True,
            "interval_functions": profile != "balanced",
            "coordinate_depth": 0 if profile == "balanced" else 1,
            "residual_depth": 0,
            "symbolic_depth": 0,
            "dag_sharing": False,
            "candidate_budget": 17,
            "work_budget": 23,
        },
        "parallel": {
            "default_threads": 1,
            "deterministic_merge": True,
        },
        "entropy": {
            "baseline_policy": "G1",
            "add_only_policy": "C8L" if enhanced else "none",
            "chain_depth": 8 if enhanced else 1,
            "one_byte_lazy": enhanced,
            "parser_scratch_bytes": 524_288 if enhanced else 262_144,
            "maximum_additional_work_per_input_byte_per_walk": (
                25 if enhanced else 0
            ),
            "maximum_policy_walks": 2 if enhanced else 0,
        },
    }


def _runtime_profile(profile: str) -> dict[str, object]:
    config = _profile_config(profile)
    search = config["search"]
    entropy = config["entropy"]
    assert isinstance(search, dict)
    assert isinstance(entropy, dict)
    return {
        "schema_version": 1,
        "profile": profile,
        "ablation": {"id": "none", "disabled_algorithms": []},
        "container_version": 1,
        "dsl_version": 1,
        "optimizer": {
            "block_bytes": 1_048_576,
            "microblock_bytes": 4096,
            "max_candidates": 17,
            "work_budget": 23,
        },
        "parallel": {
            "default_threads": 1,
            "deterministic_source_order_merge": True,
        },
        "entropy_search": {
            **entropy,
            "wire_opcode": 7,
            "decoder_semantics_changed": False,
        },
        "emission_gates": {
            "whole_block_entropy": True,
            "whole_block_functions": True,
            "interval_functions": profile != "balanced",
            "coordinates": profile != "balanced",
            "residual": False,
            "symbolic": False,
            "graph": False,
        },
        "implemented_catalogue": {
            "entropy": ["raw", "canonical_huffman"],
            "functions": search["function_catalog"],
            "coordinates": search["coordinate_catalog"],
            "experimental": ["recursive_residual"],
        },
        "format_limits": {"max_nodes": 1},
        "backend_policy": {"compression_search": "scalar"},
    }


class TemporaryFreezeRepository:
    def __init__(self, directory: str) -> None:
        self.root = pathlib.Path(directory)
        self.machine = {
            "id": "test-x86",
            "hostname": "test-host",
            "os": "TestOS",
            "os_release": "1",
            "architecture": "x86_64",
        }
        self.manifests: list[pathlib.Path] = []
        for split in ("development", "holdout", "validation"):
            path = (
                self.root
                / "mathsvg"
                / "results"
                / "manifests"
                / f"{split}.csv"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"metadata-only {split}\n", encoding="utf-8")
            self.manifests.append(path)

        self.configs: list[pathlib.Path] = []
        for profile in PROFILE_NAMES:
            path = (
                self.root
                / "mathsvg"
                / "configs"
                / profile
                / "profile.toml"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_profile_toml(profile), encoding="utf-8")
            self.configs.append(path)
        baseline = self.root / "mathsvg/configs/baselines.toml"
        baseline.write_text(
            """\
schema_version = 1
status = "frozen"
native_payload_forbidden = true
[[baseline]]
id = "raw"
family = "general"
required = true
command_template = "cp {input} {output}"
""",
            encoding="utf-8",
        )
        self.configs.append(baseline)
        ablation = self.root / "mathsvg/configs/ablation/catalog.toml"
        ablation.parent.mkdir(parents=True, exist_ok=True)
        ablation.write_text(
            """\
schema_version = 1
status = "frozen"
[[ablation]]
id = "no-coordinate"
disable = ["coordinates"]
""",
            encoding="utf-8",
        )
        self.configs.append(ablation)

        for relative in (
            "mathsvg/crates/mathsvg-container/src/lib.rs",
            "mathsvg/crates/mathsvg-dsl/src/opcode.rs",
            "mathsvg/crates/mathsvg-cli/src/main.rs",
            "mathsvg/crates/mathsvg-core/src/cost.rs",
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"frozen source policy: {relative}\n", encoding="utf-8")
        (self.root / "Cargo.lock").write_text(
            "# frozen dependencies\n", encoding="utf-8"
        )
        for relative in (
            "YeuCau.md",
            "mathsvg/README.md",
            "mathsvg/docs/benchmark-methodology.md",
            "mathsvg/docs/determinism.md",
            "mathsvg/docs/format-spec.md",
            "mathsvg/docs/impossibility-boundary.md",
            "mathsvg/docs/mathematical-spec.md",
            "mathsvg/docs/procedural-dsl.md",
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"frozen documentation: {relative}\n", encoding="utf-8"
            )

        baseline_executable = self.root / "toolchain/fake-cp"
        baseline_executable.parent.mkdir(parents=True, exist_ok=True)
        baseline_executable.write_bytes(b"fake baseline executable")
        baseline_executable.chmod(0o755)
        inventory_body: dict[str, object] = {
            "schema_version": 1,
            "catalog_sha256": sha256_file(baseline),
            "machine": {
                "system": self.machine["os"],
                "release": self.machine["os_release"],
                "machine": self.machine["architecture"],
                "python": "3.test",
            },
            "baselines": [
                {
                    "id": "raw",
                    "family": "general",
                    "status": "available",
                    "unavailable_reason": "",
                    "requested_executable": "cp",
                    "resolved_executable": str(baseline_executable.resolve()),
                    "executable_sha256": sha256_file(baseline_executable),
                    "version": "fake-cp 1",
                }
            ],
        }
        inventory_body["inventory_sha256"] = canonical_sha256(inventory_body)
        inventory = (
            self.root
            / "mathsvg/results/manifests/baseline-inventory.json"
        )
        inventory.write_text(
            json.dumps(inventory_body, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self.binary = self.root / "target/release/mathsvg"
        self.binary.parent.mkdir(parents=True, exist_ok=True)
        self.binary.write_bytes(b"fake release binary")
        self.binary.chmod(0o755)
        self.compiler = self.root / "toolchain/rustc"
        self.compiler.parent.mkdir(parents=True, exist_ok=True)
        self.compiler.write_bytes(b"fake compiler")
        self.compiler.chmod(0o755)
        self.runtime_profiles = {
            profile: _runtime_profile(profile) for profile in PROFILE_NAMES
        }


_CLEAN_SOURCE = {
    "commit": "0" * 40,
    "branch": "mathsvg-absolute",
    "dirty": False,
    "status_sha256": hashlib.sha256(b"").hexdigest(),
}


def _fake_manifest(path: pathlib.Path) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            split=path.stem,
            dataset_id=f"{path.stem}-dataset",
            split_group=f"{path.stem}-group",
            path=f"datasets/data/{path.stem}/payload.bin",
        )
    ]


class FreezeTests(unittest.TestCase):
    def test_git_identity_excludes_only_declared_generated_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            subprocess.run(
                ["git", "init", "-q"], cwd=root, check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "MathSVG test"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "mathsvg@example.invalid"],
                cwd=root,
                check=True,
            )
            source = root / "source.txt"
            source.write_text("frozen\n", encoding="utf-8")
            subprocess.run(["git", "add", "source.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "fixture"],
                cwd=root,
                check=True,
            )

            clean = git_identity(root)
            artifact = root / "results" / "raw.jsonl"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("generated\n", encoding="utf-8")
            self.assertNotEqual(clean, git_identity(root))
            self.assertEqual(
                clean,
                git_identity(root, excluded_artifacts=(artifact,)),
            )

            source.write_text("changed\n", encoding="utf-8")
            self.assertNotEqual(
                clean,
                git_identity(root, excluded_artifacts=(artifact,)),
            )
            with self.assertRaisesRegex(
                FreezeError, "outside the repository"
            ):
                git_identity(
                    root,
                    excluded_artifacts=(root.parent / "outside.json",),
                )

    def test_file_hash_and_canonical_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "sample"
            path.write_bytes(b"abc")
            self.assertEqual(
                sha256_file(path),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )
        self.assertEqual(canonical_sha256({"b": 2, "a": 1}), canonical_sha256({"a": 1, "b": 2}))
        with self.assertRaisesRegex(FreezeError, "canonical JSON"):
            canonical_sha256({"invalid": float("nan")})

    @mock.patch(
        "mathsvg.python.benchmarks.freeze.canonical_manifest_sha256",
        return_value="2" * 64,
    )
    @mock.patch(
        "mathsvg.python.benchmarks.freeze.load_manifest",
        side_effect=_fake_manifest,
    )
    @mock.patch(
        "mathsvg.python.benchmarks.freeze.git_identity",
        return_value={
            **_CLEAN_SOURCE,
            "dirty": True,
            "status_sha256": "1" * 64,
        },
    )
    def test_development_is_stable_and_allows_unfrozen_config(
        self, _git: mock.Mock, _load: mock.Mock, _canonical: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            manifest = root / "manifest.csv"
            config = root / "profile.toml"
            manifest.write_text("placeholder", encoding="utf-8")
            config.write_text("UNFROZEN: development choice\n", encoding="utf-8")
            arguments = dict(
                root=root,
                experiment_id="dev-1",
                manifest_paths=[manifest],
                config_paths=[config],
                state="development",
            )
            first = build_record(**arguments)
            second = build_record(**arguments)
            self.assertEqual(first, second)
            self.assertEqual(first["schema_version"], 2)
            self.assertFalse(first["holdout_payload_inspected"])
            self.assertNotIn("publishable_identity", first)
            digest = first.pop("freeze_sha256")
            self.assertEqual(digest, canonical_sha256(first))

    def _publishable(
        self,
        repository: TemporaryFreezeRepository,
        *,
        manifest_loader: object = _fake_manifest,
    ) -> dict[str, object]:
        with (
            mock.patch(
                "mathsvg.python.benchmarks.freeze.git_identity",
                return_value=_CLEAN_SOURCE,
            ),
            mock.patch(
                "mathsvg.python.benchmarks.freeze.load_manifest",
                side_effect=manifest_loader,
            ),
            mock.patch(
                "mathsvg.python.benchmarks.freeze.canonical_manifest_sha256",
                return_value="2" * 64,
            ),
        ):
            return build_record(
                root=repository.root,
                experiment_id="publish-1",
                manifest_paths=repository.manifests,
                config_paths=repository.configs,
                state="publishable",
                release_binary_path=repository.binary,
                release_binary_version="mathsvg 1.0",
                compiler_path=repository.compiler,
                compiler_version="rustc 1.85.0\nhost: test",
                runtime_profiles=repository.runtime_profiles,
                machine=repository.machine,
            )

    def test_publishable_record_pins_closed_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = TemporaryFreezeRepository(temp)
            first = self._publishable(repository)
            second = self._publishable(repository)
            self.assertEqual(first, second)
            self.assertEqual(first["state"], "publishable")
            self.assertEqual(first["holdout_rows"], 1)
            self.assertFalse(first["holdout_payload_inspected"])
            manifests = first["manifests"]
            self.assertIsInstance(manifests, list)
            assert isinstance(manifests, list)
            self.assertTrue(
                all(
                    row["payload_inspected_by_freeze"] is False
                    for row in manifests
                )
            )
            identity = first["publishable_identity"]
            self.assertIsInstance(identity, dict)
            assert isinstance(identity, dict)
            self.assertEqual(len(identity["runtime_profiles"]), 5)
            self.assertEqual(
                identity["release_binary"]["path"],
                "target/release/mathsvg",
            )
            for key in (
                "machine_sha256",
                "source_identity_sha256",
                "runtime_profiles_sha256",
                "runtime_primitive_catalog_sha256",
            ):
                self.assertRegex(identity[key], r"^[0-9a-f]{64}$")
            self.assertRegex(
                first["manifest_set_sha256"], r"^[0-9a-f]{64}$"
            )
            self.assertRegex(
                first["configuration_set_sha256"], r"^[0-9a-f]{64}$"
            )
            policy = identity["source_policy"]
            for key in (
                "source_requirements_sha256",
                "dsl_primitive_catalog_sha256",
                "tie_break_policy_sha256",
                "container_versions_and_limits_sha256",
            ):
                self.assertRegex(policy[key], r"^[0-9a-f]{64}$")
            documentation = identity["documentation_policy"]
            self.assertGreaterEqual(len(documentation["files"]), 8)
            self.assertRegex(
                documentation["documentation_sha256"],
                r"^[0-9a-f]{64}$",
            )
            configuration = identity["configuration_policy"]
            self.assertEqual(
                len(configuration["block_and_search_semantics"]), 5
            )
            for key in (
                "profile_configs_sha256",
                "baseline_catalog_sha256",
                "baseline_catalog_semantics_sha256",
                "ablation_catalog_sha256",
                "ablation_catalog_semantics_sha256",
                "block_and_search_semantics_sha256",
            ):
                self.assertRegex(configuration[key], r"^[0-9a-f]{64}$")
            baseline_inventory = identity["baseline_inventory"]
            self.assertEqual(baseline_inventory["available"], 1)
            self.assertEqual(baseline_inventory["unavailable"], 0)
            self.assertRegex(
                baseline_inventory["canonical_sha256"],
                r"^[0-9a-f]{64}$",
            )
            digest = first.pop("freeze_sha256")
            self.assertEqual(digest, canonical_sha256(first))

    def test_publishable_rejects_dirty_or_wrong_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = TemporaryFreezeRepository(temp)
            for source, message in (
                ({**_CLEAN_SOURCE, "dirty": True}, "clean source tree"),
                ({**_CLEAN_SOURCE, "branch": "main"}, "mathsvg-absolute"),
            ):
                with (
                    self.subTest(message=message),
                    mock.patch(
                        "mathsvg.python.benchmarks.freeze.git_identity",
                        return_value=source,
                    ),
                ):
                    with self.assertRaisesRegex(FreezeError, message):
                        build_record(
                            root=repository.root,
                            experiment_id="publish-1",
                            manifest_paths=repository.manifests,
                            config_paths=repository.configs,
                            state="publishable",
                        )

    @mock.patch(
        "mathsvg.python.benchmarks.freeze.git_identity",
        return_value=_CLEAN_SOURCE,
    )
    def test_publishable_rejects_missing_duplicate_and_outside_inputs(
        self, _git: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            repository = TemporaryFreezeRepository(temp)
            with self.assertRaisesRegex(FreezeError, "exact manifest set"):
                build_record(
                    root=repository.root,
                    experiment_id="publish-1",
                    manifest_paths=repository.manifests[:-1],
                    config_paths=repository.configs,
                    state="publishable",
                )
            with self.assertRaisesRegex(FreezeError, "duplicate manifest"):
                build_record(
                    root=repository.root,
                    experiment_id="publish-1",
                    manifest_paths=[
                        *repository.manifests,
                        repository.manifests[0],
                    ],
                    config_paths=repository.configs,
                    state="publishable",
                )
            outside = pathlib.Path(other) / "outside.toml"
            outside.write_text("x\n", encoding="utf-8")
            with self.assertRaisesRegex(FreezeError, "outside repository"):
                build_record(
                    root=repository.root,
                    experiment_id="dev-1",
                    manifest_paths=[repository.manifests[0]],
                    config_paths=[outside],
                    state="development",
                )

    def test_publishable_rejects_unfrozen_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = TemporaryFreezeRepository(temp)
            baseline = repository.root / "mathsvg/configs/baselines.toml"
            baseline.write_text(
                baseline.read_text(encoding="utf-8")
                + '\ncommand_note = "UNFROZEN: choose implementation"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FreezeError, "UNFROZEN config"):
                self._publishable(repository)

    def test_publishable_requires_frozen_status_for_all_seven_configs(
        self,
    ) -> None:
        relative_paths = [
            *(f"mathsvg/configs/{profile}/profile.toml" for profile in PROFILE_NAMES),
            "mathsvg/configs/baselines.toml",
            "mathsvg/configs/ablation/catalog.toml",
        ]
        for relative in relative_paths:
            with self.subTest(config=relative), tempfile.TemporaryDirectory() as temp:
                repository = TemporaryFreezeRepository(temp)
                path = repository.root / relative
                contents = path.read_text(encoding="utf-8")
                self.assertIn('status = "frozen"', contents)
                path.write_text(
                    contents.replace(
                        'status = "frozen"',
                        'status = "development"',
                        1,
                    ),
                    encoding="utf-8",
                )
                if relative == "mathsvg/configs/baselines.toml":
                    inventory_path = (
                        repository.root
                        / "mathsvg/results/manifests/baseline-inventory.json"
                    )
                    inventory = json.loads(
                        inventory_path.read_text(encoding="utf-8")
                    )
                    inventory["catalog_sha256"] = sha256_file(path)
                    inventory.pop("inventory_sha256")
                    inventory["inventory_sha256"] = canonical_sha256(
                        inventory
                    )
                    inventory_path.write_text(
                        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )

                with self.assertRaisesRegex(
                    FreezeError,
                    'top-level status = "frozen"',
                ):
                    self._publishable(repository)

    def test_explicit_unavailable_baseline_is_a_closed_catalog_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "baselines.toml"
            path.write_text(
                """\
schema_version = 1
status = "development"
native_payload_forbidden = true
[[baseline]]
id = "domain-codec"
family = "domain"
required = true
availability = "unavailable"
unavailable_reason = "no canonical executable is installed"
command_template = ""
""",
                encoding="utf-8",
            )
            catalog = _validate_baseline_catalog(path)
            self.assertEqual(
                catalog["domain-codec"]["availability"], "unavailable"
            )

    def test_publishable_rejects_stale_baseline_executable_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = TemporaryFreezeRepository(temp)
            executable = repository.root / "toolchain/fake-cp"
            executable.write_bytes(b"changed after inventory")
            executable.chmod(0o755)
            with self.assertRaisesRegex(
                FreezeError, "executable SHA-256 mismatch"
            ):
                self._publishable(repository)

    def test_publishable_rejects_cross_manifest_split_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = TemporaryFreezeRepository(temp)

            def leaking_manifest(path: pathlib.Path) -> list[SimpleNamespace]:
                return [
                    SimpleNamespace(
                        split=path.stem,
                        dataset_id=f"{path.stem}-dataset",
                        split_group="shared-across-splits",
                        path=f"datasets/data/{path.stem}/payload.bin",
                    )
                ]

            with self.assertRaisesRegex(FreezeError, "leak split_group"):
                self._publishable(
                    repository, manifest_loader=leaking_manifest
                )

    def test_runtime_contract_rejects_missing_profile_and_mismatch(self) -> None:
        configs = {profile: _profile_config(profile) for profile in PROFILE_NAMES}
        documents = {
            profile: _runtime_profile(profile) for profile in PROFILE_NAMES
        }
        missing = dict(documents)
        missing.pop("max")
        with self.assertRaisesRegex(FreezeError, "missing max"):
            validate_runtime_profiles(missing, configs)

        mismatched = {
            profile: json.loads(json.dumps(document))
            for profile, document in documents.items()
        }
        mismatched["fast"]["optimizer"]["work_budget"] = 24
        with self.assertRaisesRegex(FreezeError, "work_budget"):
            validate_runtime_profiles(mismatched, configs)

        wrong_name = {
            profile: json.loads(json.dumps(document))
            for profile, document in documents.items()
        }
        wrong_name["fast"]["profile"] = "balanced"
        with self.assertRaisesRegex(FreezeError, "fast.profile"):
            validate_runtime_profiles(wrong_name, configs)

    def test_runtime_contract_rejects_catalog_drift_between_profiles(self) -> None:
        configs = {profile: _profile_config(profile) for profile in PROFILE_NAMES}
        documents = {
            profile: _runtime_profile(profile) for profile in PROFILE_NAMES
        }
        documents["structured"]["implemented_catalogue"]["entropy"].append(
            "profile-only-codec"
        )
        with self.assertRaisesRegex(FreezeError, "differ by profile"):
            validate_runtime_profiles(documents, configs)

    def test_query_runtime_profiles_rejects_duplicate_json_keys(self) -> None:
        duplicate = b'{"schema_version":1,"schema_version":1}'
        with mock.patch(
            "mathsvg.python.benchmarks.freeze._run_identity_command",
            return_value=duplicate,
        ):
            with self.assertRaisesRegex(FreezeError, "duplicate key"):
                query_runtime_profiles(pathlib.Path("fake-mathsvg"))

    def test_publishable_never_hashes_or_resolves_holdout_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = TemporaryFreezeRepository(temp)
            sealed = (
                repository.root
                / "datasets"
                / "data"
                / "holdout"
                / "sealed"
                / "must-not-open.bin"
            )
            sealed.parent.mkdir(parents=True, exist_ok=True)
            sealed.write_bytes(b"secret holdout payload")
            sealed_absolute = sealed.absolute()
            real_sha256_file = sha256_file
            real_open = pathlib.Path.open
            real_lstat = pathlib.Path.lstat
            real_resolve = pathlib.Path.resolve
            real_stat = pathlib.Path.stat
            hashed: list[pathlib.Path] = []

            def tracking_hash(path: pathlib.Path) -> str:
                absolute = path.absolute()
                if absolute == sealed_absolute:
                    raise AssertionError("freeze hashed holdout payload")
                hashed.append(absolute)
                return real_sha256_file(path)

            def is_holdout(path: pathlib.Path) -> bool:
                return pathlib.Path(
                    os.path.abspath(os.fspath(path))
                ) == sealed_absolute

            def guarding_open(
                path: pathlib.Path, *args: object, **kwargs: object
            ) -> object:
                if is_holdout(path):
                    raise AssertionError("freeze opened holdout payload")
                return real_open(path, *args, **kwargs)

            def guarding_lstat(
                path: pathlib.Path, *args: object, **kwargs: object
            ) -> object:
                if is_holdout(path):
                    raise AssertionError("freeze lstat'ed holdout payload")
                return real_lstat(path, *args, **kwargs)

            def guarding_stat(
                path: pathlib.Path, *args: object, **kwargs: object
            ) -> object:
                if is_holdout(path):
                    raise AssertionError("freeze stat'ed holdout payload")
                return real_stat(path, *args, **kwargs)

            def guarding_resolve(
                path: pathlib.Path, *args: object, **kwargs: object
            ) -> object:
                if is_holdout(path):
                    raise AssertionError("freeze resolved holdout payload")
                return real_resolve(path, *args, **kwargs)

            def manifest_with_sealed_path(
                path: pathlib.Path,
            ) -> list[SimpleNamespace]:
                entry = _fake_manifest(path)[0]
                if path.stem == "holdout":
                    entry.path = (
                        "datasets/data/holdout/sealed/must-not-open.bin"
                    )
                return [entry]

            with (
                mock.patch(
                    "mathsvg.python.benchmarks.freeze.sha256_file",
                    side_effect=tracking_hash,
                ),
                mock.patch.object(pathlib.Path, "open", new=guarding_open),
                mock.patch.object(pathlib.Path, "lstat", new=guarding_lstat),
                mock.patch.object(pathlib.Path, "stat", new=guarding_stat),
                mock.patch.object(
                    pathlib.Path, "resolve", new=guarding_resolve
                ),
            ):
                record = self._publishable(
                    repository, manifest_loader=manifest_with_sealed_path
                )
            self.assertFalse(record["holdout_payload_inspected"])
            self.assertNotIn(sealed_absolute, hashed)
            self.assertTrue(
                all("holdout/sealed" not in path.as_posix() for path in hashed)
            )


if __name__ == "__main__":
    unittest.main()
