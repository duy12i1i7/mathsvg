use crate::wire::{encoded_len as varint_len, write_u64, Cursor};
use crate::{Error, Result};

const ALPHABET_SIZE: usize = 256;
const MAX_TREE_NODES: usize = ALPHABET_SIZE * 2 - 1;
const NO_NODE: u16 = u16::MAX;
const MAX_CODE_LENGTH: usize = ALPHABET_SIZE - 1;

#[derive(Clone, Copy, Debug)]
pub(crate) struct Analysis {
    pub(crate) payload_bytes: u64,
    pub(crate) symbol_count: u16,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Prepared {
    model: Model,
    analysis: Analysis,
    source_bytes: u64,
}

impl Prepared {
    pub(crate) const fn analysis(self) -> Analysis {
        self.analysis
    }

    pub(crate) const fn source_bytes(self) -> u64 {
        self.source_bytes
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct Frequencies {
    counts: [u64; ALPHABET_SIZE],
    total: u64,
}

impl Frequencies {
    pub(crate) const fn new() -> Self {
        Self {
            counts: [0; ALPHABET_SIZE],
            total: 0,
        }
    }

    pub(crate) fn observe(&mut self, byte: u8) -> Result<()> {
        let frequency = &mut self.counts[usize::from(byte)];
        *frequency = checked_add(*frequency, 1, "Huffman symbol frequency")?;
        self.total = checked_add(self.total, 1, "Huffman source length")?;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug)]
struct Model {
    lengths: [u8; ALPHABET_SIZE],
    present: [bool; ALPHABET_SIZE],
    symbol_count: u16,
    bit_length: u64,
}

#[derive(Clone, Copy, Debug)]
struct BuildNode {
    weight: u64,
    minimum_symbol: u16,
    parent: u16,
    active: bool,
}

impl BuildNode {
    const EMPTY: Self = Self {
        weight: 0,
        minimum_symbol: 0,
        parent: NO_NODE,
        active: false,
    };
}

#[derive(Clone, Copy, Debug, Default)]
struct CanonicalInteger {
    // Little-endian limbs. The largest byte-alphabet Huffman code is 255 bits.
    limbs: [u64; 4],
}

impl CanonicalInteger {
    fn increment(&mut self) -> Result<()> {
        for limb in &mut self.limbs {
            let (next, carried) = limb.overflowing_add(1);
            *limb = next;
            if !carried {
                return Ok(());
            }
        }
        Err(Error::IntegerOverflow {
            context: "canonical Huffman code",
        })
    }

    fn shift_left(&mut self, amount: usize) -> Result<()> {
        if amount == 0 {
            return Ok(());
        }
        if amount >= 256 {
            if self.limbs.iter().any(|&limb| limb != 0) {
                return Err(Error::IntegerOverflow {
                    context: "canonical Huffman code",
                });
            }
            return Ok(());
        }

        let source = self.limbs;
        self.limbs = [0; 4];
        let word_shift = amount / 64;
        let bit_shift = amount % 64;
        for (source_index, &limb) in source.iter().enumerate() {
            if limb == 0 {
                continue;
            }
            let destination_index =
                source_index
                    .checked_add(word_shift)
                    .ok_or(Error::IntegerOverflow {
                        context: "canonical Huffman code",
                    })?;
            if destination_index >= self.limbs.len() {
                return Err(Error::IntegerOverflow {
                    context: "canonical Huffman code",
                });
            }
            self.limbs[destination_index] |= limb << bit_shift;
            if bit_shift != 0 {
                let carry = limb >> (64 - bit_shift);
                if carry != 0 {
                    let carry_index =
                        destination_index
                            .checked_add(1)
                            .ok_or(Error::IntegerOverflow {
                                context: "canonical Huffman code",
                            })?;
                    if carry_index >= self.limbs.len() {
                        return Err(Error::IntegerOverflow {
                            context: "canonical Huffman code",
                        });
                    }
                    self.limbs[carry_index] |= carry;
                }
            }
        }
        Ok(())
    }

    fn bit(self, position: usize) -> bool {
        let limb = position / 64;
        let shift = position % 64;
        self.limbs[limb] & (1u64 << shift) != 0
    }

    fn fits(self, length: usize) -> bool {
        (length..256).all(|position| !self.bit(position))
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct Codeword {
    code: CanonicalInteger,
    length: u8,
}

impl Codeword {
    fn bit_from_most_significant(self, position: usize) -> Result<bool> {
        let length = usize::from(self.length);
        if position >= length {
            return Err(Error::InvalidValue(
                "Huffman code bit position is outside codeword",
            ));
        }
        Ok(self.code.bit(length - position - 1))
    }
}

#[derive(Clone, Copy, Debug)]
struct DecodeNode {
    children: [u16; 2],
    symbol: u16,
}

impl DecodeNode {
    const EMPTY: Self = Self {
        children: [NO_NODE; 2],
        symbol: NO_NODE,
    };
}

#[derive(Clone, Copy, Debug)]
struct Codebook {
    codewords: [Codeword; ALPHABET_SIZE],
    nodes: [DecodeNode; MAX_TREE_NODES],
    node_count: usize,
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

fn packed_bytes(bit_length: u64) -> Result<u64> {
    checked_add(bit_length, 7, "Huffman bitstream byte rounding").map(|rounded| rounded / 8)
}

fn select_minimum(nodes: &[BuildNode]) -> Option<usize> {
    let mut selected: Option<usize> = None;
    for (index, node) in nodes.iter().enumerate() {
        if !node.active {
            continue;
        }
        let replace = match selected {
            None => true,
            Some(current) => {
                let current_node = &nodes[current];
                (node.weight, node.minimum_symbol, index)
                    < (current_node.weight, current_node.minimum_symbol, current)
            }
        };
        if replace {
            selected = Some(index);
        }
    }
    selected
}

fn collect_frequencies(input: &[u8]) -> Result<Frequencies> {
    let mut frequencies = Frequencies::new();
    for &byte in input {
        frequencies.observe(byte)?;
    }
    Ok(frequencies)
}

fn build_model(frequencies: Frequencies) -> Result<Model> {
    let mut model = Model {
        lengths: [0; ALPHABET_SIZE],
        present: [false; ALPHABET_SIZE],
        symbol_count: 0,
        bit_length: 0,
    };
    let mut nodes = [BuildNode::EMPTY; MAX_TREE_NODES];
    let mut leaf_nodes = [NO_NODE; ALPHABET_SIZE];
    let mut node_count = 0usize;

    for (symbol, &frequency) in frequencies.counts.iter().enumerate() {
        if frequency == 0 {
            continue;
        }
        let node_index = node_count;
        node_count += 1;
        nodes[node_index] = BuildNode {
            weight: frequency,
            minimum_symbol: symbol as u16,
            parent: NO_NODE,
            active: true,
        };
        leaf_nodes[symbol] = node_index as u16;
        model.present[symbol] = true;
        model.symbol_count = model
            .symbol_count
            .checked_add(1)
            .ok_or(Error::IntegerOverflow {
                context: "Huffman symbol count",
            })?;
    }

    if model.symbol_count <= 1 {
        // Empty models have no table. A singleton uses its zero-length
        // codeword and therefore needs no bitstream.
        return Ok(model);
    }

    let mut active_count = usize::from(model.symbol_count);
    while active_count > 1 {
        let first = select_minimum(&nodes[..node_count])
            .ok_or(Error::InvalidValue("Huffman tree has no first merge node"))?;
        nodes[first].active = false;
        let second = select_minimum(&nodes[..node_count])
            .ok_or(Error::InvalidValue("Huffman tree has no second merge node"))?;
        nodes[second].active = false;

        if node_count >= nodes.len() {
            return Err(Error::InvalidValue("Huffman tree exceeds byte alphabet"));
        }
        let parent = node_count;
        node_count += 1;
        let weight = checked_add(
            nodes[first].weight,
            nodes[second].weight,
            "Huffman parent frequency",
        )?;
        nodes[parent] = BuildNode {
            weight,
            minimum_symbol: nodes[first]
                .minimum_symbol
                .min(nodes[second].minimum_symbol),
            parent: NO_NODE,
            active: true,
        };
        nodes[first].parent = parent as u16;
        nodes[second].parent = parent as u16;
        active_count -= 1;
    }

    for (symbol, &leaf_node) in leaf_nodes.iter().enumerate() {
        if !model.present[symbol] {
            continue;
        }
        let mut node = usize::from(leaf_node);
        let mut depth = 0usize;
        while nodes[node].parent != NO_NODE {
            depth = depth.checked_add(1).ok_or(Error::IntegerOverflow {
                context: "Huffman code length",
            })?;
            if depth > MAX_CODE_LENGTH {
                return Err(Error::InvalidValue(
                    "Huffman code length exceeds byte alphabet bound",
                ));
            }
            node = usize::from(nodes[node].parent);
        }
        let length = depth as u8;
        model.lengths[symbol] = length;
        model.bit_length = checked_add(
            model.bit_length,
            checked_mul(
                frequencies.counts[symbol],
                u64::from(length),
                "Huffman encoded bits",
            )?,
            "Huffman encoded bits",
        )?;
    }

    Ok(model)
}

fn analyse_model(model: Model) -> Result<Analysis> {
    let symbol_count = u64::from(model.symbol_count);
    let table_bytes = checked_mul(symbol_count, 2, "Huffman code-length table")?;
    let header_bytes = checked_add(
        u64::from(varint_len(symbol_count)),
        checked_add(
            table_bytes,
            u64::from(varint_len(model.bit_length)),
            "Huffman payload header",
        )?,
        "Huffman payload header",
    )?;
    Ok(Analysis {
        payload_bytes: checked_add(
            header_bytes,
            packed_bytes(model.bit_length)?,
            "Huffman payload",
        )?,
        symbol_count: model.symbol_count,
    })
}

pub(crate) fn analyse(input: &[u8]) -> Result<Analysis> {
    prepare(input).map(Prepared::analysis)
}

pub(crate) fn prepare(input: &[u8]) -> Result<Prepared> {
    prepare_frequencies(collect_frequencies(input)?)
}

pub(crate) fn prepare_frequencies(frequencies: Frequencies) -> Result<Prepared> {
    let model = build_model(frequencies)?;
    let analysis = analyse_model(model)?;
    Ok(Prepared {
        model,
        analysis,
        source_bytes: frequencies.total,
    })
}

fn validate_code_lengths(model: Model) -> Result<()> {
    let symbol_count = usize::from(model.symbol_count);
    if symbol_count <= 1 {
        return Ok(());
    }

    let mut counts = [0u16; MAX_CODE_LENGTH + 1];
    for symbol in 0..ALPHABET_SIZE {
        if !model.present[symbol] {
            continue;
        }
        let length = usize::from(model.lengths[symbol]);
        if length == 0 {
            return Err(Error::NonCanonical(
                "zero-length Huffman code in multi-symbol table",
            ));
        }
        counts[length] = counts[length]
            .checked_add(1)
            .ok_or(Error::IntegerOverflow {
                context: "Huffman code-length count",
            })?;
    }

    // At each depth, `slots` is the number of unused prefixes. A complete
    // binary prefix code must consume every slot. Because every remaining
    // slot needs at least one remaining symbol, values above that count prove
    // incompleteness without growing an unbounded Kraft accumulator.
    let mut slots = 1usize;
    let mut remaining_symbols = symbol_count;
    for &count in counts.iter().skip(1) {
        slots = slots.checked_mul(2).ok_or(Error::IntegerOverflow {
            context: "Huffman prefix slots",
        })?;
        let count = usize::from(count);
        if count > slots {
            return Err(Error::InvalidValue("oversubscribed Huffman code lengths"));
        }
        slots -= count;
        remaining_symbols -= count;
        if slots > remaining_symbols {
            return Err(Error::InvalidValue("incomplete Huffman code lengths"));
        }
        if remaining_symbols == 0 {
            break;
        }
    }
    if slots != 0 || remaining_symbols != 0 {
        return Err(Error::InvalidValue("incomplete Huffman code lengths"));
    }
    Ok(())
}

fn build_codebook(model: Model) -> Result<Codebook> {
    validate_code_lengths(model)?;
    let mut codebook = Codebook {
        codewords: [Codeword::default(); ALPHABET_SIZE],
        nodes: [DecodeNode::EMPTY; MAX_TREE_NODES],
        node_count: 1,
    };
    if model.symbol_count <= 1 {
        return Ok(codebook);
    }

    let mut code = CanonicalInteger::default();
    let mut previous_length = 0usize;
    let mut first = true;
    for length in 1..=MAX_CODE_LENGTH {
        for symbol in 0..ALPHABET_SIZE {
            if !model.present[symbol] || usize::from(model.lengths[symbol]) != length {
                continue;
            }
            if first {
                first = false;
            } else {
                code.increment()?;
                code.shift_left(length - previous_length)?;
            }
            if !code.fits(length) {
                return Err(Error::InvalidValue("oversubscribed Huffman code lengths"));
            }
            let word = Codeword {
                code,
                length: length as u8,
            };
            codebook.codewords[symbol] = word;
            previous_length = length;

            let mut node_index = 0usize;
            for position in 0..length {
                if codebook.nodes[node_index].symbol != NO_NODE {
                    return Err(Error::InvalidValue("oversubscribed Huffman code lengths"));
                }
                let branch = usize::from(word.bit_from_most_significant(position)?);
                let child = codebook.nodes[node_index].children[branch];
                let child_index = if child == NO_NODE {
                    if codebook.node_count >= codebook.nodes.len() {
                        return Err(Error::InvalidValue(
                            "Huffman decode tree exceeds byte alphabet bound",
                        ));
                    }
                    let created = codebook.node_count;
                    codebook.node_count += 1;
                    codebook.nodes[node_index].children[branch] = created as u16;
                    created
                } else {
                    usize::from(child)
                };
                node_index = child_index;
            }
            if codebook.nodes[node_index].symbol != NO_NODE
                || codebook.nodes[node_index]
                    .children
                    .iter()
                    .any(|&child| child != NO_NODE)
            {
                return Err(Error::InvalidValue("oversubscribed Huffman code lengths"));
            }
            codebook.nodes[node_index].symbol = symbol as u16;
        }
    }

    for node in &codebook.nodes[..codebook.node_count] {
        if node.symbol == NO_NODE {
            if node.children.contains(&NO_NODE) {
                return Err(Error::InvalidValue("incomplete Huffman code lengths"));
            }
        } else if node.children.iter().any(|&child| child != NO_NODE) {
            return Err(Error::InvalidValue("oversubscribed Huffman code lengths"));
        }
    }
    Ok(codebook)
}

pub(crate) struct PayloadWriter<'a> {
    output: &'a mut Vec<u8>,
    prepared: Prepared,
    codebook: Codebook,
    packed_start: usize,
    packed_end: usize,
    written_symbols: u64,
    bit_position: u64,
    seen: [bool; ALPHABET_SIZE],
}

impl PayloadWriter<'_> {
    pub(crate) fn write_byte(&mut self, byte: u8) -> Result<()> {
        let symbol = usize::from(byte);
        if !self.prepared.model.present[symbol] {
            return Err(Error::InvalidValue(
                "Huffman encoder observed an uncounted symbol",
            ));
        }
        self.seen[symbol] = true;
        self.written_symbols = checked_add(self.written_symbols, 1, "Huffman encoded symbols")?;
        if self.written_symbols > self.prepared.source_bytes {
            return Err(Error::LengthMismatch {
                context: "Huffman encoded symbols",
                expected: self.prepared.source_bytes,
                actual: self.written_symbols,
            });
        }

        let word = self.codebook.codewords[symbol];
        if usize::from(word.length) <= 64 {
            let length = u64::from(word.length);
            let final_bit_position =
                checked_add(self.bit_position, length, "Huffman encoded bits")?;
            if final_bit_position > self.prepared.model.bit_length {
                return Err(Error::InternalSizeMismatch {
                    counted: u64_usize(
                        self.prepared.model.bit_length,
                        "Huffman encoded bit count",
                    )?,
                    encoded: u64_usize(final_bit_position, "Huffman encoded bit count")?,
                });
            }

            let mut consumed = 0u64;
            let code = word.code.limbs[0];
            while consumed < length {
                let byte_index =
                    u64_usize(self.bit_position / 8, "Huffman packed bitstream position")?;
                let offset = (self.bit_position % 8) as u8;
                let available = u64::from(8 - offset);
                let take = available.min(length - consumed);
                let source_shift = length - consumed - take;
                let mask = (1u64 << take) - 1;
                let chunk = ((code >> source_shift) & mask) as u8;
                let destination_shift = available - take;
                let encoded = self.output.len();
                let destination = self.output.get_mut(self.packed_start + byte_index).ok_or(
                    Error::InternalSizeMismatch {
                        counted: self.packed_end,
                        encoded,
                    },
                )?;
                *destination |= chunk << destination_shift;
                self.bit_position = checked_add(self.bit_position, take, "Huffman encoded bits")?;
                consumed += take;
            }
            return Ok(());
        }
        for position in 0..usize::from(word.length) {
            if word.bit_from_most_significant(position)? {
                let byte_index =
                    u64_usize(self.bit_position / 8, "Huffman packed bitstream position")?;
                let shift = 7 - (self.bit_position % 8) as u8;
                let encoded = self.output.len();
                let destination = self.output.get_mut(self.packed_start + byte_index).ok_or(
                    Error::InternalSizeMismatch {
                        counted: self.packed_end,
                        encoded,
                    },
                )?;
                *destination |= 1u8 << shift;
            }
            self.bit_position = checked_add(self.bit_position, 1, "Huffman encoded bits")?;
        }
        Ok(())
    }

    pub(crate) fn finish(self) -> Result<()> {
        if self.written_symbols != self.prepared.source_bytes {
            return Err(Error::LengthMismatch {
                context: "Huffman encoded symbols",
                expected: self.prepared.source_bytes,
                actual: self.written_symbols,
            });
        }
        if self.bit_position != self.prepared.model.bit_length {
            return Err(Error::InternalSizeMismatch {
                counted: u64_usize(self.prepared.model.bit_length, "Huffman encoded bit count")?,
                encoded: u64_usize(self.bit_position, "Huffman encoded bit count")?,
            });
        }
        for (symbol, &present) in self.prepared.model.present.iter().enumerate() {
            if present && !self.seen[symbol] {
                return Err(Error::InvalidValue(
                    "Huffman encoder did not observe a counted symbol",
                ));
            }
        }
        Ok(())
    }
}

pub(crate) fn start_payload(prepared: Prepared, output: &mut Vec<u8>) -> Result<PayloadWriter<'_>> {
    let model = prepared.model;
    write_u64(output, u64::from(model.symbol_count));
    for symbol in 0..ALPHABET_SIZE {
        if model.present[symbol] {
            output.push(symbol as u8);
            output.push(model.lengths[symbol]);
        }
    }
    write_u64(output, model.bit_length);

    let packed_length = u64_usize(
        packed_bytes(model.bit_length)?,
        "Huffman packed bitstream length",
    )?;
    let packed_start = output.len();
    let packed_end = packed_start
        .checked_add(packed_length)
        .ok_or(Error::IntegerOverflow {
            context: "Huffman packed bitstream end",
        })?;
    output.resize(packed_end, 0);

    Ok(PayloadWriter {
        output,
        prepared,
        codebook: build_codebook(model)?,
        packed_start,
        packed_end,
        written_symbols: 0,
        bit_position: 0,
        seen: [false; ALPHABET_SIZE],
    })
}

pub(crate) fn write_payload(input: &[u8], prepared: Prepared, output: &mut Vec<u8>) -> Result<()> {
    let payload_start = output.len();
    {
        let mut writer = start_payload(prepared, output)?;
        for &byte in input {
            writer.write_byte(byte)?;
        }
        writer.finish()?;
    }
    let written = output.len() - payload_start;
    let counted = u64_usize(prepared.analysis.payload_bytes, "Huffman payload length")?;
    if written != counted {
        return Err(Error::InternalSizeMismatch {
            counted,
            encoded: written,
        });
    }
    Ok(())
}

fn parse_model(cursor: &mut Cursor<'_>, output_length: usize) -> Result<(Model, u64, usize)> {
    let symbol_count = cursor.read_usize("Huffman symbol count")?;
    if symbol_count > ALPHABET_SIZE {
        return Err(Error::InvalidValue(
            "Huffman symbol count exceeds byte alphabet",
        ));
    }

    let mut model = Model {
        lengths: [0; ALPHABET_SIZE],
        present: [false; ALPHABET_SIZE],
        symbol_count: symbol_count as u16,
        bit_length: 0,
    };
    let mut previous_symbol = None;
    for _ in 0..symbol_count {
        let symbol = cursor.read_u8("Huffman table symbol")?;
        if previous_symbol.is_some_and(|previous| symbol <= previous) {
            return Err(Error::NonCanonical(
                "Huffman table symbols are not strictly increasing",
            ));
        }
        let length = cursor.read_u8("Huffman code length")?;
        model.present[usize::from(symbol)] = true;
        model.lengths[usize::from(symbol)] = length;
        previous_symbol = Some(symbol);
    }
    model.bit_length = cursor.read_u64()?;

    if output_length == 0 {
        if symbol_count != 0 {
            return Err(Error::NonCanonical(
                "non-empty Huffman table for empty output",
            ));
        }
        if model.bit_length != 0 {
            return Err(Error::NonCanonical(
                "non-zero Huffman bit length for empty output",
            ));
        }
    } else {
        if symbol_count == 0 || symbol_count > output_length {
            return Err(Error::InvalidValue(
                "Huffman symbol count is inconsistent with output",
            ));
        }
        if symbol_count == 1 {
            let symbol = previous_symbol
                .ok_or(Error::InvalidValue("singleton Huffman symbol is missing"))?;
            if model.lengths[usize::from(symbol)] != 0 {
                return Err(Error::NonCanonical(
                    "singleton Huffman code length is not zero",
                ));
            }
            if model.bit_length != 0 {
                return Err(Error::NonCanonical(
                    "singleton Huffman stream has encoded bits",
                ));
            }
        } else if model.bit_length == 0 {
            return Err(Error::InvalidValue(
                "multi-symbol Huffman stream has no encoded bits",
            ));
        }
    }

    let packed_length = u64_usize(
        packed_bytes(model.bit_length)?,
        "Huffman packed bitstream length",
    )?;
    Ok((model, model.bit_length, packed_length))
}

fn packed_bit(packed: &[u8], position: u64) -> Result<usize> {
    let byte_index = u64_usize(position / 8, "Huffman packed bit position")?;
    let byte = packed.get(byte_index).copied().ok_or(Error::Truncated {
        context: "Huffman packed bitstream",
        position: byte_index,
    })?;
    let shift = 7 - (position % 8) as u8;
    Ok(usize::from((byte >> shift) & 1))
}

pub(crate) struct PayloadDecoder<'a> {
    model: Model,
    codebook: Codebook,
    packed: &'a [u8],
    bit_length: u64,
    bit_position: u64,
    output_length: usize,
    produced: usize,
    singleton: Option<u8>,
    seen: [bool; ALPHABET_SIZE],
}

impl PayloadDecoder<'_> {
    pub(crate) fn next_symbol(&mut self) -> Result<Option<u8>> {
        if self.produced == self.output_length {
            return Ok(None);
        }

        let symbol = if let Some(symbol) = self.singleton {
            symbol
        } else {
            let mut node_index = 0usize;
            loop {
                if self.bit_position >= self.bit_length {
                    return Err(Error::LengthMismatch {
                        context: "Huffman decoded symbols",
                        expected: usize_u64(self.output_length, "Huffman output length")?,
                        actual: usize_u64(self.produced, "Huffman decoded symbol count")?,
                    });
                }
                let branch = packed_bit(self.packed, self.bit_position)?;
                self.bit_position = checked_add(self.bit_position, 1, "Huffman decoded bits")?;
                let child = self.codebook.nodes[node_index].children[branch];
                if child == NO_NODE {
                    return Err(Error::InvalidValue(
                        "Huffman bitstream selects an absent prefix",
                    ));
                }
                node_index = usize::from(child);
                let symbol = self.codebook.nodes[node_index].symbol;
                if symbol != NO_NODE {
                    break symbol as u8;
                }
            }
        };

        self.produced += 1;
        self.seen[usize::from(symbol)] = true;
        Ok(Some(symbol))
    }

    pub(crate) fn finish(self) -> Result<()> {
        if self.produced != self.output_length {
            return Err(Error::LengthMismatch {
                context: "Huffman decoded symbols",
                expected: usize_u64(self.output_length, "Huffman output length")?,
                actual: usize_u64(self.produced, "Huffman decoded symbol count")?,
            });
        }
        if self.bit_position != self.bit_length {
            return Err(Error::LengthMismatch {
                context: "Huffman bitstream",
                expected: self.bit_length,
                actual: self.bit_position,
            });
        }
        for (symbol, &was_seen) in self.seen.iter().enumerate() {
            if self.model.present[symbol] && !was_seen {
                return Err(Error::NonCanonical(
                    "unused symbol in Huffman code-length table",
                ));
            }
        }
        Ok(())
    }
}

pub(crate) fn payload_decoder(payload: &[u8], output_length: usize) -> Result<PayloadDecoder<'_>> {
    let mut cursor = Cursor::new(payload);
    let (model, bit_length, packed_length) = parse_model(&mut cursor, output_length)?;
    let packed = cursor.read_exact(packed_length, "Huffman packed bitstream")?;
    cursor.finish("Huffman payload")?;

    let used_last_bits = (bit_length % 8) as u8;
    if used_last_bits != 0 {
        let last = packed.last().copied().ok_or(Error::Truncated {
            context: "Huffman packed bitstream",
            position: 0,
        })?;
        let unused_mask = (1u8 << (8 - used_last_bits)) - 1;
        if last & unused_mask != 0 {
            return Err(Error::NonCanonical("non-zero Huffman bit padding"));
        }
    }

    let singleton = if model.symbol_count == 1 {
        Some(
            model
                .present
                .iter()
                .position(|&present| present)
                .ok_or(Error::InvalidValue("singleton Huffman symbol is missing"))?
                as u8,
        )
    } else {
        None
    };
    Ok(PayloadDecoder {
        model,
        codebook: build_codebook(model)?,
        packed,
        bit_length,
        bit_position: 0,
        output_length,
        produced: 0,
        singleton,
        seen: [false; ALPHABET_SIZE],
    })
}

pub(crate) fn process(
    payload: &[u8],
    output_length: usize,
    mut output: Option<&mut [u8]>,
) -> Result<()> {
    let mut decoder = payload_decoder(payload, output_length)?;
    for output_index in 0..output_length {
        let symbol = decoder.next_symbol()?.ok_or(Error::LengthMismatch {
            context: "Huffman decoded symbols",
            expected: usize_u64(output_length, "Huffman output length")?,
            actual: usize_u64(output_index, "Huffman decoded symbol count")?,
        })?;
        if let Some(destination) = output.as_deref_mut() {
            destination[output_index] = symbol;
        }
    }
    decoder.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_payload_bit_by_bit(input: &[u8], prepared: Prepared, output: &mut Vec<u8>) {
        let payload_start = output.len();
        let model = prepared.model;
        write_u64(output, u64::from(model.symbol_count));
        for symbol in 0..ALPHABET_SIZE {
            if model.present[symbol] {
                output.push(symbol as u8);
                output.push(model.lengths[symbol]);
            }
        }
        write_u64(output, model.bit_length);

        let packed_length = usize::try_from(packed_bytes(model.bit_length).unwrap()).unwrap();
        let packed_start = output.len();
        output.resize(packed_start + packed_length, 0);

        let codebook = build_codebook(model).unwrap();
        let mut bit_position = 0u64;
        for &byte in input {
            let word = codebook.codewords[usize::from(byte)];
            for position in 0..usize::from(word.length) {
                if word.bit_from_most_significant(position).unwrap() {
                    let byte_index = usize::try_from(bit_position / 8).unwrap();
                    let shift = 7 - (bit_position % 8) as u8;
                    output[packed_start + byte_index] |= 1u8 << shift;
                }
                bit_position += 1;
            }
        }

        assert_eq!(bit_position, model.bit_length);
        assert_eq!(
            output.len() - payload_start,
            usize::try_from(prepared.analysis.payload_bytes).unwrap()
        );
    }

    #[test]
    fn chunked_payload_writer_matches_bit_by_bit_reference() {
        let frequencies = [144usize, 89, 55, 34, 21, 13, 8, 5, 3, 2, 1, 1];
        let mut input = Vec::new();
        for occurrence in 0..frequencies[0] {
            for (symbol, &frequency) in frequencies.iter().enumerate() {
                if occurrence < frequency {
                    input.push(symbol as u8);
                }
            }
        }

        let prepared = prepare(&input).unwrap();
        let codebook = build_codebook(prepared.model).unwrap();
        let maximum_length = codebook
            .codewords
            .iter()
            .map(|word| usize::from(word.length))
            .max()
            .unwrap();
        assert!(maximum_length > 8);
        assert!(maximum_length <= 64);

        let prefix = [0xde, 0xad, 0xbe, 0xef];
        let mut actual = prefix.to_vec();
        write_payload(&input, prepared, &mut actual).unwrap();
        let mut expected = prefix.to_vec();
        write_payload_bit_by_bit(&input, prepared, &mut expected);

        assert_eq!(actual, expected);
        let mut decoded = vec![0; input.len()];
        process(&actual[prefix.len()..], input.len(), Some(&mut decoded)).unwrap();
        assert_eq!(decoded, input);
    }

    #[test]
    fn complete_maximum_depth_byte_codebook_is_bounded() {
        let mut model = Model {
            lengths: [0; ALPHABET_SIZE],
            present: [true; ALPHABET_SIZE],
            symbol_count: ALPHABET_SIZE as u16,
            bit_length: 0,
        };
        for symbol in 0..(ALPHABET_SIZE - 2) {
            model.lengths[symbol] = (symbol + 1) as u8;
        }
        model.lengths[ALPHABET_SIZE - 2] = MAX_CODE_LENGTH as u8;
        model.lengths[ALPHABET_SIZE - 1] = MAX_CODE_LENGTH as u8;

        let codebook = build_codebook(model).unwrap();
        assert_eq!(codebook.node_count, MAX_TREE_NODES);
        assert_eq!(codebook.codewords[0].length, 1);
        assert_eq!(
            codebook.codewords[ALPHABET_SIZE - 1].length,
            MAX_CODE_LENGTH as u8
        );
    }

    #[test]
    fn maximum_depth_payload_fallback_matches_bit_by_bit_reference() {
        let mut model = Model {
            lengths: [0; ALPHABET_SIZE],
            present: [true; ALPHABET_SIZE],
            symbol_count: ALPHABET_SIZE as u16,
            bit_length: 0,
        };
        for symbol in 0..(ALPHABET_SIZE - 2) {
            model.lengths[symbol] = (symbol + 1) as u8;
        }
        model.lengths[ALPHABET_SIZE - 2] = MAX_CODE_LENGTH as u8;
        model.lengths[ALPHABET_SIZE - 1] = MAX_CODE_LENGTH as u8;
        model.bit_length = model.lengths.iter().map(|&length| u64::from(length)).sum();

        let prepared = Prepared {
            model,
            analysis: analyse_model(model).unwrap(),
            source_bytes: ALPHABET_SIZE as u64,
        };
        let input: Vec<u8> = (u8::MIN..=u8::MAX).collect();
        let codebook = build_codebook(model).unwrap();
        assert!(codebook.codewords.iter().any(|word| {
            usize::from(word.length) > 64 && word.code.limbs[1..].iter().any(|&limb| limb != 0)
        }));

        let mut actual = Vec::new();
        write_payload(&input, prepared, &mut actual).unwrap();
        let mut expected = Vec::new();
        write_payload_bit_by_bit(&input, prepared, &mut expected);

        assert_eq!(actual, expected);
        let mut decoded = vec![0; input.len()];
        process(&actual, input.len(), Some(&mut decoded)).unwrap();
        assert_eq!(decoded, input);
    }
}
