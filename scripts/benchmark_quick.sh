#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

cargo build --locked --release --bin mathzip

if ! python3 python/generate_synthetic.py \
    --output datasets/synthetic \
    --seed 1297748005 \
    --check-current; then
    python3 python/generate_synthetic.py \
        --output datasets/synthetic \
        --seed 1297748005 \
        --force
fi

python3 python/verify_results.py \
    --synthetic-manifest datasets/synthetic/manifest.json

if [ "${MATHZIP_SKIP_DOWNLOAD:-0}" != "1" ]; then
    python3 python/download_datasets.py \
        --config configs/quick.yaml \
        --output datasets/data
fi

python3 python/verify_results.py --dataset-root datasets/data

python3 python/run_benchmarks.py \
    --config configs/quick.yaml \
    --mathzip-binary target/release/mathzip \
    --strict

python3 python/verify_results.py benchmarks/results/quick/latest.json --strict
python3 python/generate_plots.py \
    benchmarks/results/quick/latest.json \
    --output benchmarks/plots/quick
python3 python/generate_report.py \
    benchmarks/results/quick/latest.json \
    --output benchmarks/results/quick/latest-report.md
