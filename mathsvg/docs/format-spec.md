# MathSVG container format v1

Status: frozen wire layout for format version 1 and DSL version 1. All offsets
and sizes below are bytes. MathSVG has no dependency on MathZip, `MZIP`, zstd,
or any other external codec.

## 1. Encoding, hashes and checksums

Fixed-width integers are unsigned little-endian. DSL integers and node records
use the canonical encoding in `procedural-dsl.md`.

`SHA-256(x)` is SHA-256 as specified by FIPS 180-4. `CRC32(x)` is
CRC-32/ISO-HDLC (the reflected `0xedb88320` polynomial, initial value
`0xffffffff`, final XOR `0xffffffff`; `CRC32("123456789") == 0xcbf43926`).
CRC fields are stored as little-endian `u32`.

Every size, offset, product and sum is checked before use. Overflow, a value
outside the implementation's configured archive/output limit, or any mismatch
with a redundant field is an error. Reserved fields, reserved flag bits,
non-canonical integers and trailing bytes are errors.

## 2. Physical layout

```text
file header                         128 bytes
block directory                    block_count * 144 bytes
block payload 0
block payload 1
...
block payload block_count-1
file footer                         128 bytes
```

The directory is mandatory and is also the random-access index. Sections and
payloads are contiguous in the order shown; padding and gaps are forbidden.
Consequently there is only one physical representation for a given directory
and sequence of block payloads.

V1 deliberately has no archive-global definition section. Every block is an
independent, closed program. Definitions and `REFERENCE` IDs are block-local,
and copy-family nodes may observe only output produced inside their own block.
This keeps sequential decode, random access, damage containment and peak
memory independent of archive size. Sharing remains available inside a block
through local definitions and `SHARE`. Cross-block/global definitions require
a later container version rather than a v1 flag.

### 2.1 File header: exactly 128 bytes

| Offset | Size | Type | Field | Canonical v1 value or meaning |
|---:|---:|---|---|---|
| 0 | 4 | bytes | `magic` | ASCII `MSVG` |
| 4 | 2 | `u16` | `container_version` | `1` |
| 6 | 2 | `u16` | `dsl_version` | `1` |
| 8 | 4 | `u32` | `flags` | `0` |
| 12 | 4 | `u32` | `header_size` | `128` |
| 16 | 8 | `u64` | `original_size` | total restored bytes |
| 24 | 8 | `u64` | `block_count` | number of directory records |
| 32 | 8 | `u64` | `directory_offset` | `128` |
| 40 | 8 | `u64` | `directory_bytes` | `block_count * 144` |
| 48 | 8 | `u64` | `payload_offset` | `128 + directory_bytes` |
| 56 | 8 | `u64` | `payload_bytes` | sum of block payload lengths |
| 64 | 8 | `u64` | `footer_offset` | `payload_offset + payload_bytes` |
| 72 | 8 | `u64` | `total_node_count` | checked sum of directory values |
| 80 | 8 | `u64` | `total_work_units` | checked sum of directory values |
| 88 | 32 | bytes | `original_sha256` | SHA-256 of the complete restored byte stream |
| 120 | 4 | `u32` | `reserved` | `0` |
| 124 | 4 | `u32` | `header_crc32` | `CRC32(header[0..124])` |

Here and below, a range such as `[0..124]` ends before byte 124. The archive
length must be exactly `footer_offset + 128`.

The empty stream has the unique structural representation `block_count == 0`,
zero directory/payload/count/work fields, and `original_sha256 ==
SHA-256(empty)`. Encoding an empty literal block instead is non-canonical.

### 2.2 Block directory record: exactly 144 bytes

Directory record `i` describes payload `i`.

| Offset | Size | Type | Field | Meaning |
|---:|---:|---|---|---|
| 0 | 8 | `u64` | `original_offset` | output position of this block |
| 8 | 8 | `u64` | `original_length` | restored bytes in this block |
| 16 | 8 | `u64` | `payload_offset` | absolute archive offset |
| 24 | 8 | `u64` | `payload_length` | complete block payload bytes |
| 32 | 4 | `u32` | `definition_count` | local definitions |
| 36 | 4 | `u32` | `node_count` | all serialized node records |
| 40 | 4 | `u32` | `edge_count` | child and definition-reference edges |
| 44 | 4 | `u32` | `flags` | `0` |
| 48 | 2 | `u16` | `max_graph_depth` | longest node path, root included |
| 50 | 2 | `u16` | `root_value_type` | `0` (`BYTES`) |
| 52 | 4 | `u32` | `reserved` | `0` |
| 56 | 8 | `u64` | `work_units` | normative static evaluator work |
| 64 | 8 | `u64` | `workspace_bytes` | normative peak scratch bound |
| 72 | 32 | bytes | `restored_sha256` | SHA-256 of this block's restored bytes |
| 104 | 32 | bytes | `payload_sha256` | SHA-256 of the complete block payload |
| 136 | 4 | `u32` | `payload_crc32` | CRC32 of the complete block payload |
| 140 | 4 | `u32` | `record_crc32` | `CRC32(record[0..140])` |

For a non-empty file every `original_length` is non-zero. Original ranges
start at zero, are contiguous, do not overlap and sum to `original_size`.
Payload ranges start at the header's `payload_offset`, are contiguous in
directory order and sum to `payload_bytes`. Specifically:

```text
record[0].original_offset = 0
record[i].original_offset =
    record[i-1].original_offset + record[i-1].original_length

record[0].payload_offset = header.payload_offset
record[i].payload_offset =
    record[i-1].payload_offset + record[i-1].payload_length
```

The directory metadata and the corresponding block header metadata must be
identical. Counts are not hints: the decoder recomputes and compares them.
`node_count` includes the root, embedded children, definitions and
`REFERENCE` node records exactly once as serialized. `edge_count` counts each
embedded-child edge and each reference-to-definition edge. Graph depth is the
longest acyclic dependency path measured in nodes, with a leaf depth of one.

### 2.3 Block payload

A block payload is:

```text
block header                         72 bytes
local definition section            definition_bytes
root node record                     root_bytes
```

#### Block header: exactly 72 bytes

| Offset | Size | Type | Field | Canonical v1 value or meaning |
|---:|---:|---|---|---|
| 0 | 4 | bytes | `magic` | ASCII `MSBL` |
| 4 | 2 | `u16` | `block_version` | `1` |
| 6 | 2 | `u16` | `dsl_version` | `1` |
| 8 | 4 | `u32` | `flags` | `0` |
| 12 | 4 | `u32` | `header_size` | `72` |
| 16 | 8 | `u64` | `decoded_length` | block restored length |
| 24 | 4 | `u32` | `definition_count` | local definition records |
| 28 | 4 | `u32` | `node_count` | exact serialized-node count |
| 32 | 4 | `u32` | `edge_count` | exact graph-edge count |
| 36 | 2 | `u16` | `max_graph_depth` | exact graph depth |
| 38 | 2 | `u16` | `root_value_type` | `0` (`BYTES`) |
| 40 | 8 | `u64` | `work_units` | normative static evaluator work |
| 48 | 8 | `u64` | `workspace_bytes` | normative peak scratch bound |
| 56 | 4 | `u32` | `definition_bytes` | exact definition-section length |
| 60 | 4 | `u32` | `root_bytes` | exact root-record length |
| 64 | 4 | `u32` | `reserved` | `0` |
| 68 | 4 | `u32` | `header_crc32` | `CRC32(block_header[0..68])` |

The enclosing directory `payload_length` must equal
`72 + definition_bytes + root_bytes`. All duplicated fields must match the
directory record.

Each local definition record is:

```text
definition_id:canonical-uLEB128
record_length:canonical-uLEB128
node_record[record_length]
```

Definition IDs are exactly `0, 1, ..., definition_count - 1` in that order. A
definition may reference only a lower local ID. IDs have no meaning outside
the block.

The root section contains exactly one complete `FILE` node record, consumes
exactly `root_bytes`, has one byte-producing child and declares
`decoded_length`. The definition section and every node payload must also be
consumed exactly. A root other than `FILE`, a nested `FILE`, or trailing bytes
is invalid.

`ENTROPY_LITERAL (0x13)` uses zero node flags and carries exactly one complete
native entropy v1 envelope as its raw node payload:

```text
format_version:u8             # 1
codec_opcode:u8               # 0 RAW, 1 BYTE_RLE, 2 ZERO_RUN,
                              # 3 SPARSE_ZERO, 4 BIT_PACK, 5 LZ_TOKENS,
                              # 6 CANONICAL_HUFFMAN, 7 LZ_HUFFMAN
flags:u8                      # 0
decoded_length:uLEB
payload_length:uLEB
payload[payload_length]
```

There is no inner DSL byte-string length: the node record already bounds the
envelope. Before output allocation, the parser inspects the whole envelope and
rejects an unknown field, non-canonical integer/payload, length mismatch,
truncation or trailing byte. Its output is `BYTES(decoded_length)`. Static
native work for opcodes `0..=5` is
`decoded_length*codec_weight + payload_length`, with weights
`1`, `2`, `2`, `2`, `3`, `4`. Opcode 6 is bounded by
`decoded_length + 8*payload_length + 131072`; opcode 7 is bounded by
`3*decoded_length + 8*payload_length + 131072`. The ordinary one-unit DSL
node base is additional. Workspace is zero because the evaluator decodes into
the final block destination.

Codec `LZ_TOKENS (5)` consumes sequences until the declared payload ends:

```text
literal_length:uLEB
literal[literal_length]
if payload bytes remain:
    match_length_minus_4:uLEB
    distance:uLEB             # 1..=min(65535, restored_prefix)
```

Copies may overlap. Empty output has an empty payload, while a terminal
literal sequence must be non-empty. Preflight validates the complete token
stream and exact restored length without materialising output. The normative
encoder uses one 65,536-entry `u32` last-position table, the four-byte hash
`(u32_le * 0x9e3779b1) >> 16`, greedy exact extension from the last position,
and insertion of every skipped position.

`CANONICAL_HUFFMAN (6)` and `LZ_HUFFMAN (7)` retain their canonical v1 payload
grammars defined by the native entropy specification. A profile may use a
different bounded encoder-only match search for opcode 7, but that policy is
not serialized and cannot alter the token grammar, Huffman grammar, preflight
bound or decoder. Existing opcode-7 archives therefore have identical
semantics.

Implemented coordinate node payloads are canonical parameter prefixes followed
by exactly one embedded child record:

```text
STRIDE(0x41):
    original_bytes:uLEB
    element_width_bytes:uLEB
    channels:uLEB
    child:node

BYTE_PLANE(0x42):
    original_bytes:uLEB
    word_width_bytes:uLEB
    endian:u8                 # 0 little, 1 big
    child:node

BIT_PLANE(0x43):
    original_bytes:uLEB
    child:node
```

All use zero node flags. The parser validates bounded widths/channels, exact
transformed child length, type and trailing-payload absence. During evaluation,
`BIT_PLANE` additionally rejects any nonzero unused high padding bit. Its
transformed length is `8*ceil(original_bytes/8)`; the other two preserve byte
length. Coordinate inverse work and scratch are recomputed using the formulas
in `procedural-dsl.md` and participate in the directory/header work and
workspace equality checks.

`SHARE` may create an additional nested local scope, but its references cannot
escape that scope and it cannot import another block. For copy-family opcodes,
"prior output" means an earlier, fully restored range in this block's
deterministic evaluation order. It never means bytes from a previous block.

### 2.4 File footer: exactly 128 bytes

| Offset | Size | Type | Field | Canonical v1 value or meaning |
|---:|---:|---|---|---|
| 0 | 4 | bytes | `magic` | ASCII `MSFT` |
| 4 | 2 | `u16` | `container_version` | `1` |
| 6 | 2 | `u16` | `footer_size` | `128` |
| 8 | 4 | `u32` | `flags` | `0` |
| 12 | 4 | `u32` | `reserved0` | `0` |
| 16 | 32 | bytes | `directory_sha256` | SHA-256 of the complete directory |
| 48 | 32 | bytes | `payload_sha256` | SHA-256 of the complete concatenated payload section |
| 80 | 32 | bytes | `prefix_sha256` | SHA-256 of archive bytes `[0..footer_offset]` |
| 112 | 8 | `u64` | `archive_length` | `footer_offset + 128` |
| 120 | 4 | `u32` | `reserved1` | `0` |
| 124 | 4 | `u32` | `footer_crc32` | `CRC32(footer[0..124])` |

SHA-256 of an empty directory or payload is SHA-256 of the empty string.
Footer hashes and CRCs provide corruption detection, not authenticity. A MAC
or signature, when required, is an outer transport concern and is not part of
v1.

## 3. Hard v1 block bounds

These wire-version bounds are enforced before graph evaluation; a deployment
may choose lower limits.

| Quantity | V1 maximum |
|---|---:|
| restored bytes per block | 16,777,216 (16 MiB) |
| encoded payload bytes per block | 33,554,432 (32 MiB) |
| local definitions per block | 262,144 |
| serialized nodes per block | 1,048,576 |
| graph edges per block | 2,097,152 |
| graph depth | 256 |
| normative scratch workspace per block | 67,108,864 (64 MiB) |
| normative work units per block | 1,099,511,627,776 (`2^40`) |

These are necessary, not sufficient, checks. The decoder also applies the DSL
limits on fan-out, tables, patterns, coordinates, recurrence/FSM state and
aggregate generated values. `work_units` and `workspace_bytes` are recomputed
from the normative DSL accounting and canonical evaluation schedule; a smaller
declared value is not trusted, and any unequal value is non-canonical.

For a block with local definition analyses `D_i` and root analysis `R`, v1
defines the workspace field exactly as:

```text
definition_cache_bytes = sum(output_bytes(D_i)) for every serialized definition
inner_scratch_peak     = max(scratch(D_i), scratch(R))
workspace_bytes        = definition_cache_bytes + inner_scratch_peak
```

The cache term deliberately includes unused definitions, making the value
independent of evaluator liveness optimizations. A decoder may allocate less by
materializing definitions lazily, but its measured peak must not exceed this
canonical bound. `work_units` is the root's static recursive work expression:
each `REFERENCE` includes its target's work at every call site. Memoization may
therefore perform less work, but never permits a different on-wire value.

A conforming decoder can process the directory as a stream, buffer at most one
32 MiB encoded block, materialize at most one 16 MiB result block and use no
more than the declared 64 MiB DSL scratch space. Parser bookkeeping and hash
state must also be bounded independently of archive size. A seekable decoder
rereads directory records by offset; a non-seekable decoder uses a bounded
on-disk directory spool rather than retaining the directory in RAM.
Implementations targeting the required sub-256 MiB decoder profile must use
compact/zero-copy node metadata rather than one heap allocation per node.

## 4. Canonical literal fallback

For a non-empty block of `n` bytes, the literal program has no definitions and
has this exact root:

```text
FILE(n, LITERAL(n, original_block_bytes))
```

Both node flag bytes are zero. Let `U(x)` be the byte length of canonical
uLEB128 for `x`. Its exact sizes are:

```text
literal_payload = U(n) + n
literal_node    = 2 + U(literal_payload) + literal_payload
file_payload    = U(n) + literal_node
root_bytes      = 2 + U(file_payload) + file_payload
block_payload   = 72 + root_bytes
```

The record has `definition_count = 0`, `node_count = 2`, `edge_count = 1`,
`max_graph_depth = 2`, and `root_value_type = BYTES`; work and workspace come
from the same normative accounting used for every candidate.

For each candidate block partition, the encoder first serializes the complete
literal archive for those exact boundaries. It also serializes the canonical
streaming literal partition whose blocks are consecutive 16 MiB chunks (the
last is shorter). The smallest complete literal archive under the normative
tie-break is the universal fallback. A procedural choice is accepted only if
its complete canonical archive, including header, all 144-byte directory
records, 72-byte block headers and footer, wins the actual serialized-size
comparison. Per-node or estimated costs cannot override that decision.

For `k > 0` literal blocks with root lengths `r_i`, total archive size is
exactly:

```text
128 + 144*k + sum(72 + r_i) + 128
= 256 + 216*k + sum(r_i)
```

The canonical empty archive is 256 bytes.

An encoder may also serialize an `ENTROPY_LITERAL` candidate and compare its
complete archive cost. This does not replace the fallback above:
`FILE(LITERAL(original_block_bytes))` is still measured first and retained as
the universal upper bound. In particular, a native entropy envelope selecting
its `RAW` codec has extra envelope bytes and cannot displace the raw DSL
literal. Existing `LITERAL (0x00)` records and the frozen empty/literal archive
goldens are unchanged.

## 5. Normative decoder order

A strict decoder processes an archive in this order:

1. Enforce the configured archive-byte limit while reading the 128-byte file
   header. Check magic, versions, all fixed values, reserved zeros and header
   CRC.
2. Checked-compute directory, payload, footer and total lengths from the header
   and require the exact canonical offsets.
3. Read exactly `block_count` directory records. Check each record CRC,
   hard/configured bounds, contiguous original and payload ranges, totals,
   root type and reserved values while incrementally hashing the directory.
   Do not allocate an array proportional to untrusted `block_count`; retain
   records by seeking the input or by writing a bounded-memory temporary spool.
4. For each block in directory order, read exactly its bounded payload while
   computing the per-block CRC/SHA, aggregate payload SHA and prefix SHA.
   Verify the per-block hashes before parsing or evaluating it.
5. Check the 72-byte block header and its CRC; require every duplicated field
   to equal the directory record. Parse local definitions topologically and
   then exactly one root, under all node/edge/depth/parameter limits. Recompute
   counts, work and scratch space and require exact equality.
6. Preflight the block's output, evaluation work and peak live memory. Evaluate
   with deterministic integer semantics, apply inverse coordinates, and
   require the exact block length and restored SHA-256.
7. Append the verified block to a temporary output sink while incrementally
   computing the complete restored SHA-256. A callback-style streaming API may
   expose verified blocks, but must report that final archive verification is
   still pending.
8. After the last payload, read exactly the 128-byte footer. Check its magic,
   version, fixed values, reserved zeros, archive length and footer CRC; compare
   directory, payload and prefix SHA-256 values.
9. Require the exact total output length and the header's
   `original_sha256`. Reject any trailing input.
10. Only now atomically commit the strict CLI's temporary output. On any error,
    discard it.

On a seekable source an implementation may pre-read the footer, but it must
perform the same comparisons and cannot relax any earlier bound. On a
non-seekable source all in-RAM state carried between blocks is fixed-size
hash/counter state; the directory spool and uncommitted output are bounded-memory
temporary sinks.

## 6. Validation and threat model

Archives are hostile input. In addition to the structural checks above, the
decoder rejects:

- cyclic, forward, cross-scope or cross-block references;
- exponential reference expansion or work beyond the recomputed budget;
- excessive fan-out/depth and parameter, table, pattern or state sizes;
- non-bijective or shape-inconsistent coordinate transforms;
- invalid recurrences, zero denominators and non-canonical coefficients;
- copy-before-start, copy outside the current block and forbidden overlap;
- truncated sections, duplicate definitions, unknown opcodes or flags;
- output-length, block-hash, whole-output-hash or section-hash mismatches.

SHA-256 and CRC defend against accidental corruption and substitution mistakes,
not against an attacker able to replace the archive and recompute public
digests.

## 7. Versioning and compatibility

Container and DSL versions are independent and both are present in the file
and block headers. A v1 decoder accepts only `(container_version, dsl_version)
== (1, 1)` and rejects unknown opcodes; optional interpretation of future
values is forbidden.

Before v1 is declared stable, byte-exact golden archives must cover the empty
archive, literal varint boundaries, multiple blocks, local definitions,
coordinates, recursive residuals, malformed lengths/checksums and every
implemented opcode. Scalar/SIMD, debug/release, thread-count, x86-64/AArch64
and supported-compiler encoders must produce identical archives.

MathZip `MZIP` bytes are never probed, wrapped, embedded as a baseline payload
or reinterpreted as MathSVG. Literal leaves contain original source bytes only.
