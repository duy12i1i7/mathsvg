"""Frozen scheduling rules for randomized, interleaved benchmark trials."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True, order=True, slots=True)
class Workload:
    dataset_id: str
    input_path: str


@dataclass(frozen=True, order=True, slots=True)
class Codec:
    codec_id: str
    config_id: str
    threads: int


@dataclass(frozen=True, slots=True)
class Trial:
    block: int
    order: int
    repetition: int
    workload: Workload
    codec: Codec
    warmup: bool


def required_repetitions(expected_delta_fraction: float | None) -> int:
    if expected_delta_fraction is not None and expected_delta_fraction < 0:
        raise ValueError("expected delta must be non-negative")
    return 10 if expected_delta_fraction is not None and expected_delta_fraction < 0.02 else 5


def _block_seed(seed: int, repetition: int, workload: Workload) -> int:
    material = (
        f"{seed}\0{repetition}\0{workload.dataset_id}\0{workload.input_path}"
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def schedule(
    workloads: Iterable[Workload],
    codecs: Iterable[Codec],
    *,
    repetitions: int,
    warmups: int,
    seed: int,
) -> list[Trial]:
    """Create workload blocks with independently shuffled codec order.

    All codecs run once inside each `(repetition, workload)` block.  This
    preserves pairing while avoiding a long codec-by-codec execution order.
    """

    ordered_workloads = sorted(set(workloads))
    ordered_codecs = sorted(set(codecs))
    if not ordered_workloads:
        raise ValueError("at least one workload is required")
    if not ordered_codecs:
        raise ValueError("at least one codec is required")
    if repetitions < 5:
        raise ValueError("measured repetitions must be at least five")
    if warmups < 1:
        raise ValueError("at least one warm-up is required")
    if any(codec.threads < 1 for codec in ordered_codecs):
        raise ValueError("thread counts must be positive")

    trials: list[Trial] = []
    block = 0
    phases = [(True, warmups), (False, repetitions)]
    for warmup, phase_repetitions in phases:
        for repetition in range(phase_repetitions):
            # Workload order is deterministic but changes by repetition.
            workload_order = list(ordered_workloads)
            random.Random(seed ^ repetition ^ (0xA5A5 if warmup else 0)).shuffle(
                workload_order
            )
            for workload in workload_order:
                codec_order = list(ordered_codecs)
                random.Random(
                    _block_seed(seed ^ (0x5A5A if warmup else 0), repetition, workload)
                ).shuffle(codec_order)
                trials.extend(
                    Trial(
                        block=block,
                        order=order,
                        repetition=repetition,
                        workload=workload,
                        codec=codec,
                        warmup=warmup,
                    )
                    for order, codec in enumerate(codec_order)
                )
                block += 1
    return trials


def validate_interleaving(trials: Sequence[Trial]) -> None:
    by_block: dict[int, list[Trial]] = {}
    for trial in trials:
        by_block.setdefault(trial.block, []).append(trial)
    if not by_block:
        raise ValueError("empty schedule")

    expected_codecs = {trial.codec for trial in by_block[min(by_block)]}
    for block, rows in sorted(by_block.items()):
        codecs = [row.codec for row in rows]
        if set(codecs) != expected_codecs or len(codecs) != len(expected_codecs):
            raise ValueError(f"block {block} does not contain every codec once")
        if sorted(row.order for row in rows) != list(range(len(rows))):
            raise ValueError(f"block {block} order is not dense")
