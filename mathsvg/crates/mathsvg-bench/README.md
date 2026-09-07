# mathsvg-bench

This crate supplies the required Rust workspace entry point while keeping the
benchmark protocol in its existing, tested Python implementation. The binary
directly launches `python3 -m mathsvg.python.benchmarks.runner` and forwards
each argument without a shell:

```text
cargo run -p mathsvg-bench -- --repository . --help
```

Run it from the repository root so the `mathsvg.python` package is importable.
Set `MATHSVG_PYTHON` to a Python executable path when `python3` is not the
desired interpreter. Benchmark schemas, scheduling, round-trip verification,
summaries and plots remain owned by `mathsvg/python`; this crate does not
silently introduce a second measurement implementation.
