//! Finite residual-sensitive equality enumeration for MathSVG microblocks.

#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};

use mathsvg_core::{CandidateCost, Error, Limits, Result, checked_u64_add};
use mathsvg_dsl::{CorrectionKind, Node, Program};
use mathsvg_evaluator::evaluate_node;
use mathsvg_functions::{CandidateFamily, SearchConfig, search_block};

const V1_SINGLE_BLOCK_OUTER_BYTES: u64 = 128 + 144 + 128;
const HARD_MAX_DEPTH: u8 = 3;
const HARD_MAX_STATES: u32 = 512;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SymbolicConfig {
    pub max_depth: u8,
    pub max_period: u16,
    pub max_states: u32,
    pub max_pairs: u64,
    pub work_budget: u64,
    pub max_ledger_entries: u32,
    pub residual_functions: SearchConfig,
}

impl Default for SymbolicConfig {
    fn default() -> Self {
        Self {
            max_depth: 2,
            max_period: 16,
            max_states: 128,
            max_pairs: 4096,
            work_budget: 1 << 26,
            max_ledger_entries: 8192,
            residual_functions: SearchConfig::default(),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ExpressionFamily {
    Const,
    Linear,
    Periodic,
    Add,
    Xor,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ExpressionKey {
    pub family: ExpressionFamily,
    pub depth: u8,
    pub parameters: Vec<u64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LedgerStatus {
    Tried,
    HeuristicSkip,
    BudgetStop,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LedgerReason {
    ExactExpression,
    ExactResidual,
    EquivalentSemantics,
    StateBudget,
    PairBudget,
    WorkBudget,
    LedgerBudget,
    ResidualSearchIncomplete,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SymbolicLedgerEntry {
    pub candidate_id: u64,
    pub key: ExpressionKey,
    pub status: LedgerStatus,
    pub reason: LedgerReason,
    pub residual_kind: Option<CorrectionKind>,
    pub actual_archive_bytes: Option<u64>,
    pub upper_bound_at_decision: u64,
    pub became_best: bool,
    pub work_before: u64,
    pub work_charged: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SymbolicCandidate {
    pub program: Program,
    pub cost: CandidateCost,
    pub expression_key: Option<ExpressionKey>,
    pub residual_kind: Option<CorrectionKind>,
    pub residual_function_family: Option<CandidateFamily>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SymbolicSearchResult {
    pub winner: SymbolicCandidate,
    pub literal_archive_bytes: u64,
    pub base_function_archive_bytes: u64,
    pub states_retained: u32,
    pub pairs_considered: u64,
    pub work_used: u64,
    pub budget_exhausted: bool,
    pub complete_within_declared_catalogue: bool,
    /// RSEE remains an oracle/ablation feature until the non-synthetic gate.
    pub emission_profile_gated: bool,
    pub ledger: Vec<SymbolicLedgerEntry>,
}

#[derive(Clone, Debug)]
struct Expression {
    key: ExpressionKey,
    node: Node,
    standalone_cost: CandidateCost,
}

#[derive(Debug)]
struct Engine<'a> {
    input: &'a [u8],
    config: &'a SymbolicConfig,
    limits: &'a Limits,
    best: SymbolicCandidate,
    literal_archive_bytes: u64,
    base_function_archive_bytes: u64,
    states: Vec<Expression>,
    semantic_index: BTreeMap<Vec<u8>, usize>,
    ledger: Vec<SymbolicLedgerEntry>,
    work_used: u64,
    pairs_considered: u64,
    budget_exhausted: bool,
    incomplete: bool,
}

/// Enumerate the frozen microblock catalogue and measure all retained
/// expression/residual candidates by complete canonical archive bytes.
pub fn search_symbolic_block(
    input: &[u8],
    config: &SymbolicConfig,
    limits: &Limits,
) -> Result<SymbolicSearchResult> {
    validate_config(input, config, limits)?;

    let literal = measure_program(Program::literal(input.to_vec()), None, None, None, limits)?;
    let literal_archive_bytes = literal.cost.archive_bytes;
    let mut base_config = config.residual_functions.clone();
    base_config.work_budget = base_config.work_budget.min(config.work_budget);
    let base = search_block(input, &base_config, limits)?;
    let base_function_archive_bytes = base.winner.cost.archive_bytes;
    let best = SymbolicCandidate {
        program: base.winner.program,
        cost: base.winner.cost,
        expression_key: None,
        residual_kind: None,
        residual_function_family: Some(base.winner.key.family),
    };
    let mut engine = Engine {
        input,
        config,
        limits,
        best,
        literal_archive_bytes,
        base_function_archive_bytes,
        states: Vec::new(),
        semantic_index: BTreeMap::new(),
        ledger: Vec::new(),
        work_used: base.work_used,
        pairs_considered: 0,
        budget_exhausted: base.budget_exhausted,
        incomplete: !base.complete_within_declared_catalogue,
    };
    if engine.work_used > config.work_budget {
        return Err(Error::LimitExceeded {
            what: "symbolic work",
            actual: engine.work_used,
            limit: config.work_budget,
        });
    }

    engine.enumerate_leaves()?;
    for depth in 2..=config.max_depth {
        if engine.budget_exhausted {
            break;
        }
        engine.enumerate_compositions(depth)?;
    }
    engine.finish()
}

fn validate_config(input: &[u8], config: &SymbolicConfig, limits: &Limits) -> Result<()> {
    if input.is_empty() {
        return Err(Error::InvalidValue(
            "symbolic search accepts non-empty v1 blocks only",
        ));
    }
    limits.check(
        "block output bytes",
        input.len() as u64,
        limits.max_block_output_bytes,
    )?;
    if config.max_depth == 0 || config.max_depth > HARD_MAX_DEPTH {
        return Err(Error::LimitExceeded {
            what: "symbolic depth",
            actual: u64::from(config.max_depth),
            limit: u64::from(HARD_MAX_DEPTH),
        });
    }
    if config.max_states == 0 || config.max_states > HARD_MAX_STATES {
        return Err(Error::LimitExceeded {
            what: "symbolic states",
            actual: u64::from(config.max_states),
            limit: u64::from(HARD_MAX_STATES),
        });
    }
    if config.max_period == 0 {
        return Err(Error::InvalidValue(
            "symbolic maximum period must be positive",
        ));
    }
    limits.check(
        "symbolic period",
        u64::from(config.max_period),
        limits.max_pattern_bytes,
    )?;
    if config.max_pairs == 0 || config.max_ledger_entries < 2 {
        return Err(Error::InvalidValue(
            "symbolic pair and ledger budgets must be positive",
        ));
    }
    Ok(())
}

impl Engine<'_> {
    fn enumerate_leaves(&mut self) -> Result<()> {
        let mut constants = BTreeSet::from([0u8, 1, u8::MAX, modal_byte(self.input)]);
        if let Some(&first) = self.input.first() {
            constants.insert(first);
        }
        for value in constants {
            let key = ExpressionKey {
                family: ExpressionFamily::Const,
                depth: 1,
                parameters: vec![u64::from(value)],
            };
            let node = Node::Const {
                length: self.input.len() as u64,
                value,
            };
            if !self.add_expression(key, node)? {
                return Ok(());
            }
        }

        if self.input.len() >= 2 {
            let b = self.input[0];
            let a = self.input[1].wrapping_sub(b);
            let key = ExpressionKey {
                family: ExpressionFamily::Linear,
                depth: 1,
                parameters: vec![u64::from(a), u64::from(b)],
            };
            let node = Node::Linear {
                count: self.input.len() as u64,
                width: 8,
                modulus: 256,
                a: u64::from(a),
                b: u64::from(b),
            };
            if !self.add_expression(key, node)? {
                return Ok(());
            }
        }

        let maximum = usize::from(self.config.max_period).min(self.input.len());
        for period in 1..=maximum {
            let repetitions = self.input.len() / period;
            if repetitions < 2 {
                continue;
            }
            let repeated = repetitions * period;
            let key = ExpressionKey {
                family: ExpressionFamily::Periodic,
                depth: 1,
                parameters: vec![period as u64],
            };
            let node = Node::Periodic {
                pattern: self.input[..period].to_vec(),
                repetitions: repetitions as u64,
                suffix: self.input[repeated..].to_vec(),
            };
            if !self.add_expression(key, node)? {
                return Ok(());
            }
        }
        Ok(())
    }

    fn enumerate_compositions(&mut self, depth: u8) -> Result<()> {
        let source_count = self.states.len();
        for left in 0..source_count {
            for right in left..source_count {
                if self.states[left]
                    .key
                    .depth
                    .max(self.states[right].key.depth)
                    + 1
                    != depth
                {
                    continue;
                }
                if self.pairs_considered >= self.config.max_pairs {
                    self.stop(self.states[left].key.clone(), LedgerReason::PairBudget);
                    return Ok(());
                }
                self.pairs_considered += 1;
                for (family, kind) in [
                    (ExpressionFamily::Add, CorrectionKind::Add),
                    (ExpressionFamily::Xor, CorrectionKind::Xor),
                ] {
                    let left_expression = self.states[left].clone();
                    let right_expression = self.states[right].clone();
                    let key = ExpressionKey {
                        family,
                        depth,
                        parameters: vec![left as u64, right as u64],
                    };
                    let node = Node::Correct {
                        kind,
                        prediction: Box::new(left_expression.node),
                        correction: Box::new(right_expression.node),
                    };
                    if !self.add_expression(key, node)? {
                        return Ok(());
                    }
                }
            }
        }
        Ok(())
    }

    fn add_expression(&mut self, key: ExpressionKey, node: Node) -> Result<bool> {
        if self.states.len() >= self.config.max_states as usize {
            self.stop(key, LedgerReason::StateBudget);
            return Ok(false);
        }
        let expression_work_before = self.work_used;
        if !self.charge(self.input.len() as u64, key.clone()) {
            return Ok(false);
        }
        let output = evaluate_node(&node, &[], self.limits)?;
        let standalone = measure_program(
            file_program(self.input.len(), node.clone()),
            Some(key.clone()),
            None,
            None,
            self.limits,
        )?;

        if let Some(&existing) = self.semantic_index.get(&output) {
            if standalone.cost >= self.states[existing].standalone_cost {
                self.incomplete = true;
                self.record(
                    key,
                    LedgerStatus::HeuristicSkip,
                    LedgerReason::EquivalentSemantics,
                    None,
                    Some(standalone.cost.archive_bytes),
                    false,
                    expression_work_before,
                    self.best.cost.archive_bytes,
                )?;
                return Ok(true);
            }

            self.consider_exact(&key, &standalone, &output, expression_work_before)?;
            self.consider_residual(&key, &node, &output, CorrectionKind::Add)?;
            self.consider_residual(&key, &node, &output, CorrectionKind::Xor)?;
            self.states[existing] = Expression {
                key,
                node,
                standalone_cost: standalone.cost,
            };
            return Ok(true);
        }

        self.consider_exact(&key, &standalone, &output, expression_work_before)?;
        self.consider_residual(&key, &node, &output, CorrectionKind::Add)?;
        self.consider_residual(&key, &node, &output, CorrectionKind::Xor)?;

        let index = self.states.len();
        self.semantic_index.insert(output.clone(), index);
        self.states.push(Expression {
            key,
            node,
            standalone_cost: standalone.cost,
        });
        Ok(true)
    }

    fn consider_exact(
        &mut self,
        key: &ExpressionKey,
        candidate: &SymbolicCandidate,
        output: &[u8],
        work_before: u64,
    ) -> Result<()> {
        if output != self.input {
            return Ok(());
        }
        let upper = self.best.cost.archive_bytes;
        let became_best = candidate.cost < self.best.cost;
        if became_best {
            self.best = candidate.clone();
        }
        self.record(
            key.clone(),
            LedgerStatus::Tried,
            LedgerReason::ExactExpression,
            None,
            Some(candidate.cost.archive_bytes),
            became_best,
            work_before,
            upper,
        )?;
        if upper < self.best.cost.archive_bytes {
            return Err(Error::InvalidValue("symbolic upper bound increased"));
        }
        Ok(())
    }

    fn consider_residual(
        &mut self,
        key: &ExpressionKey,
        expression: &Node,
        output: &[u8],
        kind: CorrectionKind,
    ) -> Result<()> {
        let work_before = self.work_used;
        let residual: Vec<u8> = match kind {
            CorrectionKind::Add => self
                .input
                .iter()
                .zip(output)
                .map(|(target, prediction)| target.wrapping_sub(*prediction))
                .collect(),
            CorrectionKind::Xor => self
                .input
                .iter()
                .zip(output)
                .map(|(target, prediction)| target ^ prediction)
                .collect(),
            CorrectionKind::Subtract => {
                return Err(Error::InvalidValue(
                    "SUB is not an RSEE residual reconstruction domain",
                ));
            }
        };
        let residual_work = residual.len() as u64;
        if !self.charge(residual_work, key.clone()) {
            return Ok(());
        }
        let remaining = self.config.work_budget.saturating_sub(self.work_used);
        let mut function_config = self.config.residual_functions.clone();
        function_config.work_budget = function_config.work_budget.min(remaining);
        let search = search_block(&residual, &function_config, self.limits)?;
        self.work_used = checked_u64_add(
            self.work_used,
            search.work_used,
            "symbolic residual search work",
        )?;
        if self.work_used > self.config.work_budget {
            return Err(Error::LimitExceeded {
                what: "symbolic work",
                actual: self.work_used,
                limit: self.config.work_budget,
            });
        }
        if !search.complete_within_declared_catalogue {
            self.incomplete = true;
        }
        if search.budget_exhausted {
            self.budget_exhausted = true;
            self.record(
                key.clone(),
                LedgerStatus::BudgetStop,
                LedgerReason::ResidualSearchIncomplete,
                Some(kind),
                None,
                false,
                work_before,
                self.best.cost.archive_bytes,
            )?;
            return Ok(());
        }
        let residual_family = search.winner.key.family;
        let residual_child = match search.winner.program.root {
            Node::File { child, .. } => *child,
            _ => return Err(Error::InvalidValue("residual function root")),
        };
        let program = file_program(
            self.input.len(),
            Node::Correct {
                kind,
                prediction: Box::new(expression.clone()),
                correction: Box::new(residual_child),
            },
        );
        let candidate = measure_program(
            program,
            Some(key.clone()),
            Some(kind),
            Some(residual_family),
            self.limits,
        )?;
        let upper = self.best.cost.archive_bytes;
        let became_best = candidate.cost < self.best.cost;
        if became_best {
            self.best = candidate.clone();
        }
        self.record(
            key.clone(),
            LedgerStatus::Tried,
            LedgerReason::ExactResidual,
            Some(kind),
            Some(candidate.cost.archive_bytes),
            became_best,
            work_before,
            upper,
        )?;
        if upper < self.best.cost.archive_bytes {
            return Err(Error::InvalidValue("symbolic upper bound increased"));
        }
        Ok(())
    }

    fn charge(&mut self, amount: u64, key: ExpressionKey) -> bool {
        match self.work_used.checked_add(amount) {
            Some(next) if next <= self.config.work_budget => {
                self.work_used = next;
                true
            }
            _ => {
                self.stop(key, LedgerReason::WorkBudget);
                false
            }
        }
    }

    fn stop(&mut self, key: ExpressionKey, reason: LedgerReason) {
        self.budget_exhausted = true;
        self.incomplete = true;
        if self.ledger.len() < self.config.max_ledger_entries as usize {
            let candidate_id = self.ledger.len() as u64;
            self.ledger.push(SymbolicLedgerEntry {
                candidate_id,
                key,
                status: LedgerStatus::BudgetStop,
                reason,
                residual_kind: None,
                actual_archive_bytes: None,
                upper_bound_at_decision: self.best.cost.archive_bytes,
                became_best: false,
                work_before: self.work_used,
                work_charged: 0,
            });
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn record(
        &mut self,
        key: ExpressionKey,
        status: LedgerStatus,
        reason: LedgerReason,
        residual_kind: Option<CorrectionKind>,
        actual_archive_bytes: Option<u64>,
        became_best: bool,
        work_before: u64,
        upper_bound_at_decision: u64,
    ) -> Result<()> {
        if self.ledger.len() >= self.config.max_ledger_entries as usize {
            self.stop(key, LedgerReason::LedgerBudget);
            return Ok(());
        }
        let candidate_id = self.ledger.len() as u64;
        self.ledger.push(SymbolicLedgerEntry {
            candidate_id,
            key,
            status,
            reason,
            residual_kind,
            actual_archive_bytes,
            upper_bound_at_decision,
            became_best,
            work_before,
            work_charged: self.work_used.saturating_sub(work_before),
        });
        Ok(())
    }

    fn finish(self) -> Result<SymbolicSearchResult> {
        Ok(SymbolicSearchResult {
            winner: self.best,
            literal_archive_bytes: self.literal_archive_bytes,
            base_function_archive_bytes: self.base_function_archive_bytes,
            states_retained: self.states.len() as u32,
            pairs_considered: self.pairs_considered,
            work_used: self.work_used,
            budget_exhausted: self.budget_exhausted,
            complete_within_declared_catalogue: !self.incomplete && !self.budget_exhausted,
            emission_profile_gated: true,
            ledger: self.ledger,
        })
    }
}

fn modal_byte(input: &[u8]) -> u8 {
    let mut counts = [0u64; 256];
    for value in input {
        counts[usize::from(*value)] += 1;
    }
    let mut best = 0u8;
    let mut best_count = 0u64;
    for value in 0u8..=u8::MAX {
        if counts[usize::from(value)] > best_count {
            best = value;
            best_count = counts[usize::from(value)];
        }
    }
    best
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

fn measure_program(
    program: Program,
    expression_key: Option<ExpressionKey>,
    residual_kind: Option<CorrectionKind>,
    residual_function_family: Option<CandidateFamily>,
    limits: &Limits,
) -> Result<SymbolicCandidate> {
    let report = program.validate(limits)?;
    let sections = program.encode_sections(limits)?;
    let archive_bytes = checked_u64_add(
        V1_SINGLE_BLOCK_OUTER_BYTES,
        sections.v1_block_payload_bytes()?,
        "symbolic complete archive bytes",
    )?;
    let decode_memory = checked_u64_add(
        report.original_bytes,
        report.temporary_bytes,
        "symbolic decode memory",
    )?;
    let cost = program.candidate_cost(archive_bytes, decode_memory, limits)?;
    Ok(SymbolicCandidate {
        program,
        cost,
        expression_key,
        residual_kind,
        residual_function_family,
    })
}
