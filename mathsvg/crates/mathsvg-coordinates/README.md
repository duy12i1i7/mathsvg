# mathsvg-coordinates

This crate is the bounded Phase-5 coordinate kernel. It implements exact,
invertible `STRIDE`, `BYTE_PLANE`, and `BIT_PLANE` transforms plus identity,
canonical descriptor records, deterministic candidate discovery, and a
complete-cost selection callback.

Supported ledger:

- `STRIDE(width, channels)` converts record-major channel elements to stable
  channel-major lanes. Input length must be divisible by `width * channels`.
- `BYTE_PLANE(width, endian)` emits numeric byte significance planes from
  least to most significant. Width is one of `1,2,3,4,6,8`.
- `BIT_PLANE` emits eight LSB-first packed planes. High bits in the final
  partial plane byte must be zero and are rejected otherwise.
- Descriptor encoding and `dsl_metadata_bytes(child_record_bytes)` use
  canonical uLEB lengths, so metadata costs are byte-exact.
- Discovery uses only frozen defaults and bounded integer autocorrelation.
  Statistics generate candidates; `select_exact` chooses solely from the
  caller's fully serialized `CandidateCost`.

Gated ledger:

- `TRANSPOSE` needs a frozen dimension/output-shape payload and rank limits.
  `TILE` additionally needs a frozen full/edge-tile traversal. Both remain
  rejected rather than being assigned guessed wire semantics.
- Signedness, reshape, Morton, reverse, explicit permutation, hierarchical
  region DP, and SIMD are later phases.
- This crate establishes correctness and finite search mechanics; the 0.5%
  keep/remove gate still requires non-synthetic oracle and archive evidence.

The descriptor catalog uses the coordinate opcode byte, zero flags, and a
length-delimited parameter payload. Identity uses catalog byte `0x00` but has
zero DSL metadata cost; it is not reinterpreted as the DSL `LITERAL` opcode.
In the integrated DSL coordinate nodes, parameters precede the child record.
The exact coordinate overhead is therefore:

```text
opcode + flags + uLEB(parameter_bytes + child_bytes) + parameter_bytes
```

Inverse work is frozen at `1 + n` for stable byte permutations and `1 + 8*n`
for bit-plane reconstruction. Result destinations are not counted as scratch.
