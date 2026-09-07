use mathsvg_core::{Error, Limits, Result};
use mathsvg_entropy::{DecodeLimits, LeafMetadata};

pub(crate) fn decode_limits(limits: &Limits) -> DecodeLimits {
    DecodeLimits {
        max_encoded_bytes: limits.max_node_payload_bytes.min(limits.max_archive_bytes),
        max_output_bytes: limits.max_output_bytes.min(limits.max_block_output_bytes),
        max_work: limits.max_work,
    }
}

pub(crate) fn inspect_leaf(envelope: &[u8], limits: &Limits) -> Result<LeafMetadata> {
    mathsvg_entropy::inspect(envelope, decode_limits(limits)).map_err(map_error)
}

pub(crate) fn decode_leaf(envelope: &[u8], limits: &Limits) -> Result<Vec<u8>> {
    mathsvg_entropy::decode(envelope, decode_limits(limits))
        .map(|decoded| decoded.bytes)
        .map_err(map_error)
}

pub(crate) fn map_error(error: mathsvg_entropy::Error) -> Error {
    match error {
        mathsvg_entropy::Error::Truncated { context, position } => {
            Error::Truncated { context, position }
        }
        mathsvg_entropy::Error::InvalidVarint { position } => Error::InvalidVarint { position },
        mathsvg_entropy::Error::IntegerOverflow { context } => Error::IntegerOverflow { context },
        mathsvg_entropy::Error::UnsupportedVersion(_) => {
            Error::InvalidValue("unsupported native entropy leaf version")
        }
        mathsvg_entropy::Error::InvalidOpcode(_) => {
            Error::InvalidValue("invalid native entropy leaf codec opcode")
        }
        mathsvg_entropy::Error::InvalidFlags(_) => {
            Error::InvalidValue("invalid native entropy leaf flags")
        }
        mathsvg_entropy::Error::InvalidValue(message)
        | mathsvg_entropy::Error::NonCanonical(message) => Error::InvalidValue(message),
        mathsvg_entropy::Error::LengthMismatch {
            context,
            expected,
            actual,
        } => Error::LengthMismatch {
            context,
            expected,
            actual,
        },
        mathsvg_entropy::Error::TrailingData { context, remaining } => {
            Error::TrailingData { context, remaining }
        }
        mathsvg_entropy::Error::LimitExceeded {
            what,
            actual,
            limit,
        } => Error::LimitExceeded {
            what,
            actual,
            limit,
        },
        mathsvg_entropy::Error::AllocationFailed { .. } => {
            Error::InvalidValue("native entropy leaf allocation failed")
        }
        mathsvg_entropy::Error::InternalSizeMismatch { .. } => {
            Error::InvalidValue("native entropy leaf internal size mismatch")
        }
    }
}
