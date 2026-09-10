'use strict'

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawn, execFile } = require('node:child_process')

const GO_VERSION = 'go1.26.1'
const WASMTIME_VERSION = '48.0.1'
const MAX_SOURCE_BYTES = 65536
const MAX_WASM_BYTES = 32 * 1024 * 1024
const MAX_RESULT_BYTES = 262144
const COMPILE_TIMEOUT_MS = 45000
const TEST_TIMEOUT_MS = 10000
const PROCESS_GROUP_RSS_LIMIT = 1536 * 1024 * 1024
const WASMTIME_ARGS = Object.freeze([
  'run', '-C', 'cache=no,parallel-compilation=no',
  '-W', 'fuel=100000000,max-memory-size=67108864,max-wasm-stack=524288,timeout=750ms,threads=no',
  '-S', 'inherit-env=no,inherit-network=no,allow-ip-name-lookup=no,tcp=no,udp=no,http=no,threads=no',
])

function failure(code, message) { return Object.assign(new Error(message), { code }) }
function unavailable() {
  return failure('runtime_unavailable', 'Go пока недоступен: нужен настроенный изолированный компилятор и WebAssembly runtime.')
}
function quote(value) {
  if (typeof value !== 'string' || /[\r\n\0]/.test(value)) throw unavailable()
  return JSON.stringify(value)
}

function compilerProfile({ goDirectory, jobDirectory }) {
  const tools = ['bin/go', 'pkg/tool/darwin_arm64/compile', 'pkg/tool/darwin_arm64/asm', 'pkg/tool/darwin_arm64/link']
    .map(name => fs.realpathSync(path.join(goDirectory, name)))
  return `(version 1)
(deny default)
(allow process-exec ${tools.map(tool => `(literal ${quote(tool)})`).join(' ')})
(allow process-fork)
(allow signal (target self))
(allow sysctl-read)
(allow file-read-metadata)
; libignition opens only the root directory as an openat base.
(allow file-read-data (require-all (literal "/") (vnode-type DIRECTORY)))
(allow file-read* (subpath ${quote(goDirectory)}) (subpath ${quote(jobDirectory)})
  (subpath "/System/Library") (subpath "/usr/lib") (subpath "/private/var/db/dyld")
  (subpath "/System/Volumes/Preboot/Cryptexes/OS")
  (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))
(allow file-write* (subpath ${quote(jobDirectory)}) (literal "/dev/null"))
`
}

function boundedProcess(executable, args, { cwd, env, timeout, maxBytes, input = '', monitorMemory = false }) {
  return new Promise(resolve => {
    const child = spawn(executable, args, { cwd, env, detached: true, stdio: ['pipe', 'pipe', 'pipe'] })
    const stdout = [], stderr = []
    let bytes = 0, reason = null, spawnError = null, closed = false, checkingMemory = false
    function kill(cause) {
      if (reason) return
      reason = cause
      if (child.pid) {
        try { process.kill(-child.pid, 'SIGKILL') } catch { child.kill('SIGKILL') }
      }
    }
    const timer = setTimeout(() => kill('timeout'), timeout)
    const memoryTimer = monitorMemory ? setInterval(() => {
      if (closed || checkingMemory || !child.pid) return
      checkingMemory = true
      execFile('/bin/ps', ['-axo', 'pgid=,rss='], { env: {}, timeout: 1000, maxBuffer: 1024 * 1024 }, (error, listing) => {
        checkingMemory = false
        if (closed) return
        if (error) return kill('monitor_unavailable')
        let rss = 0, processes = 0
        for (const line of listing.split('\n')) {
          const fields = line.trim().split(/\s+/)
          if (Number(fields[0]) === child.pid) { rss += Number(fields[1]) * 1024; processes += 1 }
        }
        if (rss > PROCESS_GROUP_RSS_LIMIT || processes > 8) kill('memory_limit')
      })
    }, 200) : null
    const collect = target => chunk => {
      bytes += chunk.length
      if (bytes > maxBytes) kill('output_limit')
      else target.push(chunk)
    }
    child.stdout.on('data', collect(stdout))
    child.stderr.on('data', collect(stderr))
    child.stdin.on('error', () => {})
    child.on('error', error => { spawnError = error })
    child.on('close', (status, signal) => {
      closed = true
      clearTimeout(timer); clearInterval(memoryTimer)
      resolve({ status, signal, reason, error: spawnError, stdout: Buffer.concat(stdout).toString('utf8'), stderr: Buffer.concat(stderr).toString('utf8') })
    })
    child.stdin.end(input)
  })
}

function configuration(options = {}) {
  const rawGo = options.goDirectory || process.env.EDIUM_GO_HOME
  const rawWasmtime = options.wasmtimeExecutable || process.env.EDIUM_WASMTIME
  if (process.platform !== 'darwin' || process.arch !== 'arm64' || !rawGo || !rawWasmtime) throw unavailable()
  try {
    const goDirectory = fs.realpathSync(rawGo)
    const wasmtimeExecutable = fs.realpathSync(rawWasmtime)
    fs.accessSync('/usr/bin/sandbox-exec', fs.constants.X_OK)
    fs.accessSync(wasmtimeExecutable, fs.constants.X_OK)
    if (fs.readFileSync(path.join(goDirectory, 'VERSION'), 'utf8').split('\n')[0] !== GO_VERSION) throw unavailable()
    for (const name of ['bin/go', 'pkg/tool/darwin_arm64/compile', 'pkg/tool/darwin_arm64/asm', 'pkg/tool/darwin_arm64/link']) fs.accessSync(path.join(goDirectory, name), fs.constants.X_OK)
    return { goDirectory, wasmtimeExecutable, compiler: path.join(goDirectory, 'bin/go') }
  } catch { throw unavailable() }
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable)
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]))
  return value
}

async function runGo(source, suite, options = {}) {
  if (typeof source !== 'string' || Buffer.byteLength(source) > MAX_SOURCE_BYTES) throw new Error('invalid_source')
  if (!Array.isArray(suite) || !suite.length || suite.length > 7) throw new Error('unknown_task')
  const config = configuration(options)
  const deadline = Date.now() + COMPILE_TIMEOUT_MS
  const jobDirectory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'edium-go-job-'))
  let runtimeDirectory
  try {
    for (const name of ['cache', 'tmp', 'gopath', 'modules']) fs.mkdirSync(path.join(jobDirectory, name))
    const profile = compilerProfile({ ...config, jobDirectory })
    // No inherited credentials, Go user config, global cache, module resolution,
    // toolchain downloads, C compiler, generate directives, or native go run.
    const environment = {
      PATH: path.join(config.goDirectory, 'bin') + ':/usr/bin:/bin', LANG: 'en_US.UTF-8', TMPDIR: path.join(jobDirectory, 'tmp'),
      GOROOT: config.goDirectory, GOENV: 'off', GOTOOLCHAIN: 'local', GOWORK: 'off', GO111MODULE: 'off',
      GOOS: 'wasip1', GOARCH: 'wasm', CGO_ENABLED: '0', GOPROXY: 'off', GOSUMDB: 'off',
      GOCACHE: path.join(jobDirectory, 'cache'), GOPATH: path.join(jobDirectory, 'gopath'),
      GOMODCACHE: path.join(jobDirectory, 'modules'), GOTMPDIR: path.join(jobDirectory, 'tmp'),
      GOMAXPROCS: '2', GOMEMLIMIT: '512MiB',
    }
    const compilerVersion = await boundedProcess('/usr/bin/sandbox-exec', ['-p', profile, config.compiler, 'version'], { cwd: jobDirectory, env: environment, timeout: 5000, maxBytes: 16384 })
    const runtimeVersion = await boundedProcess(config.wasmtimeExecutable, ['--version'], { cwd: jobDirectory, env: {}, timeout: 5000, maxBytes: 16384 })
    if (compilerVersion.status !== 0 || compilerVersion.stdout.trim() !== `go version ${GO_VERSION} darwin/arm64`
      || runtimeVersion.status !== 0 || !runtimeVersion.stdout.startsWith(`wasmtime ${WASMTIME_VERSION} (`)) throw unavailable()
    const candidate = path.join(jobDirectory, 'Candidate.go')
    const bridge = path.join(jobDirectory, 'Bridge.go')
    const artifact = path.join(jobDirectory, 'candidate.wasm')
    fs.writeFileSync(candidate, source)
    fs.copyFileSync(path.join(__dirname, 'go-bridge.go'), bridge)
    const compilation = await boundedProcess('/usr/bin/sandbox-exec', ['-p', profile, config.compiler,
      'build', '-p=2', '-trimpath', '-buildvcs=false', '-ldflags=-s -w', '-o', artifact, candidate, bridge,
    ], { cwd: jobDirectory, env: environment, timeout: Math.max(1, deadline - Date.now()), maxBytes: 1024 * 1024, monitorMemory: true })
    if (compilation.error || compilation.status !== 0) {
      const message = `${compilation.stderr}\n${compilation.stdout}`.replaceAll(jobDirectory, '<contest>').replaceAll(config.goDirectory, '<compiler>').trim().slice(0, 6000)
      if (compilation.reason === 'monitor_unavailable' || ['ENOENT', 'EACCES', 'EPERM'].includes(compilation.error?.code) || /sandbox-exec:|cannot find GOROOT|no such tool /.test(message)) throw unavailable()
      throw failure('compilation_failed', compilation.reason === 'timeout' ? 'Компиляция заняла слишком много времени.'
        : compilation.reason === 'memory_limit' ? 'Компиляция превысила ограничение памяти.'
        : compilation.reason === 'output_limit' ? 'Компилятор выдал слишком много сообщений.' : message || 'Не удалось скомпилировать Go.')
    }
    const descriptor = fs.openSync(artifact, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK)
    let wasm
    try {
      const metadata = fs.fstatSync(descriptor)
      if (!metadata.isFile() || metadata.size < 8 || metadata.size > MAX_WASM_BYTES) throw failure('compilation_failed', 'Некорректный размер скомпилированного кода.')
      wasm = Buffer.alloc(metadata.size)
      let offset = 0
      while (offset < wasm.length) {
        const count = fs.readSync(descriptor, wasm, offset, wasm.length - offset, offset)
        if (!count) break
        offset += count
      }
      if (offset !== wasm.length || fs.fstatSync(descriptor).size !== metadata.size || !wasm.subarray(0, 8).equals(Buffer.from([0, 97, 115, 109, 1, 0, 0, 0]))) throw failure('compilation_failed', 'Компилятор не создал допустимый WebAssembly-модуль.')
    } finally { fs.closeSync(descriptor) }
    runtimeDirectory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'edium-go-runtime-'))
    const runtimeArtifact = path.join(runtimeDirectory, 'candidate.wasm')
    const cacheConfig = path.join(runtimeDirectory, 'cache.toml')
    fs.writeFileSync(runtimeArtifact, wasm, { flag: 'wx', mode: 0o600 })
    fs.writeFileSync(cacheConfig, `[cache]\ndirectory = ${JSON.stringify(path.join(runtimeDirectory, 'cache'))}\n`, { flag: 'wx', mode: 0o600 })
    const results = []
    for (const test of suite) {
      const execution = await boundedProcess(config.wasmtimeExecutable, [...WASMTIME_ARGS, '-C', `cache=yes,cache-config=${cacheConfig}`, runtimeArtifact], {
        cwd: runtimeDirectory, env: {}, timeout: TEST_TIMEOUT_MS, maxBytes: MAX_RESULT_BYTES, input: JSON.stringify(test.input) + '\n',
      })
      if (execution.error || /unexpected argument|invalid value|unknown (option|wasm|wasi)|failed to (?:read|create) cache|failed to parse config/.test(execution.stderr)) throw unavailable()
      let passed = false, message = ''
      if (execution.reason === 'timeout' || /all fuel consumed|interrupt/.test(execution.stderr)) message = 'Превышено время выполнения.'
      else if (execution.reason === 'output_limit') message = 'Результат слишком большой.'
      else if (execution.status !== 0) message = 'Не удалось выполнить тест. Проверь код, типы данных и ограничения памяти.'
      else {
        try {
          const actual = JSON.parse(execution.stdout)
          passed = JSON.stringify(stable(actual)) === JSON.stringify(stable(test.expected))
          if (!passed) message = `Ожидалось ${JSON.stringify(test.expected)}, получено ${JSON.stringify(actual)}`.slice(0, 500)
        } catch { message = 'Solve должна вернуть JSON-объект. Удали отладочный вывод из решения.' }
      }
      results.push({ name: test.name, passed, message })
    }
    return results
  } finally {
    fs.rmSync(jobDirectory, { recursive: true, force: true })
    if (runtimeDirectory) fs.rmSync(runtimeDirectory, { recursive: true, force: true })
  }
}

module.exports = { runGo, compilerProfile, GO_VERSION, WASMTIME_VERSION, WASMTIME_ARGS }
