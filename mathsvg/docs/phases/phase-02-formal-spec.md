# Phase 2 — Formal specification

```text
PHASE
- Commit: working tree on mathsvg-absolute; publishable commit not frozen
- Status: complete

CORRECTNESS
- Tests: specification arithmetic reviewed; Python governance/protocol tests pass
- Round-trip: proof obligations defined; new engine not implemented in this phase
- Determinism: canonical integers, ordering and tie-break defined
- Fuzz: required per opcode; execution begins with MVP implementation

PROCEDURAL REPRESENTATION
- Function graph bytes: not measured
- Coordinate bytes: not measured
- Parameter bytes: not measured
- Residual bytes: not measured
- Literal bytes: not measured
- Procedural coverage: not measured
- Procedural gain: not measured

PERFORMANCE
- Ratio: not measured
- Compression MB/s: not measured
- Decompression MB/s: not measured
- Peak RSS: block bounds specified; implementation not measured

BASELINES
- Zstd: external-only; forbidden as native payload
- XZ: external-only; forbidden as native payload
- Brotli: external-only; forbidden as native payload
- LZ4: external-only; forbidden as native payload
- Domain codec: external-only; exact commands remain to be frozen

ORACLE
- Remaining search headroom: Phase 3
- Coordinate headroom: Phase 3
- Residual headroom: Phase 3
- Symbolic headroom: Phase 3
- DAG-sharing headroom: Phase 3

DOMINANCE
- Dominated: none claimed
- Not dominated: no new-engine measurements exist
- Blocking metrics: all four core metrics and independent machines

DECISION
- Keep: native bounded procedural DAG, recursive exact correction, exact cost
- Remove: MZIP reinterpretation, baseline payloads, floating/stochastic search
- Pivot: block-local DAG in container v1; cross-block sharing deferred to new version
- Next: execute all Phase 3 oracles before deep model implementation
```

## Normative artifacts

- `impossibility-boundary.md` fixes what a finite procedural DSL cannot
  universally beat and requires literal fallback.
- `core-thesis.md` defines the native representation claim and prohibited
  meta-codec shortcuts.
- `mathematical-spec.md` defines value domains, exact evaluation, candidate
  cost identity, tie-break and proof obligations.
- `procedural-dsl.md` assigns the v1 opcode registry and staged implementation.
- `format-spec.md` freezes a streaming block container: 128-byte file header,
  144-byte directory record, 72-byte block header and 128-byte footer.
- `coordinate-discovery.md`, `recursive-residual.md`,
  `function-graph.md` and `symbolic-search.md` define the later optimizer
  layers and their bounded search contracts.
- `determinism.md` and `benchmark-methodology.md` define the evidence required
  before any performance or dominance claim.

## Format decision

Every v1 block is an independent closed program.  Definitions, references and
copy dependencies are local to one block.  This gives bounded sequential
decode and random access without keeping archive-global graph state.  Global
repository sharing, if later justified by oracle evidence, requires an
explicit later container version rather than an ambiguous flag.

The exact literal archive cost for `k` non-empty blocks with root sizes `r_i`
is:

```text
256 + 216*k + sum(r_i)
```

Thus topology, directory and block-envelope overhead participate in actual
candidate competition.  A per-node estimate may screen a candidate but cannot
declare a winner.

## Remaining formal dependency

Golden archive bytes are intentionally not frozen yet.  The DSL still needs a
numeric per-opcode work/workspace table implemented by the evaluator.  A
golden vector created before that table would embed invented accounting rather
than a validated wire contract.  Phase 4 must close this item before format v1
is called implementation-stable.
