"""Generate redistributable mixed-file and source-version corpora."""

from __future__ import annotations

import csv
import gzip
import io
import json
import lzma
import platform
import shutil
import sqlite3
import struct
import subprocess
import wave
import zipfile
from pathlib import Path
from typing import Any

from .common import atomic_write_json, iter_files, sha256_file
from .synthetic import HashStream, _minimal_mp4, _minimal_png, _ONE_PIXEL_JPEG


def _pdf_bytes(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def _bmp_bytes(width: int = 128, height: int = 96) -> bytes:
    row_size = (width * 3 + 3) & ~3
    pixels = bytearray()
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row.extend(((x + y) & 0xFF, (2 * y) & 0xFF, (2 * x) & 0xFF))
        row.extend(b"\0" * (row_size - len(row)))
        pixels.extend(row)
    offset = 14 + 40
    file_size = offset + len(pixels)
    return (
        b"BM"
        + struct.pack("<IHHI", file_size, 0, 0, offset)
        + struct.pack(
            "<IIIHHIIIIII",
            40,
            width,
            height,
            1,
            24,
            0,
            len(pixels),
            2835,
            2835,
            0,
            0,
        )
        + pixels
    )


def _wav_bytes(sample_count: int = 44100) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44100)
        samples = bytearray()
        for index in range(sample_count):
            value = ((index * 997) % 65536) - 32768
            samples.extend(struct.pack("<h", value))
        output.writeframes(bytes(samples))
    return buffer.getvalue()


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    return buffer.getvalue()


def _compile_generated_sources(files_root: Path) -> list[dict[str, Any]]:
    compiler = shutil.which("cc")
    if not compiler:
        return [{"path": "program.elf", "reason": "cc unavailable"}, {"path": "libsample.so", "reason": "cc unavailable"}]
    source = files_root / "sample.c"
    outputs = [
        (
            files_root / "program.elf",
            [compiler, "-O2", "-s", "-Wl,--build-id=none", str(source), "-o"],
        ),
        (
            files_root / "libsample.so",
            [
                compiler,
                "-O2",
                "-s",
                "-fPIC",
                "-shared",
                "-Wl,--build-id=none",
                str(source),
                "-o",
            ],
        ),
    ]
    skipped: list[dict[str, Any]] = []
    for destination, prefix in outputs:
        try:
            completed = subprocess.run(
                [*prefix, str(destination)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            skipped.append({"path": destination.name, "reason": str(exc)})
            continue
        if completed.returncode != 0:
            skipped.append(
                {
                    "path": destination.name,
                    "reason": completed.stderr.decode("utf-8", errors="replace")[:500],
                }
            )
    return skipped


def _zstd_file(source: Path, destination: Path) -> str | None:
    executable = shutil.which("zstd")
    if not executable:
        return "zstd executable unavailable"
    try:
        completed = subprocess.run(
            [executable, "-q", "-f", "-19", str(source), "-o", str(destination)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if completed.returncode != 0:
        return completed.stderr.decode("utf-8", errors="replace")[:500]
    return None


def generate_mixed_corpus(root: Path, seed: int) -> dict[str, Any]:
    files = root / "files"
    files.mkdir(parents=True, exist_ok=True)
    repeated_text = (
        "MathZip models bytes as piecewise mathematical functions plus exact residuals.\n"
        * 1024
    )
    payloads: dict[str, bytes] = {
        "sample.txt": repeated_text.encode("utf-8"),
        "sample.md": ("# Generated corpus\n\n" + repeated_text).encode("utf-8"),
        "sample.json": json.dumps(
            [{"index": index, "value": (17 * index + 29) % 256} for index in range(2048)],
            separators=(",", ":"),
        ).encode("utf-8"),
        "sample.xml": (
            "<records>"
            + "".join(
                f'<record id="{index}">{(index * index + 3 * index + 7) % 256}</record>'
                for index in range(2048)
            )
            + "</records>"
        ).encode("utf-8"),
        "sample.csv": (
            "index,linear,quadratic\n"
            + "".join(
                f"{index},{(17*index+29)%256},{(index*index+3*index+7)%256}\n"
                for index in range(4096)
            )
        ).encode("ascii"),
        "sample.c": b"#include <stdio.h>\nint main(void){puts(\"MathZip generated ELF\");return 0;}\n",
        "sample.cpp": b"#include <iostream>\nint main(){std::cout << \"MathZip\" << '\\n';}\n",
        "sample.rs": b"fn main() { println!(\"MathZip\"); }\n",
        "sample.py": b"print('MathZip generated Python source')\n",
        "Sample.java": b"final class Sample { public static void main(String[] a) { System.out.println(\"MathZip\"); } }\n",
        "sample.pdf": _pdf_bytes("MathZip deterministic generated PDF"),
        "sample.bmp": _bmp_bytes(),
        "sample.wav": _wav_bytes(),
        "sample.png": _minimal_png(65536, seed),
        "sample.jpg": _ONE_PIXEL_JPEG,
        "sample.mp4": _minimal_mp4(65536, seed),
        "random.bin": HashStream(seed, "mixed-random").read(65536),
    }
    for name, payload in payloads.items():
        (files / name).write_bytes(payload)

    database = sqlite3.connect(files / "sample.sqlite")
    try:
        database.execute(
            "CREATE TABLE measurements (id INTEGER PRIMARY KEY, linear INTEGER, note TEXT)"
        )
        database.executemany(
            "INSERT INTO measurements VALUES (?, ?, ?)",
            [
                (index, (17 * index + 29) % 256, f"generated-row-{index % 64}")
                for index in range(4096)
            ],
        )
        database.commit()
        database.execute("VACUUM")
    finally:
        database.close()

    archive_source = payloads["sample.txt"] + payloads["sample.csv"]
    (files / "sample.gz").write_bytes(gzip.compress(archive_source, 9, mtime=0))
    (files / "sample.xz").write_bytes(
        lzma.compress(archive_source, format=lzma.FORMAT_XZ, preset=9)
    )
    (files / "sample.zip").write_bytes(
        _zip_bytes({"sample.txt": payloads["sample.txt"], "sample.csv": payloads["sample.csv"]})
    )
    skipped = _compile_generated_sources(files)
    zstd_error = _zstd_file(files / "sample.txt", files / "sample.zst")
    if zstd_error:
        skipped.append({"path": "sample.zst", "reason": zstd_error})

    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in iter_files(files)
    ]
    manifest = {
        "schema_version": "mathzip-generated-mixed-v1",
        "seed": seed,
        "generator": "python/mathzip_bench/auxiliary.py",
        "license": "CC0-1.0 for generated source/data; toolchain output is generated from CC0 source",
        "toolchain": {"platform": platform.platform(), "cc": shutil.which("cc")},
        "entries": entries,
        "skipped_optional_files": skipped,
    }
    atomic_write_json(root / "manifest.json", manifest)
    return manifest


def _write_snapshot_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def generate_git_snapshot_corpus(root: Path, seed: int) -> dict[str, Any]:
    versions = root / "versions"
    combined = root / "combined"
    combined.mkdir(parents=True, exist_ok=True)
    sources = {
        "v1": {
            "src/lib.rs": "pub fn value(i: u32) -> u32 { (17 * i + 29) % 256 }\n",
            "src/main.rs": "fn main() { println!(\"version 1\"); }\n",
            "README.md": "# Synthetic project\n\nVersion one.\n",
        },
        "v2": {
            "src/lib.rs": "pub fn value(i: u32) -> u32 { (17 * i + 31) % 256 }\npub fn square(i: u32) -> u32 { i * i }\n",
            "src/main.rs": "fn main() { println!(\"version 2\"); }\n",
            "README.md": "# Synthetic project\n\nVersion two adds square().\n",
            "tests/smoke.rs": "#[test]\nfn smoke() { assert_eq!(2 + 2, 4); }\n",
        },
        "v3": {
            "src/lib.rs": "pub fn value(i: u32) -> u32 { (19 * i + 31) % 256 }\npub fn square(i: u32) -> u32 { i.saturating_mul(i) }\n",
            "src/main.rs": "fn main() { println!(\"version 3\"); }\n",
            "README.md": "# Synthetic project\n\nVersion three changes the recurrence.\n",
            "tests/smoke.rs": "#[test]\nfn smoke() { assert_eq!(3 * 3, 9); }\n",
        },
    }
    for version, files in sources.items():
        for relative, text in files.items():
            _write_snapshot_file(versions / version / relative, text * 256)

    combined_records: list[dict[str, Any]] = []
    all_versions_path = combined / "all_versions.bin"
    with all_versions_path.open("wb") as all_output:
        all_offset = 0
        for version in sorted(sources):
            version_path = combined / f"{version}.bin"
            members: list[dict[str, Any]] = []
            offset = 0
            with version_path.open("wb") as output:
                for path in iter_files(versions / version):
                    payload = path.read_bytes()
                    output.write(payload)
                    all_output.write(payload)
                    members.append(
                        {
                            "path": path.relative_to(versions / version).as_posix(),
                            "offset": offset,
                            "length": len(payload),
                            "sha256": sha256_file(path),
                        }
                    )
                    offset += len(payload)
                    all_offset += len(payload)
            combined_records.append(
                {
                    "version": version,
                    "path": version_path.relative_to(root).as_posix(),
                    "size_bytes": offset,
                    "sha256": sha256_file(version_path),
                    "members": members,
                }
            )
    manifest = {
        "schema_version": "mathzip-generated-git-snapshots-v1",
        "seed": seed,
        "generator": "python/mathzip_bench/auxiliary.py",
        "license": "CC0-1.0",
        "versions": combined_records,
        "all_versions": {
            "path": all_versions_path.relative_to(root).as_posix(),
            "size_bytes": all_versions_path.stat().st_size,
            "sha256": sha256_file(all_versions_path),
        },
    }
    atomic_write_json(root / "manifest.json", manifest)
    return manifest


def generate_auxiliary_corpora(output: Path, seed: int, force: bool = False) -> dict[str, Any]:
    mixed = output / "mixed"
    snapshots = output / "git_snapshots"
    if not force and ((mixed / "manifest.json").exists() or (snapshots / "manifest.json").exists()):
        raise FileExistsError("generated corpus already exists; pass --force to regenerate")
    if force:
        for target in (mixed, snapshots):
            if target.exists():
                shutil.rmtree(target)
    return {
        "mixed": generate_mixed_corpus(mixed, seed),
        "git_snapshots": generate_git_snapshot_corpus(snapshots, seed),
    }
