use crate::config::DecodeLimits;
use crate::models::ModelKind;
use crate::residual::{ResidualCoder, ResidualMode};
use crate::transforms::TransformKind;
use crate::{Error, Result};
use crc32fast::hash as crc32;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub const MAGIC: [u8; 4] = *b"MZIP";
pub const FORMAT_VERSION_V1: u16 = 1;
/// Latest format version emitted when a selected transform requires it.
pub const FORMAT_VERSION: u16 = 2;
pub(crate) const HEADER_SIZE: usize = 80;
pub(crate) const SEGMENT_DESCRIPTOR_SIZE: usize = 36;
pub(crate) const FOOTER_SIZE: usize = 36;
const FOOTER_MAGIC: [u8; 4] = *b"MZFT";

/// Checksum state for a successfully inspected archive. A normal parse rejects
/// invalid header/archive checksums; segment/original fields are populated after
/// bounded reconstruction by `inspect`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChecksumStatus {
    pub header: bool,
    pub archive: bool,
    pub segments: bool,
    pub original: bool,
}

/// Machine-readable archive metadata returned by [`crate::inspect`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ArchiveInfo {
    pub format_version: u16,
    pub original_size: u64,
    pub transformed_size: u64,
    pub compressed_size: u64,
    pub payload_size: u64,
    /// `original_size / compressed_size`; `None` for an empty original stream.
    pub compression_ratio: Option<f64>,
    pub transforms: Vec<TransformKind>,
    pub transform_count: u32,
    pub segment_count: u32,
    pub mean_segment_size: Option<f64>,
    pub median_segment_size: Option<f64>,
    /// Percentage of transformed bytes represented by the Raw predictor,
    /// regardless of how its residual is coded.
    pub raw_model_percentage: Option<f64>,
    /// Percentage of transformed bytes stored as Raw predictor + Raw residual.
    /// Raw predictor bytes compressed by another residual coder are excluded.
    pub raw_fallback_percentage: Option<f64>,
    pub model_distribution: BTreeMap<ModelKind, u64>,
    pub residual_distribution: BTreeMap<ResidualCoder, u64>,
    pub residual_mode_distribution: BTreeMap<ResidualMode, u64>,
    /// Header, footer, and transform descriptors. This deliberately excludes
    /// segment descriptors so storage-breakdown consumers can add
    /// `partition_metadata_bytes` without double-counting.
    pub container_overhead_bytes: u64,
    /// All non-model/non-residual bytes, including segment descriptors.
    pub metadata_bytes: u64,
    pub transform_metadata_bytes: u64,
    pub segment_descriptor_bytes: u64,
    /// Alias for the version-1 segment descriptor overhead.
    pub partition_metadata_bytes: u64,
    pub model_parameter_bytes: u64,
    pub residual_bytes: u64,
    pub actual_residual_coded_bytes: u64,
    /// Shannon entropy of the decoded residual byte stream, in bits/byte.
    pub estimated_residual_entropy: Option<f64>,
    /// Non-Raw segments whose parameter + residual body is smaller than a
    /// same-length Raw residual body (the fixed segment prefix is common).
    pub math_segments_winning_raw: u64,
    /// Non-Raw segments whose parameter + residual body is smaller than a
    /// level-3 Zstandard frame of the same transformed segment.
    pub math_segments_winning_zstd: u64,
    pub checksums: ChecksumStatus,
}

#[derive(Debug, Clone)]
pub(crate) struct EncodedSegment {
    pub offset: u64,
    pub length: u64,
    pub model: ModelKind,
    pub model_parameters: Vec<u8>,
    pub residual_mode: ResidualMode,
    pub residual_coder: ResidualCoder,
    pub residual: Vec<u8>,
    pub decoded_crc32: u32,
}

impl EncodedSegment {
    pub(crate) fn encoded_len(&self) -> usize {
        SEGMENT_DESCRIPTOR_SIZE
            .saturating_add(self.model_parameters.len())
            .saturating_add(self.residual.len())
    }
}

#[derive(Debug, Clone)]
pub(crate) struct Header {
    pub format_version: u16,
    pub original_size: u64,
    pub transformed_size: u64,
    pub payload_size: u64,
    pub transform_count: u32,
    pub segment_count: u32,
    pub original_sha256: [u8; 32],
}

#[derive(Debug, Clone)]
pub(crate) struct ParsedSegment<'a> {
    pub offset: u64,
    pub length: u64,
    pub model: ModelKind,
    pub model_parameters: &'a [u8],
    pub residual_mode: ResidualMode,
    pub residual_coder: ResidualCoder,
    pub residual: &'a [u8],
    pub decoded_crc32: u32,
}

#[derive(Debug, Clone)]
pub(crate) struct ParsedArchive<'a> {
    pub header: Header,
    pub transforms: Vec<TransformKind>,
    pub segments: Vec<ParsedSegment<'a>>,
    pub transform_metadata_bytes: u64,
}

pub(crate) fn serialize(
    original: &[u8],
    transformed_size: usize,
    transforms: &[TransformKind],
    segments: &[EncodedSegment],
) -> Result<Vec<u8>> {
    if transforms.is_empty() {
        return Err(Error::InvalidArchive("at least one transform is required"));
    }
    let format_version = transforms
        .iter()
        .map(|transform| transform.minimum_format_version())
        .max()
        .ok_or(Error::InvalidArchive("at least one transform is required"))?;
    if format_version > FORMAT_VERSION {
        return Err(Error::UnsupportedVersion(format_version));
    }
    let original_size = u64::try_from(original.len()).map_err(|_| Error::IntegerOverflow)?;
    let transformed_size = u64::try_from(transformed_size).map_err(|_| Error::IntegerOverflow)?;
    validate_version_sizes(format_version, original_size, transformed_size)?;
    validate_transform_set(format_version, transforms)?;

    let mut payload = Vec::new();
    for &transform in transforms {
        let params = transform.parameters();
        let param_len = u16::try_from(params.len()).map_err(|_| Error::IntegerOverflow)?;
        payload.push(transform.id());
        payload.push(0); // descriptor flags
        payload.extend_from_slice(&param_len.to_le_bytes());
        payload.extend_from_slice(&params);
    }
    for segment in segments {
        let param_len =
            u32::try_from(segment.model_parameters.len()).map_err(|_| Error::IntegerOverflow)?;
        let residual_len =
            u64::try_from(segment.residual.len()).map_err(|_| Error::IntegerOverflow)?;
        payload.extend_from_slice(&segment.offset.to_le_bytes());
        payload.extend_from_slice(&segment.length.to_le_bytes());
        payload.push(segment.model.id());
        payload.push(segment.residual_mode.id());
        payload.push(segment.residual_coder.id());
        payload.push(0); // descriptor flags
        payload.extend_from_slice(&param_len.to_le_bytes());
        payload.extend_from_slice(&residual_len.to_le_bytes());
        payload.extend_from_slice(&segment.decoded_crc32.to_le_bytes());
        payload.extend_from_slice(&segment.model_parameters);
        payload.extend_from_slice(&segment.residual);
    }
    let payload_size = u64::try_from(payload.len()).map_err(|_| Error::IntegerOverflow)?;
    let transform_count = u32::try_from(transforms.len()).map_err(|_| Error::IntegerOverflow)?;
    let segment_count = u32::try_from(segments.len()).map_err(|_| Error::IntegerOverflow)?;

    let mut original_hash = [0u8; 32];
    original_hash.copy_from_slice(&Sha256::digest(original));
    let mut out = Vec::with_capacity(
        HEADER_SIZE
            .saturating_add(payload.len())
            .saturating_add(FOOTER_SIZE),
    );
    out.extend_from_slice(&MAGIC);
    out.extend_from_slice(&format_version.to_le_bytes());
    out.extend_from_slice(&(HEADER_SIZE as u16).to_le_bytes());
    out.extend_from_slice(&0u32.to_le_bytes()); // flags
    out.extend_from_slice(&original_size.to_le_bytes());
    out.extend_from_slice(&transformed_size.to_le_bytes());
    out.extend_from_slice(&payload_size.to_le_bytes());
    out.extend_from_slice(&transform_count.to_le_bytes());
    out.extend_from_slice(&segment_count.to_le_bytes());
    out.extend_from_slice(&original_hash);
    debug_assert_eq!(out.len(), HEADER_SIZE - 4);
    let header_crc = crc32(&out);
    out.extend_from_slice(&header_crc.to_le_bytes());
    out.extend_from_slice(&payload);
    let archive_hash = Sha256::digest(&out);
    out.extend_from_slice(&FOOTER_MAGIC);
    out.extend_from_slice(&archive_hash);
    Ok(out)
}

pub(crate) fn parse<'a>(archive: &'a [u8], limits: &DecodeLimits) -> Result<ParsedArchive<'a>> {
    enforce_limit(
        "archive_size",
        archive.len() as u64,
        limits.max_archive_size,
    )?;
    if archive.len() < HEADER_SIZE + FOOTER_SIZE {
        return Err(Error::Truncated {
            context: "fixed header/footer",
        });
    }
    if archive[..4] != MAGIC {
        return Err(Error::InvalidMagic);
    }
    let version = read_u16(archive, 4, "format version")?;
    if !matches!(version, FORMAT_VERSION_V1 | FORMAT_VERSION) {
        return Err(Error::UnsupportedVersion(version));
    }
    if read_u16(archive, 6, "header length")? as usize != HEADER_SIZE {
        return Err(Error::InvalidArchive("unsupported header length"));
    }
    if read_u32(archive, 8, "header flags")? != 0 {
        return Err(Error::InvalidArchive("unsupported header flags"));
    }
    let expected_header_crc = read_u32(archive, HEADER_SIZE - 4, "header CRC")?;
    if crc32(&archive[..HEADER_SIZE - 4]) != expected_header_crc {
        return Err(Error::ChecksumMismatch { kind: "header" });
    }

    let original_size = read_u64(archive, 12, "original size")?;
    let transformed_size = read_u64(archive, 20, "transformed size")?;
    let payload_size = read_u64(archive, 28, "payload size")?;
    let transform_count = read_u32(archive, 36, "transform count")?;
    let segment_count = read_u32(archive, 40, "segment count")?;
    enforce_limit("output_size", original_size, limits.max_output_size)?;
    let transformed_size_limit = if version == FORMAT_VERSION_V1 {
        limits.max_output_size
    } else {
        maximum_padded_size_for_output_limit(limits.max_output_size)
    };
    enforce_limit("transformed_size", transformed_size, transformed_size_limit)?;
    enforce_limit(
        "transform_count",
        u64::from(transform_count),
        u64::from(limits.max_transforms),
    )?;
    enforce_limit(
        "segment_count",
        u64::from(segment_count),
        u64::from(limits.max_segments),
    )?;
    validate_version_sizes(version, original_size, transformed_size)?;
    if transform_count == 0 {
        return Err(Error::InvalidArchive("transform chain is empty"));
    }
    if version == FORMAT_VERSION && transform_count != 1 {
        return Err(Error::InvalidArchive(
            "version-2 requires exactly one transform",
        ));
    }
    if (original_size == 0) != (segment_count == 0) {
        return Err(Error::InvalidArchive("empty stream/segment count mismatch"));
    }

    let payload_len = usize::try_from(payload_size).map_err(|_| Error::IntegerOverflow)?;
    if u64::from(transform_count).saturating_mul(4) > payload_size {
        return Err(Error::InvalidArchive(
            "transform count cannot fit in payload",
        ));
    }
    let minimum_transform_bytes = u64::from(transform_count) * 4;
    let maximum_segment_descriptors =
        payload_size.saturating_sub(minimum_transform_bytes) / SEGMENT_DESCRIPTOR_SIZE as u64;
    if u64::from(segment_count) > maximum_segment_descriptors
        || (transformed_size > 0 && u64::from(segment_count) > transformed_size)
    {
        return Err(Error::InvalidArchive(
            "segment count cannot fit payload/output partition",
        ));
    }
    let footer_start = HEADER_SIZE
        .checked_add(payload_len)
        .ok_or(Error::IntegerOverflow)?;
    let expected_archive_len = footer_start
        .checked_add(FOOTER_SIZE)
        .ok_or(Error::IntegerOverflow)?;
    if archive.len() != expected_archive_len {
        return Err(Error::InvalidArchive("archive/payload length mismatch"));
    }
    if archive[footer_start..footer_start + 4] != FOOTER_MAGIC {
        return Err(Error::InvalidArchive("invalid footer magic"));
    }
    let archive_hash = Sha256::digest(&archive[..footer_start]);
    if archive[footer_start + 4..] != archive_hash[..] {
        return Err(Error::ChecksumMismatch { kind: "archive" });
    }

    let mut original_sha256 = [0u8; 32];
    original_sha256.copy_from_slice(&archive[44..76]);
    let header = Header {
        format_version: version,
        original_size,
        transformed_size,
        payload_size,
        transform_count,
        segment_count,
        original_sha256,
    };

    let mut cursor = HEADER_SIZE;
    let mut transforms = Vec::with_capacity(transform_count as usize);
    for _ in 0..transform_count {
        let id = read_byte(archive, &mut cursor, footer_start, "transform type")?;
        let flags = read_byte(archive, &mut cursor, footer_start, "transform flags")?;
        if flags != 0 {
            return Err(Error::InvalidArchive("unsupported transform flags"));
        }
        let parameter_len = read_u16_cursor(archive, &mut cursor, footer_start)? as usize;
        let end = cursor
            .checked_add(parameter_len)
            .ok_or(Error::IntegerOverflow)?;
        let parameters = archive.get(cursor..end).ok_or(Error::Truncated {
            context: "transform parameters",
        })?;
        if end > footer_start {
            return Err(Error::Truncated {
                context: "transform parameters",
            });
        }
        transforms.push(TransformKind::from_descriptor(id, parameters)?);
        cursor = end;
    }
    validate_transform_set(version, &transforms)?;
    // Bound aggregate reverse-transform work, not just descriptor count.
    // Bit-plane reversal visits eight logical bits per byte; the other v1
    // transforms are linear byte passes. The default allowance therefore
    // admits one worst-case transform at max_output_size or several cheaper
    // transforms, while rejecting a tiny archive that requests a long,
    // expensive chain over a huge constant output.
    let transform_weight = transforms.iter().try_fold(0u64, |total, transform| {
        let weight = if matches!(
            transform,
            TransformKind::BitPlane | TransformKind::BitPlanePadded
        ) {
            8
        } else {
            1
        };
        total.checked_add(weight).ok_or(Error::IntegerOverflow)
    })?;
    let transform_work = transformed_size
        .checked_mul(transform_weight)
        .ok_or(Error::IntegerOverflow)?;
    let transform_work_limit = limits.max_output_size;
    let transform_work_limit = if version == FORMAT_VERSION_V1 {
        transform_work_limit
    } else {
        transformed_size_limit
    }
    .checked_mul(8)
    .ok_or(Error::IntegerOverflow)?;
    enforce_limit(
        "aggregate_transform_work",
        transform_work,
        transform_work_limit,
    )?;
    let transform_metadata_bytes = (cursor - HEADER_SIZE) as u64;

    // Do not allocate from an attacker-controlled count before parsing its
    // corresponding bounded descriptors.
    let mut segments = Vec::new();
    let mut expected_offset = 0u64;
    let mut parameter_total = 0u64;
    let mut residual_total = 0u64;
    for _ in 0..segment_count {
        if footer_start.saturating_sub(cursor) < SEGMENT_DESCRIPTOR_SIZE {
            return Err(Error::Truncated {
                context: "segment descriptor",
            });
        }
        let offset = read_u64(archive, cursor, "segment offset")?;
        let length = read_u64(archive, cursor + 8, "segment length")?;
        let model = ModelKind::from_id(archive[cursor + 16])?;
        let residual_mode = ResidualMode::from_id(archive[cursor + 17])?;
        let residual_coder = ResidualCoder::from_id(archive[cursor + 18])?;
        if archive[cursor + 19] != 0 {
            return Err(Error::InvalidArchive("unsupported segment flags"));
        }
        let parameter_len = u64::from(read_u32(archive, cursor + 20, "model parameter length")?);
        let residual_len = read_u64(archive, cursor + 24, "residual length")?;
        let decoded_crc32 = read_u32(archive, cursor + 32, "segment CRC")?;
        cursor += SEGMENT_DESCRIPTOR_SIZE;

        if length == 0 || offset != expected_offset {
            return Err(Error::InvalidArchive(
                "segments are not a contiguous partition",
            ));
        }
        expected_offset = offset.checked_add(length).ok_or(Error::IntegerOverflow)?;
        if expected_offset > transformed_size {
            return Err(Error::InvalidArchive("segment exceeds transformed stream"));
        }
        parameter_total = parameter_total
            .checked_add(parameter_len)
            .ok_or(Error::IntegerOverflow)?;
        residual_total = residual_total
            .checked_add(residual_len)
            .ok_or(Error::IntegerOverflow)?;
        enforce_limit(
            "model_parameter_bytes",
            parameter_total,
            limits.max_model_parameter_bytes,
        )?;
        enforce_limit("residual_bytes", residual_total, limits.max_residual_bytes)?;

        let parameter_len = usize::try_from(parameter_len).map_err(|_| Error::IntegerOverflow)?;
        let residual_len = usize::try_from(residual_len).map_err(|_| Error::IntegerOverflow)?;
        let params_end = cursor
            .checked_add(parameter_len)
            .ok_or(Error::IntegerOverflow)?;
        let residual_end = params_end
            .checked_add(residual_len)
            .ok_or(Error::IntegerOverflow)?;
        if residual_end > footer_start {
            return Err(Error::Truncated {
                context: "segment payload",
            });
        }
        segments.push(ParsedSegment {
            offset,
            length,
            model,
            model_parameters: &archive[cursor..params_end],
            residual_mode,
            residual_coder,
            residual: &archive[params_end..residual_end],
            decoded_crc32,
        });
        cursor = residual_end;
    }
    if cursor != footer_start || expected_offset != transformed_size {
        return Err(Error::InvalidArchive(
            "payload trailing bytes or partition size mismatch",
        ));
    }
    Ok(ParsedArchive {
        header,
        transforms,
        segments,
        transform_metadata_bytes,
    })
}

fn validate_version_sizes(version: u16, original_size: u64, transformed_size: u64) -> Result<()> {
    match version {
        FORMAT_VERSION_V1 => {
            if transformed_size != original_size {
                return Err(Error::InvalidArchive(
                    "version-1 transforms must preserve byte length",
                ));
            }
        }
        FORMAT_VERSION => {
            let expected = bit_plane_padded_size(original_size)?;
            if transformed_size != expected {
                return Err(Error::InvalidArchive(
                    "version-2 padded transform size mismatch",
                ));
            }
        }
        version => return Err(Error::UnsupportedVersion(version)),
    }
    Ok(())
}

fn validate_transform_set(version: u16, transforms: &[TransformKind]) -> Result<()> {
    if transforms
        .iter()
        .any(|transform| transform.minimum_format_version() > version)
    {
        return Err(Error::InvalidTransform(
            "transform requires a newer format version",
        ));
    }
    if version == FORMAT_VERSION && !matches!(transforms, [TransformKind::BitPlanePadded]) {
        return Err(Error::InvalidArchive(
            "version-2 requires exactly one padded bit-plane transform",
        ));
    }
    Ok(())
}

fn bit_plane_padded_size(original_size: u64) -> Result<u64> {
    let block_count = original_size
        .checked_div(8)
        .and_then(|blocks| blocks.checked_add(u64::from(original_size % 8 != 0)))
        .ok_or(Error::IntegerOverflow)?;
    block_count.checked_mul(8).ok_or(Error::IntegerOverflow)
}

/// Largest representable padded stream for any original stream no larger than
/// `output_limit`. Near `u64::MAX`, the mathematical round-up is not
/// representable, so the greatest lower multiple of eight is the safe limit.
fn maximum_padded_size_for_output_limit(output_limit: u64) -> u64 {
    let remainder = output_limit % 8;
    if remainder == 0 {
        output_limit
    } else {
        output_limit
            .checked_add(8 - remainder)
            .unwrap_or(output_limit - remainder)
    }
}

fn enforce_limit(what: &'static str, actual: u64, limit: u64) -> Result<()> {
    if actual > limit {
        Err(Error::LimitExceeded {
            what,
            actual,
            limit,
        })
    } else {
        Ok(())
    }
}

fn read_byte(input: &[u8], cursor: &mut usize, end: usize, context: &'static str) -> Result<u8> {
    if *cursor >= end {
        return Err(Error::Truncated { context });
    }
    let value = input[*cursor];
    *cursor += 1;
    Ok(value)
}

fn read_u16_cursor(input: &[u8], cursor: &mut usize, end: usize) -> Result<u16> {
    if end.saturating_sub(*cursor) < 2 {
        return Err(Error::Truncated {
            context: "u16 field",
        });
    }
    let value = read_u16(input, *cursor, "u16 field")?;
    *cursor += 2;
    Ok(value)
}

fn read_u16(input: &[u8], offset: usize, context: &'static str) -> Result<u16> {
    let bytes: [u8; 2] = input
        .get(offset..offset + 2)
        .ok_or(Error::Truncated { context })?
        .try_into()
        .map_err(|_| Error::Truncated { context })?;
    Ok(u16::from_le_bytes(bytes))
}

fn read_u32(input: &[u8], offset: usize, context: &'static str) -> Result<u32> {
    let bytes: [u8; 4] = input
        .get(offset..offset + 4)
        .ok_or(Error::Truncated { context })?
        .try_into()
        .map_err(|_| Error::Truncated { context })?;
    Ok(u32::from_le_bytes(bytes))
}

fn read_u64(input: &[u8], offset: usize, context: &'static str) -> Result<u64> {
    let bytes: [u8; 8] = input
        .get(offset..offset + 8)
        .ok_or(Error::Truncated { context })?
        .try_into()
        .map_err(|_| Error::Truncated { context })?;
    Ok(u64::from_le_bytes(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn raw_segment(bytes: &[u8]) -> EncodedSegment {
        EncodedSegment {
            offset: 0,
            length: bytes.len() as u64,
            model: ModelKind::Raw,
            model_parameters: Vec::new(),
            residual_mode: ResidualMode::AddModulo,
            residual_coder: ResidualCoder::Raw,
            residual: bytes.to_vec(),
            decoded_crc32: crc32(bytes),
        }
    }

    fn rewrite_checksums(archive: &mut [u8]) {
        let header_crc = crc32(&archive[..HEADER_SIZE - 4]);
        archive[HEADER_SIZE - 4..HEADER_SIZE].copy_from_slice(&header_crc.to_le_bytes());
        let footer_start = archive.len() - FOOTER_SIZE;
        let archive_hash = Sha256::digest(&archive[..footer_start]);
        archive[footer_start + 4..].copy_from_slice(&archive_hash);
    }

    #[test]
    fn legacy_transform_selects_version_one() {
        let original = [0u8, 1, 2, 3];
        let archive = serialize(
            &original,
            original.len(),
            &[TransformKind::Identity],
            &[raw_segment(&original)],
        )
        .unwrap();
        assert_eq!(
            u16::from_le_bytes(archive[4..6].try_into().unwrap()),
            FORMAT_VERSION_V1
        );

        let parsed = parse(&archive, &DecodeLimits::default()).unwrap();
        assert_eq!(parsed.header.format_version, FORMAT_VERSION_V1);
        assert_eq!(parsed.header.original_size, original.len() as u64);
        assert_eq!(parsed.header.transformed_size, original.len() as u64);
    }

    #[test]
    fn padded_bit_plane_selects_version_two_and_uses_padded_limit() {
        let original: Vec<u8> = (0..9).collect();
        let transformed = crate::transforms::apply(TransformKind::BitPlanePadded, &original);
        assert_eq!(transformed.len(), 16);
        let archive = serialize(
            &original,
            transformed.len(),
            &[TransformKind::BitPlanePadded],
            &[raw_segment(&transformed)],
        )
        .unwrap();
        assert_eq!(
            u16::from_le_bytes(archive[4..6].try_into().unwrap()),
            FORMAT_VERSION
        );

        let limits = DecodeLimits {
            max_archive_size: archive.len() as u64,
            max_output_size: original.len() as u64,
            ..DecodeLimits::default()
        };
        let parsed = parse(&archive, &limits).unwrap();
        assert_eq!(parsed.header.format_version, FORMAT_VERSION);
        assert_eq!(parsed.header.original_size, 9);
        assert_eq!(parsed.header.transformed_size, 16);
        assert_eq!(parsed.transforms, [TransformKind::BitPlanePadded]);
    }

    #[test]
    fn empty_version_two_archive_is_valid() {
        let archive = serialize(&[], 0, &[TransformKind::BitPlanePadded], &[]).unwrap();
        let limits = DecodeLimits {
            max_archive_size: archive.len() as u64,
            max_output_size: 0,
            ..DecodeLimits::default()
        };
        let parsed = parse(&archive, &limits).unwrap();
        assert_eq!(parsed.header.original_size, 0);
        assert_eq!(parsed.header.transformed_size, 0);
        assert!(parsed.segments.is_empty());
    }

    #[test]
    fn transform_registry_is_gated_by_archive_version() {
        let original = [0u8; 8];
        let transformed = crate::transforms::apply(TransformKind::BitPlanePadded, &original);
        let mut padded_archive = serialize(
            &original,
            transformed.len(),
            &[TransformKind::BitPlanePadded],
            &[raw_segment(&transformed)],
        )
        .unwrap();
        padded_archive[4..6].copy_from_slice(&FORMAT_VERSION_V1.to_le_bytes());
        rewrite_checksums(&mut padded_archive);
        assert!(matches!(
            parse(&padded_archive, &DecodeLimits::default()),
            Err(Error::InvalidTransform(
                "transform requires a newer format version"
            ))
        ));

        let mut legacy_archive = serialize(
            &original,
            original.len(),
            &[TransformKind::Identity],
            &[raw_segment(&original)],
        )
        .unwrap();
        legacy_archive[4..6].copy_from_slice(&FORMAT_VERSION.to_le_bytes());
        rewrite_checksums(&mut legacy_archive);
        assert!(matches!(
            parse(&legacy_archive, &DecodeLimits::default()),
            Err(Error::InvalidArchive(
                "version-2 requires exactly one padded bit-plane transform"
            ))
        ));

        assert!(matches!(
            serialize(
                &original,
                transformed.len(),
                &[TransformKind::BitPlanePadded, TransformKind::Identity],
                &[raw_segment(&transformed)]
            ),
            Err(Error::InvalidArchive(
                "version-2 requires exactly one padded bit-plane transform"
            ))
        ));
    }

    #[test]
    fn malformed_version_two_sizes_are_rejected() {
        let original: Vec<u8> = (0..9).collect();
        let transformed = crate::transforms::apply(TransformKind::BitPlanePadded, &original);
        assert!(matches!(
            serialize(
                &original,
                transformed.len() - 1,
                &[TransformKind::BitPlanePadded],
                &[raw_segment(&transformed)]
            ),
            Err(Error::InvalidArchive(
                "version-2 padded transform size mismatch"
            ))
        ));

        let archive = serialize(
            &original,
            transformed.len(),
            &[TransformKind::BitPlanePadded],
            &[raw_segment(&transformed)],
        )
        .unwrap();
        for malformed_size in [15u64, 17, 24] {
            let mut malformed = archive.clone();
            malformed[20..28].copy_from_slice(&malformed_size.to_le_bytes());
            rewrite_checksums(&mut malformed);
            assert!(matches!(
                parse(&malformed, &DecodeLimits::default()),
                Err(Error::InvalidArchive(
                    "version-2 padded transform size mismatch"
                ))
            ));
        }

        let mut overflowing = archive;
        overflowing[12..20].copy_from_slice(&u64::MAX.to_le_bytes());
        overflowing[20..28].copy_from_slice(&(u64::MAX - 7).to_le_bytes());
        rewrite_checksums(&mut overflowing);
        let limits = DecodeLimits {
            max_archive_size: overflowing.len() as u64,
            max_output_size: u64::MAX,
            ..DecodeLimits::default()
        };
        assert!(matches!(
            parse(&overflowing, &limits),
            Err(Error::IntegerOverflow)
        ));
    }

    #[test]
    fn aggregate_transform_work_is_bounded() {
        let original = vec![7u8; 1024];
        let segment = raw_segment(&original);
        let archive = serialize(
            &original,
            original.len(),
            &[TransformKind::BitPlane, TransformKind::BitPlane],
            &[segment],
        )
        .unwrap();
        let limits = DecodeLimits {
            max_archive_size: archive.len() as u64,
            max_output_size: original.len() as u64,
            ..DecodeLimits::default()
        };
        assert!(matches!(
            parse(&archive, &limits),
            Err(Error::LimitExceeded {
                what: "aggregate_transform_work",
                ..
            })
        ));
    }
}
