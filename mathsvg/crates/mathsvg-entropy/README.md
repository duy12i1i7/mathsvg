# mathsvg-entropy

`mathsvg-entropy` implements the first deterministic, native byte-leaf coders
for MathSVG. It has no external dependencies and does not call, wrap, or embed
Zstd, XZ, Brotli, PAQ, or any other baseline codec.

The v1 leaf envelope is:

```text
format_version:u8 (= 1)
codec_opcode:u8
flags:u8 (= 0)
decoded_length:canonical-uLEB128
payload_length:canonical-uLEB128
payload[payload_length]
```

This is not an `MSVG` container. The procedural DSL embeds the complete
envelope, without another inner length field, as the payload of
`ENTROPY_LITERAL (0x13)`. The containing node still supplies its opcode and
record-length framing. The leaf envelope is versioned independently so its
bytes can be counted and tested without relying on container state.

## V1 codecs

| Opcode | Codec | Canonical payload |
|---:|---|---|
| `0x00` | `RAW` | the source bytes exactly |
| `0x01` | `BYTE_RLE` | maximal `(run_length, value)` runs |
| `0x02` | `ZERO_RUN` | alternating maximal zero and non-zero-literal runs |
| `0x03` | `SPARSE_ZERO` | sorted delta-coded non-zero positions and values |
| `0x04` | `BIT_PACK` | minimum bit width `0..=8`, LSB-first, zero padding |
| `0x05` | `LZ_TOKENS` | greedy literal sequences and bounded backward overlap copies |
| `0x06` | `CANONICAL_HUFFMAN` | byte-sorted code-length table and MSB-first canonical bitstream |
| `0x07` | `LZ_HUFFMAN` | canonical LZ token bytes encoded by the canonical Huffman payload |

`LZ_TOKENS` uses the following payload, repeated until `payload_length` is
consumed:

```text
literal_length:canonical-uLEB128
literal[literal_length]
if payload bytes remain:
    match_length_minus_4:canonical-uLEB128
    distance:canonical-uLEB128
```

The empty output has an empty payload. A terminal sequence contains a non-empty
literal and ends immediately after it. Every copy has length at least four and
distance `1..=65535`, and may overlap its destination. The distance may not
exceed the already restored prefix.

The canonical `G1` encoder keeps exactly one 65,536-entry table of four-byte
last-positions (`u32`, 256 KiB). Its hash is
`(u32_le(four_bytes) * 0x9e3779b1) >> 16` with wrapping multiplication. It
tests only the last position, accepts the first exact match of at least four
bytes, extends it greedily, and updates every skipped four-byte position.
The table is cleared at each 2^32-byte position epoch. These rules make output
independent of allocation addresses, timing, threads, or platform hash maps.
Every input position is inserted at most once and only one candidate is tested,
so counting and encoding are O(n); decoder state besides its caller-owned
destination is fixed-size.

Opcode `0x07` also has bounded encoder-only parser policies `C4`, `C8`,
`C16`, `C4L`, `C8L`, `C16L`, `C32L` and `C64L`. `Ck` keeps a 65,536-entry hash head and a
65,536-entry previous-position ring (`u32`, 512 KiB total) and visits at most
the newest `k` exact four-byte candidates. It prefers longer matches, then a
shorter distance uLEB128, then a smaller distance. `CkL` additionally compares
the exact local token gain at the next byte and delays only for a strictly
larger gain; equality keeps the current match. Thus neither address order nor
elapsed time can affect a decision.

The parser reports inserted positions, chain links and extension bytes. A
lazy depth-`k` walk is bounded by `3k + 1` work units per input byte: 13 for
`C4L`, 25 for the retained `C8L` (`C8Lazy`), 49 for `C16L`, 97 for `C32L`
and 193 for `C64L`. Preparation and emission use
the same walker, and emission verifies its sequence count, token count,
frequency table and FNV fingerprint against preparation. Production prepares
the complete candidate score first and performs the second, emitting walk only
if that score strictly beats the incumbent. The deterministic search ledger still
charges two walks for every complete candidate, so this byte-identical hot
path does not enlarge the admitted search budget. No token stream proportional
to the input is retained.

`CANONICAL_HUFFMAN` uses this payload:

```text
symbol_count:canonical-uLEB128
repeat symbol_count times, in strictly increasing symbol order:
    symbol:u8
    code_length:u8
encoded_bit_length:canonical-uLEB128
bits[ceil(encoded_bit_length / 8)]
```

For two or more symbols, ordinary binary Huffman lengths are constructed by
repeatedly merging the two nodes with the smallest
`(frequency, minimum_symbol, creation_index)` tuple. Codes are then assigned
in `(code_length, symbol)` order, beginning at zero and using the canonical
increment-and-left-shift rule. Code bits and bytes are MSB-first. The final
byte has zero low padding bits. A singleton table has code length zero and an
empty bitstream; an empty output has an empty table and bitstream.

The decoder requires a complete, non-oversubscribed prefix code, strictly
increasing unique table symbols, the singleton/empty special forms above,
exact bit and decoded lengths, every declared symbol to be used, and zero
padding. Code lengths are bounded by 255, the maximum depth of a binary tree
over 256 byte symbols. Its canonical codewords and at most 511 decode-tree
nodes are fixed-size stack state, so inspection and direct decode still use no
heap scratch. Its preflight work bound is
`decoded_length + 8 * payload_length + 131072`: every declared bit is visited
at most once and the fixed term covers worst-case byte-alphabet table,
canonical-code, tree, and table-use validation.

`LZ_HUFFMAN` composes the two definitions without embedding another leaf
envelope:

```text
lz_token_length:canonical-uLEB128
huffman_payload_that_decodes_exactly[lz_token_length]
```

Counting first walks the deterministic LZ parser and accumulates frequencies
for the exact canonical token bytes. After the complete Huffman table and bit
length are known, the encoder allocates the exact final envelope and performs
a second LZ walk that sends token bytes directly into the Huffman bit writer.
It never materialises the token stream. Inspection and decoding similarly
stream Huffman symbols into the strict LZ metadata/parser state machine; only
the caller-owned final destination stores restored bytes.

The Huffman writer packs codewords of at most 64 bits in byte-sized chunks;
the bitwise path remains for longer byte-alphabet codewords. This is only a
byte-identical emission hot path: canonical code assignment, padding, counted
length and every archive byte are unchanged.

The canonical LZ token stream is bounded by twice the restored length. For a
literal span, its integer and bytes cost at most `2L`. For a matched sequence,
`M >= 4`, the literal-length field, literals, match-length field and at most
three distance bytes together cost at most `2(L + M)`. The decoder rejects a
larger declaration before reading symbols. Its preflight work bound is
`3 * decoded_length + 8 * payload_length + 131072`, covering the complete
token stream, every restored byte, every encoded Huffman bit, and fixed
byte-alphabet validation. Reported heap scratch remains exactly zero.

The development-only oracle that admitted opcode `0x07` is retained as the
ignored `lz_huffman_oracle` integration test. It includes every outer-envelope
field, the LZ token length, Huffman table, bit length, and padding. Relative to
the winner before adding this codec:

| Development sample | Previous winner bytes | `LZ_HUFFMAN` bytes | Gain |
|---|---:|---:|---:|
| Canterbury Alice | 87,849 | 79,996 | 8.939% |
| Canterbury Kennedy XLS | 457,378 | 260,561 | 43.032% |
| Calgary `progc` | 24,086 | 18,946 | 21.340% |
| Silesia `x-ray` | 7,021,859 | 7,155,836 | -1.908% |
| synthetic constant | 8 | 22 | -175.000% |
| synthetic LFSR8 | 270 | 786 | -191.111% |
| synthetic linear | 271 | 789 | -191.144% |
| synthetic periodic | 19 | 35 | -84.211% |
| synthetic piecewise mixed | 12,720 | 13,159 | -3.451% |
| synthetic polynomial d2 | 271 | 508 | -87.454% |
| synthetic random | 65,545 | 66,070 | -0.801% |
| synthetic recurrence | 399 | 703 | -76.190% |

This is algorithm-admission evidence, not a full benchmark or a claim against
external baselines. `encode_best` keeps the older winner for negative cases.

The historical `LZH-ChainLazy-v1` first-admission oracle evaluated complete
one-block and full archives on eight open real development files. Across
13,427,974 baseline archive bytes, the add-only `C4L` portfolio saved 749,570
bytes (5.582152602%), won 24 of 30 blocks and at least one block in all eight
files, with zero budget stops. Its directional aggregate search slowdown was
2.476987052x on a non-isolated GUI host. That experiment retained `C4L` as the
shallowest lazy policy then admitted; this remains historical evidence rather
than the current profile choice.

The follow-on development depth oracle measured the marginal complete-archive
gain against the same G1 baseline. Moving from `C4L` to `C8L` saved another
87,952 bytes, or 0.6549908423 percentage points of the G1 baseline, exceeding
the 0.5% retention threshold. Moving from `C8L` to `C16L` saved another 50,863
bytes, or 0.3787838731 percentage points, below that threshold. Consequently
`C8L` is retained for every non-Fast profile, while `C16L` is not promoted;
Fast remains canonical `G1`. Exact evidence and limitations are recorded in
`results/oracle/lz-parser.csv` and
`results/oracle/lz-parser-v1-provenance.json`.

All integers are canonical uLEB128. `inspect` validates the entire envelope
and returns exact decoded length, payload length, work and scratch metadata
without allocating or materialising output. `preflight` and `preflight_exact`
are deliberately weaker resource-only helpers: they parse the bounded outer
envelope but do not walk the codec payload, and therefore may only be used
after the same program has already passed strict validation. They are not
untrusted-input validators.

`decode_into` and `decode_exact_into` write into caller-owned slices with zero
heap scratch and do not modify the destination until their preliminary strict
validation succeeds. `decode_exact_into_uncommitted` performs the strict
payload validation and decode in one walk; an error may leave its destination
partially modified, so that API is restricted to a private buffer which the
caller discards on error and publishes only after containing archive hashes
also pass.
Decoding rejects unknown versions,
opcodes or flags, non-canonical integers, non-maximal runs, explicit zero
exceptions, non-minimal bit widths, non-zero padding, invalid LZ lengths or
distances, empty terminal LZ literals, malformed Huffman tables or bitstreams,
length mismatches, truncation, and trailing bytes. LZ preflight walks token
metadata without constructing output;
decode uses the already restored destination as its overlap-copy history and
has zero scratch. Allocation and work are checked against caller limits before
output allocation.

`count_candidate` and `encoded_size` compute the exact wire size without
materialising candidate payloads. `encode_best` counts every v1 codec and
re-encodes only the winner. Its ordering is the project-wide order: encoded
bytes, decode work, decode memory, node count, dependency count, opcode, then
canonical parameter bytes. Because `RAW` is always included, the selected
leaf is never larger than the native raw leaf envelope.

The global `encode_best` entry point and the Fast profile continue to use
canonical `G1` exactly. Balanced, Max, Structured and Repository may evaluate
`C8L` only as an additional opcode-`0x07` candidate after a checked preflight
for both enhanced walks and exact verification. If that budget is unavailable,
they return the already evaluated `G1` result and record `ProviderBudget`; an
enhanced result is selected only when its complete candidate score is strictly
smaller. The opcode, payload grammar and decoder are unchanged, so older
archives remain valid.

## Deliberate gaps

This crate is a bounded native stage, not a claim of state-of-the-art entropy
coding. It currently has no Rice/Golomb coder, rANS, arithmetic/range coder,
context model, cross-leaf dictionary, streaming incremental API, SIMD path,
or topology/parameter entropy model. `G1` remains a single-candidate greedy
matcher; the retained `C8L` policy is a bounded local parser, not an optimal
parser. Further additions require oracle
evidence, a frozen canonical wire definition, malformed-input coverage, and
identical scalar/SIMD output before they may be emitted. No complex coder is
represented by a placeholder or by relabelling an external payload.
