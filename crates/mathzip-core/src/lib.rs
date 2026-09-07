//! MathZip's deterministic, lossless byte-stream codec.
//!
//! The encoder searches reversible transforms, mathematical predictors,
//! segmentations, and residual coders. Every choice is made using the number of
//! bytes that will actually be written to the archive. The decoder is smaller:
//! it only parses a bounded, versioned container and evaluates the selected
//! integer-only predictors.
//!
//! This crate is a research prototype. The format is versioned, but version 1
//! should not yet be treated as a long-term archival standard.

mod codec;
mod config;
mod container;
mod error;
mod models;
mod residual;
mod segmentation;
mod transforms;
mod varint;

pub use codec::{compress, compress_with_metrics, decompress, inspect, verify, EncodeMetrics};
pub use config::{
    DecodeLimits, EncodeOptions, Mode, ModelOptions, ResidualOptions, SegmentationMode,
    TransformOptions, MAX_RECURSIVE_TREE_DEPTH,
};
pub use container::{ArchiveInfo, ChecksumStatus, FORMAT_VERSION, FORMAT_VERSION_V1, MAGIC};
pub use error::{Error, Result};
pub use models::ModelKind;
pub use residual::{ResidualCoder, ResidualMode};
pub use transforms::TransformKind;
