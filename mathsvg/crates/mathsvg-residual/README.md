# mathsvg-residual

`mathsvg-residual` is the bounded native implementation of ARPL
(Algebraic Residual Projection Lattice). It composes native Procedural DSL
nodes only; no external residual payload or learned model is admitted.

At residual state `R`, a finite canonical catalogue proposes `CONST`,
`LINEAR`, `PERIODIC`, and restored-prior `RECURRENCE` projections `P`.
Corrections are exact:

```text
ADD: next[i] = R[i] - P[i] mod 256
XOR: next[i] = R[i] XOR P[i]
```

The emitted node applies the inverse operation through `ADD(P, next)` or
`XOR(P, next)`. Projection values and complete winners are evaluated by
`mathsvg-evaluator`; complete candidate bytes are produced by
`mathsvg-container`, not estimated from entropy.

Each state evaluates literal and `mathsvg-functions` leaf options. A branch
may deepen only when its best complete wrapped archive beats the complete
parent leaf by the configured byte margin. A winner on another branch does
not suppress that local decision; it is used only as the global output upper
bound. Search has
hard depth, visited-state, live-state-byte, work, projection-catalogue and
ledger limits. Repeated-state detection compares the complete residual bytes
against every state on the active path; the stable digest in the ledger is
diagnostic only and never substitutes for full equality.

The work counter has a narrow deterministic definition: catalogue
construction, projection evaluation/residual formation, and nested
function-fitting units. Complete canonical serialization is mandatory for
every measured candidate but is bounded by the state, ledger and decoder
limits rather than charged as a wall-clock instruction estimate.

## Oracle gate

The refreshed finite Phase 3 oracle found one real 4 KiB development winner:
depth two saved 115 complete-archive bytes (3.672947940%) on Calgary `progc`,
or 0.581807144% across the 13-sample oracle. This passes the development
retention threshold, but the production whole-file paired integration then
measured exactly zero archive-byte effect on the same source file. Therefore
`ResidualConfig::default()` still uses `OracleDisabled` and depth zero, while
recursive emission remains available only through the explicit
`Experimental` gate for later validation.

Minimum-gain, catalogue and budget cuts are logged as heuristic or budget
stops. This crate does not invent `SAFE_PRUNE` for recursive branches: no such
lower bound has yet been implemented. It does preserve `mathsvg-functions`
leaf `SAFE_PRUNE` rows only when their exact nested entry carries the
admissible serialized lower-bound proof.

This is not coordinate discovery, segmentation, DAG sharing, symbolic search,
or a claim that ARPL has passed a release/holdout gate.
