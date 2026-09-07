# Phase 9 — Pruned development pilot v5

```text
PHASE
- Commit: 36f84685dc00064dae9a239face7e8eff797ed28 + dirty development tree
- Status: development experiment complete; not publishable and not holdout

CORRECTNESS
- Tests: full Rust workspace and 169 Python tests pass
- Round-trip: 550/550 benchmark rows successful
- Determinism: 3,200/3,200 local rows pass and share one archive hash; compiler/machine/architecture gate remains incomplete
- Fuzz: sanitizer v3 passed 10,000 runs on each of decode/entropy/roundtrip with stable source and immutable inputs

PROCEDURAL REPRESENTATION
- Function graph bytes: 168
- Coordinate bytes: 0
- Parameter bytes: 0
- Residual bytes: 0
- Literal bytes: 7,129,579
- Procedural coverage: 1.943761%
- Procedural gain: 196,604 bytes before entropy; 2,985,164 bytes total

PERFORMANCE
- Ratio: 1.417357x
- Compression MB/s: 0.690603
- Decompression MB/s: 4.291083
- Peak RSS: 24.34375 MiB

BASELINES
- Zstd: 1.613396x, 29.353686 MB/s encode, 62.852808 MB/s decode
- XZ: 2.195952x, 1.118042 MB/s encode, 15.825305 MB/s decode
- Brotli: not run in this pilot
- LZ4: 1.138278x, 65.515543 MB/s encode, 67.371249 MB/s decode
- Domain codec: not run in this pilot

ORACLE
- Remaining search headroom: bounded-incomplete; no global zero-headroom proof
- Coordinate headroom: independent basis 0/73 incremental real wins; Balanced interval/coordinate search removed
- Residual headroom: +115 bytes on one 4 KiB real oracle sample, 0 bytes on paired whole-file integration
- Symbolic headroom: 0 in bounded native sample; incomplete search cannot prove global zero
- DAG-sharing headroom: 0 on 13/13 complete natural sample rows

DOMINANCE
- Dominated: none under the four-metric certificate
- Not dominated: gzip-6, LZ4, XZ-9e, zstd-default
- Blocking metrics: size except versus LZ4; encode/decode time broadly; RSS except versus XZ

DECISION
- Keep: C4L entropy, exact whole-block functions, literal fallback, profile-specific deeper search
- Remove: interval-function and coordinate enumeration from Balanced's built-in path
- Pivot: optimize measured real-data blockers; do not infer procedural success from literal entropy
- Next: run memory/full-corpus/validation matrices, obtain compiler/ARM64/second-machine evidence, then freeze before holdout
```

## Evidence boundary

`development-pilot-v5-pruned` selected ten already-open development inputs
from the 69-row manifest. It used one warm-up and ten measured repetitions,
deterministic randomized/interleaved scheduling, one thread, one x86-64 host,
and five codecs. All 550 scheduled rows succeeded. Source identity was stable
during the run, but the tree was dirty and the host was not isolated; timing
and RSS are development evidence only. No validation or sealed holdout payload
was opened.

Provenance:

- development-manifest canonical SHA-256:
  `88ed376ce82fbd9301fe2fce71dd0e3a4af5f837450b50b8489d678b19d5fca7`;
- raw JSONL SHA-256:
  `b7a3c413c6846e5f0d58c1c8daa9c0194cf715e53577a54fe43264a4c042ca4c`;
- run metadata SHA-256:
  `b4adea62596c3ac83ce5e9da71801992f5fb5fa5d5f76559121d41e89f4a87fb`;
- benchmark runner SHA-256:
  `f2c995ef2bab2d3b19a81a25a5798309b83d556f0424c7645d3bad648ea9e140`;
- measured MathSVG executable SHA-256:
  `ab97034b57f903c5b9a509737b12da0980892412e2c38dfe00ba1ff077eabbbf`.

## Corpus result

The selected input total is 10,114,825 bytes.

| Codec/profile | Archive bytes | Ratio | Compression | Decompression | Peak RSS |
|---|---:|---:|---:|---:|---:|
| MathSVG Balanced | 7,136,397 | 1.417357x | 14.647 s | 2.358 s | 24.34 MiB |
| LZ4 | 8,886,079 | 1.138278x | 0.154 s | 0.151 s | 9.53 MiB |
| zstd-default | 6,269,278 | 1.613396x | 0.345 s | 0.161 s | 18.37 MiB |
| gzip-6 | 6,314,471 | 1.601848x | 0.730 s | 0.255 s | 1.95 MiB |
| XZ-9e | 4,606,124 | 2.195952x | 9.049 s | 0.640 s | 139.17 MiB |

MathSVG is 13.8312% larger than zstd-default, which passes only the
development G2 size milestone (`<=15%`). Its ratio remains below the 2x G1
threshold. This pilot did not include Brotli, high-level Zstd, domain codecs,
multiple MathSVG profiles, multiple thread counts, the full corpus, a second
machine or ARM64.

## Procedural accounting

The exact median aggregate wire partition is:

| Category | Bytes |
|---|---:|
| Container overhead | 6,448 |
| Function graph | 168 |
| Coordinates | 0 |
| Shared definitions/references | 0 |
| Parameters | 0 |
| Residual layers | 0 |
| Literal leaves | 7,129,579 |
| Entropy metadata | 202 |
| Total archive | 7,136,397 |

The literal-only counterfactual is 10,121,561 bytes. Procedural selection
before entropy is 9,924,957 bytes, a 196,604-byte gain. Native entropy then
saves 2,788,560 bytes. Function nodes reconstruct 196,608 source bytes and
literal nodes reconstruct 9,918,217 bytes, so procedural coverage is only
1.943761%. The observed ratio therefore remains dominated by entropy coding
of literal leaves.

## Balanced interval-search stop decision

`development-interval-pruning-v1` paired enabled Balanced search with
`no-interval-functions-or-coordinate` over the same ten inputs and ten
measured repetitions. All 220 rows succeeded and source identity was stable.
Every file and the corpus had a zero-byte archive delta. The disabled search
reduced aggregate compression wall time by 29.609179% (95% t interval
-29.966491% to -29.251867%) and peak RSS by 1.542605% (95% interval
-1.821453% to -1.263757%). The decompression delta interval crossed zero.

This is a stop-rule decision for Balanced, not a claim that coordinates or
interval functions are universally useless. Exact whole-block functions stay
enabled, while Fast, Max, Structured and Repository retain their deeper search
until profile-specific evidence closes it.

## Dominance result

The certificate passes 0/40 per-file comparisons and 0/4 corpus comparisons.
At corpus level:

- LZ4 is worse in size, but MathSVG is worse in encode time, decode time and
  memory;
- gzip-6 and zstd-default beat MathSVG in all four required metrics;
- XZ-9e beats MathSVG in size and both times, while MathSVG uses less memory.

Canonical final-result paths remain untouched. A final claim requires a clean
commit/configuration freeze, the complete baseline/profile/corpus matrix,
memory and determinism gates, an independent second machine including ARM64,
validation, and only then the sealed holdout.

## Local determinism campaign

`determinism-development-v2` completed 3,200/3,200 successful rows: 32 matrix
cells, 100 repetitions each, debug and release binaries, thread modes
`1/2/4/8/all` plus the supplemental default cell, and scalar/SIMD/auto decoder
backends. Every archive matched the single reference SHA-256
`8d15c228d945c3857233c58c017039aa794bb9e1521d68a85bc14ca914c6d89d`
and every restored SHA-256 matched the selected 65,536-byte input. Source and
immutable inputs were stable throughout the campaign.

The result remains `incomplete`, not failed: the tree is dirty, build-time
compiler provenance is unverified, and the required second compiler, second
machine and ARM64 architecture are unavailable locally. The canonical local
artifacts are:

- `results/determinism/determinism-development-v2.csv` (SHA-256
  `9e460c7b15cdbc6d7248e450f5433f5ffd57c680ec2a79d1f1e1a9f7c4e2f050`);
- `results/determinism/determinism-development-v2.json`.

## Versioned artifacts

- `results/raw/development-pilot-v5-pruned.jsonl`
- `results/raw/development-pilot-v5-pruned-run.json`
- `results/summary/development-pilot-v5-pruned-benchmark.csv`
- `results/summary/development-pilot-v5-pruned-procedural.csv`
- `results/profiling/development-pilot-v5-pruned.csv`
- `results/dominance/development-pilot-v5-pruned/`
- `results/plots/development-pilot-v5-pruned-*.svg`
- `results/raw/development-interval-pruning-v1.jsonl`
- `results/raw/development-interval-pruning-v1-run.json`
- `results/ablation/development-interval-pruning-v1.csv`
- `results/determinism/determinism-development-v2.csv`
- `results/determinism/determinism-development-v2.json`
