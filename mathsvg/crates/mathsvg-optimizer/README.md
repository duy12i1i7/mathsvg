# mathsvg-optimizer

This crate is the bounded exact-segmentation foundation for RC-BasisDP.

It always serializes the canonical literal archive first. Non-empty candidate
blocks are partitioned on a canonical microblock grid. A finite set of
provider nodes is independently validated, evaluated, and serialized for each
interval. The forward acyclic DP accounts for the exact bytes of every leaf
record and every canonical `SPLIT` boundary; every completed path is then
serialized as a real block and compared with the full normative tie-break.

After all chosen blocks are serialized into a complete archive, the optimizer
compares that archive again with the universal literal archive. It returns the
literal archive unless the procedural candidate strictly wins the complete
`CandidateCost`. Both archive candidates are decoded through
`mathsvg-container` and evaluated through `mathsvg-evaluator`, including
restored block and whole-file SHA-256 verification.

## Bounded search

`OptimizerConfig` has deterministic hard caps for:

- verified interval candidates;
- DP states;
- declared work units;
- retained audit-ledger entries;
- segments per block.

Budget decisions never use elapsed time. The optimizer currently performs no
admissible branch pruning, so it emits no `SAFE_PRUNE` event. Future pruning
may use that event only with a retained serialized-byte lower-bound proof.

## Engine portfolio and provider APIs

`CandidateProvider` is intentionally expressed in terms of canonical keys and
native DSL `Node` values. A provider receives the exact remaining
candidate/work budget and must return a finite deterministic catalogue.
`CoordinateProvider`, `ResidualProvider`, and `SymbolicProvider` are native
adapters for the Phase 5, 6, and 8 engines.

Graphs require a distinct `WholeBlockCandidateProvider` boundary because their
definition tables are part of a complete `Program`; flattening them into a
child node would lose both cost and semantics. `GraphProvider` therefore
competes with the segmentation winner as a complete one-block archive,
including every definition and reference byte. Its source attribution reports
only bytes actually emitted through references as graph coverage and leaves
the remainder classified as literal.

`EntropyProvider` uses the same whole-block boundary for the native entropy
leaf. It runs before interval segmentation so a legal interval budget stop
cannot starve the one general-purpose block candidate. Its catalogue work is
exactly nine units per input byte (eight codec counts and one winner
emission), and the optimizer separately charges the selected leaf's declared,
payload-sensitive decode work before evaluation. The Fast profile's
24,000,000-unit budget covers both steps on every canonical 1 MiB Fast block,
including the zero-scratch LZ-Huffman bound, while retaining room for an exact
whole-block function candidate. Entropy-reconstructed source remains
classified as literal coverage.

`WholeBlockFunctionsProvider` gives exact full-block generators one bounded
chance before interval enumeration. It delegates to the finite
`mathsvg-functions` catalogue, omits literal/entropy winners already covered
by dedicated paths, and independently charges validation/evaluation work.
This prevents exact constant, linear and short-periodic blocks from losing
merely because their full interval occurs late in segmentation.

Max and Structured use a larger, still finite function catalogue than
Fast/Balanced/Repository: periods through 256, recurrence order through three,
coefficients `{0,1,3,253,255}`, 96,000,000 work units, and 2,048 retained
ledger rows. This reaches the frozen 8-bit LFSR period and the exact
quadratic-modulo-256 recurrence `[3,253,1]` without changing the Balanced wire
search. The development procedural campaign verifies 100% function coverage,
zero literal leaves, and positive pre-entropy gain for constant, linear,
periodic, LFSR8, polynomial-d2, and Fibonacci recurrence generators.

`FunctionsProvider` adapts the existing `mathsvg-functions` search. The
optimizer itself supplies literal edges, verifies every supplemental node
against the interval bytes, and records all retained decisions in the
interval ledger. A winning native `ENTROPY_LITERAL` is eligible for exact
archive-size savings but remains classified as literal source coverage, never
as function/procedural coverage.

Interval enumeration is independently gated by
`PortfolioConfig::enable_interval_functions`. When it is disabled and no
other interval provider remains, the optimizer skips the literal-only DP:
splitting a literal block can only add canonical split/leaf metadata, so the
already constructed whole-block candidates are a complete upper bound for
that empty provider set. Whole-block entropy and whole-block function
competition are unaffected.

`CoordinateBasisProvider` is the bounded whole-block RC-BasisDP experiment.
For each discovered transform it partitions the transformed bytes into their
natural contiguous stride lanes, byte planes, or bit planes; searches an exact
literal/entropy/function child for each partition; joins those children with a
canonical `SPLIT`; and wraps the result in the normative inverse coordinate
node. Search, transform, nested-catalogue, and later verification work all fit
the caller-supplied deterministic budget. The provider is public and can be
passed explicitly to `optimize_with_all_providers`, but it is not part of any
built-in profile.

That exclusion is a measured stop-rule decision, not an unfinished runtime
path. After canonical Huffman and LZ-Huffman entered the native entropy
portfolio, a frozen development probe over 73 real 4 KiB blocks (32 Kennedy
XLS, 9 Calgary `progc`, and 32 Alice text blocks) found zero incremental
archive wins over the existing whole-transformed coordinate path. On Kennedy
block zero the independent basis still beat no-coordinate compression (2,569
versus 2,737 bytes), but the existing whole-coordinate candidate was smaller
(2,264 bytes). Enabling the new search by default would therefore add material
encoder work without an observed archive gain. The fixed Kennedy regression
retains this negative evidence and the exact experimental invocation path for
future ablation.

`PortfolioConfig::for_profile` keeps interval and coordinate search in Fast,
Max, Structured and Repository. Balanced disables both after a paired
10-repetition development experiment produced byte-identical archives on all
ten sampled files while the disabled configuration reduced aggregate
compression wall time by 29.609% (95% t interval -29.966% to -29.252%). The
deeper profiles retain the search because that Balanced result is not silently
generalised across different budgets or domains. Residual, symbolic and graph
emission remain disabled in all built-in profiles.
`PortfolioConfig::experimental_all` is the explicit opt-in for those
oracle-gated engines. Regardless of feature switches, the complete literal
archive remains the universal upper bound.

The same campaign finds no qualifying primary-real Structured domain: every
selected real domain still has zero function coverage in the winning archive.
The deeper catalogue is retained because it closes an explicit exact-generator
requirement, but it is not evidence of real structured procedural success.

`PortfolioConfig::set_algorithm_enabled` is the paired-ablation API for the
four engines with real built-in paths: whole-block entropy, whole-block
functions, interval functions and coordinates. A disabled engine is absent
from the provider catalogue and ledger while all budgets remain unchanged.
The catalogue also contains the joint interval/coordinate counterfactual used
for the Balanced stop decision. Engines already off behind oracle gates are
deliberately not exposed as disable-only ablations.

Every returned provider candidate is independently:

1. DSL-validated and evaluated against the source interval/block;
2. serialized into a complete one-block archive;
3. parsed, evaluated, and hash-verified through the container;
4. re-serialized as part of the complete candidate archive;
5. compared by the normative complete `CandidateCost`.
