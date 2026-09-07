# MathZip benchmark report

Run ID: `20260725T051723Z-a5f0fdfc`
Profile: `segmentation-ablation`
Status: `complete`
Started (UTC): `2026-07-25T05:17:23.433656+00:00`
Completed (UTC): `2026-07-25T05:18:06.432618+00:00`

This report is generated only from recorded file outputs and trial evidence. It makes no claim for configurations or datasets that failed or were absent.

## Experimental setup

- CPU: Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz
- Logical cores: 16
- Physical cores: 16
- CPU affinity count: 16
- CPU governor(s): unavailable
- RAM bytes: 16,715,317,248
- Swap bytes: 4,054,839,296
- Filesystem: ext4; free bytes 69,900,279,808
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
| canterbury_segmentation | mathzip-fast-adaptive-4kib | 1 | 190,329 | 164,995 | 1.154 | 6.935 | 0.401 | 13.301 | 4.352 | 0 |
| canterbury_segmentation | mathzip-fast-change-point-4kib | 1 | 190,329 | 164,513 | 1.157 | 6.915 | 0.576 | 13.066 | 4.223 | 0 |
| canterbury_segmentation | mathzip-fast-fixed-16kib | 1 | 190,329 | 168,429 | 1.130 | 7.079 | 1.238 | 12.241 | 4.230 | 0 |
| canterbury_segmentation | mathzip-fast-fixed-1kib | 1 | 190,329 | 170,555 | 1.116 | 7.169 | 1.081 | 13.319 | 4.281 | 0 |
| canterbury_segmentation | mathzip-fast-fixed-256b | 1 | 190,329 | 187,028 | 1.018 | 7.861 | 0.844 | 14.770 | 4.320 | 0 |
| canterbury_segmentation | mathzip-fast-fixed-256kib | 1 | 190,329 | 168,020 | 1.133 | 7.062 | 1.256 | 12.704 | 4.223 | 0 |
| canterbury_segmentation | mathzip-fast-fixed-4kib | 1 | 190,329 | 166,945 | 1.140 | 7.017 | 1.165 | 12.036 | 4.344 | 0 |
| canterbury_segmentation | mathzip-fast-fixed-64kib | 1 | 190,329 | 168,094 | 1.132 | 7.065 | 1.250 | 12.052 | 4.031 | 0 |
| canterbury_segmentation | zstd-fast | 1 | 190,329 | 73,285 | 2.597 | 3.080 | 16.776 | 17.405 | 0.344 | 0 |
| synthetic_segmentation | mathzip-fast-adaptive-4kib | 1 | 524,288 | 99,310 | 5.279 | 1.515 | 0.631 | 12.245 | 3.625 | 0 |
| synthetic_segmentation | mathzip-fast-change-point-4kib | 1 | 524,288 | 97,499 | 5.377 | 1.488 | 1.160 | 11.870 | 3.688 | 0 |
| synthetic_segmentation | mathzip-fast-fixed-16kib | 1 | 524,288 | 108,715 | 4.823 | 1.659 | 1.806 | 12.371 | 3.613 | 0 |
| synthetic_segmentation | mathzip-fast-fixed-1kib | 1 | 524,288 | 115,567 | 4.537 | 1.763 | 1.591 | 12.006 | 3.648 | 0 |
| synthetic_segmentation | mathzip-fast-fixed-256b | 1 | 524,288 | 168,277 | 3.116 | 2.568 | 1.138 | 11.723 | 3.703 | 0 |
| synthetic_segmentation | mathzip-fast-fixed-256kib | 1 | 524,288 | 118,472 | 4.425 | 1.808 | 1.777 | 12.123 | 3.727 | 0 |
| synthetic_segmentation | mathzip-fast-fixed-4kib | 1 | 524,288 | 104,210 | 5.031 | 1.590 | 1.772 | 12.703 | 3.594 | 0 |
| synthetic_segmentation | mathzip-fast-fixed-64kib | 1 | 524,288 | 118,472 | 4.425 | 1.808 | 1.786 | 12.168 | 3.695 | 0 |
| synthetic_segmentation | zstd-fast | 1 | 524,288 | 92,448 | 5.671 | 1.411 | 12.415 | 11.756 | 0.371 | 0 |

Weighted ratio is total original bytes divided by total compressed bytes. Throughput is total original bytes divided by the sum of per-file median times.

## Per-file ratio statistics

| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |
|---|---|---:|---:|---:|---:|---:|
| canterbury_segmentation | mathzip-fast-adaptive-4kib | 1 | 2 | 1.179 | 1.178 | 1.179 |
| canterbury_segmentation | mathzip-fast-change-point-4kib | 1 | 2 | 1.179 | 1.178 | 1.179 |
| canterbury_segmentation | mathzip-fast-fixed-16kib | 1 | 2 | 1.118 | 1.118 | 1.118 |
| canterbury_segmentation | mathzip-fast-fixed-1kib | 1 | 2 | 1.143 | 1.142 | 1.143 |
| canterbury_segmentation | mathzip-fast-fixed-256b | 1 | 2 | 1.049 | 1.048 | 1.049 |
| canterbury_segmentation | mathzip-fast-fixed-256kib | 1 | 2 | 1.120 | 1.120 | 1.120 |
| canterbury_segmentation | mathzip-fast-fixed-4kib | 1 | 2 | 1.155 | 1.155 | 1.155 |
| canterbury_segmentation | mathzip-fast-fixed-64kib | 1 | 2 | 1.120 | 1.120 | 1.120 |
| canterbury_segmentation | zstd-fast | 1 | 2 | 2.678 | 2.675 | 2.678 |
| synthetic_segmentation | mathzip-fast-adaptive-4kib | 1 | 8 | 94.374 | 34.018 | 140.126 |
| synthetic_segmentation | mathzip-fast-change-point-4kib | 1 | 8 | 255.550 | 64.245 | 398.399 |
| synthetic_segmentation | mathzip-fast-fixed-16kib | 1 | 8 | 147.032 | 43.259 | 223.004 |
| synthetic_segmentation | mathzip-fast-fixed-1kib | 1 | 8 | 16.547 | 10.449 | 22.592 |
| synthetic_segmentation | mathzip-fast-fixed-256b | 1 | 8 | 4.804 | 4.069 | 5.829 |
| synthetic_segmentation | mathzip-fast-fixed-256kib | 1 | 8 | 255.393 | 59.435 | 398.399 |
| synthetic_segmentation | mathzip-fast-fixed-4kib | 1 | 8 | 55.217 | 23.494 | 80.383 |
| synthetic_segmentation | mathzip-fast-fixed-64kib | 1 | 8 | 255.393 | 59.435 | 398.399 |
| synthetic_segmentation | zstd-fast | 1 | 8 | 703.578 | 78.337 | 196.729 |

Arithmetic and geometric means are shown separately from the weighted aggregate; they are not substituted for corpus-wide byte totals.

## Dataset provenance

| Corpus | License/terms | Provenance |
|---|---|---|
| canterbury_segmentation | Upstream files retain their original terms; see dataset manifest | datasets/manifests/canterbury.json |
| synthetic_segmentation | CC0-1.0 | python/mathzip_bench/synthetic.py and datasets/synthetic/manifest.json |

## Baseline comparisons

- `mathzip-fast-adaptive-4kib` against `zstd-fast` on 10 matched file/thread pairs: 1 smaller, 0 equal, 9 larger; median MathZip size gain -58.591% (positive means MathZip produced fewer bytes).
- No matched successful rows for `mathzip-fast-adaptive-4kib` against `xz-fast`.
- `mathzip-fast-change-point-4kib` against `zstd-fast` on 10 matched file/thread pairs: 4 smaller, 0 equal, 6 larger; median MathZip size gain -30.708% (positive means MathZip produced fewer bytes).
- No matched successful rows for `mathzip-fast-change-point-4kib` against `xz-fast`.
- `mathzip-fast-fixed-16kib` against `zstd-fast` on 10 matched file/thread pairs: 4 smaller, 0 equal, 6 larger; median MathZip size gain -62.047% (positive means MathZip produced fewer bytes).
- No matched successful rows for `mathzip-fast-fixed-16kib` against `xz-fast`.
- `mathzip-fast-fixed-1kib` against `zstd-fast` on 10 matched file/thread pairs: 1 smaller, 0 equal, 9 larger; median MathZip size gain -361.586% (positive means MathZip produced fewer bytes).
- No matched successful rows for `mathzip-fast-fixed-1kib` against `xz-fast`.
- `mathzip-fast-fixed-256b` against `zstd-fast` on 10 matched file/thread pairs: 0 smaller, 0 equal, 10 larger; median MathZip size gain -1356.749% (positive means MathZip produced fewer bytes).
- No matched successful rows for `mathzip-fast-fixed-256b` against `xz-fast`.
- `mathzip-fast-fixed-256kib` against `zstd-fast` on 10 matched file/thread pairs: 4 smaller, 0 equal, 6 larger; median MathZip size gain -61.768% (positive means MathZip produced fewer bytes).
- No matched successful rows for `mathzip-fast-fixed-256kib` against `xz-fast`.
- `mathzip-fast-fixed-4kib` against `zstd-fast` on 10 matched file/thread pairs: 1 smaller, 0 equal, 9 larger; median MathZip size gain -131.731% (positive means MathZip produced fewer bytes).
- No matched successful rows for `mathzip-fast-fixed-4kib` against `xz-fast`.
- `mathzip-fast-fixed-64kib` against `zstd-fast` on 10 matched file/thread pairs: 4 smaller, 0 equal, 6 larger; median MathZip size gain -61.830% (positive means MathZip produced fewer bytes).
- No matched successful rows for `mathzip-fast-fixed-64kib` against `xz-fast`.

## Failures and unavailable measurements

No failed or unavailable rows were recorded.

## Reproducibility evidence

- Result schema: `mathzip-benchmark-results-v1`
- Config SHA-256: `a5f0fdfc3a33567e2fe39dd854b897aef5646af8b6c2440dc7f809f29b47efbb`
- Source revision: `3c864d915b0d2452da712cd691796a4bdf987876`
- Source tree dirty: `False`
- Source-tree SHA-256: `9c9501edf2a575addc6f45ae5b5fce31352031a79d412d6993674c994d83991d`
- Source-tree manifest embedded: `false`
- Source stable during run: `true`
- Executable hashes stable during run: `true`
- Available executable hashes complete: `true`
- Input rights/provenance complete: `true`
- Timing protocol compliant: `true`
- Successful rows: 90
- Failed/unavailable rows: 0
- Publication-compliant evidence: `true`
- Publication evidence gaps: none

Exact command lines, compressor versions, per-trial wall/CPU/RSS values, archive SHA-256 values and restored SHA-256 values remain in `results.json`.

## Interpretation limits

- A failed row is not omitted from totals silently; it appears above and is excluded from successful aggregates.
- OS page-cache effects are not eliminated. Fresh output paths prevent reuse of prior compressed results, but do not flush the system page cache.
- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable or underestimate short-lived peaks on other systems.
- Missing MathZip inspection fields remain `n/a`; the report does not infer model, residual or segmentation breakdowns.
