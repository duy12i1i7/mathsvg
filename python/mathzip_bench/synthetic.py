"""Deterministic mathematical and incompressible synthetic corpora."""

from __future__ import annotations

import base64
import bz2
import csv
import gzip
import hashlib
import io
import json
import lzma
import shutil
import struct
import subprocess
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .common import atomic_write_bytes, atomic_write_json, atomic_write_text, sha256_file

DEFAULT_SIZES = (0, 1, 31, 256, 4096, 65536)
DEFAULT_NOISE = (0.0, 0.001, 0.01, 0.05, 0.10, 0.25)
SYNTHETIC_GENERATOR_VERSION = 2
GENERATION_MARKER = ".generation-in-progress.json"
DEFAULT_FAMILIES = (
    "constant_00",
    "constant_ff",
    "linear",
    "polynomial_d2",
    "polynomial_d3",
    "polynomial_d4",
    "periodic",
    "multi_periodic",
    "recurrence",
    "lfsr8",
    "lfsr16",
    "piecewise_mixed",
    "random",
    "encrypted",
    "already_compressed",
)


class HashStream:
    """Portable SHA-256 counter stream.

    This provides deterministic pseudo-random bytes derived from a recorded
    seed without relying on the implementation details of ``random.Random``.
    It is intended for corpus generation, not for generating secret keys.
    """

    def __init__(self, seed: int, label: str) -> None:
        seed_bytes = (int(seed) & ((1 << 128) - 1)).to_bytes(
            16, "little", signed=False
        )
        self._key = hashlib.sha256(seed_bytes + label.encode("utf-8")).digest()
        self._counter = 0
        self._buffer = bytearray()

    def read(self, length: int) -> bytes:
        while len(self._buffer) < length:
            counter = self._counter.to_bytes(16, "little")
            self._buffer.extend(hashlib.sha256(self._key + counter).digest())
            self._counter += 1
        result = bytes(self._buffer[:length])
        del self._buffer[:length]
        return result

    def randbelow(self, upper: int) -> int:
        if upper <= 0:
            raise ValueError("upper bound must be positive")
        limit = (1 << 64) - ((1 << 64) % upper)
        while True:
            value = int.from_bytes(self.read(8), "little")
            if value < limit:
                return value % upper


def _rotate_left_32(value: int, count: int) -> int:
    value &= 0xFFFFFFFF
    return ((value << count) | (value >> (32 - count))) & 0xFFFFFFFF


def _chacha20_quarter_round(
    state: list[int], a: int, b: int, c: int, d: int
) -> None:
    """Apply the RFC 8439 quarter round in place."""

    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotate_left_32(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotate_left_32(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotate_left_32(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotate_left_32(state[b] ^ state[c], 7)


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    """Return one IETF ChaCha20 block as specified by RFC 8439 section 2.3."""

    if len(key) != 32:
        raise ValueError("ChaCha20 key must contain exactly 32 bytes")
    if len(nonce) != 12:
        raise ValueError("IETF ChaCha20 nonce must contain exactly 12 bytes")
    if not 0 <= counter <= 0xFFFFFFFF:
        raise ValueError("ChaCha20 counter must fit in an unsigned 32-bit integer")

    initial = list(struct.unpack("<4I", b"expand 32-byte k"))
    initial.extend(struct.unpack("<8I", key))
    initial.append(counter)
    initial.extend(struct.unpack("<3I", nonce))
    working = initial.copy()

    for _ in range(10):
        _chacha20_quarter_round(working, 0, 4, 8, 12)
        _chacha20_quarter_round(working, 1, 5, 9, 13)
        _chacha20_quarter_round(working, 2, 6, 10, 14)
        _chacha20_quarter_round(working, 3, 7, 11, 15)
        _chacha20_quarter_round(working, 0, 5, 10, 15)
        _chacha20_quarter_round(working, 1, 6, 11, 12)
        _chacha20_quarter_round(working, 2, 7, 8, 13)
        _chacha20_quarter_round(working, 3, 4, 9, 14)

    return struct.pack(
        "<16I",
        *((working[index] + initial[index]) & 0xFFFFFFFF for index in range(16)),
    )


def chacha20_xor(
    plaintext_or_ciphertext: bytes,
    key: bytes,
    nonce: bytes,
    initial_counter: int = 1,
) -> bytes:
    """Encrypt or decrypt bytes with RFC 8439 ChaCha20.

    This dependency-free implementation exists solely to make the generated
    benchmark corpus portable. It is verified against the RFC test vectors,
    but it is not a general-purpose cryptography API.
    """

    if len(key) != 32:
        raise ValueError("ChaCha20 key must contain exactly 32 bytes")
    if len(nonce) != 12:
        raise ValueError("IETF ChaCha20 nonce must contain exactly 12 bytes")
    if not 0 <= initial_counter <= 0xFFFFFFFF:
        raise ValueError("ChaCha20 counter must fit in an unsigned 32-bit integer")
    block_count = (len(plaintext_or_ciphertext) + 63) // 64
    if block_count and initial_counter + block_count - 1 > 0xFFFFFFFF:
        raise ValueError("ChaCha20 counter would overflow")

    output = bytearray(len(plaintext_or_ciphertext))
    for block_index, offset in enumerate(range(0, len(output), 64)):
        key_stream = _chacha20_block(key, initial_counter + block_index, nonce)
        chunk = plaintext_or_ciphertext[offset : offset + 64]
        output[offset : offset + len(chunk)] = bytes(
            value ^ key_stream[index] for index, value in enumerate(chunk)
        )
    return bytes(output)


_TELEMETRY_RECORD = struct.Struct("<4sIQHiH8s")


def _structured_plaintext(size: int) -> bytes:
    """Build exact-length, strongly structured records before encryption."""

    output = bytearray()
    record_index = 0
    while len(output) < size:
        reading = (
            (record_index * record_index * 17 + record_index * 29) % 200_001
        ) - 100_000
        output.extend(
            _TELEMETRY_RECORD.pack(
                b"MZTL",
                record_index & 0xFFFFFFFF,
                1_700_000_000_000_000 + record_index * 1_000,
                record_index % 32,
                reading,
                (record_index // 16) & 0xFFFF,
                bytes(8),
            )
        )
        record_index += 1
    return bytes(output[:size])


def _encrypted_key_nonce(size: int, seed: int) -> tuple[bytes, bytes]:
    key = HashStream(seed, "encrypted:chacha20-rfc8439:key").read(32)
    nonce = HashStream(
        seed, f"encrypted:chacha20-rfc8439:nonce:size={size}"
    ).read(12)
    return key, nonce


def _encrypted(size: int, seed: int) -> bytes:
    key, nonce = _encrypted_key_nonce(size, seed)
    return chacha20_xor(_structured_plaintext(size), key, nonce, initial_counter=1)


def _constant(size: int, value: int) -> bytes:
    return bytes([value]) * size


def _linear(size: int, a: int = 17, b: int = 29) -> bytes:
    return bytes((a * index + b) & 0xFF for index in range(size))


def _polynomial(size: int, degree: int) -> bytes:
    coefficients = (7, 11, 5, 3, 13)
    output = bytearray(size)
    for index in range(size):
        value = 0
        power = 1
        for coefficient in coefficients[: degree + 1]:
            value = (value + coefficient * power) & 0xFF
            power = (power * index) & 0xFF
        output[index] = value
    return bytes(output)


def _periodic(size: int) -> bytes:
    pattern = bytes((1, 2, 3, 4, 9, 16, 25))
    return (pattern * ((size + len(pattern) - 1) // len(pattern)))[:size]


def _multi_periodic(size: int) -> bytes:
    first = (3, 17, 89, 201, 7)
    second = (11, 29, 47, 61, 83, 101, 127, 149, 173, 197, 223)
    return bytes(
        (first[index % len(first)] + second[index % len(second)]) & 0xFF
        for index in range(size)
    )


def _recurrence(size: int) -> bytes:
    if size == 0:
        return b""
    output = bytearray((1, 1)[:size])
    while len(output) < size:
        output.append((output[-1] + output[-2]) & 0xFF)
    return bytes(output)


def _lfsr(size: int, width: int, tap_positions: Sequence[int], seed: int) -> bytes:
    state = seed & ((1 << width) - 1)
    if state == 0:
        state = 1
    output = bytearray()
    current_byte = 0
    current_bits = 0
    for _ in range(size * 8):
        bit = state & 1
        current_byte |= bit << current_bits
        current_bits += 1
        feedback = 0
        for position in tap_positions:
            feedback ^= (state >> position) & 1
        state = (state >> 1) | (feedback << (width - 1))
        if current_bits == 8:
            output.append(current_byte)
            current_byte = 0
            current_bits = 0
    return bytes(output)


def _piecewise(size: int, seed: int) -> bytes:
    if size == 0:
        return b""
    boundaries = [round(index * size / 6) for index in range(7)]
    parts = [
        _constant(boundaries[1] - boundaries[0], 0x33),
        _linear(boundaries[2] - boundaries[1]),
        _periodic(boundaries[3] - boundaries[2]),
        _recurrence(boundaries[4] - boundaries[3]),
        HashStream(seed, f"piecewise-random-{size}").read(
            boundaries[5] - boundaries[4]
        ),
        _polynomial(boundaries[6] - boundaries[5], 3),
    ]
    last = bytearray(parts[-1])
    exception_stream = HashStream(seed, f"piecewise-exceptions-{size}")
    exception_count = round(len(last) * 0.01)
    for position in _sample_positions(len(last), exception_count, exception_stream):
        last[position] ^= 0xA5
    parts[-1] = bytes(last)
    return b"".join(parts)


def _sample_positions(size: int, count: int, stream: HashStream) -> set[int]:
    """Floyd sampling: exact-size sample without constructing ``range(size)``."""

    if count < 0 or count > size:
        raise ValueError("invalid sample size")
    chosen: set[int] = set()
    for candidate in range(size - count, size):
        selected = stream.randbelow(candidate + 1)
        chosen.add(candidate if selected in chosen else selected)
    return chosen


def add_noise(data: bytes, density: float, seed: int, label: str) -> bytes:
    if not 0.0 <= density <= 1.0:
        raise ValueError("noise density must be in [0, 1]")
    count = round(len(data) * density)
    if not count:
        return data
    stream = HashStream(seed, f"noise:{label}:{density:.12g}")
    output = bytearray(data)
    for position in _sample_positions(len(output), count, stream):
        # Modular addition by 1..255 guarantees a true exception.
        output[position] = (output[position] + 1 + stream.randbelow(255)) & 0xFF
    return bytes(output)


def family_bytes(family: str, size: int, seed: int) -> bytes:
    factories: dict[str, Callable[[], bytes]] = {
        "constant_00": lambda: _constant(size, 0x00),
        "constant_ff": lambda: _constant(size, 0xFF),
        "linear": lambda: _linear(size),
        "polynomial_d2": lambda: _polynomial(size, 2),
        "polynomial_d3": lambda: _polynomial(size, 3),
        "polynomial_d4": lambda: _polynomial(size, 4),
        "periodic": lambda: _periodic(size),
        "multi_periodic": lambda: _multi_periodic(size),
        "recurrence": lambda: _recurrence(size),
        # x^8 + x^6 + x^5 + x^4 + 1 and
        # x^16 + x^14 + x^13 + x^11 + 1 representations.
        "lfsr8": lambda: _lfsr(size, 8, (0, 2, 3, 4), 0xA7),
        "lfsr16": lambda: _lfsr(size, 16, (0, 2, 3, 5), 0xACE1),
        "piecewise_mixed": lambda: _piecewise(size, seed),
        "random": lambda: HashStream(seed, f"random:{size}").read(size),
        "encrypted": lambda: _encrypted(size, seed),
    }
    try:
        return factories[family]()
    except KeyError as exc:
        raise ValueError(f"unknown synthetic family: {family}") from exc


def _noise_slug(density: float) -> str:
    return f"{density * 100:07.3f}".replace(".", "p")


def _minimal_png(size_hint: int, seed: int) -> bytes:
    width = 64
    height = max(1, size_hint // width)
    pixels = HashStream(seed, f"png:{size_hint}").read(width * height)
    scanlines = b"".join(
        b"\x00" + pixels[row * width : (row + 1) * width] for row in range(height)
    )

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines, 9))
        + chunk(b"IEND", b"")
    )


# Public-domain-style generated 1x1 JPEG fixture. It is embedded so Pillow is
# not a dependency of the corpus generator.
_ONE_PIXEL_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////"
    "////////////////////////////////////////////////////////////2wBDAf"
    "//////////////////////////////////////////////////////////////"
    "////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAA"
    "AAAAAAAAAAAAAAAABf/EABQBAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhADEAAA"
    "Af/EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAQUCp//EABQRAQAAAAAAAAAA"
    "AAAAAAAAABD/2gAIAQMBAT8Bp//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIB"
    "AT8Bp//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEABj8Cp//EABQQAQAAAAAA"
    "AAAAAAAAAAAAABD/2gAIAQEAAT8hp//aAAwDAQACAAMAAAAQ/8QAFBEBAAAAAAAAA"
    "AAAAAAAAAAQ/9oACAEDAQE/EB//xAAUEQEAAAAAAAAAAAAAAAAAAAAQ/9oACAECAQ"
    "E/EB//xAAUEAEAAAAAAAAAAAAAAAAAAAAQ/9oACAEBAAE/EB//2Q=="
)


def _minimal_mp4(size_hint: int, seed: int) -> bytes:
    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind + payload

    ftyp = box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")
    # A deterministic synthetic ISO-BMFF sample. It is deliberately tiny and
    # not claimed to contain a decodable video track.
    media = HashStream(seed, f"mp4:{size_hint}").read(max(16, size_hint))
    return ftyp + box(b"free", b"MathZip synthetic ISO-BMFF sample") + box(b"mdat", media)


def _zip_bytes(source: bytes, name: str) -> bytes:
    buffer = io.BytesIO()
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(info, source)
    return buffer.getvalue()


def _zstd_bytes(source: bytes) -> bytes | None:
    executable = shutil.which("zstd")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "-q", "-19", "-c"],
            input=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError("zstd corpus sample generation timed out") from exc
    except OSError as exc:
        raise OSError(f"zstd corpus sample generation could not start: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise OSError(
            "zstd corpus sample generation failed"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout


def _optional_capabilities(*, include_zstd: bool = True) -> dict[str, object]:
    if not include_zstd:
        return {"zstd": {"used": False}}
    executable = shutil.which("zstd")
    if not executable:
        return {"zstd": {"used": True, "available": False}}
    executable_path = Path(executable)
    try:
        executable_sha256 = sha256_file(executable_path)
    except OSError:
        executable_sha256 = None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
            text=True,
        )
        version = (
            completed.stdout.strip().splitlines()[0]
            if completed.returncode == 0 and completed.stdout.strip()
            else None
        )
    except (OSError, subprocess.TimeoutExpired):
        version = None
    return {
        "zstd": {
            "used": True,
            "available": True,
            "executable_sha256": executable_sha256,
            "version": version,
        }
    }


def already_compressed_samples(size: int, seed: int) -> tuple[dict[str, bytes], list[str]]:
    source = (
        _periodic(size // 2)
        + _linear(size - size // 2)
        + HashStream(seed, f"compressed-source:{size}").read(max(0, size // 16))
    )
    samples = {
        "gzip.gz": gzip.compress(source, compresslevel=9, mtime=0),
        "bzip2.bz2": bz2.compress(source, compresslevel=9),
        "xz.xz": lzma.compress(source, format=lzma.FORMAT_XZ, preset=9),
        "zip.zip": _zip_bytes(source, "payload.bin"),
        "image.png": _minimal_png(size, seed),
        "image.jpg": _ONE_PIXEL_JPEG,
        "sample.mp4": _minimal_mp4(min(size, 64 * 1024), seed),
    }
    skipped: list[str] = []
    zstd = _zstd_bytes(source)
    if zstd is None:
        skipped.append("zstd.zst (zstd executable unavailable)")
    else:
        samples["zstd.zst"] = zstd
    return samples, skipped


@dataclass(frozen=True)
class GeneratedEntry:
    path: str
    family: str
    size_requested: int
    size_bytes: int
    noise_density: float | None
    seed: int
    sha256: str
    parameters: dict[str, object]


def corpus_manifest_is_current(
    output: Path,
    seed: int,
    sizes: Iterable[int] = DEFAULT_SIZES,
    noise_densities: Iterable[float] = DEFAULT_NOISE,
    families: Iterable[str] = DEFAULT_FAMILIES,
) -> bool:
    """Return whether a manifest was made by this generator/configuration.

    File existence and checksums remain the responsibility of the corpus
    verifier. This check only provides deterministic cache invalidation before
    benchmark scripts invoke that verifier.
    """

    if (output / GENERATION_MARKER).exists():
        return False
    try:
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict):
        return False
    expected_sizes = sorted(set(int(size) for size in sizes))
    expected_densities = sorted(set(float(value) for value in noise_densities))
    expected_families = list(dict.fromkeys(str(family) for family in families))
    expected_capabilities = _optional_capabilities(
        include_zstd="already_compressed" in expected_families
    )
    return (
        manifest.get("schema_version") == "mathzip-synthetic-corpus-v1"
        and manifest.get("generator_version") == SYNTHETIC_GENERATOR_VERSION
        and manifest.get("seed") == int(seed)
        and manifest.get("sizes") == expected_sizes
        and manifest.get("noise_densities") == expected_densities
        and manifest.get("families") == expected_families
        and manifest.get("optional_capabilities") == expected_capabilities
    )


def generate_corpus(
    output: Path,
    seed: int,
    sizes: Iterable[int] = DEFAULT_SIZES,
    noise_densities: Iterable[float] = DEFAULT_NOISE,
    families: Iterable[str] = DEFAULT_FAMILIES,
    force: bool = False,
) -> dict[str, object]:
    sizes = tuple(sorted(set(int(size) for size in sizes)))
    densities = tuple(sorted(set(float(value) for value in noise_densities)))
    families = tuple(dict.fromkeys(str(family) for family in families))
    if any(size < 0 for size in sizes):
        raise ValueError("synthetic sizes cannot be negative")
    if any(not 0.0 <= value <= 1.0 for value in densities):
        raise ValueError("noise densities must be in [0, 1]")
    unknown = set(families) - set(DEFAULT_FAMILIES)
    if unknown:
        raise ValueError(f"unknown families: {', '.join(sorted(unknown))}")
    manifest_path = output / "manifest.json"
    if manifest_path.exists() and not force:
        raise FileExistsError(
            f"{manifest_path} already exists; pass --force to regenerate files"
        )
    output.mkdir(parents=True, exist_ok=True)
    marker_path = output / GENERATION_MARKER
    atomic_write_json(
        marker_path,
        {
            "schema_version": "mathzip-synthetic-generation-marker-v1",
            "generator_version": SYNTHETIC_GENERATOR_VERSION,
            "seed": seed,
        },
    )
    if force:
        # A force regeneration is also the recovery path for a corrupt
        # manifest, orphan left by a crash, or a changed optional toolchain.
        # Clear generated leaves without following directory symlinks; retain
        # only the in-progress marker so verifiers fail closed until publish.
        for candidate in sorted(
            output.rglob("*"),
            key=lambda path: len(path.relative_to(output).parts),
            reverse=True,
        ):
            if candidate == marker_path:
                continue
            if candidate.is_symlink() or candidate.is_file():
                candidate.unlink()

    entries: list[GeneratedEntry] = []
    skipped: list[str] = []
    for family in families:
        family_root = output / family
        family_root.mkdir(parents=True, exist_ok=True)
        for size in sizes:
            if family == "already_compressed":
                samples, sample_skips = already_compressed_samples(size, seed)
                skipped.extend(f"size={size}: {reason}" for reason in sample_skips)
                for sample_name, payload in sorted(samples.items()):
                    relative = Path(family) / f"s{size:010d}_{sample_name}"
                    destination = output / relative
                    atomic_write_bytes(destination, payload)
                    entries.append(
                        GeneratedEntry(
                            path=relative.as_posix(),
                            family=family,
                            size_requested=size,
                            size_bytes=len(payload),
                            noise_density=None,
                            seed=seed,
                            sha256=sha256_file(destination),
                            parameters={"container_type": sample_name.split(".")[-1]},
                        )
                    )
                continue

            clean = family_bytes(family, size, seed)
            for density in densities:
                payload = add_noise(clean, density, seed, f"{family}:{size}")
                relative = (
                    Path(family)
                    / f"s{size:010d}_n{_noise_slug(density)}_seed{seed}.bin"
                )
                destination = output / relative
                atomic_write_bytes(destination, payload)
                entries.append(
                    GeneratedEntry(
                        path=relative.as_posix(),
                        family=family,
                        size_requested=size,
                        size_bytes=len(payload),
                        noise_density=density,
                        seed=seed,
                        sha256=sha256_file(destination),
                        parameters=_family_parameters(family, size, seed),
                    )
                )

    manifest: dict[str, object] = {
        "schema_version": "mathzip-synthetic-corpus-v1",
        "generator_version": SYNTHETIC_GENERATOR_VERSION,
        "generator": "python/mathzip_bench/synthetic.py",
        "deterministic": True,
        "random_stream": "SHA-256 counter mode derived from the recorded seed",
        "seed": seed,
        "sizes": list(sizes),
        "noise_densities": list(densities),
        "families": list(families),
        "optional_capabilities": _optional_capabilities(
            include_zstd="already_compressed" in families
        ),
        "license": "Generated by MathZip; CC0-1.0",
        "entries": [entry.__dict__ for entry in entries],
        "skipped_optional_samples": skipped,
    }
    atomic_write_json(manifest_path, manifest)
    _write_manifest_csv(output / "manifest.csv", entries)
    marker_path.unlink()
    return manifest


def _family_parameters(family: str, size: int, seed: int) -> dict[str, object]:
    if family == "encrypted":
        encrypted_key, encrypted_nonce = _encrypted_key_nonce(size, seed)
        return {
            "cipher": "ChaCha20",
            "specification": "RFC 8439",
            "variant": "IETF 96-bit nonce",
            "initial_counter": 1,
            "key_hex": encrypted_key.hex(),
            "nonce_hex": encrypted_nonce.hex(),
            "key_derivation": (
                "HashStream(seed, 'encrypted:chacha20-rfc8439:key') first 32 bytes"
            ),
            "nonce_derivation": (
                "HashStream(seed, "
                f"'encrypted:chacha20-rfc8439:nonce:size={size}') first 12 bytes"
            ),
            "plaintext_schema": (
                "repeated 32-byte little-endian MZTL telemetry records, truncated "
                "to size_requested"
            ),
            "synthetic_key_is_not_secret": True,
        }
    return {
        "constant_00": {"value": 0},
        "constant_ff": {"value": 255},
        "linear": {"a": 17, "b": 29, "modulus": 256},
        "polynomial_d2": {"coefficients": [7, 11, 5], "modulus": 256},
        "polynomial_d3": {"coefficients": [7, 11, 5, 3], "modulus": 256},
        "polynomial_d4": {"coefficients": [7, 11, 5, 3, 13], "modulus": 256},
        "periodic": {"pattern": [1, 2, 3, 4, 9, 16, 25]},
        "multi_periodic": {"periods": [5, 11], "operation": "sum_mod_256"},
        "recurrence": {"order": 2, "coefficients": [1, 1], "modulus": 256},
        "lfsr8": {"width": 8, "taps": [0, 2, 3, 4], "initial_state": 167},
        "lfsr16": {"width": 16, "taps": [0, 2, 3, 5], "initial_state": 44257},
        "piecewise_mixed": {
            "segments": [
                "constant",
                "linear",
                "periodic",
                "recurrence",
                "random",
                "polynomial_d3_with_1pct_sparse_exceptions",
            ]
        },
        "random": {"generator": "SHA-256 counter mode"},
    }[family]


def _write_manifest_csv(path: Path, entries: Sequence[GeneratedEntry]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "path",
            "family",
            "size_requested",
            "size_bytes",
            "noise_density",
            "seed",
            "sha256",
            "parameters_json",
        ],
    )
    writer.writeheader()
    for entry in entries:
        row = dict(entry.__dict__)
        row["parameters_json"] = json.dumps(
            row.pop("parameters"), sort_keys=True, separators=(",", ":")
        )
        writer.writerow(row)
    atomic_write_text(path, buffer.getvalue())
