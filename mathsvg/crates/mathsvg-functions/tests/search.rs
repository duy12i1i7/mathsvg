use mathsvg_core::{Error, Limits};
use mathsvg_dsl::Node;
use mathsvg_entropy::{encode_best, inspect, DecodeLimits, LeafCodec};
use mathsvg_functions::{
    search_block, search_block_seeded, CandidateFamily, LedgerReason, LedgerStatus, SearchConfig,
    SearchMode, SearchSeed, V1_SINGLE_BLOCK_OUTER_BYTES,
};

fn compact_config() -> SearchConfig {
    SearchConfig {
        max_period: 16,
        max_recurrence_order: 2,
        recurrence_coefficients: vec![0, 1, 2, 255],
        max_exceptions: 16,
        work_budget: 1 << 22,
        max_ledger_entries: 2048,
    }
}

fn winner_child(result: &mathsvg_functions::SearchResult) -> &Node {
    match &result.winner.program.root {
        Node::File { child, .. } => child,
        _ => panic!("winner root is not FILE"),
    }
}

#[test]
fn exact_synthetic_families_win_with_complete_dsl_nodes() {
    let limits = Limits::default();
    let config = compact_config();

    let constant = search_block(&vec![7; 512], &config, &limits).unwrap();
    assert_eq!(constant.winner.key.family, CandidateFamily::ConstExact);
    assert!(matches!(
        winner_child(&constant),
        Node::Const { value: 7, .. }
    ));

    let linear_bytes: Vec<u8> = (0..512)
        .map(|index| 3u8.wrapping_mul(index as u8).wrapping_add(11))
        .collect();
    let linear = search_block(&linear_bytes, &config, &limits).unwrap();
    assert_eq!(linear.winner.key.family, CandidateFamily::LinearExact);
    assert!(matches!(
        winner_child(&linear),
        Node::Linear { a: 3, b: 11, .. }
    ));

    let periodic_bytes = b"abcde".repeat(160);
    let periodic = search_block(&periodic_bytes, &config, &limits).unwrap();
    assert_eq!(periodic.winner.key.family, CandidateFamily::PeriodicExact);
    assert!(matches!(
        winner_child(&periodic),
        Node::Periodic { pattern, .. } if pattern == b"abcde"
    ));

    let mut recurrence_bytes = vec![3u8, 7];
    while recurrence_bytes.len() < 128 {
        let length = recurrence_bytes.len();
        recurrence_bytes
            .push(recurrence_bytes[length - 1].wrapping_add(recurrence_bytes[length - 2]));
    }
    let recurrence = search_block(&recurrence_bytes, &config, &limits).unwrap();
    assert_eq!(
        recurrence.winner.key.family,
        CandidateFamily::RecurrenceExact
    );
    assert!(matches!(
        winner_child(&recurrence),
        Node::Recurrence(value)
            if value.coefficients == vec![1, 1] && value.initial_state == vec![3, 7]
    ));
}

#[test]
fn sparse_exceptions_are_exact_and_can_win() {
    let limits = Limits::default();
    let config = compact_config();
    let mut bytes = vec![0u8; 2048];
    bytes[17] = 9;
    bytes[1000] = 4;
    bytes[2047] = 8;

    let result = search_block(&bytes, &config, &limits).unwrap();
    assert_eq!(result.winner.key.family, CandidateFamily::ConstExceptions);
    match winner_child(&result) {
        Node::Exceptions { base, exceptions } => {
            assert!(matches!(base.as_ref(), Node::Const { value: 0, .. }));
            assert_eq!(
                exceptions
                    .iter()
                    .map(|value| (value.position, value.value))
                    .collect::<Vec<_>>(),
                vec![(17, 9), (1000, 4), (2047, 8)]
            );
        }
        other => panic!("unexpected sparse winner: {other:?}"),
    }
}

fn next_random(state: &mut u64) -> u64 {
    *state = state
        .wrapping_mul(6_364_136_223_846_793_005)
        .wrapping_add(1_442_695_040_888_963_407);
    *state
}

#[test]
fn random_blocks_never_exceed_the_literal_archive() {
    let limits = Limits::default();
    let config = compact_config();
    let mut state = 0x8a5c_27d4_eb2f_165b;
    for length in 1..=128 {
        let mut bytes = Vec::with_capacity(length);
        for _ in 0..length {
            bytes.push(next_random(&mut state) as u8);
        }
        let result = search_block(&bytes, &config, &limits).unwrap();
        assert!(result.winner.cost.archive_bytes <= result.literal_archive_bytes);
        let literal = result
            .ledger
            .iter()
            .find(|entry| entry.key.family == CandidateFamily::Literal)
            .unwrap();
        assert_eq!(
            literal.actual_archive_bytes,
            Some(result.literal_archive_bytes)
        );
    }
}

#[test]
fn winner_size_identity_uses_actual_serialized_sections() {
    let bytes = b"xyzxyzxyzxyzxyzxyzxyzxyz".repeat(20);
    let result = search_block(&bytes, &compact_config(), &Limits::default()).unwrap();
    let sections = result
        .winner
        .program
        .encode_sections(&Limits::default())
        .unwrap();
    let expected = V1_SINGLE_BLOCK_OUTER_BYTES + sections.v1_block_payload_bytes().unwrap();
    assert_eq!(result.winner.cost.archive_bytes, expected);

    let mut canonical = sections.definitions;
    canonical.extend_from_slice(&sections.root);
    assert_eq!(result.winner.cost.canonical_payload, canonical);

    // Consume the normative aggregate report rather than assuming root-only
    // scratch. This remains correct when definition-cache accounting changes.
    let report = result.winner.program.validate(&Limits::default()).unwrap();
    assert_eq!(
        result.winner.cost.decode_memory,
        report.original_bytes + report.temporary_bytes
    );
}

#[test]
fn zero_work_budget_keeps_literal_and_is_deterministic() {
    let config = SearchConfig {
        work_budget: 0,
        ..compact_config()
    };
    let bytes = vec![4u8; 256];
    let first = search_block(&bytes, &config, &Limits::default()).unwrap();
    let second = search_block(&bytes, &config, &Limits::default()).unwrap();
    assert_eq!(first, second);
    assert_eq!(first.winner.key.family, CandidateFamily::Literal);
    assert!(first.budget_exhausted);
    assert_eq!(first.work_used, 0);
    assert!(first
        .ledger
        .iter()
        .any(|entry| entry.status == LedgerStatus::BudgetStop
            && entry.reason == LedgerReason::WorkBudget));
}

#[test]
fn every_safe_prune_carries_a_strict_proof() {
    let result = search_block(&vec![5u8; 1024], &compact_config(), &Limits::default()).unwrap();
    let safe: Vec<_> = result
        .ledger
        .iter()
        .filter(|entry| entry.status == LedgerStatus::SafePrune)
        .collect();
    assert!(!safe.is_empty());
    for entry in safe {
        assert!(
            entry.admissible_lower_bound_bytes.unwrap() > entry.upper_bound_at_decision,
            "{entry:?}"
        );
        assert_eq!(entry.reason, LedgerReason::ProvenSerializedLowerBound);
    }
}

#[test]
fn heuristic_and_ledger_budget_stops_are_explicit() {
    let mut bytes = vec![0u8; 128];
    bytes[7] = 1;
    bytes[33] = 2;
    let exception_limited = SearchConfig {
        max_exceptions: 1,
        ..compact_config()
    };
    let result = search_block(&bytes, &exception_limited, &Limits::default()).unwrap();
    assert!(result.ledger.iter().any(|entry| {
        entry.status == LedgerStatus::HeuristicSkip && entry.reason == LedgerReason::ExceptionLimit
    }));
    assert!(!result.complete_within_declared_catalogue);

    let ledger_limited = SearchConfig {
        max_ledger_entries: 3,
        ..compact_config()
    };
    let stopped = search_block(&bytes, &ledger_limited, &Limits::default()).unwrap();
    assert!(stopped.budget_exhausted);
    assert!(stopped.ledger.iter().any(|entry| {
        entry.status == LedgerStatus::BudgetStop && entry.reason == LedgerReason::LedgerBudget
    }));
}

#[test]
fn coefficient_input_order_does_not_change_archive_or_ledger() {
    let bytes: Vec<u8> = (0..256).map(|value| (value * 17) as u8).collect();
    let canonical = compact_config();
    let reordered = SearchConfig {
        recurrence_coefficients: vec![255, 2, 1, 0, 1],
        ..compact_config()
    };
    assert_eq!(
        search_block(&bytes, &canonical, &Limits::default()).unwrap(),
        search_block(&bytes, &reordered, &Limits::default()).unwrap()
    );
}

#[test]
fn invalid_scope_and_empty_blocks_are_rejected() {
    assert_eq!(
        search_block(&[], &compact_config(), &Limits::default()),
        Err(Error::InvalidValue(
            "function search accepts non-empty v1 blocks only"
        ))
    );

    let excessive = SearchConfig {
        max_recurrence_order: 9,
        ..compact_config()
    };
    assert!(matches!(
        search_block(b"x", &excessive, &Limits::default()),
        Err(Error::LimitExceeded {
            what: "function recurrence order",
            ..
        })
    ));
}

#[test]
fn native_entropy_leaf_can_win_and_has_a_complete_ledger_row() {
    let mut state = 0x243f_6a88_85a3_08d3u64;
    let mut bytes = Vec::with_capacity(4096);
    for _ in 0..4096 {
        bytes.push((next_random(&mut state) >> 61) as u8);
    }

    let result = search_block(&bytes, &compact_config(), &Limits::default()).unwrap();
    assert_eq!(result.winner.key.family, CandidateFamily::EntropyLiteral);
    let envelope = match winner_child(&result) {
        Node::EntropyLiteral(envelope) => envelope,
        other => panic!("unexpected entropy winner: {other:?}"),
    };
    let metadata = inspect(envelope, DecodeLimits::default()).unwrap();
    assert_eq!(metadata.codec, LeafCodec::BitPack);
    assert_eq!(metadata.decoded_bytes, bytes.len() as u64);

    let ledger = result
        .ledger
        .iter()
        .find(|entry| entry.key.family == CandidateFamily::EntropyLiteral)
        .unwrap();
    assert_eq!(ledger.status, LedgerStatus::Tried);
    assert_eq!(ledger.reason, LedgerReason::EvaluatedExact);
    assert_eq!(
        ledger.work_charged,
        bytes.len() as u64 * (LeafCodec::ALL.len() as u64 + 1)
    );
    assert_eq!(
        ledger.actual_archive_bytes,
        Some(result.winner.cost.archive_bytes)
    );
    assert!(ledger.became_best);
    assert!(result.winner.cost.archive_bytes < result.literal_archive_bytes);
}

#[test]
fn native_huffman_entropy_leaf_can_win_on_general_text() {
    let alice = include_bytes!("../../../../datasets/data/canterbury/alice29.txt");
    let bytes = &alice[..32 * 1024];
    let result = search_block(bytes, &compact_config(), &Limits::default()).unwrap();
    assert_eq!(result.winner.key.family, CandidateFamily::EntropyLiteral);
    let envelope = match winner_child(&result) {
        Node::EntropyLiteral(envelope) => envelope,
        other => panic!("unexpected text winner: {other:?}"),
    };
    let metadata = inspect(envelope, DecodeLimits::default()).unwrap();
    assert_eq!(metadata.codec, LeafCodec::CanonicalHuffman);
    assert!(result.winner.cost.archive_bytes < result.literal_archive_bytes);
}

#[test]
fn raw_dsl_literal_remains_the_universal_upper_bound() {
    let mut state = 0xd1b5_4a32_d192_ed03u64;
    let mut bytes = Vec::with_capacity(4096);
    for _ in 0..4096 {
        bytes.push((next_random(&mut state) >> 56) as u8);
    }
    assert_eq!(encode_best(&bytes).unwrap().codec, LeafCodec::Raw);

    let result = search_block(&bytes, &compact_config(), &Limits::default()).unwrap();
    assert_eq!(result.winner.key.family, CandidateFamily::Literal);
    assert_eq!(
        result.winner.cost.archive_bytes,
        result.literal_archive_bytes
    );
    let entropy = result
        .ledger
        .iter()
        .find(|entry| entry.key.family == CandidateFamily::EntropyLiteral)
        .unwrap();
    assert_eq!(entropy.status, LedgerStatus::Tried);
    assert!(entropy.actual_archive_bytes.unwrap() > result.literal_archive_bytes);
}

#[test]
fn seeded_full_search_preserves_the_existing_winner_bytes_and_tie_break() {
    let limits = Limits::default();
    let config = compact_config();
    let mut random_state = 0xd1b5_4a32_d192_ed03u64;
    let random: Vec<u8> = (0..4096)
        .map(|_| (next_random(&mut random_state) >> 56) as u8)
        .collect();
    let cases = [
        vec![7; 4096],
        (0..4096)
            .map(|index| 3u8.wrapping_mul(index as u8).wrapping_add(11))
            .collect(),
        b"seeded native function search ".repeat(160),
        random,
    ];

    for input in cases {
        let existing = search_block(&input, &config, &limits).unwrap();
        let seed =
            SearchSeed::with_precomputed_entropy(&input, encode_best(&input).unwrap(), &limits)
                .unwrap();
        let seeded = search_block_seeded(&seed, &config, SearchMode::Full).unwrap();
        assert_eq!(seeded.winner.as_ref(), Some(&existing.winner));
        assert_eq!(
            seeded.winner.as_ref().unwrap().cost.canonical_payload,
            existing.winner.cost.canonical_payload
        );
    }
}

#[test]
fn non_literal_mode_returns_only_a_function_that_beats_the_seed() {
    let limits = Limits::default();
    let config = compact_config();
    let input = vec![9u8; 4096];
    let seed = SearchSeed::with_precomputed_entropy(&input, encode_best(&input).unwrap(), &limits)
        .unwrap();
    let result = search_block_seeded(&seed, &config, SearchMode::NonLiteral).unwrap();
    let winner = result
        .winner
        .expect("the exact constant must beat literal and entropy incumbents");
    assert_eq!(winner.key.family, CandidateFamily::ConstExact);
    assert!(!matches!(
        winner.key.family,
        CandidateFamily::Literal | CandidateFamily::EntropyLiteral
    ));
    assert!(winner.cost < seed.literal().cost);
    assert_eq!(
        result
            .ledger
            .iter()
            .find(|entry| entry.key.family == CandidateFamily::EntropyLiteral)
            .unwrap()
            .work_charged,
        0
    );
}

#[test]
fn non_literal_mode_returns_none_instead_of_a_literal_or_entropy_incumbent() {
    let limits = Limits::default();
    let config = compact_config();
    let mut random_state = 0xa409_3822_299f_31d0u64;
    let input: Vec<u8> = (0..4096)
        .map(|_| (next_random(&mut random_state) >> 56) as u8)
        .collect();
    let seed = SearchSeed::with_precomputed_entropy(&input, encode_best(&input).unwrap(), &limits)
        .unwrap();
    let result = search_block_seeded(&seed, &config, SearchMode::NonLiteral).unwrap();
    assert!(result.winner.is_none());
    assert!(result.work_used <= config.work_budget);
    assert!(result.ledger.len() <= config.max_ledger_entries as usize);
}

#[test]
fn seeded_search_obeys_zero_work_and_ledger_bounds() {
    let limits = Limits::default();
    let input = vec![9u8; 4096];
    let seed = SearchSeed::with_precomputed_entropy(&input, encode_best(&input).unwrap(), &limits)
        .unwrap();
    let config = SearchConfig {
        work_budget: 0,
        ..compact_config()
    };
    let result = search_block_seeded(&seed, &config, SearchMode::NonLiteral).unwrap();
    assert!(result.winner.is_none());
    assert_eq!(result.work_used, 0);
    assert!(result.budget_exhausted);
    assert!(result.ledger.len() <= config.max_ledger_entries as usize);
    assert!(result
        .ledger
        .iter()
        .any(|entry| entry.status == LedgerStatus::BudgetStop
            && entry.reason == LedgerReason::WorkBudget));
}

#[test]
fn precomputed_entropy_seed_is_bound_to_the_exact_input() {
    let limits = Limits::default();
    let first = vec![3u8; 1024];
    let second = vec![4u8; 1024];
    let error =
        SearchSeed::with_precomputed_entropy(&second, encode_best(&first).unwrap(), &limits)
            .unwrap_err();
    assert_eq!(
        error,
        Error::InvalidValue("precomputed entropy envelope differs from seed input")
    );
}
