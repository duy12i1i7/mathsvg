use crate::config::{DecodeLimits, EncodeOptions, Mode, ModelOptions, SegmentationMode};
use crate::container::{
    self, ArchiveInfo, ChecksumStatus, EncodedSegment, ParsedArchive, FOOTER_SIZE, HEADER_SIZE,
    SEGMENT_DESCRIPTOR_SIZE,
};
use crate::models::{self, Model};
use crate::residual::{self, ResidualCoder, ResidualMode};
use crate::segmentation;
use crate::transforms::{self, TransformKind};
use crate::{Error, Result};
use crc32fast::hash as crc32;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::ops::Range;
use std::rc::Rc;
use std::time::{Duration, Instant};

// Persistent recursive nodes contain four usize fields. This cap bounds that
// storage to about 61 MiB on 64-bit targets before Vec bookkeeping; exceeding
// it abandons the searched transform. The ordinary Raw fallback wins when
// enabled; an ablation that disables fallback receives NoRepresentation.
const MAX_RECURSIVE_DP_STATES: usize = 2_000_000;
/// Keep default encoder output consumable by the default decoder.
const MAX_DEFAULT_ARCHIVE_SEGMENTS: usize = 65_536;
/// Standard Balanced/Max inputs at or above this size use a deterministic
/// screened portfolio. Custom/ablation options retain the exhaustive path.
const LARGE_INPUT_THRESHOLD: usize = 1024 * 1024;
const TRANSFORM_SCREEN_WINDOWS: usize = 16;
const TRANSFORM_SCREEN_WINDOW_SIZE: usize = 64 * 1024;
const MODEL_SCREEN_WINDOWS: usize = 4;
const MODEL_SCREEN_WINDOW_SIZE: usize = 256;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct LargeInputPlan {
    transform_finalists: usize,
    fixed_segment_size: usize,
    model_mode_finalists: usize,
}

impl LargeInputPlan {
    const BALANCED: Self = Self {
        transform_finalists: 3,
        fixed_segment_size: 64 * 1024,
        model_mode_finalists: 2,
    };
    const MAX: Self = Self {
        transform_finalists: 5,
        fixed_segment_size: 256 * 1024,
        model_mode_finalists: 3,
    };

    fn for_input(input_len: usize, options: &EncodeOptions) -> Option<Self> {
        if input_len < LARGE_INPUT_THRESHOLD || !options.large_input_screening {
            return None;
        }
        if options == &EncodeOptions::for_mode(Mode::Balanced) {
            Some(Self::BALANCED)
        } else if options == &EncodeOptions::for_mode(Mode::Max) {
            Some(Self::MAX)
        } else {
            None
        }
    }

    fn effective_options(self, options: &EncodeOptions) -> EncodeOptions {
        let mut effective = options.clone();
        // Keep the profile's configured segmentation semantics.  The larger
        // regular-anchor spacing bounds the Adaptive candidate graph without
        // silently turning an Adaptive CLI/API request into Fixed
        // segmentation.
        effective.fixed_segment_size = self.fixed_segment_size;
        effective
    }
}

/// Non-overlapping encoder phase timings. These values are diagnostic only and
/// are never serialized into the deterministic archive.
#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
pub struct EncodeMetrics {
    pub search_seconds: f64,
    pub model_fitting_seconds: f64,
    pub residual_coding_seconds: f64,
    /// True when the deterministic large-input portfolio was used.
    #[serde(default)]
    pub screened_large_input: bool,
    /// Requested public search profile.
    #[serde(default)]
    pub requested_mode: Mode,
    /// Segmentation policy actually used after deterministic budget
    /// adjustments. This remains Adaptive for standard Balanced/Max.
    #[serde(default)]
    pub effective_segmentation: SegmentationMode,
    /// Regular-anchor spacing of the requested mode's primary frontier.
    #[serde(default)]
    pub primary_regular_anchor_bytes: usize,
    /// Maximum number of transforms retained in the primary frontier.
    #[serde(default)]
    pub primary_transform_finalist_limit: Option<usize>,
    /// Maximum number of non-Raw model/residual-mode pairs encoded exactly per
    /// segment in the primary frontier.
    #[serde(default)]
    pub primary_model_mode_finalist_limit: Option<usize>,
    /// Max evaluates the complete Balanced screened frontier as an additional
    /// candidate, ensuring it cannot publish a larger archive than Balanced.
    #[serde(default)]
    pub includes_balanced_frontier: bool,
    /// Regular-anchor spacing of the additional Balanced frontier, when one
    /// was evaluated.
    #[serde(default)]
    pub balanced_frontier_regular_anchor_bytes: Option<usize>,
    /// Transform and model-mode limits of the additional Balanced frontier.
    #[serde(default)]
    pub balanced_frontier_transform_finalist_limit: Option<usize>,
    #[serde(default)]
    pub balanced_frontier_model_mode_finalist_limit: Option<usize>,
    /// True when that Balanced frontier supplied the selected archive (ties
    /// intentionally retain Balanced).
    #[serde(default)]
    pub selected_balanced_frontier: bool,
    /// Effective settings of the frontier that supplied the selected archive.
    #[serde(default)]
    pub selected_regular_anchor_bytes: usize,
    #[serde(default)]
    pub selected_transform_finalist_limit: Option<usize>,
    #[serde(default)]
    pub selected_model_mode_finalist_limit: Option<usize>,
}

#[derive(Debug, Default)]
struct EncodeMetricDurations {
    model_fitting: Duration,
    residual_coding: Duration,
    selected_balanced_frontier: bool,
}

/// Compresses one arbitrary byte stream into a self-contained MathZip archive.
///
/// Search is deterministic: equal-size choices retain the earlier transform,
/// lower model ID, AddModulo residual mode, and lower residual-coder ID.
pub fn compress(input: &[u8], options: &EncodeOptions) -> Result<Vec<u8>> {
    compress_internal(input, options, None)
}

/// Compresses and also returns diagnostic phase timings for benchmark use.
pub fn compress_with_metrics(
    input: &[u8],
    options: &EncodeOptions,
) -> Result<(Vec<u8>, EncodeMetrics)> {
    let plan = LargeInputPlan::for_input(input.len(), options);
    let effective_options = plan.map(|plan| plan.effective_options(options));
    let effective_options = effective_options.as_ref().unwrap_or(options);
    let started = Instant::now();
    let mut metric_durations = EncodeMetricDurations::default();
    let archive = compress_internal(input, options, Some(&mut metric_durations))?;
    let total = started.elapsed();
    let measured_phases = metric_durations
        .model_fitting
        .saturating_add(metric_durations.residual_coding);
    let search = total.saturating_sub(measured_phases);
    let selected_plan = if metric_durations.selected_balanced_frontier {
        Some(LargeInputPlan::BALANCED)
    } else {
        plan
    };
    let selected_options = selected_plan.map(|plan| plan.effective_options(options));
    let selected_options = selected_options.as_ref().unwrap_or(options);
    Ok((
        archive,
        EncodeMetrics {
            search_seconds: search.as_secs_f64(),
            model_fitting_seconds: metric_durations.model_fitting.as_secs_f64(),
            residual_coding_seconds: metric_durations.residual_coding.as_secs_f64(),
            screened_large_input: plan.is_some(),
            requested_mode: options.mode,
            effective_segmentation: effective_options.segmentation,
            primary_regular_anchor_bytes: effective_options.fixed_segment_size,
            primary_transform_finalist_limit: plan.map(|plan| plan.transform_finalists),
            primary_model_mode_finalist_limit: plan.map(|plan| plan.model_mode_finalists),
            includes_balanced_frontier: plan == Some(LargeInputPlan::MAX),
            balanced_frontier_regular_anchor_bytes: (plan == Some(LargeInputPlan::MAX))
                .then_some(LargeInputPlan::BALANCED.fixed_segment_size),
            balanced_frontier_transform_finalist_limit: (plan == Some(LargeInputPlan::MAX))
                .then_some(LargeInputPlan::BALANCED.transform_finalists),
            balanced_frontier_model_mode_finalist_limit: (plan == Some(LargeInputPlan::MAX))
                .then_some(LargeInputPlan::BALANCED.model_mode_finalists),
            selected_balanced_frontier: metric_durations.selected_balanced_frontier,
            selected_regular_anchor_bytes: selected_options.fixed_segment_size,
            selected_transform_finalist_limit: selected_plan.map(|plan| plan.transform_finalists),
            selected_model_mode_finalist_limit: selected_plan.map(|plan| plan.model_mode_finalists),
        },
    ))
}

fn compress_internal(
    input: &[u8],
    options: &EncodeOptions,
    mut metrics: Option<&mut EncodeMetricDurations>,
) -> Result<Vec<u8>> {
    if !options.validate() {
        return Err(Error::InvalidArchive("invalid encoder options"));
    }
    let _ = u64::try_from(input.len()).map_err(|_| Error::IntegerOverflow)?;

    let plan = LargeInputPlan::for_input(input.len(), options);
    if plan == Some(LargeInputPlan::MAX) {
        let balanced_options = EncodeOptions::for_mode(Mode::Balanced);
        let mut best = compress_internal_with_plan(
            input,
            &balanced_options,
            Some(LargeInputPlan::BALANCED),
            metrics.as_deref_mut(),
        )?;
        let max_candidate =
            compress_internal_with_plan(input, options, plan, metrics.as_deref_mut())?;
        if max_candidate.len() < best.len() {
            best = max_candidate;
        } else if let Some(metrics) = metrics {
            metrics.selected_balanced_frontier = true;
        }
        return Ok(best);
    }
    compress_internal_with_plan(input, options, plan, metrics)
}

fn compress_internal_with_plan(
    input: &[u8],
    options: &EncodeOptions,
    plan: Option<LargeInputPlan>,
    mut metrics: Option<&mut EncodeMetricDurations>,
) -> Result<Vec<u8>> {
    let effective_options = plan.map(|plan| plan.effective_options(options));
    let search_options = effective_options.as_ref().unwrap_or(options);
    let mut best_archive = if options.allow_raw_fallback {
        Some(raw_archive(input)?)
    } else {
        None
    };
    let transform_candidates = match plan {
        Some(plan) => screen_transform_candidates(input, search_options, plan)?,
        None => transform_candidates(search_options),
    };
    for transform in transform_candidates {
        if transform == TransformKind::BitPlanePadded {
            checked_padded_bit_plane_size(input.len())?;
        }
        let transformed = transforms::apply(transform, input);
        let optimized = if transform == TransformKind::BitPlanePadded {
            optimize_independent_bit_planes(
                &transformed,
                input.len(),
                search_options,
                plan.map(|plan| plan.model_mode_finalists),
                metrics.as_deref_mut(),
            )
        } else {
            optimize_partition(
                &transformed,
                search_options,
                plan.map(|plan| plan.model_mode_finalists),
                metrics.as_deref_mut(),
            )
        };
        let segments = match optimized {
            Ok(segments) => segments,
            Err(Error::NoRepresentation) => continue,
            Err(error) => return Err(error),
        };
        let archive = container::serialize(input, transformed.len(), &[transform], &segments)?;
        if best_archive
            .as_ref()
            .map(|best| archive.len() < best.len())
            .unwrap_or(true)
        {
            best_archive = Some(archive);
        }
    }
    best_archive.ok_or(Error::NoRepresentation)
}

/// Decompresses and verifies a MathZip archive under caller-selected limits.
pub fn decompress(archive: &[u8], limits: &DecodeLimits) -> Result<Vec<u8>> {
    let parsed = container::parse(archive, limits)?;
    decode_parsed(&parsed, limits)
}

/// Parses, reconstructs, and returns a machine-readable archive summary.
pub fn inspect(archive: &[u8], limits: &DecodeLimits) -> Result<ArchiveInfo> {
    let parsed = container::parse(archive, limits)?;
    // Reconstruction validates every segment and the original SHA-256.
    let original = decode_parsed(&parsed, limits)?;
    let mut transformed = original;
    for &transform in &parsed.transforms {
        transformed = transforms::apply(transform, &transformed);
    }

    let mut model_distribution = BTreeMap::new();
    let mut residual_distribution = BTreeMap::new();
    let mut residual_mode_distribution = BTreeMap::new();
    let mut model_parameter_bytes = 0u64;
    let mut residual_bytes = 0u64;
    let mut raw_model_bytes = 0u64;
    let mut raw_fallback_bytes = 0u64;
    let mut residual_histogram = [0u64; 256];
    let mut residual_sample_count = 0u64;
    let mut math_segments_winning_raw = 0u64;
    let mut math_segments_winning_zstd = 0u64;
    let mut segment_lengths = Vec::with_capacity(parsed.segments.len());
    let mut zstd_probe =
        zstd::bulk::Compressor::new(3).map_err(|error| Error::ResidualCodec(error.to_string()))?;
    for segment in &parsed.segments {
        *model_distribution.entry(segment.model).or_insert(0) += 1;
        *residual_distribution
            .entry(segment.residual_coder)
            .or_insert(0) += 1;
        *residual_mode_distribution
            .entry(segment.residual_mode)
            .or_insert(0) += 1;
        model_parameter_bytes = model_parameter_bytes
            .checked_add(segment.model_parameters.len() as u64)
            .ok_or(Error::IntegerOverflow)?;
        residual_bytes = residual_bytes
            .checked_add(segment.residual.len() as u64)
            .ok_or(Error::IntegerOverflow)?;
        if segment.model == crate::ModelKind::Raw {
            raw_model_bytes = raw_model_bytes
                .checked_add(segment.length)
                .ok_or(Error::IntegerOverflow)?;
            if segment.residual_coder == ResidualCoder::Raw {
                raw_fallback_bytes = raw_fallback_bytes
                    .checked_add(segment.length)
                    .ok_or(Error::IntegerOverflow)?;
            }
        } else {
            let body_size = segment
                .model_parameters
                .len()
                .checked_add(segment.residual.len())
                .ok_or(Error::IntegerOverflow)?;
            let segment_len =
                usize::try_from(segment.length).map_err(|_| Error::IntegerOverflow)?;
            if body_size < segment_len {
                math_segments_winning_raw += 1;
            }
            let start = usize::try_from(segment.offset).map_err(|_| Error::IntegerOverflow)?;
            let end = start
                .checked_add(segment_len)
                .ok_or(Error::IntegerOverflow)?;
            let actual = transformed
                .get(start..end)
                .ok_or(Error::InvalidArchive("segment metric range is invalid"))?;
            let zstd_size = zstd_probe
                .compress(actual)
                .map_err(|error| Error::ResidualCodec(error.to_string()))?
                .len();
            if body_size < zstd_size {
                math_segments_winning_zstd += 1;
            }
        }
        let segment_len = usize::try_from(segment.length).map_err(|_| Error::IntegerOverflow)?;
        for value in residual::decode(segment.residual_coder, segment.residual, segment_len)? {
            residual_histogram[value as usize] += 1;
        }
        residual_sample_count = residual_sample_count
            .checked_add(segment.length)
            .ok_or(Error::IntegerOverflow)?;
        segment_lengths.push(segment.length);
    }
    let estimated_residual_entropy = (residual_sample_count != 0).then(|| {
        let entropy: f64 = residual_histogram
            .iter()
            .filter(|&&count| count != 0)
            .map(|&count| {
                let probability = count as f64 / residual_sample_count as f64;
                -probability * probability.log2()
            })
            .sum();
        if entropy == 0.0 {
            0.0
        } else {
            entropy
        }
    });
    segment_lengths.sort_unstable();
    let mean_segment_size = (!segment_lengths.is_empty())
        .then(|| parsed.header.transformed_size as f64 / segment_lengths.len() as f64);
    let median_segment_size = if segment_lengths.is_empty() {
        None
    } else if segment_lengths.len() % 2 == 1 {
        Some(segment_lengths[segment_lengths.len() / 2] as f64)
    } else {
        let right = segment_lengths.len() / 2;
        Some((segment_lengths[right - 1] as f64 + segment_lengths[right] as f64) / 2.0)
    };
    let segment_descriptor_bytes = (parsed.segments.len() as u64)
        .checked_mul(SEGMENT_DESCRIPTOR_SIZE as u64)
        .ok_or(Error::IntegerOverflow)?;
    let container_overhead_bytes =
        (HEADER_SIZE + FOOTER_SIZE) as u64 + parsed.transform_metadata_bytes;
    let metadata_bytes = container_overhead_bytes + segment_descriptor_bytes;
    let compressed_size = archive.len() as u64;
    Ok(ArchiveInfo {
        format_version: parsed.header.format_version,
        original_size: parsed.header.original_size,
        transformed_size: parsed.header.transformed_size,
        compressed_size,
        payload_size: parsed.header.payload_size,
        compression_ratio: (parsed.header.original_size != 0)
            .then(|| parsed.header.original_size as f64 / compressed_size as f64),
        transforms: parsed.transforms.clone(),
        transform_count: parsed.header.transform_count,
        segment_count: parsed.header.segment_count,
        mean_segment_size,
        median_segment_size,
        raw_model_percentage: (parsed.header.transformed_size != 0)
            .then(|| raw_model_bytes as f64 * 100.0 / parsed.header.transformed_size as f64),
        raw_fallback_percentage: (parsed.header.transformed_size != 0)
            .then(|| raw_fallback_bytes as f64 * 100.0 / parsed.header.transformed_size as f64),
        model_distribution,
        residual_distribution,
        residual_mode_distribution,
        container_overhead_bytes,
        metadata_bytes,
        transform_metadata_bytes: parsed.transform_metadata_bytes,
        segment_descriptor_bytes,
        partition_metadata_bytes: segment_descriptor_bytes,
        model_parameter_bytes,
        residual_bytes,
        actual_residual_coded_bytes: residual_bytes,
        estimated_residual_entropy,
        math_segments_winning_raw,
        math_segments_winning_zstd,
        checksums: ChecksumStatus {
            header: true,
            archive: true,
            segments: true,
            original: true,
        },
    })
}

/// Verifies that an archive is valid and reconstructs exactly `original`.
pub fn verify(original: &[u8], archive: &[u8], limits: &DecodeLimits) -> Result<bool> {
    Ok(decompress(archive, limits)? == original)
}

fn transform_candidates(options: &EncodeOptions) -> Vec<TransformKind> {
    let enabled = &options.transforms;
    let mut result = Vec::new();
    if enabled.identity {
        result.push(TransformKind::Identity);
    }
    if enabled.delta {
        result.push(TransformKind::Delta);
    }
    if enabled.xor {
        result.push(TransformKind::Xor);
    }
    if enabled.bit_plane {
        result.push(TransformKind::BitPlane);
    }
    if enabled.bit_plane_independent {
        result.push(TransformKind::BitPlanePadded);
    }
    if enabled.stride {
        let strides: &[u16] = match options.mode {
            Mode::Fast => &[2, 4],
            Mode::Balanced => &[2, 3, 4, 8, 16],
            Mode::Max => &[2, 3, 4, 8, 16, 32, 64],
        };
        result.extend(strides.iter().copied().map(TransformKind::Stride));
    }
    result
}

/// Ranks every enabled transform by a bounded exact residual-code probe, then
/// returns the best registry-ordered finalists. The final archive search still
/// compares complete encodings, so this score is only a deterministic
/// portfolio screen.
fn screen_transform_candidates(
    input: &[u8],
    options: &EncodeOptions,
    plan: LargeInputPlan,
) -> Result<Vec<TransformKind>> {
    let mut screen_options = options.clone();
    screen_options.models = ModelOptions {
        raw: options.models.raw,
        constant: options.models.constant,
        affine: options.models.affine,
        polynomial: false,
        periodic: options.models.periodic,
        recurrence: options.models.recurrence,
        piecewise_linear: false,
        run: options.models.run,
        sparse: options.models.sparse,
        copy: false,
    };

    let mut scored = Vec::new();
    for (registry_order, transform) in transform_candidates(options).into_iter().enumerate() {
        if transform == TransformKind::BitPlanePadded {
            checked_padded_bit_plane_size(input.len())?;
        }
        let transformed = transforms::apply(transform, input);
        match transform_screen_score(&transformed, input.len(), transform, &screen_options) {
            Ok(score) => scored.push((score, registry_order, transform)),
            Err(Error::NoRepresentation) => {}
            Err(error) => return Err(error),
        }
    }
    Ok(select_transform_portfolio(scored, plan.transform_finalists))
}

#[cfg(test)]
fn select_transform_finalists(
    mut scored: Vec<(usize, usize, TransformKind)>,
    limit: usize,
) -> Vec<TransformKind> {
    scored.sort_by_key(|&(score, registry_order, _)| (score, registry_order));
    scored.truncate(limit);
    // Preserve the global registry tie order during the complete archive
    // comparison, independently of the proxy ranking order.
    scored.sort_by_key(|&(_, registry_order, _)| registry_order);
    scored
        .into_iter()
        .map(|(_, _, transform)| transform)
        .collect()
}

fn select_transform_portfolio(
    mut scored: Vec<(usize, usize, TransformKind)>,
    limit: usize,
) -> Vec<TransformKind> {
    scored.sort_by_key(|&(score, registry_order, _)| (score, registry_order));
    let mut selected: Vec<(usize, TransformKind)> = Vec::with_capacity(limit);

    // Identity anchors the native byte representation. The independent padded
    // bit-plane representation is also retained because its final search
    // optimizes eight planes separately, an advantage a flat proxy cannot
    // faithfully rank.
    for anchor in [TransformKind::Identity, TransformKind::BitPlanePadded] {
        if selected.len() == limit {
            break;
        }
        if let Some((_, registry_order, transform)) =
            scored.iter().find(|(_, _, transform)| *transform == anchor)
        {
            selected.push((*registry_order, *transform));
        }
    }
    for &(_, registry_order, transform) in &scored {
        if selected.len() == limit {
            break;
        }
        if !selected
            .iter()
            .any(|&(_, selected_transform)| selected_transform == transform)
        {
            selected.push((registry_order, transform));
        }
    }
    selected.sort_by_key(|&(registry_order, _)| registry_order);
    selected
        .into_iter()
        .map(|(_, transform)| transform)
        .collect()
}

fn transform_screen_score(
    transformed: &[u8],
    original_size: usize,
    transform: TransformKind,
    options: &EncodeOptions,
) -> Result<usize> {
    if transform == TransformKind::BitPlanePadded {
        let plane_size = original_size.div_ceil(8);
        let windows_per_plane = (TRANSFORM_SCREEN_WINDOWS / 8).max(1);
        let mut total = 0usize;
        for plane in 0..8 {
            let start = plane * plane_size;
            let end = start + plane_size;
            total = total
                .checked_add(transform_screen_ranges_score(
                    &transformed[start..end],
                    windows_per_plane,
                    options,
                )?)
                .ok_or(Error::IntegerOverflow)?;
        }
        return Ok(total);
    }
    transform_screen_ranges_score(transformed, TRANSFORM_SCREEN_WINDOWS, options)
}

fn transform_screen_ranges_score(
    transformed: &[u8],
    max_windows: usize,
    options: &EncodeOptions,
) -> Result<usize> {
    let mut total = 0usize;
    for range in
        distributed_sample_ranges(transformed.len(), max_windows, TRANSFORM_SCREEN_WINDOW_SIZE)
    {
        let sample = &transformed[range];
        let models = models::candidates(sample, 0, sample, options, &[]);
        let mut best = None;
        for model in models {
            let parameters = model.parameters();
            let residuals = if matches!(model, Model::Raw) {
                vec![(ResidualMode::AddModulo, sample.to_vec())]
            } else {
                let (add, xor) = model.make_residual_pair(sample, 0, sample)?;
                vec![(ResidualMode::AddModulo, add), (ResidualMode::Xor, xor)]
            };
            for (_, residual_bytes) in residuals {
                let Some((_, payload)) =
                    residual::encode_best(&residual_bytes, &options.residuals)?
                else {
                    continue;
                };
                let cost = parameters
                    .len()
                    .checked_add(payload.len())
                    .ok_or(Error::IntegerOverflow)?;
                if best.map(|current| cost < current).unwrap_or(true) {
                    best = Some(cost);
                }
            }
        }
        total = total
            .checked_add(best.ok_or(Error::NoRepresentation)?)
            .ok_or(Error::IntegerOverflow)?;
    }
    Ok(total)
}

/// Produces non-overlapping complete coverage when `len` fits in the budget,
/// otherwise evenly distributes fixed-size windows including both ends.
fn distributed_sample_ranges(
    len: usize,
    max_windows: usize,
    window_size: usize,
) -> Vec<Range<usize>> {
    if len == 0 || max_windows == 0 || window_size == 0 {
        return Vec::new();
    }
    let budget = max_windows.saturating_mul(window_size);
    if len <= budget {
        return (0..len)
            .step_by(window_size)
            .map(|start| start..(start + window_size).min(len))
            .collect();
    }
    if max_windows == 1 {
        return std::iter::once(0..window_size.min(len)).collect();
    }

    let last_start = len - window_size;
    (0..max_windows)
        .map(|window| {
            let start = (window as u128 * last_start as u128 / (max_windows - 1) as u128) as usize;
            start..start + window_size
        })
        .collect()
}

fn checked_padded_bit_plane_size(original_size: usize) -> Result<usize> {
    original_size
        .div_ceil(8)
        .checked_mul(8)
        .ok_or(Error::IntegerOverflow)
}

/// Optimizes every byte-aligned logical bit-plane as its own stream. Offsets
/// are rebased only after fitting, so segmentation, model parameters, Copy
/// search, and recursive-tree limits cannot leak across a plane boundary.
fn optimize_independent_bit_planes(
    transformed: &[u8],
    original_size: usize,
    options: &EncodeOptions,
    model_mode_finalists: Option<usize>,
    mut metrics: Option<&mut EncodeMetricDurations>,
) -> Result<Vec<EncodedSegment>> {
    let transformed_size = checked_padded_bit_plane_size(original_size)?;
    if transformed.len() != transformed_size {
        return Err(Error::InvalidArchive(
            "independent bit-plane transformed size mismatch",
        ));
    }
    if transformed.is_empty() {
        return Ok(Vec::new());
    }

    let plane_size = original_size.div_ceil(8);
    let mut result = Vec::new();
    for plane in 0..8usize {
        let plane_offset = plane
            .checked_mul(plane_size)
            .ok_or(Error::IntegerOverflow)?;
        let plane_end = plane_offset
            .checked_add(plane_size)
            .ok_or(Error::IntegerOverflow)?;
        let mut segments = optimize_partition(
            &transformed[plane_offset..plane_end],
            options,
            model_mode_finalists,
            metrics.as_deref_mut(),
        )?;
        let plane_offset = u64::try_from(plane_offset).map_err(|_| Error::IntegerOverflow)?;
        for segment in &mut segments {
            segment.offset = segment
                .offset
                .checked_add(plane_offset)
                .ok_or(Error::IntegerOverflow)?;
        }
        result.extend(segments);
        if result.len() > MAX_DEFAULT_ARCHIVE_SEGMENTS {
            return Err(Error::NoRepresentation);
        }
    }
    Ok(result)
}

fn raw_archive(input: &[u8]) -> Result<Vec<u8>> {
    let segments = if input.is_empty() {
        Vec::new()
    } else {
        vec![EncodedSegment {
            offset: 0,
            length: input.len() as u64,
            model: crate::ModelKind::Raw,
            model_parameters: Vec::new(),
            residual_mode: ResidualMode::AddModulo,
            residual_coder: ResidualCoder::Raw,
            residual: input.to_vec(),
            decoded_crc32: crc32(input),
        }]
    };
    container::serialize(input, input.len(), &[TransformKind::Identity], &segments)
}

#[derive(Debug, Clone)]
struct PartitionNode {
    cost: usize,
    segment_count: usize,
    previous: usize,
    segment: EncodedSegment,
}

#[derive(Debug, Clone)]
struct RecursiveNode {
    cost: usize,
    segment_count: usize,
    previous_boundary: usize,
    previous_state: usize,
}

#[derive(Debug, Clone)]
struct RecursiveCandidate {
    node: RecursiveNode,
    segment: Rc<EncodedSegment>,
}

fn optimize_partition(
    data: &[u8],
    options: &EncodeOptions,
    model_mode_finalists: Option<usize>,
    mut metrics: Option<&mut EncodeMetricDurations>,
) -> Result<Vec<EncodedSegment>> {
    if data.is_empty() {
        return Ok(Vec::new());
    }
    if options.segmentation == SegmentationMode::Recursive {
        return optimize_recursive_partition(data, options, model_mode_finalists, metrics);
    }
    let boundaries = segmentation::candidate_boundaries(data, options);
    if boundaries.first() != Some(&0) || boundaries.last() != Some(&data.len()) {
        return Err(Error::InvalidArchive("invalid generated boundary set"));
    }
    let copy_index = if options.models.copy {
        build_copy_index(data, &boundaries)
    } else {
        BTreeMap::new()
    };

    let mut nodes: Vec<Option<PartitionNode>> = vec![None; boundaries.len()];
    let mut reachable_cost = vec![usize::MAX; boundaries.len()];
    let mut reachable_count = vec![usize::MAX; boundaries.len()];
    reachable_cost[0] = 0;
    reachable_count[0] = 0;

    for end_index in 1..boundaries.len() {
        let predecessor_start = match options.segmentation {
            SegmentationMode::Fixed => end_index - 1,
            SegmentationMode::ChangePoint | SegmentationMode::Adaptive => {
                end_index.saturating_sub(options.dp_lookback)
            }
            SegmentationMode::Recursive => unreachable!("recursive search is dispatched above"),
        };
        for start_index in predecessor_start..end_index {
            if reachable_cost[start_index] == usize::MAX {
                continue;
            }
            let start = boundaries[start_index];
            let end = boundaries[end_index];
            let length = end - start;
            if options.segmentation != SegmentationMode::Fixed && length > options.max_segment_size
            {
                continue;
            }
            if options.segmentation != SegmentationMode::Fixed
                && length < options.min_segment_size
                && start != 0
                && end != data.len()
            {
                continue;
            }
            let copy_sources = copy_key(data, start)
                .and_then(|key| copy_index.get(&key))
                .map(Vec::as_slice)
                .unwrap_or(&[]);
            let segment = match best_segment(
                data,
                start,
                end,
                options,
                copy_sources,
                model_mode_finalists,
                metrics.as_deref_mut(),
            ) {
                Ok(segment) => segment,
                Err(Error::NoRepresentation) => continue,
                Err(error) => return Err(error),
            };
            let candidate_cost = reachable_cost[start_index]
                .checked_add(segment.encoded_len())
                .ok_or(Error::IntegerOverflow)?;
            let candidate_count = reachable_count[start_index] + 1;
            let replace = candidate_cost < reachable_cost[end_index]
                || candidate_cost == reachable_cost[end_index]
                    && candidate_count < reachable_count[end_index]
                || candidate_cost == reachable_cost[end_index]
                    && candidate_count == reachable_count[end_index]
                    && nodes[end_index]
                        .as_ref()
                        .map(|node| segment_order_key(&segment) < segment_order_key(&node.segment))
                        .unwrap_or(true);
            if replace {
                reachable_cost[end_index] = candidate_cost;
                reachable_count[end_index] = candidate_count;
                nodes[end_index] = Some(PartitionNode {
                    cost: candidate_cost,
                    segment_count: candidate_count,
                    previous: start_index,
                    segment,
                });
            }
        }
    }

    if nodes.last().and_then(Option::as_ref).is_none() {
        return Err(Error::NoRepresentation);
    }
    let mut result = Vec::with_capacity(reachable_count[boundaries.len() - 1]);
    let mut index = boundaries.len() - 1;
    while index != 0 {
        let node = nodes[index].as_ref().ok_or(Error::NoRepresentation)?;
        debug_assert_eq!(node.cost, reachable_cost[index]);
        debug_assert_eq!(node.segment_count, reachable_count[index]);
        result.push(node.segment.clone());
        index = node.previous;
    }
    result.reverse();
    Ok(result)
}

fn optimize_recursive_partition(
    data: &[u8],
    options: &EncodeOptions,
    model_mode_finalists: Option<usize>,
    mut metrics: Option<&mut EncodeMetricDurations>,
) -> Result<Vec<EncodedSegment>> {
    let backbone =
        segmentation::recursive_backbone(data.len(), options).ok_or(Error::NoRepresentation)?;
    let boundaries = segmentation::candidate_boundaries(data, options);
    if boundaries.first() != Some(&0) || boundaries.last() != Some(&data.len()) {
        return Err(Error::InvalidArchive("invalid generated boundary set"));
    }

    let max_tree_leaves = 1usize
        .checked_shl(u32::from(options.max_tree_depth))
        .ok_or(Error::IntegerOverflow)?;
    let max_size_leaves = if data.len() < options.min_segment_size {
        1
    } else {
        data.len() / options.min_segment_size
    };
    let max_leaves = max_tree_leaves
        .min(max_size_leaves)
        .min(boundaries.len().saturating_sub(1));
    if max_leaves == 0 || backbone.len().saturating_sub(1) > max_leaves {
        return Err(Error::NoRepresentation);
    }

    let mut backbone_predecessor = vec![None; boundaries.len()];
    for pair in backbone.windows(2) {
        let start_index = boundaries
            .binary_search(&pair[0])
            .map_err(|_| Error::InvalidArchive("recursive backbone start is missing"))?;
        let end_index = boundaries
            .binary_search(&pair[1])
            .map_err(|_| Error::InvalidArchive("recursive backbone end is missing"))?;
        backbone_predecessor[end_index] = Some(start_index);
    }

    let copy_index = if options.models.copy {
        build_copy_index(data, &boundaries)
    } else {
        BTreeMap::new()
    };

    // Format v1 serializes only an ordered flat list of leaves. Any ordered
    // partition with L <= 2^depth leaves can be assigned a balanced binary
    // topology of height <= depth, so a leaf-count DP is equivalent here and
    // avoids storing non-semantic tree structure. Each searched edge is fitted
    // once in this search pass and its exact descriptor + parameter + residual
    // size is reused for every predecessor state. Payloads live only for this
    // end boundary's tie comparisons; persistent DP nodes contain indices and
    // costs. Winning edges are fitted once more during materialization. There
    // is no additional split token in the v1 container.
    let mut states: Vec<Vec<RecursiveNode>> = vec![Vec::new(); boundaries.len()];
    states[0].push(RecursiveNode {
        cost: 0,
        segment_count: 0,
        previous_boundary: 0,
        previous_state: 0,
    });
    let mut persistent_state_count = 1usize;

    for end_index in 1..boundaries.len() {
        let mut by_count: Vec<Option<RecursiveCandidate>> = vec![None; max_leaves + 1];
        for start_index in recursive_predecessors(
            &boundaries,
            end_index,
            options,
            backbone_predecessor[end_index],
        ) {
            let start = boundaries[start_index];
            let end = boundaries[end_index];
            let minimum_partition_leaves =
                minimum_recursive_leaves(start, options.max_segment_size)
                    .checked_add(1)
                    .and_then(|value| {
                        value.checked_add(minimum_recursive_leaves(
                            data.len() - end,
                            options.max_segment_size,
                        ))
                    })
                    .ok_or(Error::IntegerOverflow)?;
            if minimum_partition_leaves > max_leaves || states[start_index].is_empty() {
                continue;
            }
            let copy_sources = copy_key(data, start)
                .and_then(|key| copy_index.get(&key))
                .map(Vec::as_slice)
                .unwrap_or(&[]);
            let segment = match best_segment(
                data,
                start,
                end,
                options,
                copy_sources,
                model_mode_finalists,
                metrics.as_deref_mut(),
            ) {
                Ok(segment) => Rc::new(segment),
                Err(Error::NoRepresentation) => continue,
                Err(error) => return Err(error),
            };
            let required_suffix_leaves =
                minimum_recursive_leaves(data.len() - end, options.max_segment_size);

            for (previous_state, predecessor) in states[start_index].iter().enumerate() {
                let segment_count = predecessor
                    .segment_count
                    .checked_add(1)
                    .ok_or(Error::IntegerOverflow)?;
                if segment_count > max_leaves
                    || segment_count
                        .checked_add(required_suffix_leaves)
                        .ok_or(Error::IntegerOverflow)?
                        > max_leaves
                {
                    continue;
                }
                let cost = predecessor
                    .cost
                    .checked_add(segment.encoded_len())
                    .ok_or(Error::IntegerOverflow)?;
                let candidate = RecursiveCandidate {
                    node: RecursiveNode {
                        cost,
                        segment_count,
                        previous_boundary: start_index,
                        previous_state,
                    },
                    segment: Rc::clone(&segment),
                };
                let replace = by_count[segment_count]
                    .as_ref()
                    .map(|current| {
                        candidate.node.cost < current.node.cost
                            || candidate.node.cost == current.node.cost
                                && segment_order_key(&candidate.segment)
                                    < segment_order_key(&current.segment)
                    })
                    .unwrap_or(true);
                if replace {
                    by_count[segment_count] = Some(candidate);
                }
            }
        }

        // A lower/equal-cost state using fewer leaves dominates a larger one:
        // all future edge costs and Copy candidates are independent of how the
        // already reconstructed prefix was partitioned.
        let mut best_lower_cost = usize::MAX;
        for candidate in by_count.into_iter().flatten() {
            if candidate.node.cost < best_lower_cost {
                best_lower_cost = candidate.node.cost;
                states[end_index].push(candidate.node);
            }
        }
        persistent_state_count = persistent_state_count
            .checked_add(states[end_index].len())
            .ok_or(Error::IntegerOverflow)?;
        if persistent_state_count > MAX_RECURSIVE_DP_STATES {
            return Err(Error::NoRepresentation);
        }
    }

    let final_index = boundaries.len() - 1;
    let (mut state_index, _) = states[final_index]
        .iter()
        .enumerate()
        .min_by_key(|(_, node)| (node.cost, node.segment_count))
        .ok_or(Error::NoRepresentation)?;
    let mut boundary_index = final_index;
    let mut result = Vec::with_capacity(states[final_index][state_index].segment_count);
    while boundary_index != 0 {
        let node = &states[boundary_index][state_index];
        let start_index = node.previous_boundary;
        let previous_state = node.previous_state;
        let expected_edge_cost = node
            .cost
            .checked_sub(states[start_index][previous_state].cost)
            .ok_or(Error::IntegerOverflow)?;
        let start = boundaries[start_index];
        let end = boundaries[boundary_index];
        let copy_sources = copy_key(data, start)
            .and_then(|key| copy_index.get(&key))
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        // Re-fit only winning edges to materialize their bytes. This repeat is
        // deterministic and remains part of the measured model/residual work.
        let segment = best_segment(
            data,
            start,
            end,
            options,
            copy_sources,
            model_mode_finalists,
            metrics.as_deref_mut(),
        )?;
        if segment.encoded_len() != expected_edge_cost {
            return Err(Error::InvalidArchive(
                "recursive edge cost changed during materialization",
            ));
        }
        result.push(segment);
        boundary_index = start_index;
        state_index = previous_state;
    }
    result.reverse();
    Ok(result)
}

fn minimum_recursive_leaves(len: usize, max_segment_size: usize) -> usize {
    len / max_segment_size + usize::from(len % max_segment_size != 0)
}

fn recursive_predecessors(
    boundaries: &[usize],
    end_index: usize,
    options: &EncodeOptions,
    required_predecessor: Option<usize>,
) -> Vec<usize> {
    let end = boundaries[end_index];
    if end == *boundaries.last().unwrap_or(&0) && end < options.min_segment_size {
        return vec![0];
    }

    let minimum_start = end.saturating_sub(options.max_segment_size);
    let maximum_start = match end.checked_sub(options.min_segment_size) {
        Some(value) => value,
        None => return Vec::new(),
    };
    let first = boundaries.partition_point(|&boundary| boundary < minimum_start);
    let after_last = boundaries[..end_index].partition_point(|&boundary| boundary <= maximum_start);
    if first >= after_last {
        return Vec::new();
    }

    let candidate_count = after_last - first;
    let sample_count = options.dp_lookback.min(candidate_count);
    let mut predecessors =
        Vec::with_capacity(sample_count + usize::from(required_predecessor.is_some()));
    if sample_count == 1 {
        // Prefer the longest valid edge when only one candidate is allowed;
        // this preserves the best chance of satisfying the leaf-depth cap.
        predecessors.push(first);
    } else {
        for sample in 0..sample_count {
            let offset = (sample as u128 * (candidate_count - 1) as u128
                / (sample_count - 1) as u128) as usize;
            predecessors.push(first + offset);
        }
    }
    if let Some(required) = required_predecessor {
        predecessors.push(required);
    }
    predecessors.sort_unstable();
    predecessors.dedup();
    predecessors
}

fn best_segment(
    transformed: &[u8],
    start: usize,
    end: usize,
    options: &EncodeOptions,
    copy_sources: &[usize],
    model_mode_finalists: Option<usize>,
    mut metrics: Option<&mut EncodeMetricDurations>,
) -> Result<EncodedSegment> {
    let actual = &transformed[start..end];
    let checksum = crc32(actual);
    let model_started = metrics.as_ref().map(|_| Instant::now());
    let models = models::candidates(actual, start, transformed, options, copy_sources);
    if let (Some(metrics), Some(started)) = (metrics.as_deref_mut(), model_started) {
        metrics.model_fitting = metrics.model_fitting.saturating_add(started.elapsed());
    }
    let residual_started = metrics.as_ref().map(|_| Instant::now());
    let best = match model_mode_finalists {
        Some(limit) => {
            screened_best_segment(actual, start, transformed, options, checksum, models, limit)?
        }
        None => exhaustive_best_segment(actual, start, transformed, options, checksum, models)?,
    };
    if let (Some(metrics), Some(started)) = (metrics, residual_started) {
        metrics.residual_coding = metrics.residual_coding.saturating_add(started.elapsed());
    }
    best.ok_or(Error::NoRepresentation)
}

fn exhaustive_best_segment(
    actual: &[u8],
    start: usize,
    transformed: &[u8],
    options: &EncodeOptions,
    checksum: u32,
    models: Vec<Model>,
) -> Result<Option<EncodedSegment>> {
    let mut best: Option<EncodedSegment> = None;
    for model in models {
        let parameters = model.parameters();
        if parameters.len() > u32::MAX as usize {
            continue;
        }
        let (residuals, residual_count) = if matches!(model, Model::Raw) {
            (
                [
                    (ResidualMode::AddModulo, actual.to_vec()),
                    (ResidualMode::Xor, Vec::new()),
                ],
                1,
            )
        } else {
            let (add, xor) = model.make_residual_pair(actual, start, transformed)?;
            (
                [(ResidualMode::AddModulo, add), (ResidualMode::Xor, xor)],
                2,
            )
        };
        for (mode, residual_bytes) in residuals.into_iter().take(residual_count) {
            let Some((coder, payload)) =
                residual::encode_best(&residual_bytes, &options.residuals)?
            else {
                continue;
            };
            let candidate = EncodedSegment {
                offset: start as u64,
                length: actual.len() as u64,
                model: model.kind(),
                model_parameters: parameters.clone(),
                residual_mode: mode,
                residual_coder: coder,
                residual: payload,
                decoded_crc32: checksum,
            };
            if best
                .as_ref()
                .map(|current| {
                    candidate.encoded_len() < current.encoded_len()
                        || candidate.encoded_len() == current.encoded_len()
                            && segment_order_key(&candidate) < segment_order_key(current)
                })
                .unwrap_or(true)
            {
                best = Some(candidate);
            }
        }
    }
    Ok(best)
}

#[derive(Debug)]
struct ScreenedModelMode {
    proxy_cost: usize,
    catalog_order: usize,
    model: crate::ModelKind,
    model_parameters: Vec<u8>,
    residual_mode: ResidualMode,
    residual: Vec<u8>,
}

fn screened_best_segment(
    actual: &[u8],
    start: usize,
    transformed: &[u8],
    options: &EncodeOptions,
    checksum: u32,
    models: Vec<Model>,
    finalist_limit: usize,
) -> Result<Option<EncodedSegment>> {
    let mut best = None;
    let mut finalists = Vec::with_capacity(finalist_limit.saturating_add(1));
    for (catalog_order, model) in models.into_iter().enumerate() {
        let parameters = model.parameters();
        if parameters.len() > u32::MAX as usize {
            continue;
        }
        if matches!(model, Model::Raw) {
            let Some((coder, payload)) = residual::encode_best(actual, &options.residuals)? else {
                continue;
            };
            consider_segment(
                &mut best,
                EncodedSegment {
                    offset: start as u64,
                    length: actual.len() as u64,
                    model: model.kind(),
                    model_parameters: parameters,
                    residual_mode: ResidualMode::AddModulo,
                    residual_coder: coder,
                    residual: payload,
                    decoded_crc32: checksum,
                },
            );
            continue;
        }

        // Predictions/residuals are derived once for the complete segment.
        // Only the retained model-mode pairs reach full residual encoding.
        let model_kind = model.kind();
        let (add, xor) = model.make_residual_pair(actual, start, transformed)?;
        for (residual_mode, residual_bytes) in
            [(ResidualMode::AddModulo, add), (ResidualMode::Xor, xor)]
        {
            let proxy_residual_cost = screened_residual_proxy_cost(
                &residual_bytes,
                MODEL_SCREEN_WINDOWS,
                MODEL_SCREEN_WINDOW_SIZE,
                options,
            )?;
            let proxy_cost = parameters
                .len()
                .checked_add(proxy_residual_cost)
                .ok_or(Error::IntegerOverflow)?;
            retain_model_mode_finalist(
                &mut finalists,
                ScreenedModelMode {
                    proxy_cost,
                    catalog_order,
                    model: model_kind,
                    model_parameters: parameters.clone(),
                    residual_mode,
                    residual: residual_bytes,
                },
                finalist_limit,
            );
        }
    }

    for finalist in finalists {
        let Some((coder, payload)) = residual::encode_best(&finalist.residual, &options.residuals)?
        else {
            continue;
        };
        consider_segment(
            &mut best,
            EncodedSegment {
                offset: start as u64,
                length: actual.len() as u64,
                model: finalist.model,
                model_parameters: finalist.model_parameters,
                residual_mode: finalist.residual_mode,
                residual_coder: coder,
                residual: payload,
                decoded_crc32: checksum,
            },
        );
    }
    Ok(best)
}

fn screened_residual_proxy_cost(
    residual_bytes: &[u8],
    max_windows: usize,
    window_size: usize,
    options: &EncodeOptions,
) -> Result<usize> {
    let ranges = distributed_sample_ranges(residual_bytes.len(), max_windows, window_size);
    let mut sampled_bytes = 0usize;
    let mut sampled_encoded_bytes = 0usize;
    for range in ranges {
        sampled_bytes = sampled_bytes
            .checked_add(range.len())
            .ok_or(Error::IntegerOverflow)?;
        let Some((_, payload)) = residual::encode_best(&residual_bytes[range], &options.residuals)?
        else {
            return Err(Error::NoRepresentation);
        };
        sampled_encoded_bytes = sampled_encoded_bytes
            .checked_add(payload.len())
            .ok_or(Error::IntegerOverflow)?;
    }
    if sampled_bytes == 0 {
        return Ok(0);
    }
    let scaled = (sampled_encoded_bytes as u128)
        .checked_mul(residual_bytes.len() as u128)
        .ok_or(Error::IntegerOverflow)?
        .div_ceil(sampled_bytes as u128);
    usize::try_from(scaled).map_err(|_| Error::IntegerOverflow)
}

fn retain_model_mode_finalist(
    finalists: &mut Vec<ScreenedModelMode>,
    candidate: ScreenedModelMode,
    limit: usize,
) {
    finalists.push(candidate);
    finalists.sort_by_key(|candidate| {
        (
            candidate.proxy_cost,
            candidate.catalog_order,
            candidate.residual_mode.id(),
        )
    });
    finalists.truncate(limit);
}

fn consider_segment(best: &mut Option<EncodedSegment>, candidate: EncodedSegment) {
    if best
        .as_ref()
        .map(|current| {
            candidate.encoded_len() < current.encoded_len()
                || candidate.encoded_len() == current.encoded_len()
                    && segment_order_key(&candidate) < segment_order_key(current)
        })
        .unwrap_or(true)
    {
        *best = Some(candidate);
    }
}

fn copy_key(data: &[u8], start: usize) -> Option<u32> {
    let bytes: [u8; 4] = data.get(start..start.checked_add(4)?)?.try_into().ok()?;
    Some(u32::from_le_bytes(bytes))
}

fn build_copy_index(data: &[u8], boundaries: &[usize]) -> BTreeMap<u32, Vec<usize>> {
    let mut index = BTreeMap::<u32, Vec<usize>>::new();
    for &start in boundaries {
        if let Some(key) = copy_key(data, start) {
            index.entry(key).or_default().push(start);
        }
    }
    index
}

fn segment_order_key(segment: &EncodedSegment) -> (u8, u8, u8, &[u8], &[u8]) {
    (
        segment.model.id(),
        segment.residual_mode.id(),
        segment.residual_coder.id(),
        &segment.model_parameters,
        &segment.residual,
    )
}

fn decode_parsed(parsed: &ParsedArchive<'_>, limits: &DecodeLimits) -> Result<Vec<u8>> {
    let transformed_len =
        usize::try_from(parsed.header.transformed_size).map_err(|_| Error::IntegerOverflow)?;
    let model_work_limit = parsed
        .header
        .transformed_size
        .checked_mul(8)
        .ok_or(Error::IntegerOverflow)?;
    let mut model_work = 0u64;
    let single_segment = parsed.segments.len() == 1;
    let mut transformed = if single_segment {
        Vec::new()
    } else {
        Vec::with_capacity(transformed_len)
    };
    for segment in &parsed.segments {
        let offset = usize::try_from(segment.offset).map_err(|_| Error::IntegerOverflow)?;
        let length = usize::try_from(segment.length).map_err(|_| Error::IntegerOverflow)?;
        if transformed.len() != offset {
            return Err(Error::InvalidArchive("segment decode order mismatch"));
        }
        let model = Model::decode(
            segment.model,
            segment.model_parameters,
            length,
            offset,
            limits,
        )?;
        let segment_work = segment
            .length
            .checked_mul(model.decode_work_weight())
            .ok_or(Error::IntegerOverflow)?;
        model_work = model_work
            .checked_add(segment_work)
            .ok_or(Error::IntegerOverflow)?;
        if model_work > model_work_limit {
            return Err(Error::LimitExceeded {
                what: "aggregate_model_work",
                actual: model_work,
                limit: model_work_limit,
            });
        }
        let residual_bytes = residual::decode(segment.residual_coder, segment.residual, length)?;
        let mut restored =
            model.restore(residual_bytes, offset, &transformed, segment.residual_mode)?;
        if restored.len() != length {
            return Err(Error::InvalidArchive("model output length mismatch"));
        }
        if crc32(&restored) != segment.decoded_crc32 {
            return Err(Error::ChecksumMismatch { kind: "segment" });
        }
        if single_segment {
            transformed = restored;
        } else {
            transformed.append(&mut restored);
        }
    }
    if transformed.len() != transformed_len {
        return Err(Error::InvalidArchive("transformed output length mismatch"));
    }
    let mut original = transformed;
    let original_len =
        usize::try_from(parsed.header.original_size).map_err(|_| Error::IntegerOverflow)?;
    for &transform in parsed.transforms.iter().rev() {
        original = transforms::reverse_with_output_size(transform, &original, original_len)?;
    }
    if original.len() != original_len {
        return Err(Error::InvalidArchive("original output length mismatch"));
    }
    let digest = Sha256::digest(&original);
    if digest[..] != parsed.header.original_sha256 {
        return Err(Error::ChecksumMismatch {
            kind: "original SHA-256",
        });
    }
    Ok(original)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn large_input_plan_is_thresholded_and_standard_only() {
        let balanced = EncodeOptions::for_mode(Mode::Balanced);
        let max = EncodeOptions::for_mode(Mode::Max);
        let fast = EncodeOptions::for_mode(Mode::Fast);

        assert_eq!(
            LargeInputPlan::for_input(LARGE_INPUT_THRESHOLD - 1, &balanced),
            None
        );
        assert_eq!(
            LargeInputPlan::for_input(LARGE_INPUT_THRESHOLD, &balanced),
            Some(LargeInputPlan::BALANCED)
        );
        assert_eq!(
            LargeInputPlan::for_input(LARGE_INPUT_THRESHOLD, &max),
            Some(LargeInputPlan::MAX)
        );
        assert_eq!(
            LargeInputPlan::for_input(LARGE_INPUT_THRESHOLD * 8, &fast),
            None
        );

        let mut custom = balanced.clone();
        custom.fixed_segment_size += 1;
        assert_eq!(
            LargeInputPlan::for_input(LARGE_INPUT_THRESHOLD, &custom),
            None
        );
        custom = balanced.clone();
        custom.allow_raw_fallback = false;
        assert_eq!(
            LargeInputPlan::for_input(LARGE_INPUT_THRESHOLD, &custom),
            None
        );
        custom = balanced.clone();
        custom.large_input_screening = false;
        assert_eq!(
            LargeInputPlan::for_input(LARGE_INPUT_THRESHOLD, &custom),
            None
        );

        let balanced_effective = LargeInputPlan::BALANCED.effective_options(&balanced);
        assert_eq!(balanced_effective.segmentation, SegmentationMode::Adaptive);
        assert_eq!(balanced_effective.fixed_segment_size, 64 * 1024);
        let max_effective = LargeInputPlan::MAX.effective_options(&max);
        assert_eq!(max_effective.segmentation, SegmentationMode::Adaptive);
        assert_eq!(max_effective.fixed_segment_size, 256 * 1024);
    }

    #[test]
    fn distributed_samples_obey_integer_work_budgets() {
        let large_len = 40 * TRANSFORM_SCREEN_WINDOW_SIZE + 17;
        let ranges = distributed_sample_ranges(
            large_len,
            TRANSFORM_SCREEN_WINDOWS,
            TRANSFORM_SCREEN_WINDOW_SIZE,
        );
        assert_eq!(ranges.len(), TRANSFORM_SCREEN_WINDOWS);
        assert_eq!(
            ranges.iter().map(Range::len).sum::<usize>(),
            TRANSFORM_SCREEN_WINDOWS * TRANSFORM_SCREEN_WINDOW_SIZE
        );
        assert_eq!(ranges.first().unwrap().start, 0);
        assert_eq!(ranges.last().unwrap().end, large_len);

        let fully_covered = distributed_sample_ranges(
            LARGE_INPUT_THRESHOLD,
            TRANSFORM_SCREEN_WINDOWS,
            TRANSFORM_SCREEN_WINDOW_SIZE,
        );
        assert_eq!(
            fully_covered.iter().map(Range::len).sum::<usize>(),
            LARGE_INPUT_THRESHOLD
        );
        assert!(fully_covered
            .windows(2)
            .all(|pair| pair[0].end == pair[1].start));

        let residual = vec![7; 16 * 1024];
        let options = EncodeOptions::for_mode(Mode::Balanced);
        let proxy = screened_residual_proxy_cost(
            &residual,
            MODEL_SCREEN_WINDOWS,
            MODEL_SCREEN_WINDOW_SIZE,
            &options,
        )
        .unwrap();
        assert!(proxy > 0);
        assert_eq!(
            distributed_sample_ranges(
                residual.len(),
                MODEL_SCREEN_WINDOWS,
                MODEL_SCREEN_WINDOW_SIZE
            )
            .iter()
            .map(Range::len)
            .sum::<usize>(),
            MODEL_SCREEN_WINDOWS * MODEL_SCREEN_WINDOW_SIZE
        );
    }

    #[test]
    fn transform_and_model_shortlists_have_stable_ties_and_caps() {
        let transforms = select_transform_finalists(
            vec![
                (9, 3, TransformKind::BitPlane),
                (9, 1, TransformKind::Delta),
                (9, 2, TransformKind::Xor),
                (9, 0, TransformKind::Identity),
            ],
            3,
        );
        assert_eq!(
            transforms,
            [
                TransformKind::Identity,
                TransformKind::Delta,
                TransformKind::Xor
            ]
        );
        let anchored = select_transform_portfolio(
            vec![
                (1, 2, TransformKind::Xor),
                (2, 1, TransformKind::Delta),
                (3, 0, TransformKind::Identity),
                (99, 4, TransformKind::BitPlanePadded),
            ],
            3,
        );
        assert_eq!(
            anchored,
            [
                TransformKind::Identity,
                TransformKind::Xor,
                TransformKind::BitPlanePadded
            ]
        );

        let candidate = |catalog_order, residual_mode| ScreenedModelMode {
            proxy_cost: 11,
            catalog_order,
            model: crate::ModelKind::Constant,
            model_parameters: vec![0],
            residual_mode,
            residual: vec![0; 32],
        };
        let mut finalists = Vec::new();
        retain_model_mode_finalist(&mut finalists, candidate(1, ResidualMode::AddModulo), 2);
        retain_model_mode_finalist(&mut finalists, candidate(0, ResidualMode::Xor), 2);
        retain_model_mode_finalist(&mut finalists, candidate(0, ResidualMode::AddModulo), 2);
        assert_eq!(finalists.len(), 2);
        assert_eq!(
            finalists
                .iter()
                .map(|candidate| (candidate.catalog_order, candidate.residual_mode))
                .collect::<Vec<_>>(),
            [(0, ResidualMode::AddModulo), (0, ResidualMode::Xor)]
        );
    }

    #[test]
    fn screened_portfolios_are_bounded_lossless_and_deterministic() {
        let data: Vec<u8> = (0..4096)
            .map(|index| ((index * 7 + index / 97 + 3) & 255) as u8)
            .collect();
        for (mode, plan) in [
            (Mode::Balanced, LargeInputPlan::BALANCED),
            (Mode::Max, LargeInputPlan::MAX),
        ] {
            let options = EncodeOptions::for_mode(mode);
            let screened = screen_transform_candidates(&data, &options, plan).unwrap();
            assert_eq!(screened.len(), plan.transform_finalists);

            // Force only the already-gated plan so this test covers the large
            // path without paying for a MiB-scale fixture.
            let first = compress_internal_with_plan(&data, &options, Some(plan), None).unwrap();
            let repeated = compress_internal_with_plan(&data, &options, Some(plan), None).unwrap();
            assert_eq!(first, repeated);
            assert_eq!(decompress(&first, &DecodeLimits::default()).unwrap(), data);
        }
    }

    #[test]
    fn public_threshold_path_reports_effective_search_provenance() {
        let data = vec![0u8; LARGE_INPUT_THRESHOLD];
        let options = EncodeOptions::for_mode(Mode::Balanced);
        let (archive, metrics) = compress_with_metrics(&data, &options).unwrap();
        assert_eq!(
            decompress(&archive, &DecodeLimits::default()).unwrap(),
            data
        );
        assert!(metrics.screened_large_input);
        assert_eq!(metrics.requested_mode, Mode::Balanced);
        assert_eq!(metrics.effective_segmentation, SegmentationMode::Adaptive);
        assert_eq!(metrics.primary_regular_anchor_bytes, 64 * 1024);
        assert_eq!(metrics.primary_transform_finalist_limit, Some(3));
        assert_eq!(metrics.primary_model_mode_finalist_limit, Some(2));
        assert!(!metrics.includes_balanced_frontier);
        assert_eq!(metrics.balanced_frontier_regular_anchor_bytes, None);
        assert_eq!(metrics.balanced_frontier_transform_finalist_limit, None);
        assert_eq!(metrics.balanced_frontier_model_mode_finalist_limit, None);
        assert!(!metrics.selected_balanced_frontier);
        assert_eq!(metrics.selected_regular_anchor_bytes, 64 * 1024);
        assert_eq!(metrics.selected_transform_finalist_limit, Some(3));
        assert_eq!(metrics.selected_model_mode_finalist_limit, Some(2));
    }

    #[test]
    fn screened_path_does_not_invent_a_raw_representation() {
        let data = vec![23; 4096];
        let mut options = EncodeOptions::for_mode(Mode::Balanced);
        options.models = ModelOptions {
            raw: false,
            constant: true,
            affine: false,
            polynomial: false,
            periodic: false,
            recurrence: false,
            piecewise_linear: false,
            run: false,
            sparse: false,
            copy: false,
        };
        options.transforms = crate::TransformOptions::identity_only();
        options.allow_raw_fallback = false;

        let archive =
            compress_internal_with_plan(&data, &options, Some(LargeInputPlan::BALANCED), None)
                .unwrap();
        let parsed = container::parse(&archive, &DecodeLimits::default()).unwrap();
        assert!(parsed
            .segments
            .iter()
            .all(|segment| segment.model == crate::ModelKind::Constant));
        assert_eq!(
            decompress(&archive, &DecodeLimits::default()).unwrap(),
            data
        );
    }

    fn recursive_raw_options() -> EncodeOptions {
        let mut options = EncodeOptions::for_mode(Mode::Fast);
        options.segmentation = SegmentationMode::Recursive;
        options.fixed_segment_size = 7;
        options.min_segment_size = 6;
        options.max_segment_size = 10;
        options.max_tree_depth = 2;
        options.dp_lookback = 8;
        options.models = crate::ModelOptions::raw_only();
        options.residuals = crate::ResidualOptions::raw_only();
        options.transforms = crate::TransformOptions::identity_only();
        options.allow_raw_fallback = false;
        options
    }

    fn independent_bit_plane_raw_options() -> EncodeOptions {
        let mut options = EncodeOptions::for_mode(Mode::Fast);
        options.segmentation = SegmentationMode::Fixed;
        options.fixed_segment_size = 3;
        options.models = crate::ModelOptions::raw_only();
        options.residuals = crate::ResidualOptions::raw_only();
        options.transforms = crate::TransformOptions {
            identity: false,
            delta: false,
            xor: false,
            bit_plane: false,
            bit_plane_independent: true,
            stride: false,
        };
        options.allow_raw_fallback = false;
        options
    }

    #[test]
    fn padded_bit_planes_are_independently_partitioned_and_round_trip() {
        let options = independent_bit_plane_raw_options();
        for original_size in 0..=65usize {
            let original: Vec<u8> = (0..original_size)
                .map(|index| ((index * 73 + 19) & 255) as u8)
                .collect();
            let archive = compress(&original, &options).unwrap();
            let parsed = container::parse(&archive, &DecodeLimits::default()).unwrap();
            let plane_size = original_size.div_ceil(8);

            assert_eq!(parsed.header.format_version, crate::FORMAT_VERSION);
            assert_eq!(parsed.header.transformed_size as usize, plane_size * 8);
            assert_eq!(parsed.transforms, [TransformKind::BitPlanePadded]);
            assert_eq!(
                decompress(&archive, &DecodeLimits::default()).unwrap(),
                original
            );

            for segment in &parsed.segments {
                let start = segment.offset as usize;
                let end = start + segment.length as usize;
                assert_ne!(plane_size, 0);
                assert_eq!(start / plane_size, (end - 1) / plane_size);
            }
            let expected_segments = if plane_size == 0 {
                0
            } else {
                8 * plane_size.div_ceil(options.fixed_segment_size)
            };
            assert_eq!(parsed.segments.len(), expected_segments);
        }
    }

    #[test]
    fn raw_fallback_is_exact_and_bounded_expansion() {
        let data: Vec<u8> = (0..4096)
            .scan(0x1234_5678u32, |state, _| {
                *state = state.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
                Some((*state >> 24) as u8)
            })
            .collect();
        let options = EncodeOptions::for_mode(Mode::Fast);
        let archive = compress(&data, &options).unwrap();
        assert!(archive.len() <= data.len() + HEADER_SIZE + FOOTER_SIZE + 40);
        assert_eq!(
            decompress(&archive, &DecodeLimits::default()).unwrap(),
            data
        );
    }

    #[test]
    fn deterministic_output() {
        let data: Vec<u8> = (0..8192).map(|i| ((i * 7 + 3) & 255) as u8).collect();
        let options = EncodeOptions::for_mode(Mode::Fast);
        let plain = compress(&data, &options).unwrap();
        let repeated = compress(&data, &options).unwrap();
        let (instrumented, metrics) = compress_with_metrics(&data, &options).unwrap();
        assert_eq!(plain, repeated);
        assert_eq!(plain, instrumented);
        assert!(metrics.search_seconds.is_finite() && metrics.search_seconds >= 0.0);
        assert!(metrics.model_fitting_seconds.is_finite() && metrics.model_fitting_seconds >= 0.0);
        assert!(
            metrics.residual_coding_seconds.is_finite() && metrics.residual_coding_seconds >= 0.0
        );
    }

    #[test]
    fn recursive_partition_is_bounded_lossless_and_deterministic() {
        let data: Vec<u8> = (0..23).map(|value| (value * 17) as u8).collect();
        let options = recursive_raw_options();
        let first = compress(&data, &options).unwrap();
        let repeated = compress(&data, &options).unwrap();
        let (instrumented, metrics) = compress_with_metrics(&data, &options).unwrap();
        assert_eq!(first, repeated);
        assert_eq!(first, instrumented);
        assert!(metrics.search_seconds.is_finite());
        assert!(metrics.model_fitting_seconds.is_finite());
        assert!(metrics.residual_coding_seconds.is_finite());
        assert_eq!(decompress(&first, &DecodeLimits::default()).unwrap(), data);

        let parsed = container::parse(&first, &DecodeLimits::default()).unwrap();
        assert_eq!(parsed.segments.len(), 3);
        assert!(parsed.segments.len() <= 1usize << options.max_tree_depth);
        assert!(parsed.segments.iter().all(|segment| {
            (options.min_segment_size as u64..=options.max_segment_size as u64)
                .contains(&segment.length)
        }));
    }

    #[test]
    fn recursive_partition_allows_only_whole_input_below_minimum() {
        let data = vec![3, 1, 4, 1, 5];
        let mut options = recursive_raw_options();
        options.min_segment_size = 8;
        options.max_segment_size = 16;
        options.max_tree_depth = 0;
        let archive = compress(&data, &options).unwrap();
        let parsed = container::parse(&archive, &DecodeLimits::default()).unwrap();
        assert_eq!(parsed.segments.len(), 1);
        assert_eq!(parsed.segments[0].length, data.len() as u64);
        assert_eq!(
            decompress(&archive, &DecodeLimits::default()).unwrap(),
            data
        );
    }

    #[test]
    fn recursive_partition_rejects_more_leaves_than_depth_permits() {
        let data = vec![0; 21];
        let mut options = recursive_raw_options();
        options.min_segment_size = 5;
        options.max_segment_size = 10;
        options.max_tree_depth = 1;
        assert!(matches!(
            compress(&data, &options),
            Err(Error::NoRepresentation)
        ));
    }

    #[test]
    fn recursive_partition_uses_serialized_cost_including_descriptors() {
        let data = vec![42; 64];
        let mut options = recursive_raw_options();
        options.fixed_segment_size = 16;
        options.min_segment_size = 16;
        options.max_segment_size = 128;
        options.max_tree_depth = 2;
        let archive = compress(&data, &options).unwrap();
        let parsed = container::parse(&archive, &DecodeLimits::default()).unwrap();
        // Raw payload bytes sum to the same value for every partition, so the
        // exact 36-byte descriptor cost must make one leaf win.
        assert_eq!(parsed.segments.len(), 1);
    }

    #[test]
    fn recursive_depth_zero_and_one_control_a_profitable_split() {
        let mut data = vec![0; 64];
        data.extend_from_slice(&[255; 64]);
        let mut options = recursive_raw_options();
        options.fixed_segment_size = 64;
        options.min_segment_size = 64;
        options.max_segment_size = 128;
        options.models = crate::ModelOptions {
            raw: false,
            constant: true,
            affine: false,
            polynomial: false,
            periodic: false,
            recurrence: false,
            piecewise_linear: false,
            run: false,
            sparse: false,
            copy: false,
        };
        options.residuals = crate::ResidualOptions {
            raw: false,
            rle: false,
            zero_run: true,
            sparse: false,
            bit_pack: false,
            zstd: false,
        };

        options.max_tree_depth = 0;
        let unsplit = compress(&data, &options).unwrap();
        let parsed = container::parse(&unsplit, &DecodeLimits::default()).unwrap();
        assert_eq!(parsed.segments.len(), 1);

        options.max_tree_depth = 1;
        let split = compress(&data, &options).unwrap();
        let parsed = container::parse(&split, &DecodeLimits::default()).unwrap();
        assert_eq!(parsed.segments.len(), 2);
        assert!(split.len() < unsplit.len());
        assert_eq!(decompress(&split, &DecodeLimits::default()).unwrap(), data);
    }

    #[test]
    fn recursive_partition_can_select_copy_from_prior_leaf() {
        let first: Vec<u8> = (0..64)
            .map(|index| ((index * 73 + 19) % 251 + 1) as u8)
            .collect();
        let mut data = first.clone();
        data.extend_from_slice(&first);
        let mut options = recursive_raw_options();
        options.fixed_segment_size = 64;
        options.min_segment_size = 64;
        options.max_segment_size = 64;
        options.max_tree_depth = 1;
        options.models = crate::ModelOptions {
            raw: true,
            constant: false,
            affine: false,
            polynomial: false,
            periodic: false,
            recurrence: false,
            piecewise_linear: false,
            run: false,
            sparse: false,
            copy: true,
        };
        options.residuals = crate::ResidualOptions {
            raw: false,
            rle: false,
            zero_run: true,
            sparse: false,
            bit_pack: false,
            zstd: false,
        };

        let archive = compress(&data, &options).unwrap();
        let parsed = container::parse(&archive, &DecodeLimits::default()).unwrap();
        assert_eq!(parsed.segments.len(), 2);
        assert_eq!(parsed.segments[1].model, crate::ModelKind::Copy);
        assert_eq!(
            decompress(&archive, &DecodeLimits::default()).unwrap(),
            data
        );
    }

    #[test]
    fn recursive_depth_validation_is_bounded() {
        let mut options = recursive_raw_options();
        options.max_tree_depth = crate::MAX_RECURSIVE_TREE_DEPTH + 1;
        assert!(matches!(
            compress(b"invalid depth", &options),
            Err(Error::InvalidArchive("invalid encoder options"))
        ));
    }

    #[test]
    fn recursive_persistent_nodes_do_not_retain_encoded_payloads() {
        assert_eq!(
            std::mem::size_of::<RecursiveNode>(),
            4 * std::mem::size_of::<usize>()
        );
        assert!(!std::mem::needs_drop::<RecursiveNode>());
        assert!(
            MAX_RECURSIVE_DP_STATES
                .checked_mul(std::mem::size_of::<RecursiveNode>())
                .unwrap()
                <= 64 * 1024 * 1024
        );
    }

    #[test]
    fn aggregate_model_work_is_bounded() {
        let original = vec![0u8; 1024];
        let model = Model::Recurrence {
            coefficients: vec![0; 16],
            seeds: vec![0; 16],
        };
        let segment = EncodedSegment {
            offset: 0,
            length: original.len() as u64,
            model: crate::ModelKind::Recurrence,
            model_parameters: model.parameters(),
            residual_mode: ResidualMode::AddModulo,
            residual_coder: ResidualCoder::Raw,
            residual: original.clone(),
            decoded_crc32: crc32(&original),
        };
        let archive = container::serialize(
            &original,
            original.len(),
            &[TransformKind::Identity],
            &[segment],
        )
        .unwrap();
        let limits = DecodeLimits {
            max_archive_size: archive.len() as u64,
            max_output_size: original.len() as u64,
            ..DecodeLimits::default()
        };
        assert!(matches!(
            decompress(&archive, &limits),
            Err(Error::LimitExceeded {
                what: "aggregate_model_work",
                ..
            })
        ));
    }
}
