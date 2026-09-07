# mathsvg-kernels

Exact byte-wise execution kernels used by MathSVG corrections.

- Scalar is the normative reference.
- x86-64 uses runtime-detected SSE2.
- ARM64 uses runtime-detected NEON.
- XOR, wrapping ADD and wrapping SUB operate on 16-byte lanes followed by the
  same scalar tail.
- No kernel affects candidate discovery, cost, tie-breaking or archive bytes.

This is the only MathSVG crate that contains `unsafe`: each intrinsic load and
store is range-checked by an equal-slice-length check and a
`offset + 16 <= len` loop invariant. Unit tests compare every tail length
through 257 bytes, including deliberately misaligned slices.
