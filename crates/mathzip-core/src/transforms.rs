use crate::{Error, Result};
use serde::{Deserialize, Serialize};

/// Reversible transform registered by the MathZip container.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(tag = "type", content = "stride", rename_all = "snake_case")]
pub enum TransformKind {
    Identity,
    Delta,
    Xor,
    BitPlane,
    Stride(u16),
    BitPlanePadded,
}

impl TransformKind {
    pub(crate) fn id(self) -> u8 {
        match self {
            Self::Identity => 0,
            Self::Delta => 1,
            Self::Xor => 2,
            Self::BitPlane => 3,
            Self::Stride(_) => 4,
            Self::BitPlanePadded => 5,
        }
    }

    pub(crate) const fn minimum_format_version(self) -> u16 {
        match self {
            Self::BitPlanePadded => 2,
            _ => 1,
        }
    }

    pub(crate) fn parameters(self) -> Vec<u8> {
        match self {
            Self::Stride(stride) => stride.to_le_bytes().to_vec(),
            _ => Vec::new(),
        }
    }

    pub(crate) fn from_descriptor(id: u8, params: &[u8]) -> Result<Self> {
        match id {
            0 if params.is_empty() => Ok(Self::Identity),
            1 if params.is_empty() => Ok(Self::Delta),
            2 if params.is_empty() => Ok(Self::Xor),
            3 if params.is_empty() => Ok(Self::BitPlane),
            4 if params.len() == 2 => {
                let stride = u16::from_le_bytes([params[0], params[1]]);
                if !matches!(stride, 2 | 3 | 4 | 8 | 16 | 32 | 64) {
                    Err(Error::InvalidTransform("unsupported version-1 stride"))
                } else {
                    Ok(Self::Stride(stride))
                }
            }
            5 if params.is_empty() => Ok(Self::BitPlanePadded),
            0..=5 => Err(Error::InvalidTransform(
                "unexpected transform parameter length",
            )),
            id => Err(Error::UnknownId {
                kind: "transform",
                id,
            }),
        }
    }
}

pub(crate) fn apply(kind: TransformKind, input: &[u8]) -> Vec<u8> {
    match kind {
        TransformKind::Identity => input.to_vec(),
        TransformKind::Delta => {
            let mut out = Vec::with_capacity(input.len());
            let mut previous = 0u8;
            for (i, &value) in input.iter().enumerate() {
                out.push(if i == 0 {
                    value
                } else {
                    value.wrapping_sub(previous)
                });
                previous = value;
            }
            out
        }
        TransformKind::Xor => {
            let mut out = Vec::with_capacity(input.len());
            let mut previous = 0u8;
            for (i, &value) in input.iter().enumerate() {
                out.push(if i == 0 { value } else { value ^ previous });
                previous = value;
            }
            out
        }
        TransformKind::BitPlane => bit_plane_forward(input),
        TransformKind::Stride(stride) => stride_forward(input, usize::from(stride)),
        TransformKind::BitPlanePadded => bit_plane_padded_forward(input),
    }
}

pub(crate) fn reverse(kind: TransformKind, input: &[u8]) -> Result<Vec<u8>> {
    Ok(match kind {
        TransformKind::Identity => input.to_vec(),
        TransformKind::Delta => {
            let mut out = Vec::with_capacity(input.len());
            let mut previous = 0u8;
            for (i, &delta) in input.iter().enumerate() {
                let value = if i == 0 {
                    delta
                } else {
                    previous.wrapping_add(delta)
                };
                out.push(value);
                previous = value;
            }
            out
        }
        TransformKind::Xor => {
            let mut out = Vec::with_capacity(input.len());
            let mut previous = 0u8;
            for (i, &delta) in input.iter().enumerate() {
                let value = if i == 0 { delta } else { previous ^ delta };
                out.push(value);
                previous = value;
            }
            out
        }
        TransformKind::BitPlane => bit_plane_reverse(input),
        TransformKind::Stride(stride) => {
            if !matches!(stride, 2 | 3 | 4 | 8 | 16 | 32 | 64) {
                return Err(Error::InvalidTransform("unsupported version-1 stride"));
            }
            stride_reverse(input, usize::from(stride))
        }
        TransformKind::BitPlanePadded => {
            return Err(Error::InvalidTransform(
                "bit-plane padded reverse requires expected output size",
            ));
        }
    })
}

pub(crate) fn reverse_with_output_size(
    kind: TransformKind,
    input: &[u8],
    expected_output_size: usize,
) -> Result<Vec<u8>> {
    if kind == TransformKind::BitPlanePadded {
        return bit_plane_padded_reverse(input, expected_output_size);
    }
    if input.len() != expected_output_size {
        return Err(Error::InvalidTransform(
            "length-preserving transform size mismatch",
        ));
    }
    reverse(kind, input)
}

/// Packs the eight logical planes consecutively without padding between them,
/// so the transformed stream always has exactly the input length.
fn bit_plane_forward(input: &[u8]) -> Vec<u8> {
    let mut out = vec![0u8; input.len()];
    let mut destination_byte = 0usize;
    let mut destination_bit = 0u8;
    for plane in 0..8usize {
        for &value in input {
            let bit = (value >> plane) & 1;
            out[destination_byte] |= bit << destination_bit;
            destination_bit += 1;
            if destination_bit == 8 {
                destination_bit = 0;
                destination_byte += 1;
            }
        }
    }
    out
}

fn bit_plane_reverse(input: &[u8]) -> Vec<u8> {
    let mut out = vec![0u8; input.len()];
    let mut source_byte = 0usize;
    let mut source_bit = 0u8;
    for plane in 0..8usize {
        for value in &mut out {
            let bit = (input[source_byte] >> source_bit) & 1;
            *value |= bit << plane;
            source_bit += 1;
            if source_bit == 8 {
                source_bit = 0;
                source_byte += 1;
            }
        }
    }
    out
}

/// Stores each logical plane in its own byte-aligned span. Bits within a plane
/// are LSB-first, and unused high bits in the final byte are canonical zeroes.
fn bit_plane_padded_forward(input: &[u8]) -> Vec<u8> {
    let plane_size = input.len().div_ceil(8);
    let transformed_size = plane_size
        .checked_mul(8)
        .expect("bit-plane padded output length overflow");
    let mut out = vec![0u8; transformed_size];
    for plane in 0..8usize {
        let plane_offset = plane * plane_size;
        for (index, &value) in input.iter().enumerate() {
            let bit = (value >> plane) & 1;
            out[plane_offset + index / 8] |= bit << (index % 8);
        }
    }
    out
}

fn bit_plane_padded_reverse(input: &[u8], expected_output_size: usize) -> Result<Vec<u8>> {
    let plane_size = expected_output_size.div_ceil(8);
    let expected_transformed_size = plane_size.checked_mul(8).ok_or(Error::IntegerOverflow)?;
    if input.len() != expected_transformed_size {
        return Err(Error::InvalidTransform(
            "bit-plane padded transformed length mismatch",
        ));
    }

    let remainder = expected_output_size % 8;
    if remainder != 0 {
        let valid_mask = ((1u16 << remainder) - 1) as u8;
        for plane in 0..8usize {
            let final_byte = input[plane * plane_size + plane_size - 1];
            if final_byte & !valid_mask != 0 {
                return Err(Error::InvalidTransform(
                    "nonzero bit-plane padded padding bits",
                ));
            }
        }
    }

    let mut out = vec![0u8; expected_output_size];
    for plane in 0..8usize {
        let plane_offset = plane * plane_size;
        for (index, value) in out.iter_mut().enumerate() {
            let bit = (input[plane_offset + index / 8] >> (index % 8)) & 1;
            *value |= bit << plane;
        }
    }
    Ok(out)
}

fn stride_forward(input: &[u8], stride: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(input.len());
    for column in 0..stride {
        let mut index = column;
        while index < input.len() {
            out.push(input[index]);
            index = match index.checked_add(stride) {
                Some(next) => next,
                None => break,
            };
        }
    }
    out
}

fn stride_reverse(input: &[u8], stride: usize) -> Vec<u8> {
    let mut out = vec![0u8; input.len()];
    let mut source = 0usize;
    for column in 0..stride {
        let mut index = column;
        while index < input.len() {
            out[index] = input[source];
            source += 1;
            index = match index.checked_add(stride) {
                Some(next) => next,
                None => break,
            };
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_transforms_round_trip_odd_sizes() {
        let source: Vec<u8> = (0..=250).map(|x| (x * 17) as u8).collect();
        for transform in [
            TransformKind::Identity,
            TransformKind::Delta,
            TransformKind::Xor,
            TransformKind::BitPlane,
            TransformKind::Stride(2),
            TransformKind::Stride(3),
            TransformKind::Stride(64),
        ] {
            let transformed = apply(transform, &source);
            assert_eq!(transformed.len(), source.len());
            assert_eq!(reverse(transform, &transformed).unwrap(), source);
        }
    }

    #[test]
    fn legacy_empty_transforms_round_trip() {
        for transform in [
            TransformKind::Identity,
            TransformKind::Delta,
            TransformKind::Xor,
            TransformKind::BitPlane,
            TransformKind::Stride(16),
        ] {
            assert_eq!(reverse(transform, &apply(transform, &[])).unwrap(), []);
        }
    }

    #[test]
    fn legacy_registry_and_packed_bit_plane_layout_are_unchanged() {
        let source = [0x01, 0x03, 0x02, 0xff];
        assert_eq!(
            apply(TransformKind::BitPlane, &source),
            [0xeb, 0x88, 0x88, 0x88]
        );
        for (transform, id) in [
            (TransformKind::Identity, 0),
            (TransformKind::Delta, 1),
            (TransformKind::Xor, 2),
            (TransformKind::BitPlane, 3),
            (TransformKind::Stride(16), 4),
        ] {
            assert_eq!(transform.id(), id);
            assert_eq!(transform.minimum_format_version(), 1);
            assert_eq!(
                TransformKind::from_descriptor(id, &transform.parameters()).unwrap(),
                transform
            );
        }
    }

    #[test]
    fn padded_bit_plane_round_trips_every_length_through_65() {
        for length in 0..=65usize {
            let source: Vec<u8> = (0..length)
                .map(|index| index.wrapping_mul(37).wrapping_add(length.wrapping_mul(11)) as u8)
                .collect();
            let transformed = apply(TransformKind::BitPlanePadded, &source);
            let plane_size = length.div_ceil(8);
            assert_eq!(transformed.len(), plane_size * 8, "length {length}");

            if length % 8 != 0 {
                let valid_mask = ((1u16 << (length % 8)) - 1) as u8;
                for plane in 0..8usize {
                    assert_eq!(
                        transformed[plane * plane_size + plane_size - 1] & !valid_mask,
                        0,
                        "length {length}, plane {plane}"
                    );
                }
            }

            assert_eq!(
                reverse_with_output_size(TransformKind::BitPlanePadded, &transformed, length)
                    .unwrap(),
                source,
                "length {length}"
            );
        }
    }

    #[test]
    fn padded_bit_plane_rejects_nonzero_padding() {
        for length in [1usize, 2, 7, 9, 15, 17, 65] {
            let source: Vec<u8> = (0..length).map(|index| index as u8).collect();
            let transformed = apply(TransformKind::BitPlanePadded, &source);
            let plane_size = length.div_ceil(8);
            let first_padding_bit = 1u8 << (length % 8);
            for plane in 0..8usize {
                let mut corrupted = transformed.clone();
                corrupted[plane * plane_size + plane_size - 1] |= first_padding_bit;
                assert!(matches!(
                    reverse_with_output_size(TransformKind::BitPlanePadded, &corrupted, length),
                    Err(Error::InvalidTransform(
                        "nonzero bit-plane padded padding bits"
                    ))
                ));
            }
        }
    }

    #[test]
    fn reverse_with_output_size_rejects_wrong_lengths() {
        let source = [3, 1, 4, 1, 5, 9, 2, 6, 5];
        let transformed = apply(TransformKind::BitPlanePadded, &source);

        assert!(matches!(
            reverse(TransformKind::BitPlanePadded, &transformed),
            Err(Error::InvalidTransform(
                "bit-plane padded reverse requires expected output size"
            ))
        ));

        let mut too_long = transformed.clone();
        too_long.push(0);
        assert!(matches!(
            reverse_with_output_size(TransformKind::BitPlanePadded, &too_long, source.len()),
            Err(Error::InvalidTransform(
                "bit-plane padded transformed length mismatch"
            ))
        ));
        assert!(matches!(
            reverse_with_output_size(
                TransformKind::BitPlanePadded,
                &transformed[..transformed.len() - 1],
                source.len()
            ),
            Err(Error::InvalidTransform(
                "bit-plane padded transformed length mismatch"
            ))
        ));
        assert!(matches!(
            reverse_with_output_size(TransformKind::Identity, &source, source.len() - 1),
            Err(Error::InvalidTransform(
                "length-preserving transform size mismatch"
            ))
        ));
    }

    #[test]
    fn padded_bit_plane_registry_requires_format_version_two() {
        assert_eq!(TransformKind::BitPlanePadded.id(), 5);
        assert_eq!(TransformKind::BitPlanePadded.parameters(), []);
        assert_eq!(TransformKind::BitPlanePadded.minimum_format_version(), 2);
        assert_eq!(
            TransformKind::from_descriptor(5, &[]).unwrap(),
            TransformKind::BitPlanePadded
        );
        assert!(matches!(
            TransformKind::from_descriptor(5, &[0]),
            Err(Error::InvalidTransform(
                "unexpected transform parameter length"
            ))
        ));
    }
}
