use serde::{Deserialize, Serialize};

/// Encoder search profile.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Fast,
    #[default]
    Balanced,
    Max,
}

/// How candidate segment boundaries are generated.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum SegmentationMode {
    /// Split only on `fixed_segment_size`.
    Fixed,
    /// Select boundaries whose neighbouring block statistics differ.
    ChangePoint,
    /// Use change points plus fixed anchors and dynamic programming.
    #[default]
    Adaptive,
    /// Select a flat leaf partition representable by a bounded-depth binary
    /// segmentation tree. Version 1 stores only the ordered leaves, not tree
    /// topology.
    Recursive,
}

/// Largest recursive segmentation depth accepted by the encoder.
///
/// A depth of 16 permits at most 65,536 leaves, matching the default decoder
/// segment-count limit and keeping the leaf-count state space explicitly
/// bounded.
pub const MAX_RECURSIVE_TREE_DEPTH: u8 = 16;

const DEFAULT_MAX_TREE_DEPTH: u8 = 12;

/// Model-family switches used by ablation experiments.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelOptions {
    pub raw: bool,
    pub constant: bool,
    pub affine: bool,
    pub polynomial: bool,
    pub periodic: bool,
    pub recurrence: bool,
    pub piecewise_linear: bool,
    pub run: bool,
    pub sparse: bool,
    pub copy: bool,
}

impl ModelOptions {
    pub fn raw_only() -> Self {
        Self {
            raw: true,
            constant: false,
            affine: false,
            polynomial: false,
            periodic: false,
            recurrence: false,
            piecewise_linear: false,
            run: false,
            sparse: false,
            copy: false,
        }
    }

    pub(crate) fn any_enabled(&self) -> bool {
        self.raw
            || self.constant
            || self.affine
            || self.polynomial
            || self.periodic
            || self.recurrence
            || self.piecewise_linear
            || self.run
            || self.sparse
            || self.copy
    }
}

impl Default for ModelOptions {
    fn default() -> Self {
        Self {
            raw: true,
            constant: true,
            affine: true,
            polynomial: true,
            periodic: true,
            recurrence: true,
            piecewise_linear: true,
            run: true,
            sparse: true,
            copy: true,
        }
    }
}

/// Residual-coder switches used by ablation experiments. Zstandard is disabled
/// in normal modes so hybrid gains remain separable from custom residual gains.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResidualOptions {
    pub raw: bool,
    pub rle: bool,
    pub zero_run: bool,
    pub sparse: bool,
    pub bit_pack: bool,
    /// Hybrid baseline. Disabled by normal modes so custom residual results
    /// remain separable from the Zstandard contribution.
    pub zstd: bool,
}

impl ResidualOptions {
    pub fn raw_only() -> Self {
        Self {
            raw: true,
            rle: false,
            zero_run: false,
            sparse: false,
            bit_pack: false,
            zstd: false,
        }
    }

    pub(crate) fn any_enabled(&self) -> bool {
        self.raw || self.rle || self.zero_run || self.sparse || self.bit_pack || self.zstd
    }
}

impl Default for ResidualOptions {
    fn default() -> Self {
        Self {
            raw: true,
            rle: true,
            zero_run: true,
            sparse: true,
            bit_pack: true,
            zstd: false,
        }
    }
}

/// Reversible-transform switches used by ablation experiments.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TransformOptions {
    pub identity: bool,
    pub delta: bool,
    pub xor: bool,
    /// Version-1 packed bit-plane transpose. Logical plane boundaries can be
    /// sub-byte when the input length is not a multiple of eight.
    pub bit_plane: bool,
    /// Version-2 byte-aligned bit-plane transpose. Each plane is optimized as
    /// an independent byte stream.
    #[serde(default)]
    pub bit_plane_independent: bool,
    pub stride: bool,
}

impl TransformOptions {
    pub fn identity_only() -> Self {
        Self {
            identity: true,
            delta: false,
            xor: false,
            bit_plane: false,
            bit_plane_independent: false,
            stride: false,
        }
    }

    pub(crate) fn any_enabled(&self) -> bool {
        self.identity
            || self.delta
            || self.xor
            || self.bit_plane
            || self.bit_plane_independent
            || self.stride
    }
}

impl Default for TransformOptions {
    fn default() -> Self {
        Self {
            identity: true,
            delta: true,
            xor: true,
            bit_plane: true,
            bit_plane_independent: true,
            stride: true,
        }
    }
}

/// Deterministic encoder configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EncodeOptions {
    pub mode: Mode,
    pub segmentation: SegmentationMode,
    /// Baseline/fallback chunk size and fixed segmentation size.
    pub fixed_segment_size: usize,
    /// Smallest segment considered by adaptive segmentation.
    pub min_segment_size: usize,
    /// Largest ordinary modelled segment. The raw whole-file fallback is not
    /// subject to this bound.
    pub max_segment_size: usize,
    /// Largest periodic pattern fitted by the encoder.
    pub max_period: usize,
    /// Maximum recurrence order fitted by the encoder.
    pub max_recurrence_order: usize,
    /// Candidate control-point count for piecewise linear predictors.
    pub max_control_points: usize,
    /// Candidate predecessor boundaries considered by adaptive DP.
    pub dp_lookback: usize,
    /// Maximum binary-tree depth used by recursive segmentation. A depth of
    /// zero permits only the root leaf.
    #[serde(default = "default_max_tree_depth")]
    pub max_tree_depth: u8,
    pub models: ModelOptions,
    pub residuals: ResidualOptions,
    pub transforms: TransformOptions,
    /// Compare every searched representation with a one-segment raw archive.
    /// Disabling this is intended only for ablation experiments.
    pub allow_raw_fallback: bool,
    /// Use the deterministic screened portfolio for standard Balanced/Max
    /// inputs at or above the implementation threshold. Disable this only for
    /// an explicitly exhaustive diagnostic/ablation run.
    #[serde(default = "default_large_input_screening")]
    pub large_input_screening: bool,
}

impl EncodeOptions {
    pub fn for_mode(mode: Mode) -> Self {
        match mode {
            Mode::Fast => Self {
                mode,
                segmentation: SegmentationMode::Fixed,
                fixed_segment_size: 16 * 1024,
                min_segment_size: 512,
                max_segment_size: 64 * 1024,
                max_period: 16,
                max_recurrence_order: 2,
                max_control_points: 2,
                dp_lookback: 2,
                max_tree_depth: DEFAULT_MAX_TREE_DEPTH,
                models: ModelOptions {
                    polynomial: false,
                    piecewise_linear: false,
                    run: false,
                    sparse: false,
                    copy: false,
                    ..ModelOptions::default()
                },
                residuals: ResidualOptions::default(),
                transforms: TransformOptions {
                    bit_plane: false,
                    bit_plane_independent: false,
                    stride: false,
                    ..TransformOptions::default()
                },
                allow_raw_fallback: true,
                large_input_screening: true,
            },
            Mode::Balanced => Self {
                mode,
                segmentation: SegmentationMode::Adaptive,
                fixed_segment_size: 4 * 1024,
                min_segment_size: 256,
                max_segment_size: 64 * 1024,
                max_period: 64,
                max_recurrence_order: 4,
                max_control_points: 4,
                dp_lookback: 8,
                max_tree_depth: DEFAULT_MAX_TREE_DEPTH,
                models: ModelOptions::default(),
                residuals: ResidualOptions::default(),
                transforms: TransformOptions::default(),
                allow_raw_fallback: true,
                large_input_screening: true,
            },
            Mode::Max => Self {
                mode,
                segmentation: SegmentationMode::Adaptive,
                fixed_segment_size: 1024,
                min_segment_size: 128,
                max_segment_size: 256 * 1024,
                max_period: 256,
                // Keep encoder output within the default aggregate model-work
                // budget even when recurrence wins every segment.
                max_recurrence_order: 8,
                max_control_points: 8,
                // Max expands the model/transform/period catalog substantially.
                // Keep the predecessor-count budget equal to Balanced so that
                // the denser 1 KiB/change-point boundary set remains bounded
                // on MiB-scale inputs. Max is a broader catalog search, not a
                // strict superset of Balanced partitions.
                dp_lookback: 8,
                max_tree_depth: DEFAULT_MAX_TREE_DEPTH,
                models: ModelOptions::default(),
                residuals: ResidualOptions::default(),
                transforms: TransformOptions::default(),
                allow_raw_fallback: true,
                large_input_screening: true,
            },
        }
    }

    pub(crate) fn validate(&self) -> bool {
        self.fixed_segment_size > 0
            && self.min_segment_size > 0
            && self.max_segment_size >= self.min_segment_size
            && self.max_period > 0
            && self.max_recurrence_order > 0
            && self.max_recurrence_order <= 8
            && self.max_control_points >= 2
            && self.dp_lookback > 0
            && self.max_tree_depth <= MAX_RECURSIVE_TREE_DEPTH
            && self.models.any_enabled()
            && self.residuals.any_enabled()
            && self.transforms.any_enabled()
    }
}

const fn default_max_tree_depth() -> u8 {
    DEFAULT_MAX_TREE_DEPTH
}

const fn default_large_input_screening() -> bool {
    true
}

impl Default for EncodeOptions {
    fn default() -> Self {
        Self::for_mode(Mode::Balanced)
    }
}

/// Hard limits applied before allocating or evaluating attacker-controlled data.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecodeLimits {
    pub max_archive_size: u64,
    pub max_output_size: u64,
    pub max_segments: u32,
    pub max_transforms: u32,
    pub max_model_parameter_bytes: u64,
    pub max_residual_bytes: u64,
    pub max_period: u32,
    pub max_recurrence_order: u32,
    pub max_control_points: u32,
}

impl Default for DecodeLimits {
    fn default() -> Self {
        Self {
            // Decoding can transiently hold the archive, transformed bytes,
            // predictor output, residuals, and restored bytes. Conservative
            // defaults keep a tiny authenticated archive from requesting many
            // gigabytes. Trusted larger files remain supported through
            // caller-selected limits.
            max_archive_size: 256 * 1024 * 1024,
            max_output_size: 128 * 1024 * 1024,
            max_segments: 65_536,
            max_transforms: 16,
            max_model_parameter_bytes: 64 * 1024 * 1024,
            max_residual_bytes: 16 * 1024 * 1024 * 1024,
            max_period: 1_048_576,
            max_recurrence_order: 16,
            max_control_points: 65_536,
        }
    }
}
