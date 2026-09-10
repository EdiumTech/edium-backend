# Local Python WASI runtime

AI/ML candidates use genuine **CPython 3.12.0**, with `def solve(data: dict) -> dict` and the standard library. No pip dependencies, native extensions, network or host filesystem are provided. This is a local-only prototype: Python 3.12.0 is an old vendor build, not an up-to-date production interpreter. The production image remains gated; do not deploy this build as-is.

## Isolation

`runner/python-runtime.js` starts the trusted `python-wasi-host.py` using host Python `-I`, an empty environment, private temporary directory and stdin JSON. Host Python never evaluates, executes or compiles candidate text. Only a fixed WASM bootstrap does that inside the interpreter's WebAssembly linear memory. Candidate source does not appear in process arguments or logs.

The official Wasmtime Python binding compiles the immutable, SHA256-pinned interpreter once per request. Every test gets a fresh Store/instance and input fd. Only `/usr/local/lib` is preopened, with `fs_mutable=False`; it contains the interpreter's stdlib zip and minimal companion files. There is no host-root or job-directory preopen, inherited environment, socket/network grant, native module import, candidate-provided Wasm, or precompiled/native cache deserialization. Expected answers never enter the guest.

Limits: 64 KiB source, at most seven tests, 64 MiB guest linear memory, two billion Wasm fuel units and two seconds per test (including interpreter startup), combined stdout/stderr 256 KiB per test, entire helper 45 seconds, and bounded host protocol output. Timers are canceled and joined before the next test. Output callbacks never raise across the native binding. Host JIT memory is not the guest-memory limit: production still needs separately verified hard OS/container memory/CPU limits and a maintained interpreter.

## Pinned artifacts

Downloaded only into a private temporary directory; no system/home Python package installation or user configuration change.

| Artifact | SHA256 |
| --- | --- |
| [VMware CPython 3.12.0 WASI SDK 20 archive](https://github.com/vmware-labs/webassembly-language-runtimes/releases/download/python/3.12.0%2B20231211-040d5a6/python-3.12.0-wasi-sdk-20.0.tar.gz) | `6c1cddbb69ae09e87eee2906bdc70539bff5f2969818a6f8457d4e6a6eb67d4d` |
| Extracted `bin/python-3.12.0.wasm` | `5ce0cbeb843e6e5abf2d50c7158002e8333c26a40fbe27a7a52e66ee48cf64a8` |
| Extracted `usr/local/lib/python312.zip` | `74130c400ba5b818bf58bfc2f41fc075f4350cbb13e53099c84e9c0494ec5444` |
| [Official Wasmtime 48.0.0 macOS arm64 wheel](https://files.pythonhosted.org/packages/dc/a6/91c9c19ed7f8e164f4db6405d872c9397be9f53e4f325d0adcd5e67598f4/wasmtime-48.0.0-py3-none-macosx_11_0_arm64.whl) | `ea69889a3c51702e9da5f5f441027ca934f7758f8926a4ed167b0d6877f092e8` |

Archive checksum was verified against the [vendor checksum](https://github.com/vmware-labs/webassembly-language-runtimes/releases/download/python/3.12.0%2B20231211-040d5a6/python-3.12.0-wasi-sdk-20.0.tar.gz.sha256sum); wheel against [PyPI metadata](https://pypi.org/pypi/wasmtime/48.0.0/json). Runtime startup also checks exact WASM/stdlib hashes and binding version metadata. Treat configured binding/interpreter directories and the host Python executable as trusted operator-owned dependencies, never as candidate-writable directories.

Verified local configuration for this development session:

```sh
export EDIUM_PYTHON_HOST=/opt/homebrew/bin/python3
export EDIUM_PYTHON_WASI_HOME=/private/tmp/edium-python-wasi.FTGnVE/runtime
export EDIUM_WASMTIME_PYTHON_PATH=/private/tmp/edium-python-wasi.FTGnVE/wheel
node dev/python-runtime-smoke.js
```

The temporary directories will not survive all cleanup/reboots; re-extract the same verified artifacts into a private directory and update environment variables if needed. The wheel is extracted, not installed into host site-packages. The trusted helper imports it explicitly under isolated host Python.

## Verification

`dev/python-runtime-smoke.js` checks all three AI reference solutions against all seven tests each, syntax errors, infinite loops, memory exhaustion and output floods, then asserts genuine `sys.platform == "wasi"`, Python version, `re`/`math` availability, empty environment, and denied host read/write, stdlib write/traversal, subprocess, native FFI and network. The host-file proof uses only a synthetic private canary and verifies it remains unchanged. The network proof targets an actually listening ephemeral loopback server and asserts it receives no guest connection.

Primary API reference: [Bytecode Alliance Wasmtime Python API](https://bytecodealliance.github.io/wasmtime-py/). Version 48.0.0's actual `WasiConfig.preopen_dir` accepts `fs_mutable=False`; do not substitute an older API or a writable CLI `--dir` preopen.
