//! Deterministic, bounded byte-function candidates for the minimal MathSVG
//! engine.
//!
//! Every winning decision uses a complete, canonically serialized
//! [`mathsvg_dsl::Program`]. Statistical observations are used only to derive
//! finite candidates (for example the modal constant); they never replace the
//! actual-size comparison.

#![forbid(unsafe_code)]

use std::collections::VecDeque;

use mathsvg_core::{checked_u64_add, checked_u64_mul, CandidateCost, Error, Limits, Result};
use mathsvg_dsl::{EncodedSections, Exception, Node, Program, Recurrence};
use mathsvg_entropy::{
    decode_exact, encode_best, inspect as inspect_entropy, BestEncoding,
    DecodeLimits as EntropyDecodeLimits, LeafCodec,
};

/// File header + one directory record + file footer.
///
/// The 72-byte block header is already included by
/// [`EncodedSections::v1_block_payload_bytes`].
pub const V1_SINGLE_BLOCK_OUTER_BYTES: u64 = 128 + 144 + 128;

/// A deliberately small implementation bound. Profiles may select less.
pub const HARD_MAX_RECURRENCE_ORDER: u8 = 8;

/// A deliberately small coefficient alphabet bound. Enumeration is additionally
/// stopped by the work and ledger budgets.
pub const HARD_MAX_COEFFICIENT_VALUES: usize = 16;

/// A deterministic bounded-search profile.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SearchConfig {
    /// Largest tested exact period. A meaningful candidate must repeat twice.
    pub max_period: u32,
    /// Largest tested restored-prior recurrence order.
    pub max_recurrence_order: u8,
    /// Finite coefficient alphabet, normalized to sorted unique byte values.
    pub recurrence_coefficients: Vec<u8>,
    /// Largest exception support retained for one current candidate.
    pub max_exceptions: u32,
    /// Predictor-fitting work units. Literal measurement is always permitted.
    pub work_budget: u64,
    /// Hard bound on retained ledger rows, including the literal and stop row.
    pub max_ledger_entries: u32,
}

impl Default for SearchConfig {
    fn default() -> Self {
        Self {
            max_period: 64,
            max_recurrence_order: 4,
            recurrence_coefficients: vec![0, 1, 2, 255],
            max_exceptions: 256,
            work_budget: 1 << 24,
            max_ledger_entries: 4096,
        }
    }
}

/// Stable candidate-family order. Exception variants immediately follow their
/// direct predictor so generation and audit ordering agree.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
#[repr(u8)]
pub enum CandidateFamily {
    Literal = 0x00,
    EntropyLiteral = 0x01,
    ConstExact = 0x10,
    ConstExceptions = 0x11,
    LinearExact = 0x20,
    LinearExceptions = 0x21,
    PeriodicExact = 0x30,
    PeriodicExceptions = 0x31,
    RecurrenceExact = 0x40,
    RecurrenceExceptions = 0x41,
}

/// Canonical descriptor assigned before candidate payload comparison.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct CandidateKey {
    pub family: CandidateFamily,
    /// Family-specific unsigned parameters in normative order.
    pub parameters: Vec<u64>,
}

impl CandidateKey {
    fn new(family: CandidateFamily, parameters: Vec<u64>) -> Self {
        Self { family, parameters }
    }
}

/// Search/pruning classification required by the MathSVG specification.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LedgerStatus {
    Tried,
    SafePrune,
    HeuristicSkip,
    BudgetStop,
}

impl LedgerStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Tried => "TRIED",
            Self::SafePrune => "SAFE_PRUNE",
            Self::HeuristicSkip => "HEURISTIC_SKIP",
            Self::BudgetStop => "BUDGET_STOP",
        }
    }
}

/// Machine-readable explanation separated from the safety classification.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LedgerReason {
    EvaluatedExact,
    ModelNotExact,
    ProvenSerializedLowerBound,
    ExceptionLimit,
    PeriodLimit,
    RecurrenceOrderLimit,
    EmptyCoefficientCatalogue,
    InsufficientInput,
    WorkBudget,
    LedgerBudget,
}

/// One retained audit event. IDs are assigned after canonical key sorting.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LedgerEntry {
    pub candidate_id: u64,
    pub key: CandidateKey,
    pub status: LedgerStatus,
    pub reason: LedgerReason,
    pub work_before: u64,
    pub work_charged: u64,
    pub actual_archive_bytes: Option<u64>,
    pub admissible_lower_bound_bytes: Option<u64>,
    pub upper_bound_at_decision: u64,
    pub became_best: bool,
    /// More than one descriptor may be summarized by a profile-limit row.
    pub represented_descriptors: u64,
}

/// The sole retained complete candidate payload.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Candidate {
    pub key: CandidateKey,
    pub program: Program,
    pub cost: CandidateCost,
}

/// Output of one finite, bounded block search.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SearchResult {
    pub winner: Candidate,
    pub literal_archive_bytes: u64,
    pub ledger: Vec<LedgerEntry>,
    pub work_used: u64,
    pub budget_exhausted: bool,
    /// False when a configured policy omitted descriptors or a budget stopped
    /// the search. This never means global optimality outside the catalogue.
    pub complete_within_declared_catalogue: bool,
}

/// Candidate classes returned by [`search_block_seeded`].
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum SearchMode {
    /// The exact winner may be the supplied literal/entropy incumbent or a
    /// newly fitted function.
    Full,
    /// Supplied literal/entropy candidates are upper bounds only. The result
    /// contains a winner only when a non-literal function beats them.
    NonLiteral,
}

/// Opaque, input-bound literal/entropy state reusable by function search.
///
/// Seed preparation is intentionally separate from search work accounting.
/// A caller that already paid for native entropy can therefore reuse the
/// exact incumbent without charging or executing `encode_best` again.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SearchSeed<'a> {
    input: &'a [u8],
    limits: Limits,
    literal: Candidate,
    entropy: Option<Candidate>,
}

impl<'a> SearchSeed<'a> {
    /// Prepare only the universal literal incumbent.
    pub fn literal_only(input: &'a [u8], limits: &Limits) -> Result<Self> {
        validate_search_input(input, limits)?;
        Ok(Self {
            input,
            limits: limits.clone(),
            literal: measure(
                CandidateKey::new(CandidateFamily::Literal, Vec::new()),
                Program::literal(input.to_vec()),
                limits,
            )?,
            entropy: None,
        })
    }

    /// Bind a previously computed exact native entropy winner to this input.
    ///
    /// The envelope is decoded once here so a fabricated or cross-input
    /// `BestEncoding` cannot alter pruning or winner selection. Search itself
    /// performs no entropy analysis or emission.
    pub fn with_precomputed_entropy(
        input: &'a [u8],
        encoding: BestEncoding,
        limits: &Limits,
    ) -> Result<Self> {
        let mut seed = Self::literal_only(input, limits)?;
        seed.entropy = Some(validated_entropy_candidate(input, encoding, limits)?);
        Ok(seed)
    }

    pub fn input(&self) -> &'a [u8] {
        self.input
    }

    pub fn literal(&self) -> &Candidate {
        &self.literal
    }

    pub fn entropy(&self) -> Option<&Candidate> {
        self.entropy.as_ref()
    }
}

/// Bounded result of a seeded function-only or full-catalogue search.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SeededSearchResult {
    pub winner: Option<Candidate>,
    pub literal_archive_bytes: u64,
    pub ledger: Vec<LedgerEntry>,
    pub work_used: u64,
    pub budget_exhausted: bool,
    pub complete_within_declared_catalogue: bool,
}

#[derive(Clone, Debug)]
enum Predictor {
    Const(u8),
    Linear { a: u8, b: u8 },
    Periodic { period: usize },
    Recurrence { coefficients: Vec<u8> },
}

impl Predictor {
    fn direct_key(&self) -> CandidateKey {
        match self {
            Self::Const(value) => {
                CandidateKey::new(CandidateFamily::ConstExact, vec![u64::from(*value)])
            }
            Self::Linear { a, b } => CandidateKey::new(
                CandidateFamily::LinearExact,
                vec![u64::from(*a), u64::from(*b)],
            ),
            Self::Periodic { period } => {
                CandidateKey::new(CandidateFamily::PeriodicExact, vec![*period as u64])
            }
            Self::Recurrence { coefficients } => {
                let mut parameters = Vec::with_capacity(coefficients.len() + 1);
                parameters.push(coefficients.len() as u64);
                parameters.extend(coefficients.iter().map(|value| u64::from(*value)));
                CandidateKey::new(CandidateFamily::RecurrenceExact, parameters)
            }
        }
    }

    fn exception_key(&self) -> CandidateKey {
        let direct = self.direct_key();
        let family = match direct.family {
            CandidateFamily::ConstExact => CandidateFamily::ConstExceptions,
            CandidateFamily::LinearExact => CandidateFamily::LinearExceptions,
            CandidateFamily::PeriodicExact => CandidateFamily::PeriodicExceptions,
            CandidateFamily::RecurrenceExact => CandidateFamily::RecurrenceExceptions,
            _ => direct.family,
        };
        CandidateKey::new(family, direct.parameters)
    }

    fn base_node(&self, input: &[u8]) -> Node {
        match self {
            Self::Const(value) => Node::Const {
                length: input.len() as u64,
                value: *value,
            },
            Self::Linear { a, b } => Node::Linear {
                count: input.len() as u64,
                width: 8,
                modulus: 256,
                a: u64::from(*a),
                b: u64::from(*b),
            },
            Self::Periodic { period } => {
                let repetitions = input.len() / *period;
                let repeated_bytes = repetitions * *period;
                Node::Periodic {
                    pattern: input[..*period].to_vec(),
                    repetitions: repetitions as u64,
                    suffix: input[repeated_bytes..].to_vec(),
                }
            }
            Self::Recurrence { coefficients } => {
                let order = coefficients.len();
                Node::Recurrence(Recurrence {
                    count: input.len() as u64,
                    width: 8,
                    modulus: 256,
                    coefficients: coefficients.iter().map(|value| u64::from(*value)).collect(),
                    initial_state: input[..order]
                        .iter()
                        .map(|value| u64::from(*value))
                        .collect(),
                })
            }
        }
    }
}

#[derive(Debug)]
struct SearchState {
    config: SearchConfig,
    limits: Limits,
    normalized_coefficients: Vec<u8>,
    best: Candidate,
    literal_archive_bytes: u64,
    ledger: Vec<LedgerEntry>,
    work_used: u64,
    exhausted: bool,
    unproven_omission: bool,
}

fn complete_archive_bytes(sections: &EncodedSections) -> Result<u64> {
    checked_u64_add(
        V1_SINGLE_BLOCK_OUTER_BYTES,
        sections.v1_block_payload_bytes()?,
        "complete one-block archive bytes",
    )
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

fn measure(key: CandidateKey, program: Program, limits: &Limits) -> Result<Candidate> {
    let report = program.validate(limits)?;
    let sections = program.encode_sections(limits)?;
    let archive_bytes = complete_archive_bytes(&sections)?;
    let decode_memory = checked_u64_add(
        report.original_bytes,
        report.temporary_bytes,
        "candidate decode memory",
    )?;
    let cost = program.candidate_cost(archive_bytes, decode_memory, limits)?;

    let mut canonical_payload = sections.definitions;
    canonical_payload.extend_from_slice(&sections.root);
    if cost.archive_bytes != archive_bytes || cost.canonical_payload != canonical_payload {
        return Err(Error::InvalidValue(
            "candidate cost disagrees with actual serialized sections",
        ));
    }
    Ok(Candidate { key, program, cost })
}

fn validate_search_input(input: &[u8], limits: &Limits) -> Result<()> {
    if input.is_empty() {
        return Err(Error::InvalidValue(
            "function search accepts non-empty v1 blocks only",
        ));
    }
    limits.check(
        "block output bytes",
        input.len() as u64,
        limits.max_block_output_bytes,
    )
}

fn validated_entropy_candidate(
    input: &[u8],
    encoding: BestEncoding,
    limits: &Limits,
) -> Result<Candidate> {
    let BestEncoding {
        codec,
        score,
        bytes,
    } = encoding;
    let entropy_limits = EntropyDecodeLimits {
        max_encoded_bytes: limits.max_archive_bytes.min(limits.max_node_payload_bytes),
        max_output_bytes: limits.max_block_output_bytes,
        max_work: limits.max_work,
    };
    let metadata = inspect_entropy(&bytes, entropy_limits).map_err(map_entropy_error)?;
    if metadata.codec != codec
        || metadata.decoded_bytes != input.len() as u64
        || metadata.encoded_bytes != score.encoded_bytes
        || metadata.decode_work != score.decode_work
        || score.encoded_bytes != bytes.len() as u64
        || score.decode_memory != input.len() as u64
        || score.node_count != 1
        || score.dependency_count != 0
        || score.opcode != codec.opcode()
    {
        return Err(Error::InvalidValue(
            "precomputed entropy score disagrees with its envelope",
        ));
    }
    let decoded =
        decode_exact(&bytes, input.len() as u64, entropy_limits).map_err(map_entropy_error)?;
    if decoded.codec != codec || decoded.bytes != input {
        return Err(Error::InvalidValue(
            "precomputed entropy envelope differs from seed input",
        ));
    }

    let mut parameters = vec![u64::from(codec.opcode())];
    parameters.extend(
        score
            .parameter_payload
            .as_bytes()
            .iter()
            .map(|byte| u64::from(*byte)),
    );
    measure(
        CandidateKey::new(CandidateFamily::EntropyLiteral, parameters),
        file_program(input.len(), Node::EntropyLiteral(bytes)),
        limits,
    )
}

fn validate_config(config: &SearchConfig, limits: &Limits) -> Result<Vec<u8>> {
    if config.max_recurrence_order > HARD_MAX_RECURRENCE_ORDER {
        return Err(Error::LimitExceeded {
            what: "function recurrence order",
            actual: u64::from(config.max_recurrence_order),
            limit: u64::from(HARD_MAX_RECURRENCE_ORDER),
        });
    }
    limits.check(
        "function recurrence order",
        u64::from(config.max_recurrence_order),
        limits.max_recurrence_order,
    )?;
    if config.recurrence_coefficients.len() > HARD_MAX_COEFFICIENT_VALUES {
        return Err(Error::LimitExceeded {
            what: "recurrence coefficient catalogue values",
            actual: config.recurrence_coefficients.len() as u64,
            limit: HARD_MAX_COEFFICIENT_VALUES as u64,
        });
    }
    limits.check(
        "function maximum period",
        u64::from(config.max_period),
        limits.max_pattern_bytes,
    )?;
    limits.check(
        "function maximum exceptions",
        u64::from(config.max_exceptions),
        limits.max_fan_out,
    )?;
    if config.max_ledger_entries < 2 {
        return Err(Error::InvalidValue(
            "function ledger budget must retain literal and stop rows",
        ));
    }
    limits.check(
        "function ledger rows",
        u64::from(config.max_ledger_entries),
        limits.max_nodes,
    )?;

    let mut coefficients = config.recurrence_coefficients.clone();
    coefficients.sort_unstable();
    coefficients.dedup();
    Ok(coefficients)
}

impl SearchState {
    fn upper_bound(&self) -> u64 {
        self.best.cost.archive_bytes
    }

    fn push(&mut self, mut entry: LedgerEntry) {
        entry.candidate_id = 0;
        self.ledger.push(entry);
    }

    fn stop(&mut self, key: CandidateKey, reason: LedgerReason) {
        if self.ledger.len() < self.config.max_ledger_entries as usize {
            self.push(LedgerEntry {
                candidate_id: 0,
                key,
                status: LedgerStatus::BudgetStop,
                reason,
                work_before: self.work_used,
                work_charged: 0,
                actual_archive_bytes: None,
                admissible_lower_bound_bytes: None,
                upper_bound_at_decision: self.upper_bound(),
                became_best: false,
                represented_descriptors: 1,
            });
        }
        self.exhausted = true;
        self.unproven_omission = true;
    }

    fn begin(&mut self, key: CandidateKey, work: u64, retained_rows: usize) -> Option<u64> {
        if self.exhausted {
            return None;
        }
        let maximum = self.config.max_ledger_entries as usize;
        let consumes_stop_row = match self.ledger.len().checked_add(retained_rows) {
            Some(needed) => needed >= maximum,
            None => true,
        };
        if consumes_stop_row {
            self.stop(key, LedgerReason::LedgerBudget);
            return None;
        }
        let next = match self.work_used.checked_add(work) {
            Some(next) => next,
            None => {
                self.stop(key, LedgerReason::WorkBudget);
                return None;
            }
        };
        if next > self.config.work_budget {
            self.stop(key, LedgerReason::WorkBudget);
            return None;
        }
        let before = self.work_used;
        self.work_used = next;
        Some(before)
    }

    fn record_tried(
        &mut self,
        key: CandidateKey,
        reason: LedgerReason,
        work_before: u64,
        work_charged: u64,
        candidate: Option<Candidate>,
    ) {
        let upper = self.upper_bound();
        let actual = candidate.as_ref().map(|value| value.cost.archive_bytes);
        let became_best = candidate
            .as_ref()
            .is_some_and(|value| value.cost < self.best.cost);
        if let Some(candidate) = candidate {
            if became_best {
                self.best = candidate;
            }
        }
        self.push(LedgerEntry {
            candidate_id: 0,
            key,
            status: LedgerStatus::Tried,
            reason,
            work_before,
            work_charged,
            actual_archive_bytes: actual,
            admissible_lower_bound_bytes: None,
            upper_bound_at_decision: upper,
            became_best,
            represented_descriptors: 1,
        });
    }

    fn record_skip(
        &mut self,
        key: CandidateKey,
        reason: LedgerReason,
        work_before: u64,
        represented_descriptors: u64,
    ) {
        self.unproven_omission = true;
        self.push(LedgerEntry {
            candidate_id: 0,
            key,
            status: LedgerStatus::HeuristicSkip,
            reason,
            work_before,
            work_charged: 0,
            actual_archive_bytes: None,
            admissible_lower_bound_bytes: None,
            upper_bound_at_decision: self.upper_bound(),
            became_best: false,
            represented_descriptors,
        });
    }

    fn process_predictor(
        &mut self,
        input: &[u8],
        predictor: Predictor,
        fitting_work: u64,
    ) -> Result<()> {
        let direct_key = predictor.direct_key();
        let Some(work_before) = self.begin(direct_key.clone(), fitting_work, 2) else {
            return Ok(());
        };
        let exception_key = predictor.exception_key();
        let maximum_exceptions = self.config.max_exceptions as usize;
        let exceptions = fit_exceptions(input, &predictor, maximum_exceptions)?;

        if exceptions.is_empty() {
            let direct = measure(
                direct_key.clone(),
                file_program(input.len(), predictor.base_node(input)),
                &self.limits,
            )?;
            self.record_tried(
                direct_key,
                LedgerReason::EvaluatedExact,
                work_before,
                fitting_work,
                Some(direct),
            );
        } else {
            self.record_tried(
                direct_key,
                LedgerReason::ModelNotExact,
                work_before,
                fitting_work,
                None,
            );
        }

        if exceptions.len() > maximum_exceptions {
            self.record_skip(
                exception_key,
                LedgerReason::ExceptionLimit,
                self.work_used,
                1,
            );
            return Ok(());
        }

        let optimistic_exceptions = (0..exceptions.len())
            .map(|position| Exception {
                position: position as u64,
                value: 0,
            })
            .collect();
        let optimistic = measure(
            exception_key.clone(),
            file_program(
                input.len(),
                Node::Exceptions {
                    base: Box::new(predictor.base_node(input)),
                    exceptions: optimistic_exceptions,
                },
            ),
            &self.limits,
        )?;
        let lower_bound = optimistic.cost.archive_bytes;
        let upper_bound = self.upper_bound();
        if lower_bound > upper_bound {
            self.push(LedgerEntry {
                candidate_id: 0,
                key: exception_key,
                status: LedgerStatus::SafePrune,
                reason: LedgerReason::ProvenSerializedLowerBound,
                work_before: self.work_used,
                work_charged: 0,
                actual_archive_bytes: None,
                admissible_lower_bound_bytes: Some(lower_bound),
                upper_bound_at_decision: upper_bound,
                became_best: false,
                represented_descriptors: 1,
            });
            return Ok(());
        }

        let candidate = measure(
            exception_key.clone(),
            file_program(
                input.len(),
                Node::Exceptions {
                    base: Box::new(predictor.base_node(input)),
                    exceptions,
                },
            ),
            &self.limits,
        )?;
        self.record_tried(
            exception_key,
            LedgerReason::EvaluatedExact,
            self.work_used,
            0,
            Some(candidate),
        );
        Ok(())
    }

    fn record_policy_range(
        &mut self,
        direct_family: CandidateFamily,
        exception_family: CandidateFamily,
        first: u64,
        last: u64,
        reason: LedgerReason,
        represented: u64,
    ) {
        if self.exhausted {
            return;
        }
        let key = CandidateKey::new(direct_family, vec![first, last]);
        if self.begin(key.clone(), 0, 2).is_none() {
            return;
        }
        self.record_skip(key, reason, self.work_used, represented);
        self.record_skip(
            CandidateKey::new(exception_family, vec![first, last]),
            reason,
            self.work_used,
            represented,
        );
    }

    fn finish(mut self) -> Result<SearchResult> {
        let sections = self.best.program.encode_sections(&self.limits)?;
        let actual = complete_archive_bytes(&sections)?;
        let report = self.best.program.validate(&self.limits)?;
        let decode_memory = checked_u64_add(
            report.original_bytes,
            report.temporary_bytes,
            "winner decode memory",
        )?;
        let repeated_cost =
            self.best
                .program
                .candidate_cost(actual, decode_memory, &self.limits)?;
        if repeated_cost != self.best.cost {
            return Err(Error::InvalidValue(
                "winner re-encoding changed canonical size or tie-break",
            ));
        }

        self.ledger.sort_by(|left, right| {
            left.key
                .cmp(&right.key)
                .then_with(|| status_order(left.status).cmp(&status_order(right.status)))
                .then_with(|| left.reason.cmp(&right.reason))
        });
        for (index, entry) in self.ledger.iter_mut().enumerate() {
            entry.candidate_id = index as u64;
        }
        let complete = !self.exhausted && !self.unproven_omission;
        Ok(SearchResult {
            winner: self.best,
            literal_archive_bytes: self.literal_archive_bytes,
            ledger: self.ledger,
            work_used: self.work_used,
            budget_exhausted: self.exhausted,
            complete_within_declared_catalogue: complete,
        })
    }
}

const fn status_order(status: LedgerStatus) -> u8 {
    match status {
        LedgerStatus::Tried => 0,
        LedgerStatus::SafePrune => 1,
        LedgerStatus::HeuristicSkip => 2,
        LedgerStatus::BudgetStop => 3,
    }
}

impl Ord for LedgerReason {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        reason_order(*self).cmp(&reason_order(*other))
    }
}

impl PartialOrd for LedgerReason {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

const fn reason_order(reason: LedgerReason) -> u8 {
    match reason {
        LedgerReason::EvaluatedExact => 0,
        LedgerReason::ModelNotExact => 1,
        LedgerReason::ProvenSerializedLowerBound => 2,
        LedgerReason::ExceptionLimit => 3,
        LedgerReason::PeriodLimit => 4,
        LedgerReason::RecurrenceOrderLimit => 5,
        LedgerReason::EmptyCoefficientCatalogue => 6,
        LedgerReason::InsufficientInput => 7,
        LedgerReason::WorkBudget => 8,
        LedgerReason::LedgerBudget => 9,
    }
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

fn push_exception(
    exceptions: &mut Vec<Exception>,
    maximum: usize,
    position: usize,
    value: u8,
) -> bool {
    exceptions.push(Exception {
        position: position as u64,
        value,
    });
    exceptions.len() > maximum
}

fn fit_exceptions(input: &[u8], predictor: &Predictor, maximum: usize) -> Result<Vec<Exception>> {
    let retained_capacity = input.len().min(maximum.saturating_add(1));
    let mut exceptions = Vec::with_capacity(retained_capacity);
    match predictor {
        Predictor::Const(value) => {
            for (position, &actual) in input.iter().enumerate() {
                if actual != *value && push_exception(&mut exceptions, maximum, position, actual) {
                    break;
                }
            }
        }
        Predictor::Linear { a, b } => {
            for (position, &actual) in input.iter().enumerate() {
                let index = (position & 0xff) as u8;
                let predicted = a.wrapping_mul(index).wrapping_add(*b);
                if actual != predicted && push_exception(&mut exceptions, maximum, position, actual)
                {
                    break;
                }
            }
        }
        Predictor::Periodic { period } => {
            let repeated_bytes = (input.len() / *period) * *period;
            for position in 0..repeated_bytes {
                let predicted = input[position % *period];
                let actual = input[position];
                if actual != predicted && push_exception(&mut exceptions, maximum, position, actual)
                {
                    break;
                }
            }
        }
        Predictor::Recurrence { coefficients } => {
            let order = coefficients.len();
            let mut history = VecDeque::with_capacity(order);
            history.extend(input[..order].iter().copied());
            for (position, &actual) in input.iter().enumerate().skip(order) {
                let mut sum = 0u64;
                for (lag, &coefficient) in coefficients.iter().enumerate() {
                    let prior = history[order - 1 - lag];
                    sum += u64::from(coefficient) * u64::from(prior);
                }
                let predicted = (sum & 0xff) as u8;
                if actual != predicted && push_exception(&mut exceptions, maximum, position, actual)
                {
                    break;
                }
                history.pop_front();
                history.push_back(predicted);
            }
        }
    }
    Ok(exceptions)
}

fn linear_work(length: u64) -> Result<u64> {
    checked_u64_mul(length, 2, "linear fitting work")
}

fn recurrence_work(length: u64, order: u64) -> Result<u64> {
    let generated = length.checked_sub(order).ok_or(Error::InvalidValue(
        "recurrence order exceeds input during fitting",
    ))?;
    checked_u64_add(
        length,
        checked_u64_mul(
            checked_u64_mul(generated, order, "recurrence fitting work")?,
            2,
            "recurrence fitting work",
        )?,
        "recurrence fitting work",
    )
}

fn entropy_work(length: u64) -> Result<u64> {
    checked_u64_mul(
        length,
        LeafCodec::ALL.len() as u64 + 1,
        "native entropy search work",
    )
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

fn catalogue_power(base: usize, exponent: usize) -> u64 {
    let mut result = 1u64;
    for _ in 0..exponent {
        result = result.saturating_mul(base as u64);
    }
    result
}

fn omitted_recurrence_descriptors(
    coefficient_count: usize,
    first_order: usize,
    last_order: usize,
) -> u64 {
    (first_order..=last_order).fold(0u64, |sum, order| {
        sum.saturating_add(catalogue_power(coefficient_count, order))
    })
}

/// Search one non-empty v1 block.
///
/// The literal is always measured first and remains the upper bound even when
/// `work_budget == 0`. The result is the smallest candidate in the declared
/// catalogue and consumed budget, never a claim of global optimality.
pub fn search_block(input: &[u8], config: &SearchConfig, limits: &Limits) -> Result<SearchResult> {
    validate_search_input(input, limits)?;
    let normalized_coefficients = validate_config(config, limits)?;

    let literal_key = CandidateKey::new(CandidateFamily::Literal, Vec::new());
    let literal = measure(
        literal_key.clone(),
        Program::literal(input.to_vec()),
        limits,
    )?;
    let literal_archive_bytes = literal.cost.archive_bytes;
    let literal_entry = LedgerEntry {
        candidate_id: 0,
        key: literal_key,
        status: LedgerStatus::Tried,
        reason: LedgerReason::EvaluatedExact,
        work_before: 0,
        work_charged: 0,
        actual_archive_bytes: Some(literal_archive_bytes),
        admissible_lower_bound_bytes: None,
        upper_bound_at_decision: literal_archive_bytes,
        became_best: true,
        represented_descriptors: 1,
    };
    let mut state = SearchState {
        config: config.clone(),
        limits: limits.clone(),
        normalized_coefficients,
        best: literal,
        literal_archive_bytes,
        ledger: vec![literal_entry],
        work_used: 0,
        exhausted: false,
        unproven_omission: false,
    };

    let input_length = input.len() as u64;
    let entropy_key = CandidateKey::new(CandidateFamily::EntropyLiteral, Vec::new());
    let entropy_work = entropy_work(input_length)?;
    if let Some(before) = state.begin(entropy_key, entropy_work, 1) {
        let encoding = encode_best(input).map_err(map_entropy_error)?;
        let mut parameters = vec![u64::from(encoding.codec.opcode())];
        parameters.extend(
            encoding
                .score
                .parameter_payload
                .as_bytes()
                .iter()
                .map(|byte| u64::from(*byte)),
        );
        let key = CandidateKey::new(CandidateFamily::EntropyLiteral, parameters);
        let candidate = measure(
            key.clone(),
            file_program(input.len(), Node::EntropyLiteral(encoding.bytes)),
            &state.limits,
        )?;
        state.record_tried(
            key,
            LedgerReason::EvaluatedExact,
            before,
            entropy_work,
            Some(candidate),
        );
    }

    search_predictor_catalogue(input, &mut state)?;
    state.finish()
}

/// Search the bounded function catalogue from caller-prepared exact
/// literal/entropy incumbents.
///
/// Seed preparation work is not charged again. All newly fitted predictors
/// retain the ordinary work and ledger bounds. In [`SearchMode::NonLiteral`],
/// a `None` winner means no fitted function beat the supplied incumbents under
/// the complete [`CandidateCost`] order.
pub fn search_block_seeded(
    seed: &SearchSeed<'_>,
    config: &SearchConfig,
    mode: SearchMode,
) -> Result<SeededSearchResult> {
    let input = seed.input;
    validate_search_input(input, &seed.limits)?;
    let normalized_coefficients = validate_config(config, &seed.limits)?;
    let literal = seed.literal.clone();
    let literal_archive_bytes = literal.cost.archive_bytes;
    let literal_key = literal.key.clone();
    let literal_entry = LedgerEntry {
        candidate_id: 0,
        key: literal_key,
        status: LedgerStatus::Tried,
        reason: LedgerReason::EvaluatedExact,
        work_before: 0,
        work_charged: 0,
        actual_archive_bytes: Some(literal_archive_bytes),
        admissible_lower_bound_bytes: None,
        upper_bound_at_decision: literal_archive_bytes,
        became_best: true,
        represented_descriptors: 1,
    };
    let mut state = SearchState {
        config: config.clone(),
        limits: seed.limits.clone(),
        normalized_coefficients,
        best: literal,
        literal_archive_bytes,
        ledger: vec![literal_entry],
        work_used: 0,
        exhausted: false,
        unproven_omission: false,
    };
    if let Some(entropy) = &seed.entropy {
        state.record_tried(
            entropy.key.clone(),
            LedgerReason::EvaluatedExact,
            0,
            0,
            Some(entropy.clone()),
        );
    }

    search_predictor_catalogue(input, &mut state)?;
    let result = state.finish()?;
    let winner = match mode {
        SearchMode::Full => Some(result.winner),
        SearchMode::NonLiteral
            if !matches!(
                result.winner.key.family,
                CandidateFamily::Literal | CandidateFamily::EntropyLiteral
            ) =>
        {
            Some(result.winner)
        }
        SearchMode::NonLiteral => None,
    };
    Ok(SeededSearchResult {
        winner,
        literal_archive_bytes: result.literal_archive_bytes,
        ledger: result.ledger,
        work_used: result.work_used,
        budget_exhausted: result.budget_exhausted,
        complete_within_declared_catalogue: result.complete_within_declared_catalogue,
    })
}

fn search_predictor_catalogue(input: &[u8], state: &mut SearchState) -> Result<()> {
    let input_length = input.len() as u64;
    let provisional_const = CandidateKey::new(CandidateFamily::ConstExact, Vec::new());
    let const_work = checked_u64_mul(input_length, 2, "constant fitting work")?;
    if let Some(before) = state.begin(provisional_const, const_work, 2) {
        let predictor = Predictor::Const(modal_byte(input));
        // One pass derives the modal byte and one bounded pass finds support.
        let fitting_work = const_work;
        let direct_key = predictor.direct_key();
        let exception_key = predictor.exception_key();
        let maximum = state.config.max_exceptions as usize;
        let exceptions = fit_exceptions(input, &predictor, maximum)?;
        if exceptions.is_empty() {
            let candidate = measure(
                direct_key.clone(),
                file_program(input.len(), predictor.base_node(input)),
                &state.limits,
            )?;
            state.record_tried(
                direct_key,
                LedgerReason::EvaluatedExact,
                before,
                fitting_work,
                Some(candidate),
            );
        } else {
            state.record_tried(
                direct_key,
                LedgerReason::ModelNotExact,
                before,
                fitting_work,
                None,
            );
        }
        if exceptions.len() > maximum {
            state.record_skip(
                exception_key,
                LedgerReason::ExceptionLimit,
                state.work_used,
                1,
            );
        } else {
            let optimistic_exceptions = (0..exceptions.len())
                .map(|position| Exception {
                    position: position as u64,
                    value: 0,
                })
                .collect();
            let optimistic = measure(
                exception_key.clone(),
                file_program(
                    input.len(),
                    Node::Exceptions {
                        base: Box::new(predictor.base_node(input)),
                        exceptions: optimistic_exceptions,
                    },
                ),
                &state.limits,
            )?;
            let lower = optimistic.cost.archive_bytes;
            let upper = state.upper_bound();
            if lower > upper {
                state.push(LedgerEntry {
                    candidate_id: 0,
                    key: exception_key,
                    status: LedgerStatus::SafePrune,
                    reason: LedgerReason::ProvenSerializedLowerBound,
                    work_before: state.work_used,
                    work_charged: 0,
                    actual_archive_bytes: None,
                    admissible_lower_bound_bytes: Some(lower),
                    upper_bound_at_decision: upper,
                    became_best: false,
                    represented_descriptors: 1,
                });
            } else {
                let candidate = measure(
                    exception_key.clone(),
                    file_program(
                        input.len(),
                        Node::Exceptions {
                            base: Box::new(predictor.base_node(input)),
                            exceptions,
                        },
                    ),
                    &state.limits,
                )?;
                state.record_tried(
                    exception_key,
                    LedgerReason::EvaluatedExact,
                    state.work_used,
                    0,
                    Some(candidate),
                );
            }
        }
    }

    if !state.exhausted {
        if input.len() >= 2 {
            let predictor = Predictor::Linear {
                a: input[1].wrapping_sub(input[0]),
                b: input[0],
            };
            state.process_predictor(input, predictor, linear_work(input_length)?)?;
        } else {
            state.record_policy_range(
                CandidateFamily::LinearExact,
                CandidateFamily::LinearExceptions,
                0,
                0,
                LedgerReason::InsufficientInput,
                1,
            );
        }
    }

    let meaningful_period = input.len() / 2;
    let tested_period = meaningful_period.min(state.config.max_period as usize);
    for period in 1..=tested_period {
        if state.exhausted {
            break;
        }
        state.process_predictor(input, Predictor::Periodic { period }, input_length)?;
    }
    if !state.exhausted && tested_period < meaningful_period {
        state.record_policy_range(
            CandidateFamily::PeriodicExact,
            CandidateFamily::PeriodicExceptions,
            (tested_period + 1) as u64,
            meaningful_period as u64,
            LedgerReason::PeriodLimit,
            (meaningful_period - tested_period) as u64,
        );
    }

    let meaningful_order = input
        .len()
        .saturating_sub(1)
        .min(usize::from(HARD_MAX_RECURRENCE_ORDER));
    let tested_order = meaningful_order.min(usize::from(state.config.max_recurrence_order));
    if !state.exhausted && tested_order > 0 && state.normalized_coefficients.is_empty() {
        state.record_policy_range(
            CandidateFamily::RecurrenceExact,
            CandidateFamily::RecurrenceExceptions,
            1,
            tested_order as u64,
            LedgerReason::EmptyCoefficientCatalogue,
            tested_order as u64,
        );
    } else {
        for order in 1..=tested_order {
            if state.exhausted {
                break;
            }
            let mut indices = vec![0usize; order];
            loop {
                let coefficients: Vec<u8> = indices
                    .iter()
                    .map(|index| state.normalized_coefficients[*index])
                    .collect();
                state.process_predictor(
                    input,
                    Predictor::Recurrence { coefficients },
                    recurrence_work(input_length, order as u64)?,
                )?;
                if state.exhausted {
                    break;
                }

                let mut position = order;
                while position > 0 {
                    position -= 1;
                    indices[position] += 1;
                    if indices[position] < state.normalized_coefficients.len() {
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
    if !state.exhausted
        && tested_order < meaningful_order
        && !state.normalized_coefficients.is_empty()
    {
        let first = tested_order + 1;
        let represented = omitted_recurrence_descriptors(
            state.normalized_coefficients.len(),
            first,
            meaningful_order,
        );
        state.record_policy_range(
            CandidateFamily::RecurrenceExact,
            CandidateFamily::RecurrenceExceptions,
            first as u64,
            meaningful_order as u64,
            LedgerReason::RecurrenceOrderLimit,
            represented,
        );
    }

    Ok(())
}
