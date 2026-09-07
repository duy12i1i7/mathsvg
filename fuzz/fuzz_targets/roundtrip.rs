#![no_main]

use libfuzzer_sys::fuzz_target;
use mathzip_core::{DecodeLimits, EncodeOptions, Mode, compress, decompress, inspect};

fuzz_target!(|input: &[u8]| {
    // Keep encoder fuzz iterations bounded while exercising every Balanced
    // transform, model family, residual mode/coder, and reverse path.
    let input = &input[..input.len().min(512)];
    let archive = compress(input, &EncodeOptions::for_mode(Mode::Balanced))
        .expect("valid encoder options must represent every byte string");
    let restored =
        decompress(&archive, &DecodeLimits::default()).expect("fresh archive must decode");
    assert_eq!(restored, input);
    let info = inspect(&archive, &DecodeLimits::default()).expect("fresh archive must inspect");
    assert_eq!(info.original_size, input.len() as u64);
});
