use mathsvg_core::{Error, Limits};
use mathsvg_dsl::Node;
use mathsvg_evaluator::evaluate_program;
use mathsvg_graph::{GraphConfig, LedgerReason, LedgerStatus, search_shared_block};

fn config() -> GraphConfig {
    GraphConfig {
        chunk_bytes: 32,
        min_occurrences: 3,
        max_definitions: 8,
        max_subsets: 1 << 8,
        work_budget: 1 << 22,
        max_ledger_entries: (1 << 8) + 2,
    }
}

#[test]
fn repeated_chunks_activate_a_real_definition_and_round_trip() {
    let mut chunk = Vec::new();
    for value in 0..32u8 {
        chunk.push(value.wrapping_mul(17).wrapping_add(3));
    }
    let input = chunk.repeat(24);
    let result = search_shared_block(&input, &config(), &Limits::default()).unwrap();

    assert!(result.winner.cost.archive_bytes < result.literal_archive_bytes);
    assert!(result.winner.active_definitions >= 1);
    assert_eq!(result.winner.referenced_source_bytes, input.len() as u64);
    assert!(!result.winner.program.definitions.is_empty());
    assert_eq!(
        evaluate_program(&result.winner.program, &Limits::default()).unwrap(),
        input
    );
}

#[test]
fn random_input_never_loses_to_the_literal_upper_bound() {
    let mut state = 0x51d7_34a9_8321_0bcdu64;
    for chunks in 1..20 {
        let mut input = Vec::with_capacity(chunks * 32);
        for _ in 0..chunks * 32 {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            input.push((state >> 23) as u8);
        }
        let result = search_shared_block(&input, &config(), &Limits::default()).unwrap();
        assert!(result.winner.cost.archive_bytes <= result.literal_archive_bytes);
        assert_eq!(
            evaluate_program(&result.winner.program, &Limits::default()).unwrap(),
            input
        );
    }
}

#[test]
fn every_retained_subset_is_measured_when_the_budget_completes() {
    let a = [1u8; 32];
    let b = [2u8; 32];
    let input = [a.repeat(4), b.repeat(4)].concat();
    let result = search_shared_block(&input, &config(), &Limits::default()).unwrap();
    assert_eq!(result.mined_definitions, 2);
    assert_eq!(result.evaluated_subsets, 4);
    assert!(result.complete_within_retained_catalogue);
    assert!(result.complete_within_declared_catalogue);
    assert!(!result.budget_exhausted);
    let masks: Vec<_> = result
        .ledger
        .iter()
        .filter(|row| row.status == LedgerStatus::Tried)
        .map(|row| row.activation_mask)
        .collect();
    assert_eq!(masks, vec![0, 1, 2, 3]);
}

#[test]
fn archive_and_ledger_are_deterministic() {
    let input = b"0123456789abcdef".repeat(24);
    let first = search_shared_block(&input, &config(), &Limits::default()).unwrap();
    let second = search_shared_block(&input, &config(), &Limits::default()).unwrap();
    assert_eq!(first, second);
}

#[test]
fn subset_work_and_catalogue_omissions_are_explicit() {
    let input = [
        [1u8; 32].repeat(3),
        [2u8; 32].repeat(3),
        [3u8; 32].repeat(3),
    ]
    .concat();
    let stopped = GraphConfig {
        max_subsets: 2,
        ..config()
    };
    let result = search_shared_block(&input, &stopped, &Limits::default()).unwrap();
    assert!(result.budget_exhausted);
    assert!(result.ledger.iter().any(|row| {
        row.status == LedgerStatus::BudgetStop && row.reason == LedgerReason::SubsetBudget
    }));

    let truncated = GraphConfig {
        max_definitions: 1,
        ..config()
    };
    let result = search_shared_block(&input, &truncated, &Limits::default()).unwrap();
    assert_eq!(result.omitted_definitions, 2);
    assert!(!result.complete_within_declared_catalogue);
    assert!(result.ledger.iter().any(|row| {
        row.status == LedgerStatus::HeuristicSkip && row.reason == LedgerReason::CatalogueLimit
    }));
}

#[test]
fn invalid_scope_and_limits_are_rejected() {
    assert_eq!(
        search_shared_block(&[], &config(), &Limits::default()),
        Err(Error::InvalidValue(
            "graph search accepts non-empty v1 blocks only"
        ))
    );
    let invalid = GraphConfig {
        chunk_bytes: 0,
        ..config()
    };
    assert!(search_shared_block(b"x", &invalid, &Limits::default()).is_err());

    let excessive = GraphConfig {
        max_definitions: 21,
        ..config()
    };
    assert!(matches!(
        search_shared_block(b"x", &excessive, &Limits::default()),
        Err(Error::LimitExceeded {
            what: "graph candidate definitions",
            ..
        })
    ));
}

#[test]
fn winner_uses_reference_nodes_when_sharing_wins() {
    let input = [9u8; 32].repeat(20);
    let result = search_shared_block(&input, &config(), &Limits::default()).unwrap();
    match &result.winner.program.root {
        Node::File { child, .. } => match child.as_ref() {
            Node::Concat(children) => {
                assert!(
                    children
                        .iter()
                        .all(|node| matches!(node, Node::Reference { .. }))
                );
            }
            Node::Reference { .. } => {}
            other => panic!("unexpected shared root {other:?}"),
        },
        other => panic!("unexpected root {other:?}"),
    }
}
