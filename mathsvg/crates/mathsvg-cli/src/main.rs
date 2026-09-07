#![forbid(unsafe_code)]

use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom, Write};
use std::num::NonZeroUsize;
use std::path::{Path, PathBuf};
use std::str::FromStr;

use anyhow::{Context, Result, anyhow, bail};
use clap::{Args, Parser, Subcommand, ValueEnum};
use mathsvg_container::{
    ArchiveBlock, ArchiveStreamEncoder, CONTAINER_VERSION, DSL_VERSION, DecodedArchive,
    decode_archive,
};
use mathsvg_core::Limits;
use mathsvg_dsl::{Program, ProgramBreakdown};
use mathsvg_evaluator::{ActiveBackend, EvaluationBackend, evaluate_program_with_backend};
use mathsvg_optimizer::{PortfolioAlgorithm, PortfolioConfig, PortfolioProfile};
use mathsvg_stream::{
    MAX_COMPRESSION_THREADS, StreamCompressionReport, compress_reader_portfolio_threads,
    decompress_reader_to_spool_with_backend,
};
use serde_json::json;
use tempfile::{Builder, NamedTempFile, tempfile};

const DEFAULT_MAX_INPUT_BYTES: u64 = 512 * 1024 * 1024;
const DEFAULT_MAX_ARCHIVE_BYTES: u64 = 1024 * 1024 * 1024;
const DEFAULT_MAX_OUTPUT_BYTES: u64 = 512 * 1024 * 1024;
const COMPRESSION_ARCHIVE_HEADROOM_BYTES: u64 = 1024 * 1024;

#[derive(Debug, Parser)]
#[command(
    name = "mathsvg",
    version,
    about = "Strict deterministic native MathSVG compressor",
    long_about = None
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Compress a regular file into a self-contained native MSVG archive.
    #[command(visible_alias = "c")]
    Compress(CompressArgs),
    /// Strictly decode, length-check and SHA-256-verify an MSVG archive.
    #[command(visible_alias = "d")]
    Decompress(DecompressArgs),
    /// Inspect a structurally verified archive as deterministic JSON.
    Inspect(InspectArgs),
    /// Print the exact built-in runtime search contract as canonical JSON.
    Profile(ProfileArgs),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum ProfileArg {
    Fast,
    Balanced,
    Max,
    Structured,
    Repository,
}

impl ProfileArg {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Fast => "fast",
            Self::Balanced => "balanced",
            Self::Max => "max",
            Self::Structured => "structured",
            Self::Repository => "repository",
        }
    }

    const fn portfolio_profile(self) -> PortfolioProfile {
        match self {
            Self::Fast => PortfolioProfile::Fast,
            Self::Balanced => PortfolioProfile::Balanced,
            Self::Max => PortfolioProfile::Max,
            Self::Structured => PortfolioProfile::Structured,
            Self::Repository => PortfolioProfile::Repository,
        }
    }

    fn portfolio_config(self) -> PortfolioConfig {
        PortfolioConfig::for_profile(self.portfolio_profile())
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, ValueEnum)]
enum AblationArg {
    WholeBlockEntropy,
    WholeBlockFunctions,
    IntervalFunctions,
    Coordinates,
}

impl AblationArg {
    const fn portfolio_algorithm(self) -> PortfolioAlgorithm {
        match self {
            Self::WholeBlockEntropy => PortfolioAlgorithm::WholeBlockEntropy,
            Self::WholeBlockFunctions => PortfolioAlgorithm::WholeBlockFunctions,
            Self::IntervalFunctions => PortfolioAlgorithm::IntervalFunctions,
            Self::Coordinates => PortfolioAlgorithm::Coordinates,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum BackendArg {
    Scalar,
    Simd,
    Auto,
}

impl BackendArg {
    const fn evaluation_backend(self) -> EvaluationBackend {
        match self {
            Self::Scalar => EvaluationBackend::Scalar,
            Self::Simd => EvaluationBackend::Simd,
            Self::Auto => EvaluationBackend::Auto,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ThreadsArg {
    Count(NonZeroUsize),
    All,
}

impl ThreadsArg {
    fn resolve(self) -> Result<usize> {
        let threads = match self {
            Self::Count(value) => value.get(),
            Self::All => std::thread::available_parallelism()
                .context("could not determine available parallelism")?
                .get()
                .min(MAX_COMPRESSION_THREADS),
        };
        if threads > MAX_COMPRESSION_THREADS {
            bail!(
                "compression thread count {threads} exceeds the supported maximum \
                 {MAX_COMPRESSION_THREADS}"
            );
        }
        Ok(threads)
    }
}

impl FromStr for ThreadsArg {
    type Err = String;

    fn from_str(value: &str) -> std::result::Result<Self, Self::Err> {
        if value == "all" {
            return Ok(Self::All);
        }
        let parsed = value
            .parse::<usize>()
            .map_err(|_| "threads must be an integer from 1 through 64, or 'all'".to_owned())?;
        let count =
            NonZeroUsize::new(parsed).ok_or_else(|| "threads must be positive".to_owned())?;
        if count.get() > MAX_COMPRESSION_THREADS {
            return Err(format!("threads must not exceed {MAX_COMPRESSION_THREADS}"));
        }
        Ok(Self::Count(count))
    }
}

#[derive(Debug, Args)]
struct CompressArgs {
    /// Frozen deterministic search budget profile.
    #[arg(long, value_enum, default_value_t = ProfileArg::Balanced)]
    profile: ProfileArg,
    /// Deterministic block workers: 1..64 or all available logical CPUs.
    #[arg(long, default_value = "1", value_name = "N|all")]
    threads: ThreadsArg,
    /// Disable a real candidate engine for a controlled ablation; repeatable.
    #[arg(long, value_enum, value_name = "ALGORITHM")]
    disable: Vec<AblationArg>,
    /// Reject a larger source before allocating its contents.
    #[arg(
        long,
        default_value_t = DEFAULT_MAX_INPUT_BYTES,
        value_name = "BYTES"
    )]
    max_input_bytes: u64,
    /// Atomically replace an existing output file.
    #[arg(short, long)]
    force: bool,
    #[arg(value_name = "INPUT")]
    input: PathBuf,
    #[arg(value_name = "OUTPUT")]
    output: PathBuf,
}

#[derive(Debug, Args)]
struct DecompressArgs {
    /// Evaluator implementation; SIMD is bit-exact and archive-independent.
    #[arg(long, value_enum, default_value_t = BackendArg::Scalar)]
    backend: BackendArg,
    /// Reject a larger archive before allocating its contents.
    #[arg(
        long,
        default_value_t = DEFAULT_MAX_ARCHIVE_BYTES,
        value_name = "BYTES"
    )]
    max_archive_bytes: u64,
    /// Reject an archive declaring a larger restored stream.
    #[arg(
        long,
        default_value_t = DEFAULT_MAX_OUTPUT_BYTES,
        value_name = "BYTES"
    )]
    max_output_bytes: u64,
    /// Atomically replace an existing output file.
    #[arg(short, long)]
    force: bool,
    #[arg(value_name = "ARCHIVE")]
    archive: PathBuf,
    #[arg(value_name = "OUTPUT")]
    output: PathBuf,
}

#[derive(Debug, Args)]
struct InspectArgs {
    /// Evaluate every block and verify restored length and SHA-256 as well.
    #[arg(long)]
    verify: bool,
    /// Evaluator used by --verify.
    #[arg(long, value_enum, default_value_t = BackendArg::Scalar)]
    backend: BackendArg,
    /// Reject a larger archive before allocating its contents.
    #[arg(
        long,
        default_value_t = DEFAULT_MAX_ARCHIVE_BYTES,
        value_name = "BYTES"
    )]
    max_archive_bytes: u64,
    /// Reject an archive declaring a larger restored stream.
    #[arg(
        long,
        default_value_t = DEFAULT_MAX_OUTPUT_BYTES,
        value_name = "BYTES"
    )]
    max_output_bytes: u64,
    #[arg(value_name = "ARCHIVE")]
    archive: PathBuf,
}

#[derive(Debug, Args)]
struct ProfileArgs {
    /// Built-in runtime profile to describe.
    #[arg(long, value_enum, default_value_t = ProfileArg::Balanced)]
    profile: ProfileArg,
    /// Mirror a compression ablation in the runtime identity; repeatable.
    #[arg(long, value_enum, value_name = "ALGORITHM")]
    disable: Vec<AblationArg>,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("mathsvg: {error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    match Cli::parse().command {
        Command::Compress(args) => compress_command(args),
        Command::Decompress(args) => decompress_command(args),
        Command::Inspect(args) => inspect_command(args),
        Command::Profile(args) => profile_command(args),
    }
}

fn compress_command(args: CompressArgs) -> Result<()> {
    ensure_distinct_file_paths(&args.input, &args.output)?;
    validate_output_target(&args.output, args.force)?;
    let (mut input, _) = open_regular_file_bounded(&args.input, args.max_input_bytes)?;
    let mut limits = Limits {
        // BIT_PLANE pads its transformed domain to the next whole byte in
        // each of eight planes. The restored source remains bounded by
        // max_input_bytes; only the internal exact candidate may need +7.
        max_output_bytes: args.max_input_bytes.saturating_add(7),
        ..Limits::default()
    };
    // Keep the literal upper bound representable even when callers raise the
    // input limit beyond the default archive-read policy.
    limits.max_archive_bytes = args
        .max_input_bytes
        .saturating_mul(2)
        .saturating_add(COMPRESSION_ARCHIVE_HEADROOM_BYTES);

    let payload_spool = tempfile().context("could not create compression payload spool")?;
    let mut temporary = create_atomic_temporary(&args.output)?;
    let threads = args.threads.resolve()?;
    let (portfolio, disabled_algorithms) = portfolio_with_ablation(args.profile, &args.disable);
    let report = compress_reader_portfolio_threads(
        &mut input,
        payload_spool,
        &mut temporary,
        &portfolio,
        &limits,
        threads,
    )
    .context("native MathSVG streaming optimization failed")?;
    persist_atomic(temporary, &args.output, args.force)?;
    eprintln!(
        "mathsvg: compressed input_bytes={} archive_bytes={} blocks={} profile={} ablation={} \
         selection={} threads={} maximum_inflight_input_bytes={} original_sha256={} streamed=true",
        report.envelope.original_size,
        report.envelope.archive_bytes,
        report.envelope.block_count,
        args.profile.as_str(),
        ablation_identity(&disabled_algorithms),
        stream_selection_name(&report),
        report.threads,
        report.maximum_inflight_input_bytes,
        hex::encode(report.envelope.original_sha256),
    );
    Ok(())
}

fn decompress_command(args: DecompressArgs) -> Result<()> {
    ensure_distinct_file_paths(&args.archive, &args.output)?;
    validate_output_target(&args.output, args.force)?;
    let limits = decode_limits(args.max_archive_bytes, args.max_output_bytes);
    let (mut archive, _) = open_regular_file_bounded(&args.archive, args.max_archive_bytes)?;
    let mut temporary = create_atomic_temporary(&args.output)?;
    let (report, active_backend) = decompress_reader_to_spool_with_backend(
        &mut archive,
        &mut temporary,
        &limits,
        args.backend.evaluation_backend(),
    )
    .context("archive streaming restored-byte verification failed")?;
    persist_atomic(temporary, &args.output, args.force)?;
    eprintln!(
        "mathsvg: restored output_bytes={} blocks={} backend={} original_sha256={} \
         verified=true streamed=true",
        report.verified.original_size,
        report.verified.block_count,
        active_backend.name(),
        hex::encode(report.verified.original_sha256),
    );
    Ok(())
}

fn inspect_command(args: InspectArgs) -> Result<()> {
    let limits = decode_limits(args.max_archive_bytes, args.max_output_bytes);
    let (mut archive_file, archive_length) =
        open_regular_file_bounded(&args.archive, args.max_archive_bytes)?;
    let active_backend = if args.verify {
        let (_, active_backend) = decompress_reader_to_spool_with_backend(
            &mut archive_file,
            &mut std::io::sink(),
            &limits,
            args.backend.evaluation_backend(),
        )
        .context("archive streaming restored-byte verification failed")?;
        archive_file
            .seek(SeekFrom::Start(0))
            .with_context(|| format!("could not rewind {}", args.archive.display()))?;
        Some(active_backend)
    } else {
        None
    };
    let archive = read_open_file_bounded(
        &mut archive_file,
        &args.archive,
        archive_length,
        args.max_archive_bytes,
    )?;
    let decoded =
        decode_archive(&archive, &limits).context("archive structural verification failed")?;
    let breakdown = archive_procedural_breakdown(
        &decoded,
        archive.len(),
        &limits,
        active_backend.map(|_| args.backend.evaluation_backend()),
    )?;
    let rendered = inspection_json(&decoded, archive.len(), active_backend, &breakdown);
    println!(
        "{}",
        serde_json::to_string_pretty(&rendered)
            .context("could not serialize archive inspection")?
    );
    Ok(())
}

fn profile_command(args: ProfileArgs) -> Result<()> {
    println!(
        "{}",
        serde_json::to_string_pretty(&runtime_profile_json(args.profile, &args.disable))
            .context("could not serialize runtime profile")?
    );
    Ok(())
}

fn canonical_disabled_algorithms(disable: &[AblationArg]) -> Vec<PortfolioAlgorithm> {
    let mut algorithms: Vec<_> = disable
        .iter()
        .map(|value| value.portfolio_algorithm())
        .collect();
    algorithms.sort_unstable();
    algorithms.dedup();
    algorithms
}

fn portfolio_with_ablation(
    profile: ProfileArg,
    disable: &[AblationArg],
) -> (PortfolioConfig, Vec<PortfolioAlgorithm>) {
    let algorithms = canonical_disabled_algorithms(disable);
    let mut portfolio = profile.portfolio_config();
    for &algorithm in &algorithms {
        portfolio.set_algorithm_enabled(algorithm, false);
    }
    (portfolio, algorithms)
}

fn ablation_identity(disabled: &[PortfolioAlgorithm]) -> String {
    if disabled.is_empty() {
        return "none".to_owned();
    }
    let mut identity = "without-".to_owned();
    for (index, algorithm) in disabled.iter().enumerate() {
        if index != 0 {
            identity.push_str("-and-");
        }
        identity.push_str(algorithm.as_str());
    }
    identity
}

fn runtime_profile_json(profile: ProfileArg, disable: &[AblationArg]) -> serde_json::Value {
    let (portfolio, disabled_algorithms) = portfolio_with_ablation(profile, disable);
    let optimizer = &portfolio.optimizer;
    let functions = &optimizer.functions;
    let coordinates = &portfolio.coordinates;
    let residual = &portfolio.residual.config;
    let limits = Limits::default();
    let (
        entropy_add_only_policy,
        entropy_chain_depth,
        entropy_lazy,
        entropy_scratch,
        entropy_work,
        entropy_walks,
    ) = match portfolio.entropy.lz_huffman_policy {
        None => ("none", 1, false, 262_144, 0, 0),
        Some(policy) => (
            policy.as_str(),
            policy.chain_depth(),
            policy.lazy(),
            policy.scratch_bytes(),
            policy.maximum_work_per_input_byte(),
            2,
        ),
    };
    json!({
        "schema_version": 1,
        "profile": profile.as_str(),
        "ablation": {
            "id": ablation_identity(&disabled_algorithms),
            "disabled_algorithms": disabled_algorithms
                .iter()
                .map(|algorithm| algorithm.as_str())
                .collect::<Vec<_>>(),
        },
        "container_version": CONTAINER_VERSION,
        "dsl_version": DSL_VERSION,
        "optimizer": {
            "block_bytes": optimizer.block_bytes,
            "microblock_bytes": optimizer.microblock_bytes,
            "max_segments_per_block": optimizer.max_segments_per_block,
            "max_states": optimizer.max_states,
            "max_candidates": optimizer.max_candidates,
            "work_budget": optimizer.work_budget,
            "max_ledger_entries": optimizer.max_ledger_entries,
        },
        "function_search": {
            "max_period": functions.max_period,
            "max_recurrence_order": functions.max_recurrence_order,
            "recurrence_coefficients": functions.recurrence_coefficients,
            "max_exceptions": functions.max_exceptions,
            "work_budget": functions.work_budget,
            "max_ledger_entries": functions.max_ledger_entries,
        },
        "entropy_search": {
            "baseline_policy": "G1",
            "add_only_policy": entropy_add_only_policy,
            "chain_depth": entropy_chain_depth,
            "one_byte_lazy": entropy_lazy,
            "parser_scratch_bytes": entropy_scratch,
            "maximum_additional_work_per_input_byte_per_walk": entropy_work,
            "maximum_policy_walks": entropy_walks,
            "wire_opcode": 7,
            "decoder_semantics_changed": false,
        },
        "coordinate_search": {
            "enabled": portfolio.enable_coordinates,
            "max_candidates": coordinates.discovery.max_candidates,
            "max_channels": coordinates.discovery.max_channels,
            "max_probe_lag": coordinates.discovery.max_probe_lag,
            "max_peak_lags": coordinates.discovery.max_peak_lags,
            "max_probe_bytes": coordinates.discovery.max_probe_bytes,
            "function_max_period": coordinates.functions.max_period,
            "function_max_exceptions": coordinates.functions.max_exceptions,
            "function_work_budget": coordinates.functions.work_budget,
            "function_max_ledger_entries": coordinates.functions.max_ledger_entries,
        },
        "residual_search": {
            "enabled": portfolio.enable_residual,
            "gate": residual.gate.as_str(),
            "max_depth": residual.max_depth,
            "domains": residual.domains.iter().map(|domain| domain.as_str()).collect::<Vec<_>>(),
            "max_projection_period": residual.max_projection_period,
            "max_recurrence_order": residual.max_recurrence_order,
            "recurrence_coefficients": residual.recurrence_coefficients,
            "max_projection_descriptors": residual.max_projection_descriptors,
            "max_states": residual.max_states,
            "max_state_bytes": residual.max_state_bytes,
            "work_budget": residual.work_budget,
            "max_ledger_entries": residual.max_ledger_entries,
            "minimum_gain_bytes": residual.minimum_gain_bytes,
        },
        "emission_gates": {
            "whole_block_entropy": portfolio.enable_whole_block_entropy,
            "whole_block_functions": portfolio.enable_whole_block_functions,
            "interval_functions": portfolio.enable_interval_functions,
            "coordinates": portfolio.enable_coordinates,
            "residual": portfolio.enable_residual,
            "symbolic": portfolio.enable_symbolic,
            "graph": portfolio.enable_graph,
        },
        "implemented_catalogue": {
            "entropy": [
                "raw", "byte_rle", "zero_run", "sparse_zero", "bit_pack", "lz_tokens",
                "canonical_huffman", "lz_huffman"
            ],
            "functions": [
                "literal", "entropy_literal", "const", "linear", "periodic",
                "recurrence", "exceptions"
            ],
            "coordinates": ["identity", "stride", "byte_plane", "bit_plane"],
            "experimental": ["recursive_residual", "shared_dag", "symbolic_synthesis"],
        },
        "format_limits": {
            "max_block_payload_bytes": limits.max_block_payload_bytes,
            "max_block_output_bytes": limits.max_block_output_bytes,
            "max_definitions": limits.max_definitions,
            "max_nodes": limits.max_nodes,
            "max_edges": limits.max_edges,
            "max_graph_depth": limits.max_graph_depth,
            "max_fan_out": limits.max_fan_out,
            "max_node_payload_bytes": limits.max_node_payload_bytes,
            "max_parameter_bytes": limits.max_parameter_bytes,
            "max_pattern_bytes": limits.max_pattern_bytes,
            "max_recurrence_order": limits.max_recurrence_order,
            "max_temporary_bytes": limits.max_temporary_bytes,
            "max_work": limits.max_work,
        },
        "parallel": {
            "default_threads": 1,
            "maximum_threads": MAX_COMPRESSION_THREADS,
            "deterministic_source_order_merge": true,
        },
        "backend_policy": {
            "compression_search": "scalar",
            "decoder_default": "scalar",
            "decoder_available": ["scalar", "simd", "auto"],
        },
    })
}

fn inspection_json(
    decoded: &DecodedArchive,
    archive_bytes: usize,
    active_backend: Option<ActiveBackend>,
    breakdown: &ArchiveProceduralBreakdown,
) -> serde_json::Value {
    let blocks = decoded
        .blocks
        .iter()
        .enumerate()
        .map(|(index, block)| {
            let metadata = &block.metadata;
            json!({
                "index": index,
                "original_offset": metadata.original_offset,
                "original_bytes": metadata.original_length,
                "payload_offset": metadata.payload_offset,
                "payload_bytes": metadata.payload_length,
                "definitions": metadata.definition_count,
                "nodes": metadata.node_count,
                "edges": metadata.edge_count,
                "graph_depth": metadata.max_graph_depth,
                "work_units": metadata.work_units,
                "workspace_bytes": metadata.workspace_bytes,
                "restored_sha256": hex::encode(metadata.restored_sha256),
                "payload_sha256": hex::encode(metadata.payload_sha256),
                "payload_crc32": format!("{:08x}", metadata.payload_crc32),
            })
        })
        .collect::<Vec<_>>();
    json!({
        "container_version": CONTAINER_VERSION,
        "dsl_version": DSL_VERSION,
        "archive_bytes": archive_bytes,
        "original_bytes": decoded.original_size,
        "block_count": decoded.blocks.len(),
        "total_nodes": decoded.total_node_count,
        "total_work_units": decoded.total_work_units,
        "original_sha256": hex::encode(decoded.original_sha256),
        "restored_verified": active_backend.is_some(),
        "evaluation_backend": active_backend.map(ActiveBackend::name),
        "procedural_breakdown": {
            "container_overhead_bytes": breakdown.container_overhead_bytes,
            "function_graph_bytes": breakdown.program.function_graph_bytes,
            "coordinate_bytes": breakdown.program.coordinate_bytes,
            "shared_definition_bytes": breakdown.program.shared_definition_bytes,
            "reference_bytes": breakdown.program.reference_bytes,
            "parameter_bytes": breakdown.program.parameter_bytes,
            "residual_layer_bytes": breakdown.program.residual_layer_bytes,
            "literal_leaf_bytes": breakdown.program.literal_leaf_bytes,
            "entropy_metadata_bytes": breakdown.program.entropy_metadata_bytes,
            "node_count": breakdown.program.node_count,
            "shared_node_count": breakdown.program.shared_node_count,
            "residual_depth_sum": breakdown.program.residual_depth_sum,
            "residual_root_count": breakdown.program.residual_root_count,
            "function_reconstructed_bytes": breakdown.program.function_reconstructed_bytes,
            "literal_reconstructed_bytes": breakdown.program.literal_reconstructed_bytes,
            "literal_only_archive_bytes": breakdown.literal_only_archive_bytes,
            "pre_entropy_archive_bytes": breakdown.pre_entropy_archive_bytes,
            "entropy_saved_bytes": breakdown.entropy_saved_bytes,
            "entropy_penalty_bytes": breakdown.entropy_penalty_bytes,
            "procedural_gain_bytes": breakdown.procedural_gain_bytes,
            "procedural_penalty_bytes": breakdown.procedural_penalty_bytes,
            "coordinate_saved_bytes": serde_json::Value::Null,
            "dag_saved_bytes": serde_json::Value::Null,
            "recursive_residual_saved_bytes": serde_json::Value::Null,
            "symbolic_saved_bytes": serde_json::Value::Null,
        },
        "blocks": blocks,
    })
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct ArchiveProceduralBreakdown {
    container_overhead_bytes: u64,
    program: ProgramBreakdown,
    literal_only_archive_bytes: Option<u64>,
    pre_entropy_archive_bytes: Option<u64>,
    entropy_saved_bytes: Option<u64>,
    entropy_penalty_bytes: Option<u64>,
    procedural_gain_bytes: Option<u64>,
    procedural_penalty_bytes: Option<u64>,
}

fn archive_procedural_breakdown(
    decoded: &DecodedArchive,
    archive_bytes: usize,
    limits: &Limits,
    counterfactual_backend: Option<EvaluationBackend>,
) -> Result<ArchiveProceduralBreakdown> {
    let mut program = ProgramBreakdown::default();
    for block in &decoded.blocks {
        add_program_breakdown(
            &mut program,
            &block
                .program
                .procedural_breakdown(limits)
                .context("could not partition canonical block program")?,
        )?;
    }
    if program.node_count != decoded.total_node_count {
        bail!(
            "procedural node accounting {} differs from container metadata {}",
            program.node_count,
            decoded.total_node_count
        );
    }
    let dsl_bytes = program
        .wire_bytes()
        .context("procedural wire accounting overflowed")?;
    let archive_bytes =
        u64::try_from(archive_bytes).context("archive size is not representable as u64")?;
    let container_overhead_bytes = archive_bytes
        .checked_sub(dsl_bytes)
        .ok_or_else(|| anyhow!("canonical DSL accounting exceeds archive length"))?;
    if checked_archive_partition(container_overhead_bytes, &program)? != archive_bytes {
        bail!("procedural wire categories do not partition the complete archive");
    }
    if program
        .function_reconstructed_bytes
        .checked_add(program.literal_reconstructed_bytes)
        .ok_or_else(|| anyhow!("reconstructed-byte accounting overflowed"))?
        != decoded.original_size
    {
        bail!("procedural reconstructed-byte accounting differs from original size");
    }

    let mut result = ArchiveProceduralBreakdown {
        container_overhead_bytes,
        program,
        ..ArchiveProceduralBreakdown::default()
    };
    if let Some(backend) = counterfactual_backend {
        let (literal_only, pre_entropy) = counterfactual_archive_sizes(decoded, limits, backend)?;
        result.literal_only_archive_bytes = Some(literal_only);
        result.pre_entropy_archive_bytes = Some(pre_entropy);
        result.entropy_saved_bytes = Some(pre_entropy.saturating_sub(archive_bytes));
        result.entropy_penalty_bytes = Some(archive_bytes.saturating_sub(pre_entropy));
        result.procedural_gain_bytes = Some(literal_only.saturating_sub(archive_bytes));
        result.procedural_penalty_bytes = Some(archive_bytes.saturating_sub(literal_only));
    }
    Ok(result)
}

fn add_breakdown_value(target: &mut u64, value: u64, context: &'static str) -> Result<()> {
    *target = target
        .checked_add(value)
        .ok_or_else(|| anyhow!("{context} overflowed"))?;
    Ok(())
}

fn add_program_breakdown(total: &mut ProgramBreakdown, block: &ProgramBreakdown) -> Result<()> {
    add_breakdown_value(
        &mut total.function_graph_bytes,
        block.function_graph_bytes,
        "function graph bytes",
    )?;
    add_breakdown_value(
        &mut total.coordinate_bytes,
        block.coordinate_bytes,
        "coordinate bytes",
    )?;
    add_breakdown_value(
        &mut total.shared_definition_bytes,
        block.shared_definition_bytes,
        "shared definition bytes",
    )?;
    add_breakdown_value(
        &mut total.reference_bytes,
        block.reference_bytes,
        "reference bytes",
    )?;
    add_breakdown_value(
        &mut total.parameter_bytes,
        block.parameter_bytes,
        "parameter bytes",
    )?;
    add_breakdown_value(
        &mut total.residual_layer_bytes,
        block.residual_layer_bytes,
        "residual layer bytes",
    )?;
    add_breakdown_value(
        &mut total.literal_leaf_bytes,
        block.literal_leaf_bytes,
        "literal leaf bytes",
    )?;
    add_breakdown_value(
        &mut total.entropy_metadata_bytes,
        block.entropy_metadata_bytes,
        "entropy metadata bytes",
    )?;
    add_breakdown_value(&mut total.node_count, block.node_count, "node count")?;
    add_breakdown_value(
        &mut total.shared_node_count,
        block.shared_node_count,
        "shared node count",
    )?;
    add_breakdown_value(
        &mut total.residual_depth_sum,
        block.residual_depth_sum,
        "residual depth sum",
    )?;
    add_breakdown_value(
        &mut total.residual_root_count,
        block.residual_root_count,
        "residual root count",
    )?;
    add_breakdown_value(
        &mut total.function_reconstructed_bytes,
        block.function_reconstructed_bytes,
        "function reconstructed bytes",
    )?;
    add_breakdown_value(
        &mut total.literal_reconstructed_bytes,
        block.literal_reconstructed_bytes,
        "literal reconstructed bytes",
    )
}

fn checked_archive_partition(
    container_overhead_bytes: u64,
    program: &ProgramBreakdown,
) -> Result<u64> {
    container_overhead_bytes
        .checked_add(
            program
                .wire_bytes()
                .context("program wire partition overflowed")?,
        )
        .ok_or_else(|| anyhow!("complete archive wire partition overflowed"))
}

fn counterfactual_archive_sizes(
    decoded: &DecodedArchive,
    limits: &Limits,
    backend: EvaluationBackend,
) -> Result<(u64, u64)> {
    let literal_spool = tempfile().context("could not create literal counterfactual spool")?;
    let pre_entropy_spool =
        tempfile().context("could not create pre-entropy counterfactual spool")?;
    let mut literal_encoder = ArchiveStreamEncoder::new(literal_spool, limits)
        .context("could not initialize literal counterfactual encoder")?;
    let mut pre_entropy_encoder = ArchiveStreamEncoder::new(pre_entropy_spool, limits)
        .context("could not initialize pre-entropy counterfactual encoder")?;

    for block in &decoded.blocks {
        let restored = evaluate_program_with_backend(&block.program, limits, backend)
            .context("could not evaluate counterfactual source block")?
            .bytes;
        let literal = Program::literal(restored.clone());
        let pre_entropy = block
            .program
            .without_entropy_literals(limits)
            .context("could not construct pre-entropy counterfactual")?;
        literal_encoder
            .push_block(ArchiveBlock {
                program: &literal,
                restored: &restored,
            })
            .context("could not encode literal counterfactual block")?;
        pre_entropy_encoder
            .push_block(ArchiveBlock {
                program: &pre_entropy,
                restored: &restored,
            })
            .context("could not encode pre-entropy counterfactual block")?;
    }
    let literal = literal_encoder
        .finish(&mut std::io::sink())
        .context("could not finish literal counterfactual archive")?;
    let pre_entropy = pre_entropy_encoder
        .finish(&mut std::io::sink())
        .context("could not finish pre-entropy counterfactual archive")?;
    Ok((literal.archive_bytes, pre_entropy.archive_bytes))
}

fn decode_limits(max_archive_bytes: u64, max_output_bytes: u64) -> Limits {
    Limits {
        max_archive_bytes,
        max_output_bytes,
        ..Limits::default()
    }
}

fn stream_selection_name(report: &StreamCompressionReport) -> &'static str {
    match (report.procedural_blocks, report.literal_fallback_blocks) {
        (0, _) => "literal-fallback",
        (_, 0) => "native-optimized",
        _ => "mixed-native",
    }
}

fn open_regular_file_bounded(path: &Path, limit: u64) -> Result<(File, u64)> {
    let file = File::open(path).with_context(|| format!("could not open {}", path.display()))?;
    let metadata = file
        .metadata()
        .with_context(|| format!("could not stat {}", path.display()))?;
    if !metadata.is_file() {
        bail!("{} is not a regular file", path.display());
    }
    if metadata.len() > limit {
        bail!(
            "{} is {} bytes, exceeding the configured {}-byte limit",
            path.display(),
            metadata.len(),
            limit
        );
    }
    Ok((file, metadata.len()))
}

fn read_open_file_bounded(
    file: &mut File,
    path: &Path,
    known_length: u64,
    limit: u64,
) -> Result<Vec<u8>> {
    let capacity =
        usize::try_from(known_length).context("input size is not addressable on this platform")?;
    let mut bytes = Vec::with_capacity(capacity);
    file.take(limit.saturating_add(1))
        .read_to_end(&mut bytes)
        .with_context(|| format!("could not read {}", path.display()))?;
    if bytes.len() as u64 > limit {
        bail!("{} grew beyond the configured byte limit", path.display());
    }
    Ok(bytes)
}

fn validate_output_target(path: &Path, force: bool) -> Result<()> {
    let parent = output_parent(path)?;
    if !parent.is_dir() {
        bail!("output directory {} does not exist", parent.display());
    }
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_dir() {
                bail!("output path {} is a directory", path.display());
            }
            if !force {
                bail!(
                    "{} already exists; pass --force to replace it atomically",
                    path.display()
                );
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => {
            return Err(error)
                .with_context(|| format!("could not inspect output {}", path.display()));
        }
    }
    Ok(())
}

fn create_atomic_temporary(path: &Path) -> Result<NamedTempFile> {
    let parent = output_parent(path)?;
    Builder::new()
        .prefix(".mathsvg-")
        .suffix(".tmp")
        .tempfile_in(parent)
        .with_context(|| format!("could not create atomic output beside {}", path.display()))
}

fn persist_atomic(mut temporary: NamedTempFile, path: &Path, force: bool) -> Result<()> {
    temporary
        .flush()
        .with_context(|| format!("could not flush atomic output for {}", path.display()))?;
    temporary
        .as_file()
        .sync_all()
        .with_context(|| format!("could not sync atomic output for {}", path.display()))?;
    if force {
        temporary
            .persist(path)
            .map_err(|error| error.error)
            .with_context(|| format!("could not atomically replace {}", path.display()))?;
    } else {
        temporary
            .persist_noclobber(path)
            .map_err(|error| error.error)
            .with_context(|| format!("could not atomically create {}", path.display()))?;
    }
    Ok(())
}

fn ensure_distinct_file_paths(input: &Path, output: &Path) -> Result<()> {
    if absolute_destination(input)? == absolute_destination(output)? {
        bail!("input and output resolve to the same path");
    }
    if input.exists() && output.exists() {
        let input_metadata = fs::metadata(input)?;
        let output_metadata = fs::metadata(output)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if input_metadata.dev() == output_metadata.dev()
                && input_metadata.ino() == output_metadata.ino()
            {
                bail!("input and output refer to the same file");
            }
        }
    }
    Ok(())
}

fn absolute_destination(path: &Path) -> Result<PathBuf> {
    if path.exists() {
        return fs::canonicalize(path)
            .with_context(|| format!("could not resolve {}", path.display()));
    }
    let parent = fs::canonicalize(output_parent(path)?)
        .with_context(|| format!("could not resolve output directory for {}", path.display()))?;
    let name = path
        .file_name()
        .ok_or_else(|| anyhow!("{} has no file name", path.display()))?;
    Ok(parent.join(name))
}

fn output_parent(path: &Path) -> Result<&Path> {
    if path.as_os_str().is_empty() {
        bail!("output path is empty");
    }
    Ok(path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or(Path::new(".")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profiles_have_stable_strictly_increasing_search_budgets() {
        let fast = ProfileArg::Fast.portfolio_config();
        let balanced = ProfileArg::Balanced.portfolio_config();
        let max = ProfileArg::Max.portfolio_config();
        assert!(fast.optimizer.max_candidates < balanced.optimizer.max_candidates);
        assert!(balanced.optimizer.max_candidates < max.optimizer.max_candidates);
        assert!(fast.optimizer.work_budget < balanced.optimizer.work_budget);
        assert!(balanced.optimizer.work_budget < max.optimizer.work_budget);
        assert!(fast.enable_coordinates);
        assert!(!balanced.enable_coordinates);
        assert!(!max.enable_coordinates);
        assert!(!balanced.enable_interval_functions);
        assert!(!max.enable_interval_functions);
        assert!(!balanced.enable_residual);
        assert!(!ProfileArg::Structured.portfolio_config().enable_residual);
        assert!(!balanced.enable_symbolic);
        assert!(!balanced.enable_graph);
        assert_eq!(
            ProfileArg::Structured.portfolio_profile(),
            PortfolioProfile::Structured
        );
        assert_eq!(
            ProfileArg::Repository.portfolio_profile(),
            PortfolioProfile::Repository
        );
    }

    #[test]
    fn command_line_defaults_to_balanced_and_parses_force() {
        let cli =
            Cli::try_parse_from(["mathsvg", "compress", "--force", "input.bin", "output.msvg"])
                .unwrap();
        let Command::Compress(args) = cli.command else {
            panic!("compress command was not parsed");
        };
        assert_eq!(args.profile, ProfileArg::Balanced);
        assert_eq!(
            args.threads,
            ThreadsArg::Count(NonZeroUsize::new(1).unwrap())
        );
        assert!(args.disable.is_empty());
        assert!(args.force);
    }

    #[test]
    fn thread_and_backend_arguments_are_strict() {
        for value in ["1", "2", "64", "all"] {
            assert!(value.parse::<ThreadsArg>().is_ok(), "{value}");
        }
        for value in ["0", "65", "-1", "auto", "1.5"] {
            assert!(value.parse::<ThreadsArg>().is_err(), "{value}");
        }
        let cli = Cli::try_parse_from([
            "mathsvg",
            "decompress",
            "--backend",
            "simd",
            "archive.msvg",
            "output.bin",
        ])
        .unwrap();
        let Command::Decompress(args) = cli.command else {
            panic!("decompress command was not parsed");
        };
        assert_eq!(args.backend, BackendArg::Simd);
    }

    #[test]
    fn runtime_profile_json_exposes_the_actual_search_and_gate_contract() {
        let value = runtime_profile_json(ProfileArg::Fast, &[]);
        let config = ProfileArg::Fast.portfolio_config();
        assert_eq!(value["profile"], "fast");
        assert_eq!(
            value["optimizer"]["block_bytes"],
            config.optimizer.block_bytes
        );
        assert_eq!(
            value["optimizer"]["work_budget"],
            config.optimizer.work_budget
        );
        assert_eq!(
            value["function_search"]["work_budget"],
            config.optimizer.functions.work_budget
        );
        assert_eq!(
            value["coordinate_search"]["max_candidates"],
            config.coordinates.discovery.max_candidates
        );
        assert_eq!(value["emission_gates"]["residual"], config.enable_residual);
        assert_eq!(value["residual_search"]["enabled"], false);
        assert_eq!(value["residual_search"]["gate"], "oracle-disabled");
        assert_eq!(value["residual_search"]["max_depth"], 0);
        assert_eq!(
            value["emission_gates"]["whole_block_entropy"],
            config.enable_whole_block_entropy
        );
        assert_eq!(
            value["emission_gates"]["whole_block_functions"],
            config.enable_whole_block_functions
        );
        assert_eq!(
            value["emission_gates"]["interval_functions"],
            config.enable_interval_functions
        );
        assert_eq!(value["ablation"]["id"], "none");
        assert_eq!(
            value["ablation"]["disabled_algorithms"],
            serde_json::json!([])
        );
        assert_eq!(
            value["parallel"]["maximum_threads"],
            MAX_COMPRESSION_THREADS
        );
        assert_eq!(value["entropy_search"]["baseline_policy"], "G1");
        assert_eq!(value["entropy_search"]["add_only_policy"], "none");
        assert_eq!(value["entropy_search"]["chain_depth"], 1);
        assert_eq!(value["entropy_search"]["one_byte_lazy"], false);
        assert_eq!(value["entropy_search"]["parser_scratch_bytes"], 262_144);
        assert_eq!(
            value["entropy_search"]["maximum_additional_work_per_input_byte_per_walk"],
            0
        );
        assert_eq!(value["entropy_search"]["maximum_policy_walks"], 0);

        let balanced = runtime_profile_json(ProfileArg::Balanced, &[]);
        assert_eq!(balanced["entropy_search"]["baseline_policy"], "G1");
        assert_eq!(balanced["entropy_search"]["add_only_policy"], "C8L");
        assert_eq!(balanced["entropy_search"]["chain_depth"], 8);
        assert_eq!(balanced["entropy_search"]["one_byte_lazy"], true);
        assert_eq!(balanced["entropy_search"]["parser_scratch_bytes"], 524_288);
        assert_eq!(
            balanced["entropy_search"]["maximum_additional_work_per_input_byte_per_walk"],
            25
        );
        assert_eq!(balanced["entropy_search"]["maximum_policy_walks"], 2);
        assert_eq!(
            balanced["entropy_search"]["decoder_semantics_changed"],
            false
        );
    }

    #[test]
    fn ablation_arguments_are_canonical_and_change_real_runtime_gates() {
        let cli = Cli::try_parse_from([
            "mathsvg",
            "compress",
            "--disable",
            "coordinates",
            "--disable",
            "whole-block-entropy",
            "--disable",
            "interval-functions",
            "--disable",
            "coordinates",
            "input.bin",
            "output.msvg",
        ])
        .unwrap();
        let Command::Compress(args) = cli.command else {
            panic!("compress command was not parsed");
        };
        let (portfolio, disabled) = portfolio_with_ablation(args.profile, &args.disable);
        assert_eq!(
            disabled,
            vec![
                PortfolioAlgorithm::WholeBlockEntropy,
                PortfolioAlgorithm::IntervalFunctions,
                PortfolioAlgorithm::Coordinates,
            ]
        );
        assert!(!portfolio.enable_whole_block_entropy);
        assert!(portfolio.enable_whole_block_functions);
        assert!(!portfolio.enable_interval_functions);
        assert!(!portfolio.enable_coordinates);

        let value = runtime_profile_json(args.profile, &args.disable);
        assert_eq!(
            value["ablation"]["id"],
            "without-whole-block-entropy-and-interval-functions-and-coordinates"
        );
        assert_eq!(
            value["ablation"]["disabled_algorithms"],
            serde_json::json!(["whole-block-entropy", "interval-functions", "coordinates"])
        );
        assert_eq!(value["emission_gates"]["whole_block_entropy"], false);
        assert_eq!(value["emission_gates"]["whole_block_functions"], true);
        assert_eq!(value["emission_gates"]["interval_functions"], false);
        assert_eq!(value["emission_gates"]["coordinates"], false);

        let structured = runtime_profile_json(ProfileArg::Structured, &[]);
        assert_eq!(structured["emission_gates"]["residual"], false);
        assert_eq!(structured["residual_search"]["enabled"], false);
        assert_eq!(structured["residual_search"]["gate"], "oracle-disabled");
        assert_eq!(structured["residual_search"]["max_depth"], 0);
    }

    #[test]
    fn inspection_json_is_stable_for_empty_metadata() {
        let decoded = DecodedArchive {
            original_size: 0,
            original_sha256: [
                0xe3, 0xb0, 0xc4, 0x42, 0x98, 0xfc, 0x1c, 0x14, 0x9a, 0xfb, 0xf4, 0xc8, 0x99, 0x6f,
                0xb9, 0x24, 0x27, 0xae, 0x41, 0xe4, 0x64, 0x9b, 0x93, 0x4c, 0xa4, 0x95, 0x99, 0x1b,
                0x78, 0x52, 0xb8, 0x55,
            ],
            total_node_count: 0,
            total_work_units: 0,
            blocks: Vec::new(),
        };
        let breakdown =
            archive_procedural_breakdown(&decoded, 256, &Limits::default(), None).unwrap();
        let value = inspection_json(&decoded, 256, Some(ActiveBackend::Scalar), &breakdown);
        assert_eq!(value["archive_bytes"], 256);
        assert_eq!(value["block_count"], 0);
        assert_eq!(value["restored_verified"], true);
        assert_eq!(value["evaluation_backend"], "scalar");
        assert_eq!(
            value["procedural_breakdown"]["container_overhead_bytes"],
            256
        );
        assert_eq!(
            value["original_sha256"],
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }
}
