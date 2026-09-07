# MathSVG Absolute local completion audit

Status date: 2026-08-02

Status: the implementation and the reproducible local evidence bundle are
complete for the work that can be performed in this workspace. The project
does **not** satisfy the final acceptance condition in `YeuCau.md`: no
dominance certificate passes, and the finite oracle does not prove that all
remaining representation/search headroom is zero.

This distinction is deliberate. A complete local engineering bundle is not a
claim that MathSVG Absolute has achieved its research stretch goal.

## Original objective

MathSVG represents an arbitrary byte stream as a bounded, deterministic,
self-contained procedural function DAG, coordinate descriptions, parameters
and recursively exact corrections, with literal leaves as the universal
fallback. The requested finish condition is one of:

1. a reproducible certificate showing that a MathSVG profile Pareto-dominates
   every frozen baseline/configuration on the frozen suite; or
2. an oracle proof that no further headroom exists inside the current finite
   representation and search space.

Neither finish condition is currently true.

## Evidence identity

- branch: `mathsvg-absolute`;
- predecessor commit: `36f84685dc00064dae9a239face7e8eff797ed28`;
- final local benchmark binary:
  `8f1ca1238a4c2d6495b7cff729ce6ecb34da3f844a557ea7b90b0d9d52a10787`;
- validation manifest canonical SHA-256:
  `eb89ca9bad1783e5aada0b0b7b949e1097f0f3dfb84dcd87dd1006670a8e8e5e`;
- baseline inventory file SHA-256:
  `ba6469bd1cf0dd5864f96f564ab5fc5ad98350c314a961d91683b6e3392655e0`;
- canonical evidence manifest:
  `results/manifests/final-local-evidence.json`.

The source tree is dirty, so this is not a publishable clean freeze. Every
reported campaign captures its own before/after source identity and reports a
stable tree during that campaign. The enwik9 memory campaign predates the
explicit final binary rebuild and records binary SHA-256 `07bd81ab...`; it
uses the same source/configuration semantics, but is not represented as an
exact measurement of binary `8f1ca123...`.

No holdout payload was opened.

## Corpus and baseline coverage

The open corpus satisfies the section-20 composition rule:

- development: 69 files, 49 qualifying primary-real files (71.0145%),
  99.9185% qualifying primary-real bytes;
- validation: 40 files, 37 qualifying primary-real files (92.5%), 99.9012%
  qualifying primary-real bytes;
- combined: 109 files, 86 qualifying primary-real files (78.8991%), 99.9176%
  qualifying primary-real bytes;
- all 57 required dataset categories have selected evidence and zero recorded
  coverage gaps.

The frozen inventory contains 21 baseline rows. Twelve are available on this
host: raw, LZ4, gzip, bzip2, zstd-default, zstd-19, Brotli-6, Brotli-11,
XZ-9e, 7-Zip LZMA2, ZPAQ and FLAC. Nine are explicitly unavailable with a
reason: Snappy, CMIX/PAQ, PNG, JPEG-LS, JPEG XL lossless, LAZ, Parquet/ORC,
time-series lossless and scientific lossless.

The full validation campaign measures the four core general-purpose
baselines: gzip-6, LZ4, XZ-9e and zstd-default. The other available adapters
are inventoried and tested at adapter level, but do not have a full 40-file,
ten-repetition validation campaign. Consequently, the complete frozen
baseline matrix remains open even before the unavailable domain adapters are
considered.

## Final open-validation benchmark

`validation-v4-final-binary` ran all 40 validation inputs with one warm-up and
ten measured repetitions, blocked randomized/interleaved order, one thread,
five codecs and no tuning. It produced 2,200/2,200 successful rows, zero
failures, zero timeouts and zero unavailable rows. Source identity and inputs
were stable, all round trips passed and no holdout payload was opened.

| Codec/profile | Archive bytes | Ratio | Compression median | Decompression median | Compression peak RSS |
|---|---:|---:|---:|---:|---:|
| MathSVG Balanced | 66,766,367 | 1.190387x | 80.744 s | 9.245 s | 27,561,984 B |
| LZ4 | 69,643,519 | 1.141209x | 0.634 s | 0.569 s | 9,986,048 B |
| zstd-default | 65,735,539 | 1.209054x | 1.242 s | 0.568 s | 55,142,400 B |
| gzip-6 | 65,399,882 | 1.215259x | 4.166 s | 1.177 s | 2,084,864 B |
| XZ-9e | 62,312,892 | 1.275464x | 57.039 s | 5.501 s | 647,796,736 B |

The MathSVG archive mapping is identical in all 400 measured native rows to
the preceding validation-v3 campaign. The canonical raw artifact SHA-256 is
`c405f8a6362492073b9d5d6e7818215b31f5755ed80ab1a05b164f575a776788`.

## Dominance result

The certificate passes 0/160 per-file comparisons and 0/4 corpus comparisons.
At corpus level:

- gzip-6: MathSVG is 1,366,485 bytes (2.08943%) larger, 1,838.26% slower to
  encode, 685.435% slower to decode and uses 1,222% more peak RSS;
- LZ4: MathSVG is 2,877,152 bytes smaller, but 12,645.3% slower to encode,
  1,523.52% slower to decode and uses 175.666% more peak RSS;
- XZ-9e: MathSVG is 4,453,475 bytes (7.14696%) larger, 41.5609% slower to
  encode and 68.0744% slower to decode; memory is better;
- zstd-default: MathSVG is 1,030,828 bytes (1.56814%) larger, 6,401.29%
  slower to encode and 1,528.56% slower to decode; memory is better.

The canonical certificate is under `results/dominance/`. It is a reproducible
failure certificate, not a dominance claim.

## Acceptance gate matrix

| Gate | Status | Evidence and blocker |
|---|---|---|
| 1 — Correctness | incomplete globally; local matrix passes | 3,200/3,200 local rows are `ok`, one archive hash, zero failures/timeouts; 30,000 sanitizer runs pass. Dirty source, one compiler identity, one x86-64 host and no ARM64/second machine prevent the publishable gate. |
| 2 — Literal safety | pass | 72/72 rows pass the exact `0.1% + fixed envelope` bound across all 12 control domains and Fast/Balanced/Max. |
| 3 — Memory | pass for measured source/config build | enwik9: Fast max encoder RSS 157,425,664 B; Balanced 146,010,112 B; Max 304,930,816 B; decoder max 8,429,568 B. All are below their strict limits and all round trips pass. Exact artifact binary is `07bd81ab...`, not the later rebuild `8f1ca123...`. |
| 4 — Procedural proof | fail | Six exact synthetic generators pass with 100% function coverage and zero literal leaves. Zero of six measured primary-real domains has positive pre-entropy gain with at least 50% function coverage. |
| 5 — General purpose | partial/fail | G1 fails (1.190387x < 2x); G2 passes (only 1.56814% larger than zstd-default); G3, G4 and G5 fail on open validation. |
| 6 — Structured holdout | not run | Requires at least two sealed domain holdouts after a clean freeze and on an independent machine. Holdout remains unopened. |
| 7 — Max | incomplete | Max enwik9 archive is 409,218,082 B, but no same-campaign XZ-9e/Brotli-11/zstd-19 minimum and speed/memory comparison exists. |
| 8 — Absolute stretch | fail | The core-four validation certificate already has zero dominated baselines; the wider frozen baseline matrix is also incomplete. |

## Oracle stop-policy result

The refreshed production-API oracle contains 13 search rows, 73 real
coordinate rows, 13 segmentation rows, 39 residual-depth rows, 42 function
family rows, 13 symbolic rows and 13 DAG rows. Its explicit stop policy is:

- DAG sharing: 13/13 rows complete, zero wins; emission is stopped;
- coordinate basis: 0/73 rows complete, zero observed wins; emission is
  stopped for the measured profile, but this is not a global no-headroom
  proof;
- segmentation: 0/13 complete, one synthetic win worth 61 bytes; deeper
  search remains bounded-inconclusive;
- symbolic depth two: 0/13 complete, zero observed wins; profile disabled,
  bounded-inconclusive;
- recursive residual depth two: 0/13 complete, one real 4 KiB win worth 115
  bytes (0.581807%); retained experimentally. A paired whole-file Calgary
  `progc` production ablation measured exactly zero archive-byte benefit, so
  built-in profiles keep residual emission disabled.

The separate complete LZ parser oracle selects C4L. C8L and C16L save an
additional 87,952 and 138,815 aggregate bytes over C4L on its development-real
sample, but require greater directional search time. This is real finite
headroom, yet it is too small to close the validation size gap by itself and
moves encode speed farther away from dominance.

Therefore the second permitted finish condition—an oracle proof of no
remaining headroom—is false.

## Verification state

- Python research/benchmark suite: 175/175 tests pass;
- legacy MathZip Python suite: 55 pass, 2 expected skips (optional matplotlib
  and clean-checkout-only release smoke);
- Rust workspace debug and release tests: pass;
- optimizer suite: 31 pass, 1 explicitly ignored exhaustive test;
- `cargo fmt --check`: pass;
- `cargo clippy --workspace --all-targets -- -D warnings`: pass;
- fuzz crate `cargo check`: pass;
- AddressSanitizer fuzz campaigns: 10,000 runs each for decode, entropy and
  round trip; 30,000 total, zero failures.

The mathematical display line `=======` in the user-owned `YeuCau.md` makes
`git diff --check` report a conflict-marker-style warning; it is equation
formatting, not an unresolved merge conflict.

## Canonical outputs

All paths required by section 29 now exist. Generic names are byte-identical
copies of their versioned evidence:

- `results/raw/benchmark.jsonl` and `results/raw/benchmark-run.json` →
  validation v4;
- `results/summary/benchmark.csv` and
  `results/summary/procedural-breakdown.csv` → validation v4;
- `results/dominance/{per-file,per-corpus,pareto-envelope,failures}.csv` →
  validation v4;
- `results/determinism/results.csv` → determinism v4;
- `results/ablation/ablation.csv` → the broad 72-row paired ablation suite;
  the later interval-pruning and residual whole-file experiments remain
  separately versioned;
- oracle aliases are generated and hash-checked by
  `results/manifests/oracle-manifest.json`.

`python/tests/test_final_evidence.py` checks required-path existence,
byte-identical aliases, every declared size/hash and the rule that local
evidence cannot be promoted to final acceptance.

## Work required for actual project completion

The remaining work is not a hidden local checklist:

1. Commit/freeze the implementation, DSL, profiles, budgets, tie-breaks,
   manifests and baseline commands on a clean tree.
2. Rebuild with cryptographic compiler/build attestation and repeat Gate 1 on
   a second compiler, ARM64 and an independent second machine.
3. Run the complete available baseline matrix, and provide/install the nine
   missing codec/schema adapters where a fair byte-stream comparison exists.
4. Improve representation and runtime enough to reverse the measured size,
   encode and decode blockers. Deeper LZ search alone cannot produce
   four-metric dominance.
5. Alternatively, fully exhaust every remaining finite oracle row and remove
   the observed residual/deeper-LZ headroom; current bounded-incomplete rows
   cannot support this proof.
6. Only after a clean freeze, authorize and run the sealed structured holdout
   on an independent machine. Do not tune after opening it.
7. Regenerate the full dominance envelope. The project is complete only if a
   certificate passes or a valid finite no-headroom proof exists.

Until then, the scientifically correct conclusion is:

> MathSVG is a functioning, bounded and reproducibly benchmarked native codec.
> It succeeds on exact synthetic procedural generators and passes local
> literal-safety and memory gates, but it does not yet procedurally explain
> real structured data and does not Pareto-dominate the frozen baselines.
