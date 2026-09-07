# MathZip benchmark report

Run ID: `20260724T134555Z-8b4c06c2`
Profile: `quick`
Status: `complete`
Started (UTC): `2026-07-24T13:45:55.277604+00:00`
Completed (UTC): `2026-07-24T13:58:44.131544+00:00`

This report is generated only from recorded file outputs and trial evidence. It makes no claim for configurations or datasets that failed or were absent.

## Experimental setup

- CPU: Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz
- Logical cores: 16
- Physical cores: 16
- CPU affinity count: 16
- CPU governor(s): unavailable
- RAM bytes: 16,715,317,248
- Swap bytes: 4,054,839,296
- Filesystem: ext4; free bytes 70,788,550,656
- Container image digest: unavailable
- OS: Linux-7.0.0-28-generic-x86_64-with-glibc2.39
- Rust: rustc 1.97.1 (8bab26f4f 2026-07-14)
- Python: 3.12.3 (main, Jun 19 2026, 12:46:00) [GCC 13.3.0]
- Warm-ups: 1
- Measured repeats: 3
- Per-operation timeout: 120.0 s
- Aggregate: median of successful measured repetitions.
- Every measured round trip is checked against the original SHA-256.

## Weighted corpus results

| Corpus | Codec | Threads | Original bytes | Compressed bytes | Weighted ratio | Bits/byte | Compression MB/s | Decompression MB/s | Peak RSS MiB | Failures |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| calgary_combined | 7z-fast | 1 | 3,141,622 | 919,765 | 3.416 | 2.342 | 12.084 | 41.047 | 11.176 | 0 |
| calgary_combined | brotli-fast | 1 | 3,141,622 | 990,929 | 3.170 | 2.523 | 22.581 | 125.014 | 13.848 | 0 |
| calgary_combined | bzip2-fast | 1 | 3,141,622 | 924,922 | 3.397 | 2.355 | 10.505 | 21.614 | 2.344 | 0 |
| calgary_combined | gzip-fast | 1 | 3,141,622 | 1,196,883 | 2.625 | 3.048 | 44.799 | 86.009 | 1.879 | 0 |
| calgary_combined | lz4-fast | 1 | 3,141,622 | 1,622,118 | 1.937 | 4.131 | 112.202 | 179.084 | 6.242 | 0 |
| calgary_combined | mathzip-fast | 1 | 3,141,622 | 2,455,746 | 1.279 | 6.253 | 1.400 | 35.613 | 22.426 | 0 |
| calgary_combined | raw | 1 | 3,141,622 | 3,141,622 | 1.000 | 8.000 | 1145.923 | 1098.924 | n/a | 0 |
| calgary_combined | xz-fast | 1 | 3,141,622 | 940,132 | 3.342 | 2.394 | 7.802 | 36.003 | 10.348 | 0 |
| calgary_combined | zstd-fast | 1 | 3,141,622 | 1,136,806 | 2.764 | 2.895 | 82.973 | 184.368 | 7.598 | 0 |
| calgary_files | 7z-fast | 1 | 3,141,622 | 927,839 | 3.386 | 2.363 | 7.982 | 17.016 | 8.926 | 0 |
| calgary_files | brotli-fast | 1 | 3,141,622 | 1,010,907 | 3.108 | 2.574 | 16.365 | 38.824 | 5.398 | 0 |
| calgary_files | bzip2-fast | 1 | 3,141,622 | 914,040 | 3.437 | 2.328 | 8.621 | 16.808 | 2.344 | 0 |
| calgary_files | gzip-fast | 1 | 3,141,622 | 1,191,338 | 2.637 | 3.034 | 27.787 | 42.951 | 1.879 | 0 |
| calgary_files | lz4-fast | 1 | 3,141,622 | 1,625,275 | 1.933 | 4.139 | 44.490 | 47.290 | 0.703 | 0 |
| calgary_files | mathzip-fast | 1 | 3,141,622 | 2,448,322 | 1.283 | 6.235 | 1.356 | 20.297 | 8.246 | 0 |
| calgary_files | raw | 1 | 3,141,622 | 3,141,622 | 1.000 | 8.000 | 543.949 | 574.057 | n/a | 0 |
| calgary_files | xz-fast | 1 | 3,141,622 | 948,696 | 3.312 | 2.416 | 6.258 | 20.159 | 7.922 | 0 |
| calgary_files | zstd-fast | 1 | 3,141,622 | 1,118,299 | 2.809 | 2.848 | 24.959 | 33.650 | 4.285 | 0 |
| canterbury_combined | 7z-fast | 1 | 2,810,784 | 558,068 | 5.037 | 1.588 | 13.997 | 53.647 | 10.879 | 0 |
| canterbury_combined | brotli-fast | 1 | 2,810,784 | 597,909 | 4.701 | 1.702 | 21.866 | 130.469 | 13.980 | 0 |
| canterbury_combined | bzip2-fast | 1 | 2,810,784 | 583,549 | 4.817 | 1.661 | 12.328 | 27.320 | 2.344 | 0 |
| canterbury_combined | gzip-fast | 1 | 2,810,784 | 871,191 | 3.226 | 2.480 | 54.049 | 95.956 | 1.883 | 0 |
| canterbury_combined | lz4-fast | 1 | 2,810,784 | 1,227,840 | 2.289 | 3.495 | 137.333 | 188.050 | 5.555 | 0 |
| canterbury_combined | mathzip-fast | 1 | 2,810,784 | 2,242,788 | 1.253 | 6.383 | 1.606 | 37.728 | 20.398 | 0 |
| canterbury_combined | raw | 1 | 2,810,784 | 2,810,784 | 1.000 | 8.000 | 1119.793 | 1090.236 | n/a | 0 |
| canterbury_combined | xz-fast | 1 | 2,810,784 | 568,060 | 4.948 | 1.617 | 10.443 | 49.446 | 10.344 | 0 |
| canterbury_combined | zstd-fast | 1 | 2,810,784 | 699,742 | 4.017 | 1.992 | 104.099 | 229.529 | 7.000 | 0 |
| canterbury_files | 7z-fast | 1 | 2,810,784 | 561,802 | 5.003 | 1.599 | 8.936 | 21.712 | 9.180 | 0 |
| canterbury_files | brotli-fast | 1 | 2,810,784 | 634,667 | 4.429 | 1.806 | 18.527 | 45.822 | 5.359 | 0 |
| canterbury_files | bzip2-fast | 1 | 2,810,784 | 575,983 | 4.880 | 1.639 | 10.324 | 20.543 | 2.340 | 0 |
| canterbury_files | gzip-fast | 1 | 2,810,784 | 865,428 | 3.248 | 2.463 | 32.349 | 46.492 | 1.812 | 0 |
| canterbury_files | lz4-fast | 1 | 2,810,784 | 1,229,454 | 2.286 | 3.499 | 58.679 | 64.246 | 0.508 | 0 |
| canterbury_files | mathzip-fast | 1 | 2,810,784 | 2,235,631 | 1.257 | 6.363 | 1.549 | 23.493 | 10.207 | 0 |
| canterbury_files | raw | 1 | 2,810,784 | 2,810,784 | 1.000 | 8.000 | 602.982 | 624.195 | n/a | 0 |
| canterbury_files | xz-fast | 1 | 2,810,784 | 570,256 | 4.929 | 1.623 | 7.876 | 28.510 | 8.535 | 0 |
| canterbury_files | zstd-fast | 1 | 2,810,784 | 686,824 | 4.092 | 1.955 | 37.060 | 46.786 | 3.906 | 0 |
| silesia_quick_subset | 7z-fast | 1 | 100,311,651 | 21,925,286 | 4.575 | 1.749 | 18.278 | 55.625 | 11.277 | 0 |
| silesia_quick_subset | brotli-fast | 1 | 100,311,651 | 24,675,187 | 4.065 | 1.968 | 25.310 | 194.290 | 72.680 | 0 |
| silesia_quick_subset | bzip2-fast | 1 | 100,311,651 | 24,898,857 | 4.029 | 1.986 | 8.937 | 24.515 | 2.434 | 0 |
| silesia_quick_subset | gzip-fast | 1 | 100,311,651 | 30,859,192 | 3.251 | 2.461 | 56.572 | 107.307 | 1.883 | 0 |
| silesia_quick_subset | lz4-fast | 1 | 100,311,651 | 39,640,378 | 2.531 | 3.161 | 270.651 | 387.845 | 8.996 | 0 |
| silesia_quick_subset | mathzip-fast | 1 | 100,311,651 | 88,266,059 | 1.136 | 7.039 | 1.592 | 38.633 | 328.770 | 0 |
| silesia_quick_subset | raw | 1 | 100,311,651 | 100,311,651 | 1.000 | 8.000 | 1055.268 | 1062.202 | n/a | 0 |
| silesia_quick_subset | xz-fast | 1 | 100,311,651 | 21,687,320 | 4.625 | 1.730 | 11.717 | 48.105 | 10.352 | 0 |
| silesia_quick_subset | zstd-fast | 1 | 100,311,651 | 27,933,482 | 3.591 | 2.228 | 185.926 | 485.055 | 15.328 | 0 |
| synthetic | 7z-fast | 1 | 69,695 | 28,906 | 2.411 | 3.318 | 0.338 | 0.473 | 1.594 | 0 |
| synthetic | brotli-fast | 1 | 69,695 | 25,672 | 2.715 | 2.947 | 0.732 | 0.967 | 0.352 | 0 |
| synthetic | bzip2-fast | 1 | 69,695 | 31,480 | 2.214 | 3.613 | 0.869 | 1.018 | 0.971 | 0 |
| synthetic | gzip-fast | 1 | 69,695 | 26,627 | 2.617 | 3.056 | 1.053 | 1.216 | 0.512 | 0 |
| synthetic | lz4-fast | 1 | 69,695 | 27,315 | 2.552 | 3.135 | 1.124 | 1.199 | 0.643 | 0 |
| synthetic | mathzip-fast | 1 | 69,695 | 35,847 | 1.944 | 4.115 | 0.531 | 0.757 | 2.848 | 0 |
| synthetic | raw | 1 | 69,695 | 69,695 | 1.000 | 8.000 | 14.490 | 16.369 | n/a | 0 |
| synthetic | xz-fast | 1 | 69,695 | 26,380 | 2.642 | 3.028 | 0.415 | 0.835 | 1.922 | 0 |
| synthetic | zstd-fast | 1 | 69,695 | 25,476 | 2.736 | 2.924 | 0.662 | 0.711 | 0.824 | 0 |

Weighted ratio is total original bytes divided by total compressed bytes. Throughput is total original bytes divided by the sum of per-file median times.

## Per-file ratio statistics

| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |
|---|---|---:|---:|---:|---:|---:|
| calgary_combined | 7z-fast | 1 | 1 | 3.416 | 3.416 | 3.416 |
| calgary_combined | brotli-fast | 1 | 1 | 3.170 | 3.170 | 3.170 |
| calgary_combined | bzip2-fast | 1 | 1 | 3.397 | 3.397 | 3.397 |
| calgary_combined | gzip-fast | 1 | 1 | 2.625 | 2.625 | 2.625 |
| calgary_combined | lz4-fast | 1 | 1 | 1.937 | 1.937 | 1.937 |
| calgary_combined | mathzip-fast | 1 | 1 | 1.279 | 1.279 | 1.279 |
| calgary_combined | raw | 1 | 1 | 1.000 | 1.000 | 1.000 |
| calgary_combined | xz-fast | 1 | 1 | 3.342 | 3.342 | 3.342 |
| calgary_combined | zstd-fast | 1 | 1 | 2.764 | 2.764 | 2.764 |
| calgary_files | 7z-fast | 1 | 14 | 3.783 | 3.445 | 3.117 |
| calgary_files | brotli-fast | 1 | 14 | 3.517 | 3.177 | 2.946 |
| calgary_files | bzip2-fast | 1 | 14 | 3.859 | 3.505 | 3.247 |
| calgary_files | gzip-fast | 1 | 14 | 2.991 | 2.747 | 2.498 |
| calgary_files | lz4-fast | 1 | 14 | 2.251 | 2.061 | 1.865 |
| calgary_files | mathzip-fast | 1 | 14 | 1.400 | 1.245 | 1.138 |
| calgary_files | raw | 1 | 14 | 1.000 | 1.000 | 1.000 |
| calgary_files | xz-fast | 1 | 14 | 3.770 | 3.412 | 3.055 |
| calgary_files | zstd-fast | 1 | 14 | 3.275 | 2.920 | 2.662 |
| canterbury_combined | 7z-fast | 1 | 1 | 5.037 | 5.037 | 5.037 |
| canterbury_combined | brotli-fast | 1 | 1 | 4.701 | 4.701 | 4.701 |
| canterbury_combined | bzip2-fast | 1 | 1 | 4.817 | 4.817 | 4.817 |
| canterbury_combined | gzip-fast | 1 | 1 | 3.226 | 3.226 | 3.226 |
| canterbury_combined | lz4-fast | 1 | 1 | 2.289 | 2.289 | 2.289 |
| canterbury_combined | mathzip-fast | 1 | 1 | 1.253 | 1.253 | 1.253 |
| canterbury_combined | raw | 1 | 1 | 1.000 | 1.000 | 1.000 |
| canterbury_combined | xz-fast | 1 | 1 | 4.948 | 4.948 | 4.948 |
| canterbury_combined | zstd-fast | 1 | 1 | 4.017 | 4.017 | 4.017 |
| canterbury_files | 7z-fast | 1 | 11 | 4.631 | 3.761 | 3.038 |
| canterbury_files | brotli-fast | 1 | 11 | 4.028 | 3.516 | 3.008 |
| canterbury_files | bzip2-fast | 1 | 11 | 4.226 | 3.757 | 3.227 |
| canterbury_files | gzip-fast | 1 | 11 | 3.149 | 2.902 | 2.706 |
| canterbury_files | lz4-fast | 1 | 11 | 2.271 | 2.084 | 1.927 |
| canterbury_files | mathzip-fast | 1 | 11 | 1.464 | 1.269 | 1.125 |
| canterbury_files | raw | 1 | 11 | 1.000 | 1.000 | 1.000 |
| canterbury_files | xz-fast | 1 | 11 | 4.751 | 3.786 | 3.045 |
| canterbury_files | zstd-fast | 1 | 11 | 3.852 | 3.282 | 2.775 |
| silesia_quick_subset | 7z-fast | 1 | 4 | 7.326 | 5.943 | 6.417 |
| silesia_quick_subset | brotli-fast | 1 | 4 | 6.957 | 5.614 | 6.082 |
| silesia_quick_subset | bzip2-fast | 1 | 4 | 7.371 | 5.805 | 6.255 |
| silesia_quick_subset | gzip-fast | 1 | 4 | 4.371 | 3.855 | 4.001 |
| silesia_quick_subset | lz4-fast | 1 | 4 | 3.483 | 2.999 | 3.145 |
| silesia_quick_subset | mathzip-fast | 1 | 4 | 1.138 | 1.138 | 1.139 |
| silesia_quick_subset | raw | 1 | 4 | 1.000 | 1.000 | 1.000 |
| silesia_quick_subset | xz-fast | 1 | 4 | 7.489 | 5.984 | 6.466 |
| silesia_quick_subset | zstd-fast | 1 | 4 | 6.089 | 4.839 | 5.103 |
| synthetic | 7z-fast | 1 | 22 | 5.891 | 3.108 | 3.231 |
| synthetic | brotli-fast | 1 | 22 | 44.010 | 5.726 | 4.036 |
| synthetic | bzip2-fast | 1 | 22 | 15.144 | 3.577 | 3.017 |
| synthetic | gzip-fast | 1 | 22 | 14.596 | 4.360 | 3.164 |
| synthetic | lz4-fast | 1 | 22 | 17.167 | 4.395 | 2.603 |
| synthetic | mathzip-fast | 1 | 22 | 8.318 | 3.012 | 1.186 |
| synthetic | raw | 1 | 22 | 1.000 | 1.000 | 1.000 |
| synthetic | xz-fast | 1 | 22 | 10.557 | 4.083 | 3.724 |
| synthetic | zstd-fast | 1 | 22 | 29.116 | 5.360 | 3.770 |

Arithmetic and geometric means are shown separately from the weighted aggregate; they are not substituted for corpus-wide byte totals.

## Dataset provenance

| Corpus | License/terms | Provenance |
|---|---|---|
| calgary_combined | Upstream files retain their original terms; see dataset manifest | datasets/manifests/calgary.json |
| calgary_files | Upstream files retain their original terms; see dataset manifest | datasets/manifests/calgary.json |
| canterbury_combined | Upstream files retain their original terms; see dataset manifest | datasets/manifests/canterbury.json |
| canterbury_files | Upstream files retain their original terms; see dataset manifest | datasets/manifests/canterbury.json |
| silesia_quick_subset | Per-file upstream terms; see Silesia provenance | datasets/manifests/silesia.json |
| synthetic | CC0-1.0 | python/mathzip_bench/synthetic.py and datasets/synthetic/manifest.json |

## Fixed baseline comparisons

- Against `zstd-fast` on 53 matched file/thread pairs: 6 smaller, 0 equal, 47 larger; median MathZip size gain -119.830% (positive means MathZip produced fewer bytes).
- Against `xz-fast` on 53 matched file/thread pairs: 6 smaller, 0 equal, 47 larger; median MathZip size gain -125.673% (positive means MathZip produced fewer bytes).

## Failures and unavailable measurements

No failed or unavailable rows were recorded.

## Reproducibility evidence

- Result schema: `mathzip-benchmark-results-v1`
- Config SHA-256: `8b4c06c211c6377c8ba0670b307ea34f70bd8cdff6e458450b989e3f8079c058`
- Source revision: `6dbc3860910e8d310cfa8bbeadbf97f0529beff1`
- Source tree dirty: `False`
- Source-tree SHA-256: `91797df21983656b4d8020958e3619a646f46dfe86a4560d854e22483838c196`
- Source-tree manifest embedded: `false`
- Source stable during run: `true`
- Executable hashes stable during run: `true`
- Available executable hashes complete: `true`
- Input rights/provenance complete: `true`
- Timing protocol compliant: `true`
- Successful rows: 477
- Failed/unavailable rows: 0
- Publication-compliant evidence: `true`
- Publication evidence gaps: none

Exact command lines, compressor versions, per-trial wall/CPU/RSS values, archive SHA-256 values and restored SHA-256 values remain in `results.json`.

## Interpretation limits

- A failed row is not omitted from totals silently; it appears above and is excluded from successful aggregates.
- OS page-cache effects are not eliminated. Fresh output paths prevent reuse of prior compressed results, but do not flush the system page cache.
- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable or underestimate short-lived peaks on other systems.
- Missing MathZip inspection fields remain `n/a`; the report does not infer model, residual or segmentation breakdowns.
