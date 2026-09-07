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
for tool in gzip bzip2 xz zstd lz4 brotli 7z; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        missing_tools="${missing_tools}${missing_tools:+ }$tool"
    fi
done
if [ -n "$missing_tools" ]; then
    echo "missing required Silesia baseline executable(s): $missing_tools" >&2
    echo "install them or prepend their directories to PATH before this strict run" >&2
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
    if [ "${MATHZIP_SKIP_DOWNLOAD:-0}" != "1" ]; then
        python3 python/download_datasets.py \
            --config configs/silesia.yaml \
            --output datasets/data
    fi
fi

python3 python/verify_results.py --dataset-root datasets/data

if [ -n "$resume_path" ]; then
    python3 python/run_benchmarks.py \
        --config configs/silesia.yaml \
        --mathzip-binary target/release/mathzip \
        --resume "$resume_path" \
        --strict
else
    python3 python/run_benchmarks.py \
        --config configs/silesia.yaml \
        --mathzip-binary target/release/mathzip \
        --strict
fi

python3 python/verify_results.py \
    benchmarks/results/silesia/latest.json \
    --strict
python3 python/generate_plots.py \
    benchmarks/results/silesia/latest.json \
    --output benchmarks/plots/silesia
python3 python/generate_report.py \
    benchmarks/results/silesia/latest.json \
    --output benchmarks/results/silesia/latest-report.md
