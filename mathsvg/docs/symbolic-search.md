# Symbolic search

## RSEE — Residual-Sensitive Equality Enumeration

Traditional exact synthesis keeps expressions that exactly equal a target or
the cheapest representative of an equivalence class. MathSVG also needs an
expression that is imperfect but leaves a cheap exact residual.

For expression \(E\), RSEE records:

\[
\left(
L(E),
L(\operatorname{Represent}(X\ominus E)),
W_d(E),
M_d(E)
\right).
\]

Each typed semantic class retains a Pareto frontier under those four values.
An expression is removed only when another expression is no worse in every
component under the same domain, length and residual operation.

## Enumeration

- bottom-up by exact serialized expression cost;
- typed operators and bounded constants;
- canonical commutative child order;
- constant folding and versioned algebraic rewrites;
- semantic digest on the current microblock;
- exact evaluation with integer/modular semantics;
- deterministic maximum depth, operator count, state count and work units.

Microblocks use exhaustive enumeration within the frozen DSL. Larger blocks
use the same ordered catalogue with explicit `BUDGET_STOP`.

## E-graph use

Equality saturation is optional search infrastructure, not archive semantics.
Every rewrite is versioned and proven valid in its arithmetic domain.
Extraction still minimises complete expression plus recursive residual bytes.
Platform-dependent floating-point rewrites are forbidden.

## Lower bounds

The minimum possible remaining cost is at least the mandatory node envelope
and zero content bytes. Stronger admissible bounds can use type/length-specific
minimum operator and literal framing. Entropy and fit scores are ordering
heuristics only.

## Stop condition

Symbolic synthesis is removed from a profile if its oracle gain is below 0.5%,
it wins no non-synthetic block, or it costs more than twice the search time for
less than 0.5% gain. Synthetic-only exact formulas are retained as correctness
tests but do not justify a general-purpose profile.

## Implemented bounded engine

`mathsvg-symbolic` now resolves the former `requires_engine` cell for a frozen
microblock catalogue. It enumerates canonical constant, linear and periodic
leaves plus bounded ADD/XOR compositions, evaluates every expression with the
normative integer evaluator, and constructs both exact-expression candidates
and expression-plus-exact-residual candidates. Residuals are represented by
the native finite function search, and every retained winner is a complete
serialized v1 program compared against both the literal and direct-function
upper bounds.

Depth, semantic states, expression pairs, function work and ledger rows are
hard-capped. The current development oracle records all of those counters per
input and compares the result with the Canonical/LZ-Huffman-aware native
function winner. Equal-semantic state removal is currently logged as
`HEURISTIC_SKIP`, rather than overstating the proof needed for `SAFE_PRUNE`;
all budget exits are `BUDGET_STOP`. A two-component synthetic diagnostic
produces a strictly smaller residual-sensitive archive and exact evaluator
round-trip, but that synthetic result cannot enable a profile. The emitting
profile remains disabled unless the generated oracle shows both at least
0.5% complete-byte gain and a non-synthetic winning block.

`mathsvg-optimizer::SymbolicProvider` is the explicit ablation path from that
engine into real archive competition. A winner with a retained symbolic
expression is submitted as a native interval node, independently
round-tripped, and reported as symbolic rather than residual coverage. The
provider is disabled in every built-in profile; `experimental_all` is an
explicit opt-in and does not change the oracle conclusion.
