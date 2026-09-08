'use strict'

const assert = require('node:assert/strict')
const { runSubmission } = require('../runner/dispatcher')
const { runSwift } = require('../runner/swift-runtime')
const { mobileReferenceSolutions } = require('../runner/mobile-reference-solutions.testdata')
const version = 'edium-mobile-2026-09-v3'

async function main() {
  for (const taskId of ['mobile-permission-panda', 'mobile-download-lunch']) {
    const result = await runSubmission(taskId, mobileReferenceSolutions[taskId].swift, version, 'swift')
    assert.equal(result.total, 7)
    assert.equal(result.passed, 7, JSON.stringify(result.tests))
    console.log(JSON.stringify({ name: taskId, passed: result.passed, total: result.total, durationMs: result.durationMs }))
  }
  const syntax = await runSubmission('mobile-permission-panda', 'func solve(_ input: [String: Any]) -> [String: Any] { notDefined }', version, 'swift')
  assert.equal(syntax.passed, 0)
  assert.equal(syntax.tests.length, 7)
  assert.match(syntax.tests[0].message, /cannot find 'notDefined'/)
  console.log('syntax error: all seven tests report a compilation failure')

  const loop = await runSubmission('mobile-permission-panda', 'func solve(_ input: [String: Any]) -> [String: Any] { while true {} }', version, 'swift')
  assert.equal(loop.passed, 0)
  assert.equal(loop.tests.length, 7)
  assert.ok(loop.tests.every(test => /время выполнения/.test(test.message)), JSON.stringify(loop.tests))
  console.log('infinite loop: all seven tests are bounded and fail')

  const noHost = await runSwift(`import Foundation
func solve(_ input: [String: Any]) -> [String: Any] {
    return ["outsideFile": FileManager.default.fileExists(atPath: "/private/etc/passwd"),
            "environment": ProcessInfo.processInfo.environment]
}`, [{ name: 'no filesystem or environment', input: {}, expected: { outsideFile: false, environment: {} } }])
  assert.equal(noHost[0].passed, true, JSON.stringify(noHost))
  console.log('WASI filesystem and inherited environment: unavailable')

  const macro = await runSubmission('mobile-permission-panda', `
@freestanding(expression)
macro evil() -> Int = #externalMacro(module: "CandidatePluginMustNotExist", type: "Probe")
func solve(_ input: [String: Any]) -> [String: Any] { return ["value": #evil()] }
`, version, 'swift')
  assert.equal(macro.passed, 0)
  assert.equal(macro.tests.length, 7)
  assert.match(macro.tests[0].message, /macro|plugin/i)
  console.log('candidate external macro: compilation refused')
}

main().catch(error => { console.error(error.stack); process.exitCode = 1 })
