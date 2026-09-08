# Local Kotlin compiler

The verified local path uses the official Kotlin/JS **2.2.21-release-469** compiler,
OpenJDK **25.0.2**, and macOS `sandbox-exec`. It compiles `.kt` source, never runs
candidate JVM code, and sends the resulting standalone JavaScript to QuickJS.
Linux fails closed with `runtime_unavailable`; no native or unisolated fallback
is provided. A production Linux compiler sandbox still needs to be provisioned
and verified before enabling this runtime there.

## Dependency pin

Official archive:
[kotlin-compiler-2.2.21.zip](https://github.com/JetBrains/kotlin/releases/download/v2.2.21/kotlin-compiler-2.2.21.zip)

SHA-256, verified against the
[official checksum](https://github.com/JetBrains/kotlin/releases/download/v2.2.21/kotlin-compiler-2.2.21.zip.sha256):

```text
a623871f1cd9c938946948b70ef9170879f0758043885bbd30c32f024e511714
```

Unzip into a task-local toolchain directory and configure these environment
variables on the runner process:

```text
EDIUM_KOTLIN_HOME=/absolute/path/to/kotlinc
EDIUM_JAVA_HOME=/absolute/path/to/jdk/Contents/Home
```

The runner only forwards `PATH`, `LANG`, and `LC_ALL` to the compiler.
CoreFoundation adds its own `__CF_USER_TEXT_ENCODING` locale variable. JVM
`user.home` and `java.io.tmpdir` point to a fresh per-request job directory.

## Compiler contract

Candidates provide `fun solve(input: Map<String, Any?>): Map<String, Any?>`.
JSON objects become maps, arrays become lists, and numeric input is `Number`
(use `(value as Number).toInt()` when an integer is required). The bridge
preserves strings, booleans, null, nested lists and maps. Return JSON-compatible
maps/lists and scalar values.

`runner/kotlin-compile.js` exposes `compileKotlin(source) -> JavaScript string`.
It accepts at most 64 KiB of source and returns at most 4 MiB of compiled code,
including the fixed `solve` adapter. Both compiler stages share a 45-second
deadline. JVM heap, metaspace, code cache, concurrency, output capture, and
temporary filesystem scope are bounded. The job directory is removed after
success or failure.

The fixed compiler stages are:

```text
org.jetbrains.kotlin.cli.js.K2JSCompiler
  -libraries <kotlinc>/lib/kotlin-stdlib-js.klib
  -Xir-produce-klib-file -ir-output-dir <job> -ir-output-name candidate
  Candidate.kt Bridge.kt

org.jetbrains.kotlin.cli.js.K2JSCompiler
  -libraries <kotlinc>/lib/kotlin-stdlib-js.klib
  -Xir-produce-js -Xir-dce -Xinclude=<job>/candidate.klib
  -module-kind plain -main noCall
  -ir-output-dir <job>/js -ir-output-name ediumCandidate
```

The stdlib is linked into the artifact. The exported function is
`ediumCandidate.runCase(inputJson)`, and the host adds this adapter before the
artifact is passed to QuickJS:

```js
function solve(input) {
  return JSON.parse(ediumCandidate.runCase(JSON.stringify(input)))
}
```

## Isolation and proof

The deny-by-default macOS profile permits read-only access to the configured
compiler/JDK and required system libraries. Only the fresh job directory is
writable. Network and unrelated executable access remain denied. JVM startup
requires the system `com.apple.system.opendirectoryd.libinfo` service and the
public `/etc/passwd` account file. Reading the root directory itself is limited
to `(require-all (literal "/") (vnode-type DIRECTORY))`: Apple documents this
`openat` bootstrap need in `/System/Library/Sandbox/Profiles/dyld-support.sb`.
That rule does not permit reading descendant files.

Run from the backend repository root on macOS:

```sh
node infra/terraform/accounts/join-site/dev/kotlin-toolchain-proof.js /absolute/path/to/kotlinc /absolute/path/to/jdk/Contents/Home
node infra/terraform/accounts/join-site/dev/kotlin-sandbox-proof.js /absolute/path/to/kotlinc /absolute/path/to/jdk/Contents/Home
```

The first compiles a trusted sample and checks its output in the same 64 MiB,
512 KiB stack, 750 ms QuickJS environment used for contest cases. The original
proof produced a 120,236-byte artifact in approximately five seconds. The
second probes synthetic files only and verifies outside reads/writes,
toolchain writes, network, and unrelated executables are denied; sanitized
environment and writes within the job are allowed. If run inside an outer
sandbox that disallows applying Seatbelt, the compiler reports
`runtime_unavailable` instead of misreporting a candidate compilation error.

Primary references: [compiler options](https://kotlinlang.org/docs/compiler-reference.html),
[plain JavaScript modules](https://kotlinlang.org/docs/js-modules.html), and
[Kotlin exports](https://kotlinlang.org/docs/js-to-kotlin-interop.html).
