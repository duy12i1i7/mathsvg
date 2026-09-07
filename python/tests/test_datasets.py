from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mathzip_bench.datasets import DatasetError, prepare_dataset


class DatasetTests(unittest.TestCase):
    def _manifest(
        self, root: Path, archive: Path, member: str = "payload.bin"
    ) -> Path:
        payload = b"deterministic dataset payload\n" * 32
        manifest = {
            "schema_version": 1,
            "name": "fixture",
            "description": "test",
            "archive": {
                "urls": [archive.as_uri()],
                "filename": archive.name,
                "type": "zip",
                "size_bytes": archive.stat().st_size,
                "max_download_bytes": archive.stat().st_size,
                "max_uncompressed_bytes": 10_000,
                "max_entries": 4,
                "checksums": {
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()
                },
                "members": [member],
            },
            "expected_files": [
                {
                    "path": member,
                    "size_bytes": len(payload),
                    "checksums": {
                        "sha256": hashlib.sha256(payload).hexdigest()
                    },
                }
            ],
            "combined": {
                "output": "combined.bin",
                "manifest": "combined.manifest.json",
                "members": [member],
            },
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_checksumming_safe_extraction_and_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = b"deterministic dataset payload\n" * 32
            archive = root / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("payload.bin", payload)
            manifest = self._manifest(root, archive)
            output_root = root / "data"
            first = prepare_dataset(manifest, output_root)
            self.assertEqual(first["status"], "prepared")
            self.assertEqual(
                (output_root / "fixture" / "combined.bin").read_bytes(), payload
            )
            second = prepare_dataset(manifest, output_root)
            self.assertEqual(second["status"], "already_prepared")

    def test_zip_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "evil.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../payload.bin", b"deterministic dataset payload\n" * 32)
            manifest = self._manifest(root, archive, "../payload.bin")
            with self.assertRaises(DatasetError):
                prepare_dataset(manifest, root / "data")
