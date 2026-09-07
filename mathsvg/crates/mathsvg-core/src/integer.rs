use crate::{Error, Result};

pub fn checked_u64_add(left: u64, right: u64, context: &'static str) -> Result<u64> {
    left.checked_add(right)
        .ok_or(Error::IntegerOverflow { context })
}

pub fn checked_u64_mul(left: u64, right: u64, context: &'static str) -> Result<u64> {
    left.checked_mul(right)
        .ok_or(Error::IntegerOverflow { context })
}

pub fn checked_usize_add(left: usize, right: usize, context: &'static str) -> Result<usize> {
    left.checked_add(right)
        .ok_or(Error::IntegerOverflow { context })
}

pub fn checked_usize_mul(left: usize, right: usize, context: &'static str) -> Result<usize> {
    left.checked_mul(right)
        .ok_or(Error::IntegerOverflow { context })
}

pub fn checked_product(
    values: impl IntoIterator<Item = u64>,
    context: &'static str,
) -> Result<u64> {
    values.into_iter().try_fold(1u64, |product, value| {
        checked_u64_mul(product, value, context)
    })
}

pub const fn validate_word_width(width: u8) -> bool {
    matches!(width, 8 | 16 | 24 | 32 | 48 | 64)
}

pub fn word_bytes(width: u8) -> Result<u8> {
    if validate_word_width(width) {
        Ok(width / 8)
    } else {
        Err(Error::InvalidValue("unsupported word width"))
    }
}

pub fn word_mask(width: u8) -> Result<u64> {
    if !validate_word_width(width) {
        return Err(Error::InvalidValue("unsupported word width"));
    }
    if width == 64 {
        Ok(u64::MAX)
    } else {
        Ok((1u64 << width) - 1)
    }
}

/// Whether a positive explicit modulus is representable by `width`.
///
/// For widths below 64 this includes the full wrapping modulus `2^width`.
/// The value `2^64` cannot be represented by the v1 `u64` field, so a
/// 64-bit explicit modulus is at most `u64::MAX`.
pub const fn modulus_fits_width(modulus: u64, width: u8) -> bool {
    if modulus == 0 || !validate_word_width(width) {
        return false;
    }
    width == 64 || modulus <= (1u64 << width)
}

pub fn wrapping_to_width(value: u64, width: u8) -> Result<u64> {
    Ok(value & word_mask(width)?)
}

pub fn wrapping_add_width(left: u64, right: u64, width: u8) -> Result<u64> {
    let mask = u128::from(word_mask(width)?);
    Ok(((u128::from(left) + u128::from(right)) & mask) as u64)
}

pub fn wrapping_sub_width(left: u64, right: u64, width: u8) -> Result<u64> {
    word_mask(width)?;
    let modulus = 1u128 << width;
    let result = (u128::from(left) + modulus - (u128::from(right) % modulus)) % modulus;
    Ok(result as u64)
}

pub fn wrapping_mul_width(left: u64, right: u64, width: u8) -> Result<u64> {
    let mask = u128::from(word_mask(width)?);
    Ok(((u128::from(left) * u128::from(right)) & mask) as u64)
}

pub fn mul_mod(left: u64, right: u64, modulus: u64) -> Result<u64> {
    if modulus == 0 {
        return Err(Error::InvalidValue("zero modulus"));
    }
    Ok(((u128::from(left) * u128::from(right)) % u128::from(modulus)) as u64)
}

pub fn mul_add_mod(left: u64, right: u64, addend: u64, modulus: u64) -> Result<u64> {
    if modulus == 0 {
        return Err(Error::InvalidValue("zero modulus"));
    }
    Ok(((u128::from(left) * u128::from(right) + u128::from(addend)) % u128::from(modulus)) as u64)
}

pub fn encode_word_le(value: u64, width: u8, output: &mut Vec<u8>) -> Result<()> {
    let bytes = word_bytes(width)? as usize;
    if value > word_mask(width)? {
        return Err(Error::InvalidValue("word value exceeds declared width"));
    }
    output.extend_from_slice(&value.to_le_bytes()[..bytes]);
    Ok(())
}

pub fn decode_word_le(input: &[u8], width: u8) -> Result<u64> {
    let bytes = word_bytes(width)? as usize;
    if input.len() != bytes {
        return Err(Error::LengthMismatch {
            context: "little-endian word",
            expected: bytes as u64,
            actual: input.len() as u64,
        });
    }
    let mut array = [0u8; 8];
    array[..bytes].copy_from_slice(input);
    Ok(u64::from_le_bytes(array))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn word_widths_and_moduli_are_explicit() {
        for width in [8, 16, 24, 32, 48, 64] {
            assert!(validate_word_width(width));
            assert_eq!(word_bytes(width).unwrap(), width / 8);
            assert_eq!(word_mask(width).unwrap().count_ones(), u32::from(width));
        }
        assert!(!validate_word_width(7));
        assert!(modulus_fits_width(256, 8));
        assert!(!modulus_fits_width(257, 8));
        assert!(modulus_fits_width(u64::MAX, 64));
        assert!(!modulus_fits_width(0, 64));
    }

    #[test]
    fn widened_modular_arithmetic_does_not_overflow() {
        assert_eq!(mul_mod(u64::MAX, u64::MAX, 251).unwrap(), 106);
        assert_eq!(mul_add_mod(u64::MAX, u64::MAX, u64::MAX, 251).unwrap(), 174);
        assert_eq!(wrapping_add_width(250, 20, 8).unwrap(), 14);
        assert_eq!(wrapping_sub_width(3, 5, 8).unwrap(), 254);
        assert_eq!(wrapping_mul_width(200, 3, 8).unwrap(), 88);
        assert_eq!(
            wrapping_sub_width(3, 5, 7),
            Err(Error::InvalidValue("unsupported word width"))
        );
    }

    #[test]
    fn little_endian_word_round_trip() {
        for (value, width, expected) in [
            (0xabu64, 8, vec![0xab]),
            (0x1234, 16, vec![0x34, 0x12]),
            (0x123456, 24, vec![0x56, 0x34, 0x12]),
            (
                0x0123_4567_89ab_cdef,
                64,
                vec![0xef, 0xcd, 0xab, 0x89, 0x67, 0x45, 0x23, 0x01],
            ),
        ] {
            let mut bytes = Vec::new();
            encode_word_le(value, width, &mut bytes).unwrap();
            assert_eq!(bytes, expected);
            assert_eq!(decode_word_le(&bytes, width).unwrap(), value);
        }
    }
}
