use anyhow::{Context, Result, anyhow, bail};
use clap::{Args, Parser, Subcommand, ValueEnum};
use mathzip_core::{
    DecodeLimits, EncodeOptions, MAX_RECURSIVE_TREE_DEPTH, Mode, ModelOptions, ResidualOptions,
    SegmentationMode, TransformOptions, compress, compress_with_metrics, decompress, inspect,
    verify,
};
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Instant;
use tempfile::Builder;

const DEFAULT_MAX_INPUT_BYTES: u64 = 512 * 1024 * 1024;

#[derive(Debug, Parser)]
#[command(
    name = "mathzip",
    version,
    about = "Deterministic lossless mathematical-modelling research codec",
    long_about = None
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Subcommand)]
enum Commands {
    /// Compress a file into a self-contained MathZip archive.
    #[command(visible_alias = "c")]
    Compress(CompressArgs),
    /// Decompress and checksum-verify a MathZip archive.
    #[command(visible_alias = "d")]
    Decompress(DecompressArgs),
    /// Inspect archive structure without writing the decoded file.
    Inspect(InspectArgs),
    /// Decode an archive and compare it with an original file.
    Verify(VerifyArgs),
    /// Run the reproducible Python benchmark harness.
    Benchmark(BenchmarkArgs),
    /// Emit source provenance embedded when this binary was built.
    #[command(hide = true)]
    BuildInfo,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum ModeArg {
    Fast,
    Balanced,
    Max,
}

impl From<ModeArg> for Mode {
    fn from(value: ModeArg) -> Self {
        match value {
            ModeArg::Fast => Self::Fast,
            ModeArg::Balanced => Self::Balanced,
            ModeArg::Max => Self::Max,
        }
    }
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum SegmentationArg {
    Fixed,
    ChangePoint,
    Adaptive,
    Recursive,
}

impl From<SegmentationArg> for SegmentationMode {
    fn from(value: SegmentationArg) -> Self {
        match value {
            SegmentationArg::Fixed => Self::Fixed,
            SegmentationArg::ChangePoint => Self::ChangePoint,
            SegmentationArg::Adaptive => Self::Adaptive,
            SegmentationArg::Recursive => Self::Recursive,
        }
    }
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum ModelArg {
    Raw,
    Constant,
    Affine,
    Polynomial,
    Periodic,
    Recurrence,
    PiecewiseLinear,
    Run,
    Sparse,
    Copy,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum ResidualArg {
    Raw,
    Rle,
    ZeroRun,
    Sparse,
    BitPack,
    Zstd,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum TransformArg {
    Identity,
    Delta,
    Xor,
    /// Enable both packed v1 and independently segmented byte-aligned v2 search.
    BitPlane,
    /// Restrict search to the packed version-1 bit-plane representation.
    BitPlanePacked,
    /// Restrict search to the byte-aligned, independently segmented v2 representation.
    BitPlaneIndependent,
    Stride,
}

#[derive(Debug, Args)]
struct CompressArgs {
    /// Search profile. Wider profiles can be substantially slower.
    #[arg(long, value_enum, default_value_t = ModeArg::Balanced)]
    mode: ModeArg,
    /// Override the profile's segmentation mode.
    #[arg(long, value_enum)]
    segmentation: Option<SegmentationArg>,
    /// Override the profile's fixed/anchor segment size in bytes.
    #[arg(long, value_name = "BYTES")]
    segment_size: Option<usize>,
    /// Maximum binary-tree depth for recursive segmentation (0 permits one leaf).
    #[arg(long, value_name = "DEPTH")]
    max_tree_depth: Option<u8>,
    /// Restrict model families (comma-delimited; intended for ablation).
    #[arg(long, value_enum, value_delimiter = ',', value_name = "MODELS")]
    models: Vec<ModelArg>,
    /// Restrict residual coders (comma-delimited; intended for ablation).
    #[arg(
        long,
        value_enum,
        value_delimiter = ',',
        value_name = "RESIDUAL_CODERS"
    )]
    residual_coders: Vec<ResidualArg>,
    /// Restrict reversible transforms (comma-delimited; intended for ablation).
    #[arg(long, value_enum, value_delimiter = ',', value_name = "TRANSFORMS")]
    transforms: Vec<TransformArg>,
    /// Do not compare the search winner with the whole-file raw archive.
    #[arg(long)]
    no_raw_fallback: bool,
    /// Disable copy/reference candidates (ablation).
    #[arg(long)]
    no_copy_model: bool,
    /// Disable stride-transpose candidates (ablation).
    #[arg(long)]
    no_stride_transform: bool,
    /// Disable both packed and independently segmented bit-plane candidates.
    #[arg(long)]
    no_bit_plane: bool,
    /// Disable the standard >=1 MiB screened portfolio and run the configured
    /// candidate search exhaustively (potentially very slow).
    #[arg(long)]
    exhaustive_search: bool,
    /// Refuse inputs larger than this many bytes before allocation.
    #[arg(long, default_value_t = DEFAULT_MAX_INPUT_BYTES, value_name = "BYTES")]
    max_input_bytes: u64,
    /// Write non-overlapping encoder phase timings as JSON (benchmark aid).
    #[arg(long, value_name = "PATH")]
    metrics_output: Option<PathBuf>,
    /// Skip durable fsync of output files (benchmark parity with other codecs).
    #[arg(long)]
    no_sync: bool,
    /// Atomically replace an existing output path.
    #[arg(short, long)]
    force: bool,
    /// Input file, or '-' for standard input.
    #[arg(value_name = "INPUT")]
    input: PathBuf,
    /// Destination `.mz` file, or '-' for standard output.
    #[arg(value_name = "OUTPUT")]
    output: PathBuf,
}

#[derive(Debug, Args)]
struct DecompressArgs {
    /// Refuse archives larger than this many bytes before allocation.
    #[arg(
        long,
        default_value_t = DecodeLimits::default().max_archive_size,
        value_name = "BYTES"
    )]
    max_archive_bytes: u64,
    /// Refuse archives that declare a larger reconstructed output.
    #[arg(
        long,
        default_value_t = DecodeLimits::default().max_output_size,
        value_name = "BYTES"
    )]
    max_output_bytes: u64,
    /// Refuse archives declaring more segments than this limit.
    #[arg(
        long,
        default_value_t = DecodeLimits::default().max_segments,
        value_name = "COUNT"
    )]
    max_segments: u32,
    /// Skip durable fsync of the output file (benchmark parity with other codecs).
    #[arg(long)]
    no_sync: bool,
    /// Atomically replace an existing output path.
    #[arg(short, long)]
    force: bool,
    /// Input `.mz` file, or '-' for standard input.
    #[arg(value_name = "ARCHIVE")]
    archive: PathBuf,
    /// Restored file, or '-' for standard output.
    #[arg(value_name = "OUTPUT")]
    output: PathBuf,
}

#[derive(Debug, Args)]
struct InspectArgs {
    /// Emit compact JSON instead of human-readable pretty JSON.
    #[arg(long)]
    json: bool,
    /// Refuse archives larger than this many bytes before allocation.
    #[arg(
        long,
        default_value_t = DecodeLimits::default().max_archive_size,
        value_name = "BYTES"
    )]
    max_archive_bytes: u64,
    /// Refuse archives that declare a larger reconstructed output.
    #[arg(
        long,
        default_value_t = DecodeLimits::default().max_output_size,
        value_name = "BYTES"
    )]
    max_output_bytes: u64,
    /// Refuse archives declaring more segments than this limit.
    #[arg(
        long,
        default_value_t = DecodeLimits::default().max_segments,
        value_name = "COUNT"
    )]
    max_segments: u32,
    /// Input `.mz` archive, or '-' for standard input.
    #[arg(value_name = "ARCHIVE")]
    archive: PathBuf,
}

#[derive(Debug, Args)]
struct VerifyArgs {
    /// Refuse archives larger than this many bytes before allocation.
    #[arg(
        long,
        default_value_t = DecodeLimits::default().max_archive_size,
        value_name = "BYTES"
    )]
    max_archive_bytes: u64,
    /// Refuse archives that declare a larger reconstructed output.
    #[arg(
        long,
        default_value_t = DecodeLimits::default().max_output_size,
        value_name = "BYTES"
    )]
    max_output_bytes: u64,
    /// Refuse archives declaring more segments than this limit.
    #[arg(
        long,
        default_value_t = DecodeLimits::default().max_segments,
        value_name = "COUNT"
    )]
    max_segments: u32,
    #[arg(value_name = "ORIGINAL")]
    original: PathBuf,
    #[arg(value_name = "ARCHIVE")]
    archive: PathBuf,
}

#[derive(Debug, Args)]
struct BenchmarkArgs {
    /// JSON-compatible YAML benchmark profile.
    #[arg(value_name = "CONFIG")]
    config: PathBuf,
    /// Python interpreter used for benchmark orchestration.
    #[arg(long, default_value = "python3")]
    python: OsString,
    /// Extra options passed verbatim to `run_benchmarks.py`.
    #[arg(last = true, trailing_var_arg = true)]
    extra_args: Vec<OsString>,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("mathzip: {error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    match Cli::parse().command {
        Commands::Compress(args) => compress_command(args),
        Commands::Decompress(args) => decompress_command(args),
        Commands::Inspect(args) => inspect_command(args),
        Commands::Verify(args) => verify_command(args),
        Commands::Benchmark(args) => benchmark_command(args),
        Commands::BuildInfo => build_info_command(),
    }
}

fn build_info_command() -> Result<()> {
    let dirty = env!("MATHZIP_BUILD_DIRTY")
        .parse::<bool>()
        .context("invalid embedded build dirty flag")?;
    println!(
        "{}",
        serde_json::json!({
            "package_version": env!("CARGO_PKG_VERSION"),
            "source_revision": match env!("MATHZIP_BUILD_REVISION") {
                "unversioned" => None,
                revision => Some(revision),
            },
            "source_dirty": dirty,
        })
    );
    Ok(())
}

fn compress_command(args: CompressArgs) -> Result<()> {
    ensure_distinct_file_paths(&args.input, &args.output)?;
    if let Some(metrics_output) = &args.metrics_output {
        if metrics_output == Path::new("-") {
            bail!("--metrics-output must be a file path");
        }
        ensure_distinct_file_paths(&args.input, metrics_output)?;
        ensure_distinct_file_paths(&args.output, metrics_output)?;
    }
    let mut options = EncodeOptions::for_mode(args.mode.into());
    if let Some(segmentation) = args.segmentation {
        options.segmentation = segmentation.into();
    }
    if let Some(segment_size) = args.segment_size {
        if segment_size == 0 {
            bail!("--segment-size must be greater than zero");
        }
        options.fixed_segment_size = segment_size;
    }
    if let Some(max_tree_depth) = args.max_tree_depth {
        if max_tree_depth > MAX_RECURSIVE_TREE_DEPTH {
            bail!("--max-tree-depth must be at most {MAX_RECURSIVE_TREE_DEPTH}");
        }
        options.max_tree_depth = max_tree_depth;
    }
    if !args.models.is_empty() {
        options.models = model_options(&args.models);
    }
    if !args.residual_coders.is_empty() {
        options.residuals = residual_options(&args.residual_coders);
    }
    if !args.transforms.is_empty() {
        options.transforms = transform_options(&args.transforms);
    }
    options.allow_raw_fallback = !args.no_raw_fallback;
    if args.no_copy_model {
        options.models.copy = false;
    }
    if args.no_stride_transform {
        options.transforms.stride = false;
    }
    if args.no_bit_plane {
        options.transforms.bit_plane = false;
        options.transforms.bit_plane_independent = false;
    }
    if args.exhaustive_search {
        options.large_input_screening = false;
    }
    let input = read_path_bounded(&args.input, args.max_input_bytes)?;

    let started = Instant::now();
    let (archive, metrics) = if args.metrics_output.is_some() {
        let (archive, metrics) =
            compress_with_metrics(&input, &options).context("codec compression failed")?;
        (archive, Some(metrics))
    } else {
        (
            compress(&input, &options).context("codec compression failed")?,
            None,
        )
    };
    let elapsed = started.elapsed();
    write_path_atomic(&args.output, &archive, args.force, !args.no_sync)?;
    if let (Some(metrics_output), Some(metrics)) = (&args.metrics_output, metrics) {
        let mut encoded = serde_json::to_vec_pretty(&metrics)?;
        encoded.push(b'\n');
        write_path_atomic(metrics_output, &encoded, args.force, !args.no_sync)?;
    }

    let ratio = if archive.is_empty() {
        None
    } else {
        Some(input.len() as f64 / archive.len() as f64)
    };
    eprintln!(
        "compressed {} -> {} bytes{} in {:.3}s",
        input.len(),
        archive.len(),
        ratio
            .map(|value| format!(" (ratio {value:.4}:1)"))
            .unwrap_or_default(),
        elapsed.as_secs_f64()
    );
    Ok(())
}

fn model_options(values: &[ModelArg]) -> ModelOptions {
    let mut options = ModelOptions {
        raw: false,
        constant: false,
        affine: false,
        polynomial: false,
        periodic: false,
        recurrence: false,
        piecewise_linear: false,
        run: false,
        sparse: false,
        copy: false,
    };
    for value in values {
        match value {
            ModelArg::Raw => options.raw = true,
            ModelArg::Constant => options.constant = true,
            ModelArg::Affine => options.affine = true,
            ModelArg::Polynomial => options.polynomial = true,
            ModelArg::Periodic => options.periodic = true,
            ModelArg::Recurrence => options.recurrence = true,
            ModelArg::PiecewiseLinear => options.piecewise_linear = true,
            ModelArg::Run => options.run = true,
            ModelArg::Sparse => options.sparse = true,
            ModelArg::Copy => options.copy = true,
        }
    }
    options
}

fn residual_options(values: &[ResidualArg]) -> ResidualOptions {
    let mut options = ResidualOptions {
        raw: false,
        rle: false,
        zero_run: false,
        sparse: false,
        bit_pack: false,
        zstd: false,
    };
    for value in values {
        match value {
            ResidualArg::Raw => options.raw = true,
            ResidualArg::Rle => options.rle = true,
            ResidualArg::ZeroRun => options.zero_run = true,
            ResidualArg::Sparse => options.sparse = true,
            ResidualArg::BitPack => options.bit_pack = true,
            ResidualArg::Zstd => options.zstd = true,
        }
    }
    options
}

fn transform_options(values: &[TransformArg]) -> TransformOptions {
    let mut options = TransformOptions {
        identity: false,
        delta: false,
        xor: false,
        bit_plane: false,
        bit_plane_independent: false,
        stride: false,
    };
    for value in values {
        match value {
            TransformArg::Identity => options.identity = true,
            TransformArg::Delta => options.delta = true,
            TransformArg::Xor => options.xor = true,
            TransformArg::BitPlane => {
                options.bit_plane = true;
                options.bit_plane_independent = true;
            }
            TransformArg::BitPlanePacked => options.bit_plane = true,
            TransformArg::BitPlaneIndependent => options.bit_plane_independent = true,
            TransformArg::Stride => options.stride = true,
        }
    }
    options
}

fn decompress_command(args: DecompressArgs) -> Result<()> {
    ensure_distinct_file_paths(&args.archive, &args.output)?;
    let limits = DecodeLimits {
        max_archive_size: args.max_archive_bytes,
        max_output_size: args.max_output_bytes,
        max_segments: args.max_segments,
        ..DecodeLimits::default()
    };
    let archive = read_path_bounded(&args.archive, limits.max_archive_size)?;
    let started = Instant::now();
    let restored = decompress(&archive, &limits).context("archive decompression failed")?;
    let elapsed = started.elapsed();
    write_path_atomic(&args.output, &restored, args.force, !args.no_sync)?;
    eprintln!(
        "restored {} bytes in {:.3}s; original SHA-256 verified",
        restored.len(),
        elapsed.as_secs_f64()
    );
    Ok(())
}

fn inspect_command(args: InspectArgs) -> Result<()> {
    let limits = DecodeLimits {
        max_archive_size: args.max_archive_bytes,
        max_output_size: args.max_output_bytes,
        max_segments: args.max_segments,
        ..DecodeLimits::default()
    };
    let archive = read_path_bounded(&args.archive, limits.max_archive_size)?;
    let info = inspect(&archive, &limits).context("archive inspection failed")?;
    let rendered = if args.json {
        serde_json::to_string(&info)
    } else {
        serde_json::to_string_pretty(&info)
    }
    .context("could not serialize inspection result")?;
    println!("{rendered}");
    Ok(())
}

fn verify_command(args: VerifyArgs) -> Result<()> {
    if args.original == Path::new("-") && args.archive == Path::new("-") {
        bail!("ORIGINAL and ARCHIVE cannot both use standard input");
    }
    let limits = DecodeLimits {
        max_archive_size: args.max_archive_bytes,
        max_output_size: args.max_output_bytes,
        max_segments: args.max_segments,
        ..DecodeLimits::default()
    };
    let original = read_path_bounded(&args.original, args.max_output_bytes)?;
    let archive = read_path_bounded(&args.archive, limits.max_archive_size)?;
    if verify(&original, &archive, &limits).context("archive verification failed")? {
        println!("OK: decoded bytes and SHA-256 match the original");
        Ok(())
    } else {
        bail!("decoded archive does not match the original")
    }
}

fn benchmark_command(args: BenchmarkArgs) -> Result<()> {
    let script = benchmark_script()?;
    let executable =
        std::env::current_exe().context("could not resolve current MathZip executable")?;
    let status = Command::new(&args.python)
        .arg(script)
        .arg("--config")
        .arg(&args.config)
        .arg("--mathzip-binary")
        .arg(executable)
        .args(args.extra_args)
        .status()
        .with_context(|| format!("could not start {:?}", args.python))?;
    if !status.success() {
        bail!("benchmark harness exited with {status}");
    }
    Ok(())
}

fn benchmark_script() -> Result<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(directory) = std::env::var_os("MATHZIP_PYTHON_DIR") {
        candidates.push(PathBuf::from(directory).join("run_benchmarks.py"));
    }
    candidates.push(PathBuf::from("python/run_benchmarks.py"));
    candidates
        .push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../python/run_benchmarks.py"));
    candidates
        .into_iter()
        .find(|path| path.is_file())
        .ok_or_else(|| {
            anyhow!(
                "run_benchmarks.py was not found; run from the repository root or set \
                 MATHZIP_PYTHON_DIR"
            )
        })
}

fn read_path_bounded(path: &Path, limit: u64) -> Result<Vec<u8>> {
    if path == Path::new("-") {
        let mut input = std::io::stdin().lock().take(limit.saturating_add(1));
        let mut bytes = Vec::new();
        input
            .read_to_end(&mut bytes)
            .context("could not read standard input")?;
        if bytes.len() as u64 > limit {
            bail!("standard input exceeds the configured {limit}-byte limit");
        }
        return Ok(bytes);
    }

    let metadata =
        fs::metadata(path).with_context(|| format!("could not stat {}", path.display()))?;
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
    let capacity = usize::try_from(metadata.len())
        .context("input size is not addressable on this platform")?;
    let mut bytes = Vec::with_capacity(capacity);
    File::open(path)
        .with_context(|| format!("could not open {}", path.display()))?
        .take(limit.saturating_add(1))
        .read_to_end(&mut bytes)
        .with_context(|| format!("could not read {}", path.display()))?;
    if bytes.len() as u64 > limit {
        bail!("{} grew beyond the configured byte limit", path.display());
    }
    Ok(bytes)
}

fn write_path_atomic(path: &Path, bytes: &[u8], force: bool, sync: bool) -> Result<()> {
    if path == Path::new("-") {
        let mut stdout = std::io::stdout().lock();
        stdout
            .write_all(bytes)
            .context("could not write standard output")?;
        stdout.flush().context("could not flush standard output")?;
        return Ok(());
    }
    if path.exists() && !force {
        bail!(
            "{} already exists; pass --force to replace it atomically",
            path.display()
        );
    }
    let parent = path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    if !parent.is_dir() {
        bail!("output directory {} does not exist", parent.display());
    }
    let mut temporary = Builder::new()
        .prefix(".mathzip-")
        .suffix(".tmp")
        .tempfile_in(parent)
        .with_context(|| format!("could not create a temporary file in {}", parent.display()))?;
    temporary
        .write_all(bytes)
        .with_context(|| format!("could not write temporary output for {}", path.display()))?;
    if sync {
        temporary
            .as_file()
            .sync_all()
            .context("could not sync temporary output")?;
    }
    if force {
        temporary
            .persist(path)
            .map_err(|error| error.error)
            .with_context(|| format!("could not replace {}", path.display()))?;
    } else {
        temporary
            .persist_noclobber(path)
            .map_err(|error| error.error)
            .with_context(|| format!("could not create {}", path.display()))?;
    }
    Ok(())
}

fn ensure_distinct_file_paths(input: &Path, output: &Path) -> Result<()> {
    if input == Path::new("-") || output == Path::new("-") {
        return Ok(());
    }
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
    let parent = path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let parent = fs::canonicalize(parent)
        .with_context(|| format!("could not resolve output directory {}", parent.display()))?;
    let name = path
        .file_name()
        .ok_or_else(|| anyhow!("{} has no file name", path.display()))?;
    Ok(parent.join(name))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mode_mapping_is_exact() {
        assert_eq!(Mode::from(ModeArg::Fast), Mode::Fast);
        assert_eq!(Mode::from(ModeArg::Balanced), Mode::Balanced);
        assert_eq!(Mode::from(ModeArg::Max), Mode::Max);
    }

    #[test]
    fn recursive_segmentation_mapping_is_exact() {
        assert_eq!(
            SegmentationMode::from(SegmentationArg::Recursive),
            SegmentationMode::Recursive
        );
    }

    #[test]
    fn bit_plane_transform_arguments_are_unambiguous() {
        let both = transform_options(&[TransformArg::BitPlane]);
        assert!(both.bit_plane);
        assert!(both.bit_plane_independent);

        let packed = transform_options(&[TransformArg::BitPlanePacked]);
        assert!(packed.bit_plane);
        assert!(!packed.bit_plane_independent);

        let independent = transform_options(&[TransformArg::BitPlaneIndependent]);
        assert!(!independent.bit_plane);
        assert!(independent.bit_plane_independent);
    }

    #[test]
    fn recursive_depth_option_is_parsed() {
        let cli = Cli::try_parse_from([
            "mathzip",
            "compress",
            "--segmentation",
            "recursive",
            "--max-tree-depth",
            "7",
            "input.bin",
            "output.mz",
        ])
        .unwrap();
        let Commands::Compress(args) = cli.command else {
            panic!("compress command was not parsed");
        };
        assert!(matches!(
            args.segmentation,
            Some(SegmentationArg::Recursive)
        ));
        assert_eq!(args.max_tree_depth, Some(7));
    }

    #[test]
    fn exhaustive_search_opt_out_is_parsed() {
        let cli = Cli::try_parse_from([
            "mathzip",
            "compress",
            "--exhaustive-search",
            "input.bin",
            "output.mz",
        ])
        .unwrap();
        let Commands::Compress(args) = cli.command else {
            panic!("compress command was not parsed");
        };
        assert!(args.exhaustive_search);
    }

    #[test]
    fn excessive_recursive_depth_is_rejected_before_input_io() {
        let cli = Cli::try_parse_from([
            "mathzip",
            "compress",
            "--segmentation",
            "recursive",
            "--max-tree-depth",
            "17",
            "definitely-missing-input.bin",
            "unused-output.mz",
        ])
        .unwrap();
        let Commands::Compress(args) = cli.command else {
            panic!("compress command was not parsed");
        };
        let error = compress_command(args).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("--max-tree-depth must be at most 16")
        );
    }

    #[test]
    fn benchmark_script_exists_in_checkout() {
        // The Python agent creates this entry point. Keeping this test makes an
        // accidentally incomplete source distribution fail loudly.
        assert!(benchmark_script().is_ok());
    }
}
