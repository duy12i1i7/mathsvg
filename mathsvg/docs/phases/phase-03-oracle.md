# Phase 3 — Oracle, native entropy refresh

```text
PHASE
- Commit: working tree on mathsvg-absolute; development evidence only
- Status: native engine oracle implemented; holdout not opened

CORRECTNESS
- Round-trip: production search APIs serialize complete v1 archives
- Determinism: canonical input/candidate order and byte-sorted CSV output
- Bounds: production hard caps and explicit bounded-incomplete status

ORACLE
- Search: native function catalogue plus all eight literal codecs
- LZ parser: G1 baseline plus C4/C8/C16 and one-byte-lazy variants
- Coordinate: 73-block real incremental coordinate-basis scan
- Segmentation: 256-byte production DP under Balanced hard caps
- Residual: depth 0/1/2 complete-archive competition
- Symbolic: native depth-2 RSEE, no requires_engine placeholder
- DAG: natural development blocks versus current entropy-aware winner

DECISION
- Keep: exact winners and complete provenance rows
- Remove: no feature solely because it lowers entropy or node count
- Gate: 0.5% complete-byte gain and a non-synthetic winning block
- Holdout: not_opened_not_evaluated
```

## Why the refresh was required

The original Phase 3 reference oracle used an independent Python serializer
and a raw literal baseline. It was useful before a production engine existed,
but it became stale after native Canonical Huffman (`0x06`) and LZ-Huffman
(`0x07`) were added. A model that appeared to save bytes against raw literal
may have no incremental gain against the new entropy winner.

`python/oracle/native_probe` now calls the public Rust search APIs and records
complete canonical archive bytes, not entropy estimates. The checked probe
catalogue covers the same 13 exposed development microblocks and a separate
73-block real coordinate-basis scan. It refuses paths containing `holdout` or
`sealed`.

## Bounded-search interpretation

Every selected winner is exact and serialized, even when a work/state/ledger
cap stops the search. Such a row is `bounded_incomplete`: a positive gain is a
real found candidate, while zero gain is not a proof that no unsearched
candidate could win.

The production segmentation oracle uses the Balanced caps:

- 65,536 states;
- 4,096 candidates;
- 50,000,000 work units;
- 16,384 ledger rows.

This replaced an invalid diagnostic attempt that raised the ledger/state caps
to their hard maxima and retained about 6.3 GiB of losing states. That
interrupted run was discarded and produced no evidence.

## Completed native development probe

The accepted bounded probe completed with exit status 0 in 45.49 seconds
wall-clock time (44.28 seconds user, 1.11 seconds system) and reached 550,540
KiB peak RSS on the development host. It evaluated 13 frozen 4 KiB
microblocks and 73 real coordinate-basis blocks.

Under complete canonical archive accounting:

- coordinate basis produced 0/73 incremental wins;
- 256-byte segmentation saved 61 aggregate bytes, only on a synthetic row;
- residual depth two saved 115 bytes on the real `real-calgary-progc`
  microblock (3.672947940% for that row; 0.581807144% aggregate);
- symbolic depth two saved 0 bytes;
- DAG sharing saved 0 bytes and all 13 declared DAG rows were complete.

These are bounded development observations. In particular, zero on a
bounded-incomplete row does not prove global zero headroom. The one residual
winner passes the development retention threshold but is not validation or
holdout evidence.

## Canonical evidence

- `results/oracle/lz-parser-v1-raw.json` — exact entropy leaf, one-block and
  full-archive add-only evidence on eight open real development files;
- `results/oracle/lz-parser-v1-timing-raw.json` — separate ten-repetition
  directional timing evidence with explicit host limitations;
- `results/oracle/lz-parser.csv`;
- `results/oracle/lz-parser-v1-provenance.json`;
- `results/oracle/native-development-oracle.json` — raw production probe;
- `results/oracle/search.csv`;
- `results/oracle/coordinate.csv`;
- `results/oracle/segmentation.csv`;
- `results/oracle/residual.csv`;
- `results/oracle/symbolic.csv`;
- `results/oracle/dag-sharing.csv`;
- `results/oracle/function-family-headroom.csv`;
- `results/oracle/stop-policy.csv`;
- `results/oracle/external-gap.csv`;
- `results/manifests/oracle-manifest.json`.

`stop-policy.csv` records the 0.5% threshold, real winning rows, catalogue
completeness, decision and explicit
`holdout_status=not_opened_not_evaluated`. Exact numeric results and current
decisions are generated into `docs/oracle-analysis.md`; no stale percentage is
copied into this phase description.

No artifact in this phase is validation, holdout, dominance or publication
evidence.
