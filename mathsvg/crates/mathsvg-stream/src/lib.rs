//! Bounded block-stream orchestration for native MathSVG.
//!
//! The container crate owns the frozen wire format. This crate connects its
//! incremental encoder/decoder to the exact optimizer and evaluator without
//! ever materializing a complete source file, archive, or restored file.

#![forbid(unsafe_code)]

use std::io::{Read, Seek, Write};

use mathsvg_container::{
    decode_archive_stream, ArchiveBlock, ArchiveStreamEncoder, StreamDecodeReport,
    StreamEncodeReport, StreamError, StreamResult,
};
use mathsvg_core::{checked_u64_add, Error, Limits};
use mathsvg_evaluator::{
    evaluate_container_validated_program, evaluate_container_validated_program_with_backend,
    resolve_evaluation_backend, ActiveBackend, EvaluationBackend,
};
use mathsvg_optimizer::{
    optimize, optimize_portfolio, ArchiveSelection, CoverageBreakdown, OptimizationResult,
    OptimizerConfig, PortfolioConfig, SearchUsage,
};

pub const MAX_COMPRESSION_THREADS: usize = 64;

/// Auditable aggregate facts from a streaming compression.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StreamCompressionReport {
    pub envelope: StreamEncodeReport,
    pub threads: u32,
    pub input_blocks: u64,
    pub procedural_blocks: u64,
    pub literal_fallback_blocks: u64,
    pub maximum_input_block_bytes: u64,
    /// Largest sum of source-block buffers concurrently owned by the
    /// deterministic batch executor.
    pub maximum_inflight_input_bytes: u64,
    /// Largest complete one-block optimizer archive retained at once.
    pub maximum_optimizer_archive_bytes: u64,
    pub selected_segments: u64,
    pub literal_source_bytes: u64,
    pub function_source_bytes: u64,
    pub coordinate_source_bytes: u64,
    pub residual_source_bytes: u64,
    pub graph_source_bytes: u64,
    pub symbolic_source_bytes: u64,
    pub other_source_bytes: u64,
    pub selected_leaf_node_bytes: u64,
    pub search: SearchUsage,
}

impl StreamCompressionReport {
    pub fn procedural_source_bytes(&self) -> u64 {
        self.function_source_bytes
            .saturating_add(self.coordinate_source_bytes)
            .saturating_add(self.residual_source_bytes)
            .saturating_add(self.graph_source_bytes)
            .saturating_add(self.symbolic_source_bytes)
            .saturating_add(self.other_source_bytes)
    }
}

/// Compress `input` into one canonical archive using a seekable payload spool.
///
/// The read buffer is exactly `optimizer_config.block_bytes`; each block is
/// optimized and discarded before the next is read. The final `output` can be
/// partially written after an I/O failure, so file callers should pass an
/// uncommitted temporary file and atomically publish it only on success.
pub fn compress_reader<R, S, W>(
    input: &mut R,
    payload_spool: S,
    output: &mut W,
    optimizer_config: &OptimizerConfig,
    limits: &Limits,
) -> StreamResult<StreamCompressionReport>
where
    R: Read,
    S: Read + Write + Seek,
    W: Write,
{
    compress_reader_with(
        input,
        payload_spool,
        output,
        optimizer_config,
        limits,
        |block| optimize(block, optimizer_config, limits),
    )
}

/// Compress with the oracle-gated built-in native engine portfolio.
///
/// This preserves the same bounded one-block lifecycle as [`compress_reader`]
/// while allowing coordinate and any explicitly enabled portfolio providers
/// to compete before the block is spooled.
pub fn compress_reader_portfolio<R, S, W>(
    input: &mut R,
    payload_spool: S,
    output: &mut W,
    portfolio_config: &PortfolioConfig,
    limits: &Limits,
) -> StreamResult<StreamCompressionReport>
where
    R: Read,
    S: Read + Write + Seek,
    W: Write,
{
    compress_reader_with(
        input,
        payload_spool,
        output,
        &portfolio_config.optimizer,
        limits,
        |block| optimize_portfolio(block, portfolio_config, limits),
    )
}

/// Deterministically optimize independent blocks in bounded parallel batches.
///
/// Worker completion order is never observed. Results are joined and emitted
/// in source-block order, so every supported thread count produces the exact
/// same archive as [`compress_reader_portfolio`]. At most `threads` source
/// blocks and their bounded optimizer states are live concurrently.
pub fn compress_reader_portfolio_threads<R, S, W>(
    input: &mut R,
    payload_spool: S,
    output: &mut W,
    portfolio_config: &PortfolioConfig,
    limits: &Limits,
    threads: usize,
) -> StreamResult<StreamCompressionReport>
where
    R: Read,
    S: Read + Write + Seek,
    W: Write,
{
    if threads == 1 {
        return compress_reader_portfolio(input, payload_spool, output, portfolio_config, limits);
    }
    if threads == 0 || threads > MAX_COMPRESSION_THREADS {
        return Err(StreamError::Format(Error::LimitExceeded {
            what: "compression threads",
            actual: threads as u64,
            limit: MAX_COMPRESSION_THREADS as u64,
        }));
    }
    let optimizer_config = &portfolio_config.optimizer;
    if optimizer_config.block_bytes == 0 {
        return Err(StreamError::Format(Error::InvalidValue(
            "stream optimizer block size must be positive",
        )));
    }
    limits.check(
        "stream block output bytes",
        u64::from(optimizer_config.block_bytes),
        limits.max_block_output_bytes,
    )?;
    let block_capacity = usize::try_from(optimizer_config.block_bytes).map_err(|_| {
        StreamError::Format(Error::IntegerOverflow {
            context: "stream input block allocation",
        })
    })?;
    let mut encoder = ArchiveStreamEncoder::new(payload_spool, limits)?;
    let mut aggregate = Aggregate::new(threads)?;

    loop {
        let mut batch = Vec::with_capacity(threads);
        let mut inflight_input_bytes = 0u64;
        for _ in 0..threads {
            let mut block = vec![0u8; block_capacity];
            let length = read_block(input, &mut block)?;
            if length == 0 {
                break;
            }
            block.truncate(length);
            inflight_input_bytes = add(
                inflight_input_bytes,
                length as u64,
                "parallel inflight input bytes",
            )?;
            batch.push(block);
        }
        if batch.is_empty() {
            break;
        }

        let results = std::thread::scope(|scope| {
            let handles = batch
                .iter()
                .map(|block| {
                    scope.spawn(move || optimize_portfolio(block, portfolio_config, limits))
                })
                .collect::<Vec<_>>();
            handles
                .into_iter()
                .map(|handle| match handle.join() {
                    Ok(result) => result,
                    Err(_) => Err(Error::InvalidValue("optimizer worker panicked")),
                })
                .collect::<Vec<_>>()
        });

        for (block, optimized) in batch.iter().zip(results) {
            let optimized = optimized?;
            if optimized.programs.len() != 1 {
                return Err(StreamError::Format(Error::InvalidValue(
                    "one streaming input block produced multiple archive programs",
                )));
            }
            encoder.push_block(ArchiveBlock {
                program: &optimized.programs[0],
                restored: block,
            })?;
            aggregate.observe(
                optimized.selection,
                &optimized.coverage,
                &optimized.usage,
                optimized.archive.len() as u64,
                block.len() as u64,
                inflight_input_bytes,
            )?;
        }
    }

    let envelope = encoder.finish(output)?;
    finish_compression(aggregate, envelope)
}

fn compress_reader_with<R, S, W, F>(
    input: &mut R,
    payload_spool: S,
    output: &mut W,
    optimizer_config: &OptimizerConfig,
    limits: &Limits,
    mut optimize_block: F,
) -> StreamResult<StreamCompressionReport>
where
    R: Read,
    S: Read + Write + Seek,
    W: Write,
    F: FnMut(&[u8]) -> mathsvg_core::Result<OptimizationResult>,
{
    if optimizer_config.block_bytes == 0 {
        return Err(StreamError::Format(Error::InvalidValue(
            "stream optimizer block size must be positive",
        )));
    }
    limits.check(
        "stream block output bytes",
        u64::from(optimizer_config.block_bytes),
        limits.max_block_output_bytes,
    )?;
    let block_capacity = usize::try_from(optimizer_config.block_bytes).map_err(|_| {
        StreamError::Format(Error::IntegerOverflow {
            context: "stream input block allocation",
        })
    })?;
    let mut encoder = ArchiveStreamEncoder::new(payload_spool, limits)?;
    let mut aggregate = Aggregate::new(1)?;
    let mut buffer = vec![0u8; block_capacity];

    loop {
        let length = read_block(input, &mut buffer)?;
        if length == 0 {
            break;
        }
        let block = &buffer[..length];
        let optimized = optimize_block(block)?;
        if optimized.programs.len() != 1 {
            return Err(StreamError::Format(Error::InvalidValue(
                "one streaming input block produced multiple archive programs",
            )));
        }
        let program = &optimized.programs[0];
        encoder.push_block(ArchiveBlock {
            program,
            restored: block,
        })?;
        aggregate.observe(
            optimized.selection,
            &optimized.coverage,
            &optimized.usage,
            optimized.archive.len() as u64,
            length as u64,
            length as u64,
        )?;
    }

    let envelope = encoder.finish(output)?;
    finish_compression(aggregate, envelope)
}

fn finish_compression(
    aggregate: Aggregate,
    envelope: StreamEncodeReport,
) -> StreamResult<StreamCompressionReport> {
    if envelope.original_size != aggregate.original_bytes {
        return Err(StreamError::Format(Error::InvalidValue(
            "stream optimizer accounting differs from envelope length",
        )));
    }
    if envelope.block_count != aggregate.input_blocks {
        return Err(StreamError::Format(Error::InvalidValue(
            "stream optimizer accounting differs from envelope block count",
        )));
    }
    Ok(aggregate.finish(envelope))
}

/// Strictly decode an archive to a caller-owned uncommitted output spool.
///
/// The caller must not publish `output_spool` unless this function succeeds:
/// verified blocks may be written before the final footer and whole-file hash
/// can be checked.
pub fn decompress_reader_to_spool<R, W>(
    input: &mut R,
    output_spool: &mut W,
    limits: &Limits,
) -> StreamResult<StreamDecodeReport>
where
    R: Read + Seek,
    W: Write,
{
    decode_archive_stream(
        input,
        limits,
        |program| evaluate_container_validated_program(program, limits),
        |_, block| output_spool.write_all(block),
    )
}

/// Backend-selecting counterpart of [`decompress_reader_to_spool`].
pub fn decompress_reader_to_spool_with_backend<R, W>(
    input: &mut R,
    output_spool: &mut W,
    limits: &Limits,
    backend: EvaluationBackend,
) -> StreamResult<(StreamDecodeReport, ActiveBackend)>
where
    R: Read + Seek,
    W: Write,
{
    let active = resolve_evaluation_backend(backend).map_err(StreamError::Format)?;
    let report = decode_archive_stream(
        input,
        limits,
        |program| {
            evaluate_container_validated_program_with_backend(program, limits, backend)
                .map(|evaluated| evaluated.bytes)
        },
        |_, block| output_spool.write_all(block),
    )?;
    Ok((report, active))
}

fn read_block<R>(input: &mut R, buffer: &mut [u8]) -> StreamResult<usize>
where
    R: Read,
{
    let mut filled = 0usize;
    while filled < buffer.len() {
        match input.read(&mut buffer[filled..]) {
            Ok(0) => break,
            Ok(amount) => {
                filled = filled.checked_add(amount).ok_or(StreamError::Format(
                    Error::IntegerOverflow {
                        context: "stream input block length",
                    },
                ))?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(error) => return Err(StreamError::Io(error)),
        }
    }
    Ok(filled)
}

#[derive(Default)]
struct Aggregate {
    threads: u32,
    original_bytes: u64,
    input_blocks: u64,
    procedural_blocks: u64,
    literal_fallback_blocks: u64,
    maximum_input_block_bytes: u64,
    maximum_inflight_input_bytes: u64,
    maximum_optimizer_archive_bytes: u64,
    selected_segments: u64,
    literal_source_bytes: u64,
    function_source_bytes: u64,
    coordinate_source_bytes: u64,
    residual_source_bytes: u64,
    graph_source_bytes: u64,
    symbolic_source_bytes: u64,
    other_source_bytes: u64,
    selected_leaf_node_bytes: u64,
    candidates: u64,
    states: u64,
    work: u64,
    ledger_entries: u64,
    budget_exhausted: bool,
    complete_within_declared_catalogue: bool,
}

impl Aggregate {
    fn new(threads: usize) -> StreamResult<Self> {
        let threads = u32::try_from(threads).map_err(|_| {
            StreamError::Format(Error::IntegerOverflow {
                context: "stream compression thread count",
            })
        })?;
        Ok(Self {
            threads,
            complete_within_declared_catalogue: true,
            ..Self::default()
        })
    }

    fn observe(
        &mut self,
        selection: ArchiveSelection,
        coverage: &CoverageBreakdown,
        usage: &SearchUsage,
        optimizer_archive_bytes: u64,
        input_bytes: u64,
        inflight_input_bytes: u64,
    ) -> StreamResult<()> {
        self.original_bytes = add(self.original_bytes, input_bytes, "stream original bytes")?;
        self.input_blocks = add(self.input_blocks, 1, "stream input blocks")?;
        match selection {
            ArchiveSelection::LiteralFallback => {
                self.literal_fallback_blocks = add(
                    self.literal_fallback_blocks,
                    1,
                    "stream literal fallback blocks",
                )?;
            }
            ArchiveSelection::Procedural => {
                self.procedural_blocks =
                    add(self.procedural_blocks, 1, "stream procedural blocks")?;
            }
        }
        self.maximum_input_block_bytes = self.maximum_input_block_bytes.max(input_bytes);
        self.maximum_inflight_input_bytes =
            self.maximum_inflight_input_bytes.max(inflight_input_bytes);
        self.maximum_optimizer_archive_bytes = self
            .maximum_optimizer_archive_bytes
            .max(optimizer_archive_bytes);
        self.selected_segments = add(
            self.selected_segments,
            coverage.segment_count,
            "stream selected segments",
        )?;
        self.literal_source_bytes = add(
            self.literal_source_bytes,
            coverage.literal_source_bytes,
            "stream literal source bytes",
        )?;
        self.function_source_bytes = add(
            self.function_source_bytes,
            coverage.function_source_bytes,
            "stream function source bytes",
        )?;
        self.coordinate_source_bytes = add(
            self.coordinate_source_bytes,
            coverage.coordinate_source_bytes,
            "stream coordinate source bytes",
        )?;
        self.residual_source_bytes = add(
            self.residual_source_bytes,
            coverage.residual_source_bytes,
            "stream residual source bytes",
        )?;
        self.graph_source_bytes = add(
            self.graph_source_bytes,
            coverage.graph_source_bytes,
            "stream graph source bytes",
        )?;
        self.symbolic_source_bytes = add(
            self.symbolic_source_bytes,
            coverage.symbolic_source_bytes,
            "stream symbolic source bytes",
        )?;
        self.other_source_bytes = add(
            self.other_source_bytes,
            coverage.other_source_bytes,
            "stream other source bytes",
        )?;
        self.selected_leaf_node_bytes = add(
            self.selected_leaf_node_bytes,
            coverage.leaf_node_bytes,
            "stream selected leaf node bytes",
        )?;
        self.candidates = add(
            self.candidates,
            usage.candidates,
            "stream search candidates",
        )?;
        self.states = add(self.states, usage.states, "stream search states")?;
        self.work = add(self.work, usage.work, "stream search work")?;
        self.ledger_entries = add(
            self.ledger_entries,
            usage.ledger_entries,
            "stream ledger entries",
        )?;
        self.budget_exhausted |= usage.budget_exhausted;
        self.complete_within_declared_catalogue &= usage.complete_within_declared_catalogue;
        Ok(())
    }

    fn finish(self, envelope: StreamEncodeReport) -> StreamCompressionReport {
        StreamCompressionReport {
            envelope,
            threads: self.threads,
            input_blocks: self.input_blocks,
            procedural_blocks: self.procedural_blocks,
            literal_fallback_blocks: self.literal_fallback_blocks,
            maximum_input_block_bytes: self.maximum_input_block_bytes,
            maximum_inflight_input_bytes: self.maximum_inflight_input_bytes,
            maximum_optimizer_archive_bytes: self.maximum_optimizer_archive_bytes,
            selected_segments: self.selected_segments,
            literal_source_bytes: self.literal_source_bytes,
            function_source_bytes: self.function_source_bytes,
            coordinate_source_bytes: self.coordinate_source_bytes,
            residual_source_bytes: self.residual_source_bytes,
            graph_source_bytes: self.graph_source_bytes,
            symbolic_source_bytes: self.symbolic_source_bytes,
            other_source_bytes: self.other_source_bytes,
            selected_leaf_node_bytes: self.selected_leaf_node_bytes,
            search: SearchUsage {
                candidates: self.candidates,
                states: self.states,
                work: self.work,
                ledger_entries: self.ledger_entries,
                budget_exhausted: self.budget_exhausted,
                complete_within_declared_catalogue: self.complete_within_declared_catalogue,
            },
        }
    }
}

fn add(left: u64, right: u64, context: &'static str) -> StreamResult<u64> {
    checked_u64_add(left, right, context).map_err(StreamError::Format)
}

#[cfg(test)]
mod tests {
    use super::*;
    use mathsvg_container::decode_archive;
    use std::io::Cursor;

    fn limits() -> Limits {
        Limits {
            max_archive_bytes: 64 * 1024 * 1024,
            max_output_bytes: 32 * 1024 * 1024,
            ..Limits::default()
        }
    }

    fn config() -> OptimizerConfig {
        OptimizerConfig {
            block_bytes: 1024,
            microblock_bytes: 256,
            max_segments_per_block: 4,
            max_states: 1 << 12,
            max_candidates: 1 << 10,
            work_budget: 1 << 24,
            max_ledger_entries: 1 << 11,
            ..OptimizerConfig::default()
        }
    }

    #[test]
    fn empty_stream_round_trips_canonically() {
        let mut input = Cursor::new(Vec::<u8>::new());
        let spool = Cursor::new(Vec::new());
        let mut archive = Cursor::new(Vec::new());
        let compressed =
            compress_reader(&mut input, spool, &mut archive, &config(), &limits()).unwrap();
        assert_eq!(compressed.input_blocks, 0);
        assert_eq!(compressed.envelope.block_count, 0);

        archive.set_position(0);
        let mut restored = Cursor::new(Vec::new());
        let decoded = decompress_reader_to_spool(&mut archive, &mut restored, &limits()).unwrap();
        assert_eq!(decoded.verified.original_size, 0);
        assert!(restored.into_inner().is_empty());
    }

    #[test]
    fn multiblock_stream_is_deterministic_lossless_and_bounded() {
        let mut source = Vec::new();
        source.extend((0..2_500).map(|index| (index % 7) as u8));
        source.extend((0..2_000).map(|index| ((index * 101 + 17) & 0xff) as u8));

        let compress = |bytes: &[u8]| {
            let mut input = Cursor::new(bytes.to_vec());
            let spool = Cursor::new(Vec::new());
            let mut archive = Cursor::new(Vec::new());
            let report =
                compress_reader(&mut input, spool, &mut archive, &config(), &limits()).unwrap();
            (archive.into_inner(), report)
        };
        let (first, first_report) = compress(&source);
        let (second, second_report) = compress(&source);
        assert_eq!(first, second);
        assert_eq!(first_report, second_report);
        assert_eq!(first_report.input_blocks, 5);
        assert!(first_report.maximum_input_block_bytes <= 1024);
        assert_eq!(
            first_report.literal_source_bytes + first_report.procedural_source_bytes(),
            source.len() as u64
        );

        let decoded_in_memory = decode_archive(&first, &limits()).unwrap();
        assert_eq!(decoded_in_memory.blocks.len(), 5);
        let mut archive = Cursor::new(first);
        let mut restored = Cursor::new(Vec::new());
        let decode_report =
            decompress_reader_to_spool(&mut archive, &mut restored, &limits()).unwrap();
        assert_eq!(restored.into_inner(), source);
        assert!(decode_report.maximum_payload_buffer_bytes < decode_report.archive_bytes);
        assert!(decode_report.maximum_restored_buffer_bytes <= 1024);
        assert_eq!(decode_report.retained_directory_bytes, 0);
    }

    #[test]
    fn short_readers_are_filled_without_changing_archive() {
        struct ShortReader {
            bytes: Cursor<Vec<u8>>,
        }

        impl Read for ShortReader {
            fn read(&mut self, output: &mut [u8]) -> std::io::Result<usize> {
                let maximum = output.len().min(7);
                self.bytes.read(&mut output[..maximum])
            }
        }

        let source = b"deterministic short reads".repeat(131);
        let mut normal = Cursor::new(source.clone());
        let mut short = ShortReader {
            bytes: Cursor::new(source),
        };
        let mut normal_archive = Cursor::new(Vec::new());
        let mut short_archive = Cursor::new(Vec::new());
        compress_reader(
            &mut normal,
            Cursor::new(Vec::new()),
            &mut normal_archive,
            &config(),
            &limits(),
        )
        .unwrap();
        compress_reader(
            &mut short,
            Cursor::new(Vec::new()),
            &mut short_archive,
            &config(),
            &limits(),
        )
        .unwrap();
        assert_eq!(normal_archive.into_inner(), short_archive.into_inner());
    }

    #[test]
    fn portfolio_stream_preserves_the_direct_native_winner() {
        let source: Vec<u8> = (0..4096).map(|index| (index % 11) as u8).collect();
        let mut portfolio = PortfolioConfig::for_profile(mathsvg_optimizer::PortfolioProfile::Fast);
        portfolio.optimizer.block_bytes = source.len() as u32;
        portfolio.optimizer.microblock_bytes = 256;
        let direct = optimize_portfolio(&source, &portfolio, &limits()).unwrap();

        let mut input = Cursor::new(source);
        let mut streamed = Cursor::new(Vec::new());
        let report = compress_reader_portfolio(
            &mut input,
            Cursor::new(Vec::new()),
            &mut streamed,
            &portfolio,
            &limits(),
        )
        .unwrap();

        assert_eq!(streamed.into_inner(), direct.archive);
        assert_eq!(report.input_blocks, 1);
        assert_eq!(report.envelope.block_count, 1);
    }

    #[test]
    fn portfolio_thread_counts_emit_identical_archives_in_source_order() {
        let source: Vec<u8> = (0..4_501)
            .map(|index| ((index * 101 + index / 7 + 19) & 0xff) as u8)
            .collect();
        let mut portfolio = PortfolioConfig::for_profile(mathsvg_optimizer::PortfolioProfile::Fast);
        portfolio.optimizer = config();
        portfolio.enable_coordinates = false;

        let compress = |threads| {
            let mut input = Cursor::new(source.clone());
            let mut archive = Cursor::new(Vec::new());
            let report = compress_reader_portfolio_threads(
                &mut input,
                Cursor::new(Vec::new()),
                &mut archive,
                &portfolio,
                &limits(),
                threads,
            )
            .unwrap();
            (archive.into_inner(), report)
        };

        let (expected_archive, expected_report) = compress(1);
        for threads in [2, 4, 8] {
            let (archive, report) = compress(threads);
            assert_eq!(archive, expected_archive);
            assert_eq!(report.threads, threads as u32);
            assert!(
                report.maximum_inflight_input_bytes
                    <= report
                        .maximum_input_block_bytes
                        .saturating_mul(threads as u64)
            );

            let mut expected_semantics = expected_report.clone();
            expected_semantics.threads = 0;
            expected_semantics.maximum_inflight_input_bytes = 0;
            let mut actual_semantics = report;
            actual_semantics.threads = 0;
            actual_semantics.maximum_inflight_input_bytes = 0;
            assert_eq!(actual_semantics, expected_semantics);
        }
    }

    #[test]
    fn invalid_portfolio_thread_counts_are_rejected_before_reading() {
        let portfolio = PortfolioConfig::for_profile(mathsvg_optimizer::PortfolioProfile::Fast);
        for threads in [0, MAX_COMPRESSION_THREADS + 1] {
            let error = compress_reader_portfolio_threads(
                &mut Cursor::new(Vec::<u8>::new()),
                Cursor::new(Vec::new()),
                &mut Cursor::new(Vec::new()),
                &portfolio,
                &limits(),
                threads,
            )
            .unwrap_err();
            assert!(matches!(
                error,
                StreamError::Format(Error::LimitExceeded {
                    what: "compression threads",
                    ..
                })
            ));
        }
    }
}
