# MathSVG dataset protocol

This package owns manifest validation and development/validation
materialization. It deliberately has no API that opens a sealed holdout
payload.

## Commands

| Command | Purpose | Payload work |
|---|---|---|
| `python3 -m mathsvg.python.datasets.coverage_matrix` | Produce the exact `YeuCau.md` §20 coverage/gap matrix | Hashes only small generated diagnostic artefacts |
| `python3 -m mathsvg.python.datasets.build_development_manifest` | Verify and select pinned development payloads | Rehashes roughly 1.36 GB |
| `python3 -m mathsvg.python.datasets.materialize_validation ARCHIVE.zip` | Verify the pinned UCI sensor CSV validation source | Reads one local ZIP |
| `python3 -m mathsvg.python.datasets.materialize_crate_validation SERDE.crate` | Verify the pinned serde Rust-source validation corpus | Reads one local `.crate`; no network |
| `python3 -m mathsvg.python.datasets.materialize_research_corpus SOURCE_DIR` | Verify and merge the pinned §20 mixed/structured/control bundle | Reads 37,720,672 bytes of exact local source files; no network or holdout |

Run the development builder only when no benchmark is using the corpus. The
materializers never download automatically: callers obtain the exact URL,
then pass the local archive through the pinned size/checksum/catalogue checks.

## Origin and balance rules

- Only `origin=real AND primary=true` contributes to the 70% requirement.
- `synthetic`, `control`, and `derived` rows must have `primary=false`.
- A common generator seed/lineage receives one `split_group`.
- Exact upstream duplicate payloads receive one primary row.
- Every validation split group is disjoint from all development groups.

The base development selection has 39 unique primary-real files and 16
non-primary files. The research-corpus materializer adds real sources before
adding four generated controls, then requires development, validation, and the
combined selection to pass the 70% rule before either manifest is written.

## Status meanings in the coverage matrix

- `selected-real`, `selected-control`, `selected-synthetic`: requirement met.
- `selected-partial-real`: useful real proxy, but the exact requested format
  or provenance is not proven.
- `pinned-recipe`: URL, archive checksum, license, and extraction checks exist,
  but the payload is not yet selected.
- `available-derived`, `available-control`, `available-synthetic`: integrity-
  verified local artifact exists but is not an eligible selected replacement.
- `missing`: no eligible selected payload or complete recipe.

The coverage report always emits `holdout_payload_inspected=false`.

## Research bundle safety and labels

The research materializer rejects source/destination symlinks, traversal,
duplicate or forbidden archive members, catalogue drift, checksum drift, and
changed existing payloads. ZIP and tar catalogues are validated in full even
though only pinned members are extracted. Re-running is byte-for-byte
idempotent, and a changed manifest row is never silently replaced.

Real data and controls are separate:

- actual upstream project files and collected observations may be
  `origin=real, primary=true`;
- Docker Official image layers count as real mixed software artifacts only
  when OCI index, platform manifest, and blob digests are all pinned;
- codec fixtures/conformance vectors are `origin=control, primary=false`;
- generated formats and version streams remain `derived` or `control`; and
- pydicom, GDAL, PX4, and similar test fixtures cannot satisfy a real-data row
  without independent collection provenance.

Exact URLs, versions, licenses, archive/member SHA-256 values, composition, and
the selected-source byte cap are in
`mathsvg/results/manifests/research-corpus-report.json`.
