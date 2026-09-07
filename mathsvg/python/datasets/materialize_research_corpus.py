#!/usr/bin/env python3
"""Materialize the pinned §20 research-corpus completion bundle.

The command has no downloader and never opens holdout data.  Callers download
the exact files listed in ``DIRECT_SOURCES`` and ``UAV_TELEMETRY`` into one
source directory.  This module then verifies every byte before atomically
creating development/validation payloads and merging their manifest rows.

Real rows are limited to upstream material with explicit collection or project
provenance.  Codec fixtures and locally generated compressed media are controls,
never primary real data.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import stat
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from typing import BinaryIO, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
    from mathsvg.python.datasets.manifest import (  # type: ignore[import-not-found]
        DatasetEntry,
        ManifestError,
        composition,
        load_manifest,
        report,
        validate_collection,
    )
    from mathsvg.python.datasets.materialize_validation import (
        MaterializationError,
        merge_development_validation,
        write_manifest,
    )
    from mathsvg.python.datasets.materialize_crate_validation import (
        CrateLimits,
        CrateMember,
        CrateSource,
        materialize_crate,
    )
else:
    from .manifest import (
        DatasetEntry,
        ManifestError,
        composition,
        load_manifest,
        report,
        validate_collection,
    )
    from .materialize_validation import (
        MaterializationError,
        merge_development_validation,
        write_manifest,
    )
    from .materialize_crate_validation import (
        CrateLimits,
        CrateMember,
        CrateSource,
        materialize_crate,
    )


MEBIBYTE = 1024 * 1024
COPY_CHUNK_BYTES = MEBIBYTE
DOWNLOAD_CAP_BYTES = 100 * MEBIBYTE
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MIXED_SCHEMA = "mathzip-generated-mixed-v1"
GENERATED_SEED = 1_297_748_005


@dataclass(frozen=True, slots=True)
class DirectSource:
    """One exact upstream file copied byte-for-byte into the corpus."""

    cache_name: str
    dataset_id: str
    split: str
    split_group: str
    origin: str
    primary: bool
    domain: str
    source_url: str
    version: str
    license: str
    sha256: str
    bytes: int
    output_relative: str
    content_profile: str | None = None


@dataclass(frozen=True, slots=True)
class ZipMember:
    """One selected regular file from a fully pinned ZIP catalogue."""

    name: str
    dataset_id: str
    domain: str
    sha256: str
    bytes: int
    compressed_bytes: int
    output_relative: str


@dataclass(frozen=True, slots=True)
class ZipSource:
    """A pinned ZIP plus exact catalogue invariants and selected members."""

    cache_name: str
    split: str
    split_group: str
    origin: str
    primary: bool
    source_url: str
    version: str
    license: str
    archive_sha256: str
    archive_bytes: int
    catalogue_entries: int
    catalogue_files: int
    catalogue_uncompressed_bytes: int
    catalogue_compressed_bytes: int
    members: tuple[ZipMember, ...]


@dataclass(frozen=True, slots=True)
class GzipSource:
    """One pinned gzip stream materialized as its exact uncompressed payload."""

    cache_name: str
    dataset_id: str
    split: str
    split_group: str
    origin: str
    primary: bool
    domain: str
    source_url: str
    version: str
    license: str
    archive_sha256: str
    archive_bytes: int
    output_sha256: str
    output_bytes: int
    output_relative: str
    content_profile: str | None = None


@dataclass(frozen=True, slots=True)
class LocalControl:
    """A generated mixed-corpus artifact selected as a non-primary control."""

    manifest_relative: str
    dataset_id: str
    domain: str


DIRECT_SOURCES = (
    DirectSource(
        cache_name="usgs-ofr-2006-1216-digital-elevation.tif",
        dataset_id="development-usgs-ofr-2006-1216-geotiff",
        split="development",
        split_group="usgs-ofr-2006-1216",
        origin="real",
        primary=True,
        domain="real-raster-gis-uncompressed-geotiff",
        source_url=(
            "https://pubs.usgs.gov/of/2006/1216/downloads/IMAGES/"
            "GEOTIFF/digital_elevation.tif"
        ),
        version="USGS Open-File Report 2006-1216, version 1.0",
        license="U.S. Government Work (public domain)",
        sha256=(
            "d3411ad7d005b32c3850c63d8a8aa87111620a373799df261c383d482b4707dd"
        ),
        bytes=1_938_305,
        output_relative="usgs-ofr-2006-1216/digital_elevation.tif",
    ),
    DirectSource(
        cache_name="usgs-fs-2010-3086.pdf",
        dataset_id="development-usgs-fs-2010-3086-pdf",
        split="development",
        split_group="usgs-fs-2010-3086",
        origin="real",
        primary=True,
        domain="real-world-pdf-report",
        source_url="https://pubs.usgs.gov/fs/2010/3086/pdf/fs2010-3086.pdf",
        version=(
            "USGS Fact Sheet 2010-3086; Pennsylvania StreamStats: A Water-"
            "Resources Web Application; PDF 1.5, 2 pages"
        ),
        license="U.S. Government Work (public domain)",
        sha256=(
            "2542a8a5b4394db449db5d1baa3d289e14da40070c9a51bd4df10431d6dae317"
        ),
        bytes=452_576,
        output_relative="usgs-fs-2010-3086/fs2010-3086.pdf",
        content_profile="pdf-1.5-usgs-fact-sheet",
    ),
    DirectSource(
        cache_name="geoserver-2.28.2-natural-earth.gpkg",
        dataset_id="validation-geoserver-2-28-2-natural-earth-gpkg",
        split="validation",
        split_group="geoserver-2-28-2-natural-earth",
        origin="real",
        primary=True,
        domain="real-world-sqlite-ogc-geopackage-natural-earth",
        source_url=(
            "https://raw.githubusercontent.com/geoserver/geoserver/"
            "2abcae00385ff91d2fdcefe13b112ad91d0d3804/build/cite/"
            "ogcapi-features10/release/data/ne/natural_earth.gpkg"
        ),
        version=(
            "GeoServer 2.28.2; commit "
            "2abcae00385ff91d2fdcefe13b112ad91d0d3804; Natural Earth "
            "OGC GeoPackage sample"
        ),
        license=(
            "Natural Earth data: public domain; GeoServer packaging/code: "
            "GPL-2.0-or-later"
        ),
        sha256=(
            "141750d37ba1d561eab77700e5d9da197f768ac296bf4b2bc01070071c286433"
        ),
        bytes=4_288_512,
        output_relative="geoserver-2.28.2/natural_earth.gpkg",
        content_profile="geopackage-natural-earth-2.28.2",
    ),
    DirectSource(
        cache_name="noaa-globaltemp-v5.1.0.nc",
        dataset_id="development-noaa-globaltemp-v5-1-0-netcdf",
        split="development",
        split_group="noaa-globaltemp-v5-1-0",
        origin="real",
        primary=True,
        domain="real-climate-gridded-float-array-netcdf",
        source_url=(
            "https://www.ncei.noaa.gov/data/"
            "noaa-global-surface-temperature/v5.1/access/gridded/"
            "NOAAGlobalTemp_v5.1.0_gridded_s185001_e202312_"
            "c20240108T150239.nc"
        ),
        version=(
            "NOAAGlobalTemp v5.1.0; DOI 10.25921/2tj4-0e21; "
            "file creation 2024-01-08T15:02:39"
        ),
        license="U.S. Government Work (NOAA public data)",
        sha256=(
            "fd14db28556b2d054774eebb02af4daf2da45d864ed5f293b2979209b0dffd59"
        ),
        bytes=21_687_376,
        output_relative=(
            "noaa-globaltemp-v5.1.0/"
            "NOAAGlobalTemp_v5.1.0_gridded_s185001_e202312_"
            "c20240108T150239.nc"
        ),
    ),
    DirectSource(
        cache_name="linux-v6.10-kernel-sched-core.c",
        dataset_id="validation-linux-v6-10-kernel-sched-core-c",
        split="validation",
        split_group="linux-v6-10",
        origin="real",
        primary=True,
        domain="real-world-linux-source",
        source_url=(
            "https://raw.githubusercontent.com/torvalds/linux/"
            "0c3836482481200ead7b416ca80c68a29cfdaabd/kernel/sched/core.c"
        ),
        version=(
            "Linux v6.10; commit "
            "0c3836482481200ead7b416ca80c68a29cfdaabd"
        ),
        license="GPL-2.0-only",
        sha256=(
            "d9f511f3ea2902bf1f5e583e12b5e988e80f012f268bf79be4f9f88357fd81fa"
        ),
        bytes=315_511,
        output_relative="linux-v6.10/kernel/sched/core.c",
    ),
    DirectSource(
        cache_name="llvm-18.1.8-Function.cpp",
        dataset_id="validation-llvm-18-1-8-function-cpp",
        split="validation",
        split_group="llvm-18-1-8",
        origin="real",
        primary=True,
        domain="real-world-llvm-source",
        source_url=(
            "https://raw.githubusercontent.com/llvm/llvm-project/"
            "3b5b5c1ec4a3095ab096dd780e84d7ab81f3d7ff/"
            "llvm/lib/IR/Function.cpp"
        ),
        version=(
            "llvmorg-18.1.8; commit "
            "3b5b5c1ec4a3095ab096dd780e84d7ab81f3d7ff"
        ),
        license="Apache-2.0 WITH LLVM-exception",
        sha256=(
            "60d7d14f5fb19b05919b5fa5e2f9f8160c2f5180d593459d603f9432b908bcfc"
        ),
        bytes=72_100,
        output_relative="llvm-18.1.8/llvm/lib/IR/Function.cpp",
    ),
    DirectSource(
        cache_name="docker-hello-world-amd64-layer.tar.gz",
        dataset_id="validation-docker-official-hello-world-amd64-layer",
        split="validation",
        split_group="docker-official-hello-world-c3cbe1cc-amd64",
        origin="real",
        primary=True,
        domain="real-world-oci-container-layer-gzip",
        source_url=(
            "https://registry-1.docker.io/v2/library/hello-world/blobs/"
            "sha256:4f55086f7dd096d48b0e49be066971a8ed996521c2e190aa21b2435a847198b4"
        ),
        version=(
            "Docker Official Image hello-world; OCI index "
            "sha256:c3cbe1cc1aa588a64951ac6286e0df7b27fe2e6324b1001c619bb358770c0178; "
            "linux/amd64 manifest "
            "sha256:d1a8d0a4eeb63aff09f5f34d4d80505e0ba81905f36158cc3970d8e07179e59e; "
            "layer digest equals file SHA-256"
        ),
        license="MIT",
        sha256=(
            "4f55086f7dd096d48b0e49be066971a8ed996521c2e190aa21b2435a847198b4"
        ),
        bytes=2_415,
        output_relative=(
            "docker-official/hello-world/"
            "4f55086f7dd096d48b0e49be066971a8.layer.tar.gz"
        ),
    ),
    DirectSource(
        cache_name="nasa-fos-y19g0309t-c2f.fits",
        dataset_id="validation-nasa-hst-fos-y19g0309t-fits",
        split="validation",
        split_group="nasa-hst-fos-y19g0309t",
        origin="real",
        primary=True,
        domain="real-astronomy-fits-float32-spectrum",
        source_url="https://fits.gsfc.nasa.gov/samples/FOSy19g0309t_c2f.fits",
        version=(
            "NASA FITS Support Office HST/FOS sample y19g0309t; 2x2064 "
            "primary spectrum containing flux and wavelength arrays plus "
            "a two-row table extension"
        ),
        license=(
            "Upstream rights retained; publicly distributed NASA/MAST sample"
        ),
        sha256=(
            "059710c4f988f6319e0146f932a68a0ec6ba09d82293b978a294da0c58281086"
        ),
        bytes=43_200,
        output_relative="nasa-fits/FOSy19g0309t_c2f.fits",
        content_profile="fits-hst-fos-2x2064-float32",
    ),
    DirectSource(
        cache_name="idc-cptac-sar-ct.dcm",
        dataset_id="validation-idc-cptac-sar-ct-dicom",
        split="validation",
        split_group="idc-cptac-sar-ct-instance",
        origin="real",
        primary=True,
        domain="real-dicom-ct-pixel-uncompressed-implicit-vr-little-endian",
        source_url=(
            "https://idc-open-data.s3.amazonaws.com/"
            "28dfda52-b261-4505-bb06-824d5a9b1f90/"
            "e740f507-0387-4d39-a09c-8527fac58c52.dcm"
        ),
        version=(
            "IDC index release 24.2.2, collection cptac_sar; "
            "DOI 10.7937/tcia.2019.9bt23r95; CT Image Storage "
            "1.2.840.10008.5.1.4.1.1.2; Transfer Syntax "
            "1.2.840.10008.1.2 (uncompressed)"
        ),
        license="CC BY 4.0",
        sha256=(
            "1470b19381a0b28d1937ab9a8b443db3b4c6df22b13d792adc9e0639cae88e62"
        ),
        bytes=133_552,
        output_relative=(
            "idc-cptac-sar/"
            "e740f507-0387-4d39-a09c-8527fac58c52.dcm"
        ),
        content_profile="dicom-ct-implicit-vr-little-endian",
    ),
    DirectSource(
        cache_name="nyc-yellow-trip-2020-04.parquet",
        dataset_id="validation-nyc-tlc-yellow-trip-2020-04-parquet",
        split="validation",
        split_group="nyc-tlc-yellow-trip-2020-04",
        origin="real",
        primary=True,
        domain="real-tabular-integer-float-columns-parquet",
        source_url=(
            "https://d37ci6vzurychx.cloudfront.net/trip-data/"
            "yellow_tripdata_2020-04.parquet"
        ),
        version="NYC TLC Yellow Taxi Trip Records, 2020-04",
        license="NYC Open Data Terms of Use",
        sha256=(
            "3104e1e5548ade630988a4a8b9ec76c51a057b88159f78676c7d344f48491450"
        ),
        bytes=4_442_620,
        output_relative="nyc-tlc/yellow_tripdata_2020-04.parquet",
    ),
    DirectSource(
        cache_name="fsdd-v1.0.9-0_george_0.wav",
        dataset_id="validation-fsdd-v1-0-9-0-george-0-wav",
        split="validation",
        split_group="fsdd-v1-0-9",
        origin="real",
        primary=True,
        domain="real-recorded-speech-pcm16-wav",
        source_url=(
            "https://raw.githubusercontent.com/"
            "Jakobovski/free-spoken-digit-dataset/"
            "2b2c7c40d93a401feccf428247dcd2317431fdd6/"
            "recordings/0_george_0.wav"
        ),
        version=(
            "Free Spoken Digit Dataset v1.0.9; commit "
            "2b2c7c40d93a401feccf428247dcd2317431fdd6"
        ),
        license="CC BY-SA 4.0",
        sha256=(
            "228ab63fccdf262d2e05817b6ec918b15e7d9e4bfb6bb20183c46ae088405240"
        ),
        bytes=4_812,
        output_relative="fsdd-v1.0.9/recordings/0_george_0.wav",
    ),
    DirectSource(
        cache_name="earthscope-iu-anmo-20100227.mseed",
        dataset_id="validation-earthscope-iu-anmo-bhz-20100227-mseed",
        split="validation",
        split_group="earthscope-iu-anmo-2010-02-27",
        origin="real",
        primary=True,
        domain="real-seismic-fixed-record-miniseed",
        source_url=(
            "https://service.earthscope.org/fdsnws/dataselect/1/query?"
            "net=IU&sta=ANMO&loc=00&cha=BHZ&"
            "starttime=2010-02-27T06:30:00&"
            "endtime=2010-02-27T06:31:00&format=miniseed"
        ),
        version=(
            "FDSN dataselect service v1; IU.ANMO.00.BHZ; "
            "2010-02-27T06:30:00Z/60s"
        ),
        license="CC BY 4.0 (EarthScope Data)",
        sha256=(
            "ae1457bb8db83cdec15858f370217ea15cc651da267bd66c3d019a42ab06e23a"
        ),
        bytes=2_048,
        output_relative=(
            "earthscope/IU.ANMO.00.BHZ_20100227T063000_60s.mseed"
        ),
    ),
    DirectSource(
        cache_name="zenodo-15978325-sem-tem.bmp",
        dataset_id="validation-zenodo-15978325-sem-tem-bmp",
        split="validation",
        split_group="zenodo-15978325-sem-tem",
        origin="real",
        primary=True,
        domain="real-sem-tem-rgb24-bmp",
        source_url=(
            "https://zenodo.org/api/records/15978325/files/"
            "8a_Live%20Live%20Spim_HADF_ADF_Analog.bmp/content"
        ),
        version=(
            "Zenodo record 15978325; DOI 10.5281/zenodo.15978325; "
            "SEM/TEM image 8a; 132x132 RGB24 BMP"
        ),
        license="CC BY 4.0",
        sha256=(
            "7a7cc8f6a12aba4d3a55da02dbb685374fe86ee76ce69779b6d7fe72922d1826"
        ),
        bytes=52_326,
        output_relative="zenodo-15978325/8a-sem-tem.bmp",
        content_profile="bmp-rgb24-132x132",
    ),
    DirectSource(
        cache_name="gnulib-20250729.bundle",
        dataset_id="validation-gnu-gnulib-20250729-git-bundle",
        split="validation",
        split_group="gnu-gnulib-20250729",
        origin="real",
        primary=True,
        domain="real-world-git-complete-history-bundle",
        source_url="https://ftp.gnu.org/gnu/gnulib/gnulib-20250729.bundle",
        version=(
            "GNU gnulib bundle 2025-07-29; detached GNU OpenPGP signature "
            "verified for Simon Josefsson fingerprint "
            "A3CC9C870B9D310ABAD4CF2F51722B08FE4745A2; complete SHA-1 "
            "history, 336 refs"
        ),
        license="Mixed/per-file GNU project licenses; see bundled history",
        sha256=(
            "f01e423a7ef6b48e947fabd24bb11744204f4549342416e15dc64f427caa32e2"
        ),
        bytes=64_282_180,
        output_relative="gnu/gnulib-20250729.bundle",
        content_profile="git-bundle-gnulib-20250729",
    ),
    DirectSource(
        cache_name="libavif-v1.2.1-paris.avif",
        dataset_id="validation-control-libavif-v1-2-1-avif",
        split="validation",
        split_group="libavif-v1-2-1-test-data",
        origin="control",
        primary=False,
        domain="incompressible-control-avif",
        source_url=(
            "https://raw.githubusercontent.com/AOMediaCodec/libavif/"
            "fcb084c9387e367750f5375e462005ce298f57cc/"
            "tests/data/paris_icc_exif_xmp.avif"
        ),
        version=(
            "libavif v1.2.1; commit "
            "fcb084c9387e367750f5375e462005ce298f57cc"
        ),
        license="BSD-2-Clause",
        sha256=(
            "961bc38b61e60b7651fa20efa24269ae2f35e4958822a81c908c9bbf9b3f66e1"
        ),
        bytes=21_132,
        output_relative="controls/libavif-v1.2.1/paris_icc_exif_xmp.avif",
    ),
    DirectSource(
        cache_name="flac-test-aa7b0c6-mono-audio.flac",
        dataset_id="validation-control-ietf-flac-mono-audio",
        split="validation",
        split_group="ietf-cellar-flac-test-aa7b0c6",
        origin="control",
        primary=False,
        domain="incompressible-control-flac",
        source_url=(
            "https://raw.githubusercontent.com/ietf-wg-cellar/"
            "flac-test-files/"
            "aa7b0c6cf32994c106ae517a08134c28a96ff5b2/"
            "subset/60%20-%20mono%20audio.flac"
        ),
        version=(
            "IETF CELLAR flac-test-files commit "
            "aa7b0c6cf32994c106ae517a08134c28a96ff5b2"
        ),
        license="CC0-1.0",
        sha256=(
            "20539fbbd6ae28cea2b2182a4c60c6c70cbb710a136199fc83200c4e5fa00b1c"
        ),
        bytes=47_782,
        output_relative="controls/flac-test/60-mono-audio.flac",
    ),
    DirectSource(
        cache_name="minimp3-ea99364-ILL2_layer3.bit",
        dataset_id="validation-control-minimp3-ill2-layer3",
        split="validation",
        split_group="minimp3-conformance-ea99364",
        origin="control",
        primary=False,
        domain="incompressible-control-mp3",
        source_url=(
            "https://raw.githubusercontent.com/lieff/minimp3/"
            "ea99364f61c14656440e8d77e9c233ccf3124633/"
            "vectors/ILL2_layer3.bit"
        ),
        version=(
            "minimp3 MPEG Layer III conformance vector; commit "
            "ea99364f61c14656440e8d77e9c233ccf3124633"
        ),
        license="CC0-1.0",
        sha256=(
            "5af6ab144de30aa837b63608c850611b0b4ae40a640404130be324805f0f0337"
        ),
        bytes=9_600,
        output_relative="controls/minimp3/ILL2_layer3.bit",
    ),
)


WIKIMEDIA_SQL_DUMP = GzipSource(
    cache_name="enwiki-20260701-protected_titles.sql.gz",
    dataset_id="development-wikimedia-enwiki-20260701-protected-titles-sql",
    split="development",
    split_group="wikimedia-enwiki-20260701-protected-titles",
    origin="real",
    primary=True,
    domain="real-world-mariadb-database-dump-sql",
    source_url=(
        "https://dumps.wikimedia.org/enwiki/20260701/"
        "enwiki-20260701-protected_titles.sql.gz"
    ),
    version=(
        "English Wikipedia database dump 2026-07-01; protected_titles table; "
        "MariaDB dump completed 2026-07-07T17:40:54Z"
    ),
    license=(
        "CC BY-SA 4.0 / GFDL as applicable; see Wikimedia Foundation "
        "licensing terms"
    ),
    archive_sha256=(
        "4cc571bd1665dc64ad38b9ea9b2fa778b7eaf00a604ca7578b1afb9eff2a810a"
    ),
    archive_bytes=1_136_926,
    output_sha256=(
        "660ee2daf1e2ab9a435b484d924b0bb892f461682cf598d77becfef2a28ea364"
    ),
    output_bytes=4_455_489,
    output_relative="wikimedia/enwiki-20260701-protected_titles.sql",
    content_profile="wikimedia-protected-titles-mariadb-sql",
)


UAV_TELEMETRY = ZipSource(
    cache_name="zenodo-15912415.zip",
    split="development",
    split_group="zenodo-15912415-uav-telemetry",
    origin="real",
    primary=True,
    source_url=(
        "https://zenodo.org/api/records/15912415/files/"
        "telemetry_UAV_plan.zip/content"
    ),
    version=(
        "Zenodo record 15912415; DOI 10.5281/zenodo.15912415; "
        "published 2025-07-15"
    ),
    license="CC BY 4.0",
    archive_sha256=(
        "1949faaaec8c0b521f506f60442d9a2cced5d626dabecb8135ea950dc2aaff17"
    ),
    archive_bytes=451_314,
    catalogue_entries=788,
    catalogue_files=785,
    catalogue_uncompressed_bytes=774_677,
    catalogue_compressed_bytes=246_604,
    members=(
        ZipMember(
            name=(
                "UAV_telemetry_dataset/flightplan_drone1/"
                "message_1752507115773.json"
            ),
            dataset_id="development-zenodo-uav-drone1-message-1752507115773",
            domain="real-uav-flight-telemetry-json",
            sha256=(
                "b15aa63a0618532cc18c271c759185ddaba330284c10d96f1a8dc6ab2ed7b097"
            ),
            bytes=439,
            compressed_bytes=232,
            output_relative=(
                "zenodo-15912415/flightplan_drone1/"
                "message_1752507115773.json"
            ),
        ),
        ZipMember(
            name=(
                "UAV_telemetry_dataset/flightplan_drone1/"
                "message_1752507288092.json"
            ),
            dataset_id="development-zenodo-uav-drone1-message-1752507288092",
            domain="real-uav-flight-telemetry-json",
            sha256=(
                "e34464977a5b2f056e9927939824b3cf7d3cfb1c0c9af4967a21cb96ee979abb"
            ),
            bytes=922,
            compressed_bytes=314,
            output_relative=(
                "zenodo-15912415/flightplan_drone1/"
                "message_1752507288092.json"
            ),
        ),
        ZipMember(
            name=(
                "UAV_telemetry_dataset/flightplan_drone2/"
                "message_1752507693033.json"
            ),
            dataset_id="development-zenodo-uav-drone2-message-1752507693033",
            domain="real-uav-flight-telemetry-json",
            sha256=(
                "38f939f81b0a10ccaff1729ee384b0b61e9e998e8a70a7a87c9d40012c43e4d3"
            ),
            bytes=1_121,
            compressed_bytes=328,
            output_relative=(
                "zenodo-15912415/flightplan_drone2/"
                "message_1752507693033.json"
            ),
        ),
        ZipMember(
            name=(
                "UAV_telemetry_dataset/flightplan_drone2/"
                "message_1752507889384.json"
            ),
            dataset_id="development-zenodo-uav-drone2-message-1752507889384",
            domain="real-uav-flight-telemetry-json",
            sha256=(
                "68e41ba218faf5f47e402801a277d2ecd190f5b0d4b0ab9689278047a1c8eeff"
            ),
            bytes=1_099,
            compressed_bytes=324,
            output_relative=(
                "zenodo-15912415/flightplan_drone2/"
                "message_1752507889384.json"
            ),
        ),
        ZipMember(
            name=(
                "UAV_telemetry_dataset/flightplan_drone2/"
                "message_1752508085725.json"
            ),
            dataset_id="development-zenodo-uav-drone2-message-1752508085725",
            domain="real-uav-flight-telemetry-json",
            sha256=(
                "f46f51858e66110443b5ae43f849bb2a7bdc1e919773e734090296c4cfb13618"
            ),
            bytes=1_087,
            compressed_bytes=312,
            output_relative=(
                "zenodo-15912415/flightplan_drone2/"
                "message_1752508085725.json"
            ),
        ),
    ),
)


STANFORD_BUNNY = CrateSource(
    dataset_prefix="development-stanford-bunny-1994",
    split_group="stanford-3d-scanrep-bunny-1994",
    domain="real-laser-range-scan-ply",
    source_url=(
        "https://graphics.stanford.edu/pub/3Dscanrep/bunny.tar.gz"
    ),
    license=(
        "Stanford 3D Scanning Repository research/non-commercial terms; "
        "attribution required"
    ),
    archive_sha256=(
        "a5720bd96d158df403d153381b8411a727a1d73cff2f33dc9b212d6f75455b84"
    ),
    archive_bytes=4_894_286,
    archive_root="bunny",
    catalogue_entries=21,
    catalogue_file_bytes=22_233_698,
    members=(
        CrateMember(
            name="bunny/data/bun000.ply",
            bytes=1_988_159,
            sha256=(
                "7d48f9fdf917311de680d074edce8aff25a4b9bfd87be9301822dace811209fb"
            ),
        ),
    ),
)

STANFORD_BUNNY_VERSION = (
    "Stanford Bunny raw Cyberware 3030 MS range scan; scanned 1994; "
    "archive modified 1998-12-05"
)


LOCAL_CONTROLS = (
    LocalControl(
        "files/sample.zst",
        "development-control-generated-zstd",
        "incompressible-control-zstd",
    ),
    LocalControl(
        "files/sample.png",
        "development-control-generated-png",
        "incompressible-control-png",
    ),
    LocalControl(
        "files/sample.jpg",
        "development-control-generated-jpeg",
        "incompressible-control-jpeg",
    ),
    LocalControl(
        "files/sample.mp4",
        "development-control-generated-mp4",
        "incompressible-control-mp4-av1",
    ),
)

RETIRED_RESEARCH_ROWS = {
    "validation-idc-lidc-idri-0001-qiicr-sr-dicom": (
        "validation",
        "974860e871549d6d57635f45a99d02dc2e3e162d0193e87b9bce2477910a7f63",
    ),
    "development-usgs-ofr-2006-1216-pdf": (
        "development",
        "1c352f5203ec2e097d0882051e2b59e8ba256a44156a982cdd57cf405dc22a08",
    ),
    "validation-nasa-hst-wfpc2-u5780205r-fits": (
        "validation",
        "da7c0f1b6643850856cba100e9b3e8db76b80e91583eb088635c416a2b4161b3",
    ),
}


def _safe_relative(value: str, label: str) -> pathlib.PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    if value.startswith("/") or value.endswith("/"):
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    result = pathlib.PurePosixPath(value)
    if result.is_absolute() or ".." in result.parts:
        raise MaterializationError(f"{label} is not a safe POSIX relative path")
    return result


def _regular_file(path: pathlib.Path, label: str) -> os.stat_result:
    try:
        result = path.lstat()
    except FileNotFoundError as exc:
        raise MaterializationError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(result.st_mode):
        raise MaterializationError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(result.st_mode):
        raise MaterializationError(f"{label} must be a regular file: {path}")
    return result


def _sha256_stream(handle: BinaryIO, limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while chunk := handle.read(COPY_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise MaterializationError(
                f"stream exceeds declared {limit}-byte limit"
            )
        digest.update(chunk)
    return digest.hexdigest(), total


def _verify_file(
    path: pathlib.Path,
    expected_bytes: int,
    expected_sha256: str,
    label: str,
) -> None:
    if expected_bytes < 0 or not SHA256_RE.fullmatch(expected_sha256):
        raise MaterializationError(f"{label} has an invalid pinned declaration")
    result = _regular_file(path, label)
    if result.st_size != expected_bytes:
        raise MaterializationError(
            f"{label} byte count mismatch: expected {expected_bytes}, "
            f"got {result.st_size}"
        )
    with path.open("rb") as handle:
        digest, size = _sha256_stream(handle, expected_bytes)
    if size != expected_bytes or digest != expected_sha256:
        raise MaterializationError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, got {digest}"
        )


def _dicom_file_meta(path: pathlib.Path) -> dict[tuple[int, int], str]:
    """Parse the mandatory Explicit-VR little-endian DICOM file-meta header."""

    with path.open("rb") as handle:
        payload = handle.read(64 * 1024)
    if len(payload) < 132 or payload[128:132] != b"DICM":
        raise MaterializationError("DICOM payload is missing the DICM preamble")
    offset = 132
    values: dict[tuple[int, int], str] = {}
    long_vr = {
        b"OB",
        b"OD",
        b"OF",
        b"OL",
        b"OW",
        b"SQ",
        b"UC",
        b"UR",
        b"UT",
        b"UN",
    }
    while offset + 8 <= len(payload):
        group = int.from_bytes(payload[offset : offset + 2], "little")
        element = int.from_bytes(payload[offset + 2 : offset + 4], "little")
        if group != 0x0002:
            break
        vr = payload[offset + 4 : offset + 6]
        if vr in long_vr:
            if offset + 12 > len(payload):
                raise MaterializationError("truncated DICOM file-meta element")
            length = int.from_bytes(payload[offset + 8 : offset + 12], "little")
            value_offset = offset + 12
        else:
            length = int.from_bytes(payload[offset + 6 : offset + 8], "little")
            value_offset = offset + 8
        end = value_offset + length
        if end > len(payload):
            raise MaterializationError("truncated DICOM file-meta value")
        if vr == b"UI":
            try:
                values[(group, element)] = (
                    payload[value_offset:end].rstrip(b"\x00 ").decode("ascii")
                )
            except UnicodeDecodeError as exc:
                raise MaterializationError(
                    "DICOM UID is not ASCII"
                ) from exc
        offset = end
    return values


def _validate_geopackage(path: pathlib.Path) -> None:
    with path.open("rb") as handle:
        header = handle.read(100)
    if len(header) < 72 or header[:16] != b"SQLite format 3\x00":
        raise MaterializationError("GeoPackage is not a SQLite 3 database")
    if int.from_bytes(header[68:72], "big") != 0x47504B47:
        raise MaterializationError("SQLite application_id is not GPKG")

    expected = {
        "boundary_lines_land": 390,
        "coastlines": 1_428,
        "countries": 242,
        "disputed_areas": 28,
        "populated_places": 1_251,
    }
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            if integrity != [("ok",)]:
                raise MaterializationError(
                    f"GeoPackage integrity_check failed: {integrity!r}"
                )
            contents = connection.execute(
                "SELECT table_name, data_type FROM gpkg_contents "
                "ORDER BY table_name"
            ).fetchall()
            if contents != [(name, "features") for name in sorted(expected)]:
                raise MaterializationError(
                    f"unexpected GeoPackage contents: {contents!r}"
                )
            for table, expected_rows in expected.items():
                observed = connection.execute(
                    f'SELECT count(*) FROM "{table}"'
                ).fetchone()
                if observed != (expected_rows,):
                    raise MaterializationError(
                        f"GeoPackage row count mismatch for {table}: "
                        f"expected {expected_rows}, got {observed!r}"
                    )
    except sqlite3.Error as exc:
        raise MaterializationError(f"invalid GeoPackage database: {exc}") from exc


def _validate_bmp(path: pathlib.Path) -> None:
    with path.open("rb") as handle:
        header = handle.read(54)
    if len(header) != 54 or header[:2] != b"BM":
        raise MaterializationError("BMP payload has no BITMAPFILEHEADER")
    fields = {
        "file_size": int.from_bytes(header[2:6], "little"),
        "pixel_offset": int.from_bytes(header[10:14], "little"),
        "dib_size": int.from_bytes(header[14:18], "little"),
        "width": int.from_bytes(header[18:22], "little", signed=True),
        "height": int.from_bytes(header[22:26], "little", signed=True),
        "planes": int.from_bytes(header[26:28], "little"),
        "bits_per_pixel": int.from_bytes(header[28:30], "little"),
        "compression": int.from_bytes(header[30:34], "little"),
        "image_size": int.from_bytes(header[34:38], "little"),
    }
    expected = {
        "file_size": 52_326,
        "pixel_offset": 54,
        "dib_size": 40,
        "width": 132,
        "height": 132,
        "planes": 1,
        "bits_per_pixel": 24,
        "compression": 0,
        "image_size": 52_272,
    }
    if fields != expected:
        raise MaterializationError(
            f"unexpected 132x132 RGB24 BMP header: {fields!r}"
        )


def _validate_git_bundle(path: pathlib.Path) -> None:
    with path.open("rb") as handle:
        prefix = handle.read(128 * 1024)
    separator = prefix.find(b"\n\n")
    if separator < 0 or prefix[:16] != b"# v2 git bundle\n":
        raise MaterializationError("invalid Git bundle v2 header")
    if prefix[separator + 2 : separator + 6] != b"PACK":
        raise MaterializationError("Git bundle header is not followed by PACK")

    refs: dict[str, str] = {}
    prerequisites = 0
    try:
        lines = prefix[:separator].decode("ascii").splitlines()[1:]
    except UnicodeDecodeError as exc:
        raise MaterializationError("Git bundle header is not ASCII") from exc
    for line in lines:
        if line.startswith("-"):
            prerequisites += 1
            continue
        parts = line.split(" ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{40}", parts[0]):
            raise MaterializationError(f"invalid Git bundle ref line: {line!r}")
        if parts[1] in refs:
            raise MaterializationError(f"duplicate Git bundle ref: {parts[1]!r}")
        refs[parts[1]] = parts[0]
    expected_head = "e8cc0791e6bb0814cf4e88395c06d5e06655d8b5"
    if prerequisites != 0:
        raise MaterializationError("Git bundle is incremental, not complete")
    if len(refs) != 336:
        raise MaterializationError(
            f"Git bundle ref count mismatch: expected 336, got {len(refs)}"
        )
    expected_refs = {
        "HEAD": expected_head,
        "refs/heads/stable-202507": expected_head,
        "refs/heads/master": "225973a89f50c2b494ad947399425182dd42618c",
    }
    for name, expected_oid in expected_refs.items():
        if refs.get(name) != expected_oid:
            raise MaterializationError(
                f"Git bundle ref mismatch for {name}: {refs.get(name)!r}"
            )


def _fits_values(block: bytes) -> dict[str, str]:
    if not block or len(block) % 2_880 != 0:
        raise MaterializationError("truncated FITS header block")
    result: dict[str, str] = {}
    found_end = False
    for offset in range(0, len(block), 80):
        card = block[offset : offset + 80]
        key = card[:8].decode("ascii", errors="strict").strip()
        if key == "END":
            found_end = True
            break
        if card[8:10] != b"= ":
            continue
        raw = card[10:80].split(b"/", 1)[0].strip()
        if raw.startswith(b"'") and raw.endswith(b"'"):
            raw = raw[1:-1].strip()
        result[key] = raw.decode("ascii")
    if not found_end:
        raise MaterializationError("FITS header has no END card")
    return result


def _validate_fits(path: pathlib.Path) -> None:
    with path.open("rb") as handle:
        primary = handle.read(14_400)
        handle.seek(31_680)
        extension = handle.read(8_640)
    primary_values = _fits_values(primary)
    expected_primary = {
        "SIMPLE": "T",
        "BITPIX": "-32",
        "NAXIS": "2",
        "NAXIS1": "2064",
        "NAXIS2": "2",
    }
    if any(primary_values.get(key) != value for key, value in expected_primary.items()):
        raise MaterializationError(
            f"unexpected HST/FOS primary FITS layout: {primary_values!r}"
        )
    extension_values = _fits_values(extension)
    expected_extension = {
        "XTENSION": "TABLE",
        "BITPIX": "8",
        "NAXIS": "2",
        "NAXIS1": "336",
        "NAXIS2": "2",
        "PCOUNT": "0",
        "GCOUNT": "1",
    }
    if any(
        extension_values.get(key) != value
        for key, value in expected_extension.items()
    ):
        raise MaterializationError(
            f"unexpected HST/FOS FITS table layout: {extension_values!r}"
        )


def _validate_sql_dump(path: pathlib.Path) -> None:
    with path.open("rb") as handle:
        payload = handle.read(4_455_490)
    required = (
        b"-- MariaDB dump 10.19",
        b"CREATE TABLE `protected_titles`",
        b"-- Dump completed on 2026-07-07 17:40:54",
    )
    if len(payload) != 4_455_489 or any(value not in payload for value in required):
        raise MaterializationError("unexpected Wikimedia protected_titles SQL dump")
    if payload.count(b"INSERT INTO `protected_titles` VALUES") != 5:
        raise MaterializationError(
            "Wikimedia protected_titles dump must contain exactly five INSERTs"
        )


def _validate_content_profile(
    path: pathlib.Path, content_profile: str | None
) -> None:
    if content_profile is None:
        return
    if content_profile == "dicom-ct-implicit-vr-little-endian":
        values = _dicom_file_meta(path)
        sop_class = values.get((0x0002, 0x0002))
        transfer_syntax = values.get((0x0002, 0x0010))
        if sop_class != "1.2.840.10008.5.1.4.1.1.2":
            raise MaterializationError(
                f"expected CT Image Storage SOP Class, got {sop_class!r}"
            )
        if transfer_syntax != "1.2.840.10008.1.2":
            raise MaterializationError(
                "expected uncompressed Implicit VR Little Endian transfer "
                f"syntax, got {transfer_syntax!r}"
            )
        return
    if content_profile == "geopackage-natural-earth-2.28.2":
        _validate_geopackage(path)
        return
    if content_profile == "bmp-rgb24-132x132":
        _validate_bmp(path)
        return
    if content_profile == "git-bundle-gnulib-20250729":
        _validate_git_bundle(path)
        return
    if content_profile == "pdf-1.5-usgs-fact-sheet":
        with path.open("rb") as handle:
            prefix = handle.read(8)
            handle.seek(max(0, path.stat().st_size - 64))
            suffix = handle.read()
        if prefix != b"%PDF-1.5" or b"%%EOF" not in suffix:
            raise MaterializationError("unexpected USGS PDF 1.5 framing")
        return
    if content_profile == "fits-hst-fos-2x2064-float32":
        _validate_fits(path)
        return
    if content_profile == "wikimedia-protected-titles-mariadb-sql":
        _validate_sql_dump(path)
        return
    raise MaterializationError(
        f"unsupported content_profile: {content_profile!r}"
    )


def _ensure_directory(path: pathlib.Path) -> None:
    if path.exists() or path.is_symlink():
        result = path.lstat()
        if stat.S_ISLNK(result.st_mode):
            raise MaterializationError(
                f"destination directory must not be a symlink: {path}"
            )
        if not stat.S_ISDIR(result.st_mode):
            raise MaterializationError(
                f"destination parent is not a directory: {path}"
            )
        return
    if path.parent != path:
        _ensure_directory(path.parent)
    path.mkdir()


def _destination(root: pathlib.Path, split: str, relative: str) -> pathlib.Path:
    if split not in {"development", "validation"}:
        raise MaterializationError(f"unsupported materialized split: {split}")
    normalized = _safe_relative(relative, "output_relative")
    base = root / split
    _ensure_directory(base)
    current = base
    for part in normalized.parts[:-1]:
        current /= part
        _ensure_directory(current)
    result = current / normalized.name
    if result.is_symlink():
        raise MaterializationError(
            f"destination payload must not be a symlink: {result}"
        )
    return result


def _copy_verified(
    source: BinaryIO,
    destination: pathlib.Path,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    if destination.exists():
        _verify_file(
            destination, expected_bytes, expected_sha256, "existing payload"
        )
        return

    temporary: pathlib.Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = pathlib.Path(temporary_name)
        with os.fdopen(descriptor, "wb") as output:
            digest = hashlib.sha256()
            total = 0
            while chunk := source.read(COPY_CHUNK_BYTES):
                total += len(chunk)
                if total > expected_bytes:
                    raise MaterializationError(
                        "source stream exceeds pinned payload byte count"
                    )
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total != expected_bytes:
            raise MaterializationError(
                f"copied payload byte count mismatch: expected "
                f"{expected_bytes}, got {total}"
            )
        if digest.hexdigest() != expected_sha256:
            raise MaterializationError(
                "copied payload SHA-256 mismatch: expected "
                f"{expected_sha256}, got {digest.hexdigest()}"
            )
        temporary.chmod(0o644)
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            _verify_file(
                destination,
                expected_bytes,
                expected_sha256,
                "concurrent payload",
            )
        temporary.unlink()
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    _verify_file(destination, expected_bytes, expected_sha256, "payload")


def _entry(
    repository: pathlib.Path,
    payload: pathlib.Path,
    *,
    dataset_id: str,
    split: str,
    split_group: str,
    origin: str,
    primary: bool,
    domain: str,
    source_url: str,
    version: str,
    license_text: str,
    sha256: str,
    bytes_value: int,
    legacy_observed: bool = False,
) -> DatasetEntry:
    try:
        relative = payload.resolve(strict=True).relative_to(repository.resolve())
    except ValueError as exc:
        raise MaterializationError(
            "materialized payload must remain inside the repository"
        ) from exc
    value = DatasetEntry(
        split=split,
        dataset_id=dataset_id,
        split_group=split_group,
        origin=origin,
        primary=primary,
        domain=domain,
        source=f"{source_url}; version={version}",
        license=license_text,
        sha256=sha256,
        bytes=bytes_value,
        sealed=False,
        path=relative.as_posix(),
        legacy_observed=legacy_observed,
    )
    value.validate(2)
    return value


def materialize_direct_source(
    source_path: pathlib.Path,
    output_root: pathlib.Path,
    repository: pathlib.Path,
    source: DirectSource,
) -> tuple[pathlib.Path, DatasetEntry]:
    """Verify and atomically copy one direct source without overwriting."""

    _safe_relative(source.cache_name, "cache_name")
    if source.primary != (source.origin == "real"):
        raise MaterializationError(
            f"{source.dataset_id}: invalid origin/primary declaration"
        )
    _verify_file(source_path, source.bytes, source.sha256, "source payload")
    _validate_content_profile(source_path, source.content_profile)
    destination = _destination(
        output_root, source.split, source.output_relative
    )
    with source_path.open("rb") as handle:
        _copy_verified(handle, destination, source.bytes, source.sha256)
    _validate_content_profile(destination, source.content_profile)
    return destination, _entry(
        repository,
        destination,
        dataset_id=source.dataset_id,
        split=source.split,
        split_group=source.split_group,
        origin=source.origin,
        primary=source.primary,
        domain=source.domain,
        source_url=source.source_url,
        version=source.version,
        license_text=source.license,
        sha256=source.sha256,
        bytes_value=source.bytes,
    )


def materialize_gzip_source(
    archive_path: pathlib.Path,
    output_root: pathlib.Path,
    repository: pathlib.Path,
    source: GzipSource,
) -> tuple[pathlib.Path, DatasetEntry]:
    """Validate one gzip archive and atomically emit its pinned raw stream."""

    _safe_relative(source.cache_name, "cache_name")
    if source.primary != (source.origin == "real"):
        raise MaterializationError(
            f"{source.dataset_id}: invalid gzip origin/primary declaration"
        )
    _verify_file(
        archive_path,
        source.archive_bytes,
        source.archive_sha256,
        "source gzip archive",
    )
    destination = _destination(
        output_root, source.split, source.output_relative
    )
    try:
        with gzip.open(archive_path, "rb") as payload:
            _copy_verified(
                payload,
                destination,
                source.output_bytes,
                source.output_sha256,
            )
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise MaterializationError(f"invalid gzip stream: {exc}") from exc
    _validate_content_profile(destination, source.content_profile)
    return destination, _entry(
        repository,
        destination,
        dataset_id=source.dataset_id,
        split=source.split,
        split_group=source.split_group,
        origin=source.origin,
        primary=source.primary,
        domain=source.domain,
        source_url=source.source_url,
        version=source.version,
        license_text=source.license,
        sha256=source.output_sha256,
        bytes_value=source.output_bytes,
    )


def _verify_zip_catalogue(
    archive: zipfile.ZipFile, source: ZipSource
) -> dict[str, zipfile.ZipInfo]:
    entries = archive.infolist()
    if len(entries) != source.catalogue_entries:
        raise MaterializationError(
            "archive catalogue entry count mismatch: expected "
            f"{source.catalogue_entries}, got {len(entries)}"
        )
    by_name: dict[str, zipfile.ZipInfo] = {}
    file_count = 0
    uncompressed = 0
    compressed = 0
    for info in entries:
        checked_name = info.filename[:-1] if info.is_dir() else info.filename
        _safe_relative(checked_name, "archive member")
        if info.filename in by_name:
            raise MaterializationError(
                f"archive contains duplicate member {info.filename!r}"
            )
        by_name[info.filename] = info
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if info.is_dir():
            if kind not in {0, stat.S_IFDIR}:
                raise MaterializationError(
                    f"archive directory has forbidden type: {info.filename!r}"
                )
            continue
        if kind == stat.S_IFLNK:
            raise MaterializationError(
                f"archive member must not be a symlink: {info.filename!r}"
            )
        if kind not in {0, stat.S_IFREG}:
            raise MaterializationError(
                f"archive member has forbidden type: {info.filename!r}"
            )
        if info.flag_bits & 0x1:
            raise MaterializationError(
                f"encrypted archive member is forbidden: {info.filename!r}"
            )
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise MaterializationError(
                f"unsupported ZIP method for {info.filename!r}"
            )
        file_count += 1
        uncompressed += info.file_size
        compressed += info.compress_size
        if uncompressed > source.catalogue_uncompressed_bytes:
            raise MaterializationError(
                "archive exceeds pinned uncompressed catalogue size"
            )

    observed = (
        file_count,
        uncompressed,
        compressed,
    )
    expected = (
        source.catalogue_files,
        source.catalogue_uncompressed_bytes,
        source.catalogue_compressed_bytes,
    )
    if observed != expected:
        raise MaterializationError(
            f"archive catalogue totals mismatch: expected {expected}, "
            f"got {observed}"
        )
    for member in source.members:
        _safe_relative(member.name, "selected archive member")
        info = by_name.get(member.name)
        if info is None or info.is_dir():
            raise MaterializationError(
                f"selected archive member is absent: {member.name!r}"
            )
        if (
            info.file_size != member.bytes
            or info.compress_size != member.compressed_bytes
        ):
            raise MaterializationError(
                f"selected archive member metadata drift: {member.name!r}"
            )
    return by_name


def materialize_zip_source(
    archive_path: pathlib.Path,
    output_root: pathlib.Path,
    repository: pathlib.Path,
    source: ZipSource,
) -> tuple[list[pathlib.Path], list[DatasetEntry]]:
    """Validate the entire ZIP catalogue and extract only pinned members."""

    _safe_relative(source.cache_name, "cache_name")
    if source.primary != (source.origin == "real"):
        raise MaterializationError("invalid ZIP source origin/primary declaration")
    _verify_file(
        archive_path,
        source.archive_bytes,
        source.archive_sha256,
        "source archive",
    )
    paths: list[pathlib.Path] = []
    rows: list[DatasetEntry] = []
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            by_name = _verify_zip_catalogue(archive, source)
            for member in source.members:
                destination = _destination(
                    output_root, source.split, member.output_relative
                )
                with archive.open(by_name[member.name], "r") as payload:
                    _copy_verified(
                        payload,
                        destination,
                        member.bytes,
                        member.sha256,
                    )
                paths.append(destination)
                rows.append(
                    _entry(
                        repository,
                        destination,
                        dataset_id=member.dataset_id,
                        split=source.split,
                        split_group=source.split_group,
                        origin=source.origin,
                        primary=source.primary,
                        domain=member.domain,
                        source_url=source.source_url,
                        version=source.version,
                        license_text=source.license,
                        sha256=member.sha256,
                        bytes_value=member.bytes,
                    )
                )
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise MaterializationError(f"invalid ZIP archive: {exc}") from exc
    return paths, rows


def local_control_entries(repository: pathlib.Path) -> list[DatasetEntry]:
    """Integrity-check generated controls and select them in development."""

    root = repository / "datasets/generated/mixed"
    manifest_path = root / "manifest.json"
    _regular_file(manifest_path, "generated mixed manifest")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != MIXED_SCHEMA:
        raise MaterializationError(
            f"{manifest_path}: expected schema_version={MIXED_SCHEMA!r}"
        )
    if value.get("seed") != GENERATED_SEED:
        raise MaterializationError(
            f"{manifest_path}: expected seed={GENERATED_SEED}"
        )
    generator = value.get("generator")
    license_text = value.get("license")
    catalogue = value.get("entries")
    if (
        not isinstance(generator, str)
        or not generator
        or not isinstance(license_text, str)
        or not license_text
        or not isinstance(catalogue, list)
    ):
        raise MaterializationError(
            f"{manifest_path}: generator/license/entries declaration invalid"
        )
    by_path: dict[str, dict[str, object]] = {}
    for index, item in enumerate(catalogue):
        if not isinstance(item, dict):
            raise MaterializationError(
                f"{manifest_path}: entries[{index}] must be an object"
            )
        raw_path = item.get("path")
        if not isinstance(raw_path, str):
            raise MaterializationError(
                f"{manifest_path}: entries[{index}].path must be a string"
            )
        normalized = _safe_relative(
            raw_path, f"{manifest_path}.entries[{index}].path"
        ).as_posix()
        if normalized in by_path:
            raise MaterializationError(
                f"{manifest_path}: duplicate path {normalized!r}"
            )
        by_path[normalized] = item

    result: list[DatasetEntry] = []
    for selection in LOCAL_CONTROLS:
        item = by_path.get(selection.manifest_relative)
        if item is None:
            raise MaterializationError(
                f"{manifest_path}: missing {selection.manifest_relative!r}"
            )
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if (
            not isinstance(size, int)
            or not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
        ):
            raise MaterializationError(
                f"{manifest_path}: invalid integrity declaration for "
                f"{selection.manifest_relative!r}"
            )
        relative = _safe_relative(
            selection.manifest_relative, "local control path"
        )
        payload = root.joinpath(*relative.parts)
        _verify_file(payload, size, digest, "local control")
        result.append(
            _entry(
                repository,
                payload,
                dataset_id=selection.dataset_id,
                split="development",
                split_group=f"legacy-generated-seed-{GENERATED_SEED}",
                origin="control",
                primary=False,
                domain=selection.domain,
                source_url=(
                    f"{generator}; "
                    "manifest=datasets/generated/mixed/manifest.json"
                ),
                version=f"seed={GENERATED_SEED}",
                license_text=license_text,
                sha256=digest,
                bytes_value=size,
                legacy_observed=True,
            )
        )
    return result


def upsert_split_entries(
    existing: Sequence[DatasetEntry],
    additions: Sequence[DatasetEntry],
    split: str,
) -> list[DatasetEntry]:
    """Merge one split idempotently and refuse changed-row replacement."""

    if any(entry.split != split for entry in [*existing, *additions]):
        raise MaterializationError(f"{split} merge contains another split")
    by_id = {entry.dataset_id: entry for entry in existing}
    if len(by_id) != len(existing):
        raise MaterializationError(f"existing {split} rows contain duplicates")
    for entry in additions:
        previous = by_id.get(entry.dataset_id)
        if previous is not None and previous != entry:
            raise MaterializationError(
                f"refusing to replace changed {split} row "
                f"{entry.dataset_id!r}"
            )
        by_id[entry.dataset_id] = entry
    merged = sorted(by_id.values(), key=lambda item: item.dataset_id)
    validate_collection(merged)
    return merged


def remove_retired_research_rows(
    entries: Sequence[DatasetEntry],
) -> tuple[list[DatasetEntry], int]:
    """Unselect only exact rows superseded by a stronger pinned source."""

    result: list[DatasetEntry] = []
    retired = 0
    for entry in entries:
        declaration = RETIRED_RESEARCH_ROWS.get(entry.dataset_id)
        if declaration is None:
            result.append(entry)
            continue
        expected_split, expected_sha256 = declaration
        if (
            entry.split != expected_split
            or entry.sha256 != expected_sha256
            or entry.origin != "real"
            or not entry.primary
        ):
            raise MaterializationError(
                f"refusing to retire changed row {entry.dataset_id!r}"
            )
        retired += 1
    validate_collection(result)
    return result, retired


def materialize_bundle(
    source_directory: pathlib.Path,
    output_root: pathlib.Path,
    repository: pathlib.Path,
) -> list[DatasetEntry]:
    """Materialize all pinned sources and return canonical manifest rows."""

    _ensure_directory(output_root)
    additions: list[DatasetEntry] = []
    for source in DIRECT_SOURCES:
        source_path = source_directory / source.cache_name
        _, entry = materialize_direct_source(
            source_path, output_root, repository, source
        )
        additions.append(entry)
    _, gzip_entry = materialize_gzip_source(
        source_directory / WIKIMEDIA_SQL_DUMP.cache_name,
        output_root,
        repository,
        WIKIMEDIA_SQL_DUMP,
    )
    additions.append(gzip_entry)
    _, zip_entries = materialize_zip_source(
        source_directory / UAV_TELEMETRY.cache_name,
        output_root,
        repository,
        UAV_TELEMETRY,
    )
    additions.extend(zip_entries)
    bunny_payloads = materialize_crate(
        source_directory / "stanford-bunny.tar.gz",
        output_root / "development",
        STANFORD_BUNNY,
        CrateLimits(
            max_entries=32,
            max_archive_bytes=6 * MEBIBYTE,
            max_uncompressed_bytes=24 * MEBIBYTE,
            max_selected_bytes=2 * MEBIBYTE,
        ),
    )
    bunny_member = STANFORD_BUNNY.members[0]
    additions.append(
        _entry(
            repository,
            bunny_payloads[0],
            dataset_id="development-stanford-bunny-1994-bun000-ply",
            split="development",
            split_group=STANFORD_BUNNY.split_group,
            origin="real",
            primary=True,
            domain=STANFORD_BUNNY.domain,
            source_url=STANFORD_BUNNY.source_url,
            version=STANFORD_BUNNY_VERSION,
            license_text=STANFORD_BUNNY.license,
            sha256=bunny_member.sha256,
            bytes_value=bunny_member.bytes,
        )
    )
    additions.extend(local_control_entries(repository))
    validate_collection(additions)
    return sorted(additions, key=lambda item: item.dataset_id)


def _under(repository: pathlib.Path, configured: pathlib.Path) -> pathlib.Path:
    candidate = configured if configured.is_absolute() else repository / configured
    normalized = pathlib.Path(os.path.abspath(candidate))
    try:
        normalized.relative_to(repository)
    except ValueError as exc:
        raise MaterializationError(
            f"output must remain inside repository: {configured}"
        ) from exc
    return normalized


def _atomic_json(path: pathlib.Path, value: object) -> None:
    _ensure_directory(path.parent)
    if path.is_symlink():
        raise MaterializationError(f"report must not be a symlink: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source_directory",
        type=pathlib.Path,
        help="directory containing every exact cache_name pinned by this module",
    )
    parser.add_argument(
        "--repository",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[3],
    )
    parser.add_argument(
        "--data-root",
        type=pathlib.Path,
        default=pathlib.Path("datasets/data/corpus-completion"),
    )
    parser.add_argument(
        "--development-manifest",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/manifests/development.csv"),
    )
    parser.add_argument(
        "--validation-manifest",
        type=pathlib.Path,
        default=pathlib.Path("mathsvg/results/manifests/validation.csv"),
    )
    parser.add_argument(
        "--report",
        type=pathlib.Path,
        default=pathlib.Path(
            "mathsvg/results/manifests/research-corpus-report.json"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    repository = args.repository.resolve()
    source_directory = args.source_directory.absolute()
    data_root = _under(repository, args.data_root)
    development_path = _under(repository, args.development_manifest)
    validation_path = _under(repository, args.validation_manifest)
    report_path = _under(repository, args.report)
    try:
        source_bytes = (
            sum(source.bytes for source in DIRECT_SOURCES)
            + WIKIMEDIA_SQL_DUMP.archive_bytes
            + UAV_TELEMETRY.archive_bytes
            + STANFORD_BUNNY.archive_bytes
        )
        if source_bytes > DOWNLOAD_CAP_BYTES:
            raise MaterializationError(
                f"pinned source bundle exceeds {DOWNLOAD_CAP_BYTES} bytes"
            )
        additions = materialize_bundle(
            source_directory, data_root, repository
        )
        development_existing, retired_development = (
            remove_retired_research_rows(load_manifest(development_path))
        )
        validation_existing, retired_validation = (
            remove_retired_research_rows(load_manifest(validation_path))
        )
        development_additions = [
            entry for entry in additions if entry.split == "development"
        ]
        validation_additions = [
            entry for entry in additions if entry.split == "validation"
        ]
        development = upsert_split_entries(
            development_existing, development_additions, "development"
        )
        validation = upsert_split_entries(
            validation_existing, validation_additions, "validation"
        )
        merge_value = merge_development_validation(development, validation)
        if not composition(development)["passes_70_percent_rule"]:
            raise MaterializationError(
                "development split violates the 70% primary-real rule"
            )
        if not composition(validation)["passes_70_percent_rule"]:
            raise MaterializationError(
                "validation split violates the 70% primary-real rule"
            )
        merged_composition = merge_value["composition"]
        if not merged_composition["passes_70_percent_rule"]:  # type: ignore[index]
            raise MaterializationError(
                "combined corpus violates the 70% primary-real rule"
            )
        write_manifest(development_path, development)
        write_manifest(validation_path, validation)
        value = {
            "schema_version": 1,
            "holdout_payload_inspected": False,
            "source_directory_recorded": False,
            "selected_source_download_bytes": source_bytes,
            "download_cap_bytes": DOWNLOAD_CAP_BYTES,
            "direct_sources": [asdict(source) for source in DIRECT_SOURCES],
            "gzip_sources": [asdict(WIKIMEDIA_SQL_DUMP)],
            "zip_sources": [asdict(UAV_TELEMETRY)],
            "tar_sources": [
                {
                    **asdict(STANFORD_BUNNY),
                    "version": STANFORD_BUNNY_VERSION,
                    "cache_name": "stanford-bunny.tar.gz",
                }
            ],
            "local_controls": [asdict(value) for value in LOCAL_CONTROLS],
            "added_rows": {
                "development": len(development_additions),
                "validation": len(validation_additions),
            },
            "retired_rows": {
                "development": retired_development,
                "validation": retired_validation,
            },
            "development": report(development),
            "validation": report(validation),
            "development_validation_merge": merge_value,
        }
        _atomic_json(report_path, value)
    except (
        json.JSONDecodeError,
        ManifestError,
        MaterializationError,
        OSError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"research corpus materialization error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
