# Local Go contest runtime

The verified path compiles Go **1.26.1** with `GOOS=wasip1 GOARCH=wasm`, then
executes each test in a fresh Wasmtime **48.0.1** process. Candidates never run
as native Go executables. This local macOS ARM implementation requires its
deny-default Seatbelt compiler sandbox; unsupported hosts fail closed with
`runtime_unavailable`.

An existing Homebrew Go installation was used without changing or downloading
it: `/opt/homebrew/Cellar/go/1.26.1/libexec`. Both the `VERSION` file and compiler
version output are checked. The Wasmtime version is checked before compilation.
The existing official Wasmtime release and checksum are documented in
[SWIFT-TOOLCHAIN.md](./SWIFT-TOOLCHAIN.md).

## Configuration and candidate contract

Set these variables on the local demo or smoke command only; no shell profile
or global Go configuration needs to change:

```sh
export EDIUM_GO_HOME="/absolute/path/to/go/libexec"
export EDIUM_WASMTIME="/absolute/path/to/wasmtime"
node dev/go-runtime-smoke.js
node dev/go-sandbox-proof.js
```

Run from `infra/terraform/accounts/join-site`. Candidates provide `package main`
and this function, without their own `main`:

```go
package main

func Solve(input map[string]any) map[string]any {
    return input
}
```

The fixed bridge decodes JSON numbers as `float64`, objects as `map[string]any`,
arrays as `[]any`, and preserves strings, booleans, and null. Return a JSON
object. The Go standard library is available; external modules and C imports
are not. Debug output to stdout interferes with the JSON result.

`runner/go-runtime.js` exports `async runGo(source, suite) -> test[]`. The normal
dispatcher handles compilation errors by reporting 0/N with a diagnostic;
configuration or sandbox startup failures remain `runtime_unavailable`.

## Build and resource boundaries

The only build command is:

```text
go build -p=2 -trimpath -buildvcs=false -ldflags="-s -w"
  -o <job>/candidate.wasm <job>/Candidate.go <job>/Bridge.go
```

The compiler receives a fixed environment: `GOOS=wasip1`, `GOARCH=wasm`,
`CGO_ENABLED=0`, `GOENV=off`, `GOTOOLCHAIN=local`, `GOWORK=off`,
`GO111MODULE=off`, `GOPROXY=off`, `GOSUMDB=off`, `GOMAXPROCS=2`, and
`GOMEMLIMIT=512MiB`. Go build cache, GOPATH, module cache, and temporary
directories all live inside a fresh private job directory. Credentials and
user configuration variables are not inherited. `go generate`, `go run`,
`go test`, toolchain switching, and dependency downloads are never invoked.

The compiler can execute only the canonical Go, compile, asm, and link binaries
from the configured read-only toolchain. It can read its own job, the toolchain,
and required system libraries; only the job is writable. Network and unrelated
executables are denied. Both version probes and compilation share a 45-second
deadline. A sampled 200 ms watchdog kills the entire process group above
1.5 GiB RSS or eight processes, or if monitoring fails. Compiler stdout/stderr
are bounded to 1 MiB. This RSS watchdog is not an OS hard memory limit.

The parent opens the compiler artifact with `O_NOFOLLOW`, requires a regular
file of at most 32 MiB, and checks the core WebAssembly header and stable size
before copying it into a new host-owned runtime directory. The runtime cache
exists only there, outside the compiler's write scope. No candidate-controlled
precompiled native artifact or `--allow-precompiled` option is accepted.

Each test gets fresh WASI state, piped JSON input/output, no preopened host
directory, no inherited environment, and no network. Limits: 64 MiB linear
memory, 512 KiB Wasm stack, 100 million fuel, 750 ms guest execution, 10 seconds
including host compilation, and 256 KiB output. Parallel host compilation is
disabled. Up to seven cases plus compilation fit a 115-second budget. Cases
continue after a candidate timeout or trap. All job and runtime files are
removed after completion.

## Verified behavior

The smoke checks all 21 reference cases for webhook receipts, retry decisions,
and quota reservations through the real dispatcher (7/7 for each task), seven
JSON round trips including special object keys, an actionable compiler error,
interruption of seven infinite loops, WASI filesystem/network/environment
denial, and refusal of an unavailable external package without downloading it.
The separate fixed native diagnostic probes the compiler profile using only
synthetic canaries: outside reads/writes, toolchain writes, runtime-cache reads,
network and unrelated executables are denied; job writes remain allowed.

Primary references: [Go WASI support](https://go.dev/blog/wasi),
[Go WebAssembly documentation](https://go.dev/wiki/WebAssembly), and
[Go 1.26 release notes](https://go.dev/doc/go1.26).
