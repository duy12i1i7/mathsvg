#![no_main]

use libfuzzer_sys::fuzz_target;
use mathsvg_entropy::{decode, decode_into, encode_best, inspect, DecodeLimits};

const MAX_ENVELOPE_BYTES: usize = 16 * 1024;
const MAX_OUTPUT_BYTES: u64 = 4 * 1024;
const MAX_ENCODER_INPUT_BYTES: usize = 256;

fn fuzz_limits() -> DecodeLimits {
    DecodeLimits {
        max_encoded_bytes: MAX_ENVELOPE_BYTES as u64,
        max_output_bytes: MAX_OUTPUT_BYTES,
        max_work: 1 << 20,
    }
}

fuzz_target!(|input: &[u8]| {
    let envelope = &input[..input.len().min(MAX_ENVELOPE_BYTES)];
    let limits = fuzz_limits();
    let inspected = inspect(envelope, limits);
    let decoded = decode(envelope, limits);

    match (inspected, decoded) {
        (Ok(metadata), Ok(decoded)) => {
            assert_eq!(decoded.codec, metadata.codec);
            assert_eq!(decoded.bytes.len() as u64, metadata.decoded_bytes);

            let mut direct = vec![0u8; decoded.bytes.len()];
            let direct_metadata = decode_into(envelope, &mut direct, limits)
                .expect("inspect and allocating decode accepted the envelope");
            assert_eq!(direct_metadata, metadata);
            assert_eq!(direct, decoded.bytes);
        }
        (Err(_), Err(_)) => {}
        (Ok(_), Err(error)) => {
            panic!("entropy inspect accepted an envelope rejected by decode: {error}");
        }
        (Err(error), Ok(_)) => {
            panic!("entropy decode accepted an envelope rejected by inspect: {error}");
        }
    }

    // Also feed a bounded source through the canonical encoder so every fuzz
    // iteration exercises a valid envelope, not only malformed parser paths.
    let source = &input[..input.len().min(MAX_ENCODER_INPUT_BYTES)];
    let best = encode_best(source).expect("native entropy must represent every byte string");
    let metadata = inspect(&best.bytes, limits).expect("fresh entropy envelope must inspect");
    let restored = decode(&best.bytes, limits).expect("fresh entropy envelope must decode");
    assert_eq!(metadata.codec, best.codec);
    assert_eq!(metadata.encoded_bytes, best.bytes.len() as u64);
    assert_eq!(restored.codec, best.codec);
    assert_eq!(restored.bytes, source);
});
