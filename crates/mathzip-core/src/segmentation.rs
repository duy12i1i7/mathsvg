use crate::config::{EncodeOptions, SegmentationMode};
use crate::varint;

/// Encoder-only cap for Recursive candidate positions. Together with the
/// persistent-state cap in the optimizer, this prevents hostile-but-valid
/// options such as a one-byte anchor size from allocating one boundary per
/// byte of a large input.
pub(crate) const MAX_RECURSIVE_BOUNDARIES: usize = 262_144;

/// Generates a bounded boundary set. Actual partition selection is performed by
/// dynamic programming in the optimizer and therefore uses serialized segment
/// sizes, not an entropy proxy.
pub(crate) fn candidate_boundaries(data: &[u8], options: &EncodeOptions) -> Vec<usize> {
    if data.is_empty() {
        return vec![0];
    }
    let mut boundaries = vec![0, data.len()];
    match options.segmentation {
        SegmentationMode::Fixed => {
            add_regular(&mut boundaries, data.len(), options.fixed_segment_size);
        }
        SegmentationMode::ChangePoint => {
            add_change_points(&mut boundaries, data, options);
            // Ensure there is always a path respecting max_segment_size.
            add_regular(&mut boundaries, data.len(), options.max_segment_size);
        }
        SegmentationMode::Adaptive => {
            add_regular(&mut boundaries, data.len(), options.fixed_segment_size);
            add_change_points(&mut boundaries, data, options);
        }
        SegmentationMode::Recursive => {
            if let Some(backbone) = recursive_backbone(data.len(), options) {
                boundaries.extend(backbone);
            }
            boundaries.sort_unstable();
            boundaries.dedup();

            let remaining = MAX_RECURSIVE_BOUNDARIES.saturating_sub(boundaries.len());
            let regular_budget = remaining.saturating_mul(3) / 4;
            add_regular_sampled(
                &mut boundaries,
                data.len(),
                options.fixed_segment_size,
                regular_budget,
            );
            boundaries.sort_unstable();
            boundaries.dedup();

            let change_budget = MAX_RECURSIVE_BOUNDARIES.saturating_sub(boundaries.len());
            add_change_points_sampled(&mut boundaries, data, options, change_budget);
        }
    }
    boundaries.sort_unstable();
    boundaries.dedup();
    boundaries
}

/// Returns an ordered partition that satisfies the recursive leaf bounds and
/// depth limit, when one exists. These positions are added to the general
/// candidate set as a guaranteed feasible backbone; the optimizer may replace
/// any or all of its leaves with cheaper candidate edges.
pub(crate) fn recursive_backbone(len: usize, options: &EncodeOptions) -> Option<Vec<usize>> {
    if len == 0 {
        return Some(vec![0]);
    }
    if len < options.min_segment_size {
        return Some(vec![0, len]);
    }

    let max_leaves = 1usize.checked_shl(u32::from(options.max_tree_depth))?;
    let leaf_count =
        len / options.max_segment_size + usize::from(len % options.max_segment_size != 0);
    if leaf_count == 0 || leaf_count > max_leaves || len / leaf_count < options.min_segment_size {
        return None;
    }

    // Distribute the remainder over the earliest leaves. Every leaf then has
    // one of two adjacent sizes, which makes the min/max proof immediate and
    // avoids multiplication overflow when constructing boundaries.
    let base = len / leaf_count;
    let remainder = len % leaf_count;
    let mut boundaries = Vec::with_capacity(leaf_count + 1);
    boundaries.push(0);
    let mut position = 0usize;
    for leaf in 0..leaf_count {
        position = position.checked_add(base + usize::from(leaf < remainder))?;
        boundaries.push(position);
    }
    debug_assert_eq!(boundaries.last(), Some(&len));
    Some(boundaries)
}

fn add_regular(boundaries: &mut Vec<usize>, len: usize, step: usize) {
    let mut position = step;
    while position < len {
        boundaries.push(position);
        position = match position.checked_add(step) {
            Some(next) => next,
            None => break,
        };
    }
}

fn add_regular_sampled(boundaries: &mut Vec<usize>, len: usize, step: usize, max_points: usize) {
    if len <= 1 || max_points == 0 {
        return;
    }
    let point_count = (len - 1) / step;
    if point_count <= max_points {
        add_regular(boundaries, len, step);
        return;
    }
    if max_points == 1 {
        let index = point_count.div_ceil(2);
        if let Some(position) = index.checked_mul(step).filter(|&value| value < len) {
            boundaries.push(position);
        }
        return;
    }
    for sample in 0..max_points {
        let index =
            1 + (sample as u128 * (point_count - 1) as u128 / (max_points - 1) as u128) as usize;
        if let Some(position) = index.checked_mul(step).filter(|&value| value < len) {
            boundaries.push(position);
        }
    }
}

fn add_change_points(boundaries: &mut Vec<usize>, data: &[u8], options: &EncodeOptions) {
    add_change_points_sampled(boundaries, data, options, usize::MAX);
}

fn add_change_points_sampled(
    boundaries: &mut Vec<usize>,
    data: &[u8],
    options: &EncodeOptions,
    max_points: usize,
) {
    if max_points == 0 {
        return;
    }
    let window = (options.fixed_segment_size / 2)
        .max(options.min_segment_size)
        .min(4096);
    let step = options
        .min_segment_size
        .max(options.fixed_segment_size / 4)
        .max(64);
    if data.len() < window.saturating_mul(2) {
        return;
    }
    let span = data.len() - window.saturating_mul(2);
    let scan_count = span / step + 1;
    let sampled_count = scan_count.min(max_points);
    for sample in 0..sampled_count {
        let scan_index = if sampled_count == scan_count {
            sample
        } else if sampled_count == 1 {
            scan_count / 2
        } else {
            (sample as u128 * (scan_count - 1) as u128 / (sampled_count - 1) as u128) as usize
        };
        let Some(position) = scan_index
            .checked_mul(step)
            .and_then(|offset| window.checked_add(offset))
        else {
            break;
        };
        let left = &data[position - window..position];
        let right = &data[position..position + window];
        if change_score(left, right) >= 96 {
            boundaries.push(position);
        }
    }
}

fn change_score(left: &[u8], right: &[u8]) -> u32 {
    debug_assert_eq!(left.len(), right.len());
    if left.is_empty() {
        return 0;
    }
    let left_features = window_features(left);
    let right_features = window_features(right);
    let len = left.len() as u64;

    // All feature extraction and scaling is integer-only so the selected
    // candidate boundaries remain deterministic across architectures. The
    // histogram and mean terms preserve the original MVP calibration; the
    // additional bounded terms cover every signal required by the design.
    let mean_difference = left_features.sum.abs_diff(right_features.sum) / len;
    let histogram_l1 = left_features
        .histogram
        .iter()
        .zip(right_features.histogram.iter())
        .map(|(&a, &b)| u64::from(a.abs_diff(b)))
        .sum::<u64>()
        * 128
        / len;
    let entropy_difference =
        u64::from(left_features.entropy_q8.abs_diff(right_features.entropy_q8)) / 8;
    let variance_difference = u64::from(
        left_features
            .variance_q8
            .abs_diff(right_features.variance_q8),
    ) / 2;
    let autocorrelation_difference = u64::from(
        left_features
            .autocorrelation_q8
            .abs_diff(right_features.autocorrelation_q8),
    ) / 4;
    let periodicity_difference = u64::from(
        left_features
            .periodicity_q8
            .abs_diff(right_features.periodicity_q8),
    ) / 2;
    let compression_probe_difference = u64::from(
        left_features
            .compression_savings_q8
            .abs_diff(right_features.compression_savings_q8),
    ) / 2;
    let bit_density_difference = u64::from(
        left_features
            .bit_density_q8
            .abs_diff(right_features.bit_density_q8),
    ) / 2;
    let prediction_residual_difference = u64::from(
        left_features
            .prediction_residual_q8
            .abs_diff(right_features.prediction_residual_q8),
    ) / 2;

    mean_difference
        .saturating_mul(2)
        .saturating_add(histogram_l1)
        .saturating_add(entropy_difference)
        .saturating_add(variance_difference)
        .saturating_add(autocorrelation_difference)
        .saturating_add(periodicity_difference)
        .saturating_add(compression_probe_difference)
        .saturating_add(bit_density_difference)
        .saturating_add(prediction_residual_difference)
        .try_into()
        .unwrap_or(u32::MAX)
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct WindowFeatures {
    histogram: [u32; 256],
    sum: u64,
    entropy_q8: u32,
    variance_q8: u32,
    autocorrelation_q8: i32,
    periodicity_q8: u32,
    compression_savings_q8: u32,
    bit_density_q8: u32,
    prediction_residual_q8: u32,
}

fn window_features(data: &[u8]) -> WindowFeatures {
    debug_assert!(!data.is_empty());
    let mut histogram = [0u32; 256];
    let mut sum = 0u64;
    let mut sum_squares = 0u64;
    let mut ones = 0u64;
    let mut difference_histogram = [0u32; 256];
    let mut previous = None;
    let mut rle_size = 0usize;
    let mut run_start = 0usize;
    for (position, &value) in data.iter().enumerate() {
        histogram[value as usize] += 1;
        sum += u64::from(value);
        sum_squares += u64::from(value) * u64::from(value);
        ones += u64::from(value.count_ones());
        if let Some(previous) = previous {
            difference_histogram[value.wrapping_sub(previous) as usize] += 1;
            if value != previous {
                rle_size = rle_size
                    .saturating_add(varint::encoded_len((position - run_start) as u64))
                    .saturating_add(1);
                run_start = position;
            }
        }
        previous = Some(value);
    }
    rle_size = rle_size
        .saturating_add(varint::encoded_len((data.len() - run_start) as u64))
        .saturating_add(1);

    let len = data.len() as u64;
    let weighted_log_sum = histogram
        .iter()
        .filter(|&&count| count != 0)
        .map(|&count| u64::from(count) * u64::from(log2_q8(u64::from(count))))
        .sum::<u64>();
    let entropy_q8 = log2_q8(len).saturating_sub((weighted_log_sum / len) as u32);

    // Population variance normalized to the maximum byte variance and Q8.
    // The largest analysis window is 4096 bytes, but u128 keeps this helper
    // correct if that bound is widened later.
    let variance_numerator =
        u128::from(len) * u128::from(sum_squares) - u128::from(sum) * u128::from(sum);
    let maximum_byte_variance = 16_256u128;
    let variance_denominator = u128::from(len) * u128::from(len) * maximum_byte_variance;
    let variance_q8 = ((variance_numerator * 256) / variance_denominator)
        .min(256)
        .try_into()
        .unwrap_or(256);

    let autocorrelation_q8 = lag_one_autocorrelation_q8(data, sum);
    let periodicity_q8 = periodicity_q8(data);
    let best_probe_size = data.len().min(rle_size);
    let compression_savings_q8 =
        ((data.len() - best_probe_size) as u128 * 256 / data.len() as u128) as u32;
    let bit_density_q8 = (ones * 32 / len) as u32;
    let prediction_residual_q8 = if data.len() < 2 {
        0
    } else {
        let modal_difference = difference_histogram.iter().copied().max().unwrap_or(0);
        (u64::from(modal_difference) * 256 / (len - 1)) as u32
    };

    WindowFeatures {
        histogram,
        sum,
        entropy_q8,
        variance_q8,
        autocorrelation_q8,
        periodicity_q8,
        compression_savings_q8,
        bit_density_q8,
        prediction_residual_q8,
    }
}

/// Deterministic fixed-point log2 with eight fractional bits.
fn log2_q8(value: u64) -> u32 {
    debug_assert!(value != 0);
    let integer = 63 - value.leading_zeros();
    let mut normalized = (u128::from(value) << 32) >> integer;
    let mut fraction = 0u32;
    for bit in 0..8u32 {
        normalized = (normalized * normalized) >> 32;
        if normalized >= (2u128 << 32) {
            normalized >>= 1;
            fraction |= 1 << (7 - bit);
        }
    }
    integer * 256 + fraction
}

fn lag_one_autocorrelation_q8(data: &[u8], sum: u64) -> i32 {
    if data.len() < 2 {
        return 0;
    }
    let len = data.len() as i128;
    let sum = i128::from(sum);
    let mut covariance = 0i128;
    let mut left_energy = 0i128;
    let mut right_energy = 0i128;
    for pair in data.windows(2) {
        let left = i128::from(pair[0]) * len - sum;
        let right = i128::from(pair[1]) * len - sum;
        covariance += left * right;
        left_energy += left * left;
        right_energy += right * right;
    }
    let denominator = left_energy.max(right_energy);
    if denominator == 0 {
        return 0;
    }
    ((covariance * 256 / denominator).clamp(-256, 256)) as i32
}

fn periodicity_q8(data: &[u8]) -> u32 {
    [2usize, 3, 4, 8, 16, 32]
        .into_iter()
        .filter(|&period| period < data.len())
        .map(|period| {
            let comparisons = data.len() - period;
            let matches = data[period..]
                .iter()
                .zip(&data[..comparisons])
                .filter(|(left, right)| left == right)
                .count();
            (matches as u128 * 256 / comparisons as u128) as u32
        })
        .max()
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{EncodeOptions, SegmentationMode};

    #[test]
    fn fixed_boundaries_are_stable() {
        let options = EncodeOptions {
            segmentation: SegmentationMode::Fixed,
            fixed_segment_size: 4,
            ..EncodeOptions::default()
        };
        assert_eq!(candidate_boundaries(&[0; 10], &options), vec![0, 4, 8, 10]);
    }

    #[test]
    fn detects_strong_change() {
        let options = EncodeOptions {
            fixed_segment_size: 256,
            min_segment_size: 64,
            segmentation: SegmentationMode::ChangePoint,
            ..EncodeOptions::default()
        };
        let mut data = vec![0; 512];
        data.extend(vec![255; 512]);
        assert!(candidate_boundaries(&data, &options)
            .iter()
            .any(|&position| position.abs_diff(512) <= 64));
    }

    #[test]
    fn fixed_point_logarithm_and_entropy_match_known_values() {
        for exponent in 0..=32 {
            assert_eq!(log2_q8(1u64 << exponent), exponent * 256);
        }

        let constant = window_features(&[7; 256]);
        let uniform = window_features(&(0u8..=u8::MAX).collect::<Vec<_>>());
        assert_eq!(constant.entropy_q8, 0);
        assert_eq!(uniform.entropy_q8, 8 * 256);
    }

    #[test]
    fn window_features_cover_all_required_change_signals() {
        let zeros = window_features(&[0; 256]);
        let ones = window_features(&[u8::MAX; 256]);
        let uniform = window_features(&(0u8..=u8::MAX).collect::<Vec<_>>());

        assert_eq!(zeros.sum, 0);
        assert!(ones.sum > zeros.sum);
        assert_eq!(zeros.variance_q8, 0);
        assert!(uniform.variance_q8 > zeros.variance_q8);
        assert_eq!(zeros.bit_density_q8, 0);
        assert_eq!(ones.bit_density_q8, 256);
        assert!(zeros.compression_savings_q8 > uniform.compression_savings_q8);

        let periodic = (0..512)
            .map(|position| (position % 4) as u8)
            .collect::<Vec<_>>();
        let mut state = 0x9e37_79b9u32;
        let irregular = (0..512)
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 17;
                state ^= state << 5;
                state as u8
            })
            .collect::<Vec<_>>();
        let periodic_features = window_features(&periodic);
        let irregular_features = window_features(&irregular);
        assert_eq!(periodic_features.periodicity_q8, 256);
        assert!(periodic_features.periodicity_q8 > irregular_features.periodicity_q8);
        assert!(
            periodic_features.prediction_residual_q8 > irregular_features.prediction_residual_q8
        );
    }

    #[test]
    fn change_score_detects_order_changes_with_equal_byte_statistics() {
        let alternating = (0..256)
            .map(|position| if position % 2 == 0 { 0 } else { u8::MAX })
            .collect::<Vec<_>>();
        let mut runs = vec![0; 128];
        runs.extend(vec![u8::MAX; 128]);

        let alternating_features = window_features(&alternating);
        let run_features = window_features(&runs);
        assert_eq!(alternating_features.histogram, run_features.histogram);
        assert_eq!(alternating_features.sum, run_features.sum);
        assert_eq!(alternating_features.entropy_q8, run_features.entropy_q8);
        assert_eq!(alternating_features.variance_q8, run_features.variance_q8);
        assert_eq!(
            alternating_features.bit_density_q8,
            run_features.bit_density_q8
        );
        assert_ne!(
            alternating_features.autocorrelation_q8,
            run_features.autocorrelation_q8
        );
        assert_ne!(
            alternating_features.compression_savings_q8,
            run_features.compression_savings_q8
        );
        assert_ne!(
            alternating_features.prediction_residual_q8,
            run_features.prediction_residual_q8
        );
        assert!(change_score(&alternating, &runs) >= 96);
    }

    #[test]
    fn recursive_backbone_obeys_leaf_bounds_and_depth() {
        let options = EncodeOptions {
            segmentation: SegmentationMode::Recursive,
            min_segment_size: 6,
            max_segment_size: 10,
            max_tree_depth: 2,
            ..EncodeOptions::default()
        };
        let backbone = recursive_backbone(23, &options).unwrap();
        assert_eq!(backbone, vec![0, 8, 16, 23]);
        assert!(backbone
            .windows(2)
            .all(|pair| (6..=10).contains(&(pair[1] - pair[0]))));
        assert!(backbone.len() - 1 <= 1usize << options.max_tree_depth);
    }

    #[test]
    fn recursive_backbone_rejects_an_infeasible_depth_or_leaf_range() {
        let depth_limited = EncodeOptions {
            min_segment_size: 5,
            max_segment_size: 10,
            max_tree_depth: 1,
            ..EncodeOptions::default()
        };
        assert_eq!(recursive_backbone(21, &depth_limited), None);

        let bounds_limited = EncodeOptions {
            min_segment_size: 6,
            max_segment_size: 10,
            max_tree_depth: 4,
            ..EncodeOptions::default()
        };
        assert_eq!(recursive_backbone(11, &bounds_limited), None);
    }

    #[test]
    fn recursive_backbone_allows_only_the_whole_short_input() {
        let options = EncodeOptions {
            min_segment_size: 8,
            max_segment_size: 16,
            max_tree_depth: 0,
            ..EncodeOptions::default()
        };
        assert_eq!(recursive_backbone(5, &options), Some(vec![0, 5]));
    }

    #[test]
    fn recursive_candidate_count_is_bounded_with_one_byte_anchors() {
        let data = vec![0; MAX_RECURSIVE_BOUNDARIES + 101];
        let options = EncodeOptions {
            segmentation: SegmentationMode::Recursive,
            fixed_segment_size: 1,
            min_segment_size: 1,
            max_segment_size: 64,
            max_tree_depth: 16,
            ..EncodeOptions::default()
        };
        let backbone = recursive_backbone(data.len(), &options).unwrap();
        let boundaries = candidate_boundaries(&data, &options);
        assert!(boundaries.len() <= MAX_RECURSIVE_BOUNDARIES);
        assert_eq!(boundaries.first(), Some(&0));
        assert_eq!(boundaries.last(), Some(&data.len()));
        assert!(backbone
            .iter()
            .all(|boundary| boundaries.binary_search(boundary).is_ok()));
    }
}
