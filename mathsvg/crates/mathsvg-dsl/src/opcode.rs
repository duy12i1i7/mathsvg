use mathsvg_core::{Error, Result};

/// Implemented Procedural DSL v1 opcodes.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
#[repr(u8)]
pub enum Opcode {
    Literal = 0x00,
    Const = 0x01,
    Linear = 0x03,
    Periodic = 0x07,
    Recurrence = 0x09,
    EntropyLiteral = 0x13,
    File = 0x20,
    Concat = 0x21,
    Split = 0x22,
    Group = 0x24,
    Reference = 0x26,
    Stride = 0x41,
    BytePlane = 0x42,
    BitPlane = 0x43,
    Add = 0x60,
    Sub = 0x61,
    Xor = 0x63,
    Exceptions = 0x6e,
}

impl Opcode {
    pub const fn byte(self) -> u8 {
        self as u8
    }

    pub fn parse(byte: u8) -> Result<Self> {
        let opcode = match byte {
            0x00 => Self::Literal,
            0x01 => Self::Const,
            0x03 => Self::Linear,
            0x07 => Self::Periodic,
            0x09 => Self::Recurrence,
            0x13 => Self::EntropyLiteral,
            0x20 => Self::File,
            0x21 => Self::Concat,
            0x22 => Self::Split,
            0x24 => Self::Group,
            0x26 => Self::Reference,
            0x41 => Self::Stride,
            0x42 => Self::BytePlane,
            0x43 => Self::BitPlane,
            0x60 => Self::Add,
            0x61 => Self::Sub,
            0x63 => Self::Xor,
            0x6e => Self::Exceptions,
            0xc0..=0xff => return Err(Error::InvalidOpcode(byte)),
            _ => return Err(Error::UnsupportedOpcode(byte)),
        };
        Ok(opcode)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn implemented_reserved_and_invalid_ranges_are_distinct() {
        assert_eq!(Opcode::parse(0x00).unwrap(), Opcode::Literal);
        assert_eq!(Opcode::parse(0x13).unwrap(), Opcode::EntropyLiteral);
        assert_eq!(Opcode::parse(0x40), Err(Error::UnsupportedOpcode(0x40)));
        assert_eq!(Opcode::parse(0x41).unwrap(), Opcode::Stride);
        assert_eq!(Opcode::parse(0x42).unwrap(), Opcode::BytePlane);
        assert_eq!(Opcode::parse(0x43).unwrap(), Opcode::BitPlane);
        assert_eq!(Opcode::parse(0xc0), Err(Error::InvalidOpcode(0xc0)));
    }
}
