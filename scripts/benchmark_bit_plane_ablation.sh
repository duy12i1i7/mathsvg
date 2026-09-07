#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

if ! command -v zstd >/dev/null 2>&1; then
    echo "missing required bit-plane ablation baseline executable: zstd" >&2
    echo "install zstd or prepend its directory to PATH before this strict run" >&2
    exit 2
fi

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

python3 python/run_benchmarks.py \
    --config configs/bit-plane-ablation.yaml \
    --mathzip-binary target/release/mathzip \
    --strict

python3 python/verify_results.py \
    benchmarks/results/bit-plane-ablation/latest.json \
    --strict
python3 python/generate_plots.py \
    benchmarks/results/bit-plane-ablation/latest.json \
    --output benchmarks/plots/bit-plane-ablation
python3 python/generate_report.py \
    benchmarks/results/bit-plane-ablation/latest.json \
    --output benchmarks/results/bit-plane-ablation/latest-report.md
