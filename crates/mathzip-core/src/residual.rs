use crate::config::ResidualOptions;
use crate::varint;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};

/// How a predictor and a residual byte are combined.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResidualMode {
    AddModulo,
    Xor,
}

impl ResidualMode {
    pub(crate) fn id(self) -> u8 {
        match self {
            Self::AddModulo => 0,
            Self::Xor => 1,
        }
    }

    pub(crate) fn from_id(id: u8) -> Result<Self> {
        match id {
            0 => Ok(Self::AddModulo),
            1 => Ok(Self::Xor),
            id => Err(Error::UnknownId {
                kind: "residual mode",
                id,
            }),
        }
    }
}

/// Byte residual encoding used by a segment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResidualCoder {
    Raw,
    Rle,
    ZeroRun,
    Sparse,
    BitPack,
    Zstd,
}

impl ResidualCoder {
    pub(crate) fn id(self) -> u8 {
        match self {
            Self::Raw => 0,
            Self::Rle => 1,
            Self::ZeroRun => 2,
            Self::Sparse => 3,
            Self::BitPack => 4,
            Self::Zstd => 5,
        }
    }

    pub(crate) fn from_id(id: u8) -> Result<Self> {
        match id {
            0 => Ok(Self::Raw),
            1 => Ok(Self::Rle),
            2 => Ok(Self::ZeroRun),
            3 => Ok(Self::Sparse),
            4 => Ok(Self::BitPack),
            5 => Ok(Self::Zstd),
            id => Err(Error::UnknownId {
                kind: "residual coder",
                id,
            }),
        }
    }
}

#[cfg(test)]
pub(crate) fn make(actual: &[u8], predicted: &[u8], mode: ResidualMode) -> Vec<u8> {
    debug_assert_eq!(actual.len(), predicted.len());
    match mode {
        ResidualMode::AddModulo => actual
            .iter()
            .zip(predicted)
            .map(|(&value, &prediction)| value.wrapping_sub(prediction))
            .collect(),
        ResidualMode::Xor => actual
            .iter()
            .zip(predicted)
            .map(|(&value, &prediction)| value ^ prediction)
            .collect(),
    }
}

pub(crate) fn encode_best(
    input: &[u8],
    options: &ResidualOptions,
) -> Result<Option<(ResidualCoder, Vec<u8>)>> {
    let custom_sizes = CustomEncodedSizes::measure(input, options);
    let mut best: Option<(ResidualCoder, usize, Option<Vec<u8>>)> = None;
    for (enabled, coder) in [
        (options.raw, ResidualCoder::Raw),
        (options.rle, ResidualCoder::Rle),
        (options.zero_run, ResidualCoder::ZeroRun),
        (options.sparse, ResidualCoder::Sparse),
        (options.bit_pack, ResidualCoder::BitPack),
        (options.zstd, ResidualCoder::Zstd),
    ] {
        if !enabled {
            continue;
        }
        let encoded = if coder == ResidualCoder::Zstd {
            Some(encode(coder, input)?)
        } else {
            None
        };
        let size = encoded
            .as_ref()
            .map(Vec::len)
            .unwrap_or_else(|| custom_sizes.get(coder));
        // Stable tie break: candidate order / lower coder ID wins.
        if best
            .as_ref()
            .map(|(_, current_size, _)| size < *current_size)
            .unwrap_or(true)
        {
            best = Some((coder, size, encoded));
        }
    }
    best.map(|(coder, expected_size, encoded)| {
        let encoded = match encoded {
            Some(payload) => Ok(payload),
            None => encode(coder, input),
        }?;
        debug_assert_eq!(encoded.len(), expected_size);
        Ok((coder, encoded))
    })
    .transpose()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct CustomEncodedSizes {
    raw: usize,
    rle: usize,
    zero_run: usize,
    sparse: usize,
    bit_pack: usize,
}

impl CustomEncodedSizes {
    /// Measure every enabled custom residual representation in one pass.
    ///
    /// Raw has no measurement cost, and the raw-only/Zstandard-only ablation
    /// profiles retain their previous zero-scan fast path. Zstandard is still
    /// encoded separately because its exact frame size cannot be derived from
    /// these byte statistics.
    fn measure(input: &[u8], options: &ResidualOptions) -> Self {
        let mut sizes = Self {
            raw: input.len(),
            rle: 0,
            zero_run: 0,
            sparse: 0,
            bit_pack: 1,
        };
        if !(options.rle || options.zero_run || options.sparse || options.bit_pack) {
            return sizes;
        }

        let mut rle_run_start = 0usize;
        let mut rle_value = 0u8;

        let mut zero_run_length = 0usize;
        let mut literal_run_length = 0usize;
        let mut in_literal_run = false;

        let mut sparse_count = 0usize;
        let mut sparse_previous = None;
        let mut sparse_body_size = 0usize;

        let mut maximum = 0u8;

        for (position, &value) in input.iter().enumerate() {
            if options.rle {
                if position == 0 {
                    rle_value = value;
                } else if value != rle_value {
                    sizes.rle = sizes
                        .rle
                        .saturating_add(varint::encoded_len((position - rle_run_start) as u64))
                        .saturating_add(1);
                    rle_run_start = position;
                    rle_value = value;
                }
            }

            if options.zero_run {
                if in_literal_run {
                    if value == 0 {
                        sizes.zero_run = sizes
                            .zero_run
                            .saturating_add(varint::encoded_len(zero_run_length as u64))
                            .saturating_add(varint::encoded_len(literal_run_length as u64))
                            .saturating_add(literal_run_length);
                        zero_run_length = 1;
                        literal_run_length = 0;
                        in_literal_run = false;
                    } else {
                        literal_run_length += 1;
                    }
                } else if value == 0 {
                    zero_run_length += 1;
                } else {
                    literal_run_length = 1;
                    in_literal_run = true;
                }
            }

            if options.sparse && value != 0 {
                let delta = match sparse_previous {
                    None => position,
                    Some(previous) => position - previous - 1,
                };
                sparse_body_size = sparse_body_size
                    .saturating_add(varint::encoded_len(delta as u64))
                    .saturating_add(1);
                sparse_count += 1;
                sparse_previous = Some(position);
            }

            if options.bit_pack {
                maximum = maximum.max(value);
            }
        }

        if options.rle && !input.is_empty() {
            sizes.rle = sizes
                .rle
                .saturating_add(varint::encoded_len((input.len() - rle_run_start) as u64))
                .saturating_add(1);
        }
        if options.zero_run && !input.is_empty() {
            sizes.zero_run = sizes
                .zero_run
                .saturating_add(varint::encoded_len(zero_run_length as u64))
                .saturating_add(varint::encoded_len(literal_run_length as u64))
                .saturating_add(literal_run_length);
        }
        if options.sparse {
            sizes.sparse =
                varint::encoded_len(sparse_count as u64).saturating_add(sparse_body_size);
        }
        if options.bit_pack {
            let width = if maximum == 0 {
                0
            } else {
                (8 - maximum.leading_zeros()) as usize
            };
            sizes.bit_pack = 1usize.saturating_add(input.len().saturating_mul(width).div_ceil(8));
        }

        sizes
    }

    fn get(self, coder: ResidualCoder) -> usize {
        match coder {
            ResidualCoder::Raw => self.raw,
            ResidualCoder::Rle => self.rle,
            ResidualCoder::ZeroRun => self.zero_run,
            ResidualCoder::Sparse => self.sparse,
            ResidualCoder::BitPack => self.bit_pack,
            // `encode_best` always has the encoded Zstandard frame available.
            ResidualCoder::Zstd => unreachable!("Zstandard size requires an encoded frame"),
        }
    }
}

pub(crate) fn encode(coder: ResidualCoder, input: &[u8]) -> Result<Vec<u8>> {
    Ok(match coder {
        ResidualCoder::Raw => input.to_vec(),
        ResidualCoder::Rle => encode_rle(input),
        ResidualCoder::ZeroRun => encode_zero_run(input),
        ResidualCoder::Sparse => encode_sparse(input),
        ResidualCoder::BitPack => encode_bit_pack(input),
        ResidualCoder::Zstd => zstd::bulk::compress(input, 3)
            .map_err(|error| Error::ResidualCodec(error.to_string()))?,
    })
}

pub(crate) fn decode(coder: ResidualCoder, payload: &[u8], expected_len: usize) -> Result<Vec<u8>> {
    match coder {
        ResidualCoder::Raw => {
            if payload.len() != expected_len {
                Err(Error::InvalidResidual("raw residual length mismatch"))
            } else {
                Ok(payload.to_vec())
            }
        }
        ResidualCoder::Rle => decode_rle(payload, expected_len),
        ResidualCoder::ZeroRun => decode_zero_run(payload, expected_len),
        ResidualCoder::Sparse => decode_sparse(payload, expected_len),
        ResidualCoder::BitPack => decode_bit_pack(payload, expected_len),
        ResidualCoder::Zstd => {
            let frame_size = zstd::zstd_safe::find_frame_compressed_size(payload)
                .map_err(|error| Error::ResidualCodec(error.to_string()))?;
            if frame_size != payload.len() {
                return Err(Error::InvalidResidual(
                    "Zstandard residual must contain exactly one frame",
                ));
            }
            zstd::bulk::decompress(payload, expected_len)
                .map_err(|error| Error::ResidualCodec(error.to_string()))
                .and_then(|decoded| {
                    if decoded.len() == expected_len {
                        Ok(decoded)
                    } else {
                        Err(Error::InvalidResidual("Zstandard residual length mismatch"))
                    }
                })
        }
    }
}

fn encode_rle(input: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut cursor = 0usize;
    while cursor < input.len() {
        let value = input[cursor];
        let mut end = cursor + 1;
        while end < input.len() && input[end] == value {
            end += 1;
        }
        varint::put((end - cursor) as u64, &mut out);
        out.push(value);
        cursor = end;
    }
    out
}

fn decode_rle(payload: &[u8], expected_len: usize) -> Result<Vec<u8>> {
    let mut out = Vec::with_capacity(expected_len);
    let mut cursor = 0usize;
    while out.len() < expected_len {
        let run = varint::usize_from(varint::get(payload, &mut cursor)?)?;
        if run == 0 {
            return Err(Error::InvalidResidual("zero-length RLE run"));
        }
        let value = *payload.get(cursor).ok_or(Error::Truncated {
            context: "RLE value",
        })?;
        cursor += 1;
        let new_len = out.len().checked_add(run).ok_or(Error::IntegerOverflow)?;
        if new_len > expected_len {
            return Err(Error::InvalidResidual("RLE run exceeds segment"));
        }
        out.resize(new_len, value);
    }
    if cursor != payload.len() {
        return Err(Error::InvalidResidual("trailing RLE bytes"));
    }
    Ok(out)
}

fn encode_zero_run(input: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut cursor = 0usize;
    while cursor < input.len() {
        let zero_start = cursor;
        while cursor < input.len() && input[cursor] == 0 {
            cursor += 1;
        }
        varint::put((cursor - zero_start) as u64, &mut out);
        let literal_start = cursor;
        while cursor < input.len() && input[cursor] != 0 {
            cursor += 1;
        }
        varint::put((cursor - literal_start) as u64, &mut out);
        out.extend_from_slice(&input[literal_start..cursor]);
    }
    out
}

fn decode_zero_run(payload: &[u8], expected_len: usize) -> Result<Vec<u8>> {
    let mut out = Vec::with_capacity(expected_len);
    let mut cursor = 0usize;
    while out.len() < expected_len {
        let zeros = varint::usize_from(varint::get(payload, &mut cursor)?)?;
        let literals = varint::usize_from(varint::get(payload, &mut cursor)?)?;
        if zeros == 0 && literals == 0 {
            return Err(Error::InvalidResidual("empty zero-run token"));
        }
        let after_zeros = out.len().checked_add(zeros).ok_or(Error::IntegerOverflow)?;
        let after_literals = after_zeros
            .checked_add(literals)
            .ok_or(Error::IntegerOverflow)?;
        if after_literals > expected_len {
            return Err(Error::InvalidResidual("zero-run token exceeds segment"));
        }
        out.resize(after_zeros, 0);
        let end = cursor.checked_add(literals).ok_or(Error::IntegerOverflow)?;
        let bytes = payload.get(cursor..end).ok_or(Error::Truncated {
            context: "zero-run literals",
        })?;
        if bytes.contains(&0) {
            return Err(Error::InvalidResidual(
                "zero-run literal block contains zero",
            ));
        }
        out.extend_from_slice(bytes);
        cursor = end;
    }
    if cursor != payload.len() {
        return Err(Error::InvalidResidual("trailing zero-run bytes"));
    }
    Ok(out)
}

fn encode_sparse(input: &[u8]) -> Vec<u8> {
    let count = input.iter().filter(|&&value| value != 0).count();
    let mut out = Vec::with_capacity(count.saturating_mul(2) + varint::encoded_len(count as u64));
    varint::put(count as u64, &mut out);
    let mut previous: Option<usize> = None;
    for (position, &value) in input.iter().enumerate() {
        if value == 0 {
            continue;
        }
        let delta = match previous {
            None => position,
            Some(previous) => position - previous - 1,
        };
        varint::put(delta as u64, &mut out);
        out.push(value);
        previous = Some(position);
    }
    out
}

fn decode_sparse(payload: &[u8], expected_len: usize) -> Result<Vec<u8>> {
    let mut cursor = 0usize;
    let count = varint::usize_from(varint::get(payload, &mut cursor)?)?;
    if count > expected_len {
        return Err(Error::InvalidResidual("too many sparse entries"));
    }
    let mut out = vec![0u8; expected_len];
    let mut previous: Option<usize> = None;
    for _ in 0..count {
        let delta = varint::usize_from(varint::get(payload, &mut cursor)?)?;
        let position = match previous {
            None => delta,
            Some(previous) => previous
                .checked_add(1)
                .and_then(|x| x.checked_add(delta))
                .ok_or(Error::IntegerOverflow)?,
        };
        if position >= expected_len {
            return Err(Error::InvalidResidual("sparse position out of bounds"));
        }
        let value = *payload.get(cursor).ok_or(Error::Truncated {
            context: "sparse value",
        })?;
        cursor += 1;
        if value == 0 {
            return Err(Error::InvalidResidual("sparse value is zero"));
        }
        out[position] = value;
        previous = Some(position);
    }
    if cursor != payload.len() {
        return Err(Error::InvalidResidual("trailing sparse bytes"));
    }
    Ok(out)
}

fn encode_bit_pack(input: &[u8]) -> Vec<u8> {
    let max = input.iter().copied().max().unwrap_or(0);
    let width = if max == 0 {
        0
    } else {
        (8 - max.leading_zeros()) as u8
    };
    let bit_len = input.len().saturating_mul(usize::from(width));
    let mut out = vec![0u8; 1 + bit_len.div_ceil(8)];
    out[0] = width;
    let mut bit_cursor = 0usize;
    for &value in input {
        for bit in 0..width {
            if (value >> bit) & 1 != 0 {
                let destination = 8 + bit_cursor;
                out[destination / 8] |= 1 << (destination % 8);
            }
            bit_cursor += 1;
        }
    }
    out
}

fn decode_bit_pack(payload: &[u8], expected_len: usize) -> Result<Vec<u8>> {
    let &width = payload.first().ok_or(Error::Truncated {
        context: "bit-pack width",
    })?;
    if width > 8 {
        return Err(Error::InvalidResidual("bit-pack width exceeds eight"));
    }
    let bits = expected_len
        .checked_mul(usize::from(width))
        .ok_or(Error::IntegerOverflow)?;
    let expected_payload = 1usize
        .checked_add(bits.div_ceil(8))
        .ok_or(Error::IntegerOverflow)?;
    if payload.len() != expected_payload {
        return Err(Error::InvalidResidual("bit-pack payload length mismatch"));
    }
    let mut out = vec![0u8; expected_len];
    let mut bit_cursor = 0usize;
    for value in &mut out {
        for bit in 0..width {
            let source = 8 + bit_cursor;
            *value |= ((payload[source / 8] >> (source % 8)) & 1) << bit;
            bit_cursor += 1;
        }
    }
    if bits % 8 != 0 && payload[payload.len() - 1] >> (bits % 8) != 0 {
        return Err(Error::InvalidResidual("non-zero bit-pack padding"));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn all_custom_options() -> ResidualOptions {
        ResidualOptions {
            raw: true,
            rle: true,
            zero_run: true,
            sparse: true,
            bit_pack: true,
            zstd: false,
        }
    }

    fn options_from_mask(mask: u8) -> ResidualOptions {
        ResidualOptions {
            raw: mask & (1 << 0) != 0,
            rle: mask & (1 << 1) != 0,
            zero_run: mask & (1 << 2) != 0,
            sparse: mask & (1 << 3) != 0,
            bit_pack: mask & (1 << 4) != 0,
            zstd: mask & (1 << 5) != 0,
        }
    }

    fn encode_best_allocating(
        input: &[u8],
        options: &ResidualOptions,
    ) -> Result<Option<(ResidualCoder, Vec<u8>)>> {
        let mut best: Option<(ResidualCoder, Vec<u8>)> = None;
        for (enabled, coder) in [
            (options.raw, ResidualCoder::Raw),
            (options.rle, ResidualCoder::Rle),
            (options.zero_run, ResidualCoder::ZeroRun),
            (options.sparse, ResidualCoder::Sparse),
            (options.bit_pack, ResidualCoder::BitPack),
            (options.zstd, ResidualCoder::Zstd),
        ] {
            if !enabled {
                continue;
            }
            let payload = encode(coder, input)?;
            // Deliberately independent reference: encode every candidate, then
            // apply the format's first/lower-ID-wins tie rule.
            if best
                .as_ref()
                .map(|(_, current)| payload.len() < current.len())
                .unwrap_or(true)
            {
                best = Some((coder, payload));
            }
        }
        Ok(best)
    }

    fn assert_custom_sizes_match_payloads(input: &[u8]) {
        let sizes = CustomEncodedSizes::measure(input, &all_custom_options());
        for coder in [
            ResidualCoder::Raw,
            ResidualCoder::Rle,
            ResidualCoder::ZeroRun,
            ResidualCoder::Sparse,
            ResidualCoder::BitPack,
        ] {
            assert_eq!(
                sizes.get(coder),
                encode(coder, input).unwrap().len(),
                "coder {coder:?}, input length {}",
                input.len()
            );
        }
    }

    #[test]
    fn every_coder_round_trips_edge_cases() {
        let cases = [
            vec![],
            vec![0],
            vec![255],
            vec![0; 1000],
            vec![7; 1000],
            (0..=255).collect(),
            vec![0, 0, 1, 0, 2, 3, 0, 0, 0, 4],
        ];
        for case in cases {
            for coder in [
                ResidualCoder::Raw,
                ResidualCoder::Rle,
                ResidualCoder::ZeroRun,
                ResidualCoder::Sparse,
                ResidualCoder::BitPack,
                ResidualCoder::Zstd,
            ] {
                let payload = encode(coder, &case).unwrap();
                assert_eq!(decode(coder, &payload, case.len()).unwrap(), case);
            }
        }
    }

    #[test]
    fn fused_sizes_match_payloads_at_varint_and_run_boundaries() {
        for length in [
            0usize, 1, 2, 126, 127, 128, 129, 16_382, 16_383, 16_384, 16_385,
        ] {
            assert_custom_sizes_match_payloads(&vec![0; length]);
            assert_custom_sizes_match_payloads(&vec![7; length]);
            assert_custom_sizes_match_payloads(
                &(0..length)
                    .map(|position| if position % 2 == 0 { 0 } else { 255 })
                    .collect::<Vec<_>>(),
            );

            let mut sparse = vec![0; length];
            if let Some(first) = sparse.first_mut() {
                *first = 1;
            }
            if let Some(last) = sparse.last_mut() {
                *last = 2;
            }
            assert_custom_sizes_match_payloads(&sparse);
        }
    }

    #[test]
    fn selection_matches_reference_for_every_mask_and_stable_tie() {
        let cases = [
            vec![],
            vec![0],
            vec![255],
            vec![0; 128],
            vec![7; 128],
            (0..=255).collect(),
            vec![0, 0, 1, 0, 2, 3, 0, 0, 0, 4],
        ];
        for input in cases {
            for mask in 0u8..64 {
                let options = options_from_mask(mask);
                assert_eq!(
                    encode_best(&input, &options).unwrap(),
                    encode_best_allocating(&input, &options).unwrap(),
                    "mask {mask:#08b}, input length {}",
                    input.len()
                );
            }
        }

        let rle_zero_tie = ResidualOptions {
            raw: false,
            rle: true,
            zero_run: true,
            sparse: false,
            bit_pack: false,
            zstd: false,
        };
        assert_eq!(
            encode_best(&[], &rle_zero_tie).unwrap().unwrap().0,
            ResidualCoder::Rle
        );

        let sparse_bit_pack_tie = ResidualOptions {
            raw: false,
            rle: false,
            zero_run: false,
            sparse: true,
            bit_pack: true,
            zstd: false,
        };
        assert_eq!(
            encode_best(&[0; 128], &sparse_bit_pack_tie)
                .unwrap()
                .unwrap()
                .0,
            ResidualCoder::Sparse
        );
    }

    proptest! {
        #![proptest_config(ProptestConfig {
            cases: 512,
            max_shrink_iters: 1024,
            .. ProptestConfig::default()
        })]

        #[test]
        fn fused_sizes_equal_actual_custom_payload_lengths(
            input in prop::collection::vec(any::<u8>(), 0..4096)
        ) {
            let sizes = CustomEncodedSizes::measure(&input, &all_custom_options());
            for coder in [
                ResidualCoder::Raw,
                ResidualCoder::Rle,
                ResidualCoder::ZeroRun,
                ResidualCoder::Sparse,
                ResidualCoder::BitPack,
            ] {
                prop_assert_eq!(
                    sizes.get(coder),
                    encode(coder, &input).unwrap().len(),
                    "coder {:?}",
                    coder
                );
            }
        }

        #[test]
        fn optimized_selection_matches_reference_for_arbitrary_coder_masks(
            input in prop::collection::vec(any::<u8>(), 0..2048),
            mask in 0u8..64,
        ) {
            let options = options_from_mask(mask);
            prop_assert_eq!(
                encode_best(&input, &options).unwrap(),
                encode_best_allocating(&input, &options).unwrap()
            );
        }
    }

    #[test]
    fn malformed_payloads_are_rejected() {
        assert!(decode(ResidualCoder::Rle, &[0, 1], 1).is_err());
        assert!(decode(ResidualCoder::Sparse, &[1, 5, 1], 2).is_err());
        assert!(decode(ResidualCoder::BitPack, &[9], 0).is_err());
        assert!(decode(ResidualCoder::ZeroRun, &[0, 0], 1).is_err());
        let mut concatenated = encode(ResidualCoder::Zstd, b"first").unwrap();
        concatenated.extend(encode(ResidualCoder::Zstd, b"second").unwrap());
        assert!(decode(ResidualCoder::Zstd, &concatenated, 11).is_err());
    }
}
