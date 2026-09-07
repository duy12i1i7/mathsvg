#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::env;
use std::error::Error;
use std::fmt::Write as _;
use std::fs;
use std::path::{Path, PathBuf};

use mathsvg_container::{decode_archive, encode_archive, ArchiveBlock};
use mathsvg_core::Limits;
use mathsvg_dsl::{Node, Program};
use mathsvg_evaluator::evaluate_program;
use mathsvg_functions::{
    search_block, CandidateFamily, LedgerStatus as FunctionLedgerStatus, SearchConfig,
};
use mathsvg_graph::{search_shared_block, GraphConfig};
use mathsvg_optimizer::{
    optimize, CandidateProvider, CoordinateBasisProvider, CoordinateProvider, OptimizerConfig,
    ProviderContext, WholeBlockCandidateProvider,
};
use mathsvg_residual::{search_residual, ArplGate, ResidualConfig};
use mathsvg_symbolic::{search_symbolic_block, SymbolicConfig};
use serde::Serialize;
use sha2::{Digest, Sha256};

const SAMPLE_BYTES: usize = 4096;
const SCHEMA: &str = "mathsvg-native-development-oracle-v2";

#[derive(Clone, Copy)]
struct SampleSpec {
    input_id: &'static str,
    relative_path: &'static str,
    origin: &'static str,
}

const SAMPLES: &[SampleSpec] = &[
    SampleSpec {
        input_id: "syn-constant",
        relative_path: "datasets/synthetic/constant_00/s0000004096_n000p000_seed1297748005.bin",
        origin: "synthetic",
    },
    SampleSpec {
        input_id: "syn-linear",
        relative_path: "datasets/synthetic/linear/s0000004096_n000p000_seed1297748005.bin",
        origin: "synthetic",
    },
    SampleSpec {
        input_id: "syn-periodic",
        relative_path: "datasets/synthetic/periodic/s0000004096_n000p000_seed1297748005.bin",
        origin: "synthetic",
    },
    SampleSpec {
        input_id: "syn-recurrence",
        relative_path: "datasets/synthetic/recurrence/s0000004096_n000p000_seed1297748005.bin",
        origin: "synthetic",
    },
    SampleSpec {
        input_id: "syn-piecewise",
        relative_path: "datasets/synthetic/piecewise_mixed/s0000004096_n000p000_seed1297748005.bin",
        origin: "synthetic",
    },
    SampleSpec {
        input_id: "syn-random",
        relative_path: "datasets/synthetic/random/s0000004096_n000p000_seed1297748005.bin",
        origin: "control",
    },
    SampleSpec {
        input_id: "syn-linear-noise1",
        relative_path: "datasets/synthetic/linear/s0000004096_n001p000_seed1297748005.bin",
        origin: "synthetic",
    },
    SampleSpec {
        input_id: "syn-periodic-noise1",
        relative_path: "datasets/synthetic/periodic/s0000004096_n001p000_seed1297748005.bin",
        origin: "synthetic",
    },
    SampleSpec {
        input_id: "syn-recurrence-noise1",
        relative_path: "datasets/synthetic/recurrence/s0000004096_n001p000_seed1297748005.bin",
        origin: "synthetic",
    },
    SampleSpec {
        input_id: "real-canterbury-alice",
        relative_path: "datasets/data/canterbury/alice29.txt",
        origin: "real",
    },
    SampleSpec {
        input_id: "real-canterbury-kennedy",
        relative_path: "datasets/data/canterbury/kennedy.xls",
        origin: "real",
    },
    SampleSpec {
        input_id: "real-calgary-pic",
        relative_path: "datasets/data/calgary/pic",
        origin: "real",
    },
    SampleSpec {
        input_id: "real-calgary-progc",
        relative_path: "datasets/data/calgary/progc",
        origin: "real",
    },
];

#[derive(Serialize)]
struct ProbeOutput {
    schema: &'static str,
    engine: &'static str,
    entropy_catalog: &'static str,
    sample_bytes_cap: usize,
    holdout_payload_inspected: bool,
    samples: Vec<SampleMeasurement>,
    coordinate_basis_blocks: Vec<CoordinateMeasurement>,
}

#[derive(Serialize)]
struct SampleMeasurement {
    input_id: &'static str,
    origin: &'static str,
    source: &'static str,
    sample_bytes: usize,
    sample_sha256: String,
    search: SearchMeasurement,
    segmentation: SegmentationMeasurement,
    residual: Vec<ResidualMeasurement>,
    symbolic: SymbolicMeasurement,
    dag: DagMeasurement,
}

#[derive(Serialize)]
struct SearchMeasurement {
    literal_archive_bytes: u64,
    winner_archive_bytes: u64,
    winner_family: String,
    winner_entropy_opcode: Option<u64>,
    headroom_bytes: u64,
    evaluated_family_minima: BTreeMap<String, u64>,
    work_used: u64,
    ledger_entries: usize,
    budget_exhausted: bool,
    complete_within_declared_catalogue: bool,
}

#[derive(Serialize)]
struct SegmentationMeasurement {
    baseline_archive_bytes: usize,
    oracle_archive_bytes: usize,
    headroom_bytes: usize,
    segment_count: u64,
    boundaries: Vec<u64>,
    selected_providers: Vec<String>,
    quantum_bytes: u32,
    work_used: u64,
    ledger_entries: u64,
    budget_exhausted: bool,
    complete_within_declared_catalogue: bool,
}

#[derive(Serialize)]
struct ResidualMeasurement {
    depth_limit: u8,
    baseline_function_archive_bytes: u64,
    winner_archive_bytes: u64,
    headroom_bytes: u64,
    winner_residual_depth: u8,
    winner_domains: Vec<String>,
    winner_projections: Vec<String>,
    states_visited: u32,
    work_used: u64,
    ledger_entries: usize,
    budget_exhausted: bool,
    heuristic_omission: bool,
    complete_within_declared_catalogue: bool,
}

#[derive(Serialize)]
struct SymbolicMeasurement {
    baseline_function_archive_bytes: u64,
    winner_archive_bytes: u64,
    headroom_bytes: u64,
    winner_expression_family: Option<String>,
    winner_expression_depth: Option<u8>,
    winner_residual_kind: Option<String>,
    states_retained: u32,
    pairs_considered: u64,
    work_used: u64,
    ledger_entries: usize,
    budget_exhausted: bool,
    complete_within_declared_catalogue: bool,
}

#[derive(Serialize)]
struct DagMeasurement {
    baseline_function_archive_bytes: u64,
    graph_archive_bytes: u64,
    portfolio_best_archive_bytes: u64,
    incremental_headroom_bytes: u64,
    active_definitions: u8,
    referenced_source_bytes: u64,
    mined_definitions: u8,
    omitted_definitions: u64,
    evaluated_subsets: u64,
    work_used: u64,
    ledger_entries: usize,
    budget_exhausted: bool,
    complete_within_declared_catalogue: bool,
}

#[derive(Serialize)]
struct CoordinateMeasurement {
    input_id: String,
    origin: &'static str,
    source: &'static str,
    block_index: usize,
    sample_bytes: usize,
    sample_sha256: String,
    no_coordinate_archive_bytes: usize,
    whole_coordinate_archive_bytes: Option<usize>,
    basis_archive_bytes: Option<usize>,
    basis_headroom_vs_no_coordinate_bytes: Option<i64>,
    basis_incremental_headroom_bytes: Option<i64>,
    basis_is_incremental_winner: bool,
    whole_complete_within_declared_catalogue: bool,
    whole_budget_exhausted: bool,
    basis_complete_within_declared_catalogue: bool,
    basis_budget_exhausted: bool,
}

fn family_name(family: CandidateFamily) -> &'static str {
    match family {
        CandidateFamily::Literal => "literal",
        CandidateFamily::EntropyLiteral => "entropy_literal",
        CandidateFamily::ConstExact => "const_exact",
        CandidateFamily::ConstExceptions => "const_exceptions",
        CandidateFamily::LinearExact => "linear_exact",
        CandidateFamily::LinearExceptions => "linear_exceptions",
        CandidateFamily::PeriodicExact => "periodic_exact",
        CandidateFamily::PeriodicExceptions => "periodic_exceptions",
        CandidateFamily::RecurrenceExact => "recurrence_exact",
        CandidateFamily::RecurrenceExceptions => "recurrence_exceptions",
    }
}

fn read_sample(repo_root: &Path, spec: SampleSpec) -> Result<Vec<u8>, Box<dyn Error>> {
    if spec.relative_path.contains("holdout") || spec.relative_path.contains("sealed") {
        return Err("native development probe refuses holdout/sealed paths".into());
    }
    let bytes = fs::read(repo_root.join(spec.relative_path))?;
    if bytes.is_empty() {
        return Err(format!("empty oracle input: {}", spec.relative_path).into());
    }
    Ok(bytes.into_iter().take(SAMPLE_BYTES).collect())
}

fn sha256_bytes(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut output = String::with_capacity(64);
    for byte in digest {
        write!(&mut output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
}

fn search_config() -> SearchConfig {
    SearchConfig::default()
}

fn measure_search(input: &[u8], limits: &Limits) -> Result<SearchMeasurement, Box<dyn Error>> {
    let result = search_block(input, &search_config(), limits)?;
    let verified = complete_archive_bytes(&result.winner.program, input, limits)?;
    if verified as u64 != result.winner.cost.archive_bytes {
        return Err("native search cost differs from verified archive bytes".into());
    }
    let mut family_minima = BTreeMap::new();
    for row in &result.ledger {
        if row.status != FunctionLedgerStatus::Tried {
            continue;
        }
        let Some(bytes) = row.actual_archive_bytes else {
            continue;
        };
        family_minima
            .entry(family_name(row.key.family).to_owned())
            .and_modify(|minimum: &mut u64| *minimum = (*minimum).min(bytes))
            .or_insert(bytes);
    }
    let winner_entropy_opcode = (result.winner.key.family == CandidateFamily::EntropyLiteral)
        .then(|| result.winner.key.parameters.first().copied())
        .flatten();
    Ok(SearchMeasurement {
        literal_archive_bytes: result.literal_archive_bytes,
        winner_archive_bytes: result.winner.cost.archive_bytes,
        winner_family: family_name(result.winner.key.family).to_owned(),
        winner_entropy_opcode,
        headroom_bytes: result
            .literal_archive_bytes
            .saturating_sub(result.winner.cost.archive_bytes),
        evaluated_family_minima: family_minima,
        work_used: result.work_used,
        ledger_entries: result.ledger.len(),
        budget_exhausted: result.budget_exhausted,
        complete_within_declared_catalogue: result.complete_within_declared_catalogue,
    })
}

fn segmentation_config(input_bytes: usize, quantum: u32, segments: u32) -> OptimizerConfig {
    // Match the production Balanced caps.  Raising this to the hard maximum
    // made the diagnostic itself retain gigabytes of losing DP states, which
    // is contrary to the bounded-memory question the oracle is meant to ask.
    OptimizerConfig {
        block_bytes: input_bytes as u32,
        microblock_bytes: quantum,
        max_segments_per_block: segments,
        max_states: 1 << 16,
        max_candidates: 4096,
        work_budget: 80_000_000,
        max_ledger_entries: 1 << 14,
        ..OptimizerConfig::default()
    }
}

fn measure_segmentation(
    input: &[u8],
    limits: &Limits,
) -> Result<SegmentationMeasurement, Box<dyn Error>> {
    let unsplit = optimize(
        input,
        &segmentation_config(input.len(), input.len() as u32, 1),
        limits,
    )?;
    let quantum = 256u32.min(input.len() as u32);
    let segment_limit = (input.len() as u32).div_ceil(quantum);
    let segmented = optimize(
        input,
        &segmentation_config(input.len(), quantum, segment_limit),
        limits,
    )?;
    let baseline = unsplit.archive.len();
    let oracle = segmented.archive.len();
    let boundaries = segmented
        .programs
        .first()
        .and_then(|program| match &program.root {
            Node::File { child, .. } => match child.as_ref() {
                Node::Split { boundaries, .. } => Some(boundaries.clone()),
                _ => Some(Vec::new()),
            },
            _ => None,
        })
        .ok_or("segmentation winner did not contain a FILE root")?;
    let selected_providers = segmented
        .ledger
        .iter()
        .filter(|row| row.selected)
        .map(|row| row.descriptor.provider.clone())
        .collect();
    Ok(SegmentationMeasurement {
        baseline_archive_bytes: baseline,
        oracle_archive_bytes: oracle,
        headroom_bytes: baseline.saturating_sub(oracle),
        segment_count: segmented.coverage.segment_count,
        boundaries,
        selected_providers,
        quantum_bytes: quantum,
        work_used: segmented.usage.work,
        ledger_entries: segmented.usage.ledger_entries,
        budget_exhausted: segmented.usage.budget_exhausted,
        complete_within_declared_catalogue: segmented.usage.complete_within_declared_catalogue,
    })
}

fn residual_config(depth: u8) -> ResidualConfig {
    ResidualConfig {
        gate: ArplGate::Experimental,
        max_depth: depth,
        ..ResidualConfig::default()
    }
}

fn measure_residual(
    input: &[u8],
    limits: &Limits,
) -> Result<Vec<ResidualMeasurement>, Box<dyn Error>> {
    let mut rows = Vec::new();
    for depth in 0..=2 {
        let result = search_residual(input, &residual_config(depth), limits)?;
        let verified = complete_archive_bytes(&result.winner.program, input, limits)?;
        if verified as u64 != result.winner.cost.archive_bytes {
            return Err("native residual cost differs from verified archive bytes".into());
        }
        let winner_domains = result
            .winner
            .key
            .layers
            .iter()
            .map(|layer| format!("{:?}", layer.domain))
            .collect();
        let winner_projections = result
            .winner
            .key
            .layers
            .iter()
            .map(|layer| {
                format!(
                    "{:?}:{:?}",
                    layer.projection.family, layer.projection.parameters
                )
            })
            .collect();
        rows.push(ResidualMeasurement {
            depth_limit: depth,
            baseline_function_archive_bytes: result.function_archive_bytes,
            winner_archive_bytes: result.winner.cost.archive_bytes,
            headroom_bytes: result
                .function_archive_bytes
                .saturating_sub(result.winner.cost.archive_bytes),
            winner_residual_depth: result.winner.residual_depth,
            winner_domains,
            winner_projections,
            states_visited: result.states_visited,
            work_used: result.work_used,
            ledger_entries: result.ledger.len(),
            budget_exhausted: result.budget_exhausted,
            heuristic_omission: result.heuristic_omission,
            complete_within_declared_catalogue: !result.budget_exhausted
                && !result.heuristic_omission,
        });
    }
    Ok(rows)
}

fn measure_symbolic(input: &[u8], limits: &Limits) -> Result<SymbolicMeasurement, Box<dyn Error>> {
    let result = search_symbolic_block(input, &SymbolicConfig::default(), limits)?;
    let verified = complete_archive_bytes(&result.winner.program, input, limits)?;
    if verified as u64 != result.winner.cost.archive_bytes {
        return Err("native symbolic cost differs from verified archive bytes".into());
    }
    let expression = result.winner.expression_key.as_ref();
    Ok(SymbolicMeasurement {
        baseline_function_archive_bytes: result.base_function_archive_bytes,
        winner_archive_bytes: result.winner.cost.archive_bytes,
        headroom_bytes: result
            .base_function_archive_bytes
            .saturating_sub(result.winner.cost.archive_bytes),
        winner_expression_family: expression.map(|value| format!("{:?}", value.family)),
        winner_expression_depth: expression.map(|value| value.depth),
        winner_residual_kind: result
            .winner
            .residual_kind
            .map(|value| format!("{value:?}")),
        states_retained: result.states_retained,
        pairs_considered: result.pairs_considered,
        work_used: result.work_used,
        ledger_entries: result.ledger.len(),
        budget_exhausted: result.budget_exhausted,
        complete_within_declared_catalogue: result.complete_within_declared_catalogue,
    })
}

fn measure_dag(input: &[u8], limits: &Limits) -> Result<DagMeasurement, Box<dyn Error>> {
    let baseline = search_block(input, &search_config(), limits)?;
    let graph = search_shared_block(input, &GraphConfig::default(), limits)?;
    let baseline_verified = complete_archive_bytes(&baseline.winner.program, input, limits)?;
    let graph_verified = complete_archive_bytes(&graph.winner.program, input, limits)?;
    if baseline_verified as u64 != baseline.winner.cost.archive_bytes
        || graph_verified as u64 != graph.winner.cost.archive_bytes
    {
        return Err("native DAG comparison cost differs from verified archive bytes".into());
    }
    let portfolio_best = baseline
        .winner
        .cost
        .archive_bytes
        .min(graph.winner.cost.archive_bytes);
    Ok(DagMeasurement {
        baseline_function_archive_bytes: baseline.winner.cost.archive_bytes,
        graph_archive_bytes: graph.winner.cost.archive_bytes,
        portfolio_best_archive_bytes: portfolio_best,
        incremental_headroom_bytes: baseline
            .winner
            .cost
            .archive_bytes
            .saturating_sub(portfolio_best),
        active_definitions: graph.winner.active_definitions,
        referenced_source_bytes: graph.winner.referenced_source_bytes,
        mined_definitions: graph.mined_definitions,
        omitted_definitions: graph.omitted_definitions,
        evaluated_subsets: graph.evaluated_subsets,
        work_used: graph.work_used,
        ledger_entries: graph.ledger.len(),
        budget_exhausted: graph.budget_exhausted,
        complete_within_declared_catalogue: graph.complete_within_declared_catalogue,
    })
}

fn coordinate_function_config() -> SearchConfig {
    SearchConfig {
        max_period: 0,
        max_recurrence_order: 0,
        recurrence_coefficients: Vec::new(),
        max_exceptions: 0,
        work_budget: 1 << 20,
        max_ledger_entries: 256,
    }
}

fn complete_archive_bytes(
    program: &Program,
    restored: &[u8],
    limits: &Limits,
) -> Result<usize, Box<dyn Error>> {
    let archive = encode_archive(&[ArchiveBlock { program, restored }], limits)?;
    let decoded = decode_archive(&archive, limits)?;
    let mut output = Vec::new();
    decoded.verify_restored_with(
        |decoded_program| evaluate_program(decoded_program, limits),
        |_, block| {
            output.extend_from_slice(block);
            Ok(())
        },
    )?;
    if output != restored {
        return Err("native oracle archive did not restore exact input bytes".into());
    }
    Ok(archive.len())
}

fn measure_coordinate(
    input_id: String,
    origin: &'static str,
    source: &'static str,
    block_index: usize,
    input: &[u8],
    limits: &Limits,
) -> Result<CoordinateMeasurement, Box<dyn Error>> {
    let discovery = mathsvg_optimizer::CoordinateProvider::default().discovery;
    let discovery = mathsvg_coordinates_config(discovery);
    let functions = coordinate_function_config();
    let baseline = search_block(input, &functions, limits)?;
    let no_coordinate_archive_bytes = baseline.winner.cost.archive_bytes as usize;
    let context = ProviderContext {
        block_index: block_index as u64,
        global_start: (block_index * input.len()) as u64,
        global_end: ((block_index + 1) * input.len()) as u64,
        candidates_remaining: 64,
        work_remaining: 1 << 32,
    };

    let mut whole_provider = CoordinateProvider {
        discovery,
        functions: functions.clone(),
    };
    let whole = CandidateProvider::search(&mut whole_provider, input, context, limits)?;
    let whole_coordinate_archive_bytes = whole
        .candidates
        .iter()
        .map(|candidate| {
            let program = Program {
                definitions: Vec::new(),
                root: Node::File {
                    original_length: input.len() as u64,
                    child: Box::new(candidate.node.clone()),
                },
            };
            complete_archive_bytes(&program, input, limits)
        })
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .min();

    let mut basis_provider = CoordinateBasisProvider {
        discovery,
        functions,
    };
    let basis = WholeBlockCandidateProvider::search(&mut basis_provider, input, context, limits)?;
    let basis_archive_bytes = basis
        .candidates
        .iter()
        .map(|candidate| complete_archive_bytes(&candidate.program, input, limits))
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .min();
    let basis_headroom_vs_no_coordinate_bytes =
        basis_archive_bytes.map(|bytes| no_coordinate_archive_bytes as i64 - bytes as i64);
    let basis_incremental_headroom_bytes =
        match (whole_coordinate_archive_bytes, basis_archive_bytes) {
            (Some(whole_bytes), Some(basis_bytes)) => Some(whole_bytes as i64 - basis_bytes as i64),
            _ => None,
        };
    let basis_is_incremental_winner = matches!(
        (whole_coordinate_archive_bytes, basis_archive_bytes),
        (Some(whole_bytes), Some(basis_bytes))
            if basis_bytes < whole_bytes && basis_bytes < no_coordinate_archive_bytes
    );
    Ok(CoordinateMeasurement {
        input_id,
        origin,
        source,
        block_index,
        sample_bytes: input.len(),
        sample_sha256: sha256_bytes(input),
        no_coordinate_archive_bytes,
        whole_coordinate_archive_bytes,
        basis_archive_bytes,
        basis_headroom_vs_no_coordinate_bytes,
        basis_incremental_headroom_bytes,
        basis_is_incremental_winner,
        whole_complete_within_declared_catalogue: whole.complete_within_declared_catalogue,
        whole_budget_exhausted: whole.budget_exhausted,
        basis_complete_within_declared_catalogue: basis.complete_within_declared_catalogue,
        basis_budget_exhausted: basis.budget_exhausted,
    })
}

fn mathsvg_coordinates_config(
    mut config: mathsvg_coordinates::DiscoveryConfig,
) -> mathsvg_coordinates::DiscoveryConfig {
    config.max_candidates = 16;
    config.max_channels = 16;
    config.max_probe_lag = 0;
    config.max_peak_lags = 0;
    config.max_probe_bytes = 4096;
    config
}

fn coordinate_scan(
    repo_root: &Path,
    limits: &Limits,
) -> Result<Vec<CoordinateMeasurement>, Box<dyn Error>> {
    let datasets: &[(&str, &'static str, &'static str, usize)] = &[
        (
            "kennedy.xls",
            "datasets/data/canterbury/kennedy.xls",
            "real",
            32,
        ),
        ("progc", "datasets/data/calgary/progc", "real", 9),
        (
            "alice29.txt",
            "datasets/data/canterbury/alice29.txt",
            "real",
            32,
        ),
    ];
    let mut rows = Vec::new();
    for &(dataset, source, origin, count) in datasets {
        let bytes = fs::read(repo_root.join(source))?;
        for (block_index, block) in bytes.chunks_exact(SAMPLE_BYTES).take(count).enumerate() {
            if block_index % 8 == 0 {
                eprintln!("coordinate {dataset} block {block_index}/{count}");
            }
            rows.push(measure_coordinate(
                format!("{dataset}:block-{block_index:04}"),
                origin,
                source,
                block_index,
                block,
                limits,
            )?);
        }
    }
    Ok(rows)
}

fn main() -> Result<(), Box<dyn Error>> {
    let repo_root = env::args_os()
        .nth(1)
        .map(PathBuf::from)
        .ok_or("usage: mathsvg-native-oracle-probe REPO_ROOT")?;
    let repo_root = repo_root.canonicalize()?;
    let limits = Limits::default();
    let mut samples = Vec::new();
    for &spec in SAMPLES {
        eprintln!("sample {}", spec.input_id);
        let input = read_sample(&repo_root, spec)?;
        let sample_sha256 = sha256_bytes(&input);
        eprintln!("  search");
        let search = measure_search(&input, &limits)?;
        eprintln!("  segmentation");
        let segmentation = measure_segmentation(&input, &limits)?;
        eprintln!("  residual");
        let residual = measure_residual(&input, &limits)?;
        eprintln!("  symbolic");
        let symbolic = measure_symbolic(&input, &limits)?;
        eprintln!("  dag");
        let dag = measure_dag(&input, &limits)?;
        samples.push(SampleMeasurement {
            input_id: spec.input_id,
            origin: spec.origin,
            source: spec.relative_path,
            sample_bytes: input.len(),
            sample_sha256,
            search,
            segmentation,
            residual,
            symbolic,
            dag,
        });
    }
    let output = ProbeOutput {
        schema: SCHEMA,
        engine: "native-rust-complete-v1-archive",
        entropy_catalog:
            "raw,byte-rle,zero-run,sparse-zero,bit-pack,lz-tokens,canonical-huffman,lz-huffman",
        sample_bytes_cap: SAMPLE_BYTES,
        holdout_payload_inspected: false,
        samples,
        coordinate_basis_blocks: coordinate_scan(&repo_root, &limits)?,
    };
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frozen_probe_inputs_are_development_only() {
        assert_eq!(SAMPLES.len(), 13);
        for sample in SAMPLES {
            assert!(!sample.relative_path.contains("holdout"));
            assert!(!sample.relative_path.contains("sealed"));
            assert!(matches!(sample.origin, "real" | "synthetic" | "control"));
        }
    }

    #[test]
    fn segmentation_probe_uses_production_balanced_caps() {
        let config = segmentation_config(SAMPLE_BYTES, 256, 16);
        assert_eq!(config.max_states, 1 << 16);
        assert_eq!(config.max_candidates, 4096);
        assert_eq!(config.work_budget, 80_000_000);
        assert_eq!(config.max_ledger_entries, 1 << 14);
    }

    #[test]
    fn current_entropy_families_are_not_mislabeled_as_functions() {
        assert_eq!(
            family_name(CandidateFamily::EntropyLiteral),
            "entropy_literal"
        );
        assert_eq!(family_name(CandidateFamily::Literal), "literal");
        assert_eq!(family_name(CandidateFamily::LinearExact), "linear_exact");
    }

    #[test]
    fn input_digest_is_canonical_sha256() {
        assert_eq!(
            sha256_bytes(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
