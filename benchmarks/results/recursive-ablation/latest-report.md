# MathZip benchmark report

Run ID: `20260724T161541Z-70dfeff6`
Profile: `recursive-ablation`
Status: `complete`
Started (UTC): `2026-07-24T16:15:41.545672+00:00`
Completed (UTC): `2026-07-24T16:16:04.978349+00:00`

This report is generated only from recorded file outputs and trial evidence. It makes no claim for configurations or datasets that failed or were absent.

## Experimental setup

- CPU: Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz
- Logical cores: 16
- Physical cores: 16
- CPU affinity count: 16
- CPU governor(s): unavailable
- RAM bytes: 16,715,317,248
- Swap bytes: 4,054,839,296
- Filesystem: ext4; free bytes 70,643,892,224
- Container image digest: unavailable
- OS: Linux-7.0.0-28-generic-x86_64-with-glibc2.39
- Rust: rustc 1.97.1 (8bab26f4f 2026-07-14)
- Python: 3.12.3 (main, Jun 19 2026, 12:46:00) [GCC 13.3.0]
- Warm-ups: 1
- Measured repeats: 3
- Per-operation timeout: 600.0 s
- Aggregate: median of successful measured repetitions.
- Every measured round trip is checked against the original SHA-256.

## Weighted corpus results

| Corpus | Codec | Threads | Original bytes | Compressed bytes | Weighted ratio | Bits/byte | Compression MB/s | Decompression MB/s | Peak RSS MiB | Failures |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| canterbury_recursive | mathzip-fast-adaptive | 1 | 190,329 | 166,308 | 1.144 | 6.990 | 0.489 | 13.668 | 4.211 | 0 |
| canterbury_recursive | mathzip-fast-recursive | 1 | 190,329 | 166,272 | 1.145 | 6.989 | 0.218 | 12.823 | 4.184 | 0 |
| canterbury_recursive | zstd-fast | 1 | 190,329 | 73,285 | 2.597 | 3.080 | 17.147 | 14.994 | 0.344 | 0 |
| synthetic_recursive | mathzip-fast-adaptive | 1 | 524,288 | 99,843 | 5.251 | 1.523 | 0.846 | 12.807 | 3.645 | 0 |
| synthetic_recursive | mathzip-fast-recursive | 1 | 524,288 | 99,639 | 5.262 | 1.520 | 0.504 | 11.038 | 3.629 | 0 |
| synthetic_recursive | zstd-fast | 1 | 524,288 | 92,448 | 5.671 | 1.411 | 12.616 | 12.736 | 0.387 | 0 |

Weighted ratio is total original bytes divided by total compressed bytes. Throughput is total original bytes divided by the sum of per-file median times.

## Per-file ratio statistics

| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |
|---|---|---:|---:|---:|---:|---:|
| canterbury_recursive | mathzip-fast-adaptive | 1 | 2 | 1.151 | 1.151 | 1.151 |
| canterbury_recursive | mathzip-fast-recursive | 1 | 2 | 1.150 | 1.150 | 1.150 |
| canterbury_recursive | zstd-fast | 1 | 2 | 2.678 | 2.675 | 2.678 |
| synthetic_recursive | mathzip-fast-adaptive | 1 | 8 | 204.769 | 55.262 | 316.666 |
| synthetic_recursive | mathzip-fast-recursive | 1 | 8 | 255.522 | 63.570 | 398.399 |
| synthetic_recursive | zstd-fast | 1 | 8 | 703.578 | 78.337 | 196.729 |

Arithmetic and geometric means are shown separately from the weighted aggregate; they are not substituted for corpus-wide byte totals.

## Dataset provenance

| Corpus | License/terms | Provenance |
|---|---|---|
| canterbury_recursive | Upstream files retain their original terms; see dataset manifest | datasets/manifests/canterbury.json |
| synthetic_recursive | CC0-1.0 | python/mathzip_bench/synthetic.py and datasets/synthetic/manifest.json |

## Fixed baseline comparisons

No successful MathZip rows were available for fixed comparisons.

## Failures and unavailable measurements

No failed or unavailable rows were recorded.

## Reproducibility evidence

- Result schema: `mathzip-benchmark-results-v1`
- Config SHA-256: `70dfeff692526283285f1df3e0da9c5c132b9a6f13053a0c492447e1b05f45d8`
- Source revision: `aac547e57647b0135c555c0b59a5b0c01db476ea`
- Source tree dirty: `False`
- Source-tree SHA-256: `0164eab3f424d99e9976ecfe9b1f5ceeff2c8fea88b62624c7b4adb3ad8bf33f`
- Source-tree manifest embedded: `false`
- Source stable during run: `true`
- Executable hashes stable during run: `true`
- Available executable hashes complete: `true`
- Input rights/provenance complete: `true`
- Timing protocol compliant: `true`
- Successful rows: 30
- Failed/unavailable rows: 0
- Publication-compliant evidence: `true`
- Publication evidence gaps: none

Exact command lines, compressor versions, per-trial wall/CPU/RSS values, archive SHA-256 values and restored SHA-256 values remain in `results.json`.

## Interpretation limits

- A failed row is not omitted from totals silently; it appears above and is excluded from successful aggregates.
- OS page-cache effects are not eliminated. Fresh output paths prevent reuse of prior compressed results, but do not flush the system page cache.
- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable or underestimate short-lived peaks on other systems.
- Missing MathZip inspection fields remain `n/a`; the report does not infer model, residual or segmentation breakdowns.
