'use strict'

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawn, execFile } = require('node:child_process')

const SWIFT_VERSION = '6.3.1'
const SWIFT_SDK_SHA256 = 'bd47baa20771f366d8beed7970afaa30742b2210097afd15f85427226d8f4cf2'
const WASMTIME_VERSION = '48.0.1'
const WASMTIME_MACOS_ARM_SHA256 = '88cc08b395fbfb960b99f355a81224af975679b8a5f4b74a51d59e5e34b20dcd'
const MAX_SOURCE_BYTES = 65536
const MAX_WASM_BYTES = 128 * 1024 * 1024
const MAX_RESULT_BYTES = 262144
const COMPILE_TIMEOUT_MS = 45000
const TEST_PROCESS_TIMEOUT_MS = 10000
const PROCESS_GROUP_RSS_LIMIT = 1536 * 1024 * 1024

// No directories, sockets, inherited environment, or native extension imports
// are granted to candidate modules. The default cache stays disabled; runSwift
// enables only a fresh host-owned cache outside compiler-writable directories.
const WASMTIME_ARGS = Object.freeze([
  'run', '-C', 'cache=no',
  '-W', 'fuel=100000000,max-memory-size=67108864,max-wasm-stack=524288,timeout=750ms,threads=no',
  '-S', 'inherit-env=no,inherit-network=no,allow-ip-name-lookup=no,tcp=no,udp=no,http=no,threads=no',
])

function failure(code, message) {
  const error = new Error(message)
  error.code = code
  return error
}

function unavailable() {
  return failure('runtime_unavailable', 'Swift пока недоступен: нужен настроенный изолированный компилятор и WebAssembly runtime.')
}

function quoted(value) {
  if (typeof value !== 'string' || /[\r\n\0]/.test(value)) throw unavailable()
  return JSON.stringify(value)
}

function compilerProfile({ toolchainDirectory, sdkDirectory, jobDirectory }) {
  const executables = ['swiftc', 'swift-frontend', 'clang', 'wasm-ld'].map((name) => fs.realpathSync(path.join(toolchainDirectory, 'usr/bin', name)))
  return `(version 1)
(deny default)
(allow process-exec ${executables.map((executable) => `(literal ${quoted(executable)})`).join(' ')})
(allow process-fork)
(allow signal (target self))
(allow sysctl-read)
(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo"))
(allow file-read-metadata)
; dyld opens the root directory as an openat base; its descendants stay denied.
(allow file-read-data (require-all (literal "/") (vnode-type DIRECTORY)))
(allow file-read* (subpath ${quoted(toolchainDirectory)}) (subpath ${quoted(sdkDirectory)})
  (subpath "/System/Library") (subpath "/usr/lib") (subpath "/private/var/db/dyld")
  (subpath "/System/Volumes/Preboot/Cryptexes/OS")
  (literal "/private/etc/passwd")
  (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random")
  (subpath ${quoted(jobDirectory)}))
(allow file-write* (subpath ${quoted(jobDirectory)}) (literal "/dev/null"))
`
}

// Kill the entire compiler process group, including swift-frontend and wasm-ld.
// Neither a slow type checker nor an output flood may outlive the request.
function boundedProcess(executable, args, { cwd, env, timeout, maxBytes, input = '', monitorMemory = false }) {
  return new Promise((resolve) => {
    const child = spawn(executable, args, { cwd, env, detached: true, stdio: ['pipe', 'pipe', 'pipe'] })
    const stdout = []
    const stderr = []
    let bytes = 0
    let reason = null
    let spawnError = null
    let closed = false
    let checkingMemory = false
    const kill = (cause) => {
      if (reason) return
      reason = cause
      if (child.pid) {
        try { process.kill(-child.pid, 'SIGKILL') } catch { child.kill('SIGKILL') }
      }
    }
    const timer = setTimeout(() => kill('timeout'), timeout)
    // Seatbelt controls capabilities, not allocation. For this local macOS
    // bridge, monitor the whole compiler group as an additional DoS guard.
    // This is a sampled RSS guard, not a substitute for Linux cgroup limits.
    const memoryTimer = monitorMemory ? setInterval(() => {
      if (closed || checkingMemory || !child.pid) return
      checkingMemory = true
      execFile('/bin/ps', ['-axo', 'pgid=,rss='], { env: {}, timeout: 1000, maxBuffer: 1024 * 1024 }, (error, listing) => {
        checkingMemory = false
        if (closed) return
        if (error) return kill('monitor_unavailable')
        let rss = 0
        let processes = 0
        for (const line of listing.split('\n')) {
          const fields = line.trim().split(/\s+/)
          if (Number(fields[0]) === child.pid) {
            rss += Number(fields[1]) * 1024
            processes += 1
          }
        }
        if (rss > PROCESS_GROUP_RSS_LIMIT || processes > 8) kill('memory_limit')
      })
    }, 200) : null
    const collect = (target) => (chunk) => {
      bytes += chunk.length
      if (bytes > maxBytes) kill('output_limit')
      else target.push(chunk)
    }
    child.stdout.on('data', collect(stdout))
    child.stderr.on('data', collect(stderr))
    child.stdin.on('error', () => {})
    child.on('error', (error) => { spawnError = error })
    child.on('close', (status, signal) => {
      closed = true
      clearTimeout(timer)
      clearInterval(memoryTimer)
      resolve({ status, signal, reason, error: spawnError, stdout: Buffer.concat(stdout).toString('utf8'), stderr: Buffer.concat(stderr).toString('utf8') })
    })
    child.stdin.end(input)
  })
}

function configuration(options = {}) {
  const rawToolchain = options.toolchainDirectory || process.env.EDIUM_SWIFT_HOME
  const rawSdk = options.sdkDirectory || process.env.EDIUM_SWIFT_SDK
  const rawWasmtime = options.wasmtimeExecutable || process.env.EDIUM_WASMTIME
  const supportedHost = (process.platform === 'darwin' && process.arch === 'arm64')
    || (process.platform === 'linux' && process.arch === 'x64')
  if (!supportedHost || !rawToolchain || !rawSdk || !rawWasmtime) throw unavailable()
  try {
    const toolchainDirectory = fs.realpathSync(rawToolchain)
    const sdkDirectory = fs.realpathSync(rawSdk)
    const wasmtimeExecutable = fs.realpathSync(rawWasmtime)
    const compiler = path.join(toolchainDirectory, 'usr/bin/swiftc')
    const resources = path.join(sdkDirectory, 'swift.xctoolchain/usr/lib/swift_static')
    const executables = [compiler, path.join(toolchainDirectory, 'usr/bin/wasm-ld'), wasmtimeExecutable]
    if (process.platform === 'darwin') executables.push('/usr/bin/sandbox-exec')
    for (const executable of executables) {
      fs.accessSync(executable, fs.constants.X_OK)
    }
    fs.accessSync(path.join(resources, 'wasi/static-executable-args.lnk'), fs.constants.R_OK)
    fs.accessSync(path.join(sdkDirectory, 'WASI.sdk'), fs.constants.R_OK)
    return { toolchainDirectory, sdkDirectory, wasmtimeExecutable, compiler, resources }
  } catch {
    throw unavailable()
  }
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable)
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]))
  return value
}

async function runSwift(source, suite, options = {}) {
  if (typeof source !== 'string' || Buffer.byteLength(source) > MAX_SOURCE_BYTES) throw new Error('invalid_source')
  // Current mobile tasks have seven tests. Revisit the request time budget
  // before introducing a longer suite: compilation 45s + 7 * 10s < API 119s.
  if (!Array.isArray(suite) || !suite.length || suite.length > 7) throw new Error('unknown_task')
  const config = configuration(options)
  const compileDeadline = Date.now() + COMPILE_TIMEOUT_MS
  const jobDirectory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'edium-swift-job-'))
  let runtimeDirectory
  try {
    const profile = process.platform === 'darwin' ? compilerProfile({ ...config, jobDirectory }) : null
    const compilerProcess = args => process.platform === 'darwin'
      ? ['/usr/bin/sandbox-exec', ['-p', profile, config.compiler, ...args]]
      : [config.compiler, args]
    const environment = { PATH: path.join(config.toolchainDirectory, 'usr/bin') + ':/usr/bin:/bin', TMPDIR: jobDirectory, LANG: 'en_US.UTF-8' }
    const [versionExecutable, versionArgs] = compilerProcess(['--version'])
    const compilerVersion = await boundedProcess(versionExecutable, versionArgs, {
      cwd: jobDirectory, env: environment, timeout: 5000, maxBytes: 16384,
    })
    const runtimeVersion = await boundedProcess(config.wasmtimeExecutable, ['--version'], {
      cwd: jobDirectory, env: {}, timeout: 5000, maxBytes: 16384,
    })
    if (compilerVersion.status !== 0 || !compilerVersion.stdout.includes(`Swift version ${SWIFT_VERSION} (swift-${SWIFT_VERSION}-RELEASE)`)
      || runtimeVersion.status !== 0 || !runtimeVersion.stdout.startsWith(`wasmtime ${WASMTIME_VERSION} (`)) throw unavailable()
    const candidate = path.join(jobDirectory, 'Candidate.swift')
    const bridge = path.join(jobDirectory, 'Bridge.swift')
    const artifact = path.join(jobDirectory, 'candidate.wasm')
    fs.writeFileSync(candidate, source)
    fs.copyFileSync(path.join(__dirname, 'swift-bridge.swift'), bridge)
    const [compileExecutable, compileArgs] = compilerProcess([
      '-target', 'wasm32-unknown-wasip1', '-sdk', path.join(config.sdkDirectory, 'WASI.sdk'),
      '-resource-dir', config.resources, '-module-cache-path', path.join(jobDirectory, 'module-cache'),
      '-static-stdlib', '-parse-as-library', '-O', '-Xlinker', '--strip-debug',
      '-Xclang-linker', '-resource-dir=' + path.join(config.sdkDirectory, 'swift.xctoolchain/usr/lib/clang'),
      candidate, bridge, '-o', artifact,
    ])
    const compilation = await boundedProcess(compileExecutable, compileArgs, {
      cwd: jobDirectory, env: environment,
      timeout: Math.max(1, compileDeadline - Date.now()), maxBytes: 1024 * 1024, monitorMemory: true,
    })
    if (compilation.error || compilation.status !== 0) {
      const diagnostic = `${compilation.stderr}\n${compilation.stdout}`
        .replaceAll(jobDirectory, '<contest>').replaceAll(config.toolchainDirectory, '<compiler>').replaceAll(config.sdkDirectory, '<sdk>').trim().slice(0, 6000)
      if (compilation.reason === 'monitor_unavailable' || ['ENOENT', 'EPERM', 'EACCES'].includes(compilation.error?.code) || /sandbox-exec:|Library not loaded:|No available targets are compatible|unable to load standard library/.test(diagnostic)) throw unavailable()
      throw failure('compilation_failed', compilation.reason === 'timeout' ? 'Компиляция заняла слишком много времени.'
        : compilation.reason === 'memory_limit' ? 'Компиляция превысила ограничение памяти.'
        : compilation.reason === 'output_limit' ? 'Компилятор выдал слишком много сообщений.' : diagnostic || 'Не удалось скомпилировать Swift. Проверь код и ограничения памяти.')
    }
    let wasm
    const descriptor = fs.openSync(artifact, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK)
    try {
      const stat = fs.fstatSync(descriptor)
      if (!stat.isFile() || stat.size > MAX_WASM_BYTES || stat.size < 8) throw failure('compilation_failed', 'Некорректный размер скомпилированного кода.')
      wasm = Buffer.alloc(stat.size)
      let offset = 0
      while (offset < wasm.length) {
        const count = fs.readSync(descriptor, wasm, offset, wasm.length - offset, offset)
        if (!count) break
        offset += count
      }
      if (offset !== wasm.length || fs.fstatSync(descriptor).size !== stat.size
        || !wasm.subarray(0, 8).equals(Buffer.from([0, 97, 115, 109, 1, 0, 0, 0]))) {
        throw failure('compilation_failed', 'Компилятор не создал допустимый WebAssembly-модуль.')
      }
    } finally {
      fs.closeSync(descriptor)
    }
    // Created only after the compiler exits, outside its Seatbelt write scope.
    // Neither a compromised compiler nor the WASI guest can preseed the native
    // compilation cache. Wasmtime parses the copied Wasm module normally; no
    // --allow-precompiled/deserialization option is accepted from a candidate.
    runtimeDirectory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'edium-swift-runtime-'))
    const runtimeArtifact = path.join(runtimeDirectory, 'candidate.wasm')
    const cacheConfig = path.join(runtimeDirectory, 'cache.toml')
    fs.writeFileSync(runtimeArtifact, wasm, { flag: 'wx', mode: 0o600 })
    fs.writeFileSync(cacheConfig, `[cache]\ndirectory = ${JSON.stringify(path.join(runtimeDirectory, 'cache'))}\n`, { flag: 'wx', mode: 0o600 })
    const results = []
    for (const test of suite) {
      const execution = await boundedProcess(config.wasmtimeExecutable, [...WASMTIME_ARGS, '-C', `cache=yes,cache-config=${cacheConfig}`, runtimeArtifact], {
        cwd: runtimeDirectory, env: {}, timeout: TEST_PROCESS_TIMEOUT_MS, maxBytes: MAX_RESULT_BYTES,
        input: JSON.stringify(test.input) + '\n',
      })
      if (execution.reason === 'monitor_unavailable' || execution.error || /unexpected argument|invalid value|unknown (option|wasm|wasi)|failed to (?:read|create) cache|failed to parse config/.test(execution.stderr)) throw unavailable()
      let message = ''
      let passed = false
      if (execution.reason === 'timeout' || /all fuel consumed|interrupt|wasm trap:.*unreachable/.test(execution.stderr)) {
        message = 'Превышено время выполнения или программа аварийно завершилась.'
      } else if (execution.reason === 'memory_limit') {
        message = 'Превышено ограничение памяти среды выполнения.'
      } else if (execution.reason === 'output_limit') {
        message = 'Результат слишком большой.'
      } else if (execution.status !== 0) {
        message = 'Не удалось выполнить тест. Проверь код, типы данных и ограничения памяти.'
      } else {
        try {
          const actual = JSON.parse(execution.stdout)
          passed = JSON.stringify(stable(actual)) === JSON.stringify(stable(test.expected))
          if (!passed) message = `Ожидалось ${JSON.stringify(test.expected)}, получено ${JSON.stringify(actual)}`.slice(0, 500)
        } catch {
          message = 'solve должна вернуть JSON-совместимый объект. Удали отладочный print из решения.'
        }
      }
      results.push({ name: test.name, passed, message })
    }
    return results
  } finally {
    // This exact private job directory is generated above and never comes from
    // candidate input. Compiler/runtime children have exited before cleanup.
    fs.rmSync(jobDirectory, { recursive: true, force: true })
    if (runtimeDirectory) fs.rmSync(runtimeDirectory, { recursive: true, force: true })
  }
}

module.exports = { runSwift, compilerProfile, WASMTIME_ARGS, SWIFT_VERSION, SWIFT_SDK_SHA256, WASMTIME_VERSION, WASMTIME_MACOS_ARM_SHA256 }
