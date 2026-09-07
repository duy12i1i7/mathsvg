# MathZip benchmark report

Run ID: `20260725T055807Z-2e6ce6d4`
Profile: `max-timeout-regression`
Status: `complete`
Started (UTC): `2026-07-25T05:58:07.663340+00:00`
Completed (UTC): `2026-07-25T06:59:45.986775+00:00`

This report is generated only from recorded file outputs and trial evidence. It makes no claim for configurations or datasets that failed or were absent.

## Experimental setup

- CPU: Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz
- Logical cores: 16
- Physical cores: 16
- CPU affinity count: 16
- CPU governor(s): unavailable
- RAM bytes: 16,715,317,248
- Swap bytes: 4,054,839,296
- Filesystem: ext4; free bytes 69,149,745,152
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
| canterbury_max_timeout_regression | mathzip-max | 1 | 1,542,960 | 530,968 | 2.906 | 2.753 | 0.003 | 44.281 | 11.035 | 0 |
| canterbury_max_timeout_regression | zstd-max | 1 | 1,542,960 | 113,190 | 13.632 | 0.587 | 1.306 | 69.861 | 21.082 | 0 |

Weighted ratio is total original bytes divided by total compressed bytes. Throughput is total original bytes divided by the sum of per-file median times.

## Per-file ratio statistics

| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |
|---|---|---:|---:|---:|---:|---:|
| canterbury_max_timeout_regression | mathzip-max | 1 | 2 | 3.749 | 3.495 | 3.749 |
| canterbury_max_timeout_regression | zstd-max | 1 | 2 | 13.288 | 13.206 | 13.288 |

Arithmetic and geometric means are shown separately from the weighted aggregate; they are not substituted for corpus-wide byte totals.

## Dataset provenance

| Corpus | License/terms | Provenance |
|---|---|---|
| canterbury_max_timeout_regression | Upstream files retain their original terms; see dataset manifest | datasets/manifests/canterbury.json |

## Baseline comparisons

- `mathzip-max` against `zstd-max` on 2 matched file/thread pairs: 0 smaller, 0 equal, 2 larger; median MathZip size gain -324.304% (positive means MathZip produced fewer bytes).
- No matched successful rows for `mathzip-max` against `xz-max`.

## Failures and unavailable measurements

No failed or unavailable rows were recorded.

## Reproducibility evidence

- Result schema: `mathzip-benchmark-results-v1`
- Config SHA-256: `2e6ce6d4196d632269e08d471fdc486273d054af6304912af45cf43cb5d3b8fd`
- Source revision: `e6ce84274a45b5a6e4738844921f9b374439c643`
- Source tree dirty: `False`
- Source-tree SHA-256: `e0f67fc713a0684287f72014b3c539c2d369324137d1fc77367b08bb27b5516e`
- Source-tree manifest embedded: `false`
- Source stable during run: `true`
- Executable hashes stable during run: `true`
- Available executable hashes complete: `true`
- Input rights/provenance complete: `true`
- Timing protocol compliant: `true`
- Successful rows: 4
- Failed/unavailable rows: 0
- Publication-compliant evidence: `true`
- Publication evidence gaps: none

Exact command lines, compressor versions, per-trial wall/CPU/RSS values, archive SHA-256 values and restored SHA-256 values remain in `results.json`.

## Interpretation limits

- A failed row is not omitted from totals silently; it appears above and is excluded from successful aggregates.
- OS page-cache effects are not eliminated. Fresh output paths prevent reuse of prior compressed results, but do not flush the system page cache.
- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable or underestimate short-lived peaks on other systems.
- Missing MathZip inspection fields remain `n/a`; the report does not infer model, residual or segmentation breakdowns.
