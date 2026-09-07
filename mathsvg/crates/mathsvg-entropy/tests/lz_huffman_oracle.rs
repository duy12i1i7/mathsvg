use std::path::PathBuf;

use mathsvg_entropy::{count_candidate, decode, encode, encode_best, DecodeLimits, LeafCodec};

fn read_u64(input: &[u8], position: &mut usize) -> u64 {
    let mut value = 0u64;
    for shift in (0..=63).step_by(7) {
        let byte = input[*position];
        *position += 1;
        value |= u64::from(byte & 0x7f) << shift;
        if byte & 0x80 == 0 {
            return value;
        }
    }
    panic!("oracle received invalid canonical envelope");
}

fn payload(envelope: &[u8]) -> &[u8] {
    let mut position = 3;
    let _decoded_length = read_u64(envelope, &mut position);
    let payload_length = read_u64(envelope, &mut position) as usize;
    assert_eq!(position + payload_length, envelope.len());
    &envelope[position..]
}

fn varint_length(mut value: usize) -> usize {
    let mut length = 1;
    while value >= 0x80 {
        value >>= 7;
        length += 1;
    }
    length
}

fn composed_envelope_size(decoded_length: usize, lz_payload: &[u8]) -> usize {
    let huffman_envelope = encode(LeafCodec::CanonicalHuffman, lz_payload).unwrap();
    let huffman_payload = payload(&huffman_envelope);
    let composed_payload_length = varint_length(lz_payload.len()) + huffman_payload.len();
    3 + varint_length(decoded_length)
        + varint_length(composed_payload_length)
        + composed_payload_length
}

#[test]
#[ignore = "development-only oracle; opens only the listed development corpus files"]
fn report_complete_lz_huffman_envelope_sizes() {
    let repository = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let samples = [
        ("real/alice", "datasets/data/canterbury/alice29.txt"),
        ("real/kennedy", "datasets/data/canterbury/kennedy.xls"),
        ("real/calgary-progc", "datasets/data/calgary/progc"),
        ("real/silesia-x-ray", "datasets/data/silesia/x-ray"),
        (
            "synthetic/constant",
            "datasets/synthetic/constant_00/s0000065536_n000p000_seed1297748005.bin",
        ),
        (
            "synthetic/lfsr8",
            "datasets/synthetic/lfsr8/s0000065536_n000p000_seed1297748005.bin",
        ),
        (
            "synthetic/linear",
            "datasets/synthetic/linear/s0000065536_n000p000_seed1297748005.bin",
        ),
        (
            "synthetic/periodic",
            "datasets/synthetic/periodic/s0000065536_n000p000_seed1297748005.bin",
        ),
        (
            "synthetic/piecewise-mixed",
            "datasets/synthetic/piecewise_mixed/s0000065536_n000p000_seed1297748005.bin",
        ),
        (
            "synthetic/polynomial-d2",
            "datasets/synthetic/polynomial_d2/s0000065536_n000p000_seed1297748005.bin",
        ),
        (
            "synthetic/random",
            "datasets/synthetic/random/s0000065536_n000p000_seed1297748005.bin",
        ),
        (
            "synthetic/recurrence",
            "datasets/synthetic/recurrence/s0000065536_n000p000_seed1297748005.bin",
        ),
    ];

    eprintln!(
        "sample,input,best_codec,best_bytes,lz_bytes,lz_payload_bytes,composed_bytes,gain_percent"
    );
    for (name, relative_path) in samples {
        let input = std::fs::read(repository.join(relative_path)).unwrap();
        let (previous_score, previous_codec) = LeafCodec::ALL
            .into_iter()
            .filter(|codec| *codec != LeafCodec::LzHuffman)
            .map(|codec| (count_candidate(codec, &input).unwrap(), codec))
            .min()
            .unwrap();
        let previous = encode(previous_codec, &input).unwrap();
        assert_eq!(previous.len() as u64, previous_score.encoded_bytes);
        let lz_envelope = encode(LeafCodec::LzTokens, &input).unwrap();
        let lz_payload = payload(&lz_envelope);
        let composed = composed_envelope_size(input.len(), lz_payload);
        let implemented = encode(LeafCodec::LzHuffman, &input).unwrap();
        assert_eq!(implemented.len(), composed);
        assert_eq!(
            decode(&implemented, DecodeLimits::default()).unwrap().bytes,
            input
        );
        let current = encode_best(&input).unwrap();
        assert!(current.bytes.len() <= previous.len());
        let gain_percent =
            100.0 * (previous.len() as f64 - composed as f64) / previous.len() as f64;
        eprintln!(
            "{name},{},{:?},{},{},{},{},{gain_percent:.6}",
            input.len(),
            previous_codec,
            previous.len(),
            lz_envelope.len(),
            lz_payload.len(),
            composed,
        );
    }
}
