//! Deterministic, bounded evaluation of native MathSVG DSL programs.
//!
//! Evaluation is deliberately separated from parsing.  Callers first obtain a
//! [`Program`] from `mathsvg-dsl`; this crate always runs the DSL's complete
//! structural/type/resource preflight before producing bytes.

use mathsvg_coordinates::{inverse_into, CoordinateDescriptor as NativeCoordinateDescriptor};
use mathsvg_core::{
    checked_u64_add, checked_u64_mul, decode_word_le, mul_add_mod, word_bytes, wrapping_add_width,
    wrapping_sub_width, Error, Limits, Result,
};
use mathsvg_dsl::{CorrectionKind, Node, Program, Recurrence};
use mathsvg_entropy::{DecodeLimits as EntropyDecodeLimits, LeafMetadata};
use mathsvg_kernels::{apply_bytes_active, resolve_backend, ByteOperation, KernelError};

pub use mathsvg_kernels::{ActiveBackend, RequestedBackend as EvaluationBackend};

/// Resource facts observed by the concrete evaluation schedule.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct EvaluationReport {
    /// Exact number of bytes returned or passed to the sink.
    pub output_bytes: u64,
    /// Peak live evaluator scratch, including memoized definition values.
    ///
    /// The final result buffer is the caller's block destination and is not
    /// scratch.  A correction operand and every live memoized definition are.
    pub temporary_bytes_peak: u64,
    /// Number of block-local definitions actually evaluated and memoized.
    pub memoized_definitions: u64,
    /// Backend actually selected after runtime feature detection.
    pub backend: ActiveBackend,
}

/// Complete evaluated program bytes plus the backend actually used.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EvaluatedProgram {
    pub bytes: Vec<u8>,
    pub backend: ActiveBackend,
}

pub fn resolve_evaluation_backend(requested: EvaluationBackend) -> Result<ActiveBackend> {
    resolve_backend(requested).map_err(map_kernel_error)
}

/// Evaluate one complete block program into a byte vector.
///
/// `Program::validate` runs before the result allocation or any node
/// evaluation.  Success always contains exactly the `FILE` node's declared
/// length.
pub fn evaluate_program(program: &Program, limits: &Limits) -> Result<Vec<u8>> {
    evaluate_program_internal(program, limits, EvaluationBackend::Scalar)
        .map(|evaluated| evaluated.bytes)
}

/// Evaluate with an explicit scalar, native SIMD, or auto-detected backend.
///
/// Backend choice affects execution only. All outputs must be byte-identical
/// and no backend value participates in parsing, cost, search, or wire bytes.
pub fn evaluate_program_with_backend(
    program: &Program,
    limits: &Limits,
    backend: EvaluationBackend,
) -> Result<EvaluatedProgram> {
    let evaluated = evaluate_program_internal(program, limits, backend)?;
    Ok(EvaluatedProgram {
        backend: evaluated.report.backend,
        bytes: evaluated.bytes,
    })
}

/// Evaluate a program that has already passed the container decoder's strict
/// `Program::validate` pass.
///
/// This entry point exists for the streaming container/evaluator boundary: the
/// container validates every payload and checks the resulting report against
/// redundant block metadata immediately before invoking the evaluator. Running
/// the identical full entropy validation again would add a complete payload
/// walk. The evaluator still checks layouts and limits, strictly decodes every
/// entropy leaf into a private buffer, and returns an error before publication
/// if any invariant fails. Callers with an arbitrary `Program` must use
/// [`evaluate_program`] instead.
pub fn evaluate_container_validated_program(program: &Program, limits: &Limits) -> Result<Vec<u8>> {
    evaluate_container_validated_program_with_backend(program, limits, EvaluationBackend::Scalar)
        .map(|evaluated| evaluated.bytes)
}

/// Backend-selecting counterpart of
/// [`evaluate_container_validated_program`].
pub fn evaluate_container_validated_program_with_backend(
    program: &Program,
    limits: &Limits,
    requested_backend: EvaluationBackend,
) -> Result<EvaluatedProgram> {
    let expected = match &program.root {
        Node::File {
            original_length, ..
        } => *original_length,
        _ => {
            return Err(Error::InvalidValue(
                "container-validated program root is not FILE",
            ));
        }
    };
    check_output_limits(expected, limits)?;
    let backend = resolve_evaluation_backend(requested_backend)?;
    let layouts = definition_layouts(&program.definitions, limits)?;
    let mut output = allocate_exact(expected)?;
    let mut runtime = Runtime::new(program.definitions.len(), limits, backend);
    evaluate_into(
        &program.root,
        &program.definitions,
        &layouts,
        &mut runtime,
        &mut output,
    )?;
    require_length("FILE result", expected, output.len())?;
    let report = runtime.report(expected);
    limits.check(
        "temporary bytes",
        report.temporary_bytes_peak,
        limits.max_temporary_bytes,
    )?;
    Ok(EvaluatedProgram {
        bytes: output,
        backend: report.backend,
    })
}

/// Evaluate a complete program, then pass its whole verified block value to a
/// sink callback.
///
/// The callback is not invoked when validation or evaluation fails.  A block
/// is at most `limits.max_block_output_bytes`, so keeping this commit boundary
/// makes it possible for a container decoder to hash/verify a block before it
/// exposes bytes downstream.
pub fn evaluate_program_to_sink<F>(
    program: &Program,
    limits: &Limits,
    sink: F,
) -> Result<EvaluationReport>
where
    F: FnMut(&[u8]) -> Result<()>,
{
    evaluate_program_to_sink_with_backend(program, limits, EvaluationBackend::Scalar, sink)
}

pub fn evaluate_program_to_sink_with_backend<F>(
    program: &Program,
    limits: &Limits,
    backend: EvaluationBackend,
    mut sink: F,
) -> Result<EvaluationReport>
where
    F: FnMut(&[u8]) -> Result<()>,
{
    let evaluated = evaluate_program_internal(program, limits, backend)?;
    sink(&evaluated.bytes)?;
    Ok(evaluated.report)
}

/// Evaluate an individual node against a topologically ordered block-local
/// definition table.
///
/// This convenience API also preflights through `Program::validate`: it
/// validates the supplied definitions followed by `node` as a final
/// definition, while an empty `FILE` serves only as the validation root.  This
/// allows word-producing generator nodes to be tested directly even though
/// the current DSL has no word-to-byte coordinate node yet.
pub fn evaluate_node(node: &Node, definitions: &[Node], limits: &Limits) -> Result<Vec<u8>> {
    evaluate_node_with_report(node, definitions, limits).map(|evaluated| evaluated.0)
}

/// Report-producing counterpart of [`evaluate_node`].
pub fn evaluate_node_with_report(
    node: &Node,
    definitions: &[Node],
    limits: &Limits,
) -> Result<(Vec<u8>, EvaluationReport)> {
    let mut validation_definitions = definitions.to_vec();
    validation_definitions.push(node.clone());
    let validation_program = Program {
        definitions: validation_definitions,
        root: Node::File {
            original_length: 0,
            child: Box::new(Node::Literal(Vec::new())),
        },
    };
    let validation = validation_program.validate(limits)?;

    let layouts = definition_layouts(definitions, limits)?;
    let layout = infer_layout(node, &layouts, limits)?;
    let expected = layout.byte_len()?;
    check_output_limits(expected, limits)?;

    let mut output = allocate_exact(expected)?;
    let mut runtime = Runtime::new(definitions.len(), limits, ActiveBackend::Scalar);
    evaluate_into(node, definitions, &layouts, &mut runtime, &mut output)?;
    require_length("evaluated node", expected, output.len())?;

    let report = runtime.report(expected);
    require_workspace_bound(report.temporary_bytes_peak, validation.temporary_bytes)?;
    Ok((output, report))
}

struct Evaluated {
    bytes: Vec<u8>,
    report: EvaluationReport,
}

fn evaluate_program_internal(
    program: &Program,
    limits: &Limits,
    requested_backend: EvaluationBackend,
) -> Result<Evaluated> {
    let validation = program.validate(limits)?;
    let expected = validation.original_bytes;
    check_output_limits(expected, limits)?;
    let backend = resolve_evaluation_backend(requested_backend)?;

    let layouts = definition_layouts(&program.definitions, limits)?;
    let mut output = allocate_exact(expected)?;
    let mut runtime = Runtime::new(program.definitions.len(), limits, backend);
    evaluate_into(
        &program.root,
        &program.definitions,
        &layouts,
        &mut runtime,
        &mut output,
    )?;
    require_length("FILE result", expected, output.len())?;

    let report = runtime.report(expected);
    require_workspace_bound(report.temporary_bytes_peak, validation.temporary_bytes)?;
    Ok(Evaluated {
        bytes: output,
        report,
    })
}

fn require_workspace_bound(actual_peak: u64, normative_bound: u64) -> Result<()> {
    if actual_peak <= normative_bound {
        Ok(())
    } else {
        Err(Error::InvalidValue(
            "evaluator exceeded the normative workspace bound",
        ))
    }
}

fn entropy_limits(limits: &Limits) -> EntropyDecodeLimits {
    EntropyDecodeLimits {
        max_encoded_bytes: limits.max_node_payload_bytes.min(limits.max_archive_bytes),
        max_output_bytes: limits.max_output_bytes.min(limits.max_block_output_bytes),
        max_work: limits.max_work,
    }
}

fn preflight_entropy(envelope: &[u8], limits: &Limits) -> Result<LeafMetadata> {
    mathsvg_entropy::preflight(envelope, entropy_limits(limits)).map_err(map_entropy_error)
}

fn map_entropy_error(error: mathsvg_entropy::Error) -> Error {
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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Layout {
    Bytes(u64),
    Words { width: u8, count: u64 },
}

impl Layout {
    fn byte_len(self) -> Result<u64> {
        match self {
            Self::Bytes(length) => Ok(length),
            Self::Words { width, count } => checked_u64_mul(
                count,
                u64::from(word_bytes(width)?),
                "evaluator word output",
            ),
        }
    }
}

fn definition_layouts(definitions: &[Node], limits: &Limits) -> Result<Vec<Layout>> {
    let mut layouts = Vec::with_capacity(definitions.len());
    for definition in definitions {
        layouts.push(infer_layout(definition, &layouts, limits)?);
    }
    Ok(layouts)
}

fn infer_layout(node: &Node, definitions: &[Layout], limits: &Limits) -> Result<Layout> {
    match node {
        Node::Literal(bytes) => Ok(Layout::Bytes(bytes.len() as u64)),
        Node::EntropyLiteral(envelope) => Ok(Layout::Bytes(
            preflight_entropy(envelope, limits)?.decoded_bytes,
        )),
        Node::Const { length, .. } => Ok(Layout::Bytes(*length)),
        Node::Linear { count, width, .. } => generator_layout(*count, *width),
        Node::Periodic {
            pattern,
            repetitions,
            suffix,
        } => {
            let repeated = checked_u64_mul(
                pattern.len() as u64,
                *repetitions,
                "PERIODIC evaluator layout",
            )?;
            Ok(Layout::Bytes(checked_u64_add(
                repeated,
                suffix.len() as u64,
                "PERIODIC evaluator layout",
            )?))
        }
        Node::Recurrence(recurrence) => generator_layout(recurrence.count, recurrence.width),
        Node::File {
            original_length, ..
        } => Ok(Layout::Bytes(*original_length)),
        Node::Concat(children) => concatenate_layouts(children, definitions, limits),
        Node::Split { children, .. } => {
            let mut total = 0u64;
            for child in children {
                match infer_layout(child, definitions, limits)? {
                    Layout::Bytes(length) => {
                        total = checked_u64_add(total, length, "SPLIT evaluator layout")?;
                    }
                    Layout::Words { .. } => {
                        return Err(Error::TypeMismatch {
                            context: "SPLIT evaluator children",
                        });
                    }
                }
            }
            Ok(Layout::Bytes(total))
        }
        Node::Group { child, .. } => infer_layout(child, definitions, limits),
        Node::Coordinate { descriptor, .. } => Ok(Layout::Bytes(descriptor.original_bytes)),
        Node::Correct { prediction, .. } => infer_layout(prediction, definitions, limits),
        Node::Exceptions { base, .. } => infer_layout(base, definitions, limits),
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

fn concatenate_layouts(
    children: &[Node],
    definitions: &[Layout],
    limits: &Limits,
) -> Result<Layout> {
    let Some(first) = children.first() else {
        return Ok(Layout::Bytes(0));
    };
    let first_layout = infer_layout(first, definitions, limits)?;
    match first_layout {
        Layout::Bytes(first_length) => {
            let mut total = first_length;
            for child in &children[1..] {
                let Layout::Bytes(length) = infer_layout(child, definitions, limits)? else {
                    return Err(Error::TypeMismatch {
                        context: "CONCAT evaluator children",
                    });
                };
                total = checked_u64_add(total, length, "CONCAT evaluator layout")?;
            }
            Ok(Layout::Bytes(total))
        }
        Layout::Words { width, count } => {
            let mut total = count;
            for child in &children[1..] {
                let Layout::Words {
                    width: child_width,
                    count: child_count,
                } = infer_layout(child, definitions, limits)?
                else {
                    return Err(Error::TypeMismatch {
                        context: "CONCAT evaluator children",
                    });
                };
                if child_width != width {
                    return Err(Error::TypeMismatch {
                        context: "CONCAT evaluator word layout",
                    });
                }
                total = checked_u64_add(total, child_count, "CONCAT evaluator layout")?;
            }
            Ok(Layout::Words {
                width,
                count: total,
            })
        }
    }
}

fn generator_layout(count: u64, width: u8) -> Result<Layout> {
    word_bytes(width)?;
    if width == 8 {
        Ok(Layout::Bytes(count))
    } else {
        Ok(Layout::Words { width, count })
    }
}

#[derive(Debug)]
enum CacheEntry {
    Empty,
    Evaluating,
    Ready(Vec<u8>),
}

struct Runtime<'a> {
    cache: Vec<CacheEntry>,
    limits: &'a Limits,
    temporary_live: u64,
    temporary_peak: u64,
    memoized_definitions: u64,
    backend: ActiveBackend,
}

impl<'a> Runtime<'a> {
    fn new(definition_count: usize, limits: &'a Limits, backend: ActiveBackend) -> Self {
        let cache = core::iter::repeat_with(|| CacheEntry::Empty)
            .take(definition_count)
            .collect();
        Self {
            cache,
            limits,
            temporary_live: 0,
            temporary_peak: 0,
            memoized_definitions: 0,
            backend,
        }
    }

    fn charge_temporary(&mut self, bytes: u64) -> Result<()> {
        self.temporary_live =
            checked_u64_add(self.temporary_live, bytes, "evaluator live temporary bytes")?;
        self.temporary_peak = self.temporary_peak.max(self.temporary_live);
        self.limits.check(
            "temporary bytes",
            self.temporary_peak,
            self.limits.max_temporary_bytes,
        )
    }

    fn release_temporary(&mut self, bytes: u64) -> Result<()> {
        self.temporary_live =
            self.temporary_live
                .checked_sub(bytes)
                .ok_or(Error::IntegerOverflow {
                    context: "evaluator temporary release",
                })?;
        Ok(())
    }

    fn report(&self, output_bytes: u64) -> EvaluationReport {
        EvaluationReport {
            output_bytes,
            temporary_bytes_peak: self.temporary_peak,
            memoized_definitions: self.memoized_definitions,
            backend: self.backend,
        }
    }
}

fn evaluate_into(
    node: &Node,
    definitions: &[Node],
    layouts: &[Layout],
    runtime: &mut Runtime<'_>,
    output: &mut Vec<u8>,
) -> Result<()> {
    match node {
        Node::Literal(bytes) => append_bytes(output, bytes),
        Node::EntropyLiteral(envelope) => evaluate_entropy(envelope, runtime, output),
        Node::Const { length, value } => append_repeated(output, *value, *length),
        Node::Linear {
            count,
            width,
            modulus,
            a,
            b,
        } => evaluate_linear(*count, *width, *modulus, *a, *b, output),
        Node::Periodic {
            pattern,
            repetitions,
            suffix,
        } => evaluate_periodic(pattern, *repetitions, suffix, output),
        Node::Recurrence(recurrence) => evaluate_recurrence(recurrence, output),
        Node::File {
            original_length,
            child,
        } => {
            let start = output.len();
            evaluate_into(child, definitions, layouts, runtime, output)?;
            require_delta("FILE child", *original_length, start, output.len())
        }
        Node::Concat(children) => {
            for child in children {
                evaluate_into(child, definitions, layouts, runtime, output)?;
            }
            Ok(())
        }
        Node::Split {
            boundaries,
            children,
        } => {
            let start = output.len();
            for (index, child) in children.iter().enumerate() {
                evaluate_into(child, definitions, layouts, runtime, output)?;
                if let Some(boundary) = boundaries.get(index) {
                    require_delta("SPLIT boundary", *boundary, start, output.len())?;
                }
            }
            Ok(())
        }
        Node::Group { child, .. } => {
            // GROUP keeps the original metadata-only identity semantics.
            evaluate_into(child, definitions, layouts, runtime, output)
        }
        Node::Coordinate { descriptor, child } => {
            evaluate_coordinate(descriptor, child, definitions, layouts, runtime, output)
        }
        Node::Correct {
            kind,
            prediction,
            correction,
        } => evaluate_correction(
            *kind,
            prediction,
            correction,
            definitions,
            layouts,
            runtime,
            output,
        ),
        Node::Exceptions { base, exceptions } => {
            let start = output.len();
            evaluate_into(base, definitions, layouts, runtime, output)?;
            for exception in exceptions {
                let relative =
                    usize::try_from(exception.position).map_err(|_| Error::IntegerOverflow {
                        context: "exception position",
                    })?;
                let position = start.checked_add(relative).ok_or(Error::IntegerOverflow {
                    context: "exception position",
                })?;
                let target = output
                    .get_mut(position)
                    .ok_or(Error::InvalidValue("exception position is outside base"))?;
                *target = exception.value;
            }
            Ok(())
        }
        Node::Reference {
            definition,
            parameter_delta,
        } => {
            if !parameter_delta.is_empty() {
                return Err(Error::InvalidValue(
                    "parameterized references are reserved but not implemented in MVP",
                ));
            }
            evaluate_reference(*definition as usize, definitions, layouts, runtime, output)
        }
    }
}

fn evaluate_coordinate(
    descriptor: &NativeCoordinateDescriptor,
    child: &Node,
    definitions: &[Node],
    layouts: &[Layout],
    runtime: &mut Runtime<'_>,
    output: &mut Vec<u8>,
) -> Result<()> {
    let transformed_bytes = descriptor.transformed_bytes()?;
    runtime.charge_temporary(transformed_bytes)?;
    let mut transformed = match allocate_exact(transformed_bytes) {
        Ok(value) => value,
        Err(error) => {
            runtime.release_temporary(transformed_bytes)?;
            return Err(error);
        }
    };
    let evaluated =
        evaluate_into(child, definitions, layouts, runtime, &mut transformed).and_then(|()| {
            require_length(
                "coordinate transformed child",
                transformed_bytes,
                transformed.len(),
            )
        });
    if let Err(error) = evaluated {
        runtime.release_temporary(transformed_bytes)?;
        return Err(error);
    }

    let start = match extend_zeroed(
        output,
        descriptor.original_bytes,
        "coordinate output length",
    ) {
        Ok(start) => start,
        Err(error) => {
            runtime.release_temporary(transformed_bytes)?;
            return Err(error);
        }
    };
    let inverse = inverse_into(
        &transformed,
        descriptor,
        &mut output[start..],
        runtime.limits,
    );
    runtime.release_temporary(transformed_bytes)?;
    inverse
}

fn evaluate_entropy(envelope: &[u8], runtime: &Runtime<'_>, output: &mut Vec<u8>) -> Result<()> {
    // `evaluate_program_internal` has already run the DSL's strict validation,
    // including a complete entropy-payload walk. Re-reading the envelope here
    // is only needed to size the private result buffer. The one-pass decoder
    // below still validates the payload while producing bytes; the enclosing
    // evaluator discards this uncommitted buffer on any error.
    let metadata = preflight_entropy(envelope, runtime.limits)?;
    let start = extend_zeroed(
        output,
        metadata.decoded_bytes,
        "native entropy output length",
    )?;
    mathsvg_entropy::decode_exact_into_uncommitted(
        envelope,
        metadata.decoded_bytes,
        &mut output[start..],
        entropy_limits(runtime.limits),
    )
    .map_err(map_entropy_error)?;
    Ok(())
}

fn evaluate_reference(
    id: usize,
    definitions: &[Node],
    layouts: &[Layout],
    runtime: &mut Runtime<'_>,
    output: &mut Vec<u8>,
) -> Result<()> {
    let definition = definitions.get(id).ok_or(Error::InvalidReference {
        id: id as u64,
        available_definitions: definitions.len(),
    })?;
    let layout = layouts.get(id).copied().ok_or(Error::InvalidReference {
        id: id as u64,
        available_definitions: layouts.len(),
    })?;

    match runtime.cache.get(id) {
        Some(CacheEntry::Ready(bytes)) => return append_bytes(output, bytes),
        Some(CacheEntry::Evaluating) => {
            return Err(Error::InvalidValue(
                "cyclic definition encountered during evaluation",
            ));
        }
        Some(CacheEntry::Empty) => {}
        None => {
            return Err(Error::InvalidReference {
                id: id as u64,
                available_definitions: runtime.cache.len(),
            });
        }
    }

    let expected = layout.byte_len()?;
    runtime.charge_temporary(expected)?;
    runtime.cache[id] = CacheEntry::Evaluating;

    let mut value = match allocate_exact(expected) {
        Ok(value) => value,
        Err(error) => {
            runtime.cache[id] = CacheEntry::Empty;
            runtime.release_temporary(expected)?;
            return Err(error);
        }
    };
    if let Err(error) = evaluate_into(definition, definitions, layouts, runtime, &mut value) {
        runtime.cache[id] = CacheEntry::Empty;
        runtime.release_temporary(expected)?;
        return Err(error);
    }
    if let Err(error) = require_length("definition result", expected, value.len()) {
        runtime.cache[id] = CacheEntry::Empty;
        runtime.release_temporary(expected)?;
        return Err(error);
    }

    runtime.cache[id] = CacheEntry::Ready(value);
    runtime.memoized_definitions =
        checked_u64_add(runtime.memoized_definitions, 1, "memoized definitions")?;
    let CacheEntry::Ready(bytes) = &runtime.cache[id] else {
        return Err(Error::InvalidValue("definition cache state"));
    };
    append_bytes(output, bytes)
}

#[allow(clippy::too_many_arguments)]
fn evaluate_correction(
    kind: CorrectionKind,
    prediction: &Node,
    correction: &Node,
    definitions: &[Node],
    layouts: &[Layout],
    runtime: &mut Runtime<'_>,
    output: &mut Vec<u8>,
) -> Result<()> {
    let layout = infer_layout(prediction, layouts, runtime.limits)?;
    let expected = layout.byte_len()?;
    let start = output.len();
    evaluate_into(prediction, definitions, layouts, runtime, output)?;
    require_delta("correction prediction", expected, start, output.len())?;

    runtime.charge_temporary(expected)?;
    let mut correction_value = match allocate_exact(expected) {
        Ok(value) => value,
        Err(error) => {
            runtime.release_temporary(expected)?;
            return Err(error);
        }
    };
    let evaluated = evaluate_into(
        correction,
        definitions,
        layouts,
        runtime,
        &mut correction_value,
    )
    .and_then(|()| require_length("correction operand", expected, correction_value.len()));
    if let Err(error) = evaluated {
        runtime.release_temporary(expected)?;
        return Err(error);
    }

    let result = apply_correction(
        kind,
        layout,
        &mut output[start..],
        &correction_value,
        runtime.backend,
    );
    runtime.release_temporary(expected)?;
    result
}

fn apply_correction(
    kind: CorrectionKind,
    layout: Layout,
    prediction: &mut [u8],
    correction: &[u8],
    backend: ActiveBackend,
) -> Result<()> {
    if prediction.len() != correction.len() {
        return Err(Error::LengthMismatch {
            context: "correction operands",
            expected: prediction.len() as u64,
            actual: correction.len() as u64,
        });
    }

    match (kind, layout) {
        (CorrectionKind::Xor, _) => {
            apply_bytes_active(backend, ByteOperation::Xor, prediction, correction)
                .map_err(map_kernel_error)
        }
        (CorrectionKind::Add, Layout::Bytes(_)) => {
            apply_bytes_active(backend, ByteOperation::AddWrapping, prediction, correction)
                .map_err(map_kernel_error)
        }
        (CorrectionKind::Subtract, Layout::Bytes(_)) => apply_bytes_active(
            backend,
            ByteOperation::SubtractWrapping,
            prediction,
            correction,
        )
        .map_err(map_kernel_error),
        (kind @ (CorrectionKind::Add | CorrectionKind::Subtract), Layout::Words { width, .. }) => {
            let bytes_per_word = usize::from(word_bytes(width)?);
            for (target, delta) in prediction
                .chunks_exact_mut(bytes_per_word)
                .zip(correction.chunks_exact(bytes_per_word))
            {
                let left = decode_word_le(target, width)?;
                let right = decode_word_le(delta, width)?;
                let value = if kind == CorrectionKind::Add {
                    wrapping_add_width(left, right, width)?
                } else {
                    wrapping_sub_width(left, right, width)?
                };
                target.copy_from_slice(&value.to_le_bytes()[..bytes_per_word]);
            }
            Ok(())
        }
    }
}

fn map_kernel_error(error: KernelError) -> Error {
    match error {
        KernelError::LengthMismatch { target, operand } => Error::LengthMismatch {
            context: "correction operands",
            expected: target as u64,
            actual: operand as u64,
        },
        KernelError::SimdUnavailable => {
            Error::InvalidValue("requested native SIMD backend is unavailable")
        }
    }
}

fn evaluate_linear(
    count: u64,
    width: u8,
    modulus: u64,
    a: u64,
    b: u64,
    output: &mut Vec<u8>,
) -> Result<()> {
    for index in 0..count {
        let value = mul_add_mod(a, index, b, modulus)?;
        append_word(output, value, width)?;
    }
    Ok(())
}

fn evaluate_periodic(
    pattern: &[u8],
    repetitions: u64,
    suffix: &[u8],
    output: &mut Vec<u8>,
) -> Result<()> {
    for _ in 0..repetitions {
        append_bytes(output, pattern)?;
    }
    append_bytes(output, suffix)
}

fn evaluate_recurrence(recurrence: &Recurrence, output: &mut Vec<u8>) -> Result<()> {
    let start = output.len();
    let order = recurrence.coefficients.len() as u64;
    for index in 0..recurrence.count {
        let value = if index < order {
            recurrence.initial_state[index as usize]
        } else {
            let mut accumulator = 0u64;
            for (coefficient_index, coefficient) in recurrence.coefficients.iter().enumerate() {
                let lag = coefficient_index as u64 + 1;
                let prior = read_generated_word(output, start, index - lag, recurrence.width)?;
                accumulator = mul_add_mod(*coefficient, prior, accumulator, recurrence.modulus)?;
            }
            accumulator
        };
        append_word(output, value, recurrence.width)?;
    }
    Ok(())
}

fn read_generated_word(output: &[u8], start: usize, index: u64, width: u8) -> Result<u64> {
    let word_length = usize::from(word_bytes(width)?);
    let index = usize::try_from(index).map_err(|_| Error::IntegerOverflow {
        context: "recurrence prior index",
    })?;
    let relative = index
        .checked_mul(word_length)
        .ok_or(Error::IntegerOverflow {
            context: "recurrence prior byte offset",
        })?;
    let word_start = start.checked_add(relative).ok_or(Error::IntegerOverflow {
        context: "recurrence prior byte offset",
    })?;
    let word_end = word_start
        .checked_add(word_length)
        .ok_or(Error::IntegerOverflow {
            context: "recurrence prior byte end",
        })?;
    let bytes = output
        .get(word_start..word_end)
        .ok_or(Error::InvalidValue("RECURRENCE prior value is unavailable"))?;
    decode_word_le(bytes, width)
}

fn append_word(output: &mut Vec<u8>, value: u64, width: u8) -> Result<()> {
    let length = usize::from(word_bytes(width)?);
    append_bytes(output, &value.to_le_bytes()[..length])
}

fn allocate_exact(length: u64) -> Result<Vec<u8>> {
    let length = addressable_length(length)?;
    let mut output = Vec::new();
    output
        .try_reserve_exact(length)
        .map_err(|_| Error::InvalidValue("evaluator allocation failed"))?;
    Ok(output)
}

fn append_bytes(output: &mut Vec<u8>, bytes: &[u8]) -> Result<()> {
    output
        .try_reserve_exact(bytes.len())
        .map_err(|_| Error::InvalidValue("evaluator allocation failed"))?;
    output.extend_from_slice(bytes);
    Ok(())
}

fn append_repeated(output: &mut Vec<u8>, value: u8, count: u64) -> Result<()> {
    let count = addressable_length(count)?;
    output
        .try_reserve_exact(count)
        .map_err(|_| Error::InvalidValue("evaluator allocation failed"))?;
    let new_length = output
        .len()
        .checked_add(count)
        .ok_or(Error::IntegerOverflow {
            context: "CONST output length",
        })?;
    output.resize(new_length, value);
    Ok(())
}

fn extend_zeroed(output: &mut Vec<u8>, count: u64, context: &'static str) -> Result<usize> {
    let count = addressable_length(count)?;
    output
        .try_reserve_exact(count)
        .map_err(|_| Error::InvalidValue("evaluator allocation failed"))?;
    let start = output.len();
    let end = start
        .checked_add(count)
        .ok_or(Error::IntegerOverflow { context })?;
    output.resize(end, 0);
    Ok(start)
}

fn addressable_length(length: u64) -> Result<usize> {
    usize::try_from(length).map_err(|_| Error::LimitExceeded {
        what: "addressable output bytes",
        actual: length,
        limit: usize::MAX as u64,
    })
}

fn check_output_limits(length: u64, limits: &Limits) -> Result<()> {
    limits.check("output bytes", length, limits.max_output_bytes)?;
    limits.check("block output bytes", length, limits.max_block_output_bytes)?;
    let _ = addressable_length(length)?;
    Ok(())
}

fn require_delta(context: &'static str, expected: u64, start: usize, end: usize) -> Result<()> {
    let actual = end.checked_sub(start).ok_or(Error::IntegerOverflow {
        context: "evaluated output length",
    })?;
    require_length(context, expected, actual)
}

fn require_length(context: &'static str, expected: u64, actual: usize) -> Result<()> {
    if actual as u64 == expected {
        Ok(())
    } else {
        Err(Error::LengthMismatch {
            context,
            expected,
            actual: actual as u64,
        })
    }
}
