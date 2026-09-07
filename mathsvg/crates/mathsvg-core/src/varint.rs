use crate::{Error, Result};

/// Return the one and only valid uLEB128 length for `value`.
pub const fn encoded_len(mut value: u64) -> usize {
    let mut length = 1;
    while value >= 0x80 {
        value >>= 7;
        length += 1;
    }
    length
}

/// Canonical ZigZag mapping from every `i64` to one `u64`.
pub const fn zigzag_encode(value: i64) -> u64 {
    ((value as u64) << 1) ^ ((value >> 63) as u64)
}

/// Inverse of [`zigzag_encode`].
pub const fn zigzag_decode(value: u64) -> i64 {
    ((value >> 1) as i64) ^ (-((value & 1) as i64))
}

/// A minimal canonical byte writer.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct Writer {
    bytes: Vec<u8>,
}

impl Writer {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            bytes: Vec::with_capacity(capacity),
        }
    }

    pub fn write_u8(&mut self, value: u8) {
        self.bytes.push(value);
    }

    pub fn write_u64(&mut self, mut value: u64) {
        while value >= 0x80 {
            self.bytes.push(((value as u8) & 0x7f) | 0x80);
            value >>= 7;
        }
        self.bytes.push(value as u8);
    }

    pub fn write_usize(&mut self, value: usize) {
        self.write_u64(value as u64);
    }

    pub fn write_i64(&mut self, value: i64) {
        self.write_u64(zigzag_encode(value));
    }

    pub fn write_raw(&mut self, bytes: &[u8]) {
        self.bytes.extend_from_slice(bytes);
    }

    pub fn write_bytes(&mut self, bytes: &[u8]) {
        self.write_usize(bytes.len());
        self.write_raw(bytes);
    }

    pub fn len(&self) -> usize {
        self.bytes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.bytes.is_empty()
    }

    pub fn as_slice(&self) -> &[u8] {
        &self.bytes
    }

    pub fn into_inner(self) -> Vec<u8> {
        self.bytes
    }
}

/// A bounded reader for canonical MathSVG integers and payloads.
#[derive(Clone, Debug)]
pub struct Cursor<'a> {
    input: &'a [u8],
    position: usize,
}

impl<'a> Cursor<'a> {
    pub fn new(input: &'a [u8]) -> Self {
        Self { input, position: 0 }
    }

    pub fn position(&self) -> usize {
        self.position
    }

    pub fn remaining(&self) -> usize {
        self.input.len() - self.position
    }

    pub fn is_empty(&self) -> bool {
        self.remaining() == 0
    }

    pub fn read_u8(&mut self, context: &'static str) -> Result<u8> {
        let value = *self.input.get(self.position).ok_or(Error::Truncated {
            context,
            position: self.position,
        })?;
        self.position += 1;
        Ok(value)
    }

    pub fn read_u64(&mut self) -> Result<u64> {
        let start = self.position;
        let mut value = 0u64;

        for index in 0..10 {
            let byte = self.read_u8("unsigned LEB128 integer")?;
            if index == 9 && byte > 1 {
                return Err(Error::InvalidVarint { position: start });
            }

            value |= u64::from(byte & 0x7f) << (index * 7);
            if byte & 0x80 == 0 {
                if self.position - start != encoded_len(value) {
                    return Err(Error::InvalidVarint { position: start });
                }
                return Ok(value);
            }
        }

        Err(Error::InvalidVarint { position: start })
    }

    pub fn read_usize(&mut self, context: &'static str) -> Result<usize> {
        usize::try_from(self.read_u64()?).map_err(|_| Error::IntegerOverflow { context })
    }

    pub fn read_i64(&mut self) -> Result<i64> {
        Ok(zigzag_decode(self.read_u64()?))
    }

    pub fn read_exact(&mut self, length: usize, context: &'static str) -> Result<&'a [u8]> {
        let end = self
            .position
            .checked_add(length)
            .ok_or(Error::IntegerOverflow { context })?;
        let bytes = self.input.get(self.position..end).ok_or(Error::Truncated {
            context,
            position: self.position,
        })?;
        self.position = end;
        Ok(bytes)
    }

    pub fn read_bytes(&mut self, context: &'static str) -> Result<&'a [u8]> {
        let length = self.read_usize(context)?;
        self.read_exact(length, context)
    }

    /// Advance by `length` and return a cursor bounded to exactly that slice.
    pub fn subcursor(&mut self, length: usize, context: &'static str) -> Result<Cursor<'a>> {
        Ok(Cursor::new(self.read_exact(length, context)?))
    }

    pub fn finish(self, context: &'static str) -> Result<()> {
        if self.is_empty() {
            Ok(())
        } else {
            Err(Error::TrailingData {
                context,
                remaining: self.remaining(),
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unsigned_boundaries_and_many_values_round_trip() {
        let boundaries = [
            0,
            1,
            126,
            127,
            128,
            129,
            16_383,
            16_384,
            u32::MAX as u64,
            u64::MAX,
        ];
        for value in boundaries.into_iter().chain(0..100_000) {
            let mut writer = Writer::new();
            writer.write_u64(value);
            assert_eq!(writer.len(), encoded_len(value));
            let encoded = writer.into_inner();
            let mut cursor = Cursor::new(&encoded);
            assert_eq!(cursor.read_u64().unwrap(), value);
            cursor.finish("test integer").unwrap();
        }
    }

    #[test]
    fn signed_boundaries_and_deterministic_sample_round_trip() {
        let boundaries = [i64::MIN, -65, -64, -1, 0, 1, 63, 64, i64::MAX];
        for value in boundaries {
            let mut writer = Writer::new();
            writer.write_i64(value);
            let encoded = writer.into_inner();
            let mut cursor = Cursor::new(&encoded);
            assert_eq!(cursor.read_i64().unwrap(), value);
            cursor.finish("signed integer").unwrap();
        }

        let mut state = 0x4d59_5df4_d0f3_3173u64;
        for _ in 0..10_000 {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1);
            let value = state as i64;
            assert_eq!(zigzag_decode(zigzag_encode(value)), value);
        }
    }

    #[test]
    fn rejects_noncanonical_truncated_and_overflowing_integers() {
        assert_eq!(
            Cursor::new(&[0x80, 0]).read_u64(),
            Err(Error::InvalidVarint { position: 0 })
        );
        assert!(matches!(
            Cursor::new(&[0x80]).read_u64(),
            Err(Error::Truncated { .. })
        ));
        assert_eq!(
            Cursor::new(&[0xff; 10]).read_u64(),
            Err(Error::InvalidVarint { position: 0 })
        );
        assert_eq!(
            Cursor::new(&[0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 2]).read_u64(),
            Err(Error::InvalidVarint { position: 0 })
        );
    }

    #[test]
    fn bounded_cursor_rejects_truncation_and_trailing_data() {
        let mut cursor = Cursor::new(&[1, 2, 3]);
        assert_eq!(cursor.read_exact(2, "pair").unwrap(), &[1, 2]);
        assert!(matches!(
            cursor.clone().finish("outer"),
            Err(Error::TrailingData { remaining: 1, .. })
        ));
        assert!(matches!(
            cursor.read_exact(2, "pair"),
            Err(Error::Truncated { position: 2, .. })
        ));
    }
}
