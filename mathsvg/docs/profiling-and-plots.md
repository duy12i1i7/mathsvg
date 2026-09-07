# Profiling and plots

`python/analysis/profile_benchmark.py` consumes strict benchmark
`TrialRecord` JSONL and emits development-only profiling evidence. It rejects
any validation or holdout row, excludes warm-ups, and uses only successful
measured rows for numeric metrics. Failed, timed-out and unavailable rows
remain visible as counts.

Run the checked-in development pilot with:

```text
python3 -m mathsvg.python.analysis.profile_benchmark \
  mathsvg/results/raw/development-pilot-v1.jsonl \
  --profiling-output \
    mathsvg/results/profiling/development-pilot-v1.csv \
  --plots-directory mathsvg/results/plots \
  --artifact-prefix development-pilot-v1
```

The CSV contains file and corpus rows for every codec/configuration. It reports
archive fraction and compression ratio, compression/decompression wall and CPU
time, CPU/wall fraction, decimal MB/s throughput, phase and overall peak RSS,
status counts, and exact native procedural/entropy attribution when present.

Corpus archive/time/CPU values are summed within a repetition; corpus RSS is
the maximum file peak. A corpus repetition contributes metrics only when every
selected file has a successful row. Missing evidence remains an empty CSV cell
and an SVG `not measured` marker. An exact measured zero remains zero.

The five deterministic, code-native SVGs cover:

- archive/original fraction;
- compression throughput;
- decompression throughput;
- peak RSS;
- procedural versus entropy gains and penalties.

SVGs contain no scripts, timestamps, external resources or stochastic layout.
Every chart is labelled as development evidence.
