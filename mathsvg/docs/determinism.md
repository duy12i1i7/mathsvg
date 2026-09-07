# Determinism contract

For frozen input bytes \(X\) and configuration \(C\):

\[
E(X,C,\text{threads}=t)=A
\]

must produce the identical archive byte string \(A\) for every supported
thread count, scalar/SIMD path, debug/release build and supported architecture.

## Normative rules

- No floating point, random seed, clock, address, hash-map iteration order or
  environment-dependent candidate enters archive selection.
- Candidate enumeration is assigned a canonical key before parallel work.
- Parallel workers return `(candidate_key, exact_cost, descriptor)`.
- Merge sorts by the normative cost/tie-break tuple, never completion order.
- Signed representation, fixed-point scale/rounding, wrapping and saturation
  are stored or fixed by the opcode version.
- Tables, definitions, exceptions and references use canonical sorted order.
- Archive metadata contains no timestamp, host path or non-deterministic UUID.
- Compression thread count may affect throughput only.

## Required matrix

Before a profile is released, representative literal, procedural, coordinate,
recursive-residual and DAG archives are encoded:

- 100 times;
- debug and release;
- 1, 2, 4, 8 and all supported threads;
- scalar and each enabled SIMD path;
- x86-64 and ARM64 when available;
- two supported compiler versions.

Both restored SHA-256 and archive SHA-256 are compared. Unsupported matrix
cells remain explicit gaps; they are not inferred from another machine.

## Deterministic failure

Budget exhaustion is deterministic. The budget is a count of declared work
units, candidates, graph states and bytes, not elapsed wall time. A wall-time
timeout may abort a benchmark but may not select a different archive and call
it a successful deterministic result.
