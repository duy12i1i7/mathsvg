use std::fs;
use std::path::Path;
use std::process::{Command, Output};

use serde_json::Value;
use sha2::{Digest, Sha256};
use tempfile::tempdir;

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_mathsvg")
}

fn run(command: &mut Command) -> Output {
    command.output().expect("could not run mathsvg binary")
}

fn assert_success(output: &Output) {
    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
}

fn compress(input: &Path, archive: &Path) -> Output {
    run(Command::new(binary())
        .arg("compress")
        .arg("--profile")
        .arg("fast")
        .arg(input)
        .arg(archive))
}

fn decompress(archive: &Path, output: &Path) -> Output {
    run(Command::new(binary())
        .arg("decompress")
        .arg(archive)
        .arg(output))
}

fn next_random(state: &mut u64) -> u64 {
    *state = state
        .wrapping_mul(6_364_136_223_846_793_005)
        .wrapping_add(1_442_695_040_888_963_407);
    *state
}

#[test]
fn empty_file_round_trips_and_inspects_with_strict_verification() {
    let directory = tempdir().unwrap();
    let input = directory.path().join("empty.bin");
    let archive = directory.path().join("empty.msvg");
    let restored = directory.path().join("empty.out");
    fs::write(&input, []).unwrap();

    let encoded = compress(&input, &archive);
    assert_success(&encoded);
    let archive_bytes = fs::read(&archive).unwrap();
    assert_eq!(archive_bytes.len(), 256);
    assert_eq!(&archive_bytes[..4], b"MSVG");

    let decoded = decompress(&archive, &restored);
    assert_success(&decoded);
    assert_eq!(fs::read(&restored).unwrap(), Vec::<u8>::new());

    let inspected = run(Command::new(binary())
        .arg("inspect")
        .arg("--verify")
        .arg(&archive));
    assert_success(&inspected);
    let value: Value = serde_json::from_slice(&inspected.stdout).unwrap();
    assert_eq!(value["archive_bytes"], 256);
    assert_eq!(value["original_bytes"], 0);
    assert_eq!(value["block_count"], 0);
    assert_eq!(value["restored_verified"], true);
}

#[test]
fn synthetic_file_uses_a_real_archive_and_round_trips() {
    let directory = tempdir().unwrap();
    let input = directory.path().join("synthetic.bin");
    let archive = directory.path().join("synthetic.msvg");
    let restored = directory.path().join("synthetic.out");
    let bytes: Vec<u8> = (0..4096).map(|index| (index % 7) as u8).collect();
    fs::write(&input, &bytes).unwrap();

    assert_success(&compress(&input, &archive));
    assert_success(&decompress(&archive, &restored));
    assert_eq!(fs::read(&restored).unwrap(), bytes);

    let inspected = run(Command::new(binary())
        .arg("inspect")
        .arg("--verify")
        .arg("--backend")
        .arg("auto")
        .arg(&archive));
    assert_success(&inspected);
    let value: Value = serde_json::from_slice(&inspected.stdout).unwrap();
    assert_eq!(value["original_bytes"], 4096);
    assert_eq!(value["block_count"], 1);
    assert_eq!(value["restored_verified"], true);
    assert!(value["evaluation_backend"].as_str().is_some());
    assert!(value["total_nodes"].as_u64().unwrap() >= 2);
    let breakdown = &value["procedural_breakdown"];
    let partition_fields = [
        "container_overhead_bytes",
        "function_graph_bytes",
        "coordinate_bytes",
        "shared_definition_bytes",
        "reference_bytes",
        "parameter_bytes",
        "residual_layer_bytes",
        "literal_leaf_bytes",
        "entropy_metadata_bytes",
    ];
    let partition_bytes: u64 = partition_fields
        .iter()
        .map(|field| breakdown[*field].as_u64().unwrap())
        .sum();
    assert_eq!(partition_bytes, value["archive_bytes"].as_u64().unwrap());
    assert_eq!(
        breakdown["function_reconstructed_bytes"].as_u64().unwrap()
            + breakdown["literal_reconstructed_bytes"].as_u64().unwrap(),
        4096
    );
    assert!(breakdown["literal_only_archive_bytes"].as_u64().is_some());
    assert!(breakdown["pre_entropy_archive_bytes"].as_u64().is_some());
}

#[test]
fn deterministic_random_bytes_round_trip_losslessly() {
    let directory = tempdir().unwrap();
    let input = directory.path().join("random.bin");
    let archive = directory.path().join("random.msvg");
    let restored = directory.path().join("random.out");
    let mut state = 0x8a5c_27d4_eb2f_165bu64;
    let bytes: Vec<u8> = (0..8192)
        .map(|_| (next_random(&mut state) >> 56) as u8)
        .collect();
    fs::write(&input, &bytes).unwrap();

    assert_success(&compress(&input, &archive));
    assert_success(&decompress(&archive, &restored));
    assert_eq!(fs::read(&restored).unwrap(), bytes);
}

#[test]
fn multiblock_stream_is_byte_identical_and_round_trips() {
    let directory = tempdir().unwrap();
    let input = directory.path().join("multiblock.bin");
    let first = directory.path().join("first.msvg");
    let second = directory.path().join("second.msvg");
    let restored = directory.path().join("multiblock.out");
    let mut state = 0x1319_8a2e_0370_7344u64;
    let block_bytes = 1024 * 1024;
    let bytes: Vec<u8> = (0..(3 * block_bytes + 733))
        .map(|index| {
            if index < 2 * block_bytes {
                (index % 13) as u8
            } else {
                (next_random(&mut state) >> 58) as u8
            }
        })
        .collect();
    fs::write(&input, &bytes).unwrap();

    let first_run = compress(&input, &first);
    assert_success(&first_run);
    assert!(String::from_utf8_lossy(&first_run.stderr).contains("streamed=true"));
    let second_run = run(Command::new(binary())
        .arg("compress")
        .arg("--profile")
        .arg("fast")
        .arg("--threads")
        .arg("4")
        .arg(&input)
        .arg(&second));
    assert_success(&second_run);
    assert!(String::from_utf8_lossy(&second_run.stderr).contains("threads=4"));
    assert_eq!(fs::read(&first).unwrap(), fs::read(&second).unwrap());

    let inspected = run(Command::new(binary()).arg("inspect").arg(&first));
    assert_success(&inspected);
    let value: Value = serde_json::from_slice(&inspected.stdout).unwrap();
    assert_eq!(value["block_count"], 4);

    assert_success(&decompress(&first, &restored));
    assert_eq!(fs::read(restored).unwrap(), bytes);
}

#[test]
fn corrupt_archive_never_commits_partial_or_replacement_output() {
    let directory = tempdir().unwrap();
    let input = directory.path().join("source.bin");
    let archive = directory.path().join("valid.msvg");
    let corrupt = directory.path().join("corrupt.msvg");
    let restored = directory.path().join("restored.bin");
    fs::write(&input, vec![0x5a; 2048]).unwrap();
    assert_success(&compress(&input, &archive));

    let mut bytes = fs::read(&archive).unwrap();
    let position = bytes.len() / 2;
    bytes[position] ^= 0x80;
    fs::write(&corrupt, bytes).unwrap();
    fs::write(&restored, b"sentinel").unwrap();

    let decoded = run(Command::new(binary())
        .arg("decompress")
        .arg("--force")
        .arg(&corrupt)
        .arg(&restored));
    assert!(!decoded.status.success());
    assert!(String::from_utf8_lossy(&decoded.stderr).contains("verification failed"));
    assert_eq!(fs::read(&restored).unwrap(), b"sentinel");

    let inspected = run(Command::new(binary()).arg("inspect").arg(&corrupt));
    assert!(!inspected.status.success());
}

#[test]
fn corrupt_footer_never_publishes_verified_prefix_blocks() {
    let directory = tempdir().unwrap();
    let input = directory.path().join("source.bin");
    let archive = directory.path().join("valid.msvg");
    let corrupt = directory.path().join("footer-corrupt.msvg");
    let restored = directory.path().join("restored.bin");
    let bytes = b"footer verification must guard publication".repeat(4_000);
    fs::write(&input, bytes).unwrap();
    assert_success(&compress(&input, &archive));

    let mut archive_bytes = fs::read(&archive).unwrap();
    *archive_bytes.last_mut().unwrap() ^= 0x01;
    fs::write(&corrupt, archive_bytes).unwrap();
    fs::write(&restored, b"pre-existing sentinel").unwrap();

    let decoded = run(Command::new(binary())
        .arg("decompress")
        .arg("--force")
        .arg(&corrupt)
        .arg(&restored));
    assert!(!decoded.status.success());
    assert!(String::from_utf8_lossy(&decoded.stderr).contains("verification failed"));
    assert_eq!(fs::read(&restored).unwrap(), b"pre-existing sentinel");
}

#[test]
fn overwrite_is_rejected_by_default_and_force_is_atomic() {
    let directory = tempdir().unwrap();
    let input = directory.path().join("source.bin");
    let archive = directory.path().join("archive.msvg");
    let restored = directory.path().join("restored.bin");
    let bytes = b"native MathSVG overwrite policy".repeat(64);
    fs::write(&input, &bytes).unwrap();
    fs::write(&archive, b"existing archive").unwrap();

    let rejected = compress(&input, &archive);
    assert!(!rejected.status.success());
    assert!(String::from_utf8_lossy(&rejected.stderr).contains("already exists"));
    assert_eq!(fs::read(&archive).unwrap(), b"existing archive");

    let replaced = run(Command::new(binary())
        .arg("compress")
        .arg("--profile")
        .arg("fast")
        .arg("--force")
        .arg(&input)
        .arg(&archive));
    assert_success(&replaced);
    assert_eq!(&fs::read(&archive).unwrap()[..4], b"MSVG");

    fs::write(&restored, b"existing output").unwrap();
    let rejected = decompress(&archive, &restored);
    assert!(!rejected.status.success());
    assert_eq!(fs::read(&restored).unwrap(), b"existing output");

    let replaced = run(Command::new(binary())
        .arg("decompress")
        .arg("--force")
        .arg(&archive)
        .arg(&restored));
    assert_success(&replaced);
    assert_eq!(fs::read(&restored).unwrap(), bytes);
}

#[test]
fn same_profile_produces_byte_identical_archive_and_frozen_hash() {
    let directory = tempdir().unwrap();
    let input = directory.path().join("deterministic.bin");
    let first = directory.path().join("first.msvg");
    let second = directory.path().join("second.msvg");
    let mut state = 0x243f_6a88_85a3_08d3u64;
    let bytes: Vec<u8> = (0..4096)
        .map(|_| (next_random(&mut state) >> 61) as u8)
        .collect();
    fs::write(&input, bytes).unwrap();

    assert_success(&compress(&input, &first));
    assert_success(&compress(&input, &second));
    let first = fs::read(first).unwrap();
    let second = fs::read(second).unwrap();
    assert_eq!(first, second);
    assert_eq!(
        hex::encode(Sha256::digest(&first)),
        "78eb684fdf73a8b72aa336efcd00ed7d2878839384465f9751169b237f7f8a48"
    );
}

#[test]
fn runtime_ablation_identity_matches_the_compression_catalogue() {
    let directory = tempdir().unwrap();
    let input = directory.path().join("alice.txt");
    let baseline = directory.path().join("baseline.msvg");
    let ablated = directory.path().join("ablated.msvg");
    let restored = directory.path().join("restored.txt");
    let bytes = include_bytes!("../../../../datasets/data/canterbury/alice29.txt");
    fs::write(&input, bytes).unwrap();

    let baseline_run = compress(&input, &baseline);
    assert_success(&baseline_run);
    let ablated_run = run(Command::new(binary())
        .arg("compress")
        .arg("--profile")
        .arg("fast")
        .arg("--disable")
        .arg("whole-block-entropy")
        .arg(&input)
        .arg(&ablated));
    assert_success(&ablated_run);
    assert!(
        fs::metadata(&baseline).unwrap().len() < fs::metadata(&ablated).unwrap().len(),
        "the entropy ablation must remove the winning whole-block candidate"
    );
    assert!(
        String::from_utf8_lossy(&ablated_run.stderr)
            .contains("ablation=without-whole-block-entropy")
    );
    assert_success(&decompress(&ablated, &restored));
    assert_eq!(fs::read(&restored).unwrap(), bytes);

    let profile = run(Command::new(binary())
        .arg("profile")
        .arg("--profile")
        .arg("fast")
        .arg("--disable")
        .arg("coordinates")
        .arg("--disable")
        .arg("whole-block-entropy")
        .arg("--disable")
        .arg("coordinates"));
    assert_success(&profile);
    let value: Value = serde_json::from_slice(&profile.stdout).unwrap();
    assert_eq!(
        value["ablation"]["disabled_algorithms"],
        serde_json::json!(["whole-block-entropy", "coordinates"])
    );
    assert_eq!(value["emission_gates"]["whole_block_entropy"], false);
    assert_eq!(value["emission_gates"]["whole_block_functions"], true);
    assert_eq!(value["emission_gates"]["coordinates"], false);
}

#[test]
fn usage_and_runtime_failures_have_distinct_exit_codes() {
    let usage = run(Command::new(binary()).arg("compress"));
    assert_eq!(usage.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&usage.stderr).contains("Usage:"));

    let directory = tempdir().unwrap();
    let missing = directory.path().join("missing.bin");
    let output = directory.path().join("output.msvg");
    let runtime = compress(&missing, &output);
    assert_eq!(runtime.status.code(), Some(1));
    let stderr = String::from_utf8_lossy(&runtime.stderr);
    assert!(stderr.starts_with("mathsvg:"));
    assert!(stderr.contains("could not open"));
    assert!(!output.exists());
}
