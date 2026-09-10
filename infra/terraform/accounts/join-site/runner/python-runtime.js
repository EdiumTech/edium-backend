'use strict'

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawn } = require('node:child_process')

const PYTHON_VERSION = '3.12.0'
const WASMTIME_PYTHON_VERSION = '48.0.0'
const MAX_SOURCE_BYTES = 65536
const MAX_REQUEST_BYTES = 2 * 1024 * 1024
const PROCESS_TIMEOUT_MS = 45000

function unavailable() {
  const error = new Error('Python пока недоступен: нужен настроенный изолированный CPython WebAssembly runtime.')
  error.code = 'runtime_unavailable'
  return error
}

function configuration(options = {}) {
  // This verified local prototype is deliberately not enabled in the Linux
  // production image: that requires a current interpreter and host limits.
  if (process.platform !== 'darwin') throw unavailable()
  try {
    const hostPython = fs.realpathSync(options.hostPython || process.env.EDIUM_PYTHON_HOST || '')
    const runtimeDirectory = fs.realpathSync(options.runtimeDirectory || process.env.EDIUM_PYTHON_WASI_HOME || '')
    const bindingsDirectory = fs.realpathSync(options.bindingsDirectory || process.env.EDIUM_WASMTIME_PYTHON_PATH || '')
    fs.accessSync(hostPython, fs.constants.X_OK)
    fs.accessSync(path.join(runtimeDirectory, 'bin/python-3.12.0.wasm'), fs.constants.R_OK)
    fs.accessSync(path.join(bindingsDirectory, 'wasmtime-48.0.0.dist-info/METADATA'), fs.constants.R_OK)
    return { hostPython, runtimeDirectory, bindingsDirectory }
  } catch {
    throw unavailable()
  }
}

function boundedHost(executable, args, cwd, input) {
  return new Promise((resolve) => {
    // Only this trusted helper is native. -I ignores Python environment and
    // user-site customization. No candidate code enters executable arguments.
    const child = spawn(executable, args, { cwd, env: {}, detached: true, stdio: ['pipe', 'pipe', 'pipe'] })
    const stdout = []
    let bytes = 0
    let reason = null
    const kill = (cause) => {
      if (reason) return
      reason = cause
      if (child.pid) {
        try { process.kill(-child.pid, 'SIGKILL') } catch { child.kill('SIGKILL') }
      }
    }
    const timer = setTimeout(() => kill('timeout'), PROCESS_TIMEOUT_MS)
    child.stdout.on('data', (chunk) => {
      bytes += chunk.length
      if (bytes > 4 * 1024 * 1024) kill('output_limit')
      else stdout.push(chunk)
    })
    // Host diagnostics are never forwarded; guest diagnostics are already
    // bounded inside the helper and transmitted in its JSON protocol.
    child.stderr.on('data', chunk => { bytes += chunk.length; if (bytes > 4 * 1024 * 1024) kill('output_limit') })
    child.stdin.on('error', () => {})
    child.on('error', () => { reason = 'spawn_error' })
    child.on('close', (status) => {
      clearTimeout(timer)
      resolve({ status, reason, stdout: Buffer.concat(stdout).toString('utf8') })
    })
    child.stdin.end(input)
  })
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable)
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]))
  return value
}

async function runPython(source, suite, options = {}) {
  if (typeof source !== 'string' || Buffer.byteLength(source) > MAX_SOURCE_BYTES) throw new Error('invalid_source')
  if (!Array.isArray(suite) || !suite.length || suite.length > 7) throw new Error('unknown_task')
  const config = configuration(options)
  const input = JSON.stringify({ source, inputs: suite.map(test => test.input) })
  if (Buffer.byteLength(input) > MAX_REQUEST_BYTES) throw unavailable()
  const jobDirectory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'edium-python-job-'))
  try {
    const execution = await boundedHost(config.hostPython, ['-I', path.join(__dirname, 'python-wasi-host.py'),
      '--bindings', config.bindingsDirectory, '--runtime', config.runtimeDirectory, '--job', jobDirectory], jobDirectory, input)
    if (execution.reason || execution.status !== 0) throw unavailable()
    let response
    try { response = JSON.parse(execution.stdout) } catch { throw unavailable() }
    if (response.error || !Array.isArray(response.results) || response.results.length !== suite.length) throw unavailable()
    return suite.map((test, index) => {
      const result = response.results[index]
      let passed = false
      let message = ''
      if (result.error === 'timeout') message = 'Превышено время выполнения.'
      else if (result.error === 'output_limit') message = 'Результат или отладочный вывод слишком большой.'
      else if (result.error === 'candidate_error') {
        const diagnostic = String(result.diagnostic || 'Проверь код и ограничение памяти.').trim()
        // The API keeps the first 500 characters. Tracebacks put the useful
        // exception last, so preserve that tail within the API's whole budget.
        message = `Ошибка Python. ${diagnostic.length > 450 ? '…' : ''}${diagnostic.slice(-450)}`
      }
      else if (result.error === 'invalid_output') message = 'solve должна вернуть JSON-совместимый объект. Удали отладочный print из решения.'
      else if (result.error) throw unavailable()
      else {
        try {
          const actual = JSON.parse(result.output)
          passed = JSON.stringify(stable(actual)) === JSON.stringify(stable(test.expected))
          if (!passed) message = `Ожидалось ${JSON.stringify(test.expected)}, получено ${JSON.stringify(actual)}`.slice(0, 500)
        } catch {
          message = 'solve должна вернуть JSON-совместимый объект. Удали отладочный print из решения.'
        }
      }
      return { name: test.name, passed, message }
    })
  } finally {
    // Exact host-created private directory; the guest cannot see or write it.
    fs.rmSync(jobDirectory, { recursive: true, force: true })
  }
}

module.exports = { runPython, PYTHON_VERSION, WASMTIME_PYTHON_VERSION }
