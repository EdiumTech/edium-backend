const test = require('node:test')
const assert = require('node:assert/strict')
const { runTask } = require('./sandbox')

test('rejects unknown tasks', async () => {
  await assert.rejects(() => runTask('not-a-task', 'function solve() {}'), /unknown_task/)
})

test('interrupts an infinite loop', async () => {
  const result = await runTask('ai-evidence', 'function solve() { while (true) {} }')
  assert.equal(result.passed, 0)
  assert.equal(result.total, 3)
  assert.equal(result.allPassed, false)
  assert.equal(result.tests.length, 3)
  assert.ok(result.tests.every(test => !test.passed))
})

test('candidate cannot use host APIs', async () => {
  const result = await runTask('ai-evidence', 'function solve() { return { fetch: typeof fetch, process: typeof process, require: typeof require } }')
  assert.equal(result.passed, 0)
  assert.match(result.tests[0].message, /undefined/)
})

test('non-JSON results fail inside the sandbox without crashing the runner', async () => {
  const result = await runTask('ai-evidence', 'function solve() { return undefined }')
  assert.equal(result.passed, 0)
  assert.equal(result.tests.length, 3)
})

test('TypeScript syntax reports a failed result for every test', async () => {
  const result = await runTask('ai-evidence', 'function solve(input: unknown): unknown { return input }')
  assert.equal(result.passed, 0)
  assert.equal(result.total, 3)
  assert.ok(result.tests.every(test => !test.passed && test.message.length > 0))
})

test('runs remaining cases after an exception and counts partial success', async () => {
  const result = await runTask('ai-evidence', `function solve(input) {
    if (input.threshold === 0.8) throw new Error('Принтер съел отчёт');
    const supportedIds = [], uncertainIds = [];
    let requiredMissing = false;
    for (const claim of input.claims) {
      const found = input.sources.some(source => source.claimId === claim.id && source.status === 'verified' && source.confidence >= input.threshold);
      if (found) supportedIds.push(claim.id);
      else { uncertainIds.push(claim.id); requiredMissing ||= claim.required; }
    }
    return { supportedIds, uncertainIds, action: requiredMissing ? 'human_review' : uncertainIds.length ? 'caution' : 'answer' };
  }`)
  assert.equal(result.passed, 2)
  assert.equal(result.total, 3)
  assert.equal(result.allPassed, false)
  assert.deepEqual(result.tests.map(test => test.passed), [false, true, true])
  assert.match(result.tests[0].message, /Принтер/)
})

test('version selection fails closed instead of running a different assignment', async () => {
  await assert.rejects(() => runTask('ai-evidence', 'function solve() {}', 'missing-version'), /unknown_task/)
  await assert.rejects(() => runTask('ai-evidence', 'function solve() {}', 'edium-js-2026-09-v2-design'), /unknown_task/)
})
