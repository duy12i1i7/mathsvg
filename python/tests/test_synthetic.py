from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mathzip_bench.synthetic import (
    DEFAULT_NOISE,
    HashStream,
    _chacha20_block,
    _encrypted_key_nonce,
    _structured_plaintext,
    _zstd_bytes,
    add_noise,
    chacha20_xor,
    corpus_manifest_is_current,
    family_bytes,
    generate_corpus,
)
from mathzip_bench.verification import verify_synthetic_manifest


class SyntheticTests(unittest.TestCase):
    def test_available_zstd_failure_is_not_cached_as_an_optional_skip(self) -> None:
        failed = subprocess.CompletedProcess(
            args=["zstd"], returncode=1, stdout=b"", stderr=b"failed"
        )
        with patch(
            "mathzip_bench.synthetic.shutil.which",
            return_value="/usr/bin/zstd",
        ), patch(
            "mathzip_bench.synthetic.subprocess.run",
            return_value=failed,
        ):
            with self.assertRaisesRegex(OSError, "generation failed: failed"):
                _zstd_bytes(b"payload")

    def test_all_core_families_are_deterministic(self) -> None:
        families = (
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
        )
        for family in families:
            with self.subTest(family=family):
                first = family_bytes(family, 1024, 123)
                second = family_bytes(family, 1024, 123)
                self.assertEqual(first, second)
                self.assertEqual(len(first), 1024)

    def test_chacha20_matches_rfc_8439_block_vector(self) -> None:
        key = bytes(range(32))
        nonce = bytes.fromhex("000000090000004a00000000")
        expected = bytes.fromhex(
            "10f1e7e4d13b5915500fdd1fa32071c4"
            "c7d1f4c733c068030422aa9ac3d46c4e"
            "d2826446079faa0914c2d705d98b02a2"
            "b5129cd1de164eb9cbd083e8a2503c4e"
        )
        self.assertEqual(_chacha20_block(key, 1, nonce), expected)

    def test_chacha20_matches_rfc_8439_encryption_vector(self) -> None:
        plaintext = (
            b"Ladies and Gentlemen of the class of '99: If I could offer you only "
            b"one tip for the future, sunscreen would be it."
        )
        key = bytes(range(32))
        nonce = bytes.fromhex("000000000000004a00000000")
        expected = bytes.fromhex(
            "6e2e359a2568f98041ba0728dd0d6981"
            "e97e7aec1d4360c20a27afccfd9fae0b"
            "f91b65c5524733ab8f593dabcd62b357"
            "1639d624e65152ab8f530c359f0861d8"
            "07ca0dbf500d6a6156a38e088a22b65e"
            "52bc514d16ccf806818ce91ab7793736"
            "5af90bbf74a35be6b40b8eedf2785e42"
            "874d"
        )
        ciphertext = chacha20_xor(plaintext, key, nonce, initial_counter=1)
        self.assertEqual(ciphertext, expected)
        self.assertEqual(
            chacha20_xor(ciphertext, key, nonce, initial_counter=1), plaintext
        )

    def test_encrypted_family_encrypts_structured_plaintext(self) -> None:
        size = 4096
        seed = 1297748005
        plaintext = _structured_plaintext(size)
        ciphertext = family_bytes("encrypted", size, seed)
        key, nonce = _encrypted_key_nonce(size, seed)
        self.assertNotEqual(ciphertext, plaintext)
        self.assertEqual(
            chacha20_xor(ciphertext, key, nonce, initial_counter=1), plaintext
        )
        self.assertEqual(ciphertext, family_bytes("encrypted", size, seed))

    def test_encrypted_family_has_a_pinned_official_seed_vector(self) -> None:
        size = 4096
        seed = 1297748005
        key, nonce = _encrypted_key_nonce(size, seed)
        plaintext = _structured_plaintext(size)
        ciphertext = family_bytes("encrypted", size, seed)
        self.assertEqual(
            key.hex(),
            "27f54699a1cf0dab6316bdae1b92361e"
            "60381685a5993e0ddb6d221f8da43fa6",
        )
        self.assertEqual(nonce.hex(), "217ecfe88832c77a66996d5d")
        self.assertEqual(
            hashlib.sha256(plaintext).hexdigest(),
            "40230e87929e43fd929be9b2c8cafe85"
            "e5f4d4c05cd2598f006fbc7ad58204d4",
        )
        self.assertEqual(
            hashlib.sha256(ciphertext).hexdigest(),
            "68699529e0cf99fe05e26d08baafa6dd"
            "26f337257df6ac43a531dd445c628f4b",
        )

    def test_chacha20_boundary_lengths_and_counter_errors(self) -> None:
        key = bytes(range(32))
        nonce = bytes(12)
        for size in (0, 1, 31, 63, 64, 65, 127, 128, 129):
            plaintext = bytes(index % 251 for index in range(size))
            ciphertext = chacha20_xor(plaintext, key, nonce)
            self.assertEqual(chacha20_xor(ciphertext, key, nonce), plaintext)
        self.assertEqual(len(chacha20_xor(b"x", key, nonce, 0xFFFFFFFF)), 1)
        with self.assertRaises(ValueError):
            chacha20_xor(bytes(65), key, nonce, 0xFFFFFFFF)
        for invalid_key in (bytes(31), bytes(33)):
            with self.assertRaises(ValueError):
                chacha20_xor(b"x", invalid_key, nonce)
        for invalid_nonce in (bytes(11), bytes(13)):
            with self.assertRaises(ValueError):
                chacha20_xor(b"x", key, invalid_nonce)

    def test_noise_has_exact_exception_count(self) -> None:
        clean = bytes(10_000)
        noisy = add_noise(clean, 0.05, 42, "test")
        self.assertEqual(sum(left != right for left, right in zip(clean, noisy)), 500)

    def test_hash_stream_is_chunking_independent(self) -> None:
        first = HashStream(7, "stream").read(100)
        stream = HashStream(7, "stream")
        second = stream.read(13) + stream.read(87)
        self.assertEqual(first, second)

    def test_manifest_and_files_verify(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "synthetic"
            first = generate_corpus(
                output,
                seed=123,
                sizes=(0, 1, 64),
                noise_densities=DEFAULT_NOISE,
                families=(
                    "constant_00",
                    "linear",
                    "random",
                    "encrypted",
                    "already_compressed",
                ),
            )
            self.assertGreater(len(first["entries"]), 0)
            self.assertEqual(first["generator_version"], 2)
            self.assertTrue(
                corpus_manifest_is_current(
                    output,
                    seed=123,
                    sizes=(0, 1, 64),
                    noise_densities=DEFAULT_NOISE,
                    families=(
                        "constant_00",
                        "linear",
                        "random",
                        "encrypted",
                        "already_compressed",
                    ),
                )
            )
            self.assertFalse(
                corpus_manifest_is_current(
                    output,
                    seed=124,
                    sizes=(0, 1, 64),
                    noise_densities=DEFAULT_NOISE,
                    families=(
                        "constant_00",
                        "linear",
                        "random",
                        "encrypted",
                        "already_compressed",
                    ),
                )
            )
            errors, warnings = verify_synthetic_manifest(output / "manifest.json")
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])
            jpeg_entries = [
                entry
                for entry in first["entries"]
                if str(entry["path"]).endswith(".jpg")
            ]
            self.assertTrue(jpeg_entries)
            jpeg = (output / jpeg_entries[0]["path"]).read_bytes()
            self.assertTrue(jpeg.startswith(b"\xff\xd8"))
            self.assertTrue(jpeg.endswith(b"\xff\xd9"))
            encrypted = [
                entry for entry in first["entries"] if entry["family"] == "encrypted"
            ]
            self.assertTrue(encrypted)
            self.assertEqual(encrypted[0]["parameters"]["cipher"], "ChaCha20")
            self.assertEqual(encrypted[0]["parameters"]["specification"], "RFC 8439")

            marker = output / ".generation-in-progress.json"
            marker.write_text("{}", encoding="utf-8")
            self.assertFalse(
                corpus_manifest_is_current(
                    output,
                    seed=123,
                    sizes=(0, 1, 64),
                    noise_densities=DEFAULT_NOISE,
                    families=(
                        "constant_00",
                        "linear",
                        "random",
                        "encrypted",
                        "already_compressed",
                    ),
                )
            )
            errors, _ = verify_synthetic_manifest(output / "manifest.json")
            self.assertTrue(any("unfinished generation marker" in value for value in errors))
            marker.unlink()

            orphan = output / "undeclared.bin"
            orphan.write_bytes(b"orphan")
            errors, _ = verify_synthetic_manifest(output / "manifest.json")
            self.assertTrue(any("undeclared file" in value for value in errors))
            generate_corpus(
                output,
                seed=123,
                sizes=(0, 1, 64),
                families=(
                    "constant_00",
                    "linear",
                    "random",
                    "encrypted",
                    "already_compressed",
                ),
                force=True,
            )
            self.assertFalse(orphan.exists())
            errors, warnings = verify_synthetic_manifest(output / "manifest.json")
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])

            (output / "manifest.json").write_bytes(b"\xff")
            self.assertFalse(
                corpus_manifest_is_current(
                    output,
                    seed=123,
                    sizes=(0, 1, 64),
                    noise_densities=DEFAULT_NOISE,
                    families=(
                        "constant_00",
                        "linear",
                        "random",
                        "encrypted",
                        "already_compressed",
                    ),
                )
            )
            generate_corpus(
                output,
                seed=123,
                sizes=(0, 1, 64),
                families=(
                    "constant_00",
                    "linear",
                    "random",
                    "encrypted",
                    "already_compressed",
                ),
                force=True,
            )
            errors, warnings = verify_synthetic_manifest(output / "manifest.json")
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])

            with patch(
                "mathzip_bench.synthetic._optional_capabilities",
                return_value={"zstd": {"used": True, "available": "changed"}},
            ):
                self.assertFalse(
                    corpus_manifest_is_current(
                        output,
                        seed=123,
                        sizes=(0, 1, 64),
                        noise_densities=DEFAULT_NOISE,
                        families=(
                            "constant_00",
                            "linear",
                            "random",
                            "encrypted",
                            "already_compressed",
                        ),
                    )
                )

    def test_full_encrypted_matrix_has_42_entries_and_size_scoped_nonces(
        self,
    ) -> None:
        sizes = (0, 1, 31, 256, 4096, 65536, 1048576)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "encrypted"
            manifest = generate_corpus(
                output,
                seed=1297748005,
                sizes=sizes,
                noise_densities=DEFAULT_NOISE,
                families=("encrypted",),
            )
            entries = manifest["entries"]
            self.assertEqual(len(entries), 42)
            clean_entries = [
                entry for entry in entries if entry["noise_density"] == 0.0
            ]
            self.assertEqual(len(clean_entries), len(sizes))
            self.assertEqual(
                len(
                    {
                        entry["parameters"]["nonce_hex"]
                        for entry in clean_entries
                    }
                ),
                len(sizes),
            )
            for entry in clean_entries:
                ciphertext = (output / entry["path"]).read_bytes()
                parameters = entry["parameters"]
                plaintext = chacha20_xor(
                    ciphertext,
                    bytes.fromhex(parameters["key_hex"]),
                    bytes.fromhex(parameters["nonce_hex"]),
                    parameters["initial_counter"],
                )
                self.assertEqual(
                    plaintext, _structured_plaintext(entry["size_requested"])
                )
