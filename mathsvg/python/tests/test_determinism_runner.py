from __future__ import annotations

import dataclasses
import hashlib
import pathlib
import stat
import sys
import tempfile
import textwrap
import unittest

from mathsvg.python.determinism.runner import (
    DEFAULT_BACKENDS,
    DEFAULT_THREAD_MODES,
    BuildSpec,
    CompilerIdentity,
    RunRequest,
    RunnerError,
    evaluate_gate,
    probe_capabilities,
    run_matrix,
)
from mathsvg.python.determinism.schema import DeterminismRecord

FAKE_CLI = r"""
#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sys
import time

name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]

if args == ["--version"]:
    print("fake-mathsvg 1.0")
    raise SystemExit(0)

if len(args) == 2 and args[1] == "--help":
    operation = args[0]
    if operation == "compress":
        print("Usage: fake compress [OPTIONS] INPUT OUTPUT")
        print("  --profile <PROFILE>")
        if "unsupported" not in name:
            print("  --threads <THREADS>  integer or all logical CPUs")
            if "decodeonly" not in name:
                print("  --backend <BACKEND>  [possible values: scalar, simd, auto]")
    elif operation == "decompress":
        print("Usage: fake decompress [OPTIONS] ARCHIVE OUTPUT")
        if "unsupported" not in name:
            if "decodeonly" not in name:
                print("  --threads <THREADS>  integer or all logical CPUs")
            print("  --backend <BACKEND>  [possible values: scalar, simd, auto]")
    else:
        raise SystemExit(2)
    raise SystemExit(0)

if not args:
    raise SystemExit(2)
operation = args.pop(0)
selected_backend = "default"
while args and args[0].startswith("--"):
    option = args.pop(0)
    if option in {"--profile", "--threads", "--backend"}:
        if not args:
            raise SystemExit(2)
        value = args.pop(0)
        if option == "--backend":
            selected_backend = value
    else:
        raise SystemExit(2)
if len(args) != 2:
    raise SystemExit(2)
source = pathlib.Path(args[0])
destination = pathlib.Path(args[1])

if "timeout" in name and operation == "compress":
    time.sleep(2)
if "failure" in name and operation == "compress":
    print("intentional failure", file=sys.stderr)
    raise SystemExit(7)
if (
    "simdunavailable" in name
    and operation == "decompress"
    and selected_backend == "simd"
):
    print("SIMD backend unavailable on this host", file=sys.stderr)
    raise SystemExit(9)

if operation == "compress":
    payload = source.read_bytes()
    nonce = ""
    if "nondeterministic" in name:
        nonce = destination.stem.rsplit("-", 1)[-1]
    metadata = json.dumps(
        {"input_sha256": hashlib.sha256(payload).hexdigest(), "nonce": nonce},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    destination.write_bytes(
        b"FAKE" + len(metadata).to_bytes(4, "little") + metadata + payload
    )
elif operation == "decompress":
    archive = source.read_bytes()
    if archive[:4] != b"FAKE":
        raise SystemExit(8)
    metadata_bytes = int.from_bytes(archive[4:8], "little")
    destination.write_bytes(archive[8 + metadata_bytes:])
else:
    raise SystemExit(2)
"""


def _compiler() -> CompilerIdentity:
    executable = pathlib.Path(sys.executable).resolve()
    return CompilerIdentity(
        executable=executable,
        binary_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        version="fake-compiler 1",
    )


def _write_fake(root: pathlib.Path, name: str) -> pathlib.Path:
    path = root / name
    path.write_text(textwrap.dedent(FAKE_CLI).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _request(
    root: pathlib.Path,
    executable: pathlib.Path,
    *,
    repetitions: int = 2,
    thread_modes: tuple[str, ...] = ("1", "all"),
    backends: tuple[str, ...] = ("scalar", "auto"),
    timeout_seconds: float = 2.0,
) -> RunRequest:
    input_path = root / "input;not-a-shell-command.bin"
    input_path.write_bytes(bytes(range(32)))
    config = root / "profile.toml"
    config.write_text("schema_version = 1\nprofile = 'balanced'\n", encoding="utf-8")
    return RunRequest(
        repository=root,
        experiment_id="smoke",
        machine_id="unit-test",
        split="development",
        input_path=input_path,
        config_path=config,
        profile="balanced",
        builds=(BuildSpec("debug", executable, _compiler()),),
        repetitions=repetitions,
        timeout_seconds=timeout_seconds,
        thread_modes=thread_modes,
        backends=backends,
        publishable=False,
        source_identity={
            "commit": "0" * 40,
            "dirty": True,
            "status_sha256": "1" * 64,
        },
    )


def _synthetic_gate_records(
    template: DeterminismRecord,
    *,
    repetitions: int,
) -> list[DeterminismRecord]:
    records: list[DeterminismRecord] = []
    environments = (
        ("machine-x86", "x86_64", "rustc 1.80.0", "a" * 64),
        ("machine-arm", "aarch64", "rustc 1.81.0", "b" * 64),
    )
    empty_status = hashlib.sha256(b"").hexdigest()
    for machine, architecture, compiler_version, compiler_hash in environments:
        for build in ("debug", "release"):
            binary_hash = hashlib.sha256(
                f"{machine}:{build}".encode("ascii")
            ).hexdigest()
            for thread_mode in DEFAULT_THREAD_MODES:
                resolved_threads = 8 if thread_mode == "all" else int(
                    thread_mode, 10
                )
                for backend in DEFAULT_BACKENDS:
                    for repetition in range(repetitions):
                        records.append(
                            dataclasses.replace(
                                template,
                                source_dirty=False,
                                source_status_sha256=empty_status,
                                machine_id=machine,
                                architecture=architecture,
                                compiler_version=compiler_version,
                                compiler_binary_sha256=compiler_hash,
                                build=build,
                                binary_path=f"/evidence/{machine}/{build}/mathsvg",
                                binary_sha256=binary_hash,
                                planned_repetitions=repetitions,
                                repetition=repetition,
                                thread_mode=thread_mode,
                                resolved_threads=resolved_threads,
                                backend=backend,
                                required_axis=True,
                            )
                        )
    return records


class DeterminismRunnerTests(unittest.TestCase):
    def test_supported_fake_cli_runs_full_small_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            executable = _write_fake(root, "fake mathsvg supported")
            capabilities = probe_capabilities(
                BuildSpec("debug", executable, _compiler())
            )
            self.assertTrue(capabilities.threads_supported)
            self.assertTrue(capabilities.all_threads_supported)
            self.assertEqual(
                capabilities.backend_values,
                frozenset({"scalar", "simd", "auto"}),
            )

            records = run_matrix(_request(root, executable))
            # default/default plus the 2x2 required matrix, each repeated twice
            self.assertEqual(len(records), 10)
            self.assertTrue(all(row.status == "ok" for row in records))
            self.assertEqual(
                len({row.archive_sha256 for row in records}),
                1,
            )
            self.assertTrue(all(row.roundtrip_ok for row in records))
            self.assertFalse((root / "not-a-shell-command.bin").exists())
            summary = evaluate_gate(records)
            self.assertEqual(summary.status, "incomplete")
            self.assertIn(
                "fewer than 100 repetitions",
                " ".join(summary.reasons),
            )

    def test_missing_cli_flags_are_explicitly_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            executable = _write_fake(root, "fake-mathsvg-unsupported")
            records = run_matrix(
                _request(
                    root,
                    executable,
                    thread_modes=("1", "all"),
                    backends=("scalar", "simd", "auto"),
                )
            )
            default_rows = [
                row
                for row in records
                if row.thread_mode == "default" and row.backend == "default"
            ]
            required_rows = [row for row in records if row.required_axis]
            self.assertEqual(len(default_rows), 2)
            self.assertTrue(all(row.status == "ok" for row in default_rows))
            self.assertEqual(len(required_rows), 6)
            self.assertTrue(
                all(row.status == "unavailable" for row in required_rows)
            )
            self.assertTrue(
                all("--threads" in row.reason for row in required_rows)
            )
            summary = evaluate_gate(records)
            self.assertEqual(summary.status, "incomplete")
            self.assertIn("required axis", " ".join(summary.reasons))

    def test_decode_only_backend_is_not_passed_to_compression(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            executable = _write_fake(root, "fake-mathsvg-decodeonly")
            records = run_matrix(
                _request(
                    root,
                    executable,
                    repetitions=1,
                    thread_modes=("1",),
                    backends=("scalar", "simd", "auto"),
                )
            )
            required = [row for row in records if row.required_axis]
            self.assertEqual(len(required), 3)
            self.assertTrue(all(row.status == "ok" for row in required))
            self.assertTrue(
                all("--backend" not in row.compress_command for row in required)
            )
            self.assertTrue(
                all("--backend" in row.decompress_command for row in required)
            )

    def test_archive_nondeterminism_is_failed_even_when_roundtrip_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            executable = _write_fake(root, "fake-mathsvg-nondeterministic")
            records = run_matrix(
                _request(
                    root,
                    executable,
                    thread_modes=("1",),
                    backends=("scalar",),
                )
            )
            mismatches = [
                row
                for row in records
                if row.status == "failed"
                and not row.archive_matches_reference
            ]
            self.assertTrue(mismatches)
            self.assertTrue(all(row.roundtrip_ok for row in mismatches))
            self.assertEqual(evaluate_gate(records).status, "failed")

    def test_runtime_backend_capability_failure_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            executable = _write_fake(
                root, "fake-mathsvg-simdunavailable"
            )
            records = run_matrix(
                _request(
                    root,
                    executable,
                    repetitions=1,
                    thread_modes=("1",),
                    backends=("scalar", "simd"),
                )
            )
            simd = [
                row
                for row in records
                if row.thread_mode == "1" and row.backend == "simd"
            ]
            self.assertEqual(len(simd), 1)
            self.assertEqual(simd[0].status, "unavailable")
            self.assertIn("SIMD backend unavailable", simd[0].reason)

    def test_timeout_and_unexecuted_repetitions_are_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            executable = _write_fake(root, "fake-mathsvg-timeout")
            records = run_matrix(
                _request(
                    root,
                    executable,
                    repetitions=3,
                    thread_modes=("1",),
                    backends=("scalar",),
                    timeout_seconds=0.05,
                )
            )
            self.assertTrue(any(row.status == "timeout" for row in records))
            self.assertTrue(
                any(
                    row.status == "unavailable"
                    and "not executed after earlier" in row.reason
                    for row in records
                )
            )
            self.assertEqual(evaluate_gate(records).status, "failed")

    def test_publishable_mode_requires_100_runs_and_clean_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            executable = _write_fake(root, "fake-mathsvg-supported")
            smoke = _request(root, executable)
            with self.assertRaisesRegex(RunnerError, "100 repetitions"):
                run_matrix(dataclasses.replace(smoke, publishable=True))

            compiler = _compiler()
            publishable = dataclasses.replace(
                smoke,
                publishable=True,
                repetitions=100,
                builds=(
                    BuildSpec("debug", executable, compiler),
                    BuildSpec("release", executable, compiler),
                ),
            )
            with self.assertRaisesRegex(RunnerError, "clean source"):
                run_matrix(publishable)

    def test_gate_pass_requires_full_100_run_cross_environment_matrix(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            executable = _write_fake(root, "fake-mathsvg-supported")
            template = run_matrix(
                _request(
                    root,
                    executable,
                    repetitions=1,
                    thread_modes=("1",),
                    backends=("scalar",),
                )
            )[0]

            short = _synthetic_gate_records(template, repetitions=1)
            short_summary = evaluate_gate(short)
            self.assertEqual(short_summary.status, "incomplete")
            self.assertIn(
                "fewer than 100 repetitions",
                " ".join(short_summary.reasons),
            )

            complete = _synthetic_gate_records(template, repetitions=100)
            complete_summary = evaluate_gate(complete)
            self.assertEqual(complete_summary.status, "pass")
            self.assertEqual(complete_summary.rows, 6000)

            same_version = [
                dataclasses.replace(row, compiler_version="rustc same")
                for row in complete
            ]
            version_summary = evaluate_gate(same_version)
            self.assertEqual(version_summary.status, "incomplete")
            self.assertIn(
                "two compiler versions",
                " ".join(version_summary.reasons),
            )


if __name__ == "__main__":
    unittest.main()
