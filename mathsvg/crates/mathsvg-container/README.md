# mathsvg-container

Strict in-memory implementation of the frozen MathSVG container v1 envelope.
It implements the exact 128-byte file header, 144-byte directory record,
72-byte block header, block-local DSL sections, and 128-byte footer.

Main APIs:

- `encode_archive` serializes independent `Program` blocks and hashes their
  supplied restored bytes.
- `encode_literal_archive` emits the canonical 16 MiB literal partition, or
  the unique 256-byte empty archive.
- `decode_archive` checks all fixed fields, offsets, limits, CRC-32/ISO-HDLC,
  SHA-256 values, redundant metadata, canonical DSL sections, work, and
  workspace accounting.
- `DecodedArchive::verify_restored_with` integrates an evaluator, checks every
  restored block and the complete restored hash, and exposes verified blocks
  through a callback.
- In-memory and streaming encoders share one canonical block-preparation path.
  The stream encoder writes the prepared payload directly instead of building
  and decoding a temporary one-block archive.
- Block parsing retains the strict DSL validation report used for redundant
  directory checks, avoiding an identical second validation pass without
  weakening canonical parsing.

The golden empty and `abc` literal archives are stored under `tests/golden/`.
Debug and release tests produce the same bytes.

## Known limitations

- This MVP consumes a complete archive slice and retains parsed block programs.
  It rereads directory records in memory and does not retain a second directory
  array. Non-seekable input, on-disk directory/output spooling, and async I/O
  belong to the later streaming crate.
- Procedural evaluation is deliberately not duplicated here. A structural
  decode still has restored hashes pending until `verify_restored_with`
  receives an evaluator. A strict file caller must keep callback output
  uncommitted until final verification succeeds.
- At encode time, supplied restored bytes are length-checked and hashed, but
  their equality to procedural evaluation is the encoder caller's
  responsibility. The literal helper guarantees that equality by construction.
- Container v1 has no archive-global definitions or cross-block copies.
- CRC and SHA fields detect corruption; they do not authenticate an archive.
- Only DSL v1 opcodes implemented by `mathsvg-dsl` can currently be parsed.

Run:

```text
cargo test -p mathsvg-container
cargo test -p mathsvg-container --release
cargo clippy -p mathsvg-container --all-targets -- -D warnings
```
