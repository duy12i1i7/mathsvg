use mathsvg_container::{encode_archive, encode_literal_archive, ArchiveBlock};
use mathsvg_coordinates::{
    inverse as coordinate_inverse, CoordinateDescriptor as NativeCoordinateDescriptor,
    CoordinateTransform,
};
use mathsvg_core::{checked_u64_add, CandidateCost, Error, Limits, Result};
use mathsvg_dsl::{CoordinateDescriptor, Node, Program};
use mathsvg_entropy::{count_lz_huffman_policy, encode_best, LeafCodec, LzParserPolicy};
use mathsvg_evaluator::evaluate_program;
use mathsvg_graph::GraphConfig;
use mathsvg_optimizer::{
    canonical_microblock_boundaries, optimize, optimize_portfolio, optimize_with_all_providers,
    optimize_with_providers, ArchiveSelection, CandidateProvider, CoordinateBasisProvider,
    CoordinateProvider, CoverageClass, EntropyProvider, LedgerEvent, LedgerReason,
    OptimizationResult, OptimizerConfig, PortfolioAlgorithm, PortfolioConfig, PortfolioProfile,
    ProvidedCandidate, ProvidedProgramCandidate, ProviderCandidateKey, ProviderContext,
    ProviderSearch, WholeBlockCandidateProvider, WholeBlockProviderSearch,
};
use mathsvg_residual::{ArplGate, ResidualDomain};
use sha2::{Digest, Sha256};

fn test_config(block_bytes: u32, microblock_bytes: u32) -> OptimizerConfig {
    OptimizerConfig {
        block_bytes,
        microblock_bytes,
        max_segments_per_block: 8,
        max_states: 50_000,
        max_candidates: 5_000,
        work_budget: 10_000_000,
        max_ledger_entries: 10_000,
        functions: mathsvg_functions::SearchConfig {
            max_period: 8,
            max_recurrence_order: 0,
            recurrence_coefficients: Vec::new(),
            max_exceptions: 0,
            work_budget: 1_000_000,
            max_ledger_entries: 256,
        },
    }
}

fn assert_verified(result: &OptimizationResult, input: &[u8]) {
    assert_eq!(result.verified.original_size, input.len() as u64);
    let expected: [u8; 32] = Sha256::digest(input).into();
    assert_eq!(result.verified.original_sha256, expected);
    assert_eq!(result.archive.len() as u64, result.cost.archive_bytes);
    assert!(result.archive.len() as u64 <= result.literal_archive_bytes);
}

#[derive(Default)]
struct ExactConstProvider;

impl CandidateProvider for ExactConstProvider {
    fn provider_id(&self) -> &'static str {
        "test-exact-const"
    }

    fn search(
        &mut self,
        interval: &[u8],
        _context: ProviderContext,
        _limits: &Limits,
    ) -> Result<ProviderSearch> {
        let candidates = interval
            .first()
            .copied()
            .filter(|value| interval.iter().all(|actual| actual == value))
            .map(|value| ProvidedCandidate {
                key: ProviderCandidateKey {
                    family: 1,
                    parameters: vec![u64::from(value)],
                    canonical_payload: Vec::new(),
                },
                class: CoverageClass::Function,
                node: Node::Const {
                    length: interval.len() as u64,
                    value,
                },
            })
            .into_iter()
            .collect();
        Ok(ProviderSearch::complete(candidates, 0))
    }
}

#[test]
fn empty_input_is_the_unique_verified_literal_archive() {
    let result = optimize(&[], &OptimizerConfig::default(), &Limits::default()).unwrap();
    assert_eq!(result.selection, ArchiveSelection::LiteralFallback);
    assert_eq!(result.archive.len(), 256);
    assert!(result.programs.is_empty());
    assert_eq!(result.coverage.original_bytes, 0);
    assert_verified(&result, &[]);
}

#[test]
fn built_in_functions_win_on_piecewise_linear_data() {
    let mut input: Vec<u8> = (0..64)
        .map(|index| 3u8.wrapping_mul(index).wrapping_add(1))
        .collect();
    input.extend((0..64).map(|index| 5u8.wrapping_mul(index).wrapping_add(7)));
    let result = optimize(&input, &test_config(128, 64), &Limits::default()).unwrap();

    assert_eq!(result.selection, ArchiveSelection::Procedural);
    assert!(result.archive.len() < result.literal_archive_bytes as usize);
    assert_eq!(result.programs.len(), 1);
    assert!(matches!(
        &result.programs[0].root,
        Node::File { child, .. } if matches!(child.as_ref(), Node::Split { boundaries, children }
            if boundaries == &[64] && children.len() == 2)
    ));
    assert_eq!(result.coverage.function_segments, 2);
    assert_eq!(result.coverage.function_source_bytes, input.len() as u64);
    assert!(result
        .ledger
        .iter()
        .any(|entry| entry.selected && entry.descriptor.provider == "mathsvg-functions"));
    assert_verified(&result, &input);
}

#[test]
fn native_entropy_savings_are_reported_as_literal_not_function_coverage() {
    let mut state = 0x243f_6a88_85a3_08d3u64;
    let mut input = Vec::with_capacity(4096);
    for _ in 0..4096 {
        state = state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        input.push((state >> 61) as u8);
    }
    let result = optimize(&input, &test_config(4096, 4096), &Limits::default()).unwrap();

    assert_eq!(result.selection, ArchiveSelection::Procedural);
    assert!(result.archive.len() < result.literal_archive_bytes as usize);
    assert!(matches!(
        &result.programs[0].root,
        Node::File { child, .. } if matches!(child.as_ref(), Node::EntropyLiteral(_))
    ));
    assert_eq!(result.coverage.literal_segments, 1);
    assert_eq!(result.coverage.literal_source_bytes, input.len() as u64);
    assert_eq!(result.coverage.function_segments, 0);
    assert_eq!(result.coverage.function_source_bytes, 0);
    assert_verified(&result, &input);
}

#[test]
fn multi_block_candidate_roundtrips_and_reports_every_block() {
    let mut input = vec![3; 1024];
    input.extend(vec![91; 1024]);
    input.extend(vec![207; 1024]);
    let result = optimize(&input, &test_config(1024, 1024), &Limits::default()).unwrap();

    assert_eq!(result.selection, ArchiveSelection::Procedural);
    assert_eq!(result.programs.len(), 3);
    assert_eq!(result.coverage.block_count, 3);
    assert_eq!(result.coverage.function_segments, 3);
    assert_verified(&result, &input);
}

#[test]
fn deterministic_random_like_bytes_never_lose_to_literal() {
    let mut value = 0x1234_5678u32;
    let mut input = Vec::new();
    for _ in 0..257 {
        value = value.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
        input.push((value >> 24) as u8);
    }
    let config = test_config(257, 64);
    let first = optimize(&input, &config, &Limits::default()).unwrap();
    let second = optimize(&input, &config, &Limits::default()).unwrap();

    assert_eq!(first.archive, second.archive);
    assert_eq!(first.ledger, second.ledger);
    assert!(
        first.archive.len()
            <= encode_literal_archive(&input, &Limits::default())
                .unwrap()
                .len()
    );
    assert_verified(&first, &input);
}

#[test]
fn every_hard_budget_stops_deterministically_and_falls_back() {
    let input = vec![7; 96];
    let cases = [
        (
            {
                let mut config = test_config(96, 32);
                config.work_budget = 0;
                config
            },
            LedgerReason::WorkBudget,
        ),
        (
            {
                let mut config = test_config(96, 32);
                config.max_candidates = 0;
                config
            },
            LedgerReason::CandidateBudget,
        ),
        (
            {
                let mut config = test_config(96, 32);
                config.max_states = 1;
                config
            },
            LedgerReason::StateBudget,
        ),
        (
            {
                let mut config = test_config(96, 32);
                config.max_ledger_entries = 2;
                config
            },
            LedgerReason::LedgerBudget,
        ),
    ];

    for (config, reason) in cases {
        let first = optimize(&input, &config, &Limits::default()).unwrap();
        let second = optimize(&input, &config, &Limits::default()).unwrap();
        assert_eq!(first.selection, ArchiveSelection::LiteralFallback);
        assert_eq!(first.archive, second.archive);
        assert_eq!(first.ledger, second.ledger);
        assert!(first.usage.budget_exhausted);
        assert!(first
            .ledger
            .iter()
            .any(|entry| entry.event == LedgerEvent::BudgetStop && entry.reason == reason));
    }
}

#[derive(Clone)]
struct BruteEdge {
    end: usize,
    node: Node,
}

fn brute_archive_cost(program: &Program, archive: &[u8], limits: &Limits) -> CandidateCost {
    let report = program.validate(limits).unwrap();
    let memory = checked_u64_add(
        report.original_bytes,
        report.temporary_bytes,
        "test decode memory",
    )
    .unwrap();
    let local = program
        .candidate_cost(archive.len() as u64, memory, limits)
        .unwrap();
    CandidateCost {
        canonical_payload: archive.to_vec(),
        ..local
    }
}

fn brute_program(input: &[u8], edges: &[BruteEdge]) -> Program {
    let child = if edges.len() == 1 {
        edges[0].node.clone()
    } else {
        Node::Split {
            boundaries: edges[..edges.len() - 1]
                .iter()
                .map(|edge| edge.end as u64)
                .collect(),
            children: edges.iter().map(|edge| edge.node.clone()).collect(),
        }
    };
    Program {
        definitions: Vec::new(),
        root: Node::File {
            original_length: input.len() as u64,
            child: Box::new(child),
        },
    }
}

fn enumerate_brute(
    input: &[u8],
    boundaries: &[usize],
    start_index: usize,
    path: &mut Vec<BruteEdge>,
    candidates: &mut Vec<Program>,
) {
    if start_index == boundaries.len() - 1 {
        candidates.push(brute_program(input, path));
        return;
    }
    for end_index in start_index + 1..boundaries.len() {
        let start = boundaries[start_index];
        let end = boundaries[end_index];
        let interval = &input[start..end];
        let mut nodes = vec![Node::Literal(interval.to_vec())];
        if interval.iter().all(|value| *value == interval[0]) {
            nodes.push(Node::Const {
                length: interval.len() as u64,
                value: interval[0],
            });
        }
        for node in nodes {
            path.push(BruteEdge { end, node });
            enumerate_brute(input, boundaries, end_index, path, candidates);
            path.pop();
        }
    }
}

#[test]
fn exact_dp_matches_exhaustive_small_instance() {
    let mut input = vec![4; 8];
    input.extend(vec![9; 8]);
    input.extend(vec![4; 8]);
    let config = test_config(24, 8);
    let limits = Limits::default();
    let mut provider = ExactConstProvider;
    let mut providers: [&mut dyn CandidateProvider; 1] = [&mut provider];
    let optimized = optimize_with_providers(&input, &config, &limits, &mut providers).unwrap();

    let mut programs = Vec::new();
    enumerate_brute(&input, &[0, 8, 16, 24], 0, &mut Vec::new(), &mut programs);
    let mut best: Option<(CandidateCost, Vec<u8>)> = None;
    for program in programs {
        let archive = encode_archive(
            &[ArchiveBlock {
                program: &program,
                restored: &input,
            }],
            &limits,
        )
        .unwrap();
        let cost = brute_archive_cost(&program, &archive, &limits);
        if best.as_ref().is_none_or(|(best_cost, _)| cost < *best_cost) {
            best = Some((cost, archive));
        }
    }
    let literal = encode_literal_archive(&input, &limits).unwrap();
    let literal_program = Program::literal(input.clone());
    let literal_cost = brute_archive_cost(&literal_program, &literal, &limits);
    if best
        .as_ref()
        .is_none_or(|(best_cost, _)| literal_cost < *best_cost)
    {
        best = Some((literal_cost, literal));
    }

    assert_eq!(optimized.archive, best.unwrap().1);
    assert_verified(&optimized, &input);
}

struct OrderedProvider {
    id: &'static str,
}

impl CandidateProvider for OrderedProvider {
    fn provider_id(&self) -> &'static str {
        self.id
    }

    fn search(
        &mut self,
        interval: &[u8],
        _context: ProviderContext,
        _limits: &Limits,
    ) -> Result<ProviderSearch> {
        Ok(ProviderSearch::complete(
            vec![ProvidedCandidate {
                key: ProviderCandidateKey {
                    family: 7,
                    parameters: Vec::new(),
                    canonical_payload: Vec::new(),
                },
                class: CoverageClass::Function,
                node: Node::Const {
                    length: interval.len() as u64,
                    value: interval[0],
                },
            }],
            0,
        ))
    }
}

#[test]
fn provider_order_does_not_change_archive_or_ledger() {
    let input = vec![0; 128];
    let config = test_config(128, 64);
    let limits = Limits::default();
    let mut alpha = OrderedProvider { id: "alpha" };
    let mut zeta = OrderedProvider { id: "zeta" };
    let mut first_order: [&mut dyn CandidateProvider; 2] = [&mut zeta, &mut alpha];
    let first = optimize_with_providers(&input, &config, &limits, &mut first_order).unwrap();

    let mut alpha = OrderedProvider { id: "alpha" };
    let mut zeta = OrderedProvider { id: "zeta" };
    let mut second_order: [&mut dyn CandidateProvider; 2] = [&mut alpha, &mut zeta];
    let second = optimize_with_providers(&input, &config, &limits, &mut second_order).unwrap();

    assert_eq!(first.archive, second.archive);
    assert_eq!(first.ledger, second.ledger);
}

struct WorkTieProvider;

impl CandidateProvider for WorkTieProvider {
    fn provider_id(&self) -> &'static str {
        "work-tie"
    }

    fn search(
        &mut self,
        interval: &[u8],
        _context: ProviderContext,
        _limits: &Limits,
    ) -> Result<ProviderSearch> {
        let length = interval.len() as u64;
        // Both nodes have the same exact serialized record length for this
        // interval. The lower key intentionally belongs to LINEAR, while
        // GROUP(CONST) must win because its exact decode work is lower.
        Ok(ProviderSearch::complete(
            vec![
                ProvidedCandidate {
                    key: ProviderCandidateKey {
                        family: 0,
                        parameters: Vec::new(),
                        canonical_payload: Vec::new(),
                    },
                    class: CoverageClass::Function,
                    node: Node::Linear {
                        count: length,
                        width: 8,
                        modulus: 256,
                        a: 0,
                        b: 0,
                    },
                },
                ProvidedCandidate {
                    key: ProviderCandidateKey {
                        family: 1,
                        parameters: Vec::new(),
                        canonical_payload: Vec::new(),
                    },
                    class: CoverageClass::Coordinate,
                    node: Node::Group {
                        coordinate: CoordinateDescriptor::Identity,
                        child: Box::new(Node::Const { length, value: 0 }),
                    },
                },
            ],
            0,
        ))
    }
}

#[test]
fn normative_decode_work_breaks_an_exact_archive_size_tie() {
    let input = vec![0; 64];
    let config = test_config(64, 64);
    let limits = Limits::default();
    let mut provider = WorkTieProvider;
    let mut providers: [&mut dyn CandidateProvider; 1] = [&mut provider];
    let result = optimize_with_providers(&input, &config, &limits, &mut providers).unwrap();

    assert_eq!(result.selection, ArchiveSelection::Procedural);
    assert!(matches!(
        &result.programs[0].root,
        Node::File { child, .. } if matches!(child.as_ref(), Node::Group { .. })
    ));
    assert_eq!(result.coverage.coordinate_segments, 1);
}

#[test]
fn canonical_grid_has_only_multiples_and_one_final_tail() {
    assert_eq!(canonical_microblock_boundaries(0, 8).unwrap(), vec![0]);
    assert_eq!(
        canonical_microblock_boundaries(25, 8).unwrap(),
        vec![0, 8, 16, 24, 25]
    );
    assert!(canonical_microblock_boundaries(25, 0).is_err());
}

fn single_block_portfolio(length: u32) -> PortfolioConfig {
    let mut config = PortfolioConfig::for_profile(PortfolioProfile::Balanced);
    config.optimizer.block_bytes = length;
    config.optimizer.microblock_bytes = length;
    config.optimizer.max_segments_per_block = 1;
    config.optimizer.max_states = 4096;
    config.optimizer.max_candidates = 4096;
    config.optimizer.work_budget = 1 << 34;
    config.optimizer.max_ledger_entries = 16_384;
    config.optimizer.functions.max_period = 8;
    config.optimizer.functions.max_recurrence_order = 0;
    config.optimizer.functions.recurrence_coefficients.clear();
    config.optimizer.functions.max_exceptions = 0;
    config.optimizer.functions.work_budget = 1 << 26;
    config.coordinates.functions = config.optimizer.functions.clone();
    config.enable_coordinates = false;
    config
}

#[test]
fn built_in_profiles_keep_oracle_gated_engines_disabled() {
    for profile in [
        PortfolioProfile::Fast,
        PortfolioProfile::Balanced,
        PortfolioProfile::Max,
        PortfolioProfile::Structured,
        PortfolioProfile::Repository,
    ] {
        let config = PortfolioConfig::for_profile(profile);
        assert!(config.enable_whole_block_entropy);
        assert!(config.enable_whole_block_functions);
        assert_eq!(
            config.enable_interval_functions,
            !matches!(profile, PortfolioProfile::Balanced | PortfolioProfile::Max)
        );
        assert_eq!(
            config.enable_coordinates,
            !matches!(profile, PortfolioProfile::Balanced | PortfolioProfile::Max)
        );
        assert!(!config.enable_residual);
        assert!(!config.enable_symbolic);
        assert!(!config.enable_graph);
        assert_eq!(config.residual.config.gate, ArplGate::OracleDisabled);
        assert_eq!(config.residual.config.max_depth, 0);
    }
}

#[test]
fn built_in_profile_wire_search_sizes_match_checked_in_contracts() {
    let expected = [
        (PortfolioProfile::Fast, 1_048_576, 4_096, 256, 24_000_000),
        (
            PortfolioProfile::Balanced,
            1_048_576,
            4_096,
            4_096,
            80_000_000,
        ),
        (
            PortfolioProfile::Max,
            2_097_152,
            4_096,
            65_536,
            1_000_000_000,
        ),
        (
            PortfolioProfile::Structured,
            1_048_576,
            4_096,
            16_384,
            300_000_000,
        ),
        (
            PortfolioProfile::Repository,
            1_048_576,
            4_096,
            65_536,
            1_000_000_000,
        ),
    ];
    for (profile, block, microblock, candidates, work) in expected {
        let config = PortfolioConfig::for_profile(profile);
        let expected_policy = if profile == PortfolioProfile::Fast {
            None
        } else {
            Some(LzParserPolicy::C8Lazy)
        };
        assert_eq!(config.entropy.lz_huffman_policy, expected_policy);
        let optimizer = config.optimizer;
        assert_eq!(optimizer.block_bytes, block);
        assert_eq!(optimizer.microblock_bytes, microblock);
        assert_eq!(optimizer.max_candidates, candidates);
        assert_eq!(optimizer.work_budget, work);
        if matches!(
            profile,
            PortfolioProfile::Max | PortfolioProfile::Structured
        ) {
            assert_eq!(optimizer.functions.max_period, 256);
            assert_eq!(optimizer.functions.max_recurrence_order, 3);
            assert_eq!(
                optimizer.functions.recurrence_coefficients,
                vec![0, 1, 3, 253, 255]
            );
            assert_eq!(optimizer.functions.work_budget, 96_000_000);
            assert_eq!(optimizer.functions.max_ledger_entries, 2048);
        } else {
            assert_eq!(optimizer.functions.max_period, 32);
            assert_eq!(optimizer.functions.max_recurrence_order, 2);
            assert_eq!(optimizer.functions.recurrence_coefficients, vec![0, 1, 255]);
            assert_eq!(optimizer.functions.work_budget, 1 << 20);
            assert_eq!(optimizer.functions.max_ledger_entries, 1024);
        }
    }
}

#[test]
fn structured_profile_explains_the_frozen_exact_generator_families() {
    let recurrence = {
        let mut bytes = vec![1u8, 1];
        while bytes.len() < 65_536 {
            let end = bytes.len();
            bytes.push(bytes[end - 1].wrapping_add(bytes[end - 2]));
        }
        bytes
    };
    let polynomial = (0..65_536u64)
        .map(|index| {
            let x = index as u8;
            7u8.wrapping_add(11u8.wrapping_mul(x))
                .wrapping_add(5u8.wrapping_mul(x.wrapping_mul(x)))
        })
        .collect::<Vec<_>>();
    let lfsr = include_bytes!(
        "../../../../datasets/synthetic/lfsr8/s0000065536_n000p000_seed1297748005.bin"
    )
    .to_vec();

    for (family, input) in [
        ("recurrence", recurrence),
        ("polynomial-d2", polynomial),
        ("lfsr8", lfsr),
    ] {
        let mut config = PortfolioConfig::for_profile(PortfolioProfile::Structured);
        config.enable_interval_functions = false;
        config.enable_coordinates = false;
        let result = optimize_portfolio(&input, &config, &Limits::default()).unwrap();
        assert_eq!(result.selection, ArchiveSelection::Procedural, "{family}");
        assert_eq!(
            result.coverage.function_source_bytes,
            input.len() as u64,
            "{family}: root={:?}",
            result.programs[0].root
        );
        assert_eq!(result.coverage.literal_source_bytes, 0, "{family}");
        assert!(
            result
                .ledger
                .iter()
                .any(|row| row.selected && row.descriptor.provider == "mathsvg-functions-whole"),
            "{family}"
        );
        assert_verified(&result, &input);
    }
}

#[test]
fn whole_block_entropy_is_deterministic_and_strictly_budgeted() {
    let input = b"the quick brown fox jumps over the lazy dog. ".repeat(128);
    let entropy_work = input.len() as u64 * (LeafCodec::ALL.len() as u64 + 1);
    let context = ProviderContext {
        block_index: 0,
        global_start: 0,
        global_end: input.len() as u64,
        candidates_remaining: 1,
        work_remaining: u64::MAX,
    };
    let mut first_provider = EntropyProvider::default();
    let first = first_provider
        .search(&input, context, &Limits::default())
        .unwrap();
    let mut second_provider = EntropyProvider::default();
    let second = second_provider
        .search(&input, context, &Limits::default())
        .unwrap();
    assert_eq!(first, second);
    assert_eq!(first.work_used, entropy_work);
    assert_eq!(first.represented_descriptors, 1);
    assert_eq!(first.candidates.len(), 1);
    assert!(first.complete_within_declared_catalogue);
    assert!(!first.budget_exhausted);

    let mut no_candidate_provider = EntropyProvider::default();
    let no_candidate = no_candidate_provider
        .search(
            &input,
            ProviderContext {
                candidates_remaining: 0,
                ..context
            },
            &Limits::default(),
        )
        .unwrap();
    assert!(no_candidate.candidates.is_empty());
    assert_eq!(no_candidate.work_used, 0);
    assert_eq!(no_candidate.represented_descriptors, 1);
    assert!(no_candidate.budget_exhausted);

    let mut no_work_provider = EntropyProvider::default();
    let no_work = no_work_provider
        .search(
            &input,
            ProviderContext {
                work_remaining: entropy_work - 1,
                ..context
            },
            &Limits::default(),
        )
        .unwrap();
    assert!(no_work.candidates.is_empty());
    assert_eq!(no_work.work_used, 0);
    assert_eq!(no_work.represented_descriptors, 1);
    assert!(no_work.budget_exhausted);

    let mut no_verification_provider = EntropyProvider::default();
    let no_verification = no_verification_provider
        .search(
            &input,
            ProviderContext {
                work_remaining: entropy_work,
                ..context
            },
            &Limits::default(),
        )
        .unwrap();
    assert!(no_verification.candidates.is_empty());
    assert_eq!(no_verification.work_used, entropy_work);
    assert_eq!(no_verification.represented_descriptors, 1);
    assert!(no_verification.budget_exhausted);
}

#[test]
fn whole_block_entropy_verification_is_charged_exactly_once_at_the_optimizer_boundary() {
    let input = include_bytes!("../../../../datasets/data/canterbury/alice29.txt");
    let entropy_work = input.len() as u64 * (LeafCodec::ALL.len() as u64 + 1);
    let encoding = encode_best(input).unwrap();
    let scored_verification_work = encoding.score.decode_work + 2;
    let verification_work = Program {
        definitions: Vec::new(),
        root: Node::File {
            original_length: input.len() as u64,
            child: Box::new(Node::EntropyLiteral(encoding.bytes)),
        },
    }
    .validate(&Limits::default())
    .unwrap()
    .decode_work;
    assert_eq!(verification_work, scored_verification_work);
    let exact_budget = entropy_work + verification_work;
    let mut config = test_config(input.len() as u32, input.len() as u32);
    config.max_segments_per_block = 1;
    config.max_candidates = 1;
    config.max_ledger_entries = 16;
    config.work_budget = exact_budget;
    let mut entropy = EntropyProvider::default();
    let result = optimize_with_all_providers(
        input,
        &config,
        &Limits::default(),
        &mut [],
        &mut [&mut entropy],
    )
    .unwrap();

    assert_eq!(
        result.selection,
        ArchiveSelection::Procedural,
        "usage={:?}; ledger={:#?}",
        result.usage,
        result.ledger
    );
    assert_eq!(result.usage.work, exact_budget);
    assert!(!result.usage.budget_exhausted);
    assert!(result.usage.complete_within_declared_catalogue);
    assert_verified(&result, input);
}

#[test]
fn enhanced_entropy_policy_preflights_and_retains_the_g1_candidate_on_budget_stop() {
    let input = b"preflight keeps the exact G1 candidate; ".repeat(256);
    let baseline = encode_best(&input).unwrap();
    let baseline_search_work = input.len() as u64 * (LeafCodec::ALL.len() as u64 + 1);
    let baseline_required_work = baseline_search_work + baseline.score.decode_work + 2;
    let mut provider = EntropyProvider {
        lz_huffman_policy: Some(LzParserPolicy::C4Lazy),
    };
    let searched = provider
        .search(
            &input,
            ProviderContext {
                block_index: 0,
                global_start: 0,
                global_end: input.len() as u64,
                candidates_remaining: 1,
                work_remaining: baseline_required_work,
            },
            &Limits::default(),
        )
        .unwrap();
    assert_eq!(searched.work_used, baseline_search_work);
    assert_eq!(searched.candidates.len(), 1);
    assert!(!searched.complete_within_declared_catalogue);
    assert!(searched.budget_exhausted);
    assert_eq!(
        searched.candidates[0].key.canonical_payload,
        b"configured=G1+C4L;effective=G1"
    );
    let envelope = match &searched.candidates[0].program.root {
        Node::File { child, .. } => match child.as_ref() {
            Node::EntropyLiteral(envelope) => envelope,
            other => panic!("unexpected G1 child after policy preflight: {other:?}"),
        },
        other => panic!("unexpected G1 program after policy preflight: {other:?}"),
    };
    assert_eq!(envelope, &baseline.bytes);
}

#[test]
fn losing_enhanced_entropy_policy_retains_the_eager_two_walk_work_charge() {
    let mut value = 0x7f4a_7c15u32;
    let input = (0..8192)
        .map(|_| {
            value = value.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
            (value >> 24) as u8
        })
        .collect::<Vec<_>>();
    let baseline = encode_best(&input).unwrap();
    let policy = count_lz_huffman_policy(&input, LzParserPolicy::C4Lazy).unwrap();
    assert!(policy.score.unwrap() >= baseline.score);
    let baseline_search_work = input.len() as u64 * (LeafCodec::ALL.len() as u64 + 1);
    let policy_work = policy.stats.work_units().unwrap() * 2;
    let expected_work = baseline_search_work + policy_work;

    let mut provider = EntropyProvider {
        lz_huffman_policy: Some(LzParserPolicy::C4Lazy),
    };
    let searched = provider
        .search(
            &input,
            ProviderContext {
                block_index: 0,
                global_start: 0,
                global_end: input.len() as u64,
                candidates_remaining: 1,
                work_remaining: u64::MAX,
            },
            &Limits::default(),
        )
        .unwrap();
    assert_eq!(searched.work_used, expected_work);
    assert!(searched.complete_within_declared_catalogue);
    assert!(!searched.budget_exhausted);
    assert_eq!(
        searched.candidates[0].key.canonical_payload,
        b"configured=G1+C4L;effective=G1"
    );
}

#[test]
fn built_in_fast_and_balanced_profiles_select_whole_block_entropy_on_alice() {
    let input = include_bytes!("../../../../datasets/data/canterbury/alice29.txt");
    let mut archive_bytes = Vec::new();
    for profile in [PortfolioProfile::Fast, PortfolioProfile::Balanced] {
        let result = optimize_portfolio(
            input,
            &PortfolioConfig::for_profile(profile),
            &Limits::default(),
        )
        .unwrap();
        assert_eq!(result.selection, ArchiveSelection::Procedural);
        assert!(matches!(
            &result.programs[0].root,
            Node::File { child, .. } if matches!(child.as_ref(), Node::EntropyLiteral(_))
        ));
        assert_eq!(result.coverage.literal_source_bytes, input.len() as u64);
        let selected_entropy = result
            .ledger
            .iter()
            .find(|row| row.selected && row.descriptor.provider == "mathsvg-entropy")
            .expect("whole-block entropy must be the selected Alice provider");
        let expected_policy = match profile {
            PortfolioProfile::Fast => b"configured=G1;effective=G1".as_slice(),
            PortfolioProfile::Balanced => b"configured=G1+C8L;effective=C8L".as_slice(),
            _ => unreachable!(),
        };
        assert_eq!(
            selected_entropy.descriptor.key.canonical_payload,
            expected_policy
        );
        archive_bytes.push(result.archive.len());
        assert_verified(&result, input);
    }
    assert!(
        archive_bytes[1] < archive_bytes[0],
        "Balanced C8L must improve exact Alice archive bytes: {archive_bytes:?}"
    );
}

#[test]
fn structured_whole_block_functions_beat_entropy_on_exact_64k_generators() {
    let periodic_pattern: Vec<u8> = (0..3u8)
        .map(|value| value.wrapping_mul(29).wrapping_add(11))
        .collect();
    let inputs = [
        ("constant", vec![37; 65_536]),
        (
            "linear",
            (0..65_536)
                .map(|index| (index as u8).wrapping_mul(3).wrapping_add(7))
                .collect(),
        ),
        (
            "periodic",
            (0..65_536)
                .map(|index| periodic_pattern[index % periodic_pattern.len()])
                .collect(),
        ),
    ];
    for (family, input) in inputs {
        let mut config = PortfolioConfig::for_profile(PortfolioProfile::Structured);
        assert_eq!(config.optimizer.work_budget, 300_000_000);
        // The whole-block phase is identical to the built-in profile. Disable
        // only the irrelevant post-winner interval tail so the deeper frozen
        // generator catalogue retains its real work budget while this remains
        // a unit test rather than a long-running segmentation benchmark.
        config.enable_interval_functions = false;
        config.enable_coordinates = false;
        let first = optimize_portfolio(&input, &config, &Limits::default()).unwrap();
        let second = optimize_portfolio(&input, &config, &Limits::default()).unwrap();
        assert_eq!(first.archive, second.archive);
        assert_eq!(first.ledger, second.ledger);
        assert_eq!(first.selection, ArchiveSelection::Procedural);
        assert_eq!(
            first.coverage.function_source_bytes,
            input.len() as u64,
            "root={:?} selected={:?}",
            first.programs[0].root,
            first
                .ledger
                .iter()
                .filter(|row| row.selected)
                .map(|row| (&row.descriptor.provider, row.event, row.reason))
                .collect::<Vec<_>>()
        );
        assert_eq!(first.coverage.literal_source_bytes, 0);
        assert!(first
            .ledger
            .iter()
            .any(|row| row.selected && row.descriptor.provider == "mathsvg-functions-whole"));

        let entropy = encode_best(&input).unwrap();
        let entropy_program = Program {
            definitions: Vec::new(),
            root: Node::File {
                original_length: input.len() as u64,
                child: Box::new(Node::EntropyLiteral(entropy.bytes)),
            },
        };
        let entropy_archive = encode_archive(
            &[ArchiveBlock {
                program: &entropy_program,
                restored: &input,
            }],
            &Limits::default(),
        )
        .unwrap();
        eprintln!(
            "structured exact {family}: archive={} entropy={} literal={}",
            first.archive.len(),
            entropy_archive.len(),
            first.literal_archive_bytes
        );
        assert!(first.archive.len() < entropy_archive.len());
        assert!(first.archive.len() as u64 <= first.literal_archive_bytes);
        assert_verified(&first, &input);
    }
}

#[test]
fn every_public_ablation_removes_its_real_provider_from_the_catalogue() {
    let input = b"bounded deterministic ablation evidence ".repeat(128);
    let provider_ids = [
        (PortfolioAlgorithm::WholeBlockEntropy, "mathsvg-entropy"),
        (
            PortfolioAlgorithm::WholeBlockFunctions,
            "mathsvg-functions-whole",
        ),
        (PortfolioAlgorithm::IntervalFunctions, "mathsvg-functions"),
        (PortfolioAlgorithm::Coordinates, "mathsvg-coordinates"),
    ];
    for (algorithm, provider_id) in provider_ids {
        let mut enabled = single_block_portfolio(input.len() as u32);
        enabled.set_algorithm_enabled(algorithm, true);
        enabled.optimizer.work_budget = 1 << 28;
        enabled.optimizer.max_ledger_entries = 16_384;
        assert!(enabled.algorithm_enabled(algorithm));
        let enabled_result = optimize_portfolio(&input, &enabled, &Limits::default()).unwrap();
        assert!(enabled_result
            .ledger
            .iter()
            .any(|row| row.descriptor.provider == provider_id));

        let mut disabled = enabled;
        disabled.set_algorithm_enabled(algorithm, false);
        assert!(!disabled.algorithm_enabled(algorithm));
        let first = optimize_portfolio(&input, &disabled, &Limits::default()).unwrap();
        let second = optimize_portfolio(&input, &disabled, &Limits::default()).unwrap();
        assert_eq!(first.archive, second.archive);
        assert_eq!(first.ledger, second.ledger);
        assert!(!first
            .ledger
            .iter()
            .any(|row| row.descriptor.provider == provider_id));
        assert!(first.archive.len() as u64 <= first.literal_archive_bytes);
        assert_verified(&first, &input);
    }
}

#[test]
fn coordinate_portfolio_emits_a_real_inverse_transform() {
    let limits = Limits::default();
    let descriptor = NativeCoordinateDescriptor {
        original_bytes: 512,
        transform: CoordinateTransform::Stride {
            element_width: 1,
            channels: 2,
        },
    };
    let transformed: Vec<u8> = (0..512).map(|index| index as u8).collect();
    let input = coordinate_inverse(&transformed, &descriptor, &limits).unwrap();
    let mut config = single_block_portfolio(512);
    config.enable_coordinates = true;
    config.coordinates.discovery.max_candidates = 64;
    config.coordinates.discovery.max_channels = 4;
    config.coordinates.discovery.max_probe_lag = 0;
    config.coordinates.discovery.max_peak_lags = 0;
    config.coordinates.discovery.max_probe_bytes = 512;

    let first = optimize_portfolio(&input, &config, &limits).unwrap();
    let second = optimize_portfolio(&input, &config, &limits).unwrap();
    assert_eq!(first.archive, second.archive);
    assert_eq!(first.ledger, second.ledger);
    assert!(matches!(
        &first.programs[0].root,
        Node::File { child, .. } if matches!(child.as_ref(), Node::Coordinate { .. })
    ));
    assert!(!first
        .ledger
        .iter()
        .any(|row| row.descriptor.provider == "mathsvg-whole-coordinate-basis"));
    assert_eq!(first.coverage.coordinate_source_bytes, input.len() as u64);
    assert_verified(&first, &input);

    let mut ablated = config;
    ablated.set_algorithm_enabled(PortfolioAlgorithm::Coordinates, false);
    let without_coordinates = optimize_portfolio(&input, &ablated, &limits).unwrap();
    assert!(first.archive.len() < without_coordinates.archive.len());
    assert!(!without_coordinates
        .ledger
        .iter()
        .any(|row| row.descriptor.provider == "mathsvg-coordinates"));
    assert!(!without_coordinates
        .ledger
        .iter()
        .any(|row| row.descriptor.provider == "mathsvg-whole-coordinate-basis"));
    assert_verified(&without_coordinates, &input);
}

#[test]
fn coordinate_basis_splits_natural_stride_lanes_exactly_and_deterministically() {
    let limits = Limits::default();
    let descriptor = NativeCoordinateDescriptor {
        original_bytes: 4096,
        transform: CoordinateTransform::Stride {
            element_width: 1,
            channels: 4,
        },
    };
    let mut transformed = Vec::with_capacity(4096);
    for value in [3u8, 17, 89, 241] {
        transformed.extend(std::iter::repeat_n(value, 1024));
    }
    let input = coordinate_inverse(&transformed, &descriptor, &limits).unwrap();
    let provider = CoordinateBasisProvider {
        discovery: mathsvg_coordinates::DiscoveryConfig {
            max_candidates: 16,
            max_channels: 4,
            max_probe_lag: 0,
            max_peak_lags: 0,
            max_probe_bytes: 4096,
        },
        functions: mathsvg_functions::SearchConfig {
            max_period: 8,
            max_recurrence_order: 0,
            recurrence_coefficients: Vec::new(),
            max_exceptions: 0,
            work_budget: 1 << 20,
            max_ledger_entries: 256,
        },
    };
    let context = ProviderContext {
        block_index: 0,
        global_start: 0,
        global_end: input.len() as u64,
        candidates_remaining: 32,
        work_remaining: 1 << 32,
    };
    let mut first_provider = provider.clone();
    let first = first_provider.search(&input, context, &limits).unwrap();
    let mut second_provider = provider;
    let second = second_provider.search(&input, context, &limits).unwrap();
    assert_eq!(first, second);
    assert!(first.work_used <= context.work_remaining);

    let candidate = first
        .candidates
        .iter()
        .find(|candidate| {
            matches!(
                &candidate.program.root,
                Node::File { child, .. }
                    if matches!(
                        child.as_ref(),
                        Node::Coordinate {
                            descriptor: actual,
                            child,
                        } if *actual == descriptor
                            && matches!(
                                child.as_ref(),
                                Node::Split { boundaries, children }
                                    if boundaries == &[1024, 2048, 3072]
                                        && children.len() == 4
                            )
                    )
            )
        })
        .expect("stride-four independent-lane candidate");
    assert_eq!(
        evaluate_program(&candidate.program, &limits).unwrap(),
        input
    );
    assert_eq!(candidate.class_source_bytes, 4096);
    assert_eq!(candidate.literal_source_bytes, 0);
    let breakdown = candidate.program.procedural_breakdown(&limits).unwrap();
    assert!(breakdown.coordinate_bytes > 0);
    assert_eq!(breakdown.function_reconstructed_bytes, 4096);
    assert_eq!(breakdown.literal_reconstructed_bytes, 0);

    let mut optimizer_config = test_config(4096, 4096);
    optimizer_config.work_budget = 1 << 30;
    optimizer_config.max_ledger_entries = 16_384;
    let mut explicit_provider = CoordinateBasisProvider {
        discovery: mathsvg_coordinates::DiscoveryConfig {
            max_candidates: 16,
            max_channels: 4,
            max_probe_lag: 0,
            max_peak_lags: 0,
            max_probe_bytes: 4096,
        },
        functions: mathsvg_functions::SearchConfig {
            max_period: 8,
            max_recurrence_order: 0,
            recurrence_coefficients: Vec::new(),
            max_exceptions: 0,
            work_budget: 1 << 20,
            max_ledger_entries: 256,
        },
    };
    let mut interval_providers: [&mut dyn CandidateProvider; 0] = [];
    let mut whole_providers: [&mut dyn WholeBlockCandidateProvider; 1] = [&mut explicit_provider];
    let optimized = optimize_with_all_providers(
        &input,
        &optimizer_config,
        &limits,
        &mut interval_providers,
        &mut whole_providers,
    )
    .unwrap();
    assert_eq!(optimized.selection, ArchiveSelection::Procedural);
    assert!(optimized.ledger.iter().any(|row| {
        row.selected && row.descriptor.provider == "mathsvg-whole-coordinate-basis"
    }));
    assert_eq!(optimized.coverage.coordinate_source_bytes, 4096);
    assert_eq!(optimized.coverage.literal_source_bytes, 0);
    assert_verified(&optimized, &input);
}

#[test]
fn coordinate_basis_budget_stop_is_exact_and_emits_nothing_unverifiable() {
    let input = b"coordinate budget stop".repeat(64);
    let mut provider = CoordinateBasisProvider::default();
    let searched = provider
        .search(
            &input,
            ProviderContext {
                block_index: 0,
                global_start: 0,
                global_end: input.len() as u64,
                candidates_remaining: 1,
                work_remaining: 1,
            },
            &Limits::default(),
        )
        .unwrap();
    assert!(searched.candidates.is_empty());
    assert_eq!(searched.work_used, 0);
    assert!(searched.budget_exhausted);
    assert!(!searched.complete_within_declared_catalogue);
}

struct CoordinateAblationMeasurement {
    no_coordinate_bytes: usize,
    whole_coordinate_bytes: usize,
    basis_bytes: usize,
    basis_program: Program,
}

fn measure_coordinate_ablation(input: &[u8], block_index: usize) -> CoordinateAblationMeasurement {
    let limits = Limits::default();
    let discovery = mathsvg_coordinates::DiscoveryConfig {
        max_candidates: 16,
        max_channels: 16,
        max_probe_lag: 0,
        max_peak_lags: 0,
        max_probe_bytes: 4096,
    };
    let functions = mathsvg_functions::SearchConfig {
        max_period: 0,
        max_recurrence_order: 0,
        recurrence_coefficients: Vec::new(),
        max_exceptions: 0,
        work_budget: 1 << 20,
        max_ledger_entries: 256,
    };
    let archive_size = |program: &Program| {
        encode_archive(
            &[ArchiveBlock {
                program,
                restored: input,
            }],
            &limits,
        )
        .unwrap()
        .len()
    };

    let baseline = mathsvg_functions::search_block(input, &functions, &limits).unwrap();
    let no_coordinate_bytes = archive_size(&baseline.winner.program);
    let context = ProviderContext {
        block_index: block_index as u64,
        global_start: (block_index * input.len()) as u64,
        global_end: ((block_index + 1) * input.len()) as u64,
        candidates_remaining: 64,
        work_remaining: 1 << 32,
    };

    let mut whole_coordinate = CoordinateProvider {
        discovery,
        functions: functions.clone(),
    };
    let whole = CandidateProvider::search(&mut whole_coordinate, input, context, &limits).unwrap();
    let whole_coordinate_bytes = whole
        .candidates
        .into_iter()
        .map(|candidate| {
            archive_size(&Program {
                definitions: Vec::new(),
                root: Node::File {
                    original_length: input.len() as u64,
                    child: Box::new(candidate.node),
                },
            })
        })
        .min()
        .expect("whole-coordinate development candidate");

    let mut basis_provider = CoordinateBasisProvider {
        discovery,
        functions,
    };
    let basis =
        WholeBlockCandidateProvider::search(&mut basis_provider, input, context, &limits).unwrap();
    let (basis_bytes, basis_program) = basis
        .candidates
        .into_iter()
        .map(|candidate| {
            let size = archive_size(&candidate.program);
            (size, candidate.program)
        })
        .min_by_key(|(size, _)| *size)
        .expect("independent-basis development candidate");

    CoordinateAblationMeasurement {
        no_coordinate_bytes,
        whole_coordinate_bytes,
        basis_bytes,
        basis_program,
    }
}

#[test]
fn coordinate_basis_real_development_ablation_triggers_stop_rule() {
    let limits = Limits::default();
    let input = &include_bytes!("../../../../datasets/data/canterbury/kennedy.xls")[..4096];
    // Constructing this provider directly is the explicit experimental API.
    // It is intentionally absent from built-in profiles after the paired
    // development ablation below failed the incremental retention gate.
    let measured = measure_coordinate_ablation(input, 0);

    eprintln!(
        "coordinate basis stop evidence: kennedy.xls block=0 \
         basis={} whole-coordinate={} no-coordinate={}",
        measured.basis_bytes, measured.whole_coordinate_bytes, measured.no_coordinate_bytes
    );
    assert_eq!(
        evaluate_program(&measured.basis_program, &limits).unwrap(),
        input
    );
    assert!(matches!(
        &measured.basis_program.root,
        Node::File { child, .. }
            if matches!(
                child.as_ref(),
                Node::Coordinate { child, .. }
                    if matches!(child.as_ref(), Node::Split { boundaries, children }
                        if boundaries.len() + 1 == children.len())
            )
    ));
    let gain = measured.no_coordinate_bytes - measured.basis_bytes;
    assert!(
        gain * 1000 >= measured.no_coordinate_bytes * 5,
        "coordinate basis gain {gain}/{} is below 0.5%",
        measured.no_coordinate_bytes
    );
    assert!(
        measured.whole_coordinate_bytes < measured.basis_bytes,
        "independent basis must remain experimental unless it beats the active coordinate path"
    );
}

#[test]
#[ignore = "frozen 73-block development evidence scan; run explicitly before freeze"]
fn coordinate_basis_full_real_development_scan_has_zero_incremental_wins() {
    let datasets: [(&str, &[u8], usize); 3] = [
        (
            "kennedy.xls",
            include_bytes!("../../../../datasets/data/canterbury/kennedy.xls"),
            32,
        ),
        (
            "progc",
            include_bytes!("../../../../datasets/data/calgary/progc"),
            9,
        ),
        (
            "alice29.txt",
            include_bytes!("../../../../datasets/data/canterbury/alice29.txt"),
            32,
        ),
    ];
    let mut sampled = 0usize;
    let mut incremental_wins = Vec::new();
    let mut representative = None;
    for (dataset, bytes, count) in datasets {
        for (block_index, input) in bytes.chunks_exact(4096).take(count).enumerate() {
            let measured = measure_coordinate_ablation(input, block_index);
            if dataset == "kennedy.xls" && block_index == 0 {
                representative = Some((
                    measured.no_coordinate_bytes,
                    measured.whole_coordinate_bytes,
                    measured.basis_bytes,
                ));
            }
            if measured.basis_bytes < measured.no_coordinate_bytes
                && measured.basis_bytes < measured.whole_coordinate_bytes
            {
                incremental_wins.push((
                    dataset,
                    block_index,
                    measured.no_coordinate_bytes,
                    measured.whole_coordinate_bytes,
                    measured.basis_bytes,
                ));
            }
            sampled += 1;
        }
    }
    assert_eq!(sampled, 73);
    assert_eq!(representative, Some((2737, 2264, 2569)));
    assert!(
        incremental_wins.is_empty(),
        "unexpected independent-basis wins require a new retention review: {incremental_wins:?}"
    );
}

#[test]
fn experimental_residual_adapter_wins_on_two_components() {
    let periodic = [5u8, 5, 9, 1];
    let input: Vec<u8> = (0..4096)
        .map(|index| {
            (index as u8)
                .wrapping_mul(3)
                .wrapping_add(17)
                .wrapping_add(periodic[index % periodic.len()])
        })
        .collect();
    let mut config = single_block_portfolio(4096);
    config.enable_residual = true;
    config.residual.config.gate = ArplGate::Experimental;
    config.residual.config.max_depth = 1;
    config.residual.config.domains = vec![ResidualDomain::AddMod256];
    config.residual.config.max_projection_period = 8;
    config.residual.config.max_recurrence_order = 0;
    config.residual.config.recurrence_coefficients.clear();
    config.residual.config.max_projection_descriptors = 16;
    config.residual.config.max_states = 128;
    config.residual.config.work_budget = 1 << 28;
    config.residual.config.max_ledger_entries = 4096;
    config.residual.config.minimum_gain_bytes = 1;
    config.residual.config.function_search = config.optimizer.functions.clone();

    let result = optimize_portfolio(&input, &config, &Limits::default()).unwrap();
    assert!(matches!(
        &result.programs[0].root,
        Node::File { child, .. } if matches!(child.as_ref(), Node::Correct { .. })
    ));
    assert_eq!(result.coverage.residual_source_bytes, input.len() as u64);
    assert!(result
        .ledger
        .iter()
        .any(|row| row.selected && row.descriptor.provider == "mathsvg-residual"));
    assert_verified(&result, &input);
}

#[test]
fn experimental_symbolic_adapter_wins_and_is_deterministic() {
    let pattern = [0u8, 0, 5, 5];
    let input: Vec<_> = (0..2048)
        .map(|index| {
            (3u8)
                .wrapping_mul(index as u8)
                .wrapping_add(7)
                .wrapping_add(pattern[index % pattern.len()])
        })
        .collect();
    let mut config = single_block_portfolio(2048);
    config.enable_symbolic = true;
    config.symbolic.config.max_depth = 2;
    config.symbolic.config.max_period = 8;
    config.symbolic.config.max_states = 96;
    config.symbolic.config.max_pairs = 2048;
    config.symbolic.config.work_budget = 1 << 25;
    config.symbolic.config.max_ledger_entries = 4096;
    config.symbolic.config.residual_functions = config.optimizer.functions.clone();

    let first = optimize_portfolio(&input, &config, &Limits::default()).unwrap();
    let second = optimize_portfolio(&input, &config, &Limits::default()).unwrap();
    assert_eq!(first.archive, second.archive);
    assert_eq!(first.ledger, second.ledger);
    assert!(first
        .ledger
        .iter()
        .any(|row| row.selected && row.descriptor.provider == "mathsvg-symbolic"));
    assert!(matches!(
        &first.programs[0].root,
        Node::File { child, .. } if matches!(child.as_ref(), Node::Correct { .. })
    ));
    assert_eq!(first.coverage.symbolic_source_bytes, input.len() as u64);
    assert_eq!(first.coverage.residual_source_bytes, 0);
    assert_verified(&first, &input);
}

#[test]
fn graph_competes_as_a_whole_program_and_preserves_definitions() {
    // Keep repeated chunks outside the native LZ leaf's 65,535-byte window.
    // This fixture therefore exercises graph definitions as a genuinely
    // distinct whole-program candidate instead of relying on graph to beat a
    // closer repetition that the entropy portfolio can encode directly.
    let mut chunk_state = 0x4d59_5df4_d0f3_3173u64;
    let chunk: Vec<u8> = (0..128)
        .map(|_| {
            chunk_state = chunk_state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            (chunk_state >> 56) as u8
        })
        .collect();
    let mut filler_state = 0xd1b5_4a32_d192_ed03u64;
    let mut input = Vec::with_capacity(16 * chunk.len() + 15 * 65_536);
    for occurrence in 0..16 {
        if occurrence != 0 {
            input.extend((0..65_536).map(|_| {
                filler_state = filler_state
                    .wrapping_mul(2_862_933_555_777_941_757)
                    .wrapping_add(3_037_000_493);
                (filler_state >> 56) as u8
            }));
        }
        input.extend_from_slice(&chunk);
    }
    let mut config = single_block_portfolio(input.len() as u32);
    config.enable_graph = true;
    config.graph.config = GraphConfig {
        chunk_bytes: 128,
        min_occurrences: 3,
        max_definitions: 8,
        max_subsets: 1 << 8,
        work_budget: 1 << 22,
        max_ledger_entries: (1 << 8) + 2,
    };

    let result = optimize_portfolio(&input, &config, &Limits::default()).unwrap();
    assert!(!result.programs[0].definitions.is_empty());
    assert_eq!(
        result.coverage.graph_source_bytes,
        (16 * chunk.len()) as u64
    );
    assert_eq!(
        result.coverage.literal_source_bytes,
        (input.len() - 16 * chunk.len()) as u64
    );
    assert!(result
        .ledger
        .iter()
        .any(|row| row.selected && row.descriptor.provider == "mathsvg-graph"));
    assert_verified(&result, &input);
}

#[test]
fn graph_subset_budget_is_visible_in_the_optimizer_ledger() {
    let chunk: Vec<u8> = (0..32u8)
        .map(|value| value.wrapping_mul(17).wrapping_add(3))
        .collect();
    let input = chunk.repeat(8);
    let mut config = single_block_portfolio(input.len() as u32);
    config.enable_graph = true;
    config.graph.config = GraphConfig {
        chunk_bytes: 32,
        min_occurrences: 3,
        max_definitions: 8,
        max_subsets: 1,
        work_budget: 1 << 22,
        max_ledger_entries: 4,
    };

    let result = optimize_portfolio(&input, &config, &Limits::default()).unwrap();
    assert!(result.usage.budget_exhausted);
    assert!(!result.usage.complete_within_declared_catalogue);
    assert!(result.ledger.iter().any(|row| {
        row.descriptor.provider == "mathsvg-graph"
            && row.event == LedgerEvent::BudgetStop
            && row.reason == LedgerReason::ProviderBudget
    }));
    assert_verified(&result, &input);
}

struct MalformedWholeBlockProvider;

impl WholeBlockCandidateProvider for MalformedWholeBlockProvider {
    fn provider_id(&self) -> &'static str {
        "malformed-whole"
    }

    fn search(
        &mut self,
        block: &[u8],
        _context: ProviderContext,
        _limits: &Limits,
    ) -> Result<WholeBlockProviderSearch> {
        Ok(WholeBlockProviderSearch {
            candidates: vec![ProvidedProgramCandidate {
                key: ProviderCandidateKey {
                    family: 1,
                    parameters: Vec::new(),
                    canonical_payload: Vec::new(),
                },
                class: CoverageClass::Graph,
                program: Program::literal(vec![0; block.len()]),
                class_source_bytes: block.len() as u64,
                literal_source_bytes: 0,
            }],
            work_used: 0,
            represented_descriptors: 1,
            complete_within_declared_catalogue: true,
            budget_exhausted: false,
        })
    }
}

#[test]
fn malformed_whole_block_candidate_is_independently_rejected() {
    let input = vec![7; 64];
    let config = test_config(64, 64);
    let limits = Limits::default();
    let mut interval = ExactConstProvider;
    let mut whole = MalformedWholeBlockProvider;
    let mut intervals: [&mut dyn CandidateProvider; 1] = [&mut interval];
    let mut wholes: [&mut dyn WholeBlockCandidateProvider; 1] = [&mut whole];
    assert_eq!(
        optimize_with_all_providers(&input, &config, &limits, &mut intervals, &mut wholes),
        Err(Error::InvalidValue(
            "whole-block provider did not exactly restore its block"
        ))
    );
}

struct OrderedWholeBlockProvider {
    id: &'static str,
}

impl WholeBlockCandidateProvider for OrderedWholeBlockProvider {
    fn provider_id(&self) -> &'static str {
        self.id
    }

    fn search(
        &mut self,
        block: &[u8],
        _context: ProviderContext,
        _limits: &Limits,
    ) -> Result<WholeBlockProviderSearch> {
        Ok(WholeBlockProviderSearch {
            candidates: vec![ProvidedProgramCandidate {
                key: ProviderCandidateKey {
                    family: 1,
                    parameters: vec![u64::from(block[0])],
                    canonical_payload: Vec::new(),
                },
                class: CoverageClass::Function,
                program: Program {
                    definitions: Vec::new(),
                    root: Node::File {
                        original_length: block.len() as u64,
                        child: Box::new(Node::Const {
                            length: block.len() as u64,
                            value: block[0],
                        }),
                    },
                },
                class_source_bytes: block.len() as u64,
                literal_source_bytes: 0,
            }],
            work_used: 0,
            represented_descriptors: 1,
            complete_within_declared_catalogue: true,
            budget_exhausted: false,
        })
    }
}

#[test]
fn whole_block_provider_order_is_canonical() {
    let input = vec![31; 128];
    let config = test_config(128, 128);
    let limits = Limits::default();

    let mut interval = ExactConstProvider;
    let mut alpha = OrderedWholeBlockProvider { id: "alpha-whole" };
    let mut zeta = OrderedWholeBlockProvider { id: "zeta-whole" };
    let mut intervals: [&mut dyn CandidateProvider; 1] = [&mut interval];
    let mut first_wholes: [&mut dyn WholeBlockCandidateProvider; 2] = [&mut zeta, &mut alpha];
    let first =
        optimize_with_all_providers(&input, &config, &limits, &mut intervals, &mut first_wholes)
            .unwrap();

    let mut interval = ExactConstProvider;
    let mut alpha = OrderedWholeBlockProvider { id: "alpha-whole" };
    let mut zeta = OrderedWholeBlockProvider { id: "zeta-whole" };
    let mut intervals: [&mut dyn CandidateProvider; 1] = [&mut interval];
    let mut second_wholes: [&mut dyn WholeBlockCandidateProvider; 2] = [&mut alpha, &mut zeta];
    let second =
        optimize_with_all_providers(&input, &config, &limits, &mut intervals, &mut second_wholes)
            .unwrap();

    assert_eq!(first.archive, second.archive);
    assert_eq!(first.ledger, second.ledger);
}

#[test]
fn residual_emission_requires_the_explicit_oracle_gate() {
    let mut config = single_block_portfolio(64);
    config.enable_residual = true;
    assert_eq!(
        optimize_portfolio(&[9; 64], &config, &Limits::default()),
        Err(Error::InvalidValue(
            "residual portfolio emission requires explicit Experimental gate"
        ))
    );
}

#[test]
fn every_portfolio_ablation_retains_the_literal_upper_bound() {
    let mut state = 0x6a09_e667_f3bc_c909u64;
    let input: Vec<u8> = (0..257)
        .map(|_| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            (state >> 19) as u8
        })
        .collect();
    for mut config in [
        single_block_portfolio(257),
        PortfolioConfig::experimental_all(PortfolioProfile::Balanced),
    ] {
        config.optimizer.block_bytes = 257;
        config.optimizer.microblock_bytes = 257;
        config.optimizer.max_segments_per_block = 1;
        config.optimizer.work_budget = 1 << 30;
        config.optimizer.max_ledger_entries = 16_384;
        config.coordinates.discovery.max_probe_lag = 8;
        config.coordinates.discovery.max_peak_lags = 4;
        config.coordinates.discovery.max_probe_bytes = 257;
        config.graph.config.chunk_bytes = 16;
        config.residual.config.max_depth = 1;
        let first = optimize_portfolio(&input, &config, &Limits::default()).unwrap();
        let second = optimize_portfolio(&input, &config, &Limits::default()).unwrap();
        assert_eq!(first.archive, second.archive);
        assert_eq!(first.ledger, second.ledger);
        assert!(first.archive.len() as u64 <= first.literal_archive_bytes);
        assert_verified(&first, &input);
    }
}

#[test]
fn seeded_whole_functions_preserve_archives_without_duplicate_entropy_work() {
    struct RegressionCase {
        name: &'static str,
        input: Vec<u8>,
        expected_candidates: usize,
        expected_work: u64,
        expected_represented: u64,
        expected_archive_bytes: usize,
        expected_archive_sha256: &'static str,
        expected_selection: ArchiveSelection,
    }

    let mut random_state = 0xa409_3822_299f_31d0u64;
    let random: Vec<u8> = (0..4096)
        .map(|_| {
            random_state ^= random_state << 13;
            random_state ^= random_state >> 7;
            random_state ^= random_state << 17;
            (random_state >> 56) as u8
        })
        .collect();
    let cases = [
        RegressionCase {
            name: "constant",
            input: vec![37; 4096],
            expected_candidates: 1,
            expected_work: 49_152,
            expected_represented: 4_101,
            expected_archive_bytes: 483,
            expected_archive_sha256:
                "b6a4c487a3757025763fb0f1139c6d3eeb1a5956dd3087618ec068ac0f49195c",
            expected_selection: ArchiveSelection::Procedural,
        },
        RegressionCase {
            name: "linear",
            input: (0..4096)
                .map(|index| 3u8.wrapping_mul(index as u8).wrapping_add(7))
                .collect(),
            expected_candidates: 1,
            expected_work: 49_152,
            expected_represented: 4_101,
            expected_archive_bytes: 487,
            expected_archive_sha256:
                "945874b03dddfabee0c3c19a8cd36e7c448222ac42cb25c255196aa4f48b7281",
            expected_selection: ArchiveSelection::Procedural,
        },
        RegressionCase {
            name: "random",
            input: random,
            expected_candidates: 0,
            expected_work: 49_152,
            expected_represented: 4_101,
            expected_archive_bytes: 4_580,
            expected_archive_sha256:
                "cbaf6688673642d736d105b428af761c3db1d9b4f952639a74c625cbe07ebf79",
            expected_selection: ArchiveSelection::LiteralFallback,
        },
        RegressionCase {
            name: "alice",
            input: include_bytes!("../../../../datasets/data/canterbury/alice29.txt").to_vec(),
            expected_candidates: 0,
            expected_work: 1_825_068,
            expected_represented: 152_093,
            expected_archive_bytes: 152_577,
            expected_archive_sha256:
                "1ef621d47039e2d38fcd3ed5b41f21e2b65a88c6433669ed3bc0efa4d2cc4232",
            expected_selection: ArchiveSelection::LiteralFallback,
        },
    ];
    for case in cases {
        let RegressionCase {
            name,
            input,
            expected_candidates,
            expected_work,
            expected_represented,
            expected_archive_bytes,
            expected_archive_sha256,
            expected_selection,
        } = case;
        let config = single_block_portfolio(input.len() as u32);
        let mut provider = mathsvg_optimizer::WholeBlockFunctionsProvider {
            config: config.optimizer.functions.clone(),
        };
        let searched = WholeBlockCandidateProvider::search(
            &mut provider,
            &input,
            ProviderContext {
                block_index: 0,
                global_start: 0,
                global_end: input.len() as u64,
                candidates_remaining: config.optimizer.max_candidates,
                work_remaining: config.optimizer.work_budget,
            },
            &Limits::default(),
        )
        .unwrap();
        let mut intervals: [&mut dyn CandidateProvider; 0] = [];
        let mut whole: [&mut dyn WholeBlockCandidateProvider; 1] = [&mut provider];
        let optimized = optimize_with_all_providers(
            &input,
            &config.optimizer,
            &Limits::default(),
            &mut intervals,
            &mut whole,
        )
        .unwrap();
        assert_eq!(
            searched.candidates.len(),
            expected_candidates,
            "{name}: candidates"
        );
        assert_eq!(searched.work_used, expected_work, "{name}: work");
        assert_eq!(
            searched.represented_descriptors, expected_represented,
            "{name}: represented descriptors"
        );
        assert!(!searched.complete_within_declared_catalogue, "{name}");
        assert!(!searched.budget_exhausted, "{name}");
        assert_eq!(
            optimized.archive.len(),
            expected_archive_bytes,
            "{name}: archive bytes"
        );
        assert_eq!(
            format!("{:x}", Sha256::digest(&optimized.archive)),
            expected_archive_sha256,
            "{name}: archive SHA-256"
        );
        assert_eq!(optimized.selection, expected_selection, "{name}: selection");
        assert_verified(&optimized, &input);
    }
}
