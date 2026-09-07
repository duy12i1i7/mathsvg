# mathsvg-simd

This crate is the required `mathsvg-simd` workspace boundary. It re-exports
the public API of `mathsvg-kernels`, which remains the single implementation
of MathSVG's scalar, x86 SSE2 and ARM NEON byte kernels.

Keeping this facade free of copied kernel code preserves one bit-exact scalar
reference and one runtime-dispatch implementation. SIMD remains an execution
detail and never affects archive search, serialization or tie-breaking.
