# Fuzzing

The existing `decode`, `inspect`, and `roundtrip` targets exercise the
predecessor MathZip format. They remain unchanged.

MathSVG adds three bounded targets:

- `mathsvg_decode`: hostile container/DSL parsing, read-only inspection
  accounting, evaluator execution, and restored-hash verification;
- `mathsvg_entropy`: hostile leaf inspection versus decode consistency plus
  canonical encoder round trips;
- `mathsvg_roundtrip`: deterministic literal archive construction, strict
  container decode, evaluation, restored-hash verification, and exact bytes.

Small checked-in corpus directories live under `fuzz/seeds/`. Example local
runs from the repository root:

```text
cargo fuzz run mathsvg_decode fuzz/seeds/mathsvg_decode -- -max_len=65536
cargo fuzz run mathsvg_entropy fuzz/seeds/mathsvg_entropy -- -max_len=16384
cargo fuzz run mathsvg_roundtrip fuzz/seeds/mathsvg_roundtrip -- -max_len=4096
```

The target code also truncates inputs and applies explicit decoder/evaluator
limits. A CI compile check does not replace a timed sanitizer-backed fuzz run:

```text
cargo check --manifest-path fuzz/Cargo.toml --bins --offline
```
