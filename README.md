# MathSVG

MathSVG nghiên cứu cách biểu diễn luồng byte bằng đồ thị hàm thủ tục/toán học có giới hạn, tương tự cách ảnh vector lưu mô tả hình học thay vì từng điểm ảnh, kèm phần dư chính xác để luôn khôi phục lossless.

Mã nguồn và tài liệu của hướng nghiên cứu hiện tại nằm tại [mathsvg/](mathsvg/README.md). Phần MathZip bên dưới được giữ nguyên làm baseline đối chiếu.

---

# MathZip

MathZip is a deterministic, lossless research codec for arbitrary byte streams.
It asks whether parts of a file can be represented compactly as a mathematical
model plus an exactly encoded residual. It does **not** assume that every file is
compressible: a raw representation is always available and participates in the
same encoded-size comparison as every model.

> MathZip is a research prototype and a file-format experiment. It is not a
> replacement for Zstandard, XZ, or other production compressors. No general
> compression advantage is claimed without reproducible benchmark evidence.

## Core idea

For input bytes \(X\), the encoder considers a reversible transform \(Y=T(X)\),
partitions \(Y\) into segments, and predicts each segment with a deterministic
model \(f_j(i;\theta_j)\). The exact residual is

\[
r_i=(y_i-\hat y_i)\bmod 256,\qquad
\hat y_i=f_j(i;\theta_j)\bmod 256.
\]

The decoder reconstructs

\[
y_i=(\hat y_i+r_i)\bmod 256
\]

and applies \(T^{-1}\). Candidate representations are compared using the actual
serialized byte length of model parameters, residual data, and partition
metadata—not prediction error alone:

\[
C=L(\text{header})+L(T)+L(\text{partition})+
\sum_j\bigl[L(\text{model}_j)+L(\theta_j)+L(R_j)\bigr].
\]

The archive stores the original SHA-256 digest, and decompression succeeds only
when the reconstructed bytes match it.

## Architecture

```text
bytes -> analyse -> reversible transforms -> candidate boundaries
      -> deterministic models + exact residuals -> residual coders
      -> encoded-size optimization -> versioned .mz container
```

The decoder follows the inverse path and is deliberately simpler than the
encoder. Rust implements the codec, parser, and CLI. Python implements corpus
generation, dataset acquisition, benchmark orchestration, verification, plots,
and report generation.

The repository is organized as follows:

```text
crates/mathzip-core   codec, transforms, models, optimizer, container
crates/mathzip-cli    `mathzip` command-line program
crates/mathzip-bench  small Rust-side validation/measurement helper
python/               benchmark and reporting tools
configs/              quick, full, and ablation profiles
synthetic/            deterministic generated-corpus entry points
datasets/             manifests and acquisition documentation
docs/                 format, method, benchmark, and research report
scripts/              reproducible convenience commands
```

## Build

Rust 1.85 or newer and Python 3.10 or newer are required.

```bash
cargo build --release
cargo test --workspace
```

The binary is `target/release/mathzip`. A reproducible container build is also
provided:

```bash
docker build -t mathzip .
docker run --rm -v "$PWD:/work" mathzip --help
```

## Compress, inspect, restore, and verify

```bash
./target/release/mathzip c --mode balanced input.bin output.mz
./target/release/mathzip inspect output.mz
./target/release/mathzip d output.mz restored.bin
./target/release/mathzip verify input.bin output.mz
sha256sum input.bin restored.bin
```

Long command names (`compress` and `decompress`) are equivalent to `c` and `d`.
`fast`, `balanced`, and `max` progressively widen model and partition search.
For standard Balanced/Max inputs of at least 1 MiB, the encoder keeps Adaptive
segmentation but uses a deterministic screened portfolio: coarser regular
anchors, bounded transform/model-mode finalists, exact residual encoding for
every retained finalist, and an exact whole-file Raw comparison. Identity and
the independently optimized padded Bit-plane transform are always retained.
Large-input Max additionally evaluates the complete Balanced screened
frontier, so its selected archive cannot be larger than Balanced's. This is
transparent approximate pruning rather than an exhaustive proof over every
enabled candidate; the metrics sidecar records the requested mode, effective
segmentation/anchor, finalist limits, and selected frontier.
`--exhaustive-search` is the explicit opt-out for diagnostics; it can be
orders of magnitude slower and Full intentionally does not use it.

The archive remains self-contained and decompression never needs Python, a
network connection, AI, or an external model. The five custom residual coders
are enabled by normal profiles. Zstandard residual ID 5 is an explicit
level-3 hybrid for ablation (`--residual-coders zstd`) and is off by default.

Recursive binary segmentation is an explicit encoder search mode; version 1
stores its selected leaves as the same flat segment list, so the archive format
and decoder do not change:

```bash
./target/release/mathzip c --segmentation recursive \
  --max-tree-depth 12 input.bin output.mz
```

Balanced and Max also evaluate two distinct bit-plane representations:

- `bit-plane-packed` is transform ID 3 in v1. It is length-preserving, but a
  logical plane boundary can fall inside a byte.
- `bit-plane-independent` is transform ID 5 in v2. It stores eight
  byte-aligned LSB-first planes with canonical zero high padding, and optimizes
  the partition and models of each plane independently.

`--transforms bit-plane` enables both candidates; the two explicit names select
only one. V2 currently permits ID 5 as its sole transform. Archives selected
from legacy transforms—and the whole-file Raw fallback—remain v1, so existing
v1 decoding and its pinned golden vector are preserved. These implementation
semantics do not by themselves imply a compression or speed improvement.

Run `mathzip <command> --help` for safety limits and optional JSON output.
Defaults cap compress input at 512 MiB and untrusted decode/inspect/verify at
256 MiB archive, 128 MiB output, and 65,536 segments. Larger trusted
corpora require explicit flags; the benchmark harness records its overrides.

## Datasets and benchmarks

Generate the deterministic synthetic corpus first:

```bash
python3 python/generate_synthetic.py \
  --output datasets/synthetic \
  --seed 1297748005
```

The default corpus includes a genuine `encrypted` workload: structured
telemetry plaintext encrypted with RFC 8439 ChaCha20-IETF. Its synthetic
key/nonce and derivation are reproducible from the manifest. Use `--force` to
upgrade a corpus created by an older generator version.

Download only the datasets selected by a manifest/profile:

```bash
python3 python/download_datasets.py \
  --config configs/quick.yaml \
  --output datasets/data
```

Downloaded archives are checksum-verified when a manifest checksum is present
and are extracted with path traversal checks. Large corpora are never committed
to the repository.

After building the release binary, run:

```bash
./scripts/benchmark_quick.sh
./scripts/benchmark_enwik8.sh
./scripts/benchmark_silesia.sh
./scripts/benchmark_ablation.sh
./scripts/benchmark_residual_ablation.sh
./scripts/benchmark_git_copy_ablation.sh
./scripts/benchmark_recursive_ablation.sh
./scripts/benchmark_bit_plane_ablation.sh
./scripts/benchmark_segmentation_ablation.sh
./scripts/benchmark_max_timeout_regression.sh
./scripts/benchmark_full.sh
```

or through the CLI:

```bash
mathzip benchmark configs/quick.yaml
```

The harness records the exact command, host/tool versions, warm-up, per-run
timings, median statistics, real output size, peak RSS where available, and a
SHA-256 round-trip result. It also verifies each input against its manifest and
binds the MathZip binary's embedded build revision to the clean source.
Legacy/focused profiles default to an untimed second metrics probe whose
archive hash must match the timed output. Full and the current ablation
profiles select `mathzip_metrics_mode: inline`: one timed MathZip compression
produces both the measured archive and its metrics sidecar, and the result
records that sidecar work as part of compression time. Strict verification
checks the declared protocol and exact command relationship.
Missing baseline programs and timeouts are reported as
failures/skips; they are not silently removed. Raw JSON/CSV goes to
`benchmarks/results/`, and plots go to `benchmarks/plots/`.

The quick profile is intended for development. The full profile can download
hundreds of megabytes or more and can take a long time, particularly in
MathZip's maximum-search mode.

Published Full run `20260726T044843Z-213f07c3` completed the exact grid of
725 inputs (1.468 GB) × 34 codec/thread combinations = 24,650/24,650 aggregate
rows with zero failure and zero resume. It contains 73,950 measured trials
after one warm-up per row, took 129,095.967 seconds (35 h 51 min 35.967 s), and
passes strict result/plot verification with zero warning. The run records
`scientifically_compliant_run=true`, stable source/config/input/binary identity,
and a verified SHA-256 round trip for every trial.

Full pins a 5,400-second per-operation timeout. An exact enwik9 preflight on
the publication host completed all eight high-risk Max/thread cells and
verified every restored SHA-256; Brotli q11 took 3,489.38 seconds, leaving only
110.62 seconds under the former 3,600-second limit. The larger timeout changes
neither the grid nor the warm-up/repetition counts. Preflight measurements are
diagnostic only and are not merged into the Full result document.

Python runs checkpoint every completed input/codec/thread row outside timed
regions. Start normally, or resume one explicit Full run:

```bash
./scripts/benchmark_full.sh
./scripts/benchmark_full.sh \
  --resume benchmarks/results/full/RUN_ID
```

For automation, the equivalent explicit input is:

```bash
MATHZIP_BENCHMARK_RESUME=benchmarks/results/full/RUN_ID \
  ./scripts/benchmark_full.sh
```

The script never scans for or guesses a checkpoint.
In resume mode it is verify-only before entering the runner: it does not
rebuild the binary, regenerate synthetic/auxiliary corpora, or download data,
so a mismatched checkpoint cannot alter the evidence it is about to reject.

Resume fails closed if config, clean source/tree, executable/build-info, input
evidence, execution environment/affinity, host CPU/RAM/OS, or timing/thread
policy changed. Concurrent invocations targeting the same result root are
rejected. `latest.*` is not published until the exact expected row grid is
complete and its JSON/CSV structure validates.

`benchmark_enwik8.sh` is a focused, single-thread Fast-class comparison on the
mandatory 100 MB enwik8 workload. It keeps that evidence separate from the
much larger multi-mode Full experiment.

`benchmark_silesia.sh` is the focused, single-thread Fast-class comparison on
all 12 files named by the Silesia manifest. Like Full, it supports an explicit
fail-closed `--resume RUN_DIRECTORY_OR_CHECKPOINT`; it closes per-file Silesia
coverage without being relabelled or merged into the separate Full experiment.

`benchmark_git_copy_ablation.sh` is the paired single-factor Copy/reference
experiment on generated Git snapshots. Its two MathZip entries differ only by
whether the Copy model is enabled; direct Zstd remains a separate control.

`benchmark_residual_ablation.sh` is the focused single-factor residual
experiment on the published ablation corpus. Its two Balanced entries differ
only between the five custom residual coders and Zstd residual ID 5; direct
Zstd remains a separate control.

`benchmark_ablation.sh` is the complete corrected 20-variant matrix plus direct
Zstd control. Published run `20260725T170925Z-dde3921f` used the current
single-factor definitions for V13/V14/V17, inline MathZip metrics, one warm-up,
three measured repetitions, and completed its exact 12-input × 21-codec grid
(252/252 rows) without failure or warning in 9,327.963 seconds. Its checkpoint,
expected grid, generated report, and 15 PNG plots all pass strict verification.
The older `20260724T032334Z-aca5bed4` artifact remains immutable legacy
evidence: it contains two Max warm-up timeouts and confounded V13/V14/V17
definitions, so its historical bytes remain reportable but those three legacy
variants are not used for single-factor attribution.

`benchmark_recursive_ablation.sh` is the focused segmentation experiment. Its
two MathZip Fast entries keep the model/transform/residual search fixed and
change Adaptive to Recursive depth 4; Zstd Fast remains a separate control.

`benchmark_segmentation_ablation.sh` covers the required Fixed sweep at
256 B, 1/4/16/64/256 KiB and compares 4 KiB ChangePoint and Adaptive variants
under the same MathZip Fast model/transform/residual profile. Zstd Fast remains
a separate control.

`benchmark_max_timeout_regression.sh` replays the two Canterbury inputs whose
historical Max warm-ups reached 600 seconds. It uses the same 600-second
per-operation limit, three measured repetitions, and Zstd Max as a control.
Strict inspection and phase probes are enabled for every measured MathZip
trial, so the artifact carries the same evidence fields as the other
publication profiles. Published run `20260725T055807Z-2e6ce6d4` completed all
4/4 rows without failure on clean source revision
`e6ce84274a45b5a6e4738844921f9b374439c643`: the two current-source MathZip
Max medians were 368.526 s for `kennedy.xls` and 158.502 s for `ptt5`, both
below the unchanged limit.

`benchmark_bit_plane_ablation.sh` compares forced packed ID 3/v1 with forced
independently segmented padded ID 5/v2 under the same Balanced
model/residual/segmentation settings, with direct Zstd as a separate control.
The profile includes odd 31-byte inputs to exercise padding as well as 64 KiB
inputs. Published run `20260724T174812Z-583dbb61` completed all 42/42 rows
without failure. On these 14 inputs the independent representation compressed
about 8.60 times faster than packed, but used 4,330 more bytes (2.916%): it had
0 size wins, 2 ties, and 12 losses. This is focused implementation evidence,
not a general compression claim.

## Reading results

Compression ratio is reported as `original_bytes / compressed_bytes`; values
above one are smaller. Bits per input byte is
`8 * compressed_bytes / original_bytes`. Corpus totals use total original bytes
divided by total compressed bytes (weighted aggregation); per-file arithmetic,
geometric, and median summaries are kept separate.

MathZip inspection keeps Raw-model bytes distinct from the stricter
Raw-model-plus-Raw-residual fallback. Its storage breakdown, decoded-residual
entropy, actual residual payload, and per-segment Raw/Zstd probes are diagnostic
evidence; they do not replace a whole-file baseline comparison.

Generated numbers are evidence only for the recorded commit, tool versions,
machine, configuration, and corpus checksums. See
[`docs/benchmark-methodology.md`](docs/benchmark-methodology.md) and
[`docs/research-report.md`](docs/research-report.md).

## Recorded evidence

The current Quick run uses clean codec revision
`6dbc3860910e8d310cfa8bbeadbf97f0529beff1`; the focused Git Copy run uses
clean revision `163fc78cbadc0fdb5888781080baade7b576811d`; the focused residual
run uses clean revision `a130e30389cad1efe6db3ffad7764022a446531e`; the
focused Recursive run uses clean revision
`aac547e57647b0135c555c0b59a5b0c01db476ea`; the complete Silesia run uses
clean revision `0e3bd26e9883b7b6d6ed51def05b1822efbda6bf`; the focused
Bit-plane run uses clean revision
`1703b73f3ef51753ed1f92ea5e2ce28da9bcdf22`; the Max timeout regression uses
clean revision `e6ce84274a45b5a6e4738844921f9b374439c643`; the corrected
ablation uses clean revision
`e5366fa7c5c1cd5801bb9cc86b19a09592403137`; and the focused enwik8 and
historical ablation runs use the earlier clean codec revision
`235d190a20e64b6ddc5ce002379809ebbe8fe0cd`. Full uses clean revision
`e60f423b5f85d1716eb2356f2460f97ba4227885`, source-tree SHA-256
`55b6a32cc5abedcdcba8989065be83cbaa8600bede102e73c39306bb58153e63`,
and MathZip binary SHA-256
`366ca45932751ecb10d52f4db3f4806b94acd44e3ec9ccbf45e33b45bd82e228`.
Each uses one warm-up, three
measured repetitions, real archive files, and SHA-256 verification for every
successful round trip:

| Profile | Result | Validation |
|---|---|---|
| [Full](benchmarks/results/full/20260726T044843Z-213f07c3/results.json) | 24,650/24,650 rows successful | strict, exact-grid + 15 Full plots, 0 warnings |
| [Quick](benchmarks/results/quick/20260724T134555Z-8b4c06c2/results.json) | 477/477 rows successful | strict, exact-grid, 0 warnings |
| [enwik8](benchmarks/results/enwik8/20260724T031043Z-f6e4fbfe/results.json) | 9/9 rows successful | strict, 0 warnings |
| [Corrected ablation](benchmarks/results/ablation/20260725T170925Z-dde3921f/results.json) | 252/252 rows successful | strict, exact-grid, 0 warnings |
| [Historical ablation](benchmarks/results/ablation/20260724T032334Z-aca5bed4/results.json) | 250/252 rows successful | legacy strict evidence checks, 2 recorded warnings |
| [Git Copy ablation](benchmarks/results/git-copy-ablation/20260724T140810Z-c825d7e1/results.json) | 45/45 rows successful | strict, exact-grid, 0 warnings |
| [Residual ablation](benchmarks/results/residual-ablation/20260724T143401Z-3bb520a6/results.json) | 36/36 rows successful | strict, exact-grid, 0 warnings |
| [Recursive ablation](benchmarks/results/recursive-ablation/20260724T161541Z-70dfeff6/results.json) | 30/30 rows successful | strict, exact-grid, 0 warnings |
| [Silesia](benchmarks/results/silesia/20260724T165449Z-4ef6e508/results.json) | 108/108 rows successful | strict, exact-grid, 0 warnings |
| [Bit-plane ablation](benchmarks/results/bit-plane-ablation/20260724T174812Z-583dbb61/results.json) | 42/42 rows successful | strict, exact-grid, 0 warnings |
| [Segmentation ablation](benchmarks/results/segmentation-ablation/20260725T051723Z-a5f0fdfc/results.json) | 90/90 rows successful | strict, exact-grid, 0 warnings |
| [Max timeout regression](benchmarks/results/max-timeout-regression/20260725T055807Z-2e6ce6d4/results.json) | 4/4 rows successful | strict, exact-grid, 0 warnings |

The Segmentation run uses clean revision
`3c864d915b0d2452da712cd691796a4bdf987876`. Across Full and the eleven
previously listed main/focused artifacts, all 77,979 measured successful trials
restored the recorded input SHA-256. The separately retained historical Quick
snapshot adds 1,404 successful trials, for 79,383 across all 13 tracked result
documents.

The current Quick artifact contains 53 inputs, including a 4 KiB structured
plaintext encrypted with ChaCha20. MathZip stored it in 4,252 bytes (ratio
0.9633; 3.8086% expansion); direct Zstd used 4,110 bytes. The previous immutable
[52-input Quick snapshot](benchmarks/results/quick/20260724T025637Z-8563453d/results.json)
is retained and is not retroactively reinterpreted.

The two historical ablation failures are retained: Max-mode compression
exceeded the 600-second warm-up limit on `kennedy.xls` and `ptt5` at its
historical source revision. The immutable failure rows are not rewritten or
silently replaced by the corrected zero-failure matrix. The focused
current-source regression independently closes those two cases under the same
limit:
MathZip used 430,446 bytes for `kennedy.xls` (ratio 2.392; median 368.526 s)
and 100,522 bytes for `ptt5` (5.106; 158.502 s). Across both inputs it used
530,968 bytes (ratio 2.906; 0.003 MB/s), versus Zstd Max's 113,190 bytes
(13.632; 1.306 MB/s). Thus the timeout regression passes, while Max remains
far slower and larger than the control on this focused workload.

Quick shows MathZip smaller than Zstd/XZ on only 6 of 53 matched files, all
synthetic. On enwik8, MathZip reaches ratio 1.009 versus 2.458 for Zstd and
3.005 for XZ, while using 672.762 MiB peak RSS. These results support a narrow
conclusion: the model catalog recognizes some generated mathematical
structure, but this prototype is not competitive on the measured
general-purpose corpora.

The paired Git experiment holds every MathZip option fixed except Copy. Copy
was selected on only `combined/all_versions.bin`: it reduced the 15 paired
MathZip archives from 90,843 to 90,803 bytes (40 bytes, 0.0440%). The other
14 pairs were byte-size ties, including every individual snapshot file.

The paired residual experiment holds the Balanced model/search pipeline fixed.
Replacing the five custom residual coders with Zstd reduced the 12 MathZip
archives from 1,045,829 to 322,067 bytes (69.2046%). The hybrid was 6,226 bytes
(1.8965%) smaller than direct Zstd on this matrix, but compressed about 2,552
times more slowly. This isolates a strong residual-coder effect; it does not
establish a general-purpose advantage.

The corrected complete ablation independently repeats the intended
single-factor comparisons on one clean revision. V13 custom residual used
1,044,616 bytes; V14 Zstd residual used 324,348 bytes, a 720,268-byte
(68.9505%) reduction. Zstd residual won five files, tied one, and lost six:
the byte-weighted result is dominated by the larger Canterbury inputs, while
custom coders were smaller on six structured synthetic inputs. V17, which only
disables whole-file Raw fallback, used 1,044,652 bytes: 11 files tied V13 and
the incompressible random input cost 36 additional bytes. These are causal
claims only for the pinned 12-input matrix.

On the same corrected artifact, Balanced and V13 produced identical sizes on
12/12 files. Max reduced the byte-weighted total from 1,044,616 to 776,839
bytes (25.6340%) but took about 2.58 times as much aggregate median compression
time; it was smaller on only 3 files, equal on 1, and larger on 8. Balanced is
therefore the more defensible default for this mixed small matrix, while Max is
a size/search trade-off rather than a monotonic per-file improvement. Because
the profiles change several search factors, this Balanced/Max comparison is
descriptive, not single-factor attribution.

The focused Recursive experiment covers eight 64 KiB mathematical/random
inputs and two Canterbury files. Recursive depth 4 reduced the ten paired
MathZip archives from 266,151 to 265,911 bytes: 240 bytes (0.0902%), with
7 wins, 1 tie, and 2 losses. Its aggregate compression throughput was
0.373 MB/s versus 0.708 MB/s for Adaptive, about 1.90 times slower; Zstd Fast
was both smaller (165,733 bytes) and much faster. This is evidence of a small
partition-metadata saving, not a general compression win.

The complete Silesia experiment covers all 12 manifest files and nine
single-thread Fast-class codecs. On 211,938,580 input bytes, MathZip Fast used
187,537,684 bytes (ratio 1.130) at 1.567 MB/s. Zstd Fast used 73,448,492 bytes
(2.886), and XZ Fast used 58,417,824 bytes (3.628). MathZip lost to both on
12/12 files; it was smaller than Raw on 10 files and expanded `sao` and
`x-ray` by 156 bytes each. This closes the required per-file Silesia coverage
while reinforcing the negative general-purpose result.

The focused Bit-plane experiment covers nine 64 KiB and five 31-byte synthetic
inputs. Packed v1 used 148,473 bytes (ratio 3.974) at 0.093 MB/s; independent
v2 used 152,803 bytes (3.861) at 0.800 MB/s; direct Zstd used 81,901 bytes
(7.204) at 8.014 MB/s. Independent v2's excess over packed was 3,043 bytes
(2.062%) on the 64 KiB group but 1,287 bytes (143.159%) on the five tiny
inputs, where fixed per-plane metadata and padding dominate. This demonstrates
the v2 path and its speed/size trade-off on the selected synthetic matrix; it
does not demonstrate a compression gain or generalize to other corpora.

The focused Segmentation experiment covers the six required Fixed sizes plus
ChangePoint and Adaptive on eight 64 KiB synthetic inputs and two Canterbury
files. ChangePoint used 262,012 bytes (ratio 2.727) versus 264,305 bytes
(2.704) for Adaptive and 271,155 bytes (2.635) for Fixed 4 KiB. It beat
Adaptive on 8 inputs, tied 1 and lost 1; it was never larger than the best
Fixed size per input. Zstd Fast remained smaller at 165,733 bytes. This is a
ten-input policy/size sweep, not Full-corpus evidence.

Generated summaries are available for
[Full](benchmarks/results/full/latest-report.md),
[Quick](benchmarks/results/quick/latest-report.md),
[enwik8](benchmarks/results/enwik8/latest-report.md), and
[ablation](benchmarks/results/ablation/latest-report.md), plus the focused
[Git Copy ablation](benchmarks/results/git-copy-ablation/latest-report.md) and
[residual ablation](benchmarks/results/residual-ablation/latest-report.md), and
[Recursive ablation](benchmarks/results/recursive-ablation/latest-report.md),
the complete [Silesia run](benchmarks/results/silesia/latest-report.md), and
the focused [Bit-plane ablation](benchmarks/results/bit-plane-ablation/latest-report.md)
and [Segmentation ablation](benchmarks/results/segmentation-ablation/latest-report.md),
plus the focused
[Max timeout regression](benchmarks/results/max-timeout-regression/latest-report.md).
The Full raw JSON/CSV, durable checkpoint rows, report and plot hash manifest
are published under `benchmarks/results/full/` and `benchmarks/plots/full/`.
On the 1,467,705,562-byte single-thread workload, MathZip
Fast/Balanced/Max ratios are
1.0787/1.2513/1.2572 at 1.7906/0.4289/0.1388 MB/s. Zstd-default reaches
3.2479 at 75.0528 MB/s and XZ-default reaches 4.3610 at 1.3131 MB/s, also at
one thread. Balanced
is smaller than Zstd-default on 125/725 files and larger on 600; against
XZ-default it is 105/0/620. Max saves only 0.4627% weighted bytes over
Balanced while taking 3.090× its aggregate compression time. MathZip therefore
recognizes selected mathematical and generated BMP/WAV structure but is not a
general-purpose competitor to Zstd/XZ on this Full workload.

## Current limits

- Searching transforms, boundaries, models, and residual coders can be much
  slower than a conventional compressor. Max timed out on two 0.5--1.0 MiB
  Canterbury files in the immutable historical ablation; current source
  completes both under 600 seconds in the published focused regression. The
  newer ≥1 MiB screened portfolio substantially bounds larger inputs, but it
  is approximate pruning rather than an exhaustive global-MDL proof. Full
  completed it successfully, but single-thread Max still used 10,574.5
  aggregate seconds versus 19.56 seconds for single-thread Zstd-default.
- A byte stream often has no useful mathematical smoothness. Random, encrypted,
  and already-compressed inputs normally select raw storage and still pay a
  bounded container overhead.
- Model metadata can dominate small segments, so adaptive segmentation has
  conservative minimum sizes and exact serialized-size accounting.
- Byte-aligned per-plane segmentation is implemented in the narrow v2 profile,
  but a bit-level LFSR predictor and harmonic/Fourier predictors remain
  deferred until their integer semantics, parameter layout, and cross-platform
  test vectors are normative.
- The format is versioned but experimental; compatibility is guaranteed only as
  documented in the format specification.
- Dataset downloads and optional third-party compressor executables are not
  bundled. Reproducing Full requires the pinned manifests, all mandatory
  baseline executables, roughly 1.468 GB of verified input, and a long
  separately budgeted run.

## Reproducibility and project status

The exact archive format is in [`docs/format-spec.md`](docs/format-spec.md), the
mathematical method in
[`docs/mathematical-model.md`](docs/mathematical-model.md), and build/experiment
instructions in [`docs/reproducibility.md`](docs/reproducibility.md).
Implemented and deferred features are tracked explicitly in
[`docs/implementation-status.md`](docs/implementation-status.md). Recorded
robustness-smoke commands and coverage are in
[`docs/fuzzing.md`](docs/fuzzing.md).

## License

Source code is released under the MIT License. External corpora retain their
own licenses; consult each manifest and upstream source before redistribution.
