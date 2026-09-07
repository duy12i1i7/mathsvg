# mathsvg-functions

This crate is the bounded, deterministic byte-function fitter for the minimal
MathSVG engine. Every emitted candidate is a complete `mathsvg_dsl::Program`
whose root is `FILE`; there are no proxy models or estimated winning costs.

The v1 catalogue contains:

- the mandatory raw `LITERAL` program, measured first as the universal upper
  bound;
- one native `ENTROPY_LITERAL` candidate selected exactly by
  `mathsvg_entropy::encode_best` across all six v1 leaf codecs;
- exact byte constants;
- exact affine sequences modulo 256;
- exact prefix-periodic sequences under a configured maximum period;
- exact restored-prior recurrences modulo 256, with bounded order and a
  finite coefficient catalogue;
- each of those predictors wrapped in exact, sorted `EXCEPTIONS` when its
  mismatch support is within the configured bound.

For a recurrence, `coefficients[0]` multiplies the nearest restored lag
`x[i-1]`, `coefficients[1]` multiplies `x[i-2]`, and so on. Catalogue vectors
are enumerated lexicographically after sorting and deduplicating the configured
coefficient values.

Candidate fitting charges deterministic integer work units. Native entropy
search charges seven abstract units per input byte: one for each of six
catalogued codecs and one for winner emission. The raw DSL literal is measured
before that budget and therefore remains available even when the search budget
is zero. The entropy envelope is accepted only when its complete serialized
archive cost wins; entropy `RAW` cannot displace the smaller raw DSL literal. A
ledger records every tried descriptor and all
`SAFE_PRUNE`, `HEURISTIC_SKIP`, and `BUDGET_STOP` events. `SAFE_PRUNE` is used
only when a fully serialized optimistic `EXCEPTIONS` program proves a strict
byte lower bound above the current complete upper bound. Equality is evaluated
to preserve the normative tie-break.

The final order comes from `mathsvg_core::CandidateCost`: complete bytes,
decode work, decode memory, node count, dependencies, opcode sequence,
parameter bytes, and complete canonical payload. Complete bytes are computed
from the actual encoded definition/root sections plus the frozen v1 one-block
framing (file header, directory record, block header, and footer). The winner
is re-encoded and its size identity is checked before return.

## Deliberate boundary

This is not recursive residual modelling, coordinate discovery, segmentation,
DAG sharing, symbolic synthesis, or a high-level optimiser. It does not claim
a global optimum outside its declared finite catalogue and budget. It also
does not invent a general recurrence solver: coefficients outside the frozen
catalogue are omitted policy choices and must be evaluated by an oracle before
the catalogue is widened.
