#![no_main]

use libfuzzer_sys::fuzz_target;
use mathsvg_container::{decode_archive, encode_literal_archive};
use mathsvg_core::Limits;
use mathsvg_evaluator::evaluate_program;

const MAX_INPUT_BYTES: usize = 4 * 1024;

fn fuzz_limits() -> Limits {
    Limits {
        max_archive_bytes: 64 * 1024,
        max_output_bytes: MAX_INPUT_BYTES as u64,
        max_block_payload_bytes: 16 * 1024,
        max_block_output_bytes: MAX_INPUT_BYTES as u64,
        max_definitions: 128,
        max_nodes: 4 * 1024,
        max_edges: 8 * 1024,
        max_graph_depth: 64,
        max_fan_out: 1024,
        max_node_payload_bytes: 16 * 1024,
        max_parameter_bytes: 16 * 1024,
        max_pattern_bytes: 16 * 1024,
        max_recurrence_order: 64,
        max_generated_bytes: 16 * 1024,
        max_temporary_bytes: 16 * 1024,
        max_work: 1 << 20,
        max_parameter_delta_abs: 1 << 16,
    }
}

fuzz_target!(|input: &[u8]| {
    let input = &input[..input.len().min(MAX_INPUT_BYTES)];
    let limits = fuzz_limits();
    let archive = encode_literal_archive(input, &limits)
        .expect("literal archive must represent every bounded input");
    let second = encode_literal_archive(input, &limits)
        .expect("literal archive construction must remain deterministic");
    assert_eq!(archive, second);

    let decoded =
        decode_archive(&archive, &limits).expect("fresh literal archive must decode structurally");
    let mut restored = Vec::with_capacity(input.len());
    let verified = decoded
        .verify_restored_with(
            |program| evaluate_program(program, &limits),
            |_, block| {
                restored.extend_from_slice(block);
                Ok(())
            },
        )
        .expect("fresh literal archive must verify restored hashes");
    assert_eq!(restored, input);
    assert_eq!(verified.original_size, input.len() as u64);
    assert_eq!(verified.original_sha256, decoded.original_sha256);
});
