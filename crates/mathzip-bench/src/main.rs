use anyhow::{Context, Result, bail};
use clap::{Parser, ValueEnum};
use mathzip_core::{DecodeLimits, EncodeOptions, Mode, compress, decompress, inspect};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::PathBuf;
use std::time::Instant;

#[derive(Debug, Parser)]
#[command(
    name = "mathzip-bench",
    about = "Low-overhead MathZip round-trip measurement helper"
)]
struct Args {
    #[arg(long, value_enum, default_value_t = ModeArg::Balanced)]
    mode: ModeArg,
    #[arg(long, default_value_t = 3)]
    repeats: usize,
    #[arg(long)]
    pretty: bool,
    #[arg(required = true)]
    inputs: Vec<PathBuf>,
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

#[derive(Debug, Serialize)]
struct Measurement {
    path: String,
    mode: String,
    original_bytes: usize,
    compressed_bytes: usize,
    ratio: Option<f64>,
    compression_seconds: Vec<f64>,
    decompression_seconds: Vec<f64>,
    deterministic_archive: bool,
    roundtrip_verified: bool,
    input_sha256: String,
    archive_sha256: String,
    archive: serde_json::Value,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("mathzip-bench: {error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let args = Args::parse();
    if args.repeats == 0 {
        bail!("--repeats must be greater than zero");
    }
    let mode: Mode = args.mode.into();
    let options = EncodeOptions::for_mode(mode);
    let limits = DecodeLimits::default();
    let mut measurements = Vec::with_capacity(args.inputs.len());

    for path in args.inputs {
        let input =
            fs::read(&path).with_context(|| format!("could not read {}", path.display()))?;
        let mut compression_seconds = Vec::with_capacity(args.repeats);
        let mut decompression_seconds = Vec::with_capacity(args.repeats);
        let mut first_archive: Option<Vec<u8>> = None;
        let mut deterministic = true;
        let mut roundtrip = true;

        for _ in 0..args.repeats {
            let started = Instant::now();
            let archive = compress(&input, &options)?;
            compression_seconds.push(started.elapsed().as_secs_f64());
            if let Some(first) = &first_archive {
                deterministic &= first == &archive;
            } else {
                first_archive = Some(archive.clone());
            }

            let started = Instant::now();
            let restored = decompress(&archive, &limits)?;
            decompression_seconds.push(started.elapsed().as_secs_f64());
            roundtrip &= restored == input;
        }

        let archive = first_archive.context("no benchmark iterations ran")?;
        let archive_info = inspect(&archive, &limits)?;
        measurements.push(Measurement {
            path: path.display().to_string(),
            mode: format!("{mode:?}").to_lowercase(),
            original_bytes: input.len(),
            compressed_bytes: archive.len(),
            ratio: (!archive.is_empty()).then(|| input.len() as f64 / archive.len() as f64),
            compression_seconds,
            decompression_seconds,
            deterministic_archive: deterministic,
            roundtrip_verified: roundtrip,
            input_sha256: digest(&input),
            archive_sha256: digest(&archive),
            archive: serde_json::to_value(archive_info)?,
        });
    }

    let output = if args.pretty {
        serde_json::to_string_pretty(&measurements)?
    } else {
        serde_json::to_string(&measurements)?
    };
    println!("{output}");
    Ok(())
}

fn digest(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hex::encode(hasher.finalize())
}
