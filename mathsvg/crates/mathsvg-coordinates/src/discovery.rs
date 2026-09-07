use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

use mathsvg_core::{CandidateCost, Error, Limits, Result};

use crate::{forward, CoordinateDescriptor, CoordinateTransform, Endianness, MAX_CHANNELS};

const WIDTHS: [u8; 6] = [1, 2, 3, 4, 6, 8];
const DEFAULT_CHANNELS: [u16; 6] = [2, 3, 4, 6, 8, 16];
const HARD_MAX_CANDIDATES: u16 = 4_096;
const HARD_MAX_PROBE_LAG: u16 = 4_096;
const HARD_MAX_PROBE_BYTES: u32 = 1_048_576;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DiscoveryConfig {
    pub max_candidates: u16,
    pub max_channels: u16,
    pub max_probe_lag: u16,
    pub max_peak_lags: u16,
    pub max_probe_bytes: u32,
}

impl Default for DiscoveryConfig {
    fn default() -> Self {
        Self {
            max_candidates: 256,
            max_channels: 64,
            max_probe_lag: 256,
            max_peak_lags: 16,
            max_probe_bytes: 65_536,
        }
    }
}

impl DiscoveryConfig {
    fn validate(&self) -> Result<()> {
        if self.max_candidates == 0 || self.max_candidates > HARD_MAX_CANDIDATES {
            return Err(Error::InvalidValue(
                "coordinate candidate budget is outside the bounded range",
            ));
        }
        if !(2..=MAX_CHANNELS).contains(&self.max_channels) {
            return Err(Error::InvalidValue(
                "coordinate discovery channel bound is invalid",
            ));
        }
        if self.max_probe_lag > HARD_MAX_PROBE_LAG {
            return Err(Error::InvalidValue(
                "coordinate probe lag exceeds the hard bound",
            ));
        }
        if self.max_probe_bytes == 0 || self.max_probe_bytes > HARD_MAX_PROBE_BYTES {
            return Err(Error::InvalidValue(
                "coordinate probe byte budget is outside the bounded range",
            ));
        }
        if self.max_peak_lags > self.max_probe_lag {
            return Err(Error::InvalidValue(
                "coordinate peak count exceeds the lag range",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum CandidateOrigin {
    Mandatory,
    FrozenDefault,
    AutocorrelationPeak,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CoordinateCandidate {
    pub descriptor: CoordinateDescriptor,
    pub origin: CandidateOrigin,
    pub canonical_descriptor_bytes: Vec<u8>,
}

impl CoordinateCandidate {
    pub fn descriptor_bytes(&self) -> u64 {
        self.canonical_descriptor_bytes.len() as u64
    }

    pub fn dsl_metadata_bytes(&self, child_record_bytes: u64) -> Result<u64> {
        self.descriptor.dsl_metadata_bytes(child_record_bytes)
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum DiscoveryEventKind {
    HeuristicSkip,
    BudgetStop,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DiscoveryEvent {
    pub descriptor: CoordinateDescriptor,
    pub event: DiscoveryEventKind,
    pub reason: &'static str,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DiscoveryResult {
    pub candidates: Vec<CoordinateCandidate>,
    pub events: Vec<DiscoveryEvent>,
    pub probe_bytes: u64,
    pub probe_work: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExactSelection {
    pub candidate: CoordinateCandidate,
    pub cost: CandidateCost,
    pub transformed: Vec<u8>,
    pub discovery_events: Vec<DiscoveryEvent>,
}

pub fn discover_candidates(
    input: &[u8],
    limits: &Limits,
    config: &DiscoveryConfig,
) -> Result<DiscoveryResult> {
    config.validate()?;
    limits.check(
        "coordinate discovery input",
        input.len() as u64,
        limits.max_block_output_bytes,
    )?;
    let original_bytes = input.len() as u64;

    let mut mandatory = BTreeSet::new();
    mandatory.insert(CoordinateDescriptor::identity(original_bytes));
    mandatory.insert(CoordinateDescriptor {
        original_bytes,
        transform: CoordinateTransform::BitPlane,
    });
    for width in WIDTHS {
        if !is_multiple(original_bytes, u64::from(width)) {
            continue;
        }
        mandatory.insert(CoordinateDescriptor {
            original_bytes,
            transform: CoordinateTransform::BytePlane {
                word_width: width,
                endian: Endianness::Little,
            },
        });
        if width > 1 {
            mandatory.insert(CoordinateDescriptor {
                original_bytes,
                transform: CoordinateTransform::BytePlane {
                    word_width: width,
                    endian: Endianness::Big,
                },
            });
        }
    }

    if mandatory.len() > usize::from(config.max_candidates) {
        return Err(Error::LimitExceeded {
            what: "mandatory coordinate candidates",
            actual: mandatory.len() as u64,
            limit: u64::from(config.max_candidates),
        });
    }

    let mut optional = BTreeMap::new();
    for width in WIDTHS {
        for channels in DEFAULT_CHANNELS {
            insert_stride_candidate(
                &mut optional,
                original_bytes,
                width,
                channels,
                config.max_channels,
                CandidateOrigin::FrozenDefault,
            );
        }
    }

    let (peaks, skipped_peaks, probe_bytes, probe_work) =
        autocorrelation_peaks(input, config, limits)?;
    for lag in peaks {
        for width in WIDTHS {
            let width = u16::from(width);
            if lag.checked_rem(width) != Some(0) {
                continue;
            }
            let channels = lag / width;
            insert_stride_candidate(
                &mut optional,
                original_bytes,
                width as u8,
                channels,
                config.max_channels,
                CandidateOrigin::AutocorrelationPeak,
            );
        }
    }
    let mut heuristic_skips = BTreeSet::new();
    for lag in skipped_peaks {
        for width in WIDTHS {
            let width_u16 = u16::from(width);
            if lag.checked_rem(width_u16) != Some(0) {
                continue;
            }
            let channels = lag / width_u16;
            if let Some(descriptor) =
                stride_descriptor(original_bytes, width, channels, config.max_channels)
            {
                heuristic_skips.insert(descriptor);
            }
        }
    }
    for descriptor in &mandatory {
        optional.remove(descriptor);
        heuristic_skips.remove(descriptor);
    }
    for descriptor in optional.keys() {
        heuristic_skips.remove(descriptor);
    }

    let mut selected: Vec<(CoordinateDescriptor, CandidateOrigin)> = mandatory
        .into_iter()
        .map(|descriptor| (descriptor, CandidateOrigin::Mandatory))
        .collect();
    let optional_capacity = usize::from(config.max_candidates) - selected.len();
    let mut events: Vec<_> = heuristic_skips
        .into_iter()
        .map(|descriptor| DiscoveryEvent {
            descriptor,
            event: DiscoveryEventKind::HeuristicSkip,
            reason: "autocorrelation peak budget",
        })
        .collect();
    for (index, (descriptor, origin)) in optional.into_iter().enumerate() {
        if index < optional_capacity {
            selected.push((descriptor, origin));
        } else {
            events.push(DiscoveryEvent {
                descriptor,
                event: DiscoveryEventKind::BudgetStop,
                reason: "coordinate candidate budget",
            });
        }
    }
    selected.sort_by_key(|(descriptor, _)| *descriptor);
    events.sort_by_key(|event| event.descriptor);

    let mut candidates = Vec::with_capacity(selected.len());
    for (descriptor, origin) in selected {
        descriptor.validate(limits)?;
        candidates.push(CoordinateCandidate {
            canonical_descriptor_bytes: descriptor.encode(limits)?,
            descriptor,
            origin,
        });
    }

    Ok(DiscoveryResult {
        candidates,
        events,
        probe_bytes,
        probe_work,
    })
}

pub fn select_exact<F>(
    input: &[u8],
    limits: &Limits,
    config: &DiscoveryConfig,
    mut complete_cost: F,
) -> Result<ExactSelection>
where
    F: FnMut(&CoordinateCandidate, &[u8]) -> Result<CandidateCost>,
{
    let discovery = discover_candidates(input, limits, config)?;
    let mut best: Option<(CoordinateCandidate, CandidateCost)> = None;

    for candidate in discovery.candidates {
        let transformed = forward(input, &candidate.descriptor, limits)?;
        let cost = complete_cost(&candidate, &transformed)?;
        let replace = best.as_ref().is_none_or(|(best_candidate, best_cost)| {
            cost.cmp(best_cost)
                .then_with(|| candidate.descriptor.cmp(&best_candidate.descriptor))
                == Ordering::Less
        });
        if replace {
            best = Some((candidate, cost));
        }
    }

    let (candidate, cost) = best.ok_or(Error::InvalidValue(
        "coordinate discovery produced no candidates",
    ))?;
    let transformed = forward(input, &candidate.descriptor, limits)?;
    Ok(ExactSelection {
        candidate,
        cost,
        transformed,
        discovery_events: discovery.events,
    })
}

fn insert_stride_candidate(
    candidates: &mut BTreeMap<CoordinateDescriptor, CandidateOrigin>,
    original_bytes: u64,
    width: u8,
    channels: u16,
    channel_limit: u16,
    origin: CandidateOrigin,
) {
    let Some(descriptor) = stride_descriptor(original_bytes, width, channels, channel_limit) else {
        return;
    };
    candidates
        .entry(descriptor)
        .and_modify(|current| *current = (*current).min(origin))
        .or_insert(origin);
}

fn stride_descriptor(
    original_bytes: u64,
    width: u8,
    channels: u16,
    channel_limit: u16,
) -> Option<CoordinateDescriptor> {
    if original_bytes == 0 || !(2..=channel_limit).contains(&channels) {
        return None;
    }
    let record_bytes = u64::from(width) * u64::from(channels);
    if !is_multiple(original_bytes, record_bytes) {
        return None;
    }
    Some(CoordinateDescriptor {
        original_bytes,
        transform: CoordinateTransform::Stride {
            element_width: width,
            channels,
        },
    })
}

fn is_multiple(value: u64, divisor: u64) -> bool {
    value.checked_rem(divisor) == Some(0)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Peak {
    lag: u16,
    matches: u64,
    comparisons: u64,
}

fn autocorrelation_peaks(
    input: &[u8],
    config: &DiscoveryConfig,
    limits: &Limits,
) -> Result<(Vec<u16>, Vec<u16>, u64, u64)> {
    let probe_length = input.len().min(config.max_probe_bytes as usize);
    let max_lag = usize::from(config.max_probe_lag).min(probe_length.saturating_sub(1));
    let mut peaks = Vec::new();
    let mut work = 0u64;

    for lag in 2..=max_lag {
        work = work
            .checked_add((probe_length - lag) as u64)
            .ok_or(Error::IntegerOverflow {
                context: "coordinate probe work",
            })?;
    }
    limits.check("coordinate probe work", work, limits.max_work)?;

    for lag in 2..=max_lag {
        let mut matches = 0u64;
        for index in lag..probe_length {
            matches += u64::from(input[index] == input[index - lag]);
        }
        let comparisons = (probe_length - lag) as u64;
        if matches > 0 {
            peaks.push(Peak {
                lag: lag as u16,
                matches,
                comparisons,
            });
        }
    }

    peaks.sort_by(|left, right| {
        let left_cross = u128::from(left.matches) * u128::from(right.comparisons);
        let right_cross = u128::from(right.matches) * u128::from(left.comparisons);
        right_cross
            .cmp(&left_cross)
            .then_with(|| left.lag.cmp(&right.lag))
    });
    let skipped = peaks.split_off(usize::from(config.max_peak_lags).min(peaks.len()));
    let lags = peaks.into_iter().map(|peak| peak.lag).collect();
    let skipped_lags = skipped.into_iter().map(|peak| peak.lag).collect();
    Ok((lags, skipped_lags, probe_length as u64, work))
}
