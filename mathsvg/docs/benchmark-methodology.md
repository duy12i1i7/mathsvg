# Benchmark methodology

## 1. Frozen experiment identity

Every publishable run freezes:

- clean `mathsvg-absolute` source commit and source identity;
- DSL/container version, primitive catalogue and normative source semantics;
- profile budget, block sizes and tie-break rules;
- the exact development, validation and sealed-holdout manifests;
- baseline versions, build flags, commands and executable hashes;
- ablation catalogue;
- release binary, compiler, runtime-profile and machine identities;
- governing specification and all checked-in MathSVG Markdown documentation.

Native MathSVG rows must set `native_mathsvg=true`; baseline output may never
be stored inside a native archive.

`python/benchmarks/freeze.py --state publishable` is deliberately a closed
protocol. It accepts exactly these configuration files:

```text
configs/{fast,balanced,max,structured,repository}/profile.toml
configs/baselines.toml
configs/ablation/catalog.toml
```

and exactly these dataset manifests:

```text
results/manifests/development.csv
results/manifests/validation.csv
results/manifests/holdout.csv
```

For `--state publishable`, every accepted configuration TOML must also declare
the top-level field `status = "frozen"`. A byte-stable file that still says
`development` is intentionally rejected; freezing is an explicit protocol
transition, not an inference made from a hash.

Paths above are under `mathsvg/`. Duplicate, missing, additional, symlinked or
out-of-repository inputs are rejected. Dataset IDs and split groups must remain
isolated across all three manifests. File SHA-256 and canonical metadata
SHA-256 are both stored, along with aggregate manifest/configuration
identities.

The fixed
`mathsvg/results/manifests/baseline-inventory.json` must identify the selected
baseline catalogue exactly. Its canonical hash, machine identity, complete
baseline-ID set, versions and executable hashes are pinned. Every executable
marked available is re-hashed at freeze time; explicitly unavailable catalogue
rows require a non-empty reason and remain visible rather than disappearing.

Each of the five release-binary `profile` contracts is queried as strict JSON.
The freeze checks its block/microblock and candidate/work budgets, entropy
parser baseline/add-only policy, chain depth, lazy flag, scratch/work/walk
bounds, unchanged opcode/decoder declaration, implemented
function/coordinate catalogues, emission gates and deterministic thread
default against the corresponding TOML. It additionally hashes the complete
runtime contracts, DSL opcode source, container limits, CLI runtime catalogue,
tie-break implementation, `Cargo.lock`, the governing `YeuCau.md`,
`mathsvg/README.md`, and every Markdown file under `mathsvg/docs/`.

The freeze module opens only the three manifest CSVs. It never resolves,
stats, hashes or opens a dataset path named by any manifest, including sealed
holdout payloads. Payload integrity is a separate post-freeze benchmark
operation.

A publishable invocation, run only after building the final release binary and
committing a clean tree, is:

```bash
python3 -m mathsvg.python.benchmarks.freeze \
  --root "$PWD" \
  --experiment-id absolute-v1 \
  --state publishable \
  --manifest mathsvg/results/manifests/development.csv \
  --manifest mathsvg/results/manifests/validation.csv \
  --manifest mathsvg/results/manifests/holdout.csv \
  --config mathsvg/configs/fast/profile.toml \
  --config mathsvg/configs/balanced/profile.toml \
  --config mathsvg/configs/max/profile.toml \
  --config mathsvg/configs/structured/profile.toml \
  --config mathsvg/configs/repository/profile.toml \
  --config mathsvg/configs/baselines.toml \
  --config mathsvg/configs/ablation/catalog.toml \
  --release-binary target/release/mathsvg \
  --compiler "$(rustup which rustc)" \
  --machine-id primary-x86-64 \
  --output mathsvg/results/freezes/absolute-v1.json
```

Any code, documentation, manifest, catalogue, runtime contract, toolchain,
machine or executable change after this record requires a new experiment ID
before opening holdout results.

## 2. Dataset policy

Datasets are assigned before tuning to `development`, `validation` and
`holdout`. A file already measured by the predecessor Full experiment is
labelled legacy-observed and cannot become a clean MathSVG holdout.

The main aggregate requires at least 70% non-synthetic files and 70%
non-synthetic original bytes. File and concatenated views are separate
workloads and are never presented as independent natural samples without a
disclosure.

Holdout manifests, configuration and commit are frozen before content metrics
are inspected. Post-holdout changes create a new experiment ID.

## 3. Timing protocol

- one or more warm-ups;
- at least five measured repetitions;
- ten repetitions when a claimed delta is below 2%;
- deterministic blocked randomisation and interleaving of codec order;
- fresh output paths and real archives;
- SHA-256 round trip after every measured repetition;
- median, mean, standard deviation and 95% interval;
- paired/bootstrap interval for corpus aggregates;
- wall time, CPU time and peak process-tree RSS;
- energy and hardware counters only when a calibrated facility is available;
- single-thread and supported multi-thread rows;
- two machines, including x86-64 and ARM64 when available.

The randomised order seed is stored, but archive generation itself remains
non-stochastic.

## 4. Statistics

Per-row reports retain all trials. A row does not become zero or disappear
when it fails. Size comparisons use exact archive bytes. Time claims use
paired trial or blocked-run statistics with an explicit confidence state:
`confirmed`, `inconclusive` or `not-measured`.

For repeated values \(x_i\), the report emits:

\[
\bar x,\quad \operatorname{median}(x),\quad
s=\sqrt{\frac{\sum_i(x_i-\bar x)^2}{n-1}}
\]

and a t-based 95% interval for the mean. Corpus aggregate intervals use a
deterministic seeded paired bootstrap over files; the seed and replicate count
are recorded.

## 5. Required measurements

- original/compressed bytes, ratio and bits/byte;
- compression/decompression wall and CPU time;
- peak RSS and optional energy/counters;
- container overhead, function graph, coordinate, shared-definition,
  reference, parameter, residual, literal and entropy-metadata bytes, as a
  disjoint partition that sums exactly to the archive;
- node/shared-node counts, residual depth and procedural/literal coverage;
- exact literal-only and pre-entropy counterfactuals, with separate gain and
  penalty fields;
- optional exact coordinate/DAG/residual/symbolic ablation savings;
- compression wall time per procedurally saved byte, explicitly labelled as
  the timing basis until a separately measured search timer exists;
- archive/restored hashes and deterministic archive status;
- failure, timeout, unavailable and confidence status.

## 6. Dominance

Pareto dominance is evaluated per file, corpus and frozen configuration over
size, compression time, decompression time and memory. Energy and random
access join the vector only where both sides have valid measurements.

No prose claim of victory is valid without the corresponding row in:

```text
results/dominance/per-file.csv
results/dominance/per-corpus.csv
results/dominance/pareto-envelope.csv
results/dominance/failures.csv
```

## 7. Current environmental gaps

The initial workspace exposes one x86-64 machine. ARM64, a second independent
machine, CPU-governor control, calibrated energy and some frozen/domain
baseline executables are not yet available. Local development evidence is
valid, but final gates requiring those cells remain open rather than inferred.

## 8. Executable development/validation protocol

`python/benchmarks/runner.py` implements this protocol for development and
validation manifests. It rejects every manifest containing a holdout row,
validates payload size/SHA before scheduling, uses a closed argv adapter
catalogue without a shell, and retains unavailable, failed and timed-out cells
in append-and-fsync JSONL.

The runner defaults to ten measured repetitions plus a warm-up. Five
repetitions are permitted only with a declared expected delta of at least 2%.
The summarizer independently marks observed cross-codec deltas below 2% as
inconclusive until ten repetitions exist.

Linux process measurements use a hashed GNU `/usr/bin/time`: monotonic wall
time around the wrapper, `%U + %S` CPU time and `%M * 1024` peak RSS. The run
metadata records that `%M` is not a sum of concurrent descendants, filesystem
caches and the CPU governor are not controlled, and energy/counters are absent
unless a later calibrated backend supplies them.

The runner records the complete Git identity both before scheduling and after
the final trial. `source_stable_during_run` is true only when those records are
identical. A changed identity is retained as an explicit measurement
limitation; such a run can guide development but cannot become publishable
evidence.

Every successful native row must carry a disjoint exact wire partition that
sums to the physical archive plus exact structural/output attribution.
Counterfactual literal-only, pre-entropy and per-algorithm saved-byte fields
remain nullable. Missing evidence makes the cell incomplete; it is never
replaced by zero.

Native thread selectors are explicit experiment dimensions; repeated
`--native-threads` arguments produce the 1/2/4/8/all matrix while recording
the effective affinity-aware count for `all`. Native input/output limits equal
the manifest size plus the seven-byte maximum bit-plane padding, and the finite
archive-read limit is `2 * (original_bytes + 7) + 1 MiB` with checked u64
arithmetic. Thus files above the CLI defaults are measured without replacing a
safety bound with infinity.

The checked-in profile TOML is not assumed to describe compiled behaviour.
Before scheduling, the runner queries `mathsvg profile --profile NAME`,
validates the operational mirror fields, canonicalizes the returned JSON and
includes its SHA-256 in the effective configuration identity. Both the
contract and hash remain in run metadata; unavailable or mismatched
introspection prevents the native cell from being reported as measured.
Operational mirrors include block/microblock and candidate/work budgets,
the entropy parser policy/depth/lazy/scratch/work/walk contract, fixed opcode
`0x07` and unchanged decoder semantics, implemented function/coordinate
catalogues, emission gates and deterministic parallel defaults. Profile RSS
entries are benchmark targets rather than runtime-enforcement claims.

Paired native ablations come only from the checked-in ablation catalogue. The
same canonical disabled-provider set is supplied to profile introspection and
compression, then frozen into the runtime/config hashes, argv and codec config
ID. A disabled engine must be absent from the optimizer ledger. Engines already
off behind oracle gates are not admitted as disable-only ablations.

`python/analysis/ablation_pipeline.py` creates the single canonical
`results/ablation/ablation.csv`. It resolves `none` and each counterfactual
through run metadata rather than interpreting config names, then pairs only
identical environment/profile/thread/dataset/repetition cells from the same
randomized schedule block. All deltas are `ablated - enabled`: a positive
archive-byte delta is evidence that the enabled algorithm saved bytes.

The CSV contains both per-file and corpus rows. It retains baseline and
ablated measurements, paired delta intervals, status counts, provenance,
round-trip/determinism/protocol state and conservative decision evidence.
Corpus size and wall time are summed within repetition; corpus RSS is the
maximum file RSS. Student-t paired-repetition intervals are accompanied by a
deterministic paired-file bootstrap interval on corpus rows. A missing
baseline or counterfactual, failure, timeout, unavailable cell, schedule
mismatch, nondeterministic archive, or insufficient repetition count remains
`inconclusive`/`not-measured`; it is never discarded or converted to zero.

`python/benchmarks/summarize.py` writes the required:

```text
results/summary/benchmark.csv
results/summary/procedural-breakdown.csv
```

under the `mathsvg/` project root. It emits file and corpus rows, descriptive
t-intervals, deterministic file-bootstrap corpus intervals, explicit status
counts and the basis used for time per saved byte. Corpus archive bytes, time
and energy are summed within each repetition; corpus peak RSS is the maximum
per-file peak within that repetition.
