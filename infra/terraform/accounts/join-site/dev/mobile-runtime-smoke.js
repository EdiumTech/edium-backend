'use strict'

// Explicit smoke command: trusted mobile fixtures and deliberate bounded
// failures run through the real dispatcher and isolated language runtime.
const assert = require('node:assert/strict')
const { runSubmission } = require('../runner/dispatcher')
const { mobileReferenceSolutions } = require('../runner/mobile-reference-solutions.testdata')
const version = 'edium-mobile-2026-09-v3'

async function check(taskId, source, language, expected, name) {
  const result = await runSubmission(taskId, source, version, language)
  assert.equal(result.total, 7, name)
  assert.equal(result.tests.length, 7, name)
  assert.equal(result.passed, expected, `${name}: ${JSON.stringify(result.tests)}`)
  assert.equal(result.allPassed, expected === 7, name)
  console.log(JSON.stringify({ name, language, passed: result.passed, total: result.total, durationMs: result.durationMs }))
  return result
}

async function main() {
  for (const taskId of ['mobile-outbox-cat', 'mobile-download-lunch']) {
    await check(taskId, mobileReferenceSolutions[taskId].kotlin, 'kotlin', 7, taskId)
  }
  const syntax = await check('mobile-outbox-cat', 'fun solve(input: Map<String, Any?>): Map<String, Any?> = notDefined', 'kotlin', 0, 'syntax error')
  assert.match(syntax.tests[0].message, /unresolved reference/i)
  assert.doesNotMatch(syntax.tests[0].message, /java\.lang\.System|sun\.misc\.Unsafe/)
  const loop = await check('mobile-outbox-cat', 'fun solve(input: Map<String, Any?>): Map<String, Any?> { js("(function(){ while (true) {} })()"); return emptyMap() }', 'kotlin', 0, 'infinite interop loop')
  assert.ok(loop.tests.every(test => /interrupted|времени|ограничения/.test(test.message)))
  const noHost = mobileReferenceSolutions['mobile-outbox-cat'].kotlin.replace('{', `{
    check(js("typeof process === 'undefined' && typeof require === 'undefined' && typeof fetch === 'undefined' && typeof globalThis.process === 'undefined' && typeof globalThis.require === 'undefined' && typeof globalThis.fetch === 'undefined'") as Boolean)
  `)
  await check('mobile-outbox-cat', noHost, 'kotlin', 7, 'no host capabilities')
}

main().catch(error => { console.error(error.stack); process.exitCode = 1 })
