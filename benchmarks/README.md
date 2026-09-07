# Benchmark artifacts

- `raw/` is reserved for explicitly retained per-run artifacts.
- `results/` contains versioned JSON/CSV run directories and generated reports.
- `plots/` contains plot data and PNG/SVG images when matplotlib is available.

Generated artifacts are ignored by default. Evidence selected for publication
is force-added and checked in with its immutable run directory, generated
report/plots, source revision, and dataset manifests. The repository currently
retains thirteen immutable result documents: the twelve-item current/focused
evidence set (Full, Quick, Silesia, enwik8, corrected and historical ablation,
Git Copy, residual, Recursive, Bit-plane, Segmentation, and Max timeout
regression) plus the retained historical Quick snapshot. An untracked
generated run is not evidence merely because it exists below this directory.

Every regenerated `plot_manifest.json` records the validated input document
through `result_document_sha256` (`canonical-json-v1`), the SHA-256 of
`python/mathzip_bench/plots.py`, and a SHA-256 map for `plot_data.json` plus
every generated image. The project-local normalized JSON representation sorts
keys, uses compact separators and UTF-8 without ASCII escaping, rejects
non-finite numbers, and has no trailing newline; it is not RFC 8785. Strict
release verification checks all of these bindings.

Long Python-orchestrated runs create, inside each run directory:

- `expected-grid.json` and `run-identity.json`, immutable snapshots used for
  fail-closed resume checks;
- `checkpoint.json`, small progress/interval metadata;
- `checkpoint-rows/NNNNNNNN.json`, one atomic durable file per completed
  `(corpus,input_path,codec,threads)` row.

Resume with `python/run_benchmarks.py --config ... --resume RUN_DIRECTORY`.
An interrupted row is absent from `checkpoint-rows/` and therefore reruns its
whole warm-up and repetition sequence. `latest.json` and `latest.txt` are
published only after every key in the expected grid has exactly one row.

Quick run `20260724T134555Z-8b4c06c2`, Silesia
`20260724T165449Z-4ef6e508`, Git Copy
`20260724T140810Z-c825d7e1`, residual ablation
`20260724T143401Z-3bb520a6`, and Recursive ablation
`20260724T161541Z-70dfeff6`, and Bit-plane ablation
`20260724T174812Z-583dbb61`, and Segmentation ablation
`20260725T051723Z-a5f0fdfc`, and Max timeout regression
`20260725T055807Z-2e6ce6d4`, and corrected ablation
`20260725T170925Z-dde3921f`, and Full
`20260726T044843Z-213f07c3` use and retain this checkpoint/exact-grid layout.
The older Quick `20260724T025637Z-8563453d`, enwik8
`20260724T031043Z-f6e4fbfe`, and ablation
`20260724T032334Z-aca5bed4` artifacts predate it. They remain immutable
legacy-v1 evidence accepted by the backward-compatible validator, but they do
not contain `expected-grid.json`, `run-identity.json`, or resume metadata.

The Max timeout regression records 4/4 successful rows and zero failures at
clean revision `e6ce84274a45b5a6e4738844921f9b374439c643`, source-tree SHA-256
`e0f67fc713a0684287f72014b3c539c2d369324137d1fc77367b08bb27b5516e`,
and MathZip binary SHA-256
`60b440370688da1926dabd9a0b1c5fc304969dce095c2abc2e86d03845d828f2`.
It does not replace the two timeout rows in the immutable historical ablation.

The corrected ablation records 12 inputs × 21 codecs = 252/252 successful rows,
zero failures/warnings, one warm-up plus three measured repetitions, and
9,327.963 seconds elapsed. It ran from clean revision
`e5366fa7c5c1cd5801bb9cc86b19a09592403137`, source-tree SHA-256
`e3813b1523ee29c09f8b60b9d7a06844219e21c20114e3a3ce5762ae75b5323a`,
and MathZip binary SHA-256
`728494e5c0d1ba0aad42a039b056d26372cd60b9a744c0a4ad1744552084cd30`.
Its exact-grid/checkpoint evidence, report, 15 generated PNG plots, and plot
hash manifest pass strict verification. It is the publication source for
single-factor V13/V14/V17 analysis. The immutable 2026-07-24 artifact remains
the source for its own historical bytes, two Max timeouts, and causal-confound
warning; no row was rewritten.

Full records 725 inputs × 34 codec/thread keys = 24,650/24,650 successful rows,
73,950 measured trials and zero failure/resume in
129,095.967 seconds. It ran from clean revision
`e60f423b5f85d1716eb2356f2460f97ba4227885`, source-tree SHA-256
`55b6a32cc5abedcdcba8989065be83cbaa8600bede102e73c39306bb58153e63`,
and MathZip binary SHA-256
`366ca45932751ecb10d52f4db3f4806b94acd44e3ec9ccbf45e33b45bd82e228`.
Its 24,650 durable checkpoint rows, exact-grid/run identity, raw JSON/CSV,
generated report, 15 Full PNG plots and plot hash manifest are retained and
strict-verify with zero warning. The companion corrected ablation supplies the
sixteenth requested plot type, `ablation_comparison.png`, without mixing
ablation rows into Full.

`results.json` is about 203 MB and the complete Full evidence directory is
about 441 MB. The local Git commit is canonical; mirrors with a 100 MB
per-blob limit must distribute that immutable blob through a release asset or
large-file-aware transport without regenerating or rewriting the run.

The older main/focused artifacts use the legacy out-of-timing metrics probe.
Corrected ablation and Full use `mathzip_metrics_mode: inline`: timed
compression produces archive and sidecar once, and JSON rows carry
aggregate-consistent search provenance. Current ablation/residual configs also
declare `inline`. Validators remain backward compatible; protocols must never
be silently relabelled when comparing speed.
