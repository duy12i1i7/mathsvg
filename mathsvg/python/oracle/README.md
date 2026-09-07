# Development oracle runner

`analyze.py` regenerates the canonical development-only oracle bundle. It
first executes `native_probe`, a standalone Rust diagnostic that calls only
the public production APIs. The probe measures complete v1 archives; it does
not estimate archive size from entropy, fit score or node count.

```bash
python3 mathsvg/python/oracle/analyze.py \
  --current-benchmark-run \
  mathsvg/results/raw/development-pilot-v8-final-binary-run.json
```

The explicit run metadata argument is part of canonical regeneration. It
causes the analyzer to verify the completed development JSONL, captured runner
identity, manifest split, status counts and raw digest before writing the
current external gap. Omitting it intentionally falls back to predecessor
legacy evidence and must not be used to refresh the final local bundle.

For an interrupted analysis, a completed probe JSON may be reused:

```bash
python3 mathsvg/python/oracle/analyze.py \
  --native-probe-json /path/to/completed-probe.json
```

The loader still validates the schema, exact 13-sample development set,
73-block real coordinate scan, Canonical/LZ-Huffman catalogue and
`holdout_payload_inspected=false`. It rejects any source path containing
`holdout` or `sealed`.

The production segmentation comparison uses the Balanced hard caps
(`65,536` states, `4,096` candidates, `80,000,000` work units and `16,384`
ledger rows). A row stopped by one of these caps is
`bounded_incomplete`; its selected winner is exact, but zero observed gain is
not a proof of zero headroom outside the consumed budget.

Canonical outputs are written under `mathsvg/results/oracle/`, with source and
artifact hashes in `mathsvg/results/manifests/oracle-manifest.json`.
`stop-policy.csv` consolidates the 0.5% threshold, real winning rows,
completeness, decision and explicit `not_opened_not_evaluated` holdout status.
`model.py` remains an independently decoded reference-model test fixture; its
old raw-literal percentages no longer decide production emission.

## Bounded LZ parser oracle

`lz_parser_probe` is a separate development-real probe for encoder policies
that all emit the existing opcode-`0x07` wire format. It evaluates G1 against
C4/C8/C16 and their one-byte-lazy variants at the entropy leaf, one-block
archive and full-archive levels. It verifies exact count/model/emit identity,
round-trip, deterministic output, fixed scratch and checked work bounds.

The checked artifacts are:

- `results/oracle/lz-parser-v1-raw.json` for authoritative exact
  size/work/round-trip evidence;
- `results/oracle/lz-parser-v1-timing-raw.json` for directional timing only;
- `results/oracle/lz-parser.csv` for per-file and aggregate rows;
- `results/oracle/lz-parser-v1-provenance.json` for hashes, limitations and
  both the historical C4L admission and current C8L retention decision.

The marginal development result from `C4L` to `C8L` is 87,952 archive bytes,
or 0.6549908423 percentage points of the G1 baseline, which exceeds the 0.5%
retention threshold. The next step from `C8L` to `C16L` is 50,863 bytes, or
0.3787838731 percentage points, below the threshold. Therefore every non-Fast
profile retains `C8L` (`C8Lazy`), while Fast stays on `G1`; the depth-admission
decision stops at `C8L` rather than promoting `C16L`.

Conditional candidate emission (which skips the second walk for a loser) and
chunked Huffman bit packing are byte-identical encoder hot paths, not
additional oracle candidates. They do not change archive sizes, canonical
bytes or the two-walk deterministic budget charged to a complete
enhanced-policy evaluation.

Regenerate the summary without opening holdout payloads:

```bash
python3 -m mathsvg.python.oracle.lz_parser \
  --raw mathsvg/results/oracle/lz-parser-v1-raw.json \
  --timing-raw mathsvg/results/oracle/lz-parser-v1-timing-raw.json \
  --manifest mathsvg/results/manifests/development.csv \
  --binary mathsvg/python/oracle/lz_parser_probe/target/release/mathsvg-lz-parser-oracle-probe \
  --prior-size-provenance mathsvg/results/oracle/lz-parser-v1-provenance.json \
  --csv-output mathsvg/results/oracle/lz-parser.csv \
  --provenance-output mathsvg/results/oracle/lz-parser-v1-provenance.json \
  --source mathsvg/python/oracle/lz_parser.py
```
