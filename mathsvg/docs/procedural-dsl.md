# Procedural DSL v1

## 1. Encoding discipline

Every node is encoded as:

```text
opcode:u8
flags:u8
payload_length:canonical-uLEB128
payload[payload_length]
```

Unsigned integers use canonical uLEB128. Signed integers use canonical ZigZag
followed by uLEB128. Byte strings and vectors carry a canonical element count.
No alternate representation of the same value is accepted.

Children are embedded node records or `REFERENCE` records. Definition records
are topologically ordered and length-delimited. Unknown opcode, flag, trailing
payload, non-canonical integer or type mismatch is an error.

## 2. Opcode registry

| Range | Class |
|---:|---|
| `0x00..0x1f` | leaves and function generators |
| `0x20..0x3f` | topology and DAG |
| `0x40..0x5f` | coordinate transforms |
| `0x60..0x7f` | algebraic composition and corrections |
| `0x80..0xbf` | reserved for versioned native extensions |
| `0xc0..0xff` | invalid in v1 |

### Function leaves

| Opcode | Node | Canonical payload and exact semantics |
|---:|---|---|
| `00` | `LITERAL` | `n, bytes[n]`; returns those bytes |
| `01` | `CONST` | `n, value`; returns `value` exactly `n` times |
| `02` | `INDEX` | `n, width, modulus, scale, offset`; word \(i\) is `(scale*i+offset) mod modulus` |
| `03` | `LINEAR` | `n, width, modulus, a, b`; word \(i=(a*i+b)\bmod modulus\) |
| `04` | `POLYNOMIAL` | `n, width, modulus, degree, coefficients`; Horner evaluation with wrapping modulus |
| `05` | `RATIONAL` | `n, width, numerator polynomial, denominator polynomial, rounding`; zero denominator invalid |
| `06` | `PIECEWISE` | ordered unique positions plus exact integer interpolation rule |
| `07` | `PERIODIC` | `pattern, repetitions, suffix`; exact concatenation |
| `08` | `MODULAR_POLYNOMIAL` | explicit modulus and canonical reduced coefficients |
| `09` | `RECURRENCE` | `n, width, modulus, coefficients, initial_state`; for order \(k\), \(x_i=\sum_{j=1}^{k}c_{j-1}x_{i-j}\bmod m\), so coefficient zero multiplies the nearest prior value |
| `0a` | `LFSR` | `n_bits, polynomial, seed`; LSB-first Fibonacci step defined by stored width |
| `0b` | `LOOKUP` | finite table plus bounded integer index child |
| `0c` | `FINITE_STATE_MACHINE` | finite output-labelled transitions and seed; exactly `n` steps |
| `0d` | `RUN` | bounded value and run-length child vectors whose lengths sum exactly to `n` |
| `0e` | `COPY` | prior output offset and length; source must end before destination |
| `0f` | `FRAME_OF_REFERENCE` | base plus packed bounded deltas |
| `10` | `CROSS_CHANNEL` | fixed-width channel coefficients and initial values |
| `11` | `DELTA` | prefix value plus modular first differences |
| `12` | `DELTA_OF_DELTA` | two initial values plus modular second differences |
| `13` | `ENTROPY_LITERAL` | one complete canonical native leaf envelope; restores its declared byte sequence |

`ENTROPY_LITERAL` carries these bytes directly as its node payload, with no
second descriptor or envelope-length field:

```text
format_version:u8             # exactly 1
codec_opcode:u8               # one of the frozen values 0..=7 below
flags:u8                      # exactly 0
decoded_length:uLEB
payload_length:uLEB
payload[payload_length]
```

The codec opcode and canonical payload are:

| Opcode | Codec | Canonical payload and exact semantics |
|---:|---|---|
| `00` | `RAW` | The restored bytes exactly; payload length equals decoded length |
| `01` | `BYTE_RLE` | Run count followed by maximal `(run_length, value)` pairs; adjacent runs have different values |
| `02` | `ZERO_RUN` | Token count followed by alternating maximal tagged zero and non-zero-literal runs; non-zero literals contain no zero byte |
| `03` | `SPARSE_ZERO` | Exception count followed by strictly increasing delta-coded positions and non-zero values; all omitted positions restore as zero |
| `04` | `BIT_PACK` | Minimum width `0..=8` followed by LSB-first packed byte values with zero high padding |
| `05` | `LZ_TOKENS` | Canonical literal/copy sequences using the grammar below |
| `06` | `CANONICAL_HUFFMAN` | Byte-sorted code-length table followed by an MSB-first canonical Huffman bitstream |
| `07` | `LZ_HUFFMAN` | The exact canonical LZ token byte stream encoded by the opcode-`06` Huffman payload, without a nested leaf envelope |

Preflight consumes and validates the complete envelope without materialising
the restored output. It rejects non-canonical integers, runs, sparse positions,
bit widths, tables, bitstreams or padding as well as truncation, length
mismatches and trailing bytes. Its static type is `Bytes(decoded_length)`.
For opcodes `00..05`, native decode work is
`decoded_length*codec_weight + payload_length`, with respective weights
`1, 2, 2, 2, 3, 4`. Opcode `06` work is
`decoded_length + 8*payload_length + 131072`; opcode `07` work is
`3*decoded_length + 8*payload_length + 131072`. The fixed term covers the
bounded byte-alphabet table, canonical-code and decode-tree validation. The
DSL leaf adds its ordinary one-unit node base. Scratch is exactly zero because
evaluation decodes directly into the caller's result destination.

The `LZ_TOKENS` payload is a sequence of
`literal_length:uLEB, literal_bytes`, followed—when payload bytes remain—by
`match_length_minus_4:uLEB, distance:uLEB`. Copies have distance
`1..=65535`, may overlap, and cannot reference before the restored prefix. A
terminal literal must be non-empty; empty output uses an empty payload.

The `CANONICAL_HUFFMAN` payload is:

```text
symbol_count:uLEB
repeat symbol_count times in strictly increasing symbol order:
    symbol:u8
    code_length:u8
encoded_bit_length:uLEB
bits[ceil(encoded_bit_length/8)]
```

For two or more symbols, the table is a complete, non-oversubscribed canonical
prefix code and every declared symbol must be used. Codes are ordered by
`(code_length, symbol)`, and both code bits and packed bytes are MSB-first; low
padding bits in the final byte are zero. A singleton has code length zero and
no encoded bits. Empty output has an empty table and bitstream.

The `LZ_HUFFMAN` payload is:

```text
lz_token_length:uLEB
huffman_payload_that_decodes_exactly[lz_token_length]
```

The declared token length may not exceed twice `decoded_length`. Strict
preflight streams the decoded Huffman symbols through the same canonical
`LZ_TOKENS` metadata parser, requiring both the exact declared token length and
the exact restored length. Encoder-only bounded match-search policies are not
serialized and do not change opcode `07` semantics.

Opcode `00` `LITERAL` and all of its wire bytes remain unchanged and continue
to define the universal raw fallback.

### Topology and sharing

| Opcode | Node | Constraints |
|---:|---|---|
| `20` | `FILE` | exactly one byte-producing child and declared original length |
| `21` | `CONCAT` | child count is bounded; concatenates exact child outputs |
| `22` | `SPLIT` | increasing boundaries; child lengths must match intervals |
| `23` | `INTERLEAVE` | bounded lane count and canonical round-robin/block rule |
| `24` | `GROUP` | one coordinate descriptor and one transformed child |
| `25` | `SHARE` | topologically ordered definitions followed by body |
| `26` | `REFERENCE` | lower definition ID plus canonical bounded parameter delta |

### Coordinate transforms

| Opcode | Node | Inverse semantics and constraints |
|---:|---|---|
| `40` | `RESHAPE` | metadata-only dimensions whose checked product equals element count |
| `41` | `STRIDE` | `original_bytes, element_width, channels, child`; stable record-major to channel-major lane transpose |
| `42` | `BYTE_PLANE` | `original_bytes, word_width, endian:u8, child`; numeric-significance byte planes |
| `43` | `BIT_PLANE` | `original_bytes, child`; eight LSB-first packed planes with canonical zero high padding |
| `44` | `TRANSPOSE` | bounded rank and a true permutation of axes |
| `45` | `TILE` | rectangular canonical row-major tiles plus exact edge tiles |
| `46` | `MORTON` | bounded 2D/3D integer bit interleave; dimensions stored exactly |
| `47` | `REVERSE` | reverses the child vector |
| `48` | `PERMUTE` | explicit bijection or versioned deterministic permutation descriptor |

For the implemented `41`–`43` nodes, the listed unsigned integer parameters
are canonical uLEB128, except `endian`, which is exactly one byte (`0` little,
`1` big). Parameters occur directly before the one embedded child record; no
second descriptor-length field is present. `element_width` and `word_width`
are measured in bytes and belong to `1,2,3,4,6,8`. A one-byte `BYTE_PLANE`
uses little endian as its only canonical spelling.

`STRIDE(w,c)` requires `original_bytes` to be divisible by `w*c`. It reads
record-major values whose records contain `c` contiguous `w`-byte channel
elements and emits stable channel-major lanes. `BYTE_PLANE(w,e)` requires a
whole number of words and emits planes from least to most numeric-significant
byte; `e` maps numeric significance to the stored byte offset. `BIT_PLANE`
turns `n` bytes into eight consecutive planes of `ceil(n/8)` bytes. Bits
inside every plane are packed LSB-first, and unused high bits in its last byte
must be zero.

Static typing for all three is:

```text
child: Bytes(transformed_bytes) -> node: Bytes(original_bytes)
```

The inverse work is `1 + original_bytes` for `STRIDE`/`BYTE_PLANE` and
`1 + 8*original_bytes` for `BIT_PLANE`. The coordinate node's inner scratch is
exactly `transformed_bytes + child_scratch`: the child is generated into one
transformed-domain buffer, then inverted directly into the parent's output
destination. `GROUP(Identity)` retains its original opcode `24` encoding and
metadata-only behavior; native identity never has an alternate `41`–`43`
spelling.

### Algebra and correction

| Opcode | Node | Semantics |
|---:|---|---|
| `60` | `ADD` | equal typed vectors, wrapping at stored width |
| `61` | `SUB` | equal typed vectors, wrapping at stored width |
| `62` | `MUL` | equal typed vectors, wrapping at stored width |
| `63` | `XOR` | equal-length bit vectors |
| `64` | `AND` | equal-length bit vectors |
| `65` | `OR` | equal-length bit vectors |
| `66` | `NOT` | bitwise complement inside declared width |
| `67` | `SHIFT` | explicit logical/arithmetic direction and checked amount |
| `68` | `ROTATE` | rotate inside declared word width |
| `69` | `SELECT` | mask and two equal typed branches |
| `6a` | `COMPOSE` | type-correct outer/inner nodes |
| `6b` | `TENSOR_PRODUCT` | checked output shape and product |
| `6c` | `KRONECKER` | modular word matrix product with checked dimensions |
| `6d` | `LOW_RANK` | exact modular factors plus correction child |
| `6e` | `EXCEPTIONS` | sorted delta-coded unique positions and replacement values |
| `6f` | `SPARSE_SUM` | canonical component order, equal type and length |
| `70` | `XOR_COPY` | prior source XOR recursively represented correction |
| `71` | `ADD_COPY` | prior source plus correction modulo stored width |
| `72` | `AFFINE_COPY` | `scale*source+offset+correction` modulo stored width |
| `73` | `PERMUTED_COPY` | bounded permutation of prior source plus correction |

## 3. Bounds

Each opcode has a constant base work plus a declared per-output-element work
weight. The decoder accumulates work before evaluating a node and rejects an
archive that would exceed its limit. It also checks:

- maximum archive/output bytes;
- maximum definitions, nodes, edges, graph depth and fan-out;
- maximum parameter/table/pattern bytes;
- maximum coordinate rank, tile count, recurrence order and FSM states;
- maximum aggregate generated bytes and temporary memory.

The v1 temporary-memory value is a canonical conservative bound, not a
platform allocator measurement: it is the sum of every local definition's
output size plus the greatest inner scratch requirement among all definitions
and the root. Static work recursively charges every reference call; a lazy
memoizing evaluator may use less work and memory but must remain within the
declared, recomputed bounds.

The evaluator never executes native code, bytecode, shell commands, external
models or URLs. The DSL is data, not a general-purpose virtual machine.

## 4. Implementation stages

The v1 registry is normative, but implementation is gated:

1. MVP: `LITERAL`, `ENTROPY_LITERAL`, `CONST`, `LINEAR`, `PERIODIC`,
   `RECURRENCE`, `CONCAT`, `SPLIT`, `GROUP`, `EXCEPTIONS`.
2. Coordinate phase: `STRIDE`, `BYTE_PLANE`, `BIT_PLANE`, `TRANSPOSE`, `TILE`.
3. Recursive residual/DAG phase: algebra nodes, `SHARE`, `REFERENCE`.
4. Oracle-approved extensions: symbolic, copy, basis and grammar nodes.

An unimplemented opcode is rejected, never silently interpreted as literal.
Every implemented opcode requires unit, property, malformed-input and fuzz
coverage before it may be emitted by the encoder.
