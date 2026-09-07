# Verification campaigns

These runners add independent, finite evidence for the acceptance requirements
that are not satisfied by ordinary unit tests. Both runners reject holdout
execution and pin the source, binary, toolchain, inputs, limits, commands and
logs used by a campaign.

## Sanitizer-backed fuzzing

`fuzz_campaign` copies checked-in MathSVG seeds to temporary storage, runs the
three native targets under cargo-fuzz's AddressSanitizer backend, and records
the exact run count and corpus/artifact identities. The checked-in seed corpus
is never mutated. Before `mathsvg_decode`, the runner also creates and verifies
small constant, linear and periodic native archives with the pinned release
binary. These checksum-valid seeds make mutation reach strict DSL/evaluator
paths instead of spending the campaign only on container-magic rejection.

```text
PYTHONPATH=. python3 -m mathsvg.python.repro.fuzz_campaign \
  --repository . \
  --experiment-id development-sanitizer-v1 \
  --machine-id local-x86 \
  --runs 10000
```

A successful finite campaign is labelled `development-pass`, not proof that
all defects are absent. A timeout, non-zero exit, crash artifact, short
reported run count, source mutation or input mutation remains visible.

## Gate 2 literal safety

`literal_safety` measures every required random/already-compressed control in
the development and validation manifests under Fast, Balanced, and Max. It
strictly inspects each archive, restores and hashes the payload, and requires
two identical archives per dataset/profile. The exact integer bound separates
the canonical v1 literal envelope from the permitted 0.1% expansion:

```text
archive_bytes <= original_bytes + floor(original_bytes / 1000)
                 + 256 + 232 * block_count
```

The 256 bytes are the file header/footer. The 232 bytes per block are the
directory record, block header, and canonical `Concat + Literal` wire envelope;
literal payload bytes are not counted as overhead.

```text
PYTHONPATH=. python3 -m mathsvg.python.repro.literal_safety \
  --repository . \
  --experiment-id development-validation-literal-safety-v1 \
  --machine-id local-x86 \
  --manifest mathsvg/results/manifests/development.csv \
  --manifest mathsvg/results/manifests/validation.csv \
  --repetitions 2
```

## Gate 3 memory

`memory_gate` first compares the CLI's built-in runtime profile with the
checked-in TOML through the benchmark runner's strict preflight. This prevents
a stale binary from being measured under a newer configuration identity. It
then measures real compression and decompression with GNU time, checks the
restored SHA-256, and applies strict (`<`) profile thresholds.

```text
PYTHONPATH=. python3 -m mathsvg.python.repro.memory_gate \
  --repository . \
  --experiment-id memory-enwik9-v1 \
  --machine-id local-x86 \
  --manifest mathsvg/results/manifests/development.csv \
  --split development \
  --dataset-id dev-enwik9-enwik9 \
  --profile fast \
  --profile balanced \
  --profile max \
  --repetitions 5 \
  --warmups 1
```

Gate 3 can pass only on the canonical 1,000,000,000-byte enwik9 row, all three
profiles, at least five measured repetitions and a warm-up. A smaller input or
short campaign is useful development evidence but remains `incomplete`.

## Gate 4 procedural proof

`procedural_gate` runs the Structured profile on the frozen exact-generator
set and the selected primary-real structured development domains. Exact
generators require at least 99% function coverage, zero literal-leaf bytes and
positive pre-entropy procedural gain. At least one real domain must have both
positive pre-entropy gain and 50% function coverage. The report remains a
useful negative result when the latter condition is not met.

```text
PYTHONPATH=. python3 -m mathsvg.python.repro.procedural_gate \
  --repository . \
  --experiment-id development-procedural-gate-v1 \
  --machine-id local-x86 \
  --manifest mathsvg/results/manifests/development.csv
```

## Determinism

Archive determinism is handled by `mathsvg.python.determinism.runner`. Its
publishable mode requires 100 repetitions and records unavailable
thread/backend/compiler/machine/architecture cells rather than inferring them.
The safer development wrapper also requires a manifest row, refuses any
manifest containing holdout, and preflights both debug/release runtime profiles:

```text
PYTHONPATH=. python3 -m mathsvg.python.repro.determinism_campaign \
  --repository . \
  --experiment-id determinism-smoke-v1 \
  --machine-id local-x86 \
  --manifest mathsvg/results/manifests/development.csv \
  --dataset-id dev-canterbury-alice29-txt \
  --profile balanced \
  --repetitions 2
```

This wrapper deliberately records the compiler-to-binary relationship as
`provenance_status=unverified`: hashing a compiler executable after a build
does not cryptographically prove that it produced an existing binary. The
evidence therefore remains `incomplete` even if its local byte matrix agrees.
A publishable two-compiler claim still needs build-time provenance from the
freeze/build procedure.

### Local compiler matrix

`compiler_matrix` produces the narrower, build-linked local compiler check.
It is pinned to stable Rust 1.97.1, compatibility Rust 1.85.1, the Balanced
profile, and the development Canterbury `alice29.txt` identity. The runner
uses a separate temporary `CARGO_TARGET_DIR` for each compiler and binds each
Cargo process to the absolute matching `RUSTC` and `RUSTDOC`. It refuses an
existing report, builds with `--locked --offline`, requires the isolated
stable build to match the canonical release binary, compares two archives
byte-for-byte, and verifies all four compiler/archive decode pairings before
atomically writing JSON:

```text
PYTHONPATH=. python3 -m mathsvg.python.repro.compiler_matrix generate \
  --repository . \
  --experiment-id compiler-matrix-local-v2 \
  --machine-id local-x86-64 \
  --stable-toolchain stable-x86_64-unknown-linux-gnu \
  --compat-toolchain 1.85-x86_64-unknown-linux-gnu \
  --canonical-release target/release/mathsvg \
  --input datasets/data/canterbury/alice29.txt \
  --profile balanced \
  --output mathsvg/results/determinism/compiler-matrix-local-v2.json
```

The generated document retains the exact
`mathsvg-local-compiler-matrix-v1` field contract; `v2` identifies the
experiment revision, not a new schema. Validate its shape and claims without
running either compiler, or additionally re-hash the local input, toolchains,
and canonical binary:

```text
PYTHONPATH=. python3 -m mathsvg.python.repro.compiler_matrix validate \
  --repository . \
  --report mathsvg/results/determinism/compiler-matrix-local-v2.json

PYTHONPATH=. python3 -m mathsvg.python.repro.compiler_matrix validate \
  --repository . \
  --report mathsvg/results/determinism/compiler-matrix-local-v2.json \
  --canonical-release target/release/mathsvg \
  --verify-files
```

This remains a same-host, one-input development spot check. It cannot satisfy
the independent-machine, ARM64, 100-repetition, clean-freeze, or holdout gates.
