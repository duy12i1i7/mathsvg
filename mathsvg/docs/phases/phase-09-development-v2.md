# Phase 9 — Development pilot v2

## Evidence boundary

`development-pilot-v2-retry` is a completed development experiment, not a
publishable benchmark or holdout result. It used ten measured repetitions
after one warm-up and produced 1,573/1,573 successful rows. No validation or
holdout payload was opened.

The run started from commit
`36f84685dc00064dae9a239face7e8eff797ed28` with a dirty source tree. The host
was not isolated, background processes were not quiesced, CPU affinity was not
pinned, and the initial load averages were 8.84/9.72/7.82. Archive sizes,
round trips, deterministic native hashes and ablation identities are usable
development evidence. Timing and RSS are directional only.

Provenance:

- raw JSONL SHA-256:
  `158c6bafe466a94d0f90df39b2b3038ade08238b91d61c7acf380d4fac516594`;
- run metadata SHA-256:
  `2a5428b45f9141d72b19a597f37239f8f149806b4ec11f52bee488ddf0d58164`;
- frozen development-manifest identity:
  `b1d7dba75560d44d8e234e4205e09487f93678fb8688cac6a0905486c969c2ca`;
- measured MathSVG executable SHA-256:
  `81128211b782e3a4331d1c5fcd6db21eecf78dc823faab718e7ebe3f2ceeeee8`.

The manifest was expanded after this run. Therefore these results remain tied
to the exact manifest identity above and must not be relabelled as evidence for
the newer corpus.

## Corpus result

The selected 11-file pilot contained 10,180,361 source bytes.

| Codec/profile | Archive bytes | Ratio | Compression | Decompression | Peak RSS |
|---|---:|---:|---:|---:|---:|
| MathSVG Fast | 7,257,191 | 1.4028x | 14.939 s | 2.254 s | 21.49 MiB |
| MathSVG Balanced | 7,257,191 | 1.4028x | 22.641 s | 2.235 s | 21.49 MiB |
| raw | 10,180,361 | 1.0000x | 0.111 s | 0.110 s | 2.01 MiB |
| LZ4 | 8,951,634 | 1.1373x | 0.163 s | 0.159 s | 9.54 MiB |
| zstd-default | 6,334,828 | 1.6070x | 0.352 s | 0.175 s | 18.47 MiB |
| gzip-6 | 6,380,035 | 1.5957x | 0.741 s | 0.265 s | 1.99 MiB |
| xz-9e | 4,671,724 | 2.1791x | 9.216 s | 0.643 s | 139.23 MiB |

MathSVG was 14.5602% larger than zstd-default, so the size-only Gate 5/G2
milestone (no more than 15% larger) passed on this pilot. Ratio remained below
2x, no independent standard corpus was beaten, and no new Pareto point or
baseline dominance was established.

On the three non-synthetic standard files, MathSVG produced 7,174,101 bytes
from 9,656,073 bytes (1.3460x). On the eight synthetic diagnostics, it produced
83,090 bytes from 524,288 bytes (6.3099x).

## Procedural and ablation evidence

Against the literal-only counterfactual:

- pre-entropy procedural selection saved 131,074 bytes;
- native entropy saved 2,799,320 bytes;
- their combined saving was 2,930,394 bytes;
- function nodes reconstructed 131,072 source bytes, only 1.2875% of the
  complete pilot corpus.

Paired corpus ablations used the exact same schedule cells:

| Disabled engine | Archive delta (`ablated - enabled`) | Decision |
|---|---:|---|
| whole-block entropy | +2,799,320 bytes (+38.573%) | confirmed size benefit |
| whole-block functions | +269 bytes (+0.003707%) | size effect inconclusive |
| coordinates | 0 bytes | size effect inconclusive |

The ratio improvement is consequently dominated by native literal entropy.
This run is not evidence that the procedural-function representation succeeded
on real structured data.

## Dominance and gate status

- Primary Fast/Balanced corpus comparisons: 0/10 certificates passed.
- Primary per-file comparisons: 0/110 passed; 52 failed and 58 were
  inconclusive.
- Including ablation configurations: 0/40 corpus comparisons passed.
- Gate 5/G2 passed on this pilot; Gate 5/G1 and G3--G5 did not.
- Gate 4 real-structured proof did not pass.
- The enwik9 memory gate, full determinism campaign, fuzz campaign, validation
  benchmark and all holdout gates were not evaluated by this run.
- Gates 6--8 remain closed.

## Versioned artifacts

- `results/raw/development-pilot-v2-retry.jsonl`
- `results/raw/development-pilot-v2-retry-run.json`
- `results/summary/development-pilot-v2-retry-benchmark.csv`
- `results/summary/development-pilot-v2-retry-procedural.csv`
- `results/ablation/development-pilot-v2-retry.csv`
- `results/profiling/development-pilot-v2-retry.csv`
- `results/dominance/development-pilot-v2-retry/`
- `results/plots/development-pilot-v2-retry-*.svg`

The canonical final-result paths remain intentionally untouched until a clean
commit/config freeze and the one-time holdout phase.
