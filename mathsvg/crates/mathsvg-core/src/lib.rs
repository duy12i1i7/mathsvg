//! Small, dependency-free primitives shared by the MathSVG implementation.
//!
//! This crate deliberately contains no codec policy and no floating-point
//! arithmetic.  It provides the canonical integers, checked arithmetic,
//! resource limits and normative candidate ordering required by the format.

mod cost;
mod error;
mod integer;
mod limits;
mod varint;

pub use cost::{select_best, CandidateCost, CostBreakdown};
pub use error::{Error, Result};
pub use integer::{
    checked_product, checked_u64_add, checked_u64_mul, checked_usize_add, checked_usize_mul,
    decode_word_le, encode_word_le, modulus_fits_width, mul_add_mod, mul_mod, validate_word_width,
    word_bytes, word_mask, wrapping_add_width, wrapping_mul_width, wrapping_sub_width,
    wrapping_to_width,
};
pub use limits::{Limits, Usage};
pub use varint::{encoded_len, zigzag_decode, zigzag_encode, Cursor, Writer};
