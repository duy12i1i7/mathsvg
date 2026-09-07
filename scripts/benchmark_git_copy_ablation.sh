#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

cargo build --locked --release --bin mathzip

if [ ! -f datasets/generated/git_snapshots/manifest.json ]; then
    python3 python/generate_auxiliary_corpora.py \
        --output datasets/generated \
        --seed 1297748005 \
        --force
fi

python3 python/verify_results.py --generated-root datasets/generated

python3 python/run_benchmarks.py \
    --config configs/git-copy-ablation.yaml \
    --mathzip-binary target/release/mathzip \
    --strict

python3 python/verify_results.py \
    benchmarks/results/git-copy-ablation/latest.json \
    --strict
python3 python/generate_plots.py \
    benchmarks/results/git-copy-ablation/latest.json \
    --output benchmarks/plots/git-copy-ablation
python3 python/generate_report.py \
    benchmarks/results/git-copy-ablation/latest.json \
    --output benchmarks/results/git-copy-ablation/latest-report.md
