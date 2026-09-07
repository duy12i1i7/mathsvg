# MathZip benchmark report

Run ID: `20260724T031043Z-f6e4fbfe`  
Profile: `enwik8`  
Status: `complete`  
Started (UTC): `2026-07-24T03:10:43.983261+00:00`  
Completed (UTC): `2026-07-24T03:22:33.090096+00:00`

This report is generated only from recorded file outputs and trial evidence. It makes no claim for configurations or datasets that failed or were absent.

## Experimental setup

- CPU: Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz
- Logical cores: 16
- Physical cores: 16
- CPU affinity count: 16
- CPU governor(s): unavailable
- RAM bytes: 16,715,317,248
- Swap bytes: 4,054,839,296
- Filesystem: ext4; free bytes 71,844,220,928
- Container image digest: unavailable
- OS: Linux-7.0.0-28-generic-x86_64-with-glibc2.39
- Rust: rustc 1.97.1 (8bab26f4f 2026-07-14)
- Python: 3.12.3 (main, Jun 19 2026, 12:46:00) [GCC 13.3.0]
- Warm-ups: 1
- Measured repeats: 3
- Per-operation timeout: 3600.0 s
- Aggregate: median of successful measured repetitions.
- Every measured round trip is checked against the original SHA-256.

## Weighted corpus results

| Corpus | Codec | Threads | Original bytes | Compressed bytes | Weighted ratio | Bits/byte | Compression MB/s | Decompression MB/s | Peak RSS MiB | Failures |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| enwik8 | 7z-fast | 1 | 100,000,000 | 32,984,096 | 3.032 | 2.639 | 10.835 | 42.459 | 11.262 | 0 |
| enwik8 | brotli-fast | 1 | 100,000,000 | 33,426,985 | 2.992 | 2.674 | 16.940 | 144.445 | 66.641 | 0 |
| enwik8 | bzip2-fast | 1 | 100,000,000 | 33,259,568 | 3.007 | 2.661 | 9.221 | 19.873 | 2.340 | 0 |
| enwik8 | gzip-fast | 1 | 100,000,000 | 42,265,725 | 2.366 | 3.381 | 44.697 | 87.017 | 1.879 | 0 |
| enwik8 | lz4-fast | 1 | 100,000,000 | 57,285,984 | 1.746 | 4.583 | 200.061 | 401.648 | 8.051 | 0 |
| enwik8 | mathzip-fast | 1 | 100,000,000 | 99,095,482 | 1.009 | 7.928 | 1.618 | 45.905 | 672.762 | 0 |
| enwik8 | raw | 1 | 100,000,000 | 100,000,000 | 1.000 | 8.000 | 994.912 | 980.509 | n/a | 0 |
| enwik8 | xz-fast | 1 | 100,000,000 | 33,276,380 | 3.005 | 2.662 | 6.403 | 36.810 | 10.348 | 0 |
| enwik8 | zstd-fast | 1 | 100,000,000 | 40,678,709 | 2.458 | 3.254 | 141.185 | 505.828 | 13.484 | 0 |

Weighted ratio is total original bytes divided by total compressed bytes. Throughput is total original bytes divided by the sum of per-file median times.

## Per-file ratio statistics

| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |
|---|---|---:|---:|---:|---:|---:|
| enwik8 | 7z-fast | 1 | 1 | 3.032 | 3.032 | 3.032 |
| enwik8 | brotli-fast | 1 | 1 | 2.992 | 2.992 | 2.992 |
| enwik8 | bzip2-fast | 1 | 1 | 3.007 | 3.007 | 3.007 |
| enwik8 | gzip-fast | 1 | 1 | 2.366 | 2.366 | 2.366 |
| enwik8 | lz4-fast | 1 | 1 | 1.746 | 1.746 | 1.746 |
| enwik8 | mathzip-fast | 1 | 1 | 1.009 | 1.009 | 1.009 |
| enwik8 | raw | 1 | 1 | 1.000 | 1.000 | 1.000 |
| enwik8 | xz-fast | 1 | 1 | 3.005 | 3.005 | 3.005 |
| enwik8 | zstd-fast | 1 | 1 | 2.458 | 2.458 | 2.458 |

Arithmetic and geometric means are shown separately from the weighted aggregate; they are not substituted for corpus-wide byte totals.

## Dataset provenance

| Corpus | License/terms | Provenance |
|---|---|---|
| enwik8 | Derived from Wikipedia; consult the upstream dump and Wikimedia licensing terms | datasets/manifests/enwik8.json |

## Fixed baseline comparisons

- Against `zstd-fast` on 1 matched file/thread pairs: 0 smaller, 0 equal, 1 larger; median MathZip size gain -143.605% (positive means MathZip produced fewer bytes).
- Against `xz-fast` on 1 matched file/thread pairs: 0 smaller, 0 equal, 1 larger; median MathZip size gain -197.795% (positive means MathZip produced fewer bytes).

## Failures and unavailable measurements

No failed or unavailable rows were recorded.

## Reproducibility evidence

- Result schema: `mathzip-benchmark-results-v1`
- Config SHA-256: `f6e4fbfe08845a692f82f3e880ca7bcf451050dda79bea0608ab305fd7374dea`
- Source revision: `235d190a20e64b6ddc5ce002379809ebbe8fe0cd`
- Source tree dirty: `False`
- Source-tree SHA-256: `0d566a5ec2985f9e6bdc3ece3cdeb897525b487a61c5b409304e3db7306ce05d`
- Source-tree manifest embedded: `false`
- Source stable during run: `true`
- Executable hashes stable during run: `true`
- Available executable hashes complete: `true`
- Input rights/provenance complete: `true`
- Timing protocol compliant: `true`
- Successful rows: 9
- Failed/unavailable rows: 0
- Publication-compliant evidence: `true`
- Publication evidence gaps: none

Exact command lines, compressor versions, per-trial wall/CPU/RSS values, archive SHA-256 values and restored SHA-256 values remain in `results.json`.

## Interpretation limits

- A failed row is not omitted from totals silently; it appears above and is excluded from successful aggregates.
- OS page-cache effects are not eliminated. Fresh output paths prevent reuse of prior compressed results, but do not flush the system page cache.
- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable or underestimate short-lived peaks on other systems.
- Missing MathZip inspection fields remain `n/a`; the report does not infer model, residual or segmentation breakdowns.
