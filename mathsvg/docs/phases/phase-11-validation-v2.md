# Phase 11 — Full open validation v2

```text
PHASE
- Commit: 36f84685dc00064dae9a239face7e8eff797ed28 + dirty development tree
- Status: complete one-host open-validation experiment; dominance failed; not holdout

CORRECTNESS
- Tests: full local quality gates pass before the frozen benchmark
- Round-trip: 2,200/2,200 scheduled rows successful; zero failed, timeout or unavailable rows
- Determinism: ten measured archives per input/config are identical; all 40 MathSVG archive identities equal validation v1
- Fuzz: sanitizer v4 passed 10,000 runs on each of decode/entropy/roundtrip

PROCEDURAL REPRESENTATION
- Function graph bytes: 854
- Coordinate bytes: 0
- Parameter bytes: 0
- Residual bytes: 0
- Literal bytes: 66,729,383
- Procedural coverage: 0%
- Procedural gain: 0 bytes before entropy; 12,747,826 bytes from entropy

PERFORMANCE
- Ratio: 1.190387x
- Compression MB/s: 0.984248
- Decompression MB/s: 8.642976
- Peak RSS: 26.314 MiB

BASELINES
- Zstd: 1.209054x, 64.362060 MB/s encode, 140.600760 MB/s decode
- XZ: 1.275464x, 1.390912 MB/s encode, 14.347459 MB/s decode
- Brotli: not scheduled
- LZ4: 1.141209x, 123.828451 MB/s encode, 144.141207 MB/s decode
- Domain codec: not scheduled

ORACLE
- Remaining search headroom: development oracle remains bounded-incomplete; validation was not used to tune the catalogue
- Coordinate headroom: 0 bytes in the frozen development scan, not a validation/global proof
- Residual headroom: 115 development-sample bytes; no enabled validation emission
- Symbolic headroom: 0 bounded development bytes; no global proof
- DAG-sharing headroom: 0 on 13/13 complete development rows

DOMINANCE
- Dominated: none under the required four-metric certificate
- Not dominated: gzip-6, LZ4, XZ-9e, zstd-default
- Blocking metrics: size versus gzip/Zstd/XZ; encode/decode time versus all; RSS versus gzip/LZ4

DECISION
- Keep: the validation-overhead refactor; archive bytes are unchanged and both times improve materially
- Remove: no additional representation provider on validation evidence; validation is evaluation-only
- Pivot: new representation/entropy evidence is required; runtime-only tuning cannot close the measured size and throughput gaps
- Next: close local acceptance gates, obtain second-machine/ARM64 evidence, create a clean freeze, and only then authorize holdout
```

## Protocol and provenance

The validation manifest contains 40 files and 79,477,825 bytes. Thirty-seven
files (92.5%) and 99.9012% of bytes are independent primary-real inputs. The
run scheduled one warm-up and ten measured repetitions for MathSVG Balanced,
gzip-6, LZ4, zstd-default and XZ-9e using blocked randomized codec order.
Source identity stayed stable, the runner performed no tuning, and no holdout
payload was opened. The host was not isolated and the tree was dirty, so this
is strong local validation evidence but not a publishable final result.

- raw JSONL: 2,200 rows, 5,079,191 bytes, SHA-256
  `c2ef3f1df543e7621257f07b249fec289d1557b6afdd1bb050809c9572344f4e`;
- run metadata SHA-256:
  `52c76c4a23f79e0ae64a544230080cd314663451bc5712c8d2281352d8ce0c7e`;
- measured executable SHA-256:
  `6b3bfbe2caa5cdad79a201a99511c6d32760b0a1eb3ec1448325c20b2a3c211d`;
- benchmark summary SHA-256:
  `09646eb8c161ff29f66ce41f88d7c45c5c2eec11c581665ad17e694ce945e292`;
- procedural summary SHA-256:
  `74780af2968d7e69f657a4757996a622eb5dc0c8ae62649dc1017d533c44506e`.

## Aggregate result

| Codec/profile | Archive bytes | Ratio | Compression | Decompression | Peak RSS |
|---|---:|---:|---:|---:|---:|
| MathSVG Balanced | 66,766,367 | 1.190387x | 80.750 s | 9.196 s | 26.31 MiB |
| LZ4 | 69,643,519 | 1.141209x | 0.642 s | 0.551 s | 9.54 MiB |
| zstd-default | 65,735,539 | 1.209054x | 1.235 s | 0.565 s | 52.60 MiB |
| gzip-6 | 65,399,882 | 1.215259x | 4.149 s | 1.164 s | 1.99 MiB |
| XZ-9e | 62,312,892 | 1.275464x | 57.141 s | 5.540 s | 617.91 MiB |

Relative to validation v1, MathSVG compression median improved 31.6570%,
decompression median improved 49.6973%, and peak RSS improved 2.7992%. Archive
sizes and every per-input MathSVG archive SHA-256 are identical. This supports
the implementation refactor; it does not change the dominance outcome.

## Dominance result

The certificate passes 0/160 per-file comparisons and 0/4 corpus comparisons.
At corpus level:

- versus gzip-6: size +2.08943%, compression +1,846.31%, decompression
  +689.853%, memory +1,223.48%;
- versus LZ4: MathSVG is smaller, but compression +12,480.9%, decompression
  +1,567.73%, memory +175.973%;
- versus XZ-9e: size +7.14696%, compression +41.3171%, decompression
  +66.0016%, while memory is lower;
- versus zstd-default: size +1.56814%, compression +6,438.98%, decompression
  +1,526.77%, while memory is lower.

The per-file matrix contains 77 confirmed failures and 83 statistically
inconclusive comparisons; none passes all four metrics with a strict win.

## Gate 2 control campaign

`development-validation-literal-safety-v2-structured-generators` measures the complete twelve-domain
random/already-compressed control set under Fast, Balanced and Max, twice per
cell. All 72 rows pass strict inspection, restored SHA-256, archive identity,
and the integer `0.1% + declared literal envelope` bound. Source and immutable
inputs stayed stable; no holdout was opened. Report SHA-256:
`b096d6e2b5e554daba7e1e6e603b7e5fb8bb0c5e1cf1495de7844c04fbf1b312`.

## Structured generator closure and Gate 4

Max and Structured now use a finite deeper function catalogue: period at most
256, recurrence order at most three, coefficients `{0,1,3,253,255}`, a
96,000,000-unit function budget and 2,048 ledger rows. Fast, Balanced and
Repository are unchanged. This admits the exact LFSR8 period and the
quadratic-modulo-256 recurrence `[3,253,1]`.

The frozen `development-procedural-gate-v1` campaign ran 17/17 rows with stable
source and immutable inputs. All six exact generators pass: each has 100%
function coverage, zero literal-leaf bytes and positive pre-entropy gain.
LFSR8, polynomial-d2 and Fibonacci recurrence produce 744-, 496- and 493-byte
archives for their 65,536-byte inputs.

Gate 4 as a whole still fails. Across six complete primary-real domains
(climate NetCDF, PLY, GeoTIFF, UAV JSON, Calgary bitmap and Silesia medical
binary), no winning archive contains function-reconstructed bytes; every
domain has zero pre-entropy procedural gain and zero coverage. The single
failure reason is therefore exact: no real domain simultaneously has positive
gain and at least 50% function coverage. Report SHA-256:
`26d069ea2d14577ac2632d68870c50f46a665cbf19322c34134f763cf9c15a7d`.

## Versioned artifacts

- `results/raw/validation-v2-validation-overhead.jsonl`
- `results/raw/validation-v2-validation-overhead-run.json`
- `results/summary/validation-v2-validation-overhead-benchmark.csv`
- `results/summary/validation-v2-validation-overhead-procedural.csv`
- `results/dominance/validation-v2-validation-overhead/per-file.csv`
- `results/dominance/validation-v2-validation-overhead/per-corpus.csv`
- `results/dominance/validation-v2-validation-overhead/pareto-envelope.csv`
- `results/dominance/validation-v2-validation-overhead/failures.csv`
- `results/profiling/development-validation-literal-safety-v2-structured-generators.json`
- `results/profiling/development-procedural-gate-v1.json`
