#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

cargo build --locked --release --bin mathzip

if [ "${MATHZIP_SKIP_DOWNLOAD:-0}" != "1" ]; then
    python3 python/download_datasets.py \
        --config configs/enwik8.yaml \
        --output datasets/data
fi

python3 python/verify_results.py --dataset-root datasets/data

python3 python/run_benchmarks.py \
    --config configs/enwik8.yaml \
    --mathzip-binary target/release/mathzip \
    --strict

python3 python/verify_results.py benchmarks/results/enwik8/latest.json --strict
python3 python/generate_plots.py \
    benchmarks/results/enwik8/latest.json \
    --output benchmarks/plots/enwik8
python3 python/generate_report.py \
    benchmarks/results/enwik8/latest.json \
    --output benchmarks/results/enwik8/latest-report.md
