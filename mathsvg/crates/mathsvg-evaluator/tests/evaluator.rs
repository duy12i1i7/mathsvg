use mathsvg_coordinates::forward;
use mathsvg_core::{Error, Limits, Result};
use mathsvg_dsl::{CoordinateDescriptor, CorrectionKind, Exception, Node, Program, Recurrence};
use mathsvg_dsl::{CoordinateEndianness, CoordinateTransform, NativeCoordinateDescriptor};
use mathsvg_entropy::{encode, LeafCodec};
use mathsvg_evaluator::{
    evaluate_node, evaluate_node_with_report, evaluate_program, evaluate_program_to_sink,
    evaluate_program_with_backend, EvaluationBackend,
};

fn file(child: Node, length: u64) -> Node {
    Node::File {
        original_length: length,
        child: Box::new(child),
    }
}

fn reference(definition: u32) -> Node {
    Node::Reference {
        definition,
        parameter_delta: Vec::new(),
    }
}

fn coordinate_program(descriptor: NativeCoordinateDescriptor, transformed: Vec<u8>) -> Program {
    Program {
        definitions: Vec::new(),
        root: file(
            Node::Coordinate {
                descriptor,
                child: Box::new(Node::Literal(transformed)),
            },
            descriptor.original_bytes,
        ),
    }
}

fn entropy_program(codec: LeafCodec, restored: &[u8]) -> Program {
    Program {
        definitions: Vec::new(),
        root: file(
            Node::EntropyLiteral(encode(codec, restored).unwrap()),
            restored.len() as u64,
        ),
    }
}

#[test]
fn literal_const_linear_and_periodic_have_golden_bytes() {
    let program = Program {
        definitions: Vec::new(),
        root: file(
            Node::Concat(vec![
                Node::Literal(b"Hi".to_vec()),
                Node::Const {
                    length: 2,
                    value: b'!',
                },
                Node::Linear {
                    count: 4,
                    width: 8,
                    modulus: 251,
                    a: 3,
                    b: 2,
                },
                Node::Periodic {
                    pattern: b"ab".to_vec(),
                    repetitions: 2,
                    suffix: b"z".to_vec(),
                },
            ]),
            13,
        ),
    };
    assert_eq!(
        evaluate_program(&program, &Limits::default()).unwrap(),
        b"Hi!!\x02\x05\x08\x0bababz"
    );
}

#[test]
fn recurrence_coefficient_zero_is_the_nearest_lag() {
    let recurrence = Node::Recurrence(Recurrence {
        count: 5,
        width: 8,
        modulus: 251,
        coefficients: vec![2, 0],
        initial_state: vec![3, 5],
    });
    assert_eq!(
        evaluate_node(&recurrence, &[], &Limits::default()).unwrap(),
        vec![3, 5, 10, 20, 40]
    );

    let fibonacci = Node::Recurrence(Recurrence {
        count: 8,
        width: 8,
        modulus: 251,
        coefficients: vec![1, 1],
        initial_state: vec![1, 1],
    });
    assert_eq!(
        evaluate_node(&fibonacci, &[], &Limits::default()).unwrap(),
        vec![1, 1, 2, 3, 5, 8, 13, 21]
    );
}

#[test]
fn wide_generators_are_canonical_little_endian() {
    let linear = Node::Linear {
        count: 3,
        width: 16,
        modulus: 65_536,
        a: 256,
        b: 1,
    };
    assert_eq!(
        evaluate_node(&linear, &[], &Limits::default()).unwrap(),
        vec![1, 0, 1, 1, 1, 2]
    );

    let recurrence = Node::Recurrence(Recurrence {
        count: 4,
        width: 24,
        modulus: 1 << 24,
        coefficients: vec![1, 1],
        initial_state: vec![0x010203, 0x040506],
    });
    assert_eq!(
        evaluate_node(&recurrence, &[], &Limits::default()).unwrap(),
        vec![0x03, 0x02, 0x01, 0x06, 0x05, 0x04, 0x09, 0x07, 0x05, 0x0f, 0x0c, 0x09,]
    );
}

#[test]
fn all_topology_correction_exception_and_reference_nodes_evaluate() {
    let definitions = vec![
        Node::Periodic {
            pattern: vec![10, 20],
            repetitions: 2,
            suffix: Vec::new(),
        },
        Node::Correct {
            kind: CorrectionKind::Add,
            prediction: Box::new(reference(0)),
            correction: Box::new(Node::Literal(vec![1, 2, 3, 4])),
        },
        Node::Correct {
            kind: CorrectionKind::Subtract,
            prediction: Box::new(reference(1)),
            correction: Box::new(Node::Const {
                length: 4,
                value: 1,
            }),
        },
        Node::Correct {
            kind: CorrectionKind::Xor,
            prediction: Box::new(reference(2)),
            correction: Box::new(Node::Literal(vec![0xff, 0, 0xff, 0])),
        },
    ];
    let left = Node::Group {
        coordinate: CoordinateDescriptor::Identity,
        child: Box::new(Node::Exceptions {
            base: Box::new(reference(3)),
            exceptions: vec![
                Exception {
                    position: 1,
                    value: 99,
                },
                Exception {
                    position: 3,
                    value: 77,
                },
            ],
        }),
    };
    let right = Node::Literal(vec![7, 8]);
    let root_child = Node::Split {
        boundaries: vec![4],
        children: vec![left, right],
    };
    let program = Program {
        definitions,
        root: file(root_child, 6),
    };

    let (bytes, report) = {
        let mut sink_value = Vec::new();
        let report =
            evaluate_program_to_sink(&program, &Limits::default(), |bytes| -> Result<()> {
                sink_value.extend_from_slice(bytes);
                Ok(())
            })
            .unwrap();
        (sink_value, report)
    };
    assert_eq!(bytes, vec![245, 99, 243, 77, 7, 8]);
    assert_eq!(report.output_bytes, 6);
    assert_eq!(report.memoized_definitions, 4);
    assert_eq!(report.temporary_bytes_peak, 20);
}

#[test]
fn word_add_and_subtract_wrap_at_the_declared_width() {
    let predictor = Node::Linear {
        count: 2,
        width: 16,
        modulus: 65_536,
        a: 0,
        b: 65_535,
    };
    let delta = Node::Linear {
        count: 2,
        width: 16,
        modulus: 65_536,
        a: 0,
        b: 2,
    };
    let add = Node::Correct {
        kind: CorrectionKind::Add,
        prediction: Box::new(predictor.clone()),
        correction: Box::new(delta.clone()),
    };
    assert_eq!(
        evaluate_node(&add, &[], &Limits::default()).unwrap(),
        vec![1, 0, 1, 0]
    );

    let subtract = Node::Correct {
        kind: CorrectionKind::Subtract,
        prediction: Box::new(delta),
        correction: Box::new(predictor),
    };
    assert_eq!(
        evaluate_node(&subtract, &[], &Limits::default()).unwrap(),
        vec![3, 0, 3, 0]
    );
}

#[test]
fn scalar_auto_and_native_simd_evaluate_corrections_identically() {
    let length = 4_113usize;
    let first_delta = (0..length)
        .map(|index| ((index * 37 + 11) & 0xff) as u8)
        .collect::<Vec<_>>();
    let second_delta = (0..length)
        .map(|index| ((index * 101 + 29) & 0xff) as u8)
        .collect::<Vec<_>>();
    let child = Node::Correct {
        kind: CorrectionKind::Subtract,
        prediction: Box::new(Node::Correct {
            kind: CorrectionKind::Xor,
            prediction: Box::new(Node::Correct {
                kind: CorrectionKind::Add,
                prediction: Box::new(Node::Periodic {
                    pattern: vec![0, 1, 2, 250, 255],
                    repetitions: (length / 5) as u64,
                    suffix: vec![7, 8, 9],
                }),
                correction: Box::new(Node::Literal(first_delta)),
            }),
            correction: Box::new(Node::Literal(second_delta)),
        }),
        correction: Box::new(Node::Const {
            length: length as u64,
            value: 0x81,
        }),
    };
    let program = Program {
        definitions: Vec::new(),
        root: file(child, length as u64),
    };
    let limits = Limits::default();
    let scalar =
        evaluate_program_with_backend(&program, &limits, EvaluationBackend::Scalar).unwrap();
    assert_eq!(scalar.backend.name(), "scalar");
    assert_eq!(evaluate_program(&program, &limits).unwrap(), scalar.bytes);

    let automatic =
        evaluate_program_with_backend(&program, &limits, EvaluationBackend::Auto).unwrap();
    assert_eq!(automatic.bytes, scalar.bytes);
    if let Some(expected) = mathsvg_kernels::native_simd_backend() {
        assert_eq!(automatic.backend, expected);
        let simd =
            evaluate_program_with_backend(&program, &limits, EvaluationBackend::Simd).unwrap();
        assert_eq!(simd.backend, expected);
        assert_eq!(simd.bytes, scalar.bytes);
    }
}

#[test]
fn references_are_lazy_and_memoized_once() {
    let definitions = vec![
        Node::Literal(b"xy".to_vec()),
        Node::Concat(vec![reference(0), reference(0)]),
        Node::Literal(b"unused".to_vec()),
    ];
    let target = Node::Concat(vec![reference(1), reference(1)]);
    let (bytes, report) =
        evaluate_node_with_report(&target, &definitions, &Limits::default()).unwrap();
    assert_eq!(bytes, b"xyxyxyxy");
    assert_eq!(report.memoized_definitions, 2);
    assert_eq!(report.temporary_bytes_peak, 6);
}

#[test]
fn preflight_rejects_malformed_program_before_sink_side_effects() {
    let invalid = Program {
        definitions: Vec::new(),
        root: file(reference(0), 1),
    };
    let mut called = false;
    let result = evaluate_program_to_sink(&invalid, &Limits::default(), |_| {
        called = true;
        Ok(())
    });
    assert!(matches!(result, Err(Error::InvalidReference { .. })));
    assert!(!called);

    let wrong_length = Program {
        definitions: Vec::new(),
        root: file(Node::Literal(vec![1, 2]), 3),
    };
    assert!(matches!(
        evaluate_program(&wrong_length, &Limits::default()),
        Err(Error::LengthMismatch {
            context: "FILE child",
            ..
        })
    ));
}

#[test]
fn output_work_and_concrete_scratch_limits_are_enforced() {
    let output_limited = Limits {
        max_output_bytes: 3,
        max_block_output_bytes: 3,
        ..Limits::default()
    };
    assert!(matches!(
        evaluate_program(&Program::literal(vec![0; 4]), &output_limited),
        Err(Error::LimitExceeded {
            what: "output bytes",
            ..
        })
    ));

    let work_limited = Limits {
        max_work: 2,
        ..Limits::default()
    };
    assert!(matches!(
        evaluate_program(&Program::literal(vec![0; 4]), &work_limited),
        Err(Error::LimitExceeded { what: "work", .. })
    ));

    // The normative DSL workspace includes the complete definition cache, so
    // preflight rejects this before the evaluator allocates the memoized value.
    let cache_limited = Limits {
        max_temporary_bytes: 3,
        ..Limits::default()
    };
    let program = Program {
        definitions: vec![Node::Literal(vec![1, 2, 3, 4])],
        root: file(reference(0), 4),
    };
    assert_eq!(
        program
            .validate(&Limits::default())
            .unwrap()
            .temporary_bytes,
        4
    );
    assert!(matches!(
        evaluate_program(&program, &cache_limited),
        Err(Error::LimitExceeded {
            what: "temporary bytes",
            actual: 4,
            limit: 3
        })
    ));
}

#[test]
fn deterministic_literal_roundtrip_over_many_generated_inputs() {
    let limits = Limits::default();
    let mut state = 0x9e37_79b9_7f4a_7c15u64;
    for length in 0..256usize {
        let mut input = Vec::with_capacity(length);
        for _ in 0..length {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            input.push((state >> 56) as u8);
        }
        let program = Program::literal(input.clone());
        assert_eq!(evaluate_program(&program, &limits).unwrap(), input);
        assert_eq!(
            evaluate_program(&program, &limits).unwrap(),
            evaluate_program(&program, &limits).unwrap()
        );
    }
}

#[test]
fn deterministic_random_corrections_and_exceptions_roundtrip() {
    let limits = Limits::default();
    let mut state = 0xd1b5_4a32_d192_ed03u64;
    for length in 0..192usize {
        let mut input = Vec::with_capacity(length);
        for _ in 0..length {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            input.push((state >> 24) as u8);
        }

        let predictor = input.first().copied().unwrap_or(0);
        let xor_residual = input.iter().map(|byte| byte ^ predictor).collect();
        let corrected = Program {
            definitions: Vec::new(),
            root: file(
                Node::Correct {
                    kind: CorrectionKind::Xor,
                    prediction: Box::new(Node::Const {
                        length: length as u64,
                        value: predictor,
                    }),
                    correction: Box::new(Node::Literal(xor_residual)),
                },
                length as u64,
            ),
        };
        assert_eq!(evaluate_program(&corrected, &limits).unwrap(), input);

        let exceptions = input
            .iter()
            .enumerate()
            .filter_map(|(position, value)| {
                (*value != predictor).then_some(Exception {
                    position: position as u64,
                    value: *value,
                })
            })
            .collect();
        let patched = Program {
            definitions: Vec::new(),
            root: file(
                Node::Exceptions {
                    base: Box::new(Node::Const {
                        length: length as u64,
                        value: predictor,
                    }),
                    exceptions,
                },
                length as u64,
            ),
        };
        assert_eq!(evaluate_program(&patched, &limits).unwrap(), input);
    }
}

#[test]
fn coordinate_nodes_restore_all_three_native_transform_families() {
    let limits = Limits::default();
    let input: Vec<u8> = (0..96)
        .map(|index| ((index * 37 + index / 3) & 0xff) as u8)
        .collect();
    let descriptors = [
        NativeCoordinateDescriptor {
            original_bytes: input.len() as u64,
            transform: CoordinateTransform::Stride {
                element_width: 2,
                channels: 3,
            },
        },
        NativeCoordinateDescriptor {
            original_bytes: input.len() as u64,
            transform: CoordinateTransform::BytePlane {
                word_width: 4,
                endian: CoordinateEndianness::Little,
            },
        },
        NativeCoordinateDescriptor {
            original_bytes: input.len() as u64,
            transform: CoordinateTransform::BytePlane {
                word_width: 4,
                endian: CoordinateEndianness::Big,
            },
        },
        NativeCoordinateDescriptor {
            original_bytes: input.len() as u64,
            transform: CoordinateTransform::BitPlane,
        },
    ];

    for descriptor in descriptors {
        let transformed = forward(&input, &descriptor, &limits).unwrap();
        let program = coordinate_program(descriptor, transformed.clone());
        let mut restored = Vec::new();
        let report = evaluate_program_to_sink(&program, &limits, |bytes| {
            restored.extend_from_slice(bytes);
            Ok(())
        })
        .unwrap();
        assert_eq!(restored, input);
        assert_eq!(report.temporary_bytes_peak, transformed.len() as u64);

        let sections = program.encode_sections(&limits).unwrap();
        let parsed =
            Program::decode_sections(0, &sections.definitions, &sections.root, &limits).unwrap();
        assert_eq!(evaluate_program(&parsed, &limits).unwrap(), input);
    }
}

#[test]
fn nested_coordinates_use_the_exact_additive_scratch_schedule() {
    let limits = Limits::default();
    let original: Vec<u8> = (0..12).collect();
    let outer = NativeCoordinateDescriptor {
        original_bytes: 12,
        transform: CoordinateTransform::Stride {
            element_width: 2,
            channels: 3,
        },
    };
    let outer_domain = forward(&original, &outer, &limits).unwrap();
    let inner = NativeCoordinateDescriptor {
        original_bytes: outer_domain.len() as u64,
        transform: CoordinateTransform::BytePlane {
            word_width: 2,
            endian: CoordinateEndianness::Little,
        },
    };
    let inner_domain = forward(&outer_domain, &inner, &limits).unwrap();
    let program = Program {
        definitions: Vec::new(),
        root: file(
            Node::Coordinate {
                descriptor: outer,
                child: Box::new(Node::Coordinate {
                    descriptor: inner,
                    child: Box::new(Node::Literal(inner_domain)),
                }),
            },
            12,
        ),
    };
    let static_report = program.validate(&limits).unwrap();
    assert_eq!(static_report.temporary_bytes, 24);
    let mut restored = Vec::new();
    let concrete_report = evaluate_program_to_sink(&program, &limits, |bytes| {
        restored.extend_from_slice(bytes);
        Ok(())
    })
    .unwrap();
    assert_eq!(restored, original);
    assert_eq!(
        concrete_report.temporary_bytes_peak,
        static_report.temporary_bytes
    );

    let tiny = Limits {
        max_temporary_bytes: 23,
        ..Limits::default()
    };
    assert!(matches!(
        evaluate_program(&program, &tiny),
        Err(Error::LimitExceeded {
            what: "temporary bytes",
            actual: 24,
            limit: 23
        })
    ));
}

#[test]
fn bit_plane_nonzero_high_padding_is_rejected_during_evaluation() {
    let limits = Limits::default();
    let input = vec![0x5a; 9];
    let descriptor = NativeCoordinateDescriptor {
        original_bytes: input.len() as u64,
        transform: CoordinateTransform::BitPlane,
    };
    let mut transformed = forward(&input, &descriptor, &limits).unwrap();
    transformed[1] |= 0x02;
    let program = coordinate_program(descriptor, transformed);
    program.validate(&limits).unwrap();
    assert_eq!(
        evaluate_program(&program, &limits),
        Err(Error::InvalidValue(
            "BIT_PLANE high padding bits must be zero"
        ))
    );
}

#[test]
fn randomized_bit_plane_dsl_roundtrips_include_every_padding_width() {
    let limits = Limits::default();
    let mut state = 0x243f_6a88_85a3_08d3u64;
    for length in 0..96usize {
        let mut input = Vec::with_capacity(length);
        for _ in 0..length {
            state = state
                .wrapping_mul(2_862_933_555_777_941_757)
                .wrapping_add(3_037_000_493);
            input.push((state >> 47) as u8);
        }
        let descriptor = NativeCoordinateDescriptor {
            original_bytes: length as u64,
            transform: CoordinateTransform::BitPlane,
        };
        let transformed = forward(&input, &descriptor, &limits).unwrap();
        let program = coordinate_program(descriptor, transformed);
        let sections = program.encode_sections(&limits).unwrap();
        let parsed =
            Program::decode_sections(0, &sections.definitions, &sections.root, &limits).unwrap();
        assert_eq!(evaluate_program(&parsed, &limits).unwrap(), input);
    }
}

#[test]
fn all_native_entropy_codecs_decode_directly_with_zero_scratch() {
    let limits = Limits::default();
    let samples = [
        (LeafCodec::Raw, b"native raw bytes".to_vec()),
        (LeafCodec::ByteRle, vec![0x5a; 1024]),
        (
            LeafCodec::ZeroRun,
            [vec![0; 300], b"entropy".to_vec(), vec![0; 500]].concat(),
        ),
        (
            LeafCodec::SparseZero,
            [vec![0; 700], vec![9], vec![0; 900], vec![4]].concat(),
        ),
        (
            LeafCodec::BitPack,
            (0..1025).map(|index| (index % 8) as u8).collect(),
        ),
        (
            LeafCodec::LzTokens,
            b"overlap copies and literal runs restore directly; ".repeat(80),
        ),
    ];

    for (codec, restored) in samples {
        let program = entropy_program(codec, &restored);
        let static_report = program.validate(&limits).unwrap();
        assert_eq!(static_report.temporary_bytes, 0);
        let sections = program.encode_sections(&limits).unwrap();
        let parsed =
            Program::decode_sections(0, &sections.definitions, &sections.root, &limits).unwrap();
        let mut output = Vec::new();
        let concrete = evaluate_program_to_sink(&parsed, &limits, |bytes| {
            output.extend_from_slice(bytes);
            Ok(())
        })
        .unwrap();
        assert_eq!(output, restored);
        assert_eq!(concrete.temporary_bytes_peak, 0);
    }
}

#[test]
fn malformed_entropy_is_rejected_before_sink_side_effects() {
    let program = Program {
        definitions: Vec::new(),
        root: file(Node::EntropyLiteral(vec![1, 4, 0, 1, 2, 1, 0x80]), 1),
    };
    let mut called = false;
    let result = evaluate_program_to_sink(&program, &Limits::default(), |_| {
        called = true;
        Ok(())
    });
    assert_eq!(
        result,
        Err(Error::InvalidValue("non-zero bit-pack padding"))
    );
    assert!(!called);
}
