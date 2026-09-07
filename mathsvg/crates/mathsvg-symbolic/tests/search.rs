use mathsvg_core::{Error, Limits};
use mathsvg_evaluator::evaluate_program;
use mathsvg_functions::SearchConfig;
use mathsvg_symbolic::{LedgerReason, LedgerStatus, SymbolicConfig, search_symbolic_block};

fn config() -> SymbolicConfig {
    SymbolicConfig {
        max_depth: 2,
        max_period: 8,
        max_states: 96,
        max_pairs: 2048,
        work_budget: 1 << 25,
        max_ledger_entries: 4096,
        residual_functions: SearchConfig {
            max_period: 8,
            max_recurrence_order: 2,
            recurrence_coefficients: vec![0, 1, 2, 255],
            max_exceptions: 32,
            work_budget: 1 << 22,
            max_ledger_entries: 1024,
        },
    }
}

#[test]
fn residual_sensitive_search_round_trips_and_never_loses_base() {
    let pattern = [0u8, 0, 5, 5];
    let input: Vec<_> = (0..2048)
        .map(|index| {
            (3u8)
                .wrapping_mul(index as u8)
                .wrapping_add(7)
                .wrapping_add(pattern[index % pattern.len()])
        })
        .collect();
    let result = search_symbolic_block(&input, &config(), &Limits::default()).unwrap();
    assert!(result.winner.cost.archive_bytes < result.base_function_archive_bytes);
    assert!(result.winner.cost.archive_bytes <= result.literal_archive_bytes);
    assert_eq!(
        evaluate_program(&result.winner.program, &Limits::default()).unwrap(),
        input
    );
    assert!(
        result
            .ledger
            .iter()
            .any(|row| row.reason == LedgerReason::ExactResidual)
    );
    assert!(result.emission_profile_gated);
}

#[test]
fn deterministic_random_blocks_keep_the_native_upper_bound() {
    let mut state = 0x6a09_e667_f3bc_c909u64;
    for length in [8usize, 31, 64, 127] {
        let mut input = Vec::with_capacity(length);
        for _ in 0..length {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            input.push((state >> 19) as u8);
        }
        let first = search_symbolic_block(&input, &config(), &Limits::default()).unwrap();
        let second = search_symbolic_block(&input, &config(), &Limits::default()).unwrap();
        assert_eq!(first, second);
        assert!(first.winner.cost.archive_bytes <= first.literal_archive_bytes);
        assert_eq!(
            evaluate_program(&first.winner.program, &Limits::default()).unwrap(),
            input
        );
    }
}

#[test]
fn budgets_stop_explicitly_without_claiming_completeness() {
    let tiny = SymbolicConfig {
        work_budget: 1,
        ..config()
    };
    let result = search_symbolic_block(&[3; 128], &tiny, &Limits::default()).unwrap();
    assert!(result.budget_exhausted);
    assert!(!result.complete_within_declared_catalogue);
    assert!(
        result
            .ledger
            .iter()
            .any(|row| row.status == LedgerStatus::BudgetStop)
    );
}

#[test]
fn invalid_scope_is_rejected() {
    assert_eq!(
        search_symbolic_block(&[], &config(), &Limits::default()),
        Err(Error::InvalidValue(
            "symbolic search accepts non-empty v1 blocks only"
        ))
    );
    let excessive = SymbolicConfig {
        max_depth: 4,
        ..config()
    };
    assert!(matches!(
        search_symbolic_block(b"x", &excessive, &Limits::default()),
        Err(Error::LimitExceeded {
            what: "symbolic depth",
            ..
        })
    ));
}
