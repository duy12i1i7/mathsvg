"""Small, exact reference model used by the Phase 3 oracles.

This is deliberately not the production encoder.  It serialises a frozen,
finite subset of Procedural DSL v1 using the normative node framing:

    opcode:u8 | flags:u8 | payload_length:canonical-uLEB128 | payload

All oracle comparisons use the resulting complete node byte strings.  The
decoder below is independent enough to make every emitted candidate prove an
exact round trip.  Container bytes are omitted because they are identical for
all candidates in the same microblock oracle.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import math
from typing import Iterable, Iterator, Sequence


LITERAL = 0x00
CONST = 0x01
LINEAR = 0x03
PERIODIC = 0x07
RECURRENCE = 0x09
CONCAT = 0x21
SHARE = 0x25
REFERENCE = 0x26
STRIDE = 0x41
BYTE_PLANE = 0x42
BIT_PLANE = 0x43
ADD = 0x60
XOR = 0x63
EXCEPTIONS = 0x6E

MAX_U64 = (1 << 64) - 1


def encode_uleb(value: int) -> bytes:
    """Encode one canonical unsigned LEB128 value in the u64 domain."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("uLEB value must be an integer")
    if value < 0 or value > MAX_U64:
        raise ValueError("uLEB value outside u64")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode_uleb(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Decode a canonical u64 uLEB value and return (value, new_offset)."""
    start = offset
    value = 0
    shift = 0
    for index in range(10):
        if offset >= len(data):
            raise ValueError("truncated uLEB")
        byte = data[offset]
        offset += 1
        payload = byte & 0x7F
        if shift == 63 and payload > 1:
            raise ValueError("uLEB overflow")
        value |= payload << shift
        if not byte & 0x80:
            encoded = data[start:offset]
            if encoded != encode_uleb(value):
                raise ValueError("non-canonical uLEB")
            return value, offset
        shift += 7
    raise ValueError("uLEB too long")


def _node(opcode: int, payload: bytes, flags: int = 0) -> bytes:
    if not 0 <= opcode <= 0xBF:
        raise ValueError("invalid v1 opcode")
    if not 0 <= flags <= 0xFF:
        raise ValueError("invalid flags")
    return bytes((opcode, flags)) + encode_uleb(len(payload)) + payload


def literal_node(data: bytes) -> bytes:
    return _node(LITERAL, encode_uleb(len(data)) + data)


def const_node(length: int, value: int) -> bytes:
    return _node(CONST, encode_uleb(length) + bytes((value,)))


def linear_node(length: int, a: int, b: int) -> bytes:
    payload = b"".join(
        encode_uleb(value) for value in (length, 1, 256, a & 0xFF, b & 0xFF)
    )
    return _node(LINEAR, payload)


def periodic_node(pattern: bytes, repetitions: int, suffix: bytes) -> bytes:
    payload = (
        encode_uleb(len(pattern))
        + pattern
        + encode_uleb(repetitions)
        + encode_uleb(len(suffix))
        + suffix
    )
    return _node(PERIODIC, payload)


def recurrence_node(length: int, coefficients: Sequence[int], initial: bytes) -> bytes:
    if len(coefficients) != len(initial) or not coefficients:
        raise ValueError("recurrence order and initial state differ")
    payload = b"".join(encode_uleb(value) for value in (length, 1, 256))
    payload += encode_uleb(len(coefficients))
    payload += b"".join(encode_uleb(value & 0xFF) for value in coefficients)
    payload += encode_uleb(len(initial)) + initial
    return _node(RECURRENCE, payload)


def exceptions_node(base: bytes, positions: Sequence[int], values: bytes) -> bytes:
    if len(positions) != len(values):
        raise ValueError("exception position/value length differs")
    previous = 0
    deltas = bytearray()
    for index, position in enumerate(positions):
        if position < 0 or (index and position <= positions[index - 1]):
            raise ValueError("exception positions must be sorted and unique")
        delta = position if index == 0 else position - previous
        deltas += encode_uleb(delta)
        previous = position
    return _node(
        EXCEPTIONS,
        base + encode_uleb(len(positions)) + bytes(deltas) + values,
    )


def algebra_node(opcode: int, predictor: bytes, correction: bytes) -> bytes:
    if opcode not in (ADD, XOR):
        raise ValueError("unsupported algebra node")
    return _node(opcode, predictor + correction)


def concat_node(children: Sequence[bytes]) -> bytes:
    return _node(CONCAT, encode_uleb(len(children)) + b"".join(children))


def reference_node(definition_id: int) -> bytes:
    # v1 oracle calls carry an explicit empty parameter-delta vector.
    return _node(REFERENCE, encode_uleb(definition_id) + encode_uleb(0))


def share_node(definitions: Sequence[bytes], body: bytes) -> bytes:
    return _node(SHARE, encode_uleb(len(definitions)) + b"".join(definitions) + body)


def stride_node(original_length: int, stride: int, child: bytes) -> bytes:
    return _node(STRIDE, encode_uleb(original_length) + encode_uleb(stride) + child)


def byte_plane_node(
    original_length: int, width: int, little_endian: bool, child: bytes
) -> bytes:
    return _node(
        BYTE_PLANE,
        encode_uleb(original_length)
        + encode_uleb(width)
        + bytes((0 if little_endian else 1,))
        + child,
    )


def bit_plane_node(original_length: int, child: bytes) -> bytes:
    return _node(BIT_PLANE, encode_uleb(original_length) + child)


def _read_byte(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ValueError("truncated byte")
    return data[offset], offset + 1


def _parse_node(
    data: bytes, offset: int = 0, definitions: Sequence[bytes] = ()
) -> tuple[bytes, int]:
    if offset + 2 > len(data):
        raise ValueError("truncated node header")
    opcode = data[offset]
    flags = data[offset + 1]
    if opcode > 0xBF or flags != 0:
        raise ValueError("invalid opcode or unsupported flags")
    payload_length, payload_offset = decode_uleb(data, offset + 2)
    end = payload_offset + payload_length
    if end > len(data):
        raise ValueError("truncated node payload")
    payload = data[payload_offset:end]
    cursor = 0

    if opcode == LITERAL:
        length, cursor = decode_uleb(payload, cursor)
        result = payload[cursor : cursor + length]
        cursor += length
    elif opcode == CONST:
        length, cursor = decode_uleb(payload, cursor)
        value, cursor = _read_byte(payload, cursor)
        result = bytes((value,)) * length
    elif opcode == LINEAR:
        length, cursor = decode_uleb(payload, cursor)
        width, cursor = decode_uleb(payload, cursor)
        modulus, cursor = decode_uleb(payload, cursor)
        a, cursor = decode_uleb(payload, cursor)
        b, cursor = decode_uleb(payload, cursor)
        if width != 1 or modulus != 256:
            raise ValueError("oracle LINEAR type mismatch")
        result = bytes((a * i + b) % modulus for i in range(length))
    elif opcode == PERIODIC:
        pattern_length, cursor = decode_uleb(payload, cursor)
        pattern = payload[cursor : cursor + pattern_length]
        cursor += pattern_length
        repetitions, cursor = decode_uleb(payload, cursor)
        suffix_length, cursor = decode_uleb(payload, cursor)
        suffix = payload[cursor : cursor + suffix_length]
        cursor += suffix_length
        if not pattern and repetitions:
            raise ValueError("empty repeated pattern")
        result = pattern * repetitions + suffix
    elif opcode == RECURRENCE:
        length, cursor = decode_uleb(payload, cursor)
        width, cursor = decode_uleb(payload, cursor)
        modulus, cursor = decode_uleb(payload, cursor)
        order, cursor = decode_uleb(payload, cursor)
        coefficients = []
        for _ in range(order):
            coefficient, cursor = decode_uleb(payload, cursor)
            coefficients.append(coefficient)
        initial_count, cursor = decode_uleb(payload, cursor)
        initial = payload[cursor : cursor + initial_count]
        cursor += initial_count
        if width != 1 or modulus != 256 or order == 0 or initial_count != order:
            raise ValueError("oracle RECURRENCE type mismatch")
        values = list(initial[:length])
        while len(values) < length:
            value = 0
            for lag, coefficient in enumerate(coefficients, start=1):
                value += coefficient * values[-lag]
            values.append(value % modulus)
        result = bytes(values)
    elif opcode == CONCAT:
        count, cursor = decode_uleb(payload, cursor)
        parts = []
        for _ in range(count):
            part, cursor = _parse_node(payload, cursor, definitions)
            parts.append(part)
        result = b"".join(parts)
    elif opcode == SHARE:
        count, cursor = decode_uleb(payload, cursor)
        local_definitions: list[bytes] = []
        for _ in range(count):
            value, cursor = _parse_node(payload, cursor, local_definitions)
            local_definitions.append(value)
        result, cursor = _parse_node(payload, cursor, local_definitions)
    elif opcode == REFERENCE:
        definition_id, cursor = decode_uleb(payload, cursor)
        delta_count, cursor = decode_uleb(payload, cursor)
        if delta_count != 0 or definition_id >= len(definitions):
            raise ValueError("invalid oracle reference")
        result = definitions[definition_id]
    elif opcode in (STRIDE, BYTE_PLANE, BIT_PLANE):
        original_length, cursor = decode_uleb(payload, cursor)
        if opcode == STRIDE:
            stride, cursor = decode_uleb(payload, cursor)
            transformed, cursor = _parse_node(payload, cursor, definitions)
            result = inverse_stride(transformed, original_length, stride)
        elif opcode == BYTE_PLANE:
            width, cursor = decode_uleb(payload, cursor)
            endian, cursor = _read_byte(payload, cursor)
            transformed, cursor = _parse_node(payload, cursor, definitions)
            result = inverse_byte_plane(
                transformed, original_length, width, endian == 0
            )
        else:
            transformed, cursor = _parse_node(payload, cursor, definitions)
            result = inverse_bit_plane(transformed, original_length)
    elif opcode == EXCEPTIONS:
        base, cursor = _parse_node(payload, cursor, definitions)
        count, cursor = decode_uleb(payload, cursor)
        positions = []
        previous = 0
        for index in range(count):
            delta, cursor = decode_uleb(payload, cursor)
            position = delta if index == 0 else previous + delta
            if position >= len(base) or (positions and position <= positions[-1]):
                raise ValueError("invalid exception positions")
            positions.append(position)
            previous = position
        values = payload[cursor : cursor + count]
        cursor += count
        mutable = bytearray(base)
        for position, value in zip(positions, values):
            mutable[position] = value
        result = bytes(mutable)
    elif opcode in (ADD, XOR):
        predictor, cursor = _parse_node(payload, cursor, definitions)
        correction, cursor = _parse_node(payload, cursor, definitions)
        if len(predictor) != len(correction):
            raise ValueError("algebra operand length mismatch")
        if opcode == XOR:
            result = bytes(a ^ b for a, b in zip(predictor, correction))
        else:
            result = bytes((a + b) & 0xFF for a, b in zip(predictor, correction))
    else:
        raise ValueError(f"unsupported oracle opcode 0x{opcode:02x}")

    if cursor != len(payload):
        raise ValueError("trailing node payload")
    return result, end


def decode_exact(blob: bytes) -> bytes:
    result, offset = _parse_node(blob)
    if offset != len(blob):
        raise ValueError("trailing bytes after root node")
    return result


@dataclass(frozen=True)
class ResidualLayer:
    predictor: str
    domain: str
    residual: bytes


@dataclass(frozen=True)
class Candidate:
    name: str
    blob: bytes
    output: bytes
    work: int
    nodes: int
    residual_layers: tuple[ResidualLayer, ...] = ()

    @property
    def size(self) -> int:
        return len(self.blob)


def _candidate_key(candidate: Candidate) -> tuple[int, int, int, bytes]:
    return candidate.size, candidate.work, candidate.nodes, candidate.blob


def _best(candidates: Iterable[Candidate]) -> Candidate:
    materialized = list(candidates)
    if not materialized:
        raise ValueError("empty candidate set")
    winner = min(materialized, key=_candidate_key)
    if decode_exact(winner.blob) != winner.output:
        raise AssertionError(f"candidate {winner.name} failed exact decode")
    return winner


def _literal_candidate(data: bytes) -> Candidate:
    return Candidate("literal", literal_node(data), data, len(data), 1)


def _mode(data: bytes) -> int:
    # Counter insertion order would depend on the input.  Explicit value tie
    # breaking is stable and agrees with the DSL tie-break.
    counts = Counter(data)
    return min(counts, key=lambda value: (-counts[value], value))


def _predict_recurrence(
    length: int, coefficients: Sequence[int], initial: bytes
) -> bytes:
    values = list(initial[:length])
    while len(values) < length:
        values.append(
            sum(
                coefficient * values[-lag]
                for lag, coefficient in enumerate(coefficients, start=1)
            )
            & 0xFF
        )
    return bytes(values)


def _best_recurrence_predictor(data: bytes) -> tuple[bytes, bytes, str] | None:
    """Fit the frozen recurrence catalogue, screening on a bounded prefix.

    Evaluating all 281 recurrence specifications across every byte of every DP
    interval is unnecessary: the prefix score only ranks specifications.  The
    selected specification is then evaluated across the complete interval, and
    complete candidate bytes still decide the oracle.
    """
    if len(data) < 3:
        return None
    screen_length = min(len(data), 64)
    specs: list[tuple[tuple[int, ...], bytes, str]] = [
        ((coefficient,), data[:1], "recurrence-1")
        for coefficient in range(256)
    ]
    if len(data) >= 4:
        small = (0, 1, 255, 2, 254)
        specs.extend(
            ((c1, c2), data[:2], "recurrence-2")
            for c1 in small
            for c2 in small
        )
    scored = []
    for coefficients, initial, name in specs:
        prefix = _predict_recurrence(screen_length, coefficients, initial)
        mismatches = sum(
            actual != predicted
            for actual, predicted in zip(data[:screen_length], prefix)
        )
        blob = recurrence_node(len(data), coefficients, initial)
        scored.append((mismatches, len(blob), blob, coefficients, initial, name))
    _, _, blob, coefficients, initial, name = min(scored)
    predicted = _predict_recurrence(len(data), coefficients, initial)
    return predicted, blob, name


@lru_cache(maxsize=8192)
def predictor_candidates(data: bytes) -> tuple[Candidate, ...]:
    """Return one canonical best-fitting predictor per finite family."""
    if not data:
        return ()
    candidates: list[Candidate] = []

    value = _mode(data)
    predicted = bytes((value,)) * len(data)
    candidates.append(
        Candidate("const", const_node(len(data), value), predicted, len(data), 1)
    )

    if len(data) >= 2:
        differences = Counter((data[i] - data[i - 1]) & 0xFF for i in range(1, len(data)))
        a = min(differences, key=lambda value: (-differences[value], value))
        offsets = Counter((value - a * index) & 0xFF for index, value in enumerate(data))
        b = min(offsets, key=lambda value: (-offsets[value], value))
        predicted = bytes((a * index + b) & 0xFF for index in range(len(data)))
        candidates.append(
            Candidate(
                "linear",
                linear_node(len(data), a, b),
                predicted,
                len(data),
                1,
            )
        )

    if len(data) >= 2:
        periodic_fits: list[tuple[int, int, bytes, bytes]] = []
        for period in range(1, min(64, len(data) // 2) + 1):
            pattern = bytes(
                _mode(data[offset::period])
                for offset in range(period)
            )
            repetitions, suffix_length = divmod(len(data), period)
            suffix = pattern[:suffix_length]
            predicted = pattern * repetitions + suffix
            mismatches = sum(a != b for a, b in zip(data, predicted))
            periodic_fits.append((mismatches, period, pattern, predicted))
        if periodic_fits:
            _, period, pattern, predicted = min(periodic_fits)
            repetitions, suffix_length = divmod(len(data), period)
            candidates.append(
                Candidate(
                    f"periodic-{period}",
                    periodic_node(pattern, repetitions, pattern[:suffix_length]),
                    predicted,
                    len(data),
                    1,
                )
            )

    recurrence_fit = _best_recurrence_predictor(data)
    if recurrence_fit is not None:
        predicted, blob, name = recurrence_fit
        candidates.append(Candidate(name, blob, predicted, len(data) * 2, 1))

    unique: dict[tuple[str, bytes], Candidate] = {}
    for candidate in candidates:
        unique[(candidate.name.split("-", 1)[0], candidate.output)] = candidate
    return tuple(sorted(unique.values(), key=lambda c: (c.name, c.blob)))


def _exact_direct_candidates(data: bytes) -> Iterator[Candidate]:
    yield _literal_candidate(data)
    if not data:
        return
    if len(set(data)) == 1:
        blob = const_node(len(data), data[0])
        yield Candidate("const", blob, data, len(data), 1)
    if len(data) >= 2:
        a = (data[1] - data[0]) & 0xFF
        b = data[0]
        if all(value == ((a * index + b) & 0xFF) for index, value in enumerate(data)):
            yield Candidate("linear", linear_node(len(data), a, b), data, len(data), 1)
        for period in range(1, min(64, len(data) // 2) + 1):
            pattern = data[:period]
            repetitions, suffix_length = divmod(len(data), period)
            suffix = data[repetitions * period :]
            if data == pattern * repetitions + suffix:
                yield Candidate(
                    f"periodic-{period}",
                    periodic_node(pattern, repetitions, suffix),
                    data,
                    len(data),
                    1,
                )
                break
    recurrence_fit = _best_recurrence_predictor(data)
    if recurrence_fit is not None:
        predicted, blob, name = recurrence_fit
        if predicted == data:
            yield Candidate(name, blob, data, len(data) * 2, 1)


@lru_cache(maxsize=16384)
def choose_direct_representation(data: bytes) -> Candidate:
    """Best literal or exact generator, with no fitted corrections."""
    return _best(_exact_direct_candidates(data))


def exception_candidates(data: bytes) -> Iterator[Candidate]:
    for predictor in predictor_candidates(data):
        positions = [
            index
            for index, (actual, predicted) in enumerate(zip(data, predictor.output))
            if actual != predicted
        ]
        if not positions:
            continue
        values = bytes(data[position] for position in positions)
        blob = exceptions_node(predictor.blob, positions, values)
        yield Candidate(
            f"exceptions({predictor.name})",
            blob,
            data,
            predictor.work + len(positions),
            1 + predictor.nodes,
        )


@lru_cache(maxsize=16384)
def choose_representation(data: bytes, residual_depth: int = 0) -> Candidate:
    """Find the exact best candidate in the frozen finite reference catalogue."""
    if residual_depth < 0 or residual_depth > 2:
        raise ValueError("reference residual depth must be 0..2")
    candidates = list(_exact_direct_candidates(data))
    candidates.extend(exception_candidates(data))
    if residual_depth:
        for predictor in predictor_candidates(data):
            xor_residual = bytes(
                actual ^ predicted
                for actual, predicted in zip(data, predictor.output)
            )
            xor_child = choose_representation(xor_residual, residual_depth - 1)
            xor_blob = algebra_node(XOR, predictor.blob, xor_child.blob)
            candidates.append(
                Candidate(
                    f"xor({predictor.name},{xor_child.name})",
                    xor_blob,
                    data,
                    predictor.work + xor_child.work + len(data),
                    1 + predictor.nodes + xor_child.nodes,
                    (
                        ResidualLayer(predictor.name, "xor", xor_residual),
                    )
                    + xor_child.residual_layers,
                )
            )
            add_residual = bytes(
                (actual - predicted) & 0xFF
                for actual, predicted in zip(data, predictor.output)
            )
            add_child = choose_representation(add_residual, residual_depth - 1)
            add_blob = algebra_node(ADD, predictor.blob, add_child.blob)
            candidates.append(
                Candidate(
                    f"add({predictor.name},{add_child.name})",
                    add_blob,
                    data,
                    predictor.work + add_child.work + len(data),
                    1 + predictor.nodes + add_child.nodes,
                    (
                        ResidualLayer(predictor.name, "add_mod_256", add_residual),
                    )
                    + add_child.residual_layers,
                )
            )
    return _best(candidates)


def family_candidate(data: bytes, family: str) -> Candidate:
    """Best complete representation restricted to literal plus one family."""
    candidates = [_literal_candidate(data)]
    for candidate in _exact_direct_candidates(data):
        if candidate.name.split("-", 1)[0] == family:
            candidates.append(candidate)
    for predictor in predictor_candidates(data):
        if predictor.name.split("-", 1)[0] != family:
            continue
        positions = [
            index
            for index, (actual, predicted) in enumerate(zip(data, predictor.output))
            if actual != predicted
        ]
        if positions:
            values = bytes(data[position] for position in positions)
            blob = exceptions_node(predictor.blob, positions, values)
            candidates.append(
                Candidate(
                    f"exceptions({predictor.name})",
                    blob,
                    data,
                    predictor.work + len(positions),
                    1 + predictor.nodes,
                )
            )
        elif predictor.output == data:
            candidates.append(
                Candidate(
                    predictor.name,
                    predictor.blob,
                    data,
                    predictor.work,
                    predictor.nodes,
                )
            )
    return _best(candidates)


def transform_stride(data: bytes, stride: int) -> bytes:
    if stride < 2:
        raise ValueError("stride must be at least two")
    return b"".join(data[lane::stride] for lane in range(stride))


def inverse_stride(data: bytes, original_length: int, stride: int) -> bytes:
    if stride < 2 or len(data) != original_length:
        raise ValueError("invalid stride payload")
    lane_lengths = [
        (original_length - lane + stride - 1) // stride
        if lane < original_length
        else 0
        for lane in range(stride)
    ]
    lanes = []
    offset = 0
    for length in lane_lengths:
        lanes.append(data[offset : offset + length])
        offset += length
    result = bytearray(original_length)
    for lane, values in enumerate(lanes):
        result[lane::stride] = values
    return bytes(result)


def transform_byte_plane(data: bytes, width: int, little_endian: bool = True) -> bytes:
    if width not in (2, 3, 4, 6, 8) or len(data) % width:
        raise ValueError("invalid byte-plane width or input length")
    plane_order = range(width) if little_endian else range(width - 1, -1, -1)
    return b"".join(data[plane::width] for plane in plane_order)


def inverse_byte_plane(
    data: bytes, original_length: int, width: int, little_endian: bool = True
) -> bytes:
    if (
        width not in (2, 3, 4, 6, 8)
        or len(data) != original_length
        or original_length % width
    ):
        raise ValueError("invalid byte-plane payload")
    elements = original_length // width
    plane_order = list(range(width) if little_endian else range(width - 1, -1, -1))
    result = bytearray(original_length)
    offset = 0
    for plane in plane_order:
        result[plane::width] = data[offset : offset + elements]
        offset += elements
    return bytes(result)


def transform_bit_plane(data: bytes) -> bytes:
    plane_bytes = (len(data) + 7) // 8
    result = bytearray()
    for bit in range(8):
        packed = bytearray(plane_bytes)
        for index, value in enumerate(data):
            packed[index // 8] |= ((value >> bit) & 1) << (index % 8)
        result += packed
    return bytes(result)


def inverse_bit_plane(data: bytes, original_length: int) -> bytes:
    plane_bytes = (original_length + 7) // 8
    if len(data) != plane_bytes * 8:
        raise ValueError("invalid bit-plane payload length")
    if original_length % 8:
        valid_mask = (1 << (original_length % 8)) - 1
        for bit in range(8):
            if data[bit * plane_bytes + plane_bytes - 1] & ~valid_mask:
                raise ValueError("non-zero bit-plane padding")
    result = bytearray(original_length)
    for bit in range(8):
        plane = data[bit * plane_bytes : (bit + 1) * plane_bytes]
        for index in range(original_length):
            result[index] |= ((plane[index // 8] >> (index % 8)) & 1) << bit
    return bytes(result)


@lru_cache(maxsize=8192)
def coordinate_candidates(data: bytes, residual_depth: int = 1) -> tuple[Candidate, ...]:
    identity = choose_representation(data, residual_depth)
    candidates = [
        Candidate(
            f"identity:{identity.name}",
            identity.blob,
            data,
            identity.work,
            identity.nodes,
            identity.residual_layers,
        )
    ]
    for stride in (2, 3, 4, 8, 16):
        transformed = transform_stride(data, stride)
        whole_child = choose_representation(transformed, residual_depth)
        lane_children = [
            choose_representation(data[lane::stride], residual_depth)
            for lane in range(stride)
            if lane < len(data)
        ]
        independent_blob = (
            lane_children[0].blob
            if len(lane_children) == 1
            else concat_node([child.blob for child in lane_children])
        )
        independent_child = Candidate(
            "independent-lanes",
            independent_blob,
            transformed,
            sum(child.work for child in lane_children),
            1 + sum(child.nodes for child in lane_children),
            tuple(
                layer
                for child in lane_children
                for layer in child.residual_layers
            ),
        )
        for layout, child in (("whole", whole_child), ("lanes", independent_child)):
            blob = stride_node(len(data), stride, child.blob)
            candidates.append(
                Candidate(
                    f"stride-{stride}-{layout}:{child.name}",
                    blob,
                    data,
                    child.work + len(data),
                    child.nodes + 1,
                    child.residual_layers,
                )
            )
    for width in (2, 3, 4, 6, 8):
        if len(data) % width:
            continue
        for little_endian in (True, False):
            transformed = transform_byte_plane(data, width, little_endian)
            endian = "le" if little_endian else "be"
            whole_child = choose_representation(transformed, residual_depth)
            plane_length = len(data) // width
            plane_children = [
                choose_representation(
                    transformed[index * plane_length : (index + 1) * plane_length],
                    residual_depth,
                )
                for index in range(width)
            ]
            independent_blob = concat_node([child.blob for child in plane_children])
            independent_child = Candidate(
                "independent-planes",
                independent_blob,
                transformed,
                sum(child.work for child in plane_children),
                1 + sum(child.nodes for child in plane_children),
                tuple(
                    layer
                    for child in plane_children
                    for layer in child.residual_layers
                ),
            )
            for layout, child in (
                ("whole", whole_child),
                ("planes", independent_child),
            ):
                blob = byte_plane_node(len(data), width, little_endian, child.blob)
                candidates.append(
                    Candidate(
                        f"byte-plane-{width}-{endian}-{layout}:{child.name}",
                        blob,
                        data,
                        child.work + len(data),
                        child.nodes + 1,
                        child.residual_layers,
                    )
                )
    transformed = transform_bit_plane(data)
    whole_child = choose_representation(transformed, residual_depth)
    plane_length = (len(data) + 7) // 8
    plane_children = [
        choose_representation(
            transformed[index * plane_length : (index + 1) * plane_length],
            residual_depth,
        )
        for index in range(8)
    ]
    independent_blob = concat_node([child.blob for child in plane_children])
    independent_child = Candidate(
        "independent-planes",
        independent_blob,
        transformed,
        sum(child.work for child in plane_children),
        1 + sum(child.nodes for child in plane_children),
        tuple(
            layer
            for child in plane_children
            for layer in child.residual_layers
        ),
    )
    for layout, child in (("whole", whole_child), ("planes", independent_child)):
        blob = bit_plane_node(len(data), child.blob)
        candidates.append(
            Candidate(
                f"bit-plane-{layout}:{child.name}",
                blob,
                data,
                child.work + len(data) * 8,
                child.nodes + 1,
                child.residual_layers,
            )
        )
    # A transform can produce an identical blob/output through endian symmetry.
    unique = {candidate.blob: candidate for candidate in candidates}
    return tuple(sorted(unique.values(), key=lambda candidate: candidate.name))


def best_coordinate(data: bytes, residual_depth: int = 1) -> Candidate:
    return _best(coordinate_candidates(data, residual_depth))


@dataclass(frozen=True)
class SegmentationResult:
    blob: bytes
    boundaries: tuple[int, ...]
    leaf_names: tuple[str, ...]
    output: bytes

    @property
    def size(self) -> int:
        return len(self.blob)


def segmentation_oracle(
    data: bytes, quantum: int = 256, residual_depth: int = 0
) -> SegmentationResult:
    """Exact DP over every interval on the frozen boundary grid.

    The state includes the number of leaves.  Therefore the one-time CONCAT
    count and TLV length costs are evaluated on the complete root candidate,
    not approximated as a per-split penalty.
    """
    if quantum <= 0:
        raise ValueError("quantum must be positive")
    points = list(range(0, len(data), quantum))
    if not points or points[-1] != len(data):
        points.append(len(data))
    if points[0] != 0:
        points.insert(0, 0)
    last = len(points) - 1
    leaves: dict[tuple[int, int], Candidate] = {}
    for start in range(last):
        for end in range(start + 1, last + 1):
            interval = data[points[start] : points[end]]
            leaves[start, end] = (
                choose_direct_representation(interval)
                if residual_depth == 0
                else choose_representation(interval, residual_depth)
            )

    # dp[end][leaf_count] = (sum child bytes, tuple child candidates, boundaries)
    dp: list[dict[int, tuple[int, tuple[Candidate, ...], tuple[int, ...]]]] = [
        {} for _ in points
    ]
    dp[0][0] = (0, (), (0,))
    for end in range(1, last + 1):
        for start in range(end):
            leaf = leaves[start, end]
            for old_count, (old_size, old_children, old_bounds) in dp[start].items():
                count = old_count + 1
                proposal = (
                    old_size + leaf.size,
                    old_children + (leaf,),
                    old_bounds + (points[end],),
                )
                current = dp[end].get(count)
                proposal_key = (
                    proposal[0],
                    tuple(child.blob for child in proposal[1]),
                    proposal[2],
                )
                if current is None:
                    dp[end][count] = proposal
                else:
                    current_key = (
                        current[0],
                        tuple(child.blob for child in current[1]),
                        current[2],
                    )
                    if proposal_key < current_key:
                        dp[end][count] = proposal

    complete: list[SegmentationResult] = []
    for count, (_, children, boundaries) in dp[last].items():
        if count == 1:
            blob = children[0].blob
        else:
            blob = concat_node([child.blob for child in children])
        result = SegmentationResult(
            blob,
            boundaries,
            tuple(child.name for child in children),
            data,
        )
        if decode_exact(blob) != data:
            raise AssertionError("segmentation oracle produced a non-exact root")
        complete.append(result)
    return min(
        complete,
        key=lambda result: (
            result.size,
            len(result.boundaries),
            result.blob,
            result.boundaries,
        ),
    )


@dataclass(frozen=True)
class DagSharingResult:
    inline_blob: bytes
    oracle_blob: bytes
    active_definitions: int
    output: bytes

    @property
    def inline_size(self) -> int:
        return len(self.inline_blob)

    @property
    def oracle_size(self) -> int:
        return len(self.oracle_blob)


def dag_sharing_oracle(blocks: Sequence[bytes]) -> DagSharingResult:
    """Enumerate every activation subset of exact duplicate block nodes."""
    if not blocks:
        raise ValueError("DAG oracle requires at least one block")
    leaf_candidates = [choose_representation(block, 0) for block in blocks]
    inline_blob = (
        leaf_candidates[0].blob
        if len(leaf_candidates) == 1
        else concat_node([candidate.blob for candidate in leaf_candidates])
    )
    counts = Counter(candidate.blob for candidate in leaf_candidates)
    reusable = sorted(blob for blob, count in counts.items() if count >= 2)
    if len(reusable) > 20:
        raise ValueError("reference DAG oracle activation set is too large")
    candidates: list[tuple[bytes, int]] = [(inline_blob, 0)]
    for mask in range(1, 1 << len(reusable)):
        definitions = [
            blob for index, blob in enumerate(reusable) if mask & (1 << index)
        ]
        definition_ids = {blob: index for index, blob in enumerate(definitions)}
        calls = [
            reference_node(definition_ids[candidate.blob])
            if candidate.blob in definition_ids
            else candidate.blob
            for candidate in leaf_candidates
        ]
        body = calls[0] if len(calls) == 1 else concat_node(calls)
        candidates.append((share_node(definitions, body), len(definitions)))
    oracle_blob, active = min(candidates, key=lambda item: (len(item[0]), item[1], item[0]))
    output = b"".join(blocks)
    if decode_exact(inline_blob) != output or decode_exact(oracle_blob) != output:
        raise AssertionError("DAG oracle produced a non-exact root")
    return DagSharingResult(inline_blob, oracle_blob, active, output)


def entropy_lower_bound(data: bytes) -> tuple[float, int]:
    """Return zero-order empirical entropy bits and its rounded byte bound."""
    if not data:
        return 0.0, 0
    length = len(data)
    bits = 0.0
    for count in Counter(data).values():
        probability = count / length
        bits -= count * math.log2(probability)
    return bits, math.ceil(bits / 8.0)
