"""Deterministic statistics required by the MathSVG benchmark protocol."""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")

# Two-sided 95% Student-t critical values for 1..30 degrees of freedom.
_T95 = (
    12.706,
    4.303,
    3.182,
    2.776,
    2.571,
    2.447,
    2.365,
    2.306,
    2.262,
    2.228,
    2.201,
    2.179,
    2.160,
    2.145,
    2.131,
    2.120,
    2.110,
    2.101,
    2.093,
    2.086,
    2.080,
    2.074,
    2.069,
    2.064,
    2.060,
    2.056,
    2.052,
    2.048,
    2.045,
    2.042,
)


@dataclass(frozen=True, slots=True)
class Summary:
    count: int
    mean: float
    median: float
    standard_deviation: float
    ci95_low: float
    ci95_high: float


def _t95(degrees_of_freedom: int) -> float:
    if degrees_of_freedom < 1:
        raise ValueError("degrees of freedom must be positive")
    if degrees_of_freedom <= len(_T95):
        return _T95[degrees_of_freedom - 1]
    if degrees_of_freedom <= 40:
        return 2.021
    if degrees_of_freedom <= 60:
        return 2.000
    if degrees_of_freedom <= 120:
        return 1.980
    return 1.960


def summarize(values: Iterable[float]) -> Summary:
    samples = [float(value) for value in values]
    if not samples:
        raise ValueError("at least one sample is required")
    if not all(math.isfinite(value) for value in samples):
        raise ValueError("all samples must be finite")
    mean = statistics.fmean(samples)
    median = statistics.median(samples)
    if len(samples) == 1:
        deviation = 0.0
        half_width = 0.0
    else:
        deviation = statistics.stdev(samples)
        half_width = _t95(len(samples) - 1) * deviation / math.sqrt(len(samples))
    return Summary(
        count=len(samples),
        mean=mean,
        median=median,
        standard_deviation=deviation,
        ci95_low=mean - half_width,
        ci95_high=mean + half_width,
    )


def percentile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("at least one value is required")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(
        sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight
    )


def paired_bootstrap_interval(
    pairs: Sequence[T],
    statistic: Callable[[Sequence[T]], float],
    *,
    seed: int,
    replicates: int = 10_000,
) -> tuple[float, float]:
    """Return a deterministic percentile interval over paired file rows."""

    if not pairs:
        raise ValueError("at least one pair is required")
    if replicates < 100:
        raise ValueError("at least 100 bootstrap replicates are required")
    generator = random.Random(seed)
    count = len(pairs)
    estimates: list[float] = []
    for _ in range(replicates):
        sample = [pairs[generator.randrange(count)] for _ in range(count)]
        estimate = float(statistic(sample))
        if not math.isfinite(estimate):
            raise ValueError("bootstrap statistic must be finite")
        estimates.append(estimate)
    estimates.sort()
    return percentile(estimates, 0.025), percentile(estimates, 0.975)
