use crate::{Error, Result};

pub(crate) const fn encoded_len(mut value: u64) -> u8 {
    let mut length = 1u8;
    while value >= 0x80 {
        value >>= 7;
        length += 1;
    }
    length
}

pub(crate) fn write_u64(output: &mut Vec<u8>, mut value: u64) {
    while value >= 0x80 {
        output.push(((value as u8) & 0x7f) | 0x80);
        value >>= 7;
    }
    output.push(value as u8);
}

#[derive(Clone, Debug)]
pub(crate) struct Cursor<'a> {
    input: &'a [u8],
    position: usize,
}

impl<'a> Cursor<'a> {
    pub(crate) fn new(input: &'a [u8]) -> Self {
        Self { input, position: 0 }
    }

    pub(crate) fn remaining(&self) -> usize {
        self.input.len() - self.position
    }

    pub(crate) fn read_u8(&mut self, context: &'static str) -> Result<u8> {
        let value = self
            .input
            .get(self.position)
            .copied()
            .ok_or(Error::Truncated {
                context,
                position: self.position,
            })?;
        self.position += 1;
        Ok(value)
    }

    pub(crate) fn read_u64(&mut self) -> Result<u64> {
        let start = self.position;
        let mut value = 0u64;

        for index in 0..10 {
            let byte = self.read_u8("unsigned LEB128 integer")?;
            if index == 9 && byte > 1 {
                return Err(Error::InvalidVarint { position: start });
            }

            value |= u64::from(byte & 0x7f) << (index * 7);
            if byte & 0x80 == 0 {
                if usize::from(encoded_len(value)) != self.position - start {
                    return Err(Error::InvalidVarint { position: start });
                }
                return Ok(value);
            }
        }

        Err(Error::InvalidVarint { position: start })
    }

    pub(crate) fn read_usize(&mut self, context: &'static str) -> Result<usize> {
        usize::try_from(self.read_u64()?).map_err(|_| Error::IntegerOverflow { context })
    }

    pub(crate) fn read_exact(&mut self, length: usize, context: &'static str) -> Result<&'a [u8]> {
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

    pub(crate) fn finish(self, context: &'static str) -> Result<()> {
        let remaining = self.remaining();
        if remaining == 0 {
            Ok(())
        } else {
            Err(Error::TrailingData { context, remaining })
        }
    }
}
