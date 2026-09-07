# MathZip benchmark datasets

Large corpora are intentionally not committed. The downloader reads the
versioned JSON manifests in `datasets/manifests/`, downloads into an isolated
cache, verifies declared byte counts and cryptographic checksums, and extracts
only regular files whose normalized paths stay below the destination.

## Quick preparation

From the repository root:

```bash
python3 python/generate_synthetic.py \
  --output datasets/synthetic \
  --seed 1297748005

python3 python/download_datasets.py \
  --config configs/quick.yaml \
  --output datasets/data
```

The quick config enables Canterbury, Calgary, and Silesia. `enwik8` is present
in the config but disabled because it is resource-dependent. Enable it in a
local config or select it explicitly:

```bash
python3 python/download_datasets.py \
  --config configs/quick.yaml \
  --output datasets/data \
  --dataset enwik8
```

For the focused 100 MB enwik8 suite, prefer its dedicated manifest/config:

```bash
python3 python/download_datasets.py \
  --config configs/enwik8.yaml \
  --output datasets/data
scripts/benchmark_enwik8.sh
```

The script verifies/downloads the pinned input unless
`MATHZIP_SKIP_DOWNLOAD=1` is set. That override is appropriate only after the
local `dataset.json` and extracted checksum have already been verified.

The full preparation downloads about 500 MB of archives and expands to more
than 1.4 GB:

```bash
python3 python/generate_synthetic.py \
  --output datasets/synthetic_full \
  --seed 1297748005 \
  --sizes 0,1,31,256,4096,65536,1048576

python3 python/generate_auxiliary_corpora.py \
  --output datasets/generated \
  --seed 1297748005

python3 python/download_datasets.py \
  --config configs/full.yaml \
  --output datasets/data
```

Prepared datasets contain `dataset.json` with the exact source URL, manifest
hash, downloaded archive hash, and SHA-256 of every extracted file. Re-running
without `--force` verifies all those files and reuses an exact match. It refuses
an existing mismatched directory. `--force` replaces only the named dataset
directory after a new extraction has succeeded.

## Pinned upstream archives

| Manifest | Archive bytes | Archive SHA-256 |
|---|---:|---|
| Canterbury `cantrbry.tar.gz` | 739,071 | `f140e8a5b73d3f53198555a63bfb827889394a42f20825df33c810c3d5e3f8fb` |
| Calgary `calgary.tar.gz` | 1,070,276 | `e109eebdc19c5cee533c58bd6a49a4be3a77cc52f84ba234a089148a4f2093b7` |
| Silesia `silesia.zip` | 67,633,896 | `7d1dd71bfecda66a0ca30d863ed031809f67ecf12717a60fe72c1cc39e28434e` |
| enwik8 `enwik8.zip` | 36,445,475 | `547994d9980ebed1288380d652999f38a14fe291a6247c157c3d33d4932534bc` |
| enwik9 `enwik9.zip` | 322,592,222 | `99cdb5ac84392252d3f0912ccedd195bc95bd80cbef3b0cdf2eee4ad9a3b7a51` |
| Pizza & Chili `world_leaders.gz` | 8,287,665 | `9bb5e541067e1c63387dc600458f676efcb509452800c7fc45a3369fe7979052` |

The enwik payloads and Pizza & Chili payload also have independently declared
post-extraction checksums. Checksum failure is fatal; there is no
trust-on-first-use mode.

## Normalization

- Canterbury is extracted as 11 individual files and a byte-for-byte
  `combined.bin`. `combined.manifest.json` records every member boundary and
  SHA-256.
- The upstream Calgary archive contains the retired `paper3` through `paper6`.
  The manifest selects the canonical 14-file corpus and creates its combined
  stream.
- Silesia retains all 12 files. Quick benchmarks select four; full benchmarks
  report all 12 individually.
- `enwik8` and `enwik9` are each normalized to one uncompressed file.
- The resource-bounded Pizza & Chili subset is `world_leaders`.

## Extraction safety limits

The implementation does not call `extractall`. It rejects absolute paths,
`..`, drive-like names, backslashes, NUL bytes, duplicate destinations,
symlinks, hardlinks, devices, and non-regular tar members. Each manifest caps
download bytes, archive entries, total uncompressed bytes, and ZIP compression
ratio. Downloads use a `.part` file and are atomically promoted only after the
declared size is satisfied.

## Synthetic and generated corpora

`generate_synthetic.py` records its seed and SHA-256 for every output. Random
and noise bytes come from a portable SHA-256 counter stream. The required noise
densities are 0%, 0.1%, 1%, 5%, 10%, and 25%. Families include constants,
affine and degree-2/3/4 polynomial sequences, periodic and multi-periodic
sequences, recurrence, two LFSRs, piecewise mixed data, random bytes, and small
already-compressed/container samples.

`generate_auxiliary_corpora.py` creates the mixed-file and version-snapshot
corpora from CC0 source/data. It includes text, markup, tabular data, five
source languages, SQLite, PDF, BMP, WAV, PNG, JPEG, ZIP, gzip, XZ, MP4, random
bytes, and—when local tools exist—Zstandard, an ELF executable, and a shared
library. Optional omissions are written to the manifest rather than hidden.

## MathSVG Absolute selection

MathSVG uses a stricter manifest vocabulary:

- `origin=real, primary=true` is independently sourced upstream data and is the
  only class counted toward the 70% file/byte rule.
- `origin=synthetic` is deterministic generator output used for mathematical
  diagnostics.
- `origin=control` is deterministic random, encrypted, or already-compressed
  data used to detect expansion.
- `origin=derived` is a locally generated format/toolchain artifact. It can be
  benchmarked, but it never masquerades as a real JSON, SQLite, WAV, image, or
  Git corpus.

The development builder validates every selected payload against its pinned
metadata. It also deduplicates exact upstream bytes before composition:
Calgary `pic` and Canterbury `ptt5` have the same SHA-256, so only the first
receives a primary benchmark row. The base builder prepares 39 unique real
files plus 16 non-primary diagnostics/controls (55 files total). After the
pinned research-corpus bundle below is merged, the current development
manifest contains 49 qualifying real files out of 69 (71.0145%) and 99.9185%
qualifying real bytes. The separate validation manifest contains 37 qualifying
real files out of 40 (92.5%) and 99.9012% qualifying real bytes. Combined, the
open manifests contain 86/109 qualifying files (78.8991%) and 99.9176%
qualifying bytes.

Regenerating the development manifest hashes about 1.36 GB of local upstream
payloads and should be scheduled outside an active benchmark:

```bash
python3 -m mathsvg.python.datasets.build_development_manifest
```

The exact §20 gap matrix is separate from the selection itself:

```bash
python3 -m mathsvg.python.datasets.coverage_matrix
```

It writes `datasets/COVERAGE.md` and
`mathsvg/results/manifests/dataset-coverage.json`. Generated substitutes are
reported as diagnostic-only, and missing formats stay missing.

### Pinned Rust-crate validation source

`materialize_crate_validation.py` adds an independent real Rust source domain
without downloading automatically:

| Source | Archive bytes | Archive SHA-256 | License |
|---|---:|---|---|
| `https://static.crates.io/crates/serde/serde-1.0.229.crate` | 83,669 | `4148590afebada386688f18773da617792bf2ef03ffc1e4cbd2b1d45b023e0ba` | MIT OR Apache-2.0 |

The recipe pins 25 `.rs` members individually (538,001 bytes), validates all
33 tar catalogue entries and 561,555 uncompressed catalogue bytes, and rejects
path traversal, duplicate paths, symlinks, hardlinks, devices, archive drift,
and destination overwrite. After obtaining the archive independently:

```bash
python3 -m mathsvg.python.datasets.materialize_crate_validation \
  /path/to/serde-1.0.229.crate
```

All 25 rows share `split_group=crates-io-serde-1-0-229`, preventing a later
split from receiving correlated members. This operation is validation-only and
does not access sealed holdout payloads.

### Pinned §20 research-corpus completion bundle

`materialize_research_corpus.py` adds provenance-backed mixed/structured data
and codec controls without downloading automatically:

```bash
python3 -m mathsvg.python.datasets.materialize_research_corpus \
  /path/to/exact-downloaded-files
python3 -m mathsvg.python.datasets.coverage_matrix
```

The authoritative machine-readable provenance record is
`mathsvg/results/manifests/research-corpus-report.json`. For every direct file
it records the exact cache filename, URL, version/commit or immutable registry
digest, license/rights statement, byte count, and SHA-256. It also records:

- the complete Zenodo UAV ZIP catalogue (788 entries, 785 files), archive
  SHA-256, and five individually hashed real telemetry messages;
- the complete Stanford Bunny tar catalogue (21 entries, 22,233,698 regular
  file bytes), archive SHA-256, and the individually hashed raw
  `bunny/data/bun000.ply` laser scan;
- the Docker Official `hello-world` OCI index digest, linux/amd64 manifest
  digest, and registry layer digest;
- generated Zstd, PNG, JPEG, and MP4 as `origin=control`, never real data; and
- libavif, IETF FLAC, and minimp3 conformance files as non-primary controls.

The selected source bundle is 37,720,672 bytes, below the 100 MiB cap. The
completion run downloaded 71,279,781 payload bytes in total: that includes two
discarded USGS/PDAL probes which contained XYZ/templates rather than LAS, one
redundant Zenodo download, and a superseded DICOM SR probe. Those probes are
not selected and cannot affect benchmark composition.

The real structured selections include a Zenodo waypoint-flight telemetry
dataset, a raw Stanford Cyberware range scan, uncompressed USGS GeoTIFF,
NOAA climate NetCDF, EarthScope miniSEED, recorded-speech PCM WAV, NASA/MAST
FITS, NYC TLC Parquet, and an IDC CPTAC-SAR CT Image Storage object. The DICOM
materializer additionally requires the `DICM` preamble, CT Image Storage SOP
Class UID, and uncompressed Implicit VR Little Endian Transfer Syntax UID; a
matching file hash alone is not enough.

The bundle deliberately leaves generated BMP/SQLite/version streams and
pydicom/PX4/GDAL fixtures ineligible for `selected-real`.
`datasets/COVERAGE.md` is the canonical coverage statement; the current matrix
meets all 57 section-20 requirements and reports zero gaps without promoting
those generated fixtures.

## Rights and provenance

The downloader does not relicense upstream data. Canterbury, Calgary, and
Silesia aggregate files with their own provenance; consult the linked upstream
descriptions before redistribution. enwik8/enwik9 derive from a Wikipedia dump
and remain subject to the applicable Wikimedia terms. Pizza & Chili files
retain their upstream terms. Only the locally generated synthetic, mixed, and
snapshot data is declared CC0-1.0 by this project.

To validate local corpus evidence:

```bash
python3 python/verify_results.py \
  --synthetic-manifest datasets/synthetic/manifest.json \
  --dataset-root datasets/data \
  --generated-root datasets/generated
```
