//! Version 1 of MathSVG's native, lossless procedural data language.
//!
//! The DSL is deliberately not executable bytecode.  Every implemented node
//! has finite typed output, exact canonical serialisation and deterministic
//! preflight costs.  Unknown or not-yet-implemented opcodes are rejected.

mod entropy;
mod node;
mod opcode;
mod program;
mod value;

pub use mathsvg_coordinates::{
    CoordinateDescriptor as NativeCoordinateDescriptor, CoordinateTransform,
    Endianness as CoordinateEndianness,
};
pub use node::{CoordinateDescriptor, CorrectionKind, Exception, Node, Recurrence};
pub use opcode::Opcode;
pub use program::{EncodedSections, Program, ProgramBreakdown, ValidationReport};
pub use value::{Endian, ValueType};
