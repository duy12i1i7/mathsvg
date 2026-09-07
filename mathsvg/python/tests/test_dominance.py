from __future__ import annotations

import pathlib
import tempfile
import unittest

from mathsvg.python.analysis.dominance import (
    Measurement,
    compare,
    failures,
    write_certificates,
)


def measurement(**overrides: object) -> Measurement:
    values: dict[str, object] = {
        "codec": "mathsvg",
        "config": "balanced",
        "dataset": "suite",
        "file": "one.bin",
        "original_bytes": 1000,
        "compressed_bytes": 500,
        "compression_ns": 100,
        "decompression_ns": 100,
        "peak_rss_bytes": 100,
        "confidence_status": "confirmed",
        "roundtrip_ok": True,
        "native_mathsvg": True,
    }
    values.update(overrides)
    return Measurement(**values)  # type: ignore[arg-type]


class DominanceTests(unittest.TestCase):
    def test_strict_dominance_passes(self) -> None:
        mathsvg = measurement()
        baseline = measurement(
            codec="zstd",
            config="default",
            compressed_bytes=600,
            compression_ns=110,
            decompression_ns=100,
            peak_rss_bytes=150,
            native_mathsvg=False,
        )
        certificate = compare(mathsvg, baseline)
        self.assertTrue(certificate.passes)
        self.assertEqual(certificate.strict_metric_count, 3)

    def test_missing_metric_cannot_pass(self) -> None:
        mathsvg = measurement(peak_rss_bytes=None)
        baseline = measurement(
            codec="zstd", config="default", native_mathsvg=False
        )
        certificate = compare(mathsvg, baseline)
        self.assertFalse(certificate.memory_not_worse)
        self.assertFalse(certificate.passes)
        self.assertEqual(failures([certificate]), [certificate])

    def test_inconclusive_confidence_cannot_pass(self) -> None:
        mathsvg = measurement(confidence_status="inconclusive")
        baseline = measurement(
            codec="zstd",
            config="default",
            compressed_bytes=600,
            compression_ns=110,
            decompression_ns=110,
            peak_rss_bytes=110,
            native_mathsvg=False,
        )
        self.assertFalse(compare(mathsvg, baseline).passes)

    def test_csv_uses_required_boolean_spelling(self) -> None:
        baseline = measurement(
            codec="zstd", config="default", native_mathsvg=False
        )
        certificate = compare(measurement(), baseline)
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "certificates.csv"
            write_certificates(path, [certificate])
            encoded = path.read_text(encoding="utf-8")
            self.assertIn("native_mathsvg", encoded)
            self.assertIn("true", encoded)


if __name__ == "__main__":
    unittest.main()
