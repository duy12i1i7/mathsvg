# mathzip-core

`mathzip-core` is the deterministic, lossless codec used by MathZip. It treats
an input as bytes, searches reversible transforms, partitions the transformed
stream, fits integer-only predictors, encodes the residual, and selects
candidates by their actual serialized byte length.

This is a research prototype, not yet a long-term archival format.

## Public API

```rust
use mathzip_core::{
    compress, decompress, inspect, verify, DecodeLimits, EncodeOptions, Mode,
};

let input = b"example";
let options = EncodeOptions::for_mode(Mode::Balanced);
let archive = compress(input, &options)?;
let restored = decompress(&archive, &DecodeLimits::default())?;
assert_eq!(restored, input);
assert!(verify(input, &archive, &DecodeLimits::default())?);
let information = inspect(&archive, &DecodeLimits::default())?;
# Ok::<(), mathzip_core::Error>(())
```

`Mode::{Fast, Balanced, Max}` supplies reproducible search profiles.
`ModelOptions`, `ResidualOptions`, and `TransformOptions` expose family-level
switches for ablation runs. `SegmentationMode` supports fixed, change-point, and
adaptive candidate partitions. `ArchiveInfo` is Serde-serializable and reports
sizes, transforms, distributions, exact storage overheads, segment statistics,
Raw-model and Raw+Raw fallback percentages, decoded-residual entropy, actual
residual bytes, per-segment Raw/Zstd probes, and checksum status.

Non-Raw fitting shares one prediction pass between AddModulo and XOR. Max
retains its wider model/transform/period catalog but uses a bounded
eight-predecessor frontier and a 65,536-comparison period-probe budget per
edge. Its exact top-k early-abort preserves the ranking for that deterministic
probe budget; Max is not specified as a strict superset of Balanced partitions.

## Implemented container components

- V1 transforms: Identity, modulo-256 Delta, previous-byte XOR, packed
  LSB-first BitPlane ID 3, and Stride transpose for
  2/3/4/8/16/32/64. Their registry and pinned v1 golden archive remain
  compatible.
- V2 transform: padded BitPlane ID 5 stores eight byte-aligned, LSB-first
  planes of `ceil(original_size / 8)` bytes each. Unused high bits in each
  final plane byte are canonical zeroes. Each plane is segmented, fitted, and
  optimized independently; v2 currently requires ID 5 as its sole transform.
- Models: Raw, Constant, Affine, degree-2/3/4 Polynomial in the Newton
  forward-difference basis, Periodic, restored-feedback linear Recurrence,
  PiecewiseLinear, Run, Sparse exceptions, and non-overlapping prior Copy.
- Residuals: modulo addition or XOR combined with Raw, RLE, ZeroRun, Sparse,
  unsigned BitPack, or an explicitly enabled Zstandard level-3 hybrid.
- Selection: fixed/change-point/adaptive segmentation plus opt-in bounded-depth
  recursive segmentation, all using exact segment descriptor + parameter +
  residual size. Change-point extraction covers byte entropy, histogram
  divergence, mean/variance, lag-one autocorrelation, periodicity, an RLE/Raw
  compression probe, bit density, and prediction-residual regularity using
  deterministic integer/Q8 arithmetic. Recursive topology is encoder-only and
  v1 stores flat leaves.
- Integrity: CRC-32 for the fixed header and every transformed segment,
  SHA-256 for the complete pre-footer archive, and SHA-256 for original bytes.

The one-segment raw fallback is selected using the complete archive size. A
non-empty raw archive is exactly the input size plus 156 bytes in version 1; an
empty archive is 120 bytes. Raw fallback always remains a version-1 Identity
archive, even when the search also evaluates the v2 candidate.

`TransformOptions::bit_plane` controls packed ID 3 and
`TransformOptions::bit_plane_independent` controls padded ID 5. Balanced and
Max enable both by default; Fast disables both. Enabling the v2 candidate is a
search choice, not a compression-performance claim.

Zstandard residual is disabled in `Mode::{Fast, Balanced, Max}` and must be
enabled through `ResidualOptions` or an explicit CLI/config ablation. Fixed
segmentation connects consecutive fixed anchors even when a block is smaller
than a profile's adaptive minimum. Copy fitting uses a deterministic four-byte
index over candidate boundaries, a 1 MiB backward window, and at most 16
eligible source candidates.

Recursive search applies `min_segment_size`/`max_segment_size` strictly, except
that an entire input shorter than the minimum remains one valid searched leaf.
The independent whole-file Raw fallback remains exempt from segmentation
bounds. `max_tree_depth` is bounded to 16, hence at most 65,536 searched leaves.
The complete Recursive candidate set, including endpoints and its feasible
backbone, is capped at 262,144 positions; the persistent DP frontier has a
2,000,000-state hard cap.

## Default decoder limits

- Archive: 256 MiB
- Reconstructed output: 128 MiB
- Segments: 65,536
- Transform descriptors: 16
- Total model parameters: 64 MiB
- Total residual payload: 16 GiB
- Period: 1,048,576 bytes
- Recurrence order: 16
- Piecewise control points and Run/Sparse model entries: 65,536

All archive lengths are checked before allocation and additionally remain
limited by the platform's `usize`. Applications may lower these values for
untrusted workloads, or explicitly raise them for a trusted, checksummed corpus.
Aggregate reverse-transform work is also capped at eight times the configured
output-byte limit (using the permitted rounded-up transformed size for v2),
with either BitPlane representation charged eight units per transformed byte.
Aggregate model work has a separate eight-times-transformed-size cap;
Polynomial and Recurrence are charged by coefficient/order. The encoder never
emits Recurrence order above eight, while the bounded decoder can accept order
16 when the aggregate budget allows it.

## Current limitations

- The encoder emits one transform descriptor. V1 decoding supports a bounded
  length-preserving chain; v2 deliberately accepts only sole ID 5.
- No streaming or random-access decode; reconstructed output is held in memory.
- No Huffman, arithmetic/rANS, Rice/Golomb, harmonic model, bit-plane LFSR, or
  overlapping LZ copy in either supported version.
- Copy and mathematical search are intentionally bounded heuristics and cannot
  find a shortest description in general.
- `inspect` fully reconstructs the archive to report successful segment and
  original checksums; checksum failure is returned as an error.
- No compression-ratio or speed claim is made without external benchmark data.
