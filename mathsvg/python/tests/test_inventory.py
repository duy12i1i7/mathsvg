from __future__ import annotations

import pathlib
import tempfile
import unittest

from mathsvg.python.benchmarks.inventory import InventoryError, inventory


class InventoryTests(unittest.TestCase):
    def test_available_missing_and_explicit_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            executable = root / "tiny"
            executable.write_text("#!/bin/sh\necho tiny-1\n", encoding="utf-8")
            executable.chmod(0o755)
            catalog = root / "baselines.toml"
            catalog.write_text(
                """
schema_version = 1
[[baseline]]
id = "tiny"
family = "test"
command_template = "tiny -c {input}"
[[baseline]]
id = "missing"
family = "test"
command_template = "definitely-not-installed {input}"
[[baseline]]
id = "explicit"
family = "test"
availability = "unavailable"
unavailable_reason = "no canonical test adapter"
command_template = ""
""".strip()
                + "\n",
                encoding="utf-8",
            )
            result = inventory(catalog, search_path=str(root))
            statuses = {
                row["id"]: row["status"] for row in result["baselines"]  # type: ignore[index]
            }
            self.assertEqual(
                statuses,
                {
                    "tiny": "available",
                    "missing": "unavailable",
                    "explicit": "unavailable",
                },
            )
            explicit = next(
                row
                for row in result["baselines"]  # type: ignore[index]
                if row["id"] == "explicit"
            )
            self.assertEqual(
                explicit["unavailable_reason"], "no canonical test adapter"
            )

    def test_unfrozen_placeholder_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            catalog = pathlib.Path(temp) / "baselines.toml"
            catalog.write_text(
                """
schema_version = 1
[[baseline]]
id = "bad"
command_template = "UNFROZEN: choose"
""".strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(InventoryError, "UNFROZEN"):
                inventory(catalog, search_path=temp)

    def test_duplicate_identifier_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            catalog = pathlib.Path(temp) / "baselines.toml"
            catalog.write_text(
                """
schema_version = 1
[[baseline]]
id = "same"
command_template = "one"
[[baseline]]
id = "same"
command_template = "two"
""".strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(InventoryError, "duplicate"):
                inventory(catalog, search_path=temp)

    def test_failed_version_probe_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            executable = root / "broken"
            executable.write_text(
                "#!/bin/sh\necho missing-runtime >&2\nexit 127\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            catalog = root / "baselines.toml"
            catalog.write_text(
                """
schema_version = 1
[[baseline]]
id = "broken"
command_template = "broken {input}"
""".strip()
                + "\n",
                encoding="utf-8",
            )

            row = inventory(catalog, search_path=str(root))["baselines"][0]  # type: ignore[index]
            self.assertEqual(row["status"], "unavailable")
            self.assertIn("exited 127", row["unavailable_reason"])
            self.assertEqual(row["resolved_executable"], "")

    def test_alias_falls_back_after_failed_version_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            broken = root / "7z"
            broken.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
            broken.chmod(0o755)
            working = root / "7zz"
            working.write_text(
                "#!/bin/sh\necho '7-Zip fixture 1.0'\n",
                encoding="utf-8",
            )
            working.chmod(0o755)
            catalog = root / "baselines.toml"
            catalog.write_text(
                """
schema_version = 1
[[baseline]]
id = "seven-zip"
command_template = "7z a {output} {input}"
""".strip()
                + "\n",
                encoding="utf-8",
            )

            row = inventory(catalog, search_path=str(root))["baselines"][0]  # type: ignore[index]
            self.assertEqual(row["status"], "available")
            self.assertEqual(row["resolved_executable"], str(working))
            self.assertEqual(row["version"], "7-Zip fixture 1.0")


if __name__ == "__main__":
    unittest.main()
