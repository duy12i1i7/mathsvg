#!/usr/bin/env python3
"""Generate conservative Pareto-dominance certificates."""

from __future__ import annotations

import csv
import dataclasses
import pathlib
from dataclasses import dataclass
from typing import Iterable, Sequence

CERTIFICATE_FIELDS = (
    "baseline_codec",
    "baseline_config",
    "mathsvg_profile",
    "dataset",
    "file",
    "original_bytes",
    "baseline_bytes",
    "mathsvg_bytes",
    "baseline_compression_ns",
    "mathsvg_compression_ns",
    "baseline_decompression_ns",
    "mathsvg_decompression_ns",
    "baseline_peak_rss",
    "mathsvg_peak_rss",
    "size_not_worse",
    "compression_not_worse",
    "decompression_not_worse",
    "memory_not_worse",
    "strict_metric_count",
    "confidence_status",
    "roundtrip_ok",
    "native_mathsvg",
)


@dataclass(frozen=True, slots=True)
class Measurement:
    codec: str
    config: str
    dataset: str
    file: str
    original_bytes: int
    compressed_bytes: int
    compression_ns: int | None
    decompression_ns: int | None
    peak_rss_bytes: int | None
    confidence_status: str
    roundtrip_ok: bool
    native_mathsvg: bool

    def validate(self) -> None:
        if self.original_bytes < 0 or self.compressed_bytes < 0:
            raise ValueError("byte counts must be non-negative")
        for name in ("compression_ns", "decompression_ns", "peak_rss_bytes"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.confidence_status not in {
            "confirmed",
            "inconclusive",
            "not-measured",
        }:
            raise ValueError("invalid confidence status")


@dataclass(frozen=True, slots=True)
class Certificate:
    baseline_codec: str
    baseline_config: str
    mathsvg_profile: str
    dataset: str
    file: str
    original_bytes: int
    baseline_bytes: int
    mathsvg_bytes: int
    baseline_compression_ns: int | None
    mathsvg_compression_ns: int | None
    baseline_decompression_ns: int | None
    mathsvg_decompression_ns: int | None
    baseline_peak_rss: int | None
    mathsvg_peak_rss: int | None
    size_not_worse: bool
    compression_not_worse: bool
    decompression_not_worse: bool
    memory_not_worse: bool
    strict_metric_count: int
    confidence_status: str
    roundtrip_ok: bool
    native_mathsvg: bool

    @property
    def passes(self) -> bool:
        return (
            self.roundtrip_ok
            and self.native_mathsvg
            and self.confidence_status == "confirmed"
            and self.size_not_worse
            and self.compression_not_worse
            and self.decompression_not_worse
            and self.memory_not_worse
            and self.strict_metric_count >= 1
        )


def _not_worse(left: int | None, right: int | None) -> bool:
    return left is not None and right is not None and left <= right


def _strict(left: int | None, right: int | None) -> int:
    return int(left is not None and right is not None and left < right)


def compare(mathsvg: Measurement, baseline: Measurement) -> Certificate:
    mathsvg.validate()
    baseline.validate()
    if not mathsvg.native_mathsvg:
        raise ValueError("the MathSVG side must have native_mathsvg=true")
    if baseline.native_mathsvg:
        raise ValueError("the baseline side must have native_mathsvg=false")
    identity_mathsvg = (mathsvg.dataset, mathsvg.file, mathsvg.original_bytes)
    identity_baseline = (
        baseline.dataset,
        baseline.file,
        baseline.original_bytes,
    )
    if identity_mathsvg != identity_baseline:
        raise ValueError("measurement identity mismatch")

    size_not_worse = mathsvg.compressed_bytes <= baseline.compressed_bytes
    compression_not_worse = _not_worse(
        mathsvg.compression_ns, baseline.compression_ns
    )
    decompression_not_worse = _not_worse(
        mathsvg.decompression_ns, baseline.decompression_ns
    )
    memory_not_worse = _not_worse(
        mathsvg.peak_rss_bytes, baseline.peak_rss_bytes
    )
    strict_count = int(mathsvg.compressed_bytes < baseline.compressed_bytes)
    strict_count += _strict(mathsvg.compression_ns, baseline.compression_ns)
    strict_count += _strict(mathsvg.decompression_ns, baseline.decompression_ns)
    strict_count += _strict(mathsvg.peak_rss_bytes, baseline.peak_rss_bytes)

    if not mathsvg.roundtrip_ok or not baseline.roundtrip_ok:
        confidence = "inconclusive"
    elif (
        mathsvg.confidence_status == "confirmed"
        and baseline.confidence_status == "confirmed"
    ):
        confidence = "confirmed"
    elif (
        mathsvg.confidence_status == "not-measured"
        or baseline.confidence_status == "not-measured"
    ):
        confidence = "not-measured"
    else:
        confidence = "inconclusive"

    return Certificate(
        baseline_codec=baseline.codec,
        baseline_config=baseline.config,
        mathsvg_profile=mathsvg.config,
        dataset=mathsvg.dataset,
        file=mathsvg.file,
        original_bytes=mathsvg.original_bytes,
        baseline_bytes=baseline.compressed_bytes,
        mathsvg_bytes=mathsvg.compressed_bytes,
        baseline_compression_ns=baseline.compression_ns,
        mathsvg_compression_ns=mathsvg.compression_ns,
        baseline_decompression_ns=baseline.decompression_ns,
        mathsvg_decompression_ns=mathsvg.decompression_ns,
        baseline_peak_rss=baseline.peak_rss_bytes,
        mathsvg_peak_rss=mathsvg.peak_rss_bytes,
        size_not_worse=size_not_worse,
        compression_not_worse=compression_not_worse,
        decompression_not_worse=decompression_not_worse,
        memory_not_worse=memory_not_worse,
        strict_metric_count=strict_count,
        confidence_status=confidence,
        roundtrip_ok=mathsvg.roundtrip_ok and baseline.roundtrip_ok,
        native_mathsvg=True,
    )


def _csv_value(value: object) -> object:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    return value


def write_certificates(
    path: pathlib.Path, certificates: Iterable[Certificate]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CERTIFICATE_FIELDS)
        writer.writeheader()
        for certificate in certificates:
            row = dataclasses.asdict(certificate)
            writer.writerow({key: _csv_value(row[key]) for key in CERTIFICATE_FIELDS})


def failures(certificates: Sequence[Certificate]) -> list[Certificate]:
    return [certificate for certificate in certificates if not certificate.passes]
