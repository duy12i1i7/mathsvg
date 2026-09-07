# MathZip benchmark report

Run ID: `20260724T165449Z-4ef6e508`
Profile: `silesia`
Status: `complete`
Started (UTC): `2026-07-24T16:54:49.742984+00:00`
Completed (UTC): `2026-07-24T17:19:23.044770+00:00`

This report is generated only from recorded file outputs and trial evidence. It makes no claim for configurations or datasets that failed or were absent.

## Experimental setup

- CPU: Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz
- Logical cores: 16
- Physical cores: 16
- CPU affinity count: 16
- CPU governor(s): unavailable
- RAM bytes: 16,715,317,248
- Swap bytes: 4,054,839,296
- Filesystem: ext4; free bytes 70,618,349,568
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
| silesia | 7z-fast | 1 | 211,938,580 | 58,456,161 | 3.626 | 2.207 | 14.362 | 45.931 | 11.270 | 0 |
| silesia | brotli-fast | 1 | 211,938,580 | 64,205,968 | 3.301 | 2.424 | 19.849 | 163.545 | 72.730 | 0 |
| silesia | bzip2-fast | 1 | 211,938,580 | 60,509,041 | 3.503 | 2.284 | 9.185 | 22.919 | 2.438 | 0 |
| silesia | gzip-fast | 1 | 211,938,580 | 77,366,636 | 2.739 | 2.920 | 49.808 | 98.606 | 1.883 | 0 |
| silesia | lz4-fast | 1 | 211,938,580 | 100,934,427 | 2.100 | 3.810 | 223.220 | 334.105 | 9.680 | 0 |
| silesia | mathzip-fast | 1 | 211,938,580 | 187,537,684 | 1.130 | 7.079 | 1.567 | 37.157 | 328.750 | 0 |
| silesia | raw | 1 | 211,938,580 | 211,938,580 | 1.000 | 8.000 | 1022.953 | 1017.004 | n/a | 0 |
| silesia | xz-fast | 1 | 211,938,580 | 58,417,824 | 3.628 | 2.205 | 8.839 | 39.807 | 10.352 | 0 |
| silesia | zstd-fast | 1 | 211,938,580 | 73,448,492 | 2.886 | 2.772 | 157.447 | 431.280 | 15.320 | 0 |

Weighted ratio is total original bytes divided by total compressed bytes. Throughput is total original bytes divided by the sum of per-file median times.

## Per-file ratio statistics

| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |
|---|---|---:|---:|---:|---:|---:|
| silesia | 7z-fast | 1 | 12 | 4.435 | 3.591 | 3.269 |
| silesia | brotli-fast | 1 | 12 | 4.200 | 3.324 | 2.959 |
| silesia | bzip2-fast | 1 | 12 | 4.519 | 3.627 | 3.479 |
| silesia | gzip-fast | 1 | 12 | 3.008 | 2.664 | 2.535 |
| silesia | lz4-fast | 1 | 12 | 2.343 | 2.040 | 1.927 |
| silesia | mathzip-fast | 1 | 12 | 1.124 | 1.119 | 1.139 |
| silesia | raw | 1 | 12 | 1.000 | 1.000 | 1.000 |
| silesia | xz-fast | 1 | 12 | 4.470 | 3.581 | 3.311 |
| silesia | zstd-fast | 1 | 12 | 3.652 | 2.898 | 2.657 |

Arithmetic and geometric means are shown separately from the weighted aggregate; they are not substituted for corpus-wide byte totals.

## Dataset provenance

| Corpus | License/terms | Provenance |
|---|---|---|
| silesia | Per-file upstream terms; see Silesia provenance | datasets/manifests/silesia.json |

## Fixed baseline comparisons

- Against `zstd-fast` on 12 matched file/thread pairs: 0 smaller, 0 equal, 12 larger; median MathZip size gain -144.552% (positive means MathZip produced fewer bytes).
- Against `xz-fast` on 12 matched file/thread pairs: 0 smaller, 0 equal, 12 larger; median MathZip size gain -193.970% (positive means MathZip produced fewer bytes).

## Failures and unavailable measurements

No failed or unavailable rows were recorded.

## Reproducibility evidence

- Result schema: `mathzip-benchmark-results-v1`
- Config SHA-256: `4ef6e508882c5a13c61fcae49162cda90fd25e310ed1be1e674f5c9da6167298`
- Source revision: `0e3bd26e9883b7b6d6ed51def05b1822efbda6bf`
- Source tree dirty: `False`
- Source-tree SHA-256: `dd883471e0a661461fd56cf892f01dfbc90a8ec2e9d192d87f07b8e0b5e4cfad`
- Source-tree manifest embedded: `false`
- Source stable during run: `true`
- Executable hashes stable during run: `true`
- Available executable hashes complete: `true`
- Input rights/provenance complete: `true`
- Timing protocol compliant: `true`
- Successful rows: 108
- Failed/unavailable rows: 0
- Publication-compliant evidence: `true`
- Publication evidence gaps: none

Exact command lines, compressor versions, per-trial wall/CPU/RSS values, archive SHA-256 values and restored SHA-256 values remain in `results.json`.

## Interpretation limits

- A failed row is not omitted from totals silently; it appears above and is excluded from successful aggregates.
- OS page-cache effects are not eliminated. Fresh output paths prevent reuse of prior compressed results, but do not flush the system page cache.
- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable or underestimate short-lived peaks on other systems.
- Missing MathZip inspection fields remain `n/a`; the report does not infer model, residual or segmentation breakdowns.
