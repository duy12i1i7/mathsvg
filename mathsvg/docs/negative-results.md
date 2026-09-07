# Negative results and rejected shortcuts

This is a living ledger.  It separates evidence that closes a direction from
work that is merely not implemented yet.

## Rejected architecture shortcuts

### Reusing the MathZip container as MathSVG

Rejected.  The predecessor format stores a global transform and flat
predictor segments.  It cannot canonically encode a typed procedural AST/DAG,
recursive correction graph, shared activation costs or block-independent
streaming.  MathSVG uses a new `MSVG` version space and does not reinterpret
`MZIP`.

### Depending directly on `mathzip-core`

Rejected for the new runtime.  Most reusable wire and model helpers are
crate-private, the public format contract is flat, and the crate includes a
zstd dependency.  Small integer, safety, predictor and residual ideas may be
ported with new semantics and tests; the old codec is not linked as the
MathSVG engine.

### Calling flat recursive segmentation a function graph

Rejected.  The predecessor recursively found boundaries but serialized flat
leaves, not the search topology.  Search recursion alone is not a procedural
representation.

### Embedding Zstd or another baseline as a residual leaf

Rejected.  This would turn Native MathSVG into a meta-codec and make a ratio
claim about the embedded baseline rather than the procedural thesis.  External
codecs remain oracle/baseline measurements only.

### Holding every candidate payload in memory

Rejected.  Candidate descriptors are retained, finalists are measured through
a counting sink, and only the winner is emitted again.  The v1 format has
independent bounded blocks so later streaming work does not require a format
rewrite.

### Raising oracle DP caps to hard maxima

Rejected. An interrupted development diagnostic configured one 4 KiB
segmentation oracle with roughly one million retained states/ledger rows and
reached about 6.3 GiB RSS. It produced no accepted evidence. The native oracle
now uses the production Balanced caps and records `bounded_incomplete` when a
cap stops search; hard-max brute force is not treated as a valid way to hide a
memory failure.

## Evidence that is insufficient for the new claims

### Predecessor Full benchmark

Useful as a development baseline, but not publishable MathSVG evidence:

- three repetitions instead of five/ten;
- one x86-64 machine;
- no randomized/interleaved codec ordering;
- no t interval or paired bootstrap aggregate;
- no ARM64/compiler/SIMD/thread determinism matrix;
- missing Snappy, ZPAQ, CMIX/PAQ and most domain baselines;
- all inputs have already been observed.

### Predecessor corpus balance

Rejected for the new main benchmark. The predecessor Full run had 644
synthetic and 81 labelled non-synthetic files, or 11.17% non-synthetic by that
older label. The old corpus remains development-only and is not relabelled.
This is no longer an infrastructure gap: the replacement development and
validation manifests contain 86/109 qualifying primary-real files (78.8991%)
and 99.9176% qualifying primary-real bytes, with all 57 section-20 requirements
covered.

### Existing general-purpose dominance

Not observed.  On the predecessor Full data, Balanced was materially worse
than Zstd-default and XZ in aggregate size, compression time, decompression
time and peak memory.  No aggregate core-four dominance pair existed, and no
per-file core-four dominance was found on the 81 labelled non-synthetic
inputs.

The current native implementation does not change that conclusion. Final open
validation v4 completed all 2,200 rows, but passed 0/160 per-file and 0/4
corpus certificates. At corpus level MathSVG is 1.56814% larger than
zstd-default, 2.08943% larger than gzip-6 and 7.14696% larger than XZ-9e. It
is smaller than LZ4 but remains roughly 127.5x slower to encode, 16.2x slower
to decode and 2.76x larger in peak RSS. No current result may be described as
general-purpose dominance.

### Best-profile selection

Too little real-data headroom to justify search-only optimism.  Selecting the
best of the already measured Fast/Balanced/Max outputs improved Balanced by
about 0.4741% overall and only about 0.09793% on the labelled non-synthetic
subset.  Representation changes are required.

## Deferred algorithms

- ARPL remains explicit-only. The refreshed finite oracle found a real Calgary
  `progc` 4 KiB win of 115 complete-archive bytes, but the subsequent paired
  production whole-file experiment measured exactly 0 bytes of archive
  difference. A local microblock win is sufficient to retain the experiment,
  not to enable it in built-in profiles.
- The bounded LZ depth sequence now stops at `C8L` for every non-Fast built-in
  profile. Relative to `C4L`, `C8L` saves another 87,952 complete development
  archive bytes, or 0.6549908423 percentage points of the G1 baseline, so it
  clears the frozen 0.5% incremental-retention threshold. The next step,
  `C16L`, saves only another 50,863 bytes (0.3787838731 percentage points),
  below that threshold and with one 90-byte Kennedy regression. Explicit
  `C32L` and `C64L` probes have still smaller marginal returns. They remain
  finite oracle policies, but are not enabled in built-in profiles. This is a
  measured stop decision, not a claim that deeper parsers are impossible.
- RSEE has a bounded native oracle, but remains emission-gated until that
  oracle finds at least 0.5% complete-archive gain and a non-synthetic block
  win. Equality saturation and synthesis for compression already have close
  prior art.
- AMCC is deferred until a transformed-copy oracle beats exact-copy cost on
  non-synthetic repository/structured blocks.  The predecessor copy ablation
  was too small to justify a large decoder surface.
- Exact SADH beyond microblocks is deferred until shared-definition headroom
  pays its activation metadata and search exceeds deterministic greedy by a
  meaningful amount.
- The independent RC-BasisDP provider remains an explicit experiment after its
  frozen 73-block real development scan produced no incremental winner over
  the active whole-coordinate path. The scan is rerun whenever native entropy
  changes; it does not reject coordinate discovery as a whole.

These directions are not declared permanently impossible.  Their current
status is recorded per generated native oracle row; a bounded-incomplete zero
is not promoted to a global impossibility result.

## Removed work with measured zero end-to-end benefit

- Buffering the entire C4L token stream to avoid its second parser walk was
  prototyped under a strict `<= 2 * block_bytes` allocation bound. The native
  archive bytes and hash remained identical, but the representative Silesia
  `x-ray` wall time did not improve (about 16.59 s before and 16.61 s after)
  and peak RSS did not improve. The buffer implementation was reverted; C4L
  keeps its two bounded parser walks.
- Balanced interval-function plus coordinate enumeration produced exactly
  zero archive-byte change in a paired ten-file, ten-repetition experiment.
  Disabling the searches reduced aggregate compression wall time by 29.6092%
  (95% interval -29.9665% to -29.2519%) and peak RSS by 1.5426%. The searches
  were therefore removed from Balanced's built-in path while exact
  whole-block functions remain enabled. They remain in other profiles and in
  the versioned ablation catalogue because their budgets/domains differ.

## Retained implementation optimization with no dominance claim

- Removing repeated program/entropy validation and the stream encoder's
  temporary one-block archive round-trip preserved every validation-v1
  archive identity. On full validation, compression median improved 31.6570%,
  decompression improved 49.6973%, and peak RSS improved 2.7992%. The change is
  retained because it is byte-identical and materially faster. It does not
  close size or throughput gaps: final validation v4 still passes zero dominance
  certificates.

## Structured procedural gate

- A deeper finite Structured/Max catalogue now explains all six frozen exact
  generators with 100% function coverage, zero literal leaves and positive
  pre-entropy gain. This closes the synthetic half of Gate 4.
- The complete 17-row development campaign still fails Gate 4 overall. None
  of the six primary-real domains has a winning function node or positive
  pre-entropy procedural gain. The implementation must not present exact
  synthetic LFSR/polynomial/recurrence results as evidence of real structured
  procedural compression.

## Stop-condition policy

A primitive or algorithm is removed from an emitting profile when any frozen
evaluation shows:

- oracle gain below 0.5%;
- no non-synthetic winning block;
- only synthetic wins;
- more than 2x search for less than 0.5% gain;
- parameter/definition cost exceeding correction reduction;
- graph larger than literal fallback;
- loss of gain on holdout;
- no symbolic gain inside the budget;
- DAG sharing below break-even;
- deeper correction without a smaller archive.

Removed experiments remain in the ablation/oracle tables with their failure
reason.  They are not deleted from the scientific record.

All stop decisions after entropy opcode `0x06`/`0x07` use the native oracle
probe. Earlier raw-literal reference percentages are historical and cannot
re-enable a feature.
