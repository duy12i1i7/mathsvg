# MathSVG Absolute research report

Status: active implementation; no final dominance claim.

## Objective

MathSVG Absolute tests whether arbitrary byte streams can be represented
losslessly as a native, versioned procedural function DAG plus reversible
coordinates, parameters and recursively represented exact corrections.
Literal byte leaves are the universal fallback.  Baseline-compressor payloads,
learned models, external services and executable programs are outside the
native archive boundary.

The stretch goal is not merely a good compression ratio.  It is a
benchmark-proven Pareto result over size, encode time, decode time and memory
against every frozen baseline in the requested matrix.  Until a corresponding
dominance certificate exists, this report uses the terms *headroom*,
*development evidence* and *milestone*, not *victory*.

## Reproducible starting point

- predecessor commit: `36f84685dc00064dae9a239face7e8eff797ed28`;
- development branch: `mathsvg-absolute`;
- rewritten requirement SHA-256:
  `b02eb222e39e4e772a4bb883aaaff73efd5e4d25a8fb8ad3773ea43a01018ece`;
- predecessor verification: 55 Python tests passed, 79 Rust tests passed,
  fuzz workspace checked, strict published artifacts reported zero warnings;
- one strict release-binary smoke was intentionally skipped because the
  user-supplied requirement file differs from the predecessor commit.

The predecessor Full run
`20260726T044843Z-213f07c3` remains development evidence: 24,650 of 24,650
jobs, 73,950 trials, zero recorded job failures, and approximately 35 h 51 min
wall time.  It is not a MathSVG final run because it used three repetitions,
one x86-64 host, sequential codec order and the predecessor format/search.

## What the predecessor evidence says

The predecessor Balanced aggregate compressed ratio was about 1.2513 with
about 0.4289 MB/s compression throughput.  On the same experiment,
Zstd-default was about 3.2479 and 75.0528 MB/s; XZ was about 4.361 and
1.3131 MB/s.  There was no aggregate four-metric Pareto dominance over the
core baselines and no per-file core-four dominance on the 81 rows labelled
non-synthetic in that run.

The useful conclusion is diagnostic, not celebratory:

- best-observed profile selection saved only about 0.4741% over Balanced
  overall and 0.09793% on the labelled non-synthetic subset;
- native residual-coder configuration headroom was much larger on the small
  controlled ablation, but concentrated in synthetic data;
- stride, bit-plane, recurrence, periodic and copy families showed measurable
  but uneven local headroom;
- the former residual layer dominated archive bytes and a large part of
  compression time.

This evidence motivates exact residual/coordinate/DAG oracles.  It does not
justify implementing every proposed model.

## Frozen design decisions

- New `MSVG` format; `MZIP` is never reinterpreted.
- Block-oriented v1 with independent, closed block programs and local DAG
  sharing; no cross-block reference in v1.
- Canonical integer-only DSL and a bounded evaluator, not arbitrary bytecode.
- Complete serialized candidate cost and a deterministic total-order tie-break.
- Literal archive is constructed first as the universal upper bound.
- Streaming architecture and bounded work/memory are format concerns from the
  first implementation, not a later wrapper.
- Every search cut is classified as `SAFE_PRUNE`, `HEURISTIC_SKIP` or
  `BUDGET_STOP`; only a proved admissible bound may use the first label.

The normative details live in `mathematical-spec.md`, `procedural-dsl.md`,
`format-spec.md`, `determinism.md` and `benchmark-methodology.md`.

## Candidate algorithms

Seven narrowly defined candidates are under evaluation:

1. ARPL — Algebraic Residual Projection Lattice.
2. RC-BasisDP — Recursive Coordinate Best-Basis DP.
3. RSEE — Residual-Sensitive Equality Enumeration.
4. AMCC — Affine Multiscale Corrected Copy.
5. ALTFB — Archive-Local Typed Function Basis.
6. PDPTG — Parameter-Delta Procedural Tree Grammar.
7. SADH — Shared-Activation Description Hypergraph.

The broad parent ideas all have close prior art.  The project therefore makes
no broad novelty claim.  Implementation priority is controlled by actual-byte
oracle headroom, a non-synthetic block win and the 0.5% stop threshold.

## Data and experiment governance

All inputs observed by the predecessor are development-only.  A manifest
validator enforces group-level split isolation, sealed holdout metadata and the
rule that only `origin=real AND primary=true` counts toward the 70% file/byte
balance.  A freeze tool hashes source identity, DSL/container versions,
manifests, profile configurations and baseline commands without opening sealed
holdout payloads.

The predecessor Full mixture did not satisfy the new rule: only 81 of 725
inputs were labelled non-synthetic (11.17%), and several were generated
replicas rather than independent primary sources. That mixture has been
retired from MathSVG's main aggregate.

The current integrity-checked manifests do satisfy section 20 without
relabeling generated data. Development contains 69 files and 1,396,010,270
bytes; 49 files (71.0145%) and 1,394,872,524 bytes (99.9185%) are
`origin=real AND primary=true`. Validation contains 40 files and 79,477,825
bytes; 37 files (92.5%) and 79,399,311 bytes (99.9012%) qualify. The combined
open development/validation inventory contains 109 files, 86 qualifying real
files (78.8991%), and 99.9176% qualifying real bytes. All 57 requirements in
section 20 have selected evidence and the coverage matrix reports zero gaps.
These facts make the corpus eligible for later main/validation runs; they do
not make the ten-file pilot below a full-corpus result.

## Benchmark discipline

The checked-in protocol requires warm-up, blocked randomized codec
interleaving, at least five repetitions, ten below a 2% claimed delta,
mean/median/standard deviation/t-based interval, paired deterministic bootstrap
intervals, archive and restored SHA-256, wall/CPU/RSS measurements, explicit
failures, and single/multithread rows.  Final gates still require an independent
second machine and x86-64/ARM64 coverage.

## Current implementation state

The formal specification, native block container, bounded evaluator,
function/entropy search, coordinate transforms, interval DP and explicit
experimental residual/symbolic/DAG providers now exist and produce verified
MathSVG archives. Native literal competition includes Canonical Huffman and
LZ-Huffman.

The current Balanced profile also applies a measured stop decision. Exact
whole-block function competition stays enabled, but interval-function and
coordinate enumeration are disabled. In the paired
`development-interval-pruning-v1` experiment, all ten files and the aggregate
produced exactly the same archive bytes with those searches disabled.
Compression wall time fell 29.6092% (95% interval -29.9665% to -29.2519%) and
peak RSS fell 1.5426%; decompression timing was statistically inconclusive.
The searches remain available in profiles with different budgets/domains and
as explicit ablations.

The streaming validation path now removes three repeated full-data operations
without changing the format: duplicate program validation, the entropy
inspect-then-decode pair, and the encoder's temporary one-block archive
round-trip. A strict resource-only preflight is used only after container
validation; actual entropy decode remains canonical and writes into an
uncommitted private buffer. On the development pilot, archive bytes were
unchanged while aggregate compression fell from 14.6469 s to 9.9637 s and
decompression from 2.3576 s to 1.2210 s. Full validation preserved every v1
archive identity while improving the corresponding medians by 31.6570% and
49.6973%.

Phase 3 has therefore been refreshed with a production-API probe rather than
the earlier raw-literal reference serializer. It measures complete archive
bytes on development inputs and records bounded-incomplete rows without
calling them optima. Exact results and stop decisions are in
`oracle-analysis.md` and `results/oracle/stop-policy.csv`.

This is still not sealed structured holdout, external determinism-matrix or
final dominance evidence. Open validation, literal safety and the enwik9
memory gate are complete locally; publication claims remain open until the
remaining prescribed experiments run after a clean freeze.

The final local 100-run determinism matrix is complete as development
evidence: 3,200/3,200 rows passed across 32 debug/release, thread and
decoder-backend cells, with archive SHA-256 `8d15c228...`, exact restored
hashes, zero failures and zero timeouts. Its formal status is still
`incomplete`, because the dirty tree has no build-time compiler attestation
and this host cannot supply a second compiler version, a second machine or
ARM64.

## Latest development benchmark

`development-pilot-v8-final-binary` ran one warm-up and ten measured
repetitions for five codecs over ten selected development files (550/550 scheduled trial
rows). Source identity was stable during the run, every measured archive
round-tripped, and native archive hashes were deterministic. It remains a
single-thread, single-x86-64-host, dirty-tree development pilot.

| Codec | Archive bytes | Ratio | Compress | Decompress | Peak RSS |
|---|---:|---:|---:|---:|---:|
| MathSVG Balanced | 7,136,397 | 1.4174x | 9.932 s | 1.230 s | 25.33 MiB |
| LZ4 | 8,886,079 | 1.1383x | 0.156 s | 0.151 s | 9.51 MiB |
| zstd-default | 6,269,278 | 1.6134x | 0.339 s | 0.157 s | 18.44 MiB |
| gzip-6 | 6,314,471 | 1.6018x | 0.732 s | 0.254 s | 1.99 MiB |
| XZ-9e | 4,606,124 | 2.1960x | 8.989 s | 0.631 s | 139.22 MiB |

MathSVG is 13.8312% larger than zstd-default, so the development-only G2 size
milestone remains inside its 15% limit. G1 is not met (ratio 1.4174x, below
2x), and the four-metric certificates pass 0/40 file comparisons and 0/4
corpus comparisons. Aggregate function nodes reconstruct 196,608 source bytes
(1.9438%); pre-entropy procedural selection saves 196,604 bytes, while native
entropy saves 2,788,560 bytes. The ratio is still predominantly literal
entropy, not proof of real structured procedural success.

## Full open validation v4

`validation-v4-final-binary` completed 2,200/2,200 scheduled rows over
all 40 open validation inputs, five codecs, one warm-up and ten measured
repetitions. Source identity stayed stable, no tuning was performed, every
archive round-tripped, and no holdout payload was opened.

| Codec | Archive bytes | Ratio | Compress | Decompress | Peak RSS |
|---|---:|---:|---:|---:|---:|
| MathSVG Balanced | 66,766,367 | 1.190387x | 80.744 s | 9.245 s | 26.29 MiB |
| LZ4 | 69,643,519 | 1.141209x | 0.634 s | 0.569 s | 9.52 MiB |
| zstd-default | 65,735,539 | 1.209054x | 1.242 s | 0.568 s | 52.59 MiB |
| gzip-6 | 65,399,882 | 1.215259x | 4.166 s | 1.177 s | 1.99 MiB |
| XZ-9e | 62,312,892 | 1.275464x | 57.039 s | 5.501 s | 617.79 MiB |

The certificate passes 0/160 per-file and 0/4 corpus comparisons. MathSVG is
1.56814% larger than zstd-default, 2.08943% larger than gzip and 7.14696%
larger than XZ; it is smaller than LZ4 but much slower and uses more memory.
Procedural coverage is 0%: all 12,747,826 saved bytes come from native entropy
coding of literal leaves. This validation therefore rejects a current
dominance or structured-procedural claim.

The final Gate 2 campaign covers all twelve required random/already-compressed
domains, Fast/Balanced/Max and two repetitions per cell. All 72
rows pass the exact integer bound, strict inspection, restored SHA-256 and
archive-identity checks. The worst row retains an eight-byte margin after
separating the declared literal envelope from the permitted 0.1% expansion.

Structured and Max now extend their finite function search to period 256 and
order-three recurrence coefficients `{0,1,3,253,255}`. The exact-generator
subgate consequently passes 6/6 rows with 100% function coverage and zero
literal leaves, including LFSR8, polynomial-d2 and Fibonacci recurrence. This
does not generalize to real data: the final 17-row Gate 4 campaign finds zero
function coverage and zero pre-entropy procedural gain in every selected
primary-real structured domain. Gate 4 therefore has a measured `failed`
status, not an unmeasured open status.

Gate 3 now passes on the measured source/configuration build. Across its
enwik9 measured rows, maximum encoder RSS is 157,425,664 bytes for Fast,
146,010,112 for Balanced and 304,930,816 for Max; maximum decoder RSS is
8,429,568 bytes. The artifact records binary SHA-256 `07bd81ab...`. The later
explicit final benchmark build is `8f1ca123...`, so the memory evidence is not
misrepresented as an exact run of that later binary.

## Gate ledger

| Gate | Current status | Required closing evidence |
|---|---|---|
| Correctness | partial | 175/175 Python research tests, Rust quality gates, sanitizer v6 and 3,200-row final local matrix pass; clean compiler/ARM64/second-machine cells remain open |
| Literal safety | pass locally | final 72/72 rows cover all 12 required controls, all built-in profiles, exact bound, SHA-256 and repeated archive identity |
| Memory | pass on measured build | enwik9 process-tree RSS for all profiles and decoder is below every strict limit; artifact binary is `07bd81ab...` |
| Procedural proof | failed current evidence | exact synthetic generators pass 6/6; all six measured primary-real domains have 0% winning function coverage and zero pre-entropy gain |
| General purpose | partial/fail | open-validation G2 passes; G1 and G3--G5 fail; the wider frozen baseline suite remains incomplete |
| Structured domain | open | at least two sealed, independent domain holdouts |
| Max | open | frozen XZ/Brotli/Zstd-19 comparison |
| Absolute stretch | failed current evidence | core-four certificate passes 0/160 file and 0/4 corpus comparisons; wider matrix incomplete |

The canonical section-29 output aliases, exact artifact hashes, oracle stop
policy and remaining completion work are consolidated in `final-status.md` and
`results/manifests/final-local-evidence.json`. An open gate is not treated as a
failed scientific result until its prescribed experiment runs, but it is also
never reported as satisfied.
