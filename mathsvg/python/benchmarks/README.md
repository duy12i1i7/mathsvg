# MathSVG benchmark runner

`runner.py` executes only development or validation manifests. It rejects any
manifest containing a holdout row. Validation support exists for the
post-development validation phase; validation data must not be used to tune
the engine.

Frozen baseline adapters are single-thread (`threads=1`). The native CLI
supports a repeatable runner matrix via `--native-threads`; the default remains
one thread. For example, the required local determinism/performance matrix can
select `1`, `2`, `4`, `8` and affinity-aware `all`:

```text
--native-threads 1 --native-threads 2 --native-threads 4 \
--native-threads 8 --native-threads all
```

The effective count for `all` is recorded in the codec identity and trial row.
Every native invocation also receives finite workload-specific limits:
input/output are bounded by manifest bytes plus the seven-byte maximum
bit-plane padding, and archive reads by
`2 * (original_bytes + 7) + 1 MiB`, with checked unsigned-64-bit arithmetic.
This allows frozen large workloads such as enwik9 without silently using an
unbounded CLI limit.

Native ablations are selected from `configs/ablation/catalog.toml` with a
repeatable `--native-ablation ID`; omission records the `none` baseline. Each
ID becomes a separate codec configuration. Its canonical disable set is passed
to both `mathsvg profile` and `mathsvg compress`, and is retained in the
runtime-profile hash, effective configuration hash, argv, config ID and run
metadata. Only providers with active built-in candidate paths are catalogued.

Before scheduling any native trial, the runner queries
`mathsvg profile --profile NAME`, validates the operational fields mirrored by
the checked-in TOML (format versions, block/microblock sizes, candidate/work
budgets, exact implemented function/coordinate catalogues, emission gates and
deterministic parallel defaults), and hashes the canonical runtime JSON into
the effective `config_sha256`. The runtime contract and its own hash are
retained in run metadata; a missing or mismatched introspection result makes
that native cell unavailable. TOML RSS values remain benchmark targets, not a
claim that the runtime enforces those resident-set limits.

Build the native release binary first:

```text
cargo build --release -p mathsvg-cli
```

A development run with one native profile and two frozen baselines is:

```text
python3 -m mathsvg.python.benchmarks.runner \
  --repository . \
  --experiment-id dev-absolute-001 \
  --machine-id local-x86-64 \
  --manifest mathsvg/results/manifests/development.csv \
  --split development \
  --mathsvg-binary target/release/mathsvg \
  --native-profile fast \
  --baseline raw \
  --baseline zstd-default \
  --output mathsvg/results/raw/benchmark.jsonl \
  --run-metadata mathsvg/results/raw/benchmark-run.json
```

The default is ten measured repetitions plus one warm-up. A caller may request
five repetitions only when it declares an expected delta of at least 2%:

```text
--expected-delta-fraction 0.02 --repetitions 5
```

For deltas below 2%, fewer than ten repetitions are rejected. Trials are
randomized and interleaved by the deterministic scheduler. Each invocation
uses a fresh temporary directory, creates an actual archive, decompresses it,
and verifies restored size and SHA-256.

Baseline commands come from a closed argv adapter catalogue. The TOML command
templates are never executed and `shell=True` is never used. Missing binaries,
unfrozen baselines, unsupported domain adapters, failures and timeouts remain
explicit JSONL rows.

The ZPAQ adapter freezes method 5 and one worker, strips file attributes, and
renames the sole archived member to `payload.bin`; extraction maps that member
directly to the runner's verified output path. ZPAQ 7.15 records an update
timestamp, so repeated archives can legitimately have different SHA-256
values. The runner retains `deterministic_archive=false` for those baseline
rows instead of normalising or hiding the native archive bytes.

Linux measurements use the pinned `/usr/bin/time` executable:

- wall time: `time.monotonic_ns` around the wrapper;
- CPU time: GNU time `%U + %S`;
- peak RSS: GNU time `%M * 1024`.

The run metadata records the measurement executable hash and its limitations.
In particular, `%M` is not a sum of concurrently resident descendants,
filesystem caches are not dropped, and energy/hardware counters remain
unmeasured. It also records Git identity before and after all trials;
`source_stable_during_run=false` makes the run development-only.
The comparison covers the complete worktree except the runner's two declared
raw/metadata output files. Those files are excluded because writing them is
the experiment itself; every other source, configuration, manifest or
unrelated artifact change still changes the recorded status digest.

Native successful rows require `inspect --verify` to expose an exact
`procedural_breakdown` object containing the disjoint wire categories and
reconstructed source coverage. The current CLI exposes that analyzer,
including exact literal-only and pre-entropy counterfactuals. An older or
incompatible CLI that omits the breakdown is retained as an explicit
failed-evidence row; the runner never invents a byte category.

The wire partition is container overhead, function graph, coordinates, shared
definitions, references, parameters, residual layers, literal leaves and
entropy metadata. It must sum exactly to physical archive bytes. Literal-only
and pre-entropy deltas are represented as separate gain and penalty fields, so
an adverse counterfactual is not hidden by a saturating subtraction.

Search elapsed time is not reconstructed from an archive. Unless a separately
measured search timer is supplied in future, time-per-saved-byte uses the
measured compression wall time and labels that basis explicitly.
Coordinate/DAG/residual/symbolic saved-byte counters are optional exact
counterfactuals: absent ablation evidence stays blank and is never defaulted to
zero.

Summarize a completed JSONL file with:

```text
python3 -m mathsvg.python.benchmarks.summarize \
  mathsvg/results/raw/benchmark.jsonl \
  --benchmark-output mathsvg/results/summary/benchmark.csv \
  --procedural-output \
    mathsvg/results/summary/procedural-breakdown.csv
```

The main CSV contains per-file and corpus rows, mean/median/sample deviation,
t-based 95% intervals, failure counts and deterministic seeded corpus
bootstrap intervals. Within each corpus repetition, archive bytes, time and
measured energy are additive across files, while peak RSS is the maximum file
peak rather than a pooled median. The procedural CSV is emitted even when no
native row qualifies; in that case it contains only its canonical header or
explicit failed rows rather than fabricated metrics.

The summarizer compares observed size, wall-time and RSS medians. If any
cross-codec delta is below 2%, a five-repetition row is marked
`confidence_status=inconclusive`; ten measured repetitions are required before
that cell becomes protocol-complete.

Procedural coverage is computed from reconstructed source coverage,
`function_reconstructed_bytes / original_bytes`, equivalently
`1 - literal_reconstructed_bytes / original_bytes`. Compressed
`literal_leaf_bytes` remains a separate wire-size metric and is not confused
with source-byte coverage.

Generate the canonical paired native-ablation artifact only after the runner
has finalized both files:

```text
python3 -m mathsvg.python.analysis.ablation_pipeline \
  mathsvg/results/raw/benchmark.jsonl \
  --run-metadata mathsvg/results/raw/benchmark-run.json \
  --output mathsvg/results/ablation/ablation.csv
```

The pipeline reads run metadata first and refuses to open a still-running
JSONL. A completed run must match the metadata byte count, SHA-256, trial
count, status counts, environment, dataset identities, and exact native
configuration identities. The mapping from configuration to `ablation_id` and
`disabled_algorithms` comes solely from metadata; codec-config names are never
parsed for meaning.

Every output row compares `none` with one catalogued ablation inside the same
experiment, machine, source, measurement method, profile, thread count,
dataset, repetition, and randomized schedule block. Delta direction is always
`ablated_minus_enabled`. Per-file confidence intervals are paired
repetition-level Student-t intervals. Corpus rows sum archive bytes and wall
times, take maximum sequential-workload RSS, and additionally report a
deterministic paired-file bootstrap interval. Missing, failed, timed-out,
unavailable, schedule-mismatched, round-trip-incomplete, nondeterministic, and
under-replicated evidence remains explicit and cannot produce a confirmed
decision.
