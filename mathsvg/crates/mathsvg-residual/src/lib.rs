//! Oracle-gated, bounded recursive residual search for MathSVG.
//!
//! The default configuration deliberately emits no recursive residual nodes.
//! The refreshed finite Phase 3 oracle found one qualifying real 4 KiB
//! development win, but a paired whole-file production ablation measured zero
//! archive gain. [`ArplGate::Experimental`] is therefore still required.

#![forbid(unsafe_code)]

use std::rc::Rc;

use mathsvg_container::{encode_archive, ArchiveBlock};
use mathsvg_core::{checked_u64_add, checked_u64_mul, CandidateCost, Error, Limits, Result};
use mathsvg_dsl::{CorrectionKind, Node, Program, Recurrence};
use mathsvg_evaluator::evaluate_program;
use mathsvg_functions::{
    search_block as search_functions, CandidateKey as FunctionCandidateKey,
    LedgerEntry as FunctionLedgerEntry, LedgerStatus as FunctionLedgerStatus,
    SearchConfig as FunctionSearchConfig, SearchResult as FunctionSearchResult,
};

/// Aggregate finite-oracle gain on the retained real development winner.
pub const PHASE3_DEPTH_0_TO_2_HEADROOM_BYTES: u64 = 115;
pub const HARD_MAX_RESIDUAL_DEPTH: u8 = 8;
pub const HARD_MAX_PROJECTION_DESCRIPTORS: u32 = 4096;
pub const HARD_MAX_RECURRENCE_ORDER: u8 = 8;
pub const HARD_MAX_COEFFICIENT_VALUES: usize = 16;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ArplGate {
    OracleDisabled,
    Experimental,
}

impl ArplGate {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::OracleDisabled => "oracle-disabled",
            Self::Experimental => "experimental",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ResidualDomain {
    AddMod256,
    Xor,
}

impl ResidualDomain {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::AddMod256 => "add-mod-256",
            Self::Xor => "xor",
        }
    }

    const fn correction_kind(self) -> CorrectionKind {
        match self {
            Self::AddMod256 => CorrectionKind::Add,
            Self::Xor => CorrectionKind::Xor,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ProjectionFamily {
    Const,
    Linear,
    Periodic,
    Recurrence,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ProjectionKey {
    pub family: ProjectionFamily,
    pub parameters: Vec<u64>,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct LayerKey {
    pub domain: ResidualDomain,
    pub projection: ProjectionKey,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ResidualLeaf {
    Literal,
    FunctionWinner(FunctionCandidateKey),
    /// A classified nested function descriptor retained for ledger audit.
    FunctionCandidate(FunctionCandidateKey),
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ResidualCandidateKey {
    pub layers: Vec<LayerKey>,
    pub leaf: ResidualLeaf,
}

/// Only the current winner is retained after search.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResidualCandidate {
    pub key: ResidualCandidateKey,
    pub program: Program,
    pub cost: CandidateCost,
    pub archive: Vec<u8>,
    pub residual_depth: u8,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LedgerStatus {
    Tried,
    SafePrune,
    HeuristicSkip,
    BudgetStop,
    DepthStop,
    RepeatedStateStop,
    ProfileDisabled,
}

impl LedgerStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Tried => "TRIED",
            Self::SafePrune => "SAFE_PRUNE",
            Self::HeuristicSkip => "HEURISTIC_SKIP",
            Self::BudgetStop => "BUDGET_STOP",
            Self::DepthStop => "DEPTH_STOP",
            Self::RepeatedStateStop => "REPEATED_STATE_STOP",
            Self::ProfileDisabled => "PROFILE_DISABLED",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LedgerReason {
    BaselineLiteral,
    BaselineFunctionWinner,
    ProjectionExpanded,
    ProjectionCatalogueBuilt,
    LeafLiteral,
    LeafFunctionWinner,
    OracleNoHeadroom,
    DepthLimit,
    MinimumGain,
    RepeatedState,
    ProjectionPeriodLimit,
    ProjectionRecurrenceOrderLimit,
    ProjectionCoefficientCatalogueEmpty,
    ProjectionDescriptorLimit,
    WorkBudget,
    StateBudget,
    StateBytesBudget,
    LedgerBudget,
    CandidateDecoderLimit,
    NestedFunctionEvent,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResidualLedgerEntry {
    pub event_id: u64,
    pub path: Vec<LayerKey>,
    pub status: LedgerStatus,
    pub reason: LedgerReason,
    pub depth: u8,
    /// Stable diagnostic digest. Repeated-state decisions compare full bytes.
    pub state_digest: u64,
    pub work_before: u64,
    pub work_charged: u64,
    pub states_before: u32,
    pub actual_archive_bytes: Option<u64>,
    pub upper_bound_at_decision: u64,
    pub became_best: bool,
    /// Complete candidate identity for leaf rows; projection/stop rows use
    /// `None`.
    pub candidate_key: Option<ResidualCandidateKey>,
    /// Function descriptors represented by a nested classification row.
    pub represented_descriptors: u64,
    /// Exact nested classification, including its key, reason and lower bound.
    pub nested_function_entry: Option<FunctionLedgerEntry>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResidualConfig {
    pub gate: ArplGate,
    pub max_depth: u8,
    pub domains: Vec<ResidualDomain>,
    pub max_projection_period: u32,
    pub max_recurrence_order: u8,
    pub recurrence_coefficients: Vec<u8>,
    pub max_projection_descriptors: u32,
    pub max_states: u32,
    pub max_state_bytes: u64,
    /// Hard budget for declared search units: catalogue construction,
    /// projection evaluation/residual formation and nested function fitting.
    /// Canonical serialization work is bounded separately by state, ledger
    /// and decoder limits and is not included in this counter.
    pub work_budget: u64,
    pub max_ledger_entries: u32,
    /// Required complete-archive byte improvement before a branch may deepen.
    pub minimum_gain_bytes: u64,
    pub function_search: FunctionSearchConfig,
}

impl Default for ResidualConfig {
    fn default() -> Self {
        Self {
            gate: ArplGate::OracleDisabled,
            max_depth: 0,
            domains: vec![ResidualDomain::AddMod256, ResidualDomain::Xor],
            max_projection_period: 16,
            max_recurrence_order: 2,
            recurrence_coefficients: vec![0, 1, 255],
            max_projection_descriptors: 128,
            max_states: 64,
            max_state_bytes: 64 * 1024 * 1024,
            work_budget: 1 << 24,
            max_ledger_entries: 4096,
            minimum_gain_bytes: 1,
            function_search: FunctionSearchConfig::default(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResidualResult {
    pub winner: ResidualCandidate,
    pub literal_archive_bytes: u64,
    pub function_archive_bytes: u64,
    pub baseline_function_complete: bool,
    pub ledger: Vec<ResidualLedgerEntry>,
    /// Consumed units from the narrowly defined [`ResidualConfig::work_budget`].
    pub work_used: u64,
    pub states_visited: u32,
    pub state_bytes_peak: u64,
    pub budget_exhausted: bool,
    pub heuristic_omission: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum ProjectionDescriptor {
    Const(u8),
    Linear { a: u8, b: u8 },
    Periodic { period: usize },
    Recurrence { coefficients: Vec<u8> },
}

impl ProjectionDescriptor {
    fn key(&self) -> ProjectionKey {
        match self {
            Self::Const(value) => ProjectionKey {
                family: ProjectionFamily::Const,
                parameters: vec![u64::from(*value)],
            },
            Self::Linear { a, b } => ProjectionKey {
                family: ProjectionFamily::Linear,
                parameters: vec![u64::from(*a), u64::from(*b)],
            },
            Self::Periodic { period } => ProjectionKey {
                family: ProjectionFamily::Periodic,
                parameters: vec![*period as u64],
            },
            Self::Recurrence { coefficients } => {
                let mut parameters = Vec::with_capacity(coefficients.len() + 1);
                parameters.push(coefficients.len() as u64);
                parameters.extend(coefficients.iter().map(|value| u64::from(*value)));
                ProjectionKey {
                    family: ProjectionFamily::Recurrence,
                    parameters,
                }
            }
        }
    }

    fn node(&self, state: &[u8]) -> Node {
        match self {
            Self::Const(value) => Node::Const {
                length: state.len() as u64,
                value: *value,
            },
            Self::Linear { a, b } => Node::Linear {
                count: state.len() as u64,
                width: 8,
                modulus: 256,
                a: u64::from(*a),
                b: u64::from(*b),
            },
            Self::Periodic { period } => {
                let repetitions = state.len() / *period;
                let repeated_bytes = repetitions * *period;
                Node::Periodic {
                    pattern: state[..*period].to_vec(),
                    repetitions: repetitions as u64,
                    suffix: state[repeated_bytes..].to_vec(),
                }
            }
            Self::Recurrence { coefficients } => {
                let order = coefficients.len();
                Node::Recurrence(Recurrence {
                    count: state.len() as u64,
                    width: 8,
                    modulus: 256,
                    coefficients: coefficients.iter().map(|value| u64::from(*value)).collect(),
                    initial_state: state[..order]
                        .iter()
                        .map(|value| u64::from(*value))
                        .collect(),
                })
            }
        }
    }

    fn retained_parameter_bytes(&self, state_length: usize) -> u64 {
        match self {
            Self::Const(_) => 1,
            Self::Linear { .. } => 2,
            Self::Periodic { period } => {
                let suffix = state_length % *period;
                (*period + suffix) as u64
            }
            Self::Recurrence { coefficients } => (coefficients.len() * 2) as u64,
        }
    }
}

#[derive(Debug)]
struct ProjectionCatalogue {
    descriptors: Vec<ProjectionDescriptor>,
    omissions: Vec<CatalogueOmission>,
}

#[derive(Clone, Copy, Debug)]
struct CatalogueOmission {
    reason: LedgerReason,
    represented_descriptors: u64,
}

#[derive(Clone, Debug)]
struct Layer {
    key: LayerKey,
    prediction: Node,
    retained_parameter_bytes: u64,
}

#[derive(Clone, Copy, Debug)]
struct LedgerDecision {
    work_before: u64,
    work_charged: u64,
    actual_archive_bytes: Option<u64>,
    upper_bound_at_decision: u64,
    became_best: bool,
}

#[derive(Clone, Copy, Debug)]
struct CandidateDecision {
    reason: LedgerReason,
    work_before: u64,
    work_charged: u64,
}

#[derive(Clone, Copy, Debug)]
struct PathFrontier {
    live_state_bytes: u64,
    live_parameter_bytes: u64,
    parent_archive_bytes: u64,
}

struct Context<'a> {
    original: &'a [u8],
    config: ResidualConfig,
    limits: Limits,
    domains: Vec<ResidualDomain>,
    coefficients: Vec<u8>,
    best: ResidualCandidate,
    ledger: Vec<ResidualLedgerEntry>,
    work_used: u64,
    states_visited: u32,
    state_bytes_peak: u64,
    hard_stop: bool,
    budget_exhausted: bool,
    heuristic_omission: bool,
}

fn validate_config(
    input: &[u8],
    config: &ResidualConfig,
    limits: &Limits,
) -> Result<(Vec<ResidualDomain>, Vec<u8>)> {
    if input.is_empty() {
        return Err(Error::InvalidValue(
            "residual search accepts non-empty v1 blocks only",
        ));
    }
    limits.check(
        "block output bytes",
        input.len() as u64,
        limits.max_block_output_bytes,
    )?;
    if config.max_depth > HARD_MAX_RESIDUAL_DEPTH {
        return Err(Error::LimitExceeded {
            what: "residual depth",
            actual: u64::from(config.max_depth),
            limit: u64::from(HARD_MAX_RESIDUAL_DEPTH),
        });
    }
    if config.max_projection_descriptors == 0 {
        return Err(Error::InvalidValue(
            "residual projection catalogue must be non-empty",
        ));
    }
    if config.max_projection_descriptors > HARD_MAX_PROJECTION_DESCRIPTORS {
        return Err(Error::LimitExceeded {
            what: "projection descriptors",
            actual: u64::from(config.max_projection_descriptors),
            limit: u64::from(HARD_MAX_PROJECTION_DESCRIPTORS),
        });
    }
    if config.max_recurrence_order > HARD_MAX_RECURRENCE_ORDER {
        return Err(Error::LimitExceeded {
            what: "residual recurrence order",
            actual: u64::from(config.max_recurrence_order),
            limit: u64::from(HARD_MAX_RECURRENCE_ORDER),
        });
    }
    limits.check(
        "residual recurrence order",
        u64::from(config.max_recurrence_order),
        limits.max_recurrence_order,
    )?;
    limits.check(
        "residual projection period",
        u64::from(config.max_projection_period),
        limits.max_pattern_bytes,
    )?;
    if config.recurrence_coefficients.len() > HARD_MAX_COEFFICIENT_VALUES {
        return Err(Error::LimitExceeded {
            what: "residual coefficient catalogue values",
            actual: config.recurrence_coefficients.len() as u64,
            limit: HARD_MAX_COEFFICIENT_VALUES as u64,
        });
    }
    if config.max_states == 0 {
        return Err(Error::InvalidValue(
            "residual state budget must include the root state",
        ));
    }
    limits.check(
        "residual states",
        u64::from(config.max_states),
        limits.max_nodes,
    )?;
    if config.max_state_bytes == 0 {
        return Err(Error::InvalidValue(
            "residual live-state byte budget must be positive",
        ));
    }
    if config.max_ledger_entries < 4 {
        return Err(Error::InvalidValue(
            "residual ledger must retain baselines and a stop row",
        ));
    }
    limits.check(
        "residual ledger rows",
        u64::from(config.max_ledger_entries),
        limits.max_nodes,
    )?;

    let mut domains = config.domains.clone();
    domains.sort_unstable();
    domains.dedup();
    if config.gate == ArplGate::Experimental && config.max_depth > 0 && domains.is_empty() {
        return Err(Error::InvalidValue(
            "experimental residual search requires a correction domain",
        ));
    }

    let mut coefficients = config.recurrence_coefficients.clone();
    coefficients.sort_unstable();
    coefficients.dedup();
    Ok((domains, coefficients))
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

fn into_program_child(program: Program) -> Result<Node> {
    match program.root {
        Node::File { child, .. } => Ok(*child),
        _ => Err(Error::InvalidValue("program did not have a FILE root")),
    }
}

fn wrap_layers(length: usize, layers: &[Layer], leaf: Node) -> Program {
    let mut child = leaf;
    for layer in layers.iter().rev() {
        child = Node::Correct {
            kind: layer.key.domain.correction_kind(),
            prediction: Box::new(layer.prediction.clone()),
            correction: Box::new(child),
        };
    }
    file_program(length, child)
}

fn measure(
    original: &[u8],
    key: ResidualCandidateKey,
    program: Program,
    limits: &Limits,
) -> Result<ResidualCandidate> {
    let report = program.validate(limits)?;
    let archive = encode_archive(
        &[ArchiveBlock {
            program: &program,
            restored: original,
        }],
        limits,
    )?;
    let archive_bytes = u64::try_from(archive.len()).map_err(|_| Error::IntegerOverflow {
        context: "residual archive length",
    })?;
    let decode_memory = checked_u64_add(
        report.original_bytes,
        report.temporary_bytes,
        "residual candidate decode memory",
    )?;
    let cost = program.candidate_cost(archive_bytes, decode_memory, limits)?;
    Ok(ResidualCandidate {
        residual_depth: key.layers.len() as u8,
        key,
        program,
        cost,
        archive,
    })
}

fn stable_digest(bytes: &[u8]) -> u64 {
    let mut hash = 0xcbf2_9ce4_8422_2325u64;
    for &byte in bytes {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash ^ (bytes.len() as u64).rotate_left(17)
}

fn modal_byte(input: &[u8]) -> u8 {
    let mut counts = [0u64; 256];
    for &value in input {
        counts[usize::from(value)] += 1;
    }
    let mut best_value = 0u8;
    let mut best_count = 0u64;
    for value in 0u8..=u8::MAX {
        let count = counts[usize::from(value)];
        if count > best_count {
            best_count = count;
            best_value = value;
        }
    }
    best_value
}

fn catalogue(state: &[u8], config: &ResidualConfig, coefficients: &[u8]) -> ProjectionCatalogue {
    let maximum = config.max_projection_descriptors as usize;
    let meaningful_period = state.len() / 2;
    let tested_period = meaningful_period.min(config.max_projection_period as usize);
    let meaningful_order = state
        .len()
        .saturating_sub(1)
        .min(usize::from(HARD_MAX_RECURRENCE_ORDER));
    let tested_order = meaningful_order.min(usize::from(config.max_recurrence_order));

    let mut declared_descriptors = 1u64 + u64::from(state.len() >= 2);
    declared_descriptors = declared_descriptors.saturating_add(tested_period as u64);
    if !coefficients.is_empty() {
        for order in 1..=tested_order {
            declared_descriptors =
                declared_descriptors.saturating_add(catalogue_power(coefficients.len(), order));
        }
    }

    let mut omissions = Vec::new();
    if tested_period < meaningful_period {
        omissions.push(CatalogueOmission {
            reason: LedgerReason::ProjectionPeriodLimit,
            represented_descriptors: (meaningful_period - tested_period) as u64,
        });
    }
    if tested_order < meaningful_order && !coefficients.is_empty() {
        let represented_descriptors = ((tested_order + 1)..=meaningful_order)
            .fold(0u64, |total, order| {
                total.saturating_add(catalogue_power(coefficients.len(), order))
            });
        omissions.push(CatalogueOmission {
            reason: LedgerReason::ProjectionRecurrenceOrderLimit,
            represented_descriptors,
        });
    }
    if coefficients.is_empty() && tested_order > 0 {
        omissions.push(CatalogueOmission {
            reason: LedgerReason::ProjectionCoefficientCatalogueEmpty,
            represented_descriptors: tested_order as u64,
        });
    }
    if declared_descriptors > maximum as u64 {
        omissions.push(CatalogueOmission {
            reason: LedgerReason::ProjectionDescriptorLimit,
            represented_descriptors: declared_descriptors - maximum as u64,
        });
    }

    let mut descriptors = Vec::with_capacity(
        usize::try_from(declared_descriptors.min(maximum as u64)).unwrap_or(maximum),
    );
    descriptors.push(ProjectionDescriptor::Const(modal_byte(state)));
    if state.len() >= 2 && descriptors.len() < maximum {
        descriptors.push(ProjectionDescriptor::Linear {
            a: state[1].wrapping_sub(state[0]),
            b: state[0],
        });
    }

    for period in 1..=tested_period {
        if descriptors.len() == maximum {
            break;
        }
        descriptors.push(ProjectionDescriptor::Periodic { period });
    }

    if !coefficients.is_empty() {
        'orders: for order in 1..=tested_order {
            let mut indices = vec![0usize; order];
            loop {
                if descriptors.len() == maximum {
                    break 'orders;
                }
                let values = indices.iter().map(|index| coefficients[*index]).collect();
                descriptors.push(ProjectionDescriptor::Recurrence {
                    coefficients: values,
                });

                let mut position = order;
                while position > 0 {
                    position -= 1;
                    indices[position] += 1;
                    if indices[position] < coefficients.len() {
                        break;
                    }
                    indices[position] = 0;
                }
                if position == 0 && indices[0] == 0 {
                    break;
                }
            }
        }
    }

    ProjectionCatalogue {
        descriptors,
        omissions,
    }
}

fn catalogue_power(base: usize, exponent: usize) -> u64 {
    (0..exponent).fold(1u64, |value, _| value.saturating_mul(base as u64))
}

fn catalogue_work_upper_bound(
    state_length: usize,
    config: &ResidualConfig,
    coefficient_count: usize,
) -> Result<u64> {
    let maximum = config.max_projection_descriptors as usize;
    let mut remaining = maximum;
    let mut work = state_length as u64;

    let fixed_descriptors = (1 + usize::from(state_length >= 2)).min(remaining);
    remaining -= fixed_descriptors;
    work = checked_u64_add(work, fixed_descriptors as u64, "residual catalogue work")?;

    let periodic_descriptors = (state_length / 2)
        .min(config.max_projection_period as usize)
        .min(remaining);
    remaining -= periodic_descriptors;
    work = checked_u64_add(work, periodic_descriptors as u64, "residual catalogue work")?;

    let recurrence_orders = state_length
        .saturating_sub(1)
        .min(usize::from(config.max_recurrence_order));
    if coefficient_count == 0 {
        return Ok(work);
    }
    for order in 1..=recurrence_orders {
        if remaining == 0 {
            break;
        }
        let mut combinations = 1usize;
        for _ in 0..order {
            combinations = combinations
                .saturating_mul(coefficient_count)
                .min(remaining);
        }
        let descriptors = combinations.min(remaining);
        remaining -= descriptors;
        let per_descriptor = (order as u64)
            .checked_mul(2)
            .and_then(|value| value.checked_add(1))
            .ok_or(Error::IntegerOverflow {
                context: "residual catalogue descriptor work",
            })?;
        work = checked_u64_add(
            work,
            checked_u64_mul(
                descriptors as u64,
                per_descriptor,
                "residual catalogue recurrence work",
            )?,
            "residual catalogue work",
        )?;
    }
    Ok(work)
}

fn residual_bytes(
    domain: ResidualDomain,
    state: &[u8],
    mut prediction: Vec<u8>,
) -> Result<Vec<u8>> {
    if state.len() != prediction.len() {
        return Err(Error::LengthMismatch {
            context: "projection output",
            expected: state.len() as u64,
            actual: prediction.len() as u64,
        });
    }
    match domain {
        ResidualDomain::AddMod256 => {
            for (projected, actual) in prediction.iter_mut().zip(state) {
                *projected = actual.wrapping_sub(*projected);
            }
        }
        ResidualDomain::Xor => {
            for (projected, actual) in prediction.iter_mut().zip(state) {
                *projected ^= actual;
            }
        }
    }
    Ok(prediction)
}

fn has_required_gain(candidate_bytes: u64, parent_bytes: u64, minimum_gain_bytes: u64) -> bool {
    candidate_bytes < parent_bytes
        && parent_bytes.saturating_sub(candidate_bytes) >= minimum_gain_bytes
}

impl Context<'_> {
    fn upper_bound(&self) -> u64 {
        self.best.cost.archive_bytes
    }

    fn path_keys(layers: &[Layer]) -> Vec<LayerKey> {
        layers.iter().map(|layer| layer.key.clone()).collect()
    }

    fn push_entry(
        &mut self,
        path: Vec<LayerKey>,
        status: LedgerStatus,
        reason: LedgerReason,
        state: &[u8],
        decision: LedgerDecision,
    ) {
        let event_id = self.ledger.len() as u64;
        self.ledger.push(ResidualLedgerEntry {
            event_id,
            depth: path.len() as u8,
            path,
            status,
            reason,
            state_digest: stable_digest(state),
            work_before: decision.work_before,
            work_charged: decision.work_charged,
            states_before: self.states_visited,
            actual_archive_bytes: decision.actual_archive_bytes,
            upper_bound_at_decision: decision.upper_bound_at_decision,
            became_best: decision.became_best,
            candidate_key: None,
            represented_descriptors: 0,
            nested_function_entry: None,
        });
    }

    fn annotate_last_candidate(&mut self, candidate_key: ResidualCandidateKey) {
        if let Some(entry) = self.ledger.last_mut() {
            entry.candidate_key = Some(candidate_key);
        }
    }

    fn append_function_classifications(
        &mut self,
        function_ledger: &[FunctionLedgerEntry],
        layers: &[Layer],
        state: &[u8],
    ) -> bool {
        for nested in function_ledger
            .iter()
            .filter(|entry| entry.status != FunctionLedgerStatus::Tried)
        {
            if !self.ensure_slots(1, layers, state) {
                return false;
            }
            let status = match nested.status {
                FunctionLedgerStatus::Tried => LedgerStatus::Tried,
                FunctionLedgerStatus::SafePrune => LedgerStatus::SafePrune,
                FunctionLedgerStatus::HeuristicSkip => LedgerStatus::HeuristicSkip,
                FunctionLedgerStatus::BudgetStop => LedgerStatus::BudgetStop,
            };
            let candidate_key = ResidualCandidateKey {
                layers: Self::path_keys(layers),
                leaf: ResidualLeaf::FunctionCandidate(nested.key.clone()),
            };
            self.push_entry(
                candidate_key.layers.clone(),
                status,
                LedgerReason::NestedFunctionEvent,
                state,
                LedgerDecision {
                    work_before: self.work_used,
                    work_charged: 0,
                    actual_archive_bytes: None,
                    upper_bound_at_decision: self.upper_bound(),
                    became_best: false,
                },
            );
            if let Some(entry) = self.ledger.last_mut() {
                entry.candidate_key = Some(candidate_key);
                entry.represented_descriptors = nested.represented_descriptors;
                entry.nested_function_entry = Some(nested.clone());
            }
        }
        true
    }

    fn stop_global(&mut self, layers: &[Layer], state: &[u8], reason: LedgerReason) {
        if self.ledger.len() < self.config.max_ledger_entries as usize {
            self.push_entry(
                Self::path_keys(layers),
                LedgerStatus::BudgetStop,
                reason,
                state,
                LedgerDecision {
                    work_before: self.work_used,
                    work_charged: 0,
                    actual_archive_bytes: None,
                    upper_bound_at_decision: self.upper_bound(),
                    became_best: false,
                },
            );
        }
        self.hard_stop = true;
        self.budget_exhausted = true;
    }

    fn ensure_slots(&mut self, count: usize, layers: &[Layer], state: &[u8]) -> bool {
        let maximum = self.config.max_ledger_entries as usize;
        let consumes_stop_row = match self.ledger.len().checked_add(count) {
            Some(needed) => needed >= maximum,
            None => true,
        };
        if consumes_stop_row {
            self.stop_global(layers, state, LedgerReason::LedgerBudget);
            false
        } else {
            true
        }
    }

    fn charge_work(&mut self, amount: u64, layers: &[Layer], state: &[u8]) -> Option<u64> {
        let before = self.work_used;
        let Some(next) = before.checked_add(amount) else {
            self.stop_global(layers, state, LedgerReason::WorkBudget);
            return None;
        };
        if next > self.config.work_budget {
            self.stop_global(layers, state, LedgerReason::WorkBudget);
            return None;
        }
        self.work_used = next;
        Some(before)
    }

    fn measure_candidate(
        &mut self,
        state: &[u8],
        key: ResidualCandidateKey,
        program: Program,
        limit_work_before: u64,
        limit_work_charged: u64,
    ) -> Result<Option<ResidualCandidate>> {
        let retained_key = key.clone();
        match measure(self.original, key, program, &self.limits) {
            Ok(candidate) => Ok(Some(candidate)),
            Err(Error::LimitExceeded { .. }) => {
                self.budget_exhausted = true;
                self.push_entry(
                    retained_key.layers.clone(),
                    LedgerStatus::BudgetStop,
                    LedgerReason::CandidateDecoderLimit,
                    state,
                    LedgerDecision {
                        work_before: limit_work_before,
                        work_charged: limit_work_charged,
                        actual_archive_bytes: None,
                        upper_bound_at_decision: self.upper_bound(),
                        became_best: false,
                    },
                );
                self.annotate_last_candidate(retained_key);
                Ok(None)
            }
            Err(error) => Err(error),
        }
    }

    fn consider(
        &mut self,
        candidate: ResidualCandidate,
        state: &[u8],
        decision: CandidateDecision,
    ) -> bool {
        let upper = self.upper_bound();
        let strictly_better = candidate.cost < self.best.cost;
        let became_best = strictly_better;
        let path = candidate.key.layers.clone();
        let candidate_key = candidate.key.clone();
        let actual = candidate.cost.archive_bytes;
        if became_best {
            self.best = candidate;
        }
        self.push_entry(
            path,
            LedgerStatus::Tried,
            decision.reason,
            state,
            LedgerDecision {
                work_before: decision.work_before,
                work_charged: decision.work_charged,
                actual_archive_bytes: Some(actual),
                upper_bound_at_decision: upper,
                became_best,
            },
        );
        self.annotate_last_candidate(candidate_key);
        became_best
    }

    fn evaluate_leaf(&mut self, state: &[u8], layers: &[Layer]) -> Result<Option<u64>> {
        if !self.ensure_slots(3, layers, state) {
            return Ok(None);
        }
        let layer_keys = Self::path_keys(layers);
        let literal_key = ResidualCandidateKey {
            layers: layer_keys.clone(),
            leaf: ResidualLeaf::Literal,
        };
        let literal = self.measure_candidate(
            state,
            literal_key,
            wrap_layers(self.original.len(), layers, Node::Literal(state.to_vec())),
            self.work_used,
            0,
        )?;
        let literal_archive_bytes = literal.map(|literal| {
            let archive_bytes = literal.cost.archive_bytes;
            self.consider(
                literal,
                state,
                CandidateDecision {
                    reason: LedgerReason::LeafLiteral,
                    work_before: self.work_used,
                    work_charged: 0,
                },
            );
            archive_bytes
        });

        let remaining = self.config.work_budget.saturating_sub(self.work_used);
        let mut function_config = self.config.function_search.clone();
        function_config.work_budget = function_config.work_budget.min(remaining);
        let function_result = match search_functions(state, &function_config, &self.limits) {
            Ok(result) => result,
            Err(Error::LimitExceeded { .. }) => {
                self.budget_exhausted = true;
                self.push_entry(
                    layer_keys,
                    LedgerStatus::BudgetStop,
                    LedgerReason::CandidateDecoderLimit,
                    state,
                    LedgerDecision {
                        work_before: self.work_used,
                        work_charged: 0,
                        actual_archive_bytes: None,
                        upper_bound_at_decision: self.upper_bound(),
                        became_best: false,
                    },
                );
                return Ok(literal_archive_bytes);
            }
            Err(error) => return Err(error),
        };
        let FunctionSearchResult {
            winner: function_winner,
            work_used: function_work,
            budget_exhausted: function_budget_exhausted,
            complete_within_declared_catalogue: function_complete,
            ledger: function_ledger,
            ..
        } = function_result;
        let Some(work_before) = self.charge_work(function_work, layers, state) else {
            return Ok(None);
        };
        if function_budget_exhausted {
            self.heuristic_omission = true;
            self.budget_exhausted = true;
        }
        if !function_complete {
            self.heuristic_omission = true;
        }
        let function_key = ResidualCandidateKey {
            layers: layer_keys,
            leaf: ResidualLeaf::FunctionWinner(function_winner.key.clone()),
        };
        let function_candidate = self.measure_candidate(
            state,
            function_key,
            wrap_layers(
                self.original.len(),
                layers,
                into_program_child(function_winner.program)?,
            ),
            work_before,
            function_work,
        )?;
        let function_archive_bytes = function_candidate.map(|function_candidate| {
            let archive_bytes = function_candidate.cost.archive_bytes;
            self.consider(
                function_candidate,
                state,
                CandidateDecision {
                    reason: LedgerReason::LeafFunctionWinner,
                    work_before,
                    work_charged: function_work,
                },
            );
            archive_bytes
        });
        self.append_function_classifications(&function_ledger, layers, state);

        Ok(match (literal_archive_bytes, function_archive_bytes) {
            (Some(literal), Some(function)) => Some(literal.min(function)),
            (Some(literal), None) => Some(literal),
            (None, Some(function)) => Some(function),
            (None, None) => None,
        })
    }

    fn explore(
        &mut self,
        state: &[u8],
        layers: &mut Vec<Layer>,
        path_states: &mut Vec<Rc<Vec<u8>>>,
        frontier: PathFrontier,
    ) -> Result<()> {
        if self.hard_stop {
            return Ok(());
        }
        let depth = layers.len() as u8;
        if depth >= self.config.max_depth {
            if self.ensure_slots(1, layers, state) {
                self.push_entry(
                    Self::path_keys(layers),
                    LedgerStatus::DepthStop,
                    LedgerReason::DepthLimit,
                    state,
                    LedgerDecision {
                        work_before: self.work_used,
                        work_charged: 0,
                        actual_archive_bytes: None,
                        upper_bound_at_decision: self.upper_bound(),
                        became_best: false,
                    },
                );
            }
            return Ok(());
        }

        let catalogue_work =
            catalogue_work_upper_bound(state.len(), &self.config, self.coefficients.len())?;
        if !self.ensure_slots(1, layers, state) {
            return Ok(());
        }
        let Some(catalogue_work_before) = self.charge_work(catalogue_work, layers, state) else {
            return Ok(());
        };
        let projections = catalogue(state, &self.config, &self.coefficients);
        self.push_entry(
            Self::path_keys(layers),
            LedgerStatus::Tried,
            LedgerReason::ProjectionCatalogueBuilt,
            state,
            LedgerDecision {
                work_before: catalogue_work_before,
                work_charged: catalogue_work,
                actual_archive_bytes: None,
                upper_bound_at_decision: self.upper_bound(),
                became_best: false,
            },
        );
        for omission in &projections.omissions {
            if !self.ensure_slots(1, layers, state) {
                return Ok(());
            }
            self.heuristic_omission = true;
            self.push_entry(
                Self::path_keys(layers),
                LedgerStatus::HeuristicSkip,
                omission.reason,
                state,
                LedgerDecision {
                    work_before: self.work_used,
                    work_charged: 0,
                    actual_archive_bytes: None,
                    upper_bound_at_decision: self.upper_bound(),
                    became_best: false,
                },
            );
            if let Some(entry) = self.ledger.last_mut() {
                entry.represented_descriptors = omission.represented_descriptors;
            }
        }

        for domain in self.domains.clone() {
            for descriptor in &projections.descriptors {
                if self.hard_stop {
                    return Ok(());
                }
                if !self.ensure_slots(5, layers, state) {
                    return Ok(());
                }
                if self.states_visited >= self.config.max_states {
                    self.stop_global(layers, state, LedgerReason::StateBudget);
                    return Ok(());
                }

                let parameter_bytes = descriptor.retained_parameter_bytes(state.len());
                let next_live_state = checked_u64_add(
                    frontier.live_state_bytes,
                    state.len() as u64,
                    "residual live state bytes",
                )?;
                let next_live_parameters = checked_u64_add(
                    frontier.live_parameter_bytes,
                    parameter_bytes,
                    "residual live parameter bytes",
                )?;
                let next_live =
                    checked_u64_add(next_live_state, next_live_parameters, "residual live bytes")?;
                let layer_key = LayerKey {
                    domain,
                    projection: descriptor.key(),
                };
                let mut prospective_keys = Self::path_keys(layers);
                prospective_keys.push(layer_key.clone());
                if next_live > self.config.max_state_bytes {
                    self.budget_exhausted = true;
                    self.push_entry(
                        prospective_keys,
                        LedgerStatus::BudgetStop,
                        LedgerReason::StateBytesBudget,
                        state,
                        LedgerDecision {
                            work_before: self.work_used,
                            work_charged: 0,
                            actual_archive_bytes: None,
                            upper_bound_at_decision: self.upper_bound(),
                            became_best: false,
                        },
                    );
                    continue;
                }

                let projection_program = file_program(state.len(), descriptor.node(state));
                let projection_report = match projection_program.validate(&self.limits) {
                    Ok(report) => report,
                    Err(Error::LimitExceeded { .. }) => {
                        self.budget_exhausted = true;
                        self.push_entry(
                            prospective_keys,
                            LedgerStatus::BudgetStop,
                            LedgerReason::CandidateDecoderLimit,
                            state,
                            LedgerDecision {
                                work_before: self.work_used,
                                work_charged: 0,
                                actual_archive_bytes: None,
                                upper_bound_at_decision: self.upper_bound(),
                                became_best: false,
                            },
                        );
                        continue;
                    }
                    Err(error) => return Err(error),
                };
                let branch_work = checked_u64_add(
                    projection_report.decode_work,
                    state.len() as u64,
                    "residual projection work",
                )?;
                let Some(work_before) = self.charge_work(branch_work, layers, state) else {
                    return Ok(());
                };
                let prediction = match evaluate_program(&projection_program, &self.limits) {
                    Ok(prediction) => prediction,
                    Err(Error::LimitExceeded { .. }) => {
                        self.budget_exhausted = true;
                        self.push_entry(
                            prospective_keys,
                            LedgerStatus::BudgetStop,
                            LedgerReason::CandidateDecoderLimit,
                            state,
                            LedgerDecision {
                                work_before,
                                work_charged: branch_work,
                                actual_archive_bytes: None,
                                upper_bound_at_decision: self.upper_bound(),
                                became_best: false,
                            },
                        );
                        continue;
                    }
                    Err(error) => return Err(error),
                };
                let next = residual_bytes(domain, state, prediction)?;

                // Digest is diagnostic; this complete byte comparison is the
                // normative repeated-state decision.
                if path_states
                    .iter()
                    .any(|prior| prior.as_slice() == next.as_slice())
                {
                    self.push_entry(
                        prospective_keys,
                        LedgerStatus::RepeatedStateStop,
                        LedgerReason::RepeatedState,
                        &next,
                        LedgerDecision {
                            work_before,
                            work_charged: branch_work,
                            actual_archive_bytes: None,
                            upper_bound_at_decision: self.upper_bound(),
                            became_best: false,
                        },
                    );
                    continue;
                }

                let prediction_node = into_program_child(projection_program)?;
                let next = Rc::new(next);
                self.state_bytes_peak = self.state_bytes_peak.max(next_live);
                self.push_entry(
                    prospective_keys,
                    LedgerStatus::Tried,
                    LedgerReason::ProjectionExpanded,
                    next.as_slice(),
                    LedgerDecision {
                        work_before,
                        work_charged: branch_work,
                        actual_archive_bytes: None,
                        upper_bound_at_decision: self.upper_bound(),
                        became_best: false,
                    },
                );
                self.states_visited += 1;
                layers.push(Layer {
                    key: layer_key,
                    prediction: prediction_node,
                    retained_parameter_bytes: parameter_bytes,
                });
                path_states.push(Rc::clone(&next));

                let local_archive_bytes = self.evaluate_leaf(next.as_slice(), layers)?;
                let improved_archive_bytes = local_archive_bytes.filter(|archive_bytes| {
                    has_required_gain(
                        *archive_bytes,
                        frontier.parent_archive_bytes,
                        self.config.minimum_gain_bytes,
                    )
                });
                if self.hard_stop {
                    path_states.pop();
                    layers.pop();
                    return Ok(());
                }
                if layers.len() as u8 >= self.config.max_depth {
                    if self.ensure_slots(1, layers, next.as_slice()) {
                        self.push_entry(
                            Self::path_keys(layers),
                            LedgerStatus::DepthStop,
                            LedgerReason::DepthLimit,
                            next.as_slice(),
                            LedgerDecision {
                                work_before: self.work_used,
                                work_charged: 0,
                                actual_archive_bytes: None,
                                upper_bound_at_decision: self.upper_bound(),
                                became_best: false,
                            },
                        );
                    }
                } else if let Some(parent_archive_bytes) = improved_archive_bytes {
                    let next_frontier = PathFrontier {
                        live_state_bytes: next_live_state,
                        live_parameter_bytes: next_live_parameters,
                        parent_archive_bytes,
                    };
                    self.explore(next.as_slice(), layers, path_states, next_frontier)?;
                } else if self.ensure_slots(1, layers, next.as_slice()) {
                    self.heuristic_omission = true;
                    self.push_entry(
                        Self::path_keys(layers),
                        LedgerStatus::HeuristicSkip,
                        LedgerReason::MinimumGain,
                        next.as_slice(),
                        LedgerDecision {
                            work_before: self.work_used,
                            work_charged: 0,
                            actual_archive_bytes: None,
                            upper_bound_at_decision: self.upper_bound(),
                            became_best: false,
                        },
                    );
                }
                path_states.pop();
                let removed = layers
                    .pop()
                    .ok_or(Error::InvalidValue("residual layer stack underflow"))?;
                if removed.retained_parameter_bytes != parameter_bytes {
                    return Err(Error::InvalidValue(
                        "residual layer parameter accounting changed",
                    ));
                }
            }
        }
        Ok(())
    }

    fn finish(
        self,
        literal_archive_bytes: u64,
        function_archive_bytes: u64,
        baseline_function_complete: bool,
    ) -> Result<ResidualResult> {
        let restored = evaluate_program(&self.best.program, &self.limits)?;
        if restored != self.original {
            return Err(Error::InvalidValue(
                "residual winner failed evaluator round-trip",
            ));
        }
        let repeated_archive = encode_archive(
            &[ArchiveBlock {
                program: &self.best.program,
                restored: self.original,
            }],
            &self.limits,
        )?;
        if repeated_archive != self.best.archive {
            return Err(Error::InvalidValue(
                "residual winner archive changed during re-encoding",
            ));
        }
        Ok(ResidualResult {
            winner: self.best,
            literal_archive_bytes,
            function_archive_bytes,
            baseline_function_complete,
            ledger: self.ledger,
            work_used: self.work_used,
            states_visited: self.states_visited,
            state_bytes_peak: self.state_bytes_peak,
            budget_exhausted: self.budget_exhausted,
            heuristic_omission: self.heuristic_omission,
        })
    }
}

/// Search a single non-empty block.
pub fn search_residual(
    input: &[u8],
    config: &ResidualConfig,
    limits: &Limits,
) -> Result<ResidualResult> {
    let (domains, coefficients) = validate_config(input, config, limits)?;

    let literal_key = ResidualCandidateKey {
        layers: Vec::new(),
        leaf: ResidualLeaf::Literal,
    };
    let literal = measure(input, literal_key, Program::literal(input.to_vec()), limits)?;
    let literal_archive_bytes = literal.cost.archive_bytes;
    let literal_candidate_key = literal.key.clone();

    let mut baseline_function_config = config.function_search.clone();
    baseline_function_config.work_budget =
        baseline_function_config.work_budget.min(config.work_budget);
    let (baseline_function_result, baseline_function_decoder_limited) =
        match search_functions(input, &baseline_function_config, limits) {
            Ok(result) => (result, false),
            Err(Error::LimitExceeded { .. }) => {
                let mut fallback_config = baseline_function_config.clone();
                fallback_config.work_budget = 0;
                (search_functions(input, &fallback_config, limits)?, true)
            }
            Err(error) => return Err(error),
        };
    let FunctionSearchResult {
        winner: function_winner,
        work_used: baseline_function_work,
        budget_exhausted: nested_baseline_budget_exhausted,
        complete_within_declared_catalogue: nested_baseline_complete,
        ledger: baseline_function_ledger,
        ..
    } = baseline_function_result;
    let baseline_function_budget_exhausted =
        nested_baseline_budget_exhausted || baseline_function_decoder_limited;
    let baseline_function_complete = nested_baseline_complete && !baseline_function_decoder_limited;
    let function_key = ResidualCandidateKey {
        layers: Vec::new(),
        leaf: ResidualLeaf::FunctionWinner(function_winner.key.clone()),
    };
    let function_candidate = measure(input, function_key, function_winner.program, limits)?;
    let function_archive_bytes = function_candidate.cost.archive_bytes;
    let function_candidate_key = function_candidate.key.clone();

    let function_became_best = function_candidate.cost < literal.cost;
    let best = if function_became_best {
        function_candidate
    } else {
        literal
    };
    let mut context = Context {
        original: input,
        config: config.clone(),
        limits: limits.clone(),
        domains,
        coefficients,
        best,
        ledger: Vec::new(),
        work_used: baseline_function_work,
        states_visited: 1,
        state_bytes_peak: input.len() as u64,
        hard_stop: false,
        budget_exhausted: baseline_function_budget_exhausted,
        heuristic_omission: !baseline_function_complete,
    };
    context.push_entry(
        Vec::new(),
        LedgerStatus::Tried,
        LedgerReason::BaselineLiteral,
        input,
        LedgerDecision {
            work_before: 0,
            work_charged: 0,
            actual_archive_bytes: Some(literal_archive_bytes),
            upper_bound_at_decision: literal_archive_bytes,
            became_best: true,
        },
    );
    context.annotate_last_candidate(literal_candidate_key);
    context.push_entry(
        Vec::new(),
        LedgerStatus::Tried,
        LedgerReason::BaselineFunctionWinner,
        input,
        LedgerDecision {
            work_before: 0,
            work_charged: baseline_function_work,
            actual_archive_bytes: Some(function_archive_bytes),
            upper_bound_at_decision: literal_archive_bytes,
            became_best: function_became_best,
        },
    );
    context.annotate_last_candidate(function_candidate_key);
    if baseline_function_decoder_limited && context.ensure_slots(1, &[], input) {
        context.push_entry(
            Vec::new(),
            LedgerStatus::BudgetStop,
            LedgerReason::CandidateDecoderLimit,
            input,
            LedgerDecision {
                work_before: context.work_used,
                work_charged: 0,
                actual_archive_bytes: None,
                upper_bound_at_decision: context.upper_bound(),
                became_best: false,
            },
        );
    }
    if !context.hard_stop {
        context.append_function_classifications(&baseline_function_ledger, &[], input);
    }

    if context.hard_stop {
        // The retained ledger budget was exhausted while importing nested
        // function classifications; the already-valid baseline remains.
    } else if input.len() as u64 > config.max_state_bytes {
        context.stop_global(&[], input, LedgerReason::StateBytesBudget);
    } else if config.gate == ArplGate::OracleDisabled {
        context.push_entry(
            Vec::new(),
            LedgerStatus::ProfileDisabled,
            LedgerReason::OracleNoHeadroom,
            input,
            LedgerDecision {
                work_before: context.work_used,
                work_charged: 0,
                actual_archive_bytes: None,
                upper_bound_at_decision: context.upper_bound(),
                became_best: false,
            },
        );
    } else if config.max_depth == 0 {
        context.push_entry(
            Vec::new(),
            LedgerStatus::DepthStop,
            LedgerReason::DepthLimit,
            input,
            LedgerDecision {
                work_before: context.work_used,
                work_charged: 0,
                actual_archive_bytes: None,
                upper_bound_at_decision: context.upper_bound(),
                became_best: false,
            },
        );
    } else {
        let mut layers = Vec::new();
        let mut path_states = vec![Rc::new(input.to_vec())];
        let frontier = PathFrontier {
            live_state_bytes: input.len() as u64,
            live_parameter_bytes: 0,
            parent_archive_bytes: context.upper_bound(),
        };
        context.explore(input, &mut layers, &mut path_states, frontier)?;
    }

    context.finish(
        literal_archive_bytes,
        function_archive_bytes,
        baseline_function_complete,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn projection_catalogue_is_finite_and_canonical() {
        let config = ResidualConfig {
            max_projection_period: 2,
            max_recurrence_order: 2,
            max_projection_descriptors: 32,
            ..ResidualConfig::default()
        };
        let full_catalogue = catalogue(&[7, 9, 1, 2, 3, 4, 5, 6], &config, &[0, 1]);

        assert_eq!(full_catalogue.descriptors.len(), 10);
        assert!(full_catalogue
            .descriptors
            .windows(2)
            .all(|window| window[0].key() < window[1].key()));
        assert_eq!(catalogue_work_upper_bound(8, &config, 2).unwrap(), 38);

        let capped_config = ResidualConfig {
            max_projection_period: 2,
            max_recurrence_order: 2,
            max_projection_descriptors: 4,
            ..ResidualConfig::default()
        };
        let capped = catalogue(&[7, 9, 1, 2, 3, 4, 5, 6], &capped_config, &[0, 1]);
        assert_eq!(capped.descriptors.len(), 4);
        assert!(capped.omissions.iter().any(|omission| {
            omission.reason == LedgerReason::ProjectionDescriptorLimit
                && omission.represented_descriptors == 6
        }));
        assert_eq!(
            catalogue_work_upper_bound(8, &capped_config, 2).unwrap(),
            12
        );
    }

    #[test]
    fn mixed_multi_layer_wrapping_reconstructs_in_inverse_order() {
        let add_prediction = vec![250u8, 1, 200, 128];
        let xor_prediction = vec![0xf0u8, 0x0f, 0xaa, 0x55];
        let leaf = vec![0x0fu8, 0xf0, 0x55, 0xaa];
        let inner: Vec<u8> = xor_prediction
            .iter()
            .zip(&leaf)
            .map(|(left, right)| left ^ right)
            .collect();
        let expected: Vec<u8> = add_prediction
            .iter()
            .zip(&inner)
            .map(|(left, right)| left.wrapping_add(*right))
            .collect();
        let layers = vec![
            Layer {
                key: LayerKey {
                    domain: ResidualDomain::AddMod256,
                    projection: ProjectionKey {
                        family: ProjectionFamily::Const,
                        parameters: Vec::new(),
                    },
                },
                prediction: Node::Literal(add_prediction),
                retained_parameter_bytes: 4,
            },
            Layer {
                key: LayerKey {
                    domain: ResidualDomain::Xor,
                    projection: ProjectionKey {
                        family: ProjectionFamily::Const,
                        parameters: Vec::new(),
                    },
                },
                prediction: Node::Literal(xor_prediction),
                retained_parameter_bytes: 4,
            },
        ];

        let program = wrap_layers(leaf.len(), &layers, Node::Literal(leaf));
        assert_eq!(
            evaluate_program(&program, &Limits::default()).unwrap(),
            expected
        );
    }
}
