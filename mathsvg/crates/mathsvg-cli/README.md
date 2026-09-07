# mathsvg-cli

`mathsvg-cli` is the strict end-to-end interface for native MathSVG v1. It
never invokes or embeds an external codec.

```text
mathsvg compress [--profile PROFILE] [--threads 1..64|all]
  [--disable whole-block-entropy|whole-block-functions|coordinates]...
  [--force] INPUT OUTPUT.msvg
mathsvg decompress [--backend scalar|simd|auto] [--force] ARCHIVE.msvg OUTPUT
mathsvg inspect [--verify] [--backend scalar|simd|auto] ARCHIVE.msvg
mathsvg profile [--profile PROFILE] [--disable ALGORITHM]...
```

`compress` reads one profile-sized block at a time, runs the finite native
portfolio optimizer, and writes each canonical block payload to a temporary
seekable spool. The final archive is assembled into an uncommitted output file
and atomically published only after all blocks and the footer are complete.
Parallel workers are joined and emitted in source-block order, so every thread
count produces the same bytes. At most `threads` input blocks and their bounded
optimizer states are live. The five profiles are built-in deterministic
integer budgets; they do not depend on elapsed time, scheduling, or external
codec configuration.

`decompress` validates the fixed directory without retaining it, then reads and
evaluates one block payload at a time into an uncommitted temporary file. It
checks each restored length/SHA-256, the footer, and the whole-file
length/SHA-256 before atomically publishing the output. No verified prefix or
partial output is committed on failure. `--backend simd` requires a supported
native SSE2 or NEON implementation; `auto` reports which implementation was
actually selected. Backend choice never changes archive bytes.

`inspect` emits deterministic JSON after structural verification. With
`--verify`, restoration is checked in a bounded streaming pass and reports
`"restored_verified": true`; without that flag the restored hash remains
explicitly pending. Detailed inspection metadata still uses the in-memory
structural decoder and is bounded by `--max-archive-bytes`.
`procedural_breakdown` is an exact, non-overlapping wire partition whose
mandatory byte fields sum to `archive_bytes`. Verified inspection also builds
real literal-only and pre-entropy counterfactual archives; unavailable
coordinate/DAG/residual/symbolic ablations remain JSON `null`, never guessed
zero.

`profile` prints the effective Rust runtime search contract as JSON. Benchmark
freeze tooling hashes this output separately from the checked-in TOML
provenance file, preventing a profile label from silently describing different
runtime behavior.

`--disable` is a controlled ablation, not a reporting-only label. It removes
the named provider from the runtime candidate catalogue while holding profile
budgets fixed. Only active built-in paths are exposed: whole-block entropy,
whole-block functions, and coordinates. Repeated or reordered flags are
canonicalized. Supplying the same flags to `profile` exposes the exact gate set
and canonical ablation identity hashed by benchmark tooling.

Output paths are never overwritten by default. `--force` permits an atomic
replacement, but input and output may not resolve to the same file. Runtime
failures exit with status 1 and a `mathsvg:` error; command-line usage errors
use clap's status 2.

Compression memory is bounded by the selected worker batch plus one prepared
output block per worker and a disk payload spool;
decompression memory is bounded by one encoded payload plus one restored
block. Temporary spools carry whole-file data on disk rather than in RAM.
