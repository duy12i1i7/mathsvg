//! Exact reversible coordinate transforms for MathSVG.
//!
//! Statistical probes in this crate only construct a finite candidate set.
//! Final selection requires a caller-supplied complete [`CandidateCost`].

mod descriptor;
mod discovery;
mod transform;

pub use descriptor::{
    CoordinateAnalysis, CoordinateDescriptor, CoordinateTransform, Endianness, BIT_PLANE_OPCODE,
    BYTE_PLANE_OPCODE, MAX_CHANNELS, MAX_RECORD_BYTES, STRIDE_OPCODE,
};
pub use discovery::{
    discover_candidates, select_exact, CandidateOrigin, CoordinateCandidate, DiscoveryConfig,
    DiscoveryEvent, DiscoveryEventKind, DiscoveryResult, ExactSelection,
};
pub use transform::{forward, inverse, inverse_into};
