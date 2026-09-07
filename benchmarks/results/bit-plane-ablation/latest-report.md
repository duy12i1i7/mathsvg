# MathZip benchmark report

Run ID: `20260724T174812Z-583dbb61`
Profile: `bit-plane-ablation`
Status: `complete`
Started (UTC): `2026-07-24T17:48:12.420544+00:00`
Completed (UTC): `2026-07-24T17:49:05.921204+00:00`

This report is generated only from recorded file outputs and trial evidence. It makes no claim for configurations or datasets that failed or were absent.

## Experimental setup

- CPU: Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz
- Logical cores: 16
- Physical cores: 16
- CPU affinity count: 16
- CPU governor(s): unavailable
- RAM bytes: 16,715,317,248
- Swap bytes: 4,054,839,296
- Filesystem: ext4; free bytes 69,958,103,040
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
| synthetic_bit_plane_ablation | mathzip-balanced-bit-plane-independent-v2 | 1 | 589,979 | 152,803 | 3.861 | 2.072 | 0.800 | 6.797 | 3.637 | 0 |
| synthetic_bit_plane_ablation | mathzip-balanced-bit-plane-packed-v1 | 1 | 589,979 | 148,473 | 3.974 | 2.013 | 0.093 | 6.663 | 4.078 | 0 |
| synthetic_bit_plane_ablation | zstd-default | 1 | 589,979 | 81,901 | 7.204 | 1.111 | 8.014 | 8.202 | 1.334 | 0 |

Weighted ratio is total original bytes divided by total compressed bytes. Throughput is total original bytes divided by the sum of per-file median times.

## Per-file ratio statistics

| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |
|---|---|---:|---:|---:|---:|---:|
| synthetic_bit_plane_ablation | mathzip-balanced-bit-plane-independent-v2 | 1 | 14 | 55.828 | 3.339 | 1.793 |
| synthetic_bit_plane_ablation | mathzip-balanced-bit-plane-packed-v1 | 1 | 14 | 75.884 | 5.114 | 1.901 |
| synthetic_bit_plane_ablation | zstd-default | 1 | 14 | 477.992 | 24.531 | 82.333 |

Arithmetic and geometric means are shown separately from the weighted aggregate; they are not substituted for corpus-wide byte totals.

## Dataset provenance

| Corpus | License/terms | Provenance |
|---|---|---|
| synthetic_bit_plane_ablation | CC0-1.0 | python/mathzip_bench/synthetic.py and datasets/synthetic/manifest.json |

## Fixed baseline comparisons

No successful MathZip rows were available for fixed comparisons.

## Failures and unavailable measurements

No failed or unavailable rows were recorded.

## Reproducibility evidence

- Result schema: `mathzip-benchmark-results-v1`
- Config SHA-256: `583dbb61df0f6cf0fd1813c81eb468b97ebee334070acdafa6a9f0b15df78ec7`
- Source revision: `1703b73f3ef51753ed1f92ea5e2ce28da9bcdf22`
- Source tree dirty: `False`
- Source-tree SHA-256: `612a5cf7d57a2607bfc8c297bf9bf08214ac25e065ea4d9a08ee881ddac733ac`
- Source-tree manifest embedded: `false`
- Source stable during run: `true`
- Executable hashes stable during run: `true`
- Available executable hashes complete: `true`
- Input rights/provenance complete: `true`
- Timing protocol compliant: `true`
- Successful rows: 42
- Failed/unavailable rows: 0
- Publication-compliant evidence: `true`
- Publication evidence gaps: none

Exact command lines, compressor versions, per-trial wall/CPU/RSS values, archive SHA-256 values and restored SHA-256 values remain in `results.json`.

## Interpretation limits

- A failed row is not omitted from totals silently; it appears above and is excluded from successful aggregates.
- OS page-cache effects are not eliminated. Fresh output paths prevent reuse of prior compressed results, but do not flush the system page cache.
- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable or underestimate short-lived peaks on other systems.
- Missing MathZip inspection fields remain `n/a`; the report does not infer model, residual or segmentation breakdowns.
