from __future__ import annotations

import unittest

from mathsvg.python.benchmarks.protocol import (
    Codec,
    Workload,
    required_repetitions,
    schedule,
    validate_interleaving,
)


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workloads = [
            Workload("b", "validation/b"),
            Workload("a", "validation/a"),
        ]
        self.codecs = [
            Codec("mathsvg", "balanced", 1),
            Codec("zstd", "default", 1),
            Codec("xz", "9e", 1),
        ]

    def test_required_repetitions(self) -> None:
        self.assertEqual(required_repetitions(None), 5)
        self.assertEqual(required_repetitions(0.02), 5)
        self.assertEqual(required_repetitions(0.0199), 10)

    def test_schedule_is_reproducible_and_interleaved(self) -> None:
        first = schedule(
            self.workloads, self.codecs, repetitions=5, warmups=1, seed=42
        )
        second = schedule(
            reversed(self.workloads),
            reversed(self.codecs),
            repetitions=5,
            warmups=1,
            seed=42,
        )
        self.assertEqual(first, second)
        validate_interleaving(first)
        self.assertEqual(len(first), 2 * 3 * 6)

    def test_rejects_too_few_repetitions(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least five"):
            schedule(
                self.workloads, self.codecs, repetitions=4, warmups=1, seed=42
            )


if __name__ == "__main__":
    unittest.main()
