//! Bounded exact activation search for block-local MathSVG definition DAGs.
//!
//! The implemented catalogue contains aligned, equal-width exact chunks.
//! Within that finite catalogue, every activation subset is measured from a
//! complete canonical `Program`; no statistical estimate can select a winner.

#![forbid(unsafe_code)]

use std::collections::BTreeMap;

use mathsvg_core::{CandidateCost, Error, Limits, Result, checked_u64_add};
use mathsvg_dsl::{Node, Program};

/// File header + one directory record + file footer. The block header is
/// included by `EncodedSections::v1_block_payload_bytes`.
const V1_SINGLE_BLOCK_OUTER_BYTES: u64 = 128 + 144 + 128;
const HARD_MAX_DEFINITIONS: u8 = 20;

/// Finite, deterministic SADH microblock budget.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GraphConfig {
    /// Aligned chunk width used by this catalogue.
    pub chunk_bytes: u32,
    /// A chunk must occur this many times to become a definition candidate.
    pub min_occurrences: u32,
    /// Maximum retained candidate definitions (hard-capped at 20).
    pub max_definitions: u8,
    /// Maximum activation subsets evaluated, including the empty subset.
    pub max_subsets: u64,
    /// Encoder work budget; one input byte is charged per measured subset.
    pub work_budget: u64,
    /// Maximum audit rows, including a final stop/omission row.
    pub max_ledger_entries: u64,
}

impl Default for GraphConfig {
    fn default() -> Self {
        Self {
            chunk_bytes: 64,
            min_occurrences: 3,
            max_definitions: 12,
            max_subsets: 1 << 12,
            work_budget: 1 << 26,
            max_ledger_entries: (1 << 12) + 1,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LedgerStatus {
    Tried,
    HeuristicSkip,
    BudgetStop,
}

impl LedgerStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Tried => "TRIED",
            Self::HeuristicSkip => "HEURISTIC_SKIP",
            Self::BudgetStop => "BUDGET_STOP",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LedgerReason {
    EvaluatedExact,
    CatalogueLimit,
    SubsetBudget,
    WorkBudget,
    LedgerBudget,
}

/// One canonical subset decision or explicit omission.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LedgerEntry {
    pub candidate_id: u64,
    /// Bit `i` activates retained definition candidate `i`.
    pub activation_mask: u64,
    pub status: LedgerStatus,
    pub reason: LedgerReason,
    pub active_definitions: u8,
    pub represented_subsets: u64,
    pub actual_archive_bytes: Option<u64>,
    pub upper_bound_at_decision: u64,
    pub became_best: bool,
    pub work_before: u64,
    pub work_charged: u64,
}

/// The selected complete block program and its normative total-order cost.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GraphCandidate {
    pub program: Program,
    pub cost: CandidateCost,
    pub activation_mask: u64,
    pub active_definitions: u8,
    /// Restored source bytes emitted through `REFERENCE` nodes.
    pub referenced_source_bytes: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GraphSearchResult {
    pub winner: GraphCandidate,
    pub literal_archive_bytes: u64,
    pub mined_definitions: u8,
    pub omitted_definitions: u64,
    pub evaluated_subsets: u64,
    pub work_used: u64,
    pub budget_exhausted: bool,
    pub complete_within_retained_catalogue: bool,
    /// True only when no mined descriptor was truncated and every activation
    /// subset was measured.
    pub complete_within_declared_catalogue: bool,
    pub ledger: Vec<LedgerEntry>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct DefinitionCandidate {
    bytes: Vec<u8>,
    occurrences: u64,
    first_position: u64,
}

/// Search exact aligned sharing for one non-empty v1 block.
pub fn search_shared_block(
    input: &[u8],
    config: &GraphConfig,
    limits: &Limits,
) -> Result<GraphSearchResult> {
    validate_config(input, config, limits)?;
    let literal_program = Program::literal(input.to_vec());
    let literal = measure(&literal_program, 0, 0, limits)?;
    let literal_archive_bytes = literal.cost.archive_bytes;

    let (catalogue, omitted_definitions) = mine_catalogue(input, config)?;
    let definition_count = catalogue.len();
    let total_subsets =
        1u64.checked_shl(definition_count as u32)
            .ok_or(Error::IntegerOverflow {
                context: "graph activation subset count",
            })?;
    let allowed_subsets = total_subsets.min(config.max_subsets);

    let mut best = literal;
    let mut ledger = Vec::new();
    let mut work_used = 0u64;
    let mut evaluated_subsets = 0u64;
    let mut budget_exhausted = false;
    let mut stop_reason = None;

    for mask in 0..allowed_subsets {
        if ledger.len() as u64 + 1 >= config.max_ledger_entries {
            budget_exhausted = true;
            stop_reason = Some(LedgerReason::LedgerBudget);
            break;
        }
        let work = input.len() as u64;
        let next_work = match checked_u64_add(work_used, work, "graph search work") {
            Ok(value) if value <= config.work_budget => value,
            Ok(_) | Err(_) => {
                budget_exhausted = true;
                stop_reason = Some(LedgerReason::WorkBudget);
                break;
            }
        };
        let work_before = work_used;
        work_used = next_work;

        let (program, referenced_source_bytes, active_definitions) =
            build_program(input, &catalogue, mask, config.chunk_bytes as usize)?;
        let candidate = measure(&program, mask, referenced_source_bytes, limits)?;
        let upper = best.cost.archive_bytes;
        let became_best = candidate.cost < best.cost;
        let actual = candidate.cost.archive_bytes;
        if became_best {
            best = candidate;
        }
        ledger.push(LedgerEntry {
            candidate_id: ledger.len() as u64,
            activation_mask: mask,
            status: LedgerStatus::Tried,
            reason: LedgerReason::EvaluatedExact,
            active_definitions,
            represented_subsets: 1,
            actual_archive_bytes: Some(actual),
            upper_bound_at_decision: upper,
            became_best,
            work_before,
            work_charged: work,
        });
        evaluated_subsets += 1;
    }

    if !budget_exhausted && allowed_subsets < total_subsets {
        budget_exhausted = true;
        stop_reason = Some(LedgerReason::SubsetBudget);
    }

    if let Some(reason) = stop_reason {
        if ledger.len() as u64 >= config.max_ledger_entries {
            return Err(Error::LimitExceeded {
                what: "graph ledger rows",
                actual: ledger.len() as u64 + 1,
                limit: config.max_ledger_entries,
            });
        }
        ledger.push(LedgerEntry {
            candidate_id: ledger.len() as u64,
            activation_mask: evaluated_subsets,
            status: LedgerStatus::BudgetStop,
            reason,
            active_definitions: 0,
            represented_subsets: total_subsets.saturating_sub(evaluated_subsets),
            actual_archive_bytes: None,
            upper_bound_at_decision: best.cost.archive_bytes,
            became_best: false,
            work_before: work_used,
            work_charged: 0,
        });
    }

    if omitted_definitions != 0 {
        if ledger.len() as u64 >= config.max_ledger_entries {
            return Err(Error::LimitExceeded {
                what: "graph ledger rows",
                actual: ledger.len() as u64 + 1,
                limit: config.max_ledger_entries,
            });
        }
        ledger.push(LedgerEntry {
            candidate_id: ledger.len() as u64,
            activation_mask: 0,
            status: LedgerStatus::HeuristicSkip,
            reason: LedgerReason::CatalogueLimit,
            active_definitions: 0,
            represented_subsets: omitted_definitions,
            actual_archive_bytes: None,
            upper_bound_at_decision: best.cost.archive_bytes,
            became_best: false,
            work_before: work_used,
            work_charged: 0,
        });
    }

    Ok(GraphSearchResult {
        winner: best,
        literal_archive_bytes,
        mined_definitions: definition_count as u8,
        omitted_definitions,
        evaluated_subsets,
        work_used,
        budget_exhausted,
        complete_within_retained_catalogue: !budget_exhausted,
        complete_within_declared_catalogue: !budget_exhausted && omitted_definitions == 0,
        ledger,
    })
}

fn validate_config(input: &[u8], config: &GraphConfig, limits: &Limits) -> Result<()> {
    if input.is_empty() {
        return Err(Error::InvalidValue(
            "graph search accepts non-empty v1 blocks only",
        ));
    }
    limits.check(
        "block output bytes",
        input.len() as u64,
        limits.max_block_output_bytes,
    )?;
    if config.chunk_bytes == 0 {
        return Err(Error::InvalidValue("graph chunk width must be positive"));
    }
    limits.check(
        "graph chunk bytes",
        u64::from(config.chunk_bytes),
        limits.max_pattern_bytes,
    )?;
    if config.min_occurrences < 2 {
        return Err(Error::InvalidValue(
            "shared definitions require at least two occurrences",
        ));
    }
    if config.max_definitions > HARD_MAX_DEFINITIONS {
        return Err(Error::LimitExceeded {
            what: "graph candidate definitions",
            actual: u64::from(config.max_definitions),
            limit: u64::from(HARD_MAX_DEFINITIONS),
        });
    }
    limits.check(
        "graph candidate definitions",
        u64::from(config.max_definitions),
        limits.max_definitions,
    )?;
    if config.max_subsets == 0 {
        return Err(Error::InvalidValue(
            "graph subset budget must include the empty subset",
        ));
    }
    if config.max_ledger_entries < 2 {
        return Err(Error::InvalidValue(
            "graph ledger budget must retain a candidate and stop row",
        ));
    }
    Ok(())
}

fn mine_catalogue(input: &[u8], config: &GraphConfig) -> Result<(Vec<DefinitionCandidate>, u64)> {
    let width = config.chunk_bytes as usize;
    let mut occurrences: BTreeMap<Vec<u8>, Vec<u64>> = BTreeMap::new();
    for (index, chunk) in input.chunks_exact(width).enumerate() {
        let position = index.checked_mul(width).ok_or(Error::IntegerOverflow {
            context: "graph chunk position",
        })?;
        occurrences
            .entry(chunk.to_vec())
            .or_default()
            .push(position as u64);
    }

    let mut candidates: Vec<_> = occurrences
        .into_iter()
        .filter(|(_, positions)| positions.len() >= config.min_occurrences as usize)
        .map(|(bytes, positions)| DefinitionCandidate {
            bytes,
            occurrences: positions.len() as u64,
            first_position: positions[0],
        })
        .collect();
    // Potential saving ranks only catalogue retention. Subset selection below
    // still uses exact serialized bytes. Equal scores use stable byte and
    // first-position order.
    candidates.sort_by(|left, right| {
        potential_saving(right)
            .cmp(&potential_saving(left))
            .then_with(|| left.bytes.cmp(&right.bytes))
            .then_with(|| left.first_position.cmp(&right.first_position))
    });
    let retained = candidates.len().min(config.max_definitions as usize);
    let omitted = (candidates.len() - retained) as u64;
    candidates.truncate(retained);
    Ok((candidates, omitted))
}

fn potential_saving(candidate: &DefinitionCandidate) -> u64 {
    candidate
        .occurrences
        .saturating_sub(1)
        .saturating_mul(candidate.bytes.len() as u64)
}

fn build_program(
    input: &[u8],
    catalogue: &[DefinitionCandidate],
    mask: u64,
    width: usize,
) -> Result<(Program, u64, u8)> {
    let mut active = Vec::new();
    for (catalogue_id, candidate) in catalogue.iter().enumerate() {
        if mask & (1u64 << catalogue_id) != 0 {
            active.push((catalogue_id, candidate));
        }
    }

    let mut definition_ids = BTreeMap::new();
    let mut definitions = Vec::with_capacity(active.len());
    for (definition_id, (catalogue_id, candidate)) in active.iter().enumerate() {
        definition_ids.insert(*catalogue_id, definition_id as u32);
        definitions.push(Node::Literal(candidate.bytes.clone()));
    }

    let active_by_bytes: BTreeMap<&[u8], u32> = active
        .iter()
        .map(|(catalogue_id, candidate)| (candidate.bytes.as_slice(), definition_ids[catalogue_id]))
        .collect();
    let mut children = Vec::new();
    let mut pending_literal = Vec::new();
    let mut referenced_source_bytes = 0u64;

    for chunk in input.chunks_exact(width) {
        if let Some(&definition) = active_by_bytes.get(chunk) {
            flush_literal(&mut children, &mut pending_literal);
            children.push(Node::Reference {
                definition,
                parameter_delta: Vec::new(),
            });
            referenced_source_bytes = checked_u64_add(
                referenced_source_bytes,
                width as u64,
                "graph referenced source bytes",
            )?;
        } else {
            pending_literal.extend_from_slice(chunk);
        }
    }
    pending_literal.extend_from_slice(input.chunks_exact(width).remainder());
    flush_literal(&mut children, &mut pending_literal);

    let child = match children.len() {
        0 => Node::Literal(Vec::new()),
        1 => children
            .pop()
            .ok_or(Error::InvalidValue("graph child state"))?,
        _ => Node::Concat(children),
    };
    Ok((
        Program {
            definitions,
            root: Node::File {
                original_length: input.len() as u64,
                child: Box::new(child),
            },
        },
        referenced_source_bytes,
        active.len() as u8,
    ))
}

fn flush_literal(children: &mut Vec<Node>, pending: &mut Vec<u8>) {
    if !pending.is_empty() {
        children.push(Node::Literal(std::mem::take(pending)));
    }
}

fn measure(
    program: &Program,
    activation_mask: u64,
    referenced_source_bytes: u64,
    limits: &Limits,
) -> Result<GraphCandidate> {
    let report = program.validate(limits)?;
    let sections = program.encode_sections(limits)?;
    let archive_bytes = checked_u64_add(
        V1_SINGLE_BLOCK_OUTER_BYTES,
        sections.v1_block_payload_bytes()?,
        "graph complete archive bytes",
    )?;
    let decode_memory = checked_u64_add(
        report.original_bytes,
        report.temporary_bytes,
        "graph candidate decode memory",
    )?;
    let cost = program.candidate_cost(archive_bytes, decode_memory, limits)?;
    Ok(GraphCandidate {
        program: program.clone(),
        cost,
        activation_mask,
        active_definitions: activation_mask.count_ones() as u8,
        referenced_source_bytes,
    })
}
