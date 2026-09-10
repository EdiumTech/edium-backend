'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const net = require('node:net')
const { runPython } = require('../runner/python-runtime')
const { roleReferenceSolutions } = require('../runner/role-reference-solutions.testdata')
const catalog = require('../functions/app/contest_catalog.json')

async function main() {
  const set = Object.values(catalog.sets).find(set => set.tasks.some(task => task.id === 'ai-dataset-quarantine'))
  assert.ok(set, 'new AI task set must exist')
  for (const task of set.tasks) {
    const source = roleReferenceSolutions[task.id]?.python
    if (!source) continue
    const started = Date.now()
    const tests = await runPython(source, task.tests)
    assert.equal(tests.length, 7)
    assert.equal(tests.filter(test => test.passed).length, 7, JSON.stringify(tests))
    console.log(JSON.stringify({ taskId: task.id, passed: 7, total: 7, durationMs: Date.now() - started }))
  }

  const cases = Array.from({ length: 7 }, (_, index) => ({ name: `case ${index}`, input: {}, expected: {} }))
  for (const [name, source, pattern] of [
    ['syntax', 'def solve(data)\n return {}', /SyntaxError/],
    ['loop', 'def solve(data):\n while True: pass', /время выполнения/],
    ['memory', 'def solve(data):\n return {"huge": "x" * 1000000000}', /MemoryError|памяти/],
    ['output', 'def solve(data):\n print("x" * 300000)\n return {}', /слишком большой/],
    ['control-output', 'def solve(data):\n print(chr(0) * 200000)\n return {}', /Удали отладочный print/],
    ['control-output-before-error', 'def solve(data):\n print(chr(0) * 200000)\n raise ValueError("bounded failure")', /ValueError: bounded failure/],
    ['long-traceback', `def solve(data):\n raise ValueError("${'x'.repeat(3000)} actionable-error-tail")`, /actionable-error-tail/],
    ['long-syntax', `def solve(data = "${'x'.repeat(3000)}")\n return {}`, /SyntaxError/],
  ]) {
    const started = Date.now()
    const tests = await runPython(source, cases)
    assert.equal(tests.length, 7)
    assert.ok(tests.every(test => !test.passed && pattern.test(test.message)), JSON.stringify(tests))
    assert.ok(tests.every(test => test.message.length <= 500), 'candidate diagnostics must survive the API limit')
    console.log(JSON.stringify({ negative: name, boundedFailures: 7, durationMs: Date.now() - started }))
  }

  const probeDirectory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'edium-python-canary-'))
  const outside = path.join(probeDirectory, 'host-only.txt')
  fs.writeFileSync(outside, 'synthetic host-only test canary', { mode: 0o600 })
  let connections = 0
  const server = net.createServer(socket => { connections += 1; socket.destroy() })
  try {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
    const source = `def solve(data):
 import os, sys, math, re
 checks = {}
 def denied(name, callback):
  try:
   callback()
   checks[name] = False
  except Exception:
   checks[name] = True
 denied("outside_read", lambda: open(data["outside"]).read())
 denied("outside_write", lambda: open(data["outside"], "w"))
 denied("stdlib_write", lambda: open("/usr/local/lib/edium-must-not-exist", "w"))
 denied("stdlib_traversal", lambda: open("/usr/local/lib/../../../private/etc/passwd").read())
 denied("subprocess", lambda: __import__("subprocess").check_output(["/bin/echo", "escaped"]))
 denied("native_ffi", lambda: __import__("ctypes").CDLL(None))
 denied("network", lambda: __import__("socket").create_connection(("127.0.0.1", data["port"]), timeout=0.1))
 return {"checks":checks,"env":dict(os.environ),"platform":sys.platform,"version":list(sys.version_info[:3]),"stdlib":math.sqrt(16)==4 and re.fullmatch("a+", "aaa") is not None}
`
    const tests = await runPython(source, [{ name: 'WASI capabilities', input: { outside, port: server.address().port }, expected: {
      checks: { outside_read: true, outside_write: true, stdlib_write: true, stdlib_traversal: true, subprocess: true, native_ffi: true, network: true },
      env: {}, platform: 'wasi', version: [3, 12, 0], stdlib: true,
    } }])
    assert.equal(tests[0].passed, true, JSON.stringify(tests))
    assert.equal(fs.readFileSync(outside, 'utf8'), 'synthetic host-only test canary')
    assert.equal(fs.existsSync(path.join(process.env.EDIUM_PYTHON_WASI_HOME, 'usr/local/lib/edium-must-not-exist')), false)
    assert.equal(connections, 0, 'guest must not reach an actually listening loopback server')
    console.log('WASI capability denial: host read/write, stdlib writes/traversal, subprocess, FFI, network; env is empty; genuine Python 3.12.0')
  } finally {
    await new Promise(resolve => server.close(resolve))
    fs.rmSync(probeDirectory, { recursive: true, force: true })
  }
}

main().catch(error => { console.error(error.stack); process.exitCode = 1 })
