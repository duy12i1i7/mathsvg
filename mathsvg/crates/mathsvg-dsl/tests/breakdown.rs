use mathsvg_core::Limits;
use mathsvg_dsl::{CorrectionKind, Node, Program};
use mathsvg_entropy::{encode, LeafCodec};

#[test]
fn wire_categories_are_exact_and_reconstruction_is_conservative() {
    let coded = encode(LeafCodec::ByteRle, &[1, 1, 1, 1]).unwrap();
    let program = Program {
        definitions: vec![Node::Literal(b"xy".to_vec())],
        root: Node::File {
            original_length: 13,
            child: Box::new(Node::Concat(vec![
                Node::Reference {
                    definition: 0,
                    parameter_delta: Vec::new(),
                },
                Node::Reference {
                    definition: 0,
                    parameter_delta: Vec::new(),
                },
                Node::Correct {
                    kind: CorrectionKind::Add,
                    prediction: Box::new(Node::Const {
                        length: 4,
                        value: 7,
                    }),
                    correction: Box::new(Node::EntropyLiteral(coded)),
                },
                Node::Periodic {
                    pattern: b"ab".to_vec(),
                    repetitions: 2,
                    suffix: b"c".to_vec(),
                },
            ])),
        },
    };
    let limits = Limits::default();
    let sections = program.encode_sections(&limits).unwrap();
    let breakdown = program.procedural_breakdown(&limits).unwrap();

    assert_eq!(
        breakdown.wire_bytes().unwrap(),
        sections.dsl_bytes().unwrap()
    );
    assert_eq!(
        breakdown.function_reconstructed_bytes + breakdown.literal_reconstructed_bytes,
        13
    );
    assert_eq!(breakdown.function_reconstructed_bytes, 5);
    assert_eq!(breakdown.literal_reconstructed_bytes, 8);
    assert_eq!(breakdown.shared_node_count, 1);
    assert_eq!(breakdown.residual_root_count, 1);
    assert_eq!(breakdown.residual_depth_sum, 1);
    assert!(breakdown.shared_definition_bytes > 0);
    assert!(breakdown.reference_bytes > 0);
    assert!(breakdown.parameter_bytes > 0);
    assert!(breakdown.residual_layer_bytes > 0);
    assert!(breakdown.literal_leaf_bytes > 0);
    assert!(breakdown.entropy_metadata_bytes > 0);
    assert!(breakdown.function_graph_bytes > 0);

    let expanded = program.without_entropy_literals(&limits).unwrap();
    let expanded_breakdown = expanded.procedural_breakdown(&limits).unwrap();
    assert_eq!(expanded_breakdown.entropy_metadata_bytes, 0);
    assert_eq!(
        expanded_breakdown.function_reconstructed_bytes,
        breakdown.function_reconstructed_bytes
    );
    assert_eq!(
        expanded_breakdown.literal_reconstructed_bytes,
        breakdown.literal_reconstructed_bytes
    );
}

#[test]
fn nested_corrections_report_one_root_and_exact_maximum_depth() {
    let inner = Node::Correct {
        kind: CorrectionKind::Xor,
        prediction: Box::new(Node::Const {
            length: 8,
            value: 3,
        }),
        correction: Box::new(Node::Const {
            length: 8,
            value: 1,
        }),
    };
    let program = Program {
        definitions: Vec::new(),
        root: Node::File {
            original_length: 8,
            child: Box::new(Node::Correct {
                kind: CorrectionKind::Add,
                prediction: Box::new(inner),
                correction: Box::new(Node::Const {
                    length: 8,
                    value: 2,
                }),
            }),
        },
    };
    let breakdown = program.procedural_breakdown(&Limits::default()).unwrap();
    assert_eq!(breakdown.residual_root_count, 1);
    assert_eq!(breakdown.residual_depth_sum, 2);
    assert_eq!(breakdown.function_reconstructed_bytes, 8);
    assert_eq!(breakdown.literal_reconstructed_bytes, 0);
}
