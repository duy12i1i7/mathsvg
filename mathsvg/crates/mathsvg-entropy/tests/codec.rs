use mathsvg_entropy::{
    count_candidate, count_lz_huffman_policy, decode, decode_exact, decode_exact_into,
    decode_exact_into_uncommitted, decode_into, encode, encode_best, encode_best_audited,
    encode_lz_huffman_policy, encode_lz_huffman_policy_if_better, encoded_size, inspect,
    inspect_exact, preflight_exact, DecodeLimits, Error, LeafCodec, LzParserPolicy, FORMAT_VERSION,
};

fn put_u64(output: &mut Vec<u8>, mut value: u64) {
    while value >= 0x80 {
        output.push(((value as u8) & 0x7f) | 0x80);
        value >>= 7;
    }
    output.push(value as u8);
}

fn take_u64(input: &[u8], position: &mut usize) -> u64 {
    let mut value = 0u64;
    for shift in (0..=63).step_by(7) {
        let byte = input[*position];
        *position += 1;
        value |= u64::from(byte & 0x7f) << shift;
        if byte & 0x80 == 0 {
            return value;
        }
    }
    panic!("test helper received invalid varint");
}

fn envelope(codec: u8, decoded: u64, payload: &[u8]) -> Vec<u8> {
    let mut output = vec![FORMAT_VERSION, codec, 0];
    put_u64(&mut output, decoded);
    put_u64(&mut output, payload.len() as u64);
    output.extend_from_slice(payload);
    output
}

fn leaf_payload(envelope: &[u8]) -> &[u8] {
    let mut position = 3;
    let _decoded = take_u64(envelope, &mut position);
    let payload_length = take_u64(envelope, &mut position) as usize;
    assert_eq!(position + payload_length, envelope.len());
    &envelope[position..]
}

fn lz_huffman_from_tokens(decoded: u64, tokens: &[u8]) -> Vec<u8> {
    let huffman = encode(LeafCodec::CanonicalHuffman, tokens).unwrap();
    let mut payload = Vec::new();
    put_u64(&mut payload, tokens.len() as u64);
    payload.extend_from_slice(leaf_payload(&huffman));
    envelope(LeafCodec::LzHuffman.opcode(), decoded, &payload)
}

fn assert_inspect_and_decode_reject(bytes: &[u8]) {
    assert!(inspect(bytes, DecodeLimits::default()).is_err());
    assert!(decode(bytes, DecodeLimits::default()).is_err());
}

fn next_random(state: &mut u64) -> u64 {
    *state = state
        .wrapping_mul(6_364_136_223_846_793_005)
        .wrapping_add(1_442_695_040_888_963_407);
    *state
}

#[test]
fn golden_wire_bytes_are_stable() {
    assert_eq!(
        encode(LeafCodec::Raw, b"abc").unwrap(),
        vec![1, 0, 0, 3, 3, b'a', b'b', b'c']
    );
    assert_eq!(
        encode(LeafCodec::ByteRle, b"aaabb").unwrap(),
        vec![1, 1, 0, 5, 5, 2, 3, b'a', 2, b'b']
    );
    assert_eq!(
        encode(LeafCodec::SparseZero, &[0, 0, 0, 5]).unwrap(),
        vec![1, 3, 0, 4, 3, 1, 4, 5]
    );
    assert_eq!(
        encode(LeafCodec::BitPack, &[0, 0, 0, 0]).unwrap(),
        vec![1, 4, 0, 4, 1, 0]
    );
    assert_eq!(
        encode(LeafCodec::LzTokens, b"abcabcabcXabcabcabc").unwrap(),
        vec![1, 5, 0, 19, 14, 3, b'a', b'b', b'c', 2, 3, 1, b'X', 2, 7, 3, b'a', b'b', b'c',]
    );
    assert_eq!(
        encode(LeafCodec::CanonicalHuffman, b"aaabbc").unwrap(),
        vec![1, 6, 0, 6, 10, 3, b'a', 1, b'b', 2, b'c', 2, 9, 0x15, 0x80,]
    );
    assert_eq!(
        encode(LeafCodec::LzHuffman, b"aaabbc").unwrap(),
        vec![1, 7, 0, 6, 13, 7, 4, 6, 3, b'a', 1, b'b', 2, b'c', 3, 13, 0xc2, 0xb8,]
    );
}

#[test]
fn every_codec_round_trips_deterministic_patterns() {
    let mut samples = vec![
        Vec::new(),
        vec![0],
        vec![255],
        vec![0; 4096],
        vec![7; 4096],
        (0u8..=255).collect(),
        [vec![0; 200], vec![1, 2, 3], vec![0; 300]].concat(),
        (0..4096).map(|index| (index % 4) as u8).collect(),
    ];

    let mut state = 0x4d59_5df4_d0f3_3173;
    for length in [2, 3, 7, 8, 9, 31, 127, 128, 129, 511, 1024] {
        let mut bytes = Vec::with_capacity(length);
        for _ in 0..length {
            bytes.push(next_random(&mut state) as u8);
        }
        samples.push(bytes);
    }

    for sample in samples {
        for codec in LeafCodec::ALL {
            let encoded = encode(codec, &sample).unwrap();
            assert_eq!(encoded.len(), encoded_size(codec, &sample).unwrap());
            assert_eq!(
                encoded.len() as u64,
                count_candidate(codec, &sample).unwrap().encoded_bytes
            );
            let restored =
                decode_exact(&encoded, sample.len() as u64, DecodeLimits::default()).unwrap();
            assert_eq!(restored.codec, codec);
            assert_eq!(restored.bytes, sample);
            let metadata =
                inspect_exact(&encoded, sample.len() as u64, DecodeLimits::default()).unwrap();
            assert_eq!(metadata.codec, codec);
            assert_eq!(metadata.encoded_bytes, encoded.len() as u64);
            assert_eq!(metadata.decoded_bytes, sample.len() as u64);
            assert_eq!(metadata.temporary_bytes, 0);
            assert_eq!(
                metadata.decode_work,
                count_candidate(codec, &sample).unwrap().decode_work
            );

            let mut direct = vec![0xa5; sample.len()];
            assert_eq!(
                decode_exact_into(
                    &encoded,
                    sample.len() as u64,
                    &mut direct,
                    DecodeLimits::default(),
                )
                .unwrap(),
                metadata
            );
            assert_eq!(direct, sample);

            let preflight =
                preflight_exact(&encoded, sample.len() as u64, DecodeLimits::default()).unwrap();
            assert_eq!(preflight, metadata);
            let mut uncommitted = vec![0xa5; sample.len()];
            assert_eq!(
                decode_exact_into_uncommitted(
                    &encoded,
                    sample.len() as u64,
                    &mut uncommitted,
                    DecodeLimits::default(),
                )
                .unwrap(),
                metadata
            );
            assert_eq!(uncommitted, sample);
            assert_eq!(encode(codec, &sample).unwrap(), encoded);
        }
    }
}

#[test]
fn resource_preflight_is_explicitly_weaker_than_strict_inspection() {
    // The outer envelope is well formed, but the RLE payload stores adjacent
    // equal runs and is therefore non-canonical.
    let malformed = vec![1, 1, 0, 2, 5, 2, 1, b'x', 1, b'x'];
    let metadata = preflight_exact(&malformed, 2, DecodeLimits::default()).unwrap();
    assert_eq!(metadata.decoded_bytes, 2);
    assert!(inspect_exact(&malformed, 2, DecodeLimits::default()).is_err());

    let mut private = vec![0xa5; 2];
    assert!(
        decode_exact_into_uncommitted(&malformed, 2, &mut private, DecodeLimits::default(),)
            .is_err()
    );
    // The uncommitted API is allowed to expose partial writes only inside the
    // caller's private buffer; strict public decode APIs remain atomic.
    assert_eq!(private, [b'x', 0xa5]);
}

#[test]
fn inspect_and_decode_into_reject_before_materialisation() {
    let valid = encode(LeafCodec::ByteRle, &[9; 32]).unwrap();
    let metadata = inspect(&valid, DecodeLimits::default()).unwrap();
    let mut direct = vec![0; metadata.decoded_bytes as usize];
    assert_eq!(
        decode_into(&valid, &mut direct, DecodeLimits::default()).unwrap(),
        metadata
    );
    assert_eq!(direct, vec![9; 32]);

    let mut malformed = valid;
    malformed.push(0);
    let mut untouched = vec![0xa5; 32];
    assert!(inspect(&malformed, DecodeLimits::default()).is_err());
    assert!(decode_into(&malformed, &mut untouched, DecodeLimits::default()).is_err());
    assert_eq!(untouched, vec![0xa5; 32]);

    let valid = encode(LeafCodec::Raw, b"abc").unwrap();
    let mut wrong_length = [0xa5; 2];
    assert!(matches!(
        decode_into(&valid, &mut wrong_length, DecodeLimits::default()),
        Err(Error::LengthMismatch {
            context: "decoded destination length",
            ..
        })
    ));
    assert_eq!(wrong_length, [0xa5; 2]);
}

#[test]
fn encode_best_is_exact_deterministic_and_raw_bounded() {
    let mut state = 0x9e37_79b9_7f4a_7c15;
    let mut samples = vec![
        vec![0; 10_000],
        vec![42; 10_000],
        (0..10_000).map(|value| (value % 4) as u8).collect(),
    ];
    for length in 0..512 {
        let mut sample = Vec::with_capacity(length);
        for _ in 0..length {
            sample.push(next_random(&mut state) as u8);
        }
        samples.push(sample);
    }

    for sample in samples {
        let best = encode_best(&sample).unwrap();
        let (repeated, audit) = encode_best_audited(&sample).unwrap();
        let raw = encode(LeafCodec::Raw, &sample).unwrap();
        assert_eq!(best, repeated);
        assert_eq!(audit.g1_preparation_walks, 1);
        assert_eq!(
            audit.g1_emission_walks,
            u64::from(matches!(
                best.codec,
                LeafCodec::LzTokens | LeafCodec::LzHuffman
            ))
        );
        assert_eq!(
            audit.g1_parser_walks,
            audit.g1_preparation_walks + audit.g1_emission_walks
        );
        assert!(audit.g1_parser_walks <= 2);
        assert!(audit.g1_token_bytes <= sample.len() as u64 * 2);
        assert!(best.bytes.len() <= raw.len());
        assert_eq!(best.bytes.len() as u64, best.score.encoded_bytes);
        assert_eq!(best.bytes, encode(best.codec, &sample).unwrap());
        assert_eq!(
            decode(&best.bytes, DecodeLimits::default()).unwrap().bytes,
            sample
        );

        let expected = LeafCodec::ALL
            .into_iter()
            .map(|codec| (count_candidate(codec, &sample).unwrap(), codec))
            .min()
            .unwrap();
        assert_eq!((best.score, best.codec), expected);
    }
}

#[test]
fn representative_families_choose_native_winners() {
    assert_eq!(
        encode_best(&vec![5; 1000]).unwrap().codec,
        LeafCodec::ByteRle
    );
    assert_eq!(
        encode_best(&[vec![0; 1000], vec![8, 9], vec![0; 1000]].concat())
            .unwrap()
            .codec,
        LeafCodec::SparseZero
    );
    let mut state = 0xa409_3822_299f_31d0u64;
    let bit_packed: Vec<u8> = (0..2000)
        .map(|_| (next_random(&mut state) >> 62) as u8)
        .collect();
    assert_eq!(encode_best(&bit_packed).unwrap().codec, LeafCodec::BitPack);
    assert_eq!(
        encode_best(&b"the quick brown fox jumps over the lazy dog; ".repeat(200))
            .unwrap()
            .codec,
        LeafCodec::LzTokens
    );
}

#[test]
fn envelope_rejects_versions_opcodes_flags_lengths_and_varints() {
    assert!(matches!(
        decode(&[], DecodeLimits::default()),
        Err(Error::Truncated { .. })
    ));

    let mut invalid = encode(LeafCodec::Raw, b"x").unwrap();
    invalid[0] = 2;
    assert_eq!(
        decode(&invalid, DecodeLimits::default()),
        Err(Error::UnsupportedVersion(2))
    );

    invalid[0] = 1;
    invalid[1] = 0x80;
    assert_eq!(
        decode(&invalid, DecodeLimits::default()),
        Err(Error::InvalidOpcode(0x80))
    );

    invalid[1] = 0;
    invalid[2] = 1;
    assert_eq!(
        decode(&invalid, DecodeLimits::default()),
        Err(Error::InvalidFlags(1))
    );

    let overlong_length = vec![1, 0, 0, 0x80, 0, 0];
    assert!(matches!(
        decode(&overlong_length, DecodeLimits::default()),
        Err(Error::InvalidVarint { position: 3 })
    ));

    let overflow_length = vec![
        1, 0, 0, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 2,
    ];
    assert!(matches!(
        decode(&overflow_length, DecodeLimits::default()),
        Err(Error::InvalidVarint { position: 3 })
    ));

    let valid = encode(LeafCodec::Raw, b"abcdef").unwrap();
    for end in 0..valid.len() {
        assert_inspect_and_decode_reject(&valid[..end]);
    }
    let mut trailing = valid.clone();
    trailing.push(0);
    assert!(matches!(
        decode(&trailing, DecodeLimits::default()),
        Err(Error::TrailingData {
            context: "leaf envelope",
            remaining: 1
        })
    ));

    assert!(matches!(
        decode_exact(&valid, 5, DecodeLimits::default()),
        Err(Error::LengthMismatch {
            context: "expected decoded leaf length",
            ..
        })
    ));
}

#[test]
fn raw_and_rle_malformed_payloads_are_rejected() {
    assert_inspect_and_decode_reject(&envelope(0, 2, b"x"));
    assert!(matches!(
        decode(&envelope(0, 2, b"x"), DecodeLimits::default()),
        Err(Error::LengthMismatch {
            context: "raw payload",
            ..
        })
    ));

    let cases = [
        envelope(1, 1, &[0]),
        envelope(1, 1, &[1, 0, b'x']),
        envelope(1, 2, &[2, 1, b'x', 1, b'x']),
        envelope(1, 1, &[2, 1, b'x', 1, b'y']),
        envelope(1, 2, &[1, 1, b'x', 0]),
    ];
    for bytes in cases {
        assert_inspect_and_decode_reject(&bytes);
    }

    assert!(matches!(
        decode(
            &envelope(1, 2, &[2, 1, b'x', 1, b'x']),
            DecodeLimits::default()
        ),
        Err(Error::NonCanonical("adjacent equal RLE runs"))
    ));
}

#[test]
fn zero_run_malformed_payloads_are_rejected() {
    let cases = [
        envelope(2, 1, &[0]),
        envelope(2, 1, &[1, 2, 1]),
        envelope(2, 1, &[1, 0, 0]),
        envelope(2, 1, &[1, 1, 1, 0]),
        envelope(2, 2, &[2, 0, 1, 0, 1]),
        envelope(2, 1, &[1, 1, 1, 7, 0]),
    ];
    for bytes in cases {
        assert_inspect_and_decode_reject(&bytes);
    }
}

#[test]
fn sparse_malformed_payloads_are_rejected() {
    let cases = [
        envelope(3, 1, &[2]),
        envelope(3, 1, &[1, 0, 7]),
        envelope(3, 1, &[1, 1, 0]),
        envelope(3, 1, &[1, 2, 7]),
        envelope(3, 2, &[1, 1, 7, 0]),
    ];
    for bytes in cases {
        assert_inspect_and_decode_reject(&bytes);
    }
}

#[test]
fn bit_pack_malformed_payloads_are_rejected() {
    let cases = [
        envelope(4, 1, &[9, 0]),
        envelope(4, 1, &[1]),
        envelope(4, 1, &[1, 0x80]),
        envelope(4, 1, &[2, 1]),
        envelope(4, 0, &[1]),
        envelope(4, 8, &[1, 0, 0]),
    ];
    for bytes in cases {
        assert_inspect_and_decode_reject(&bytes);
    }
}

#[test]
fn lz_tokens_support_overlap_and_reject_malformed_sequences() {
    let repeated = vec![b'a'; 4096];
    let encoded = encode(LeafCodec::LzTokens, &repeated).unwrap();
    assert_eq!(
        decode(&encoded, DecodeLimits::default()).unwrap().bytes,
        repeated
    );

    let cases = [
        envelope(5, 0, &[0]),
        envelope(5, 1, &[]),
        envelope(5, 2, &[2, b'a']),
        envelope(5, 5, &[1, b'a', 0, 0]),
        envelope(5, 5, &[1, b'a', 0, 2]),
        envelope(5, 5, &[1, b'a', 0, 0x80, 0x80, 0x04]),
        envelope(5, 4, &[1, b'a', 0, 1]),
        envelope(5, 5, &[1, b'a', 0, 1, 0]),
    ];
    for bytes in cases {
        assert_inspect_and_decode_reject(&bytes);
    }

    let malformed = envelope(5, 5, &[1, b'a', 0, 0]);
    let mut untouched = [0xa5; 5];
    assert!(decode_into(&malformed, &mut untouched, DecodeLimits::default()).is_err());
    assert_eq!(untouched, [0xa5; 5]);
}

#[test]
fn canonical_huffman_round_trips_required_families_and_counts_exactly() {
    let mut state = 0x243f_6a88_85a3_08d3u64;
    let random: Vec<u8> = (0..8192).map(|_| next_random(&mut state) as u8).collect();
    let skewed: Vec<u8> = (0..8192)
        .map(|_| {
            let value = next_random(&mut state) as u8;
            match value {
                0..=191 => b'a',
                192..=223 => b'b',
                224..=239 => b'c',
                other => other,
            }
        })
        .collect();
    assert!(
        encode(LeafCodec::CanonicalHuffman, &skewed).unwrap().len()
            < encode(LeafCodec::Raw, &skewed).unwrap().len()
    );
    let samples = [
        Vec::new(),
        vec![0x5a; 4096],
        random,
        skewed,
        (0u8..=255).collect(),
    ];

    for sample in samples {
        let counted = count_candidate(LeafCodec::CanonicalHuffman, &sample).unwrap();
        let encoded = encode(LeafCodec::CanonicalHuffman, &sample).unwrap();
        assert_eq!(counted.encoded_bytes, encoded.len() as u64);
        assert_eq!(
            encoded_size(LeafCodec::CanonicalHuffman, &sample).unwrap(),
            encoded.len()
        );
        assert_eq!(
            encode(LeafCodec::CanonicalHuffman, &sample).unwrap(),
            encoded
        );
        let metadata = inspect(&encoded, DecodeLimits::default()).unwrap();
        assert_eq!(metadata.codec, LeafCodec::CanonicalHuffman);
        assert_eq!(metadata.decode_work, counted.decode_work);
        assert_eq!(metadata.temporary_bytes, 0);
        assert_eq!(
            decode(&encoded, DecodeLimits::default()).unwrap().bytes,
            sample
        );
    }
}

#[test]
fn canonical_huffman_decode_work_is_exact_and_preflight_bounded() {
    let sample: Vec<u8> = (0..4096)
        .map(|index| if index % 5 == 0 { b'b' } else { b'a' })
        .collect();
    let encoded = encode(LeafCodec::CanonicalHuffman, &sample).unwrap();
    let counted = count_candidate(LeafCodec::CanonicalHuffman, &sample).unwrap();
    let metadata = inspect(&encoded, DecodeLimits::default()).unwrap();
    assert_eq!(metadata.decode_work, counted.decode_work);
    assert_eq!(
        metadata.decode_work,
        metadata.decoded_bytes + 8 * metadata.payload_bytes + 131_072
    );

    let limits = DecodeLimits {
        max_work: metadata.decode_work - 1,
        ..DecodeLimits::default()
    };
    assert!(matches!(
        inspect(&encoded, limits),
        Err(Error::LimitExceeded {
            what: "decode work",
            ..
        })
    ));
}

#[test]
fn canonical_huffman_rejects_malformed_tables_bits_padding_and_lengths() {
    let cases = [
        // Non-empty table on an empty output.
        envelope(6, 0, &[1, b'a', 0, 0]),
        // Singleton encodings use a zero-length code and no bits.
        envelope(6, 1, &[1, b'a', 1, 0]),
        envelope(6, 1, &[1, b'a', 0, 1, 0]),
        // Non-empty output needs at least one symbol.
        envelope(6, 2, &[0, 0]),
        // Table symbols must be unique and increasing.
        envelope(6, 2, &[2, b'a', 1, b'a', 1, 2, 0x40]),
        envelope(6, 2, &[2, b'b', 1, b'a', 1, 2, 0x40]),
        // Multi-symbol tables may not use a zero-length code.
        envelope(6, 2, &[2, b'a', 0, b'b', 1, 1, 0]),
        // Three one-bit codes oversubscribe the binary prefix space.
        envelope(6, 3, &[3, b'a', 1, b'b', 1, b'c', 1, 3, 0]),
        // Two two-bit codes leave the prefix space incomplete.
        envelope(6, 2, &[2, b'a', 2, b'b', 2, 4, 0]),
        // The six unused bits in the final byte must be zero.
        envelope(6, 2, &[2, b'a', 1, b'b', 1, 2, 0x41]),
        // Nine declared bits require two packed bytes.
        envelope(6, 2, &[2, b'a', 1, b'b', 1, 9, 0]),
        // Too few or too many complete codes for the declared output.
        envelope(6, 2, &[2, b'a', 1, b'b', 1, 1, 0]),
        envelope(6, 2, &[2, b'a', 1, b'b', 1, 3, 0x40]),
        // A declared code-table symbol must occur in the output.
        envelope(6, 2, &[2, b'a', 1, b'b', 1, 2, 0]),
        // Packed bytes beyond ceil(bit_length / 8) are trailing data.
        envelope(6, 2, &[2, b'a', 1, b'b', 1, 2, 0x40, 0]),
    ];
    for bytes in cases {
        assert_inspect_and_decode_reject(&bytes);
    }

    let malformed = envelope(6, 2, &[2, b'a', 1, b'b', 1, 1, 0]);
    let mut untouched = [0xa5; 2];
    assert!(decode_into(&malformed, &mut untouched, DecodeLimits::default()).is_err());
    assert_eq!(untouched, [0xa5; 2]);
}

#[test]
fn lz_huffman_streams_tokens_without_scratch_and_rejects_malformed_inputs() {
    let repeated = b"canonical LZ plus Huffman; ".repeat(4096);
    let encoded = encode(LeafCodec::LzHuffman, &repeated).unwrap();
    assert_eq!(
        encoded.len(),
        encoded_size(LeafCodec::LzHuffman, &repeated).unwrap()
    );
    let metadata = inspect(&encoded, DecodeLimits::default()).unwrap();
    assert_eq!(metadata.codec, LeafCodec::LzHuffman);
    assert_eq!(metadata.temporary_bytes, 0);
    assert_eq!(
        metadata.decode_work,
        count_candidate(LeafCodec::LzHuffman, &repeated)
            .unwrap()
            .decode_work
    );
    assert_eq!(
        decode(&encoded, DecodeLimits::default()).unwrap().bytes,
        repeated
    );

    let cases = [
        lz_huffman_from_tokens(0, &[0]),
        lz_huffman_from_tokens(1, &[]),
        lz_huffman_from_tokens(2, &[2, b'a']),
        lz_huffman_from_tokens(5, &[1, b'a', 0, 0]),
        lz_huffman_from_tokens(5, &[1, b'a', 0, 2]),
        lz_huffman_from_tokens(4, &[1, b'a', 0, 1]),
        lz_huffman_from_tokens(5, &[1, b'a', 0, 1, 0]),
        // The declared stream exceeds the proven 2*decoded token bound.
        {
            let mut payload = vec![3];
            payload.extend_from_slice(leaf_payload(
                &encode(LeafCodec::CanonicalHuffman, &[1, b'a', 0]).unwrap(),
            ));
            envelope(LeafCodec::LzHuffman.opcode(), 1, &payload)
        },
    ];
    for bytes in cases {
        assert_inspect_and_decode_reject(&bytes);
    }

    for end in 0..encoded.len() {
        assert_inspect_and_decode_reject(&encoded[..end]);
    }
    let malformed = lz_huffman_from_tokens(5, &[1, b'a', 0, 0]);
    let mut untouched = [0xa5; 5];
    assert!(decode_into(&malformed, &mut untouched, DecodeLimits::default()).is_err());
    assert_eq!(untouched, [0xa5; 5]);
}

#[test]
fn lz_count_is_exact_for_arbitrary_inputs_and_encoding_is_stable() {
    let mut state = 0x082e_fa98_ec4e_6c89u64;
    for length in 0..2048 {
        let mut bytes = Vec::with_capacity(length);
        for index in 0..length {
            let random = next_random(&mut state) as u8;
            bytes.push(if index % 17 < 12 {
                b"canonical-lz-token"[index % 18]
            } else {
                random
            });
        }
        let counted = count_candidate(LeafCodec::LzTokens, &bytes).unwrap();
        let encoded = encode(LeafCodec::LzTokens, &bytes).unwrap();
        assert_eq!(counted.encoded_bytes, encoded.len() as u64);
        assert!(leaf_payload(&encoded).len() <= bytes.len().saturating_mul(2));
        assert_eq!(
            encoded_size(LeafCodec::LzTokens, &bytes).unwrap(),
            encoded.len()
        );
        assert_eq!(
            encode(LeafCodec::LzTokens, &bytes).unwrap(),
            encoded,
            "non-deterministic LZ encoding at length {length}"
        );
        assert_eq!(
            decode(&encoded, DecodeLimits::default()).unwrap().bytes,
            bytes
        );
    }
}

#[test]
fn oracle_g1_is_byte_identical_to_the_v1_lz_huffman_parser() {
    let mut state = 0x243f_6a88_85a3_08d3u64;
    for length in 0..1024 {
        let mut bytes = Vec::with_capacity(length);
        for index in 0..length {
            let random = next_random(&mut state) as u8;
            bytes.push(if index % 29 < 23 {
                b"single-candidate-greedy"[index % 23]
            } else {
                random
            });
        }
        let baseline = encode(LeafCodec::LzHuffman, &bytes).unwrap();
        let counted = count_lz_huffman_policy(&bytes, LzParserPolicy::G1).unwrap();
        let encoded = encode_lz_huffman_policy(&bytes, LzParserPolicy::G1).unwrap();
        assert_eq!(encoded.bytes.as_deref(), Some(baseline.as_slice()));
        assert_eq!(encoded.analysis, counted);
        assert_eq!(counted.score.unwrap().encoded_bytes, baseline.len() as u64);
        assert!(!counted.stats.budget_exhausted);
        assert_eq!(counted.stats.scratch_bytes, 65_536 * 4);
    }
}

#[test]
fn chain_policies_count_emit_roundtrip_and_obey_fixed_bounds() {
    let mut state = 0x1319_8a2e_0370_7344u64;
    let random: Vec<u8> = (0..8192).map(|_| next_random(&mut state) as u8).collect();
    let samples = [
        Vec::new(),
        b"abc".to_vec(),
        b"bounded-chain-overlap; ".repeat(2048),
        [
            b"varint-boundary-".repeat(127),
            b"varint-boundary-".repeat(128),
        ]
        .concat(),
        random,
    ];
    for policy in LzParserPolicy::EXPERIMENTAL {
        let depth = match policy {
            LzParserPolicy::C4 | LzParserPolicy::C4Lazy => 4,
            LzParserPolicy::C8 | LzParserPolicy::C8Lazy => 8,
            LzParserPolicy::C16 | LzParserPolicy::C16Lazy => 16,
            LzParserPolicy::C32Lazy => 32,
            LzParserPolicy::C64Lazy => 64,
            LzParserPolicy::G1 => unreachable!(),
        };
        for sample in &samples {
            let counted = count_lz_huffman_policy(sample, policy).unwrap();
            let first = encode_lz_huffman_policy(sample, policy).unwrap();
            let second = encode_lz_huffman_policy(sample, policy).unwrap();
            assert_eq!(first, second);
            assert_eq!(first.analysis, counted);
            assert!(!counted.stats.budget_exhausted);
            assert_eq!(counted.stats.scratch_bytes, 2 * 65_536 * 4);
            assert!(counted.stats.positions_inserted <= sample.len() as u64);
            assert!(counted.stats.chain_links_examined <= sample.len() as u64 * depth);
            assert!(counted.stats.extension_bytes_compared <= counted.stats.extension_byte_budget);
            let bytes = first.bytes.unwrap();
            assert_eq!(counted.score.unwrap().encoded_bytes, bytes.len() as u64);
            assert_eq!(
                decode(&bytes, DecodeLimits::default()).unwrap().bytes,
                *sample
            );
        }
    }
}

#[test]
fn conditional_policy_emission_reuses_the_counted_model_only_for_a_winner() {
    let losing = b"abc";
    let losing_incumbent = encode_best(losing).unwrap();
    let losing_full = encode_lz_huffman_policy(losing, LzParserPolicy::C4Lazy).unwrap();
    let losing_conditional =
        encode_lz_huffman_policy_if_better(losing, LzParserPolicy::C4Lazy, losing_incumbent.score)
            .unwrap();
    assert_eq!(losing_conditional.analysis, losing_full.analysis);
    assert!(losing_conditional.analysis.score.is_some());
    assert!(!losing_conditional.selected);
    assert!(losing_conditional.bytes.is_none());

    let tied_score = losing_full.analysis.score.unwrap();
    let tied =
        encode_lz_huffman_policy_if_better(losing, LzParserPolicy::C4Lazy, tied_score).unwrap();
    assert_eq!(tied.analysis, losing_full.analysis);
    assert!(!tied.selected, "normative score ties retain the incumbent");
    assert!(tied.bytes.is_none());

    let winning = include_bytes!("../../../../datasets/data/canterbury/alice29.txt");
    let winning_incumbent = encode_best(winning).unwrap();
    let winning_full = encode_lz_huffman_policy(winning, LzParserPolicy::C4Lazy).unwrap();
    let winning_conditional = encode_lz_huffman_policy_if_better(
        winning,
        LzParserPolicy::C4Lazy,
        winning_incumbent.score,
    )
    .unwrap();
    assert_eq!(winning_conditional.analysis, winning_full.analysis);
    assert_eq!(winning_conditional.bytes, winning_full.bytes);
    assert!(winning_conditional.selected);
    assert!(
        winning_conditional.analysis.score.unwrap() < winning_incumbent.score,
        "frozen Alice sample must retain the admitted C4L win"
    );
}

#[test]
fn already_compressed_bytes_never_lose_to_raw_leaf() {
    let compressed = include_bytes!("../../../../datasets/generated/mixed/files/sample.gz");
    let best = encode_best(compressed).unwrap();
    let raw = encode(LeafCodec::Raw, compressed).unwrap();
    assert!(best.bytes.len() <= raw.len());
    assert_eq!(
        decode(&best.bytes, DecodeLimits::default()).unwrap().bytes,
        compressed
    );
}

#[test]
fn representative_alice_text_is_compressed_by_native_entropy() {
    let alice = include_bytes!("../../../../datasets/data/canterbury/alice29.txt");
    let started = std::time::Instant::now();
    let (best, audit) = encode_best_audited(alice).unwrap();
    let elapsed = started.elapsed();
    let raw = encode(LeafCodec::Raw, alice).unwrap();
    assert_eq!(best.codec, LeafCodec::LzHuffman);
    assert_eq!(audit.g1_preparation_walks, 1);
    assert_eq!(audit.g1_emission_walks, 1);
    assert_eq!(audit.g1_parser_walks, 2);
    assert_eq!(best.bytes, encode(LeafCodec::LzHuffman, alice).unwrap());
    assert!(best.bytes.len() < raw.len());
    let decode_started = std::time::Instant::now();
    let restored = decode(&best.bytes, DecodeLimits::default()).unwrap();
    let decode_elapsed = decode_started.elapsed();
    assert_eq!(restored.bytes, alice);
    eprintln!(
        "alice29 native entropy leaf: codec={:?} input={} encoded={} ratio_millionths={} encode_us={} decode_us={}",
        best.codec,
        alice.len(),
        best.bytes.len(),
        best.bytes.len() as u64 * 1_000_000 / alice.len() as u64,
        elapsed.as_micros(),
        decode_elapsed.as_micros(),
    );
}

#[test]
fn decode_limits_are_enforced_before_unbounded_work_or_allocation() {
    let encoded = encode(LeafCodec::ByteRle, &[7; 100]).unwrap();
    let limits = DecodeLimits {
        max_encoded_bytes: (encoded.len() - 1) as u64,
        ..DecodeLimits::default()
    };
    assert!(matches!(
        decode(&encoded, limits),
        Err(Error::LimitExceeded {
            what: "encoded bytes",
            ..
        })
    ));

    let limits = DecodeLimits {
        max_output_bytes: 99,
        ..DecodeLimits::default()
    };
    assert!(matches!(
        decode(&encoded, limits),
        Err(Error::LimitExceeded {
            what: "decoded bytes",
            ..
        })
    ));

    let limits = DecodeLimits {
        max_work: 1,
        ..DecodeLimits::default()
    };
    assert!(matches!(
        decode(&encoded, limits),
        Err(Error::LimitExceeded {
            what: "decode work",
            ..
        })
    ));
}

#[test]
fn arbitrary_hostile_bytes_do_not_panic() {
    let limits = DecodeLimits {
        max_encoded_bytes: 256,
        max_output_bytes: 256,
        max_work: 4096,
    };
    let mut state = 0xd1b5_4a32_d192_ed03;
    for _ in 0..20_000 {
        let length = (next_random(&mut state) % 96) as usize;
        let mut bytes = Vec::with_capacity(length);
        for _ in 0..length {
            bytes.push(next_random(&mut state) as u8);
        }
        let result = std::panic::catch_unwind(|| decode(&bytes, limits));
        assert!(result.is_ok(), "decoder panicked for {bytes:02x?}");
    }
}

#[test]
fn arbitrary_hostile_lz_payloads_do_not_panic_or_modify_on_rejection() {
    let limits = DecodeLimits {
        max_encoded_bytes: 512,
        max_output_bytes: 256,
        max_work: 4096,
    };
    let mut state = 0x4528_21e6_38d0_1377u64;
    for _ in 0..10_000 {
        let decoded = (next_random(&mut state) % 257) as usize;
        let payload_length = (next_random(&mut state) % 192) as usize;
        let mut payload = Vec::with_capacity(payload_length);
        for _ in 0..payload_length {
            payload.push(next_random(&mut state) as u8);
        }
        let bytes = envelope(LeafCodec::LzTokens.opcode(), decoded as u64, &payload);
        let inspected = std::panic::catch_unwind(|| inspect(&bytes, limits));
        assert!(inspected.is_ok(), "LZ inspect panicked for {bytes:02x?}");

        let mut destination = vec![0xa5; decoded];
        let decoded_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            decode_into(&bytes, &mut destination, limits)
        }));
        assert!(
            decoded_result.is_ok(),
            "LZ direct decode panicked for {bytes:02x?}"
        );
        if decoded_result.unwrap().is_err() {
            assert_eq!(destination, vec![0xa5; decoded]);
        }
    }
}

#[test]
fn arbitrary_hostile_huffman_payloads_do_not_panic_or_modify_on_rejection() {
    let limits = DecodeLimits {
        max_encoded_bytes: 768,
        max_output_bytes: 256,
        max_work: 1_000_000,
    };
    let mut state = 0x1319_8a2e_0370_7344u64;
    for _ in 0..10_000 {
        let decoded = (next_random(&mut state) % 257) as usize;
        let payload_length = (next_random(&mut state) % 512) as usize;
        let mut payload = Vec::with_capacity(payload_length);
        for _ in 0..payload_length {
            payload.push(next_random(&mut state) as u8);
        }
        let bytes = envelope(
            LeafCodec::CanonicalHuffman.opcode(),
            decoded as u64,
            &payload,
        );
        let inspected = std::panic::catch_unwind(|| inspect(&bytes, limits));
        assert!(
            inspected.is_ok(),
            "Huffman inspect panicked for {bytes:02x?}"
        );

        let mut destination = vec![0xa5; decoded];
        let decoded_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            decode_into(&bytes, &mut destination, limits)
        }));
        assert!(
            decoded_result.is_ok(),
            "Huffman direct decode panicked for {bytes:02x?}"
        );
        if decoded_result.unwrap().is_err() {
            assert_eq!(destination, vec![0xa5; decoded]);
        }
    }
}

#[test]
fn arbitrary_hostile_lz_huffman_payloads_do_not_panic_or_modify_on_rejection() {
    let limits = DecodeLimits {
        max_encoded_bytes: 768,
        max_output_bytes: 256,
        max_work: 1_000_000,
    };
    let mut state = 0xa409_3822_299f_31d0u64;
    for _ in 0..10_000 {
        let decoded = (next_random(&mut state) % 257) as usize;
        let payload_length = (next_random(&mut state) % 512) as usize;
        let mut payload = Vec::with_capacity(payload_length);
        for _ in 0..payload_length {
            payload.push(next_random(&mut state) as u8);
        }
        let bytes = envelope(LeafCodec::LzHuffman.opcode(), decoded as u64, &payload);
        let inspected = std::panic::catch_unwind(|| inspect(&bytes, limits));
        assert!(
            inspected.is_ok(),
            "LZ-Huffman inspect panicked for {bytes:02x?}"
        );

        let mut destination = vec![0xa5; decoded];
        let decoded_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            decode_into(&bytes, &mut destination, limits)
        }));
        assert!(
            decoded_result.is_ok(),
            "LZ-Huffman direct decode panicked for {bytes:02x?}"
        );
        if decoded_result.unwrap().is_err() {
            assert_eq!(destination, vec![0xa5; decoded]);
        }
    }
}
