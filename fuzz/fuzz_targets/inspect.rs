#![no_main]

use libfuzzer_sys::fuzz_target;
use mathzip_core::{DecodeLimits, inspect};

fuzz_target!(|archive: &[u8]| {
    let limits = DecodeLimits {
        max_archive_size: 1 << 20,
        max_output_size: 1 << 20,
        max_segments: 4096,
        max_transforms: 8,
        max_model_parameter_bytes: 1 << 20,
        max_residual_bytes: 1 << 20,
        max_period: 4096,
        max_recurrence_order: 64,
        max_control_points: 4096,
    };
    let _ = inspect(archive, &limits);
});
