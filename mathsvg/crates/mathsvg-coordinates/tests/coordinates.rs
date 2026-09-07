use mathsvg_coordinates::{
    discover_candidates, forward, inverse, inverse_into, select_exact, CandidateOrigin,
    CoordinateDescriptor, CoordinateTransform, DiscoveryConfig, Endianness,
};
use mathsvg_core::{CandidateCost, Cursor, Error, Limits};

fn stride(original_bytes: u64, element_width: u8, channels: u16) -> CoordinateDescriptor {
    CoordinateDescriptor {
        original_bytes,
        transform: CoordinateTransform::Stride {
            element_width,
            channels,
        },
    }
}

fn byte_plane(original_bytes: u64, word_width: u8, endian: Endianness) -> CoordinateDescriptor {
    CoordinateDescriptor {
        original_bytes,
        transform: CoordinateTransform::BytePlane { word_width, endian },
    }
}

fn bit_plane(original_bytes: u64) -> CoordinateDescriptor {
    CoordinateDescriptor {
        original_bytes,
        transform: CoordinateTransform::BitPlane,
    }
}

#[test]
fn descriptor_records_and_metadata_costs_are_canonical() {
    let limits = Limits::default();
    let value = stride(12, 2, 3);
    assert_eq!(
        value.encode(&limits).unwrap(),
        vec![0x41, 0x00, 0x03, 12, 2, 3]
    );
    assert_eq!(value.encode_parameters(&limits).unwrap(), vec![12, 2, 3]);
    let mut prefixed_child = Cursor::new(&[12, 2, 3, 0xaa]);
    assert_eq!(
        CoordinateDescriptor::decode_parameters(0x41, &mut prefixed_child, &limits).unwrap(),
        value
    );
    assert_eq!(prefixed_child.read_u8("mock child").unwrap(), 0xaa);
    assert_eq!(value.dsl_metadata_bytes(5).unwrap(), 6);
    assert_eq!(
        CoordinateDescriptor::decode(&value.encode(&limits).unwrap(), &limits).unwrap(),
        value
    );
    assert_eq!(value.dsl_metadata_bytes(124).unwrap(), 6);
    assert_eq!(value.dsl_metadata_bytes(125).unwrap(), 7);

    let value = byte_plane(4, 2, Endianness::Big);
    assert_eq!(
        value.encode(&limits).unwrap(),
        vec![0x42, 0x00, 0x03, 4, 2, 1]
    );
    let value = bit_plane(9);
    assert_eq!(value.encode(&limits).unwrap(), vec![0x43, 0x00, 0x01, 9]);
    let identity = CoordinateDescriptor::identity(12);
    assert_eq!(identity.dsl_metadata_bytes(u64::MAX).unwrap(), 0);
}

#[test]
fn stride_has_stable_channel_major_golden_order() {
    let input: Vec<u8> = (0..12).collect();
    let descriptor = stride(12, 2, 3);
    let transformed = forward(&input, &descriptor, &Limits::default()).unwrap();
    assert_eq!(transformed, vec![0, 1, 6, 7, 2, 3, 8, 9, 4, 5, 10, 11]);
    assert_eq!(
        inverse(&transformed, &descriptor, &Limits::default()).unwrap(),
        input
    );
}

#[test]
fn byte_planes_are_ordered_by_numeric_significance() {
    let little_input = vec![0x22, 0x11, 0x44, 0x33];
    let little = byte_plane(4, 2, Endianness::Little);
    let big_input = vec![0x11, 0x22, 0x33, 0x44];
    let big = byte_plane(4, 2, Endianness::Big);
    let expected = vec![0x22, 0x44, 0x11, 0x33];
    assert_eq!(
        forward(&little_input, &little, &Limits::default()).unwrap(),
        expected
    );
    assert_eq!(
        forward(&big_input, &big, &Limits::default()).unwrap(),
        expected
    );
    assert_eq!(
        inverse(&expected, &little, &Limits::default()).unwrap(),
        little_input
    );
    assert_eq!(
        inverse(&expected, &big, &Limits::default()).unwrap(),
        big_input
    );
}

#[test]
fn bit_planes_are_lsb_first_and_padding_is_canonical() {
    let input = vec![0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0xff];
    let descriptor = bit_plane(input.len() as u64);
    let transformed = forward(&input, &descriptor, &Limits::default()).unwrap();
    assert_eq!(
        transformed,
        vec![
            0x01, 0x01, 0x02, 0x01, 0x04, 0x01, 0x08, 0x01, 0x10, 0x01, 0x20, 0x01, 0x40, 0x01,
            0x80, 0x01,
        ]
    );
    assert_eq!(
        inverse(&transformed, &descriptor, &Limits::default()).unwrap(),
        input
    );

    let mut noncanonical = transformed;
    noncanonical[1] |= 0x02;
    assert_eq!(
        inverse(&noncanonical, &descriptor, &Limits::default()),
        Err(Error::InvalidValue(
            "BIT_PLANE high padding bits must be zero"
        ))
    );
}

#[test]
fn all_supported_transforms_roundtrip_deterministic_generated_bytes() {
    let limits = Limits::default();
    let mut state = 0xa076_1d64_78bd_642fu64;
    for length in 0..260usize {
        let mut input = Vec::with_capacity(length);
        for _ in 0..length {
            state ^= state >> 12;
            state ^= state << 25;
            state ^= state >> 27;
            input.push((state.wrapping_mul(0x2545_f491_4f6c_dd1d) >> 56) as u8);
        }

        let mut descriptors = vec![
            CoordinateDescriptor::identity(length as u64),
            bit_plane(length as u64),
        ];
        for width in [1u8, 2, 3, 4, 6, 8] {
            if is_multiple(length, usize::from(width)) {
                descriptors.push(byte_plane(length as u64, width, Endianness::Little));
                if width > 1 {
                    descriptors.push(byte_plane(length as u64, width, Endianness::Big));
                }
            }
            for channels in [2u16, 3, 4, 6, 8] {
                let record = usize::from(width) * usize::from(channels);
                if length != 0 && is_multiple(length, record) {
                    descriptors.push(stride(length as u64, width, channels));
                }
            }
        }

        for descriptor in descriptors {
            let first = forward(&input, &descriptor, &limits).unwrap();
            let second = forward(&input, &descriptor, &limits).unwrap();
            assert_eq!(first, second);
            assert_eq!(inverse(&first, &descriptor, &limits).unwrap(), input);
            let mut into = vec![0; input.len()];
            inverse_into(&first, &descriptor, &mut into, &limits).unwrap();
            assert_eq!(into, input);
        }
    }
}

fn is_multiple(value: usize, divisor: usize) -> bool {
    value.checked_rem(divisor) == Some(0)
}

#[test]
fn malformed_descriptors_lengths_limits_and_padding_are_rejected() {
    let limits = Limits::default();
    assert!(matches!(
        CoordinateDescriptor::decode(&[0x41, 1, 0], &limits),
        Err(Error::InvalidFlags { .. })
    ));
    assert!(matches!(
        CoordinateDescriptor::decode(&[0x41, 0, 3, 12, 2, 1], &limits),
        Err(Error::InvalidValue(_))
    ));
    assert!(matches!(
        CoordinateDescriptor::decode(&[0x42, 0, 3, 12, 5, 0], &limits),
        Err(Error::InvalidValue(_))
    ));
    assert!(matches!(
        CoordinateDescriptor::decode(&[0x43, 0, 2, 1, 0], &limits),
        Err(Error::TrailingData { .. })
    ));
    assert!(matches!(
        CoordinateDescriptor::decode(&[0x43, 0, 2, 0x81, 0x00], &limits),
        Err(Error::InvalidVarint { .. })
    ));
    assert!(matches!(
        forward(&[0; 11], &stride(12, 2, 3), &limits),
        Err(Error::LengthMismatch { .. })
    ));

    let tiny = Limits {
        max_output_bytes: 3,
        max_block_output_bytes: 3,
        ..Limits::default()
    };
    assert!(matches!(
        forward(&[1, 2, 3], &bit_plane(3), &tiny),
        Err(Error::LimitExceeded {
            what: "coordinate transformed bytes",
            actual: 8,
            limit: 3
        })
    ));

    let tiny_work = Limits {
        max_work: 4,
        ..Limits::default()
    };
    let config = DiscoveryConfig {
        max_probe_lag: 8,
        max_peak_lags: 4,
        max_probe_bytes: 16,
        ..DiscoveryConfig::default()
    };
    assert!(matches!(
        discover_candidates(&[0; 16], &tiny_work, &config),
        Err(Error::LimitExceeded {
            what: "coordinate probe work",
            ..
        })
    ));
}

#[test]
fn discovery_is_bounded_canonical_and_deterministic() {
    let input: Vec<u8> = (0..96).map(|index| (index % 12) as u8).collect();
    let limits = Limits::default();
    let config = DiscoveryConfig {
        max_candidates: 20,
        max_channels: 32,
        max_probe_lag: 32,
        max_peak_lags: 8,
        max_probe_bytes: 96,
    };
    let first = discover_candidates(&input, &limits, &config).unwrap();
    let second = discover_candidates(&input, &limits, &config).unwrap();
    assert_eq!(first, second);
    assert!(first.candidates.len() <= usize::from(config.max_candidates));
    assert!(!first.events.is_empty());
    assert!(first
        .candidates
        .windows(2)
        .all(|pair| pair[0].descriptor < pair[1].descriptor));
    assert!(first
        .candidates
        .iter()
        .any(|candidate| candidate.origin == CandidateOrigin::Mandatory));
    assert!(first
        .candidates
        .iter()
        .any(|candidate| candidate.descriptor.transform == CoordinateTransform::BitPlane));
}

#[test]
fn exact_selection_uses_complete_candidate_cost_not_probe_score() {
    let input: Vec<u8> = (0..96).map(|index| (index % 12) as u8).collect();
    let limits = Limits::default();
    let selection = select_exact(
        &input,
        &limits,
        &DiscoveryConfig::default(),
        |candidate, transformed| {
            let preferred = matches!(
                candidate.descriptor.transform,
                CoordinateTransform::BytePlane {
                    word_width: 3,
                    endian: Endianness::Little
                }
            );
            Ok(CandidateCost {
                archive_bytes: if preferred { 90 } else { 100 },
                decode_work: candidate.descriptor.inverse_work()?,
                decode_memory: 0,
                node_count: 1,
                dependency_count: 1,
                opcode_sequence: vec![candidate.descriptor.catalog_opcode()],
                parameter_payload: candidate.canonical_descriptor_bytes.clone(),
                canonical_payload: transformed.to_vec(),
            })
        },
    )
    .unwrap();
    assert_eq!(
        selection.candidate.descriptor.transform,
        CoordinateTransform::BytePlane {
            word_width: 3,
            endian: Endianness::Little
        }
    );
    assert_eq!(
        inverse(
            &selection.transformed,
            &selection.candidate.descriptor,
            &limits
        )
        .unwrap(),
        input
    );
}
