# Coordinate discovery

## Objective

For a byte interval \(X[a:b]\), coordinate discovery enumerates a finite set of
reversible layouts \(T\) and competes complete representations:

\[
(T^*,G^*)=\arg\min_{T,G}
\left[L(T)+L(G(T(X[a:b])))\right].
\]

Autocorrelation, mutual information, column entropy, numeric smoothness and
match peaks may generate or rank candidates. They never replace the final
serialized-size comparison.

## Candidate descriptors

A descriptor is canonical:

```text
kind
input_interval
element_width
signedness
endianness
dimensions
axis_permutation
stride
channels
tile_dimensions
padding_rule
```

V1 candidate ranges are finite:

- widths `1,2,3,4,6,8` bytes;
- unsigned and two's-complement signed interpretations;
- little and big endian;
- strides/channels derived from bounded peaks plus a frozen small default set;
- rank at most three;
- bounded row/tile sizes and true axis permutations;
- byte-plane, bit-plane and identity always considered where type-correct.

Every transform proves a bijection for the declared dimensions or is rejected.

## RC-BasisDP

Recursive Coordinate Best-Basis DP uses state:

```text
(interval, coordinate_descriptor, residual_depth, budget_bucket)
```

and hyperedges:

- emit literal;
- apply a native function;
- transform the whole state;
- split and independently transform children;
- apply a function and recursively represent its correction.

The exact recurrence is:

\[
C(s)=\min\left\{
L_{\text{literal}}(s),
L(T)+\sum_i C(T_i(s)),
L(P)+C(s\ominus P)
\right\}.
\]

Canonical interval boundaries and bounded depth make the graph acyclic.
Definition sharing is disabled in the exact local oracle and added later as a
global activation cost.

### Implemented optimizer edge

`mathsvg-optimizer::CoordinateProvider` now runs the bounded discovery
catalogue on each eligible interval, applies every retained non-identity
transform, searches a native function child in transformed space, and wraps
that child in the corresponding inverse `COORDINATE` node. The optimizer then
independently evaluates, serializes, parses and hash-verifies the complete
candidate before it can enter segmentation DP. Identity is already represented
by the base function provider. A transformed `ENTROPY_LITERAL` remains literal
coverage; only a procedural transformed-space child is attributed coordinate
coverage.

Coordinate search is enabled in the built-in `Fast`, `Structured` and
`Repository` portfolio profiles. It is deliberately pruned from `Balanced`
and `Max`: the real-block incremental scan found no retained coordinate-basis
win, and the frozen Max X-ray ablation produced identical archive bytes while
removing substantial search time. The engine remains available in the two
experiment-oriented profiles and in Fast's bounded catalogue. These are
development emission gates, not holdout or dominance claims.

### Independent coordinate-basis experiment

`CoordinateBasisProvider` additionally splits the transformed domain into its
natural stride lanes or byte/bit planes and represents those partitions
independently. It is deliberately absent from all built-in profiles. Its
retention question is incremental: it must beat both the no-coordinate path
and the already active whole-coordinate path, not merely beat raw literal.

The frozen stop scan covers 73 real development blocks (Kennedy, Calgary
`progc`, and Canterbury Alice) using complete native archives with the current
Canonical/LZ-Huffman leaves. Canonical per-block rows live in
`results/oracle/coordinate.csv`; the generated report applies the 0.5% and
non-synthetic-win rule. This decision does not claim that all future
coordinate catalogues lack headroom.

## Pruning

An admissible coordinate lower bound includes mandatory node/descriptor bytes
and a lower bound of zero for not-yet-described content. Stronger bounds may
use an exact literal lower bound for bytes that no remaining primitive can
generate. A statistical score is not an admissible byte bound.

Every omission is logged:

```text
candidate_id, interval, descriptor, event,
lower_bound, upper_bound, work_used, reason
```

`HEURISTIC_SKIP` regret is measured by the exact coordinate oracle on sampled
microblocks.

## Acceptance

A coordinate feature is retained only if it saves at least 0.5% actual native
archive bytes and wins at least one non-synthetic development block. It then
requires validation and untouched-domain holdout evidence. Selected-transform
frequency alone is not evidence of gain.
