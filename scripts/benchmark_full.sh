#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

resume_path=${MATHZIP_BENCHMARK_RESUME:-}
case $# in
    0)
        ;;
    2)
        if [ "$1" != "--resume" ]; then
            echo "usage: $0 [--resume RUN_DIRECTORY_OR_CHECKPOINT]" >&2
            exit 2
        fi
        if [ -n "$resume_path" ]; then
            echo "set resume target by argument or MATHZIP_BENCHMARK_RESUME, not both" >&2
            exit 2
        fi
        resume_path=$2
        ;;
    *)
        echo "usage: $0 [--resume RUN_DIRECTORY_OR_CHECKPOINT]" >&2
        exit 2
        ;;
esac
if [ "${1:-}" = "--resume" ] && [ -z "$resume_path" ]; then
    echo "--resume requires a non-empty explicit target" >&2
    exit 2
fi

cd "$REPO_ROOT"

missing_tools=
for tool in gzip bzip2 xz zstd lz4 brotli; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        missing_tools="${missing_tools}${missing_tools:+ }$tool"
    fi
done
if ! command -v 7z >/dev/null 2>&1 && ! command -v 7zz >/dev/null 2>&1; then
    missing_tools="${missing_tools}${missing_tools:+ }7z/7zz"
fi
if [ -n "$missing_tools" ]; then
    echo "missing required Full baseline executable(s): $missing_tools" >&2
    echo "install them or prepend stable executable paths to PATH before this run" >&2
    exit 2
fi

if [ -n "$resume_path" ] && \
   [ ! -d "$resume_path" ] && [ ! -f "$resume_path" ]; then
    echo "resume target does not exist: $resume_path" >&2
    exit 2
fi

if [ -n "$resume_path" ]; then
    if [ ! -x target/release/mathzip ]; then
        echo "resume requires the original executable target/release/mathzip" >&2
        exit 2
    fi
else
    cargo build --locked --release --bin mathzip

    if ! python3 python/generate_synthetic.py \
        --output datasets/synthetic_full \
        --seed 1297748005 \
        --sizes 0,1,31,256,4096,65536,1048576 \
        --check-current; then
        python3 python/generate_synthetic.py \
            --output datasets/synthetic_full \
            --seed 1297748005 \
            --sizes 0,1,31,256,4096,65536,1048576 \
            --force
    fi

    if [ ! -f datasets/generated/mixed/manifest.json ] || \
       [ ! -f datasets/generated/git_snapshots/manifest.json ]; then
        python3 python/generate_auxiliary_corpora.py \
            --output datasets/generated \
            --seed 1297748005 \
            --force
    fi

    if [ "${MATHZIP_SKIP_DOWNLOAD:-0}" != "1" ]; then
        python3 python/download_datasets.py \
            --config configs/full.yaml \
            --output datasets/data
    fi
fi

python3 python/verify_results.py \
    --synthetic-manifest datasets/synthetic_full/manifest.json

python3 python/verify_results.py --generated-root datasets/generated
python3 python/verify_results.py --dataset-root datasets/data

if [ -n "$resume_path" ]; then
    python3 python/run_benchmarks.py \
        --config configs/full.yaml \
        --mathzip-binary target/release/mathzip \
        --resume "$resume_path" \
        --strict \
        --allow-failures
else
    python3 python/run_benchmarks.py \
        --config configs/full.yaml \
        --mathzip-binary target/release/mathzip \
        --strict \
        --allow-failures
fi

python3 python/verify_results.py \
    benchmarks/results/full/latest.json \
    --strict \
    --allow-failures
python3 python/generate_plots.py \
    benchmarks/results/full/latest.json \
    --output benchmarks/plots/full
python3 python/generate_report.py \
    benchmarks/results/full/latest.json \
    --output benchmarks/results/full/latest-report.md
