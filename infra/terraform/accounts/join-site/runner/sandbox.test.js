const test = require('node:test')
const assert = require('node:assert/strict')
const { runTask } = require('./sandbox')

test('rejects unknown tasks', async () => {
  await assert.rejects(() => runTask('not-a-task', 'function solve() {}'), /unknown_task/)
})

test('interrupts an infinite loop', async () => {
  const result = await runTask('ai-evidence', 'function solve() { while (true) {} }')
  assert.equal(result.passed, false)
  assert.equal(result.tests.length, 3)
})

test('candidate cannot use host APIs', async () => {
  const result = await runTask('ai-evidence', 'function solve() { return { fetch: typeof fetch, process: typeof process, require: typeof require } }')
  assert.equal(result.passed, false)
  assert.match(result.tests[0].message, /undefined/)
})

test('non-JSON results fail inside the sandbox without crashing the runner', async () => {
  const result = await runTask('ai-evidence', 'function solve() { return undefined }')
  assert.equal(result.passed, false)
  assert.equal(result.tests.length, 3)
})
