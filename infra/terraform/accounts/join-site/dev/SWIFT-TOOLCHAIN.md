# Local Swift contest runtime

The local macOS bridge compiles real Swift 6.3.1 to WebAssembly with the official
WASI SDK, then runs every test in Wasmtime 48.0.1. It does not use SwiftPM,
candidate manifests, dependency downloads, or native execution of candidate code.
`swift-runtime.js` checks both executable versions before compiling.

Xcode's Apple Swift 6.3.1 does **not** include the Wasm LLVM backend. A probe
failed with `No available targets are compatible with triple wasm32-unknown-wasip1`.
The standalone Swift.org toolchain is necessary; it can be extracted to a private
temporary directory without installing it or editing any user configuration.

## Pinned official artifacts

| Artifact | SHA-256 |
| --- | --- |
| [Swift 6.3.1 macOS package](https://download.swift.org/swift-6.3.1-release/xcode/swift-6.3.1-RELEASE/swift-6.3.1-RELEASE-osx.pkg) (1.5 GB) | `6e31d6696e7a2819f3effa7ccbbca721a45c9e300ae5f5200cb4da8ced2f82c8` |
| [Swift 6.3.1 WASI SDK](https://download.swift.org/swift-6.3.1-release/wasm-sdk/swift-6.3.1-RELEASE/swift-6.3.1-RELEASE_wasm.artifactbundle.tar.gz) (73 MB) | `bd47baa20771f366d8beed7970afaa30742b2210097afd15f85427226d8f4cf2` |
| [Wasmtime 48.0.1 macOS ARM](https://github.com/bytecodealliance/wasmtime/releases/download/v48.0.1/wasmtime-v48.0.1-aarch64-macos.tar.xz) (9 MB) | `88cc08b395fbfb960b99f355a81224af975679b8a5f4b74a51d59e5e34b20dcd` |

The Wasmtime digest matches its immutable GitHub release asset metadata. The
Swift package also passed `pkgutil --check-signature`: Swift Open Source
Developer ID Installer, trusted Apple notarization and timestamp. That check
requires access to macOS trust services; a containing development sandbox can
otherwise incorrectly report an invalid signature.

After checking downloads, use `pkgutil --expand-full` for the `.pkg` and `tar`
for the two archives. Do not run `installer` or `swift sdk install`.

Set these environment variables only for the local demo server or smoke command:

```sh
export EDIUM_SWIFT_HOME="<extracted-package>/swift-6.3.1-RELEASE-osx-package.pkg/Payload"
export EDIUM_SWIFT_SDK="<sdk-directory>/swift-6.3.1-RELEASE_wasm.artifactbundle/swift-6.3.1-RELEASE_wasm/wasm32-unknown-wasip1"
export EDIUM_WASMTIME="<runtime-directory>/wasmtime-v48.0.1-aarch64-macos/wasmtime"
node dev/swift-sandbox-proof.js
node dev/swift-runtime-smoke.js
```

Run the commands from `infra/terraform/accounts/join-site`. The demo uses the
same runner module and dispatcher. No paths are stored in user shell profiles.

## Boundaries and tested behavior

The compiler runs in a deny-default Seatbelt profile. It can read only the
trusted toolchain, WASI SDK, needed system libraries and its own fresh job
directory. It can execute only canonical `swift-driver`, `swift-frontend`,
`clang` and `wasm-ld` binaries. Writes are limited to its job directory, and
network access is denied. Environment variables are explicitly constructed.
All compiler stages, including version checks, share a 45-second deadline.
The entire subprocess group is killed on timeout or excessive output.

A parent watchdog samples compiler process-group RSS and process count every 200 ms,
killing the group above 1.5 GiB or eight processes. Failure to read monitoring
data aborts execution. This sampled guard is **not** an OS hard memory limit.
It applies to the compiler, not Wasmtime's host JIT. The WASI guest has a hard
64 MiB linear-memory limit, while the local host JIT can use additional memory
within its process timeout; Linux production requires an overall OS hard limit.

Before execution, the parent opens the artifact with `O_NOFOLLOW`, requires a
regular bounded file with the core Wasm header, and copies it into a new private
runtime directory outside compiler access. Its native compilation cache is
created only there by trusted Wasmtime; there is no cross-submission cache or
`--allow-precompiled` input. Each test gets a fresh Wasmtime process, no preopened
directories, no inherited environment, no sockets, and piped JSON stdin/stdout.
Limits: 64 MiB linear memory, 100 million fuel, 750 ms Wasm execution time,
10 seconds including host JIT work, and 256 KiB output. Foundation initialization
needs more than 10 million fuel. Current mobile suites are capped at seven tests
so compilation plus test-process deadlines fit the 119-second transport budget.

The smoke script exercises all 14 Swift reference cases, syntax failure for all
seven tests, an infinite loop in all seven tests, WASI filesystem/environment
denial, and refusal to load a candidate-specified external macro. The separate
native diagnostic uses only synthetic canaries and verifies outside read/write,
toolchain write, runtime-cache read, network and outside-executable denial.

Linux/production compilation deliberately fails closed. Enabling it requires a
verified compiler sandbox with kernel-enforced memory/process limits and a
compatible pinned toolchain image. The local macOS proof does not establish
that nested namespaces or Landlock are enabled in Yandex Serverless Containers.
No production Terraform apply is part of this setup.
