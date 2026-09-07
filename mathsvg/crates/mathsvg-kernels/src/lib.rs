//! Bit-exact byte kernels with explicit scalar and native SIMD backends.
//!
//! SIMD is an execution detail only: it never participates in archive search,
//! serialization, or tie-breaking. Every operation is wrapping integer
//! arithmetic with a scalar reference implementation.

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum RequestedBackend {
    Scalar,
    Simd,
    Auto,
}

#[derive(Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd)]
pub enum ActiveBackend {
    #[default]
    Scalar,
    X86Sse2,
    ArmNeon,
}

impl ActiveBackend {
    pub const fn name(self) -> &'static str {
        match self {
            Self::Scalar => "scalar",
            Self::X86Sse2 => "x86-sse2",
            Self::ArmNeon => "arm-neon",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ByteOperation {
    Xor,
    AddWrapping,
    SubtractWrapping,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum KernelError {
    LengthMismatch { target: usize, operand: usize },
    SimdUnavailable,
}

impl core::fmt::Display for KernelError {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::LengthMismatch { target, operand } => {
                write!(
                    formatter,
                    "kernel operand length mismatch: target={target}, operand={operand}"
                )
            }
            Self::SimdUnavailable => {
                formatter.write_str("native SIMD is unavailable on this target")
            }
        }
    }
}

impl std::error::Error for KernelError {}

pub fn native_simd_backend() -> Option<ActiveBackend> {
    #[cfg(target_arch = "x86_64")]
    {
        if std::arch::is_x86_feature_detected!("sse2") {
            return Some(ActiveBackend::X86Sse2);
        }
    }
    #[cfg(target_arch = "aarch64")]
    {
        if std::arch::is_aarch64_feature_detected!("neon") {
            return Some(ActiveBackend::ArmNeon);
        }
    }
    None
}

pub fn resolve_backend(requested: RequestedBackend) -> Result<ActiveBackend, KernelError> {
    match requested {
        RequestedBackend::Scalar => Ok(ActiveBackend::Scalar),
        RequestedBackend::Auto => Ok(native_simd_backend().unwrap_or(ActiveBackend::Scalar)),
        RequestedBackend::Simd => native_simd_backend().ok_or(KernelError::SimdUnavailable),
    }
}

/// Apply one exact byte-wise operation in place.
///
/// The chosen active backend is returned so benchmark evidence does not infer
/// SIMD use merely from a requested mode.
pub fn apply_bytes(
    requested: RequestedBackend,
    operation: ByteOperation,
    target: &mut [u8],
    operand: &[u8],
) -> Result<ActiveBackend, KernelError> {
    let active = resolve_backend(requested)?;
    apply_bytes_active(active, operation, target, operand)?;
    Ok(active)
}

pub fn apply_bytes_active(
    active: ActiveBackend,
    operation: ByteOperation,
    target: &mut [u8],
    operand: &[u8],
) -> Result<(), KernelError> {
    if target.len() != operand.len() {
        return Err(KernelError::LengthMismatch {
            target: target.len(),
            operand: operand.len(),
        });
    }
    match active {
        ActiveBackend::Scalar => apply_scalar(operation, target, operand),
        ActiveBackend::X86Sse2 => apply_x86_sse2(operation, target, operand)?,
        ActiveBackend::ArmNeon => apply_arm_neon(operation, target, operand)?,
    }
    Ok(())
}

fn apply_scalar(operation: ByteOperation, target: &mut [u8], operand: &[u8]) {
    match operation {
        ByteOperation::Xor => {
            for (left, right) in target.iter_mut().zip(operand) {
                *left ^= *right;
            }
        }
        ByteOperation::AddWrapping => {
            for (left, right) in target.iter_mut().zip(operand) {
                *left = left.wrapping_add(*right);
            }
        }
        ByteOperation::SubtractWrapping => {
            for (left, right) in target.iter_mut().zip(operand) {
                *left = left.wrapping_sub(*right);
            }
        }
    }
}

#[cfg(target_arch = "x86_64")]
fn apply_x86_sse2(
    operation: ByteOperation,
    target: &mut [u8],
    operand: &[u8],
) -> Result<(), KernelError> {
    // SAFETY: runtime feature detection in `resolve_backend` precedes this
    // dispatch. The implementation bounds every 16-byte unaligned access by
    // the two equal-length slices.
    unsafe { apply_x86_sse2_inner(operation, target, operand) };
    Ok(())
}

#[cfg(not(target_arch = "x86_64"))]
fn apply_x86_sse2(
    _operation: ByteOperation,
    _target: &mut [u8],
    _operand: &[u8],
) -> Result<(), KernelError> {
    Err(KernelError::SimdUnavailable)
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "sse2")]
#[allow(unused_unsafe)] // Intrinsics changed from unsafe to safe after Rust 1.85.
unsafe fn apply_x86_sse2_inner(operation: ByteOperation, target: &mut [u8], operand: &[u8]) {
    use std::arch::x86_64::{
        __m128i, _mm_add_epi8, _mm_loadu_si128, _mm_storeu_si128, _mm_sub_epi8, _mm_xor_si128,
    };

    let vector_bytes = 16usize;
    let vector_end = target.len() / vector_bytes * vector_bytes;
    let mut offset = 0usize;
    while offset < vector_end {
        // SAFETY: `offset + 16 <= vector_end <= len` for both equal slices.
        // The unaligned intrinsics impose no alignment precondition.
        let left = unsafe { _mm_loadu_si128(target.as_ptr().add(offset).cast::<__m128i>()) };
        // SAFETY: same range proof as `left`.
        let right = unsafe { _mm_loadu_si128(operand.as_ptr().add(offset).cast::<__m128i>()) };
        // SAFETY: these register-only SSE2 operations have no additional
        // memory or alignment preconditions after the guarded loads.
        let result = unsafe {
            match operation {
                ByteOperation::Xor => _mm_xor_si128(left, right),
                ByteOperation::AddWrapping => _mm_add_epi8(left, right),
                ByteOperation::SubtractWrapping => _mm_sub_epi8(left, right),
            }
        };
        // SAFETY: the 16-byte destination range is inside `target` and the
        // mutable slice excludes safe aliases.
        unsafe { _mm_storeu_si128(target.as_mut_ptr().add(offset).cast::<__m128i>(), result) };
        offset += vector_bytes;
    }
    apply_scalar(operation, &mut target[vector_end..], &operand[vector_end..]);
}

#[cfg(target_arch = "aarch64")]
fn apply_arm_neon(
    operation: ByteOperation,
    target: &mut [u8],
    operand: &[u8],
) -> Result<(), KernelError> {
    // SAFETY: runtime feature detection in `resolve_backend` precedes this
    // dispatch. The implementation bounds every 16-byte access.
    unsafe { apply_arm_neon_inner(operation, target, operand) };
    Ok(())
}

#[cfg(not(target_arch = "aarch64"))]
fn apply_arm_neon(
    _operation: ByteOperation,
    _target: &mut [u8],
    _operand: &[u8],
) -> Result<(), KernelError> {
    Err(KernelError::SimdUnavailable)
}

#[cfg(target_arch = "aarch64")]
#[target_feature(enable = "neon")]
#[allow(unused_unsafe)] // Intrinsics changed from unsafe to safe after Rust 1.85.
unsafe fn apply_arm_neon_inner(operation: ByteOperation, target: &mut [u8], operand: &[u8]) {
    use std::arch::aarch64::{vaddq_u8, veorq_u8, vld1q_u8, vst1q_u8, vsubq_u8};

    let vector_bytes = 16usize;
    let vector_end = target.len() / vector_bytes * vector_bytes;
    let mut offset = 0usize;
    while offset < vector_end {
        // SAFETY: `offset + 16 <= vector_end <= len` for both equal slices.
        let left = unsafe { vld1q_u8(target.as_ptr().add(offset)) };
        // SAFETY: same range proof as `left`.
        let right = unsafe { vld1q_u8(operand.as_ptr().add(offset)) };
        // SAFETY: these register-only NEON operations have no additional
        // memory or alignment preconditions after the guarded loads.
        let result = unsafe {
            match operation {
                ByteOperation::Xor => veorq_u8(left, right),
                ByteOperation::AddWrapping => vaddq_u8(left, right),
                ByteOperation::SubtractWrapping => vsubq_u8(left, right),
            }
        };
        // SAFETY: the 16-byte destination range is inside `target` and the
        // mutable slice excludes safe aliases.
        unsafe { vst1q_u8(target.as_mut_ptr().add(offset), result) };
        offset += vector_bytes;
    }
    apply_scalar(operation, &mut target[vector_end..], &operand[vector_end..]);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn generated(length: usize, mut state: u64) -> Vec<u8> {
        (0..length)
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                state as u8
            })
            .collect()
    }

    #[test]
    fn scalar_operations_have_exact_wrapping_semantics() {
        let right = [0xff, 0x02, 0x80, 0x55];

        let mut xor = [0x01, 0xff, 0x80, 0xaa];
        assert_eq!(
            apply_bytes(
                RequestedBackend::Scalar,
                ByteOperation::Xor,
                &mut xor,
                &right
            )
            .unwrap(),
            ActiveBackend::Scalar
        );
        assert_eq!(xor, [0xfe, 0xfd, 0x00, 0xff]);

        let mut add = [0x01, 0xff, 0x80, 0xaa];
        apply_bytes(
            RequestedBackend::Scalar,
            ByteOperation::AddWrapping,
            &mut add,
            &right,
        )
        .unwrap();
        assert_eq!(add, [0x00, 0x01, 0x00, 0xff]);

        let mut subtract = [0x01, 0xff, 0x80, 0xaa];
        apply_bytes(
            RequestedBackend::Scalar,
            ByteOperation::SubtractWrapping,
            &mut subtract,
            &right,
        )
        .unwrap();
        assert_eq!(subtract, [0x02, 0xfd, 0x00, 0x55]);
    }

    #[test]
    fn native_simd_matches_scalar_for_every_tail_and_misalignment() {
        let Some(expected_backend) = native_simd_backend() else {
            assert_eq!(
                resolve_backend(RequestedBackend::Simd),
                Err(KernelError::SimdUnavailable)
            );
            return;
        };
        for operation in [
            ByteOperation::Xor,
            ByteOperation::AddWrapping,
            ByteOperation::SubtractWrapping,
        ] {
            for length in 0..=257 {
                let source_storage = generated(length + 5, 0x1234_5678_9abc_def0);
                let initial_storage = generated(length + 7, 0xfedc_ba98_7654_3210);
                let source = &source_storage[2..2 + length];
                let initial = &initial_storage[3..3 + length];
                let mut scalar = initial.to_vec();
                let mut simd_storage = vec![0xa5; length + 9];
                simd_storage[5..5 + length].copy_from_slice(initial);
                let simd = &mut simd_storage[5..5 + length];

                apply_bytes(RequestedBackend::Scalar, operation, &mut scalar, source).unwrap();
                let active = apply_bytes(RequestedBackend::Simd, operation, simd, source).unwrap();
                assert_eq!(active, expected_backend);
                assert_eq!(simd, scalar, "operation={operation:?}, length={length}");
                assert!(simd_storage[..5].iter().all(|byte| *byte == 0xa5));
                assert!(simd_storage[5 + length..].iter().all(|byte| *byte == 0xa5));
            }
        }
    }

    #[test]
    fn auto_resolves_to_an_explicit_backend_and_lengths_are_strict() {
        assert_eq!(
            resolve_backend(RequestedBackend::Auto).unwrap(),
            native_simd_backend().unwrap_or(ActiveBackend::Scalar)
        );
        assert_eq!(
            apply_bytes(
                RequestedBackend::Scalar,
                ByteOperation::Xor,
                &mut [0; 2],
                &[0; 3],
            ),
            Err(KernelError::LengthMismatch {
                target: 2,
                operand: 3,
            })
        );
    }
}
