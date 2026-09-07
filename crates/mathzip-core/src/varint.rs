use crate::{Error, Result};

pub(crate) fn encoded_len(mut value: u64) -> usize {
    let mut n = 1;
    while value >= 0x80 {
        value >>= 7;
        n += 1;
    }
    n
}

pub(crate) fn put(mut value: u64, out: &mut Vec<u8>) {
    while value >= 0x80 {
        out.push((value as u8 & 0x7f) | 0x80);
        value >>= 7;
    }
    out.push(value as u8);
}

pub(crate) fn get(input: &[u8], cursor: &mut usize) -> Result<u64> {
    let start = *cursor;
    let mut value = 0u64;
    for shift in (0..=63).step_by(7) {
        let byte = *input.get(*cursor).ok_or(Error::Truncated {
            context: "unsigned LEB128 integer",
        })?;
        *cursor += 1;
        if shift == 63 && byte > 1 {
            return Err(Error::InvalidVarint);
        }
        value |= u64::from(byte & 0x7f) << shift;
        if byte & 0x80 == 0 {
            if *cursor - start != encoded_len(value) {
                return Err(Error::InvalidVarint);
            }
            return Ok(value);
        }
    }
    Err(Error::InvalidVarint)
}

pub(crate) fn usize_from(value: u64) -> Result<usize> {
    usize::try_from(value).map_err(|_| Error::IntegerOverflow)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_round_trip() {
        for value in [0, 1, 127, 128, 16_384, u32::MAX as u64, u64::MAX] {
            let mut buf = Vec::new();
            put(value, &mut buf);
            let mut cursor = 0;
            assert_eq!(get(&buf, &mut cursor).unwrap(), value);
            assert_eq!(cursor, buf.len());
        }
    }

    #[test]
    fn rejects_non_canonical_and_overflow() {
        assert!(matches!(get(&[0x80, 0], &mut 0), Err(Error::InvalidVarint)));
        assert!(get(&[0xff; 10], &mut 0).is_err());
    }
}
