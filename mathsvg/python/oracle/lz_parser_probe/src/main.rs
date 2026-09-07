#![forbid(unsafe_code)]

use std::env;
use std::error::Error;
use std::fs;
use std::hint::black_box;
use std::path::{Path, PathBuf};
use std::time::Instant;

use mathsvg_container::{decode_archive, encode_archive, ArchiveBlock};
use mathsvg_core::Limits;
use mathsvg_dsl::{Node, Program};
use mathsvg_entropy::{
    decode, encode_best, encode_lz_huffman_policy, DecodeLimits, LzParserPolicy, LzParserStats,
};
use mathsvg_evaluator::evaluate_program;
use serde::Serialize;
use sha2::{Digest, Sha256};

const SCHEMA: &str = "mathsvg-lzh-chain-lazy-oracle-v1";
const BLOCK_BYTES: usize = 1 << 20;

#[derive(Clone, Copy)]
struct SampleSpec {
    dataset_id: &'static str,
    domain: &'static str,
    relative_path: &'static str,
}

const SAMPLES: &[SampleSpec] = &[
    SampleSpec {
        dataset_id: "dev-canterbury-alice29-txt",
        domain: "text",
        relative_path: "datasets/data/canterbury/alice29.txt",
    },
    SampleSpec {
        dataset_id: "dev-canterbury-kennedy-xls",
        domain: "spreadsheet",
        relative_path: "datasets/data/canterbury/kennedy.xls",
    },
    SampleSpec {
        dataset_id: "dev-calgary-progc",
        domain: "source-code",
        relative_path: "datasets/data/calgary/progc",
    },
    SampleSpec {
        dataset_id: "dev-calgary-pic",
        domain: "bitmap",
        relative_path: "datasets/data/calgary/pic",
    },
    SampleSpec {
        dataset_id: "dev-calgary-book1",
        domain: "text",
        relative_path: "datasets/data/calgary/book1",
    },
    SampleSpec {
        dataset_id: "dev-silesia-xml",
        domain: "structured-text",
        relative_path: "datasets/data/silesia/xml",
    },
    SampleSpec {
        dataset_id: "dev-silesia-osdb",
        domain: "database",
        relative_path: "datasets/data/silesia/osdb",
    },
    SampleSpec {
        dataset_id: "dev-silesia-x-ray",
        domain: "high-entropy-control",
        relative_path: "datasets/data/silesia/x-ray",
    },
];

#[derive(Serialize)]
struct ProbeOutput {
    schema: &'static str,
    engine: &'static str,
    block_bytes: usize,
    warmups: usize,
    repetitions: usize,
    holdout_payload_inspected: bool,
    policies: Vec<&'static str>,
    samples: Vec<SampleOutput>,
    provenance: ProbeProvenance,
}

#[derive(Serialize)]
struct ProbeProvenance {
    timing_evidence_status: &'static str,
    project_workloads_quiesced_by_coordinator: bool,
    resource_isolation: bool,
    affinity_pinned: bool,
    background_processes_quiesced: bool,
    limitations: Vec<&'static str>,
    host_start: HostSnapshot,
    host_end: HostSnapshot,
    binary: FileFact,
    sources: Vec<FileFact>,
}

#[derive(Serialize)]
struct HostSnapshot {
    logical_parallelism: usize,
    load_average: Option<String>,
}

#[derive(Serialize)]
struct FileFact {
    path: String,
    bytes: u64,
    sha256: String,
}

#[derive(Serialize)]
struct SampleOutput {
    dataset_id: &'static str,
    origin: &'static str,
    domain: &'static str,
    source: &'static str,
    input_bytes: usize,
    input_sha256: String,
    block_count: usize,
    policies: Vec<PolicyOutput>,
}

#[derive(Serialize)]
struct PolicyOutput {
    policy: &'static str,
    complete: bool,
    budget_stop_blocks: usize,
    winning_blocks: usize,
    baseline_leaf_bytes: u64,
    policy_leaf_bytes: Option<u64>,
    portfolio_leaf_bytes: u64,
    baseline_one_block_archive_bytes: u64,
    policy_one_block_archive_bytes: Option<u64>,
    portfolio_one_block_archive_bytes: u64,
    baseline_full_archive_bytes: usize,
    policy_full_archive_bytes: Option<usize>,
    portfolio_full_archive_bytes: usize,
    saved_full_archive_bytes: i64,
    gain_fraction: f64,
    baseline_search_ns_median: u64,
    portfolio_search_ns_median: u64,
    search_slowdown: f64,
    parser_stats: AggregateStats,
    blocks: Vec<BlockOutput>,
}

#[derive(Clone, Copy, Default, Serialize)]
struct AggregateStats {
    positions_inserted: u64,
    chain_links_examined: u64,
    extension_bytes_compared: u64,
    extension_byte_budget: u64,
    sequence_count: u64,
    token_bytes: u64,
    maximum_scratch_bytes: u64,
}

impl AggregateStats {
    fn observe(&mut self, stats: LzParserStats) -> Result<(), Box<dyn Error>> {
        self.positions_inserted = checked_add(self.positions_inserted, stats.positions_inserted)?;
        self.chain_links_examined =
            checked_add(self.chain_links_examined, stats.chain_links_examined)?;
        self.extension_bytes_compared = checked_add(
            self.extension_bytes_compared,
            stats.extension_bytes_compared,
        )?;
        self.extension_byte_budget =
            checked_add(self.extension_byte_budget, stats.extension_byte_budget)?;
        self.sequence_count = checked_add(self.sequence_count, stats.sequence_count)?;
        self.token_bytes = checked_add(self.token_bytes, stats.token_bytes)?;
        self.maximum_scratch_bytes = self.maximum_scratch_bytes.max(stats.scratch_bytes);
        Ok(())
    }
}

#[derive(Serialize)]
struct BlockOutput {
    block_index: usize,
    input_bytes: usize,
    baseline_leaf_bytes: usize,
    policy_leaf_bytes: Option<usize>,
    baseline_one_block_archive_bytes: usize,
    policy_one_block_archive_bytes: Option<usize>,
    portfolio_selected_policy: bool,
    budget_exhausted: bool,
    token_bytes: u64,
    token_fingerprint: String,
    positions_inserted: u64,
    chain_links_examined: u64,
    extension_bytes_compared: u64,
    extension_byte_budget: u64,
}

fn checked_add(left: u64, right: u64) -> Result<u64, Box<dyn Error>> {
    left.checked_add(right)
        .ok_or_else(|| "oracle aggregate overflow".into())
}

fn sha256(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(64);
    for byte in Sha256::digest(bytes) {
        use std::fmt::Write as _;
        write!(&mut output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
}

fn file_fact(path: &Path) -> Result<FileFact, Box<dyn Error>> {
    let bytes = fs::read(path)?;
    Ok(FileFact {
        path: path.display().to_string(),
        bytes: bytes.len() as u64,
        sha256: sha256(&bytes),
    })
}

fn host_snapshot() -> HostSnapshot {
    HostSnapshot {
        logical_parallelism: std::thread::available_parallelism()
            .map(usize::from)
            .unwrap_or(1),
        load_average: fs::read_to_string("/proc/loadavg")
            .ok()
            .map(|value| value.trim().to_owned()),
    }
}

fn leaf_program(envelope: Vec<u8>, restored_bytes: usize) -> Program {
    Program {
        definitions: Vec::new(),
        root: Node::File {
            original_length: restored_bytes as u64,
            child: Box::new(Node::EntropyLiteral(envelope)),
        },
    }
}

fn verified_archive(
    programs: &[Program],
    input: &[u8],
    limits: &Limits,
) -> Result<Vec<u8>, Box<dyn Error>> {
    if programs.len() != input.chunks(BLOCK_BYTES).count() {
        return Err("program count differs from input block count".into());
    }
    let blocks = programs
        .iter()
        .zip(input.chunks(BLOCK_BYTES))
        .map(|(program, restored)| ArchiveBlock { program, restored })
        .collect::<Vec<_>>();
    let archive = encode_archive(&blocks, limits)?;
    let decoded = decode_archive(&archive, limits)?;
    let mut restored = Vec::with_capacity(input.len());
    decoded.verify_restored_with(
        |program| evaluate_program(program, limits),
        |_, block| {
            restored.extend_from_slice(block);
            Ok(())
        },
    )?;
    if restored != input {
        return Err("complete archive did not restore the oracle input".into());
    }
    Ok(archive)
}

fn verified_one_block(
    program: &Program,
    input: &[u8],
    limits: &Limits,
) -> Result<usize, Box<dyn Error>> {
    let archive = encode_archive(
        &[ArchiveBlock {
            program,
            restored: input,
        }],
        limits,
    )?;
    let decoded = decode_archive(&archive, limits)?;
    let mut restored = Vec::new();
    decoded.verify_restored_with(
        |decoded_program| evaluate_program(decoded_program, limits),
        |_, block| {
            restored.extend_from_slice(block);
            Ok(())
        },
    )?;
    if restored != input {
        return Err("one-block archive did not restore the oracle block".into());
    }
    Ok(archive.len())
}

fn run_baseline_search(input: &[u8]) -> Result<u64, Box<dyn Error>> {
    let mut bytes = 0u64;
    for block in input.chunks(BLOCK_BYTES) {
        bytes = checked_add(bytes, encode_best(block)?.bytes.len() as u64)?;
    }
    Ok(black_box(bytes))
}

fn run_portfolio_search(input: &[u8], policy: LzParserPolicy) -> Result<u64, Box<dyn Error>> {
    let mut bytes = 0u64;
    for block in input.chunks(BLOCK_BYTES) {
        let baseline = encode_best(block)?;
        let experimental = encode_lz_huffman_policy(block, policy)?;
        let selected = experimental
            .bytes
            .as_ref()
            .filter(|candidate| candidate.len() < baseline.bytes.len())
            .unwrap_or(&baseline.bytes);
        bytes = checked_add(bytes, selected.len() as u64)?;
    }
    Ok(black_box(bytes))
}

fn elapsed_ns<T>(
    operation: impl FnOnce() -> Result<T, Box<dyn Error>>,
) -> Result<u64, Box<dyn Error>> {
    let started = Instant::now();
    black_box(operation()?);
    u64::try_from(started.elapsed().as_nanos()).map_err(|_| "timing overflow".into())
}

fn median(values: &mut [u64]) -> u64 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn paired_timings(
    input: &[u8],
    policy: LzParserPolicy,
    repetitions: usize,
) -> Result<(u64, u64), Box<dyn Error>> {
    run_baseline_search(input)?;
    run_portfolio_search(input, policy)?;
    let mut baseline = Vec::with_capacity(repetitions);
    let mut portfolio = Vec::with_capacity(repetitions);
    for repetition in 0..repetitions {
        if repetition % 2 == 0 {
            baseline.push(elapsed_ns(|| run_baseline_search(input))?);
            portfolio.push(elapsed_ns(|| run_portfolio_search(input, policy))?);
        } else {
            portfolio.push(elapsed_ns(|| run_portfolio_search(input, policy))?);
            baseline.push(elapsed_ns(|| run_baseline_search(input))?);
        }
    }
    Ok((median(&mut baseline), median(&mut portfolio)))
}

fn measure_policy(
    input: &[u8],
    policy: LzParserPolicy,
    repetitions: usize,
    limits: &Limits,
) -> Result<PolicyOutput, Box<dyn Error>> {
    let mut baseline_programs = Vec::new();
    let mut policy_programs = Vec::new();
    let mut portfolio_programs = Vec::new();
    let mut blocks = Vec::new();
    let mut aggregate = AggregateStats::default();
    let mut baseline_leaf_bytes = 0u64;
    let mut policy_leaf_bytes = 0u64;
    let mut portfolio_leaf_bytes = 0u64;
    let mut baseline_one_block_archive_bytes = 0u64;
    let mut policy_one_block_archive_bytes = 0u64;
    let mut portfolio_one_block_archive_bytes = 0u64;
    let mut budget_stop_blocks = 0usize;
    let mut winning_blocks = 0usize;

    for (block_index, block) in input.chunks(BLOCK_BYTES).enumerate() {
        let baseline = encode_best(block)?;
        let baseline_leaf = baseline.bytes.len();
        let baseline_program = leaf_program(baseline.bytes, block.len());
        let baseline_archive = verified_one_block(&baseline_program, block, limits)?;
        baseline_leaf_bytes = checked_add(baseline_leaf_bytes, baseline_leaf as u64)?;
        baseline_one_block_archive_bytes =
            checked_add(baseline_one_block_archive_bytes, baseline_archive as u64)?;

        let experimental = encode_lz_huffman_policy(block, policy)?;
        aggregate.observe(experimental.analysis.stats)?;
        let budget_exhausted = experimental.analysis.stats.budget_exhausted;
        let (policy_program, policy_leaf, policy_archive) = match experimental.bytes {
            None => {
                budget_stop_blocks += 1;
                (None, None, None)
            }
            Some(envelope) => {
                if decode(&envelope, DecodeLimits::default())?.bytes != block {
                    return Err("policy leaf did not restore its source block".into());
                }
                let leaf_bytes = envelope.len();
                let program = leaf_program(envelope, block.len());
                let archive_bytes = verified_one_block(&program, block, limits)?;
                policy_leaf_bytes = checked_add(policy_leaf_bytes, leaf_bytes as u64)?;
                policy_one_block_archive_bytes =
                    checked_add(policy_one_block_archive_bytes, archive_bytes as u64)?;
                (Some(program), Some(leaf_bytes), Some(archive_bytes))
            }
        };

        let selected_policy = policy_archive.is_some_and(|bytes| bytes < baseline_archive);
        if selected_policy {
            winning_blocks += 1;
        }
        let portfolio_leaf = if selected_policy {
            policy_leaf.expect("selected policy has a leaf")
        } else {
            baseline_leaf
        };
        let portfolio_archive = if selected_policy {
            policy_archive.expect("selected policy has an archive")
        } else {
            baseline_archive
        };
        portfolio_leaf_bytes = checked_add(portfolio_leaf_bytes, portfolio_leaf as u64)?;
        portfolio_one_block_archive_bytes =
            checked_add(portfolio_one_block_archive_bytes, portfolio_archive as u64)?;

        baseline_programs.push(baseline_program.clone());
        if let Some(program) = &policy_program {
            policy_programs.push(program.clone());
        }
        portfolio_programs.push(if selected_policy {
            policy_program.expect("selected policy has a program")
        } else {
            baseline_program
        });
        blocks.push(BlockOutput {
            block_index,
            input_bytes: block.len(),
            baseline_leaf_bytes: baseline_leaf,
            policy_leaf_bytes: policy_leaf,
            baseline_one_block_archive_bytes: baseline_archive,
            policy_one_block_archive_bytes: policy_archive,
            portfolio_selected_policy: selected_policy,
            budget_exhausted,
            token_bytes: experimental.analysis.stats.token_bytes,
            token_fingerprint: format!("{:016x}", experimental.analysis.stats.token_fingerprint),
            positions_inserted: experimental.analysis.stats.positions_inserted,
            chain_links_examined: experimental.analysis.stats.chain_links_examined,
            extension_bytes_compared: experimental.analysis.stats.extension_bytes_compared,
            extension_byte_budget: experimental.analysis.stats.extension_byte_budget,
        });
    }

    let baseline_full_archive = verified_archive(&baseline_programs, input, limits)?;
    let portfolio_full_archive = verified_archive(&portfolio_programs, input, limits)?;
    let complete = budget_stop_blocks == 0;
    let policy_full_archive_bytes = if complete {
        Some(verified_archive(&policy_programs, input, limits)?.len())
    } else {
        None
    };
    let (baseline_ns, portfolio_ns) = paired_timings(input, policy, repetitions)?;
    let saved = baseline_full_archive.len() as i64 - portfolio_full_archive.len() as i64;
    let gain = saved as f64 / baseline_full_archive.len() as f64;
    Ok(PolicyOutput {
        policy: policy.as_str(),
        complete,
        budget_stop_blocks,
        winning_blocks,
        baseline_leaf_bytes,
        policy_leaf_bytes: complete.then_some(policy_leaf_bytes),
        portfolio_leaf_bytes,
        baseline_one_block_archive_bytes,
        policy_one_block_archive_bytes: complete.then_some(policy_one_block_archive_bytes),
        portfolio_one_block_archive_bytes,
        baseline_full_archive_bytes: baseline_full_archive.len(),
        policy_full_archive_bytes,
        portfolio_full_archive_bytes: portfolio_full_archive.len(),
        saved_full_archive_bytes: saved,
        gain_fraction: gain,
        baseline_search_ns_median: baseline_ns,
        portfolio_search_ns_median: portfolio_ns,
        search_slowdown: portfolio_ns as f64 / baseline_ns as f64,
        parser_stats: aggregate,
        blocks,
    })
}

fn parse_policy(value: &str) -> Result<LzParserPolicy, Box<dyn Error>> {
    match value {
        "C4" => Ok(LzParserPolicy::C4),
        "C8" => Ok(LzParserPolicy::C8),
        "C16" => Ok(LzParserPolicy::C16),
        "C4L" => Ok(LzParserPolicy::C4Lazy),
        "C8L" => Ok(LzParserPolicy::C8Lazy),
        "C16L" => Ok(LzParserPolicy::C16Lazy),
        "C32L" => Ok(LzParserPolicy::C32Lazy),
        "C64L" => Ok(LzParserPolicy::C64Lazy),
        _ => Err(format!("unknown LZ parser policy: {value}").into()),
    }
}

fn parse_args() -> Result<(PathBuf, usize, Vec<LzParserPolicy>), Box<dyn Error>> {
    let mut args = env::args().skip(1);
    let repository = args
        .next()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let repetitions = args
        .next()
        .map(|value| value.parse::<usize>())
        .transpose()?
        .unwrap_or(3);
    if repetitions == 0 {
        return Err("repetitions must be positive".into());
    }
    let policies = match args.next() {
        None => LzParserPolicy::EXPERIMENTAL.to_vec(),
        Some(value) => {
            let policies = value
                .split(',')
                .map(parse_policy)
                .collect::<Result<Vec<_>, _>>()?;
            if policies.is_empty() {
                return Err("policy filter must not be empty".into());
            }
            let mut canonical = policies.clone();
            canonical.sort_unstable();
            canonical.dedup();
            if canonical.len() != policies.len() {
                return Err("policy filter contains duplicates".into());
            }
            policies
        }
    };
    if args.next().is_some() {
        return Err(
            "usage: mathsvg-lz-parser-oracle-probe [repository] [repetitions] [policy,...]".into(),
        );
    }
    Ok((repository, repetitions, policies))
}

fn read_sample(repository: &Path, spec: SampleSpec) -> Result<Vec<u8>, Box<dyn Error>> {
    if spec.relative_path.contains("holdout") {
        return Err("LZ parser oracle refuses holdout paths".into());
    }
    let path = repository.join(spec.relative_path);
    let bytes = fs::read(&path)?;
    if bytes.is_empty() {
        return Err(format!("empty oracle input: {}", path.display()).into());
    }
    Ok(bytes)
}

fn main() -> Result<(), Box<dyn Error>> {
    let (repository, repetitions, selected_policies) = parse_args()?;
    let host_start = host_snapshot();
    let limits = Limits::default();
    let mut samples = Vec::new();
    for spec in SAMPLES {
        let input = read_sample(&repository, *spec)?;
        eprintln!(
            "LZH oracle: {} bytes={} policies={}",
            spec.dataset_id,
            input.len(),
            selected_policies.len()
        );
        let mut policies = Vec::new();
        for &policy in &selected_policies {
            eprintln!("  policy={}", policy.as_str());
            policies.push(measure_policy(&input, policy, repetitions, &limits)?);
        }
        samples.push(SampleOutput {
            dataset_id: spec.dataset_id,
            origin: "real",
            domain: spec.domain,
            source: spec.relative_path,
            input_bytes: input.len(),
            input_sha256: sha256(&input),
            block_count: input.chunks(BLOCK_BYTES).count(),
            policies,
        });
    }
    let output = ProbeOutput {
        schema: SCHEMA,
        engine: "mathsvg-entropy opcode-0x07 add-only parser-policy competition",
        block_bytes: BLOCK_BYTES,
        warmups: 1,
        repetitions,
        holdout_payload_inspected: false,
        policies: selected_policies
            .iter()
            .map(|policy| policy.as_str())
            .collect(),
        samples,
        provenance: ProbeProvenance {
            timing_evidence_status: "development-directional-gui-host",
            project_workloads_quiesced_by_coordinator: true,
            resource_isolation: false,
            affinity_pinned: false,
            background_processes_quiesced: false,
            limitations: vec![
                "GUI host is not resource-isolated.",
                "CPU affinity and frequency were not pinned.",
                "Exact size, work, determinism and roundtrip evidence are unaffected; timing is directional.",
            ],
            host_start,
            host_end: host_snapshot(),
            binary: file_fact(&env::current_exe()?)?,
            sources: [
                "mathsvg/crates/mathsvg-entropy/src/lib.rs",
                "mathsvg/crates/mathsvg-entropy/src/huffman.rs",
                "mathsvg/python/oracle/lz_parser_probe/Cargo.toml",
                "mathsvg/python/oracle/lz_parser_probe/Cargo.lock",
                "mathsvg/python/oracle/lz_parser_probe/src/main.rs",
                "mathsvg/results/manifests/development.csv",
            ]
            .iter()
            .map(|relative| file_fact(&repository.join(relative)))
            .collect::<Result<Vec<_>, _>>()?,
        },
    };
    serde_json::to_writer_pretty(std::io::stdout().lock(), &output)?;
    println!();
    Ok(())
}
