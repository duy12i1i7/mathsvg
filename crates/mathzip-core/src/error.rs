use thiserror::Error;

/// Errors returned by MathZip. Decoder errors never require panics for malformed
/// input and deliberately avoid exposing partially decoded output.
#[derive(Debug, Error)]
pub enum Error {
    #[error("invalid MathZip magic")]
    InvalidMagic,
    #[error("unsupported MathZip format version {0}")]
    UnsupportedVersion(u16),
    #[error("truncated archive while reading {context}")]
    Truncated { context: &'static str },
    #[error("invalid archive: {0}")]
    InvalidArchive(&'static str),
    #[error("invalid archive: {0}")]
    InvalidArchiveOwned(String),
    #[error("non-canonical or overflowing unsigned LEB128 integer")]
    InvalidVarint,
    #[error("unknown {kind} identifier {id}")]
    UnknownId { kind: &'static str, id: u8 },
    #[error("{kind} checksum mismatch")]
    ChecksumMismatch { kind: &'static str },
    #[error("decoder limit exceeded: {what}={actual}, limit={limit}")]
    LimitExceeded {
        what: &'static str,
        actual: u64,
        limit: u64,
    },
    #[error("integer overflow while processing archive")]
    IntegerOverflow,
    #[error("model parameters are invalid: {0}")]
    InvalidModel(&'static str),
    #[error("residual payload is invalid: {0}")]
    InvalidResidual(&'static str),
    #[error("Zstandard residual coding failed: {0}")]
    ResidualCodec(String),
    #[error("transform parameters are invalid: {0}")]
    InvalidTransform(&'static str),
    #[error("encoder could not produce a valid representation")]
    NoRepresentation,
}

pub type Result<T> = std::result::Result<T, Error>;
