const test = require('node:test')
const assert = require('node:assert/strict')
const { runSubmission } = require('./dispatcher')

const mobile = 'edium-mobile-2026-09-v3'

test('mobile assignments enforce the mandatory language before compiling', async () => {
  await assert.rejects(() => runSubmission('mobile-outbox-cat', 'function solve() {}', mobile, 'javascript'), /invalid_language/)
  await assert.rejects(() => runSubmission('mobile-outbox-cat', 'func solve() {}', mobile, 'swift'), /invalid_language/)
  await assert.rejects(() => runSubmission('mobile-permission-panda', 'fun solve() {}', mobile, 'kotlin'), /invalid_language/)
  await assert.rejects(() => runSubmission('mobile-outbox-cat', '', mobile, ['kotlin']), /invalid_language/)
  await assert.rejects(() => runSubmission('mobile-outbox-cat', '', mobile, 'constructor'), /invalid_language/)
})

test('mobile source limits apply to candidate code before toolchain lookup', async () => {
  await assert.rejects(() => runSubmission('mobile-outbox-cat', 'я'.repeat(32769), mobile, 'kotlin'), /invalid_source/)
  await assert.rejects(() => runSubmission('mobile-permission-panda', null, mobile, 'swift'), /invalid_source/)
})

test('legacy JS requests still execute all assigned tests', async () => {
  const result = await runSubmission('ai-evidence', 'function solve() { return {} }')
  assert.equal(result.passed, 0)
  assert.equal(result.total, 3)
  assert.equal(result.tests.length, 3)
})

test('other tracks cannot request Kotlin or Swift', async () => {
  await assert.rejects(() => runSubmission('ai-evidence', 'fun solve() {}', undefined, 'kotlin'), /invalid_language/)
  await assert.rejects(() => runSubmission('ai-evidence', 'func solve() {}', undefined, 'swift'), /invalid_language/)
})

test('missing task versions fail closed', async () => {
  await assert.rejects(() => runSubmission('mobile-outbox-cat', '', 'unknown', 'kotlin'), /unknown_task/)
})

test('missing native toolchains return unavailable, never a made-up test report', async () => {
  for (const [taskId, language, key] of [
    ['mobile-outbox-cat', 'kotlin', 'EDIUM_KOTLIN_HOME'],
    ['mobile-permission-panda', 'swift', 'EDIUM_SWIFT_HOME'],
  ]) {
    const previous = process.env[key]
    delete process.env[key]
    try {
      await assert.rejects(() => runSubmission(taskId, '', mobile, language), error => error.code === 'runtime_unavailable')
    } finally {
      if (previous === undefined) delete process.env[key]
      else process.env[key] = previous
    }
  }
})

test('a compiler error is explicitly reported as 0 of the complete suite', async t => {
  const compiler = require('./kotlin-compile')
  t.mock.method(compiler, 'compileKotlin', () => {
    const error = new Error('Candidate.kt: unresolved reference: banana')
    error.code = 'compilation_failed'
    throw error
  })
  const report = await runSubmission('mobile-outbox-cat', 'banana', mobile, 'kotlin')
  assert.equal(report.passed, 0)
  assert.equal(report.total, 7)
  assert.equal(report.tests.length, 7)
  assert.equal(report.allPassed, false)
  assert.ok(report.tests.every(test => !test.passed && /Ошибка компиляции:.*banana/.test(test.message)))
})

test('an incomplete Swift runtime response cannot claim success', async t => {
  t.mock.method(require('./swift-runtime'), 'runSwift', async () => [{ name: 'only one', passed: true }])
  await assert.rejects(() => runSubmission('mobile-permission-panda', '', mobile, 'swift'), /incomplete_runner_response/)
})
