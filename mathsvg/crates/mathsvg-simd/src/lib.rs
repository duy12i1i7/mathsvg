//! Stable compatibility facade for MathSVG's bit-exact SIMD implementation.
//!
//! The implementation remains in `mathsvg-kernels`; this crate supplies the
//! required `mathsvg-simd` workspace boundary without copying dispatch or
//! architecture-specific code.

#![forbid(unsafe_code)]

pub use mathsvg_kernels::*;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn facade_preserves_exact_scalar_semantics() {
        let mut target = [0xff, 0x01, 0x80];
        let backend = apply_bytes(
            RequestedBackend::Scalar,
            ByteOperation::AddWrapping,
            &mut target,
            &[0x01, 0xff, 0x80],
        )
        .expect("scalar backend must be available");

        assert_eq!(backend, ActiveBackend::Scalar);
        assert_eq!(target, [0x00, 0x00, 0x00]);
    }
}
