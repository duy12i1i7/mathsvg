use mathsvg_core::{Error, Limits};
use mathsvg_dsl::{Node, Opcode, Program, ValueType};
use mathsvg_entropy::{encode, inspect, DecodeLimits, LeafCodec};

fn program(envelope: Vec<u8>, decoded_bytes: u64) -> Program {
    Program {
        definitions: Vec::new(),
        root: Node::File {
            original_length: decoded_bytes,
            child: Box::new(Node::EntropyLiteral(envelope)),
        },
    }
}

#[test]
fn entropy_literal_has_frozen_root_wire_bytes() {
    let envelope = encode(LeafCodec::Raw, b"abc").unwrap();
    assert_eq!(envelope, vec![1, 0, 0, 3, 3, b'a', b'b', b'c']);
    let sections = program(envelope, 3)
        .encode_sections(&Limits::default())
        .unwrap();
    assert!(sections.definitions.is_empty());
    assert_eq!(
        sections.root,
        vec![
            0x20, 0x00, 0x0c, 0x03, 0x13, 0x00, 0x08, 0x01, 0x00, 0x00, 0x03, 0x03, b'a', b'b',
            b'c',
        ]
    );
}

#[test]
fn all_native_codecs_round_trip_through_dsl_sections() {
    let samples = [
        (LeafCodec::Raw, b"raw bytes".to_vec()),
        (LeafCodec::ByteRle, vec![7; 300]),
        (
            LeafCodec::ZeroRun,
            [vec![0; 80], b"abc".to_vec(), vec![0; 90]].concat(),
        ),
        (
            LeafCodec::SparseZero,
            [vec![0; 100], vec![9], vec![0; 120], vec![3]].concat(),
        ),
        (
            LeafCodec::BitPack,
            (0..257).map(|index| (index % 4) as u8).collect(),
        ),
        (
            LeafCodec::LzTokens,
            b"native LZ text leaf with repeated words; ".repeat(40),
        ),
    ];

    for (codec, bytes) in samples {
        let original = program(encode(codec, &bytes).unwrap(), bytes.len() as u64);
        let sections = original.encode_sections(&Limits::default()).unwrap();
        let decoded =
            Program::decode_sections(0, &sections.definitions, &sections.root, &Limits::default())
                .unwrap();
        assert_eq!(decoded, original);
        assert_eq!(
            decoded
                .validate(&Limits::default())
                .unwrap()
                .opcode_sequence,
            vec![Opcode::File.byte(), Opcode::EntropyLiteral.byte()]
        );
    }
}

#[test]
fn preflight_uses_inspected_length_work_and_zero_scratch() {
    let bytes = vec![5; 100];
    let envelope = encode(LeafCodec::ByteRle, &bytes).unwrap();
    let metadata = inspect(&envelope, DecodeLimits::default()).unwrap();
    let candidate = program(envelope.clone(), bytes.len() as u64);
    let report = candidate.validate(&Limits::default()).unwrap();
    assert_eq!(report.root_type, ValueType::Bytes(100));
    assert_eq!(report.original_bytes, 100);
    assert_eq!(report.node_count, 2);
    assert_eq!(report.edge_count, 1);
    assert_eq!(report.graph_depth, 2);
    assert_eq!(report.decode_work, metadata.decode_work + 2);
    assert_eq!(report.generated_bytes, 100);
    assert_eq!(report.temporary_bytes, 0);

    let work_limited = Limits {
        max_work: report.decode_work - 1,
        ..Limits::default()
    };
    assert!(matches!(
        candidate.validate(&work_limited),
        Err(Error::LimitExceeded { what: "work", .. })
    ));

    let payload_limited = Limits {
        max_node_payload_bytes: envelope.len() as u64 - 1,
        ..Limits::default()
    };
    assert!(matches!(
        candidate.validate(&payload_limited),
        Err(Error::LimitExceeded {
            what: "encoded bytes",
            ..
        })
    ));
}

#[test]
fn malformed_noncanonical_and_truncated_envelopes_are_rejected() {
    let cases = [
        vec![],
        vec![2, 0, 0, 0, 0],
        vec![1, 0x80, 0, 0, 0],
        vec![1, 0, 1, 0, 0],
        vec![1, 0, 0, 3, 3, b'a', b'b'],
        vec![1, 1, 0, 2, 5, 2, 1, b'x', 1, b'x'],
        vec![1, 4, 0, 1, 2, 1, 0x80],
    ];
    for envelope in cases {
        assert!(program(envelope, 0).validate(&Limits::default()).is_err());
    }

    let mut sections = program(encode(LeafCodec::Raw, b"abc").unwrap(), 3)
        .encode_sections(&Limits::default())
        .unwrap();
    // FILE header (3 bytes), FILE length (1), leaf header (3), then the
    // native envelope. Envelope byte two is its flags field.
    sections.root[9] = 1;
    assert_eq!(
        Program::decode_sections(0, &[], &sections.root, &Limits::default()),
        Err(Error::InvalidValue("invalid native entropy leaf flags"))
    );
}
