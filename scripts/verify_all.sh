#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

python3 -m compileall -q python
python3 -m unittest discover -s python -t python -v
cargo fmt --all -- --check
cargo test --locked --workspace
cargo check --locked --manifest-path fuzz/Cargo.toml

if [ -f datasets/synthetic/manifest.json ]; then
    python3 python/verify_results.py \
        --synthetic-manifest datasets/synthetic/manifest.json
fi

if [ -d datasets/data ]; then
    python3 python/verify_results.py --dataset-root datasets/data
fi

if [ -d datasets/generated ]; then
    python3 python/verify_results.py --generated-root datasets/generated
fi

for profile in quick silesia enwik8 full git-copy-ablation residual-ablation recursive-ablation bit-plane-ablation segmentation-ablation max-timeout-regression; do
    if [ -f "benchmarks/results/$profile/latest.json" ]; then
        python3 python/verify_results.py \
            "benchmarks/results/$profile/latest.json" \
            --strict \
            --plots "benchmarks/plots/$profile"
    fi
done

if [ -f benchmarks/results/ablation/latest.json ]; then
    python3 python/verify_results.py \
        benchmarks/results/ablation/latest.json \
        --strict \
        --plots benchmarks/plots/ablation \
        --allow-failures
fi
