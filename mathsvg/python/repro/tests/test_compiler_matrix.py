from __future__ import annotations

import argparse
import copy
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from mathsvg.python.repro.common import ReproError, sha256_file
from mathsvg.python.repro.compiler_matrix import (
    ARCHIVE_KEYS,
    COMPAT_ID,
    COMPAT_RELEASE,
    EXPERIMENT_ID,
    IGNORED_REASON,
    INPUT_BYTES,
    INPUT_RELATIVE,
    INPUT_SHA256,
    MACHINE_KEYS,
    SCHEMA,
    SOURCE_KEYS,
    STABLE_ID,
    STABLE_RELEASE,
    STATUS,
    TOOLCHAIN_KEYS,
    TOP_LEVEL_KEYS,
    WORKSPACE_TEST_KEYS,
    RustcDetails,
    Toolchain,
    generate_report,
    parse_rustc_verbose,
    parse_test_counts,
    validate_report,
)


def _sample_report() -> dict[str, object]:
    def toolchain(
        identifier: str,
        release: str,
        digest: str,
    ) -> dict[str, object]:
        return {
            "id": identifier,
            "rustc_version": f"rustc {release} (123456789 2026-01-01)",
            "rustc_commit": "1" * 40,
            "llvm_version": "22.1.6",
            "rustc_path": f"/tools/{identifier}/rustc",
            "rustc_bytes": 100,
            "rustc_sha256": digest,
            "cargo_path": f"/tools/{identifier}/cargo",
            "cargo_bytes": 100,
            "cargo_sha256": "c" * 64,
            "release_binary_bytes": 100,
            "release_binary_sha256": "d" * 64,
        }

    return {
        "schema": SCHEMA,
        "experiment_id": EXPERIMENT_ID,
        "status": STATUS,
        "evidence_scope": "development",
        "holdout_payload_inspected": False,
        "source_identity": {
            "base_git_commit": "a" * 40,
            "worktree_state": "dirty",
            "rust_sources_changed_during_matrix": False,
            "note": "same content snapshot",
        },
        "machine": {
            "machine_id": "test-x86",
            "architecture": "x86_64",
            "target": "x86_64-unknown-linux-gnu",
            "independent_second_machine": False,
            "arm64": False,
        },
        "toolchains": [
            toolchain(STABLE_ID, STABLE_RELEASE, "a" * 64),
            toolchain(COMPAT_ID, COMPAT_RELEASE, "b" * 64),
        ],
        "workspace_test": {
            "toolchain": COMPAT_ID,
            "command": (
                "CARGO_TARGET_DIR={workdir}/target-rust-1.85 "
                "RUSTC=/tools/compat/rustc RUSTDOC=/tools/compat/rustdoc "
                "/tools/compat/cargo test --locked --offline --workspace --release"
            ),
            "working_directory": ".",
            "exit_code": 0,
            "passed": 100,
            "failed": 0,
            "ignored": 2,
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
            "archive_bytes": 100,
            "stable_archive_sha256": "e" * 64,
            "rust_1_85_archive_sha256": "e" * 64,
            "archive_byte_identical": True,
            "stable_self_decode": "pass",
            "rust_1_85_self_decode": "pass",
            "stable_decodes_rust_1_85_archive": "pass",
            "rust_1_85_decodes_stable_archive": "pass",
            "all_restored_sha256": INPUT_SHA256,
        },
        "limitations": [
            "Both compilers ran on the same physical host.",
            "This is a one-input development spot check.",
            "ARM64 and an independent second machine remain unavailable.",
            "The source tree is dirty.",
        ],
    }


class CompilerMatrixParserTests(unittest.TestCase):
    def test_rustc_verbose_parser_requires_structured_identity(self) -> None:
        details = parse_rustc_verbose(
            "\n".join(
                (
                    "rustc 1.97.1 (123456789 2026-07-14)",
                    "binary: rustc",
                    "commit-hash: " + "a" * 40,
                    "host: x86_64-unknown-linux-gnu",
                    "release: 1.97.1",
                    "LLVM version: 22.1.6",
                )
            )
        )
        self.assertEqual(details.release, STABLE_RELEASE)
        self.assertEqual(details.llvm_version, "22.1.6")
        with self.assertRaisesRegex(ReproError, "missing"):
            parse_rustc_verbose("rustc 1.97.1\nrelease: 1.97.1")

    def test_test_counts_sum_all_harnesses_and_reject_no_summary(self) -> None:
        counts = parse_test_counts(
            "\n".join(
                (
                    "test result: ok. 9 passed; 0 failed; 2 ignored; 0 measured;",
                    "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured;",
                    "test result: ok. 4 passed; 0 failed; 0 ignored; 0 measured;",
                )
            )
        )
        self.assertEqual((counts.summaries, counts.passed), (3, 13))
        self.assertEqual((counts.failed, counts.ignored), (0, 2))
        self.assertTrue(counts.all_ok)
        with self.assertRaisesRegex(ReproError, "no recognizable"):
            parse_test_counts("Finished release tests")


class CompilerMatrixSchemaTests(unittest.TestCase):
    def test_v1_contract_uses_exact_nested_key_sets(self) -> None:
        report = validate_report(_sample_report())
        self.assertEqual(frozenset(report), TOP_LEVEL_KEYS)
        self.assertEqual(
            frozenset(report["source_identity"]),  # type: ignore[arg-type]
            SOURCE_KEYS,
        )
        self.assertEqual(
            frozenset(report["machine"]),  # type: ignore[arg-type]
            MACHINE_KEYS,
        )
        self.assertEqual(
            frozenset(report["toolchains"][0]),  # type: ignore[index]
            TOOLCHAIN_KEYS,
        )
        self.assertEqual(
            frozenset(report["workspace_test"]),  # type: ignore[arg-type]
            WORKSPACE_TEST_KEYS,
        )
        self.assertEqual(
            frozenset(report["archive_comparison"]),  # type: ignore[arg-type]
            ARCHIVE_KEYS,
        )

    def test_schema_rejects_extra_keys_and_semantic_escalation(self) -> None:
        extra = _sample_report()
        extra["source_manifest_sha256"] = "a" * 64
        with self.assertRaisesRegex(ReproError, "extra"):
            validate_report(extra)

        escalated = _sample_report()
        escalated["status"] = "pass"
        with self.assertRaisesRegex(ReproError, "status"):
            validate_report(escalated)

    def test_schema_rejects_same_compiler_and_cross_decode_failure(self) -> None:
        duplicate = _sample_report()
        toolchains = duplicate["toolchains"]
        assert isinstance(toolchains, list)
        toolchains[1]["rustc_sha256"] = toolchains[0]["rustc_sha256"]
        with self.assertRaisesRegex(ReproError, "distinct rustc"):
            validate_report(duplicate)

        failed = _sample_report()
        archive = failed["archive_comparison"]
        assert isinstance(archive, dict)
        archive["rust_1_85_decodes_stable_archive"] = "failed"
        with self.assertRaisesRegex(ReproError, "must be pass"):
            validate_report(failed)


class CompilerMatrixGenerationTests(unittest.TestCase):
    @staticmethod
    def _executable(path: pathlib.Path, content: bytes) -> pathlib.Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o755)
        return path.resolve()

    def _toolchain(
        self,
        root: pathlib.Path,
        *,
        selector: str,
        identifier: str,
        release: str,
        marker: bytes,
    ) -> Toolchain:
        bin_directory = root / "tools" / selector / "bin"
        cargo = self._executable(bin_directory / "cargo", marker + b"cargo")
        rustc = self._executable(bin_directory / "rustc", marker + b"rustc")
        rustdoc = self._executable(
            bin_directory / "rustdoc", marker + b"rustdoc"
        )
        return Toolchain(
            selector=selector,
            identifier=identifier,
            cargo=cargo,
            rustc=rustc,
            rustdoc=rustdoc,
            rustc_details=RustcDetails(
                version=f"rustc {release} (123456789 2026-01-01)",
                release=release,
                commit=("a" if identifier == STABLE_ID else "b") * 40,
                host="x86_64-unknown-linux-gnu",
                llvm_version="22.1.6",
            ),
            cargo_bytes=cargo.stat().st_size,
            cargo_sha256=sha256_file(cargo),
            rustc_bytes=rustc.stat().st_size,
            rustc_sha256=sha256_file(rustc),
            rustdoc_bytes=rustdoc.stat().st_size,
            rustdoc_sha256=sha256_file(rustdoc),
        )

    def _repository(self, root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        (root / "Cargo.lock").write_text("version = 4\n", encoding="utf-8")
        for relative in (
            "crates/dummy/src/lib.rs",
            "mathsvg/crates/dummy/src/lib.rs",
            "mathsvg/python/repro/compiler_matrix.py",
            "mathsvg/python/repro/common.py",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// input\n", encoding="utf-8")
        input_path = root / INPUT_RELATIVE
        input_path.parent.mkdir(parents=True, exist_ok=True)
        source_input = (
            pathlib.Path(__file__).resolve().parents[4]
            / INPUT_RELATIVE
        )
        shutil.copyfile(source_input, input_path)
        self.assertEqual(sha256_file(input_path), INPUT_SHA256)
        canonical = self._executable(
            root / "target/release/mathsvg", b"stable-binary"
        )
        return root, canonical

    @staticmethod
    def _args(root: pathlib.Path) -> argparse.Namespace:
        return argparse.Namespace(
            repository=root,
            experiment_id=EXPERIMENT_ID,
            machine_id="test-x86",
            stable_toolchain="stable-test",
            compat_toolchain="compat-test",
            rustup=root / "rustup",
            canonical_release=pathlib.Path("target/release/mathsvg"),
            input=pathlib.Path(INPUT_RELATIVE),
            profile="balanced",
            timeout_seconds=10.0,
            output=pathlib.Path("matrix.json"),
        )

    def test_mocked_generation_binds_compilers_and_runs_all_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, canonical = self._repository(pathlib.Path(temporary))
            self._executable(root / "rustup", b"rustup")
            stable = self._toolchain(
                root,
                selector="stable",
                identifier=STABLE_ID,
                release=STABLE_RELEASE,
                marker=b"stable-",
            )
            compat = self._toolchain(
                root,
                selector="compat",
                identifier=COMPAT_ID,
                release=COMPAT_RELEASE,
                marker=b"compat-",
            )
            calls: list[tuple[list[str], dict[str, str], str]] = []

            def run_checked(argv, **kwargs):
                command = list(argv)
                environment = dict(kwargs["environment"])
                name = kwargs["name"]
                calls.append((command, environment, name))
                if name == "compat-workspace-test":
                    return (
                        "test result: ok. 10 passed; 0 failed; 2 ignored;\n"
                        "test result: ok. 1 passed; 0 failed; 0 ignored;\n"
                    )
                if name.endswith("release-build"):
                    binary = (
                        pathlib.Path(environment["CARGO_TARGET_DIR"])
                        / "release/mathsvg"
                    )
                    content = (
                        canonical.read_bytes()
                        if name.startswith("stable")
                        else b"compat-binary"
                    )
                    self._executable(binary, content)
                elif name.endswith("compress"):
                    pathlib.Path(command[-1]).write_bytes(b"same-archive")
                elif name.endswith("decode"):
                    shutil.copyfile(root / INPUT_RELATIVE, command[-1])
                return ""

            identity = {
                "commit": "a" * 40,
                "branch": "test",
                "dirty": True,
                "status_sha256": "b" * 64,
            }
            with (
                mock.patch(
                    "mathsvg.python.repro.compiler_matrix._resolve_toolchain",
                    side_effect=[stable, compat],
                ),
                mock.patch(
                    "mathsvg.python.repro.compiler_matrix._run_checked",
                    side_effect=run_checked,
                ),
                mock.patch(
                    "mathsvg.python.repro.compiler_matrix.git_identity",
                    return_value=identity,
                ),
            ):
                report = generate_report(self._args(root))

            self.assertTrue((root / "matrix.json").is_file())
            self.assertEqual(report["status"], STATUS)
            cargo_calls = [call for call in calls if call[2].endswith(("test", "build"))]
            self.assertEqual(len(cargo_calls), 3)
            targets = set()
            for argv, environment, _ in cargo_calls:
                self.assertTrue(pathlib.Path(argv[0]).is_absolute())
                self.assertIn("--locked", argv)
                self.assertIn("--offline", argv)
                self.assertTrue(pathlib.Path(environment["RUSTC"]).is_absolute())
                self.assertTrue(pathlib.Path(environment["RUSTDOC"]).is_absolute())
                targets.add(environment["CARGO_TARGET_DIR"])
            self.assertEqual(len(targets), 2)
            self.assertEqual(sum(name.endswith("compress") for _, _, name in calls), 2)
            self.assertEqual(sum(name.endswith("decode") for _, _, name in calls), 4)

    def test_existing_output_is_rejected_before_toolchain_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = self._repository(pathlib.Path(temporary))
            (root / "matrix.json").write_text("owned\n", encoding="utf-8")
            with mock.patch(
                "mathsvg.python.repro.compiler_matrix._resolve_toolchain"
            ) as resolve:
                with self.assertRaisesRegex(ReproError, "already exists"):
                    generate_report(self._args(root))
            resolve.assert_not_called()
            self.assertEqual(
                (root / "matrix.json").read_text(encoding="utf-8"),
                "owned\n",
            )


if __name__ == "__main__":
    unittest.main()
