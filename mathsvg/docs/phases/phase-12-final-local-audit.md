# Phase 12 — Final local implementation and evidence audit

```text
PHASE
- Commit: 36f84685dc00064dae9a239face7e8eff797ed28 + dirty MathSVG development tree
- Status: local implementation/evidence bundle complete; project acceptance incomplete; holdout unopened

CORRECTNESS
- Tests: 175/175 Python research tests and 55 legacy tests pass (2 expected skips); Rust debug/release, fmt, clippy and fuzz check pass
- Domain codec: 12/21 frozen baseline rows available; four core general-purpose baselines measured in full validation
- Determinism: 3,200/3,200 local rows pass with one archive SHA-256; external compiler/ARM64/machine matrix incomplete
- Sanitizer: 30,000/30,000 finite runs pass with zero artifacts

ORACLE
- Remaining search headroom: bounded-incomplete; no global zero-headroom proof
- Coordinate headroom: 0 observed bytes across 73 real rows, all bounded-incomplete
- Residual headroom: 115 bytes (0.581807%) on one real 4 KiB row; zero whole-file production gain
- Symbolic headroom: 0 observed bytes across 13 bounded-incomplete rows
- DAG-sharing headroom: 0 bytes across 13/13 complete rows; emission stopped

DOMINANCE
- Dominated: none
- Not dominated: gzip-6, LZ4, XZ-9e, zstd-default
- Blocking metrics: size versus gzip/Zstd/XZ; encode/decode time versus all; RSS versus gzip/LZ4
- Certificate: 0/160 file rows and 0/4 corpus rows pass

DECISION
- Keep: native v1 codec, bounded evaluator/streaming, C4L entropy parser, literal fallback, exact synthetic function catalogue
- Remove: DAG emission; built-in coordinate/interval-function/residual/symbolic emission where measured stop gates fail
- Pivot: representation/runtime research is required; search-only deepening cannot close four-metric gaps
- Next: clean freeze, external determinism/baseline matrix, then independent holdout only if the freeze is valid
```

## Final local artifact set

The final open validation is `validation-v4-final-binary`: 2,200/2,200 rows
are successful, source/input identity is stable, and the final benchmark
binary SHA-256 is `8f1ca1238a4c2d6495b7cff729ce6ecb34da3f844a557ea7b90b0d9d52a10787`.
The canonical aliases required by section 29 are generated and checked by
`python/tests/test_final_evidence.py`. Their hashes and versioned sources are
recorded in `results/manifests/final-local-evidence.json`.

Gate 2 passes 72/72 rows. Gate 3 passes on the measured source/config build;
its enwik9 artifact records binary `07bd81ab...`, so it is not mislabeled as
an exact run of the later `8f1ca123...` rebuild. Gate 4 fails because the six
real structured domains have zero winning function coverage and zero
pre-entropy procedural gain. Gate 5 G2 passes, while G1 and G3--G5 fail.
Gates 6 and 7 remain incomplete, and Gate 8 fails.

The full gate matrix, benchmark table, oracle interpretation and remaining
completion work are in `docs/final-status.md`.
