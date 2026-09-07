use mathsvg_container::decode_archive;
use mathsvg_core::{Limits, Result};
use mathsvg_dsl::{CorrectionKind, Node, Program};
use mathsvg_evaluator::evaluate_program;
use mathsvg_residual::{
    search_residual, ArplGate, LedgerReason, LedgerStatus, ProjectionFamily, ResidualConfig,
    ResidualDomain,
};

fn finite_config(depth: u8, domain: ResidualDomain) -> ResidualConfig {
    let mut config = ResidualConfig {
        gate: ArplGate::Experimental,
        max_depth: depth,
        domains: vec![domain],
        max_projection_period: 8,
        max_recurrence_order: 0,
        recurrence_coefficients: Vec::new(),
        max_projection_descriptors: 16,
        max_states: 128,
        max_state_bytes: 8 * 1024 * 1024,
        work_budget: 1 << 28,
        max_ledger_entries: 4096,
        minimum_gain_bytes: 1,
        ..ResidualConfig::default()
    };
    config.function_search.max_period = 8;
    config.function_search.max_recurrence_order = 0;
    config.function_search.recurrence_coefficients.clear();
    config.function_search.max_exceptions = 0;
    config.function_search.work_budget = 1 << 26;
    config.function_search.max_ledger_entries = 512;
    config
}

fn additive_fixture(length: usize) -> Vec<u8> {
    let periodic = [5u8, 5, 9, 1];
    (0..length)
        .map(|index| {
            (index as u8)
                .wrapping_mul(3)
                .wrapping_add(17)
                .wrapping_add(periodic[index % periodic.len()])
        })
        .collect()
}

fn xor_fixture(length: usize) -> Vec<u8> {
    let periodic = [0u8, 0, 0xf0, 0x0f];
    (0..length)
        .map(|index| {
            (index as u8).wrapping_mul(5).wrapping_add(29) ^ periodic[index % periodic.len()]
        })
        .collect()
}

fn assert_no_unproven_safe_prune(result: &mathsvg_residual::ResidualResult) {
    for entry in result
        .ledger
        .iter()
        .filter(|entry| entry.status == LedgerStatus::SafePrune)
    {
        let nested = entry
            .nested_function_entry
            .as_ref()
            .expect("SAFE_PRUNE must retain its nested function proof");
        assert_eq!(nested.status, mathsvg_functions::LedgerStatus::SafePrune);
        assert_eq!(
            nested.reason,
            mathsvg_functions::LedgerReason::ProvenSerializedLowerBound
        );
        assert!(nested
            .admissible_lower_bound_bytes
            .is_some_and(|lower| lower >= nested.upper_bound_at_decision));
    }
    assert_eq!(
        result
            .ledger
            .iter()
            .map(|entry| entry.work_charged)
            .sum::<u64>(),
        result.work_used,
        "every charged search unit must reconcile through the retained ledger"
    );
}

fn assert_archive_round_trip(input: &[u8], archive: &[u8], limits: &Limits) {
    let decoded = decode_archive(archive, limits).expect("winner archive must decode");
    let mut restored = Vec::new();
    decoded
        .verify_restored_with(
            |program| evaluate_program(program, limits),
            |_, block| {
                restored.extend_from_slice(block);
                Ok(())
            },
        )
        .expect("container hashes and evaluated bytes must verify");
    assert_eq!(restored, input);
}

#[test]
fn evaluator_correction_domains_are_bit_exact() {
    let limits = Limits::default();
    let add = Program {
        definitions: Vec::new(),
        root: Node::File {
            original_length: 4,
            child: Box::new(Node::Correct {
                kind: CorrectionKind::Add,
                prediction: Box::new(Node::Literal(vec![255, 250, 1, 128])),
                correction: Box::new(Node::Literal(vec![1, 10, 255, 128])),
            }),
        },
    };
    assert_eq!(evaluate_program(&add, &limits).unwrap(), [0, 4, 0, 0]);

    let xor = Program {
        definitions: Vec::new(),
        root: Node::File {
            original_length: 4,
            child: Box::new(Node::Correct {
                kind: CorrectionKind::Xor,
                prediction: Box::new(Node::Literal(vec![0xff, 0xaa, 0x0f, 0x80])),
                correction: Box::new(Node::Literal(vec![0x0f, 0x55, 0xf0, 0x80])),
            }),
        },
    };
    assert_eq!(
        evaluate_program(&xor, &limits).unwrap(),
        [0xf0, 0xff, 0xff, 0]
    );
}

#[test]
fn conservative_default_is_oracle_gated() {
    let input = additive_fixture(512);
    let result = search_residual(&input, &ResidualConfig::default(), &Limits::default()).unwrap();

    assert_eq!(result.winner.residual_depth, 0);
    assert!(result.winner.cost.archive_bytes <= result.literal_archive_bytes);
    assert!(result.ledger.iter().any(|entry| {
        entry.status == LedgerStatus::ProfileDisabled
            && entry.reason == LedgerReason::OracleNoHeadroom
    }));
    assert_no_unproven_safe_prune(&result);
}

#[test]
fn additive_two_component_fixture_has_a_depth_one_win() {
    let input = additive_fixture(4096);
    let limits = Limits::default();
    let result = search_residual(
        &input,
        &finite_config(1, ResidualDomain::AddMod256),
        &limits,
    )
    .unwrap();

    assert_eq!(result.winner.residual_depth, 1);
    assert!(result.winner.cost.archive_bytes < result.literal_archive_bytes);
    assert!(matches!(
        &result.winner.program.root,
        Node::File { child, .. }
            if matches!(
                child.as_ref(),
                Node::Correct {
                    kind: CorrectionKind::Add,
                    ..
                }
            )
    ));
    assert_eq!(
        evaluate_program(&result.winner.program, &limits).unwrap(),
        input
    );
    assert_archive_round_trip(&input, &result.winner.archive, &limits);
    assert_no_unproven_safe_prune(&result);
}

#[test]
fn xor_two_component_fixture_has_a_depth_one_win() {
    let input = xor_fixture(4096);
    let limits = Limits::default();
    let result = search_residual(&input, &finite_config(1, ResidualDomain::Xor), &limits).unwrap();

    assert_eq!(result.winner.residual_depth, 1);
    assert!(result.winner.cost.archive_bytes < result.literal_archive_bytes);
    assert!(matches!(
        &result.winner.program.root,
        Node::File { child, .. }
            if matches!(
                child.as_ref(),
                Node::Correct {
                    kind: CorrectionKind::Xor,
                    ..
                }
            )
    ));
    assert_eq!(
        evaluate_program(&result.winner.program, &limits).unwrap(),
        input
    );
    assert_archive_round_trip(&input, &result.winner.archive, &limits);
    assert_no_unproven_safe_prune(&result);
}

#[test]
fn depth_zero_one_two_ablation_is_non_increasing() {
    let input = additive_fixture(4096);
    let limits = Limits::default();
    let depth_zero = search_residual(
        &input,
        &finite_config(0, ResidualDomain::AddMod256),
        &limits,
    )
    .unwrap();
    let depth_one = search_residual(
        &input,
        &finite_config(1, ResidualDomain::AddMod256),
        &limits,
    )
    .unwrap();
    let depth_two = search_residual(
        &input,
        &finite_config(2, ResidualDomain::AddMod256),
        &limits,
    )
    .unwrap();

    assert!(depth_one.winner.cost <= depth_zero.winner.cost);
    assert!(depth_two.winner.cost <= depth_one.winner.cost);
    assert!(
        depth_one.winner.cost.archive_bytes < depth_zero.winner.cost.archive_bytes,
        "the synthetic two-component input must exercise the residual layer"
    );
}

#[test]
fn deterministic_random_blocks_never_lose_to_literal() {
    let limits = Limits::default();
    for seed in [1u64, 0x1234_5678_9abc_def0, u64::MAX] {
        let mut state = seed;
        let input: Vec<u8> = (0..257)
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                state as u8
            })
            .collect();
        let mut config = finite_config(2, ResidualDomain::AddMod256);
        config.max_projection_period = 4;
        config.max_projection_descriptors = 8;
        config.max_states = 32;

        let first = search_residual(&input, &config, &limits).unwrap();
        let second = search_residual(&input, &config, &limits).unwrap();
        assert_eq!(first, second);
        assert!(first.winner.cost.archive_bytes <= first.literal_archive_bytes);
        assert_eq!(
            evaluate_program(&first.winner.program, &limits).unwrap(),
            input
        );
        assert_no_unproven_safe_prune(&first);
    }
}

#[test]
fn work_and_state_budgets_stop_deterministically() {
    let input = additive_fixture(128);
    let limits = Limits::default();

    let mut work_limited = finite_config(2, ResidualDomain::AddMod256);
    work_limited.work_budget = 0;
    let first = search_residual(&input, &work_limited, &limits).unwrap();
    let second = search_residual(&input, &work_limited, &limits).unwrap();
    assert_eq!(first, second);
    assert!(first.budget_exhausted);
    assert!(first.ledger.iter().any(|entry| {
        entry.status == LedgerStatus::BudgetStop && entry.reason == LedgerReason::WorkBudget
    }));

    let mut state_limited = finite_config(2, ResidualDomain::AddMod256);
    state_limited.max_states = 1;
    let result = search_residual(&input, &state_limited, &limits).unwrap();
    assert!(result.budget_exhausted);
    assert!(result.ledger.iter().any(|entry| {
        entry.status == LedgerStatus::BudgetStop && entry.reason == LedgerReason::StateBudget
    }));
    assert_no_unproven_safe_prune(&result);
}

#[test]
fn ledger_and_live_state_byte_budgets_are_hard() {
    let input = additive_fixture(128);
    let limits = Limits::default();

    let mut ledger_limited = finite_config(2, ResidualDomain::AddMod256);
    ledger_limited.max_ledger_entries = 4;
    let result = search_residual(&input, &ledger_limited, &limits).unwrap();
    assert_eq!(result.ledger.len(), 4);
    assert!(result.budget_exhausted);
    assert_eq!(
        result.ledger.last().map(|entry| entry.reason),
        Some(LedgerReason::LedgerBudget)
    );

    let mut bytes_limited = finite_config(2, ResidualDomain::AddMod256);
    bytes_limited.max_state_bytes = input.len() as u64;
    let result = search_residual(&input, &bytes_limited, &limits).unwrap();
    assert!(result.budget_exhausted);
    assert!(result.ledger.iter().any(|entry| {
        entry.status == LedgerStatus::BudgetStop && entry.reason == LedgerReason::StateBytesBudget
    }));
    assert_eq!(result.state_bytes_peak, input.len() as u64);
    assert_no_unproven_safe_prune(&result);
}

#[test]
fn disabled_profile_still_enforces_the_root_state_byte_budget() {
    let input = additive_fixture(64);
    let config = ResidualConfig {
        max_state_bytes: (input.len() - 1) as u64,
        ..ResidualConfig::default()
    };
    let result = search_residual(&input, &config, &Limits::default()).unwrap();

    assert!(result.budget_exhausted);
    assert!(result.ledger.iter().any(|entry| {
        entry.status == LedgerStatus::BudgetStop && entry.reason == LedgerReason::StateBytesBudget
    }));
    assert_no_unproven_safe_prune(&result);
}

#[test]
fn infeasible_residual_candidate_cannot_defeat_literal_fallback() {
    let input = additive_fixture(128);
    let config = finite_config(1, ResidualDomain::AddMod256);
    let limits = Limits {
        max_graph_depth: 2,
        ..Limits::default()
    };
    let result = search_residual(&input, &config, &limits).unwrap();

    assert_eq!(result.winner.residual_depth, 0);
    assert!(result.winner.cost.archive_bytes <= result.literal_archive_bytes);
    assert!(result.budget_exhausted);
    assert!(result.ledger.iter().any(|entry| {
        entry.status == LedgerStatus::BudgetStop
            && entry.reason == LedgerReason::CandidateDecoderLimit
    }));
    assert_eq!(
        evaluate_program(&result.winner.program, &limits).unwrap(),
        input
    );
    assert_no_unproven_safe_prune(&result);
}

#[test]
fn repeated_state_detection_uses_the_active_state_bytes() -> Result<()> {
    let input = vec![0; 64];
    let mut config = finite_config(2, ResidualDomain::AddMod256);
    config.max_projection_period = 2;
    config.max_projection_descriptors = 4;
    let result = search_residual(&input, &config, &Limits::default())?;

    assert_eq!(result.states_visited, 1);
    assert!(result.ledger.iter().any(|entry| {
        entry.status == LedgerStatus::RepeatedStateStop
            && entry.reason == LedgerReason::RepeatedState
    }));
    assert_no_unproven_safe_prune(&result);
    Ok(())
}

#[test]
fn recurrence_projection_family_is_evaluated_exactly() {
    let input = additive_fixture(128);
    let limits = Limits::default();
    let mut config = finite_config(1, ResidualDomain::AddMod256);
    config.max_recurrence_order = 1;
    config.recurrence_coefficients = vec![1];
    let result = search_residual(&input, &config, &limits).unwrap();

    assert!(result.ledger.iter().any(|entry| {
        entry.reason == LedgerReason::ProjectionExpanded
            && entry
                .path
                .last()
                .is_some_and(|layer| layer.projection.family == ProjectionFamily::Recurrence)
    }));
    assert_eq!(
        evaluate_program(&result.winner.program, &limits).unwrap(),
        input
    );
    assert_no_unproven_safe_prune(&result);
}
