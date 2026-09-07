# MathZip benchmark report

Run ID: `20260724T143401Z-3bb520a6`
Profile: `residual-ablation`
Status: `complete`
Started (UTC): `2026-07-24T14:34:01.398869+00:00`
Completed (UTC): `2026-07-24T15:35:54.228802+00:00`

This report is generated only from recorded file outputs and trial evidence. It makes no claim for configurations or datasets that failed or were absent.

## Experimental setup

- CPU: Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz
- Logical cores: 16
- Physical cores: 16
- CPU affinity count: 16
- CPU governor(s): unavailable
- RAM bytes: 16,715,317,248
- Swap bytes: 4,054,839,296
- Filesystem: ext4; free bytes 70,764,261,376
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
| canterbury_ablation | mathzip-balanced-custom-residual | 1 | 1,733,289 | 955,990 | 1.813 | 4.412 | 0.007 | 33.429 | 14.559 | 0 |
| canterbury_ablation | mathzip-balanced-zstd-residual | 1 | 1,733,289 | 232,585 | 7.452 | 1.073 | 0.008 | 40.433 | 13.234 | 0 |
| canterbury_ablation | zstd-default | 1 | 1,733,289 | 236,574 | 7.327 | 1.092 | 36.903 | 54.429 | 5.160 | 0 |
| synthetic_ablation | mathzip-balanced-custom-residual | 1 | 524,288 | 89,839 | 5.836 | 1.371 | 0.010 | 11.532 | 4.266 | 0 |
| synthetic_ablation | mathzip-balanced-zstd-residual | 1 | 524,288 | 89,482 | 5.859 | 1.365 | 0.016 | 11.093 | 4.855 | 0 |
| synthetic_ablation | zstd-default | 1 | 524,288 | 91,719 | 5.716 | 1.400 | 10.872 | 11.549 | 0.367 | 0 |

Weighted ratio is total original bytes divided by total compressed bytes. Throughput is total original bytes divided by the sum of per-file median times.

## Per-file ratio statistics

| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |
|---|---|---:|---:|---:|---:|---:|
| canterbury_ablation | mathzip-balanced-custom-residual | 1 | 4 | 2.277 | 1.850 | 1.379 |
| canterbury_ablation | mathzip-balanced-zstd-residual | 1 | 4 | 6.194 | 5.098 | 6.165 |
| canterbury_ablation | zstd-default | 1 | 4 | 6.042 | 5.073 | 6.037 |
| synthetic_ablation | mathzip-balanced-custom-residual | 1 | 8 | 205.360 | 58.726 | 316.666 |
| synthetic_ablation | mathzip-balanced-zstd-residual | 1 | 8 | 175.882 | 52.698 | 271.938 |
| synthetic_ablation | zstd-default | 1 | 8 | 703.621 | 78.941 | 196.729 |

Arithmetic and geometric means are shown separately from the weighted aggregate; they are not substituted for corpus-wide byte totals.

## Dataset provenance

| Corpus | License/terms | Provenance |
|---|---|---|
| canterbury_ablation | Upstream files retain their original terms; see dataset manifest | datasets/manifests/canterbury.json |
| synthetic_ablation | CC0-1.0 | python/mathzip_bench/synthetic.py and datasets/synthetic/manifest.json |

## Fixed baseline comparisons

No successful MathZip rows were available for fixed comparisons.

## Failures and unavailable measurements

No failed or unavailable rows were recorded.

## Reproducibility evidence

- Result schema: `mathzip-benchmark-results-v1`
- Config SHA-256: `3bb520a6fad8f21275fe9f61e8b27a3c5922978e9da9d14caba218513d19b185`
- Source revision: `a130e30389cad1efe6db3ffad7764022a446531e`
- Source tree dirty: `False`
- Source-tree SHA-256: `6e0ac49416c7a7d9db4d492df6fa3a125c8ea1556fd7f053580499a9cc85e7dd`
- Source-tree manifest embedded: `false`
- Source stable during run: `true`
- Executable hashes stable during run: `true`
- Available executable hashes complete: `true`
- Input rights/provenance complete: `true`
- Timing protocol compliant: `true`
- Successful rows: 36
- Failed/unavailable rows: 0
- Publication-compliant evidence: `true`
- Publication evidence gaps: none

Exact command lines, compressor versions, per-trial wall/CPU/RSS values, archive SHA-256 values and restored SHA-256 values remain in `results.json`.

## Interpretation limits

- A failed row is not omitted from totals silently; it appears above and is excluded from successful aggregates.
- OS page-cache effects are not eliminated. Fresh output paths prevent reuse of prior compressed results, but do not flush the system page cache.
- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable or underestimate short-lived peaks on other systems.
- Missing MathZip inspection fields remain `n/a`; the report does not infer model, residual or segmentation breakdowns.
