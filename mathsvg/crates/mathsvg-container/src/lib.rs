//! Strict, seekable/in-memory implementation of the frozen MathSVG v1 envelope.
//!
//! The container authenticates structure only with public hashes/checksums; it
//! is not a cryptographic authenticity layer. [`decode_archive`] verifies the
//! complete physical envelope, every block payload, and each parsed
//! [`Program`]. Restored-content hashes remain explicitly pending until
//! [`DecodedArchive::verify_restored_with`] is called with an evaluator.
//!
//! This MVP accepts an in-memory archive and retains parsed block programs.
//! All allocations are bounded by the actual input plus configured and hard v1
//! limits. A later streaming crate can spool the directory and output while
//! reusing these exact wire checks.

use std::fmt;
use std::io::{Read, Seek, SeekFrom, Write};

use crc32fast::hash as crc32;
use mathsvg_core::{checked_u64_add, checked_u64_mul, Error, Limits, Result};
use mathsvg_dsl::{Program, ValidationReport, ValueType};
use sha2::{Digest, Sha256};

pub const FILE_HEADER_BYTES: usize = 128;
pub const DIRECTORY_RECORD_BYTES: usize = 144;
pub const BLOCK_HEADER_BYTES: usize = 72;
pub const FILE_FOOTER_BYTES: usize = 128;

pub const CONTAINER_VERSION: u16 = 1;
pub const DSL_VERSION: u16 = 1;
pub const ROOT_VALUE_TYPE_BYTES: u16 = 0;

pub const V1_MAX_BLOCK_OUTPUT_BYTES: u64 = 1 << 24;
pub const V1_MAX_BLOCK_PAYLOAD_BYTES: u64 = 1 << 25;
pub const V1_MAX_DEFINITIONS: u64 = 1 << 18;
pub const V1_MAX_NODES: u64 = 1 << 20;
pub const V1_MAX_EDGES: u64 = 1 << 21;
pub const V1_MAX_GRAPH_DEPTH: u64 = 256;
pub const V1_MAX_WORKSPACE_BYTES: u64 = 1 << 26;
pub const V1_MAX_WORK_UNITS: u64 = 1 << 40;

const FILE_MAGIC: &[u8; 4] = b"MSVG";
const BLOCK_MAGIC: &[u8; 4] = b"MSBL";
const FOOTER_MAGIC: &[u8; 4] = b"MSFT";
const MIN_ARCHIVE_BYTES: u64 = (FILE_HEADER_BYTES + FILE_FOOTER_BYTES) as u64;
const PER_BLOCK_ENVELOPE_BYTES: u64 = (DIRECTORY_RECORD_BYTES + BLOCK_HEADER_BYTES) as u64;

/// A SHA-256 digest in canonical byte order.
pub type Sha256Digest = [u8; 32];

/// Error returned by the streaming envelope APIs.
///
/// Format and resource failures retain the exact native MathSVG error. I/O
/// failures are kept separate so callers can distinguish a hostile archive
/// from a failed input, spool, or output device.
#[derive(Debug)]
pub enum StreamError {
    Format(Error),
    Io(std::io::Error),
}

impl fmt::Display for StreamError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Format(error) => write!(formatter, "{error}"),
            Self::Io(error) => write!(formatter, "archive stream I/O error: {error}"),
        }
    }
}

impl std::error::Error for StreamError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Format(error) => Some(error),
            Self::Io(error) => Some(error),
        }
    }
}

impl From<Error> for StreamError {
    fn from(error: Error) -> Self {
        Self::Format(error)
    }
}

impl From<std::io::Error> for StreamError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

pub type StreamResult<T> = std::result::Result<T, StreamError>;

/// Resource facts from a completed streaming encode.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StreamEncodeReport {
    pub archive_bytes: u64,
    pub original_size: u64,
    pub original_sha256: Sha256Digest,
    pub block_count: u64,
    pub total_node_count: u64,
    pub total_work_units: u64,
    /// Largest temporary one-block archive used while preparing the payload
    /// spool. This excludes the caller-owned input block and spool storage.
    pub maximum_prepared_block_bytes: u64,
    /// Metadata retained in memory is one fixed-size directory record per
    /// block. This value makes that bound directly auditable.
    pub retained_directory_bytes: u64,
}

/// Resource facts returned only after a streaming decode has verified the
/// complete envelope and restored SHA-256.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StreamDecodeReport {
    pub verified: VerifiedArchive,
    pub archive_bytes: u64,
    /// Largest encoded block payload held at once.
    pub maximum_payload_buffer_bytes: u64,
    /// Largest restored block passed to the uncommitted output sink.
    pub maximum_restored_buffer_bytes: u64,
    /// The streaming decoder rereads fixed records from the seekable input and
    /// therefore retains no directory-sized allocation.
    pub retained_directory_bytes: u64,
}

/// One independent, closed block supplied to the archive encoder.
///
/// `restored` is used for the block and whole-file SHA-256 values. The
/// container verifies that its length equals the program's declared output.
/// Matching its contents to procedural evaluation is the caller's encoder
/// responsibility; strict decoding checks that match via an evaluator.
#[derive(Clone, Copy, Debug)]
pub struct ArchiveBlock<'a> {
    pub program: &'a Program,
    pub restored: &'a [u8],
}

/// Redundant, fully checked metadata for one v1 block.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BlockMetadata {
    pub original_offset: u64,
    pub original_length: u64,
    pub payload_offset: u64,
    pub payload_length: u64,
    pub definition_count: u32,
    pub node_count: u32,
    pub edge_count: u32,
    pub max_graph_depth: u16,
    pub work_units: u64,
    pub workspace_bytes: u64,
    pub restored_sha256: Sha256Digest,
    pub payload_sha256: Sha256Digest,
    pub payload_crc32: u32,
}

/// A block whose envelope, payload and program syntax are verified.
///
/// `restored_sha256` cannot be checked until the program is evaluated.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DecodedBlock {
    pub metadata: BlockMetadata,
    pub program: Program,
}

/// A strict structural decode with restored-content verification still pending.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DecodedArchive {
    pub original_size: u64,
    pub original_sha256: Sha256Digest,
    pub total_node_count: u64,
    pub total_work_units: u64,
    pub blocks: Vec<DecodedBlock>,
}

/// Final archive facts returned only after evaluated restored hashes match.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct VerifiedArchive {
    pub original_size: u64,
    pub original_sha256: Sha256Digest,
    pub block_count: u64,
    pub total_node_count: u64,
    pub total_work_units: u64,
}

impl DecodedArchive {
    /// Evaluate every closed block, verify its length/SHA-256 and then verify
    /// the complete restored stream.
    ///
    /// `on_verified_block` runs only after that block's length and hash match.
    /// At callback time the final whole-archive hash remains pending, exactly
    /// as required for a streaming consumer. A strict file decoder should
    /// therefore write callbacks to an uncommitted spool and commit only after
    /// this method returns `Ok`.
    pub fn verify_restored_with<E, C>(
        &self,
        mut evaluate: E,
        mut on_verified_block: C,
    ) -> Result<VerifiedArchive>
    where
        E: FnMut(&Program) -> Result<Vec<u8>>,
        C: FnMut(usize, &[u8]) -> Result<()>,
    {
        let mut complete_hash = Sha256::new();
        let mut total_bytes = 0u64;

        for (index, block) in self.blocks.iter().enumerate() {
            let restored = evaluate(&block.program)?;
            let restored_length = usize_to_u64(restored.len(), "evaluated block length")?;
            require_equal(
                "evaluated block length",
                block.metadata.original_length,
                restored_length,
            )?;
            if sha256(&restored) != block.metadata.restored_sha256 {
                return Err(Error::InvalidValue("restored block SHA-256 mismatch"));
            }
            total_bytes = checked_u64_add(total_bytes, restored_length, "restored output bytes")?;
            complete_hash.update(&restored);
            on_verified_block(index, &restored)?;
        }

        require_equal("complete restored length", self.original_size, total_bytes)?;
        let complete_hash: Sha256Digest = complete_hash.finalize().into();
        if complete_hash != self.original_sha256 {
            return Err(Error::InvalidValue("complete restored SHA-256 mismatch"));
        }

        Ok(VerifiedArchive {
            original_size: self.original_size,
            original_sha256: self.original_sha256,
            block_count: usize_to_u64(self.blocks.len(), "verified block count")?,
            total_node_count: self.total_node_count,
            total_work_units: self.total_work_units,
        })
    }
}

#[derive(Debug)]
struct PreparedBlock {
    payload: Vec<u8>,
    metadata: BlockMetadata,
}

fn prepare_block(
    block: ArchiveBlock<'_>,
    original_offset: u64,
    payload_offset: u64,
    limits: &Limits,
) -> Result<PreparedBlock> {
    let (sections, report) = block.program.encode_sections_with_report(limits)?;
    let original_length = usize_to_u64(block.restored.len(), "restored block length")?;
    if original_length == 0 {
        return Err(Error::InvalidValue(
            "non-empty archive blocks must restore at least one byte",
        ));
    }
    limits.check(
        "block output bytes",
        original_length,
        limits.max_block_output_bytes,
    )?;
    require_equal(
        "program and restored block length",
        report.original_bytes,
        original_length,
    )?;
    require_bytes_root(&report)?;

    let definition_bytes =
        u32::try_from(sections.definitions.len()).map_err(|_| Error::IntegerOverflow {
            context: "definition section length",
        })?;
    let root_bytes = u32::try_from(sections.root.len()).map_err(|_| Error::IntegerOverflow {
        context: "root section length",
    })?;
    let definition_count =
        u32::try_from(block.program.definitions.len()).map_err(|_| Error::IntegerOverflow {
            context: "definition count",
        })?;
    let node_count = u32::try_from(report.node_count).map_err(|_| Error::IntegerOverflow {
        context: "node count",
    })?;
    let edge_count = u32::try_from(report.edge_count).map_err(|_| Error::IntegerOverflow {
        context: "edge count",
    })?;
    let max_graph_depth =
        u16::try_from(report.graph_depth).map_err(|_| Error::IntegerOverflow {
            context: "graph depth",
        })?;

    let block_header = encode_block_header(
        original_length,
        definition_count,
        node_count,
        edge_count,
        max_graph_depth,
        report.decode_work,
        report.temporary_bytes,
        definition_bytes,
        root_bytes,
    );
    let payload_length = checked_u64_add(
        BLOCK_HEADER_BYTES as u64,
        checked_u64_add(
            u64::from(definition_bytes),
            u64::from(root_bytes),
            "block DSL bytes",
        )?,
        "block payload bytes",
    )?;
    limits.check(
        "block payload bytes",
        payload_length,
        limits.max_block_payload_bytes,
    )?;
    let payload_capacity = u64_to_usize(payload_length, "block payload allocation")?;
    let mut payload = Vec::with_capacity(payload_capacity);
    payload.extend_from_slice(&block_header);
    payload.extend_from_slice(&sections.definitions);
    payload.extend_from_slice(&sections.root);
    require_equal(
        "encoded block payload",
        payload_length,
        usize_to_u64(payload.len(), "encoded block payload")?,
    )?;

    let metadata = BlockMetadata {
        original_offset,
        original_length,
        payload_offset,
        payload_length,
        definition_count,
        node_count,
        edge_count,
        max_graph_depth,
        work_units: report.decode_work,
        workspace_bytes: report.temporary_bytes,
        restored_sha256: sha256(block.restored),
        payload_sha256: sha256(&payload),
        payload_crc32: crc32(&payload),
    };
    Ok(PreparedBlock { payload, metadata })
}

#[derive(Clone, Debug)]
struct FileHeader {
    original_size: u64,
    block_count: u64,
    directory_bytes: u64,
    payload_offset: u64,
    payload_bytes: u64,
    footer_offset: u64,
    total_node_count: u64,
    total_work_units: u64,
    original_sha256: Sha256Digest,
}

/// Incremental v1 encoder backed by a caller-owned seekable payload spool.
///
/// Only one source block and one prepared block archive are resident at a
/// time. The retained in-memory state is the fixed 144-byte metadata record
/// for each block plus hash/counter state. `finish` writes the canonical
/// header and directory, copies the payload spool, then writes the footer.
///
/// The final output may be partially written when an I/O error occurs. File
/// callers should therefore target an uncommitted temporary file and rename
/// it only after `finish` succeeds.
pub struct ArchiveStreamEncoder<S> {
    spool: S,
    spool_start: u64,
    limits: Limits,
    records: Vec<BlockMetadata>,
    original_size: u64,
    payload_bytes: u64,
    total_node_count: u64,
    total_work_units: u64,
    complete_restored_hash: Sha256,
    maximum_prepared_block_bytes: u64,
}

impl<S> ArchiveStreamEncoder<S>
where
    S: Read + Write + Seek,
{
    pub fn new(mut spool: S, limits: &Limits) -> StreamResult<Self> {
        let limits = v1_limits(limits);
        limits.check("archive bytes", MIN_ARCHIVE_BYTES, limits.max_archive_bytes)?;
        let spool_start = spool.stream_position()?;
        Ok(Self {
            spool,
            spool_start,
            limits,
            records: Vec::new(),
            original_size: 0,
            payload_bytes: 0,
            total_node_count: 0,
            total_work_units: 0,
            complete_restored_hash: Sha256::new(),
            maximum_prepared_block_bytes: 0,
        })
    }

    /// Validate, serialize and spool one independent non-empty block.
    pub fn push_block(&mut self, block: ArchiveBlock<'_>) -> StreamResult<()> {
        // Prepare the canonical block payload directly. The previous route
        // built a complete one-block archive and decoded it again merely to
        // recover these same bytes, repeating every entropy validation walk.
        let prepared_block =
            prepare_block(block, self.original_size, self.payload_bytes, &self.limits)?;
        let prepared_archive_bytes = checked_u64_add(
            checked_u64_add(
                MIN_ARCHIVE_BYTES,
                DIRECTORY_RECORD_BYTES as u64,
                "prepared block archive bytes",
            )?,
            prepared_block.metadata.payload_length,
            "prepared block archive bytes",
        )?;
        self.maximum_prepared_block_bytes = self
            .maximum_prepared_block_bytes
            .max(prepared_archive_bytes);

        let block_count = usize_to_u64(self.records.len(), "stream block count")?
            .checked_add(1)
            .ok_or(Error::IntegerOverflow {
                context: "stream block count",
            })?;
        let next_payload_bytes = checked_u64_add(
            self.payload_bytes,
            prepared_block.metadata.payload_length,
            "stream payload bytes",
        )?;
        let minimum_archive_bytes = checked_u64_add(
            checked_u64_add(
                MIN_ARCHIVE_BYTES,
                checked_u64_mul(
                    block_count,
                    DIRECTORY_RECORD_BYTES as u64,
                    "stream directory bytes",
                )?,
                "stream archive bytes",
            )?,
            next_payload_bytes,
            "stream archive bytes",
        )?;
        self.limits.check(
            "archive bytes",
            minimum_archive_bytes,
            self.limits.max_archive_bytes,
        )?;

        let spool_offset =
            checked_u64_add(self.spool_start, self.payload_bytes, "payload spool offset")?;
        self.spool.seek(SeekFrom::Start(spool_offset))?;
        self.spool.write_all(&prepared_block.payload)?;

        // `prepare_block` used the current cumulative offsets. Payload offsets
        // remain relative until `finish` adds the final directory boundary.
        let record = prepared_block.metadata;
        self.original_size = checked_u64_add(
            self.original_size,
            record.original_length,
            "stream original bytes",
        )?;
        self.limits.check(
            "output bytes",
            self.original_size,
            self.limits.max_output_bytes,
        )?;
        self.payload_bytes = next_payload_bytes;
        self.total_node_count = checked_u64_add(
            self.total_node_count,
            u64::from(record.node_count),
            "stream total node count",
        )?;
        self.total_work_units = checked_u64_add(
            self.total_work_units,
            record.work_units,
            "stream total work units",
        )?;
        self.complete_restored_hash.update(block.restored);
        self.records.push(record);
        Ok(())
    }

    /// Finalize a canonical v1 archive into `output`.
    pub fn finish<W>(mut self, output: &mut W) -> StreamResult<StreamEncodeReport>
    where
        W: Write,
    {
        if self.records.is_empty() {
            let archive = encode_empty(&self.limits)?;
            output.write_all(&archive)?;
            output.flush()?;
            return Ok(StreamEncodeReport {
                archive_bytes: MIN_ARCHIVE_BYTES,
                original_size: 0,
                original_sha256: sha256(&[]),
                block_count: 0,
                total_node_count: 0,
                total_work_units: 0,
                maximum_prepared_block_bytes: 0,
                retained_directory_bytes: 0,
            });
        }

        let block_count = usize_to_u64(self.records.len(), "stream block count")?;
        let directory_bytes = checked_u64_mul(
            block_count,
            DIRECTORY_RECORD_BYTES as u64,
            "stream directory bytes",
        )?;
        let payload_offset = checked_u64_add(
            FILE_HEADER_BYTES as u64,
            directory_bytes,
            "stream payload offset",
        )?;
        for record in &mut self.records {
            record.payload_offset = checked_u64_add(
                payload_offset,
                record.payload_offset,
                "stream block payload offset",
            )?;
        }
        let footer_offset =
            checked_u64_add(payload_offset, self.payload_bytes, "stream footer offset")?;
        let archive_bytes = checked_u64_add(
            footer_offset,
            FILE_FOOTER_BYTES as u64,
            "stream archive bytes",
        )?;
        self.limits.check(
            "archive bytes",
            archive_bytes,
            self.limits.max_archive_bytes,
        )?;

        let original_sha256: Sha256Digest = self.complete_restored_hash.finalize().into();
        let header = encode_file_header(&FileHeader {
            original_size: self.original_size,
            block_count,
            directory_bytes,
            payload_offset,
            payload_bytes: self.payload_bytes,
            footer_offset,
            total_node_count: self.total_node_count,
            total_work_units: self.total_work_units,
            original_sha256,
        });
        let directory = encode_directory_records(&self.records)?;
        require_equal(
            "stream encoded directory bytes",
            directory_bytes,
            usize_to_u64(directory.len(), "stream encoded directory length")?,
        )?;

        let mut prefix_hash = Sha256::new();
        prefix_hash.update(header);
        prefix_hash.update(&directory);
        output.write_all(&header)?;
        output.write_all(&directory)?;

        let mut payload_hash = Sha256::new();
        self.spool.seek(SeekFrom::Start(self.spool_start))?;
        let mut remaining = self.payload_bytes;
        let mut buffer = [0u8; 64 * 1024];
        while remaining != 0 {
            let amount = usize::try_from(remaining.min(buffer.len() as u64)).map_err(|_| {
                Error::IntegerOverflow {
                    context: "stream payload copy size",
                }
            })?;
            self.spool.read_exact(&mut buffer[..amount])?;
            let chunk = &buffer[..amount];
            output.write_all(chunk)?;
            prefix_hash.update(chunk);
            payload_hash.update(chunk);
            remaining -= amount as u64;
        }

        let footer = encode_file_footer(
            sha256(&directory),
            payload_hash.finalize().into(),
            prefix_hash.finalize().into(),
            archive_bytes,
        );
        output.write_all(&footer)?;
        output.flush()?;

        Ok(StreamEncodeReport {
            archive_bytes,
            original_size: self.original_size,
            original_sha256,
            block_count,
            total_node_count: self.total_node_count,
            total_work_units: self.total_work_units,
            maximum_prepared_block_bytes: self.maximum_prepared_block_bytes,
            retained_directory_bytes: directory_bytes,
        })
    }
}

/// Encode a complete v1 archive from independent programs and restored bytes.
pub fn encode_archive(blocks: &[ArchiveBlock<'_>], limits: &Limits) -> Result<Vec<u8>> {
    let limits = v1_limits(limits);
    let block_count = usize_to_u64(blocks.len(), "block count")?;
    let minimum_size = checked_u64_add(
        MIN_ARCHIVE_BYTES,
        checked_u64_mul(
            block_count,
            PER_BLOCK_ENVELOPE_BYTES,
            "minimum archive bytes",
        )?,
        "minimum archive bytes",
    )?;
    limits.check("archive bytes", minimum_size, limits.max_archive_bytes)?;

    if blocks.is_empty() {
        return encode_empty(&limits);
    }

    let directory_bytes = checked_u64_mul(
        block_count,
        DIRECTORY_RECORD_BYTES as u64,
        "directory bytes",
    )?;
    let payload_offset =
        checked_u64_add(FILE_HEADER_BYTES as u64, directory_bytes, "payload offset")?;
    let mut original_offset = 0u64;
    let mut next_payload_offset = payload_offset;
    let mut total_node_count = 0u64;
    let mut total_work_units = 0u64;
    let mut complete_restored_hash = Sha256::new();
    let mut prepared = Vec::new();

    for block in blocks {
        let prepared_block = prepare_block(*block, original_offset, next_payload_offset, &limits)?;
        original_offset = checked_u64_add(
            original_offset,
            prepared_block.metadata.original_length,
            "original block ranges",
        )?;
        next_payload_offset = checked_u64_add(
            next_payload_offset,
            prepared_block.metadata.payload_length,
            "payload block ranges",
        )?;
        total_node_count = checked_u64_add(
            total_node_count,
            u64::from(prepared_block.metadata.node_count),
            "total node count",
        )?;
        total_work_units = checked_u64_add(
            total_work_units,
            prepared_block.metadata.work_units,
            "total work units",
        )?;
        complete_restored_hash.update(block.restored);
        prepared.push(prepared_block);
    }

    limits.check("output bytes", original_offset, limits.max_output_bytes)?;
    let payload_bytes =
        next_payload_offset
            .checked_sub(payload_offset)
            .ok_or(Error::IntegerOverflow {
                context: "payload bytes",
            })?;
    let footer_offset = next_payload_offset;
    let archive_length =
        checked_u64_add(footer_offset, FILE_FOOTER_BYTES as u64, "archive length")?;
    limits.check("archive bytes", archive_length, limits.max_archive_bytes)?;

    let original_sha256: Sha256Digest = complete_restored_hash.finalize().into();
    let header = encode_file_header(&FileHeader {
        original_size: original_offset,
        block_count,
        directory_bytes,
        payload_offset,
        payload_bytes,
        footer_offset,
        total_node_count,
        total_work_units,
        original_sha256,
    });
    let directory = encode_directory(&prepared)?;
    require_equal(
        "encoded directory bytes",
        directory_bytes,
        usize_to_u64(directory.len(), "encoded directory length")?,
    )?;

    let archive_capacity = u64_to_usize(archive_length, "archive allocation")?;
    let mut prefix = Vec::with_capacity(archive_capacity.checked_sub(FILE_FOOTER_BYTES).ok_or(
        Error::IntegerOverflow {
            context: "archive prefix allocation",
        },
    )?);
    prefix.extend_from_slice(&header);
    prefix.extend_from_slice(&directory);
    for block in &prepared {
        prefix.extend_from_slice(&block.payload);
    }
    require_equal(
        "archive prefix length",
        footer_offset,
        usize_to_u64(prefix.len(), "archive prefix length")?,
    )?;

    let payload_start = u64_to_usize(payload_offset, "payload slice offset")?;
    let footer = encode_file_footer(
        sha256(&directory),
        sha256(&prefix[payload_start..]),
        sha256(&prefix),
        archive_length,
    );
    let mut archive = prefix;
    archive.extend_from_slice(&footer);
    require_equal(
        "archive length",
        archive_length,
        usize_to_u64(archive.len(), "archive length")?,
    )?;
    Ok(archive)
}

/// Encode the canonical universal literal fallback.
///
/// Empty input uses the unique zero-block archive. Non-empty input is split
/// into consecutive 16 MiB blocks, the largest v1 block size.
pub fn encode_literal_archive(input: &[u8], limits: &Limits) -> Result<Vec<u8>> {
    let limits = v1_limits(limits);
    let input_length = usize_to_u64(input.len(), "literal input length")?;
    limits.check("output bytes", input_length, limits.max_output_bytes)?;
    if input.is_empty() {
        return encode_archive(&[], &limits);
    }

    // The frozen canonical streaming partition is defined by the wire hard
    // bound, not by a deployment's possibly lower decoder policy. If a caller
    // selects a lower per-block policy, encoding a larger canonical block
    // correctly fails rather than silently creating a different partition.
    let block_size = V1_MAX_BLOCK_OUTPUT_BYTES as usize;
    let programs: Vec<_> = input
        .chunks(block_size)
        .map(|chunk| Program::literal(chunk.to_vec()))
        .collect();
    let blocks: Vec<_> = programs
        .iter()
        .zip(input.chunks(block_size))
        .map(|(program, restored)| ArchiveBlock { program, restored })
        .collect();
    encode_archive(&blocks, &limits)
}

/// Encode the unique 256-byte empty archive.
pub fn encode_empty_archive(limits: &Limits) -> Result<Vec<u8>> {
    encode_archive(&[], limits)
}

fn encode_empty(limits: &Limits) -> Result<Vec<u8>> {
    limits.check("archive bytes", MIN_ARCHIVE_BYTES, limits.max_archive_bytes)?;
    limits.check("output bytes", 0, limits.max_output_bytes)?;
    let empty_hash = sha256(&[]);
    let header = encode_file_header(&FileHeader {
        original_size: 0,
        block_count: 0,
        directory_bytes: 0,
        payload_offset: FILE_HEADER_BYTES as u64,
        payload_bytes: 0,
        footer_offset: FILE_HEADER_BYTES as u64,
        total_node_count: 0,
        total_work_units: 0,
        original_sha256: empty_hash,
    });
    let footer = encode_file_footer(empty_hash, empty_hash, sha256(&header), MIN_ARCHIVE_BYTES);
    let mut archive = Vec::with_capacity(MIN_ARCHIVE_BYTES as usize);
    archive.extend_from_slice(&header);
    archive.extend_from_slice(&footer);
    Ok(archive)
}

/// Strictly decode and validate the complete structural v1 envelope.
///
/// This function verifies all CRCs and payload/prefix/directory hashes, exact
/// offsets and totals, block metadata, and canonical DSL sections. It returns
/// parsed programs only after those checks. Call
/// [`DecodedArchive::verify_restored_with`] to complete restored SHA checking.
pub fn decode_archive(input: &[u8], limits: &Limits) -> Result<DecodedArchive> {
    let limits = v1_limits(limits);
    let input_length = usize_to_u64(input.len(), "input archive length")?;
    limits.check("archive bytes", input_length, limits.max_archive_bytes)?;
    if input.len() < FILE_HEADER_BYTES + FILE_FOOTER_BYTES {
        return Err(Error::Truncated {
            context: "MathSVG archive",
            position: input.len(),
        });
    }

    let header_bytes = &input[..FILE_HEADER_BYTES];
    let header = decode_file_header(header_bytes)?;
    limits.check(
        "output bytes",
        header.original_size,
        limits.max_output_bytes,
    )?;

    let computed_directory_bytes = checked_u64_mul(
        header.block_count,
        DIRECTORY_RECORD_BYTES as u64,
        "directory bytes",
    )?;
    require_equal(
        "directory byte count",
        computed_directory_bytes,
        header.directory_bytes,
    )?;
    let computed_payload_offset = checked_u64_add(
        FILE_HEADER_BYTES as u64,
        header.directory_bytes,
        "payload offset",
    )?;
    require_equal(
        "payload offset",
        computed_payload_offset,
        header.payload_offset,
    )?;
    let computed_footer_offset =
        checked_u64_add(header.payload_offset, header.payload_bytes, "footer offset")?;
    require_equal(
        "footer offset",
        computed_footer_offset,
        header.footer_offset,
    )?;
    let computed_archive_length = checked_u64_add(
        header.footer_offset,
        FILE_FOOTER_BYTES as u64,
        "archive length",
    )?;
    require_equal("archive length", computed_archive_length, input_length)?;

    let directory_start = FILE_HEADER_BYTES;
    let directory_end = u64_to_usize(header.payload_offset, "directory end")?;
    let footer_start = u64_to_usize(header.footer_offset, "footer offset")?;
    let directory = input
        .get(directory_start..directory_end)
        .ok_or(Error::Truncated {
            context: "block directory",
            position: directory_start,
        })?;
    let payload_section = input
        .get(directory_end..footer_start)
        .ok_or(Error::Truncated {
            context: "payload section",
            position: directory_end,
        })?;
    let footer = input.get(footer_start..).ok_or(Error::Truncated {
        context: "file footer",
        position: footer_start,
    })?;
    decode_file_footer(
        footer,
        sha256(directory),
        sha256(payload_section),
        sha256(&input[..footer_start]),
        input_length,
    )?;

    if header.block_count == 0 {
        validate_empty_header(&header)?;
        return Ok(DecodedArchive {
            original_size: 0,
            original_sha256: header.original_sha256,
            total_node_count: 0,
            total_work_units: 0,
            blocks: Vec::new(),
        });
    }
    if header.original_size == 0 {
        return Err(Error::InvalidValue(
            "non-empty block directory for empty restored stream",
        ));
    }

    let block_count = u64_to_usize(header.block_count, "block count")?;
    let maximum_records_from_input = directory.len() / DIRECTORY_RECORD_BYTES;
    if block_count > maximum_records_from_input {
        return Err(Error::Truncated {
            context: "block directory",
            position: directory.len(),
        });
    }
    let mut expected_original_offset = 0u64;
    let mut expected_payload_offset = header.payload_offset;
    let mut total_node_count = 0u64;
    let mut total_work_units = 0u64;

    for index in 0..block_count {
        let start = index
            .checked_mul(DIRECTORY_RECORD_BYTES)
            .ok_or(Error::IntegerOverflow {
                context: "directory record offset",
            })?;
        let end = start
            .checked_add(DIRECTORY_RECORD_BYTES)
            .ok_or(Error::IntegerOverflow {
                context: "directory record offset",
            })?;
        let record = decode_directory_record(&directory[start..end])?;
        validate_record_limits(&record, &limits)?;
        if record.original_length == 0 {
            return Err(Error::InvalidValue(
                "non-empty archive blocks must restore at least one byte",
            ));
        }
        require_equal(
            "contiguous original block offset",
            expected_original_offset,
            record.original_offset,
        )?;
        require_equal(
            "contiguous payload block offset",
            expected_payload_offset,
            record.payload_offset,
        )?;
        expected_original_offset = checked_u64_add(
            expected_original_offset,
            record.original_length,
            "original block ranges",
        )?;
        expected_payload_offset = checked_u64_add(
            expected_payload_offset,
            record.payload_length,
            "payload block ranges",
        )?;
        total_node_count = checked_u64_add(
            total_node_count,
            u64::from(record.node_count),
            "total node count",
        )?;
        total_work_units =
            checked_u64_add(total_work_units, record.work_units, "total work units")?;
    }
    require_equal(
        "complete original ranges",
        header.original_size,
        expected_original_offset,
    )?;
    require_equal(
        "complete payload ranges",
        header.footer_offset,
        expected_payload_offset,
    )?;
    require_equal(
        "header total node count",
        header.total_node_count,
        total_node_count,
    )?;
    require_equal(
        "header total work units",
        header.total_work_units,
        total_work_units,
    )?;

    // In-memory/seekable v1 rereads the fixed directory rather than retaining
    // a second metadata array proportional to untrusted block_count.
    let mut blocks = Vec::new();
    for index in 0..block_count {
        let start = index
            .checked_mul(DIRECTORY_RECORD_BYTES)
            .ok_or(Error::IntegerOverflow {
                context: "directory record offset",
            })?;
        let end = start
            .checked_add(DIRECTORY_RECORD_BYTES)
            .ok_or(Error::IntegerOverflow {
                context: "directory record offset",
            })?;
        let record = decode_directory_record(&directory[start..end])?;
        let payload_start = u64_to_usize(record.payload_offset, "block payload offset")?;
        let payload_end_u64 = checked_u64_add(
            record.payload_offset,
            record.payload_length,
            "block payload end",
        )?;
        let payload_end = u64_to_usize(payload_end_u64, "block payload end")?;
        let payload = input
            .get(payload_start..payload_end)
            .ok_or(Error::Truncated {
                context: "block payload",
                position: payload_start,
            })?;
        let program = decode_program_payload(payload, &record, &limits)?;
        blocks.push(DecodedBlock {
            metadata: record,
            program,
        });
    }

    Ok(DecodedArchive {
        original_size: header.original_size,
        original_sha256: header.original_sha256,
        total_node_count: header.total_node_count,
        total_work_units: header.total_work_units,
        blocks,
    })
}

/// Strictly decode a seekable archive while retaining at most one encoded and
/// one restored block.
///
/// The fixed directory is validated in a first pass and reread one record at a
/// time in the payload pass, so no directory-sized allocation is retained.
/// `on_verified_block` is invoked only after the block program, restored
/// length and restored block SHA-256 have passed. The complete footer and
/// whole-file restored hash are necessarily still pending at that point;
/// callers must write callbacks to an uncommitted spool and publish it only
/// after this function returns `Ok`.
pub fn decode_archive_stream<R, E, C>(
    input: &mut R,
    limits: &Limits,
    mut evaluate: E,
    mut on_verified_block: C,
) -> StreamResult<StreamDecodeReport>
where
    R: Read + Seek,
    E: FnMut(&Program) -> Result<Vec<u8>>,
    C: FnMut(usize, &[u8]) -> std::io::Result<()>,
{
    let limits = v1_limits(limits);
    let archive_start = input.stream_position()?;
    let archive_end = input.seek(SeekFrom::End(0))?;
    let input_length = archive_end
        .checked_sub(archive_start)
        .ok_or(Error::IntegerOverflow {
            context: "stream archive length",
        })?;
    limits.check("archive bytes", input_length, limits.max_archive_bytes)?;
    if input_length < MIN_ARCHIVE_BYTES {
        return Err(Error::Truncated {
            context: "MathSVG archive",
            position: u64_to_usize(input_length, "stream archive length")?,
        }
        .into());
    }

    let header_bytes =
        read_stream_array_at::<R, FILE_HEADER_BYTES>(input, archive_start, 0, "file header")?;
    let header = decode_file_header(&header_bytes)?;
    limits.check(
        "output bytes",
        header.original_size,
        limits.max_output_bytes,
    )?;
    let computed_directory_bytes = checked_u64_mul(
        header.block_count,
        DIRECTORY_RECORD_BYTES as u64,
        "directory bytes",
    )?;
    require_equal(
        "directory byte count",
        computed_directory_bytes,
        header.directory_bytes,
    )?;
    let computed_payload_offset = checked_u64_add(
        FILE_HEADER_BYTES as u64,
        header.directory_bytes,
        "payload offset",
    )?;
    require_equal(
        "payload offset",
        computed_payload_offset,
        header.payload_offset,
    )?;
    let computed_footer_offset =
        checked_u64_add(header.payload_offset, header.payload_bytes, "footer offset")?;
    require_equal(
        "footer offset",
        computed_footer_offset,
        header.footer_offset,
    )?;
    let computed_archive_length = checked_u64_add(
        header.footer_offset,
        FILE_FOOTER_BYTES as u64,
        "archive length",
    )?;
    require_equal("archive length", computed_archive_length, input_length)?;

    let mut directory_hash = Sha256::new();
    let mut prefix_hash = Sha256::new();
    prefix_hash.update(header_bytes);
    let mut expected_original_offset = 0u64;
    let mut expected_payload_offset = header.payload_offset;
    let mut total_node_count = 0u64;
    let mut total_work_units = 0u64;

    input.seek(SeekFrom::Start(checked_u64_add(
        archive_start,
        FILE_HEADER_BYTES as u64,
        "stream directory start",
    )?))?;
    for _ in 0..header.block_count {
        let mut bytes = [0u8; DIRECTORY_RECORD_BYTES];
        input.read_exact(&mut bytes)?;
        directory_hash.update(bytes);
        prefix_hash.update(bytes);
        let record = decode_directory_record(&bytes)?;
        validate_record_limits(&record, &limits)?;
        if record.original_length == 0 {
            return Err(Error::InvalidValue(
                "non-empty archive blocks must restore at least one byte",
            )
            .into());
        }
        require_equal(
            "contiguous original block offset",
            expected_original_offset,
            record.original_offset,
        )?;
        require_equal(
            "contiguous payload block offset",
            expected_payload_offset,
            record.payload_offset,
        )?;
        expected_original_offset = checked_u64_add(
            expected_original_offset,
            record.original_length,
            "original block ranges",
        )?;
        expected_payload_offset = checked_u64_add(
            expected_payload_offset,
            record.payload_length,
            "payload block ranges",
        )?;
        total_node_count = checked_u64_add(
            total_node_count,
            u64::from(record.node_count),
            "total node count",
        )?;
        total_work_units =
            checked_u64_add(total_work_units, record.work_units, "total work units")?;
    }
    require_equal(
        "complete original ranges",
        header.original_size,
        expected_original_offset,
    )?;
    require_equal(
        "complete payload ranges",
        header.footer_offset,
        expected_payload_offset,
    )?;
    require_equal(
        "header total node count",
        header.total_node_count,
        total_node_count,
    )?;
    require_equal(
        "header total work units",
        header.total_work_units,
        total_work_units,
    )?;
    let directory_sha256: Sha256Digest = directory_hash.finalize().into();

    if header.block_count == 0 {
        validate_empty_header(&header)?;
        let footer = read_stream_array_at::<R, FILE_FOOTER_BYTES>(
            input,
            archive_start,
            header.footer_offset,
            "file footer",
        )?;
        let empty_hash = sha256(&[]);
        decode_file_footer(
            &footer,
            directory_sha256,
            empty_hash,
            prefix_hash.finalize().into(),
            input_length,
        )?;
        return Ok(StreamDecodeReport {
            verified: VerifiedArchive {
                original_size: 0,
                original_sha256: header.original_sha256,
                block_count: 0,
                total_node_count: 0,
                total_work_units: 0,
            },
            archive_bytes: input_length,
            maximum_payload_buffer_bytes: 0,
            maximum_restored_buffer_bytes: 0,
            retained_directory_bytes: 0,
        });
    }
    if header.original_size == 0 {
        return Err(
            Error::InvalidValue("non-empty block directory for empty restored stream").into(),
        );
    }

    let mut payload_hash = Sha256::new();
    let mut directory_recheck_hash = Sha256::new();
    let mut complete_restored_hash = Sha256::new();
    let mut restored_bytes = 0u64;
    let mut maximum_payload_buffer_bytes = 0u64;
    let mut maximum_restored_buffer_bytes = 0u64;
    let mut second_original_offset = 0u64;
    let mut second_payload_offset = header.payload_offset;

    for block_index in 0..header.block_count {
        let record_relative_offset = checked_u64_add(
            FILE_HEADER_BYTES as u64,
            checked_u64_mul(
                block_index,
                DIRECTORY_RECORD_BYTES as u64,
                "stream directory record offset",
            )?,
            "stream directory record offset",
        )?;
        let record_bytes = read_stream_array_at::<R, DIRECTORY_RECORD_BYTES>(
            input,
            archive_start,
            record_relative_offset,
            "directory record",
        )?;
        directory_recheck_hash.update(record_bytes);
        let record = decode_directory_record(&record_bytes)?;
        validate_record_limits(&record, &limits)?;
        require_equal(
            "contiguous original block offset",
            second_original_offset,
            record.original_offset,
        )?;
        require_equal(
            "contiguous payload block offset",
            second_payload_offset,
            record.payload_offset,
        )?;

        let payload_length =
            u64_to_usize(record.payload_length, "stream block payload allocation")?;
        maximum_payload_buffer_bytes = maximum_payload_buffer_bytes.max(record.payload_length);
        let payload_absolute_offset = checked_u64_add(
            archive_start,
            record.payload_offset,
            "stream block payload offset",
        )?;
        input.seek(SeekFrom::Start(payload_absolute_offset))?;
        let mut payload = vec![0u8; payload_length];
        input.read_exact(&mut payload)?;
        payload_hash.update(&payload);
        prefix_hash.update(&payload);
        let program = decode_program_payload(&payload, &record, &limits)?;

        let restored = evaluate(&program)?;
        let restored_length = usize_to_u64(restored.len(), "stream evaluated block length")?;
        require_equal(
            "evaluated block length",
            record.original_length,
            restored_length,
        )?;
        if sha256(&restored) != record.restored_sha256 {
            return Err(Error::InvalidValue("restored block SHA-256 mismatch").into());
        }
        maximum_restored_buffer_bytes = maximum_restored_buffer_bytes.max(restored_length);
        restored_bytes = checked_u64_add(
            restored_bytes,
            restored_length,
            "stream restored output bytes",
        )?;
        complete_restored_hash.update(&restored);
        let callback_index = usize::try_from(block_index).map_err(|_| Error::IntegerOverflow {
            context: "stream callback block index",
        })?;
        on_verified_block(callback_index, &restored)?;

        second_original_offset = checked_u64_add(
            second_original_offset,
            record.original_length,
            "stream original block ranges",
        )?;
        second_payload_offset = checked_u64_add(
            second_payload_offset,
            record.payload_length,
            "stream payload block ranges",
        )?;
    }

    let rechecked_directory_sha256: Sha256Digest = directory_recheck_hash.finalize().into();
    if rechecked_directory_sha256 != directory_sha256 {
        return Err(
            Error::InvalidValue("directory changed between streaming validation passes").into(),
        );
    }
    require_equal(
        "complete restored length",
        header.original_size,
        restored_bytes,
    )?;
    let restored_sha256: Sha256Digest = complete_restored_hash.finalize().into();
    if restored_sha256 != header.original_sha256 {
        return Err(Error::InvalidValue("complete restored SHA-256 mismatch").into());
    }
    let footer = read_stream_array_at::<R, FILE_FOOTER_BYTES>(
        input,
        archive_start,
        header.footer_offset,
        "file footer",
    )?;
    decode_file_footer(
        &footer,
        directory_sha256,
        payload_hash.finalize().into(),
        prefix_hash.finalize().into(),
        input_length,
    )?;

    Ok(StreamDecodeReport {
        verified: VerifiedArchive {
            original_size: header.original_size,
            original_sha256: header.original_sha256,
            block_count: header.block_count,
            total_node_count: header.total_node_count,
            total_work_units: header.total_work_units,
        },
        archive_bytes: input_length,
        maximum_payload_buffer_bytes,
        maximum_restored_buffer_bytes,
        retained_directory_bytes: 0,
    })
}

fn read_stream_array_at<R, const N: usize>(
    input: &mut R,
    archive_start: u64,
    relative_offset: u64,
    context: &'static str,
) -> StreamResult<[u8; N]>
where
    R: Read + Seek,
{
    let absolute_offset = checked_u64_add(archive_start, relative_offset, "stream archive offset")?;
    input.seek(SeekFrom::Start(absolute_offset))?;
    let mut bytes = [0u8; N];
    input.read_exact(&mut bytes).map_err(|error| {
        if error.kind() == std::io::ErrorKind::UnexpectedEof {
            StreamError::Format(Error::Truncated {
                context,
                position: usize::try_from(relative_offset).unwrap_or(usize::MAX),
            })
        } else {
            StreamError::Io(error)
        }
    })?;
    Ok(bytes)
}

fn decode_program_payload(
    payload: &[u8],
    record: &BlockMetadata,
    limits: &Limits,
) -> Result<Program> {
    if crc32(payload) != record.payload_crc32 {
        return Err(Error::InvalidValue("block payload CRC32 mismatch"));
    }
    if sha256(payload) != record.payload_sha256 {
        return Err(Error::InvalidValue("block payload SHA-256 mismatch"));
    }
    let block_header = payload.get(..BLOCK_HEADER_BYTES).ok_or(Error::Truncated {
        context: "block header",
        position: payload.len(),
    })?;
    let sections = decode_block_header(block_header, record)?;
    let definition_end = BLOCK_HEADER_BYTES
        .checked_add(sections.definition_bytes as usize)
        .ok_or(Error::IntegerOverflow {
            context: "definition section end",
        })?;
    let root_end = definition_end
        .checked_add(sections.root_bytes as usize)
        .ok_or(Error::IntegerOverflow {
            context: "root section end",
        })?;
    require_equal(
        "block payload section lengths",
        record.payload_length,
        usize_to_u64(root_end, "block payload section lengths")?,
    )?;
    let definition_bytes =
        payload
            .get(BLOCK_HEADER_BYTES..definition_end)
            .ok_or(Error::Truncated {
                context: "definition section",
                position: BLOCK_HEADER_BYTES,
            })?;
    let root_bytes = payload
        .get(definition_end..root_end)
        .ok_or(Error::Truncated {
            context: "root section",
            position: definition_end,
        })?;
    let (program, report) = Program::decode_sections_with_report(
        record.definition_count,
        definition_bytes,
        root_bytes,
        limits,
    )?;
    validate_program_report(record, &report)?;
    Ok(program)
}

#[derive(Clone, Copy, Debug)]
struct BlockSections {
    definition_bytes: u32,
    root_bytes: u32,
}

fn v1_limits(limits: &Limits) -> Limits {
    let mut effective = limits.clone();
    effective.max_block_output_bytes = effective
        .max_block_output_bytes
        .min(V1_MAX_BLOCK_OUTPUT_BYTES);
    effective.max_block_payload_bytes = effective
        .max_block_payload_bytes
        .min(V1_MAX_BLOCK_PAYLOAD_BYTES);
    effective.max_definitions = effective.max_definitions.min(V1_MAX_DEFINITIONS);
    effective.max_nodes = effective.max_nodes.min(V1_MAX_NODES);
    effective.max_edges = effective.max_edges.min(V1_MAX_EDGES);
    effective.max_graph_depth = effective.max_graph_depth.min(V1_MAX_GRAPH_DEPTH);
    effective.max_temporary_bytes = effective.max_temporary_bytes.min(V1_MAX_WORKSPACE_BYTES);
    effective.max_work = effective.max_work.min(V1_MAX_WORK_UNITS);
    effective
}

fn require_bytes_root(report: &ValidationReport) -> Result<()> {
    if matches!(report.root_type, ValueType::Bytes(_)) {
        Ok(())
    } else {
        Err(Error::TypeMismatch {
            context: "container block root",
        })
    }
}

fn validate_program_report(record: &BlockMetadata, report: &ValidationReport) -> Result<()> {
    require_bytes_root(report)?;
    require_equal(
        "block decoded length",
        record.original_length,
        report.original_bytes,
    )?;
    require_equal(
        "block node count",
        u64::from(record.node_count),
        report.node_count,
    )?;
    require_equal(
        "block edge count",
        u64::from(record.edge_count),
        report.edge_count,
    )?;
    require_equal(
        "block graph depth",
        u64::from(record.max_graph_depth),
        report.graph_depth,
    )?;
    require_equal("block work units", record.work_units, report.decode_work)?;
    require_equal(
        "block workspace bytes",
        record.workspace_bytes,
        report.temporary_bytes,
    )
}

fn validate_record_limits(record: &BlockMetadata, limits: &Limits) -> Result<()> {
    limits.check(
        "block output bytes",
        record.original_length,
        limits.max_block_output_bytes,
    )?;
    limits.check(
        "block payload bytes",
        record.payload_length,
        limits.max_block_payload_bytes,
    )?;
    limits.check(
        "definitions",
        u64::from(record.definition_count),
        limits.max_definitions,
    )?;
    limits.check("nodes", u64::from(record.node_count), limits.max_nodes)?;
    limits.check("edges", u64::from(record.edge_count), limits.max_edges)?;
    limits.check(
        "graph depth",
        u64::from(record.max_graph_depth),
        limits.max_graph_depth,
    )?;
    limits.check("work", record.work_units, limits.max_work)?;
    limits.check(
        "temporary bytes",
        record.workspace_bytes,
        limits.max_temporary_bytes,
    )?;
    if record.payload_length < BLOCK_HEADER_BYTES as u64 {
        return Err(Error::InvalidValue(
            "block payload is shorter than its header",
        ));
    }
    if record.node_count == 0 || record.max_graph_depth == 0 {
        return Err(Error::InvalidValue(
            "non-empty block must contain a rooted program",
        ));
    }
    Ok(())
}

fn validate_empty_header(header: &FileHeader) -> Result<()> {
    for (context, value) in [
        ("empty original size", header.original_size),
        ("empty directory bytes", header.directory_bytes),
        ("empty payload bytes", header.payload_bytes),
        ("empty total node count", header.total_node_count),
        ("empty total work units", header.total_work_units),
    ] {
        require_equal(context, 0, value)?;
    }
    require_equal(
        "empty payload offset",
        FILE_HEADER_BYTES as u64,
        header.payload_offset,
    )?;
    require_equal(
        "empty footer offset",
        FILE_HEADER_BYTES as u64,
        header.footer_offset,
    )?;
    if header.original_sha256 != sha256(&[]) {
        return Err(Error::InvalidValue(
            "empty archive restored SHA-256 mismatch",
        ));
    }
    Ok(())
}

fn encode_file_header(header: &FileHeader) -> [u8; FILE_HEADER_BYTES] {
    let mut bytes = [0u8; FILE_HEADER_BYTES];
    bytes[0..4].copy_from_slice(FILE_MAGIC);
    put_u16(&mut bytes, 4, CONTAINER_VERSION);
    put_u16(&mut bytes, 6, DSL_VERSION);
    put_u32(&mut bytes, 8, 0);
    put_u32(&mut bytes, 12, FILE_HEADER_BYTES as u32);
    put_u64(&mut bytes, 16, header.original_size);
    put_u64(&mut bytes, 24, header.block_count);
    put_u64(&mut bytes, 32, FILE_HEADER_BYTES as u64);
    put_u64(&mut bytes, 40, header.directory_bytes);
    put_u64(&mut bytes, 48, header.payload_offset);
    put_u64(&mut bytes, 56, header.payload_bytes);
    put_u64(&mut bytes, 64, header.footer_offset);
    put_u64(&mut bytes, 72, header.total_node_count);
    put_u64(&mut bytes, 80, header.total_work_units);
    bytes[88..120].copy_from_slice(&header.original_sha256);
    put_u32(&mut bytes, 120, 0);
    let checksum = crc32(&bytes[..124]);
    put_u32(&mut bytes, 124, checksum);
    bytes
}

fn decode_file_header(bytes: &[u8]) -> Result<FileHeader> {
    require_fixed_len(bytes, FILE_HEADER_BYTES, "file header")?;
    require_magic(bytes, FILE_MAGIC, "file header magic")?;
    require_u16(bytes, 4, CONTAINER_VERSION, "container version")?;
    require_u16(bytes, 6, DSL_VERSION, "DSL version")?;
    require_u32(bytes, 8, 0, "file header flags")?;
    require_u32(bytes, 12, FILE_HEADER_BYTES as u32, "file header size")?;
    require_u64(bytes, 32, FILE_HEADER_BYTES as u64, "directory offset")?;
    require_u32(bytes, 120, 0, "file header reserved")?;
    verify_crc(bytes, 124, "file header CRC32")?;
    Ok(FileHeader {
        original_size: read_u64(bytes, 16, "original size")?,
        block_count: read_u64(bytes, 24, "block count")?,
        directory_bytes: read_u64(bytes, 40, "directory bytes")?,
        payload_offset: read_u64(bytes, 48, "payload offset")?,
        payload_bytes: read_u64(bytes, 56, "payload bytes")?,
        footer_offset: read_u64(bytes, 64, "footer offset")?,
        total_node_count: read_u64(bytes, 72, "total node count")?,
        total_work_units: read_u64(bytes, 80, "total work units")?,
        original_sha256: read_hash(bytes, 88, "original SHA-256")?,
    })
}

#[allow(clippy::too_many_arguments)]
fn encode_block_header(
    decoded_length: u64,
    definition_count: u32,
    node_count: u32,
    edge_count: u32,
    max_graph_depth: u16,
    work_units: u64,
    workspace_bytes: u64,
    definition_bytes: u32,
    root_bytes: u32,
) -> [u8; BLOCK_HEADER_BYTES] {
    let mut bytes = [0u8; BLOCK_HEADER_BYTES];
    bytes[0..4].copy_from_slice(BLOCK_MAGIC);
    put_u16(&mut bytes, 4, CONTAINER_VERSION);
    put_u16(&mut bytes, 6, DSL_VERSION);
    put_u32(&mut bytes, 8, 0);
    put_u32(&mut bytes, 12, BLOCK_HEADER_BYTES as u32);
    put_u64(&mut bytes, 16, decoded_length);
    put_u32(&mut bytes, 24, definition_count);
    put_u32(&mut bytes, 28, node_count);
    put_u32(&mut bytes, 32, edge_count);
    put_u16(&mut bytes, 36, max_graph_depth);
    put_u16(&mut bytes, 38, ROOT_VALUE_TYPE_BYTES);
    put_u64(&mut bytes, 40, work_units);
    put_u64(&mut bytes, 48, workspace_bytes);
    put_u32(&mut bytes, 56, definition_bytes);
    put_u32(&mut bytes, 60, root_bytes);
    put_u32(&mut bytes, 64, 0);
    let checksum = crc32(&bytes[..68]);
    put_u32(&mut bytes, 68, checksum);
    bytes
}

fn decode_block_header(bytes: &[u8], record: &BlockMetadata) -> Result<BlockSections> {
    require_fixed_len(bytes, BLOCK_HEADER_BYTES, "block header")?;
    require_magic(bytes, BLOCK_MAGIC, "block header magic")?;
    require_u16(bytes, 4, CONTAINER_VERSION, "block version")?;
    require_u16(bytes, 6, DSL_VERSION, "block DSL version")?;
    require_u32(bytes, 8, 0, "block flags")?;
    require_u32(bytes, 12, BLOCK_HEADER_BYTES as u32, "block header size")?;
    require_u64(bytes, 16, record.original_length, "block decoded length")?;
    require_u32(bytes, 24, record.definition_count, "block definition count")?;
    require_u32(bytes, 28, record.node_count, "block node count")?;
    require_u32(bytes, 32, record.edge_count, "block edge count")?;
    require_u16(bytes, 36, record.max_graph_depth, "block graph depth")?;
    require_u16(bytes, 38, ROOT_VALUE_TYPE_BYTES, "block root value type")?;
    require_u64(bytes, 40, record.work_units, "block work units")?;
    require_u64(bytes, 48, record.workspace_bytes, "block workspace bytes")?;
    require_u32(bytes, 64, 0, "block reserved")?;
    verify_crc(bytes, 68, "block header CRC32")?;
    Ok(BlockSections {
        definition_bytes: read_u32(bytes, 56, "definition bytes")?,
        root_bytes: read_u32(bytes, 60, "root bytes")?,
    })
}

fn encode_directory(blocks: &[PreparedBlock]) -> Result<Vec<u8>> {
    let capacity =
        blocks
            .len()
            .checked_mul(DIRECTORY_RECORD_BYTES)
            .ok_or(Error::IntegerOverflow {
                context: "directory allocation",
            })?;
    let mut directory = Vec::with_capacity(capacity);
    for block in blocks {
        directory.extend_from_slice(&encode_directory_record(&block.metadata));
    }
    Ok(directory)
}

fn encode_directory_records(records: &[BlockMetadata]) -> Result<Vec<u8>> {
    let capacity =
        records
            .len()
            .checked_mul(DIRECTORY_RECORD_BYTES)
            .ok_or(Error::IntegerOverflow {
                context: "directory allocation",
            })?;
    let mut directory = Vec::with_capacity(capacity);
    for record in records {
        directory.extend_from_slice(&encode_directory_record(record));
    }
    Ok(directory)
}

fn encode_directory_record(record: &BlockMetadata) -> [u8; DIRECTORY_RECORD_BYTES] {
    let mut bytes = [0u8; DIRECTORY_RECORD_BYTES];
    put_u64(&mut bytes, 0, record.original_offset);
    put_u64(&mut bytes, 8, record.original_length);
    put_u64(&mut bytes, 16, record.payload_offset);
    put_u64(&mut bytes, 24, record.payload_length);
    put_u32(&mut bytes, 32, record.definition_count);
    put_u32(&mut bytes, 36, record.node_count);
    put_u32(&mut bytes, 40, record.edge_count);
    put_u32(&mut bytes, 44, 0);
    put_u16(&mut bytes, 48, record.max_graph_depth);
    put_u16(&mut bytes, 50, ROOT_VALUE_TYPE_BYTES);
    put_u32(&mut bytes, 52, 0);
    put_u64(&mut bytes, 56, record.work_units);
    put_u64(&mut bytes, 64, record.workspace_bytes);
    bytes[72..104].copy_from_slice(&record.restored_sha256);
    bytes[104..136].copy_from_slice(&record.payload_sha256);
    put_u32(&mut bytes, 136, record.payload_crc32);
    let checksum = crc32(&bytes[..140]);
    put_u32(&mut bytes, 140, checksum);
    bytes
}

fn decode_directory_record(bytes: &[u8]) -> Result<BlockMetadata> {
    require_fixed_len(bytes, DIRECTORY_RECORD_BYTES, "directory record")?;
    require_u32(bytes, 44, 0, "directory record flags")?;
    require_u16(
        bytes,
        50,
        ROOT_VALUE_TYPE_BYTES,
        "directory root value type",
    )?;
    require_u32(bytes, 52, 0, "directory record reserved")?;
    verify_crc(bytes, 140, "directory record CRC32")?;
    Ok(BlockMetadata {
        original_offset: read_u64(bytes, 0, "block original offset")?,
        original_length: read_u64(bytes, 8, "block original length")?,
        payload_offset: read_u64(bytes, 16, "block payload offset")?,
        payload_length: read_u64(bytes, 24, "block payload length")?,
        definition_count: read_u32(bytes, 32, "block definition count")?,
        node_count: read_u32(bytes, 36, "block node count")?,
        edge_count: read_u32(bytes, 40, "block edge count")?,
        max_graph_depth: read_u16(bytes, 48, "block graph depth")?,
        work_units: read_u64(bytes, 56, "block work units")?,
        workspace_bytes: read_u64(bytes, 64, "block workspace bytes")?,
        restored_sha256: read_hash(bytes, 72, "block restored SHA-256")?,
        payload_sha256: read_hash(bytes, 104, "block payload SHA-256")?,
        payload_crc32: read_u32(bytes, 136, "block payload CRC32")?,
    })
}

fn encode_file_footer(
    directory_sha256: Sha256Digest,
    payload_sha256: Sha256Digest,
    prefix_sha256: Sha256Digest,
    archive_length: u64,
) -> [u8; FILE_FOOTER_BYTES] {
    let mut bytes = [0u8; FILE_FOOTER_BYTES];
    bytes[0..4].copy_from_slice(FOOTER_MAGIC);
    put_u16(&mut bytes, 4, CONTAINER_VERSION);
    put_u16(&mut bytes, 6, FILE_FOOTER_BYTES as u16);
    put_u32(&mut bytes, 8, 0);
    put_u32(&mut bytes, 12, 0);
    bytes[16..48].copy_from_slice(&directory_sha256);
    bytes[48..80].copy_from_slice(&payload_sha256);
    bytes[80..112].copy_from_slice(&prefix_sha256);
    put_u64(&mut bytes, 112, archive_length);
    put_u32(&mut bytes, 120, 0);
    let checksum = crc32(&bytes[..124]);
    put_u32(&mut bytes, 124, checksum);
    bytes
}

fn decode_file_footer(
    bytes: &[u8],
    directory_sha256: Sha256Digest,
    payload_sha256: Sha256Digest,
    prefix_sha256: Sha256Digest,
    archive_length: u64,
) -> Result<()> {
    require_fixed_len(bytes, FILE_FOOTER_BYTES, "file footer")?;
    require_magic(bytes, FOOTER_MAGIC, "file footer magic")?;
    require_u16(bytes, 4, CONTAINER_VERSION, "footer container version")?;
    require_u16(bytes, 6, FILE_FOOTER_BYTES as u16, "file footer size")?;
    require_u32(bytes, 8, 0, "file footer flags")?;
    require_u32(bytes, 12, 0, "file footer reserved0")?;
    require_hash(bytes, 16, directory_sha256, "footer directory SHA-256")?;
    require_hash(bytes, 48, payload_sha256, "footer payload SHA-256")?;
    require_hash(bytes, 80, prefix_sha256, "footer prefix SHA-256")?;
    require_u64(bytes, 112, archive_length, "footer archive length")?;
    require_u32(bytes, 120, 0, "file footer reserved1")?;
    verify_crc(bytes, 124, "file footer CRC32")
}

fn sha256(bytes: &[u8]) -> Sha256Digest {
    Sha256::digest(bytes).into()
}

fn verify_crc(bytes: &[u8], checksum_offset: usize, context: &'static str) -> Result<()> {
    let expected = read_u32(bytes, checksum_offset, context)?;
    let actual = crc32(bytes.get(..checksum_offset).ok_or(Error::Truncated {
        context,
        position: bytes.len(),
    })?);
    if actual == expected {
        Ok(())
    } else {
        Err(Error::InvalidValue(context))
    }
}

fn require_equal(context: &'static str, expected: u64, actual: u64) -> Result<()> {
    if expected == actual {
        Ok(())
    } else {
        Err(Error::LengthMismatch {
            context,
            expected,
            actual,
        })
    }
}

fn require_fixed_len(bytes: &[u8], expected: usize, context: &'static str) -> Result<()> {
    require_equal(
        context,
        expected as u64,
        usize_to_u64(bytes.len(), context)?,
    )
}

fn require_magic(bytes: &[u8], expected: &[u8; 4], context: &'static str) -> Result<()> {
    let actual = bytes.get(..4).ok_or(Error::Truncated {
        context,
        position: bytes.len(),
    })?;
    if actual == expected {
        Ok(())
    } else {
        Err(Error::InvalidValue(context))
    }
}

fn require_u16(bytes: &[u8], offset: usize, expected: u16, context: &'static str) -> Result<()> {
    let actual = read_u16(bytes, offset, context)?;
    if actual == expected {
        Ok(())
    } else {
        Err(Error::InvalidValue(context))
    }
}

fn require_u32(bytes: &[u8], offset: usize, expected: u32, context: &'static str) -> Result<()> {
    let actual = read_u32(bytes, offset, context)?;
    if actual == expected {
        Ok(())
    } else {
        Err(Error::InvalidValue(context))
    }
}

fn require_u64(bytes: &[u8], offset: usize, expected: u64, context: &'static str) -> Result<()> {
    let actual = read_u64(bytes, offset, context)?;
    if actual == expected {
        Ok(())
    } else {
        Err(Error::InvalidValue(context))
    }
}

fn require_hash(
    bytes: &[u8],
    offset: usize,
    expected: Sha256Digest,
    context: &'static str,
) -> Result<()> {
    let actual = read_hash(bytes, offset, context)?;
    if actual == expected {
        Ok(())
    } else {
        Err(Error::InvalidValue(context))
    }
}

fn read_u16(bytes: &[u8], offset: usize, context: &'static str) -> Result<u16> {
    let raw = read_array::<2>(bytes, offset, context)?;
    Ok(u16::from_le_bytes(raw))
}

fn read_u32(bytes: &[u8], offset: usize, context: &'static str) -> Result<u32> {
    let raw = read_array::<4>(bytes, offset, context)?;
    Ok(u32::from_le_bytes(raw))
}

fn read_u64(bytes: &[u8], offset: usize, context: &'static str) -> Result<u64> {
    let raw = read_array::<8>(bytes, offset, context)?;
    Ok(u64::from_le_bytes(raw))
}

fn read_hash(bytes: &[u8], offset: usize, context: &'static str) -> Result<Sha256Digest> {
    read_array::<32>(bytes, offset, context)
}

fn read_array<const N: usize>(
    bytes: &[u8],
    offset: usize,
    context: &'static str,
) -> Result<[u8; N]> {
    let end = offset
        .checked_add(N)
        .ok_or(Error::IntegerOverflow { context })?;
    bytes
        .get(offset..end)
        .ok_or(Error::Truncated {
            context,
            position: offset,
        })?
        .try_into()
        .map_err(|_| Error::InvalidValue(context))
}

fn put_u16<const N: usize>(bytes: &mut [u8; N], offset: usize, value: u16) {
    bytes[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
}

fn put_u32<const N: usize>(bytes: &mut [u8; N], offset: usize, value: u32) {
    bytes[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

fn put_u64<const N: usize>(bytes: &mut [u8; N], offset: usize, value: u64) {
    bytes[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
}

fn usize_to_u64(value: usize, context: &'static str) -> Result<u64> {
    u64::try_from(value).map_err(|_| Error::IntegerOverflow { context })
}

fn u64_to_usize(value: u64, context: &'static str) -> Result<usize> {
    usize::try_from(value).map_err(|_| Error::IntegerOverflow { context })
}

#[cfg(test)]
mod tests {
    use super::*;
    use mathsvg_coordinates::forward;
    use mathsvg_dsl::{CoordinateTransform, NativeCoordinateDescriptor, Node, ValueType};
    use mathsvg_entropy::{encode, LeafCodec};
    use mathsvg_evaluator::evaluate_program;
    use std::io::Cursor as IoCursor;

    fn limits() -> Limits {
        Limits {
            max_archive_bytes: 64 * 1024 * 1024,
            max_output_bytes: 32 * 1024 * 1024,
            ..Limits::default()
        }
    }

    fn literal_evaluator(program: &Program) -> Result<Vec<u8>> {
        match &program.root {
            Node::File {
                original_length,
                child,
            } => match child.as_ref() {
                Node::Literal(bytes) if *original_length == bytes.len() as u64 => Ok(bytes.clone()),
                _ => Err(Error::InvalidValue("test evaluator supports literal only")),
            },
            _ => Err(Error::InvalidValue("test evaluator requires FILE")),
        }
    }

    fn recalculate_crc(bytes: &mut [u8], checksum_offset: usize) {
        let checksum = crc32(&bytes[..checksum_offset]);
        bytes[checksum_offset..checksum_offset + 4].copy_from_slice(&checksum.to_le_bytes());
    }

    fn rehash_single_block_archive(archive: &mut [u8]) {
        let payload_offset = read_u64(archive, 48, "test payload offset").unwrap() as usize;
        let footer_offset = read_u64(archive, 64, "test footer offset").unwrap() as usize;
        let directory = FILE_HEADER_BYTES..payload_offset;
        let payload = payload_offset..footer_offset;

        let payload_crc = crc32(&archive[payload.clone()]);
        put_u32_at(archive, FILE_HEADER_BYTES + 136, payload_crc);
        let payload_hash = sha256(&archive[payload.clone()]);
        archive[FILE_HEADER_BYTES + 104..FILE_HEADER_BYTES + 136].copy_from_slice(&payload_hash);
        recalculate_crc(
            &mut archive[FILE_HEADER_BYTES..payload_offset],
            DIRECTORY_RECORD_BYTES - 4,
        );
        recalculate_crc(&mut archive[..FILE_HEADER_BYTES], FILE_HEADER_BYTES - 4);

        let directory_hash = sha256(&archive[directory.clone()]);
        let payload_hash = sha256(&archive[payload]);
        let prefix_hash = sha256(&archive[..footer_offset]);
        archive[footer_offset + 16..footer_offset + 48].copy_from_slice(&directory_hash);
        archive[footer_offset + 48..footer_offset + 80].copy_from_slice(&payload_hash);
        archive[footer_offset + 80..footer_offset + 112].copy_from_slice(&prefix_hash);
        recalculate_crc(
            &mut archive[footer_offset..footer_offset + FILE_FOOTER_BYTES],
            FILE_FOOTER_BYTES - 4,
        );
    }

    fn put_u32_at(bytes: &mut [u8], offset: usize, value: u32) {
        bytes[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
    }

    fn put_u64_at(bytes: &mut [u8], offset: usize, value: u64) {
        bytes[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
    }

    fn decode_golden_hex(text: &str) -> Vec<u8> {
        let text = text.trim();
        assert_eq!(text.len() % 2, 0);
        text.as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                let digit = |value: u8| match value {
                    b'0'..=b'9' => value - b'0',
                    b'a'..=b'f' => value - b'a' + 10,
                    _ => panic!("invalid golden hex digit"),
                };
                (digit(pair[0]) << 4) | digit(pair[1])
            })
            .collect()
    }

    #[test]
    fn crc32_is_iso_hdlc() {
        assert_eq!(crc32(b"123456789"), 0xcbf4_3926);
    }

    #[test]
    fn empty_archive_is_unique_and_fully_verified() {
        let archive = encode_empty_archive(&limits()).unwrap();
        let golden = decode_golden_hex(include_str!("../tests/golden/empty-v1.hex"));
        assert_eq!(archive, golden);
        assert_eq!(
            sha256(&archive),
            [
                0x62, 0xca, 0x98, 0x0a, 0x0e, 0xda, 0x53, 0x62, 0x55, 0x0b, 0x8c, 0x96, 0xb0, 0x9e,
                0x6c, 0x04, 0xa9, 0x4f, 0x23, 0xe2, 0xb6, 0x3b, 0xd4, 0x7c, 0xbc, 0xd0, 0x29, 0x15,
                0x68, 0xba, 0xaf, 0x1f,
            ]
        );
        assert_eq!(archive.len(), 256);
        assert_eq!(&archive[..4], b"MSVG");
        assert_eq!(&archive[128..132], b"MSFT");
        let decoded = decode_archive(&archive, &limits()).unwrap();
        assert_eq!(decoded.original_size, 0);
        assert!(decoded.blocks.is_empty());
        let mut callbacks = 0;
        let verified = decoded
            .verify_restored_with(
                |_| Err(Error::InvalidValue("empty archive must not evaluate")),
                |_, _| {
                    callbacks += 1;
                    Ok(())
                },
            )
            .unwrap();
        assert_eq!(callbacks, 0);
        assert_eq!(verified.original_size, 0);
        assert_eq!(verified.original_sha256, sha256(&[]));
    }

    #[test]
    fn streaming_empty_archive_is_byte_identical() {
        let spool = IoCursor::new(Vec::new());
        let encoder = ArchiveStreamEncoder::new(spool, &limits()).unwrap();
        let mut output = IoCursor::new(Vec::new());
        let report = encoder.finish(&mut output).unwrap();
        let expected = encode_empty_archive(&limits()).unwrap();
        assert_eq!(output.into_inner(), expected);
        assert_eq!(report.archive_bytes, expected.len() as u64);
        assert_eq!(report.original_size, 0);
        assert_eq!(report.block_count, 0);
        assert_eq!(report.maximum_prepared_block_bytes, 0);
        assert_eq!(report.retained_directory_bytes, 0);
    }

    #[test]
    fn streaming_multiblock_archive_is_byte_identical_and_bounded() {
        let first = b"first procedural block".repeat(31);
        let second = (0..4_097)
            .map(|index| ((index * 73 + 19) & 0xff) as u8)
            .collect::<Vec<_>>();
        let first_program = Program::literal(first.clone());
        let second_program = Program::literal(second.clone());
        let expected = encode_archive(
            &[
                ArchiveBlock {
                    program: &first_program,
                    restored: &first,
                },
                ArchiveBlock {
                    program: &second_program,
                    restored: &second,
                },
            ],
            &limits(),
        )
        .unwrap();

        let spool = IoCursor::new(Vec::new());
        let mut encoder = ArchiveStreamEncoder::new(spool, &limits()).unwrap();
        encoder
            .push_block(ArchiveBlock {
                program: &first_program,
                restored: &first,
            })
            .unwrap();
        encoder
            .push_block(ArchiveBlock {
                program: &second_program,
                restored: &second,
            })
            .unwrap();
        let mut output = IoCursor::new(Vec::new());
        let report = encoder.finish(&mut output).unwrap();
        let actual = output.into_inner();

        assert_eq!(actual, expected);
        assert_eq!(report.archive_bytes, actual.len() as u64);
        assert_eq!(report.original_size, (first.len() + second.len()) as u64);
        assert_eq!(report.block_count, 2);
        assert_eq!(
            report.retained_directory_bytes,
            2 * DIRECTORY_RECORD_BYTES as u64
        );
        assert!(report.maximum_prepared_block_bytes < report.archive_bytes);

        let decoded = decode_archive(&actual, &limits()).unwrap();
        let mut restored = Vec::new();
        decoded
            .verify_restored_with(
                |program| evaluate_program(program, &limits()),
                |_, block| {
                    restored.extend_from_slice(block);
                    Ok(())
                },
            )
            .unwrap();
        assert_eq!(restored, [first, second].concat());
    }

    #[test]
    fn streaming_decoder_verifies_empty_and_multiblock_archives() {
        let empty = encode_empty_archive(&limits()).unwrap();
        let mut empty_reader = IoCursor::new(empty.clone());
        let mut empty_callbacks = 0usize;
        let empty_report = decode_archive_stream(
            &mut empty_reader,
            &limits(),
            |_| Err(Error::InvalidValue("empty archive must not evaluate")),
            |_, _| {
                empty_callbacks += 1;
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(empty_callbacks, 0);
        assert_eq!(empty_report.archive_bytes, empty.len() as u64);
        assert_eq!(empty_report.verified.block_count, 0);
        assert_eq!(empty_report.retained_directory_bytes, 0);

        let first = b"alpha".repeat(257);
        let second = b"beta-gamma".repeat(193);
        let programs = [
            Program::literal(first.clone()),
            Program::literal(second.clone()),
        ];
        let archive = encode_archive(
            &[
                ArchiveBlock {
                    program: &programs[0],
                    restored: &first,
                },
                ArchiveBlock {
                    program: &programs[1],
                    restored: &second,
                },
            ],
            &limits(),
        )
        .unwrap();
        let mut reader = IoCursor::new(archive.clone());
        let mut restored = Vec::new();
        let report = decode_archive_stream(
            &mut reader,
            &limits(),
            |program| evaluate_program(program, &limits()),
            |_, block| {
                restored.extend_from_slice(block);
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(restored, [first.clone(), second.clone()].concat());
        assert_eq!(report.archive_bytes, archive.len() as u64);
        assert_eq!(report.verified.block_count, 2);
        assert_eq!(report.maximum_restored_buffer_bytes, second.len() as u64);
        assert!(report.maximum_payload_buffer_bytes < archive.len() as u64);
        assert_eq!(report.retained_directory_bytes, 0);
    }

    #[test]
    fn streaming_decoder_never_reports_success_for_corruption_or_truncation() {
        let archive = encode_literal_archive(b"stream-hostile", &limits()).unwrap();
        for length in 0..archive.len() {
            let mut reader = IoCursor::new(archive[..length].to_vec());
            let result = decode_archive_stream(
                &mut reader,
                &limits(),
                |program| evaluate_program(program, &limits()),
                |_, _| Ok(()),
            );
            assert!(result.is_err(), "accepted truncation at {length}");
        }

        let mut corrupted = archive;
        let footer_byte = corrupted.len() - 17;
        corrupted[footer_byte] ^= 0x80;
        let mut reader = IoCursor::new(corrupted);
        let mut uncommitted = Vec::new();
        let result = decode_archive_stream(
            &mut reader,
            &limits(),
            |program| evaluate_program(program, &limits()),
            |_, block| {
                uncommitted.extend_from_slice(block);
                Ok(())
            },
        );
        assert!(result.is_err());
        // A caller may have received verified blocks in its private spool, but
        // success is the only authorization to publish that spool.
        assert_eq!(uncommitted, b"stream-hostile");
    }

    #[test]
    fn one_mib_stream_partition_meets_literal_expansion_gate() {
        fn literal_stream(bytes: &[u8]) -> Vec<u8> {
            let mut encoder =
                ArchiveStreamEncoder::new(IoCursor::new(Vec::new()), &limits()).unwrap();
            for block in bytes.chunks(1024 * 1024) {
                let program = Program::literal(block.to_vec());
                encoder
                    .push_block(ArchiveBlock {
                        program: &program,
                        restored: block,
                    })
                    .unwrap();
            }
            let mut archive = IoCursor::new(Vec::new());
            encoder.finish(&mut archive).unwrap();
            archive.into_inner()
        }

        let mut state = 0x6a09_e667_f3bc_c909u64;
        let random = (0..(3 * 1024 * 1024 + 173))
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                state as u8
            })
            .collect::<Vec<_>>();
        let first = literal_stream(&random);
        let allowance = random.len() / 1000 + MIN_ARCHIVE_BYTES as usize;
        assert!(first.len() <= random.len() + allowance);

        // A valid archive is an already-compressed control. Repartitioning it
        // as literal data must obey the same universal upper bound.
        let second = literal_stream(&first);
        let allowance = first.len() / 1000 + MIN_ARCHIVE_BYTES as usize;
        assert!(second.len() <= first.len() + allowance);
    }

    #[test]
    fn literal_archive_round_trips_and_verifies_restored_hashes() {
        let archive = encode_literal_archive(b"abc", &limits()).unwrap();
        let golden = decode_golden_hex(include_str!("../tests/golden/literal-abc-v1.hex"));
        assert_eq!(archive, golden);
        assert_eq!(
            sha256(&archive),
            [
                0x53, 0xa8, 0xee, 0x7c, 0x31, 0x5a, 0x7d, 0x41, 0x1f, 0x7b, 0x95, 0x9f, 0x1a, 0x38,
                0xfe, 0xaa, 0x24, 0xcf, 0xd9, 0x2e, 0xfe, 0x92, 0xe1, 0xf4, 0x8c, 0x8f, 0xec, 0x97,
                0x85, 0xff, 0xa5, 0xd5,
            ]
        );
        assert_eq!(archive.len(), 483);
        let decoded = decode_archive(&archive, &limits()).unwrap();
        assert_eq!(decoded.original_size, 3);
        assert_eq!(decoded.blocks.len(), 1);
        let metadata = &decoded.blocks[0].metadata;
        assert_eq!(metadata.original_offset, 0);
        assert_eq!(metadata.original_length, 3);
        assert_eq!(metadata.payload_offset, 272);
        assert_eq!(metadata.payload_length, 83);
        assert_eq!(metadata.definition_count, 0);
        assert_eq!(metadata.node_count, 2);
        assert_eq!(metadata.edge_count, 1);
        assert_eq!(metadata.max_graph_depth, 2);
        assert_eq!(metadata.work_units, 5);
        assert_eq!(metadata.workspace_bytes, 0);
        let mut restored = Vec::new();
        let verified = decoded
            .verify_restored_with(literal_evaluator, |_, block| {
                restored.extend_from_slice(block);
                Ok(())
            })
            .unwrap();
        assert_eq!(restored, b"abc");
        assert_eq!(verified.original_sha256, sha256(b"abc"));
    }

    #[test]
    fn native_entropy_archive_round_trips_through_strict_container() {
        let restored = b"strict native LZ container round-trip; ".repeat(100);
        let program = Program {
            definitions: Vec::new(),
            root: Node::File {
                original_length: restored.len() as u64,
                child: Box::new(Node::EntropyLiteral(
                    encode(LeafCodec::LzTokens, &restored).unwrap(),
                )),
            },
        };
        let archive = encode_archive(
            &[ArchiveBlock {
                program: &program,
                restored: &restored,
            }],
            &limits(),
        )
        .unwrap();
        let decoded = decode_archive(&archive, &limits()).unwrap();
        assert_eq!(decoded.blocks[0].program, program);
        assert_eq!(decoded.blocks[0].metadata.workspace_bytes, 0);

        let mut output = Vec::new();
        let verified = decoded
            .verify_restored_with(
                |parsed| evaluate_program(parsed, &limits()),
                |_, bytes| {
                    output.extend_from_slice(bytes);
                    Ok(())
                },
            )
            .unwrap();
        assert_eq!(output, restored);
        assert_eq!(verified.original_sha256, sha256(&output));
    }

    #[test]
    fn native_coordinate_archive_round_trips_through_strict_container() {
        let restored: Vec<u8> = (0..17)
            .map(|index| ((index * 29 + 7) & 0xff) as u8)
            .collect();
        let descriptor = NativeCoordinateDescriptor {
            original_bytes: restored.len() as u64,
            transform: CoordinateTransform::BitPlane,
        };
        let transformed = forward(&restored, &descriptor, &limits()).unwrap();
        let program = Program {
            definitions: Vec::new(),
            root: Node::File {
                original_length: restored.len() as u64,
                child: Box::new(Node::Coordinate {
                    descriptor,
                    child: Box::new(Node::Literal(transformed.clone())),
                }),
            },
        };
        let archive = encode_archive(
            &[ArchiveBlock {
                program: &program,
                restored: &restored,
            }],
            &limits(),
        )
        .unwrap();
        let decoded = decode_archive(&archive, &limits()).unwrap();
        assert_eq!(decoded.blocks[0].program, program);
        assert_eq!(
            decoded.blocks[0].metadata.workspace_bytes,
            transformed.len() as u64
        );

        let mut output = Vec::new();
        let verified = decoded
            .verify_restored_with(
                |parsed| evaluate_program(parsed, &limits()),
                |_, bytes| {
                    output.extend_from_slice(bytes);
                    Ok(())
                },
            )
            .unwrap();
        assert_eq!(output, restored);
        assert_eq!(verified.original_sha256, sha256(&output));
    }

    #[test]
    fn strict_coordinate_verification_rejects_noncanonical_bit_padding() {
        let restored = vec![0x5a; 17];
        let descriptor = NativeCoordinateDescriptor {
            original_bytes: restored.len() as u64,
            transform: CoordinateTransform::BitPlane,
        };
        let mut transformed = forward(&restored, &descriptor, &limits()).unwrap();
        transformed[2] |= 0x02;
        let program = Program {
            definitions: Vec::new(),
            root: Node::File {
                original_length: restored.len() as u64,
                child: Box::new(Node::Coordinate {
                    descriptor,
                    child: Box::new(Node::Literal(transformed)),
                }),
            },
        };
        let archive = encode_archive(
            &[ArchiveBlock {
                program: &program,
                restored: &restored,
            }],
            &limits(),
        )
        .unwrap();
        let decoded = decode_archive(&archive, &limits()).unwrap();
        let mut callback_called = false;
        let result = decoded.verify_restored_with(
            |parsed| evaluate_program(parsed, &limits()),
            |_, _| {
                callback_called = true;
                Ok(())
            },
        );
        assert_eq!(
            result,
            Err(Error::InvalidValue(
                "BIT_PLANE high padding bits must be zero"
            ))
        );
        assert!(!callback_called);
    }

    #[test]
    fn independent_blocks_have_exact_contiguous_offsets_and_totals() {
        let first_program = Program::literal(b"hello".to_vec());
        let second_program = Program::literal(b"world!".to_vec());
        let blocks = [
            ArchiveBlock {
                program: &first_program,
                restored: b"hello",
            },
            ArchiveBlock {
                program: &second_program,
                restored: b"world!",
            },
        ];
        let archive = encode_archive(&blocks, &limits()).unwrap();
        let decoded = decode_archive(&archive, &limits()).unwrap();
        assert_eq!(decoded.original_size, 11);
        assert_eq!(decoded.blocks.len(), 2);
        assert_eq!(decoded.blocks[0].metadata.original_offset, 0);
        assert_eq!(decoded.blocks[1].metadata.original_offset, 5);
        assert_eq!(
            decoded.blocks[1].metadata.payload_offset,
            decoded.blocks[0].metadata.payload_offset + decoded.blocks[0].metadata.payload_length
        );
        assert_eq!(decoded.total_node_count, 4);
        let mut output = Vec::new();
        decoded
            .verify_restored_with(literal_evaluator, |_, bytes| {
                output.extend_from_slice(bytes);
                Ok(())
            })
            .unwrap();
        assert_eq!(output, b"helloworld!");
    }

    #[test]
    fn local_definitions_parse_under_block_local_scope() {
        let program = Program {
            definitions: vec![Node::Literal(b"abc".to_vec())],
            root: Node::File {
                original_length: 6,
                child: Box::new(Node::Concat(vec![
                    Node::Reference {
                        definition: 0,
                        parameter_delta: Vec::new(),
                    },
                    Node::Reference {
                        definition: 0,
                        parameter_delta: Vec::new(),
                    },
                ])),
            },
        };
        let archive = encode_archive(
            &[ArchiveBlock {
                program: &program,
                restored: b"abcabc",
            }],
            &limits(),
        )
        .unwrap();
        let decoded = decode_archive(&archive, &limits()).unwrap();
        assert_eq!(decoded.blocks[0].program, program);
        assert_eq!(decoded.blocks[0].metadata.definition_count, 1);
        let report = program.validate(&limits()).unwrap();
        assert_eq!(decoded.blocks[0].metadata.work_units, report.decode_work);
        assert_eq!(
            decoded.blocks[0].metadata.workspace_bytes,
            report.temporary_bytes
        );
    }

    #[test]
    fn corruption_in_each_physical_region_is_rejected() {
        let archive = encode_literal_archive(b"corruption target", &limits()).unwrap();
        let positions = [
            16,
            FILE_HEADER_BYTES + 8,
            FILE_HEADER_BYTES + DIRECTORY_RECORD_BYTES + BLOCK_HEADER_BYTES,
            archive.len() - FILE_FOOTER_BYTES + 16,
        ];
        for position in positions {
            let mut corrupt = archive.clone();
            corrupt[position] ^= 0x80;
            assert!(decode_archive(&corrupt, &limits()).is_err(), "{position}");
        }
    }

    #[test]
    fn every_truncation_and_single_byte_flip_of_golden_literal_is_rejected() {
        let archive = encode_literal_archive(b"abc", &limits()).unwrap();
        for length in 0..archive.len() {
            assert!(
                decode_archive(&archive[..length], &limits()).is_err(),
                "truncation at {length}"
            );
        }
        for position in 0..archive.len() {
            let mut corrupt = archive.clone();
            corrupt[position] ^= 1;
            assert!(
                decode_archive(&corrupt, &limits()).is_err(),
                "single-byte corruption at {position}"
            );
        }
    }

    #[test]
    fn fully_rehashed_redundant_metadata_lies_are_rejected() {
        let archive = encode_literal_archive(b"abc", &limits()).unwrap();
        let payload_offset = 272;

        // Make directory, block header and file total all agree on the same
        // false node count, then repair every public checksum/hash. Parsing the
        // actual Program must still reject the lie.
        let mut node_count = archive.clone();
        put_u32_at(&mut node_count, FILE_HEADER_BYTES + 36, 3);
        put_u32_at(&mut node_count, payload_offset + 28, 3);
        put_u64_at(&mut node_count, 72, 3);
        recalculate_crc(
            &mut node_count[payload_offset..payload_offset + BLOCK_HEADER_BYTES],
            BLOCK_HEADER_BYTES - 4,
        );
        rehash_single_block_archive(&mut node_count);
        assert!(decode_archive(&node_count, &limits()).is_err());

        // Workspace has no file-total field. Matching directory/header lies
        // are caught only by recomputing the normative Program workspace.
        let mut workspace = archive.clone();
        put_u64_at(&mut workspace, FILE_HEADER_BYTES + 64, 1);
        put_u64_at(&mut workspace, payload_offset + 48, 1);
        recalculate_crc(
            &mut workspace[payload_offset..payload_offset + BLOCK_HEADER_BYTES],
            BLOCK_HEADER_BYTES - 4,
        );
        rehash_single_block_archive(&mut workspace);
        assert!(decode_archive(&workspace, &limits()).is_err());

        // A fully rehashed directory with a non-contiguous original range is
        // invalid even though all public digests are internally consistent.
        let mut offset = archive;
        put_u64_at(&mut offset, FILE_HEADER_BYTES, 1);
        rehash_single_block_archive(&mut offset);
        assert!(decode_archive(&offset, &limits()).is_err());
    }

    #[test]
    fn reserved_flags_lengths_trailing_and_noncanonical_empty_are_rejected() {
        let archive = encode_literal_archive(b"x", &limits()).unwrap();

        let mut flags = archive.clone();
        flags[8] = 1;
        recalculate_crc(&mut flags[..FILE_HEADER_BYTES], 124);
        assert!(decode_archive(&flags, &limits()).is_err());

        let mut reserved = archive.clone();
        reserved[120] = 1;
        recalculate_crc(&mut reserved[..FILE_HEADER_BYTES], 124);
        assert!(decode_archive(&reserved, &limits()).is_err());

        let mut trailing = archive.clone();
        trailing.push(0);
        assert!(decode_archive(&trailing, &limits()).is_err());

        let mut fake_empty = archive;
        fake_empty[16..24].fill(0);
        recalculate_crc(&mut fake_empty[..FILE_HEADER_BYTES], 124);
        assert!(decode_archive(&fake_empty, &limits()).is_err());
    }

    #[test]
    fn configured_limits_and_hard_v1_limits_are_enforced() {
        let archive = encode_literal_archive(b"abcd", &limits()).unwrap();
        let small = Limits {
            max_archive_bytes: archive.len() as u64 - 1,
            ..limits()
        };
        assert!(decode_archive(&archive, &small).is_err());
        assert!(encode_literal_archive(b"abcd", &small).is_err());

        let small_output = Limits {
            max_output_bytes: 3,
            ..limits()
        };
        assert!(decode_archive(&archive, &small_output).is_err());
        assert!(encode_literal_archive(b"abcd", &small_output).is_err());

        let small_block = Limits {
            max_block_output_bytes: 3,
            ..limits()
        };
        assert!(decode_archive(&archive, &small_block).is_err());
        assert!(encode_literal_archive(b"abcd", &small_block).is_err());

        let raised = Limits {
            max_block_output_bytes: u64::MAX,
            max_block_payload_bytes: u64::MAX,
            max_definitions: u64::MAX,
            max_nodes: u64::MAX,
            max_edges: u64::MAX,
            max_graph_depth: u64::MAX,
            max_temporary_bytes: u64::MAX,
            max_work: u64::MAX,
            ..limits()
        };
        let effective = v1_limits(&raised);
        assert_eq!(effective.max_block_output_bytes, V1_MAX_BLOCK_OUTPUT_BYTES);
        assert_eq!(
            effective.max_block_payload_bytes,
            V1_MAX_BLOCK_PAYLOAD_BYTES
        );
        assert_eq!(effective.max_definitions, V1_MAX_DEFINITIONS);
        assert_eq!(effective.max_nodes, V1_MAX_NODES);
        assert_eq!(effective.max_edges, V1_MAX_EDGES);
        assert_eq!(effective.max_graph_depth, V1_MAX_GRAPH_DEPTH);
        assert_eq!(effective.max_temporary_bytes, V1_MAX_WORKSPACE_BYTES);
        assert_eq!(effective.max_work, V1_MAX_WORK_UNITS);
    }

    #[test]
    fn restored_hash_mismatch_is_deferred_but_never_accepted() {
        let program = Program {
            definitions: Vec::new(),
            root: Node::File {
                original_length: 3,
                child: Box::new(Node::Const {
                    length: 3,
                    value: b'a',
                }),
            },
        };
        // Length is structurally valid, but these bytes do not match evaluation.
        let archive = encode_archive(
            &[ArchiveBlock {
                program: &program,
                restored: b"bbb",
            }],
            &limits(),
        )
        .unwrap();
        let decoded = decode_archive(&archive, &limits()).unwrap();
        let result = decoded.verify_restored_with(
            |_| Ok(b"aaa".to_vec()),
            |_, _| Err(Error::InvalidValue("callback must not run")),
        );
        assert!(matches!(
            result,
            Err(Error::InvalidValue("restored block SHA-256 mismatch"))
        ));
    }

    #[test]
    fn random_literal_structural_round_trips_are_deterministic() {
        let mut state = 0x9e37_79b9_7f4a_7c15u64;
        for length in [1usize, 2, 31, 127, 128, 255, 256, 1024, 4096] {
            let mut bytes = Vec::with_capacity(length);
            for _ in 0..length {
                state = state
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1);
                bytes.push((state >> 56) as u8);
            }
            let first = encode_literal_archive(&bytes, &limits()).unwrap();
            let second = encode_literal_archive(&bytes, &limits()).unwrap();
            assert_eq!(first, second);
            let decoded = decode_archive(&first, &limits()).unwrap();
            assert_eq!(decoded.blocks.len(), 1);
            let mut restored = Vec::new();
            decoded
                .verify_restored_with(literal_evaluator, |_, block| {
                    restored.extend_from_slice(block);
                    Ok(())
                })
                .unwrap();
            assert_eq!(restored, bytes);
        }
    }

    #[test]
    fn root_type_is_always_bytes() {
        let program = Program::literal(vec![1]);
        let report = program.validate(&limits()).unwrap();
        assert_eq!(report.root_type, ValueType::Bytes(1));
    }
}
