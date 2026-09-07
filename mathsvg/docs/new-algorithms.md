# Candidate new algorithms and prior-art boundary

## Status and claim policy

These are candidate inventions, not claims of legal or publication novelty.
Broad ideas such as MDL model selection, shortest-path parsing, transformed
copy, learned dictionaries and procedural grammars have substantial prior
art. Any future novelty claim must be limited to the exact state
representation, recurrence, admissible bound, canonical algebra and measured
effect implemented here. This review is not a patent freedom-to-operate
opinion.

## Primary prior art

- Coifman and Wickerhauser, *Entropy-based Algorithms for Best Basis
  Selection*, 1992, DOI
  [10.1109/18.119732](https://doi.org/10.1109/18.119732).
- Rissanen, *Modeling by shortest data description*, 1978, DOI
  [10.1016/0005-1098(78)90005-5](https://doi.org/10.1016/0005-1098(78)90005-5).
- Ferragina, Nitto and Venturini,
  [*Bit-Optimal Lempel-Ziv compression*](https://arxiv.org/abs/0802.0835).
- IETF,
  [RFC 9639: Free Lossless Audio Codec](https://www.rfc-editor.org/rfc/rfc9639.html).
- Willsey et al., *egg: Fast and Extensible Equality Saturation*, DOI
  [10.1145/3434304](https://doi.org/10.1145/3434304).
- Ziv and Lempel, LZ77 and LZ78, DOI
  [10.1109/TIT.1977.1055714](https://doi.org/10.1109/TIT.1977.1055714) and
  [10.1109/TIT.1978.1055934](https://doi.org/10.1109/TIT.1978.1055934).
- Bowers et al., *Top-Down Synthesis for Library Learning*, DOI
  [10.1145/3571234](https://doi.org/10.1145/3571234).
- Nevill-Manning and Witten,
  [*Identifying Hierarchical Structure in Sequences*](https://arxiv.org/abs/cs/9709102).
- Charikar et al., *The Smallest Grammar Problem*, DOI
  [10.1109/TIT.2005.850116](https://doi.org/10.1109/TIT.2005.850116).

The cited work already covers best-basis trees, MDL, weighted parse graphs,
predictor/residual coding, equality saturation, exact/dictionary copy, library
learning and grammar induction. MathSVG must not claim those broad concepts as
new.

## A. RC-BasisDP — Recursive Coordinate Best-Basis DP

**Intuition.** Jointly choose interval-specific reversible coordinates,
procedural functions, splits and recursive residual layers.

**Definition.**

\[
C(s)=\min\{L_{\rm lit}(s),L(T)+\sum_iC(T_i(s)),L(P)+C(s\ominus P)\}.
\]

State is `(interval, coordinate, residual_depth, budget_bucket)`.

**Losslessness.** Every coordinate is bijective; function output is corrected
by an exact child; split children cover the parent exactly. Structural
induction gives the original interval.

**Complexity/worst case.** Exact microblock DP is polynomial in the frozen
state/hyperedge graph size, which may itself grow exponentially with catalogue
and depth. Hard state, depth and work caps are mandatory.

**Decoder/metadata.** The decoder follows stored `GROUP`, topology, function
and correction nodes only. Metadata is the canonical node/DAG encoding.

**Evidence plan.** Unit tests for each recurrence edge, property round-trip,
oracle comparison to exhaustive enumeration, no-coordinate and no-recursion
ablation, development/validation/untouched structured holdout.

**Prior-art risk.** High: best-basis DP and transform trees are established.
The potentially distinct part is heterogeneous exact archive-cost
coordinate/function/residual hyperedges.

## B. SADH — Shared-Activation Description Hypergraph

**Intuition.** Solve exact graph selection when a shared definition has a
one-time activation cost and many calls.

**Definition.** A* state is `(uncovered_frontier, activated_definitions)`.
The heuristic makes all inactive definitions free and sums independent
per-region minima.

**Losslessness.** Selected hyperedges expand only validated definitions and
exact residual/literal leaves.

**Complexity/worst case.** Exponential in candidate definitions; exact only on
microblocks. Bounded branch-and-bound elsewhere.

**Decoder/metadata.** Standard definition table and reference nodes; no solver
exists in the archive.

**Evidence plan.** Exhaustive small-instance equivalence, admissibility proof
tests, greedy/independent/SADH ablation and Git/record/repository holdout.

**Prior-art risk.** High if described only as a DAG shortest path. The fixed
activation state and concrete admissible bound are the candidate contribution.

## C. ARPL — Algebraic Residual Projection Lattice

**Intuition.** Repeatedly project residuals into canonical components over
\(\mathbb Z/2^w\) or \(GF(2)\), then recursively represent the exact remainder.

**Definition.**

\[
R_{j+1}=R_j\ominus\pi_j(R_j),\qquad
\min\sum_jL(\pi_j,\pi_j(R_j))+L(R_{k+1}).
\]

States are canonicalised by residual digest, domain, depth and budget.

**Losslessness.** Each group operation is invertible for the stored projection;
induction over layers reconstructs the original.

**Complexity/worst case.** \(O(d|\Pi|F(n))\) without state sharing, where
\(F(n)\) is fitting/evaluation cost; candidate interactions can be
combinatorial. Depth/work/state caps bound it.

**Decoder/metadata.** A chain/DAG of algebra nodes, projection parameters and
exact correction children.

**Evidence plan.** Depth and projection-family ablations; exact storage
identity; random literal regression; non-synthetic block wins; held-out
telemetry/audio/scientific arrays.

**Prior-art risk.** Matching pursuit, lifting, predictor+residual and
low-rank+sparse decomposition are established. The candidate distinction is a
canonical finite-ring projection lattice selected by exact lossless archive
cost.

## D. RSEE — Residual-Sensitive Equality Enumeration

**Intuition.** Preserve an expression that is not exactly the target when its
exact residual is cheap.

**Definition.** Each semantic class keeps the Pareto tuple:

\[
(L(E),L(X\ominus E),W_d(E),M_d(E)).
\]

**Losslessness.** The expression plus exact residual composition evaluates to
the target.

**Complexity/worst case.** Exponential expression enumeration; exact only for
small depth/microblocks, otherwise bounded.

**Decoder/metadata.** Only the selected expression and correction are stored;
the e-graph is encoder-only.

**Evidence plan.** Exhaustive oracle agreement, rewrite-domain tests,
expression-only versus residual-sensitive ablation and unseen generator
holdout.

**Prior-art risk.** Very high: superoptimisation, equality saturation and
program-synthesis compression exist. Residual-aware Pareto retention is the
specific differentiator to test.

## E. AMCC — Affine Multiscale Corrected Copy

**Intuition.** Generalise exact copy with bounded scale, coordinate,
permutation and affine transforms followed by an exact correction.

**Definition.**

\[
B_j=\pi(aB_s+b\bmod 2^w)\oplus R.
\]

**Losslessness.** Source dependencies are prior and acyclic; deterministic
transform evaluation plus exact \(R\) reconstructs the destination.

**Complexity/worst case.** Naive source/transform search is quadratic or worse.
Canonical fingerprints, source windows and candidate caps bound encoding;
decode is linear in output and dependency work.

**Decoder/metadata.** Source ID/range, scale/layout, permutation, \(a,b\),
domain and correction reference.

**Evidence plan.** Exact-copy/affine/permuted/multiscale ladder, dependency-bomb
tests, Git/tile/channel ablation and independent repository holdout.

**Prior-art risk.** Very high: LZ, delta formats, transformed dictionaries and
fractal block coding are close. Only the exact native transform catalogue plus
residual/cost solver is potentially distinct.

## F. ALTFB — Archive-Local Typed Function Basis

**Intuition.** Extract reusable typed procedural functions from the archive
objects themselves, with no external training.

**Definition.**

\[
\min \sum_d y_dL(d)+
\sum_{s,d}z_{sd}[L(call_{sd})+C(R_{sd})],
\quad z_{sd}\le y_d.
\]

**Losslessness.** Every call evaluates an archive-contained definition and is
corrected exactly.

**Complexity/worst case.** Facility-location/grammar selection is
combinatorial; exact ILP/enumeration is microblock-only, deterministic greedy
or branch-and-bound is bounded elsewhere.

**Decoder/metadata.** Typed definition table, prototype parameters, call
deltas and residual references.

**Evidence plan.** No-basis, exact-duplicate-only, parameterised-basis and
literal comparisons; definition accounting identity; multi-object holdout.

**Prior-art risk.** Very high: dictionary learning, LZ78 and Stitch already
learn reusable libraries. Raw-byte-to-typed-function induction with exact
archive cost is the narrow candidate distinction.

## G. PDPTG — Parameter-Delta Procedural Tree Grammar

**Intuition.** Replace repeated isomorphic procedural subtrees with typed
nonterminals and delta-coded parameters.

**Definition.** Anti-unify subtree patterns, choose a canonical prototype and
accept a replacement iff:

\[
L(definition)+\sum L(call,\Delta\theta)
<
\sum L(inlined\ subtree).
\]

**Losslessness.** Calls substitute exact stored parameters into a validated
acyclic subtree.

**Complexity/worst case.** Smallest grammar is hard; candidate mining and
selection are bounded and deterministic.

**Decoder/metadata.** Definition, typed holes, prototype, call-site deltas and
exact child/residual references.

**Evidence plan.** Re-Pair/exact-subtree/parameter-delta ladder, serialized
byte accounting, repeated-record and repository holdout.

**Prior-art risk.** Very high: sequence/tree grammar and library learning are
close. Claims must concern the concrete parameter-delta algorithm and measured
native archive effect.

## Priority after the native entropy refresh

The original Phase 3 percentages used a separate reference serializer and a
raw literal baseline. They remain historical research, but no longer decide
emission after Canonical Huffman and LZ-Huffman. The canonical development
decision now comes from complete native archives in `results/oracle/`:

1. **RC-BasisDP** remains implemented as an explicit experiment. Its
   independent-basis tier is enabled only if it incrementally beats both the
   no-coordinate and active whole-coordinate paths on a non-synthetic block.
2. **SADH** remains an exact bounded activation solver and ledger oracle.
   Natural-block rows, not repeated in-memory controls, decide whether its
   definition cost breaks even against the current entropy-aware winner.
3. **RSEE** now has a native engine and no longer has `requires_engine` status.
   Budget completeness, ≥0.5% complete-byte gain and a natural
   non-synthetic win are all required before emission.
4. **ARPL** is measured at depths zero, one and two against the same current
   function/entropy baseline. One real Calgary `progc` microblock saved 115
   complete-archive bytes and keeps the experiment available, but the paired
   production whole-file integration saved zero bytes. ARPL therefore remains
   explicit-only rather than becoming a built-in emission path.

PDPTG/ALTFB and AMCC also remain profile-gated. Derived duplicate stress
headroom is not evidence for parameterized grammar or function-basis gain on a
natural archive.  Every status must be revisited from the production candidate
ledger before Phase 9 selects three emitting algorithms.
