# MathSVG Absolute

MathSVG Absolute is the successor research line to MathZip. It represents a
byte stream with a bounded, deterministic procedural function DAG plus an
exact recursively modelled remainder. Literal bytes are always available as
the final leaf, but they are not the primary representation when a shorter
native procedural description exists.

This directory is intentionally separate from the legacy `mathzip-*` crates.
Legacy artifacts remain immutable baseline evidence; MathSVG uses a new DSL,
container magic, evaluator and cost model. Native MathSVG never embeds a Zstd,
XZ, Brotli or other external baseline archive.

## Non-negotiable invariants

- Exact round trip and original SHA-256 verification.
- Identical archive bytes for the same input and configuration.
- Integer, modular, finite-field, bounded rational or normatively specified
  fixed-point semantics only.
- No ML, learned model, external API, stochastic search or platform-dependent
  floating point.
- Every final candidate is compared by the bytes actually serialized.
- Every parser loop, allocation, graph dependency and evaluator operation is
  bounded before execution.
- Literal-only blocks form a complete representation and the exact archive
  fallback.
- Claims are limited to the frozen DSL, candidate set and search budget.

## Phase status

| Phase | State | Evidence |
|---|---|---|
| 1. Reproduce MathZip baseline | complete | `docs/phases/phase-01-reproduce.md` |
| 2. Formal specification | complete | `docs/phases/phase-02-formal-spec.md` |
| 3. Oracle | refreshed; DAG complete, remaining families bounded-inconclusive | `docs/oracle-analysis.md`, `results/oracle/stop-policy.csv` |
| 4. MVP engine | complete for v1 native blocks | `crates/mathsvg-*` |
| 5--8. Coordinate/residual/DAG/symbolic engines | implemented, evidence-gated | `docs/oracle-analysis.md` |
| 9. Measured algorithm integration | development v8 complete | `docs/phases/phase-09-development-v5.md`, `results/raw/development-pilot-v8-final-binary-run.json` |
| 10. Streaming/SIMD | bounded streaming and scalar/SIMD decode implemented; Gates 2 and 3 pass locally | `docs/phases/phase-10-validation-overhead.md`, `docs/determinism.md` |
| 11. Structured domains | exact synthetic subgate passes; real procedural and validation dominance gates fail | `docs/phases/phase-12-final-local-audit.md` |
| 12. Repository/local audit | implementation and section-29 artifact bundle complete; acceptance incomplete | `docs/final-status.md` |
| 13--14. Holdout/final report | not started by design | requires a clean freeze and independent machines |

Dataset split validation, experiment freezing, randomized/interleaved trial
scheduling, confidence statistics and conservative dominance certificates live
under `python/`.  Checked-in profile budgets are development defaults until a
publishable freeze record pins their hashes.

The governing specification is the repository-level `YeuCau.md`.

The latest development pilot is `development-pilot-v8-final-binary`; the
latest complete open validation is `validation-v4-final-binary`. Validation
scheduled all 2,200 rows successfully, but the four-metric certificate is
0/160 per-file and 0/4 corpus comparisons. Gate 2 literal safety passes 72/72
control rows, Gate 3 enwik9 memory passes, and Gate 4 fails on primary-real
structured domains. The canonical section-29 paths now point byte-for-byte to
the final local open-validation evidence. Sealed holdout payloads remain
untouched until a clean commit/configuration freeze and independent machine
are available.

## Current benchmark artifacts

- Canonical raw rows and provenance: `results/raw/benchmark.jsonl` and
  `results/raw/benchmark-run.json`.
- Canonical aggregate/procedural tables: `results/summary/benchmark.csv` and
  `results/summary/procedural-breakdown.csv`.
- Canonical per-file/per-corpus certificate and failures:
  `results/dominance/`.
- Latest development profiling and plots:
  `results/profiling/development-pilot-v8-final-binary.csv` and
  `results/plots/development-pilot-v8-final-binary-*.svg`.
- Final local acceptance evidence:
  `results/manifests/final-local-evidence.json` and
  `docs/final-status.md`.

These are versioned local development/open-validation artifacts, not a final
holdout result or a dominance claim. The project is not acceptance-complete:
neither a passing dominance certificate nor a finite no-headroom oracle proof
currently exists.
