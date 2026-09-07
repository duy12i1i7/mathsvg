# Current native development oracle analysis

## Scope and validity

This is development-only evidence from the native Rust engine. Every input was already exposed to the predecessor project and is marked `legacy_observed=true`. The probe refuses holdout/sealed paths; no validation or holdout payload was opened.

Every compared value is a complete canonical v1 archive byte count from the production serializer. Native literal leaves compete through the current eight-codec catalogue, including Canonical Huffman (0x06) and LZ-Huffman (0x07). A bounded result is not an unrestricted global optimum.

## Headroom summary

| Oracle | Baseline bytes | Best bytes | Headroom | Headroom % |
|---|---:|---:|---:|---:|
| Native finite search vs raw literal | 59540 | 19766 | 39774 | 66.802149815 |
| Coordinate-basis incremental, 73 real blocks | 176022 | 176022 | 0 | 0.000000000 |
| Production bounded segmentation, 256 B | 19766 | 19705 | 61 | 0.308610746 |
| Residual depth 0→2 | 19766 | 19651 | 115 | 0.581807144 |
| Symbolic depth ≤2 | 19766 | 19766 | 0 | 0.000000000 |
| Native DAG vs function/entropy winner | 19766 | 19766 | 0 | 0.000000000 |

## Completeness and observed real wins

| Oracle | Complete rows | Real winning rows |
|---|---:|---:|
| Search | 0/13 | 4 |
| Coordinate basis | 0/73 | 0 |
| Segmentation | 0/13 | 0 |
| Residual depth 2 | 0/13 | 1 |
| Symbolic | 0/13 | 0 |
| DAG | 13/13 | 0 |

The search row counts a win against raw literal. Real wins: 4 total; 3 entropy-literal; 1 non-literal function. The total must not be reported as new function-family headroom.

## Stop-policy decisions

- **Coordinate basis: stop emission.** The frozen scan contains 0/73 incremental wins over the already active whole-coordinate/no-coordinate competition. Keep the experiment and rows, but do not add it to built-in profiles.
- **Segmentation: retain only bounded infrastructure.** It has 0 real winning sample rows and 0 at or above 0.5%. Any zero result on a bounded-incomplete row cannot close search headroom.
- **Residual: retain as an experiment.** Depth-two real winning rows: 1; rows meeting the 0.5% threshold: 1. The development retention gate passes, so the provider remains available for validation; this does not enable built-in emission. The observed real winner is `real-calgary-progc` +115 bytes (3.672947940% on its 4 KiB sample).
- **Symbolic: bounded result, not global proof.** Aggregate observed headroom is 0 bytes, with 0 qualifying real rows. Budget stops or zero qualifying real wins keep RSEE out of built-in profiles.
- **DAG sharing: stop emission.** Natural development samples contain 0 real incremental wins over the current entropy-aware function winner, 0 at or above 0.5%. Definition-count reduction without byte gain is rejected; all declared DAG rows are complete.

## Current external gap

The current native development pilot uses ten measured repetitions and captured artifact provenance. It remains one-host development evidence, not validation, holdout or a two-machine dominance certificate.

| External baseline | Size gap % | Compress slowdown | Decode slowdown | RSS ratio |
|---|---:|---:|---:|---:|
| brotli | not run | not run | not run | not run |
| gzip-6 | 13.016545646 | 13.544506639 | 4.699624048 | 12.737721022 |
| lz4 | -19.690146802 | 63.999313868 | 7.848530058 | 2.662628337 |
| xz-9e | 54.932802504 | 1.096567239 | 1.897612727 | 0.181819457 |
| zstd-default | 13.831241811 | 29.846057861 | 7.785812969 | 1.373622881 |

## Limits and publication boundary

- Search/profile rows with `bounded_incomplete` expose a real exact winner inside consumed budget, but cannot prove zero remaining headroom.
- Residual and symbolic comparisons use complete archives; no entropy lower bound is substituted for an achievable candidate.
- The 73-block coordinate-basis decision is an incremental feature decision inside the frozen catalogue, not a claim that coordinate discovery has no headroom.
- No row in this phase is publishable holdout evidence or a dominance certificate.
