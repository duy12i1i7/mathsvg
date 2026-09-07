# MathZip benchmark report

Run ID: `20260724T140810Z-c825d7e1`
Profile: `git-copy-ablation`
Status: `complete`
Started (UTC): `2026-07-24T14:08:10.713412+00:00`
Completed (UTC): `2026-07-24T14:19:32.648956+00:00`

This report is generated only from recorded file outputs and trial evidence. It makes no claim for configurations or datasets that failed or were absent.

## Experimental setup

- CPU: Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz
- Logical cores: 16
- Physical cores: 16
- CPU affinity count: 16
- CPU governor(s): unavailable
- RAM bytes: 16,715,317,248
- Swap bytes: 4,054,839,296
- Filesystem: ext4; free bytes 70,776,274,944
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
| git_snapshots_combined | mathzip-balanced-copy-off | 1 | 302,080 | 68,242 | 4.427 | 1.807 | 0.007 | 13.164 | 4.660 | 0 |
| git_snapshots_combined | mathzip-balanced-copy-on | 1 | 302,080 | 68,202 | 4.429 | 1.806 | 0.007 | 12.249 | 4.672 | 0 |
| git_snapshots_combined | zstd-default | 1 | 302,080 | 1,050 | 287.695 | 0.028 | 10.629 | 11.665 | 0.383 | 0 |
| git_snapshots_individual | mathzip-balanced-copy-off | 1 | 151,040 | 22,601 | 6.683 | 1.197 | 0.026 | 2.671 | 4.066 | 0 |
| git_snapshots_individual | mathzip-balanced-copy-on | 1 | 151,040 | 22,601 | 6.683 | 1.197 | 0.025 | 2.873 | 4.055 | 0 |
| git_snapshots_individual | zstd-default | 1 | 151,040 | 794 | 190.227 | 0.042 | 2.415 | 2.415 | 0.379 | 0 |

Weighted ratio is total original bytes divided by total compressed bytes. Throughput is total original bytes divided by the sum of per-file median times.

## Per-file ratio statistics

| Corpus | Codec | Threads | Files | Arithmetic mean ratio | Geometric mean ratio | Median ratio |
|---|---|---:|---:|---:|---:|---:|
| git_snapshots_combined | mathzip-balanced-copy-off | 1 | 4 | 9.682 | 6.956 | 6.918 |
| git_snapshots_combined | mathzip-balanced-copy-on | 1 | 4 | 9.683 | 6.958 | 6.920 |
| git_snapshots_combined | zstd-default | 1 | 4 | 276.200 | 274.531 | 285.128 |
| git_snapshots_individual | mathzip-balanced-copy-off | 1 | 11 | 46.093 | 32.936 | 48.574 |
| git_snapshots_individual | mathzip-balanced-copy-on | 1 | 11 | 46.093 | 32.936 | 48.574 |
| git_snapshots_individual | zstd-default | 1 | 11 | 183.564 | 180.516 | 169.412 |

Arithmetic and geometric means are shown separately from the weighted aggregate; they are not substituted for corpus-wide byte totals.

## Dataset provenance

| Corpus | License/terms | Provenance |
|---|---|---|
| git_snapshots_combined | CC0-1.0 | python/mathzip_bench/auxiliary.py and datasets/generated/git_snapshots/manifest.json |
| git_snapshots_individual | CC0-1.0 | python/mathzip_bench/auxiliary.py and datasets/generated/git_snapshots/manifest.json |

## Fixed baseline comparisons

No successful MathZip rows were available for fixed comparisons.

## Failures and unavailable measurements

No failed or unavailable rows were recorded.

## Reproducibility evidence

- Result schema: `mathzip-benchmark-results-v1`
- Config SHA-256: `c825d7e1b243c870e8342966cd0d5198453850e6588e969c9eb08aa669554965`
- Source revision: `163fc78cbadc0fdb5888781080baade7b576811d`
- Source tree dirty: `False`
- Source-tree SHA-256: `6397e3d01b12d567f54c378f036e785ee107916f8d9ab873b13f41a1031e0fd2`
- Source-tree manifest embedded: `false`
- Source stable during run: `true`
- Executable hashes stable during run: `true`
- Available executable hashes complete: `true`
- Input rights/provenance complete: `true`
- Timing protocol compliant: `true`
- Successful rows: 45
- Failed/unavailable rows: 0
- Publication-compliant evidence: `true`
- Publication evidence gaps: none

Exact command lines, compressor versions, per-trial wall/CPU/RSS values, archive SHA-256 values and restored SHA-256 values remain in `results.json`.

## Interpretation limits

- A failed row is not omitted from totals silently; it appears above and is excluded from successful aggregates.
- OS page-cache effects are not eliminated. Fresh output paths prevent reuse of prior compressed results, but do not flush the system page cache.
- Peak RSS uses periodic process-tree sampling on Linux and may be unavailable or underestimate short-lived peaks on other systems.
- Missing MathZip inspection fields remain `n/a`; the report does not infer model, residual or segmentation breakdowns.
