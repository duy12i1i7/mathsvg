use core::fmt;

/// Errors produced by the native leaf counter, encoder, or bounded decoder.
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
    UnsupportedVersion(u8),
    InvalidOpcode(u8),
    InvalidFlags(u8),
    InvalidValue(&'static str),
    NonCanonical(&'static str),
    LengthMismatch {
        context: &'static str,
        expected: u64,
        actual: u64,
    },
    TrailingData {
        context: &'static str,
        remaining: usize,
    },
    LimitExceeded {
        what: &'static str,
        actual: u64,
        limit: u64,
    },
    AllocationFailed {
        requested: usize,
    },
    InternalSizeMismatch {
        counted: usize,
        encoded: usize,
    },
}

impl fmt::Display for Error {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Truncated { context, position } => {
                write!(formatter, "truncated {context} at byte {position}")
            }
            Self::InvalidVarint { position } => {
                write!(
                    formatter,
                    "non-canonical or overflowing unsigned LEB128 at byte {position}"
                )
            }
            Self::IntegerOverflow { context } => {
                write!(formatter, "integer overflow while computing {context}")
            }
            Self::UnsupportedVersion(version) => {
                write!(formatter, "unsupported leaf format version {version}")
            }
            Self::InvalidOpcode(opcode) => {
                write!(formatter, "invalid native leaf opcode 0x{opcode:02x}")
            }
            Self::InvalidFlags(flags) => {
                write!(formatter, "invalid native leaf flags 0x{flags:02x}")
            }
            Self::InvalidValue(message) => write!(formatter, "invalid value: {message}"),
            Self::NonCanonical(message) => {
                write!(formatter, "non-canonical native leaf: {message}")
            }
            Self::LengthMismatch {
                context,
                expected,
                actual,
            } => write!(
                formatter,
                "length mismatch in {context}: expected {expected}, got {actual}"
            ),
            Self::TrailingData { context, remaining } => {
                write!(formatter, "{remaining} trailing bytes in {context}")
            }
            Self::LimitExceeded {
                what,
                actual,
                limit,
            } => write!(
                formatter,
                "decoder limit exceeded: {what}={actual}, limit={limit}"
            ),
            Self::AllocationFailed { requested } => {
                write!(formatter, "could not allocate {requested} output bytes")
            }
            Self::InternalSizeMismatch { counted, encoded } => write!(
                formatter,
                "internal size mismatch: counted {counted}, encoded {encoded}"
            ),
        }
    }
}

impl std::error::Error for Error {}

pub type Result<T> = core::result::Result<T, Error>;
