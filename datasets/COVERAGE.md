# MathSVG §20 dataset coverage

Generated from development/validation manifest metadata plus integrity-verified local generated catalogues. The generator does **not** inspect sealed holdout payloads.

A row is complete only when `meets_requirement=yes`. Generated or derived artifacts remain useful diagnostics but do not masquerade as real-world data.

## Summary

| Section | Met | Required | Gap |
|---|---:|---:|---:|
| incompressible-control | 12 | 12 | 0 |
| real-world-mixed | 13 | 13 | 0 |
| standard-general-purpose | 7 | 7 | 0 |
| structured-real-world | 15 | 15 | 0 |
| synthetic-diagnostic | 10 | 10 | 0 |

## standard-general-purpose

| Requirement | Status | Meets | Evidence | Note |
|---|---|:---:|---|---|
| Canterbury | selected-real | yes | dev-canterbury-alice29-txt, dev-canterbury-asyoulik-txt, dev-canterbury-cp-html (+7 more) | Selected in development/validation manifest. |
| Calgary | selected-real | yes | dev-calgary-bib, dev-calgary-book1, dev-calgary-book2 (+11 more) | Selected in development/validation manifest. |
| Silesia | selected-real | yes | dev-silesia-dickens, dev-silesia-mozilla, dev-silesia-mr (+9 more) | Selected in development/validation manifest. |
| enwik8 | selected-real | yes | dev-enwik8-enwik8 | Selected in development/validation manifest. |
| enwik9 | selected-real | yes | dev-enwik9-enwik9 | Selected in development/validation manifest. |
| Pizza & Chili | selected-real | yes | dev-pizza-chili-world-leaders | Selected in development/validation manifest. |
| Large Text Compression Benchmark subset | selected-real | yes | dev-enwik8-enwik8, dev-enwik9-enwik9 | Selected in development/validation manifest. |

## real-world-mixed

| Requirement | Status | Meets | Evidence | Note |
|---|---|:---:|---|---|
| Linux source | selected-real | yes | validation-linux-v6-10-kernel-sched-core-c | Selected in development/validation manifest. |
| LLVM source | selected-real | yes | validation-llvm-18-1-8-function-cpp | Selected in development/validation manifest. |
| Rust crates | selected-real | yes | validation-crates-io-serde-1-0-229-build-rs, validation-crates-io-serde-1-0-229-src-core-crate-root-rs, validation-crates-io-serde-1-0-229-src-core-de-ignored-any-rs (+22 more) | Selected in development/validation manifest. |
| JSON | selected-real | yes | development-zenodo-uav-drone1-message-1752507115773, development-zenodo-uav-drone1-message-1752507288092, development-zenodo-uav-drone2-message-1752507693033 (+2 more) | Selected in development/validation manifest. |
| XML | selected-real | yes | dev-silesia-xml | Selected in development/validation manifest. |
| CSV | selected-real | yes | validation-uci-1081-gsalc-csv | Selected in development/validation manifest. |
| Executables | selected-real | yes | dev-silesia-mozilla | Selected in development/validation manifest. |
| Shared libraries | selected-real | yes | dev-silesia-ooffice | Selected in development/validation manifest. |
| SQLite | selected-real | yes | validation-geoserver-2-28-2-natural-earth-gpkg | Selected in development/validation manifest. |
| Database dumps | selected-real | yes | development-wikimedia-enwiki-20260701-protected-titles-sql | Selected in development/validation manifest. |
| PDF | selected-real | yes | development-usgs-fs-2010-3086-pdf | Selected in development/validation manifest. |
| Container layers | selected-real | yes | validation-docker-official-hello-world-amd64-layer | Selected in development/validation manifest. |
| Git snapshots | selected-real | yes | validation-gnu-gnulib-20250729-git-bundle | Selected in development/validation manifest. |

## structured-real-world

| Requirement | Status | Meets | Evidence | Note |
|---|---|:---:|---|---|
| UAV flight logs | selected-real | yes | development-zenodo-uav-drone1-message-1752507115773, development-zenodo-uav-drone1-message-1752507288092, development-zenodo-uav-drone2-message-1752507693033 (+2 more) | Selected in development/validation manifest. |
| Telemetry | selected-real | yes | development-zenodo-uav-drone1-message-1752507115773, development-zenodo-uav-drone1-message-1752507288092, development-zenodo-uav-drone2-message-1752507693033 (+2 more) | Selected in development/validation manifest. |
| Public sensor datasets | selected-real | yes | validation-uci-1081-gsalc-csv | Selected in development/validation manifest. |
| PCM/WAV | selected-real | yes | validation-fsdd-v1-0-9-0-george-0-wav | Selected in development/validation manifest. |
| BMP | selected-real | yes | validation-zenodo-15978325-sem-tem-bmp | Selected in development/validation manifest. |
| TIFF raw | selected-real | yes | development-usgs-ofr-2006-1216-geotiff | Selected in development/validation manifest. |
| DICOM uncompressed | selected-real | yes | validation-idc-cptac-sar-ct-dicom | Selected in development/validation manifest. |
| FITS | selected-real | yes | validation-nasa-hst-fos-y19g0309t-fits | Selected in development/validation manifest. |
| Raster GIS | selected-real | yes | development-usgs-ofr-2006-1216-geotiff | Selected in development/validation manifest. |
| PLY/LAS | selected-real | yes | development-stanford-bunny-1994-bun000-ply | Selected in development/validation manifest. |
| Climate arrays | selected-real | yes | development-noaa-globaltemp-v5-1-0-netcdf | Selected in development/validation manifest. |
| Seismic arrays | selected-real | yes | validation-earthscope-iu-anmo-bhz-20100227-mseed | Selected in development/validation manifest. |
| Integer columns | selected-real | yes | validation-nyc-tlc-yellow-trip-2020-04-parquet | Selected in development/validation manifest. |
| Float columns | selected-real | yes | validation-nyc-tlc-yellow-trip-2020-04-parquet | Selected in development/validation manifest. |
| Fixed-record binaries | selected-real | yes | validation-earthscope-iu-anmo-bhz-20100227-mseed | Selected in development/validation manifest. |

## incompressible-control

| Requirement | Status | Meets | Evidence | Note |
|---|---|:---:|---|---|
| Cryptographic random | selected-control | yes | dev-control-cryptographic-random | Selected in development/validation manifest. |
| Encrypted data | selected-control | yes | dev-control-encrypted | Selected in development/validation manifest. |
| gzip | selected-control | yes | dev-control-gzip | Selected in development/validation manifest. |
| Zstd | selected-control | yes | development-control-generated-zstd | Selected in development/validation manifest. |
| XZ | selected-control | yes | dev-control-xz | Selected in development/validation manifest. |
| ZIP | selected-control | yes | dev-control-zip | Selected in development/validation manifest. |
| PNG | selected-control | yes | development-control-generated-png | Selected in development/validation manifest. |
| JPEG | selected-control | yes | development-control-generated-jpeg | Selected in development/validation manifest. |
| AVIF | selected-control | yes | validation-control-libavif-v1-2-1-avif | Selected in development/validation manifest. |
| MP3 | selected-control | yes | validation-control-minimp3-ill2-layer3 | Selected in development/validation manifest. |
| FLAC | selected-control | yes | validation-control-ietf-flac-mono-audio | Selected in development/validation manifest. |
| MP4/AV1 | selected-control | yes | development-control-generated-mp4 | Selected in development/validation manifest. |

## synthetic-diagnostic

| Requirement | Status | Meets | Evidence | Note |
|---|---|:---:|---|---|
| Constant | selected-synthetic | yes | dev-synthetic-constant-00 | Selected in development/validation manifest. |
| Linear | selected-synthetic | yes | dev-synthetic-linear | Selected in development/validation manifest. |
| Polynomial | selected-synthetic | yes | dev-synthetic-polynomial-d2 | Selected in development/validation manifest. |
| Periodic | selected-synthetic | yes | dev-synthetic-periodic | Selected in development/validation manifest. |
| Recurrence | selected-synthetic | yes | dev-synthetic-recurrence | Selected in development/validation manifest. |
| LFSR | selected-synthetic | yes | dev-synthetic-lfsr8 | Selected in development/validation manifest. |
| Piecewise | selected-synthetic | yes | dev-synthetic-piecewise-mixed | Selected in development/validation manifest. |
| Sparse exceptions | selected-synthetic | yes | dev-synthetic-linear-noise-0p001 | Selected in development/validation manifest. |
| Multiple noise levels | selected-synthetic | yes | dev-synthetic-linear-noise-0p001, dev-synthetic-linear-noise-0p25 | Selected in development/validation manifest. |
| Mixed structured/random | selected-synthetic | yes | dev-synthetic-piecewise-mixed | Selected in development/validation manifest. |

## Integrity notes

- Exact duplicate payload groups in the selected manifests: 0.
- The development builder rejects unpinned generated paths and deduplicates exact upstream payload bytes before applying the 70% rule.
- `origin=derived`, `origin=synthetic`, and `origin=control` are always non-primary.
