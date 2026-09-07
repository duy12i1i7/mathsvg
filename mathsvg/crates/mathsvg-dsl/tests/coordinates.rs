use mathsvg_core::{Error, Limits};
use mathsvg_dsl::{
    CoordinateEndianness, CoordinateTransform, NativeCoordinateDescriptor, Node, Program, ValueType,
};

fn coordinate_program(descriptor: NativeCoordinateDescriptor, transformed: Vec<u8>) -> Program {
    Program {
        definitions: Vec::new(),
        root: Node::File {
            original_length: descriptor.original_bytes,
            child: Box::new(Node::Coordinate {
                descriptor,
                child: Box::new(Node::Literal(transformed)),
            }),
        },
    }
}

fn empty_stride() -> NativeCoordinateDescriptor {
    NativeCoordinateDescriptor {
        original_bytes: 0,
        transform: CoordinateTransform::Stride {
            element_width: 1,
            channels: 2,
        },
    }
}

fn empty_byte_plane() -> NativeCoordinateDescriptor {
    NativeCoordinateDescriptor {
        original_bytes: 0,
        transform: CoordinateTransform::BytePlane {
            word_width: 2,
            endian: CoordinateEndianness::Little,
        },
    }
}

fn empty_bit_plane() -> NativeCoordinateDescriptor {
    NativeCoordinateDescriptor {
        original_bytes: 0,
        transform: CoordinateTransform::BitPlane,
    }
}

#[test]
fn three_coordinate_opcodes_have_byte_exact_golden_records() {
    let limits = Limits::default();
    for (descriptor, expected) in [
        (
            empty_stride(),
            vec![
                0x20, 0x00, 0x0b, 0x00, 0x41, 0x00, 0x07, 0x00, 0x01, 0x02, 0x00, 0x00, 0x01, 0x00,
            ],
        ),
        (
            empty_byte_plane(),
            vec![
                0x20, 0x00, 0x0b, 0x00, 0x42, 0x00, 0x07, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x00,
            ],
        ),
        (
            empty_bit_plane(),
            vec![
                0x20, 0x00, 0x09, 0x00, 0x43, 0x00, 0x05, 0x00, 0x00, 0x00, 0x01, 0x00,
            ],
        ),
    ] {
        let program = coordinate_program(descriptor, Vec::new());
        let sections = program.encode_sections(&limits).unwrap();
        assert!(sections.definitions.is_empty());
        assert_eq!(sections.root, expected);
        assert_eq!(
            Program::decode_sections(0, &[], &sections.root, &limits).unwrap(),
            program
        );
    }
}

#[test]
fn coordinate_static_type_work_depth_and_scratch_are_exact() {
    let program = coordinate_program(
        NativeCoordinateDescriptor {
            original_bytes: 3,
            transform: CoordinateTransform::BitPlane,
        },
        vec![0; 8],
    );
    let report = program.validate(&Limits::default()).unwrap();
    assert_eq!(report.root_type, ValueType::Bytes(3));
    assert_eq!(report.original_bytes, 3);
    assert_eq!(report.node_count, 3);
    assert_eq!(report.edge_count, 2);
    assert_eq!(report.graph_depth, 3);
    assert_eq!(report.decode_work, 35);
    assert_eq!(report.generated_bytes, 8);
    assert_eq!(report.temporary_bytes, 8);
    assert_eq!(report.opcode_sequence, vec![0x20, 0x43, 0x00]);

    let tiny = Limits {
        max_temporary_bytes: 7,
        ..Limits::default()
    };
    assert!(matches!(
        program.validate(&tiny),
        Err(Error::LimitExceeded {
            what: "temporary bytes",
            actual: 8,
            limit: 7
        })
    ));

    let tiny_work = Limits {
        max_work: 34,
        ..Limits::default()
    };
    assert!(matches!(
        program.validate(&tiny_work),
        Err(Error::LimitExceeded {
            what: "work",
            actual: 35,
            limit: 34
        })
    ));

    let tiny_parameters = Limits {
        max_parameter_bytes: 2,
        ..Limits::default()
    };
    assert!(matches!(
        coordinate_program(empty_stride(), Vec::new()).validate(&tiny_parameters),
        Err(Error::LimitExceeded {
            what: "parameter bytes",
            actual: 3,
            limit: 2
        })
    ));
}

#[test]
fn coordinate_parser_rejects_flags_parameters_lengths_and_trailing_bytes() {
    let limits = Limits::default();
    let sections = coordinate_program(empty_stride(), Vec::new())
        .encode_sections(&limits)
        .unwrap();

    let mut flags = sections.root.clone();
    flags[5] = 1;
    assert!(matches!(
        Program::decode_sections(0, &[], &flags, &limits),
        Err(Error::InvalidFlags {
            opcode: 0x41,
            flags: 1
        })
    ));

    let mut width = sections.root.clone();
    width[8] = 5;
    assert!(matches!(
        Program::decode_sections(0, &[], &width, &limits),
        Err(Error::InvalidValue(_))
    ));

    let mut trailing = sections.root;
    trailing[2] += 1;
    trailing[6] += 1;
    trailing.push(0);
    assert!(matches!(
        Program::decode_sections(0, &[], &trailing, &limits),
        Err(Error::TrailingData {
            context: "node payload",
            ..
        })
    ));

    let wrong_child = coordinate_program(
        NativeCoordinateDescriptor {
            original_bytes: 3,
            transform: CoordinateTransform::BitPlane,
        },
        vec![0; 7],
    );
    assert!(matches!(
        wrong_child.validate(&limits),
        Err(Error::LengthMismatch {
            context: "coordinate transformed child",
            expected: 8,
            actual: 7
        })
    ));

    let identity_node = coordinate_program(NativeCoordinateDescriptor::identity(0), Vec::new());
    assert!(matches!(
        identity_node.validate(&limits),
        Err(Error::InvalidValue(
            "identity coordinate must use GROUP compatibility node"
        ))
    ));
}

#[test]
fn coordinate_parameters_participate_in_the_normative_tie_break() {
    let limits = Limits::default();
    let make = |endian| {
        coordinate_program(
            NativeCoordinateDescriptor {
                original_bytes: 4,
                transform: CoordinateTransform::BytePlane {
                    word_width: 2,
                    endian,
                },
            },
            vec![1, 2, 3, 4],
        )
    };
    let little = make(CoordinateEndianness::Little)
        .candidate_cost(1_000, 16, &limits)
        .unwrap();
    let big = make(CoordinateEndianness::Big)
        .candidate_cost(1_000, 16, &limits)
        .unwrap();
    assert_eq!(little.opcode_sequence, vec![0x20, 0x42, 0x00]);
    assert_ne!(little.parameter_payload, big.parameter_payload);
    assert!(little < big);
}
