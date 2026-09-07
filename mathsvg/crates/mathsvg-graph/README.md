# mathsvg-graph

This crate implements the exact microblock portion of SADH
(Shared-Activation Description Hypergraph). It mines aligned, exact repeated
chunks, then enumerates every activation subset in a bounded canonical
catalogue. Each subset pays its complete definition table, reference, root and
v1 one-block envelope bytes; the winner uses the normative `CandidateCost`
total order.

The solver is exact only inside the retained chunk catalogue and completed
subset budget. Catalogue truncation is reported as `HEURISTIC_SKIP`; work,
subset or ledger exhaustion is reported as `BUDGET_STOP`. This implementation
does not emit `SAFE_PRUNE` because it currently has no stronger admissible
serialized lower bound.

Parameterized definitions, unaligned substring mining, cross-block references
and repository-global activation remain profile-gated. A caller must retain
the literal winner unless natural-data validation clears those gates.
