//! Deterministic, self-contained native leaf coders for MathSVG.
//!
//! The crate implements only byte-local, exactly reversible coders. It never
//! invokes an external codec and has no learned or platform-dependent model.
//! Candidate payloads are counted without materialisation; [`encode_best`]
//! serialises only the winner and always includes [`LeafCodec::Raw`] as the
//! universal upper bound.
//!
//! The complete v1 wire definition and the intentionally unimplemented
//! entropy families are documented in the crate `README.md`.

#![forbid(unsafe_code)]

mod error;
mod huffman;
mod wire;

pub use error::{Error, Result};

use core::cmp::Ordering;
use wire::{encoded_len as varint_len, write_u64, Cursor};

/// Version byte at the start of every native leaf envelope.
pub const FORMAT_VERSION: u8 = 1;

/// All v1 native leaf codecs, in stable opcode order.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
#[repr(u8)]
pub enum LeafCodec {
    /// Exact source bytes. This is the universal fallback.
    Raw = 0x00,
    /// Maximal runs of equal byte values.
    ByteRle = 0x01,
    /// Alternating maximal zero and non-zero literal runs.
    ZeroRun = 0x02,
    /// Implicit zeros plus sorted delta-coded non-zero exceptions.
    SparseZero = 0x03,
    /// LSB-first bytes at their minimum bit width from zero through eight.
    BitPack = 0x04,
    /// Greedy 4-byte-hash LZ sequences with bounded backward copies.
    LzTokens = 0x05,
    /// Deterministic canonical Huffman codes over the byte alphabet.
    CanonicalHuffman = 0x06,
    /// Canonical LZ token bytes encoded by canonical Huffman.
    LzHuffman = 0x07,
}

impl LeafCodec {
    pub const ALL: [Self; 8] = [
        Self::Raw,
        Self::ByteRle,
        Self::ZeroRun,
        Self::SparseZero,
        Self::BitPack,
        Self::LzTokens,
        Self::CanonicalHuffman,
        Self::LzHuffman,
    ];

    pub const fn opcode(self) -> u8 {
        self as u8
    }

    const fn work_weight(self) -> u64 {
        match self {
            Self::Raw => 1,
            Self::ByteRle | Self::ZeroRun | Self::SparseZero => 2,
            Self::BitPack => 3,
            Self::LzTokens => 4,
            // Canonical Huffman has a payload-sensitive bound in
            // `decode_work`; this value is never used for that codec.
            Self::CanonicalHuffman | Self::LzHuffman => 1,
        }
    }
}

impl TryFrom<u8> for LeafCodec {
    type Error = Error;

    fn try_from(value: u8) -> Result<Self> {
        match value {
            0x00 => Ok(Self::Raw),
            0x01 => Ok(Self::ByteRle),
            0x02 => Ok(Self::ZeroRun),
            0x03 => Ok(Self::SparseZero),
            0x04 => Ok(Self::BitPack),
            0x05 => Ok(Self::LzTokens),
            0x06 => Ok(Self::CanonicalHuffman),
            0x07 => Ok(Self::LzHuffman),
            other => Err(Error::InvalidOpcode(other)),
        }
    }
}

const LZ_MIN_MATCH: usize = 4;
const LZ_MAX_DISTANCE: usize = u16::MAX as usize;
const LZ_HASH_SIZE: usize = 1 << 16;
const LZ_EMPTY_POSITION: u32 = u32::MAX;
const LZ_HASH_MULTIPLIER: u32 = 0x9e37_79b1;
const LZ_FINGERPRINT_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
const LZ_FINGERPRINT_PRIME: u64 = 0x0000_0100_0000_01b3;

/// Frozen parser policies evaluated by the development-only LZ-Huffman oracle.
///
/// These policies do not change opcode `0x07` or its decoder. Even when an
/// add-only profile admits a policy, [`encode_best`] continues to use only
/// [`Self::G1`], the byte-identical v1 single-candidate parser.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum LzParserPolicy {
    /// Existing one-entry hash table and greedy parse.
    G1,
    /// Four-entry bounded hash-chain, greedy parse.
    C4,
    /// Eight-entry bounded hash-chain, greedy parse.
    C8,
    /// Sixteen-entry bounded hash-chain, greedy parse.
    C16,
    /// Four-entry chain with one-byte cost-aware lazy parsing.
    C4Lazy,
    /// Eight-entry chain with one-byte cost-aware lazy parsing.
    C8Lazy,
    /// Sixteen-entry chain with one-byte cost-aware lazy parsing.
    C16Lazy,
    /// Thirty-two-entry chain with one-byte cost-aware lazy parsing.
    C32Lazy,
    /// Sixty-four-entry chain with one-byte cost-aware lazy parsing.
    C64Lazy,
}

impl LzParserPolicy {
    /// All non-baseline policies in canonical oracle order.
    pub const EXPERIMENTAL: [Self; 6] = [
        Self::C4,
        Self::C8,
        Self::C16,
        Self::C4Lazy,
        Self::C8Lazy,
        Self::C16Lazy,
    ];

    /// Stable identifier written into oracle artifacts.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::G1 => "G1",
            Self::C4 => "C4",
            Self::C8 => "C8",
            Self::C16 => "C16",
            Self::C4Lazy => "C4L",
            Self::C8Lazy => "C8L",
            Self::C16Lazy => "C16L",
            Self::C32Lazy => "C32L",
            Self::C64Lazy => "C64L",
        }
    }

    /// Maximum chain links examined at one searched input position.
    pub const fn chain_depth(self) -> usize {
        match self {
            Self::G1 => 1,
            Self::C4 | Self::C4Lazy => 4,
            Self::C8 | Self::C8Lazy => 8,
            Self::C16 | Self::C16Lazy => 16,
            Self::C32Lazy => 32,
            Self::C64Lazy => 64,
        }
    }

    /// Whether the parser may compare the match at the following byte.
    pub const fn lazy(self) -> bool {
        matches!(
            self,
            Self::C4Lazy | Self::C8Lazy | Self::C16Lazy | Self::C32Lazy | Self::C64Lazy
        )
    }

    /// Fixed parser-table heap scratch, excluding allocator metadata.
    pub const fn scratch_bytes(self) -> u64 {
        match self {
            Self::G1 => (LZ_HASH_SIZE * core::mem::size_of::<u32>()) as u64,
            _ => (2 * LZ_HASH_SIZE * core::mem::size_of::<u32>()) as u64,
        }
    }

    /// Proven per-walk work bound per input byte.
    ///
    /// One unit is charged per inserted position, chain link, and extended
    /// byte comparison.
    pub const fn maximum_work_per_input_byte(self) -> u64 {
        let comparison_multiplier = if self.lazy() { 2 } else { 1 };
        1 + self.chain_depth() as u64 * (1 + comparison_multiplier)
    }
}

/// Auditable bounded work and token facts for an oracle parser walk.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LzParserStats {
    pub positions_inserted: u64,
    pub chain_links_examined: u64,
    pub extension_bytes_compared: u64,
    pub extension_byte_budget: u64,
    pub sequence_count: u64,
    pub token_bytes: u64,
    pub token_fingerprint: u64,
    pub budget_exhausted: bool,
    /// Fixed encoder scratch used by the parser. G1 retains its historical
    /// 256 KiB table; chain policies use exactly two 65,536-entry `u32` tables.
    pub scratch_bytes: u64,
}

impl LzParserStats {
    const fn new(extension_byte_budget: u64, scratch_bytes: u64) -> Self {
        Self {
            positions_inserted: 0,
            chain_links_examined: 0,
            extension_bytes_compared: 0,
            extension_byte_budget,
            sequence_count: 0,
            token_bytes: 0,
            token_fingerprint: LZ_FINGERPRINT_OFFSET,
            budget_exhausted: false,
            scratch_bytes,
        }
    }

    /// Exact charged work for this completed or bounded-stopped parser walk.
    pub fn work_units(self) -> Result<u64> {
        checked_add(
            checked_add(
                self.positions_inserted,
                self.chain_links_examined,
                "LZ parser work",
            )?,
            self.extension_bytes_compared,
            "LZ parser work",
        )
    }
}

/// Exact count result for one bounded LZ-Huffman parser policy.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LzPolicyAnalysis {
    pub policy: LzParserPolicy,
    /// `None` means the deterministic extension-byte budget stopped the walk.
    pub score: Option<CandidateScore>,
    pub stats: LzParserStats,
}

/// Exact encoding result for one bounded LZ-Huffman parser policy.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LzPolicyEncoding {
    pub analysis: LzPolicyAnalysis,
    /// Complete opcode-`0x07` leaf envelope, absent on `BUDGET_STOP`.
    pub bytes: Option<Vec<u8>>,
}

/// Conditional materialization result for one bounded LZ-Huffman policy.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConditionalLzPolicyEncoding {
    pub analysis: LzPolicyAnalysis,
    /// True only when the complete policy score strictly beats the incumbent.
    pub selected: bool,
    /// Present exactly when `selected` is true.
    pub bytes: Option<Vec<u8>>,
}

/// Add-only competition between the ordinary native leaf catalogue and one
/// explicitly selected LZ-Huffman parser policy.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LzPolicyCompetition {
    pub encoding: BestEncoding,
    pub policy: LzParserPolicy,
    pub policy_selected: bool,
    pub policy_analysis: LzPolicyAnalysis,
    /// Exact work from the policy's preparation walk and, when complete, its
    /// byte-identical emission walk. Ordinary catalogue work is reported by
    /// the caller's existing accounting.
    pub policy_work_units: u64,
}

/// A fixed-size canonical parameter byte key used as the final tie-break.
///
/// V1 candidates need at most one uLEB128 value, so no heap allocation is
/// required while counting candidates.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ParameterKey {
    bytes: [u8; 10],
    length: u8,
}

impl ParameterKey {
    const fn empty() -> Self {
        Self {
            bytes: [0; 10],
            length: 0,
        }
    }

    fn uleb(value: u64) -> Self {
        let mut bytes = [0u8; 10];
        let mut remaining = value;
        let mut length = 0usize;
        loop {
            let mut byte = (remaining & 0x7f) as u8;
            remaining >>= 7;
            if remaining != 0 {
                byte |= 0x80;
            }
            bytes[length] = byte;
            length += 1;
            if remaining == 0 {
                break;
            }
        }
        Self {
            bytes,
            length: length as u8,
        }
    }

    fn byte(value: u8) -> Self {
        let mut bytes = [0u8; 10];
        bytes[0] = value;
        Self { bytes, length: 1 }
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes[..usize::from(self.length)]
    }
}

impl Ord for ParameterKey {
    fn cmp(&self, other: &Self) -> Ordering {
        self.as_bytes().cmp(other.as_bytes())
    }
}

impl PartialOrd for ParameterKey {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Complete deterministic ordering key for one fully counted leaf candidate.
///
/// Field order is normative: bytes, work, memory, nodes, dependencies,
/// opcode, then canonical parameter payload.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct CandidateScore {
    pub encoded_bytes: u64,
    pub decode_work: u64,
    pub decode_memory: u64,
    pub node_count: u64,
    pub dependency_count: u64,
    pub opcode: u8,
    pub parameter_payload: ParameterKey,
}

/// Result of [`encode_best`].
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BestEncoding {
    pub codec: LeafCodec,
    pub score: CandidateScore,
    pub bytes: Vec<u8>,
}

/// Explicit G1 parser-pass accounting for [`encode_best_audited`].
///
/// This is returned data rather than a process-global counter, so concurrent
/// callers remain deterministic and do not affect one another.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EncodeBestAudit {
    /// One shared walk counts both raw LZ-token bytes and their Huffman model.
    pub g1_preparation_walks: u64,
    /// One additional walk is required only when an LZ candidate wins.
    pub g1_emission_walks: u64,
    pub g1_parser_walks: u64,
    pub g1_sequence_count: u64,
    pub g1_token_bytes: u64,
    pub g1_token_fingerprint: u64,
}

/// Limits applied before and during hostile-input decoding.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DecodeLimits {
    /// Maximum complete leaf envelope size accepted from the caller.
    pub max_encoded_bytes: u64,
    /// Maximum declared and materialised decoded bytes.
    pub max_output_bytes: u64,
    /// Maximum static v1 decode work units.
    pub max_work: u64,
}

impl Default for DecodeLimits {
    fn default() -> Self {
        Self {
            max_encoded_bytes: 32 * 1024 * 1024,
            max_output_bytes: 16 * 1024 * 1024,
            max_work: 1 << 40,
        }
    }
}

/// A decoded leaf and the codec declared by its envelope.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DecodedLeaf {
    pub codec: LeafCodec,
    pub bytes: Vec<u8>,
}

/// Canonical facts obtained without materialising the decoded bytes.
///
/// `temporary_bytes` is zero for v1 because [`decode_into`] writes directly
/// into a caller-owned destination and all codec state is fixed-size.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LeafMetadata {
    pub codec: LeafCodec,
    pub encoded_bytes: u64,
    pub decoded_bytes: u64,
    pub payload_bytes: u64,
    pub decode_work: u64,
    pub temporary_bytes: u64,
}

#[derive(Clone, Copy, Debug)]
struct Shape {
    payload_bytes: u64,
    parameter_payload: ParameterKey,
}

fn checked_add(left: u64, right: u64, context: &'static str) -> Result<u64> {
    left.checked_add(right)
        .ok_or(Error::IntegerOverflow { context })
}

fn checked_mul(left: u64, right: u64, context: &'static str) -> Result<u64> {
    left.checked_mul(right)
        .ok_or(Error::IntegerOverflow { context })
}

fn usize_u64(value: usize, context: &'static str) -> Result<u64> {
    u64::try_from(value).map_err(|_| Error::IntegerOverflow { context })
}

fn u64_usize(value: u64, context: &'static str) -> Result<usize> {
    usize::try_from(value).map_err(|_| Error::IntegerOverflow { context })
}

fn leaf_envelope_size(decoded_bytes: u64, payload_bytes: u64) -> Result<u64> {
    let fields = checked_add(
        u64::from(varint_len(decoded_bytes)),
        u64::from(varint_len(payload_bytes)),
        "leaf envelope integer fields",
    )?;
    checked_add(
        checked_add(3, fields, "leaf envelope header")?,
        payload_bytes,
        "leaf envelope",
    )
}

fn decode_work(codec: LeafCodec, decoded_bytes: u64, payload_bytes: u64) -> Result<u64> {
    if codec == LeafCodec::CanonicalHuffman {
        // Decoding visits each declared bit at most once. The bit count is no
        // greater than eight times the complete payload length. The fixed
        // allowance covers parsing and validating all 256 table entries,
        // canonical assignment, at most 511 decode-tree nodes, and table-use
        // validation. This remains a preflight bound because it needs no
        // payload parsing or output allocation.
        const FIXED_HUFFMAN_WORK: u64 = 131_072;
        return checked_add(
            checked_add(
                decoded_bytes,
                checked_mul(payload_bytes, 8, "Huffman decode work")?,
                "Huffman decode work",
            )?,
            FIXED_HUFFMAN_WORK,
            "Huffman decode work",
        );
    }
    if codec == LeafCodec::LzHuffman {
        // The canonical LZ token stream is at most twice the restored length:
        // literals account for at most 2L bytes including their length, while
        // every match consumes M>=4 source bytes and its three fields plus
        // literals are bounded by twice L+M. Huffman visits no more than eight
        // bits per complete payload byte. The final decoded-length term covers
        // literal writes and overlap copies.
        const FIXED_HUFFMAN_WORK: u64 = 131_072;
        return checked_add(
            checked_add(
                checked_mul(decoded_bytes, 3, "LZ-Huffman decode work")?,
                checked_mul(payload_bytes, 8, "LZ-Huffman decode work")?,
                "LZ-Huffman decode work",
            )?,
            FIXED_HUFFMAN_WORK,
            "LZ-Huffman decode work",
        );
    }
    checked_add(
        checked_mul(
            decoded_bytes,
            codec.work_weight(),
            "native leaf decode work",
        )?,
        payload_bytes,
        "native leaf decode work",
    )
}

fn bit_width(input: &[u8]) -> u8 {
    let maximum = input.iter().copied().max().unwrap_or(0);
    if maximum == 0 {
        0
    } else {
        (u8::BITS - maximum.leading_zeros()) as u8
    }
}

fn packed_bytes(decoded_bytes: u64, width: u8) -> Result<u64> {
    let bits = checked_mul(decoded_bytes, u64::from(width), "bit-pack payload bits")?;
    checked_add(bits, 7, "bit-pack byte rounding").map(|rounded| rounded / 8)
}

fn same_byte_run_end(input: &[u8], start: usize) -> usize {
    let value = input[start];
    let mut end = start + 1;
    while end < input.len() && input[end] == value {
        end += 1;
    }
    end
}

fn zero_class_run_end(input: &[u8], start: usize) -> usize {
    let zero = input[start] == 0;
    let mut end = start + 1;
    while end < input.len() && (input[end] == 0) == zero {
        end += 1;
    }
    end
}

fn allocate_lz_table() -> Result<Vec<u32>> {
    let mut table = Vec::new();
    table
        .try_reserve_exact(LZ_HASH_SIZE)
        .map_err(|_| Error::AllocationFailed {
            requested: LZ_HASH_SIZE * core::mem::size_of::<u32>(),
        })?;
    table.resize(LZ_HASH_SIZE, LZ_EMPTY_POSITION);
    Ok(table)
}

fn lz_hash(input: &[u8], position: usize) -> Result<usize> {
    let end = position
        .checked_add(LZ_MIN_MATCH)
        .ok_or(Error::IntegerOverflow {
            context: "LZ hash input end",
        })?;
    let bytes = input
        .get(position..end)
        .ok_or(Error::InvalidValue("LZ hash requires four source bytes"))?;
    let word = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
    Ok((word.wrapping_mul(LZ_HASH_MULTIPLIER) >> 16) as usize)
}

fn prepare_lz_epoch(position: usize, epoch: &mut u64, table: &mut [u32]) {
    let current_epoch = (position as u64) >> 32;
    if current_epoch != *epoch {
        table.fill(LZ_EMPTY_POSITION);
        *epoch = current_epoch;
    }
}

fn store_lz_position(
    input: &[u8],
    position: usize,
    epoch: &mut u64,
    table: &mut [u32],
) -> Result<()> {
    prepare_lz_epoch(position, epoch, table);
    let low = position as u32;
    if low != LZ_EMPTY_POSITION {
        table[lz_hash(input, position)?] = low;
    }
    Ok(())
}

fn previous_lz_position(
    input: &[u8],
    position: usize,
    epoch: &mut u64,
    table: &mut [u32],
) -> Result<Option<usize>> {
    prepare_lz_epoch(position, epoch, table);
    let hash = lz_hash(input, position)?;
    let stored = table[hash];
    let low = position as u32;
    table[hash] = if low == LZ_EMPTY_POSITION {
        LZ_EMPTY_POSITION
    } else {
        low
    };
    if stored == LZ_EMPTY_POSITION {
        return Ok(None);
    }
    let epoch_base = (*epoch).checked_shl(32).ok_or(Error::IntegerOverflow {
        context: "LZ epoch base",
    })?;
    let previous_u64 = epoch_base
        .checked_add(u64::from(stored))
        .ok_or(Error::IntegerOverflow {
            context: "LZ previous position",
        })?;
    let previous = u64_usize(previous_u64, "LZ previous position")?;
    if previous >= position || position - previous > LZ_MAX_DISTANCE {
        return Ok(None);
    }
    Ok(Some(previous))
}

fn visit_lz_sequences<F>(input: &[u8], mut visit: F) -> Result<()>
where
    F: FnMut(&[u8], usize, usize) -> Result<()>,
{
    if input.len() < LZ_MIN_MATCH {
        if !input.is_empty() {
            visit(input, 0, 0)?;
        }
        return Ok(());
    }
    let mut table = allocate_lz_table()?;
    let mut epoch = 0u64;
    let mut literal_start = 0usize;
    let mut position = 0usize;

    while input.len().saturating_sub(position) >= LZ_MIN_MATCH {
        let previous = previous_lz_position(input, position, &mut epoch, &mut table)?;
        let Some(previous) = previous else {
            position += 1;
            continue;
        };
        if input[previous..previous + LZ_MIN_MATCH] != input[position..position + LZ_MIN_MATCH] {
            position += 1;
            continue;
        }

        let maximum = input.len() - position;
        let mut match_length = LZ_MIN_MATCH;
        while match_length < maximum
            && input[previous + match_length] == input[position + match_length]
        {
            match_length += 1;
        }
        let distance = position - previous;
        visit(&input[literal_start..position], match_length, distance)?;

        let match_end = position
            .checked_add(match_length)
            .ok_or(Error::IntegerOverflow {
                context: "LZ match end",
            })?;
        let mut skipped = position + 1;
        while skipped < match_end && input.len().saturating_sub(skipped) >= LZ_MIN_MATCH {
            store_lz_position(input, skipped, &mut epoch, &mut table)?;
            skipped += 1;
        }
        position = match_end;
        literal_start = match_end;
    }

    if literal_start < input.len() {
        visit(&input[literal_start..], 0, 0)?;
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LzMatch {
    length: usize,
    distance: usize,
    gain: i128,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum MatchSearch {
    Complete(Option<LzMatch>),
    BudgetStop,
}

struct LzChain {
    head: Vec<u32>,
    previous: Vec<u32>,
    epoch: u64,
}

impl LzChain {
    fn allocate() -> Result<Self> {
        let mut head = Vec::new();
        head.try_reserve_exact(LZ_HASH_SIZE)
            .map_err(|_| Error::AllocationFailed {
                requested: LZ_HASH_SIZE * core::mem::size_of::<u32>(),
            })?;
        head.resize(LZ_HASH_SIZE, LZ_EMPTY_POSITION);

        let mut previous = Vec::new();
        previous
            .try_reserve_exact(LZ_HASH_SIZE)
            .map_err(|_| Error::AllocationFailed {
                requested: LZ_HASH_SIZE * core::mem::size_of::<u32>(),
            })?;
        previous.resize(LZ_HASH_SIZE, LZ_EMPTY_POSITION);
        Ok(Self {
            head,
            previous,
            epoch: 0,
        })
    }

    fn prepare_epoch(&mut self, position: usize) {
        let current_epoch = (position as u64) >> 32;
        if current_epoch != self.epoch {
            self.head.fill(LZ_EMPTY_POSITION);
            self.previous.fill(LZ_EMPTY_POSITION);
            self.epoch = current_epoch;
        }
    }

    fn insert(&mut self, input: &[u8], position: usize, stats: &mut LzParserStats) -> Result<u32> {
        self.prepare_epoch(position);
        let hash = lz_hash(input, position)?;
        let prior_head = self.head[hash];
        let low = position as u32;
        self.head[hash] = if low == LZ_EMPTY_POSITION {
            LZ_EMPTY_POSITION
        } else {
            self.previous[low as usize & (LZ_HASH_SIZE - 1)] = prior_head;
            low
        };
        stats.positions_inserted =
            checked_add(stats.positions_inserted, 1, "LZ chain inserted positions")?;
        Ok(prior_head)
    }

    fn absolute_position(&self, low: u32) -> Result<usize> {
        let base = self.epoch.checked_shl(32).ok_or(Error::IntegerOverflow {
            context: "LZ chain epoch base",
        })?;
        let absolute = base
            .checked_add(u64::from(low))
            .ok_or(Error::IntegerOverflow {
                context: "LZ chain position",
            })?;
        u64_usize(absolute, "LZ chain position")
    }

    fn predecessor(&self, low: u32) -> u32 {
        self.previous[low as usize & (LZ_HASH_SIZE - 1)]
    }
}

fn lz_local_gain(literal_length: usize, match_length: usize, distance: usize) -> Result<i128> {
    let literal = usize_u64(literal_length, "LZ lazy literal length")?;
    let matched = usize_u64(match_length, "LZ lazy match length")?;
    let distance = usize_u64(distance, "LZ lazy match distance")?;
    let all_literal_length = checked_add(literal, matched, "LZ lazy literal horizon")?;
    let all_literal_cost = checked_add(
        u64::from(varint_len(all_literal_length)),
        all_literal_length,
        "LZ lazy all-literal cost",
    )?;
    let match_fields = checked_add(
        u64::from(varint_len(matched - LZ_MIN_MATCH as u64)),
        u64::from(varint_len(distance)),
        "LZ lazy match fields",
    )?;
    let parsed_cost = checked_add(
        checked_add(
            u64::from(varint_len(literal)),
            literal,
            "LZ lazy literal cost",
        )?,
        match_fields,
        "LZ lazy parsed cost",
    )?;
    Ok(i128::from(all_literal_cost) - i128::from(parsed_cost))
}

fn better_chain_match(
    policy: LzParserPolicy,
    candidate: LzMatch,
    current: Option<LzMatch>,
) -> bool {
    let Some(current) = current else {
        return true;
    };
    if policy.lazy() {
        (
            candidate.gain,
            candidate.length,
            core::cmp::Reverse(varint_len(candidate.distance as u64)),
            core::cmp::Reverse(candidate.distance),
        ) > (
            current.gain,
            current.length,
            core::cmp::Reverse(varint_len(current.distance as u64)),
            core::cmp::Reverse(current.distance),
        )
    } else {
        (
            candidate.length,
            core::cmp::Reverse(varint_len(candidate.distance as u64)),
            core::cmp::Reverse(candidate.distance),
        ) > (
            current.length,
            core::cmp::Reverse(varint_len(current.distance as u64)),
            core::cmp::Reverse(current.distance),
        )
    }
}

fn find_chain_match(
    input: &[u8],
    position: usize,
    literal_length: usize,
    policy: LzParserPolicy,
    chain: &mut LzChain,
    stats: &mut LzParserStats,
) -> Result<MatchSearch> {
    let mut candidate_low = chain.insert(input, position, stats)?;
    let mut best = None;
    for _ in 0..policy.chain_depth() {
        if candidate_low == LZ_EMPTY_POSITION {
            break;
        }
        stats.chain_links_examined =
            checked_add(stats.chain_links_examined, 1, "LZ chain links examined")?;
        let previous_low = chain.predecessor(candidate_low);
        let previous = chain.absolute_position(candidate_low)?;
        if previous >= position || position - previous > LZ_MAX_DISTANCE {
            break;
        }
        if input[previous..previous + LZ_MIN_MATCH] == input[position..position + LZ_MIN_MATCH] {
            let maximum = input.len() - position;
            let mut match_length = LZ_MIN_MATCH;
            while match_length < maximum {
                if stats.extension_bytes_compared == stats.extension_byte_budget {
                    stats.budget_exhausted = true;
                    return Ok(MatchSearch::BudgetStop);
                }
                stats.extension_bytes_compared = checked_add(
                    stats.extension_bytes_compared,
                    1,
                    "LZ chain extension comparisons",
                )?;
                if input[previous + match_length] != input[position + match_length] {
                    break;
                }
                match_length += 1;
            }
            let distance = position - previous;
            let gain = lz_local_gain(literal_length, match_length, distance)?;
            let candidate = LzMatch {
                length: match_length,
                distance,
                gain,
            };
            if (!policy.lazy() || gain > 0) && better_chain_match(policy, candidate, best) {
                best = Some(candidate);
            }
        }
        candidate_low = previous_low;
    }
    Ok(MatchSearch::Complete(best))
}

fn insert_skipped_chain_positions(
    input: &[u8],
    start: usize,
    end: usize,
    chain: &mut LzChain,
    stats: &mut LzParserStats,
) -> Result<()> {
    let mut position = start;
    while position < end && input.len().saturating_sub(position) >= LZ_MIN_MATCH {
        chain.insert(input, position, stats)?;
        position += 1;
    }
    Ok(())
}

fn visit_chain_lz_sequences<F>(
    input: &[u8],
    policy: LzParserPolicy,
    mut visit: F,
) -> Result<LzParserStats>
where
    F: FnMut(&[u8], usize, usize) -> Result<()>,
{
    let input_bytes = usize_u64(input.len(), "LZ chain input length")?;
    let depth = usize_u64(policy.chain_depth(), "LZ chain depth")?;
    let comparison_multiplier = if policy.lazy() { 2 } else { 1 };
    let extension_byte_budget = checked_mul(
        checked_mul(input_bytes, depth, "LZ chain extension budget")?,
        comparison_multiplier,
        "LZ chain extension budget",
    )?;
    let scratch_bytes = usize_u64(
        2 * LZ_HASH_SIZE * core::mem::size_of::<u32>(),
        "LZ chain scratch bytes",
    )?;
    let mut stats = LzParserStats::new(extension_byte_budget, scratch_bytes);
    if input.len() < LZ_MIN_MATCH {
        if !input.is_empty() {
            stats.sequence_count = checked_add(stats.sequence_count, 1, "LZ chain sequence count")?;
            visit(input, 0, 0)?;
        }
        return Ok(stats);
    }

    let mut chain = LzChain::allocate()?;
    let mut literal_start = 0usize;
    let mut position = 0usize;
    while input.len().saturating_sub(position) >= LZ_MIN_MATCH {
        let literal_length = position - literal_start;
        let current = match find_chain_match(
            input,
            position,
            literal_length,
            policy,
            &mut chain,
            &mut stats,
        )? {
            MatchSearch::Complete(candidate) => candidate,
            MatchSearch::BudgetStop => return Ok(stats),
        };
        let Some(current) = current else {
            position += 1;
            continue;
        };

        let mut first_uninserted = position + 1;
        let selected = if policy.lazy() && input.len().saturating_sub(position + 1) >= LZ_MIN_MATCH
        {
            let lookahead = match find_chain_match(
                input,
                position + 1,
                literal_length + 1,
                policy,
                &mut chain,
                &mut stats,
            )? {
                MatchSearch::Complete(candidate) => candidate,
                MatchSearch::BudgetStop => return Ok(stats),
            };
            first_uninserted = position + 2;
            match lookahead {
                Some(lookahead) if lookahead.gain > current.gain => (position + 1, lookahead),
                _ => (position, current),
            }
        } else {
            (position, current)
        };

        let match_position = selected.0;
        let selected_match = selected.1;
        stats.sequence_count = checked_add(stats.sequence_count, 1, "LZ chain sequence count")?;
        visit(
            &input[literal_start..match_position],
            selected_match.length,
            selected_match.distance,
        )?;
        let match_end =
            match_position
                .checked_add(selected_match.length)
                .ok_or(Error::IntegerOverflow {
                    context: "LZ chain match end",
                })?;
        insert_skipped_chain_positions(input, first_uninserted, match_end, &mut chain, &mut stats)?;
        position = match_end;
        literal_start = match_end;
    }

    if literal_start < input.len() {
        stats.sequence_count = checked_add(stats.sequence_count, 1, "LZ chain sequence count")?;
        visit(&input[literal_start..], 0, 0)?;
    }
    Ok(stats)
}

fn visit_lz_sequences_with_policy<F>(
    input: &[u8],
    policy: LzParserPolicy,
    mut visit: F,
) -> Result<LzParserStats>
where
    F: FnMut(&[u8], usize, usize) -> Result<()>,
{
    if policy == LzParserPolicy::G1 {
        let scratch_bytes = usize_u64(
            LZ_HASH_SIZE * core::mem::size_of::<u32>(),
            "G1 LZ scratch bytes",
        )?;
        let mut stats = LzParserStats::new(u64::MAX, scratch_bytes);
        visit_lz_sequences(input, |literal, match_length, distance| {
            visit(literal, match_length, distance)?;
            stats.sequence_count = checked_add(stats.sequence_count, 1, "G1 LZ sequence count")?;
            Ok(())
        })?;
        return Ok(stats);
    }
    visit_chain_lz_sequences(input, policy, |literal, match_length, distance| {
        visit(literal, match_length, distance)
    })
}

fn visit_uleb_bytes<F>(value: u64, visit: &mut F) -> Result<()>
where
    F: FnMut(u8) -> Result<()>,
{
    let encoded = ParameterKey::uleb(value);
    for &byte in encoded.as_bytes() {
        visit(byte)?;
    }
    Ok(())
}

fn visit_lz_payload_bytes<F>(input: &[u8], visit: &mut F) -> Result<()>
where
    F: FnMut(u8) -> Result<()>,
{
    visit_lz_sequences(input, |literal, match_length, distance| {
        visit_uleb_bytes(usize_u64(literal.len(), "LZ literal length")?, visit)?;
        for &byte in literal {
            visit(byte)?;
        }
        if match_length != 0 {
            visit_uleb_bytes(
                usize_u64(match_length - LZ_MIN_MATCH, "LZ match length")?,
                visit,
            )?;
            visit_uleb_bytes(usize_u64(distance, "LZ match distance")?, visit)?;
        }
        Ok(())
    })
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LzTokenAudit {
    frequencies: huffman::Frequencies,
    token_bytes: u64,
    fingerprint: u64,
}

impl LzTokenAudit {
    const fn new() -> Self {
        Self {
            frequencies: huffman::Frequencies::new(),
            token_bytes: 0,
            fingerprint: LZ_FINGERPRINT_OFFSET,
        }
    }

    fn observe(&mut self, byte: u8) -> Result<()> {
        self.frequencies.observe(byte)?;
        self.token_bytes = checked_add(self.token_bytes, 1, "LZ policy token bytes")?;
        self.fingerprint ^= u64::from(byte);
        self.fingerprint = self.fingerprint.wrapping_mul(LZ_FINGERPRINT_PRIME);
        Ok(())
    }
}

fn visit_lz_payload_bytes_with_policy<F>(
    input: &[u8],
    policy: LzParserPolicy,
    visit: &mut F,
) -> Result<(LzParserStats, LzTokenAudit)>
where
    F: FnMut(u8) -> Result<()>,
{
    let mut audit = LzTokenAudit::new();
    let mut emit = |byte| {
        audit.observe(byte)?;
        visit(byte)
    };
    let mut stats =
        visit_lz_sequences_with_policy(input, policy, |literal, match_length, distance| {
            visit_uleb_bytes(
                usize_u64(literal.len(), "LZ policy literal length")?,
                &mut emit,
            )?;
            for &byte in literal {
                emit(byte)?;
            }
            if match_length != 0 {
                visit_uleb_bytes(
                    usize_u64(match_length - LZ_MIN_MATCH, "LZ policy match length")?,
                    &mut emit,
                )?;
                visit_uleb_bytes(usize_u64(distance, "LZ policy match distance")?, &mut emit)?;
            }
            Ok(())
        })?;
    stats.token_bytes = audit.token_bytes;
    stats.token_fingerprint = audit.fingerprint;
    Ok((stats, audit))
}

fn lz_token_bytes_bound(decoded_bytes: u64) -> Result<u64> {
    checked_mul(decoded_bytes, 2, "canonical LZ token byte bound")
}

#[derive(Clone, Copy, Debug)]
struct LzHuffmanPrepared {
    huffman: huffman::Prepared,
    token_bytes: u64,
    payload_bytes: u64,
}

#[derive(Clone, Copy, Debug)]
struct LzPolicyPrepared {
    huffman: huffman::Prepared,
    payload_bytes: u64,
    stats: LzParserStats,
    audit: LzTokenAudit,
}

fn prepare_lz_huffman(input: &[u8]) -> Result<LzHuffmanPrepared> {
    let mut frequencies = huffman::Frequencies::new();
    visit_lz_payload_bytes(input, &mut |byte| frequencies.observe(byte))?;
    let huffman = huffman::prepare_frequencies(frequencies)?;
    let token_bytes = huffman.source_bytes();
    let decoded_bytes = usize_u64(input.len(), "input length")?;
    let bound = lz_token_bytes_bound(decoded_bytes)?;
    if token_bytes > bound {
        return Err(Error::InvalidValue(
            "canonical LZ token stream exceeds proven bound",
        ));
    }
    let payload_bytes = checked_add(
        u64::from(varint_len(token_bytes)),
        huffman.analysis().payload_bytes,
        "LZ-Huffman payload",
    )?;
    Ok(LzHuffmanPrepared {
        huffman,
        token_bytes,
        payload_bytes,
    })
}

fn prepare_lz_huffman_policy(
    input: &[u8],
    policy: LzParserPolicy,
) -> Result<(Option<LzPolicyPrepared>, LzParserStats)> {
    let (stats, audit) = visit_lz_payload_bytes_with_policy(input, policy, &mut |_| Ok(()))?;
    if stats.budget_exhausted {
        return Ok((None, stats));
    }
    let decoded_bytes = usize_u64(input.len(), "input length")?;
    let bound = lz_token_bytes_bound(decoded_bytes)?;
    if audit.token_bytes > bound {
        return Err(Error::InvalidValue(
            "policy LZ token stream exceeds proven bound",
        ));
    }
    let huffman = huffman::prepare_frequencies(audit.frequencies)?;
    if huffman.source_bytes() != audit.token_bytes {
        return Err(Error::InvalidValue(
            "policy LZ frequency total differs from token count",
        ));
    }
    let payload_bytes = checked_add(
        u64::from(varint_len(audit.token_bytes)),
        huffman.analysis().payload_bytes,
        "policy LZ-Huffman payload",
    )?;
    Ok((
        Some(LzPolicyPrepared {
            huffman,
            payload_bytes,
            stats,
            audit,
        }),
        stats,
    ))
}

fn analyse(codec: LeafCodec, input: &[u8]) -> Result<Shape> {
    let input_bytes = usize_u64(input.len(), "input length")?;
    match codec {
        LeafCodec::Raw => Ok(Shape {
            payload_bytes: input_bytes,
            parameter_payload: ParameterKey::empty(),
        }),
        LeafCodec::ByteRle => {
            let mut run_count = 0u64;
            let mut pairs_bytes = 0u64;
            let mut start = 0usize;
            while start < input.len() {
                let end = same_byte_run_end(input, start);
                let length = usize_u64(end - start, "byte RLE run length")?;
                pairs_bytes = checked_add(
                    pairs_bytes,
                    checked_add(u64::from(varint_len(length)), 1, "byte RLE run")?,
                    "byte RLE payload",
                )?;
                run_count = checked_add(run_count, 1, "byte RLE run count")?;
                start = end;
            }
            Ok(Shape {
                payload_bytes: checked_add(
                    u64::from(varint_len(run_count)),
                    pairs_bytes,
                    "byte RLE payload",
                )?,
                parameter_payload: ParameterKey::uleb(run_count),
            })
        }
        LeafCodec::ZeroRun => {
            let mut token_count = 0u64;
            let mut tokens_bytes = 0u64;
            let mut start = 0usize;
            while start < input.len() {
                let end = zero_class_run_end(input, start);
                let length = usize_u64(end - start, "zero-run token length")?;
                let literal_bytes = if input[start] == 0 { 0 } else { length };
                let token_bytes = checked_add(
                    checked_add(1, u64::from(varint_len(length)), "zero-run token header")?,
                    literal_bytes,
                    "zero-run token",
                )?;
                tokens_bytes = checked_add(tokens_bytes, token_bytes, "zero-run payload")?;
                token_count = checked_add(token_count, 1, "zero-run token count")?;
                start = end;
            }
            Ok(Shape {
                payload_bytes: checked_add(
                    u64::from(varint_len(token_count)),
                    tokens_bytes,
                    "zero-run payload",
                )?,
                parameter_payload: ParameterKey::uleb(token_count),
            })
        }
        LeafCodec::SparseZero => {
            let mut exception_count = 0u64;
            let mut exceptions_bytes = 0u64;
            let mut previous = None;
            for (position, &value) in input.iter().enumerate() {
                if value == 0 {
                    continue;
                }
                let delta = match previous {
                    None => position.checked_add(1).ok_or(Error::IntegerOverflow {
                        context: "first sparse position",
                    })?,
                    Some(previous_position) => position - previous_position,
                };
                let delta = usize_u64(delta, "sparse position delta")?;
                exceptions_bytes = checked_add(
                    exceptions_bytes,
                    checked_add(u64::from(varint_len(delta)), 1, "sparse exception")?,
                    "sparse payload",
                )?;
                exception_count = checked_add(exception_count, 1, "sparse exception count")?;
                previous = Some(position);
            }
            Ok(Shape {
                payload_bytes: checked_add(
                    u64::from(varint_len(exception_count)),
                    exceptions_bytes,
                    "sparse payload",
                )?,
                parameter_payload: ParameterKey::uleb(exception_count),
            })
        }
        LeafCodec::BitPack => {
            let width = bit_width(input);
            Ok(Shape {
                payload_bytes: checked_add(
                    1,
                    packed_bytes(input_bytes, width)?,
                    "bit-pack payload",
                )?,
                parameter_payload: ParameterKey::byte(width),
            })
        }
        LeafCodec::LzTokens => {
            let mut payload_bytes = 0u64;
            visit_lz_sequences(input, |literal, match_length, distance| {
                let literal_length = usize_u64(literal.len(), "LZ literal length")?;
                payload_bytes = checked_add(
                    payload_bytes,
                    checked_add(
                        u64::from(varint_len(literal_length)),
                        literal_length,
                        "LZ literal sequence",
                    )?,
                    "LZ token payload",
                )?;
                if match_length != 0 {
                    let encoded_match_length =
                        usize_u64(match_length - LZ_MIN_MATCH, "LZ match length")?;
                    let distance = usize_u64(distance, "LZ match distance")?;
                    payload_bytes = checked_add(
                        payload_bytes,
                        checked_add(
                            u64::from(varint_len(encoded_match_length)),
                            u64::from(varint_len(distance)),
                            "LZ copy sequence",
                        )?,
                        "LZ token payload",
                    )?;
                }
                Ok(())
            })?;
            Ok(Shape {
                payload_bytes,
                parameter_payload: ParameterKey::empty(),
            })
        }
        LeafCodec::CanonicalHuffman => {
            let analysis = huffman::analyse(input)?;
            Ok(Shape {
                payload_bytes: analysis.payload_bytes,
                parameter_payload: ParameterKey::uleb(u64::from(analysis.symbol_count)),
            })
        }
        LeafCodec::LzHuffman => {
            let prepared = prepare_lz_huffman(input)?;
            Ok(Shape {
                payload_bytes: prepared.payload_bytes,
                parameter_payload: ParameterKey::uleb(prepared.token_bytes),
            })
        }
    }
}

fn candidate_score(codec: LeafCodec, decoded_bytes: u64, shape: Shape) -> Result<CandidateScore> {
    Ok(CandidateScore {
        encoded_bytes: leaf_envelope_size(decoded_bytes, shape.payload_bytes)?,
        decode_work: decode_work(codec, decoded_bytes, shape.payload_bytes)?,
        decode_memory: decoded_bytes,
        node_count: 1,
        dependency_count: 0,
        opcode: codec.opcode(),
        parameter_payload: shape.parameter_payload,
    })
}

/// Count one codec exactly without materialising its payload.
pub fn count_candidate(codec: LeafCodec, input: &[u8]) -> Result<CandidateScore> {
    let decoded_bytes = usize_u64(input.len(), "input length")?;
    let shape = analyse(codec, input)?;
    candidate_score(codec, decoded_bytes, shape)
}

/// Count a bounded LZ-Huffman parser policy exactly.
///
/// The result includes the complete opcode-`0x07` leaf envelope. A bounded
/// parser stop is returned as `score = None` and must not be interpreted as
/// evidence that no deeper-chain headroom exists.
pub fn count_lz_huffman_policy(input: &[u8], policy: LzParserPolicy) -> Result<LzPolicyAnalysis> {
    let (prepared, stats) = prepare_lz_huffman_policy(input, policy)?;
    let score = match prepared {
        None => None,
        Some(prepared) => Some(candidate_score(
            LeafCodec::LzHuffman,
            usize_u64(input.len(), "input length")?,
            Shape {
                payload_bytes: prepared.payload_bytes,
                parameter_payload: ParameterKey::uleb(prepared.audit.token_bytes),
            },
        )?),
    };
    Ok(LzPolicyAnalysis {
        policy,
        score,
        stats,
    })
}

/// Encode one bounded LZ-Huffman parser policy.
///
/// Counting/model construction and emission use the same deterministic walker.
/// The second walk must reproduce the exact token count, byte frequencies,
/// fingerprint, work counters and final envelope length from preparation.
pub fn encode_lz_huffman_policy(input: &[u8], policy: LzParserPolicy) -> Result<LzPolicyEncoding> {
    let decoded_bytes = usize_u64(input.len(), "input length")?;
    let (prepared, stats) = prepare_lz_huffman_policy(input, policy)?;
    let Some(prepared) = prepared else {
        return Ok(LzPolicyEncoding {
            analysis: LzPolicyAnalysis {
                policy,
                score: None,
                stats,
            },
            bytes: None,
        });
    };
    let score = candidate_score(
        LeafCodec::LzHuffman,
        decoded_bytes,
        Shape {
            payload_bytes: prepared.payload_bytes,
            parameter_payload: ParameterKey::uleb(prepared.audit.token_bytes),
        },
    )?;
    let bytes = encode_prepared_lz_huffman_policy(input, policy, prepared, score)?;
    Ok(LzPolicyEncoding {
        analysis: LzPolicyAnalysis {
            policy,
            score: Some(score),
            stats: prepared.stats,
        },
        bytes: Some(bytes),
    })
}

fn encode_prepared_lz_huffman_policy(
    input: &[u8],
    policy: LzParserPolicy,
    prepared: LzPolicyPrepared,
    score: CandidateScore,
) -> Result<Vec<u8>> {
    let decoded_bytes = usize_u64(input.len(), "input length")?;
    let counted = u64_usize(score.encoded_bytes, "policy LZ-Huffman leaf size")?;
    let mut output = allocate_exact(counted)?;
    write_prefix(
        &mut output,
        LeafCodec::LzHuffman,
        decoded_bytes,
        prepared.payload_bytes,
    );
    write_u64(&mut output, prepared.audit.token_bytes);
    let mut writer = huffman::start_payload(prepared.huffman, &mut output)?;
    let (emitted_stats, emitted_audit) =
        visit_lz_payload_bytes_with_policy(input, policy, &mut |byte| writer.write_byte(byte))?;
    writer.finish()?;
    if emitted_stats != prepared.stats || emitted_audit != prepared.audit {
        return Err(Error::InternalSizeMismatch {
            counted: u64_usize(prepared.audit.token_bytes, "prepared policy LZ token bytes")?,
            encoded: u64_usize(emitted_audit.token_bytes, "emitted policy LZ token bytes")?,
        });
    }
    if output.len() != counted {
        return Err(Error::InternalSizeMismatch {
            counted,
            encoded: output.len(),
        });
    }
    Ok(output)
}

/// Count one bounded parser policy and emit it only when it strictly beats
/// `incumbent` under the normative candidate order.
///
/// A complete losing analysis has `score = Some(_)`, `selected = false` and
/// no bytes; a bounded parser stop has no score. This preserves exact
/// candidate competition while avoiding the otherwise discarded second walk.
pub fn encode_lz_huffman_policy_if_better(
    input: &[u8],
    policy: LzParserPolicy,
    incumbent: CandidateScore,
) -> Result<ConditionalLzPolicyEncoding> {
    let decoded_bytes = usize_u64(input.len(), "input length")?;
    let (prepared, stats) = prepare_lz_huffman_policy(input, policy)?;
    let Some(prepared) = prepared else {
        return Ok(ConditionalLzPolicyEncoding {
            analysis: LzPolicyAnalysis {
                policy,
                score: None,
                stats,
            },
            selected: false,
            bytes: None,
        });
    };
    let score = candidate_score(
        LeafCodec::LzHuffman,
        decoded_bytes,
        Shape {
            payload_bytes: prepared.payload_bytes,
            parameter_payload: ParameterKey::uleb(prepared.audit.token_bytes),
        },
    )?;
    let selected = score < incumbent;
    let bytes = if selected {
        Some(encode_prepared_lz_huffman_policy(
            input, policy, prepared, score,
        )?)
    } else {
        None
    };
    Ok(ConditionalLzPolicyEncoding {
        analysis: LzPolicyAnalysis {
            policy,
            score: Some(score),
            stats: prepared.stats,
        },
        selected,
        bytes,
    })
}

/// Return the exact complete envelope size produced by [`encode`].
pub fn encoded_size(codec: LeafCodec, input: &[u8]) -> Result<usize> {
    u64_usize(
        count_candidate(codec, input)?.encoded_bytes,
        "encoded leaf size",
    )
}

fn allocate_exact(capacity: usize) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    output
        .try_reserve_exact(capacity)
        .map_err(|_| Error::AllocationFailed {
            requested: capacity,
        })?;
    Ok(output)
}

fn write_prefix(output: &mut Vec<u8>, codec: LeafCodec, decoded_bytes: u64, payload_bytes: u64) {
    output.push(FORMAT_VERSION);
    output.push(codec.opcode());
    output.push(0);
    write_u64(output, decoded_bytes);
    write_u64(output, payload_bytes);
}

/// Encode the input with one specified canonical native codec.
pub fn encode(codec: LeafCodec, input: &[u8]) -> Result<Vec<u8>> {
    let decoded_bytes = usize_u64(input.len(), "input length")?;
    let huffman_prepared = if codec == LeafCodec::CanonicalHuffman {
        Some(huffman::prepare(input)?)
    } else {
        None
    };
    let lz_huffman_prepared = if codec == LeafCodec::LzHuffman {
        Some(prepare_lz_huffman(input)?)
    } else {
        None
    };
    let shape = if let Some(prepared) = huffman_prepared {
        let analysis = prepared.analysis();
        Shape {
            payload_bytes: analysis.payload_bytes,
            parameter_payload: ParameterKey::uleb(u64::from(analysis.symbol_count)),
        }
    } else if let Some(prepared) = lz_huffman_prepared {
        Shape {
            payload_bytes: prepared.payload_bytes,
            parameter_payload: ParameterKey::uleb(prepared.token_bytes),
        }
    } else {
        analyse(codec, input)?
    };
    let score = candidate_score(codec, decoded_bytes, shape)?;
    let counted = u64_usize(score.encoded_bytes, "encoded leaf size")?;
    let mut output = allocate_exact(counted)?;
    write_prefix(&mut output, codec, decoded_bytes, shape.payload_bytes);

    match codec {
        LeafCodec::Raw => output.extend_from_slice(input),
        LeafCodec::ByteRle => {
            output.extend_from_slice(shape.parameter_payload.as_bytes());
            let mut start = 0usize;
            while start < input.len() {
                let end = same_byte_run_end(input, start);
                write_u64(&mut output, usize_u64(end - start, "byte RLE run length")?);
                output.push(input[start]);
                start = end;
            }
        }
        LeafCodec::ZeroRun => {
            output.extend_from_slice(shape.parameter_payload.as_bytes());
            let mut start = 0usize;
            while start < input.len() {
                let end = zero_class_run_end(input, start);
                let zero = input[start] == 0;
                output.push(if zero { 0 } else { 1 });
                write_u64(
                    &mut output,
                    usize_u64(end - start, "zero-run token length")?,
                );
                if !zero {
                    output.extend_from_slice(&input[start..end]);
                }
                start = end;
            }
        }
        LeafCodec::SparseZero => {
            output.extend_from_slice(shape.parameter_payload.as_bytes());
            let mut previous = None;
            for (position, &value) in input.iter().enumerate() {
                if value == 0 {
                    continue;
                }
                let delta = match previous {
                    None => position.checked_add(1).ok_or(Error::IntegerOverflow {
                        context: "first sparse position",
                    })?,
                    Some(previous_position) => position - previous_position,
                };
                write_u64(&mut output, usize_u64(delta, "sparse position delta")?);
                output.push(value);
                previous = Some(position);
            }
        }
        LeafCodec::BitPack => {
            let width = bit_width(input);
            output.push(width);
            let packed_length = u64_usize(packed_bytes(decoded_bytes, width)?, "bit-pack payload")?;
            let packed_start = output.len();
            let packed_end =
                packed_start
                    .checked_add(packed_length)
                    .ok_or(Error::IntegerOverflow {
                        context: "bit-pack output length",
                    })?;
            output.resize(packed_end, 0);

            if width != 0 {
                let encoded_so_far = output.len();
                for (index, &value) in input.iter().enumerate() {
                    let bit_index =
                        index
                            .checked_mul(usize::from(width))
                            .ok_or(Error::IntegerOverflow {
                                context: "bit-pack bit index",
                            })?;
                    let byte_index = bit_index / 8;
                    let shift = bit_index % 8;
                    let bits = u16::from(value) << shift;
                    let low = output.get_mut(packed_start + byte_index).ok_or(
                        Error::InternalSizeMismatch {
                            counted,
                            encoded: encoded_so_far,
                        },
                    )?;
                    *low |= bits as u8;
                    if shift + usize::from(width) > 8 {
                        let high = output.get_mut(packed_start + byte_index + 1).ok_or(
                            Error::InternalSizeMismatch {
                                counted,
                                encoded: encoded_so_far,
                            },
                        )?;
                        *high |= (bits >> 8) as u8;
                    }
                }
            }
        }
        LeafCodec::LzTokens => {
            visit_lz_sequences(input, |literal, match_length, distance| {
                write_u64(&mut output, usize_u64(literal.len(), "LZ literal length")?);
                output.extend_from_slice(literal);
                if match_length != 0 {
                    write_u64(
                        &mut output,
                        usize_u64(match_length - LZ_MIN_MATCH, "LZ match length")?,
                    );
                    write_u64(&mut output, usize_u64(distance, "LZ match distance")?);
                }
                Ok(())
            })?;
        }
        LeafCodec::CanonicalHuffman => huffman::write_payload(
            input,
            huffman_prepared.ok_or(Error::InvalidValue("prepared Huffman model is unavailable"))?,
            &mut output,
        )?,
        LeafCodec::LzHuffman => {
            let prepared = lz_huffman_prepared.ok_or(Error::InvalidValue(
                "prepared LZ-Huffman model is unavailable",
            ))?;
            write_u64(&mut output, prepared.token_bytes);
            let mut writer = huffman::start_payload(prepared.huffman, &mut output)?;
            visit_lz_payload_bytes(input, &mut |byte| writer.write_byte(byte))?;
            writer.finish()?;
        }
    }

    if output.len() != counted {
        return Err(Error::InternalSizeMismatch {
            counted,
            encoded: output.len(),
        });
    }
    Ok(output)
}

fn encode_prepared_g1_candidate(
    codec: LeafCodec,
    input: &[u8],
    score: CandidateScore,
    prepared: LzPolicyPrepared,
) -> Result<Vec<u8>> {
    if !matches!(codec, LeafCodec::LzTokens | LeafCodec::LzHuffman) {
        return Err(Error::InvalidValue(
            "prepared G1 emission requires an LZ codec",
        ));
    }
    let decoded_bytes = usize_u64(input.len(), "input length")?;
    let payload_bytes = match codec {
        LeafCodec::LzTokens => prepared.audit.token_bytes,
        LeafCodec::LzHuffman => prepared.payload_bytes,
        _ => unreachable!(),
    };
    let counted = u64_usize(score.encoded_bytes, "encoded G1 leaf size")?;
    let mut output = allocate_exact(counted)?;
    write_prefix(&mut output, codec, decoded_bytes, payload_bytes);

    let (emitted_stats, emitted_audit) = match codec {
        LeafCodec::LzTokens => {
            visit_lz_payload_bytes_with_policy(input, LzParserPolicy::G1, &mut |byte| {
                output.push(byte);
                Ok(())
            })?
        }
        LeafCodec::LzHuffman => {
            write_u64(&mut output, prepared.audit.token_bytes);
            let mut writer = huffman::start_payload(prepared.huffman, &mut output)?;
            let emitted =
                visit_lz_payload_bytes_with_policy(input, LzParserPolicy::G1, &mut |byte| {
                    writer.write_byte(byte)
                })?;
            writer.finish()?;
            emitted
        }
        _ => unreachable!(),
    };
    if emitted_stats != prepared.stats || emitted_audit != prepared.audit {
        return Err(Error::InternalSizeMismatch {
            counted: u64_usize(prepared.audit.token_bytes, "prepared G1 token bytes")?,
            encoded: u64_usize(emitted_audit.token_bytes, "emitted G1 token bytes")?,
        });
    }
    if output.len() != counted {
        return Err(Error::InternalSizeMismatch {
            counted,
            encoded: output.len(),
        });
    }
    Ok(output)
}

/// Count all v1 candidates, apply the complete total order, and encode only
/// the winner. The returned bytes can never exceed the canonical raw envelope.
pub fn encode_best(input: &[u8]) -> Result<BestEncoding> {
    encode_best_audited(input).map(|(encoding, _)| encoding)
}

/// Encode the ordinary v1 winner while reporting exact G1 parser passes.
///
/// The historical implementation walked G1 independently for `LzTokens` and
/// `LzHuffman`, then repeated model preparation during winner emission. This
/// path obtains the raw-token size and Huffman frequencies from one shared
/// preparation walk and reuses the prepared model. It performs a second walk
/// only to emit a winning LZ payload; candidate scores, bytes and decoder wire
/// semantics remain unchanged.
pub fn encode_best_audited(input: &[u8]) -> Result<(BestEncoding, EncodeBestAudit)> {
    let decoded_bytes = usize_u64(input.len(), "input length")?;
    let (prepared, stats) = prepare_lz_huffman_policy(input, LzParserPolicy::G1)?;
    let prepared = prepared.ok_or(Error::InvalidValue(
        "unbounded G1 preparation stopped unexpectedly",
    ))?;
    if stats.budget_exhausted || stats != prepared.stats {
        return Err(Error::InvalidValue(
            "unbounded G1 preparation produced inconsistent stats",
        ));
    }

    let mut best = None;
    for codec in LeafCodec::ALL {
        let shape = match codec {
            LeafCodec::LzTokens => Shape {
                payload_bytes: prepared.audit.token_bytes,
                parameter_payload: ParameterKey::empty(),
            },
            LeafCodec::LzHuffman => Shape {
                payload_bytes: prepared.payload_bytes,
                parameter_payload: ParameterKey::uleb(prepared.audit.token_bytes),
            },
            _ => analyse(codec, input)?,
        };
        let score = candidate_score(codec, decoded_bytes, shape)?;
        let replace = match best.as_ref() {
            None => true,
            Some((_, current)) => score < *current,
        };
        if replace {
            best = Some((codec, score));
        }
    }

    let (codec, score) = best.ok_or(Error::InvalidValue("empty codec catalogue"))?;
    let emission_walks = u64::from(matches!(codec, LeafCodec::LzTokens | LeafCodec::LzHuffman));
    let bytes = if emission_walks == 1 {
        encode_prepared_g1_candidate(codec, input, score, prepared)?
    } else {
        encode(codec, input)?
    };
    if usize_u64(bytes.len(), "encoded leaf size")? != score.encoded_bytes {
        return Err(Error::InternalSizeMismatch {
            counted: u64_usize(score.encoded_bytes, "encoded leaf size")?,
            encoded: bytes.len(),
        });
    }
    Ok((
        BestEncoding {
            codec,
            score,
            bytes,
        },
        EncodeBestAudit {
            g1_preparation_walks: 1,
            g1_emission_walks: emission_walks,
            g1_parser_walks: checked_add(1, emission_walks, "G1 parser walks")?,
            g1_sequence_count: prepared.stats.sequence_count,
            g1_token_bytes: prepared.audit.token_bytes,
            g1_token_fingerprint: prepared.audit.fingerprint,
        },
    ))
}

/// Add one explicitly configured parser policy to the ordinary native leaf
/// competition without changing [`encode_best`] or opcode `0x07` semantics.
///
/// Score ties retain the ordinary catalogue winner. This keeps Fast/G1 output
/// byte-identical and makes the additional policy strictly size-non-regressive.
pub fn encode_best_with_lz_policy(
    input: &[u8],
    policy: LzParserPolicy,
) -> Result<LzPolicyCompetition> {
    let baseline = encode_best(input)?;
    let experimental = encode_lz_huffman_policy_if_better(input, policy, baseline.score)?;
    let policy_analysis = experimental.analysis;
    let policy_selected = experimental.selected;
    let per_walk_work = policy_analysis.stats.work_units()?;
    // Reserve the same two-walk work charge for every complete policy as the
    // original eager implementation. Avoiding losing emission is a runtime
    // optimization, not a larger deterministic search budget.
    let policy_walks = if policy_analysis.score.is_some() {
        2
    } else {
        1
    };
    let policy_work_units = checked_mul(per_walk_work, policy_walks, "LZ policy competition work")?;
    let encoding = if policy_selected {
        BestEncoding {
            codec: LeafCodec::LzHuffman,
            score: policy_analysis.score.ok_or(Error::InvalidValue(
                "selected LZ policy has no complete score",
            ))?,
            bytes: experimental.bytes.ok_or(Error::InvalidValue(
                "selected LZ policy has no complete envelope",
            ))?,
        }
    } else {
        baseline
    };
    Ok(LzPolicyCompetition {
        encoding,
        policy,
        policy_selected,
        policy_analysis,
        policy_work_units,
    })
}

#[derive(Clone, Copy, Debug)]
struct Envelope<'a> {
    codec: LeafCodec,
    decoded_bytes: u64,
    payload: &'a [u8],
    metadata: LeafMetadata,
}

fn parse_envelope<'a>(input: &'a [u8], limits: DecodeLimits) -> Result<Envelope<'a>> {
    let encoded_bytes = usize_u64(input.len(), "encoded input length")?;
    if encoded_bytes > limits.max_encoded_bytes {
        return Err(Error::LimitExceeded {
            what: "encoded bytes",
            actual: encoded_bytes,
            limit: limits.max_encoded_bytes,
        });
    }

    let mut cursor = Cursor::new(input);
    let version = cursor.read_u8("leaf format version")?;
    if version != FORMAT_VERSION {
        return Err(Error::UnsupportedVersion(version));
    }
    let codec = LeafCodec::try_from(cursor.read_u8("leaf codec opcode")?)?;
    let flags = cursor.read_u8("leaf flags")?;
    if flags != 0 {
        return Err(Error::InvalidFlags(flags));
    }
    let decoded_bytes = cursor.read_u64()?;
    if decoded_bytes > limits.max_output_bytes {
        return Err(Error::LimitExceeded {
            what: "decoded bytes",
            actual: decoded_bytes,
            limit: limits.max_output_bytes,
        });
    }
    let payload_bytes = cursor.read_u64()?;
    let payload_length = u64_usize(payload_bytes, "leaf payload length")?;
    let payload = cursor.read_exact(payload_length, "leaf payload")?;
    cursor.finish("leaf envelope")?;

    let work = decode_work(codec, decoded_bytes, payload_bytes)?;
    if work > limits.max_work {
        return Err(Error::LimitExceeded {
            what: "decode work",
            actual: work,
            limit: limits.max_work,
        });
    }

    Ok(Envelope {
        codec,
        decoded_bytes,
        payload,
        metadata: LeafMetadata {
            codec,
            encoded_bytes,
            decoded_bytes,
            payload_bytes,
            decode_work: work,
            temporary_bytes: 0,
        },
    })
}

fn process_raw(payload: &[u8], output_length: usize, output: Option<&mut [u8]>) -> Result<()> {
    if payload.len() != output_length {
        return Err(Error::LengthMismatch {
            context: "raw payload",
            expected: usize_u64(output_length, "raw output length")?,
            actual: usize_u64(payload.len(), "raw payload length")?,
        });
    }
    if let Some(destination) = output {
        destination.copy_from_slice(payload);
    }
    Ok(())
}

fn process_rle(payload: &[u8], output_length: usize, mut output: Option<&mut [u8]>) -> Result<()> {
    let mut cursor = Cursor::new(payload);
    let run_count = cursor.read_usize("byte RLE run count")?;
    if output_length == 0 {
        if run_count != 0 {
            return Err(Error::NonCanonical(
                "non-zero RLE run count for empty output",
            ));
        }
    } else if run_count == 0 || run_count > output_length {
        return Err(Error::InvalidValue(
            "RLE run count is inconsistent with output",
        ));
    }

    let mut written = 0usize;
    let mut previous = None;
    for _ in 0..run_count {
        let length = cursor.read_usize("byte RLE run length")?;
        if length == 0 {
            return Err(Error::NonCanonical("zero-length RLE run"));
        }
        let value = cursor.read_u8("byte RLE value")?;
        if previous == Some(value) {
            return Err(Error::NonCanonical("adjacent equal RLE runs"));
        }
        let new_length = written.checked_add(length).ok_or(Error::IntegerOverflow {
            context: "RLE decoded length",
        })?;
        if new_length > output_length {
            return Err(Error::LengthMismatch {
                context: "byte RLE output",
                expected: usize_u64(output_length, "RLE output length")?,
                actual: usize_u64(new_length, "RLE decoded length")?,
            });
        }
        if let Some(destination) = output.as_deref_mut() {
            destination[written..new_length].fill(value);
        }
        written = new_length;
        previous = Some(value);
    }
    cursor.finish("byte RLE payload")?;
    if written != output_length {
        return Err(Error::LengthMismatch {
            context: "byte RLE output",
            expected: usize_u64(output_length, "RLE output length")?,
            actual: usize_u64(written, "RLE decoded length")?,
        });
    }
    Ok(())
}

fn process_zero_run(
    payload: &[u8],
    output_length: usize,
    mut output: Option<&mut [u8]>,
) -> Result<()> {
    let mut cursor = Cursor::new(payload);
    let token_count = cursor.read_usize("zero-run token count")?;
    if output_length == 0 {
        if token_count != 0 {
            return Err(Error::NonCanonical(
                "non-zero zero-run token count for empty output",
            ));
        }
    } else if token_count == 0 || token_count > output_length {
        return Err(Error::InvalidValue(
            "zero-run token count is inconsistent with output",
        ));
    }

    let mut written = 0usize;
    let mut previous_tag = None;
    for _ in 0..token_count {
        let tag = cursor.read_u8("zero-run token tag")?;
        if tag > 1 {
            return Err(Error::InvalidValue("unknown zero-run token tag"));
        }
        if previous_tag == Some(tag) {
            return Err(Error::NonCanonical("adjacent zero-run tokens of same kind"));
        }
        let length = cursor.read_usize("zero-run token length")?;
        if length == 0 {
            return Err(Error::NonCanonical("zero-length zero-run token"));
        }
        let new_length = written.checked_add(length).ok_or(Error::IntegerOverflow {
            context: "zero-run decoded length",
        })?;
        if new_length > output_length {
            return Err(Error::LengthMismatch {
                context: "zero-run output",
                expected: usize_u64(output_length, "zero-run output length")?,
                actual: usize_u64(new_length, "zero-run decoded length")?,
            });
        }
        if tag == 0 {
            if let Some(destination) = output.as_deref_mut() {
                destination[written..new_length].fill(0);
            }
        } else {
            let literal = cursor.read_exact(length, "zero-run literal bytes")?;
            if literal.contains(&0) {
                return Err(Error::NonCanonical(
                    "zero byte stored inside non-zero literal run",
                ));
            }
            if let Some(destination) = output.as_deref_mut() {
                destination[written..new_length].copy_from_slice(literal);
            }
        }
        written = new_length;
        previous_tag = Some(tag);
    }
    cursor.finish("zero-run payload")?;
    if written != output_length {
        return Err(Error::LengthMismatch {
            context: "zero-run output",
            expected: usize_u64(output_length, "zero-run output length")?,
            actual: usize_u64(written, "zero-run decoded length")?,
        });
    }
    Ok(())
}

fn process_sparse(
    payload: &[u8],
    output_length: usize,
    mut output: Option<&mut [u8]>,
) -> Result<()> {
    let mut cursor = Cursor::new(payload);
    let exception_count = cursor.read_usize("sparse exception count")?;
    if exception_count > output_length {
        return Err(Error::InvalidValue(
            "sparse exception count exceeds output length",
        ));
    }
    if let Some(destination) = output.as_deref_mut() {
        destination.fill(0);
    }
    let mut previous = None;

    for _ in 0..exception_count {
        let delta = cursor.read_u64()?;
        if delta == 0 {
            return Err(Error::NonCanonical("zero sparse position delta"));
        }
        let position_u64 = match previous {
            None => delta - 1,
            Some(previous_position) => checked_add(previous_position, delta, "sparse position")?,
        };
        let position = u64_usize(position_u64, "sparse position")?;
        if position >= output_length {
            return Err(Error::InvalidValue("sparse position outside output"));
        }
        let value = cursor.read_u8("sparse exception value")?;
        if value == 0 {
            return Err(Error::NonCanonical("explicit zero sparse exception"));
        }
        if let Some(destination) = output.as_deref_mut() {
            destination[position] = value;
        }
        previous = Some(position_u64);
    }
    cursor.finish("sparse-zero payload")?;
    Ok(())
}

fn packed_value(packed: &[u8], index: usize, width: u8) -> Result<u8> {
    let bit_index = index
        .checked_mul(usize::from(width))
        .ok_or(Error::IntegerOverflow {
            context: "bit-pack bit index",
        })?;
    let byte_index = bit_index / 8;
    let shift = bit_index % 8;
    let low = u16::from(*packed.get(byte_index).ok_or(Error::Truncated {
        context: "packed bits",
        position: byte_index,
    })?);
    let high = packed
        .get(byte_index + 1)
        .copied()
        .map(u16::from)
        .unwrap_or(0);
    let value_mask = if width == 8 {
        u16::from(u8::MAX)
    } else {
        (1u16 << width) - 1
    };
    Ok((((low | (high << 8)) >> shift) & value_mask) as u8)
}

fn process_bit_pack(
    payload: &[u8],
    output_length: usize,
    mut output: Option<&mut [u8]>,
) -> Result<()> {
    let mut cursor = Cursor::new(payload);
    let width = cursor.read_u8("bit-pack width")?;
    if width > 8 {
        return Err(Error::InvalidValue("bit-pack width exceeds eight"));
    }
    let output_u64 = usize_u64(output_length, "bit-pack output length")?;
    let packed_length = u64_usize(packed_bytes(output_u64, width)?, "bit-pack payload length")?;
    let packed = cursor.read_exact(packed_length, "packed bits")?;
    cursor.finish("bit-pack payload")?;

    let total_bits =
        output_length
            .checked_mul(usize::from(width))
            .ok_or(Error::IntegerOverflow {
                context: "bit-pack padding position",
            })?;
    let used_last_bits = total_bits % 8;
    if used_last_bits != 0 {
        let last = packed.last().copied().ok_or(Error::Truncated {
            context: "packed bits",
            position: 0,
        })?;
        let unused_mask = u8::MAX << used_last_bits;
        if last & unused_mask != 0 {
            return Err(Error::NonCanonical("non-zero bit-pack padding"));
        }
    }

    let mut maximum = 0u8;
    if width == 0 {
        if let Some(destination) = output.as_deref_mut() {
            destination.fill(0);
        }
    } else {
        for index in 0..output_length {
            let value = packed_value(packed, index, width)?;
            maximum = maximum.max(value);
            if let Some(destination) = output.as_deref_mut() {
                destination[index] = value;
            }
        }
    }

    let minimum_width = if maximum == 0 {
        0
    } else {
        (u8::BITS - maximum.leading_zeros()) as u8
    };
    if minimum_width != width {
        return Err(Error::NonCanonical("bit-pack width is not minimal"));
    }
    Ok(())
}

fn process_lz_tokens(
    payload: &[u8],
    output_length: usize,
    mut output: Option<&mut [u8]>,
) -> Result<()> {
    if output_length == 0 {
        if payload.is_empty() {
            return Ok(());
        }
        return Err(Error::NonCanonical("non-empty LZ payload for empty output"));
    }
    if payload.is_empty() {
        return Err(Error::LengthMismatch {
            context: "LZ token output",
            expected: usize_u64(output_length, "LZ output length")?,
            actual: 0,
        });
    }

    let mut cursor = Cursor::new(payload);
    let mut written = 0usize;
    while cursor.remaining() != 0 {
        let literal_length = cursor.read_usize("LZ literal length")?;
        let literal_end = written
            .checked_add(literal_length)
            .ok_or(Error::IntegerOverflow {
                context: "LZ literal output length",
            })?;
        if literal_end > output_length {
            return Err(Error::LengthMismatch {
                context: "LZ literal output",
                expected: usize_u64(output_length, "LZ output length")?,
                actual: usize_u64(literal_end, "LZ literal output length")?,
            });
        }
        let literal = cursor.read_exact(literal_length, "LZ literal bytes")?;
        if let Some(destination) = output.as_deref_mut() {
            destination[written..literal_end].copy_from_slice(literal);
        }
        written = literal_end;

        if cursor.remaining() == 0 {
            if literal_length == 0 {
                return Err(Error::NonCanonical("empty terminal LZ literal"));
            }
            break;
        }

        let encoded_match_length = cursor.read_u64()?;
        let match_length_u64 =
            checked_add(encoded_match_length, LZ_MIN_MATCH as u64, "LZ match length")?;
        let match_length = u64_usize(match_length_u64, "LZ match length")?;
        let distance_u64 = cursor.read_u64()?;
        if distance_u64 == 0 {
            return Err(Error::NonCanonical("zero LZ match distance"));
        }
        if distance_u64 > LZ_MAX_DISTANCE as u64 {
            return Err(Error::InvalidValue("LZ match distance exceeds 65535"));
        }
        let distance = u64_usize(distance_u64, "LZ match distance")?;
        if distance > written {
            return Err(Error::InvalidValue(
                "LZ match distance exceeds restored prefix",
            ));
        }
        let match_end = written
            .checked_add(match_length)
            .ok_or(Error::IntegerOverflow {
                context: "LZ match output length",
            })?;
        if match_end > output_length {
            return Err(Error::LengthMismatch {
                context: "LZ match output",
                expected: usize_u64(output_length, "LZ output length")?,
                actual: usize_u64(match_end, "LZ match output length")?,
            });
        }
        if let Some(destination) = output.as_deref_mut() {
            for target in written..match_end {
                let source = target - distance;
                destination[target] = destination[source];
            }
        }
        written = match_end;
    }
    cursor.finish("LZ token payload")?;
    if written != output_length {
        return Err(Error::LengthMismatch {
            context: "LZ token output",
            expected: usize_u64(output_length, "LZ output length")?,
            actual: usize_u64(written, "LZ decoded length")?,
        });
    }
    Ok(())
}

struct HuffmanLzReader<'a> {
    decoder: huffman::PayloadDecoder<'a>,
    remaining: usize,
    position: usize,
}

impl HuffmanLzReader<'_> {
    fn read_u8(&mut self, context: &'static str) -> Result<u8> {
        if self.remaining == 0 {
            return Err(Error::Truncated {
                context,
                position: self.position,
            });
        }
        let byte = self.decoder.next_symbol()?.ok_or(Error::Truncated {
            context,
            position: self.position,
        })?;
        self.remaining -= 1;
        self.position += 1;
        Ok(byte)
    }

    fn read_u64(&mut self) -> Result<u64> {
        let start = self.position;
        let mut value = 0u64;
        for index in 0..10 {
            let byte = self.read_u8("LZ-Huffman unsigned LEB128 integer")?;
            if index == 9 && byte > 1 {
                return Err(Error::InvalidVarint { position: start });
            }
            value |= u64::from(byte & 0x7f) << (index * 7);
            if byte & 0x80 == 0 {
                if usize::from(varint_len(value)) != self.position - start {
                    return Err(Error::InvalidVarint { position: start });
                }
                return Ok(value);
            }
        }
        Err(Error::InvalidVarint { position: start })
    }

    fn read_usize(&mut self, context: &'static str) -> Result<usize> {
        u64_usize(self.read_u64()?, context)
    }

    fn finish(self) -> Result<()> {
        if self.remaining != 0 {
            return Err(Error::TrailingData {
                context: "LZ-Huffman token stream",
                remaining: self.remaining,
            });
        }
        self.decoder.finish()
    }
}

fn process_lz_huffman(
    payload: &[u8],
    output_length: usize,
    mut output: Option<&mut [u8]>,
) -> Result<()> {
    let mut cursor = Cursor::new(payload);
    let token_bytes = cursor.read_u64()?;
    let output_bytes = usize_u64(output_length, "LZ-Huffman output length")?;
    let token_bound = lz_token_bytes_bound(output_bytes)?;
    if token_bytes > token_bound {
        return Err(Error::LimitExceeded {
            what: "LZ-Huffman token bytes",
            actual: token_bytes,
            limit: token_bound,
        });
    }
    let token_length = u64_usize(token_bytes, "LZ-Huffman token length")?;
    let huffman_payload =
        cursor.read_exact(cursor.remaining(), "LZ-Huffman canonical Huffman payload")?;
    cursor.finish("LZ-Huffman payload")?;
    let decoder = huffman::payload_decoder(huffman_payload, token_length)?;
    let mut reader = HuffmanLzReader {
        decoder,
        remaining: token_length,
        position: 0,
    };

    if output_length == 0 {
        if token_length != 0 {
            return Err(Error::NonCanonical(
                "non-empty LZ-Huffman token stream for empty output",
            ));
        }
        return reader.finish();
    }
    if token_length == 0 {
        return Err(Error::LengthMismatch {
            context: "LZ-Huffman token output",
            expected: output_bytes,
            actual: 0,
        });
    }

    let mut written = 0usize;
    while reader.remaining != 0 {
        let literal_length = reader.read_usize("LZ-Huffman literal length")?;
        let literal_end = written
            .checked_add(literal_length)
            .ok_or(Error::IntegerOverflow {
                context: "LZ-Huffman literal output length",
            })?;
        if literal_end > output_length {
            return Err(Error::LengthMismatch {
                context: "LZ-Huffman literal output",
                expected: output_bytes,
                actual: usize_u64(literal_end, "LZ-Huffman literal output length")?,
            });
        }
        for target in written..literal_end {
            let byte = reader.read_u8("LZ-Huffman literal bytes")?;
            if let Some(destination) = output.as_deref_mut() {
                destination[target] = byte;
            }
        }
        written = literal_end;

        if reader.remaining == 0 {
            if literal_length == 0 {
                return Err(Error::NonCanonical("empty terminal LZ-Huffman literal"));
            }
            break;
        }

        let encoded_match_length = reader.read_u64()?;
        let match_length_u64 = checked_add(
            encoded_match_length,
            LZ_MIN_MATCH as u64,
            "LZ-Huffman match length",
        )?;
        let match_length = u64_usize(match_length_u64, "LZ-Huffman match length")?;
        let distance_u64 = reader.read_u64()?;
        if distance_u64 == 0 {
            return Err(Error::NonCanonical("zero LZ-Huffman match distance"));
        }
        if distance_u64 > LZ_MAX_DISTANCE as u64 {
            return Err(Error::InvalidValue(
                "LZ-Huffman match distance exceeds 65535",
            ));
        }
        let distance = u64_usize(distance_u64, "LZ-Huffman match distance")?;
        if distance > written {
            return Err(Error::InvalidValue(
                "LZ-Huffman match distance exceeds restored prefix",
            ));
        }
        let match_end = written
            .checked_add(match_length)
            .ok_or(Error::IntegerOverflow {
                context: "LZ-Huffman match output length",
            })?;
        if match_end > output_length {
            return Err(Error::LengthMismatch {
                context: "LZ-Huffman match output",
                expected: output_bytes,
                actual: usize_u64(match_end, "LZ-Huffman match output length")?,
            });
        }
        if let Some(destination) = output.as_deref_mut() {
            for target in written..match_end {
                let source = target - distance;
                destination[target] = destination[source];
            }
        }
        written = match_end;
    }

    reader.finish()?;
    if written != output_length {
        return Err(Error::LengthMismatch {
            context: "LZ-Huffman token output",
            expected: output_bytes,
            actual: usize_u64(written, "LZ-Huffman decoded length")?,
        });
    }
    Ok(())
}

fn process_envelope(envelope: Envelope<'_>, output: Option<&mut [u8]>) -> Result<()> {
    let output_length = u64_usize(envelope.decoded_bytes, "decoded leaf length")?;
    match envelope.codec {
        LeafCodec::Raw => process_raw(envelope.payload, output_length, output),
        LeafCodec::ByteRle => process_rle(envelope.payload, output_length, output),
        LeafCodec::ZeroRun => process_zero_run(envelope.payload, output_length, output),
        LeafCodec::SparseZero => process_sparse(envelope.payload, output_length, output),
        LeafCodec::BitPack => process_bit_pack(envelope.payload, output_length, output),
        LeafCodec::LzTokens => process_lz_tokens(envelope.payload, output_length, output),
        LeafCodec::CanonicalHuffman => huffman::process(envelope.payload, output_length, output),
        LeafCodec::LzHuffman => process_lz_huffman(envelope.payload, output_length, output),
    }
}

fn require_expected_length(envelope: Envelope<'_>, expected: Option<u64>) -> Result<()> {
    if let Some(expected) = expected {
        if envelope.decoded_bytes != expected {
            return Err(Error::LengthMismatch {
                context: "expected decoded leaf length",
                expected,
                actual: envelope.decoded_bytes,
            });
        }
    }
    Ok(())
}

fn inspect_internal(
    input: &[u8],
    expected_output_length: Option<u64>,
    limits: DecodeLimits,
) -> Result<(Envelope<'_>, LeafMetadata)> {
    let envelope = parse_envelope(input, limits)?;
    require_expected_length(envelope, expected_output_length)?;
    process_envelope(envelope, None)?;
    Ok((envelope, envelope.metadata))
}

/// Parse the bounded outer leaf envelope and return its declared resource
/// metadata without walking the codec payload.
///
/// This is deliberately weaker than [`inspect`]: it is intended for a second
/// resource/layout pass after the same envelope has already passed strict
/// validation. Callers handling untrusted bytes for the first time must use
/// [`inspect`] or one of the decoding functions instead.
pub fn preflight(input: &[u8], limits: DecodeLimits) -> Result<LeafMetadata> {
    parse_envelope(input, limits).map(|envelope| envelope.metadata)
}

/// Resource-only envelope preflight with an exact decoded-length check.
///
/// As with [`preflight`], this does not validate the codec payload itself.
pub fn preflight_exact(
    input: &[u8],
    expected_output_length: u64,
    limits: DecodeLimits,
) -> Result<LeafMetadata> {
    let envelope = parse_envelope(input, limits)?;
    require_expected_length(envelope, Some(expected_output_length))?;
    Ok(envelope.metadata)
}

fn decode_internal(
    input: &[u8],
    expected_output_length: Option<u64>,
    limits: DecodeLimits,
) -> Result<DecodedLeaf> {
    let (envelope, metadata) = inspect_internal(input, expected_output_length, limits)?;
    let output_length = u64_usize(envelope.decoded_bytes, "decoded leaf length")?;
    let mut bytes = allocate_exact(output_length)?;
    bytes.resize(output_length, 0);
    process_envelope(envelope, Some(&mut bytes))?;
    Ok(DecodedLeaf {
        codec: metadata.codec,
        bytes,
    })
}

/// Inspect and fully validate one canonical envelope without allocating or
/// materialising its decoded output.
pub fn inspect(input: &[u8], limits: DecodeLimits) -> Result<LeafMetadata> {
    inspect_internal(input, None, limits).map(|(_, metadata)| metadata)
}

/// Inspect a canonical envelope while requiring an exact decoded length.
pub fn inspect_exact(
    input: &[u8],
    expected_output_length: u64,
    limits: DecodeLimits,
) -> Result<LeafMetadata> {
    inspect_internal(input, Some(expected_output_length), limits).map(|(_, metadata)| metadata)
}

fn decode_into_internal(
    input: &[u8],
    expected_output_length: Option<u64>,
    output: &mut [u8],
    limits: DecodeLimits,
) -> Result<LeafMetadata> {
    let (envelope, metadata) = inspect_internal(input, expected_output_length, limits)?;
    let actual = usize_u64(output.len(), "decoded destination length")?;
    if actual != metadata.decoded_bytes {
        return Err(Error::LengthMismatch {
            context: "decoded destination length",
            expected: metadata.decoded_bytes,
            actual,
        });
    }
    process_envelope(envelope, Some(output))?;
    Ok(metadata)
}

/// Decode a fully validated envelope directly into a caller-owned slice.
///
/// No heap allocation is performed. The destination is not modified unless
/// the complete envelope and codec payload are canonical.
pub fn decode_into(input: &[u8], output: &mut [u8], limits: DecodeLimits) -> Result<LeafMetadata> {
    decode_into_internal(input, None, output, limits)
}

/// Direct-slice decode with an additional containing-node length check.
pub fn decode_exact_into(
    input: &[u8],
    expected_output_length: u64,
    output: &mut [u8],
    limits: DecodeLimits,
) -> Result<LeafMetadata> {
    decode_into_internal(input, Some(expected_output_length), output, limits)
}

/// Decode and strictly validate an envelope in one payload walk into a
/// caller-owned, uncommitted destination.
///
/// Unlike [`decode_exact_into`], an error may leave `output` partially
/// modified. This API is therefore only for private buffers that are discarded
/// on error and published after the containing archive's hashes also pass. On
/// success it performs the same canonical codec validation and exact-length
/// checks as the ordinary decoder, without the preliminary validation walk.
pub fn decode_exact_into_uncommitted(
    input: &[u8],
    expected_output_length: u64,
    output: &mut [u8],
    limits: DecodeLimits,
) -> Result<LeafMetadata> {
    let envelope = parse_envelope(input, limits)?;
    require_expected_length(envelope, Some(expected_output_length))?;
    let actual = usize_u64(output.len(), "decoded destination length")?;
    if actual != envelope.metadata.decoded_bytes {
        return Err(Error::LengthMismatch {
            context: "decoded destination length",
            expected: envelope.metadata.decoded_bytes,
            actual,
        });
    }
    process_envelope(envelope, Some(output))?;
    Ok(envelope.metadata)
}

/// Decode one complete native leaf envelope under explicit resource limits.
///
/// The returned output always has exactly the envelope's declared length, and
/// both the envelope and codec payload must be consumed without trailing data.
pub fn decode(input: &[u8], limits: DecodeLimits) -> Result<DecodedLeaf> {
    decode_internal(input, None, limits)
}

/// Decode while also requiring the containing DSL node's exact output length.
///
/// The expected length is checked before output allocation.
pub fn decode_exact(
    input: &[u8],
    expected_output_length: u64,
    limits: DecodeLimits,
) -> Result<DecodedLeaf> {
    decode_internal(input, Some(expected_output_length), limits)
}
