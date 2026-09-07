use mathzip_core::{
    compress, decompress, inspect, verify, DecodeLimits, EncodeOptions, Mode, ModelKind,
    ModelOptions, ResidualOptions, TransformKind, TransformOptions, FORMAT_VERSION,
};
use proptest::prelude::*;
use sha2::{Digest, Sha256};

fn quick_property_options() -> EncodeOptions {
    let mut options = EncodeOptions::for_mode(Mode::Fast);
    options.fixed_segment_size = 64;
    options.min_segment_size = 16;
    options.models = ModelOptions::raw_only();
    options
}

proptest! {
    #![proptest_config(ProptestConfig {
        cases: 1024,
        max_shrink_iters: 2048,
        .. ProptestConfig::default()
    })]

    #[test]
    fn public_codec_round_trips_generated_byte_strings(
        data in prop::collection::vec(any::<u8>(), 0..1024)
    ) {
        let options = quick_property_options();
        let archive = compress(&data, &options).unwrap();
        let restored = decompress(&archive, &DecodeLimits::default()).unwrap();
        prop_assert_eq!(restored, data);
    }
}

proptest! {
    #![proptest_config(ProptestConfig {
        cases: 128,
        max_shrink_iters: 256,
        .. ProptestConfig::default()
    })]

    #[test]
    fn balanced_model_transform_and_residual_search_round_trips(
        data in prop::collection::vec(any::<u8>(), 0..257)
    ) {
        let mut options = EncodeOptions::for_mode(Mode::Balanced);
        options.fixed_segment_size = 128;
        options.min_segment_size = 32;
        options.max_segment_size = 256;
        let archive = compress(&data, &options).unwrap();
        let restored = decompress(&archive, &DecodeLimits::default()).unwrap();
        prop_assert_eq!(restored, data);
    }
}

proptest! {
    #![proptest_config(ProptestConfig {
        cases: 1024,
        max_shrink_iters: 0,
        .. ProptestConfig::default()
    })]

    #[test]
    fn arbitrary_archive_bytes_never_panic(
        archive in prop::collection::vec(any::<u8>(), 0..2048)
    ) {
        let limits = DecodeLimits {
            max_archive_size: 4096,
            max_output_size: 4096,
            max_segments: 64,
            max_transforms: 8,
            max_model_parameter_bytes: 4096,
            max_residual_bytes: 4096,
            max_period: 256,
            max_recurrence_order: 16,
            max_control_points: 256,
        };
        let _ = decompress(&archive, &limits);
    }
}

#[test]
fn required_edge_cases_round_trip() {
    let cases = [
        Vec::new(),
        vec![173],
        (0..=255).collect::<Vec<u8>>(),
        vec![0; 4096],
        vec![255; 4096],
        (0..4096).map(|i| ((i * 17 + 9) & 255) as u8).collect(),
        (0..4096).map(|i| [1, 7, 3, 9][i % 4]).collect(),
    ];
    let options = EncodeOptions::for_mode(Mode::Fast);
    for original in cases {
        let archive = compress(&original, &options).unwrap();
        assert!(verify(&original, &archive, &DecodeLimits::default()).unwrap());
    }
}

#[test]
fn version_one_raw_archive_golden_hash_is_stable() {
    let original = [0u8, 1, 2, 3];
    let mut options = EncodeOptions::for_mode(Mode::Fast);
    options.segmentation = mathzip_core::SegmentationMode::Fixed;
    options.fixed_segment_size = original.len();
    options.models = ModelOptions::raw_only();
    options.residuals = ResidualOptions::raw_only();
    options.transforms = TransformOptions::identity_only();
    options.allow_raw_fallback = false;
    let archive = compress(&original, &options).unwrap();
    assert_eq!(archive.len(), 160);
    assert_eq!(
        hex::encode(Sha256::digest(&archive)),
        "ef495ec6d95d329304d8481376d4ca2a263c9470107dd0f3c8d8392cf01f2c88"
    );
    assert_eq!(
        decompress(&archive, &DecodeLimits::default()).unwrap(),
        original
    );
}

#[test]
fn independent_bit_plane_archive_uses_version_two_and_round_trips_odd_size() {
    let original: Vec<u8> = (0..257)
        .map(|index| ((index * 29 + index / 7) & 255) as u8)
        .collect();
    let mut options = EncodeOptions::for_mode(Mode::Fast);
    options.fixed_segment_size = 8;
    options.models = ModelOptions::raw_only();
    options.residuals = ResidualOptions::raw_only();
    options.transforms = TransformOptions {
        identity: false,
        delta: false,
        xor: false,
        bit_plane: false,
        bit_plane_independent: true,
        stride: false,
    };
    options.allow_raw_fallback = false;

    let archive = compress(&original, &options).unwrap();
    let info = inspect(&archive, &DecodeLimits::default()).unwrap();
    assert_eq!(info.format_version, FORMAT_VERSION);
    assert_eq!(info.original_size, 257);
    assert_eq!(info.transformed_size, 264);
    assert_eq!(info.transforms, [TransformKind::BitPlanePadded]);
    assert_eq!(info.raw_model_percentage, Some(100.0));
    assert_eq!(
        decompress(&archive, &DecodeLimits::default()).unwrap(),
        original
    );
}

#[test]
fn version_two_padded_bit_plane_golden_hash_is_stable() {
    let original = [0u8, 1, 2, 3];
    let mut options = EncodeOptions::for_mode(Mode::Fast);
    options.fixed_segment_size = original.len();
    options.models = ModelOptions::raw_only();
    options.residuals = ResidualOptions::raw_only();
    options.transforms = TransformOptions {
        identity: false,
        delta: false,
        xor: false,
        bit_plane: false,
        bit_plane_independent: true,
        stride: false,
    };
    options.allow_raw_fallback = false;

    let archive = compress(&original, &options).unwrap();
    assert_eq!(archive.len(), 416);
    assert_eq!(
        hex::encode(Sha256::digest(&archive)),
        "8fd085925a7747c6668afc1ab359e26c2b1a8cf563eae59a4ff737e495d40312"
    );
    assert_eq!(
        decompress(&archive, &DecodeLimits::default()).unwrap(),
        original
    );
}

#[test]
fn every_search_mode_round_trips() {
    let mut fibonacci = vec![1u8, 1];
    while fibonacci.len() < 1024 {
        let n = fibonacci.len();
        fibonacci.push(fibonacci[n - 1].wrapping_add(fibonacci[n - 2]));
    }
    for mode in [Mode::Fast, Mode::Balanced, Mode::Max] {
        let options = EncodeOptions::for_mode(mode);
        let archive = compress(&fibonacci, &options).unwrap();
        assert_eq!(
            decompress(&archive, &DecodeLimits::default()).unwrap(),
            fibonacci,
            "mode {mode:?}"
        );
    }
}

#[test]
fn inspect_reports_empty_values_without_nan() {
    let archive = compress(&[], &EncodeOptions::default()).unwrap();
    let info = inspect(&archive, &DecodeLimits::default()).unwrap();
    assert_eq!(info.original_size, 0);
    assert_eq!(info.transformed_size, 0);
    assert_eq!(info.segment_count, 0);
    assert_eq!(info.transform_count, 1);
    assert_eq!(info.compression_ratio, None);
    assert_eq!(info.mean_segment_size, None);
    assert_eq!(info.median_segment_size, None);
    assert_eq!(info.raw_model_percentage, None);
    assert_eq!(info.raw_fallback_percentage, None);
    assert!(info.checksums.header);
    assert!(info.checksums.archive);
    assert!(info.checksums.original);
}

#[test]
fn raw_model_with_compressed_residual_is_not_reported_as_raw_fallback() {
    let original = vec![0u8; 4096];
    let mut options = EncodeOptions::for_mode(Mode::Fast);
    options.models = ModelOptions::raw_only();
    options.residuals = ResidualOptions {
        raw: false,
        rle: false,
        zero_run: true,
        sparse: false,
        bit_pack: false,
        zstd: false,
    };
    options.transforms = TransformOptions::identity_only();
    options.allow_raw_fallback = false;
    let archive = compress(&original, &options).unwrap();
    let info = inspect(&archive, &DecodeLimits::default()).unwrap();
    assert_eq!(info.raw_model_percentage, Some(100.0));
    assert_eq!(info.raw_fallback_percentage, Some(0.0));
}

#[test]
fn zstd_residual_hybrid_is_self_contained_and_lossless() {
    let original: Vec<u8> = (0..8192).map(|i| ((i * 17 + 11) & 255) as u8).collect();
    let mut options = EncodeOptions::for_mode(Mode::Fast);
    options.models = ModelOptions {
        raw: false,
        constant: false,
        affine: true,
        polynomial: false,
        periodic: false,
        recurrence: false,
        piecewise_linear: false,
        run: false,
        sparse: false,
        copy: false,
    };
    options.residuals = ResidualOptions {
        raw: false,
        rle: false,
        zero_run: false,
        sparse: false,
        bit_pack: false,
        zstd: true,
    };
    options.transforms = TransformOptions::identity_only();
    options.allow_raw_fallback = false;
    let archive = compress(&original, &options).unwrap();
    let info = inspect(&archive, &DecodeLimits::default()).unwrap();
    assert!(info
        .residual_distribution
        .contains_key(&mathzip_core::ResidualCoder::Zstd));
    assert_eq!(
        decompress(&archive, &DecodeLimits::default()).unwrap(),
        original
    );
}

#[test]
fn inspect_storage_breakdown_matches_the_archive_size() {
    let original: Vec<u8> = (0..8192).map(|i| ((i * 13 + 5) & 255) as u8).collect();
    let archive = compress(&original, &EncodeOptions::default()).unwrap();
    let info = inspect(&archive, &DecodeLimits::default()).unwrap();
    assert_eq!(
        info.container_overhead_bytes
            + info.partition_metadata_bytes
            + info.model_parameter_bytes
            + info.residual_bytes,
        info.compressed_size
    );
    assert_eq!(
        info.metadata_bytes,
        info.container_overhead_bytes + info.partition_metadata_bytes
    );
}

#[test]
fn ablation_switches_remain_lossless() {
    let original = vec![0, 0, 1, 0, 0, 2, 0, 0, 3, 0, 0, 4];
    let mut options = EncodeOptions::for_mode(Mode::Balanced);
    options.models = ModelOptions::raw_only();
    options.residuals = ResidualOptions {
        raw: false,
        rle: false,
        zero_run: false,
        sparse: true,
        bit_pack: false,
        zstd: false,
    };
    options.transforms = TransformOptions::identity_only();
    options.allow_raw_fallback = false;
    let archive = compress(&original, &options).unwrap();
    assert_eq!(
        decompress(&archive, &DecodeLimits::default()).unwrap(),
        original
    );
}

#[test]
fn fixed_segmentation_honours_sizes_below_the_profile_minimum() {
    let original: Vec<u8> = (0..1024).map(|i| (i & 255) as u8).collect();
    let mut options = EncodeOptions::for_mode(Mode::Fast);
    options.segmentation = mathzip_core::SegmentationMode::Fixed;
    options.fixed_segment_size = 256;
    options.models = ModelOptions::raw_only();
    options.residuals = ResidualOptions::raw_only();
    options.transforms = TransformOptions::identity_only();
    options.allow_raw_fallback = false;
    let archive = compress(&original, &options).unwrap();
    let info = inspect(&archive, &DecodeLimits::default()).unwrap();
    assert_eq!(info.segment_count, 4);
    assert_eq!(
        decompress(&archive, &DecodeLimits::default()).unwrap(),
        original
    );
}

#[test]
fn copy_search_uses_prior_indexed_boundaries() {
    let block: Vec<u8> = (0..64).map(|i| ((i * 29 + 7) & 255) as u8).collect();
    let original = block.repeat(4);
    let mut options = EncodeOptions::for_mode(Mode::Fast);
    options.segmentation = mathzip_core::SegmentationMode::Fixed;
    options.fixed_segment_size = block.len();
    options.models = ModelOptions {
        raw: true,
        copy: true,
        constant: false,
        affine: false,
        polynomial: false,
        periodic: false,
        recurrence: false,
        piecewise_linear: false,
        run: false,
        sparse: false,
    };
    options.transforms = TransformOptions::identity_only();
    options.allow_raw_fallback = false;
    let archive = compress(&original, &options).unwrap();
    let info = inspect(&archive, &DecodeLimits::default()).unwrap();
    assert!(info.model_distribution.contains_key(&ModelKind::Copy));
    assert_eq!(
        decompress(&archive, &DecodeLimits::default()).unwrap(),
        original
    );
}

#[test]
fn corrupted_header_footer_segment_and_truncation_are_rejected() {
    let original: Vec<u8> = (1..=64).collect();
    let mut options = EncodeOptions::for_mode(Mode::Fast);
    options.models = ModelOptions::raw_only();
    options.residuals = ResidualOptions::raw_only();
    options.transforms = TransformOptions::identity_only();
    let archive = compress(&original, &options).unwrap();

    let mut bad_header = archive.clone();
    bad_header[12] ^= 1;
    assert!(decompress(&bad_header, &DecodeLimits::default()).is_err());

    let mut bad_footer = archive.clone();
    let footer_start = bad_footer.len() - 36;
    bad_footer[footer_start] ^= 1;
    assert!(decompress(&bad_footer, &DecodeLimits::default()).is_err());

    // Header (80) + explicit Identity descriptor (4) + segment descriptor
    // (36) precede the raw residual. Re-hash the archive so segment CRC is the
    // check that rejects this corruption.
    let mut bad_segment = archive.clone();
    bad_segment[120] ^= 1;
    rewrite_archive_sha256(&mut bad_segment);
    assert!(decompress(&bad_segment, &DecodeLimits::default()).is_err());

    for length in 0..archive.len() {
        assert!(decompress(&archive[..length], &DecodeLimits::default()).is_err());
    }
}

#[test]
fn authenticated_malformed_descriptors_are_rejected_without_large_allocations() {
    let original: Vec<u8> = (1..=64).collect();
    let mut options = EncodeOptions::for_mode(Mode::Fast);
    options.models = ModelOptions::raw_only();
    options.residuals = ResidualOptions::raw_only();
    options.transforms = TransformOptions::identity_only();
    let archive = compress(&original, &options).unwrap();

    // Unknown transform identifier.
    let mut unknown_transform = archive.clone();
    unknown_transform[80] = 255;
    rewrite_archive_sha256(&mut unknown_transform);
    assert!(decompress(&unknown_transform, &DecodeLimits::default()).is_err());

    // Segment starts after the four-byte explicit Identity descriptor. Its
    // model/coder IDs are at descriptor offsets 16 and 18.
    let mut unknown_model = archive.clone();
    unknown_model[84 + 16] = 255;
    rewrite_archive_sha256(&mut unknown_model);
    assert!(decompress(&unknown_model, &DecodeLimits::default()).is_err());

    let mut unknown_residual = archive.clone();
    unknown_residual[84 + 18] = 255;
    rewrite_archive_sha256(&mut unknown_residual);
    assert!(decompress(&unknown_residual, &DecodeLimits::default()).is_err());

    // An authenticated count that cannot fit the payload must fail before
    // allocating a count-sized segment vector.
    let mut impossible_count = archive.clone();
    impossible_count[40..44].copy_from_slice(&u32::MAX.to_le_bytes());
    rewrite_header_crc32(&mut impossible_count);
    rewrite_archive_sha256(&mut impossible_count);
    assert!(decompress(&impossible_count, &DecodeLimits::default()).is_err());

    let mut impossible_parameter_length = archive.clone();
    impossible_parameter_length[84 + 20..84 + 24].copy_from_slice(&u32::MAX.to_le_bytes());
    rewrite_archive_sha256(&mut impossible_parameter_length);
    assert!(decompress(&impossible_parameter_length, &DecodeLimits::default()).is_err());
}

#[test]
fn output_limit_is_checked_before_allocation() {
    let original = vec![7; 1024];
    let archive = compress(&original, &EncodeOptions::default()).unwrap();
    let limits = DecodeLimits {
        max_output_size: 1023,
        ..DecodeLimits::default()
    };
    assert!(decompress(&archive, &limits).is_err());
}

fn rewrite_archive_sha256(archive: &mut [u8]) {
    let footer_start = archive.len() - 36;
    let digest = Sha256::digest(&archive[..footer_start]);
    archive[footer_start + 4..].copy_from_slice(&digest);
}

fn rewrite_header_crc32(archive: &mut [u8]) {
    let checksum = crc32fast::hash(&archive[..76]);
    archive[76..80].copy_from_slice(&checksum.to_le_bytes());
}
