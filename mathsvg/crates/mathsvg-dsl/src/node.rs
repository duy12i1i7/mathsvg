use mathsvg_coordinates::{
    CoordinateDescriptor as NativeCoordinateDescriptor, CoordinateTransform,
};
use mathsvg_core::{
    checked_u64_add, checked_u64_mul, modulus_fits_width, Cursor, Error, Limits, Result, Writer,
};

use crate::{entropy::inspect_leaf, Opcode};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum CoordinateDescriptor {
    /// MVP metadata-only grouping.  Coordinate transforms receive their own
    /// versioned descriptors in the coordinate phase.
    Identity,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum CorrectionKind {
    Add,
    Subtract,
    Xor,
}

impl CorrectionKind {
    pub(crate) const fn opcode(self) -> Opcode {
        match self {
            Self::Add => Opcode::Add,
            Self::Subtract => Opcode::Sub,
            Self::Xor => Opcode::Xor,
        }
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Exception {
    pub position: u64,
    pub value: u8,
}

/// Restored-prior linear feedback.
///
/// For order `k`, entries `0..k` are `initial_state`.  Later entry `i` is
/// `sum(coefficients[j-1] * x[i-j]) mod modulus` for `j = 1..=k`.
/// Therefore coefficient zero always multiplies the most recently restored
/// prior value, matching the oracle and the usual lag-polynomial convention.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Recurrence {
    pub count: u64,
    pub width: u8,
    pub modulus: u64,
    pub coefficients: Vec<u64>,
    pub initial_state: Vec<u64>,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum Node {
    Literal(Vec<u8>),
    /// A complete canonical `mathsvg-entropy` v1 leaf envelope.
    EntropyLiteral(Vec<u8>),
    Const {
        length: u64,
        value: u8,
    },
    Linear {
        count: u64,
        width: u8,
        modulus: u64,
        a: u64,
        b: u64,
    },
    Periodic {
        pattern: Vec<u8>,
        repetitions: u64,
        suffix: Vec<u8>,
    },
    Recurrence(Recurrence),
    File {
        original_length: u64,
        child: Box<Node>,
    },
    Concat(Vec<Node>),
    Split {
        /// Absolute byte offsets after every child except the last.
        boundaries: Vec<u64>,
        children: Vec<Node>,
    },
    Group {
        coordinate: CoordinateDescriptor,
        child: Box<Node>,
    },
    /// A native reversible coordinate node. Parameters are serialized before
    /// the embedded transformed-domain child.
    Coordinate {
        descriptor: NativeCoordinateDescriptor,
        child: Box<Node>,
    },
    Correct {
        kind: CorrectionKind,
        prediction: Box<Node>,
        correction: Box<Node>,
    },
    Exceptions {
        base: Box<Node>,
        exceptions: Vec<Exception>,
    },
    Reference {
        definition: u32,
        /// Reserved v1 representation for parameterized definitions.  The MVP
        /// validator accepts only the empty vector because binding semantics
        /// are intentionally not guessed.
        parameter_delta: Vec<i64>,
    },
}

impl Node {
    pub fn opcode(&self) -> Opcode {
        match self {
            Self::Literal(_) => Opcode::Literal,
            Self::EntropyLiteral(_) => Opcode::EntropyLiteral,
            Self::Const { .. } => Opcode::Const,
            Self::Linear { .. } => Opcode::Linear,
            Self::Periodic { .. } => Opcode::Periodic,
            Self::Recurrence(_) => Opcode::Recurrence,
            Self::File { .. } => Opcode::File,
            Self::Concat(_) => Opcode::Concat,
            Self::Split { .. } => Opcode::Split,
            Self::Group { .. } => Opcode::Group,
            Self::Coordinate { descriptor, .. } => coordinate_opcode(descriptor),
            Self::Correct { kind, .. } => kind.opcode(),
            Self::Exceptions { .. } => Opcode::Exceptions,
            Self::Reference { .. } => Opcode::Reference,
        }
    }
}

#[derive(Debug, Default)]
pub(crate) struct ParseState {
    pub nodes: u64,
    pub edges: u64,
    pub dependencies: u64,
}

impl ParseState {
    fn node(&mut self, depth: u64, limits: &Limits) -> Result<()> {
        limits.check("graph depth", depth, limits.max_graph_depth)?;
        self.nodes = checked_u64_add(self.nodes, 1, "parsed node count")?;
        limits.check("nodes", self.nodes, limits.max_nodes)
    }

    fn edges(&mut self, amount: u64, limits: &Limits) -> Result<()> {
        limits.check("fan-out", amount, limits.max_fan_out)?;
        self.edges = checked_u64_add(self.edges, amount, "parsed edge count")?;
        limits.check("edges", self.edges, limits.max_edges)
    }

    fn dependency(&mut self, limits: &Limits) -> Result<()> {
        self.dependencies = checked_u64_add(self.dependencies, 1, "parsed dependency count")?;
        self.edges(1, limits)
    }
}

pub(crate) fn encode_node(node: &Node, limits: &Limits, output: &mut Writer) -> Result<()> {
    let mut payload = Writer::new();
    match node {
        Node::Literal(bytes) => {
            payload.write_bytes(bytes);
        }
        Node::EntropyLiteral(envelope) => {
            payload.write_raw(envelope);
        }
        Node::Const { length, value } => {
            payload.write_u64(*length);
            payload.write_u8(*value);
        }
        Node::Linear {
            count,
            width,
            modulus,
            a,
            b,
        } => {
            payload.write_u64(*count);
            payload.write_u64(u64::from(*width));
            payload.write_u64(*modulus);
            payload.write_u64(*a);
            payload.write_u64(*b);
        }
        Node::Periodic {
            pattern,
            repetitions,
            suffix,
        } => {
            payload.write_bytes(pattern);
            payload.write_u64(*repetitions);
            payload.write_bytes(suffix);
        }
        Node::Recurrence(recurrence) => {
            payload.write_u64(recurrence.count);
            payload.write_u64(u64::from(recurrence.width));
            payload.write_u64(recurrence.modulus);
            payload.write_usize(recurrence.coefficients.len());
            for coefficient in &recurrence.coefficients {
                payload.write_u64(*coefficient);
            }
            payload.write_usize(recurrence.initial_state.len());
            for value in &recurrence.initial_state {
                payload.write_u64(*value);
            }
        }
        Node::File {
            original_length,
            child,
        } => {
            payload.write_u64(*original_length);
            encode_node(child, limits, &mut payload)?;
        }
        Node::Concat(children) => {
            payload.write_usize(children.len());
            for child in children {
                encode_node(child, limits, &mut payload)?;
            }
        }
        Node::Split {
            boundaries,
            children,
        } => {
            payload.write_usize(boundaries.len());
            for boundary in boundaries {
                payload.write_u64(*boundary);
            }
            payload.write_usize(children.len());
            for child in children {
                encode_node(child, limits, &mut payload)?;
            }
        }
        Node::Group { coordinate, child } => {
            match coordinate {
                CoordinateDescriptor::Identity => payload.write_u8(0),
            }
            encode_node(child, limits, &mut payload)?;
        }
        Node::Coordinate { descriptor, child } => {
            if descriptor.transform == CoordinateTransform::Identity {
                return Err(Error::InvalidValue(
                    "identity coordinate must use GROUP compatibility node",
                ));
            }
            payload.write_raw(&descriptor.encode_parameters(limits)?);
            encode_node(child, limits, &mut payload)?;
        }
        Node::Correct {
            prediction,
            correction,
            ..
        } => {
            encode_node(prediction, limits, &mut payload)?;
            encode_node(correction, limits, &mut payload)?;
        }
        Node::Exceptions { base, exceptions } => {
            encode_node(base, limits, &mut payload)?;
            payload.write_usize(exceptions.len());
            let mut previous = 0u64;
            for (index, exception) in exceptions.iter().enumerate() {
                let delta = if index == 0 {
                    exception.position
                } else {
                    exception
                        .position
                        .checked_sub(previous)
                        .ok_or(Error::InvalidValue("exceptions are not sorted"))?
                };
                payload.write_u64(delta);
                payload.write_u8(exception.value);
                previous = exception.position;
            }
        }
        Node::Reference {
            definition,
            parameter_delta,
        } => {
            payload.write_u64(u64::from(*definition));
            payload.write_usize(parameter_delta.len());
            for delta in parameter_delta {
                payload.write_i64(*delta);
            }
        }
    }

    limits.check(
        "node payload bytes",
        payload.len() as u64,
        limits.max_node_payload_bytes,
    )?;
    output.write_u8(node.opcode().byte());
    output.write_u8(0);
    output.write_usize(payload.len());
    output.write_raw(payload.as_slice());
    Ok(())
}

pub(crate) fn decode_node(
    cursor: &mut Cursor<'_>,
    limits: &Limits,
    state: &mut ParseState,
    depth: u64,
) -> Result<Node> {
    state.node(depth, limits)?;
    let opcode_byte = cursor.read_u8("node opcode")?;
    let opcode = Opcode::parse(opcode_byte)?;
    let flags = cursor.read_u8("node flags")?;
    if flags != 0 {
        return Err(Error::InvalidFlags {
            opcode: opcode_byte,
            flags,
        });
    }
    let payload_length = cursor.read_usize("node payload length")?;
    limits.check(
        "node payload bytes",
        payload_length as u64,
        limits.max_node_payload_bytes,
    )?;
    let mut payload = cursor.subcursor(payload_length, "node payload")?;
    let child_depth = depth.checked_add(1).ok_or(Error::IntegerOverflow {
        context: "graph depth",
    })?;

    let node = match opcode {
        Opcode::Literal => {
            let bytes = payload.read_bytes("literal bytes")?;
            limits.check(
                "block output bytes",
                bytes.len() as u64,
                limits.max_block_output_bytes,
            )?;
            Node::Literal(bytes.to_vec())
        }
        Opcode::EntropyLiteral => {
            let envelope =
                payload.read_exact(payload.remaining(), "native entropy leaf envelope")?;
            inspect_leaf(envelope, limits)?;
            Node::EntropyLiteral(envelope.to_vec())
        }
        Opcode::Const => Node::Const {
            length: payload.read_u64()?,
            value: payload.read_u8("constant value")?,
        },
        Opcode::Linear => Node::Linear {
            count: payload.read_u64()?,
            width: read_width(&mut payload)?,
            modulus: payload.read_u64()?,
            a: payload.read_u64()?,
            b: payload.read_u64()?,
        },
        Opcode::Periodic => {
            let pattern = payload.read_bytes("periodic pattern")?;
            limits.check(
                "pattern bytes",
                pattern.len() as u64,
                limits.max_pattern_bytes,
            )?;
            let repetitions = payload.read_u64()?;
            let suffix = payload.read_bytes("periodic suffix")?;
            limits.check(
                "pattern bytes",
                suffix.len() as u64,
                limits.max_pattern_bytes,
            )?;
            Node::Periodic {
                pattern: pattern.to_vec(),
                repetitions,
                suffix: suffix.to_vec(),
            }
        }
        Opcode::Recurrence => {
            let count = payload.read_u64()?;
            let width = read_width(&mut payload)?;
            let modulus = payload.read_u64()?;
            let coefficient_count = payload.read_usize("recurrence coefficient count")?;
            limits.check(
                "recurrence order",
                coefficient_count as u64,
                limits.max_recurrence_order,
            )?;
            let mut coefficients = Vec::with_capacity(coefficient_count);
            for _ in 0..coefficient_count {
                coefficients.push(payload.read_u64()?);
            }
            let state_count = payload.read_usize("recurrence initial-state count")?;
            limits.check(
                "recurrence order",
                state_count as u64,
                limits.max_recurrence_order,
            )?;
            let mut initial_state = Vec::with_capacity(state_count);
            for _ in 0..state_count {
                initial_state.push(payload.read_u64()?);
            }
            Node::Recurrence(Recurrence {
                count,
                width,
                modulus,
                coefficients,
                initial_state,
            })
        }
        Opcode::File => {
            state.edges(1, limits)?;
            Node::File {
                original_length: payload.read_u64()?,
                child: Box::new(decode_node(&mut payload, limits, state, child_depth)?),
            }
        }
        Opcode::Concat => {
            let child_count = payload.read_usize("CONCAT child count")?;
            state.edges(child_count as u64, limits)?;
            let mut children = Vec::with_capacity(child_count);
            for _ in 0..child_count {
                children.push(decode_node(&mut payload, limits, state, child_depth)?);
            }
            Node::Concat(children)
        }
        Opcode::Split => {
            let boundary_count = payload.read_usize("SPLIT boundary count")?;
            limits.check("fan-out", boundary_count as u64, limits.max_fan_out)?;
            let mut boundaries = Vec::with_capacity(boundary_count);
            for _ in 0..boundary_count {
                boundaries.push(payload.read_u64()?);
            }
            let child_count = payload.read_usize("SPLIT child count")?;
            state.edges(child_count as u64, limits)?;
            let mut children = Vec::with_capacity(child_count);
            for _ in 0..child_count {
                children.push(decode_node(&mut payload, limits, state, child_depth)?);
            }
            Node::Split {
                boundaries,
                children,
            }
        }
        Opcode::Group => {
            let coordinate = match payload.read_u8("GROUP coordinate descriptor")? {
                0 => CoordinateDescriptor::Identity,
                _ => return Err(Error::InvalidValue("unknown coordinate descriptor")),
            };
            state.edges(1, limits)?;
            Node::Group {
                coordinate,
                child: Box::new(decode_node(&mut payload, limits, state, child_depth)?),
            }
        }
        Opcode::Stride | Opcode::BytePlane | Opcode::BitPlane => {
            let descriptor =
                NativeCoordinateDescriptor::decode_parameters(opcode_byte, &mut payload, limits)?;
            state.edges(1, limits)?;
            Node::Coordinate {
                descriptor,
                child: Box::new(decode_node(&mut payload, limits, state, child_depth)?),
            }
        }
        Opcode::Add | Opcode::Sub | Opcode::Xor => {
            state.edges(2, limits)?;
            let prediction = Box::new(decode_node(&mut payload, limits, state, child_depth)?);
            let correction = Box::new(decode_node(&mut payload, limits, state, child_depth)?);
            let kind = if opcode == Opcode::Add {
                CorrectionKind::Add
            } else if opcode == Opcode::Sub {
                CorrectionKind::Subtract
            } else {
                CorrectionKind::Xor
            };
            Node::Correct {
                kind,
                prediction,
                correction,
            }
        }
        Opcode::Exceptions => {
            // One structural child plus replacement parameters.
            state.edges(1, limits)?;
            let base = Box::new(decode_node(&mut payload, limits, state, child_depth)?);
            let count = payload.read_usize("exception count")?;
            limits.check("fan-out", count as u64, limits.max_fan_out)?;
            let mut exceptions = Vec::with_capacity(count);
            let mut previous = 0u64;
            for index in 0..count {
                let delta = payload.read_u64()?;
                if index > 0 && delta == 0 {
                    return Err(Error::InvalidValue("duplicate exception position"));
                }
                let position = if index == 0 {
                    delta
                } else {
                    checked_u64_add(previous, delta, "exception position")?
                };
                exceptions.push(Exception {
                    position,
                    value: payload.read_u8("exception replacement")?,
                });
                previous = position;
            }
            Node::Exceptions { base, exceptions }
        }
        Opcode::Reference => {
            state.dependency(limits)?;
            let definition = u32::try_from(payload.read_u64()?)
                .map_err(|_| Error::InvalidValue("definition ID exceeds u32"))?;
            let count = payload.read_usize("parameter delta count")?;
            // Each delta consumes at least one byte, so this check bounds the
            // allocation before any untrusted `with_capacity` call.
            limits.check("parameter bytes", count as u64, limits.max_parameter_bytes)?;
            limits.check(
                "parameter delta allocation",
                checked_u64_mul(
                    count as u64,
                    core::mem::size_of::<i64>() as u64,
                    "parameter delta allocation",
                )?,
                limits.max_temporary_bytes,
            )?;
            let mut parameter_delta = Vec::with_capacity(count);
            for _ in 0..count {
                parameter_delta.push(payload.read_i64()?);
            }
            Node::Reference {
                definition,
                parameter_delta,
            }
        }
    };

    payload.finish("node payload")?;
    Ok(node)
}

fn read_width(cursor: &mut Cursor<'_>) -> Result<u8> {
    u8::try_from(cursor.read_u64()?).map_err(|_| Error::InvalidValue("word width exceeds u8"))
}

fn coordinate_opcode(descriptor: &NativeCoordinateDescriptor) -> Opcode {
    match descriptor.transform {
        CoordinateTransform::Identity => Opcode::Group,
        CoordinateTransform::Stride { .. } => Opcode::Stride,
        CoordinateTransform::BytePlane { .. } => Opcode::BytePlane,
        CoordinateTransform::BitPlane => Opcode::BitPlane,
    }
}

pub(crate) fn validate_generator_parameters(node: &Node, limits: &Limits) -> Result<()> {
    match node {
        Node::Linear {
            width,
            modulus,
            a,
            b,
            ..
        } => {
            if !modulus_fits_width(*modulus, *width) {
                return Err(Error::InvalidValue(
                    "LINEAR modulus does not fit declared width",
                ));
            }
            if *a >= *modulus || *b >= *modulus {
                return Err(Error::InvalidValue(
                    "LINEAR parameters must be reduced modulo modulus",
                ));
            }
        }
        Node::Periodic {
            pattern,
            repetitions,
            suffix,
        } => {
            let parameter_bytes =
                checked_u64_add(pattern.len() as u64, suffix.len() as u64, "pattern bytes")?;
            limits.check("pattern bytes", parameter_bytes, limits.max_pattern_bytes)?;
            if *repetitions > 0 && pattern.is_empty() {
                return Err(Error::InvalidValue(
                    "PERIODIC cannot repeat an empty pattern",
                ));
            }
        }
        Node::Recurrence(recurrence) => {
            if !modulus_fits_width(recurrence.modulus, recurrence.width) {
                return Err(Error::InvalidValue(
                    "RECURRENCE modulus does not fit declared width",
                ));
            }
            let order = recurrence.coefficients.len();
            if order == 0 {
                return Err(Error::InvalidValue("RECURRENCE order must be positive"));
            }
            limits.check(
                "recurrence order",
                order as u64,
                limits.max_recurrence_order,
            )?;
            if recurrence.initial_state.len() != order {
                return Err(Error::LengthMismatch {
                    context: "RECURRENCE initial state",
                    expected: order as u64,
                    actual: recurrence.initial_state.len() as u64,
                });
            }
            if recurrence.count < order as u64 {
                return Err(Error::InvalidValue("RECURRENCE count is below its order"));
            }
            if recurrence
                .coefficients
                .iter()
                .chain(&recurrence.initial_state)
                .any(|value| *value >= recurrence.modulus)
            {
                return Err(Error::InvalidValue(
                    "RECURRENCE values must be reduced modulo modulus",
                ));
            }
        }
        _ => {}
    }
    Ok(())
}

pub(crate) fn generator_output_bytes(count: u64, width: u8, context: &'static str) -> Result<u64> {
    if !mathsvg_core::validate_word_width(width) {
        return Err(Error::InvalidValue("unsupported word width"));
    }
    checked_u64_mul(count, u64::from(width / 8), context)
}
