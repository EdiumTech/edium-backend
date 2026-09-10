FROM node:22.19.0-bookworm-slim AS node-dependencies

WORKDIR /app
COPY runner/package.json runner/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force

FROM golang:1.26.1-bookworm AS go-toolchain

# The official Swift image supplies the native compiler. Submitted Swift is
# cross-compiled to WASI and is never executed as a native Linux program.
FROM swift:6.3.1-jammy

ARG KOTLIN_URL=https://github.com/JetBrains/kotlin/releases/download/v2.2.21/kotlin-compiler-2.2.21.zip
ARG KOTLIN_SHA256=a623871f1cd9c938946948b70ef9170879f0758043885bbd30c32f024e511714
ARG SWIFT_WASI_URL=https://download.swift.org/swift-6.3.1-release/wasm-sdk/swift-6.3.1-RELEASE/swift-6.3.1-RELEASE_wasm.artifactbundle.tar.gz
ARG SWIFT_WASI_SHA256=bd47baa20771f366d8beed7970afaa30742b2210097afd15f85427226d8f4cf2
ARG WASMTIME_URL=https://github.com/bytecodealliance/wasmtime/releases/download/v48.0.1/wasmtime-v48.0.1-x86_64-linux.tar.xz
ARG WASMTIME_SHA256=4c2e31b68ad99e0a519f225a261fda099eb15f056d4a24fdb3c2a46517bde1df
ARG PYTHON_WASI_URL=https://github.com/vmware-labs/webassembly-language-runtimes/releases/download/python/3.12.0%2B20231211-040d5a6/python-3.12.0-wasi-sdk-20.0.tar.gz
ARG PYTHON_WASI_SHA256=6c1cddbb69ae09e87eee2906bdc70539bff5f2969818a6f8457d4e6a6eb67d4d
ARG WASMTIME_PYTHON_URL=https://files.pythonhosted.org/packages/a2/92/e144fcf578fc394678c24b042efe45f3b0614acdb87ea95d8b839b208842/wasmtime-48.0.0-py3-none-manylinux1_x86_64.whl
ARG WASMTIME_PYTHON_SHA256=58544d539053dff7bd4cf30c40d7a540862d683013c0dfa6ba46a063f5b682f7

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl openjdk-17-jre-headless procps python3 unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-dependencies /usr/local/bin/node /usr/local/bin/node
COPY --from=node-dependencies /app/node_modules /app/node_modules
COPY --from=go-toolchain /usr/local/go /opt/go

RUN set -eux; \
    mkdir -p /opt/downloads /opt/kotlin /opt/swift-wasi /opt/wasmtime /opt/python-wasi /opt/wasmtime-python; \
    curl -fL "$KOTLIN_URL" -o /opt/downloads/kotlin.zip; \
    echo "$KOTLIN_SHA256  /opt/downloads/kotlin.zip" | sha256sum -c -; \
    unzip -q /opt/downloads/kotlin.zip -d /opt/kotlin; \
    curl -fL "$SWIFT_WASI_URL" -o /opt/downloads/swift-wasi.tar.gz; \
    echo "$SWIFT_WASI_SHA256  /opt/downloads/swift-wasi.tar.gz" | sha256sum -c -; \
    tar -xzf /opt/downloads/swift-wasi.tar.gz -C /opt/swift-wasi --strip-components=3; \
    curl -fL "$WASMTIME_URL" -o /opt/downloads/wasmtime.tar.xz; \
    echo "$WASMTIME_SHA256  /opt/downloads/wasmtime.tar.xz" | sha256sum -c -; \
    tar -xJf /opt/downloads/wasmtime.tar.xz -C /opt/wasmtime --strip-components=1; \
    curl -fL "$PYTHON_WASI_URL" -o /opt/downloads/python-wasi.tar.gz; \
    echo "$PYTHON_WASI_SHA256  /opt/downloads/python-wasi.tar.gz" | sha256sum -c -; \
    tar -xzf /opt/downloads/python-wasi.tar.gz -C /opt/python-wasi; \
    curl -fL "$WASMTIME_PYTHON_URL" -o /opt/downloads/wasmtime-python.whl; \
    echo "$WASMTIME_PYTHON_SHA256  /opt/downloads/wasmtime-python.whl" | sha256sum -c -; \
    python3 -m zipfile -e /opt/downloads/wasmtime-python.whl /opt/wasmtime-python; \
    rm -rf /opt/downloads

WORKDIR /app
COPY runner/sandbox.js runner/server.js runner/tasks.js runner/dispatcher.js ./
COPY runner/kotlin-compile.js runner/kotlin-bridge.kt ./
COPY runner/swift-runtime.js runner/swift-bridge.swift ./
COPY runner/go-runtime.js runner/go-bridge.go ./
COPY runner/python-runtime.js runner/python-wasi-host.py ./
COPY functions/app/contest_catalog.json ./contest_catalog.json

ENV NODE_ENV=production \
    CONTEST_CATALOG_PATH=/app/contest_catalog.json \
    EDIUM_KOTLIN_HOME=/opt/kotlin/kotlinc \
    EDIUM_JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    EDIUM_SWIFT_HOME=/ \
    EDIUM_SWIFT_SDK=/opt/swift-wasi \
    EDIUM_WASMTIME=/opt/wasmtime/wasmtime \
    EDIUM_GO_HOME=/opt/go \
    EDIUM_PYTHON_HOST=/usr/bin/python3 \
    EDIUM_PYTHON_WASI_HOME=/opt/python-wasi \
    EDIUM_WASMTIME_PYTHON_PATH=/opt/wasmtime-python

RUN node --version \
    && /opt/go/bin/go version \
    && /opt/wasmtime/wasmtime --version \
    && /usr/bin/python3 -I /app/python-wasi-host.py --help >/dev/null \
    && test "$(cat /opt/kotlin/kotlinc/build.txt)" = "2.2.21-release-469" \
    && swiftc --version | grep -F "Swift version 6.3.1"

RUN chown -R 65532:65532 /app /opt/kotlin /opt/swift-wasi /opt/wasmtime /opt/python-wasi /opt/wasmtime-python \
    && chmod -R a-w /app /opt/kotlin /opt/swift-wasi /opt/wasmtime /opt/python-wasi /opt/wasmtime-python /opt/go

USER 65532:65532
EXPOSE 8080
CMD ["node", "server.js"]
