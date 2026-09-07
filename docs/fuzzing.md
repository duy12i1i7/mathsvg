# Fuzzing MathZip

MathZip ships three `cargo-fuzz` targets:

- `decode`: bounded parsing and decompression of arbitrary archive bytes;
- `inspect`: the same untrusted path plus metric reconstruction;
- `roundtrip`: Balanced-mode encode/decode/inspect over arbitrary byte strings
  (capped at 512 input bytes), exercising model, residual, and transform paths.

The parser targets are seeded with a valid periodic-model archive so the initial
corpus reaches checks protected by header CRC and archive SHA-256.

## Recorded smoke campaign

The benchmark source snapshot was exercised again on `2026-07-24` with
`cargo-fuzz 0.13.2`, libFuzzer, and
`rustc 1.99.0-nightly (6f72b5dd5 2026-07-22)`.

```bash
cargo +nightly fuzz run decode -- -max_total_time=10 -timeout=2
cargo +nightly fuzz run inspect -- -max_total_time=10 -timeout=2
cargo +nightly fuzz run roundtrip -- -max_total_time=10 -timeout=3
```

| Target | Executions | Edge coverage | Feature coverage | Result |
|---|---:|---:|---:|---|
| `decode` | 1,305,168 | 441 | 519 | no crash/timeout |
| `inspect` | 1,288,144 | 534 | 612 | no crash/timeout |
| `roundtrip` | 4,529 | 1,702 | 4,277 | no crash/timeout |

This 10-second-per-target campaign is a smoke check, not evidence that the
decoder is bug-free. Longer CI/release campaigns should retain crashing inputs
under `fuzz/artifacts/`; generated corpora and artifacts are intentionally
Git-ignored.
