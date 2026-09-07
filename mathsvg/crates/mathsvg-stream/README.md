# mathsvg-stream

Bounded orchestration for MathSVG v1.

- Compression reads one configured block, runs the deterministic exact
  optimizer, spools only the encoded block payload, and discards the block.
- `compress_reader_portfolio` runs the same bounded lifecycle with the
  oracle-gated built-in portfolio, so enabled native providers are not dropped
  at the streaming boundary.
- Final archive bytes are produced by `mathsvg-container` and are identical to
  the canonical in-memory encoder for the same block programs.
- Decompression rereads fixed directory records from a seekable archive and
  retains at most one encoded payload and one restored block.
- The container strictly validates each canonical program once. The evaluator
  then reuses that validation boundary, recomputes bounded layouts, and
  strictly decodes entropy leaves in one walk into the private restored block.
- A restored-output or archive-output file must remain uncommitted until the
  streaming call succeeds; this is the atomic commit boundary for final
  footer and whole-file SHA-256 verification.

No thread scheduling, SIMD path, randomness, floating-point score, external
codec, or source-file-sized allocation participates in these APIs.
