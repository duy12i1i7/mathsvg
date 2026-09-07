# Function DAG and shared definitions

## Canonical DAG

Nodes are stored in topological order. Node identity is the canonical tuple:

```text
(opcode, output_type, output_length, canonical_parameters, child_ids)
```

Exact duplicate nodes are hash-consed. A reference can target only a lower
node ID, so cycle detection is structural rather than an evaluator search.

## Sharing cost

For definitions \(D\), call sites \(S\) and residuals \(R_s\):

\[
L=L(\text{definition table})
+\sum_{s\in S}
\left[L(\text{reference}_s)+L(\Delta\theta_s)+L(R_s)\right].
\]

A definition is activated only if its definition bytes plus all calls are
strictly better than inlining, subject to the normative tie-break. Node-count
reduction without byte reduction is not a size win.

## PDPTG — Parameter-Delta Procedural Tree Grammar

The encoder finds isomorphic procedural subtrees whose canonical parameters
differ. Anti-unification creates a typed nonterminal with parameter holes.
Calls encode deltas relative to a canonical prototype. Candidate replacement
is accepted only after serialising the definition, call sites and corrections.

The grammar is exact because expansion substitutes stored typed parameters
into a previously validated acyclic subtree.

## ALTFB — Archive-Local Typed Function Basis

Candidate basis functions are extracted from the archive's own development
objects, never from external training. Selection is a deterministic
facility-location problem:

\[
\min
\sum_d y_dL(d)
+\sum_{s,d}z_{sd}
\left[L(\operatorname{call}_{sd})+C(R_{sd})\right],
\quad z_{sd}\le y_d,
\]

with a literal option for every segment. Exact ILP/enumeration is limited to
microblocks; bounded branch-and-bound is used for larger sets.

## SADH — Shared-Activation Description Hypergraph

Sharing introduces a fixed activation cost, so independent shortest paths are
insufficient. SADH uses an A* state containing the uncovered region frontier
and activated-definition set. Its admissible heuristic assumes every inactive
definition is free and sums independent per-region minima. This can only
underestimate remaining true cost.

### Implemented bounded microblock catalogue

`mathsvg-graph` implements the first exact SADH activation tier. It mines
aligned equal-width chunks, retains a deterministic bounded definition
catalogue, and enumerates every activation subset permitted by the profile.
For each subset it constructs the complete dense definition table, merges
adjacent literal regions, emits real block-local `REFERENCE` nodes and compares
the fully serialized one-block v1 archive with the literal archive under the
normative total order.

The implementation is exact only when both the mined catalogue and the full
subset space fit the declared budgets. Catalogue truncation is
`HEURISTIC_SKIP`; subset, work or ledger exhaustion is `BUDGET_STOP`. It emits
no `SAFE_PRUNE`, because the current solver has no nontrivial serialized lower
bound proof. Parameterized, unaligned and cross-block sharing remain gated.
This micro-solver is correctness and ablation infrastructure; broad DAG
emission remains disabled until a natural non-synthetic win pays the complete
activation cost.

The refreshed development oracle no longer uses only repeated in-memory
controls. It runs the native graph solver on each frozen natural 4 KiB sample
and compares its complete archive against the current native function/entropy
winner. Rows retain mined/omitted definitions, evaluated subsets, work,
ledger, budget and completeness fields. A definition is not a win unless that
incremental comparison is positive.

The engine is wired to `mathsvg-optimizer` through the separate
`WholeBlockCandidateProvider` boundary. This is intentionally not an interval
`Node` adapter: the winner keeps its complete definition table and competes as
a fully serialized one-block `Program`. The optimizer parses, evaluates and
hash-verifies it again, then re-serializes the selected programs into the
complete archive. Graph source coverage counts only bytes actually restored
through references; any non-referenced remainder stays literal coverage.

## Safety and limits

Definition count, node count, fan-out, call count, dependency depth,
parameter-delta bytes and aggregate expanded work are checked before
evaluation. A graph with exponentially repeated references is rejected when
its preflight work/output budget exceeds limits, even if its archive is small.
