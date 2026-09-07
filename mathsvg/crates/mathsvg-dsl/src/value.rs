use mathsvg_core::{checked_u64_add, checked_u64_mul, word_bytes, Error, Result};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum Endian {
    Little,
    Big,
}

/// Finite values admitted by the normative evaluator.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ValueType {
    Bytes(u64),
    Bits(u64),
    Words {
        width: u8,
        count: u64,
        endian: Endian,
    },
    Mask(u64),
}

impl ValueType {
    pub fn logical_len(&self) -> u64 {
        match *self {
            Self::Bytes(length) | Self::Bits(length) | Self::Mask(length) => length,
            Self::Words { count, .. } => count,
        }
    }

    pub fn byte_len(&self) -> Result<u64> {
        match *self {
            Self::Bytes(length) => Ok(length),
            Self::Bits(length) | Self::Mask(length) => {
                checked_u64_add(length, 7, "packed bit length").map(|bits| bits / 8)
            }
            Self::Words { width, count, .. } => {
                checked_u64_mul(count, u64::from(word_bytes(width)?), "word vector bytes")
            }
        }
    }

    pub fn is_byte_producing(&self) -> bool {
        matches!(self, Self::Bytes(_))
    }

    pub(crate) fn concatenate(types: &[Self]) -> Result<Self> {
        let Some(first) = types.first() else {
            return Ok(Self::Bytes(0));
        };

        match first {
            Self::Bytes(_) => {
                let mut total = 0u64;
                for value_type in types {
                    let Self::Bytes(length) = value_type else {
                        return Err(Error::TypeMismatch {
                            context: "CONCAT children",
                        });
                    };
                    total = checked_u64_add(total, *length, "concatenated byte length")?;
                }
                Ok(Self::Bytes(total))
            }
            Self::Bits(_) => {
                let mut total = 0u64;
                for value_type in types {
                    let Self::Bits(length) = value_type else {
                        return Err(Error::TypeMismatch {
                            context: "CONCAT children",
                        });
                    };
                    total = checked_u64_add(total, *length, "concatenated bit length")?;
                }
                Ok(Self::Bits(total))
            }
            Self::Mask(_) => {
                let mut total = 0u64;
                for value_type in types {
                    let Self::Mask(length) = value_type else {
                        return Err(Error::TypeMismatch {
                            context: "CONCAT children",
                        });
                    };
                    total = checked_u64_add(total, *length, "concatenated mask length")?;
                }
                Ok(Self::Mask(total))
            }
            Self::Words { width, endian, .. } => {
                let mut total = 0u64;
                for value_type in types {
                    let Self::Words {
                        width: child_width,
                        count,
                        endian: child_endian,
                    } = value_type
                    else {
                        return Err(Error::TypeMismatch {
                            context: "CONCAT children",
                        });
                    };
                    if child_width != width || child_endian != endian {
                        return Err(Error::TypeMismatch {
                            context: "CONCAT word layout",
                        });
                    }
                    total = checked_u64_add(total, *count, "concatenated word count")?;
                }
                Ok(Self::Words {
                    width: *width,
                    count: total,
                    endian: *endian,
                })
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_lengths_cover_all_value_kinds() {
        assert_eq!(ValueType::Bytes(9).byte_len().unwrap(), 9);
        assert_eq!(ValueType::Bits(9).byte_len().unwrap(), 2);
        assert_eq!(ValueType::Mask(8).byte_len().unwrap(), 1);
        assert_eq!(
            ValueType::Words {
                width: 24,
                count: 3,
                endian: Endian::Little
            }
            .byte_len()
            .unwrap(),
            9
        );
    }

    #[test]
    fn concat_preserves_exact_type() {
        assert_eq!(
            ValueType::concatenate(&[ValueType::Bytes(2), ValueType::Bytes(3)]).unwrap(),
            ValueType::Bytes(5)
        );
        assert!(ValueType::concatenate(&[ValueType::Bytes(2), ValueType::Bits(16)]).is_err());
    }
}
