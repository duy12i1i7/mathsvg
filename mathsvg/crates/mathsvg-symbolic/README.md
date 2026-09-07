# mathsvg-symbolic

This crate is the bounded engine needed to resolve the Phase-3
`requires_engine` cell for RSEE (Residual-Sensitive Equality Enumeration).
It enumerates a finite integer-only byte-expression catalogue, evaluates every
expression exactly, and measures both exact expressions and expressions plus
an exactly represented ADD/XOR residual as complete v1 one-block programs.

The implementation is deliberately microblock-only. State, depth, pair,
function-search work and ledger budgets are hard and deterministic. Semantic
deduplication is reported; budget exhaustion is never called optimal. The
result always retains the stronger of the native function-search winner and
the literal fallback.

General profile emission remains gated until complete-byte gain exceeds 0.5%
and at least one natural non-synthetic block wins. There is no e-graph,
floating-point rewrite, stochastic synthesis or external model in this phase.
