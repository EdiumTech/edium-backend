'use strict'

const assert = require('node:assert/strict')
const { runGo } = require('../runner/go-runtime')
const { runSubmission } = require('../runner/dispatcher')
const { roleReferenceSolutions } = require('../runner/role-reference-solutions.testdata')
const version = 'edium-go-2026-09-v4-backend'

const echo = 'package main\nfunc Solve(input map[string]any) map[string]any { return input }'
const cases = [
  {}, { value: null }, { ready: true, count: 42 }, { greeting: 'Кот 🐈' },
  { list: [1, 'two', false, null] }, { nested: { list: [{ n: -2.5 }] } }, JSON.parse('{"__proto__":{"ok":true},"zero":0}'),
].map((input, index) => ({ name: `JSON round trip ${index + 1}`, input, expected: input }))

async function main() {
  const started = Date.now()
  const result = await runGo(echo, cases)
  assert.equal(result.length, 7)
  assert.ok(result.every(test => test.passed), JSON.stringify(result))
  console.log(JSON.stringify({ name: 'Go JSON round trip', passed: 7, total: 7, durationMs: Date.now() - started }))
  if (process.argv.includes('--quick')) return
  for (const taskId of ['backend-webhook-receipts', 'backend-retry-desk', 'backend-quota-reservations']) {
    const report = await runSubmission(taskId, roleReferenceSolutions[taskId].go, version, 'go')
    assert.equal(report.total, 7)
    assert.equal(report.tests.length, 7)
    assert.equal(report.passed, 7, JSON.stringify(report.tests))
    console.log(JSON.stringify({ name: taskId, passed: report.passed, total: report.total, durationMs: report.durationMs }))
  }
  await assert.rejects(() => runGo('package main\nfunc Solve(input map[string]any) map[string]any { return notDefined }', cases), error => error.code === 'compilation_failed' && /undefined: notDefined/.test(error.message))
  console.log('syntax error: compilation_failed with actionable diagnostic')
  const syntax = await runSubmission('backend-webhook-receipts', 'package main\nfunc Solve(input map[string]any) map[string]any { return notDefined }', version, 'go')
  assert.equal(syntax.passed, 0)
  assert.equal(syntax.total, 7)
  assert.equal(syntax.tests.length, 7)
  assert.ok(syntax.tests.every(test => !test.passed && /undefined: notDefined/.test(test.message)))
  console.log('dispatcher compilation failure: 0/7, full diagnostics')
  const loop = await runSubmission('backend-webhook-receipts', 'package main\nfunc Solve(input map[string]any) map[string]any { for {} }', version, 'go')
  assert.equal(loop.passed, 0)
  assert.equal(loop.total, 7)
  assert.equal(loop.tests.length, 7)
  assert.ok(loop.tests.every(test => !test.passed && /время/.test(test.message)), JSON.stringify(loop))
  console.log('infinite loop: all seven cases interrupted')
  const denied = await runGo(`package main
import ("os"; "net"; "time")
func Solve(input map[string]any) map[string]any {
  _, readErr := os.ReadFile("/private/etc/passwd")
  connection, netErr := net.DialTimeout("tcp", "127.0.0.1:9", time.Millisecond * 200)
  if connection != nil { connection.Close() }
  return map[string]any{"fileDenied": readErr != nil, "networkDenied": netErr != nil, "environmentCount": len(os.Environ())}
}`, [{ name: 'No WASI host access', input: {}, expected: { fileDenied: true, networkDenied: true, environmentCount: 0 } }])
  assert.ok(denied.every(test => test.passed), JSON.stringify(denied))
  console.log('filesystem, network, inherited environment: unavailable')
  await assert.rejects(() => runGo('package main\nimport _ "edium.invalid/dependency"\nfunc Solve(input map[string]any) map[string]any { return input }', cases), error => error.code === 'compilation_failed' && /cannot find package/.test(error.message))
  console.log('external dependency: compilation refused without download')
}

main().catch(error => { console.error(error.stack); process.exitCode = 1 })
