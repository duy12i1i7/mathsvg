# Gate 1 determinism runner

This package produces the required canonical
`mathsvg/results/determinism/results.csv`. It compares the SHA-256 of the
archive itself, then decompresses every executed archive and checks the
restored SHA-256 against the input.

The publishable default is 100 repetitions. `--mode smoke` permits a smaller
development matrix. Holdout is deliberately not accepted.

```text
PYTHONPATH=. python3 -m mathsvg.python.determinism.runner \
  --root . \
  --experiment-id determinism-v1 \
  --machine-id workstation-x86 \
  --split development \
  --input datasets/synthetic/recurrence/s0000004096_n000p100_seed1297748005.bin \
  --profile balanced \
  --debug-cli target/debug/mathsvg \
  --release-cli target/release/mathsvg \
  --compiler /absolute/path/to/rustc
```

Commands are always executed as argv arrays without a shell. Timeout,
non-zero exit, missing output, restored-hash mismatch, archive-hash mismatch,
and unavailable capabilities remain explicit CSV rows. A capability rejected
at runtime (for example SIMD unsupported on the current CPU) is also retained
as `unavailable`. A failure stops later
repetitions only within that matrix cell; the unexecuted repetitions are
retained as `unavailable` with the causal failure.

## Capability contract

The runner derives support only from deterministic CLI help:

- compression thread control must advertise `--threads` and explicitly
  mention the value `all` or all logical CPUs;
- a compression or decompression backend flag must advertise `--backend` with
  `[possible values: scalar, simd, auto]`;
- `compress` must advertise `--profile`.

The required matrix is the Cartesian product:

```text
threads = 1, 2, 4, 8, all
backend = scalar, simd, auto
```

Backend is applied only at stages whose help advertises it. Therefore a CLI
with threaded compression and scalar/SIMD/auto decompression is tested by
encoding under each thread setting and decoding under each backend; the
runner does not falsely claim that compression used a backend flag.

When either required flag is absent, all affected required cells are written
as `unavailable`. The runner additionally executes `default/default` so the
current CLI still receives repeated archive/round-trip coverage, but this
supplemental cell cannot satisfy the missing Gate 1 axes.

At the time this contract was written, the CLI did not yet advertise
compression `--backend`; backend-specific compression must remain unavailable
unless that capability is implemented. Decompression backend support can
independently satisfy the evaluator backend axis.

## Evidence identity and aggregation

Every row pins:

- source commit and dirty-state digest;
- machine, architecture and OS;
- exact compiler executable hash and version;
- debug/release CLI executable hash;
- runner, profile config and input hashes;
- capability-probe hash and canonical command templates.

`--append` accepts only the same experiment, input, profile and config. It can
merge evidence copied from another machine/compiler invocation while retaining
canonical row order. Duplicate matrix coordinates are rejected.

`evaluate_gate` returns `pass` only when all archives agree and all round trips
pass across at least 100 repetitions, distinct debug and release binaries, all
required axes, at least two compiler versions/binaries, two machines and two
architectures. Missing
infrastructure yields `incomplete`, never a false pass. Dirty source, command
failure, timeout or differing archive hashes prevents Gate 1 success.
