//! Bounded, deterministic exact segmentation for native MathSVG archives.
//!
//! The optimizer deliberately keeps two decisions separate:
//!
//! 1. a finite provider catalogue is searched on canonical grid intervals;
//! 2. a forward acyclic DP combines verified interval nodes using exact
//!    serialized node and boundary bytes.
//!
//! A complete canonical literal archive is serialized before either step.  A
//! selected procedural archive is serialized and verified again, and is
//! returned only when its complete normative [`CandidateCost`] beats that
//! literal upper bound.

#![forbid(unsafe_code)]

use std::cmp::Ordering;

use mathsvg_container::{
    decode_archive, encode_archive, encode_literal_archive, ArchiveBlock, VerifiedArchive,
    V1_MAX_BLOCK_OUTPUT_BYTES,
};
use mathsvg_coordinates::{
    discover_candidates, forward as coordinate_forward, CoordinateDescriptor, CoordinateTransform,
    DiscoveryConfig, DiscoveryEventKind,
};
use mathsvg_core::{
    checked_u64_add, checked_u64_mul, encoded_len, CandidateCost, Cursor, Error, Limits, Result,
};
use mathsvg_dsl::{Node, Program, ValidationReport};
use mathsvg_entropy::{encode_best, encode_lz_huffman_policy_if_better, LeafCodec, LzParserPolicy};
use mathsvg_evaluator::evaluate_program;
use mathsvg_functions::{
    search_block, search_block_seeded, CandidateFamily, SearchConfig as FunctionSearchConfig,
    SearchMode, SearchSeed,
};
use mathsvg_graph::{search_shared_block, GraphConfig};
use mathsvg_residual::{search_residual, ArplGate, ResidualConfig};
use mathsvg_symbolic::{search_symbolic_block, SymbolicConfig};

pub const HARD_MAX_STATES: u64 = 1 << 20;
pub const HARD_MAX_CANDIDATES: u64 = 1 << 20;
pub const HARD_MAX_WORK: u64 = 1 << 40;
pub const HARD_MAX_LEDGER_ENTRIES: u64 = 1 << 20;
pub const HARD_MAX_SEGMENTS_PER_BLOCK: u64 = 4096;

/// Provider-independent semantic category used in the coverage report.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum CoverageClass {
    /// Literal source bytes, including a native entropy-coded literal leaf.
    Literal,
    Function,
    Coordinate,
    Residual,
    Graph,
    Symbolic,
    Other,
}

/// A provider-local canonical descriptor.
///
/// Providers must assign this key without using timing, random state, address
/// order, or floating-point observations. `canonical_payload` is the final
/// provider-specific total-order fallback.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ProviderCandidateKey {
    pub family: u32,
    pub parameters: Vec<u64>,
    pub canonical_payload: Vec<u8>,
}

/// One finite candidate for an interval.
///
/// `node` must produce exactly the interval bytes and must be valid without
/// block-local definitions. The optimizer independently validates and
/// evaluates this claim before adding an edge to the DP.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProvidedCandidate {
    pub key: ProviderCandidateKey,
    pub class: CoverageClass,
    pub node: Node,
}

/// Deterministic budgets visible to a provider for one interval call.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ProviderContext {
    pub block_index: u64,
    pub global_start: u64,
    pub global_end: u64,
    pub candidates_remaining: u64,
    pub work_remaining: u64,
}

/// Result of one finite provider search.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProviderSearch {
    pub candidates: Vec<ProvidedCandidate>,
    pub work_used: u64,
    /// Number of canonical descriptors represented by this call, including
    /// explicit omission/stop rows in the nested engine.
    pub represented_descriptors: u64,
    /// Whether every descriptor in the provider's declared catalogue was
    /// decided, including decisions backed by admissible safe-prune proofs.
    pub complete_within_declared_catalogue: bool,
    pub budget_exhausted: bool,
}

impl ProviderSearch {
    pub fn complete(candidates: Vec<ProvidedCandidate>, work_used: u64) -> Self {
        let represented_descriptors = candidates.len() as u64;
        Self {
            candidates,
            work_used,
            represented_descriptors,
            complete_within_declared_catalogue: true,
            budget_exhausted: false,
        }
    }
}

/// Extensible interval-candidate source.
///
/// Native coordinate, residual, and symbolic adapters implement this trait.
/// Definition-owning graph programs use [`WholeBlockCandidateProvider`].
pub trait CandidateProvider {
    /// Stable non-empty identifier. Multiple providers in one run must have
    /// distinct identifiers.
    fn provider_id(&self) -> &'static str;

    fn search(
        &mut self,
        interval: &[u8],
        context: ProviderContext,
        limits: &Limits,
    ) -> Result<ProviderSearch>;
}

/// A complete program candidate for a whole block.
///
/// Unlike [`ProvidedCandidate`], this representation may own block-local
/// definitions. This is the required competition boundary for native DAG
/// search: flattening such a program into an interval node would silently
/// discard definition bytes and reference semantics.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProvidedProgramCandidate {
    pub key: ProviderCandidateKey,
    pub class: CoverageClass,
    pub program: Program,
    /// Source bytes genuinely reconstructed through `class`.
    pub class_source_bytes: u64,
    /// Source bytes still represented by literal leaves.
    pub literal_source_bytes: u64,
}

/// Result of one deterministic whole-block catalogue search.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WholeBlockProviderSearch {
    pub candidates: Vec<ProvidedProgramCandidate>,
    pub work_used: u64,
    pub represented_descriptors: u64,
    pub complete_within_declared_catalogue: bool,
    pub budget_exhausted: bool,
}

/// Provider boundary for candidates that require program definitions.
pub trait WholeBlockCandidateProvider {
    fn provider_id(&self) -> &'static str;

    fn search(
        &mut self,
        block: &[u8],
        context: ProviderContext,
        limits: &Limits,
    ) -> Result<WholeBlockProviderSearch>;
}

/// Adapter for the finite `mathsvg-functions` block catalogue.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct FunctionsProvider {
    pub config: FunctionSearchConfig,
}

impl CandidateProvider for FunctionsProvider {
    fn provider_id(&self) -> &'static str {
        "mathsvg-functions"
    }

    fn search(
        &mut self,
        interval: &[u8],
        context: ProviderContext,
        limits: &Limits,
    ) -> Result<ProviderSearch> {
        if context.candidates_remaining == 0 || context.work_remaining == 0 {
            return Ok(ProviderSearch {
                candidates: Vec::new(),
                work_used: 0,
                represented_descriptors: 0,
                complete_within_declared_catalogue: false,
                budget_exhausted: true,
            });
        }

        let mut config = self.config.clone();
        config.work_budget = config.work_budget.min(context.work_remaining);
        let searched = search_block(interval, &config, limits)?;
        let represented_descriptors = represented_function_descriptors(&searched)?;
        let mut candidates = Vec::new();
        if searched.winner.key.family != CandidateFamily::Literal {
            let child = match searched.winner.program.root {
                Node::File { child, .. } => *child,
                _ => {
                    return Err(Error::InvalidValue(
                        "function provider winner root is not FILE",
                    ))
                }
            };
            candidates.push(ProvidedCandidate {
                key: ProviderCandidateKey {
                    family: searched.winner.key.family as u8 as u32,
                    parameters: searched.winner.key.parameters,
                    canonical_payload: Vec::new(),
                },
                class: if searched.winner.key.family == CandidateFamily::EntropyLiteral {
                    CoverageClass::Literal
                } else {
                    CoverageClass::Function
                },
                node: child,
            });
        }
        Ok(ProviderSearch {
            candidates,
            work_used: searched.work_used,
            represented_descriptors,
            complete_within_declared_catalogue: searched.complete_within_declared_catalogue,
            budget_exhausted: searched.budget_exhausted,
        })
    }
}

/// Coordinate discovery followed by a native function search in transformed
/// space. Every emitted child is wrapped in the exact inverse coordinate node.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CoordinateProvider {
    pub discovery: DiscoveryConfig,
    pub functions: FunctionSearchConfig,
}

impl CandidateProvider for CoordinateProvider {
    fn provider_id(&self) -> &'static str {
        "mathsvg-coordinates"
    }

    fn search(
        &mut self,
        interval: &[u8],
        context: ProviderContext,
        limits: &Limits,
    ) -> Result<ProviderSearch> {
        if context.candidates_remaining == 0 || context.work_remaining == 0 {
            return Ok(provider_budget_stop());
        }

        let discovery = discover_candidates(interval, limits, &self.discovery)?;
        if discovery.probe_work > context.work_remaining {
            return Ok(provider_budget_stop());
        }
        let mut work_used = discovery.probe_work;
        let mut represented_descriptors =
            (discovery.candidates.len() + discovery.events.len()) as u64;
        let mut complete = discovery.events.is_empty();
        let mut budget_exhausted = discovery
            .events
            .iter()
            .any(|event| event.event == DiscoveryEventKind::BudgetStop);
        let mut candidates = Vec::new();

        for coordinate in discovery.candidates {
            if coordinate.descriptor.transform == CoordinateTransform::Identity {
                continue;
            }
            let transform_work = interval.len() as u64;
            let Some(after_transform) = work_used.checked_add(transform_work) else {
                budget_exhausted = true;
                complete = false;
                break;
            };
            if after_transform > context.work_remaining {
                budget_exhausted = true;
                complete = false;
                break;
            }
            work_used = after_transform;
            let transformed = coordinate_forward(interval, &coordinate.descriptor, limits)?;

            let mut function_config = self.functions.clone();
            function_config.work_budget = function_config
                .work_budget
                .min(context.work_remaining - work_used);
            let searched = search_block(&transformed, &function_config, limits)?;
            represented_descriptors = checked_u64_add(
                represented_descriptors,
                represented_function_descriptors(&searched)?,
                "coordinate represented descriptors",
            )?;
            work_used = checked_u64_add(work_used, searched.work_used, "coordinate provider work")?;
            if work_used > context.work_remaining {
                return Err(Error::LimitExceeded {
                    what: "coordinate provider work",
                    actual: work_used,
                    limit: context.work_remaining,
                });
            }
            complete &= searched.complete_within_declared_catalogue;
            budget_exhausted |= searched.budget_exhausted;

            if searched.winner.key.family == CandidateFamily::Literal {
                continue;
            }
            if candidates.len() as u64 >= context.candidates_remaining {
                budget_exhausted = true;
                complete = false;
                break;
            }
            let family = searched.winner.key.family;
            let child = program_child_without_definitions(searched.winner.program)?;
            let class = if family == CandidateFamily::EntropyLiteral {
                CoverageClass::Literal
            } else {
                CoverageClass::Coordinate
            };
            let mut parameters = vec![coordinate.origin as u8 as u64, family as u8 as u64];
            parameters.extend(searched.winner.key.parameters);
            candidates.push(ProvidedCandidate {
                key: ProviderCandidateKey {
                    family: 1,
                    parameters,
                    canonical_payload: coordinate.canonical_descriptor_bytes,
                },
                class,
                node: Node::Coordinate {
                    descriptor: coordinate.descriptor,
                    child: Box::new(child),
                },
            });
        }

        Ok(ProviderSearch {
            candidates,
            work_used,
            represented_descriptors,
            complete_within_declared_catalogue: complete && !budget_exhausted,
            budget_exhausted,
        })
    }
}

/// Whole-block recursive coordinate/basis candidate search.
///
/// A coordinate transform is useful only if its natural lanes or planes can
/// compete independently. Searching one function over the complete
/// transformed byte string cannot realize that best-basis edge. This provider
/// therefore transforms a block once, partitions the transformed domain into
/// the descriptor's canonical contiguous lanes/planes, selects an exact
/// literal/entropy/function representation for each partition, joins them
/// with a validated [`Node::Split`], and finally wraps the result in the exact
/// inverse coordinate node.
///
/// This provider is an explicit experiment rather than a built-in portfolio
/// member. A frozen 73-block real development ablation found no incremental
/// win over [`CoordinateProvider`] after canonical Huffman became available,
/// so the default catalogue does not pay its additional search cost.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CoordinateBasisProvider {
    pub discovery: DiscoveryConfig,
    pub functions: FunctionSearchConfig,
}

impl WholeBlockCandidateProvider for CoordinateBasisProvider {
    fn provider_id(&self) -> &'static str {
        // Whole-block providers are visited in canonical provider-ID order.
        // This stable name deliberately places the bounded basis search after
        // the universal entropy and exact whole-function opportunities.
        "mathsvg-whole-coordinate-basis"
    }

    fn search(
        &mut self,
        block: &[u8],
        context: ProviderContext,
        limits: &Limits,
    ) -> Result<WholeBlockProviderSearch> {
        if context.candidates_remaining == 0 || context.work_remaining == 0 {
            return Ok(whole_block_budget_stop(1, 0));
        }

        let discovery = discover_candidates(block, limits, &self.discovery)?;
        let mut represented_descriptors =
            (discovery.candidates.len() + discovery.events.len()) as u64;
        if discovery.probe_work > context.work_remaining {
            return Ok(whole_block_budget_stop(represented_descriptors.max(1), 0));
        }

        let mut work_used = discovery.probe_work;
        // The optimizer charges candidate validation/evaluation after this
        // provider returns. Reserve that exact declared decode work while
        // searching so every emitted candidate can actually be verified
        // within the context supplied to this call.
        let mut verification_work_reserved = 0u64;
        let mut complete = discovery.events.is_empty();
        let mut budget_exhausted = discovery
            .events
            .iter()
            .any(|event| event.event == DiscoveryEventKind::BudgetStop);
        let mut candidates = discovery.candidates;
        candidates.sort_by(|left, right| {
            coordinate_basis_search_priority(&left.descriptor)
                .cmp(&coordinate_basis_search_priority(&right.descriptor))
                .then_with(|| left.descriptor.cmp(&right.descriptor))
        });

        let mut provided = Vec::new();
        for coordinate in candidates {
            if coordinate_basis_is_dominated(&coordinate.descriptor) {
                // Identity already has the base providers. A one-byte
                // BYTE_PLANE is byte-identical to identity while adding a
                // coordinate record and inverse work, so it is strictly
                // dominated for every child representation.
                continue;
            }
            if provided.len() as u64 >= context.candidates_remaining {
                complete = false;
                budget_exhausted = true;
                break;
            }

            let transform_work = coordinate_forward_work(&coordinate.descriptor)?;
            if !basis_work_fits(
                work_used,
                verification_work_reserved,
                transform_work,
                context.work_remaining,
            )? {
                complete = false;
                budget_exhausted = true;
                break;
            }
            work_used =
                checked_u64_add(work_used, transform_work, "coordinate basis transform work")?;
            let transformed = coordinate_forward(block, &coordinate.descriptor, limits)?;
            let partitions =
                coordinate_basis_partitions(&coordinate.descriptor, transformed.len())?;

            let mut children = Vec::with_capacity(partitions.len());
            let mut boundaries = Vec::with_capacity(partitions.len().saturating_sub(1));
            let mut parameters = vec![coordinate.origin as u8 as u64, partitions.len() as u64];
            let mut descriptor_complete = true;

            for (partition_index, &(start, end)) in partitions.iter().enumerate() {
                let available = context
                    .work_remaining
                    .saturating_sub(work_used)
                    .saturating_sub(verification_work_reserved);
                let remaining_partitions = (partitions.len() - partition_index) as u64;
                // Fair deterministic allocation prevents an early plane from
                // consuming work required merely to decide later planes.
                let fair_share = available / remaining_partitions;
                let mut function_config = self.functions.clone();
                function_config.work_budget = function_config.work_budget.min(fair_share);
                let searched = search_block(&transformed[start..end], &function_config, limits)?;
                represented_descriptors = checked_u64_add(
                    represented_descriptors,
                    represented_function_descriptors(&searched)?,
                    "coordinate basis represented descriptors",
                )?;
                work_used = checked_u64_add(
                    work_used,
                    searched.work_used,
                    "coordinate basis function work",
                )?;
                if !basis_work_fits(
                    work_used,
                    verification_work_reserved,
                    0,
                    context.work_remaining,
                )? {
                    return Err(Error::LimitExceeded {
                        what: "coordinate basis provider work",
                        actual: checked_u64_add(
                            work_used,
                            verification_work_reserved,
                            "coordinate basis accounted work",
                        )?,
                        limit: context.work_remaining,
                    });
                }
                complete &= searched.complete_within_declared_catalogue;
                budget_exhausted |= searched.budget_exhausted;
                descriptor_complete &= !searched.budget_exhausted;

                let winner = searched.winner;
                parameters.push(winner.key.family as u8 as u64);
                parameters.push(winner.key.parameters.len() as u64);
                parameters.extend(winner.key.parameters.iter().copied());
                children.push(program_child_without_definitions(winner.program)?);
                if partition_index + 1 < partitions.len() {
                    boundaries.push(end as u64);
                }
            }

            // Even a budget-limited nested catalogue returns its mandatory
            // exact literal upper bound. It remains a valid, honestly marked
            // candidate; `complete` above does not overclaim catalogue
            // completeness.
            let partitioned = Node::Split {
                boundaries,
                children,
            };
            let program = Program {
                definitions: Vec::new(),
                root: Node::File {
                    original_length: block.len() as u64,
                    child: Box::new(Node::Coordinate {
                        descriptor: coordinate.descriptor,
                        child: Box::new(partitioned),
                    }),
                },
            };
            let validation = program.validate(limits)?;
            let next_reserved = checked_u64_add(
                verification_work_reserved,
                validation.decode_work,
                "coordinate basis reserved verification work",
            )?;
            if !basis_work_fits(work_used, next_reserved, 0, context.work_remaining)? {
                complete = false;
                budget_exhausted = true;
                break;
            }
            verification_work_reserved = next_reserved;

            let breakdown = program.procedural_breakdown(limits)?;
            let attributed = checked_u64_add(
                breakdown.function_reconstructed_bytes,
                breakdown.literal_reconstructed_bytes,
                "coordinate basis source attribution",
            )?;
            if attributed != block.len() as u64 {
                return Err(Error::LengthMismatch {
                    context: "coordinate basis source attribution",
                    expected: block.len() as u64,
                    actual: attributed,
                });
            }
            let canonical_payload = program_payload(&program, limits)?;
            provided.push(ProvidedProgramCandidate {
                key: ProviderCandidateKey {
                    family: 2,
                    parameters,
                    canonical_payload,
                },
                class: CoverageClass::Coordinate,
                program,
                // ProgramBreakdown deliberately treats a coordinate output as
                // literal-dependent when any required plane is literal. This
                // is conservative and prevents independent entropy planes
                // from being mislabeled as generated source.
                class_source_bytes: breakdown.function_reconstructed_bytes,
                literal_source_bytes: breakdown.literal_reconstructed_bytes,
            });

            if !descriptor_complete {
                complete = false;
            }
        }

        Ok(WholeBlockProviderSearch {
            candidates: provided,
            work_used,
            represented_descriptors,
            complete_within_declared_catalogue: complete && !budget_exhausted,
            budget_exhausted,
        })
    }
}

fn coordinate_basis_is_dominated(descriptor: &CoordinateDescriptor) -> bool {
    matches!(
        descriptor.transform,
        CoordinateTransform::Identity | CoordinateTransform::BytePlane { word_width: 1, .. }
    )
}

/// Frozen deterministic search priority backed by the Phase 3 development
/// oracle: bit planes first, then byte stride lanes (including stride eight),
/// then byte planes, and finally wider stride elements.
fn coordinate_basis_search_priority(descriptor: &CoordinateDescriptor) -> (u8, u64, u64, u64) {
    match descriptor.transform {
        CoordinateTransform::BitPlane => (0, 0, 0, 0),
        CoordinateTransform::Stride {
            element_width: 1,
            channels,
        } => {
            let oracle_rank = match channels {
                8 => 0,
                16 => 1,
                4 => 2,
                3 => 3,
                2 => 4,
                6 => 5,
                _ => 6,
            };
            (1, oracle_rank, u64::from(channels), 0)
        }
        CoordinateTransform::BytePlane { word_width, endian } => {
            if word_width > 1 {
                (2, u64::from(u8::MAX - word_width), endian as u8 as u64, 0)
            } else {
                (4, 0, 0, 0)
            }
        }
        CoordinateTransform::Stride {
            element_width,
            channels,
        } => (3, u64::from(element_width), u64::from(channels), 0),
        CoordinateTransform::Identity => (4, 0, 0, 0),
    }
}

fn coordinate_forward_work(descriptor: &CoordinateDescriptor) -> Result<u64> {
    descriptor
        .inverse_work()?
        .checked_sub(1)
        .ok_or(Error::IntegerOverflow {
            context: "coordinate basis forward work",
        })
}

fn basis_work_fits(
    work_used: u64,
    verification_reserved: u64,
    additional_work: u64,
    limit: u64,
) -> Result<bool> {
    let total = checked_u64_add(
        checked_u64_add(
            work_used,
            verification_reserved,
            "coordinate basis accounted work",
        )?,
        additional_work,
        "coordinate basis accounted work",
    )?;
    Ok(total <= limit)
}

fn coordinate_basis_partitions(
    descriptor: &CoordinateDescriptor,
    transformed_length: usize,
) -> Result<Vec<(usize, usize)>> {
    let (count, partition_bytes) = match descriptor.transform {
        CoordinateTransform::Stride {
            element_width,
            channels,
        } => {
            let record_bytes = usize::from(element_width)
                .checked_mul(usize::from(channels))
                .ok_or(Error::IntegerOverflow {
                    context: "coordinate basis stride record bytes",
                })?;
            let records = transformed_length / record_bytes;
            (
                usize::from(channels),
                records
                    .checked_mul(usize::from(element_width))
                    .ok_or(Error::IntegerOverflow {
                        context: "coordinate basis stride partition bytes",
                    })?,
            )
        }
        CoordinateTransform::BytePlane { word_width, .. } => (
            usize::from(word_width),
            transformed_length / usize::from(word_width),
        ),
        CoordinateTransform::BitPlane => (8, transformed_length / 8),
        CoordinateTransform::Identity => {
            return Err(Error::InvalidValue(
                "identity has no coordinate basis partitions",
            ))
        }
    };
    if count < 2 || partition_bytes == 0 {
        return Err(Error::InvalidValue(
            "coordinate basis requires non-empty independent partitions",
        ));
    }
    let covered = count
        .checked_mul(partition_bytes)
        .ok_or(Error::IntegerOverflow {
            context: "coordinate basis transformed coverage",
        })?;
    if covered != transformed_length {
        return Err(Error::LengthMismatch {
            context: "coordinate basis transformed coverage",
            expected: transformed_length as u64,
            actual: covered as u64,
        });
    }
    let mut partitions = Vec::with_capacity(count);
    for index in 0..count {
        let start = index
            .checked_mul(partition_bytes)
            .ok_or(Error::IntegerOverflow {
                context: "coordinate basis partition start",
            })?;
        let end = start
            .checked_add(partition_bytes)
            .ok_or(Error::IntegerOverflow {
                context: "coordinate basis partition end",
            })?;
        partitions.push((start, end));
    }
    Ok(partitions)
}

/// Explicit experimental adapter for algebraic residual decomposition.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ResidualProvider {
    pub config: ResidualConfig,
}

impl CandidateProvider for ResidualProvider {
    fn provider_id(&self) -> &'static str {
        "mathsvg-residual"
    }

    fn search(
        &mut self,
        interval: &[u8],
        context: ProviderContext,
        limits: &Limits,
    ) -> Result<ProviderSearch> {
        if context.candidates_remaining == 0 || context.work_remaining == 0 {
            return Ok(provider_budget_stop());
        }
        let mut config = self.config.clone();
        config.work_budget = config.work_budget.min(context.work_remaining);
        config.function_search.work_budget =
            config.function_search.work_budget.min(config.work_budget);
        let searched = search_residual(interval, &config, limits)?;
        let represented_descriptors = searched.ledger.iter().try_fold(0u64, |sum, entry| {
            checked_u64_add(
                sum,
                entry.represented_descriptors.max(1),
                "residual represented descriptors",
            )
        })?;
        let mut candidates = Vec::new();
        if searched.winner.residual_depth != 0 {
            let canonical_payload = program_payload(&searched.winner.program, limits)?;
            let child = program_child_without_definitions(searched.winner.program)?;
            candidates.push(ProvidedCandidate {
                key: ProviderCandidateKey {
                    family: 1,
                    parameters: vec![u64::from(searched.winner.residual_depth)],
                    canonical_payload,
                },
                class: CoverageClass::Residual,
                node: child,
            });
        }
        Ok(ProviderSearch {
            candidates,
            work_used: searched.work_used,
            represented_descriptors,
            complete_within_declared_catalogue: !searched.budget_exhausted
                && !searched.heuristic_omission,
            budget_exhausted: searched.budget_exhausted,
        })
    }
}

/// Explicit experimental adapter for residual-sensitive symbolic synthesis.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct SymbolicProvider {
    pub config: SymbolicConfig,
}

impl CandidateProvider for SymbolicProvider {
    fn provider_id(&self) -> &'static str {
        "mathsvg-symbolic"
    }

    fn search(
        &mut self,
        interval: &[u8],
        context: ProviderContext,
        limits: &Limits,
    ) -> Result<ProviderSearch> {
        if context.candidates_remaining == 0 || context.work_remaining == 0 {
            return Ok(provider_budget_stop());
        }
        let mut config = self.config.clone();
        config.work_budget = config.work_budget.min(context.work_remaining);
        config.residual_functions.work_budget = config
            .residual_functions
            .work_budget
            .min(config.work_budget);
        let searched = search_symbolic_block(interval, &config, limits)?;
        let represented_descriptors = searched.ledger.len() as u64;
        let mut candidates = Vec::new();
        if let Some(expression) = &searched.winner.expression_key {
            let canonical_payload = program_payload(&searched.winner.program, limits)?;
            let child = program_child_without_definitions(searched.winner.program)?;
            candidates.push(ProvidedCandidate {
                key: ProviderCandidateKey {
                    family: 1,
                    parameters: vec![u64::from(expression.depth), expression.family as u8 as u64],
                    canonical_payload,
                },
                class: CoverageClass::Symbolic,
                node: child,
            });
        }
        Ok(ProviderSearch {
            candidates,
            work_used: searched.work_used,
            represented_descriptors,
            complete_within_declared_catalogue: searched.complete_within_declared_catalogue,
            budget_exhausted: searched.budget_exhausted,
        })
    }
}

/// Whole-block native entropy candidate.
///
/// Running this provider before interval segmentation prevents a bounded
/// interval catalogue from consuming the work budget before the universal
/// general-purpose leaf has had one exact chance to compete.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct EntropyProvider {
    /// Optional add-only encoder policy. `None` is the byte-identical G1
    /// catalogue used by Fast; opcode `0x07` and its decoder never change.
    pub lz_huffman_policy: Option<LzParserPolicy>,
}

impl WholeBlockCandidateProvider for EntropyProvider {
    fn provider_id(&self) -> &'static str {
        "mathsvg-entropy"
    }

    fn search(
        &mut self,
        block: &[u8],
        context: ProviderContext,
        _limits: &Limits,
    ) -> Result<WholeBlockProviderSearch> {
        const REPRESENTED_DESCRIPTORS: u64 = 1;

        if context.candidates_remaining == 0 {
            return Ok(whole_block_budget_stop(REPRESENTED_DESCRIPTORS, 0));
        }
        let baseline_search_work = checked_u64_mul(
            block.len() as u64,
            LeafCodec::ALL.len() as u64 + 1,
            "whole-block native entropy search work",
        )?;
        if baseline_search_work > context.work_remaining {
            return Ok(whole_block_budget_stop(REPRESENTED_DESCRIPTORS, 0));
        }

        let mut encoding = encode_best(block).map_err(map_entropy_error)?;
        let baseline_verification_work = checked_u64_add(
            encoding.score.decode_work,
            2,
            "whole-block entropy verification work",
        )?;
        let baseline_required_work = checked_u64_add(
            baseline_search_work,
            baseline_verification_work,
            "whole-block entropy required work",
        )?;
        if baseline_required_work > context.work_remaining {
            return Ok(whole_block_budget_stop(
                REPRESENTED_DESCRIPTORS,
                baseline_search_work,
            ));
        }

        let mut search_work = baseline_search_work;
        let mut policy_incomplete = false;
        let mut policy_budget_exhausted = false;
        let mut policy_selected = false;
        if let Some(policy) = self.lz_huffman_policy {
            let per_walk_bound = checked_u64_mul(
                block.len() as u64,
                policy.maximum_work_per_input_byte(),
                "whole-block LZ policy work bound",
            )?;
            let policy_work_bound =
                checked_u64_mul(per_walk_bound, 2, "whole-block LZ policy work bound")?;
            // An admitted policy can win only with fewer complete envelope
            // bytes than the G1 catalogue winner. This is therefore a safe
            // upper bound for its payload-sensitive opcode-0x07 decode work.
            let policy_verification_bound = checked_u64_add(
                checked_u64_add(
                    checked_u64_mul(
                        block.len() as u64,
                        3,
                        "whole-block LZ policy verification bound",
                    )?,
                    checked_u64_mul(
                        encoding.score.encoded_bytes,
                        8,
                        "whole-block LZ policy verification bound",
                    )?,
                    "whole-block LZ policy verification bound",
                )?,
                131_073,
                "whole-block LZ policy verification bound",
            )?;
            let verification_bound = baseline_verification_work.max(policy_verification_bound);
            let policy_required_bound = checked_u64_add(
                checked_u64_add(
                    baseline_search_work,
                    policy_work_bound,
                    "whole-block LZ policy required work bound",
                )?,
                verification_bound,
                "whole-block LZ policy required work bound",
            )?;
            if policy_required_bound <= context.work_remaining {
                let experimental =
                    encode_lz_huffman_policy_if_better(block, policy, encoding.score)
                        .map_err(map_entropy_error)?;
                let per_walk_work = experimental
                    .analysis
                    .stats
                    .work_units()
                    .map_err(map_entropy_error)?;
                // Keep the original eager encoder's deterministic work
                // charge. The conditional emitter changes wall time only.
                let policy_walks = if experimental.analysis.score.is_some() {
                    2
                } else {
                    1
                };
                let policy_work = checked_u64_mul(
                    per_walk_work,
                    policy_walks,
                    "whole-block LZ policy actual work",
                )?;
                search_work = checked_u64_add(
                    search_work,
                    policy_work,
                    "whole-block entropy actual search work",
                )?;
                policy_budget_exhausted = experimental.analysis.stats.budget_exhausted;
                policy_incomplete = policy_budget_exhausted;
                if experimental.selected {
                    policy_selected = true;
                    encoding = mathsvg_entropy::BestEncoding {
                        codec: LeafCodec::LzHuffman,
                        score: experimental.analysis.score.ok_or(Error::InvalidValue(
                            "selected LZ parser policy has no score",
                        ))?,
                        bytes: experimental.bytes.ok_or(Error::InvalidValue(
                            "selected LZ parser policy has no envelope",
                        ))?,
                    };
                }
            } else {
                // G1 remains available, but the configured add-only catalogue
                // was not complete inside this provider budget.
                policy_incomplete = true;
                policy_budget_exhausted = true;
            }
        }

        // The optimizer boundary charges the leaf's exact declared decode work
        // and one FILE operation when it independently validates the emitted
        // candidate. Reserve that work here so a candidate is returned only
        // when the caller can verify it, but do not report it as provider
        // search work or the generic whole-block path would charge it twice.
        let verification_work = checked_u64_add(
            encoding.score.decode_work,
            2,
            "whole-block entropy verification work",
        )?;
        let required_work = checked_u64_add(
            search_work,
            verification_work,
            "whole-block entropy required work",
        )?;
        if required_work > context.work_remaining {
            return Ok(whole_block_budget_stop(
                REPRESENTED_DESCRIPTORS,
                search_work,
            ));
        }

        let mut parameters = vec![u64::from(encoding.codec.opcode())];
        parameters.extend(
            encoding
                .score
                .parameter_payload
                .as_bytes()
                .iter()
                .map(|byte| u64::from(*byte)),
        );
        let policy_audit = match self.lz_huffman_policy {
            None => "configured=G1;effective=G1".to_owned(),
            Some(policy) => format!(
                "configured=G1+{};effective={}",
                policy.as_str(),
                if policy_selected {
                    policy.as_str()
                } else {
                    "G1"
                }
            ),
        };
        Ok(WholeBlockProviderSearch {
            candidates: vec![ProvidedProgramCandidate {
                key: ProviderCandidateKey {
                    family: 1,
                    parameters,
                    canonical_payload: policy_audit.into_bytes(),
                },
                class: CoverageClass::Literal,
                program: Program {
                    definitions: Vec::new(),
                    root: Node::File {
                        original_length: block.len() as u64,
                        child: Box::new(Node::EntropyLiteral(encoding.bytes)),
                    },
                },
                class_source_bytes: 0,
                literal_source_bytes: block.len() as u64,
            }],
            work_used: search_work,
            represented_descriptors: REPRESENTED_DESCRIPTORS,
            complete_within_declared_catalogue: !policy_incomplete,
            budget_exhausted: policy_budget_exhausted,
        })
    }
}

/// Whole-block adapter for the exact native function catalogue.
///
/// The interval provider remains useful for mixed blocks, but exact generators
/// that span a complete block must not wait behind quadratic interval
/// enumeration. Literal and entropy winners are omitted here because those
/// complete-block candidates already have dedicated competition paths.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct WholeBlockFunctionsProvider {
    pub config: FunctionSearchConfig,
}

impl WholeBlockCandidateProvider for WholeBlockFunctionsProvider {
    fn provider_id(&self) -> &'static str {
        "mathsvg-functions-whole"
    }

    fn search(
        &mut self,
        block: &[u8],
        context: ProviderContext,
        limits: &Limits,
    ) -> Result<WholeBlockProviderSearch> {
        if context.candidates_remaining == 0 || context.work_remaining == 0 {
            return Ok(whole_block_budget_stop(1, 0));
        }

        let mut config = self.config.clone();
        config.work_budget = config.work_budget.min(context.work_remaining);
        // Native entropy has its own whole-block provider and competes with
        // this candidate at the optimizer boundary. Seed function fitting
        // with the exact literal upper bound so this adapter neither executes
        // nor charges the same entropy catalogue a second time.
        let seed = SearchSeed::literal_only(block, limits)?;
        let searched = search_block_seeded(&seed, &config, SearchMode::NonLiteral)?;
        let represented_descriptors = represented_function_ledger(&searched.ledger)?;
        let work_used = searched.work_used;
        let complete = searched.complete_within_declared_catalogue;
        let budget_exhausted = searched.budget_exhausted;
        let Some(winner) = searched.winner else {
            return Ok(WholeBlockProviderSearch {
                candidates: Vec::new(),
                work_used,
                represented_descriptors,
                complete_within_declared_catalogue: complete,
                budget_exhausted,
            });
        };

        if matches!(
            winner.key.family,
            CandidateFamily::Literal | CandidateFamily::EntropyLiteral
        ) {
            return Err(Error::InvalidValue(
                "non-literal function search returned a literal winner",
            ));
        }

        let validation = winner.program.validate(limits)?;
        let required_work = checked_u64_add(
            work_used,
            validation.decode_work,
            "whole-block function required work",
        )?;
        if required_work > context.work_remaining {
            return Ok(whole_block_budget_stop(
                represented_descriptors.max(1),
                work_used,
            ));
        }
        let breakdown = winner.program.procedural_breakdown(limits)?;
        let canonical_payload = program_payload(&winner.program, limits)?;
        Ok(WholeBlockProviderSearch {
            candidates: vec![ProvidedProgramCandidate {
                key: ProviderCandidateKey {
                    family: winner.key.family as u8 as u32,
                    parameters: winner.key.parameters,
                    canonical_payload,
                },
                class: CoverageClass::Function,
                program: winner.program,
                class_source_bytes: breakdown.function_reconstructed_bytes,
                literal_source_bytes: breakdown.literal_reconstructed_bytes,
            }],
            work_used,
            represented_descriptors,
            complete_within_declared_catalogue: complete,
            budget_exhausted,
        })
    }
}

/// Whole-block adapter for exact definition activation search.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct GraphProvider {
    pub config: GraphConfig,
}

impl WholeBlockCandidateProvider for GraphProvider {
    fn provider_id(&self) -> &'static str {
        "mathsvg-graph"
    }

    fn search(
        &mut self,
        block: &[u8],
        context: ProviderContext,
        limits: &Limits,
    ) -> Result<WholeBlockProviderSearch> {
        if context.candidates_remaining == 0 || context.work_remaining == 0 {
            return Ok(WholeBlockProviderSearch {
                candidates: Vec::new(),
                work_used: 0,
                represented_descriptors: 0,
                complete_within_declared_catalogue: false,
                budget_exhausted: true,
            });
        }
        let mut config = self.config.clone();
        config.work_budget = config.work_budget.min(context.work_remaining);
        let searched = search_shared_block(block, &config, limits)?;
        let represented_descriptors = searched.ledger.iter().try_fold(0u64, |sum, entry| {
            checked_u64_add(
                sum,
                entry.represented_subsets,
                "graph represented descriptors",
            )
        })?;
        let mut candidates = Vec::new();
        if searched.winner.active_definitions != 0 {
            let graph_bytes = searched.winner.referenced_source_bytes;
            let literal_bytes =
                (block.len() as u64)
                    .checked_sub(graph_bytes)
                    .ok_or(Error::LengthMismatch {
                        context: "graph source attribution",
                        expected: block.len() as u64,
                        actual: graph_bytes,
                    })?;
            candidates.push(ProvidedProgramCandidate {
                key: ProviderCandidateKey {
                    family: 1,
                    parameters: vec![
                        searched.winner.activation_mask,
                        u64::from(searched.winner.active_definitions),
                        graph_bytes,
                    ],
                    canonical_payload: program_payload(&searched.winner.program, limits)?,
                },
                class: CoverageClass::Graph,
                program: searched.winner.program,
                class_source_bytes: graph_bytes,
                literal_source_bytes: literal_bytes,
            });
        }
        Ok(WholeBlockProviderSearch {
            candidates,
            work_used: searched.work_used,
            represented_descriptors,
            complete_within_declared_catalogue: searched.complete_within_declared_catalogue,
            budget_exhausted: searched.budget_exhausted,
        })
    }
}

/// Frozen optimizer profile and deterministic hard caps.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OptimizerConfig {
    /// Candidate archive block size. The universal literal archive still uses
    /// the format's canonical 16 MiB partition.
    pub block_bytes: u32,
    /// Every interval boundary is `0`, a multiple of this value, or the final
    /// byte of its block.
    pub microblock_bytes: u32,
    pub max_segments_per_block: u32,
    /// Maximum number of DP states created, including each block's source
    /// state. Replaced or rejected states are still counted as work, not time.
    pub max_states: u64,
    /// Maximum verified interval candidates. Mandatory whole-archive and
    /// whole-block literal upper bounds are not charged to this search cap.
    pub max_candidates: u64,
    /// Provider fitting, candidate verification and DP transition work.
    pub work_budget: u64,
    /// Includes the universal literal row and the reserved terminal stop row.
    pub max_ledger_entries: u64,
    pub functions: FunctionSearchConfig,
}

impl Default for OptimizerConfig {
    fn default() -> Self {
        Self {
            block_bytes: 64 * 1024,
            microblock_bytes: 1024,
            max_segments_per_block: 16,
            max_states: 1 << 16,
            max_candidates: 1 << 13,
            work_budget: 1 << 28,
            max_ledger_entries: 1 << 14,
            functions: FunctionSearchConfig {
                max_period: 32,
                max_recurrence_order: 2,
                recurrence_coefficients: vec![0, 1, 255],
                max_exceptions: 64,
                work_budget: 1 << 20,
                max_ledger_entries: 1024,
            },
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum PortfolioProfile {
    Fast,
    Balanced,
    Max,
    Structured,
    Repository,
}

/// Runtime engines with a real built-in candidate path and therefore a
/// meaningful enable/disable ablation.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum PortfolioAlgorithm {
    WholeBlockEntropy,
    WholeBlockFunctions,
    IntervalFunctions,
    Coordinates,
}

impl PortfolioAlgorithm {
    pub const ALL: [Self; 4] = [
        Self::WholeBlockEntropy,
        Self::WholeBlockFunctions,
        Self::IntervalFunctions,
        Self::Coordinates,
    ];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::WholeBlockEntropy => "whole-block-entropy",
            Self::WholeBlockFunctions => "whole-block-functions",
            Self::IntervalFunctions => "interval-functions",
            Self::Coordinates => "coordinates",
        }
    }
}

/// Frozen feature switches and engine-local deterministic budgets.
///
/// Phase 3 found coordinate headroom, but the paired Balanced production
/// ablation found zero archive-byte effect and a material search-time cost for
/// interval functions plus coordinates. Those engines remain enabled in the
/// deeper profiles and are pruned from Balanced. Residual emission remains
/// experimental: its qualifying
/// 4 KiB oracle win produced exactly zero whole-file archive gain in the
/// paired production ablation. Symbolic and graph emission likewise remain
/// explicit experiments because their non-synthetic oracle gates did not pass.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PortfolioConfig {
    pub profile: PortfolioProfile,
    pub optimizer: OptimizerConfig,
    pub entropy: EntropyProvider,
    pub enable_whole_block_entropy: bool,
    pub enable_whole_block_functions: bool,
    pub enable_interval_functions: bool,
    pub enable_coordinates: bool,
    pub enable_residual: bool,
    pub enable_symbolic: bool,
    pub enable_graph: bool,
    pub coordinates: CoordinateProvider,
    pub residual: ResidualProvider,
    pub symbolic: SymbolicProvider,
    pub graph: GraphProvider,
}

impl Default for PortfolioConfig {
    fn default() -> Self {
        Self::for_profile(PortfolioProfile::Balanced)
    }
}

impl PortfolioConfig {
    pub fn for_profile(profile: PortfolioProfile) -> Self {
        let mut optimizer = OptimizerConfig::default();
        match profile {
            PortfolioProfile::Fast => {
                optimizer.block_bytes = 1024 * 1024;
                optimizer.microblock_bytes = 4096;
                optimizer.max_segments_per_block = 8;
                optimizer.max_states = 4096;
                optimizer.max_candidates = 256;
                // A full 1 MiB block can complete the eight-codec entropy
                // count, winner emission, and the payload-sensitive
                // LZ-Huffman verification bound, while retaining work for
                // one exact whole-block function candidate.
                optimizer.work_budget = 24_000_000;
                optimizer.max_ledger_entries = 1024;
            }
            PortfolioProfile::Balanced => {
                optimizer.block_bytes = 1024 * 1024;
                optimizer.microblock_bytes = 4096;
                optimizer.max_segments_per_block = 16;
                optimizer.max_states = 1 << 16;
                optimizer.max_candidates = 4096;
                // C8Lazy's two-walk worst-case preflight is about 73.6M
                // work units for a full 1 MiB block, including the G1
                // catalogue and verification envelope. Keep a bounded margin
                // for the exact whole-block function provider.
                optimizer.work_budget = 80_000_000;
                optimizer.max_ledger_entries = 1 << 14;
            }
            PortfolioProfile::Max => {
                optimizer.block_bytes = 2 * 1024 * 1024;
                optimizer.microblock_bytes = 4096;
                optimizer.max_segments_per_block = 64;
                optimizer.max_states = 1 << 18;
                optimizer.max_candidates = 65_536;
                optimizer.work_budget = 1_000_000_000;
                optimizer.max_ledger_entries = 1 << 17;
            }
            PortfolioProfile::Structured => {
                optimizer.block_bytes = 1024 * 1024;
                optimizer.microblock_bytes = 4096;
                optimizer.max_segments_per_block = 32;
                optimizer.max_states = 1 << 17;
                optimizer.max_candidates = 16_384;
                optimizer.work_budget = 300_000_000;
                optimizer.max_ledger_entries = 1 << 16;
            }
            PortfolioProfile::Repository => {
                optimizer.block_bytes = 1024 * 1024;
                optimizer.microblock_bytes = 4096;
                optimizer.max_segments_per_block = 32;
                optimizer.max_states = 1 << 18;
                optimizer.max_candidates = 65_536;
                optimizer.work_budget = 1_000_000_000;
                optimizer.max_ledger_entries = 1 << 17;
            }
        }
        if matches!(
            profile,
            PortfolioProfile::Max | PortfolioProfile::Structured
        ) {
            // The diagnostic/structured profiles must reach the exact
            // generators frozen in the development corpus. An 8-bit maximal
            // LFSR is byte-periodic at period 255. A quadratic modulo-256
            // sequence is the order-three recurrence [3, -3, 1]. These remain
            // a finite deterministic catalogue; Fast/Balanced are unchanged.
            optimizer.functions.max_period = 256;
            optimizer.functions.max_recurrence_order = 3;
            optimizer.functions.recurrence_coefficients = vec![0, 1, 3, 253, 255];
            optimizer.functions.work_budget = 96_000_000;
            optimizer.functions.max_ledger_entries = 2048;
        }
        let functions = optimizer.functions.clone();
        let (coordinate_candidates, coordinate_work) = match profile {
            PortfolioProfile::Fast | PortfolioProfile::Repository => (16, 1 << 16),
            PortfolioProfile::Balanced => (32, 1 << 18),
            PortfolioProfile::Max | PortfolioProfile::Structured => (64, 1 << 20),
        };
        let coordinate_functions = FunctionSearchConfig {
            max_period: functions.max_period.min(16),
            max_recurrence_order: 0,
            recurrence_coefficients: Vec::new(),
            max_exceptions: functions.max_exceptions.min(32),
            work_budget: functions.work_budget.min(coordinate_work),
            max_ledger_entries: functions.max_ledger_entries.min(256),
        };
        let entropy = EntropyProvider {
            lz_huffman_policy: match profile {
                PortfolioProfile::Fast => None,
                PortfolioProfile::Balanced
                | PortfolioProfile::Max
                | PortfolioProfile::Structured
                | PortfolioProfile::Repository => Some(LzParserPolicy::C8Lazy),
            },
        };
        // The development oracle found no incremental coordinate win in
        // 73/73 real blocks, and bounded segmentation found no real win at
        // the 0.5% retention threshold.  The frozen Max X-ray ablation then
        // produced an identical archive with these providers disabled while
        // reducing compression time by more than 4x.  Keep them available in
        // Structured/Repository experiments, but do not spend the default
        // general-purpose Balanced or Max budget on a rejected search path.
        let prune_general_interval_search =
            matches!(profile, PortfolioProfile::Balanced | PortfolioProfile::Max);
        Self {
            profile,
            optimizer,
            entropy,
            enable_whole_block_entropy: true,
            enable_whole_block_functions: true,
            enable_interval_functions: !prune_general_interval_search,
            enable_coordinates: !prune_general_interval_search,
            enable_residual: false,
            enable_symbolic: false,
            enable_graph: false,
            coordinates: CoordinateProvider {
                discovery: DiscoveryConfig {
                    max_candidates: coordinate_candidates,
                    max_channels: 16,
                    max_probe_lag: 64,
                    max_peak_lags: 8,
                    max_probe_bytes: 8192,
                },
                functions: coordinate_functions,
            },
            residual: ResidualProvider {
                config: ResidualConfig::default(),
            },
            symbolic: SymbolicProvider {
                config: SymbolicConfig::default(),
            },
            graph: GraphProvider {
                config: GraphConfig::default(),
            },
        }
    }

    /// Explicit ablation profile. Calling this method is the opt-in that
    /// crosses the current residual/symbolic/graph oracle emission gates.
    pub fn experimental_all(profile: PortfolioProfile) -> Self {
        let mut config = Self::for_profile(profile);
        config.enable_residual = true;
        config.residual.config.gate = ArplGate::Experimental;
        config.residual.config.max_depth = 2;
        config.enable_symbolic = true;
        config.enable_graph = true;
        config
    }

    /// Enable or disable one engine that has a real built-in candidate path.
    ///
    /// Budgets are intentionally held constant so paired ablation runs differ
    /// only by catalogue membership.
    pub fn set_algorithm_enabled(&mut self, algorithm: PortfolioAlgorithm, enabled: bool) {
        match algorithm {
            PortfolioAlgorithm::WholeBlockEntropy => {
                self.enable_whole_block_entropy = enabled;
            }
            PortfolioAlgorithm::WholeBlockFunctions => {
                self.enable_whole_block_functions = enabled;
            }
            PortfolioAlgorithm::IntervalFunctions => {
                self.enable_interval_functions = enabled;
            }
            PortfolioAlgorithm::Coordinates => {
                self.enable_coordinates = enabled;
            }
        }
    }

    pub const fn algorithm_enabled(&self, algorithm: PortfolioAlgorithm) -> bool {
        match algorithm {
            PortfolioAlgorithm::WholeBlockEntropy => self.enable_whole_block_entropy,
            PortfolioAlgorithm::WholeBlockFunctions => self.enable_whole_block_functions,
            PortfolioAlgorithm::IntervalFunctions => self.enable_interval_functions,
            PortfolioAlgorithm::Coordinates => self.enable_coordinates,
        }
    }
}

/// Canonical global descriptor written into the optimizer audit ledger.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct CandidateDescriptor {
    pub provider: String,
    pub key: ProviderCandidateKey,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum LedgerEvent {
    Tried,
    SafePrune,
    HeuristicSkip,
    BudgetStop,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum LedgerReason {
    UniversalLiteralUpperBound,
    BlockLiteralUpperBound,
    ProviderSearchComplete,
    ProviderCatalogueIncomplete,
    ProviderBudget,
    EvaluatedExact,
    SegmentLimit,
    CandidateBudget,
    StateBudget,
    WorkBudget,
    LedgerBudget,
    CandidateArchiveLimit,
}

/// One stable, machine-readable search event.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IntervalLedgerEntry {
    pub candidate_id: u64,
    pub block_index: Option<u64>,
    pub global_start: u64,
    pub global_end: u64,
    pub descriptor: CandidateDescriptor,
    pub event: LedgerEvent,
    pub reason: LedgerReason,
    pub exact_node_bytes: Option<u64>,
    pub actual_interval_archive_bytes: Option<u64>,
    pub admissible_lower_bound_bytes: Option<u64>,
    pub upper_bound_at_decision: u64,
    pub work_before: u64,
    pub work_charged: u64,
    pub represented_descriptors: u64,
    pub selected: bool,
}

/// Selected source-byte and exact wire-byte accounting.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CoverageBreakdown {
    pub original_bytes: u64,
    pub archive_bytes: u64,
    pub literal_archive_bytes: u64,
    pub bytes_saved_vs_literal: u64,
    pub block_count: u64,
    pub segment_count: u64,
    pub literal_segments: u64,
    pub function_segments: u64,
    pub coordinate_segments: u64,
    pub residual_segments: u64,
    pub graph_segments: u64,
    pub symbolic_segments: u64,
    pub other_segments: u64,
    pub literal_source_bytes: u64,
    pub function_source_bytes: u64,
    pub coordinate_source_bytes: u64,
    pub residual_source_bytes: u64,
    pub graph_source_bytes: u64,
    pub symbolic_source_bytes: u64,
    pub other_source_bytes: u64,
    /// Sum of exact serialized selected leaf-node records.
    pub leaf_node_bytes: u64,
    /// Exact complete archive bytes minus selected leaf records.
    pub envelope_and_topology_bytes: u64,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ArchiveSelection {
    LiteralFallback,
    Procedural,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct SearchUsage {
    pub candidates: u64,
    pub states: u64,
    pub work: u64,
    pub ledger_entries: u64,
    pub budget_exhausted: bool,
    pub complete_within_declared_catalogue: bool,
}

/// Complete optimizer output. `archive` is always a real v1 archive and
/// `programs` are exactly the programs parsed back from that archive.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OptimizationResult {
    pub archive: Vec<u8>,
    pub programs: Vec<Program>,
    pub selection: ArchiveSelection,
    pub cost: CandidateCost,
    pub literal_cost: CandidateCost,
    pub literal_archive_bytes: u64,
    pub ledger: Vec<IntervalLedgerEntry>,
    pub coverage: CoverageBreakdown,
    pub usage: SearchUsage,
    pub verified: VerifiedArchive,
}

#[derive(Clone, Debug)]
struct EdgeMetrics {
    node_bytes: u64,
}

#[derive(Clone, Debug)]
struct IntervalEdge {
    descriptor: CandidateDescriptor,
    class: Option<CoverageClass>,
    node: Node,
    start: u64,
    end: u64,
    metrics: EdgeMetrics,
    ledger_id: Option<u64>,
}

#[derive(Clone, Debug, Default)]
struct AdditiveWireCost {
    /// Serialized child-node records, excluding FILE/SPLIT framing.
    child_node_bytes: u64,
    /// Canonical uLEB128 bytes for every SPLIT boundary.
    boundary_bytes: u64,
}

#[derive(Clone, Debug, Default)]
struct DpState {
    edges: Vec<IntervalEdge>,
    wire: AdditiveWireCost,
}

impl DpState {
    fn extend(&self, edge: &IntervalEdge) -> Result<Self> {
        let mut next = self.clone();
        next.wire.child_node_bytes = checked_u64_add(
            next.wire.child_node_bytes,
            edge.metrics.node_bytes,
            "segmentation child-node bytes",
        )?;
        if !next.edges.is_empty() {
            next.wire.boundary_bytes = checked_u64_add(
                next.wire.boundary_bytes,
                encoded_len(edge.start) as u64,
                "segmentation boundary bytes",
            )?;
        }
        next.edges.push(edge.clone());
        Ok(next)
    }

    fn program(&self, block_length: usize) -> Result<Program> {
        let child = match self.edges.as_slice() {
            [] => {
                return Err(Error::InvalidValue(
                    "empty DP state cannot form a non-empty block",
                ))
            }
            [edge] => edge.node.clone(),
            edges => {
                let boundaries = edges[..edges.len() - 1]
                    .iter()
                    .map(|edge| edge.end)
                    .collect();
                let children = edges.iter().map(|edge| edge.node.clone()).collect();
                Node::Split {
                    boundaries,
                    children,
                }
            }
        };
        Ok(file_program(block_length, child))
    }

    fn predicted_root_bytes(&self, block_length: usize) -> Result<u64> {
        let inner = if self.edges.len() == 1 {
            self.wire.child_node_bytes
        } else {
            let boundary_count = self.edges.len() - 1;
            let split_payload = [
                encoded_len(boundary_count as u64) as u64,
                self.wire.boundary_bytes,
                encoded_len(self.edges.len() as u64) as u64,
                self.wire.child_node_bytes,
            ]
            .into_iter()
            .try_fold(0u64, |sum, value| {
                checked_u64_add(sum, value, "SPLIT payload bytes")
            })?;
            node_record_bytes(split_payload)?
        };
        let file_payload = checked_u64_add(
            encoded_len(block_length as u64) as u64,
            inner,
            "FILE payload bytes",
        )?;
        node_record_bytes(file_payload)
    }
}

#[derive(Clone, Debug)]
struct BlockChoice {
    program: Program,
    edges: Vec<IntervalEdge>,
    literal_ledger_id: Option<u64>,
    whole_block: Option<WholeBlockSelection>,
    order: Vec<CandidateDescriptor>,
}

#[derive(Clone, Debug)]
struct WholeBlockSelection {
    class: CoverageClass,
    class_source_bytes: u64,
    literal_source_bytes: u64,
    ledger_id: Option<u64>,
}

struct SearchSession<'a> {
    config: &'a OptimizerConfig,
    literal_archive_bytes: u64,
    ledger: Vec<IntervalLedgerEntry>,
    candidates: u64,
    states: u64,
    work: u64,
    stopped: bool,
    any_budget_exhausted: bool,
    complete: bool,
}

impl<'a> SearchSession<'a> {
    fn new(config: &'a OptimizerConfig, input_length: u64, literal_archive_bytes: u64) -> Self {
        let literal = IntervalLedgerEntry {
            candidate_id: 0,
            block_index: None,
            global_start: 0,
            global_end: input_length,
            descriptor: internal_descriptor("literal-archive", 0),
            event: LedgerEvent::Tried,
            reason: LedgerReason::UniversalLiteralUpperBound,
            exact_node_bytes: None,
            actual_interval_archive_bytes: Some(literal_archive_bytes),
            admissible_lower_bound_bytes: None,
            upper_bound_at_decision: literal_archive_bytes,
            work_before: 0,
            work_charged: 0,
            represented_descriptors: 1,
            selected: true,
        };
        Self {
            config,
            literal_archive_bytes,
            ledger: vec![literal],
            candidates: 0,
            states: 0,
            work: 0,
            stopped: false,
            any_budget_exhausted: false,
            complete: true,
        }
    }

    fn next_id(&self) -> u64 {
        self.ledger.len() as u64
    }

    fn work_remaining(&self) -> u64 {
        self.config.work_budget.saturating_sub(self.work)
    }

    fn candidates_remaining(&self) -> u64 {
        self.config.max_candidates.saturating_sub(self.candidates)
    }

    fn stop(
        &mut self,
        reason: LedgerReason,
        block_index: Option<u64>,
        global_start: u64,
        global_end: u64,
    ) {
        if self.stopped {
            return;
        }
        self.stopped = true;
        self.any_budget_exhausted = true;
        self.complete = false;
        if (self.ledger.len() as u64) < self.config.max_ledger_entries {
            let candidate_id = self.next_id();
            self.ledger.push(IntervalLedgerEntry {
                candidate_id,
                block_index,
                global_start,
                global_end,
                descriptor: internal_descriptor("optimizer", reason as u32),
                event: LedgerEvent::BudgetStop,
                reason,
                exact_node_bytes: None,
                actual_interval_archive_bytes: None,
                admissible_lower_bound_bytes: None,
                upper_bound_at_decision: self.literal_archive_bytes,
                work_before: self.work,
                work_charged: 0,
                represented_descriptors: 1,
                selected: false,
            });
        }
    }

    /// Reserve the last available row for a deterministic stop event.
    fn can_push(&mut self, block_index: Option<u64>, global_start: u64, global_end: u64) -> bool {
        if self.stopped {
            return false;
        }
        if (self.ledger.len() as u64).saturating_add(1) >= self.config.max_ledger_entries {
            self.stop(
                LedgerReason::LedgerBudget,
                block_index,
                global_start,
                global_end,
            );
            false
        } else {
            true
        }
    }

    fn push(&mut self, mut entry: IntervalLedgerEntry) -> Option<u64> {
        if !self.can_push(entry.block_index, entry.global_start, entry.global_end) {
            return None;
        }
        let id = self.next_id();
        entry.candidate_id = id;
        self.ledger.push(entry);
        Some(id)
    }

    fn charge_work(
        &mut self,
        amount: u64,
        block_index: Option<u64>,
        global_start: u64,
        global_end: u64,
    ) -> bool {
        let Some(next) = self.work.checked_add(amount) else {
            self.stop(
                LedgerReason::WorkBudget,
                block_index,
                global_start,
                global_end,
            );
            return false;
        };
        if next > self.config.work_budget {
            self.stop(
                LedgerReason::WorkBudget,
                block_index,
                global_start,
                global_end,
            );
            return false;
        }
        self.work = next;
        true
    }

    fn charge_candidate(&mut self, block_index: u64, global_start: u64, global_end: u64) -> bool {
        if self.candidates >= self.config.max_candidates {
            self.stop(
                LedgerReason::CandidateBudget,
                Some(block_index),
                global_start,
                global_end,
            );
            return false;
        }
        self.candidates += 1;
        true
    }

    fn charge_state(&mut self, block_index: u64, global_start: u64) -> bool {
        if self.states >= self.config.max_states {
            self.stop(
                LedgerReason::StateBudget,
                Some(block_index),
                global_start,
                global_start,
            );
            return false;
        }
        self.states += 1;
        true
    }

    fn usage(&self) -> SearchUsage {
        SearchUsage {
            candidates: self.candidates,
            states: self.states,
            work: self.work,
            ledger_entries: self.ledger.len() as u64,
            budget_exhausted: self.any_budget_exhausted,
            complete_within_declared_catalogue: self.complete && !self.stopped,
        }
    }
}

/// Optimize with the built-in finite function provider.
pub fn optimize(
    input: &[u8],
    config: &OptimizerConfig,
    limits: &Limits,
) -> Result<OptimizationResult> {
    let mut functions = FunctionsProvider {
        config: config.functions.clone(),
    };
    let mut providers: [&mut dyn CandidateProvider; 1] = [&mut functions];
    optimize_with_all_providers(input, config, limits, &mut providers, &mut [])
}

/// Optimize with an explicit finite provider set.
pub fn optimize_with_providers(
    input: &[u8],
    config: &OptimizerConfig,
    limits: &Limits,
    providers: &mut [&mut dyn CandidateProvider],
) -> Result<OptimizationResult> {
    optimize_with_all_providers(input, config, limits, providers, &mut [])
}

/// Run the oracle-gated built-in engine portfolio.
pub fn optimize_portfolio(
    input: &[u8],
    config: &PortfolioConfig,
    limits: &Limits,
) -> Result<OptimizationResult> {
    if config.enable_residual && config.residual.config.gate != ArplGate::Experimental {
        return Err(Error::InvalidValue(
            "residual portfolio emission requires explicit Experimental gate",
        ));
    }

    let mut functions = FunctionsProvider {
        config: config.optimizer.functions.clone(),
    };
    let mut coordinates = config.coordinates.clone();
    let mut residual = config.residual.clone();
    let mut symbolic = config.symbolic.clone();
    let mut entropy = config.entropy;
    let mut whole_functions = WholeBlockFunctionsProvider {
        config: config.optimizer.functions.clone(),
    };
    let mut graph = config.graph.clone();

    let mut providers: Vec<&mut dyn CandidateProvider> = Vec::new();
    if config.enable_interval_functions {
        providers.push(&mut functions);
    }
    if config.enable_coordinates {
        providers.push(&mut coordinates);
    }
    if config.enable_residual {
        providers.push(&mut residual);
    }
    if config.enable_symbolic {
        providers.push(&mut symbolic);
    }
    let mut whole_block_providers: Vec<&mut dyn WholeBlockCandidateProvider> = Vec::new();
    if config.enable_whole_block_entropy {
        whole_block_providers.push(&mut entropy);
    }
    if config.enable_whole_block_functions {
        whole_block_providers.push(&mut whole_functions);
    }
    if config.enable_graph {
        whole_block_providers.push(&mut graph);
    }
    optimize_with_all_providers(
        input,
        &config.optimizer,
        limits,
        &mut providers,
        &mut whole_block_providers,
    )
}

/// Optimize with explicit interval and whole-block provider sets.
///
/// Every emitted candidate is independently validated, evaluated, serialized
/// as a complete one-block archive and finally serialized once more as part of
/// the complete archive before it can defeat the universal literal bound.
pub fn optimize_with_all_providers(
    input: &[u8],
    config: &OptimizerConfig,
    limits: &Limits,
    providers: &mut [&mut dyn CandidateProvider],
    whole_block_providers: &mut [&mut dyn WholeBlockCandidateProvider],
) -> Result<OptimizationResult> {
    limits.check("output bytes", input.len() as u64, limits.max_output_bytes)?;

    // The real, complete universal upper bound is intentionally materialized
    // before optimizer-profile validation or any procedural search.
    let literal_archive = encode_literal_archive(input, limits)?;
    let literal_archive_bytes = literal_archive.len() as u64;
    validate_config(config, limits)?;
    let provider_order = canonical_provider_order(providers)?;
    let whole_block_provider_order = canonical_whole_block_provider_order(whole_block_providers)?;

    let (literal_programs, literal_verified) = decode_and_verify(&literal_archive, input, limits)?;
    let literal_cost = aggregate_archive_cost(&literal_programs, &literal_archive, limits)?;
    if literal_cost.archive_bytes != literal_archive_bytes {
        return Err(Error::InvalidValue(
            "literal archive cost disagrees with serialized length",
        ));
    }

    let mut session = SearchSession::new(config, input.len() as u64, literal_archive_bytes);
    let mut block_choices = Vec::new();
    if !input.is_empty() {
        let block_bytes = config.block_bytes as usize;
        let mut global_start = 0usize;
        for (block_index, block) in input.chunks(block_bytes).enumerate() {
            let choice = optimize_block(
                block,
                block_index as u64,
                global_start as u64,
                config,
                limits,
                providers,
                &provider_order,
                whole_block_providers,
                &whole_block_provider_order,
                &mut session,
            )?;
            block_choices.push(choice);
            global_start = global_start
                .checked_add(block.len())
                .ok_or(Error::IntegerOverflow {
                    context: "optimizer block offset",
                })?;
        }
    }

    let candidate_programs: Vec<_> = block_choices
        .iter()
        .map(|choice| choice.program.clone())
        .collect();
    let candidate_blocks: Vec<_> = candidate_programs
        .iter()
        .zip(input.chunks(config.block_bytes as usize))
        .map(|(program, restored)| ArchiveBlock { program, restored })
        .collect();
    let candidate_archive = match encode_archive(&candidate_blocks, limits) {
        Ok(archive) => Some(archive),
        Err(Error::LimitExceeded {
            what: "archive bytes",
            ..
        }) => {
            session.complete = false;
            let _ = session.push(IntervalLedgerEntry {
                candidate_id: 0,
                block_index: None,
                global_start: 0,
                global_end: input.len() as u64,
                descriptor: internal_descriptor("candidate-archive", 0),
                event: LedgerEvent::HeuristicSkip,
                reason: LedgerReason::CandidateArchiveLimit,
                exact_node_bytes: None,
                actual_interval_archive_bytes: None,
                admissible_lower_bound_bytes: None,
                upper_bound_at_decision: literal_archive_bytes,
                work_before: session.work,
                work_charged: 0,
                represented_descriptors: 1,
                selected: false,
            });
            None
        }
        Err(error) => return Err(error),
    };

    let candidate = if let Some(archive) = candidate_archive {
        let (programs, verified) = decode_and_verify(&archive, input, limits)?;
        let cost = aggregate_archive_cost(&programs, &archive, limits)?;
        Some((archive, programs, verified, cost))
    } else {
        None
    };

    let candidate_is_auditable = block_choices.iter().all(|choice| {
        if let Some(whole) = &choice.whole_block {
            whole.ledger_id.is_some()
        } else if choice.edges.is_empty() {
            choice.literal_ledger_id.is_some()
        } else {
            choice.edges.iter().all(|edge| edge.ledger_id.is_some())
        }
    });
    let procedural_wins = candidate_is_auditable
        && candidate
            .as_ref()
            .is_some_and(|(_, _, _, cost)| cost < &literal_cost);
    if procedural_wins {
        session.ledger[0].selected = false;
        for choice in &block_choices {
            if let Some(whole) = &choice.whole_block {
                if let Some(id) = whole.ledger_id {
                    mark_selected(&mut session.ledger, id)?;
                }
            } else if choice.edges.is_empty() {
                if let Some(id) = choice.literal_ledger_id {
                    mark_selected(&mut session.ledger, id)?;
                }
            } else {
                for edge in &choice.edges {
                    if let Some(id) = edge.ledger_id {
                        mark_selected(&mut session.ledger, id)?;
                    }
                }
            }
        }
    }

    let usage = session.usage();
    let ledger = session.ledger;
    validate_audit(&ledger, &usage, config)?;
    if procedural_wins {
        let (archive, programs, verified, cost) =
            candidate.ok_or(Error::InvalidValue("missing procedural candidate"))?;
        let coverage = procedural_coverage(
            input.len() as u64,
            archive.len() as u64,
            literal_archive_bytes,
            &block_choices,
            limits,
        )?;
        Ok(OptimizationResult {
            archive,
            programs,
            selection: ArchiveSelection::Procedural,
            cost,
            literal_cost,
            literal_archive_bytes,
            ledger,
            coverage,
            usage,
            verified,
        })
    } else {
        let coverage = literal_coverage(
            input.len() as u64,
            literal_archive.len() as u64,
            &literal_programs,
            limits,
        )?;
        Ok(OptimizationResult {
            archive: literal_archive,
            programs: literal_programs,
            selection: ArchiveSelection::LiteralFallback,
            cost: literal_cost.clone(),
            literal_cost,
            literal_archive_bytes,
            ledger,
            coverage,
            usage,
            verified: literal_verified,
        })
    }
}

#[allow(clippy::too_many_arguments)]
fn optimize_block(
    block: &[u8],
    block_index: u64,
    global_start: u64,
    config: &OptimizerConfig,
    limits: &Limits,
    providers: &mut [&mut dyn CandidateProvider],
    provider_order: &[usize],
    whole_block_providers: &mut [&mut dyn WholeBlockCandidateProvider],
    whole_block_provider_order: &[usize],
    session: &mut SearchSession<'_>,
) -> Result<BlockChoice> {
    let literal_program = Program::literal(block.to_vec());
    let literal_archive = encode_archive(
        &[ArchiveBlock {
            program: &literal_program,
            restored: block,
        }],
        limits,
    )?;
    let literal_cost = aggregate_archive_cost(
        std::slice::from_ref(&literal_program),
        &literal_archive,
        limits,
    )?;
    let global_end = checked_u64_add(global_start, block.len() as u64, "block end")?;
    let literal_ledger_id = session.push(IntervalLedgerEntry {
        candidate_id: 0,
        block_index: Some(block_index),
        global_start,
        global_end,
        descriptor: internal_descriptor("literal-block", block_index as u32),
        event: LedgerEvent::Tried,
        reason: LedgerReason::BlockLiteralUpperBound,
        exact_node_bytes: Some(child_record_bytes(&literal_program, limits)?),
        actual_interval_archive_bytes: Some(literal_archive.len() as u64),
        admissible_lower_bound_bytes: None,
        upper_bound_at_decision: session.literal_archive_bytes,
        work_before: session.work,
        work_charged: 0,
        represented_descriptors: 1,
        selected: false,
    });
    let mut best = BlockChoice {
        program: literal_program,
        edges: Vec::new(),
        literal_ledger_id,
        whole_block: None,
        order: vec![internal_descriptor("literal-block", block_index as u32)],
    };
    let mut best_cost = literal_cost;

    if session.stopped {
        return Ok(best);
    }

    // Whole-block catalogues get their bounded chance before interval search;
    // otherwise a legal interval budget stop can starve a general-purpose
    // candidate that applies to the entire source block.
    compete_whole_block_candidates(
        block,
        block_index,
        global_start,
        global_end,
        limits,
        whole_block_providers,
        whole_block_provider_order,
        session,
        &mut best,
        &mut best_cost,
    )?;
    if session.stopped {
        return Ok(best);
    }

    // Without an interval provider, every possible edge is a literal. A split
    // of literal bytes adds a SPLIT node and child framing, so it cannot beat
    // the already measured whole-block literal candidate. This is an exact
    // catalogue reduction for the explicit interval-search ablation.
    if providers.is_empty() {
        return Ok(best);
    }

    let boundaries = canonical_boundaries(block.len(), config.microblock_bytes as usize)?;
    let mut states: Vec<Vec<DpState>> = vec![Vec::new(); boundaries.len()];
    if !session.charge_state(block_index, global_start) {
        return Ok(best);
    }
    states[0].push(DpState::default());

    'search: for start_index in 0..boundaries.len() - 1 {
        if session.stopped {
            break;
        }
        let expandable: Vec<_> = states[start_index]
            .iter()
            .filter(|state| state.edges.len() < config.max_segments_per_block as usize)
            .cloned()
            .collect();
        if expandable.is_empty() {
            continue;
        }

        for end_index in start_index + 1..boundaries.len() {
            let local_start = boundaries[start_index];
            let local_end = boundaries[end_index];
            let interval = &block[local_start..local_end];
            let edges = interval_edges(
                interval,
                block_index,
                global_start,
                local_start,
                local_end,
                limits,
                providers,
                provider_order,
                session,
            )?;
            if session.stopped {
                break 'search;
            }

            for state in &expandable {
                for edge in &edges {
                    let transition_start =
                        checked_u64_add(global_start, local_start as u64, "transition start")?;
                    if !session.charge_work(
                        1,
                        Some(block_index),
                        transition_start,
                        transition_start,
                    ) {
                        break 'search;
                    }
                    if !session.charge_state(block_index, transition_start) {
                        break 'search;
                    }
                    states[end_index].push(state.extend(edge)?);
                }
            }
        }
    }

    if !session.stopped
        && boundaries.len().saturating_sub(1) > config.max_segments_per_block as usize
    {
        session.complete = false;
        let _ = session.push(IntervalLedgerEntry {
            candidate_id: 0,
            block_index: Some(block_index),
            global_start,
            global_end,
            descriptor: internal_descriptor("segmentation", 0),
            event: LedgerEvent::HeuristicSkip,
            reason: LedgerReason::SegmentLimit,
            exact_node_bytes: None,
            actual_interval_archive_bytes: None,
            admissible_lower_bound_bytes: None,
            upper_bound_at_decision: session.literal_archive_bytes,
            work_before: session.work,
            work_charged: 0,
            represented_descriptors: 1,
            selected: false,
        });
    }

    for state in &states[boundaries.len() - 1] {
        let program = state.program(block.len())?;
        let sections = program.encode_sections(limits)?;
        let predicted = state.predicted_root_bytes(block.len())?;
        if sections.root.len() as u64 != predicted {
            return Err(Error::LengthMismatch {
                context: "exact additive segmentation root bytes",
                expected: predicted,
                actual: sections.root.len() as u64,
            });
        }
        let archive = encode_archive(
            &[ArchiveBlock {
                program: &program,
                restored: block,
            }],
            limits,
        )?;
        let cost = aggregate_archive_cost(std::slice::from_ref(&program), &archive, limits)?;
        let ordering = cost.cmp(&best_cost).then_with(|| path_order(state, &best));
        if ordering == Ordering::Less {
            best_cost = cost;
            let order = state
                .edges
                .iter()
                .map(|edge| edge.descriptor.clone())
                .collect();
            best = BlockChoice {
                program,
                edges: state.edges.clone(),
                literal_ledger_id,
                whole_block: None,
                order,
            };
        }
    }

    Ok(best)
}

fn path_order(state: &DpState, current: &BlockChoice) -> Ordering {
    let left: Vec<_> = state.edges.iter().map(|edge| &edge.descriptor).collect();
    let right: Vec<_> = current.order.iter().collect();
    left.cmp(&right)
}

#[allow(clippy::too_many_arguments)]
fn compete_whole_block_candidates(
    block: &[u8],
    block_index: u64,
    global_start: u64,
    global_end: u64,
    limits: &Limits,
    providers: &mut [&mut dyn WholeBlockCandidateProvider],
    provider_order: &[usize],
    session: &mut SearchSession<'_>,
    best: &mut BlockChoice,
    best_cost: &mut CandidateCost,
) -> Result<()> {
    for &provider_index in provider_order {
        if session.stopped {
            break;
        }
        let provider_id = providers[provider_index].provider_id();
        if !session.can_push(Some(block_index), global_start, global_end) {
            break;
        }
        let work_before = session.work;
        let searched = providers[provider_index].search(
            block,
            ProviderContext {
                block_index,
                global_start,
                global_end,
                candidates_remaining: session.candidates_remaining(),
                work_remaining: session.work_remaining(),
            },
            limits,
        )?;
        if searched.work_used > session.work_remaining() {
            return Err(Error::LimitExceeded {
                what: "whole-block provider-reported optimizer work",
                actual: searched.work_used,
                limit: session.work_remaining(),
            });
        }
        if searched.represented_descriptors < searched.candidates.len() as u64 {
            return Err(Error::InvalidValue(
                "whole-block provider descriptor count is smaller than candidates",
            ));
        }
        if !session.charge_work(
            searched.work_used,
            Some(block_index),
            global_start,
            global_end,
        ) {
            break;
        }
        let provider_event = if searched.budget_exhausted {
            session.any_budget_exhausted = true;
            session.complete = false;
            (LedgerEvent::BudgetStop, LedgerReason::ProviderBudget)
        } else if searched.complete_within_declared_catalogue {
            (LedgerEvent::Tried, LedgerReason::ProviderSearchComplete)
        } else {
            session.complete = false;
            (
                LedgerEvent::HeuristicSkip,
                LedgerReason::ProviderCatalogueIncomplete,
            )
        };
        if session
            .push(IntervalLedgerEntry {
                candidate_id: 0,
                block_index: Some(block_index),
                global_start,
                global_end,
                descriptor: CandidateDescriptor {
                    provider: provider_id.to_owned(),
                    key: ProviderCandidateKey {
                        family: u32::MAX,
                        parameters: Vec::new(),
                        canonical_payload: Vec::new(),
                    },
                },
                event: provider_event.0,
                reason: provider_event.1,
                exact_node_bytes: None,
                actual_interval_archive_bytes: None,
                admissible_lower_bound_bytes: None,
                upper_bound_at_decision: session.literal_archive_bytes,
                work_before,
                work_charged: searched.work_used,
                represented_descriptors: searched.represented_descriptors,
                selected: false,
            })
            .is_none()
        {
            break;
        }

        let mut candidates = searched.candidates;
        candidates.sort_by(|left, right| {
            left.key
                .cmp(&right.key)
                .then_with(|| left.class.cmp(&right.class))
        });
        if candidates.windows(2).any(|pair| pair[0].key == pair[1].key) {
            return Err(Error::InvalidValue(
                "whole-block provider candidate keys must be unique",
            ));
        }
        for candidate in candidates {
            if session.stopped {
                break;
            }
            if !session.charge_candidate(block_index, global_start, global_end) {
                break;
            }
            let attributed = checked_u64_add(
                candidate.class_source_bytes,
                candidate.literal_source_bytes,
                "whole-block source attribution",
            )?;
            if attributed != block.len() as u64 {
                return Err(Error::LengthMismatch {
                    context: "whole-block source attribution",
                    expected: block.len() as u64,
                    actual: attributed,
                });
            }
            let descriptor = CandidateDescriptor {
                provider: provider_id.to_owned(),
                key: candidate.key,
            };
            let report = candidate.program.validate(limits)?;
            let verification_work = report.decode_work;
            let verification_work_before = session.work;
            if !session.charge_work(
                verification_work,
                Some(block_index),
                global_start,
                global_end,
            ) {
                break;
            }
            if evaluate_program(&candidate.program, limits)? != block {
                return Err(Error::InvalidValue(
                    "whole-block provider did not exactly restore its block",
                ));
            }
            let archive = encode_archive(
                &[ArchiveBlock {
                    program: &candidate.program,
                    restored: block,
                }],
                limits,
            )?;
            let _ = decode_and_verify(&archive, block, limits)?;
            let cost =
                aggregate_archive_cost(std::slice::from_ref(&candidate.program), &archive, limits)?;
            let sections = candidate.program.encode_sections(limits)?;
            let exact_node_bytes = checked_u64_add(
                sections.definitions.len() as u64,
                sections.root.len() as u64,
                "whole-block node bytes",
            )?;
            let ledger_id = session.push(IntervalLedgerEntry {
                candidate_id: 0,
                block_index: Some(block_index),
                global_start,
                global_end,
                descriptor: descriptor.clone(),
                event: LedgerEvent::Tried,
                reason: LedgerReason::EvaluatedExact,
                exact_node_bytes: Some(exact_node_bytes),
                actual_interval_archive_bytes: Some(archive.len() as u64),
                admissible_lower_bound_bytes: None,
                upper_bound_at_decision: session.literal_archive_bytes,
                work_before: verification_work_before,
                work_charged: verification_work,
                represented_descriptors: 1,
                selected: false,
            });
            if session.stopped {
                break;
            }

            let ordering = cost
                .cmp(best_cost)
                .then_with(|| std::slice::from_ref(&descriptor).cmp(&best.order));
            if ordering == Ordering::Less {
                *best_cost = cost;
                *best = BlockChoice {
                    program: candidate.program,
                    edges: Vec::new(),
                    literal_ledger_id: best.literal_ledger_id,
                    whole_block: Some(WholeBlockSelection {
                        class: candidate.class,
                        class_source_bytes: candidate.class_source_bytes,
                        literal_source_bytes: candidate.literal_source_bytes,
                        ledger_id,
                    }),
                    order: vec![descriptor],
                };
            }
        }
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn interval_edges(
    interval: &[u8],
    block_index: u64,
    block_global_start: u64,
    local_start: usize,
    local_end: usize,
    limits: &Limits,
    providers: &mut [&mut dyn CandidateProvider],
    provider_order: &[usize],
    session: &mut SearchSession<'_>,
) -> Result<Vec<IntervalEdge>> {
    let global_start = checked_u64_add(block_global_start, local_start as u64, "interval start")?;
    let global_end = checked_u64_add(block_global_start, local_end as u64, "interval end")?;
    let mut edges = Vec::new();

    if session.charge_candidate(block_index, global_start, global_end) {
        let descriptor = internal_descriptor("literal", 0);
        if let Some(edge) = verify_edge(
            descriptor,
            None,
            Node::Literal(interval.to_vec()),
            interval,
            block_index,
            local_start as u64,
            local_end as u64,
            global_start,
            global_end,
            limits,
            session,
        )? {
            edges.push(edge);
        }
    }
    if session.stopped {
        return Ok(edges);
    }

    for &provider_index in provider_order {
        if session.stopped {
            break;
        }
        let provider_id = providers[provider_index].provider_id();
        if !session.can_push(Some(block_index), global_start, global_end) {
            break;
        }
        let work_before = session.work;
        let searched = providers[provider_index].search(
            interval,
            ProviderContext {
                block_index,
                global_start,
                global_end,
                candidates_remaining: session.candidates_remaining(),
                work_remaining: session.work_remaining(),
            },
            limits,
        )?;
        if searched.work_used > session.work_remaining() {
            return Err(Error::LimitExceeded {
                what: "provider-reported optimizer work",
                actual: searched.work_used,
                limit: session.work_remaining(),
            });
        }
        if searched.represented_descriptors < searched.candidates.len() as u64 {
            return Err(Error::InvalidValue(
                "provider descriptor count is smaller than candidates",
            ));
        }
        if !session.charge_work(
            searched.work_used,
            Some(block_index),
            global_start,
            global_end,
        ) {
            break;
        }

        let provider_event = if searched.budget_exhausted {
            session.any_budget_exhausted = true;
            session.complete = false;
            (LedgerEvent::BudgetStop, LedgerReason::ProviderBudget)
        } else if searched.complete_within_declared_catalogue {
            (LedgerEvent::Tried, LedgerReason::ProviderSearchComplete)
        } else {
            session.complete = false;
            (
                LedgerEvent::HeuristicSkip,
                LedgerReason::ProviderCatalogueIncomplete,
            )
        };
        let provider_key = CandidateDescriptor {
            provider: provider_id.to_owned(),
            key: ProviderCandidateKey {
                family: u32::MAX,
                parameters: Vec::new(),
                canonical_payload: Vec::new(),
            },
        };
        if session
            .push(IntervalLedgerEntry {
                candidate_id: 0,
                block_index: Some(block_index),
                global_start,
                global_end,
                descriptor: provider_key,
                event: provider_event.0,
                reason: provider_event.1,
                exact_node_bytes: None,
                actual_interval_archive_bytes: None,
                admissible_lower_bound_bytes: None,
                upper_bound_at_decision: session.literal_archive_bytes,
                work_before,
                work_charged: searched.work_used,
                represented_descriptors: searched.represented_descriptors,
                selected: false,
            })
            .is_none()
        {
            break;
        }

        let mut candidates = searched.candidates;
        candidates.sort_by(|left, right| {
            left.key
                .cmp(&right.key)
                .then_with(|| left.class.cmp(&right.class))
                .then_with(|| left.node.cmp(&right.node))
        });
        for candidate in candidates {
            if session.stopped {
                break;
            }
            if !session.charge_candidate(block_index, global_start, global_end) {
                break;
            }
            let descriptor = CandidateDescriptor {
                provider: provider_id.to_owned(),
                key: candidate.key,
            };
            if let Some(edge) = verify_edge(
                descriptor,
                Some(candidate.class),
                candidate.node,
                interval,
                block_index,
                local_start as u64,
                local_end as u64,
                global_start,
                global_end,
                limits,
                session,
            )? {
                edges.push(edge);
            }
        }
    }

    edges.sort_by(|left, right| {
        left.descriptor
            .cmp(&right.descriptor)
            .then_with(|| left.node.cmp(&right.node))
    });
    Ok(edges)
}

#[allow(clippy::too_many_arguments)]
fn verify_edge(
    descriptor: CandidateDescriptor,
    class: Option<CoverageClass>,
    node: Node,
    interval: &[u8],
    block_index: u64,
    local_start: u64,
    local_end: u64,
    global_start: u64,
    global_end: u64,
    limits: &Limits,
    session: &mut SearchSession<'_>,
) -> Result<Option<IntervalEdge>> {
    let program = file_program(interval.len(), node.clone());
    let report = program.validate(limits)?;
    let verification_work = report.decode_work;
    let work_before = session.work;
    if !session.charge_work(
        verification_work,
        Some(block_index),
        global_start,
        global_end,
    ) {
        return Ok(None);
    }
    let restored = evaluate_program(&program, limits)?;
    if restored != interval {
        return Err(Error::InvalidValue(
            "candidate provider did not exactly restore its interval",
        ));
    }
    let node_bytes = child_record_bytes(&program, limits)?;
    let archive = encode_archive(
        &[ArchiveBlock {
            program: &program,
            restored: interval,
        }],
        limits,
    )?;
    let _ = decode_and_verify(&archive, interval, limits)?;
    let ledger_id = session.push(IntervalLedgerEntry {
        candidate_id: 0,
        block_index: Some(block_index),
        global_start,
        global_end,
        descriptor: descriptor.clone(),
        event: LedgerEvent::Tried,
        reason: LedgerReason::EvaluatedExact,
        exact_node_bytes: Some(node_bytes),
        actual_interval_archive_bytes: Some(archive.len() as u64),
        admissible_lower_bound_bytes: None,
        upper_bound_at_decision: session.literal_archive_bytes,
        work_before,
        work_charged: verification_work,
        represented_descriptors: 1,
        selected: false,
    });
    if session.stopped {
        return Ok(None);
    }
    Ok(Some(IntervalEdge {
        descriptor,
        class,
        node,
        start: local_start,
        end: local_end,
        metrics: EdgeMetrics { node_bytes },
        ledger_id,
    }))
}

fn validate_config(config: &OptimizerConfig, limits: &Limits) -> Result<()> {
    if config.block_bytes == 0 {
        return Err(Error::InvalidValue("optimizer block size must be non-zero"));
    }
    if u64::from(config.block_bytes) > V1_MAX_BLOCK_OUTPUT_BYTES {
        return Err(Error::LimitExceeded {
            what: "optimizer block bytes",
            actual: u64::from(config.block_bytes),
            limit: V1_MAX_BLOCK_OUTPUT_BYTES,
        });
    }
    limits.check(
        "optimizer block bytes",
        u64::from(config.block_bytes),
        limits.max_block_output_bytes,
    )?;
    if config.microblock_bytes == 0 {
        return Err(Error::InvalidValue(
            "optimizer microblock size must be non-zero",
        ));
    }
    if config.max_segments_per_block == 0 {
        return Err(Error::InvalidValue(
            "optimizer segment cap must be non-zero",
        ));
    }
    if u64::from(config.max_segments_per_block) > HARD_MAX_SEGMENTS_PER_BLOCK {
        return Err(Error::LimitExceeded {
            what: "optimizer segments per block",
            actual: u64::from(config.max_segments_per_block),
            limit: HARD_MAX_SEGMENTS_PER_BLOCK,
        });
    }
    limits.check(
        "optimizer segment fan-out",
        u64::from(config.max_segments_per_block),
        limits.max_fan_out,
    )?;
    if config.max_states == 0 {
        return Err(Error::InvalidValue(
            "optimizer state cap must retain a source state",
        ));
    }
    for (what, actual, maximum) in [
        ("optimizer states", config.max_states, HARD_MAX_STATES),
        (
            "optimizer candidates",
            config.max_candidates,
            HARD_MAX_CANDIDATES,
        ),
        ("optimizer work", config.work_budget, HARD_MAX_WORK),
        (
            "optimizer ledger rows",
            config.max_ledger_entries,
            HARD_MAX_LEDGER_ENTRIES,
        ),
    ] {
        if actual > maximum {
            return Err(Error::LimitExceeded {
                what,
                actual,
                limit: maximum,
            });
        }
    }
    if config.max_ledger_entries < 2 {
        return Err(Error::InvalidValue(
            "optimizer ledger cap must retain literal and stop rows",
        ));
    }
    Ok(())
}

fn canonical_provider_order(providers: &[&mut dyn CandidateProvider]) -> Result<Vec<usize>> {
    let mut order: Vec<_> = (0..providers.len()).collect();
    order.sort_by(|left, right| {
        providers[*left]
            .provider_id()
            .cmp(providers[*right].provider_id())
    });
    let mut previous = None;
    for &index in &order {
        let name = providers[index].provider_id();
        if name.is_empty() {
            return Err(Error::InvalidValue("provider ID must be non-empty"));
        }
        if previous == Some(name) {
            return Err(Error::InvalidValue("provider IDs must be unique"));
        }
        previous = Some(name);
    }
    Ok(order)
}

fn canonical_whole_block_provider_order(
    providers: &[&mut dyn WholeBlockCandidateProvider],
) -> Result<Vec<usize>> {
    let mut order: Vec<_> = (0..providers.len()).collect();
    order.sort_by(|left, right| {
        providers[*left]
            .provider_id()
            .cmp(providers[*right].provider_id())
    });
    let mut previous = None;
    for &index in &order {
        let name = providers[index].provider_id();
        if name.is_empty() {
            return Err(Error::InvalidValue(
                "whole-block provider ID must be non-empty",
            ));
        }
        if previous == Some(name) {
            return Err(Error::InvalidValue(
                "whole-block provider IDs must be unique",
            ));
        }
        previous = Some(name);
    }
    Ok(order)
}

fn provider_budget_stop() -> ProviderSearch {
    ProviderSearch {
        candidates: Vec::new(),
        work_used: 0,
        represented_descriptors: 0,
        complete_within_declared_catalogue: false,
        budget_exhausted: true,
    }
}

fn whole_block_budget_stop(
    represented_descriptors: u64,
    work_used: u64,
) -> WholeBlockProviderSearch {
    WholeBlockProviderSearch {
        candidates: Vec::new(),
        work_used,
        represented_descriptors,
        complete_within_declared_catalogue: false,
        budget_exhausted: true,
    }
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

fn represented_function_descriptors(searched: &mathsvg_functions::SearchResult) -> Result<u64> {
    represented_function_ledger(&searched.ledger)
}

fn represented_function_ledger(ledger: &[mathsvg_functions::LedgerEntry]) -> Result<u64> {
    ledger.iter().try_fold(0u64, |sum, entry| {
        checked_u64_add(
            sum,
            entry.represented_descriptors,
            "function represented descriptors",
        )
    })
}

fn program_child_without_definitions(program: Program) -> Result<Node> {
    if !program.definitions.is_empty() {
        return Err(Error::InvalidValue(
            "interval provider program contains block definitions",
        ));
    }
    match program.root {
        Node::File { child, .. } => Ok(*child),
        _ => Err(Error::InvalidValue(
            "interval provider program root is not FILE",
        )),
    }
}

fn program_payload(program: &Program, limits: &Limits) -> Result<Vec<u8>> {
    let sections = program.encode_sections(limits)?;
    let mut payload = Vec::with_capacity(
        16usize
            .saturating_add(sections.definitions.len())
            .saturating_add(sections.root.len()),
    );
    payload.extend_from_slice(&(sections.definitions.len() as u64).to_le_bytes());
    payload.extend_from_slice(&sections.definitions);
    payload.extend_from_slice(&(sections.root.len() as u64).to_le_bytes());
    payload.extend_from_slice(&sections.root);
    Ok(payload)
}

fn canonical_boundaries(length: usize, microblock: usize) -> Result<Vec<usize>> {
    if length == 0 {
        return Ok(vec![0]);
    }
    let mut boundaries = vec![0];
    let mut next = microblock.min(length);
    while next < length {
        boundaries.push(next);
        next = next
            .checked_add(microblock)
            .ok_or(Error::IntegerOverflow {
                context: "microblock boundary",
            })?
            .min(length);
    }
    boundaries.push(length);
    Ok(boundaries)
}

/// Return the canonical segmentation grid without running a search.
///
/// The final (possibly short) tail is always present, so the result is
/// `[0]` for an empty interval and otherwise starts at zero and ends at
/// `length`.
pub fn canonical_microblock_boundaries(length: u64, microblock: u32) -> Result<Vec<u64>> {
    if microblock == 0 {
        return Err(Error::InvalidValue(
            "optimizer microblock size must be non-zero",
        ));
    }
    let length = usize::try_from(length).map_err(|_| Error::LimitExceeded {
        what: "optimizer interval bytes",
        actual: length,
        limit: usize::MAX as u64,
    })?;
    canonical_boundaries(length, microblock as usize)
        .map(|boundaries| boundaries.into_iter().map(|value| value as u64).collect())
}

fn file_program(length: usize, child: Node) -> Program {
    Program {
        definitions: Vec::new(),
        root: Node::File {
            original_length: length as u64,
            child: Box::new(child),
        },
    }
}

fn node_record_bytes(payload_bytes: u64) -> Result<u64> {
    checked_u64_add(
        checked_u64_add(2, encoded_len(payload_bytes) as u64, "node record bytes")?,
        payload_bytes,
        "node record bytes",
    )
}

fn child_record_bytes(program: &Program, limits: &Limits) -> Result<u64> {
    let sections = program.encode_sections(limits)?;
    let mut root = Cursor::new(&sections.root);
    let opcode = root.read_u8("FILE opcode")?;
    if opcode != 0x20 {
        return Err(Error::InvalidValue("candidate root is not FILE"));
    }
    let flags = root.read_u8("FILE flags")?;
    if flags != 0 {
        return Err(Error::InvalidValue("candidate FILE flags are non-zero"));
    }
    let payload_length = root.read_usize("FILE payload length")?;
    let mut payload = root.subcursor(payload_length, "FILE payload")?;
    let _original_length = payload.read_u64()?;
    Ok(payload.remaining() as u64)
}

fn decode_memory(report: &ValidationReport) -> Result<u64> {
    checked_u64_add(
        report.original_bytes,
        report.temporary_bytes,
        "optimizer decode memory",
    )
}

fn aggregate_archive_cost(
    programs: &[Program],
    archive: &[u8],
    limits: &Limits,
) -> Result<CandidateCost> {
    let mut decode_work = 0u64;
    let mut decode_memory_peak = 0u64;
    let mut node_count = 0u64;
    let mut dependency_count = 0u64;
    let mut opcode_sequence = Vec::new();
    let mut parameter_payload = Vec::new();
    for program in programs {
        let report = program.validate(limits)?;
        decode_work = checked_u64_add(decode_work, report.decode_work, "archive decode work")?;
        decode_memory_peak = decode_memory_peak.max(decode_memory(&report)?);
        node_count = checked_u64_add(node_count, report.node_count, "archive node count")?;
        dependency_count = checked_u64_add(
            dependency_count,
            report.dependency_count,
            "archive dependency count",
        )?;
        opcode_sequence.extend_from_slice(&report.opcode_sequence);
        let local = program.candidate_cost(0, decode_memory(&report)?, limits)?;
        parameter_payload.extend_from_slice(&local.parameter_payload);
    }
    Ok(CandidateCost {
        archive_bytes: archive.len() as u64,
        decode_work,
        decode_memory: decode_memory_peak,
        node_count,
        dependency_count,
        opcode_sequence,
        parameter_payload,
        canonical_payload: archive.to_vec(),
    })
}

fn decode_and_verify(
    archive: &[u8],
    expected: &[u8],
    limits: &Limits,
) -> Result<(Vec<Program>, VerifiedArchive)> {
    let decoded = decode_archive(archive, limits)?;
    let programs: Vec<_> = decoded
        .blocks
        .iter()
        .map(|block| block.program.clone())
        .collect();
    let mut offset = 0usize;
    let verified = decoded.verify_restored_with(
        |program| evaluate_program(program, limits),
        |_index, restored| {
            let end = offset
                .checked_add(restored.len())
                .ok_or(Error::IntegerOverflow {
                    context: "verified optimizer output",
                })?;
            let expected_block = expected.get(offset..end).ok_or(Error::LengthMismatch {
                context: "verified optimizer output",
                expected: expected.len() as u64,
                actual: end as u64,
            })?;
            if restored != expected_block {
                return Err(Error::InvalidValue(
                    "verified archive bytes differ from optimizer input",
                ));
            }
            offset = end;
            Ok(())
        },
    )?;
    if offset != expected.len() {
        return Err(Error::LengthMismatch {
            context: "verified optimizer output",
            expected: expected.len() as u64,
            actual: offset as u64,
        });
    }
    Ok((programs, verified))
}

fn procedural_coverage(
    original_bytes: u64,
    archive_bytes: u64,
    literal_archive_bytes: u64,
    choices: &[BlockChoice],
    limits: &Limits,
) -> Result<CoverageBreakdown> {
    let mut coverage = CoverageBreakdown {
        original_bytes,
        archive_bytes,
        literal_archive_bytes,
        bytes_saved_vs_literal: literal_archive_bytes.checked_sub(archive_bytes).ok_or(
            Error::InvalidValue("procedural archive exceeds literal after selection"),
        )?,
        block_count: choices.len() as u64,
        ..CoverageBreakdown::default()
    };
    for choice in choices {
        if let Some(whole) = &choice.whole_block {
            let sections = choice.program.encode_sections(limits)?;
            coverage.leaf_node_bytes = checked_u64_add(
                coverage.leaf_node_bytes,
                checked_u64_add(
                    sections.definitions.len() as u64,
                    child_record_bytes(&choice.program, limits)?,
                    "whole-block coverage node bytes",
                )?,
                "coverage leaf bytes",
            )?;
            if whole.literal_source_bytes != 0 {
                coverage.segment_count += 1;
                coverage.literal_segments += 1;
                coverage.literal_source_bytes = checked_u64_add(
                    coverage.literal_source_bytes,
                    whole.literal_source_bytes,
                    "whole-block literal coverage",
                )?;
            }
            if whole.class_source_bytes != 0 {
                coverage.segment_count += 1;
                add_class_coverage(
                    &mut coverage,
                    whole.class,
                    whole.class_source_bytes,
                    "whole-block procedural coverage",
                )?;
            }
            continue;
        }
        if choice.edges.is_empty() {
            let length = choice.program.validate(limits)?.original_bytes;
            coverage.segment_count += 1;
            coverage.literal_segments += 1;
            coverage.literal_source_bytes =
                checked_u64_add(coverage.literal_source_bytes, length, "literal coverage")?;
            coverage.leaf_node_bytes = checked_u64_add(
                coverage.leaf_node_bytes,
                child_record_bytes(&choice.program, limits)?,
                "coverage leaf bytes",
            )?;
            continue;
        }
        for edge in &choice.edges {
            let length = edge.end - edge.start;
            coverage.segment_count += 1;
            coverage.leaf_node_bytes = checked_u64_add(
                coverage.leaf_node_bytes,
                edge.metrics.node_bytes,
                "coverage leaf bytes",
            )?;
            match edge.class {
                None | Some(CoverageClass::Literal) => {
                    coverage.literal_segments += 1;
                    coverage.literal_source_bytes =
                        checked_u64_add(coverage.literal_source_bytes, length, "literal coverage")?;
                }
                Some(CoverageClass::Function) => {
                    coverage.function_segments += 1;
                    coverage.function_source_bytes = checked_u64_add(
                        coverage.function_source_bytes,
                        length,
                        "function coverage",
                    )?;
                }
                Some(CoverageClass::Coordinate) => {
                    coverage.coordinate_segments += 1;
                    coverage.coordinate_source_bytes = checked_u64_add(
                        coverage.coordinate_source_bytes,
                        length,
                        "coordinate coverage",
                    )?;
                }
                Some(CoverageClass::Residual) => {
                    coverage.residual_segments += 1;
                    coverage.residual_source_bytes = checked_u64_add(
                        coverage.residual_source_bytes,
                        length,
                        "residual coverage",
                    )?;
                }
                Some(CoverageClass::Graph) => {
                    coverage.graph_segments += 1;
                    coverage.graph_source_bytes =
                        checked_u64_add(coverage.graph_source_bytes, length, "graph coverage")?;
                }
                Some(CoverageClass::Symbolic) => {
                    coverage.symbolic_segments += 1;
                    coverage.symbolic_source_bytes = checked_u64_add(
                        coverage.symbolic_source_bytes,
                        length,
                        "symbolic coverage",
                    )?;
                }
                Some(CoverageClass::Other) => {
                    coverage.other_segments += 1;
                    coverage.other_source_bytes =
                        checked_u64_add(coverage.other_source_bytes, length, "other coverage")?;
                }
            }
        }
    }
    coverage.envelope_and_topology_bytes = archive_bytes
        .checked_sub(coverage.leaf_node_bytes)
        .ok_or(Error::InvalidValue(
            "coverage leaf bytes exceed archive bytes",
        ))?;
    let covered = [
        coverage.literal_source_bytes,
        coverage.function_source_bytes,
        coverage.coordinate_source_bytes,
        coverage.residual_source_bytes,
        coverage.graph_source_bytes,
        coverage.symbolic_source_bytes,
        coverage.other_source_bytes,
    ]
    .into_iter()
    .try_fold(0u64, |sum, value| {
        checked_u64_add(sum, value, "selected source coverage")
    })?;
    if covered != original_bytes {
        return Err(Error::LengthMismatch {
            context: "selected source coverage",
            expected: original_bytes,
            actual: covered,
        });
    }
    Ok(coverage)
}

fn add_class_coverage(
    coverage: &mut CoverageBreakdown,
    class: CoverageClass,
    source_bytes: u64,
    context: &'static str,
) -> Result<()> {
    match class {
        CoverageClass::Literal => {
            coverage.literal_segments += 1;
            coverage.literal_source_bytes =
                checked_u64_add(coverage.literal_source_bytes, source_bytes, context)?;
        }
        CoverageClass::Function => {
            coverage.function_segments += 1;
            coverage.function_source_bytes =
                checked_u64_add(coverage.function_source_bytes, source_bytes, context)?;
        }
        CoverageClass::Coordinate => {
            coverage.coordinate_segments += 1;
            coverage.coordinate_source_bytes =
                checked_u64_add(coverage.coordinate_source_bytes, source_bytes, context)?;
        }
        CoverageClass::Residual => {
            coverage.residual_segments += 1;
            coverage.residual_source_bytes =
                checked_u64_add(coverage.residual_source_bytes, source_bytes, context)?;
        }
        CoverageClass::Graph => {
            coverage.graph_segments += 1;
            coverage.graph_source_bytes =
                checked_u64_add(coverage.graph_source_bytes, source_bytes, context)?;
        }
        CoverageClass::Symbolic => {
            coverage.symbolic_segments += 1;
            coverage.symbolic_source_bytes =
                checked_u64_add(coverage.symbolic_source_bytes, source_bytes, context)?;
        }
        CoverageClass::Other => {
            coverage.other_segments += 1;
            coverage.other_source_bytes =
                checked_u64_add(coverage.other_source_bytes, source_bytes, context)?;
        }
    }
    Ok(())
}

fn literal_coverage(
    original_bytes: u64,
    archive_bytes: u64,
    programs: &[Program],
    limits: &Limits,
) -> Result<CoverageBreakdown> {
    let mut leaf_node_bytes = 0u64;
    for program in programs {
        leaf_node_bytes = checked_u64_add(
            leaf_node_bytes,
            child_record_bytes(program, limits)?,
            "literal coverage leaf bytes",
        )?;
    }
    Ok(CoverageBreakdown {
        original_bytes,
        archive_bytes,
        literal_archive_bytes: archive_bytes,
        bytes_saved_vs_literal: 0,
        block_count: programs.len() as u64,
        segment_count: programs.len() as u64,
        literal_segments: programs.len() as u64,
        literal_source_bytes: original_bytes,
        leaf_node_bytes,
        envelope_and_topology_bytes: archive_bytes.checked_sub(leaf_node_bytes).ok_or(
            Error::InvalidValue("literal coverage leaf bytes exceed archive bytes"),
        )?,
        ..CoverageBreakdown::default()
    })
}

fn internal_descriptor(provider: &str, family: u32) -> CandidateDescriptor {
    CandidateDescriptor {
        provider: provider.to_owned(),
        key: ProviderCandidateKey {
            family,
            parameters: Vec::new(),
            canonical_payload: Vec::new(),
        },
    }
}

fn mark_selected(ledger: &mut [IntervalLedgerEntry], id: u64) -> Result<()> {
    let entry = ledger
        .get_mut(id as usize)
        .ok_or(Error::InvalidValue("selected ledger ID is missing"))?;
    if entry.candidate_id != id {
        return Err(Error::InvalidValue("selected ledger ID is not canonical"));
    }
    entry.selected = true;
    Ok(())
}

fn validate_audit(
    ledger: &[IntervalLedgerEntry],
    usage: &SearchUsage,
    config: &OptimizerConfig,
) -> Result<()> {
    if ledger.len() as u64 > config.max_ledger_entries {
        return Err(Error::LimitExceeded {
            what: "optimizer ledger rows",
            actual: ledger.len() as u64,
            limit: config.max_ledger_entries,
        });
    }
    if usage.ledger_entries != ledger.len() as u64 {
        return Err(Error::InvalidValue(
            "optimizer usage and ledger length disagree",
        ));
    }
    if usage.candidates > config.max_candidates
        || usage.states > config.max_states
        || usage.work > config.work_budget
    {
        return Err(Error::InvalidValue(
            "optimizer usage exceeded a deterministic hard cap",
        ));
    }
    for (index, entry) in ledger.iter().enumerate() {
        if entry.candidate_id != index as u64 {
            return Err(Error::InvalidValue(
                "optimizer ledger IDs are not dense and canonical",
            ));
        }
        if entry.event == LedgerEvent::SafePrune
            && !entry
                .admissible_lower_bound_bytes
                .is_some_and(|lower| lower >= entry.upper_bound_at_decision)
        {
            return Err(Error::InvalidValue(
                "SAFE_PRUNE lacks an admissible serialized-byte proof",
            ));
        }
        if entry.selected && entry.event != LedgerEvent::Tried {
            return Err(Error::InvalidValue(
                "selected optimizer candidate was not exactly tried",
            ));
        }
    }
    Ok(())
}
