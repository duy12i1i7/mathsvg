# Phase 10 — Streaming validation-overhead removal

```text
PHASE
- Commit: 36f84685dc00064dae9a239face7e8eff797ed28 + dirty development tree
- Status: native optimization and local evidence complete; not publishable and not holdout

CORRECTNESS
- Tests: debug/release Rust workspace, Python suite and strict Clippy pass; sanitizer v4 passes 10,000 runs on each native target
- Round-trip: 550/550 development-v6 rows and 2,200/2,200 validation-v2 rows successful
- Determinism: all 40 validation inputs retain exactly the v1 archive SHA-256 after the refactor
- Fuzz: development-sanitizer-v4-validation-overhead reports 30,000/30,000 finite sanitizer executions and zero failures

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
- Compression MB/s: 1.015169
- Decompression MB/s: 8.287223
- Peak RSS: 25.289 MiB

BASELINES
- Zstd: 1.613396x, 30.192229 MB/s encode, 62.659720 MB/s decode
- XZ: 2.195952x, 1.123504 MB/s encode, 15.984073 MB/s decode
- Brotli: not run in this pilot
- LZ4: 1.138278x, 66.085974 MB/s encode, 65.311799 MB/s decode
- Domain codec: not run in this pilot

ORACLE
- Remaining search headroom: bounded-incomplete; no global zero-headroom proof
- Coordinate headroom: 0 bytes on 73 frozen real blocks; rows remain bounded-incomplete
- Residual headroom: 115 bytes on one 4 KiB real sample; 0 bytes in the earlier whole-file integration
- Symbolic headroom: 0 bytes in the bounded native sample; not a global proof
- DAG-sharing headroom: 0 bytes on 13/13 complete natural-sample rows

DOMINANCE
- Dominated: none under the four-metric certificate
- Not dominated: gzip-6, LZ4, XZ-9e, zstd-default
- Blocking metrics: size except versus LZ4; encode/decode time broadly; RSS except versus XZ

DECISION
- Keep: single-pass uncommitted entropy decode, container-validated evaluator entry point, shared block preparation
- Remove: repeated strict DSL/entropy validation and one-block encode/decode round-trip inside stream encoding
- Pivot: representation/entropy headroom now dominates the remaining external gap; do not trade safety for more validation shortcuts
- Next: full validation, Gate 2 controls, enwik9 Gate 3, external architecture/machine matrix, then clean freeze
```

## Change boundary

The refactor does not weaken the public untrusted-input APIs. `inspect` and the
ordinary decode entry points still validate a complete entropy payload before
returning success. The new resource-only preflight is used only after the DSL
program has already passed strict container validation. The one-pass entropy
decoder writes into a private uncommitted buffer, which is discarded on any
error and is not publishable until block and whole-archive hashes pass.

The stream encoder now prepares the canonical block payload directly. It no
longer constructs a complete one-block archive and immediately decodes that
archive merely to recover the payload and metadata. The in-memory and streaming
paths share the same `prepare_block` implementation and retain identical wire
bytes.

## Focused before/after evidence

The focused Silesia `x-ray` experiment used one warm-up and ten measured
repetitions on the same 8,474,240-byte input. It is a sequential before/after
development comparison, not an interleaved ablation and not publishable timing
evidence.

| Build | Archive bytes | Compression median | Decompression median | Peak RSS |
|---|---:|---:|---:|---:|
| Before | 6,801,499 | 13.169778 s | 2.054370 s | 25,585,664 B |
| Final | 6,801,499 | 8.860523 s | 1.020449 s | 26,527,744 B |

The archive SHA-256 stayed
`734bc30d...` in both experiments. The paired-by-repetition directional mean
was -32.7425% compression time and -50.9565% decompression time, while total
peak RSS rose 3.6666%; because the two variants were run sequentially, those
intervals are diagnostic rather than a randomized interleaved claim.

## Development pilot v6

`development-pilot-v6-validation-overhead` completed all 550 scheduled rows,
with stable source identity and the same 7,136,397-byte MathSVG aggregate as
v5. Compression median fell from 14.6469 s to 9.9637 s and decompression median
from 2.3576 s to 1.2210 s. The four-metric result remained 0/40 per-file and
0/4 corpus certificates, so the optimization is retained for efficiency, not
reported as dominance.

Provenance:

- raw JSONL SHA-256:
  `1efc55827a4576d6ffd332aff28140c2afac16575fcc4b0be302b46f4ae830b3`;
- run metadata SHA-256:
  `50605bf76535f516bff67371bb0c4491c7807aa69d65e6c8c5d86788e4d2322e`;
- measured executable SHA-256:
  `6d5c87aaa6a44173eb34bd9007fc68ad23375d8286ae3ef05239b11509bb84a1`;
- sanitizer report SHA-256:
  `967b52c4ab2a98798f381e086cf87693f72f66b6b9a33ecbab48599074de06ca`.

## Versioned artifacts

- `results/raw/development-validation-overhead-before-v1.jsonl`
- `results/raw/development-validation-overhead-after-v2.jsonl`
- `results/summary/development-validation-overhead-*-benchmark.csv`
- `results/raw/development-pilot-v6-validation-overhead.jsonl`
- `results/raw/development-pilot-v6-validation-overhead-run.json`
- `results/summary/development-pilot-v6-validation-overhead-benchmark.csv`
- `results/summary/development-pilot-v6-validation-overhead-procedural.csv`
- `results/profiling/development-pilot-v6-validation-overhead.csv`
- `results/dominance/development-pilot-v6-validation-overhead/`
- `results/plots/development-pilot-v6-validation-overhead-*.svg`
- `results/fuzz/development-sanitizer-v4-validation-overhead/report.json`
