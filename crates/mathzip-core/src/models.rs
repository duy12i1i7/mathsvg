use crate::config::{DecodeLimits, EncodeOptions, Mode};
#[cfg(test)]
use crate::residual;
use crate::residual::ResidualMode;
use crate::varint;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};

/// Predictor family stored in a segment descriptor.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelKind {
    Raw,
    Constant,
    Affine,
    Polynomial,
    Periodic,
    Recurrence,
    PiecewiseLinear,
    Run,
    Sparse,
    Copy,
}

impl ModelKind {
    pub(crate) fn id(self) -> u8 {
        match self {
            Self::Raw => 0,
            Self::Constant => 1,
            Self::Affine => 2,
            Self::Polynomial => 3,
            Self::Periodic => 4,
            Self::Recurrence => 5,
            Self::PiecewiseLinear => 6,
            Self::Run => 7,
            Self::Sparse => 8,
            Self::Copy => 9,
        }
    }

    pub(crate) fn from_id(id: u8) -> Result<Self> {
        match id {
            0 => Ok(Self::Raw),
            1 => Ok(Self::Constant),
            2 => Ok(Self::Affine),
            3 => Ok(Self::Polynomial),
            4 => Ok(Self::Periodic),
            5 => Ok(Self::Recurrence),
            6 => Ok(Self::PiecewiseLinear),
            7 => Ok(Self::Run),
            8 => Ok(Self::Sparse),
            9 => Ok(Self::Copy),
            id => Err(Error::UnknownId { kind: "model", id }),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Model {
    Raw,
    Constant(u8),
    Affine {
        a: u8,
        b: u8,
    },
    /// Coefficients are initial forward differences in the Newton basis.
    Polynomial {
        coefficients: Vec<u8>,
    },
    Periodic {
        pattern: Vec<u8>,
    },
    Recurrence {
        coefficients: Vec<u8>,
        seeds: Vec<u8>,
    },
    PiecewiseLinear {
        points: Vec<(u32, u8)>,
    },
    Run {
        runs: Vec<(u64, u8)>,
    },
    Sparse {
        default: u8,
        exceptions: Vec<(u64, u8)>,
    },
    /// Version 1 only permits non-overlapping references (`distance >= len`).
    Copy {
        distance: u64,
    },
}

impl Model {
    pub(crate) fn kind(&self) -> ModelKind {
        match self {
            Self::Raw => ModelKind::Raw,
            Self::Constant(_) => ModelKind::Constant,
            Self::Affine { .. } => ModelKind::Affine,
            Self::Polynomial { .. } => ModelKind::Polynomial,
            Self::Periodic { .. } => ModelKind::Periodic,
            Self::Recurrence { .. } => ModelKind::Recurrence,
            Self::PiecewiseLinear { .. } => ModelKind::PiecewiseLinear,
            Self::Run { .. } => ModelKind::Run,
            Self::Sparse { .. } => ModelKind::Sparse,
            Self::Copy { .. } => ModelKind::Copy,
        }
    }

    pub(crate) fn parameters(&self) -> Vec<u8> {
        let mut out = Vec::new();
        match self {
            Self::Raw => {}
            Self::Constant(value) => out.push(*value),
            Self::Affine { a, b } => {
                out.push(*a);
                out.push(*b);
            }
            Self::Polynomial { coefficients } => {
                out.push((coefficients.len() - 1) as u8);
                out.extend_from_slice(coefficients);
            }
            Self::Periodic { pattern } => {
                varint::put(pattern.len() as u64, &mut out);
                out.extend_from_slice(pattern);
            }
            Self::Recurrence {
                coefficients,
                seeds,
            } => {
                debug_assert_eq!(coefficients.len(), seeds.len());
                varint::put(coefficients.len() as u64, &mut out);
                out.extend_from_slice(coefficients);
                out.extend_from_slice(seeds);
            }
            Self::PiecewiseLinear { points } => {
                varint::put(points.len() as u64, &mut out);
                let mut previous = 0u32;
                for (index, &(position, value)) in points.iter().enumerate() {
                    let delta = if index == 0 {
                        position
                    } else {
                        position - previous - 1
                    };
                    varint::put(u64::from(delta), &mut out);
                    out.push(value);
                    previous = position;
                }
            }
            Self::Run { runs } => {
                varint::put(runs.len() as u64, &mut out);
                for &(length, value) in runs {
                    varint::put(length, &mut out);
                    out.push(value);
                }
            }
            Self::Sparse {
                default,
                exceptions,
            } => {
                out.push(*default);
                varint::put(exceptions.len() as u64, &mut out);
                let mut previous = 0u64;
                for (index, &(position, value)) in exceptions.iter().enumerate() {
                    let delta = if index == 0 {
                        position
                    } else {
                        position - previous - 1
                    };
                    varint::put(delta, &mut out);
                    out.push(value);
                    previous = position;
                }
            }
            Self::Copy { distance } => varint::put(*distance, &mut out),
        }
        out
    }

    /// Conservative per-output-byte work multiplier used to reject
    /// authenticated archives that request disproportionate model evaluation.
    pub(crate) fn decode_work_weight(&self) -> u64 {
        match self {
            Self::Polynomial { coefficients } => coefficients.len() as u64,
            Self::Recurrence { coefficients, .. } => coefficients.len() as u64,
            _ => 1,
        }
    }

    pub(crate) fn decode(
        kind: ModelKind,
        params: &[u8],
        segment_len: usize,
        absolute_offset: usize,
        limits: &DecodeLimits,
    ) -> Result<Self> {
        match kind {
            ModelKind::Raw => {
                require_exact(params, 0)?;
                Ok(Self::Raw)
            }
            ModelKind::Constant => {
                require_exact(params, 1)?;
                Ok(Self::Constant(params[0]))
            }
            ModelKind::Affine => {
                require_exact(params, 2)?;
                Ok(Self::Affine {
                    a: params[0],
                    b: params[1],
                })
            }
            ModelKind::Polynomial => {
                let (&degree, coefficients) = params
                    .split_first()
                    .ok_or(Error::InvalidModel("missing polynomial degree"))?;
                if !(2..=4).contains(&degree) || coefficients.len() != usize::from(degree) + 1 {
                    return Err(Error::InvalidModel("invalid polynomial coefficient count"));
                }
                Ok(Self::Polynomial {
                    coefficients: coefficients.to_vec(),
                })
            }
            ModelKind::Periodic => {
                let mut cursor = 0;
                let period = varint::usize_from(varint::get(params, &mut cursor)?)?;
                if period == 0
                    || period > segment_len
                    || u64::try_from(period).unwrap_or(u64::MAX) > u64::from(limits.max_period)
                {
                    return Err(Error::InvalidModel("invalid or excessive period"));
                }
                if params.len() - cursor != period {
                    return Err(Error::InvalidModel("periodic pattern length mismatch"));
                }
                Ok(Self::Periodic {
                    pattern: params[cursor..].to_vec(),
                })
            }
            ModelKind::Recurrence => {
                let mut cursor = 0;
                let order = varint::usize_from(varint::get(params, &mut cursor)?)?;
                if order == 0
                    || order > segment_len
                    || u64::try_from(order).unwrap_or(u64::MAX)
                        > u64::from(limits.max_recurrence_order)
                {
                    return Err(Error::InvalidModel("invalid or excessive recurrence order"));
                }
                let bytes = order.checked_mul(2).ok_or(Error::IntegerOverflow)?;
                if params.len() - cursor != bytes {
                    return Err(Error::InvalidModel("recurrence parameter length mismatch"));
                }
                Ok(Self::Recurrence {
                    coefficients: params[cursor..cursor + order].to_vec(),
                    seeds: params[cursor + order..].to_vec(),
                })
            }
            ModelKind::PiecewiseLinear => {
                let mut cursor = 0;
                let count = varint::usize_from(varint::get(params, &mut cursor)?)?;
                if count == 0 || count as u64 > u64::from(limits.max_control_points) {
                    return Err(Error::InvalidModel(
                        "invalid or excessive piecewise point count",
                    ));
                }
                if count > (params.len().saturating_sub(cursor) / 2) {
                    return Err(Error::InvalidModel(
                        "piecewise point count cannot fit parameters",
                    ));
                }
                let mut points = Vec::with_capacity(count);
                let mut previous: Option<u64> = None;
                for _ in 0..count {
                    let delta = varint::get(params, &mut cursor)?;
                    let position = match previous {
                        None => delta,
                        Some(previous) => previous
                            .checked_add(1)
                            .and_then(|x| x.checked_add(delta))
                            .ok_or(Error::IntegerOverflow)?,
                    };
                    let position =
                        u32::try_from(position).map_err(|_| Error::InvalidModel("point > u32"))?;
                    let value = *params.get(cursor).ok_or(Error::Truncated {
                        context: "piecewise point value",
                    })?;
                    cursor += 1;
                    points.push((position, value));
                    previous = Some(u64::from(position));
                }
                if cursor != params.len()
                    || points.first().map(|point| point.0) != Some(0)
                    || points.last().map(|point| point.0 as usize) != segment_len.checked_sub(1)
                {
                    return Err(Error::InvalidModel("piecewise endpoints are invalid"));
                }
                if segment_len == 1 && count != 1 || segment_len > 1 && count < 2 {
                    return Err(Error::InvalidModel("piecewise point count is invalid"));
                }
                Ok(Self::PiecewiseLinear { points })
            }
            ModelKind::Run => {
                let mut cursor = 0;
                let count = varint::usize_from(varint::get(params, &mut cursor)?)?;
                if count == 0
                    || count > segment_len
                    || count > params.len().saturating_sub(cursor) / 2
                    || count as u64 > u64::from(limits.max_control_points)
                {
                    return Err(Error::InvalidModel("invalid or excessive run count"));
                }
                let mut total = 0u64;
                let mut runs = Vec::with_capacity(count);
                for _ in 0..count {
                    let length = varint::get(params, &mut cursor)?;
                    if length == 0 {
                        return Err(Error::InvalidModel("zero-length model run"));
                    }
                    total = total.checked_add(length).ok_or(Error::IntegerOverflow)?;
                    let value = *params.get(cursor).ok_or(Error::Truncated {
                        context: "run model value",
                    })?;
                    cursor += 1;
                    runs.push((length, value));
                }
                if cursor != params.len() || total != segment_len as u64 {
                    return Err(Error::InvalidModel("run model length mismatch"));
                }
                Ok(Self::Run { runs })
            }
            ModelKind::Sparse => {
                let (&default, rest) = params
                    .split_first()
                    .ok_or(Error::InvalidModel("missing sparse default"))?;
                let mut cursor = 0;
                let count = varint::usize_from(varint::get(rest, &mut cursor)?)?;
                if count > segment_len
                    || count > rest.len().saturating_sub(cursor) / 2
                    || count as u64 > u64::from(limits.max_control_points)
                {
                    return Err(Error::InvalidModel("too many sparse model entries"));
                }
                let mut exceptions = Vec::with_capacity(count);
                let mut previous: Option<u64> = None;
                for _ in 0..count {
                    let delta = varint::get(rest, &mut cursor)?;
                    let position = match previous {
                        None => delta,
                        Some(previous) => previous
                            .checked_add(1)
                            .and_then(|x| x.checked_add(delta))
                            .ok_or(Error::IntegerOverflow)?,
                    };
                    if position >= segment_len as u64 {
                        return Err(Error::InvalidModel("sparse model position out of bounds"));
                    }
                    let value = *rest.get(cursor).ok_or(Error::Truncated {
                        context: "sparse model value",
                    })?;
                    cursor += 1;
                    if value == default {
                        return Err(Error::InvalidModel("redundant sparse model entry"));
                    }
                    exceptions.push((position, value));
                    previous = Some(position);
                }
                if cursor != rest.len() {
                    return Err(Error::InvalidModel("trailing sparse model bytes"));
                }
                Ok(Self::Sparse {
                    default,
                    exceptions,
                })
            }
            ModelKind::Copy => {
                let mut cursor = 0;
                let distance = varint::get(params, &mut cursor)?;
                if cursor != params.len()
                    || distance < segment_len as u64
                    || distance > absolute_offset as u64
                {
                    return Err(Error::InvalidModel("invalid copy distance"));
                }
                Ok(Self::Copy { distance })
            }
        }
    }

    pub(crate) fn predict(
        &self,
        len: usize,
        absolute_offset: usize,
        history: &[u8],
    ) -> Result<Vec<u8>> {
        Ok(match self {
            Self::Raw => vec![0; len],
            Self::Constant(value) => vec![*value; len],
            Self::Affine { a, b } => (0..len)
                .map(|index| a.wrapping_mul(index as u8).wrapping_add(*b))
                .collect(),
            Self::Polynomial { coefficients } => {
                let mut state = coefficients.clone();
                let mut out = Vec::with_capacity(len);
                for _ in 0..len {
                    out.push(state[0]);
                    for degree in 0..state.len() - 1 {
                        state[degree] = state[degree].wrapping_add(state[degree + 1]);
                    }
                }
                out
            }
            Self::Periodic { pattern } => (0..len)
                .map(|index| pattern[index % pattern.len()])
                .collect(),
            Self::Recurrence {
                coefficients,
                seeds,
            } => {
                let order = coefficients.len();
                if order == 0 || order != seeds.len() {
                    return Err(Error::InvalidModel("invalid recurrence state"));
                }
                let mut out = Vec::with_capacity(len);
                out.extend_from_slice(&seeds[..len.min(order)]);
                while out.len() < len {
                    let index = out.len();
                    let mut value = 0u8;
                    for (lag, &coefficient) in coefficients.iter().enumerate() {
                        value = value.wrapping_add(coefficient.wrapping_mul(out[index - lag - 1]));
                    }
                    out.push(value);
                }
                out
            }
            Self::PiecewiseLinear { points } => {
                if points.is_empty() {
                    return Err(Error::InvalidModel("empty piecewise model"));
                }
                let mut out = Vec::with_capacity(len);
                let mut right = 1usize;
                for index in 0..len {
                    while right < points.len() && index > points[right].0 as usize {
                        right += 1;
                    }
                    if right == points.len() {
                        out.push(points[points.len() - 1].1);
                        continue;
                    }
                    let (left_pos, left_value) = points[right - 1];
                    let (right_pos, right_value) = points[right];
                    if index == left_pos as usize || right_pos == left_pos {
                        out.push(left_value);
                    } else {
                        let numerator = (i64::from(right_value) - i64::from(left_value))
                            * (index as i64 - i64::from(left_pos));
                        let denominator = i64::from(right_pos - left_pos);
                        out.push((i64::from(left_value) + numerator / denominator) as u8);
                    }
                }
                out
            }
            Self::Run { runs } => {
                let mut out = Vec::with_capacity(len);
                for &(length, value) in runs {
                    let length = varint::usize_from(length)?;
                    let new_len = out
                        .len()
                        .checked_add(length)
                        .ok_or(Error::IntegerOverflow)?;
                    if new_len > len {
                        return Err(Error::InvalidModel("run exceeds prediction length"));
                    }
                    out.resize(new_len, value);
                }
                if out.len() != len {
                    return Err(Error::InvalidModel("run prediction length mismatch"));
                }
                out
            }
            Self::Sparse {
                default,
                exceptions,
            } => {
                let mut out = vec![*default; len];
                for &(position, value) in exceptions {
                    let position = varint::usize_from(position)?;
                    let slot = out
                        .get_mut(position)
                        .ok_or(Error::InvalidModel("sparse position out of bounds"))?;
                    *slot = value;
                }
                out
            }
            Self::Copy { distance } => {
                let distance = varint::usize_from(*distance)?;
                if distance < len || distance > absolute_offset || absolute_offset > history.len() {
                    return Err(Error::InvalidModel("copy source is not prior output"));
                }
                let start = absolute_offset - distance;
                let end = start.checked_add(len).ok_or(Error::IntegerOverflow)?;
                history
                    .get(start..end)
                    .ok_or(Error::InvalidModel("copy source out of bounds"))?
                    .to_vec()
            }
        })
    }

    /// Generates residual bytes. Recurrence is intentionally special: after
    /// the stored seeds, its predictor consumes the *actual restored* previous
    /// bytes, matching `x[i] = sum(a[k] * x[i-k]) + r[i] (mod 256)`.
    #[cfg(test)]
    pub(crate) fn make_residual(
        &self,
        actual: &[u8],
        absolute_offset: usize,
        history: &[u8],
        mode: ResidualMode,
    ) -> Result<Vec<u8>> {
        if let Self::Recurrence {
            coefficients,
            seeds,
        } = self
        {
            let order = coefficients.len();
            if order == 0 || order != seeds.len() || actual.len() < order {
                return Err(Error::InvalidModel("invalid recurrence state"));
            }
            let mut out = Vec::with_capacity(actual.len());
            for index in 0..actual.len() {
                let prediction = if index < order {
                    seeds[index]
                } else {
                    coefficients
                        .iter()
                        .enumerate()
                        .fold(0u8, |value, (lag, &coefficient)| {
                            value.wrapping_add(coefficient.wrapping_mul(actual[index - lag - 1]))
                        })
                };
                out.push(combine_inverse(actual[index], prediction, mode));
            }
            Ok(out)
        } else {
            let predicted = self.predict(actual.len(), absolute_offset, history)?;
            Ok(residual::make(actual, &predicted, mode))
        }
    }

    /// Generates both non-Raw residual modes while sharing model prediction.
    ///
    /// Model fitting evaluates AddModulo before XOR for deterministic tie
    /// breaking. Computing the common prediction once preserves those exact
    /// byte streams while avoiding a second Polynomial/Periodic/Recurrence/
    /// Piecewise pass for every candidate edge.
    pub(crate) fn make_residual_pair(
        &self,
        actual: &[u8],
        absolute_offset: usize,
        history: &[u8],
    ) -> Result<(Vec<u8>, Vec<u8>)> {
        let mut add = Vec::with_capacity(actual.len());
        let mut xor = Vec::with_capacity(actual.len());
        if let Self::Recurrence {
            coefficients,
            seeds,
        } = self
        {
            let order = coefficients.len();
            if order == 0 || order != seeds.len() || actual.len() < order {
                return Err(Error::InvalidModel("invalid recurrence state"));
            }
            for index in 0..actual.len() {
                let prediction = if index < order {
                    seeds[index]
                } else {
                    coefficients
                        .iter()
                        .enumerate()
                        .fold(0u8, |value, (lag, &coefficient)| {
                            value.wrapping_add(coefficient.wrapping_mul(actual[index - lag - 1]))
                        })
                };
                add.push(actual[index].wrapping_sub(prediction));
                xor.push(actual[index] ^ prediction);
            }
        } else {
            let predicted = self.predict(actual.len(), absolute_offset, history)?;
            for (&value, prediction) in actual.iter().zip(predicted) {
                add.push(value.wrapping_sub(prediction));
                xor.push(value ^ prediction);
            }
        }
        Ok((add, xor))
    }

    /// Applies residual bytes. Recurrence feedback uses bytes reconstructed
    /// earlier in this segment, so decode remains one bounded O(N * order) pass.
    pub(crate) fn restore(
        &self,
        mut residual_bytes: Vec<u8>,
        absolute_offset: usize,
        history: &[u8],
        mode: ResidualMode,
    ) -> Result<Vec<u8>> {
        if let Self::Recurrence {
            coefficients,
            seeds,
        } = self
        {
            let order = coefficients.len();
            if order == 0 || order != seeds.len() || residual_bytes.len() < order {
                return Err(Error::InvalidModel("invalid recurrence state"));
            }
            for index in 0..residual_bytes.len() {
                let residual_value = residual_bytes[index];
                let prediction = if index < order {
                    seeds[index]
                } else {
                    coefficients
                        .iter()
                        .enumerate()
                        .fold(0u8, |value, (lag, &coefficient)| {
                            value.wrapping_add(
                                coefficient.wrapping_mul(residual_bytes[index - lag - 1]),
                            )
                        })
                };
                residual_bytes[index] = combine_forward(prediction, residual_value, mode);
            }
            Ok(residual_bytes)
        } else {
            let predicted = self.predict(residual_bytes.len(), absolute_offset, history)?;
            if predicted.len() != residual_bytes.len() {
                return Err(Error::InvalidResidual(
                    "prediction/residual length mismatch",
                ));
            }
            for (value, prediction) in residual_bytes.iter_mut().zip(predicted) {
                *value = combine_forward(prediction, *value, mode);
            }
            Ok(residual_bytes)
        }
    }
}

#[cfg(test)]
fn combine_inverse(actual: u8, prediction: u8, mode: ResidualMode) -> u8 {
    match mode {
        ResidualMode::AddModulo => actual.wrapping_sub(prediction),
        ResidualMode::Xor => actual ^ prediction,
    }
}

fn combine_forward(prediction: u8, residual: u8, mode: ResidualMode) -> u8 {
    match mode {
        ResidualMode::AddModulo => prediction.wrapping_add(residual),
        ResidualMode::Xor => prediction ^ residual,
    }
}

fn require_exact(params: &[u8], expected: usize) -> Result<()> {
    if params.len() == expected {
        Ok(())
    } else {
        Err(Error::InvalidModel("model parameter length mismatch"))
    }
}

pub(crate) fn candidates(
    data: &[u8],
    absolute_offset: usize,
    transformed: &[u8],
    options: &EncodeOptions,
    copy_sources: &[usize],
) -> Vec<Model> {
    let mut models = Vec::new();
    let enabled = &options.models;
    if enabled.raw {
        models.push(Model::Raw);
    }
    if data.is_empty() {
        return models;
    }

    let default = modal_byte(data);
    if enabled.constant {
        models.push(Model::Constant(default));
    }

    if enabled.affine {
        let first_step = data
            .get(1)
            .copied()
            .unwrap_or(data[0])
            .wrapping_sub(data[0]);
        let modal_step = modal_difference(data);
        models.push(Model::Affine {
            a: first_step,
            b: data[0],
        });
        if modal_step != first_step {
            models.push(Model::Affine {
                a: modal_step,
                b: data[0],
            });
        }
    }

    if enabled.polynomial {
        let degrees: &[usize] = match options.mode {
            Mode::Fast => &[],
            Mode::Balanced => &[2, 3],
            Mode::Max => &[2, 3, 4],
        };
        for &degree in degrees {
            if data.len() > degree {
                models.push(Model::Polynomial {
                    coefficients: forward_coefficients(&data[..=degree]),
                });
            }
        }
    }

    if enabled.periodic && data.len() >= 2 {
        for period in promising_periods(data, options.max_period, options.mode) {
            models.push(Model::Periodic {
                pattern: data[..period].to_vec(),
            });
        }
    }

    if enabled.recurrence {
        for order in [1usize, 2, 4, 8, 16] {
            if order > options.max_recurrence_order || order >= data.len() {
                continue;
            }
            let mut lag_copy = vec![0; order];
            lag_copy[order - 1] = 1;
            models.push(Model::Recurrence {
                coefficients: lag_copy,
                seeds: data[..order].to_vec(),
            });
            if order > 1 {
                models.push(Model::Recurrence {
                    coefficients: vec![1; order],
                    seeds: data[..order].to_vec(),
                });
            }
        }
    }

    if enabled.piecewise_linear && data.len() <= u32::MAX as usize {
        let maximum = options.max_control_points.min(data.len());
        let mut counts = vec![2.min(maximum), maximum];
        if maximum >= 4 {
            counts.push(4);
        }
        counts.sort_unstable();
        counts.dedup();
        for count in counts {
            if count > 0 {
                models.push(Model::PiecewiseLinear {
                    points: uniform_points(data, count),
                });
            }
        }
    }

    if enabled.run {
        let runs = collect_runs(data);
        if runs.len() <= 4096 && runs.len().saturating_mul(2) < data.len().saturating_add(1) {
            models.push(Model::Run { runs });
        }
    }

    if enabled.sparse {
        let exceptions: Vec<_> = data
            .iter()
            .enumerate()
            .filter_map(|(position, &value)| (value != default).then_some((position as u64, value)))
            .collect();
        if exceptions.len() <= 65_536
            && exceptions.len().saturating_mul(3) < data.len().saturating_add(1)
        {
            models.push(Model::Sparse {
                default,
                exceptions,
            });
        }
    }

    if enabled.copy {
        if let Some(distance) = find_copy(data, absolute_offset, transformed, copy_sources) {
            models.push(Model::Copy {
                distance: distance as u64,
            });
        }
    }
    models
}

fn modal_byte(data: &[u8]) -> u8 {
    let mut counts = [0usize; 256];
    for &value in data {
        counts[value as usize] += 1;
    }
    counts
        .iter()
        .enumerate()
        .max_by_key(|&(value, count)| (*count, std::cmp::Reverse(value)))
        .map(|(value, _)| value as u8)
        .unwrap_or(0)
}

fn modal_difference(data: &[u8]) -> u8 {
    let mut counts = [0usize; 256];
    for pair in data.windows(2) {
        counts[pair[1].wrapping_sub(pair[0]) as usize] += 1;
    }
    counts
        .iter()
        .enumerate()
        .max_by_key(|&(value, count)| (*count, std::cmp::Reverse(value)))
        .map(|(value, _)| value as u8)
        .unwrap_or(0)
}

fn forward_coefficients(values: &[u8]) -> Vec<u8> {
    let mut differences = values.to_vec();
    let mut coefficients = Vec::with_capacity(values.len());
    while !differences.is_empty() {
        coefficients.push(differences[0]);
        differences = differences
            .windows(2)
            .map(|pair| pair[1].wrapping_sub(pair[0]))
            .collect();
    }
    coefficients
}

fn promising_periods(data: &[u8], max_period: usize, mode: Mode) -> Vec<usize> {
    let mode_cap = match mode {
        Mode::Fast => 16,
        Mode::Balanced => 64,
        Mode::Max => 256,
    };
    let largest = max_period.min(mode_cap).min(data.len() / 2);
    if largest == 0 {
        return Vec::new();
    }
    let total_comparison_budget: usize = match mode {
        Mode::Fast => 16 * 256,
        Mode::Balanced => 64 * 512,
        // The old per-period budget performed 256 * 2,048 comparisons
        // for every DP edge. Max still examines four times as many periods
        // and retains twice as many candidates as Balanced, but this explicit
        // total cap prevents period probing alone from dominating the search.
        Mode::Max => 65_536,
    };
    let comparisons_per_period = total_comparison_budget.div_ceil(largest).max(1);
    let sample_step = data.len().div_ceil(comparisons_per_period).max(1);
    let candidate_limit = match mode {
        Mode::Fast => 1,
        Mode::Balanced => 3,
        Mode::Max => 6,
    };
    let mut scored = Vec::with_capacity(candidate_limit);
    for period in 1..=largest {
        let mut mismatches = 0usize;
        let mut dominated = false;
        for index in (period..data.len()).step_by(sample_step) {
            mismatches += usize::from(data[index] != data[index % period]);
            if scored.len() == candidate_limit {
                let partial_score = (mismatches.saturating_mul(2).saturating_add(period), period);
                if scored
                    .iter()
                    .max()
                    .map(|&worst| partial_score >= worst)
                    .unwrap_or(false)
                {
                    // The tuple can only increase as more mismatches are
                    // observed, so it cannot enter the exact current top-k.
                    dominated = true;
                    break;
                }
            }
        }
        if dominated {
            continue;
        }
        scored.push((mismatches.saturating_mul(2).saturating_add(period), period));
        scored.sort_unstable();
        scored.truncate(candidate_limit);
    }
    scored.into_iter().map(|(_, period)| period).collect()
}

fn uniform_points(data: &[u8], count: usize) -> Vec<(u32, u8)> {
    if count <= 1 || data.len() == 1 {
        return vec![(0, data[0])];
    }
    (0..count)
        .map(|point| {
            let position = point * (data.len() - 1) / (count - 1);
            (position as u32, data[position])
        })
        .collect()
}

fn collect_runs(data: &[u8]) -> Vec<(u64, u8)> {
    let mut runs = Vec::new();
    let mut cursor = 0;
    while cursor < data.len() {
        let value = data[cursor];
        let mut end = cursor + 1;
        while end < data.len() && data[end] == value {
            end += 1;
        }
        runs.push(((end - cursor) as u64, value));
        cursor = end;
    }
    runs
}

fn find_copy(
    data: &[u8],
    offset: usize,
    transformed: &[u8],
    candidate_starts: &[usize],
) -> Option<usize> {
    if data.len() < 8 || offset < data.len() || offset > transformed.len() {
        return None;
    }
    let latest_start = offset - data.len();
    let earliest_start = latest_start.saturating_sub(1024 * 1024);
    let mut best: Option<(usize, usize)> = None;
    let mut considered = 0usize;
    let eligible_end = candidate_starts.partition_point(|&start| start <= latest_start);
    for &start in candidate_starts[..eligible_end].iter().rev() {
        if start < earliest_start {
            break;
        }
        let source = &transformed[start..start + data.len()];
        let mismatches = source
            .iter()
            .zip(data)
            .filter(|(left, right)| left != right)
            .count();
        if best.map(|(score, _)| mismatches < score).unwrap_or(true) {
            best = Some((mismatches, offset - start));
        }
        considered += 1;
        if mismatches == 0 || considered >= 16 {
            break;
        }
    }
    best.map(|(_, distance)| distance)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn limits() -> DecodeLimits {
        DecodeLimits::default()
    }

    fn parameters_round_trip(model: Model, len: usize, offset: usize, history: &[u8]) {
        let params = model.parameters();
        let decoded = Model::decode(model.kind(), &params, len, offset, &limits()).unwrap();
        assert_eq!(decoded, model);
        assert_eq!(
            decoded.predict(len, offset, history).unwrap(),
            model.predict(len, offset, history).unwrap()
        );
    }

    #[test]
    fn parameter_formats_round_trip() {
        parameters_round_trip(Model::Raw, 5, 0, &[]);
        parameters_round_trip(Model::Constant(7), 5, 0, &[]);
        parameters_round_trip(Model::Affine { a: 3, b: 9 }, 5, 0, &[]);
        parameters_round_trip(
            Model::Polynomial {
                coefficients: vec![1, 2, 3],
            },
            10,
            0,
            &[],
        );
        parameters_round_trip(
            Model::Periodic {
                pattern: vec![1, 2, 3],
            },
            9,
            0,
            &[],
        );
        parameters_round_trip(
            Model::Recurrence {
                coefficients: vec![1, 1],
                seeds: vec![1, 1],
            },
            10,
            0,
            &[],
        );
        parameters_round_trip(
            Model::PiecewiseLinear {
                points: vec![(0, 0), (9, 9)],
            },
            10,
            0,
            &[],
        );
        parameters_round_trip(
            Model::Run {
                runs: vec![(4, 8), (2, 9)],
            },
            6,
            0,
            &[],
        );
        parameters_round_trip(
            Model::Sparse {
                default: 0,
                exceptions: vec![(2, 7), (8, 9)],
            },
            10,
            0,
            &[],
        );
        parameters_round_trip(Model::Copy { distance: 5 }, 5, 5, &[1, 2, 3, 4, 5]);
    }

    #[test]
    fn polynomial_predicts_modulo_forward_differences() {
        let data: Vec<u8> = (0u32..100).map(|i| (3 + 5 * i + 7 * i * i) as u8).collect();
        let model = Model::Polynomial {
            coefficients: forward_coefficients(&data[..3]),
        };
        assert_eq!(model.predict(data.len(), 0, &[]).unwrap(), data);
    }

    #[test]
    fn recurrence_predicts_fibonacci() {
        let mut expected = vec![1u8, 1];
        while expected.len() < 100 {
            let n = expected.len();
            expected.push(expected[n - 1].wrapping_add(expected[n - 2]));
        }
        let model = Model::Recurrence {
            coefficients: vec![1, 1],
            seeds: vec![1, 1],
        };
        assert_eq!(model.predict(100, 0, &[]).unwrap(), expected);
    }

    #[test]
    fn noisy_recurrence_uses_restored_feedback() {
        let actual = vec![1u8, 1, 2, 3, 5, 9, 14, 23, 37];
        let model = Model::Recurrence {
            coefficients: vec![1, 1],
            seeds: vec![1, 1],
        };
        for mode in [ResidualMode::AddModulo, ResidualMode::Xor] {
            let residual = model.make_residual(&actual, 0, &[], mode).unwrap();
            assert_eq!(
                model.restore(residual.clone(), 0, &[], mode).unwrap(),
                actual
            );
            assert_eq!(residual.iter().filter(|&&x| x != 0).count(), 1);
        }
    }

    #[test]
    fn paired_residuals_match_individual_mode_generation() {
        // The recurrence is deliberately noisy after its seeds so this catches
        // any accidental switch from actual-value feedback to predicted-value
        // feedback in the shared generation path.
        let actual = vec![3u8, 5, 8, 13, 21, 35, 55, 89, 144];
        let models = [
            Model::Raw,
            Model::Constant(8),
            Model::Affine { a: 2, b: 3 },
            Model::Polynomial {
                coefficients: vec![3, 2, 1],
            },
            Model::Periodic {
                pattern: vec![3, 5, 8],
            },
            Model::Recurrence {
                coefficients: vec![1, 1],
                seeds: vec![3, 5],
            },
            Model::PiecewiseLinear {
                points: vec![(0, 3), (4, 21), (8, 144)],
            },
            Model::Run {
                runs: actual.iter().map(|&value| (1, value)).collect(),
            },
            Model::Sparse {
                default: 3,
                exceptions: actual
                    .iter()
                    .enumerate()
                    .filter_map(|(index, &value)| (value != 3).then_some((index as u64, value)))
                    .collect(),
            },
        ];
        for model in models {
            let (add, xor) = model.make_residual_pair(&actual, 0, &[]).unwrap();
            assert_eq!(
                add,
                model
                    .make_residual(&actual, 0, &[], ResidualMode::AddModulo)
                    .unwrap()
            );
            assert_eq!(
                xor,
                model
                    .make_residual(&actual, 0, &[], ResidualMode::Xor)
                    .unwrap()
            );
        }

        let history = vec![144u8, 89, 55, 34, 21, 13, 8, 5, 3];
        let copy = Model::Copy {
            distance: history.len() as u64,
        };
        let (add, xor) = copy
            .make_residual_pair(&actual, history.len(), &history)
            .unwrap();
        assert_eq!(
            add,
            copy.make_residual(&actual, history.len(), &history, ResidualMode::AddModulo)
                .unwrap()
        );
        assert_eq!(
            xor,
            copy.make_residual(&actual, history.len(), &history, ResidualMode::Xor)
                .unwrap()
        );
    }

    #[test]
    fn pruned_period_ranking_matches_exhaustive_scoring() {
        fn reference(data: &[u8], max_period: usize, mode: Mode) -> Vec<usize> {
            let mode_cap = match mode {
                Mode::Fast => 16,
                Mode::Balanced => 64,
                Mode::Max => 256,
            };
            let largest = max_period.min(mode_cap).min(data.len() / 2);
            let total_budget: usize = match mode {
                Mode::Fast => 16 * 256,
                Mode::Balanced => 64 * 512,
                Mode::Max => 65_536,
            };
            let sample_step = data
                .len()
                .div_ceil(total_budget.div_ceil(largest).max(1))
                .max(1);
            let mut scored = (1..=largest)
                .map(|period| {
                    let mismatches = (period..data.len())
                        .step_by(sample_step)
                        .filter(|&index| data[index] != data[index % period])
                        .count();
                    (mismatches.saturating_mul(2).saturating_add(period), period)
                })
                .collect::<Vec<_>>();
            scored.sort_unstable();
            scored.truncate(match mode {
                Mode::Fast => 1,
                Mode::Balanced => 3,
                Mode::Max => 6,
            });
            scored.into_iter().map(|(_, period)| period).collect()
        }

        let mut state = 0x243f_6a88u32;
        let random = (0..4096)
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 17;
                state ^= state << 5;
                state as u8
            })
            .collect::<Vec<_>>();
        let inputs = [
            (0..4096)
                .map(|index| (index % 37) as u8)
                .collect::<Vec<_>>(),
            (0usize..4096)
                .map(|index| index.wrapping_mul(17) as u8)
                .collect::<Vec<_>>(),
            random,
        ];
        for mode in [Mode::Fast, Mode::Balanced, Mode::Max] {
            for data in &inputs {
                assert_eq!(
                    promising_periods(data, 256, mode),
                    reference(data, 256, mode)
                );
            }
        }
    }
}
