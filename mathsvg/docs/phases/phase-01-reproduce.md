# Phase 1 — Reproduce

```text
PHASE
- Baseline commit: 36f84685dc00064dae9a239face7e8eff797ed28
- Branch: mathsvg-absolute
- Status: complete for local native baseline; external-machine cells open
```

## Correctness

- Repository-wide `scripts/verify_all.sh` completed on 2026-07-28.
- Python: 55 tests passed; one release-binary clean-checkout smoke was skipped
  because the user-supplied replacement `YeuCau.md` was intentionally
  uncommitted at reproduction time.
- Rust: 61 unit/binary plus 18 integration tests passed.
- Fuzz workspace compiled.
- Synthetic, downloaded and generated manifests verified.
- Every published latest result/plot bundle passed strict verification with
  zero warning.

No source file was changed to make the baseline pass.

## Baseline benchmark

Immutable Full run:

```text
run_id: 20260726T044843Z-213f07c3
rows: 24,650 / 24,650
measured trials: 73,950
failures: 0
resume_count: 0
elapsed: 129,095.967 seconds
source revision: e60f423b5f85d1716eb2356f2460f97ba4227885
```

The codec source used by Full is the predecessor of the documentation/artifact
commits and is the same measured implementation line. The result document,
checkpoint rows, configuration/grid identity, plots and source/binary hashes
remain immutable.

## Archive and performance breakdown

| Configuration | Ratio | Compression MB/s | Peak RSS on enwik9 |
|---|---:|---:|---:|
| MathZip Fast | 1.0787 | 1.7906 | 6,700.87 MiB |
| MathZip Balanced | 1.2513 | 0.4289 | 6,860.98 MiB |
| MathZip Max | 1.2572 | 0.1388 | 7,693.37 MiB |
| Zstd default | 3.2479 | 75.0528 | 42.22 MiB |
| XZ default | 4.3610 | 1.3131 | 95.03 MiB |

MathZip coded residual occupies about 99.69% of Balanced and 99.91% of Max
archive bytes. Residual evaluation occupies about 74.44% and 66.39% of their
compression wall time. Max saves only about 0.4627% bytes over Balanced while
using 3.090 times its aggregate compression time.

## Oracle implications

- Literal/raw safety already works and is reusable as a design invariant.
- The flat-segment MZIP container is not a procedural DAG and must not be
  relabelled as MathSVG.
- Residual coding and memory are demonstrated blockers.
- Four non-synthetic Balanced size wins over Zstd are stride-like, making
  coordinate discovery the strongest observed domain signal.
- No current evidence supports learned selection; it is also forbidden by the
  new specification.

## Decision

```text
KEEP
- bounded parser/checksum patterns
- deterministic integer models/transforms
- exact serialized-size comparison
- literal fallback
- benchmark identity/verification infrastructure

REMOVE FROM NEW DESIGN
- flat-segment container as the primary abstraction
- custom residual coder as an unquestioned default
- whole-file buffering
- proxy result described as a global optimum

NEXT
- formal DSL/container/cost model
- development-only oracle suite
- no deep new primitive before oracle headroom
```
