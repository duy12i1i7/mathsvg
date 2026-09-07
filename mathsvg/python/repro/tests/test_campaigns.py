from __future__ import annotations

import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from mathsvg.python.repro.common import ReproError, tree_identity
from mathsvg.python.repro.determinism_campaign import (
    _limitations,
    _preflight,
    _safe_input,
)
from mathsvg.python.repro.fuzz_campaign import (
    _fuzz_command,
    _generated_decode_sources,
    _parse_executed_units,
)
from mathsvg.python.repro.literal_safety import (
    REQUIRED_DOMAINS,
    REQUIRED_PROFILES,
    assess_gate as assess_literal_safety,
    fixed_literal_overhead,
    maximum_archive_bytes,
)
from mathsvg.python.repro.memory_gate import assess_gate
from mathsvg.python.repro.procedural_gate import assess_gate as assess_procedural


class CommonTests(unittest.TestCase):
    def test_tree_identity_is_order_independent_and_content_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "b").write_bytes(b"two")
            (root / "a").write_bytes(b"one")
            first = tree_identity(root)
            self.assertEqual(first.files, 2)
            self.assertEqual(first.bytes, 6)
            (root / "b").write_bytes(b"three")
            second = tree_identity(root)
            self.assertNotEqual(first.sha256, second.sha256)

    def test_tree_identity_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            target = root / "target"
            target.write_bytes(b"x")
            (root / "link").symlink_to(target)
            with self.assertRaisesRegex(ReproError, "non-regular"):
                tree_identity(root)


class FuzzCampaignTests(unittest.TestCase):
    def test_final_libfuzzer_unit_count_is_parsed(self) -> None:
        rendered = """
        stat::number_of_executed_units: 12
        stat::number_of_executed_units: 4096
        """
        self.assertEqual(_parse_executed_units(rendered), 4096)
        self.assertIsNone(_parse_executed_units("no final stats"))

    def test_generated_decode_sources_are_bounded_and_deterministic(self) -> None:
        first = _generated_decode_sources()
        second = _generated_decode_sources()
        self.assertEqual(first, second)
        self.assertEqual(
            [name for name, _ in first],
            ["constant", "linear", "periodic"],
        )
        self.assertTrue(all(len(payload) == 4096 for _, payload in first))
        self.assertEqual(len({payload for _, payload in first}), 3)

    def test_command_pins_cargo_fuzz_without_rustup_proxy_syntax(self) -> None:
        command = _fuzz_command(
            pathlib.Path("/tools/cargo-fuzz"),
            pathlib.Path("/repo/fuzz"),
            "mathsvg_decode",
            pathlib.Path("/scratch/corpus"),
            pathlib.Path("/scratch/artifacts"),
            runs=10_000,
            max_len=65_536,
            input_timeout_seconds=10,
            rss_limit_mb=2_048,
        )
        self.assertEqual(command[:2], ["/tools/cargo-fuzz", "run"])
        self.assertNotIn("+nightly", command)
        self.assertIn("-runs=10000", command)


class DeterminismCampaignTests(unittest.TestCase):
    HEADER = (
        "split,dataset_id,split_group,origin,primary,domain,source,license,"
        "sha256,bytes,sealed,path,legacy_observed\n"
    )

    def test_repetition_limitation_only_applies_below_publishable_count(
        self,
    ) -> None:
        reason = "fewer than 100 repetitions remains incomplete"
        self.assertIn(reason, _limitations(99))
        self.assertNotIn(reason, _limitations(100))
        self.assertNotIn(reason, _limitations(101))

    @staticmethod
    def _row(
        *,
        split: str,
        dataset_id: str,
        digest: str,
        size: int,
        sealed: bool,
        path: str,
    ) -> str:
        return (
            f"{split},{dataset_id},{dataset_id},real,true,test-domain,"
            f"test-source,test-license,{digest},{size},"
            f"{str(sealed).lower()},{path},false\n"
        )

    def test_manifest_selected_input_is_checksum_verified(self) -> None:
        import hashlib

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            payload = root / "development.bin"
            payload.write_bytes(b"development")
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            manifest = root / "manifest.csv"
            manifest.write_text(
                self.HEADER
                + self._row(
                    split="development",
                    dataset_id="dev-safe",
                    digest=digest,
                    size=payload.stat().st_size,
                    sealed=False,
                    path=payload.name,
                ),
                encoding="utf-8",
            )
            entry, selected, entries = _safe_input(
                root,
                manifest,
                split="development",
                dataset_id="dev-safe",
            )
            self.assertEqual(entry.dataset_id, "dev-safe")
            self.assertEqual(selected, payload)
            self.assertEqual(len(entries), 1)

            payload.write_bytes(b"changed")
            with self.assertRaisesRegex(ReproError, "length|SHA-256"):
                _safe_input(
                    root,
                    manifest,
                    split="development",
                    dataset_id="dev-safe",
                )

    def test_any_holdout_row_is_rejected_before_payload_access(self) -> None:
        import hashlib

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            payload = root / "development.bin"
            payload.write_bytes(b"development")
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            manifest = root / "manifest.csv"
            manifest.write_text(
                self.HEADER
                + self._row(
                    split="development",
                    dataset_id="dev-safe",
                    digest=digest,
                    size=payload.stat().st_size,
                    sealed=False,
                    path=payload.name,
                )
                + self._row(
                    split="holdout",
                    dataset_id="holdout-never-open",
                    digest="a" * 64,
                    size=123,
                    sealed=True,
                    path="missing/holdout.bin",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReproError, "containing holdout rows"
            ):
                _safe_input(
                    root,
                    manifest,
                    split="development",
                    dataset_id="dev-safe",
                )
            self.assertFalse((root / "missing" / "holdout.bin").exists())

    def test_runtime_preflight_rejects_stale_contract(self) -> None:
        valid = SimpleNamespace(
            unavailable_reason="",
            runtime_profile={"schema_version": 1},
            runtime_profile_sha256="b" * 64,
            executable_sha256="c" * 64,
            version="mathsvg test",
            config_sha256="d" * 64,
        )
        with mock.patch(
            "mathsvg.python.repro.determinism_campaign.build_native_specs",
            return_value=[valid],
        ):
            result = _preflight(
                pathlib.Path("/tmp/mathsvg"),
                profile="balanced",
                config_root=pathlib.Path("/tmp/configs"),
            )
        self.assertEqual(result["runtime_profile_sha256"], "b" * 64)

        stale = SimpleNamespace(
            unavailable_reason="runtime profile/TOML mismatch",
            runtime_profile=None,
            runtime_profile_sha256="",
            executable_sha256="",
            version="",
            config_sha256="",
        )
        with mock.patch(
            "mathsvg.python.repro.determinism_campaign.build_native_specs",
            return_value=[stale],
        ):
            with self.assertRaisesRegex(ReproError, "preflight failed"):
                _preflight(
                    pathlib.Path("/tmp/stale"),
                    profile="balanced",
                    config_root=pathlib.Path("/tmp/configs"),
                )


class MemoryGateTests(unittest.TestCase):
    @staticmethod
    def _rows(*, encoder_ok: bool = True) -> list[dict[str, object]]:
        return [
            {
                "status": "ok",
                "encoder_within_limit": encoder_ok,
                "decoder_within_limit": True,
            }
            for _ in range(15)
        ]

    def test_complete_enwik9_matrix_can_pass(self) -> None:
        status, reasons = assess_gate(
            self._rows(),
            dataset_id="dev-enwik9-enwik9",
            original_bytes=1_000_000_000,
            repetitions=5,
            warmups=1,
            profiles=("fast", "balanced", "max"),
            source_stable=True,
            immutable_inputs_stable=True,
        )
        self.assertEqual(status, "pass")
        self.assertEqual(reasons, [])

    def test_smoke_is_incomplete_and_threshold_excess_is_failure(self) -> None:
        status, reasons = assess_gate(
            self._rows(),
            dataset_id="dev-canterbury-alice29-txt",
            original_bytes=152_089,
            repetitions=1,
            warmups=1,
            profiles=("fast",),
            source_stable=True,
            immutable_inputs_stable=True,
        )
        self.assertEqual(status, "incomplete")
        self.assertIn("canonical", " ".join(reasons))
        failed, failed_reasons = assess_gate(
            self._rows(encoder_ok=False),
            dataset_id="dev-enwik9-enwik9",
            original_bytes=1_000_000_000,
            repetitions=5,
            warmups=1,
            profiles=("fast", "balanced", "max"),
            source_stable=True,
            immutable_inputs_stable=True,
        )
        self.assertEqual(failed, "failed")
        self.assertIn("exceeded", " ".join(failed_reasons))


class LiteralSafetyTests(unittest.TestCase):
    @staticmethod
    def _rows(*, within_bound: bool = True) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for domain in sorted(REQUIRED_DOMAINS):
            dataset_id = domain.removeprefix("incompressible-control-")
            for profile in REQUIRED_PROFILES:
                for repetition in range(2):
                    rows.append(
                        {
                            "status": "ok",
                            "within_expansion_bound": within_bound,
                            "dataset_id": dataset_id,
                            "domain": domain,
                            "profile": profile,
                            "repetition": repetition,
                            "archive_sha256": f"{dataset_id}-{profile}",
                        }
                    )
        return rows

    def test_literal_overhead_and_integer_bound_are_exact(self) -> None:
        self.assertEqual(fixed_literal_overhead(0), 256)
        self.assertEqual(fixed_literal_overhead(1), 488)
        self.assertEqual(fixed_literal_overhead(4), 1184)
        self.assertEqual(maximum_archive_bytes(65_536, 1), 66_089)

    def test_complete_control_matrix_can_pass(self) -> None:
        status, reasons = assess_literal_safety(
            self._rows(),
            profiles=REQUIRED_PROFILES,
            repetitions=2,
            covered_domains=sorted(REQUIRED_DOMAINS),
            source_stable=True,
            immutable_inputs_stable=True,
        )
        self.assertEqual(status, "pass")
        self.assertEqual(reasons, [])

    def test_expansion_failure_fails_and_missing_domain_is_incomplete(self) -> None:
        failed, failed_reasons = assess_literal_safety(
            self._rows(within_bound=False),
            profiles=REQUIRED_PROFILES,
            repetitions=2,
            covered_domains=sorted(REQUIRED_DOMAINS),
            source_stable=True,
            immutable_inputs_stable=True,
        )
        self.assertEqual(failed, "failed")
        self.assertIn("exceeded", " ".join(failed_reasons))

        missing = sorted(REQUIRED_DOMAINS)[1:]
        incomplete, incomplete_reasons = assess_literal_safety(
            self._rows(),
            profiles=REQUIRED_PROFILES,
            repetitions=2,
            covered_domains=missing,
            source_stable=True,
            immutable_inputs_stable=True,
        )
        self.assertEqual(incomplete, "incomplete")
        self.assertIn("missing", " ".join(incomplete_reasons))


class ProceduralGateTests(unittest.TestCase):
    @staticmethod
    def _rows(*, real_winner: bool) -> list[dict[str, object]]:
        synthetic = [
            {
                "category": "synthetic-exact",
                "dataset_id": f"synthetic-{index}",
                "domain": "synthetic",
                "status": "ok",
                "original_bytes": 4096,
                "procedural_coverage": 1.0,
                "literal_leaf_bytes": 0,
                "procedural_gain_before_entropy_bytes": 4000,
            }
            for index in range(6)
        ]
        real = [
            {
                "category": "real-structured",
                "dataset_id": "real-one",
                "domain": "real-test",
                "status": "ok",
                "original_bytes": 1000,
                "function_reconstructed_bytes": 600 if real_winner else 0,
                "literal_only_archive_bytes": 1500,
                "pre_entropy_archive_bytes": 1400 if real_winner else 1500,
            }
        ]
        return synthetic + real

    def test_complete_synthetic_and_real_winner_can_pass(self) -> None:
        status, reasons, domains = assess_procedural(
            self._rows(real_winner=True),
            source_stable=True,
            immutable_inputs_stable=True,
        )
        self.assertEqual(status, "pass")
        self.assertEqual(reasons, [])
        self.assertTrue(domains[0]["gate_pass"])

    def test_absent_real_procedural_winner_fails(self) -> None:
        status, reasons, domains = assess_procedural(
            self._rows(real_winner=False),
            source_stable=True,
            immutable_inputs_stable=True,
        )
        self.assertEqual(status, "failed")
        self.assertIn("no real structured domain", " ".join(reasons))
        self.assertFalse(domains[0]["gate_pass"])


if __name__ == "__main__":
    unittest.main()
