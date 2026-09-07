FROM rust:1.85-slim-bookworm AS builder

WORKDIR /src
COPY . .
RUN cargo build --locked --release

FROM python:3.12-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive
COPY python/requirements.txt /tmp/mathzip-requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        brotli \
        bzip2 \
        ca-certificates \
        gzip \
        lz4 \
        p7zip-full \
        xz-utils \
        zstd \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --no-cache-dir \
        -r /tmp/mathzip-requirements.txt \
    && rm -f /tmp/mathzip-requirements.txt

COPY --from=builder /src/target/release/mathzip /usr/local/bin/mathzip
COPY --from=builder /src/target/release/mathzip-bench /usr/local/bin/mathzip-bench

WORKDIR /work
COPY Cargo.toml YeuCau.md /opt/mathzip/
COPY python /opt/mathzip/python
COPY configs /opt/mathzip/configs
COPY scripts /opt/mathzip/scripts
COPY datasets/manifests /opt/mathzip/datasets/manifests
ENV MATHZIP_PYTHON_DIR=/opt/mathzip/python

ENTRYPOINT ["mathzip"]
CMD ["--help"]
