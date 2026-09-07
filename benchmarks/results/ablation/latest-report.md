# MathZip benchmark report

Run ID: `20260725T170925Z-dde3921f`
Profile: `ablation`
Status: `complete`
Started (UTC): `2026-07-25T17:09:25.133684+00:00`
Completed (UTC): `2026-07-25T19:44:53.096903+00:00`

This report is generated only from recorded file outputs and trial evidence. It makes no claim for configurations or datasets that failed or were absent.

## Experimental setup

- CPU: Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz
- Logical cores: 16
- Physical cores: 16
- CPU affinity count: 16
- CPU governor(s): unavailable
- RAM bytes: 16,715,317,248
- Swap bytes: 4,054,839,296
- Filesystem: ext4; free bytes 68,550,508,544
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
| canterbury_ablation | ablation-01-raw-only | 1 | 1,733,289 | 1,733,913 | 1.000 | 8.003 | 16.873 | 30.390 | 10.191 | 0 |
| canterbury_ablation | ablation-02-residual-only | 1 | 1,733,289 | 1,301,540 | 1.332 | 6.007 | 15.205 | 28.871 | 9.199 | 0 |
| canterbury_ablation | ablation-03-fixed-constant | 1 | 1,733,289 | 1,301,540 | 1.332 | 6.007 | 11.805 | 31.653 | 10.152 | 0 |
| canterbury_ablation | ablation-04-fixed-all-models | 1 | 1,733,289 | 1,088,178 | 1.593 | 5.022 | 2.048 | 28.977 | 9.293 | 0 |
| canterbury_ablation | ablation-05-adaptive-constant | 1 | 1,733,289 | 1,295,172 | 1.338 | 5.978 | 0.943 | 28.493 | 13.504 | 0 |
| canterbury_ablation | ablation-06-adaptive-linear | 1 | 1,733,289 | 1,295,172 | 1.338 | 5.978 | 0.478 | 27.626 | 14.039 | 0 |
| canterbury_ablation | ablation-07-add-polynomial | 1 | 1,733,289 | 1,295,172 | 1.338 | 5.978 | 0.279 | 29.487 | 14.020 | 0 |
| canterbury_ablation | ablation-08-add-periodic | 1 | 1,733,289 | 1,211,218 | 1.431 | 5.590 | 0.145 | 27.821 | 10.184 | 0 |
| canterbury_ablation | ablation-09-add-recurrence | 1 | 1,733,289 | 1,210,315 | 1.432 | 5.586 | 0.096 | 24.503 | 10.211 | 0 |
| canterbury_ablation | ablation-10-add-bit-plane | 1 | 1,733,289 | 1,139,345 | 1.521 | 5.259 | 0.032 | 24.306 | 13.160 | 0 |
| canterbury_ablation | ablation-11-add-stride | 1 | 1,733,289 | 1,137,938 | 1.523 | 5.252 | 0.012 | 23.084 | 16.582 | 0 |
| canterbury_ablation | ablation-12-add-copy | 1 | 1,733,289 | 955,679 | 1.814 | 4.411 | 0.011 | 31.791 | 14.543 | 0 |
| canterbury_ablation | ablation-13-custom-residual | 1 | 1,733,289 | 955,679 | 1.814 | 4.411 | 0.007 | 30.427 | 14.547 | 0 |
| canterbury_ablation | ablation-14-zstd-residual | 1 | 1,733,289 | 234,866 | 7.380 | 1.084 | 0.008 | 35.813 | 13.414 | 0 |
| canterbury_ablation | ablation-15-no-transform | 1 | 1,733,289 | 963,623 | 1.799 | 4.448 | 0.075 | 29.219 | 9.949 | 0 |
| canterbury_ablation | ablation-16-no-adaptive-partition | 1 | 1,733,289 | 1,060,816 | 1.634 | 4.896 | 0.215 | 27.888 | 10.527 | 0 |
| canterbury_ablation | ablation-17-no-raw-fallback | 1 | 1,733,289 | 955,679 | 1.814 | 4.411 | 0.007 | 34.195 | 14.613 | 0 |
| canterbury_ablation | ablation-18-fast | 1 | 1,733,289 | 1,288,373 | 1.345 | 5.946 | 1.998 | 30.160 | 10.219 | 0 |
| canterbury_ablation | ablation-19-balanced | 1 | 1,733,289 | 955,679 | 1.814 | 4.411 | 0.007 | 32.664 | 14.301 | 0 |
| canterbury_ablation | ablation-20-max | 1 | 1,733,289 | 688,088 | 2.519 | 3.176 | 0.003 | 28.796 | 11.031 | 0 |
| canterbury_ablation | zstd-default | 1 | 1,733,289 | 236,574 | 7.327 | 1.092 | 29.704 | 50.451 | 5.137 | 0 |
| synthetic_ablation | ablation-01-raw-only | 1 | 524,288 | 525,536 | 0.998 | 8.019 | 7.721 | 10.640 | 0.508 | 0 |
| synthetic_ablation | ablation-02-residual-only | 1 | 524,288 | 428,024 | 1.225 | 6.531 | 7.250 | 10.731 | 2.234 | 0 |
| synthetic_ablation | ablation-03-fixed-constant | 1 | 524,288 | 426,468 | 1.229 | 6.507 | 6.971 | 11.030 | 3.652 | 0 |
| synthetic_ablation | ablation-04-fixed-all-models | 1 | 524,288 | 96,441 | 5.436 | 1.472 | 1.966 | 12.973 | 3.633 | 0 |
| synthetic_ablation | ablation-05-adaptive-constant | 1 | 524,288 | 424,998 | 1.234 | 6.485 | 1.197 | 10.955 | 3.926 | 0 |
| synthetic_ablation | ablation-06-adaptive-linear | 1 | 524,288 | 290,255 | 1.806 | 4.429 | 0.668 | 10.189 | 3.957 | 0 |
| synthetic_ablation | ablation-07-add-polynomial | 1 | 524,288 | 216,963 | 2.416 | 3.311 | 0.396 | 11.453 | 3.926 | 0 |
| synthetic_ablation | ablation-08-add-periodic | 1 | 524,288 | 158,539 | 3.307 | 2.419 | 0.212 | 11.993 | 4.191 | 0 |
| synthetic_ablation | ablation-09-add-recurrence | 1 | 524,288 | 89,840 | 5.836 | 1.371 | 0.140 | 11.052 | 3.934 | 0 |
| synthetic_ablation | ablation-10-add-bit-plane | 1 | 524,288 | 89,840 | 5.836 | 1.371 | 0.062 | 10.962 | 4.016 | 0 |
| synthetic_ablation | ablation-11-add-stride | 1 | 524,288 | 89,840 | 5.836 | 1.371 | 0.018 | 10.770 | 4.000 | 0 |
| synthetic_ablation | ablation-12-add-copy | 1 | 524,288 | 89,839 | 5.836 | 1.371 | 0.018 | 12.048 | 4.012 | 0 |
| synthetic_ablation | ablation-13-custom-residual | 1 | 524,288 | 88,937 | 5.895 | 1.357 | 0.012 | 11.098 | 4.469 | 0 |
| synthetic_ablation | ablation-14-zstd-residual | 1 | 524,288 | 89,482 | 5.859 | 1.365 | 0.018 | 11.711 | 4.836 | 0 |
| synthetic_ablation | ablation-15-no-transform | 1 | 524,288 | 89,839 | 5.836 | 1.371 | 0.111 | 10.248 | 4.301 | 0 |
| synthetic_ablation | ablation-16-no-adaptive-partition | 1 | 524,288 | 95,624 | 5.483 | 1.459 | 0.241 | 11.973 | 3.723 | 0 |
| synthetic_ablation | ablation-17-no-raw-fallback | 1 | 524,288 | 88,973 | 5.893 | 1.358 | 0.012 | 11.090 | 4.461 | 0 |
| synthetic_ablation | ablation-18-fast | 1 | 524,288 | 108,715 | 4.823 | 1.659 | 1.931 | 13.099 | 3.730 | 0 |
| synthetic_ablation | ablation-19-balanced | 1 | 524,288 | 88,937 | 5.895 | 1.357 | 0.012 | 11.201 | 4.402 | 0 |
| synthetic_ablation | ablation-20-max | 1 | 524,288 | 88,751 | 5.907 | 1.354 | 0.003 | 11.183 | 4.277 | 0 |
| synthetic_ablation | zstd-default | 1 | 524,288 | 91,719 | 5.716 | 1.400 | 10.099 | 12.478 | 0.344 | 0 |

Weighted ratio is total original bytes divided by total compressed bytes. Throughput is total original bytes divided by the sum of per-file median times.

## Per-file ratio statistics

| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |
|---|---|---:|---:|---:|---:|---:|
| canterbury_ablation | ablation-01-raw-only | 1 | 4 | 0.999 | 0.999 | 0.999 |
| canterbury_ablation | ablation-02-residual-only | 1 | 4 | 2.054 | 1.598 | 1.152 |
| canterbury_ablation | ablation-03-fixed-constant | 1 | 4 | 2.054 | 1.598 | 1.152 |
| canterbury_ablation | ablation-04-fixed-all-models | 1 | 4 | 2.134 | 1.699 | 1.219 |
| canterbury_ablation | ablation-05-adaptive-constant | 1 | 4 | 2.114 | 1.630 | 1.176 |
| canterbury_ablation | ablation-06-adaptive-linear | 1 | 4 | 2.114 | 1.630 | 1.176 |
| canterbury_ablation | ablation-07-add-polynomial | 1 | 4 | 2.114 | 1.630 | 1.176 |
| canterbury_ablation | ablation-08-add-periodic | 1 | 4 | 2.141 | 1.671 | 1.186 |
| canterbury_ablation | ablation-09-add-recurrence | 1 | 4 | 2.152 | 1.675 | 1.186 |
| canterbury_ablation | ablation-10-add-bit-plane | 1 | 4 | 2.187 | 1.726 | 1.216 |
| canterbury_ablation | ablation-11-add-stride | 1 | 4 | 2.201 | 1.746 | 1.245 |
| canterbury_ablation | ablation-12-add-copy | 1 | 4 | 2.280 | 1.851 | 1.379 |
| canterbury_ablation | ablation-13-custom-residual | 1 | 4 | 2.280 | 1.851 | 1.379 |
| canterbury_ablation | ablation-14-zstd-residual | 1 | 4 | 6.103 | 5.004 | 5.989 |
| canterbury_ablation | ablation-15-no-transform | 1 | 4 | 2.251 | 1.807 | 1.350 |
| canterbury_ablation | ablation-16-no-adaptive-partition | 1 | 4 | 2.157 | 1.733 | 1.241 |
| canterbury_ablation | ablation-17-no-raw-fallback | 1 | 4 | 2.280 | 1.851 | 1.379 |
| canterbury_ablation | ablation-18-fast | 1 | 4 | 2.073 | 1.589 | 1.118 |
| canterbury_ablation | ablation-19-balanced | 1 | 4 | 2.280 | 1.851 | 1.379 |
| canterbury_ablation | ablation-20-max | 1 | 4 | 2.500 | 2.089 | 1.852 |
| canterbury_ablation | zstd-default | 1 | 4 | 6.042 | 5.073 | 6.037 |
| synthetic_ablation | ablation-01-raw-only | 1 | 8 | 0.998 | 0.998 | 0.998 |
| synthetic_ablation | ablation-02-residual-only | 1 | 8 | 12.469 | 1.892 | 0.998 |
| synthetic_ablation | ablation-03-fixed-constant | 1 | 8 | 12.473 | 1.898 | 0.998 |
| synthetic_ablation | ablation-04-fixed-all-models | 1 | 8 | 56.249 | 24.795 | 84.734 |
| synthetic_ablation | ablation-05-adaptive-constant | 1 | 8 | 43.198 | 2.239 | 0.998 |
| synthetic_ablation | ablation-06-adaptive-linear | 1 | 8 | 85.569 | 6.307 | 1.526 |
| synthetic_ablation | ablation-07-add-polynomial | 1 | 8 | 126.037 | 13.311 | 5.733 |
| synthetic_ablation | ablation-08-add-periodic | 1 | 8 | 164.954 | 27.256 | 160.888 |
| synthetic_ablation | ablation-09-add-recurrence | 1 | 8 | 205.158 | 58.690 | 316.666 |
| synthetic_ablation | ablation-10-add-bit-plane | 1 | 8 | 205.158 | 58.690 | 316.666 |
| synthetic_ablation | ablation-11-add-stride | 1 | 8 | 205.158 | 58.690 | 316.666 |
| synthetic_ablation | ablation-12-add-copy | 1 | 8 | 205.360 | 58.726 | 316.666 |
| synthetic_ablation | ablation-13-custom-residual | 1 | 8 | 205.389 | 59.144 | 316.666 |
| synthetic_ablation | ablation-14-zstd-residual | 1 | 8 | 175.882 | 52.698 | 271.938 |
| synthetic_ablation | ablation-15-no-transform | 1 | 8 | 205.360 | 58.726 | 316.666 |
| synthetic_ablation | ablation-16-no-adaptive-partition | 1 | 8 | 56.635 | 25.197 | 84.995 |
| synthetic_ablation | ablation-17-no-raw-fallback | 1 | 8 | 205.389 | 59.140 | 316.666 |
| synthetic_ablation | ablation-18-fast | 1 | 8 | 147.032 | 43.259 | 223.004 |
| synthetic_ablation | ablation-19-balanced | 1 | 8 | 205.389 | 59.144 | 316.666 |
| synthetic_ablation | ablation-20-max | 1 | 8 | 96.836 | 37.062 | 148.615 |
| synthetic_ablation | zstd-default | 1 | 8 | 703.621 | 78.941 | 196.729 |

Arithmetic and geometric means are shown separately from the weighted aggregate; they are not substituted for corpus-wide byte totals.

## Dataset provenance

| Corpus | License/terms | Provenance |
|---|---|---|
| canterbury_ablation | Upstream files retain their original terms; see dataset manifest | datasets/manifests/canterbury.json |
| synthetic_ablation | CC0-1.0 | python/mathzip_bench/synthetic.py and datasets/synthetic/manifest.json |

## Baseline comparisons

- `ablation-19-balanced` against `zstd-default` on 12 matched file/thread pairs: 4 smaller, 0 equal, 8 larger; median MathZip size gain -43.679% (positive means MathZip produced fewer bytes).
- No matched successful rows for `ablation-19-balanced` against `xz-default`.

## Failures and unavailable measurements

No failed or unavailable rows were recorded.

## Reproducibility evidence

- Result schema: `mathzip-benchmark-results-v1`
- Config SHA-256: `dde3921fbeb0f6da03206cd7c3339575c9659ea4d97eb0bcf249f0adc68af85d`
- Source revision: `e5366fa7c5c1cd5801bb9cc86b19a09592403137`
- Source tree dirty: `False`
- Source-tree SHA-256: `e3813b1523ee29c09f8b60b9d7a06844219e21c20114e3a3ce5762ae75b5323a`
- Source-tree manifest embedded: `false`
- Source stable during run: `true`
- Executable hashes stable during run: `true`
- Available executable hashes complete: `true`
- Input rights/provenance complete: `true`
- Timing protocol compliant: `true`
- Successful rows: 252
- Failed/unavailable rows: 0
- Publication-compliant evidence: `true`
- Publication evidence gaps: none

Exact command lines, compressor versions, per-trial wall/CPU/RSS values, archive SHA-256 values and restored SHA-256 values remain in `results.json`.

## Interpretation limits

- A failed row is not omitted from totals silently; it appears above and is excluded from successful aggregates.
- OS page-cache effects are not eliminated. Fresh output paths prevent reuse of prior compressed results, but do not flush the system page cache.
- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable or underestimate short-lived peaks on other systems.
- Missing MathZip inspection fields remain `n/a`; the report does not infer model, residual or segmentation breakdowns.
