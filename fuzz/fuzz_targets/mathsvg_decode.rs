#![no_main]

use libfuzzer_sys::fuzz_target;
use mathsvg_container::decode_archive;
use mathsvg_core::Limits;
use mathsvg_evaluator::evaluate_program;

const MAX_ARCHIVE_BYTES: usize = 64 * 1024;

fn fuzz_limits() -> Limits {
    Limits {
        max_archive_bytes: MAX_ARCHIVE_BYTES as u64,
        max_output_bytes: 64 * 1024,
        max_block_payload_bytes: 64 * 1024,
        max_block_output_bytes: 64 * 1024,
        max_definitions: 256,
        max_nodes: 4 * 1024,
        max_edges: 8 * 1024,
        max_graph_depth: 64,
        max_fan_out: 1024,
        max_node_payload_bytes: 64 * 1024,
        max_parameter_bytes: 16 * 1024,
        max_pattern_bytes: 16 * 1024,
        max_recurrence_order: 64,
        max_generated_bytes: 256 * 1024,
        max_temporary_bytes: 256 * 1024,
        max_work: 1 << 26,
        max_parameter_delta_abs: 1 << 16,
    }
}

fuzz_target!(|input: &[u8]| {
    // The parser sees hostile bytes but never more than one small archive.
    // Oversize rejection is covered by ordinary unit tests; truncation keeps
    // each fuzz iteration's hashing and structural work bounded.
    let archive = &input[..input.len().min(MAX_ARCHIVE_BYTES)];
    let limits = fuzz_limits();

    let Ok(decoded) = decode_archive(archive, &limits) else {
        return;
    };

    // A structurally accepted program must remain valid under the two
    // read-only paths used by archive inspection. These calls are assertions:
    // disagreement after a successful strict parse is a fuzz finding.
    for block in &decoded.blocks {
        block
            .program
            .validate(&limits)
            .expect("strictly decoded MathSVG program must revalidate");
        block
            .program
            .procedural_breakdown(&limits)
            .expect("strictly decoded MathSVG program must have a bounded breakdown");
    }

    // A hostile archive may intentionally carry a restored hash that does not
    // match its program, so verification failure is valid. The invariant here
    // is that evaluation and restored-hash verification never panic.
    let _ =
        decoded.verify_restored_with(|program| evaluate_program(program, &limits), |_, _| Ok(()));
});
