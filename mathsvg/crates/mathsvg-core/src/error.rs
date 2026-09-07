use core::fmt;

/// Errors returned while parsing or validating native MathSVG data.
///
/// All variants are deterministic and safe to expose for hostile input.  No
/// error variant carries partially decoded output.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Error {
    Truncated {
        context: &'static str,
        position: usize,
    },
    InvalidVarint {
        position: usize,
    },
    IntegerOverflow {
        context: &'static str,
    },
    InvalidOpcode(u8),
    UnsupportedOpcode(u8),
    InvalidFlags {
        opcode: u8,
        flags: u8,
    },
    TrailingData {
        context: &'static str,
        remaining: usize,
    },
    InvalidValue(&'static str),
    InvalidReference {
        id: u64,
        available_definitions: usize,
    },
    TypeMismatch {
        context: &'static str,
    },
    LengthMismatch {
        context: &'static str,
        expected: u64,
        actual: u64,
    },
    LimitExceeded {
        what: &'static str,
        actual: u64,
        limit: u64,
    },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Truncated { context, position } => {
                write!(f, "truncated {context} at byte {position}")
            }
            Self::InvalidVarint { position } => {
                write!(
                    f,
                    "non-canonical or overflowing unsigned LEB128 at byte {position}"
                )
            }
            Self::IntegerOverflow { context } => {
                write!(f, "integer overflow while computing {context}")
            }
            Self::InvalidOpcode(opcode) => write!(f, "invalid opcode 0x{opcode:02x}"),
            Self::UnsupportedOpcode(opcode) => {
                write!(f, "opcode 0x{opcode:02x} is not implemented")
            }
            Self::InvalidFlags { opcode, flags } => {
                write!(f, "invalid flags 0x{flags:02x} for opcode 0x{opcode:02x}")
            }
            Self::TrailingData { context, remaining } => {
                write!(f, "{remaining} trailing bytes in {context}")
            }
            Self::InvalidValue(message) => write!(f, "invalid value: {message}"),
            Self::InvalidReference {
                id,
                available_definitions,
            } => write!(
                f,
                "reference {id} is not lower than the {available_definitions} available definitions"
            ),
            Self::TypeMismatch { context } => write!(f, "type mismatch in {context}"),
            Self::LengthMismatch {
                context,
                expected,
                actual,
            } => write!(
                f,
                "length mismatch in {context}: expected {expected}, got {actual}"
            ),
            Self::LimitExceeded {
                what,
                actual,
                limit,
            } => write!(f, "decoder limit exceeded: {what}={actual}, limit={limit}"),
        }
    }
}

impl std::error::Error for Error {}

pub type Result<T> = core::result::Result<T, Error>;
