use mathsvg_core::{Error, Limits, Result};

use crate::{CoordinateDescriptor, CoordinateTransform, Endianness};

pub fn forward(
    original: &[u8],
    descriptor: &CoordinateDescriptor,
    limits: &Limits,
) -> Result<Vec<u8>> {
    descriptor.validate(limits)?;
    require_length(
        "coordinate forward input",
        descriptor.original_bytes,
        original.len(),
    )?;
    let expected = descriptor.transformed_bytes()?;
    let mut output = allocate_zeroed(expected)?;

    match descriptor.transform {
        CoordinateTransform::Identity => output.copy_from_slice(original),
        CoordinateTransform::Stride {
            element_width,
            channels,
        } => stride_forward(
            original,
            &mut output,
            usize::from(element_width),
            usize::from(channels),
        )?,
        CoordinateTransform::BytePlane { word_width, endian } => {
            byte_plane_forward(original, &mut output, usize::from(word_width), endian)?
        }
        CoordinateTransform::BitPlane => bit_plane_forward(original, &mut output)?,
    }
    Ok(output)
}

pub fn inverse(
    transformed: &[u8],
    descriptor: &CoordinateDescriptor,
    limits: &Limits,
) -> Result<Vec<u8>> {
    let mut output = allocate_zeroed(descriptor.original_bytes)?;
    inverse_into(transformed, descriptor, &mut output, limits)?;
    Ok(output)
}

/// Invert a coordinate transform directly into a caller-owned exact-size
/// destination. This is the evaluator schedule used to avoid a second output
/// scratch allocation.
pub fn inverse_into(
    transformed: &[u8],
    descriptor: &CoordinateDescriptor,
    output: &mut [u8],
    limits: &Limits,
) -> Result<()> {
    descriptor.validate(limits)?;
    require_length(
        "coordinate inverse input",
        descriptor.transformed_bytes()?,
        transformed.len(),
    )?;
    require_length(
        "coordinate inverse output",
        descriptor.original_bytes,
        output.len(),
    )?;
    match descriptor.transform {
        CoordinateTransform::Identity => output.copy_from_slice(transformed),
        CoordinateTransform::Stride {
            element_width,
            channels,
        } => stride_inverse(
            transformed,
            output,
            usize::from(element_width),
            usize::from(channels),
        )?,
        CoordinateTransform::BytePlane { word_width, endian } => {
            byte_plane_inverse(transformed, output, usize::from(word_width), endian)?
        }
        CoordinateTransform::BitPlane => bit_plane_inverse(transformed, output)?,
    }
    Ok(())
}

fn stride_forward(input: &[u8], output: &mut [u8], width: usize, channels: usize) -> Result<()> {
    let record_bytes = width.checked_mul(channels).ok_or(Error::IntegerOverflow {
        context: "STRIDE record bytes",
    })?;
    let records = input.len() / record_bytes;
    let mut destination = 0usize;
    for channel in 0..channels {
        for record in 0..records {
            let source = record
                .checked_mul(record_bytes)
                .and_then(|offset| offset.checked_add(channel * width))
                .ok_or(Error::IntegerOverflow {
                    context: "STRIDE source offset",
                })?;
            copy_range(input, source, output, destination, width, "STRIDE forward")?;
            destination = destination
                .checked_add(width)
                .ok_or(Error::IntegerOverflow {
                    context: "STRIDE destination offset",
                })?;
        }
    }
    require_length("STRIDE forward output", input.len() as u64, destination)
}

fn stride_inverse(input: &[u8], output: &mut [u8], width: usize, channels: usize) -> Result<()> {
    let record_bytes = width.checked_mul(channels).ok_or(Error::IntegerOverflow {
        context: "STRIDE record bytes",
    })?;
    let records = output.len() / record_bytes;
    let lane_bytes = records.checked_mul(width).ok_or(Error::IntegerOverflow {
        context: "STRIDE lane bytes",
    })?;
    for channel in 0..channels {
        for record in 0..records {
            let source = channel
                .checked_mul(lane_bytes)
                .and_then(|offset| offset.checked_add(record * width))
                .ok_or(Error::IntegerOverflow {
                    context: "STRIDE inverse source offset",
                })?;
            let destination = record
                .checked_mul(record_bytes)
                .and_then(|offset| offset.checked_add(channel * width))
                .ok_or(Error::IntegerOverflow {
                    context: "STRIDE inverse destination offset",
                })?;
            copy_range(input, source, output, destination, width, "STRIDE inverse")?;
        }
    }
    Ok(())
}

fn byte_plane_forward(
    input: &[u8],
    output: &mut [u8],
    width: usize,
    endian: Endianness,
) -> Result<()> {
    let words = input.len() / width;
    for significance in 0..width {
        let storage_byte = storage_byte(significance, width, endian);
        for word in 0..words {
            let source = checked_index(word, width, storage_byte, "BYTE_PLANE source")?;
            let destination = checked_index(significance, words, word, "BYTE_PLANE destination")?;
            let value = input
                .get(source)
                .copied()
                .ok_or(Error::InvalidValue("BYTE_PLANE source"))?;
            let target = output
                .get_mut(destination)
                .ok_or(Error::InvalidValue("BYTE_PLANE destination"))?;
            *target = value;
        }
    }
    Ok(())
}

fn byte_plane_inverse(
    input: &[u8],
    output: &mut [u8],
    width: usize,
    endian: Endianness,
) -> Result<()> {
    let words = output.len() / width;
    for significance in 0..width {
        let storage_byte = storage_byte(significance, width, endian);
        for word in 0..words {
            let source = checked_index(significance, words, word, "BYTE_PLANE inverse source")?;
            let destination =
                checked_index(word, width, storage_byte, "BYTE_PLANE inverse destination")?;
            let value = input
                .get(source)
                .copied()
                .ok_or(Error::InvalidValue("BYTE_PLANE inverse source"))?;
            let target = output
                .get_mut(destination)
                .ok_or(Error::InvalidValue("BYTE_PLANE inverse destination"))?;
            *target = value;
        }
    }
    Ok(())
}

fn storage_byte(significance: usize, width: usize, endian: Endianness) -> usize {
    match endian {
        Endianness::Little => significance,
        Endianness::Big => width - 1 - significance,
    }
}

fn bit_plane_forward(input: &[u8], output: &mut [u8]) -> Result<()> {
    let plane_bytes = output.len() / 8;
    for (index, value) in input.iter().copied().enumerate() {
        let packed_byte = index / 8;
        let packed_bit = index % 8;
        for plane in 0..8usize {
            if value & (1u8 << plane) != 0 {
                let destination =
                    checked_index(plane, plane_bytes, packed_byte, "BIT_PLANE destination")?;
                let target = output
                    .get_mut(destination)
                    .ok_or(Error::InvalidValue("BIT_PLANE destination"))?;
                *target |= 1u8 << packed_bit;
            }
        }
    }
    Ok(())
}

fn bit_plane_inverse(input: &[u8], output: &mut [u8]) -> Result<()> {
    let plane_bytes = input.len() / 8;
    validate_padding(input, output.len(), plane_bytes)?;
    for (index, value) in output.iter_mut().enumerate() {
        let packed_byte = index / 8;
        let packed_bit = index % 8;
        for plane in 0..8usize {
            let source = checked_index(plane, plane_bytes, packed_byte, "BIT_PLANE source")?;
            let packed = input
                .get(source)
                .copied()
                .ok_or(Error::InvalidValue("BIT_PLANE source"))?;
            let bit = (packed >> packed_bit) & 1;
            *value |= bit << plane;
        }
    }
    Ok(())
}

fn validate_padding(input: &[u8], original_bytes: usize, plane_bytes: usize) -> Result<()> {
    let remainder = original_bytes % 8;
    if remainder == 0 {
        return Ok(());
    }
    let allowed = (1u8 << remainder) - 1;
    for plane in 0..8usize {
        let last = checked_index(plane, plane_bytes, plane_bytes - 1, "BIT_PLANE padding")?;
        let value = input
            .get(last)
            .copied()
            .ok_or(Error::InvalidValue("BIT_PLANE padding"))?;
        if value & !allowed != 0 {
            return Err(Error::InvalidValue(
                "BIT_PLANE high padding bits must be zero",
            ));
        }
    }
    Ok(())
}

fn checked_index(
    outer: usize,
    stride: usize,
    inner: usize,
    context: &'static str,
) -> Result<usize> {
    outer
        .checked_mul(stride)
        .and_then(|value| value.checked_add(inner))
        .ok_or(Error::IntegerOverflow { context })
}

fn copy_range(
    source: &[u8],
    source_offset: usize,
    destination: &mut [u8],
    destination_offset: usize,
    length: usize,
    context: &'static str,
) -> Result<()> {
    let source_end = source_offset
        .checked_add(length)
        .ok_or(Error::IntegerOverflow { context })?;
    let destination_end = destination_offset
        .checked_add(length)
        .ok_or(Error::IntegerOverflow { context })?;
    let source = source
        .get(source_offset..source_end)
        .ok_or(Error::InvalidValue(context))?;
    let destination = destination
        .get_mut(destination_offset..destination_end)
        .ok_or(Error::InvalidValue(context))?;
    destination.copy_from_slice(source);
    Ok(())
}

fn allocate_zeroed(length: u64) -> Result<Vec<u8>> {
    let length = usize::try_from(length).map_err(|_| Error::LimitExceeded {
        what: "addressable coordinate bytes",
        actual: length,
        limit: usize::MAX as u64,
    })?;
    let mut output = Vec::new();
    output
        .try_reserve_exact(length)
        .map_err(|_| Error::InvalidValue("coordinate allocation failed"))?;
    output.resize(length, 0);
    Ok(output)
}

fn require_length(context: &'static str, expected: u64, actual: usize) -> Result<()> {
    if actual as u64 == expected {
        Ok(())
    } else {
        Err(Error::LengthMismatch {
            context,
            expected,
            actual: actual as u64,
        })
    }
}
