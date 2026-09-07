use mathsvg_coordinates::CoordinateTransform;
use mathsvg_core::{
    checked_u64_add, checked_u64_mul, encoded_len, zigzag_encode, CandidateCost, Cursor, Error,
    Limits, Result, Writer,
};

use crate::entropy::{decode_leaf, inspect_leaf};
use crate::node::{
    decode_node, encode_node, generator_output_bytes, validate_generator_parameters, ParseState,
};
use crate::{CorrectionKind, Endian, Node, Opcode, ValueType};

const V1_BLOCK_HEADER_BYTES: u64 = 72;

/// Canonical local-definition and root sections for one independent v1 block.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EncodedSections {
    pub definitions: Vec<u8>,
    pub root: Vec<u8>,
}

impl EncodedSections {
    pub fn dsl_bytes(&self) -> Result<u64> {
        checked_u64_add(
            self.definitions.len() as u64,
            self.root.len() as u64,
            "encoded DSL sections",
        )
    }

    pub fn v1_block_payload_bytes(&self) -> Result<u64> {
        checked_u64_add(
            V1_BLOCK_HEADER_BYTES,
            self.dsl_bytes()?,
            "block payload bytes",
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Program {
    /// Dense, block-local definitions.  Vector index is the definition ID.
    pub definitions: Vec<Node>,
    /// Exactly one top-level `FILE` node.
    pub root: Node,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidationReport {
    pub root_type: ValueType,
    pub original_bytes: u64,
    pub node_count: u64,
    pub edge_count: u64,
    pub dependency_count: u64,
    pub graph_depth: u64,
    pub decode_work: u64,
    pub generated_bytes: u64,
    pub temporary_bytes: u64,
    pub opcode_sequence: Vec<u8>,
}

/// Exact, non-overlapping DSL wire categories and conservative output
/// attribution for one canonical program.
///
/// `wire_bytes()` equals `Program::encode_sections(...).dsl_bytes()`. Complete
/// archive reporting adds container headers, directory, hashes and footer as a
/// separate `container_overhead_bytes` category.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ProgramBreakdown {
    pub function_graph_bytes: u64,
    pub coordinate_bytes: u64,
    /// The complete definition section, including its framing and nodes.
    pub shared_definition_bytes: u64,
    pub reference_bytes: u64,
    pub parameter_bytes: u64,
    pub residual_layer_bytes: u64,
    /// Raw literal records or the coded payload portion of entropy leaves.
    pub literal_leaf_bytes: u64,
    /// Entropy node and leaf-envelope framing, excluding the coded payload.
    pub entropy_metadata_bytes: u64,
    pub node_count: u64,
    /// Definition IDs with syntactic in-degree greater than one.
    pub shared_node_count: u64,
    /// Sum of the maximum nested correction depth for every residual root.
    pub residual_depth_sum: u64,
    pub residual_root_count: u64,
    /// Output bytes whose selected expression has no literal dependency.
    pub function_reconstructed_bytes: u64,
    /// Output bytes conservatively attributed to any literal dependency.
    pub literal_reconstructed_bytes: u64,
}

impl ProgramBreakdown {
    pub fn wire_bytes(&self) -> Result<u64> {
        [
            self.function_graph_bytes,
            self.coordinate_bytes,
            self.shared_definition_bytes,
            self.reference_bytes,
            self.parameter_bytes,
            self.residual_layer_bytes,
            self.literal_leaf_bytes,
            self.entropy_metadata_bytes,
        ]
        .into_iter()
        .try_fold(0u64, |total, value| {
            checked_u64_add(total, value, "program wire breakdown")
        })
    }
}

#[derive(Clone, Debug)]
struct Analysis {
    value_type: ValueType,
    nodes: u64,
    edges: u64,
    dependencies: u64,
    depth: u64,
    work: u64,
    generated: u64,
    temporary: u64,
    opcodes: Vec<u8>,
}

impl Analysis {
    fn output_bytes(&self) -> Result<u64> {
        self.value_type.byte_len()
    }
}

impl Program {
    pub fn literal(bytes: Vec<u8>) -> Self {
        let original_length = bytes.len() as u64;
        Self {
            definitions: Vec::new(),
            root: Node::File {
                original_length,
                child: Box::new(Node::Literal(bytes)),
            },
        }
    }

    /// Validate types, topology, exact lengths and deterministic preflight
    /// resource bounds without evaluating any node.
    pub fn validate(&self, limits: &Limits) -> Result<ValidationReport> {
        limits.check(
            "definitions",
            self.definitions.len() as u64,
            limits.max_definitions,
        )?;

        let mut summaries = Vec::with_capacity(self.definitions.len());
        let mut total_nodes = 0u64;
        let mut total_edges = 0u64;
        let mut total_dependencies = 0u64;
        let mut maximum_depth = 0u64;
        let mut definition_cache_bytes = 0u64;
        let mut maximum_inner_temporary = 0u64;
        let mut all_opcodes = Vec::new();

        for definition in &self.definitions {
            let analysis = analyze_node(definition, &summaries, false, 1, limits)?;
            check_analysis_limits(&analysis, limits)?;
            total_nodes = checked_u64_add(total_nodes, analysis.nodes, "program nodes")?;
            total_edges = checked_u64_add(total_edges, analysis.edges, "program edges")?;
            total_dependencies = checked_u64_add(
                total_dependencies,
                analysis.dependencies,
                "program dependencies",
            )?;
            maximum_depth = maximum_depth.max(analysis.depth);
            definition_cache_bytes = checked_u64_add(
                definition_cache_bytes,
                analysis.output_bytes()?,
                "definition cache bytes",
            )?;
            maximum_inner_temporary = maximum_inner_temporary.max(analysis.temporary);
            all_opcodes.extend_from_slice(&analysis.opcodes);
            summaries.push(analysis);
        }

        if !matches!(self.root, Node::File { .. }) {
            return Err(Error::InvalidValue("block root must be FILE"));
        }
        let root = analyze_node(&self.root, &summaries, true, 1, limits)?;
        check_analysis_limits(&root, limits)?;
        total_nodes = checked_u64_add(total_nodes, root.nodes, "program nodes")?;
        total_edges = checked_u64_add(total_edges, root.edges, "program edges")?;
        total_dependencies = checked_u64_add(
            total_dependencies,
            root.dependencies,
            "program dependencies",
        )?;
        maximum_depth = maximum_depth.max(root.depth);
        maximum_inner_temporary = maximum_inner_temporary.max(root.temporary);
        all_opcodes.extend_from_slice(&root.opcodes);

        limits.check("nodes", total_nodes, limits.max_nodes)?;
        limits.check("edges", total_edges, limits.max_edges)?;
        limits.check("graph depth", maximum_depth, limits.max_graph_depth)?;
        let temporary_bytes = checked_u64_add(
            definition_cache_bytes,
            maximum_inner_temporary,
            "program temporary bytes",
        )?;
        limits.check(
            "temporary bytes",
            temporary_bytes,
            limits.max_temporary_bytes,
        )?;

        let original_bytes = match root.value_type {
            ValueType::Bytes(length) => length,
            _ => {
                return Err(Error::TypeMismatch {
                    context: "FILE root output",
                })
            }
        };

        Ok(ValidationReport {
            root_type: root.value_type,
            original_bytes,
            node_count: total_nodes,
            edge_count: total_edges,
            dependency_count: total_dependencies,
            graph_depth: maximum_depth,
            decode_work: root.work,
            generated_bytes: root.generated,
            temporary_bytes,
            opcode_sequence: all_opcodes,
        })
    }

    fn encode_sections_after_validation(&self, limits: &Limits) -> Result<EncodedSections> {
        let mut definitions = Writer::new();
        for (id, definition) in self.definitions.iter().enumerate() {
            let mut record = Writer::new();
            encode_node(definition, limits, &mut record)?;
            definitions.write_usize(id);
            definitions.write_usize(record.len());
            definitions.write_raw(record.as_slice());
        }

        let mut root = Writer::new();
        encode_node(&self.root, limits, &mut root)?;
        let sections = EncodedSections {
            definitions: definitions.into_inner(),
            root: root.into_inner(),
        };
        limits.check(
            "archive bytes",
            sections.dsl_bytes()?,
            limits.max_archive_bytes,
        )?;
        limits.check(
            "block payload bytes",
            sections.v1_block_payload_bytes()?,
            limits.max_block_payload_bytes,
        )?;
        Ok(sections)
    }

    /// Validate once and encode the two DSL sections carried by one v1 block.
    /// Container encoders use the returned report for their directory fields,
    /// avoiding a second complete validation of entropy leaves.
    pub fn encode_sections_with_report(
        &self,
        limits: &Limits,
    ) -> Result<(EncodedSections, ValidationReport)> {
        let report = self.validate(limits)?;
        let sections = self.encode_sections_after_validation(limits)?;
        Ok((sections, report))
    }

    /// Encode the two DSL sections carried by one v1 block.  Container headers,
    /// directory entries and hashes are intentionally outside this crate.
    pub fn encode_sections(&self, limits: &Limits) -> Result<EncodedSections> {
        self.encode_sections_with_report(limits)
            .map(|(sections, _)| sections)
    }

    /// Compute an exact canonical wire partition without evaluating the file.
    pub fn procedural_breakdown(&self, limits: &Limits) -> Result<ProgramBreakdown> {
        let validation = self.validate(limits)?;
        let sections = self.encode_sections_after_validation(limits)?;
        let mut breakdown = ProgramBreakdown {
            shared_definition_bytes: sections.definitions.len() as u64,
            node_count: validation.node_count,
            ..ProgramBreakdown::default()
        };
        let root_bytes = account_node_wire(&self.root, limits, &mut breakdown)?;
        if root_bytes != sections.root.len() as u64 {
            return Err(Error::InvalidValue(
                "root wire accounting differs from canonical encoding",
            ));
        }

        let mut reference_counts = vec![0u64; self.definitions.len()];
        for definition in &self.definitions {
            count_references(definition, &mut reference_counts)?;
        }
        count_references(&self.root, &mut reference_counts)?;
        breakdown.shared_node_count =
            reference_counts.iter().filter(|count| **count > 1).count() as u64;

        for definition in &self.definitions {
            collect_residual_metrics(definition, false, &mut breakdown)?;
        }
        collect_residual_metrics(&self.root, false, &mut breakdown)?;

        let mut definition_attribution = Vec::with_capacity(self.definitions.len());
        for definition in &self.definitions {
            definition_attribution.push(reconstruction_attribution(
                definition,
                &definition_attribution,
                limits,
            )?);
        }
        let reconstructed =
            reconstruction_attribution(&self.root, &definition_attribution, limits)?;
        if reconstructed.output_bytes != validation.original_bytes
            || checked_u64_add(
                reconstructed.function_bytes,
                reconstructed.literal_bytes,
                "reconstruction attribution",
            )? != validation.original_bytes
        {
            return Err(Error::InvalidValue(
                "reconstruction attribution differs from validated output",
            ));
        }
        breakdown.function_reconstructed_bytes = reconstructed.function_bytes;
        breakdown.literal_reconstructed_bytes = reconstructed.literal_bytes;

        if breakdown.wire_bytes()? != sections.dsl_bytes()? {
            return Err(Error::InvalidValue(
                "program wire categories do not partition canonical DSL bytes",
            ));
        }
        Ok(breakdown)
    }

    /// Clone this program while replacing every native entropy leaf with its
    /// strict decoded raw literal. This is the exact pre-entropy
    /// counterfactual used by archive reporting.
    pub fn without_entropy_literals(&self, limits: &Limits) -> Result<Self> {
        let mut program = self.clone();
        for definition in &mut program.definitions {
            expand_entropy_literals(definition, limits)?;
        }
        expand_entropy_literals(&mut program.root, limits)?;
        program.validate(limits)?;
        Ok(program)
    }

    /// Parse one block's already-bounded definition and root sections while
    /// retaining the validation report used by the container directory.
    pub fn decode_sections_with_report(
        definition_count: u32,
        definition_bytes: &[u8],
        root_bytes: &[u8],
        limits: &Limits,
    ) -> Result<(Self, ValidationReport)> {
        limits.check(
            "definitions",
            u64::from(definition_count),
            limits.max_definitions,
        )?;
        let section_bytes = checked_u64_add(
            definition_bytes.len() as u64,
            root_bytes.len() as u64,
            "encoded DSL sections",
        )?;
        limits.check("archive bytes", section_bytes, limits.max_archive_bytes)?;
        limits.check(
            "block payload bytes",
            checked_u64_add(V1_BLOCK_HEADER_BYTES, section_bytes, "block payload bytes")?,
            limits.max_block_payload_bytes,
        )?;

        let mut parse_state = ParseState::default();
        let mut definition_cursor = Cursor::new(definition_bytes);
        let mut definitions = Vec::with_capacity(definition_count as usize);
        for expected_id in 0..definition_count {
            let id = definition_cursor.read_u64()?;
            if id != u64::from(expected_id) {
                return Err(Error::InvalidValue(
                    "definition IDs must be dense and increasing",
                ));
            }
            let record_length = definition_cursor.read_usize("definition record length")?;
            let mut record = definition_cursor.subcursor(record_length, "definition record")?;
            let definition = decode_node(&mut record, limits, &mut parse_state, 1)?;
            record.finish("definition record")?;
            definitions.push(definition);
        }
        definition_cursor.finish("definition section")?;

        let mut root_cursor = Cursor::new(root_bytes);
        let root = decode_node(&mut root_cursor, limits, &mut parse_state, 1)?;
        root_cursor.finish("root section")?;
        let program = Self { definitions, root };
        let report = program.validate(limits)?;

        // The parser and semantic validator count the same serialized graph.
        // Keeping this comparison explicit protects future opcode additions.
        if parse_state.nodes != report.node_count
            || parse_state.edges != report.edge_count
            || parse_state.dependencies != report.dependency_count
        {
            return Err(Error::InvalidValue(
                "parser and validator graph accounting disagree",
            ));
        }
        let canonical = program.encode_sections_after_validation(limits)?;
        if canonical.definitions != definition_bytes || canonical.root != root_bytes {
            return Err(Error::InvalidValue(
                "decoded DSL sections are not canonical",
            ));
        }
        Ok((program, report))
    }

    /// Parse one block's already-bounded definition and root sections.
    pub fn decode_sections(
        definition_count: u32,
        definition_bytes: &[u8],
        root_bytes: &[u8],
        limits: &Limits,
    ) -> Result<Self> {
        Self::decode_sections_with_report(definition_count, definition_bytes, root_bytes, limits)
            .map(|(program, _)| program)
    }

    /// Construct the normative candidate comparator key after the caller has
    /// serialized the complete container and supplied its actual length and
    /// workspace.  The full DSL payload is the total-order fallback.
    pub fn candidate_cost(
        &self,
        complete_archive_bytes: u64,
        decode_memory: u64,
        limits: &Limits,
    ) -> Result<CandidateCost> {
        let report = self.validate(limits)?;
        let sections = self.encode_sections_after_validation(limits)?;
        let mut canonical_payload = sections.definitions;
        canonical_payload.extend_from_slice(&sections.root);
        Ok(CandidateCost {
            archive_bytes: complete_archive_bytes,
            decode_work: report.decode_work,
            decode_memory,
            node_count: report.node_count,
            dependency_count: report.dependency_count,
            opcode_sequence: report.opcode_sequence,
            parameter_payload: extract_parameter_payload(self),
            canonical_payload,
        })
    }
}

fn encoded_node_bytes(node: &Node, limits: &Limits) -> Result<u64> {
    let mut encoded = Writer::new();
    encode_node(node, limits, &mut encoded)?;
    Ok(encoded.len() as u64)
}

fn add_wire_child(total: &mut u64, child: u64) -> Result<()> {
    *total = checked_u64_add(*total, child, "encoded child node bytes")?;
    Ok(())
}

fn account_node_wire(
    node: &Node,
    limits: &Limits,
    breakdown: &mut ProgramBreakdown,
) -> Result<u64> {
    let total = encoded_node_bytes(node, limits)?;
    let mut child_bytes = 0u64;
    match node {
        Node::File { child, .. }
        | Node::Group { child, .. }
        | Node::Coordinate { child, .. }
        | Node::Exceptions { base: child, .. } => {
            add_wire_child(
                &mut child_bytes,
                account_node_wire(child, limits, breakdown)?,
            )?;
        }
        Node::Concat(children) | Node::Split { children, .. } => {
            for child in children {
                add_wire_child(
                    &mut child_bytes,
                    account_node_wire(child, limits, breakdown)?,
                )?;
            }
        }
        Node::Correct {
            prediction,
            correction,
            ..
        } => {
            add_wire_child(
                &mut child_bytes,
                account_node_wire(prediction, limits, breakdown)?,
            )?;
            add_wire_child(
                &mut child_bytes,
                account_node_wire(correction, limits, breakdown)?,
            )?;
        }
        Node::Literal(_)
        | Node::EntropyLiteral(_)
        | Node::Const { .. }
        | Node::Linear { .. }
        | Node::Periodic { .. }
        | Node::Recurrence(_)
        | Node::Reference { .. } => {}
    }
    let own = total
        .checked_sub(child_bytes)
        .ok_or(Error::IntegerOverflow {
            context: "node own wire bytes",
        })?;

    match node {
        Node::Literal(_) => {
            breakdown.literal_leaf_bytes =
                checked_u64_add(breakdown.literal_leaf_bytes, own, "literal leaf wire bytes")?;
        }
        Node::EntropyLiteral(envelope) => {
            let metadata = inspect_leaf(envelope, limits)?;
            if metadata.payload_bytes > own {
                return Err(Error::InvalidValue(
                    "entropy payload exceeds encoded node record",
                ));
            }
            breakdown.literal_leaf_bytes = checked_u64_add(
                breakdown.literal_leaf_bytes,
                metadata.payload_bytes,
                "entropy coded literal payload bytes",
            )?;
            breakdown.entropy_metadata_bytes = checked_u64_add(
                breakdown.entropy_metadata_bytes,
                own - metadata.payload_bytes,
                "entropy metadata wire bytes",
            )?;
        }
        Node::Coordinate { .. } => {
            breakdown.coordinate_bytes =
                checked_u64_add(breakdown.coordinate_bytes, own, "coordinate wire bytes")?;
        }
        Node::Correct { .. } => {
            breakdown.residual_layer_bytes = checked_u64_add(
                breakdown.residual_layer_bytes,
                own,
                "residual layer wire bytes",
            )?;
        }
        Node::Reference {
            parameter_delta, ..
        } => {
            let parameter_bytes = parameter_delta.iter().try_fold(
                encoded_len(parameter_delta.len() as u64) as u64,
                |sum, delta| {
                    checked_u64_add(
                        sum,
                        encoded_len(zigzag_encode(*delta)) as u64,
                        "reference parameter wire bytes",
                    )
                },
            )?;
            if parameter_bytes > own {
                return Err(Error::InvalidValue(
                    "reference parameters exceed encoded node record",
                ));
            }
            breakdown.parameter_bytes = checked_u64_add(
                breakdown.parameter_bytes,
                parameter_bytes,
                "parameter wire bytes",
            )?;
            breakdown.reference_bytes = checked_u64_add(
                breakdown.reference_bytes,
                own - parameter_bytes,
                "reference wire bytes",
            )?;
        }
        Node::Exceptions { exceptions, .. } => {
            let mut parameter_bytes = encoded_len(exceptions.len() as u64) as u64;
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
                parameter_bytes = checked_u64_add(
                    parameter_bytes,
                    checked_u64_add(encoded_len(delta) as u64, 1, "exception value wire byte")?,
                    "exception parameter wire bytes",
                )?;
                previous = exception.position;
            }
            if parameter_bytes > own {
                return Err(Error::InvalidValue(
                    "exception parameters exceed encoded node record",
                ));
            }
            breakdown.parameter_bytes = checked_u64_add(
                breakdown.parameter_bytes,
                parameter_bytes,
                "parameter wire bytes",
            )?;
            breakdown.function_graph_bytes = checked_u64_add(
                breakdown.function_graph_bytes,
                own - parameter_bytes,
                "function graph wire bytes",
            )?;
        }
        Node::Const { .. }
        | Node::Linear { .. }
        | Node::Periodic { .. }
        | Node::Recurrence(_)
        | Node::File { .. }
        | Node::Concat(_)
        | Node::Split { .. }
        | Node::Group { .. } => {
            breakdown.function_graph_bytes = checked_u64_add(
                breakdown.function_graph_bytes,
                own,
                "function graph wire bytes",
            )?;
        }
    }
    Ok(total)
}

fn count_references(node: &Node, counts: &mut [u64]) -> Result<()> {
    match node {
        Node::Reference { definition, .. } => {
            let available_definitions = counts.len();
            let count = counts
                .get_mut(*definition as usize)
                .ok_or(Error::InvalidReference {
                    id: u64::from(*definition),
                    available_definitions,
                })?;
            *count = checked_u64_add(*count, 1, "definition reference count")?;
        }
        Node::File { child, .. }
        | Node::Group { child, .. }
        | Node::Coordinate { child, .. }
        | Node::Exceptions { base: child, .. } => count_references(child, counts)?,
        Node::Concat(children) | Node::Split { children, .. } => {
            for child in children {
                count_references(child, counts)?;
            }
        }
        Node::Correct {
            prediction,
            correction,
            ..
        } => {
            count_references(prediction, counts)?;
            count_references(correction, counts)?;
        }
        Node::Literal(_)
        | Node::EntropyLiteral(_)
        | Node::Const { .. }
        | Node::Linear { .. }
        | Node::Periodic { .. }
        | Node::Recurrence(_) => {}
    }
    Ok(())
}

fn maximum_residual_depth(node: &Node, depth: u64) -> Result<u64> {
    let depth = if matches!(node, Node::Correct { .. }) {
        checked_u64_add(depth, 1, "residual nesting depth")?
    } else {
        depth
    };
    let mut maximum = depth;
    let mut visit = |child: &Node| -> Result<()> {
        maximum = maximum.max(maximum_residual_depth(child, depth)?);
        Ok(())
    };
    match node {
        Node::File { child, .. }
        | Node::Group { child, .. }
        | Node::Coordinate { child, .. }
        | Node::Exceptions { base: child, .. } => visit(child)?,
        Node::Concat(children) | Node::Split { children, .. } => {
            for child in children {
                visit(child)?;
            }
        }
        Node::Correct {
            prediction,
            correction,
            ..
        } => {
            visit(prediction)?;
            visit(correction)?;
        }
        Node::Literal(_)
        | Node::EntropyLiteral(_)
        | Node::Const { .. }
        | Node::Linear { .. }
        | Node::Periodic { .. }
        | Node::Recurrence(_)
        | Node::Reference { .. } => {}
    }
    Ok(maximum)
}

fn collect_residual_metrics(
    node: &Node,
    inside_residual: bool,
    breakdown: &mut ProgramBreakdown,
) -> Result<()> {
    let is_residual = matches!(node, Node::Correct { .. });
    if is_residual && !inside_residual {
        breakdown.residual_root_count =
            checked_u64_add(breakdown.residual_root_count, 1, "residual root count")?;
        breakdown.residual_depth_sum = checked_u64_add(
            breakdown.residual_depth_sum,
            maximum_residual_depth(node, 0)?,
            "residual depth sum",
        )?;
    }
    let nested = inside_residual || is_residual;
    match node {
        Node::File { child, .. }
        | Node::Group { child, .. }
        | Node::Coordinate { child, .. }
        | Node::Exceptions { base: child, .. } => {
            collect_residual_metrics(child, nested, breakdown)?
        }
        Node::Concat(children) | Node::Split { children, .. } => {
            for child in children {
                collect_residual_metrics(child, nested, breakdown)?;
            }
        }
        Node::Correct {
            prediction,
            correction,
            ..
        } => {
            collect_residual_metrics(prediction, nested, breakdown)?;
            collect_residual_metrics(correction, nested, breakdown)?;
        }
        Node::Literal(_)
        | Node::EntropyLiteral(_)
        | Node::Const { .. }
        | Node::Linear { .. }
        | Node::Periodic { .. }
        | Node::Recurrence(_)
        | Node::Reference { .. } => {}
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Default)]
struct ReconstructionAttribution {
    output_bytes: u64,
    function_bytes: u64,
    literal_bytes: u64,
}

fn generator_bytes(count: u64, width: u8) -> Result<u64> {
    let bits = checked_u64_mul(count, u64::from(width), "generator attributed bits")?;
    if bits % 8 != 0 {
        return Err(Error::InvalidValue(
            "generator attributed output is not byte aligned",
        ));
    }
    Ok(bits / 8)
}

fn concatenate_attribution(
    children: &[Node],
    definitions: &[ReconstructionAttribution],
    limits: &Limits,
) -> Result<ReconstructionAttribution> {
    let mut combined = ReconstructionAttribution::default();
    for child in children {
        let child = reconstruction_attribution(child, definitions, limits)?;
        combined.output_bytes = checked_u64_add(
            combined.output_bytes,
            child.output_bytes,
            "concatenated attributed output",
        )?;
        combined.function_bytes = checked_u64_add(
            combined.function_bytes,
            child.function_bytes,
            "concatenated function output",
        )?;
        combined.literal_bytes = checked_u64_add(
            combined.literal_bytes,
            child.literal_bytes,
            "concatenated literal output",
        )?;
    }
    Ok(combined)
}

fn reconstruction_attribution(
    node: &Node,
    definitions: &[ReconstructionAttribution],
    limits: &Limits,
) -> Result<ReconstructionAttribution> {
    let function = |output_bytes| ReconstructionAttribution {
        output_bytes,
        function_bytes: output_bytes,
        literal_bytes: 0,
    };
    let literal = |output_bytes| ReconstructionAttribution {
        output_bytes,
        function_bytes: 0,
        literal_bytes: output_bytes,
    };
    match node {
        Node::Literal(bytes) => Ok(literal(bytes.len() as u64)),
        Node::EntropyLiteral(envelope) => {
            Ok(literal(inspect_leaf(envelope, limits)?.decoded_bytes))
        }
        Node::Const { length, .. } => Ok(function(*length)),
        Node::Linear { count, width, .. } => Ok(function(generator_bytes(*count, *width)?)),
        Node::Periodic {
            pattern,
            repetitions,
            suffix,
        } => Ok(function(checked_u64_add(
            checked_u64_mul(
                pattern.len() as u64,
                *repetitions,
                "periodic attributed output",
            )?,
            suffix.len() as u64,
            "periodic attributed output",
        )?)),
        Node::Recurrence(recurrence) => Ok(function(generator_bytes(
            recurrence.count,
            recurrence.width,
        )?)),
        Node::File {
            original_length,
            child,
        } => {
            let attribution = reconstruction_attribution(child, definitions, limits)?;
            if attribution.output_bytes != *original_length {
                return Err(Error::LengthMismatch {
                    context: "attributed FILE child",
                    expected: *original_length,
                    actual: attribution.output_bytes,
                });
            }
            Ok(attribution)
        }
        Node::Concat(children) | Node::Split { children, .. } => {
            concatenate_attribution(children, definitions, limits)
        }
        Node::Group { child, .. } | Node::Exceptions { base: child, .. } => {
            reconstruction_attribution(child, definitions, limits)
        }
        Node::Coordinate { descriptor, child } => {
            let child = reconstruction_attribution(child, definitions, limits)?;
            if child.literal_bytes == 0 {
                Ok(function(descriptor.original_bytes))
            } else {
                Ok(literal(descriptor.original_bytes))
            }
        }
        Node::Correct {
            prediction,
            correction,
            ..
        } => {
            let prediction = reconstruction_attribution(prediction, definitions, limits)?;
            let correction = reconstruction_attribution(correction, definitions, limits)?;
            if prediction.output_bytes != correction.output_bytes {
                return Err(Error::LengthMismatch {
                    context: "attributed correction operands",
                    expected: prediction.output_bytes,
                    actual: correction.output_bytes,
                });
            }
            if prediction.literal_bytes == 0 && correction.literal_bytes == 0 {
                Ok(function(prediction.output_bytes))
            } else {
                Ok(literal(prediction.output_bytes))
            }
        }
        Node::Reference { definition, .. } => {
            definitions
                .get(*definition as usize)
                .copied()
                .ok_or(Error::InvalidReference {
                    id: u64::from(*definition),
                    available_definitions: definitions.len(),
                })
        }
    }
}

fn expand_entropy_literals(node: &mut Node, limits: &Limits) -> Result<()> {
    match node {
        Node::EntropyLiteral(envelope) => {
            *node = Node::Literal(decode_leaf(envelope, limits)?);
        }
        Node::File { child, .. }
        | Node::Group { child, .. }
        | Node::Coordinate { child, .. }
        | Node::Exceptions { base: child, .. } => expand_entropy_literals(child, limits)?,
        Node::Concat(children) | Node::Split { children, .. } => {
            for child in children {
                expand_entropy_literals(child, limits)?;
            }
        }
        Node::Correct {
            prediction,
            correction,
            ..
        } => {
            expand_entropy_literals(prediction, limits)?;
            expand_entropy_literals(correction, limits)?;
        }
        Node::Literal(_)
        | Node::Const { .. }
        | Node::Linear { .. }
        | Node::Periodic { .. }
        | Node::Recurrence(_)
        | Node::Reference { .. } => {}
    }
    Ok(())
}

fn analyze_node(
    node: &Node,
    definitions: &[Analysis],
    allow_file: bool,
    syntax_depth: u64,
    limits: &Limits,
) -> Result<Analysis> {
    limits.check("graph depth", syntax_depth, limits.max_graph_depth)?;
    validate_generator_parameters(node, limits)?;
    let child_depth = checked_u64_add(syntax_depth, 1, "graph depth")?;

    let mut analysis = match node {
        Node::Literal(bytes) => leaf(ValueType::Bytes(bytes.len() as u64), bytes.len() as u64)?,
        Node::EntropyLiteral(envelope) => {
            let metadata = inspect_leaf(envelope, limits)?;
            if metadata.temporary_bytes != 0 {
                return Err(Error::InvalidValue(
                    "native entropy leaf declared unsupported scratch",
                ));
            }
            leaf(
                ValueType::Bytes(metadata.decoded_bytes),
                metadata.decode_work,
            )?
        }
        Node::Const { length, .. } => leaf(ValueType::Bytes(*length), *length)?,
        Node::Linear { count, width, .. } => {
            let output_bytes = generator_output_bytes(*count, *width, "LINEAR output")?;
            let work = checked_u64_mul(*count, 3, "LINEAR work")?;
            leaf(generator_type(*count, *width)?, work)?.with_output_bytes(output_bytes)?
        }
        Node::Periodic {
            pattern,
            repetitions,
            suffix,
        } => {
            let repeated = checked_u64_mul(
                pattern.len() as u64,
                *repetitions,
                "PERIODIC repeated bytes",
            )?;
            let length = checked_u64_add(repeated, suffix.len() as u64, "PERIODIC output")?;
            leaf(ValueType::Bytes(length), length)?
        }
        Node::Recurrence(recurrence) => {
            let output_bytes =
                generator_output_bytes(recurrence.count, recurrence.width, "RECURRENCE output")?;
            let order = recurrence.coefficients.len() as u64;
            let generated_steps = recurrence.count - order;
            let feedback_work = checked_u64_mul(
                checked_u64_mul(generated_steps, order, "RECURRENCE work")?,
                2,
                "RECURRENCE work",
            )?;
            let work = checked_u64_add(recurrence.count, feedback_work, "RECURRENCE work")?;
            leaf(generator_type(recurrence.count, recurrence.width)?, work)?
                .with_output_bytes(output_bytes)?
        }
        Node::File {
            original_length,
            child,
        } => {
            if !allow_file {
                return Err(Error::InvalidValue("FILE is valid only as block root"));
            }
            let child = analyze_node(child, definitions, false, child_depth, limits)?;
            match child.value_type {
                ValueType::Bytes(actual) if actual == *original_length => {}
                ValueType::Bytes(actual) => {
                    return Err(Error::LengthMismatch {
                        context: "FILE child",
                        expected: *original_length,
                        actual,
                    })
                }
                _ => {
                    return Err(Error::TypeMismatch {
                        context: "FILE child",
                    })
                }
            }
            alias_parent(Opcode::File, child, 1)?
        }
        Node::Concat(children) => {
            limits.check("fan-out", children.len() as u64, limits.max_fan_out)?;
            let analyzed = analyze_children(children, definitions, child_depth, limits)?;
            let types: Vec<_> = analyzed
                .iter()
                .map(|child| child.value_type.clone())
                .collect();
            let value_type = ValueType::concatenate(&types)?;
            streaming_parent(
                Opcode::Concat,
                value_type,
                analyzed,
                checked_u64_add(1, children.len() as u64, "CONCAT work")?,
            )?
        }
        Node::Split {
            boundaries,
            children,
        } => {
            limits.check("fan-out", children.len() as u64, limits.max_fan_out)?;
            if children.is_empty() || boundaries.len() + 1 != children.len() {
                return Err(Error::InvalidValue(
                    "SPLIT requires one fewer boundary than children",
                ));
            }
            let analyzed = analyze_children(children, definitions, child_depth, limits)?;
            let mut cumulative = 0u64;
            for (index, child) in analyzed.iter().enumerate() {
                let ValueType::Bytes(length) = child.value_type else {
                    return Err(Error::TypeMismatch {
                        context: "SPLIT children",
                    });
                };
                if length == 0 {
                    return Err(Error::InvalidValue("SPLIT intervals must be non-empty"));
                }
                cumulative = checked_u64_add(cumulative, length, "SPLIT boundary")?;
                if index < boundaries.len() && boundaries[index] != cumulative {
                    return Err(Error::LengthMismatch {
                        context: "SPLIT boundary",
                        expected: cumulative,
                        actual: boundaries[index],
                    });
                }
            }
            streaming_parent(
                Opcode::Split,
                ValueType::Bytes(cumulative),
                analyzed,
                checked_u64_add(1, children.len() as u64, "SPLIT work")?,
            )?
        }
        Node::Group { child, .. } => {
            let child = analyze_node(child, definitions, false, child_depth, limits)?;
            alias_parent(Opcode::Group, child, 1)?
        }
        Node::Coordinate { descriptor, child } => {
            if descriptor.transform == CoordinateTransform::Identity {
                return Err(Error::InvalidValue(
                    "identity coordinate must use GROUP compatibility node",
                ));
            }
            descriptor.validate(limits)?;
            let child = analyze_node(child, definitions, false, child_depth, limits)?;
            let expected = descriptor.transformed_bytes()?;
            match child.value_type {
                ValueType::Bytes(actual) if actual == expected => {}
                ValueType::Bytes(actual) => {
                    return Err(Error::LengthMismatch {
                        context: "coordinate transformed child",
                        expected,
                        actual,
                    });
                }
                _ => {
                    return Err(Error::TypeMismatch {
                        context: "coordinate transformed child",
                    });
                }
            }
            coordinate_parent(
                node.opcode(),
                ValueType::Bytes(descriptor.original_bytes),
                child,
                descriptor.inverse_work()?,
                expected,
            )?
        }
        Node::Correct {
            kind,
            prediction,
            correction,
        } => {
            let prediction = analyze_node(prediction, definitions, false, child_depth, limits)?;
            let correction = analyze_node(correction, definitions, false, child_depth, limits)?;
            if prediction.value_type != correction.value_type {
                return Err(Error::TypeMismatch {
                    context: "correction operands",
                });
            }
            match kind {
                CorrectionKind::Add | CorrectionKind::Subtract => {
                    if !matches!(
                        prediction.value_type,
                        ValueType::Bytes(_) | ValueType::Words { .. }
                    ) {
                        return Err(Error::TypeMismatch {
                            context: "ADD/SUB correction operands",
                        });
                    }
                }
                CorrectionKind::Xor => {
                    if matches!(prediction.value_type, ValueType::Mask(_)) {
                        return Err(Error::TypeMismatch {
                            context: "XOR correction operands",
                        });
                    }
                }
            }
            let value_type = prediction.value_type.clone();
            let output = value_type.byte_len()?;
            let work = checked_u64_add(1, output, "correction work")?;
            correction_parent(kind.opcode(), value_type, prediction, correction, work)?
        }
        Node::Exceptions { base, exceptions } => {
            let base = analyze_node(base, definitions, false, child_depth, limits)?;
            let ValueType::Bytes(length) = base.value_type else {
                return Err(Error::TypeMismatch {
                    context: "EXCEPTIONS base",
                });
            };
            let mut previous = None;
            let mut parameter_bytes = encoded_len(exceptions.len() as u64) as u64;
            for exception in exceptions {
                if exception.position >= length {
                    return Err(Error::InvalidValue("exception position is outside base"));
                }
                if previous.is_some_and(|value| exception.position <= value) {
                    return Err(Error::InvalidValue(
                        "exception positions must be sorted and unique",
                    ));
                }
                let delta = previous
                    .map(|value| exception.position - value)
                    .unwrap_or(exception.position);
                parameter_bytes = checked_u64_add(
                    parameter_bytes,
                    encoded_len(delta) as u64 + 1,
                    "exception parameters",
                )?;
                previous = Some(exception.position);
            }
            limits.check(
                "parameter bytes",
                parameter_bytes,
                limits.max_parameter_bytes,
            )?;
            let work = checked_u64_add(1, exceptions.len() as u64, "EXCEPTIONS work")?;
            alias_parent(Opcode::Exceptions, base, work)?
        }
        Node::Reference {
            definition,
            parameter_delta,
        } => {
            let id = *definition as usize;
            let referenced = definitions.get(id).ok_or(Error::InvalidReference {
                id: u64::from(*definition),
                available_definitions: definitions.len(),
            })?;

            let mut parameter_bytes = encoded_len(parameter_delta.len() as u64) as u64;
            for delta in parameter_delta {
                let magnitude = delta.unsigned_abs();
                limits.check(
                    "parameter delta magnitude",
                    magnitude,
                    limits.max_parameter_delta_abs,
                )?;
                parameter_bytes = checked_u64_add(
                    parameter_bytes,
                    encoded_len(mathsvg_core::zigzag_encode(*delta)) as u64,
                    "reference parameter bytes",
                )?;
            }
            limits.check(
                "parameter bytes",
                parameter_bytes,
                limits.max_parameter_bytes,
            )?;
            if !parameter_delta.is_empty() {
                return Err(Error::InvalidValue(
                    "parameterized references are reserved but not implemented in MVP",
                ));
            }

            Analysis {
                value_type: referenced.value_type.clone(),
                nodes: 1,
                edges: 1,
                dependencies: 1,
                depth: checked_u64_add(1, referenced.depth, "reference graph depth")?,
                work: checked_u64_add(1, referenced.work, "reference work")?,
                generated: referenced.generated,
                temporary: referenced.temporary,
                opcodes: vec![Opcode::Reference.byte()],
            }
        }
    };

    if !matches!(node, Node::File { .. }) && allow_file {
        return Err(Error::InvalidValue("block root must be FILE"));
    }
    if analysis.opcodes.is_empty() {
        analysis.opcodes.push(node.opcode().byte());
    }
    limits.check(
        "output bytes",
        analysis.output_bytes()?,
        limits.max_output_bytes,
    )?;
    limits.check(
        "block output bytes",
        analysis.output_bytes()?,
        limits.max_block_output_bytes,
    )?;
    check_analysis_limits(&analysis, limits)?;
    Ok(analysis)
}

fn analyze_children(
    children: &[Node],
    definitions: &[Analysis],
    depth: u64,
    limits: &Limits,
) -> Result<Vec<Analysis>> {
    children
        .iter()
        .map(|child| analyze_node(child, definitions, false, depth, limits))
        .collect()
}

fn generator_type(count: u64, width: u8) -> Result<ValueType> {
    generator_output_bytes(count, width, "generator output")?;
    // Eight-bit generators directly produce the source byte domain.  Wider
    // generators preserve explicit word typing and little-endian layout.
    if width == 8 {
        Ok(ValueType::Bytes(count))
    } else {
        Ok(ValueType::Words {
            width,
            count,
            endian: Endian::Little,
        })
    }
}

fn leaf(value_type: ValueType, work_without_base: u64) -> Result<Analysis> {
    let output = value_type.byte_len()?;
    Ok(Analysis {
        value_type,
        nodes: 1,
        edges: 0,
        dependencies: 0,
        depth: 1,
        work: checked_u64_add(1, work_without_base, "leaf work")?,
        generated: output,
        // The block result destination is not DSL scratch workspace.
        temporary: 0,
        opcodes: Vec::new(),
    })
}

trait WithOutputBytes {
    fn with_output_bytes(self, expected: u64) -> Result<Self>
    where
        Self: Sized;
}

impl WithOutputBytes for Analysis {
    fn with_output_bytes(self, expected: u64) -> Result<Self> {
        let actual = self.value_type.byte_len()?;
        if actual == expected {
            Ok(self)
        } else {
            Err(Error::LengthMismatch {
                context: "generator output bytes",
                expected,
                actual,
            })
        }
    }
}

fn alias_parent(opcode: Opcode, child: Analysis, own_work: u64) -> Result<Analysis> {
    let mut opcodes = vec![opcode.byte()];
    opcodes.extend_from_slice(&child.opcodes);
    Ok(Analysis {
        value_type: child.value_type,
        nodes: checked_u64_add(1, child.nodes, "node count")?,
        edges: checked_u64_add(1, child.edges, "edge count")?,
        dependencies: child.dependencies,
        depth: checked_u64_add(1, child.depth, "graph depth")?,
        work: checked_u64_add(own_work, child.work, "decode work")?,
        generated: child.generated,
        temporary: child.temporary,
        opcodes,
    })
}

fn coordinate_parent(
    opcode: Opcode,
    value_type: ValueType,
    child: Analysis,
    inverse_work: u64,
    transformed_bytes: u64,
) -> Result<Analysis> {
    let mut opcodes = vec![opcode.byte()];
    opcodes.extend_from_slice(&child.opcodes);
    Ok(Analysis {
        value_type,
        nodes: checked_u64_add(1, child.nodes, "node count")?,
        edges: checked_u64_add(1, child.edges, "edge count")?,
        dependencies: child.dependencies,
        depth: checked_u64_add(1, child.depth, "graph depth")?,
        work: checked_u64_add(inverse_work, child.work, "coordinate decode work")?,
        generated: child.generated,
        // The child is generated into its complete transformed-domain buffer.
        // Its own scratch remains live inside that buffer's lifetime.
        temporary: checked_u64_add(transformed_bytes, child.temporary, "coordinate scratch")?,
        opcodes,
    })
}

fn streaming_parent(
    opcode: Opcode,
    value_type: ValueType,
    children: Vec<Analysis>,
    own_work: u64,
) -> Result<Analysis> {
    let mut nodes = 1u64;
    let mut edges = children.len() as u64;
    let mut dependencies = 0u64;
    let mut depth = 1u64;
    let mut work = own_work;
    let mut generated = 0u64;
    let mut child_peak = 0u64;
    let mut opcodes = vec![opcode.byte()];

    for child in children {
        nodes = checked_u64_add(nodes, child.nodes, "node count")?;
        edges = checked_u64_add(edges, child.edges, "edge count")?;
        dependencies = checked_u64_add(dependencies, child.dependencies, "dependency count")?;
        depth = depth.max(checked_u64_add(1, child.depth, "graph depth")?);
        work = checked_u64_add(work, child.work, "decode work")?;
        generated = checked_u64_add(generated, child.generated, "generated bytes")?;
        child_peak = child_peak.max(child.temporary);
        opcodes.extend_from_slice(&child.opcodes);
    }

    Ok(Analysis {
        value_type,
        nodes,
        edges,
        dependencies,
        depth,
        work,
        generated,
        // CONCAT/SPLIT write each child directly into its disjoint destination
        // range and therefore need only the largest child scratch bound.
        temporary: child_peak,
        opcodes,
    })
}

fn correction_parent(
    opcode: Opcode,
    value_type: ValueType,
    prediction: Analysis,
    correction: Analysis,
    own_work: u64,
) -> Result<Analysis> {
    let output = value_type.byte_len()?;
    let mut opcodes = vec![opcode.byte()];
    opcodes.extend_from_slice(&prediction.opcodes);
    opcodes.extend_from_slice(&correction.opcodes);
    Ok(Analysis {
        value_type,
        nodes: checked_u64_add(
            1,
            checked_u64_add(prediction.nodes, correction.nodes, "node count")?,
            "node count",
        )?,
        edges: checked_u64_add(
            2,
            checked_u64_add(prediction.edges, correction.edges, "edge count")?,
            "edge count",
        )?,
        dependencies: checked_u64_add(
            prediction.dependencies,
            correction.dependencies,
            "dependency count",
        )?,
        depth: checked_u64_add(1, prediction.depth.max(correction.depth), "graph depth")?,
        work: checked_u64_add(
            own_work,
            checked_u64_add(prediction.work, correction.work, "decode work")?,
            "decode work",
        )?,
        generated: checked_u64_add(
            prediction.generated,
            correction.generated,
            "generated bytes",
        )?,
        // Prediction writes into the destination.  The recursively generated
        // correction occupies one output-sized scratch vector and may require
        // its own additional scratch while being produced.
        temporary: prediction.temporary.max(checked_u64_add(
            output,
            correction.temporary,
            "correction scratch",
        )?),
        opcodes,
    })
}

fn check_analysis_limits(analysis: &Analysis, limits: &Limits) -> Result<()> {
    limits.check("nodes", analysis.nodes, limits.max_nodes)?;
    limits.check("edges", analysis.edges, limits.max_edges)?;
    limits.check("graph depth", analysis.depth, limits.max_graph_depth)?;
    limits.check("work", analysis.work, limits.max_work)?;
    limits.check(
        "generated bytes",
        analysis.generated,
        limits.max_generated_bytes,
    )?;
    limits.check(
        "temporary bytes",
        analysis.temporary,
        limits.max_temporary_bytes,
    )
}

fn extract_parameter_payload(program: &Program) -> Vec<u8> {
    let mut output = Writer::new();
    for definition in &program.definitions {
        extract_node_parameters(definition, &mut output);
    }
    extract_node_parameters(&program.root, &mut output);
    output.into_inner()
}

fn extract_node_parameters(node: &Node, output: &mut Writer) {
    output.write_u8(node.opcode().byte());
    match node {
        Node::Literal(bytes) => output.write_bytes(bytes),
        Node::EntropyLiteral(envelope) => output.write_bytes(envelope),
        Node::Const { length, value } => {
            output.write_u64(*length);
            output.write_u8(*value);
        }
        Node::Linear {
            count,
            width,
            modulus,
            a,
            b,
        } => {
            output.write_u64(*count);
            output.write_u8(*width);
            output.write_u64(*modulus);
            output.write_u64(*a);
            output.write_u64(*b);
        }
        Node::Periodic {
            pattern,
            repetitions,
            suffix,
        } => {
            output.write_bytes(pattern);
            output.write_u64(*repetitions);
            output.write_bytes(suffix);
        }
        Node::Recurrence(value) => {
            output.write_u64(value.count);
            output.write_u8(value.width);
            output.write_u64(value.modulus);
            for coefficient in &value.coefficients {
                output.write_u64(*coefficient);
            }
            for initial in &value.initial_state {
                output.write_u64(*initial);
            }
        }
        Node::File {
            original_length,
            child,
        } => {
            output.write_u64(*original_length);
            extract_node_parameters(child, output);
        }
        Node::Concat(children) => {
            for child in children {
                extract_node_parameters(child, output);
            }
        }
        Node::Split {
            boundaries,
            children,
        } => {
            for boundary in boundaries {
                output.write_u64(*boundary);
            }
            for child in children {
                extract_node_parameters(child, output);
            }
        }
        Node::Group { child, .. } => extract_node_parameters(child, output),
        Node::Coordinate { descriptor, child } => {
            output.write_u64(descriptor.original_bytes);
            match descriptor.transform {
                CoordinateTransform::Identity | CoordinateTransform::BitPlane => {}
                CoordinateTransform::Stride {
                    element_width,
                    channels,
                } => {
                    output.write_u64(u64::from(element_width));
                    output.write_u64(u64::from(channels));
                }
                CoordinateTransform::BytePlane { word_width, endian } => {
                    output.write_u64(u64::from(word_width));
                    output.write_u8(endian as u8);
                }
            }
            extract_node_parameters(child, output);
        }
        Node::Correct {
            prediction,
            correction,
            ..
        } => {
            extract_node_parameters(prediction, output);
            extract_node_parameters(correction, output);
        }
        Node::Exceptions { base, exceptions } => {
            extract_node_parameters(base, output);
            for exception in exceptions {
                output.write_u64(exception.position);
                output.write_u8(exception.value);
            }
        }
        Node::Reference {
            definition,
            parameter_delta,
        } => {
            output.write_u64(u64::from(*definition));
            for delta in parameter_delta {
                output.write_i64(*delta);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{CoordinateDescriptor, Exception, Recurrence};

    fn limits() -> Limits {
        Limits::default()
    }

    fn ref_node(id: u32) -> Node {
        Node::Reference {
            definition: id,
            parameter_delta: Vec::new(),
        }
    }

    fn comprehensive_program() -> Program {
        let definitions = vec![
            Node::Linear {
                count: 4,
                width: 8,
                modulus: 256,
                a: 1,
                b: 0,
            },
            Node::Correct {
                kind: CorrectionKind::Xor,
                prediction: Box::new(ref_node(0)),
                correction: Box::new(Node::Literal(vec![1, 1, 1, 1])),
            },
            Node::Exceptions {
                base: Box::new(ref_node(1)),
                exceptions: vec![Exception {
                    position: 2,
                    value: 9,
                }],
            },
        ];
        let child = Node::Split {
            boundaries: vec![4],
            children: vec![
                Node::Group {
                    coordinate: CoordinateDescriptor::Identity,
                    child: Box::new(ref_node(2)),
                },
                Node::Periodic {
                    pattern: b"xy".to_vec(),
                    repetitions: 2,
                    suffix: b"z".to_vec(),
                },
            ],
        };
        Program {
            definitions,
            root: Node::File {
                original_length: 9,
                child: Box::new(child),
            },
        }
    }

    #[test]
    fn comprehensive_program_is_canonical_and_round_trips() {
        let program = comprehensive_program();
        let report = program.validate(&limits()).unwrap();
        assert_eq!(report.root_type, ValueType::Bytes(9));
        assert_eq!(report.original_bytes, 9);
        assert_eq!(report.node_count, 11);
        assert_eq!(report.dependency_count, 3);

        let encoded = program.encode_sections(&limits()).unwrap();
        let decoded = Program::decode_sections(
            program.definitions.len() as u32,
            &encoded.definitions,
            &encoded.root,
            &limits(),
        )
        .unwrap();
        assert_eq!(decoded, program);
        assert_eq!(decoded.encode_sections(&limits()).unwrap(), encoded);
    }

    #[test]
    fn literal_golden_record_matches_frozen_node_shape() {
        let program = Program::literal(b"abc".to_vec());
        let report = program.validate(&limits()).unwrap();
        assert_eq!(report.node_count, 2);
        assert_eq!(report.edge_count, 1);
        assert_eq!(report.graph_depth, 2);
        let encoded = program.encode_sections(&limits()).unwrap();
        assert!(encoded.definitions.is_empty());
        assert_eq!(
            encoded.root,
            vec![
                0x20, 0x00, 0x08, 0x03, // FILE, flags, payload length, original length
                0x00, 0x00, 0x04, 0x03, b'a', b'b', b'c'
            ]
        );
    }

    #[test]
    fn every_generator_round_trips_deterministically() {
        let mut generators = vec![
            Node::Const {
                length: 7,
                value: 42,
            },
            Node::Linear {
                count: 7,
                width: 8,
                modulus: 251,
                a: 3,
                b: 4,
            },
            Node::Periodic {
                pattern: vec![1, 2],
                repetitions: 3,
                suffix: vec![3],
            },
            Node::Recurrence(Recurrence {
                count: 7,
                width: 8,
                modulus: 251,
                coefficients: vec![1, 1],
                initial_state: vec![1, 2],
            }),
        ];
        for generator in generators.drain(..) {
            let program = Program {
                definitions: Vec::new(),
                root: Node::File {
                    original_length: 7,
                    child: Box::new(generator),
                },
            };
            let first = program.encode_sections(&limits()).unwrap();
            let decoded =
                Program::decode_sections(0, &first.definitions, &first.root, &limits()).unwrap();
            assert_eq!(decoded, program);
            assert_eq!(decoded.encode_sections(&limits()).unwrap(), first);
        }
    }

    #[test]
    fn deterministic_property_like_literal_and_periodic_round_trips() {
        let mut state = 0x8f42_1d3c_79a5_0b61u64;
        for length in 0..512usize {
            let mut bytes = Vec::with_capacity(length);
            for _ in 0..length {
                state = state
                    .wrapping_mul(2_862_933_555_777_941_757)
                    .wrapping_add(3_037_000_493);
                bytes.push((state >> 24) as u8);
            }
            let program = Program::literal(bytes);
            let encoded = program.encode_sections(&limits()).unwrap();
            let decoded =
                Program::decode_sections(0, &encoded.definitions, &encoded.root, &limits())
                    .unwrap();
            assert_eq!(decoded, program);
        }
    }

    #[test]
    fn all_correction_modes_validate_and_type_mismatch_is_rejected() {
        for kind in [
            CorrectionKind::Add,
            CorrectionKind::Subtract,
            CorrectionKind::Xor,
        ] {
            let program = Program {
                definitions: Vec::new(),
                root: Node::File {
                    original_length: 3,
                    child: Box::new(Node::Correct {
                        kind,
                        prediction: Box::new(Node::Const {
                            length: 3,
                            value: 1,
                        }),
                        correction: Box::new(Node::Literal(vec![0; 3])),
                    }),
                },
            };
            program.validate(&limits()).unwrap();
        }

        let invalid = Program {
            definitions: Vec::new(),
            root: Node::File {
                original_length: 2,
                child: Box::new(Node::Correct {
                    kind: CorrectionKind::Xor,
                    prediction: Box::new(Node::Linear {
                        count: 1,
                        width: 16,
                        modulus: 65_535,
                        a: 1,
                        b: 0,
                    }),
                    correction: Box::new(Node::Literal(vec![0; 2])),
                }),
            },
        };
        assert!(matches!(
            invalid.validate(&limits()),
            Err(Error::TypeMismatch {
                context: "correction operands"
            })
        ));
    }

    #[test]
    fn rejects_forward_refs_parameter_deltas_and_nested_files() {
        let forward = Program {
            definitions: vec![ref_node(0)],
            root: Node::File {
                original_length: 0,
                child: Box::new(Node::Literal(Vec::new())),
            },
        };
        assert!(matches!(
            forward.validate(&limits()),
            Err(Error::InvalidReference { .. })
        ));

        let parameterized = Program {
            definitions: vec![Node::Literal(vec![1])],
            root: Node::File {
                original_length: 1,
                child: Box::new(Node::Reference {
                    definition: 0,
                    parameter_delta: vec![1],
                }),
            },
        };
        assert!(matches!(
            parameterized.validate(&limits()),
            Err(Error::InvalidValue(
                "parameterized references are reserved but not implemented in MVP"
            ))
        ));

        let nested = Program {
            definitions: vec![Node::File {
                original_length: 0,
                child: Box::new(Node::Literal(Vec::new())),
            }],
            root: Node::File {
                original_length: 0,
                child: Box::new(Node::Literal(Vec::new())),
            },
        };
        assert!(nested.validate(&limits()).is_err());
    }

    #[test]
    fn split_exceptions_and_recurrence_invariants_are_checked() {
        let bad_split = Program {
            definitions: Vec::new(),
            root: Node::File {
                original_length: 4,
                child: Box::new(Node::Split {
                    boundaries: vec![3],
                    children: vec![Node::Literal(vec![1, 2]), Node::Literal(vec![3, 4])],
                }),
            },
        };
        assert!(matches!(
            bad_split.validate(&limits()),
            Err(Error::LengthMismatch {
                context: "SPLIT boundary",
                ..
            })
        ));

        let bad_exceptions = Program {
            definitions: Vec::new(),
            root: Node::File {
                original_length: 2,
                child: Box::new(Node::Exceptions {
                    base: Box::new(Node::Literal(vec![1, 2])),
                    exceptions: vec![
                        Exception {
                            position: 1,
                            value: 3,
                        },
                        Exception {
                            position: 1,
                            value: 4,
                        },
                    ],
                }),
            },
        };
        assert!(bad_exceptions.validate(&limits()).is_err());

        let bad_recurrence = Program {
            definitions: Vec::new(),
            root: Node::File {
                original_length: 3,
                child: Box::new(Node::Recurrence(Recurrence {
                    count: 3,
                    width: 8,
                    modulus: 251,
                    coefficients: vec![1, 1],
                    initial_state: vec![1],
                })),
            },
        };
        assert!(matches!(
            bad_recurrence.validate(&limits()),
            Err(Error::LengthMismatch {
                context: "RECURRENCE initial state",
                ..
            })
        ));
    }

    #[test]
    fn malformed_wire_encodings_are_rejected() {
        // Non-canonical payload length for LITERAL.
        assert!(matches!(
            Program::decode_sections(
                0,
                &[],
                &[0x00, 0x00, 0x84, 0x00, 0x03, b'a', b'b', b'c'],
                &limits()
            ),
            Err(Error::InvalidVarint { .. })
        ));
        // Flags must be zero.
        assert!(matches!(
            Program::decode_sections(0, &[], &[0x00, 1, 1, 0], &limits()),
            Err(Error::InvalidFlags { .. })
        ));
        // Unimplemented and invalid opcode ranges differ.
        assert_eq!(
            Program::decode_sections(0, &[], &[0x40, 0, 0], &limits()),
            Err(Error::UnsupportedOpcode(0x40))
        );
        assert_eq!(
            Program::decode_sections(0, &[], &[0xc0, 0, 0], &limits()),
            Err(Error::InvalidOpcode(0xc0))
        );
        // A root section cannot carry trailing bytes.
        let mut root = Program::literal(vec![1])
            .encode_sections(&limits())
            .unwrap()
            .root;
        root.push(0);
        assert!(matches!(
            Program::decode_sections(0, &[], &root, &limits()),
            Err(Error::TrailingData {
                context: "root section",
                ..
            })
        ));
    }

    #[test]
    fn dense_definition_ids_and_limits_are_enforced() {
        let program = comprehensive_program();
        let encoded = program.encode_sections(&limits()).unwrap();
        let mut bad_ids = encoded.definitions.clone();
        bad_ids[0] = 1;
        assert!(Program::decode_sections(3, &bad_ids, &encoded.root, &limits()).is_err());

        let tiny = Limits {
            max_nodes: 2,
            ..limits()
        };
        assert!(matches!(
            program.validate(&tiny),
            Err(Error::LimitExceeded { what: "nodes", .. })
        ));

        let tiny = Limits {
            max_graph_depth: 1,
            ..limits()
        };
        assert!(matches!(
            Program::literal(vec![1]).validate(&tiny),
            Err(Error::LimitExceeded {
                what: "graph depth",
                ..
            })
        ));

        let tiny = Limits {
            max_output_bytes: 2,
            ..limits()
        };
        assert!(matches!(
            Program::literal(vec![1, 2, 3]).validate(&tiny),
            Err(Error::LimitExceeded {
                what: "output bytes",
                ..
            })
        ));
    }

    #[test]
    fn candidate_cost_uses_actual_container_size_and_total_fallback() {
        let program = Program::literal(vec![1, 2, 3]);
        let cost = program.candidate_cost(777, 123, &limits()).unwrap();
        assert_eq!(cost.archive_bytes, 777);
        assert_eq!(cost.decode_memory, 123);
        assert_eq!(cost.dependency_count, 0);
        assert!(!cost.canonical_payload.is_empty());
        assert_eq!(cost.opcode_sequence, vec![0x20, 0x00]);
    }

    #[test]
    fn normative_workspace_includes_the_complete_definition_cache() {
        let program = Program {
            definitions: vec![Node::Literal(vec![1, 2, 3, 4])],
            root: Node::File {
                original_length: 4,
                child: Box::new(ref_node(0)),
            },
        };
        let report = program.validate(&limits()).unwrap();
        assert_eq!(report.temporary_bytes, 4);

        let tiny = Limits {
            max_temporary_bytes: 3,
            ..limits()
        };
        assert!(matches!(
            program.validate(&tiny),
            Err(Error::LimitExceeded {
                what: "temporary bytes",
                ..
            })
        ));
    }

    #[test]
    fn frozen_v1_block_output_limit_is_enforced_without_allocating_output() {
        let length = (16 * 1024 * 1024) + 1;
        let program = Program {
            definitions: Vec::new(),
            root: Node::File {
                original_length: length,
                child: Box::new(Node::Const { length, value: 0 }),
            },
        };
        assert!(matches!(
            program.validate(&limits()),
            Err(Error::LimitExceeded {
                what: "block output bytes",
                ..
            })
        ));
    }
}
